"""Unit tests for the Terraform_Composer reuse/create selection and the
always-on security-invariant rendering (task 7.2).

These tests cover Requirement 6.1, 6.2, 6.3, 6.5, 10.5, 10.6:

* R10.5 - when the intent supplies an existing subnet group / KMS key /
  parameter group / monitoring role, the corresponding create-path module is
  SKIPPED (the existing identifier is referenced via the consuming module).
* R10.6 - when no existing resource is supplied, the create-path module is
  ENABLED to create it.
* R6.1 - storage encryption is always rendered on, with a customer-managed MRK
  CMK (multi_region_key=true when the composer creates the key).
* R6.2 - DB2COMM=SSL and ssl_svcename=50443 are always rendered (in the
  4-parameter-group parameter group), and documented in the security supplement.
* R6.3 - publicly_accessible flips to false absent
  public_access_acknowledged=true, overriding any prompt value.
* R6.5 - the SSL-only security-group ingress opens ONLY port 50443 (TCP); the
  non-SSL TCP listener port (db2_port) is never opened, and no other port is.

They run without Terraform or AWS: rendering is pure text generation, and the
module variables are parsed from the on-disk variables.tf files.
"""

from __future__ import annotations

import pytest

from scripts.render_terraform import (
    CREATE,
    REUSE,
    SSL_SERVICE_PORT,
    collect_module_variables,
    enforce_security_invariants,
    load_module_variable_index,
    render_security_supplement,
    render_terraform,
    resolve_module_dispositions,
    select_enabled_modules,
)


# ---------------------------------------------------------------------------
# Intents: one that supplies all existing resources (reuse) and one that
# supplies none (create).
# ---------------------------------------------------------------------------


def _reuse_intent() -> dict:
    """A prod intent that supplies an existing subnet group, KMS key, parameter
    group, and monitoring role -> every create-path module is reused/skipped."""
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
        "db_parameter_group_name": "rds-db2-prod-pg",
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
    """A sandbox intent that supplies NO existing reusable resources -> every
    create-path module is rendered to create the resource."""
    intent = _reuse_intent()
    intent["deployment_tier"] = "sandbox"
    # No existing resources: empty (or absent) reuse identifiers.
    intent["db_subnet_group_name"] = ""
    intent["db_parameter_group_name"] = ""
    intent["kms_key_id"] = ""  # composer will create + make MRK
    intent.pop("monitoring_role_arn", None)
    return intent


@pytest.fixture
def reuse_intent() -> dict:
    return _reuse_intent()


@pytest.fixture
def create_intent() -> dict:
    return _create_intent()


# ---------------------------------------------------------------------------
# Reuse vs create disposition per resource (R10.5/10.6).
# ---------------------------------------------------------------------------


def test_all_supplied_resources_are_reused(reuse_intent):
    d = resolve_module_dispositions(reuse_intent)
    assert d["1-networking"] == REUSE
    assert d["3-kms"] == REUSE
    assert d["4-parameter-group"] == REUSE
    assert d["2-iam"] == REUSE


def test_no_supplied_resources_are_created(create_intent):
    d = resolve_module_dispositions(create_intent)
    assert d["1-networking"] == CREATE
    assert d["3-kms"] == CREATE
    assert d["4-parameter-group"] == CREATE
    assert d["2-iam"] == CREATE


@pytest.mark.parametrize(
    "field,module",
    [
        ("db_subnet_group_name", "1-networking"),
        ("kms_key_id", "3-kms"),
        ("db_parameter_group_name", "4-parameter-group"),
        ("monitoring_role_arn", "2-iam"),
    ],
)
def test_each_resource_reused_only_when_supplied(create_intent, field, module):
    """Per-resource: supplying just one existing identifier reuses only that
    one module while the others stay on the create path (R10.5/10.6)."""
    create_intent[field] = "existing-value-arn-or-name"
    d = resolve_module_dispositions(create_intent)
    assert d[module] == REUSE
    others = {"1-networking", "3-kms", "4-parameter-group", "2-iam"} - {module}
    for other in others:
        assert d[other] == CREATE


# ---------------------------------------------------------------------------
# Enabled-module selection follows the dispositions (R10.5/10.6).
# ---------------------------------------------------------------------------


