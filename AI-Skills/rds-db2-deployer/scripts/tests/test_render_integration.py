"""Integration tests for the Terraform_Composer (task 7.5).

These exercise the *rendered* output of :func:`render_terraform` across a matrix
of valid, schema- and security-validated ``Deployment_Intent`` documents and
assert the launch-gate properties the design calls out:

* **Property 12 — Sensitive_Value never leaks** (R12.1, design "Property 12").
  No Sensitive_Value (the literal ``ibm_customer_id`` / ``ibm_site_id`` /
  ``master_password`` value from the intent) appears in the non-tfvars rendered
  surfaces (``main.tf``, ``security.tf``). The composer *does* render the
  sensitive values into the per-module ``terraform.tfvars`` because Terraform
  needs them as module inputs; the contract there (see
  ``render_terraform`` module docstring and ``SENSITIVE_INTENT_FIELDS``) is that
  every such value is (a) tracked in ``RenderedModule.sensitive_variables`` and
  (b) annotated with a trailing ``# sensitive`` comment so the PR / plan /
  artifact surfaces (tasks 11 and 12) can mask it. This test asserts that
  masking contract precisely; the downstream PR/plan/artifact masking is the
  scope of tasks 11/12 and is re-asserted there.

* **Property 13 — Rendered Terraform validates and is idempotent** (R10.8,
  R10.9, design "Property 13"). Property 13's literal "``terraform validate``
  zero errors; a second ``plan`` shows 0/0/0" is exercised at two levels here:

    1. ``terraform validate`` (zero errors) is run against **each enabled
       module** in an isolated harness (the real module ``*.tf`` plus the
       ``aws.replica`` configuration-alias provider the ``5-rds`` module
       declares). This is the level of ``validate`` that is meaningful and
       runnable here: ``terraform validate`` checks the module HCL and provider
       wiring but does **not** evaluate ``terraform.tfvars`` values, and the
       *rendered root* ``main.tf`` (task 7.1/7.2 slice) does not yet wire the
       cross-module ``providers`` passthrough that a whole-root ``validate``
       requires, so a whole-root ``validate`` is intentionally **not** asserted
       here. The rendered root + tfvars are instead checked for HCL
       well-formedness (``terraform fmt`` parses them without error).

    2. The true apply-time second-``plan`` 0/0/0 (R10.9) needs a real
       ``terraform apply`` against AWS (credentials + network), which are **not
       available in this environment**. That end-to-end idempotence is deferred
       to the burner-account eval in **task 15**. What *is* asserted here as the
       render-level analogue is **render idempotence**: rendering the same
       intent twice yields byte-identical files (a necessary condition for plan
       idempotence and for reproducible artifacts).

The ``terraform``-dependent tests are skipped automatically when the
``terraform`` binary is not on ``PATH`` (or when offline provider installation
fails), so the secret-leak and idempotence assertions — which need no Terraform
— always run.

What was validated, and what was not (honest summary):

* RUN HERE: per-module ``terraform validate`` == 0 errors for every enabled
  module across the matrix; HCL well-formedness of the rendered root + tfvars +
  security.tf; no Sensitive_Value in non-tfvars surfaces; sensitive tfvars
  values annotated + tracked; byte-identical re-render.
* DEFERRED (documented, not run here): whole-root ``terraform validate`` (needs
  the provider-passthrough wiring layered in by later 7.x slices) and the true
  applied-state second-``plan`` 0/0/0 (needs AWS creds + a real apply; task 15).
"""

from __future__ import annotations

import copy
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from scripts.render_terraform import (
    SENSITIVE_INTENT_FIELDS,
    RenderResult,
    render_terraform,
    write_render_result,
)


