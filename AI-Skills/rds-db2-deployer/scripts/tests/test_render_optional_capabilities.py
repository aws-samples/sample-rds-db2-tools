"""Unit tests for the Terraform_Composer optional-capability rendering (task 7.3).

These tests cover Requirement 13 — each optional capability renders ONLY when
the resolved Deployment_Intent requests it (gated), and is absent otherwise:

* R13.1  — Multi-AZ -> 5-rds multi_az=true.
* R13.2  — cross-region mounted standby replica -> 5-rds create_standby_replica
           + standby_* prerequisites.
* R13.3  — AWS Managed AD -> 5-rds directory_id + 2-iam directory role.
* R13.4  — self-managed AD -> 5-rds domain_* + 2-iam directory role and
           self_managed_ad_secret_arn grant.
* R13.5  — Db2 audit -> 5-rds enable_audit + audit_role_arn + audit_bucket_name,
           and 2-iam create_audit_role + (reuse/create) audit bucket.
* R13.6  — BYOK MRK key -> reuse the supplied kms_key_arn (3-kms skipped).
* R13.7  — S3 restore -> 5-rds restore_from_s3 + s3_integration_role_arn, 2-iam
           create_s3_role.
* R13.8  — License Manager -> 6-license-manager enabled only when requested.
* R13.10 — audit bucket reuse vs create from the pre-existing-bucket check.
* R13.11 — BYOK key supplied is rendered (not auto-created).
* R13.12 — audit bucket CMK wiring.
* R13.15 — same-region read replica -> 5-rds create_read_replica + read_replica_*.

They run without Terraform or AWS: rendering is pure text generation, and the
module variables are parsed from the on-disk variables.tf files.
"""

from __future__ import annotations

import pytest

from scripts.render_terraform import render_terraform


# ---------------------------------------------------------------------------
# A representative VALID, resolved Deployment_Intent (no optional capabilities
# beyond multi_az). Each test enables exactly the capability under test.
# ---------------------------------------------------------------------------


def _base_intent() -> dict:
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
        "multi_az": False,
        "backup_retention_period": 7,
        "publicly_accessible": False,
        "storage_encrypted": True,
        # No kms_key_id -> composer creates the CMK (3-kms create path) by default.
        "vpc_security_group_ids": ["sg-0123456789abcdef0"],
        # No reusable subnet group / parameter group / monitoring role, so the
        # create-path modules (incl. 2-iam) are enabled by default and the
        # optional-capability flags are easy to observe.
        "db_parameter_group_name": "",
        "monitoring_interval": 15,
        "enable_cloudwatch_logs_exports": ["diag.log", "notify.log"],
        "deletion_protection": True,
        "ibm_customer_id": "1234567",
        "ibm_site_id": "1234567890",
        "db_instance_identifier": "",
        "tags": {"Project": "ACME", "Environment": "prod", "Owner": "db-team"},
        "db_parameter_group_family": "db2-se-12.1",
        "_provenance": {"engine": "user_provided"},
    }


@pytest.fixture
def intent() -> dict:
    return _base_intent()


def _rds(result):
    return result.modules["5-rds"].variables


def _iam(result):
    return result.modules["2-iam"].variables


# ---------------------------------------------------------------------------
# R13.1 — Multi-AZ.
# ---------------------------------------------------------------------------


def test_multi_az_rendered_when_requested(intent, terraform_modules_root):
    intent["multi_az"] = True
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert _rds(result)["multi_az"] is True


def test_multi_az_absent_value_false_when_not_requested(intent, terraform_modules_root):
    intent["multi_az"] = False
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert _rds(result)["multi_az"] is False


# ---------------------------------------------------------------------------
# R13.3 — AWS Managed AD.
# ---------------------------------------------------------------------------


def test_aws_managed_ad_rendered_when_requested(intent, terraform_modules_root):
    intent["domain"] = "d-1234567890"
    intent["domain_iam_role_name"] = "rds-db2-directory-service-access-role"
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert _rds(result)["directory_id"] == "d-1234567890"
    assert _rds(result)["directory_role_name"] == "rds-db2-directory-service-access-role"
    # The directory IAM role is created in 2-iam.
    assert _iam(result)["create_directory_role"] is True
    assert "2-iam" in result.enabled_modules


