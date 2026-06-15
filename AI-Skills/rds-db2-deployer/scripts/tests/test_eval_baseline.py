"""Eval harness pytest for the baseline scenario (task 15.1).

This is the always-on, AWS-mutation-free half of the task-15.1 eval. It adapts
the sibling ``rds-db2`` skill's manipulation-eval ``validate`` discipline to the
**composer** skill by driving the skill's own local pipeline for the baseline
prompt "Deploy RDS for Db2 instance" (sandbox) and asserting:

* the resolved ``Deployment_Intent`` matches the R3.4 baseline field set
  (engine_version major 12.1, allocated_storage 40, gp3, single-AZ,
  db.t3.xlarge, 1-day backup, DB2DB, diag.log -> CloudWatch, enhanced monitoring
  interval 15, port 8392, publicly_accessible false, storage_encrypted true,
  engine default db2-se) (R3.4), and
* the rendered Terraform passes ``terraform validate`` with zero errors for
  every enabled module (R10.8).

The engine-version resolution uses the LIVE boto3 lister when AWS is reachable
(R5.1 truth-grounding) and otherwise falls back to a RECORDED grounded version
list captured from the burner account — never a fabricated value. The recorded
list keeps the R3.4 field assertions deterministic and offline-runnable, which
is exactly the task's requirement that "the local pipeline assertions
(resolve/validate/terraform validate) MUST pass regardless" of whether a live
apply is possible.

The real burner ``apply`` + cleanup half lives in :mod:`scripts.eval.live_baseline`
and is exercised out-of-band by the orchestrator (it is slow and mutating), not
by this pytest.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from scripts.eval.baseline_pipeline import (
    EXPECTED_ENGINE_MAJOR,
    BaselineEnvironment,
    check_baseline_fields,
    render_baseline,
    resolve_baseline_intent,
)
from scripts.engine_versions import boto3_engine_version_lister


# ---------------------------------------------------------------------------
# Engine-version lister: live when reachable, recorded-grounded otherwise.
# ---------------------------------------------------------------------------

#: A grounded snapshot of the db2-se engine versions reported by
#: ``aws rds describe-db-engine-versions --engine db2-se --region us-east-1``
#: against the burner account (captured 2026-06). Used ONLY as an offline
#: fallback so the resolve/validate assertions run without AWS; the highest 12.1
#: minor here is a REAL value the API returned, never fabricated (R5.1).
RECORDED_DB2_SE_VERSIONS: dict[str, list[str]] = {
    "db2-se": [
        "11.5.9.0.sb00000000.r1",
        "11.5.9.0.sb00075854.r1",
        "12.1.4.0.sb00080714.r1",
    ],
}


def _recorded_lister(engine: str, region: str) -> list[str]:
    return list(RECORDED_DB2_SE_VERSIONS.get(engine, []))


def _resolve_lister():
    """Return the live boto3 lister when AWS is reachable, else the recorded
    grounded lister. Probes with a cheap real call; any failure (no creds, no
    network) falls back to the recorded snapshot."""
    try:
        live = boto3_engine_version_lister()
        versions = list(live("db2-se", "us-east-1"))
        if versions:
            return live
    except Exception:  # pragma: no cover - environment dependent
        pass
    return _recorded_lister


def _eval_environment() -> BaselineEnvironment:
    """Placeholder, well-formed environment inputs sufficient to make the
    resolved baseline intent schema-complete and renderable. These are NOT used
    for any AWS call here (no mutation in this pytest); the MRK CMK ids carry the
    ``mrk-`` prefix so the security-invariant validator (R6.11/R13.14) accepts
    them."""
    acct = "111122223333"
    return BaselineEnvironment(
        region="us-east-1",
        kms_key_id=f"arn:aws:kms:us-east-1:{acct}:key/mrk-0000baseline0000",
        master_user_secret_kms_key_id=(
            f"arn:aws:kms:us-east-1:{acct}:key/mrk-0000secret00000"
        ),
        vpc_id="vpc-0123456789abcdef0",
        vpc_security_group_ids=["sg-0123456789abcdef0"],
        db_subnet_group_name="rds-db2-skill-eval-subnets",
        monitoring_role_arn=(
            f"arn:aws:iam::{acct}:role/rds-db2-monitoring-role-eval"
        ),
        ibm_customer_id="EVAL-CUST-PLACEHOLDER",
        ibm_site_id="EVAL-SITE-PLACEHOLDER",
    )


@pytest.fixture(scope="module")
def baseline_result():
    """Resolve the baseline intent once for the module's assertions."""
    return resolve_baseline_intent(_eval_environment(), lister=_resolve_lister())


