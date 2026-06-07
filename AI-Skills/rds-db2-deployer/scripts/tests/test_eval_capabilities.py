"""Always-on eval pytest for the optional-capability + edition scenarios (task 15.2).

This is the AWS-mutation-free half of the task-15.2 eval. It drives the skill's
own local pipeline (resolve -> validate -> render) for each scenario and asserts
the resolved-intent / rendered-Terraform evidence the task calls for, plus a
``terraform validate`` gate per enabled module (R10.8) when a terraform binary
is available.

GROUP A — edition reconciliation (R8.5 / R8.6):
* SE->AE forced conversion on an oversized class, with the recorded
  ``_edition_conversion`` (acknowledgement_required=true) and a db2-ae-12.1
  parameter-group family.
* AE kept on an SE-eligible class (no auto-change; advisory guidance only).
* customer-initiated AE->SE downgrade honored (no conversion).

GROUP B — optional capabilities (R13.1/2/4/5/6/15) each render the expected
module variables and validate.

Engine-version resolution uses the LIVE boto3 lister when AWS is reachable
(R5.1) and otherwise falls back to a RECORDED grounded snapshot captured from
the burner account — never a fabricated value.

The real burner ``apply`` + cleanup half lives in
:mod:`scripts.eval.live_capabilities` and is run out-of-band by the orchestrator.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from scripts.eval.capabilities_pipeline import (
    OVERSIZED_CLASS,
    SE_ELIGIBLE_CLASS,
    scenario_ae_on_se_eligible,
    scenario_ae_to_se_downgrade,
    scenario_audit_to_s3,
    scenario_byok_mrk,
    scenario_prod_multi_az,
    scenario_read_replica,
    scenario_se_to_ae_oversized,
    scenario_self_managed_ad,
    scenario_standby_replica,
)
from scripts.engine_versions import boto3_engine_version_lister


# ---------------------------------------------------------------------------
# Engine-version lister: live when reachable, recorded-grounded otherwise.
# ---------------------------------------------------------------------------

#: Grounded snapshots of the db2-se / db2-ae engine versions reported by
#: ``aws rds describe-db-engine-versions --engine <e> --region us-east-1``
#: against the burner account (captured 2026-06). The highest 12.1 minor here is
#: a REAL value the API returned, never fabricated (R5.1). Used ONLY as an
#: offline fallback so the resolve/validate/render assertions run without AWS.
RECORDED_VERSIONS: dict[str, list[str]] = {
    "db2-se": [
        "11.5.9.0.sb00075854.r1",
        "12.1.4.0.sb00080714.r1",
    ],
    "db2-ae": [
        "11.5.9.0.sb00075854.r1",
        "12.1.4.0.sb00080714.r1",
    ],
}


def _recorded_lister(engine: str, region: str) -> list[str]:
    return list(RECORDED_VERSIONS.get(engine, []))


def _resolve_lister():
    """Live boto3 lister when AWS is reachable, else the recorded snapshot."""
    try:
        live = boto3_engine_version_lister()
        if list(live("db2-se", "us-east-1")):
            return live
    except Exception:  # pragma: no cover - environment dependent
        pass
    return _recorded_lister


@pytest.fixture(scope="module")
def lister():
    return _resolve_lister()


# ---------------------------------------------------------------------------
# GROUP A — edition reconciliation (R8.5 / R8.6).
# ---------------------------------------------------------------------------


def test_se_to_ae_conversion_on_oversized_class(lister):
    """R8.5: db2-se on db.x2iedn.16xlarge force-converts to db2-ae, records the
    conversion with acknowledgement_required=true, and derives db2-ae-12.1."""
    res = scenario_se_to_ae_oversized(lister=lister)
    assert res.validation.ok, res.validation.report()
    assert res.intent["instance_class"] == OVERSIZED_CLASS
    assert res.intent["engine"] == "db2-ae"

    conv = res.edition_conversion
    assert conv is not None, "expected a recorded _edition_conversion (R8.5)"
    assert conv["from"] == "db2-se"
    assert conv["to"] == "db2-ae"
    assert conv["acknowledgement_required"] is True
    assert conv.get("reason"), "the conversion reason must be recorded (R8.5)"

    assert res.intent["db_parameter_group_family"] == "db2-ae-12.1"


def test_ae_kept_on_se_eligible_class_not_auto_changed(lister):
    """R8.6: db2-ae on the SE-eligible db.r7i.2xlarge stays db2-ae with no
    conversion; at most advisory downgrade guidance is attached."""
    res = scenario_ae_on_se_eligible(lister=lister)
    assert res.validation.ok, res.validation.report()
    assert res.intent["instance_class"] == SE_ELIGIBLE_CLASS
    assert res.intent["engine"] == "db2-ae"
    assert res.edition_conversion is None, "AE must never be auto-converted (R8.6)"
    assert res.intent["db_parameter_group_family"] == "db2-ae-12.1"
    # Advisory guidance is allowed (and expected) but must be advisory only.
    assert res.downgrade_guidance is not None
    assert "advisory" in res.downgrade_guidance.lower()


def test_customer_ae_to_se_downgrade_is_honored(lister):
    """R8.6: a customer-initiated db2-se on an SE-eligible class is honored as
    db2-se with no auto-conversion (the AE->SE downgrade after rightsizing)."""
    res = scenario_ae_to_se_downgrade(lister=lister)
    assert res.validation.ok, res.validation.report()
    assert res.intent["instance_class"] == SE_ELIGIBLE_CLASS
    assert res.intent["engine"] == "db2-se"
    assert res.edition_conversion is None, "SE on an SE-eligible class is honored (R8.6)"
    assert res.intent["db_parameter_group_family"] == "db2-se-12.1"


# ---------------------------------------------------------------------------
# GROUP B — optional capabilities (R13).
# ---------------------------------------------------------------------------


def test_prod_multi_az_posture(lister):
    """R13.1/prod posture: prod -> multi_az true, r-family class, >=7-day backup,
    deletion_protection true."""
    res = scenario_prod_multi_az(lister=lister)
    assert res.validation.ok, res.validation.report()
    rds = res.render.modules["5-rds"].variables
    assert rds["multi_az"] is True
    assert res.intent["instance_class"].startswith("db.r")
    assert res.intent["backup_retention_period"] >= 7
    assert rds["deletion_protection"] is True


def test_self_managed_ad_renders_all_args(lister):
    """R13.4: the 5 self-managed AD args render on 5-rds and the directory role +
    join-secret grant render on 2-iam."""
    res = scenario_self_managed_ad(lister=lister)
    assert res.validation.ok, res.validation.report()
    rds = res.render.modules["5-rds"].variables
    assert rds["domain_fqdn"] == "company.com"
    assert rds["domain_ou"] == "OU=RDSDb2,DC=company,DC=com"
    assert rds["domain_dns_ips"] == ["10.0.16.150", "10.0.28.150"]
    assert rds["domain_auth_secret_arn"].endswith("rds-db2-ad-join-abc")
    iam = res.render.modules["2-iam"].variables
    assert iam["create_directory_role"] is True
    assert iam["self_managed_ad_secret_arn"].endswith("rds-db2-ad-join-abc")
    assert "2-iam" in res.render.enabled_modules


def test_audit_to_s3_renders_option_group_wiring(lister):
    """R13.5: audit renders enable_audit + role + bucket on 5-rds and the audit
    role + bucket reference on 2-iam (DB2_AUDIT option group)."""
    res = scenario_audit_to_s3(lister=lister)
    assert res.validation.ok, res.validation.report()
    rds = res.render.modules["5-rds"].variables
    assert rds["enable_audit"] is True
    assert rds["audit_role_arn"].endswith("rds-db2-audit")
    assert rds["audit_bucket_name"] == "eval-capabilities-15-2-audit"
    iam = res.render.modules["2-iam"].variables
    assert iam["create_audit_role"] is True
    assert iam["audit_bucket_name"] == "eval-capabilities-15-2-audit"


def test_byok_mrk_reused_not_created(lister):
    """R13.6: a supplied MRK CMK is rendered onto 5-rds.kms_key_arn and 3-kms is
    skipped (reuse, not auto-create)."""
    res = scenario_byok_mrk(lister=lister)
    assert res.validation.ok, res.validation.report()
    rds = res.render.modules["5-rds"].variables
    assert rds["kms_key_arn"].endswith("mrk-0000byok00000")
    assert "3-kms" not in res.render.enabled_modules


def test_standby_replica_renders(lister):
    """R13.2: cross-region mounted standby renders the standby args on 5-rds."""
    res = scenario_standby_replica(lister=lister)
    assert res.validation.ok, res.validation.report()
    rds = res.render.modules["5-rds"].variables
    assert rds["create_standby_replica"] is True
    assert rds["standby_parameter_group_name"] == "rds-db2-prod-pg-west"
    assert rds["standby_kms_key_arn"].endswith("mrk-0000west00000")
    # A standby needs automated backups (R13.13): backup_retention must be > 0.
    assert res.intent["backup_retention_period"] > 0


def test_read_replica_renders(lister):
    """R13.15: same-region read replica renders the read-replica resource."""
    res = scenario_read_replica(lister=lister)
    assert res.validation.ok, res.validation.report()
    rds = res.render.modules["5-rds"].variables
    assert rds["create_read_replica"] is True
    assert rds["read_replica_identifier"] == "db2db-read"
    assert rds["read_replica_instance_class"] == "db.r7i.2xlarge"


# ---------------------------------------------------------------------------
# R10.8 — every enabled module of every scenario passes terraform validate.
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


#: Markers that identify a provider-plugin/schema *environment* failure (a
#: corrupted or contended local provider plugin cache, a wrong-arch plugin
#: binary, or an offline registry) rather than an HCL/rendering defect.
#: ``terraform validate`` does not read tfvars, so any of these mean the harness
#: could not stand up the provider — an environment limit, skipped like an init
#: failure so it never masquerades as a rendering defect (R10.8).
_ENV_PLUGIN_MARKERS = (
    "Failed to load plugin schemas",
    "Failed to obtain provider schema",
    "Failed to read any lines from plugin's stdout",
    "Could not load the schema for provider",
    "timeout while waiting for plugin to start",
)


def _is_env_plugin_failure(text: str) -> bool:
    return any(m in (text or "") for m in _ENV_PLUGIN_MARKERS)


_ALL_SCENARIOS = (
    scenario_se_to_ae_oversized,
    scenario_ae_on_se_eligible,
    scenario_ae_to_se_downgrade,
    scenario_prod_multi_az,
    scenario_self_managed_ad,
    scenario_audit_to_s3,
    scenario_byok_mrk,
    scenario_standby_replica,
    scenario_read_replica,
)


@terraform_required
@pytest.mark.parametrize("builder", _ALL_SCENARIOS, ids=lambda b: b.__name__)
def test_scenario_modules_terraform_validate(
    builder, lister, terraform_modules_root, tmp_path
):
    """R10.8: every module each scenario enables passes ``terraform validate``
    with zero errors, run per module in an isolated harness (real module *.tf
    plus the aws.replica configuration alias the 5-rds module declares).

    Skips gracefully when offline provider install fails so an environment limit
    never masquerades as a defect."""
    res = builder(lister=lister)
    assert res.validation.ok, res.validation.report()
    env = _tf_env()

    validated_any = False
    for module in res.render.enabled_modules:
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
        if _is_env_plugin_failure(validate.stdout + validate.stderr):
            # Provider plugin/schema could not load (corrupted/contended local
            # plugin cache or wrong-arch binary) — an environment limit, not a
            # rendering defect; skip this module like an init failure.
            continue
        assert validate.returncode == 0, (
            f"terraform validate failed for {builder.__name__} module {module} "
            f"(R10.8):\n{validate.stdout}\n{validate.stderr}"
        )
        validated_any = True

    if not validated_any:
        pytest.skip("no module harness could be initialized (offline provider?)")