def test_aws_managed_ad_absent_when_not_requested(intent, terraform_modules_root):
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert "directory_id" not in _rds(result)
    assert "create_directory_role" not in _iam(result)


def test_aws_managed_ad_reuses_existing_directory_role(intent, terraform_modules_root):
    intent["domain"] = "d-1234567890"
    intent["directory_role_exists"] = True
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert _iam(result)["directory_role_exists"] is True
    assert "create_directory_role" not in _iam(result)


# ---------------------------------------------------------------------------
# R13.4 — customer self-managed AD / Kerberos.
# ---------------------------------------------------------------------------


def test_self_managed_ad_rendered_when_requested(intent, terraform_modules_root):
    intent["domain_fqdn"] = "company.com"
    intent["domain_ou"] = "OU=RDSDb2,DC=company,DC=com"
    intent["domain_auth_secret_arn"] = (
        "arn:aws:secretsmanager:us-east-1:111122223333:secret:ad-join-abc"
    )
    intent["domain_dns_ips"] = ["10.0.16.150", "10.0.28.150"]
    intent["domain_iam_role_name"] = "rds-db2-directory-service-access-role"
    result = render_terraform(intent, modules_root=terraform_modules_root)
    rds = _rds(result)
    assert rds["domain_fqdn"] == "company.com"
    assert rds["domain_ou"] == "OU=RDSDb2,DC=company,DC=com"
    assert rds["domain_auth_secret_arn"].endswith("ad-join-abc")
    assert rds["domain_dns_ips"] == ["10.0.16.150", "10.0.28.150"]
    # 2-iam creates the directory role AND is granted the join secret.
    iam = _iam(result)
    assert iam["create_directory_role"] is True
    assert iam["self_managed_ad_secret_arn"].endswith("ad-join-abc")


def test_self_managed_ad_absent_when_not_requested(intent, terraform_modules_root):
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert "domain_fqdn" not in _rds(result)
    assert "self_managed_ad_secret_arn" not in _iam(result)


# ---------------------------------------------------------------------------
# R13.5 / R13.10 / R13.12 — Db2 audit (option group + role + bucket).
# ---------------------------------------------------------------------------


def test_audit_rendered_when_requested(intent, terraform_modules_root):
    intent["enable_audit"] = True
    intent["audit_role_arn"] = "arn:aws:iam::111122223333:role/rds-db2-audit"
    intent["audit_bucket_name"] = "acme-db2-audit"
    result = render_terraform(intent, modules_root=terraform_modules_root)
    rds = _rds(result)
    assert rds["enable_audit"] is True
    assert rds["audit_role_arn"].endswith("rds-db2-audit")
    assert rds["audit_bucket_name"] == "acme-db2-audit"
    # 2-iam builds the audit role/policy and references the bucket.
    iam = _iam(result)
    assert iam["create_audit_role"] is True
    assert iam["audit_bucket_name"] == "acme-db2-audit"
    assert "2-iam" in result.enabled_modules


def test_audit_absent_when_not_requested(intent, terraform_modules_root):
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert "enable_audit" not in _rds(result)
    assert "create_audit_role" not in _iam(result)


def test_audit_bucket_reused_when_preexisting(intent, terraform_modules_root):
    """R13.10: an audit bucket that already exists is reused, not created."""
    intent["enable_audit"] = True
    intent["audit_bucket_name"] = "acme-db2-audit"
    intent["audit_bucket_exists"] = True
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert _iam(result)["create_audit_bucket"] is False


def test_audit_bucket_default_reuse_when_check_absent(intent, terraform_modules_root):
    """R13.10: the audit bucket must pre-exist (validator rejects a missing one),
    so absent a check result the composer reuses it (create_audit_bucket=false)."""
    intent["enable_audit"] = True
    intent["audit_bucket_name"] = "acme-db2-audit"
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert _iam(result)["create_audit_bucket"] is False


