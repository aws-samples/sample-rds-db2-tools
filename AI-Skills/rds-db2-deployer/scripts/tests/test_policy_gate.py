"""Unit tests for the Policy_Gate policy-as-code checks (task 11.1, R12.3).

These cover the five discrete pass/fail checks the gate runs over the rendered
Terraform (and over a parsed ``terraform plan`` text), with no real Terraform or
AWS involved:

1. ``mrk_cmk_encryption``   — R6.1
2. ``db2comm_ssl``          — R6.2
3. ``non_public_absent_ack``— R6.3
4. ``mandatory_tags``       — R14
5. ``ibm_ids_present``      — R7/R8

Each check is asserted to PASS for a compliant rendered intent and FAIL when its
specific invariant is violated. The ``terraform plan`` parser is asserted to
extract the expected attribute/tag/parameter values and counts from sample plan
text.
"""

from __future__ import annotations

import copy

import pytest

from scripts.policy_gate import (
    CHECK_DB2COMM_SSL,
    CHECK_IBM_IDS,
    CHECK_MANDATORY_TAGS,
    CHECK_MRK_CMK,
    CHECK_NON_PUBLIC,
    evaluate_policies,
    parse_terraform_plan,
)
from scripts.render_terraform import render_terraform


# ---------------------------------------------------------------------------
# Compliant intents (mirrors the render tests' fixtures)
# ---------------------------------------------------------------------------


def _reuse_intent() -> dict:
    """A prod intent supplying an existing (MRK) CMK, subnet group, param group,
    monitoring role, IBM IDs, and the three customer tags."""
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
        "db_subnet_group_name": "rds-db2-prod-subnets",
        "db_parameter_group_name": "",
        "monitoring_interval": 15,
        "monitoring_role_arn": "arn:aws:iam::111122223333:role/rds-db2-monitoring",
        "enable_cloudwatch_logs_exports": ["diag.log", "notify.log"],
        "deletion_protection": True,
        "ibm_customer_id": "1234567",
        "ibm_site_id": "1234567890",
        "db_instance_identifier": "",
        "tags": {"Project": "ACME", "Environment": "prod", "Owner": "db-team"},
        "ingress_cidrs": ["10.0.0.0/16"],
        "db_parameter_group_family": "db2-se-12.1",
        "_provenance": {"engine": "user_provided"},
    }


def _create_intent() -> dict:
    """A sandbox intent supplying NO reusable resources -> the composer creates
    them, including a multi-region CMK via 3-kms."""
    intent = _reuse_intent()
    intent["deployment_tier"] = "sandbox"
    intent["db_subnet_group_name"] = ""
    intent["db_parameter_group_name"] = ""
    intent["kms_key_id"] = ""
    intent.pop("monitoring_role_arn", None)
    intent["tags"]["Environment"] = "sandbox"
    return intent


@pytest.fixture
def reuse_intent() -> dict:
    return _reuse_intent()


@pytest.fixture
def create_intent() -> dict:
    return _create_intent()


@pytest.fixture
def reuse_render(reuse_intent, terraform_modules_root):
    return render_terraform(reuse_intent, modules_root=terraform_modules_root)


@pytest.fixture
def create_render(create_intent, terraform_modules_root):
    return render_terraform(create_intent, modules_root=terraform_modules_root)


# ---------------------------------------------------------------------------
# The whole gate passes for a compliant rendered intent.
# ---------------------------------------------------------------------------


def test_gate_passes_for_compliant_reuse_intent(reuse_render):
    report = evaluate_policies(reuse_render)
    assert report.ok, [f"{r.check}: {r.message}" for r in report.failures]


def test_gate_passes_for_compliant_create_intent(create_render):
    report = evaluate_policies(create_render)
    assert report.ok, [f"{r.check}: {r.message}" for r in report.failures]


def test_gate_accepts_plain_files_mapping(reuse_render):
    """evaluate_policies works on a plain {path: content} mapping too."""
    report = evaluate_policies(dict(reuse_render.files))
    assert report.ok, [f"{r.check}: {r.message}" for r in report.failures]


# ---------------------------------------------------------------------------
# 1. mrk_cmk_encryption (R6.1)
# ---------------------------------------------------------------------------


def test_mrk_cmk_passes_with_supplied_mrk_key(reuse_render):
    r = evaluate_policies(reuse_render).by_name(CHECK_MRK_CMK)
    assert r.passed, r.message


def test_mrk_cmk_passes_on_create_path_multi_region_key(create_render):
    """When no CMK is supplied, the 3-kms create path must set
    multi_region_key=true (R6.1)."""
    r = evaluate_policies(create_render).by_name(CHECK_MRK_CMK)
    assert r.passed, r.message


