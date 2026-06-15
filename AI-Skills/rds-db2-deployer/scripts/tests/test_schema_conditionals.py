"""Layer-1 JSON Schema conditional-dependency tests (task 2.2).

Covers the R18 / R4.7 / R4.8 / R19 presence-conditionals and single-field
ranges encoded into deployment-intent.schema.json via if/then/else,
dependentRequired, oneOf, and not. Cross-field arithmetic (R19.6/7/8) is the
Layer-2 validator's job and is deliberately NOT asserted here.

Each conditional gets a positive (valid) and negative (invalid) example so a
regression in either direction is caught.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema.validators import Draft202012Validator


@pytest.fixture(scope="module")
def validator(schema_path: Path) -> Draft202012Validator:
    schema = json.loads(schema_path.read_text())
    # Fail loudly if the schema itself is not a valid JSON Schema (R4.1).
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _base_intent() -> dict:
    """A minimal Deployment_Intent that satisfies the always-required set and
    every conditional branch (gp3 < 400 baseline, managed password, single-AZ).
    """
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
        "kms_key_id": "arn:aws:kms:us-east-1:111122223333:key/abc",
        "vpc_security_group_ids": ["sg-0123456789abcdef0"],
        "vpc_id": "vpc-0123456789abcdef0",
        "db_subnet_group_name": "db2-subnets",
        "db_parameter_group_name": "",
        "monitoring_interval": 0,
        "enable_cloudwatch_logs_exports": ["diag.log", "notify.log"],
        "deletion_protection": True,
        "tags": {"Project": "p", "Environment": "dev", "Owner": "o"},
        # Managed-password branch of the R18.5 oneOf.
        "manage_master_user_password": True,
        "master_user_secret_kms_key_id": "arn:aws:kms:us-east-1:111122223333:key/sec",
    }


def _is_valid(validator: Draft202012Validator, doc: dict) -> bool:
    return validator.is_valid(doc)


def test_base_intent_is_valid(validator: Draft202012Validator) -> None:
    errors = sorted(validator.iter_errors(_base_intent()), key=str)
    assert errors == [], [e.message for e in errors]


# --- R18.3 / R19.3: io2 requires iops, forbids storage_throughput, >=100 GiB ---

def test_io2_with_iops_is_valid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update({"storage_type": "io2", "allocated_storage": 200, "iops": 3000})
    assert _is_valid(validator, doc)


def test_io2_without_iops_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update({"storage_type": "io2", "allocated_storage": 200})
    assert not _is_valid(validator, doc)


def test_io2_with_storage_throughput_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update(
        {"storage_type": "io2", "allocated_storage": 200, "iops": 3000, "storage_throughput": 500}
    )
    assert not _is_valid(validator, doc)


def test_io2_below_100_gib_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update({"storage_type": "io2", "allocated_storage": 50, "iops": 3000})
    assert not _is_valid(validator, doc)


# --- R19.2: gp3 minimum 20 GiB ---

def test_gp3_below_20_gib_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update({"storage_type": "gp3", "allocated_storage": 10})
    assert not _is_valid(validator, doc)


# --- R4.7 / R19.4: gp3 < 400 GiB forbids iops and storage_throughput ---

def test_gp3_small_baseline_is_valid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update({"storage_type": "gp3", "allocated_storage": 100})
    assert _is_valid(validator, doc)


def test_gp3_small_with_iops_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update({"storage_type": "gp3", "allocated_storage": 100, "iops": 12000})
    assert not _is_valid(validator, doc)


def test_gp3_small_with_throughput_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update({"storage_type": "gp3", "allocated_storage": 100, "storage_throughput": 500})
    assert not _is_valid(validator, doc)


# --- R4.7 / R18.2 / R19.5: gp3 >= 400 GiB requires both, iops 12000-64000 ---

def test_gp3_large_with_both_is_valid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update(
        {"storage_type": "gp3", "allocated_storage": 500, "iops": 12000, "storage_throughput": 3000}
    )
    assert _is_valid(validator, doc)


def test_gp3_large_missing_throughput_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update({"storage_type": "gp3", "allocated_storage": 500, "iops": 12000})
    assert not _is_valid(validator, doc)


def test_gp3_large_iops_below_range_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update(
        {"storage_type": "gp3", "allocated_storage": 500, "iops": 11999, "storage_throughput": 3000}
    )
    assert not _is_valid(validator, doc)


def test_gp3_large_iops_above_range_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update(
        {"storage_type": "gp3", "allocated_storage": 500, "iops": 64001, "storage_throughput": 3000}
    )
    assert not _is_valid(validator, doc)


# --- R18.4: enhanced monitoring requires monitoring_role_arn ---

def test_monitoring_enabled_with_role_is_valid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update({"monitoring_interval": 60, "monitoring_role_arn": "arn:aws:iam::111122223333:role/mon"})
    assert _is_valid(validator, doc)


def test_monitoring_enabled_without_role_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update({"monitoring_interval": 60})
    assert not _is_valid(validator, doc)


# --- R18.5: managed-vs-manual password oneOf ---

def test_manual_password_branch_is_valid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    del doc["manage_master_user_password"]
    del doc["master_user_secret_kms_key_id"]
    doc["manage_master_user_password"] = False
    doc["master_password"] = "manual-mode-placeholder"
    assert _is_valid(validator, doc)


def test_both_password_modes_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()  # managed branch already set
    doc["master_password"] = "manual-mode-placeholder"
    assert not _is_valid(validator, doc)


def test_neither_password_mode_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    del doc["manage_master_user_password"]
    del doc["master_user_secret_kms_key_id"]
    assert not _is_valid(validator, doc)


def test_managed_without_cmk_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    del doc["master_user_secret_kms_key_id"]
    assert not _is_valid(validator, doc)


# --- R18.6 / R18.7 / R18.8: AWS-managed vs self-managed AD ---

def test_aws_managed_ad_is_valid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update({"domain": "d-9067xxxxxx", "domain_iam_role_name": "rds-directory-role"})
    assert _is_valid(validator, doc)


def test_aws_managed_ad_missing_role_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update({"domain": "d-9067xxxxxx"})
    assert not _is_valid(validator, doc)


def test_self_managed_ad_is_valid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update(
        {
            "domain_fqdn": "company.com",
            "domain_ou": "OU=RDSDb2,DC=company,DC=com",
            "domain_auth_secret_arn": "arn:aws:secretsmanager:us-east-1:111122223333:secret:ad",
            "domain_dns_ips": ["10.0.16.150", "10.0.28.150"],
            "domain_iam_role_name": "rds-directory-role",
        }
    )
    assert _is_valid(validator, doc)


def test_self_managed_ad_partial_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update({"domain_fqdn": "company.com"})  # missing the rest
    assert not _is_valid(validator, doc)


def test_both_ad_modes_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update(
        {
            "domain": "d-9067xxxxxx",
            "domain_iam_role_name": "rds-directory-role",
            "domain_fqdn": "company.com",
            "domain_ou": "OU=RDSDb2,DC=company,DC=com",
            "domain_auth_secret_arn": "arn:aws:secretsmanager:us-east-1:111122223333:secret:ad",
            "domain_dns_ips": ["10.0.16.150"],
        }
    )
    assert not _is_valid(validator, doc)


# --- R18.9 / R18.10: multi_az vs availability_zone (not) + backup >= 1 ---

def test_multi_az_with_pinned_az_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update({"multi_az": True, "availability_zone": "us-east-1a"})
    assert not _is_valid(validator, doc)


def test_single_az_with_pinned_az_is_valid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update({"multi_az": False, "availability_zone": "us-east-1a"})
    assert _is_valid(validator, doc)


def test_multi_az_with_zero_backup_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update({"multi_az": True, "backup_retention_period": 0})
    assert not _is_valid(validator, doc)


def test_multi_az_with_backup_is_valid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update({"multi_az": True, "backup_retention_period": 1})
    assert _is_valid(validator, doc)


# --- R18.11: license_model constrained to bring-your-own-license ---

def test_other_license_model_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc["license_model"] = "license-included"
    assert not _is_valid(validator, doc)


# --- R18.12: enable_cloudwatch_logs_exports supported values only ---

def test_unsupported_log_export_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc["enable_cloudwatch_logs_exports"] = ["audit.log"]
    assert not _is_valid(validator, doc)


# --- R18.13: port range and != 50443 ---

def test_port_equal_ssl_service_port_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc["port"] = 50443
    assert not _is_valid(validator, doc)


def test_port_out_of_range_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc["port"] = 70000
    assert not _is_valid(validator, doc)


def test_port_in_range_is_valid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc["port"] = 8392
    assert _is_valid(validator, doc)


# --- R19.1: allocated_storage < 64000 ---

def test_allocated_storage_at_max_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc.update({"storage_type": "gp3", "allocated_storage": 64000})
    assert not _is_valid(validator, doc)


# --- R18.10: backup_retention_period 0-35 ---

def test_backup_retention_above_max_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc["backup_retention_period"] = 36
    assert not _is_valid(validator, doc)


# --- R20.3: identifier format ---

def test_valid_identifier_is_valid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc["db_instance_identifier"] = "db2-se-r7i-2xl-s-gp3-saz-dev"
    assert _is_valid(validator, doc)


def test_identifier_starting_with_digit_is_invalid(validator: Draft202012Validator) -> None:
    doc = _base_intent()
    doc["db_instance_identifier"] = "1bad-name"
    assert not _is_valid(validator, doc)