def test_reuse_intent_skips_all_create_path_modules(reuse_intent, terraform_modules_root):
    result = render_terraform(reuse_intent, modules_root=terraform_modules_root)
    enabled = result.enabled_modules
    # Create-path modules are skipped because existing resources were supplied.
    assert "1-networking" not in enabled
    assert "3-kms" not in enabled
    assert "4-parameter-group" not in enabled
    assert "2-iam" not in enabled
    # No tfvars emitted for skipped modules.
    assert "1-networking/terraform.tfvars" not in result.files
    assert "3-kms/terraform.tfvars" not in result.files
    # 5-rds (the instance) is always rendered.
    assert "5-rds" in enabled
    assert "5-rds/terraform.tfvars" in result.files


def test_reused_identifiers_referenced_on_consuming_module(reuse_intent, terraform_modules_root):
    """R10.5: the existing identifiers are set as the 5-rds consuming inputs."""
    result = render_terraform(reuse_intent, modules_root=terraform_modules_root)
    rds = result.modules["5-rds"].variables
    assert rds["db_subnet_group_name"] == "rds-db2-prod-subnets"
    assert rds["parameter_group_name"] == "rds-db2-prod-pg"
    assert rds["kms_key_arn"] == "arn:aws:kms:us-east-1:111122223333:key/mrk-1234"
    assert rds["monitoring_role_arn"] == "arn:aws:iam::111122223333:role/rds-db2-monitoring"


def test_create_intent_enables_all_create_path_modules(create_intent, terraform_modules_root):
    result = render_terraform(create_intent, modules_root=terraform_modules_root)
    enabled = result.enabled_modules
    assert "1-networking" in enabled
    assert "3-kms" in enabled
    assert "4-parameter-group" in enabled
    assert "2-iam" in enabled
    for module in ("1-networking", "3-kms", "4-parameter-group", "2-iam"):
        assert f"{module}/terraform.tfvars" in result.files


# ---------------------------------------------------------------------------
# R6.1 - storage encryption always on with an MRK CMK.
# ---------------------------------------------------------------------------


def test_storage_encryption_always_on(create_intent, terraform_modules_root):
    create_intent["storage_encrypted"] = False  # prompt tries to disable
    result = render_terraform(create_intent, modules_root=terraform_modules_root)
    assert result.modules["5-rds"].variables["storage_encrypted"] is True


def test_created_kms_key_is_multi_region(create_intent, terraform_modules_root):
    """R6.1: when the composer creates the CMK, it is multi-region (MRK)."""
    result = render_terraform(create_intent, modules_root=terraform_modules_root)
    assert "3-kms" in result.enabled_modules
    assert result.modules["3-kms"].variables["multi_region_key"] is True


def test_reused_kms_key_does_not_force_multi_region(reuse_intent, terraform_modules_root):
    """When the CMK is reused, 3-kms is skipped so multi_region_key is not set
    by the composer (the supplied key must already be an MRK; validator R13.14)."""
    result = render_terraform(reuse_intent, modules_root=terraform_modules_root)
    assert "3-kms" not in result.enabled_modules


# ---------------------------------------------------------------------------
# R6.3 - publicly_accessible flips to false absent acknowledgement.
# ---------------------------------------------------------------------------


def test_public_access_forced_false_without_acknowledgement(create_intent, terraform_modules_root):
    create_intent["publicly_accessible"] = True  # prompt asks for public
    # No public_access_acknowledged -> composer must render false (R6.3).
    result = render_terraform(create_intent, modules_root=terraform_modules_root)
    assert result.modules["5-rds"].variables["publicly_accessible"] is False
    # 1-networking is on the create path here, so its flag is pinned too.
    assert result.modules["1-networking"].variables["publicly_accessible"] is False


def test_public_access_preserved_with_acknowledgement(create_intent, terraform_modules_root):
    create_intent["publicly_accessible"] = True
    create_intent["public_access_acknowledged"] = True
    result = render_terraform(create_intent, modules_root=terraform_modules_root)
    assert result.modules["5-rds"].variables["publicly_accessible"] is True