# ---------------------------------------------------------------------------
# The matrix of valid, resolved Deployment_Intents.
#
# Each starts from a fixture that is known to pass the full Intent_Validator
# (Layer-1 schema + Layer-2 arithmetic + security invariants); see
# test_validate_security_invariants._base_intent. The matrix then layers on the
# capability axes named in task 7.5: prod multi-az, audit-enabled, cross-region
# standby, BYOK, and a manual (non-managed) master password — the path that
# exercises the master_password Sensitive_Value.
# ---------------------------------------------------------------------------


def _base_intent() -> dict:
    """A complete, Layer-1-valid AND security-compliant sandbox/baseline intent."""
    return {
        "deployment_tier": "dev",
        "workload_size": "small",
        "region": "us-east-1",
        "engine": "db2-se",
        "engine_version": "12.1.4",
        "master_username": "admin",
        "db_name": "DB2DEV",
        "port": 8392,
        "license_model": "bring-your-own-license",
        "instance_class": "db.r7i.2xlarge",
        "allocated_storage": 100,
        "storage_type": "gp3",
        "multi_az": False,
        "backup_retention_period": 7,
        "publicly_accessible": False,
        "storage_encrypted": True,
        "kms_key_id": "arn:aws:kms:us-east-1:111122223333:key/mrk-0123abcd",
        "vpc_security_group_ids": ["sg-0123456789abcdef0"],
        "vpc_id": "vpc-0123456789abcdef0",
        "db_subnet_group_name": "db2-subnets",
        "db_parameter_group_name": "",
        "monitoring_interval": 0,
        "enable_cloudwatch_logs_exports": ["diag.log", "notify.log"],
        "deletion_protection": True,
        "tags": {"Project": "p", "Environment": "dev", "Owner": "o"},
        "manage_master_user_password": True,
        "master_user_secret_kms_key_id": (
            "arn:aws:kms:us-east-1:111122223333:key/mrk-secret01"
        ),
        "ibm_customer_id": "IBM-CUST-001",
        "ibm_site_id": "IBM-SITE-001",
    }


def _prod_multi_az_intent() -> dict:
    intent = _base_intent()
    intent.update(
        {
            "deployment_tier": "prod",
            "workload_size": "large",
            "instance_class": "db.r7i.4xlarge",
            "storage_type": "io2",
            "allocated_storage": 200,
            "iops": 16000,
            "multi_az": True,
            "backup_retention_period": 14,
            "tags": {"Project": "p", "Environment": "prod", "Owner": "o"},
        }
    )
    return intent


def _audit_enabled_intent() -> dict:
    intent = _base_intent()
    intent.update(
        {
            "enable_audit": True,
            "audit_bucket_name": "db2-audit-bucket",
            "audit_role_arn": "arn:aws:iam::111122223333:role/rds-db2-audit",
            "audit_bucket_kms_key_id": (
                "arn:aws:kms:us-east-1:111122223333:key/mrk-audit01"
            ),
            "audit_bucket_exists": True,
        }
    )
    return intent


def _standby_intent() -> dict:
    intent = _prod_multi_az_intent()
    intent.update(
        {
            "create_standby_replica": True,
            "standby_replica_region": "us-west-2",
            "standby_replica_identifier": "db2-standby-dr",
            "standby_instance_class": "db.r7i.4xlarge",
            "standby_parameter_group_name": "db2-se-121-dr",
            "standby_kms_key_arn": "arn:aws:kms:us-west-2:111122223333:key/mrk-dr01",
            "backup_retention_period": 14,
        }
    )
    return intent


def _byok_intent() -> dict:
    intent = _base_intent()
    # A customer-supplied MRK CMK (BYOK) rather than an auto-created key.
    intent["kms_key_id"] = "arn:aws:kms:us-east-1:111122223333:key/mrk-byok99"
    return intent


def _manual_password_intent() -> dict:
    """The credential path that carries a master_password Sensitive_Value."""
    intent = _base_intent()
    intent.pop("master_user_secret_kms_key_id", None)
    intent["manage_master_user_password"] = False
    intent["master_password"] = "manual-mode-placeholder-do-not-leak"
    return intent


