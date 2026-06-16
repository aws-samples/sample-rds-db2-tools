"""The two-layer Intent_Validator for the rds-db2-provision-skill.

The validator is the gate between a resolved ``Deployment_Intent`` and any
Terraform rendering: nothing reaches the composer until it passes here (R4.2).
Validation is performed in two layers, mirroring the design's central
"definitive validation" decision:

* **Layer 1 — JSON Schema (``Schema_Constraint``)**: single-field ranges,
  enums, presence, and presence-conditionals expressed in
  ``schemas/deployment-intent.schema.json`` (R4). Implemented in this task
  (5.1) by :func:`validate_schema`.
* **Layer 2 — code validator (``Cross_Field_Rule``)**: arithmetic across
  fields the schema cannot express (gp3/io2 ratio bounds, throughput
  derivation) and the security-invariant cross-checks (R6, R19). These are
  added by tasks 5.2 and 5.3, which extend :func:`validate_intent` and append
  their findings to the same :class:`ValidationResult`.

Design for clean extension (so 5.2/5.3 plug in, not rewrite):

* Every failure is a :class:`ValidationError` carrying the ``field`` it
  concerns, the ``rule`` it violated, and a human-readable ``message`` — the
  same shape regardless of layer, so reporting is uniform (R4.3).
* :class:`ValidationResult` accumulates *every* failure across both layers
  rather than stopping at the first; callers check ``result.ok`` and halt
  before producing any artifact when it is ``False`` (R4.4).
* :func:`validate_schema` is the Layer-1 entry point. :func:`validate_intent`
  is the top-level entry point that runs Layer 1 today and will also run the
  Layer-2 rule functions added by 5.2/5.3. Both report by field + rule.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Union

from jsonschema.validators import Draft202012Validator

try:  # Prefer the package-qualified module so the helper is identical to the
    # one the Sizing_Resolver uses (single source for the R19.7 derivation).
    from scripts.resolve_intent import derive_gp3_storage_throughput
except ImportError:  # Fall back to a bare import when scripts/ is on sys.path.
    from resolve_intent import derive_gp3_storage_throughput

# ---------------------------------------------------------------------------
# Schema location
# ---------------------------------------------------------------------------

# scripts/validate_intent.py -> scripts/ -> package root -> schemas/...
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]

#: Canonical path to the single published Intent_Schema (R4.1).
DEFAULT_SCHEMA_PATH = _PACKAGE_ROOT / "schemas" / "deployment-intent.schema.json"

#: Identifies a failure produced by the JSON Schema layer.
LAYER_SCHEMA = "schema"

#: Identifies a failure produced by the Layer-2 cross-field arithmetic rules
#: (task 5.2, R19.6/19.7/19.8 and the allocated-storage bounds of R19.1/2/3).
LAYER_CROSS_FIELD = "cross_field"

#: Identifies a failure produced by the Layer-2 security-invariant cross-checks
#: (task 5.3, R6.8/6.9/6.11, R7.8/7.9, R8.12, R13.13/13.14).
LAYER_SECURITY = "security"

# ---------------------------------------------------------------------------
# Layer-2 storage arithmetic bounds (R19; ported from 0cr-ins.sh)
# ---------------------------------------------------------------------------

#: gp3 ``iops / allocated_storage`` ratio must be > 0 and <= this (R19.6).
GP3_MAX_IOPS_RATIO = 500

#: io2 ``iops / allocated_storage`` ratio inclusive bounds (R19.8).
IO2_MIN_IOPS_RATIO = 0.5
IO2_MAX_IOPS_RATIO = 1000

#: ``allocated_storage`` must be strictly less than this many GiB (R19.1).
MAX_ALLOCATED_STORAGE_GIB = 64000

#: gp3 ``allocated_storage`` floor in GiB (R19.2).
GP3_MIN_ALLOCATED_STORAGE_GIB = 20

#: io2 ``allocated_storage`` floor in GiB (R19.3).
IO2_MIN_ALLOCATED_STORAGE_GIB = 100

#: gp3 only carries IOPS/throughput at or above this allocated storage (R19.4/5).
#: Below it, RDS applies the baseline and the ratio/throughput rules don't run.
GP3_PERF_THRESHOLD_GIB = 400


# ---------------------------------------------------------------------------
# Layer-2 security-invariant constants (R6/R7/R8/R13; task 5.3)
# ---------------------------------------------------------------------------

#: The set of intent fields that name a KMS key for an encryptable resource
#: (R6.10/6.11). Each maps the field to the human-readable resource it
#: protects, so a rejection can identify the resource by name. ``storage`` uses
#: ``kms_key_id``; the managed master-user secret uses
#: ``master_user_secret_kms_key_id``; an audit/restore S3 bucket CMK uses
#: ``audit_bucket_kms_key_id`` when supplied.
ENCRYPTABLE_KEY_FIELDS: dict[str, str] = {
    "kms_key_id": "RDS storage",
    "master_user_secret_kms_key_id": "master-user secret (Secrets Manager)",
    "audit_bucket_kms_key_id": "audit/restore S3 bucket",
}

#: Substrings that mark an ARN/alias as an AWS-owned or AWS-managed default key
#: rather than a customer-managed CMK (R6.11). Matched case-insensitively.
AWS_OWNED_KEY_MARKERS: tuple[str, ...] = (
    "alias/aws/",  # any AWS-managed alias, e.g. alias/aws/rds
    "aws/rds",
    "aws/secretsmanager",
    "aws/s3",
)

#: Marker (in the key-id portion of a CMK ARN/ID) that identifies a
#: multi-region key (MRK). AWS MRK key-ids begin with ``mrk-`` (R6.11/13.14).
MRK_KEY_ID_PREFIX = "mrk-"

#: The acknowledgement field that must be ``true`` to permit public exposure
#: (R6.8/6.9). Absent or non-``true`` blocks publicly_accessible / open ingress.
PUBLIC_ACCESS_ACK_FIELD = "public_access_acknowledged"

#: An "anywhere" IPv4 ingress CIDR — opening it requires acknowledgement (R6.9).
OPEN_INGRESS_CIDR = "0.0.0.0/0"

#: Intent fields that may carry security-group ingress source CIDRs (R6.9). The
#: skill's intent uses ``ingress_cidrs``; ``ingress_cidr_blocks`` is accepted as
#: a synonym so a rename does not silently disable the check.
INGRESS_CIDR_FIELDS: tuple[str, ...] = ("ingress_cidrs", "ingress_cidr_blocks")

#: IBM licensing identifier fields required for every edition (R7.8/7.9).
IBM_ID_FIELDS: tuple[str, ...] = ("ibm_customer_id", "ibm_site_id")

#: Maximum length of an IBM identifier after trimming whitespace (R7.9).
IBM_ID_MAX_LENGTH = 255

#: The customer-supplied mandatory tag keys (R14.3/R14.6). Each must be present
#: in the intent's ``tags`` object with a non-empty value or the intent is
#: rejected and the offending key(s) named.
MANDATORY_CUSTOMER_TAG_KEYS: tuple[str, ...] = ("Project", "Environment", "Owner")

#: Db2 Community Edition — BYOL-style License Manager tracking does not apply
#: (R8.12). License Manager tracking is requested via this boolean field.
COMMUNITY_EDITION = "db2-ce"
LICENSE_MANAGER_FIELD = "license_manager"

#: Fields that, when truthy, indicate a cross-region standby/replica is
#: requested (R13.13). A standby needs automated backups, so
#: ``backup_retention_period = 0`` is a conflict.
STANDBY_REQUEST_FIELDS: tuple[str, ...] = ("standby_replica", "standby", "replica")


# ---------------------------------------------------------------------------
# Result types (shared by both validation layers)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationError:
    """A single validation failure, reported by field and the rule it violated.

    Attributes:
        field: the intent field the failure concerns, as a dotted/bracketed
            path (e.g. ``iops``, ``tags.Environment``, ``vpc_security_group_ids[0]``).
            ``"<root>"`` denotes a document-level failure with no single field.
        rule: the specific rule that was violated. For Layer 1 this is the JSON
            Schema keyword (``required``, ``enum``, ``minimum``, ``type``,
            ``const``, ``oneOf``, ``not``, ``dependentRequired``, ...). For
            Layer 2 it is the named cross-field rule (added by 5.2/5.3).
        message: a human-readable explanation suitable for surfacing to the user.
        layer: which validation layer produced this failure (``schema`` for
            Layer 1; the Layer-2 functions set their own layer label).
    """

    field: str
    rule: str
    message: str
    layer: str = LAYER_SCHEMA

    def __str__(self) -> str:  # pragma: no cover - convenience formatting
        return f"[{self.layer}] {self.field}: {self.rule} - {self.message}"


@dataclass
class ValidationResult:
    """Accumulates every validation failure across all layers.

    The validator never stops at the first failure: it collects every failing
    field together with the rule it violated so the caller can report them all
    at once (R4.3). Callers MUST check :attr:`ok` and halt before producing any
    Terraform artifact when it is ``False`` (R4.4).
    """

    errors: list[ValidationError] = dataclass_field(default_factory=list)

    @property
    def ok(self) -> bool:
        """``True`` when no failures were recorded (the intent is valid so far)."""
        return not self.errors

    def add(self, error: ValidationError) -> None:
        """Append a single failure."""
        self.errors.append(error)

    def extend(self, errors: Iterable[ValidationError]) -> None:
        """Append many failures."""
        self.errors.extend(errors)

    def fields(self) -> list[str]:
        """The distinct fields that failed, in first-seen order (R4.3)."""
        seen: dict[str, None] = {}
        for err in self.errors:
            seen.setdefault(err.field, None)
        return list(seen)

    def report(self) -> str:
        """A multi-line, field+rule report of every failure (R4.3).

        Returns an empty string when the intent is valid.
        """
        if self.ok:
            return ""
        lines = [
            f"Deployment_Intent validation failed with {len(self.errors)} "
            f"error(s); no Terraform artifacts will be produced:"
        ]
        for err in self.errors:
            lines.append(f"  - {err.field}: violates '{err.rule}' ({err.message})")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Schema loading
# ---------------------------------------------------------------------------


def load_schema(schema_path: Union[str, Path, None] = None) -> dict[str, Any]:
    """Load and parse the Intent_Schema JSON document.

    Args:
        schema_path: path to the schema file; defaults to the package's single
            published schema (R4.1).

    Returns:
        The parsed schema as a dict.
    """
    path = Path(schema_path) if schema_path is not None else DEFAULT_SCHEMA_PATH
    return json.loads(path.read_text())


def _build_validator(
    schema: Union[Mapping[str, Any], None] = None,
    schema_path: Union[str, Path, None] = None,
) -> Draft202012Validator:
    """Construct a Draft 2020-12 validator for the Intent_Schema.

    The dialect is fixed to Draft 2020-12 (the dialect the schema declares,
    R4.1) so validation results are identical across runs. The schema itself is
    checked for validity first so a malformed schema fails loudly rather than
    silently passing everything.
    """
    if schema is None:
        schema = load_schema(schema_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


# ---------------------------------------------------------------------------
# Layer-1 error translation
# ---------------------------------------------------------------------------


def _error_field(error: Any) -> str:
    """Derive the intent field a jsonschema error concerns.

    For most keywords the failing path is ``error.absolute_path``. For
    ``required`` the missing property name is not in the path (the path points
    at the containing object), so it is pulled from ``error.validator_value`` /
    the message instead, so the report names the actually-missing field (R4.3).
    """
    # A `required` failure: name the missing property rather than the container.
    if error.validator == "required":
        missing = _missing_required_field(error)
        if missing is not None:
            return _join_path(error.absolute_path, missing)

    if not error.absolute_path:
        return "<root>"
    return _join_path(error.absolute_path, None)


def _missing_required_field(error: Any) -> Optional[str]:
    """Extract the missing property name from a ``required`` error."""
    # jsonschema phrases the message as "'<name>' is a required property".
    message = error.message
    if "'" in message:
        try:
            return message.split("'")[1]
        except IndexError:  # pragma: no cover - defensive
            return None
    return None


def _join_path(path: Iterable[Any], leaf: Optional[str]) -> str:
    """Render a jsonschema path deque into a dotted/bracketed field string."""
    parts: list[str] = []
    for token in path:
        if isinstance(token, int):
            parts.append(f"[{token}]")
        else:
            parts.append(f".{token}" if parts else str(token))
    if leaf is not None:
        parts.append(f".{leaf}" if parts else leaf)
    rendered = "".join(parts)
    return rendered or "<root>"


def _schema_error_to_validation_error(error: Any) -> ValidationError:
    """Translate a jsonschema ``ValidationError`` into our uniform shape."""
    return ValidationError(
        field=_error_field(error),
        rule=str(error.validator),
        message=error.message,
        layer=LAYER_SCHEMA,
    )


# ---------------------------------------------------------------------------
# Layer 1 — JSON Schema validation (R4.2/4.3/4.4)
# ---------------------------------------------------------------------------


def validate_schema(
    intent: Mapping[str, Any],
    *,
    schema: Union[Mapping[str, Any], None] = None,
    schema_path: Union[str, Path, None] = None,
) -> ValidationResult:
    """Run Layer-1 JSON Schema validation over a complete ``Deployment_Intent``.

    Validates the *complete* intent against the published Intent_Schema (R4.2)
    and collects EVERY failing field together with the specific schema rule it
    violated (R4.3) — it never stops at the first error. The returned
    :class:`ValidationResult` has ``ok == False`` when any rule failed; callers
    MUST check it and halt before producing any Terraform artifact (R4.4).

    Args:
        intent: the deployment intent document to validate.
        schema: a pre-parsed schema to validate against (optional; defaults to
            the package's published schema).
        schema_path: an alternate path to load the schema from (optional).

    Returns:
        A :class:`ValidationResult` accumulating every Layer-1 failure.
    """
    validator = _build_validator(schema=schema, schema_path=schema_path)
    result = ValidationResult()

    # iter_errors yields ALL violations; sort for a stable, readable report.
    errors = sorted(
        validator.iter_errors(dict(intent)),
        key=lambda e: (list(e.absolute_path), str(e.validator)),
    )
    result.extend(_schema_error_to_validation_error(e) for e in errors)
    return result


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Layer 2 — cross-field arithmetic rules (R19; task 5.2)
# ---------------------------------------------------------------------------


def validate_storage_arithmetic(
    intent: Mapping[str, Any],
) -> list[ValidationError]:
    """Run the Layer-2 cross-field arithmetic rules over a ``Deployment_Intent``.

    These are the ``Cross_Field_Rule`` checks JSON Schema cannot express because
    they relate ``iops``, ``allocated_storage``, and ``storage_throughput`` to
    one another (R19). This function assumes Layer 1 has already passed, so the
    referenced fields are present and well-typed; it is invoked from
    :func:`validate_intent` only after :func:`validate_schema` succeeds.

    Rules enforced (each ported from ``0cr-ins.sh``):

    * **R19.1** — ``allocated_storage`` must be strictly less than 64000 GiB.
    * **R19.2** — gp3 ``allocated_storage`` must be >= 20 GiB.
    * **R19.3** — io2 ``allocated_storage`` must be >= 100 GiB.
    * **R19.6** — gp3 (>= 400 GiB) ``iops / allocated_storage`` ratio in (0, 500].
    * **R19.7** — gp3 (>= 400 GiB) ``storage_throughput`` must equal
      ``min(floor(iops / 4), 4000)`` (the Sizing_Resolver's derivation).
    * **R19.8** — io2 ``iops / allocated_storage`` ratio in [0.5, 1000].

    Every failure names the field, the violated rule, and reports the *computed*
    value alongside the bound it broke (R19.6/19.8/19.10), tagged with the
    :data:`LAYER_CROSS_FIELD` layer label so reporting distinguishes Layer 2
    from Layer 1.

    Returns:
        A list of :class:`ValidationError` — empty when every rule holds.
    """
    errors: list[ValidationError] = []

    storage_type = intent.get("storage_type")
    allocated_storage = intent.get("allocated_storage")
    iops = intent.get("iops")
    storage_throughput = intent.get("storage_throughput")

    # All arithmetic rules need a numeric allocated_storage; Layer 1 guarantees
    # it, but guard so a surprising shape degrades to "no Layer-2 finding"
    # rather than a crash.
    if not isinstance(allocated_storage, (int, float)):
        return errors

    # --- R19.1: hard upper bound on allocated storage, any storage type ----
    if allocated_storage >= MAX_ALLOCATED_STORAGE_GIB:
        errors.append(
            ValidationError(
                field="allocated_storage",
                rule="allocated_storage_max",
                message=(
                    f"allocated_storage {allocated_storage:g} GiB must be less "
                    f"than the maximum of {MAX_ALLOCATED_STORAGE_GIB} GiB "
                    "(R19.1)."
                ),
                layer=LAYER_CROSS_FIELD,
            )
        )

    if storage_type == "gp3":
        # --- R19.2: gp3 lower bound on allocated storage ------------------
        if allocated_storage < GP3_MIN_ALLOCATED_STORAGE_GIB:
            errors.append(
                ValidationError(
                    field="allocated_storage",
                    rule="gp3_allocated_storage_min",
                    message=(
                        f"gp3 allocated_storage {allocated_storage:g} GiB must "
                        f"be at least the gp3 minimum of "
                        f"{GP3_MIN_ALLOCATED_STORAGE_GIB} GiB (R19.2)."
                    ),
                    layer=LAYER_CROSS_FIELD,
                )
            )

        # gp3 below 400 GiB uses the RDS baseline and carries no iops/throughput
        # (R19.4, a Layer-1 concern); the ratio/throughput arithmetic only
        # applies at or above the performance threshold (R19.6/19.7).
        if allocated_storage >= GP3_PERF_THRESHOLD_GIB and isinstance(
            iops, (int, float)
        ):
            # --- R19.6: gp3 ratio in (0, 500] -----------------------------
            ratio = iops / allocated_storage
            if not (0 < ratio <= GP3_MAX_IOPS_RATIO):
                errors.append(
                    ValidationError(
                        field="iops",
                        rule="gp3_iops_ratio",
                        message=(
                            f"gp3 iops/allocated_storage ratio "
                            f"{iops:g}/{allocated_storage:g} = {ratio:g} must be "
                            f"greater than 0 and at most {GP3_MAX_IOPS_RATIO} "
                            "(R19.6)."
                        ),
                        layer=LAYER_CROSS_FIELD,
                    )
                )

            # --- R19.7: gp3 throughput must equal the derived value -------
            if isinstance(storage_throughput, (int, float)):
                derived = derive_gp3_storage_throughput(int(iops))
                if storage_throughput != derived:
                    errors.append(
                        ValidationError(
                            field="storage_throughput",
                            rule="gp3_throughput_derivation",
                            message=(
                                f"gp3 storage_throughput {storage_throughput:g} "
                                f"must equal the derived "
                                f"min(floor(iops/4), 4000) = min(floor("
                                f"{iops:g}/4), 4000) = {derived} (R19.7)."
                            ),
                            layer=LAYER_CROSS_FIELD,
                        )
                    )

    elif storage_type == "io2":
        # --- R19.3: io2 lower bound on allocated storage ------------------
        if allocated_storage < IO2_MIN_ALLOCATED_STORAGE_GIB:
            errors.append(
                ValidationError(
                    field="allocated_storage",
                    rule="io2_allocated_storage_min",
                    message=(
                        f"io2 allocated_storage {allocated_storage:g} GiB must "
                        f"be at least the io2 minimum of "
                        f"{IO2_MIN_ALLOCATED_STORAGE_GIB} GiB (R19.3)."
                    ),
                    layer=LAYER_CROSS_FIELD,
                )
            )

        # --- R19.8: io2 ratio in [0.5, 1000] ------------------------------
        if isinstance(iops, (int, float)):
            ratio = iops / allocated_storage
            if not (IO2_MIN_IOPS_RATIO <= ratio <= IO2_MAX_IOPS_RATIO):
                errors.append(
                    ValidationError(
                        field="iops",
                        rule="io2_iops_ratio",
                        message=(
                            f"io2 iops/allocated_storage ratio "
                            f"{iops:g}/{allocated_storage:g} = {ratio:g} must be "
                            f"between {IO2_MIN_IOPS_RATIO} and "
                            f"{IO2_MAX_IOPS_RATIO} inclusive (R19.8)."
                        ),
                        layer=LAYER_CROSS_FIELD,
                    )
                )

    return errors


# ---------------------------------------------------------------------------
# Layer 2 — security-invariant cross-checks (R6/R7/R8/R13; task 5.3)
# ---------------------------------------------------------------------------


def _is_aws_owned_key(key: str) -> bool:
    """True when ``key`` names an AWS-owned/managed default key (R6.11)."""
    lowered = key.lower()
    return any(marker in lowered for marker in AWS_OWNED_KEY_MARKERS)


def _is_mrk(key: str) -> bool:
    """True when ``key`` is a multi-region key (MRK) (R6.11/13.14).

    AWS MRK key-ids begin with ``mrk-``. The marker is detected anywhere in the
    value so both a bare key-id (``mrk-1234...``) and a full ARN
    (``arn:aws:kms:...:key/mrk-1234...``) are recognised.
    """
    return MRK_KEY_ID_PREFIX in key.lower()


def _acknowledged(intent: Mapping[str, Any]) -> bool:
    """True only when the public-access acknowledgement is explicitly ``True``."""
    return intent.get(PUBLIC_ACCESS_ACK_FIELD) is True


def _truthy(value: Any) -> bool:
    """A standby/replica flag is "requested" when it is a truthy scalar."""
    return bool(value)


def validate_security_invariants(
    intent: Mapping[str, Any],
) -> list[ValidationError]:
    """Run the Layer-2 security-invariant cross-checks over a ``Deployment_Intent``.

    These are the non-negotiable ``Security_Invariant`` cross-checks JSON Schema
    cannot express because they relate fields, conventions, and acknowledgements
    to one another. Each failure names the violated invariant (the ``rule``),
    identifies the offending field, and is tagged with :data:`LAYER_SECURITY`
    so reporting distinguishes it from Layer-1 and the storage arithmetic. This
    function assumes Layer 1 has passed and is invoked from
    :func:`validate_intent` only after :func:`validate_schema` succeeds.

    Rules enforced:

    * **R6.11** — every supplied encryptable-resource key
      (:data:`ENCRYPTABLE_KEY_FIELDS`) must be a customer-managed CMK; an
      AWS-owned/managed default key (``alias/aws/``, ``aws/rds``,
      ``aws/secretsmanager``, ``aws/s3``) is rejected, naming the resource and
      key.
    * **R6.11/R13.14** — the storage CMK (``kms_key_id``) and any supplied BYOK
      key must be a multi-region key (MRK); a non-MRK CMK is rejected with the
      storage-encryption invariant named.
    * **R6.8** — ``publicly_accessible=true`` requires
      ``public_access_acknowledged=true``.
    * **R6.9** — security-group ingress from ``0.0.0.0/0`` (in any of
      :data:`INGRESS_CIDR_FIELDS`) requires ``public_access_acknowledged=true``.
    * **R8.12** — ``engine=db2-ce`` combined with License Manager tracking is a
      conflict (Community Edition does not carry BYOL the way SE/AE do).
    * **R13.13** — a requested cross-region standby/replica with
      ``backup_retention_period=0`` is a conflict.
    * **R7.8** — IBM_Customer_ID and IBM_Site_ID are required for every edition;
      a missing identifier is rejected, naming which one.
    * **R7.9** — a supplied IBM identifier that is empty after trimming or longer
      than 255 characters is rejected as malformed, naming which one.
    * **R14.6** — each mandatory customer tag (``Project``/``Environment``/
      ``Owner``) must be present and non-empty; every missing or empty key is
      reported by name.

    Returns:
        A list of :class:`ValidationError` — empty when every invariant holds.
    """
    errors: list[ValidationError] = []

    # --- R6.11: encryptable-resource keys must be customer-managed CMKs ----
    for field_name, resource in ENCRYPTABLE_KEY_FIELDS.items():
        key = intent.get(field_name)
        if not isinstance(key, str) or not key.strip():
            # Absence/blankness of an *optional* CMK field is governed by the
            # schema's required set, not this invariant; only validate supplied
            # values here. (kms_key_id presence is a Layer-1 required field.)
            continue
        if _is_aws_owned_key(key):
            errors.append(
                ValidationError(
                    field=field_name,
                    rule="cmk_not_aws_owned",
                    message=(
                        f"{resource} must use a customer-managed CMK; the "
                        f"supplied key '{key}' is an AWS-owned/managed default "
                        f"key, which is not permitted for any encryptable "
                        f"resource (R6.11)."
                    ),
                    layer=LAYER_SECURITY,
                )
            )

    # --- R6.11 / R13.14: storage CMK (and BYOK) must be an MRK -------------
    storage_key = intent.get("kms_key_id")
    if (
        isinstance(storage_key, str)
        and storage_key.strip()
        and not _is_aws_owned_key(storage_key)
        and not _is_mrk(storage_key)
    ):
        errors.append(
            ValidationError(
                field="kms_key_id",
                rule="byok_key_not_mrk",
                message=(
                    f"the storage-encryption CMK '{storage_key}' is not a "
                    f"multi-region key (MRK); the storage-encryption "
                    f"Security_Invariant requires a customer-managed MRK CMK "
                    f"(R6.11, R13.14)."
                ),
                layer=LAYER_SECURITY,
            )
        )

    # --- R6.8: publicly_accessible=true requires acknowledgement ----------
    if intent.get("publicly_accessible") is True and not _acknowledged(intent):
        errors.append(
            ValidationError(
                field="publicly_accessible",
                rule="public_access_requires_acknowledgement",
                message=(
                    f"publicly_accessible=true requires "
                    f"'{PUBLIC_ACCESS_ACK_FIELD}=true'; without the "
                    f"acknowledgement the non-public-by-default "
                    f"Security_Invariant rejects the intent (R6.8)."
                ),
                layer=LAYER_SECURITY,
            )
        )

    # --- R6.9: 0.0.0.0/0 ingress requires acknowledgement -----------------
    if not _acknowledged(intent):
        for field_name in INGRESS_CIDR_FIELDS:
            cidrs = intent.get(field_name)
            if not isinstance(cidrs, (list, tuple)):
                continue
            if OPEN_INGRESS_CIDR in cidrs:
                errors.append(
                    ValidationError(
                        field=field_name,
                        rule="open_ingress_requires_acknowledgement",
                        message=(
                            f"security-group ingress from "
                            f"'{OPEN_INGRESS_CIDR}' requires "
                            f"'{PUBLIC_ACCESS_ACK_FIELD}=true'; without the "
                            f"acknowledgement the least-privilege "
                            f"Security_Invariant rejects the intent (R6.9)."
                        ),
                        layer=LAYER_SECURITY,
                    )
                )

    # --- R8.12: db2-ce + License Manager tracking is a conflict -----------
    if intent.get("engine") == COMMUNITY_EDITION and _truthy(
        intent.get(LICENSE_MANAGER_FIELD)
    ):
        errors.append(
            ValidationError(
                field=LICENSE_MANAGER_FIELD,
                rule="ce_license_manager_conflict",
                message=(
                    f"engine '{COMMUNITY_EDITION}' cannot be combined with "
                    f"License Manager tracking: Community Edition does not "
                    f"carry BYOL the way db2-se/db2-ae do (R8.12)."
                ),
                layer=LAYER_SECURITY,
            )
        )

    # --- R13.13: standby/replica + backup_retention_period=0 conflict ------
    standby_field = next(
        (f for f in STANDBY_REQUEST_FIELDS if _truthy(intent.get(f))),
        None,
    )
    if standby_field is not None and intent.get("backup_retention_period") == 0:
        errors.append(
            ValidationError(
                field=standby_field,
                rule="standby_requires_backups",
                message=(
                    f"a cross-region standby/replica ('{standby_field}') "
                    f"requires automated backups, but "
                    f"backup_retention_period=0 disables them; resolve the "
                    f"conflict before rendering (R13.13)."
                ),
                layer=LAYER_SECURITY,
            )
        )

    # --- R7.8 / R7.9: IBM identifiers present and well-formed for all editions.
    # Each ID may be supplied as a literal (ibm_customer_id / ibm_site_id) OR as
    # an SSM parameter name (ibm_customer_id_ssm / ibm_site_id_ssm) so the value
    # can be kept out of the deployment repo. Exactly one form must be present.
    for field_name in IBM_ID_FIELDS:
        value = intent.get(field_name)
        ssm_field = f"{field_name}_ssm"
        ssm_value = intent.get(ssm_field)
        has_literal = isinstance(value, str) and value.strip() != ""
        has_ssm = isinstance(ssm_value, str) and ssm_value.strip() != ""

        if not has_literal and not has_ssm:
            errors.append(
                ValidationError(
                    field=field_name,
                    rule="ibm_identifier_required",
                    message=(
                        f"{field_name} is required for every Db2 edition "
                        f"(db2-ce, db2-se, db2-ae): supply either {field_name} "
                        f"(literal) or {ssm_field} (an SSM parameter name) (R7.8)."
                    ),
                    layer=LAYER_SECURITY,
                )
            )
            continue
        if has_literal and has_ssm:
            errors.append(
                ValidationError(
                    field=field_name,
                    rule="ibm_identifier_conflict",
                    message=(
                        f"provide exactly one of {field_name} or {ssm_field}, "
                        f"not both (R7.8)."
                    ),
                    layer=LAYER_SECURITY,
                )
            )
            continue
        # Length sanity applies to the literal value (and the SSM name); both
        # must be non-empty after trimming and within the max length (R7.9).
        present_field, present_value = (
            (field_name, value) if has_literal else (ssm_field, ssm_value)
        )
        if len(present_value.strip()) > IBM_ID_MAX_LENGTH:
            errors.append(
                ValidationError(
                    field=present_field,
                    rule="ibm_identifier_malformed",
                    message=(
                        f"{present_field} is malformed: it exceeds the maximum "
                        f"of {IBM_ID_MAX_LENGTH} characters (R7.9)."
                    ),
                    layer=LAYER_SECURITY,
                )
            )

    # --- R14.6: mandatory customer tags present and non-empty -------------
    # Each of Project/Environment/Owner must be present in the tags object with
    # a non-empty value; every missing/empty key is reported by name (R14.6)
    # before rendering can proceed.
    tags = intent.get("tags")
    tags_map = tags if isinstance(tags, Mapping) else {}
    for tag_key in MANDATORY_CUSTOMER_TAG_KEYS:
        value = tags_map.get(tag_key)
        if not isinstance(value, str) or not value.strip():
            errors.append(
                ValidationError(
                    field=f"tags.{tag_key}",
                    rule="mandatory_tag_required",
                    message=(
                        f"the mandatory tag '{tag_key}' is missing or empty; "
                        f"each of {', '.join(MANDATORY_CUSTOMER_TAG_KEYS)} must "
                        f"be present with a non-empty value (R14.6)."
                    ),
                    layer=LAYER_SECURITY,
                )
            )

    return errors


# ---------------------------------------------------------------------------
# Top-level validator (Layer 1 today; Layers 1+2 once 5.2/5.3 land)
# ---------------------------------------------------------------------------


def validate_intent(
    intent: Mapping[str, Any],
    *,
    schema: Union[Mapping[str, Any], None] = None,
    schema_path: Union[str, Path, None] = None,
) -> ValidationResult:
    """Validate a complete ``Deployment_Intent`` and return all failures.

    This is the single entry point the pipeline calls before rendering. It runs
    Layer-1 JSON Schema validation now (R4.2). Tasks 5.2 (cross-field
    arithmetic) and 5.3 (security-invariant cross-checks) extend this function
    to also run their Layer-2 rules and append their findings to the same
    :class:`ValidationResult`.

    Layer 1 is run first; if it fails, the intent is structurally unsound and
    the Layer-2 arithmetic/security rules (which assume well-typed fields)
    are skipped to avoid spurious type errors — every Layer-1 failure is still
    reported in full. When Layer 1 passes, Layer-2 rules run and accumulate into
    the same result.

    Callers MUST check ``result.ok`` and halt before producing any Terraform
    artifact when it is ``False`` (R4.4).
    """
    result = validate_schema(intent, schema=schema, schema_path=schema_path)

    if not result.ok:
        # Structural failures make field-arithmetic checks unreliable; report
        # the Layer-1 findings and stop short of Layer 2. (Tasks 5.2/5.3 run
        # their rules in the branch below once Layer 1 is clean.)
        return result

    # --- Layer 2 hook (tasks 5.2 / 5.3) -----------------------------------
    # Layer 1 passed, so the referenced fields are present and well-typed and
    # the cross-field arithmetic rules can run safely. 5.2 appends the storage
    # arithmetic findings here; 5.3 will add the security-invariant cross-checks
    # alongside. Both append to `result` so every failure across both layers is
    # reported together and a single `result.ok` gates rendering (R4.4).
    result.extend(validate_storage_arithmetic(intent))  # 5.2 (R19)
    result.extend(validate_security_invariants(intent))  # 5.3 (R6/R7/R8/R13)

    return result