def test_mrk_cmk_fails_when_storage_not_encrypted():
    files = {
        "5-rds/terraform.tfvars": (
            "storage_encrypted = false\n"
            'kms_key_arn = "arn:aws:kms:us-east-1:111122223333:key/mrk-1234"\n'
        ),
    }
    r = evaluate_policies(files).by_name(CHECK_MRK_CMK)
    assert not r.passed
    assert "storage_encrypted" in r.message


def test_mrk_cmk_fails_for_aws_owned_key():
    files = {
        "5-rds/terraform.tfvars": (
            "storage_encrypted = true\n"
            'kms_key_arn = "alias/aws/rds"\n'
        ),
    }
    r = evaluate_policies(files).by_name(CHECK_MRK_CMK)
    assert not r.passed
    assert "AWS-owned" in r.message or "aws" in r.message.lower()


def test_mrk_cmk_fails_for_non_mrk_cmk():
    files = {
        "5-rds/terraform.tfvars": (
            "storage_encrypted = true\n"
            'kms_key_arn = "arn:aws:kms:us-east-1:111122223333:key/1234abcd-non-mrk"\n'
        ),
    }
    r = evaluate_policies(files).by_name(CHECK_MRK_CMK)
    assert not r.passed
    assert "MRK" in r.message


# ---------------------------------------------------------------------------
# 2. db2comm_ssl (R6.2)
# ---------------------------------------------------------------------------


def test_db2comm_ssl_passes_from_security_supplement(reuse_render):
    r = evaluate_policies(reuse_render).by_name(CHECK_DB2COMM_SSL)
    assert r.passed, r.message


def test_db2comm_ssl_fails_when_absent():
    files = {"5-rds/terraform.tfvars": "storage_encrypted = true\n"}
    r = evaluate_policies(files).by_name(CHECK_DB2COMM_SSL)
    assert not r.passed
    assert "DB2COMM=SSL" in r.message
    assert "ssl_svcename=50443" in r.message


def test_db2comm_ssl_fails_on_wrong_port():
    files = {
        "security.tf": "# DB2COMM=SSL and ssl_svcename=50000\n",
    }
    r = evaluate_policies(files).by_name(CHECK_DB2COMM_SSL)
    assert not r.passed
    assert "ssl_svcename=50443" in r.message


# ---------------------------------------------------------------------------
# 3. non_public_absent_ack (R6.3)
# ---------------------------------------------------------------------------


def test_non_public_passes_when_false(reuse_render):
    r = evaluate_policies(reuse_render).by_name(CHECK_NON_PUBLIC)
    assert r.passed, r.message


def test_non_public_fails_when_public_without_ack():
    files = {"5-rds/terraform.tfvars": "publicly_accessible = true\n"}
    r = evaluate_policies(files).by_name(CHECK_NON_PUBLIC)
    assert not r.passed
    assert "acknowledg" in r.message.lower()


def test_non_public_passes_when_public_with_ack():
    files = {"5-rds/terraform.tfvars": "publicly_accessible = true\n"}
    r = evaluate_policies(
        files, public_access_acknowledged=True
    ).by_name(CHECK_NON_PUBLIC)
    assert r.passed, r.message


# ---------------------------------------------------------------------------
# 4. mandatory_tags (R14)
# ---------------------------------------------------------------------------


def test_mandatory_tags_pass_for_compliant_render(reuse_render):
    r = evaluate_policies(reuse_render).by_name(CHECK_MANDATORY_TAGS)
    assert r.passed, r.message


def test_mandatory_tags_fail_when_one_missing():
    # 5-rds tfvars carries created_by/generation_model/tag/owner but no
    # environment -> Environment mandatory tag missing.
    files = {
        "5-rds/terraform.tfvars": (
            'created_by = "rds-db2-skill"\n'
            'generation_model = "kiro-spec-composer"\n'
            'tag = "ACME"\n'
            'owner = "db-team"\n'
        ),
    }
    r = evaluate_policies(files).by_name(CHECK_MANDATORY_TAGS)
    assert not r.passed
    assert "Environment" in r.message


def test_mandatory_tags_fail_when_empty():
    files = {
        "5-rds/terraform.tfvars": (
            'created_by = "rds-db2-skill"\n'
            'generation_model = "kiro-spec-composer"\n'
            'tag = "ACME"\n'
            'owner = "db-team"\n'
            'environment = ""\n'
        ),
    }
    r = evaluate_policies(files).by_name(CHECK_MANDATORY_TAGS)
    assert not r.passed
    assert "Environment" in r.message


# ---------------------------------------------------------------------------
# 5. ibm_ids_present (R7/R8)
# ---------------------------------------------------------------------------


def test_ibm_ids_pass_for_compliant_render(create_render):
    # create_intent puts the param group on the create path, so its tfvars carry
    # ibm_customer_id / ibm_site_id.
    r = evaluate_policies(create_render).by_name(CHECK_IBM_IDS)
    assert r.passed, r.message


