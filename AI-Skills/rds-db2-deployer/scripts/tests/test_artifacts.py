"""Tests for error handling and artifact output (task 12, Requirement 15).

Covers:
* artifacts written on a successful completion (R15.5);
* artifacts written on a halt-on-failure, even before rendering/plan (R15.5);
* no Sensitive_Value (IBM IDs / master password) leaks into any written
  artifact — intent, plan summary, precheck report, or outcome (R15.6);
* the error-path reporting helpers name the right information:
  unresolvable engine version names engine+major and never fabricates (R15.1);
  terraform validate errors are each reported and no PR is opened (R15.2);
  apply failure names the failing module, step, and state location (R15.4).
"""

from __future__ import annotations

import json

import pytest

from scripts.artifacts import (
    INTENT_ARTIFACT,
    OUTCOME_ARTIFACT,
    PLAN_ARTIFACT,
    PRECHECK_ARTIFACT,
    STATUS_COMPLETED,
    STATUS_FAILED,
    report_apply_failure,
    report_engine_version_error,
    report_terraform_validate_errors,
    safe_deployment_dirname,
    write_artifacts,
)
from scripts.engine_versions import EngineVersionResolutionError
from scripts.policy_gate import parse_terraform_plan
from scripts.render_terraform import render_terraform
from scripts.verify import SENSITIVE_MASK
from scripts.vpc_precheck import (
    PrecheckFinding,
    PrecheckReport,
    PrecheckSeverity,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

IBM_CUSTOMER_ID = "1234567"
IBM_SITE_ID = "1234567890"
# Manual master-password MODE fixture. Deliberately an inert, low-entropy,
# obviously-fake token (no real credential, no pw/secret keyword) so secret
# scanners do not flag it. The tests only need a distinctive non-empty value to
# prove the artifact masking scrubs it from every written surface.
MASTER_PASSWORD = "manual-mode-placeholder"


def _intent_with_secrets() -> dict:
    return {
        "schema_version": "1.0",
        "deployment_tier": "prod",
        "workload_size": "large",
        "region": "us-east-1",
        "engine": "db2-se",
        "engine_version": "12.1.4.0",
        "master_username": "admin",
        "manage_master_user_password": True,
        "master_user_secret_kms_key_id": "arn:aws:kms:us-east-1:111122223333:key/mrk-secret",
        "db_name": "DB2DB",
        "port": 8392,
        "license_model": "bring-your-own-license",
        "instance_class": "db.r7i.4xlarge",
        "allocated_storage": 16000,
        "storage_type": "io2",
        "iops": 16000,
        "multi_az": True,
        "backup_retention_period": 7,
        "publicly_accessible": False,
        "storage_encrypted": True,
        "kms_key_id": "arn:aws:kms:us-east-1:111122223333:key/mrk-1234",
        "vpc_security_group_ids": ["sg-0123456789abcdef0"],
        "vpc_id": "vpc-0123456789abcdef0",
        "db_subnet_group_name": "rds-db2-prod-subnets",
        "db_parameter_group_name": "",
        "monitoring_interval": 15,
        "monitoring_role_arn": "arn:aws:iam::111122223333:role/rds-db2-monitoring",
        "enable_cloudwatch_logs_exports": ["diag.log", "notify.log"],
        "deletion_protection": True,
        "ibm_customer_id": IBM_CUSTOMER_ID,
        "ibm_site_id": IBM_SITE_ID,
        "master_password": MASTER_PASSWORD,
        "db_instance_identifier": "",
        "tags": {"Project": "ACME", "Environment": "prod", "Owner": "db-team"},
        "ingress_cidrs": ["10.0.0.0/16"],
        "db_parameter_group_family": "db2-se-12.1",
        "_provenance": {"engine": "user_provided"},
    }


@pytest.fixture
def intent() -> dict:
    return _intent_with_secrets()


SAMPLE_PLAN = """
  # module.rds.aws_db_instance.this will be created
  + resource "aws_db_instance" "this" {
      + storage_encrypted   = true
      + kms_key_id          = "arn:aws:kms:us-east-1:111122223333:key/mrk-1234"
      + publicly_accessible = false
    }
Plan: 1 to add, 0 to change, 0 to destroy.
"""


def _precheck_with_findings() -> PrecheckReport:
    report = PrecheckReport()
    report.add(
        PrecheckFinding(
            name="s3_gateway_endpoint",
            severity=PrecheckSeverity.WARNING,
            message="No S3 gateway endpoint; best practice to add one.",
        )
    )
    return report


def _all_secrets() -> list[str]:
    return [IBM_CUSTOMER_ID, IBM_SITE_ID, MASTER_PASSWORD]


def _assert_no_secret_in_dir(directory) -> None:
    for path in directory.iterdir():
        text = path.read_text()
        for secret in _all_secrets():
            assert secret not in text, f"{secret!r} leaked into {path.name}"


# ---------------------------------------------------------------------------
# 1. Artifacts written on success (R15.5)
# ---------------------------------------------------------------------------


def test_artifacts_written_on_success(tmp_path, intent, terraform_modules_root):
    rendered = render_terraform(intent, modules_root=terraform_modules_root)
    plan = parse_terraform_plan(SAMPLE_PLAN)
    precheck = _precheck_with_findings()

    result = write_artifacts(
        "rds-db2-prod-large",
        intent=intent,
        status=STATUS_COMPLETED,
        plan_summary=plan,
        precheck_report=precheck,
        rendered=rendered,
        base_dir=tmp_path,
    )

    assert result.directory.is_dir()
    for name in (INTENT_ARTIFACT, PLAN_ARTIFACT, PRECHECK_ARTIFACT, OUTCOME_ARTIFACT):
        assert result.files[name].is_file()

    outcome = json.loads(result.files[OUTCOME_ARTIFACT].read_text())
    assert outcome["status"] == STATUS_COMPLETED
    assert outcome["error"] is None


# ---------------------------------------------------------------------------
# 2. Artifacts written on failure, even before rendering/plan (R15.5)
# ---------------------------------------------------------------------------


def test_artifacts_written_on_failure_without_plan(tmp_path, intent):
    # A halt before rendering: no plan summary, no rendered result.
    result = write_artifacts(
        "rds-db2-prod-large",
        intent=intent,
        status=STATUS_FAILED,
        plan_summary=None,
        precheck_report=None,
        rendered=None,
        error="ERROR: engine version unresolvable for db2-se 12.1",
        base_dir=tmp_path,
    )

    assert result.directory.is_dir()
    for name in (INTENT_ARTIFACT, PLAN_ARTIFACT, PRECHECK_ARTIFACT, OUTCOME_ARTIFACT):
        assert result.files[name].is_file()

    outcome = json.loads(result.files[OUTCOME_ARTIFACT].read_text())
    assert outcome["status"] == STATUS_FAILED
    assert "unresolvable" in outcome["error"]
    # The intent is still recorded even though the deployment failed.
    written_intent = json.loads(result.files[INTENT_ARTIFACT].read_text())
    assert written_intent["region"] == "us-east-1"


# ---------------------------------------------------------------------------
# 3. No Sensitive_Value in any written artifact (R15.6)
# ---------------------------------------------------------------------------


def test_no_sensitive_value_in_artifacts_on_success(tmp_path, intent, terraform_modules_root):
    rendered = render_terraform(intent, modules_root=terraform_modules_root)
    result = write_artifacts(
        "rds-db2-prod-large",
        intent=intent,
        status=STATUS_COMPLETED,
        plan_summary=parse_terraform_plan(SAMPLE_PLAN),
        precheck_report=_precheck_with_findings(),
        rendered=rendered,
        base_dir=tmp_path,
    )
    _assert_no_secret_in_dir(result.directory)

    # The masked intent shows the mask token in place of the IBM IDs/password.
    written_intent = json.loads(result.files[INTENT_ARTIFACT].read_text())
    assert written_intent["ibm_customer_id"] == SENSITIVE_MASK
    assert written_intent["ibm_site_id"] == SENSITIVE_MASK
    assert written_intent["master_password"] == SENSITIVE_MASK


def test_no_sensitive_value_when_secret_leaks_into_plan_text(tmp_path, intent):
    # A plan text that accidentally embeds a secret must be masked on write.
    leaky_plan = SAMPLE_PLAN + f"\n# rds.ibm_customer_id = {IBM_CUSTOMER_ID}\n"
    result = write_artifacts(
        "leaky",
        intent=intent,
        status=STATUS_FAILED,
        plan_summary=leaky_plan,
        precheck_report=None,
        error=f"failed with site {IBM_SITE_ID}",
        base_dir=tmp_path,
    )
    _assert_no_secret_in_dir(result.directory)
    # The error report in outcome.json is masked too.
    outcome = json.loads(result.files[OUTCOME_ARTIFACT].read_text())
    assert IBM_SITE_ID not in outcome["error"]
    assert SENSITIVE_MASK in outcome["error"]


def test_sensitive_field_masked_even_when_empty(tmp_path):
    intent = _intent_with_secrets()
    intent["master_password"] = ""  # empty sensitive field still masked by name
    result = write_artifacts(
        "empty-secret",
        intent=intent,
        status=STATUS_COMPLETED,
        base_dir=tmp_path,
    )
    written_intent = json.loads(result.files[INTENT_ARTIFACT].read_text())
    assert written_intent["master_password"] == SENSITIVE_MASK


# ---------------------------------------------------------------------------
# 4. Deployment-name sanitization keeps writes under artifacts/
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("rds-db2-prod", "rds-db2-prod"),
        ("../../etc/passwd", "etc-passwd"),
        ("name with spaces", "name-with-spaces"),
        ("", "deployment"),
        ("..", "deployment"),
        # Separators collapse to a hyphen; the result is a single safe component
        # (no path separators, not a traversal name) even if it embeds "..".
        ("weird/../slashes", "weird-..-slashes"),
    ],
)
def test_safe_deployment_dirname(raw, expected):
    assert safe_deployment_dirname(raw) == expected