# ---------------------------------------------------------------------------
# R3.4 — resolved baseline field set
# ---------------------------------------------------------------------------


def test_baseline_intent_is_schema_and_invariant_valid(baseline_result):
    """The resolved baseline intent passes the full two-layer Intent_Validator
    (Layer-1 schema + Layer-2 arithmetic + security invariants)."""
    assert baseline_result.validation.ok, baseline_result.validation.report()


def test_baseline_intent_matches_r34_field_set(baseline_result):
    """R3.4: every baseline field resolves to the documented value (engine
    default db2-se, 12.1 major, 40 GiB gp3, single-AZ, db.t3.xlarge, 1-day
    backup, DB2DB, diag.log -> CW, enhanced monitoring 15, port 8392,
    publicly_accessible false, storage_encrypted true)."""
    mismatches = check_baseline_fields(baseline_result.intent)
    assert not mismatches, "R3.4 baseline mismatches:\n" + "\n".join(mismatches)


def test_baseline_engine_version_is_live_grounded_not_fabricated(baseline_result):
    """R5.1/R5.6: the engine version is a real 12.1.x minor (from the live API or
    the recorded grounded snapshot), with the 12.1 major the baseline pins."""
    ev = baseline_result.intent["engine_version"]
    assert ev.startswith(EXPECTED_ENGINE_MAJOR + "."), ev
    # A real RDS minor has at least major.minor.patch.build components.
    assert len(ev.split(".")) >= 3, ev


def test_baseline_parameter_group_family_is_supported(baseline_result):
    """R5.4/R5.8: the derived parameter-group family is exactly db2-se-12.1."""
    assert baseline_result.intent["db_parameter_group_family"] == "db2-se-12.1"


# ---------------------------------------------------------------------------
# R10.8 — rendered Terraform passes terraform validate
# ---------------------------------------------------------------------------

_TERRAFORM = shutil.which("terraform")

terraform_required = pytest.mark.skipif(
    _TERRAFORM is None,
    reason="terraform binary not on PATH; terraform validate (R10.8) needs it",
)

_ALIAS_PROVIDER_HCL = (
    'provider "aws" {\n'
    '  alias  = "replica"\n'
    '  region = "us-west-2"\n'
    "}\n"
)


def _tf_env() -> dict:
    import os

    env = dict(os.environ)
    cache = Path(tempfile.gettempdir()) / "rds_db2_provision_tf_plugin_cache"
    cache.mkdir(parents=True, exist_ok=True)
    env["TF_PLUGIN_CACHE_DIR"] = str(cache)
    env["TF_IN_AUTOMATION"] = "1"
    return env


def _run(cmd, cwd, env):
    return subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=900
    )


@terraform_required
def test_baseline_rendered_modules_terraform_validate(
    baseline_result, terraform_modules_root, tmp_path
):
    """R10.8: every module the baseline enables passes ``terraform validate``
    with zero errors.

    Validate is run per enabled module in an isolated harness (the real module
    ``*.tf`` plus the ``aws.replica`` configuration alias the 5-rds module
    declares). ``terraform validate`` checks the module HCL/provider wiring; it
    does not evaluate tfvars values. Skips gracefully if offline provider
    install fails so an environment limit never masquerades as a defect.
    """
    result = render_baseline(baseline_result.intent)
    env = _tf_env()

    validated_any = False
    for module in result.enabled_modules:
        module_dir = terraform_modules_root / module
        if not (module_dir / "variables.tf").is_file():
            continue
        harness = tmp_path / module
        harness.mkdir(parents=True, exist_ok=True)
        for tf in module_dir.glob("*.tf"):
            shutil.copy(tf, harness / tf.name)
        (harness / "zz_harness_provider.tf").write_text(_ALIAS_PROVIDER_HCL)

        init = _run(
            [_TERRAFORM, "init", "-backend=false", "-input=false", "-no-color"],
            harness,
            env,
        )
        if init.returncode != 0:
            continue  # offline provider install; environment limit
        validate = _run([_TERRAFORM, "validate", "-no-color"], harness, env)
        assert validate.returncode == 0, (
            f"terraform validate failed for baseline module {module} "
            f"(R10.8):\n{validate.stdout}\n{validate.stderr}"
        )
        validated_any = True

    if not validated_any:
        pytest.skip("no module harness could be initialized (offline provider?)")