def test_ibm_ids_fail_when_missing():
    files = {
        "4-parameter-group/terraform.tfvars": (
            'engine_edition = "se"\n'
            'engine_major_version = "12.1"\n'
        ),
    }
    r = evaluate_policies(files).by_name(CHECK_IBM_IDS)
    assert not r.passed
    assert "ibm_customer_id" in r.message
    assert "ibm_site_id" in r.message


def test_ibm_ids_fail_when_only_customer_present():
    files = {
        "4-parameter-group/terraform.tfvars": (
            'ibm_customer_id = "1234567"\n'
        ),
    }
    r = evaluate_policies(files).by_name(CHECK_IBM_IDS)
    assert not r.passed
    # The "missing" clause names only ibm_site_id (customer id was supplied).
    missing_clause = r.message.split(";", 1)[0]
    assert "ibm_site_id" in missing_clause
    assert "ibm_customer_id" not in missing_clause


# ---------------------------------------------------------------------------
# terraform plan parser
# ---------------------------------------------------------------------------


SAMPLE_PLAN = """
Terraform used the selected providers to generate the following execution plan.

  # module.rds.aws_db_instance.this will be created
  + resource "aws_db_instance" "this" {
      + identifier            = "rds-db2-se-121-xl"
      + storage_encrypted     = true
      + kms_key_id            = "arn:aws:kms:us-east-1:111122223333:key/mrk-1234"
      + publicly_accessible   = false
      + multi_az              = true
      + tags                  = {
          + "created_by"       = "rds-db2-skill"
          + "generation_model" = "kiro-spec-composer"
          + "Project"          = "ACME"
          + "Environment"      = "prod"
          + "Owner"            = "db-team"
        }
    }

  # module.parameter_group.aws_db_parameter_group.this will be created
  + resource "aws_db_parameter_group" "this" {
      + name = "rds-db2-se-121"
      + parameter {
          + name  = "DB2COMM"
          + value = "SSL"
        }
      + parameter {
          + name  = "ssl_svcename"
          + value = "50443"
        }
      + parameter {
          + name  = "rds.ibm_customer_id"
          + value = "1234567"
        }
      + parameter {
          + name  = "rds.ibm_site_id"
          + value = "1234567890"
        }
    }

Plan: 2 to add, 0 to change, 0 to destroy.
"""


def test_plan_parser_extracts_counts():
    summary = parse_terraform_plan(SAMPLE_PLAN)
    assert summary.add == 2
    assert summary.change == 0
    assert summary.destroy == 0


def test_plan_parser_extracts_attributes():
    summary = parse_terraform_plan(SAMPLE_PLAN)
    assert summary.attribute("storage_encrypted") == "true"
    assert summary.attribute("publicly_accessible") == "false"
    assert (
        summary.attribute("kms_key_id")
        == "arn:aws:kms:us-east-1:111122223333:key/mrk-1234"
    )


def test_plan_parser_extracts_tags():
    summary = parse_terraform_plan(SAMPLE_PLAN)
    assert summary.tags["created_by"] == "rds-db2-skill"
    assert summary.tags["Project"] == "ACME"
    assert summary.tags["Environment"] == "prod"
    assert summary.tags["Owner"] == "db-team"


def test_plan_parser_extracts_parameters():
    summary = parse_terraform_plan(SAMPLE_PLAN)
    assert summary.parameters["DB2COMM"] == "SSL"
    assert summary.parameters["ssl_svcename"] == "50443"
    assert summary.parameters["rds.ibm_customer_id"] == "1234567"
    assert summary.parameters["rds.ibm_site_id"] == "1234567890"


def test_plan_parser_two_resources():
    summary = parse_terraform_plan(SAMPLE_PLAN)
    assert len(summary.resources) == 2
    types = {r["type"] for r in summary.resources}
    assert types == {"aws_db_instance", "aws_db_parameter_group"}


# ---------------------------------------------------------------------------
# The gate can run entirely off a parsed plan (no rendered files).
# ---------------------------------------------------------------------------


def test_gate_passes_on_plan_only_evidence():
    report = evaluate_policies({}, plan_output=SAMPLE_PLAN)
    assert report.ok, [f"{r.check}: {r.message}" for r in report.failures]


def test_gate_plan_detects_public_violation():
    bad_plan = SAMPLE_PLAN.replace(
        'publicly_accessible   = false', 'publicly_accessible   = true'
    )
    report = evaluate_policies({}, plan_output=bad_plan)
    r = report.by_name(CHECK_NON_PUBLIC)
    assert not r.passed


def test_gate_plan_detects_aws_owned_key():
    bad_plan = SAMPLE_PLAN.replace(
        "arn:aws:kms:us-east-1:111122223333:key/mrk-1234", "alias/aws/rds"
    )
    report = evaluate_policies({}, plan_output=bad_plan)
    r = report.by_name(CHECK_MRK_CMK)
    assert not r.passed