def test_audit_bucket_cmk_wired_when_supplied(intent, terraform_modules_root):
    """R13.12: a supplied audit-bucket CMK is rendered onto the 2-iam variable."""
    intent["enable_audit"] = True
    intent["audit_bucket_name"] = "acme-db2-audit"
    intent["audit_bucket_kms_key_id"] = "arn:aws:kms:us-east-1:111122223333:key/mrk-audit"
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert _iam(result)["audit_bucket_kms_key_arn"].endswith("mrk-audit")


# ---------------------------------------------------------------------------
# R13.7 — S3 restore integration.
# ---------------------------------------------------------------------------


def test_s3_restore_rendered_when_requested(intent, terraform_modules_root):
    intent["restore_from_s3"] = True
    intent["s3_integration_role_arn"] = "arn:aws:iam::111122223333:role/rds-db2-s3"
    intent["s3_backup_bucket_name"] = "acme-db2-backups"
    result = render_terraform(intent, modules_root=terraform_modules_root)
    rds = _rds(result)
    assert rds["restore_from_s3"] is True
    assert rds["s3_integration_role_arn"].endswith("rds-db2-s3")
    iam = _iam(result)
    assert iam["create_s3_role"] is True
    assert iam["s3_backup_bucket_name"] == "acme-db2-backups"


def test_s3_restore_absent_when_not_requested(intent, terraform_modules_root):
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert "restore_from_s3" not in _rds(result)
    assert "create_s3_role" not in _iam(result)


def test_s3_backup_bucket_reused_when_preexisting(intent, terraform_modules_root):
    intent["restore_from_s3"] = True
    intent["s3_backup_bucket_name"] = "acme-db2-backups"
    intent["s3_backup_bucket_exists"] = True
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert _iam(result)["create_s3_backup_bucket"] is False


def test_s3_backup_bucket_created_when_absent(intent, terraform_modules_root):
    intent["restore_from_s3"] = True
    intent["s3_backup_bucket_name"] = "acme-db2-backups"
    intent["s3_backup_bucket_exists"] = False
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert _iam(result)["create_s3_backup_bucket"] is True


# ---------------------------------------------------------------------------
# R13.6 / R13.11 — BYOK MRK key reuse.
# ---------------------------------------------------------------------------


def test_byok_key_reused_not_created(intent, terraform_modules_root):
    """R13.6/R13.11: a supplied CMK is rendered onto 5-rds and 3-kms is skipped
    (the composer reuses the key rather than auto-creating one)."""
    intent["kms_key_id"] = "arn:aws:kms:us-east-1:111122223333:key/mrk-byok"
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert _rds(result)["kms_key_arn"].endswith("mrk-byok")
    assert "3-kms" not in result.enabled_modules


def test_composer_creates_mrk_key_when_no_byok(intent, terraform_modules_root):
    """Absent a supplied key, the composer creates an MRK CMK (3-kms enabled)."""
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert "3-kms" in result.enabled_modules
    assert result.modules["3-kms"].variables["multi_region_key"] is True


# ---------------------------------------------------------------------------
# R13.8 — License Manager tracking (gated, not edition-driven).
# ---------------------------------------------------------------------------


def test_license_manager_enabled_only_when_requested(intent, terraform_modules_root):
    intent["license_manager"] = True
    intent["license_count"] = 16
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert "6-license-manager" in result.enabled_modules
    lm = result.modules["6-license-manager"].variables
    assert lm["db2_edition"] == "SE"
    assert lm["license_count"] == 16
    assert "6-license-manager/terraform.tfvars" in result.files


def test_license_manager_absent_when_not_requested(intent, terraform_modules_root):
    """The edition maps to 6-license-manager for value rendering, but the module
    must NOT be enabled unless License Manager tracking is requested (R13.8)."""
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert "6-license-manager" not in result.enabled_modules
    assert "6-license-manager/terraform.tfvars" not in result.files


# ---------------------------------------------------------------------------
# R13.2 — cross-region mounted standby replica.
# ---------------------------------------------------------------------------