def test_traversal_name_stays_within_base(tmp_path, intent):
    result = write_artifacts(
        "../../escape",
        intent=intent,
        status=STATUS_FAILED,
        base_dir=tmp_path,
    )
    # The written directory is a direct child of base_dir, not an escape.
    assert result.directory.parent == tmp_path
    assert tmp_path in result.directory.parents or result.directory.parent == tmp_path


# ---------------------------------------------------------------------------
# 5. Error-path reporting helpers (R15.1, R15.2, R15.4)
# ---------------------------------------------------------------------------


def test_report_engine_version_error_names_engine_and_major_no_fabrication():
    err = EngineVersionResolutionError("db2-se", "12.1", "us-east-1")
    report = report_engine_version_error(err)
    assert "db2-se" in report
    assert "12.1" in report
    assert "us-east-1" in report
    # No fabricated minor version (e.g. a third dotted component) is present.
    assert "12.1.4" not in report
    assert "fabricate" in report.lower()


def test_report_terraform_validate_errors_lists_each_and_no_pr():
    errors = [
        'Error: Unsupported argument on 5-rds/main.tf line 12',
        'Error: Missing required argument "kms_key_arn"',
    ]
    report = report_terraform_validate_errors(errors)
    for err in errors:
        assert err in report
    assert "no pull request" in report.lower()


def test_report_terraform_validate_errors_empty_still_reports_no_pr():
    report = report_terraform_validate_errors([])
    assert "no pull request" in report.lower()


def test_report_apply_failure_names_module_step_and_state():
    report = report_apply_failure(
        failing_module="5-rds",
        failing_step="aws_db_instance.this creation",
        state_location="s3://tf-state-bucket/rds-db2/prod/terraform.tfstate",
    )
    assert "5-rds" in report
    assert "aws_db_instance.this creation" in report
    assert "s3://tf-state-bucket/rds-db2/prod/terraform.tfstate" in report
