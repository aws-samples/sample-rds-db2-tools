"""Account-level defaults loader for the rds-db2-deployer skill.

A customer fills one small JSON file (``account-defaults.json``) ONCE per
account/environment, by reading values from the AWS console (view access is
enough). The skill loads it and merges it into every ``Deployment_Intent`` so
the customer never re-types the account-level facts (region, subnet group,
security group, KMS MRK, monitoring role, ingress, IBM identifiers, default
tags). Anything named in the prompt overrides a default.

Design notes (kept deliberately small and dependency-free beyond ``jsonschema``,
which the skill already depends on):

* The file's field names match the ``Deployment_Intent`` field names exactly, so
  merging is a straight copy — no translation table to drift out of sync.
* A few keys are *meta* (``schema_version``, ``_about``, ``gitops_aws_account_id``)
  and never reach the intent; :data:`META_FIELDS` lists them.
* Every value sourced from the defaults file is tagged provenance
  ``user_provided`` — the customer supplied it, just ahead of time.
* Validation reuses the published ``account-defaults.schema.json`` so a typo
  (wrong type, unknown key) is caught with a clear message instead of silently
  producing a broken intent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, MutableMapping, Optional, Union

from jsonschema.validators import Draft202012Validator

# scripts/account_defaults.py -> scripts/ -> package root -> schemas/...
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]

#: Canonical path to the account-defaults JSON Schema.
DEFAULT_SCHEMA_PATH = _PACKAGE_ROOT / "schemas" / "account-defaults.schema.json"

#: Conventional filename a customer places in their gitops repo.
DEFAULT_FILENAME = "account-defaults.json"

#: Keys that exist for humans / tooling and must NOT be copied into the intent.
#: ``engine_major_version`` is consumed by the agent as the pinned major version
#: (it flows into ``engine_version`` / ``db_parameter_group_family`` via live
#: resolution), so it is not a 1:1 intent field and must not be merged directly.
META_FIELDS: frozenset[str] = frozenset(
    {"schema_version", "_about", "gitops_aws_account_id", "engine_major_version"}
)


class AccountDefaultsError(Exception):
    """A failure loading or validating the account-defaults file. Halts before
    any intent is built rather than producing a partial/incorrect intent."""


def load_schema(schema_path: Union[str, Path, None] = None) -> dict[str, Any]:
    """Load and parse the account-defaults JSON Schema."""
    path = Path(schema_path) if schema_path is not None else DEFAULT_SCHEMA_PATH
    return json.loads(path.read_text())


def load_account_defaults(
    path: Union[str, Path],
    *,
    validate: bool = True,
    schema_path: Union[str, Path, None] = None,
) -> dict[str, Any]:
    """Load (and by default validate) an ``account-defaults.json`` file.

    Args:
        path: path to the customer's account-defaults file.
        validate: when True (default), validate against the published schema and
            raise :class:`AccountDefaultsError` on any violation (every error is
            reported, not just the first).
        schema_path: optional alternate schema location.

    Returns:
        The parsed defaults document as a dict.

    Raises:
        AccountDefaultsError: the file is missing, not valid JSON, or fails
            schema validation.
    """
    p = Path(path)
    if not p.is_file():
        raise AccountDefaultsError(f"account-defaults file not found: {p}")
    try:
        doc = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise AccountDefaultsError(f"account-defaults file is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise AccountDefaultsError("account-defaults file must be a JSON object")

    if validate:
        validator = Draft202012Validator(load_schema(schema_path))
        errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
        if errors:
            lines = [f"account-defaults file failed validation ({len(errors)} error(s)):"]
            for e in errors:
                where = ".".join(str(t) for t in e.absolute_path) or "<root>"
                lines.append(f"  - {where}: {e.message}")
            raise AccountDefaultsError("\n".join(lines))
    return doc


def intent_fields_from_defaults(defaults: Mapping[str, Any]) -> dict[str, Any]:
    """Return only the intent-applicable fields from a defaults document.

    Drops the meta keys (:data:`META_FIELDS`) so the result is a clean mapping of
    ``Deployment_Intent`` field name -> value, ready to layer into an intent.
    """
    return {k: v for k, v in defaults.items() if k not in META_FIELDS}


def engine_major_version_from_defaults(defaults: Mapping[str, Any]) -> Optional[str]:
    """Return the account-default Db2 major version to pin (``"11.5"``/``"12.1"``),
    or ``None`` when unset.

    This is a META field (not a 1:1 intent field): the agent passes it to
    :func:`resolve_intent.apply_engine_version_to_intent` as the
    ``pinned_major_version`` so the live-resolved ``engine_version`` and
    ``db_parameter_group_family`` reflect it. A prompt-supplied major overrides
    this account default.
    """
    value = defaults.get("engine_major_version")
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def merge_into_intent(
    intent: MutableMapping[str, Any],
    provenance: MutableMapping[str, str],
    defaults: Mapping[str, Any],
    *,
    prompt_fields: Iterable[str] = (),
) -> list[str]:
    """Merge account defaults into a resolved intent in place.

    For each intent-applicable default field that the prompt did NOT already
    set, the value is written into ``intent`` and its provenance recorded as
    ``user_provided`` (the customer supplied it ahead of time). A field named in
    ``prompt_fields`` is left untouched — the prompt always wins over a default.

    Args:
        intent: the resolved intent mapping to update.
        provenance: the parallel provenance mapping to update.
        defaults: the loaded account-defaults document.
        prompt_fields: field names the prompt explicitly set (these win).

    Returns:
        The list of field names that were applied from the defaults, in file
        order, so the caller can echo "filled from account-defaults: ...".
    """
    prompt = set(prompt_fields)
    applied: list[str] = []
    for key, value in intent_fields_from_defaults(defaults).items():
        if key in prompt:
            continue
        intent[key] = value
        provenance[key] = "user_provided"
        applied.append(key)
    intent["_provenance"] = dict(provenance)
    return applied


def missing_required_account_fields(defaults: Mapping[str, Any]) -> list[str]:
    """Return the account-level fields a deployable intent needs that are still
    absent from the defaults, so the agent knows exactly what to ask for.

    Only the fields that can be neither created nor defaulted are hard-required:
    ``region``, ``vpc_id`` (the skill never creates a VPC), ``vpc_security_group_ids``
    (the security group is always customer-supplied), and the IBM identifiers
    (which must never be fabricated; satisfied by the literal field OR its
    ``*_ssm`` companion).

    The reusable account resources — ``kms_key_id``, ``db_subnet_group_name`` and
    ``monitoring_role_arn`` (and the optional ``master_user_secret_kms_key_id``) —
    are NOT listed here: leaving them blank tells the composer to CREATE them on
    the first apply (via ``3-kms`` / ``1-networking`` / ``2-iam``); supplying a
    value REUSES the existing resource (R10.5/10.6). Record the created
    identifiers in account-defaults.json after the first deploy to reuse them for
    every subsequent instance. See ``references/account-defaults.md``.
    """
    must_have = (
        "region",
        "vpc_id",
        "vpc_security_group_ids",
    )

    def _present(f: str) -> bool:
        v = defaults.get(f)
        if isinstance(v, (list, tuple)):
            return len(v) > 0
        return bool(str(v or "").strip())

    out: list[str] = [f for f in must_have if not _present(f)]
    # IBM IDs: satisfied by either the literal field or its SSM-name companion.
    if not (_present("ibm_customer_id") or _present("ibm_customer_id_ssm")):
        out.append("ibm_customer_id (or ibm_customer_id_ssm)")
    if not (_present("ibm_site_id") or _present("ibm_site_id_ssm")):
        out.append("ibm_site_id (or ibm_site_id_ssm)")
    return out


def _main(argv: list[str] | None = None) -> int:
    """CLI: validate an account-defaults file and print the resolved fields.

    Usage: ``python -m scripts.account_defaults <account-defaults.json>``
    Exit 0 = valid; non-zero = a load/validation error (message on stderr).
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Validate an account-defaults.json file and print resolved fields."
    )
    parser.add_argument("path", help="path to the account-defaults.json file")
    parser.add_argument(
        "--no-validate", action="store_true", help="skip JSON Schema validation"
    )
    args = parser.parse_args(argv)

    try:
        defaults = load_account_defaults(args.path, validate=not args.no_validate)
    except AccountDefaultsError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    fields = intent_fields_from_defaults(defaults)
    missing = missing_required_account_fields(defaults)
    print(f"account-defaults OK: {len(fields)} intent field(s) defined")
    for k in fields:
        print(f"  - {k}")
    if missing:
        print("still-needed (no safe default; agent must ask): " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