def test_standby_replica_rendered_when_requested(intent, terraform_modules_root):
    intent["create_standby_replica"] = True
    intent["standby_replica_region"] = "us-west-2"
    intent["standby_replica_identifier"] = "db2db-standby"
    intent["standby_instance_class"] = "db.r7i.4xlarge"
    intent["standby_parameter_group_name"] = "rds-db2-prod-pg-west"
    intent["standby_kms_key_arn"] = "arn:aws:kms:us-west-2:111122223333:key/mrk-west"
    result = render_terraform(intent, modules_root=terraform_modules_root)
    rds = _rds(result)
    assert rds["create_standby_replica"] is True
    assert rds["standby_replica_region"] == "us-west-2"
    assert rds["standby_replica_identifier"] == "db2db-standby"
    assert rds["standby_parameter_group_name"] == "rds-db2-prod-pg-west"
    assert rds["standby_kms_key_arn"].endswith("mrk-west")


def test_standby_replica_absent_when_not_requested(intent, terraform_modules_root):
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert "create_standby_replica" not in _rds(result)
    assert "standby_kms_key_arn" not in _rds(result)


# ---------------------------------------------------------------------------
# R13.15 — same-region read replica.
# ---------------------------------------------------------------------------


def test_read_replica_rendered_when_requested(intent, terraform_modules_root):
    intent["create_read_replica"] = True
    intent["read_replica_identifier"] = "db2db-read"
    intent["read_replica_instance_class"] = "db.r7i.2xlarge"
    result = render_terraform(intent, modules_root=terraform_modules_root)
    rds = _rds(result)
    assert rds["create_read_replica"] is True
    assert rds["read_replica_identifier"] == "db2db-read"
    assert rds["read_replica_instance_class"] == "db.r7i.2xlarge"


def test_read_replica_absent_when_not_requested(intent, terraform_modules_root):
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert "create_read_replica" not in _rds(result)
    assert "read_replica_identifier" not in _rds(result)


# ---------------------------------------------------------------------------
# Cross-capability: a plain deployment renders no optional capability.
# ---------------------------------------------------------------------------


def test_plain_deployment_has_no_optional_capabilities(intent, terraform_modules_root):
    result = render_terraform(intent, modules_root=terraform_modules_root)
    rds = _rds(result)
    for absent in (
        "enable_audit",
        "restore_from_s3",
        "directory_id",
        "domain_fqdn",
        "create_standby_replica",
        "create_read_replica",
    ):
        assert absent not in rds
    assert "6-license-manager" not in result.enabled_modules


def test_rendered_tfvars_use_real_variable_names_only(intent, terraform_modules_root):
    """R10.3 still holds with every optional capability turned on at once."""
    from scripts.render_terraform import load_module_variable_index

    intent["multi_az"] = True
    intent["enable_audit"] = True
    intent["audit_role_arn"] = "arn:aws:iam::111122223333:role/rds-db2-audit"
    intent["audit_bucket_name"] = "acme-db2-audit"
    intent["audit_bucket_kms_key_id"] = "arn:aws:kms:us-east-1:111122223333:key/mrk-audit"
    intent["restore_from_s3"] = True
    intent["s3_integration_role_arn"] = "arn:aws:iam::111122223333:role/rds-db2-s3"
    intent["s3_backup_bucket_name"] = "acme-db2-backups"
    intent["domain_fqdn"] = "company.com"
    intent["domain_iam_role_name"] = "rds-db2-directory-service-access-role"
    intent["domain_auth_secret_arn"] = (
        "arn:aws:secretsmanager:us-east-1:111122223333:secret:ad-join-abc"
    )
    intent["license_manager"] = True
    intent["license_count"] = 16
    intent["create_standby_replica"] = True
    intent["standby_parameter_group_name"] = "pg-west"
    intent["standby_kms_key_arn"] = "arn:aws:kms:us-west-2:111122223333:key/mrk-west"
    intent["create_read_replica"] = True

    result = render_terraform(intent, modules_root=terraform_modules_root)
    index = load_module_variable_index(terraform_modules_root)
    for module, rendered in result.modules.items():
        declared = index[module]
        for name in rendered.variables:
            assert name in declared, f"{module}: {name} is not a real variable"