def test_false_acknowledgement_still_forces_false(create_intent, terraform_modules_root):
    create_intent["publicly_accessible"] = True
    create_intent["public_access_acknowledged"] = False
    result = render_terraform(create_intent, modules_root=terraform_modules_root)
    assert result.modules["5-rds"].variables["publicly_accessible"] is False


# ---------------------------------------------------------------------------
# R6.2 - DB2COMM=SSL / ssl_svcename=50443 always rendered in the parameter group.
# ---------------------------------------------------------------------------


def test_db2_ssl_parameters_in_parameter_group_module(create_intent, terraform_modules_root):
    """R6.2: the parameter-group module always carries DB2COMM=SSL and
    ssl_svcename=50443 (hardcoded in the module, since they are invariants)."""
    pg_main = (terraform_modules_root / "4-parameter-group" / "main.tf").read_text()
    assert 'name         = "DB2COMM"' in pg_main
    assert 'value        = "SSL"' in pg_main
    assert 'name         = "ssl_svcename"' in pg_main
    assert 'value        = "50443"' in pg_main


def test_security_supplement_documents_ssl_parameters(create_intent, terraform_modules_root):
    result = render_terraform(create_intent, modules_root=terraform_modules_root)
    sec = result.files["security.tf"]
    assert "DB2COMM=SSL" in sec
    assert "ssl_svcename=50443" in sec


# ---------------------------------------------------------------------------
# R6.5 - SSL-only ingress on 50443; TCP listener port never opened.
# ---------------------------------------------------------------------------


def test_security_supplement_opens_only_50443(reuse_intent, terraform_modules_root):
    result = render_terraform(reuse_intent, modules_root=terraform_modules_root)
    sec = result.files["security.tf"]
    assert "from_port         = 50443" in sec
    assert "to_port           = 50443" in sec
    assert 'ip_protocol       = "tcp"' in sec
    # The CIDR source from the intent is wired in.
    assert "10.0.0.0/16" in sec


def test_tcp_listener_port_never_opened(reuse_intent, terraform_modules_root):
    """R6.5: the non-SSL TCP listener port (8392) must never appear as an open
    ingress port."""
    reuse_intent["port"] = 8392
    result = render_terraform(reuse_intent, modules_root=terraform_modules_root)
    sec = result.files["security.tf"]
    # 8392 must not be an opened ingress port.
    assert "from_port         = 8392" not in sec
    assert "to_port           = 8392" not in sec
    assert "8392" not in sec


def test_no_ingress_without_sources(create_intent, terraform_modules_root):
    """With no ingress sources supplied, the most restrictive posture: no
    ingress rule is rendered (R6.5)."""
    create_intent.pop("ingress_cidrs", None)
    result = render_terraform(create_intent, modules_root=terraform_modules_root)
    sec = result.files["security.tf"]
    assert "aws_vpc_security_group_ingress_rule" not in sec
    assert "no ingress rule" in sec


def test_only_50443_ingress_no_other_port(reuse_intent, terraform_modules_root):
    """R6.5: only 50443 ingress is opened; assert there is no other from_port."""
    result = render_terraform(reuse_intent, modules_root=terraform_modules_root)
    sec = result.files["security.tf"]
    from_ports = [
        line for line in sec.splitlines() if "from_port" in line and "=" in line
    ]
    assert from_ports, "expected at least one ingress rule"
    for line in from_ports:
        assert "50443" in line


def test_ingress_via_source_security_groups(create_intent, terraform_modules_root):
    create_intent.pop("ingress_cidrs", None)
    create_intent["ingress_source_security_group_ids"] = ["sg-aaaa1111"]
    result = render_terraform(create_intent, modules_root=terraform_modules_root)
    sec = result.files["security.tf"]
    assert "referenced_security_group_id = each.value" in sec
    assert "sg-aaaa1111" in sec
    assert "from_port                    = 50443" in sec


# ---------------------------------------------------------------------------
# Determinism across the new behavior.
# ---------------------------------------------------------------------------


def test_render_with_security_invariants_is_deterministic(create_intent, terraform_modules_root):
    import copy

    first = render_terraform(create_intent, modules_root=terraform_modules_root)
    second = render_terraform(
        copy.deepcopy(create_intent), modules_root=terraform_modules_root
    )
    assert first.files == second.files