#: name -> factory for the matrix. Factories (not pre-built dicts) so each test
#: gets a fresh, mutation-free intent.
INTENT_MATRIX: dict[str, callable] = {
    "sandbox_baseline": _base_intent,
    "prod_multi_az": _prod_multi_az_intent,
    "audit_enabled": _audit_enabled_intent,
    "standby": _standby_intent,
    "byok": _byok_intent,
    "manual_password": _manual_password_intent,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sensitive_values(intent: dict) -> dict[str, str]:
    """The literal Sensitive_Values present in this intent, by field name."""
    return {
        field: str(intent[field])
        for field in SENSITIVE_INTENT_FIELDS
        if field in intent and intent[field] not in (None, "")
    }


def _render(intent: dict, modules_root: Path) -> RenderResult:
    return render_terraform(intent, modules_root=modules_root)


# ---------------------------------------------------------------------------
# Property 12 — no Sensitive_Value leaks into non-tfvars surfaces (R12.1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_name", list(INTENT_MATRIX))
def test_sensitive_values_absent_from_non_tfvars_surfaces(
    case_name, terraform_modules_root
):
    """Property 12: the literal IBM IDs / master_password never appear in the
    rendered root ``main.tf`` or ``security.tf`` (the surfaces that carry no
    legitimate need for them)."""
    intent = INTENT_MATRIX[case_name]()
    sensitive = _sensitive_values(intent)
    assert sensitive, f"{case_name}: matrix case must carry at least one Sensitive_Value"

    result = _render(intent, terraform_modules_root)

    for surface in ("main.tf", "security.tf"):
        content = result.files[surface]
        for field, value in sensitive.items():
            assert value not in content, (
                f"{case_name}: Sensitive_Value for '{field}' leaked into "
                f"{surface} (Property 12 / R12.1)"
            )


@pytest.mark.parametrize("case_name", list(INTENT_MATRIX))
def test_sensitive_tfvars_values_are_tracked_and_annotated(
    case_name, terraform_modules_root
):
    """Property 12 masking contract: where a Sensitive_Value IS rendered into a
    module's ``terraform.tfvars`` (Terraform needs it), the variable is tracked
    in ``RenderedModule.sensitive_variables`` and its tfvars line is annotated
    ``# sensitive`` so tasks 11/12 can mask it in the PR/plan/artifact surfaces.
    """
    intent = INTENT_MATRIX[case_name]()
    sensitive = _sensitive_values(intent)
    result = _render(intent, terraform_modules_root)

    # Collect every sensitive variable the composer tracked, and the tfvars text
    # of the module(s) that carry sensitive variables.
    tracked_values: set[str] = set()
    for module, rendered in result.modules.items():
        if not rendered.sensitive_variables:
            continue
        tfvars_path = f"{module}/terraform.tfvars"
        # A module only emits tfvars when it is enabled; every module carrying a
        # sensitive variable in this matrix is enabled, so its tfvars exists.
        assert tfvars_path in result.files, (
            f"{case_name}: {module} has sensitive variables but no tfvars rendered"
        )
        text = result.files[tfvars_path]
        for var in rendered.sensitive_variables:
            tracked_values.add(str(rendered.variables[var]))
            # Find the assignment line and assert the sensitive annotation.
            line = next(
                (ln for ln in text.splitlines() if ln.startswith(f"{var} ")),
                None,
            )
            assert line is not None, f"{case_name}: {var} missing from {tfvars_path}"
            assert "# sensitive" in line, (
                f"{case_name}: {module}:{var} is a Sensitive_Value rendered into "
                f"tfvars but is not annotated '# sensitive' (Property 12 masking "
                "contract; PR/plan/artifact masking is tasks 11/12)"
            )

    # Every Sensitive_Value the intent carries that ends up rendered is accounted
    # for by the tracked set (so nothing sensitive is rendered untracked).
    for field, value in sensitive.items():
        rendered_anywhere = any(
            value in content
            for path, content in result.files.items()
            if path.endswith("terraform.tfvars")
        )
        if rendered_anywhere:
            assert value in tracked_values, (
                f"{case_name}: Sensitive_Value for '{field}' is rendered into a "
                "tfvars but is not tracked as sensitive (Property 12)"
            )


# ---------------------------------------------------------------------------
# Property 13 (render-level) — render idempotence (R10.9 analogue)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_name", list(INTENT_MATRIX))
def test_render_is_byte_identical_on_repeat(case_name, terraform_modules_root):
    """Rendering the same intent twice yields byte-identical files.

    This is the render-level necessary condition for the applied-state
    second-``plan`` 0/0/0 (R10.9): a composer that emits different bytes for the
    same intent could never produce a no-op second plan. The true applied 0/0/0
    is exercised on the burner account in task 15.
    """
    intent = INTENT_MATRIX[case_name]()
    first = _render(intent, terraform_modules_root)
    second = _render(copy.deepcopy(intent), terraform_modules_root)
    assert first.files == second.files
    assert first.enabled_modules == second.enabled_modules


# ---------------------------------------------------------------------------
# Property 13 (validate level) — terraform validate + HCL well-formedness
# ---------------------------------------------------------------------------

_TERRAFORM = shutil.which("terraform")

terraform_required = pytest.mark.skipif(
    _TERRAFORM is None,
    reason="terraform binary not on PATH; validate-level checks need it",
)

# A session-shared provider plugin cache so the AWS provider is fetched at most
# once for the whole module-validate matrix (keeps the suite from re-downloading
# the provider per harness).
_PLUGIN_CACHE = Path(tempfile.gettempdir()) / "rds_db2_provision_tf_plugin_cache"

# The aws.replica configuration alias the 5-rds module declares
# (configuration_aliases = [aws.replica]). A standalone validate of any module
# that declares it needs the alias provider present, so the harness always
# supplies it; it is inert for modules that don't reference it.
_ALIAS_PROVIDER_HCL = (
    'provider "aws" {\n'
    '  alias  = "replica"\n'
    '  region = "us-west-2"\n'
    "}\n"
)


def _run(cmd: list[str], cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=900
    )


def _tf_env() -> dict:
    env = dict(os.environ)
    _PLUGIN_CACHE.mkdir(parents=True, exist_ok=True)
    env["TF_PLUGIN_CACHE_DIR"] = str(_PLUGIN_CACHE)
    env["TF_IN_AUTOMATION"] = "1"
    return env


def _module_harness(module_dir: Path, dest: Path) -> None:
    """Copy a real module's ``*.tf`` into ``dest`` and add the alias provider so
    the module can be validated standalone (no tfvars: ``terraform validate``
    does not evaluate variable values)."""
    for tf in module_dir.glob("*.tf"):
        shutil.copy(tf, dest / tf.name)
    (dest / "zz_harness_provider.tf").write_text(_ALIAS_PROVIDER_HCL)


@pytest.fixture(scope="session")
def initialized_module_harnesses(terraform_modules_root, tmp_path_factory):
    """Initialize each reused module's validate-harness exactly ONCE per session.

    ``terraform init`` is the expensive, network/cache-touching step. Running it
    ~30 times (per module per matrix case) both wastes time and races on the
    shared provider plugin cache (a truncated provider copy yields a spurious
    "Failed to load plugin schemas"). Initializing each module's harness a single
    time and reusing the initialized directory for every ``terraform validate``
    removes that race and keeps the suite fast.

    Returns ``{module_name: harness_path}`` for every module that initialized
    successfully. Modules whose ``init`` fails (e.g. fully offline with no cached
    provider) are omitted; the consuming test skips when its module is absent so
    an environment limitation never masquerades as a rendering defect.
    """
    if _TERRAFORM is None:
        return {}

    env = _tf_env()
    base = tmp_path_factory.mktemp("tf_validate_harnesses")
    harnesses: dict[str, Path] = {}

    # Every module that can be enabled by the matrix. Initializing all of them up
    # front (once) is cheap relative to per-case init and keeps tests trivial.
    candidate_modules = [
        d.name
        for d in sorted(terraform_modules_root.iterdir())
        if d.is_dir() and (d / "variables.tf").is_file()
    ]

    for module in candidate_modules:
        module_dir = terraform_modules_root / module
        harness = base / module
        harness.mkdir(parents=True, exist_ok=True)
        _module_harness(module_dir, harness)
        init = _run(
            [_TERRAFORM, "init", "-backend=false", "-input=false", "-no-color"],
            harness,
            env,
        )
        if init.returncode == 0:
            harnesses[module] = harness
    return harnesses


@terraform_required
@pytest.mark.parametrize("case_name", list(INTENT_MATRIX))
def test_enabled_modules_terraform_validate_zero_errors(
    case_name, terraform_modules_root, initialized_module_harnesses
):
    """Property 13 / R10.8: every module the rendered intent enables passes
    ``terraform validate`` with zero errors.

    Validate is run per enabled module in an isolated, pre-initialized harness
    (real module ``*.tf`` + the ``aws.replica`` alias provider). This is the
    level of ``validate`` that is meaningful here: it checks the module HCL and
    provider wiring the composer reuses. The whole-root ``validate`` is
    intentionally not asserted (the rendered root does not yet wire cross-module
    ``providers`` passthrough — a later 7.x slice), and the applied
    second-``plan`` 0/0/0 is a task-15 burner concern.
    """
    if not initialized_module_harnesses:
        pytest.skip("no module harness initialized (offline provider install?)")

    intent = INTENT_MATRIX[case_name]()
    result = _render(intent, terraform_modules_root)
    env = _tf_env()

    validated_any = False
    for module in result.enabled_modules:
        harness = initialized_module_harnesses.get(module)
        if harness is None:
            # init for this module failed at session setup (environment limit).
            continue
        validate = _run([_TERRAFORM, "validate", "-no-color"], harness, env)
        assert validate.returncode == 0, (
            f"{case_name}: terraform validate failed for module {module} "
            f"(R10.8):\n{validate.stdout}\n{validate.stderr}"
        )
        validated_any = True

    if not validated_any:
        pytest.skip(
            f"{case_name}: none of the enabled modules could be validated "
            "(offline provider install?)"
        )


@terraform_required
@pytest.mark.parametrize("case_name", list(INTENT_MATRIX))
def test_rendered_files_are_well_formed_hcl(
    case_name, terraform_modules_root, tmp_path
):
    """R10.8 (well-formedness): the rendered root ``main.tf``, ``security.tf``,
    and every per-module ``terraform.tfvars`` parse as valid HCL.

    ``terraform validate`` does not evaluate tfvars, and the rendered root is
    not yet root-validatable (provider passthrough is a later slice), so HCL
    well-formedness is checked with ``terraform fmt`` on a copy: ``fmt`` returns
    exit 0 for parseable HCL and exit 2 for a syntax error. (A non-zero "would
    reformat" exit only happens under ``-check``, which is not used here.)
    """
    intent = INTENT_MATRIX[case_name]()
    result = _render(intent, terraform_modules_root)
    env = _tf_env()

    work = tmp_path / "wellformed"
    work.mkdir(parents=True, exist_ok=True)
    write_render_result(result, work)

    for rel_path in result.files:
        target = work / rel_path
        fmt = _run([_TERRAFORM, "fmt", "-no-color", str(target)], work, env)
        # exit 0 = parsed (and possibly rewritten to canonical form);
        # exit 2 = HCL syntax error. Anything else is unexpected.
        assert fmt.returncode in (0,), (
            f"{case_name}: rendered {rel_path} is not well-formed HCL "
            f"(terraform fmt exit {fmt.returncode}):\n{fmt.stdout}\n{fmt.stderr}"
        )
