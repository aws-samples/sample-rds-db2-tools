"""Layer-2 security-invariant cross-check tests for the Intent_Validator (5.3).

Covers the non-negotiable ``Security_Invariant`` cross-checks JSON Schema
cannot express:

* CMK must be customer-managed, never an AWS-owned/managed default key (R6.11).
* The storage CMK / BYOK key must be a multi-region key (MRK) (R6.11, R13.14).
* ``publicly_accessible=true`` requires ``public_access_acknowledged=true`` (R6.8).
* ``0.0.0.0/0`` ingress requires ``public_access_acknowledged=true`` (R6.9).
* ``db2-ce`` + License Manager tracking is a conflict (R8.12).
* A standby/replica with ``backup_retention_period=0`` is a conflict (R13.13).
* IBM_Customer_ID / IBM_Site_ID required for every edition (R7.8) and
  well-formed (non-empty after trim, <= 255 chars) (R7.9).

Both ``validate_security_invariants`` (the pure Layer-2 unit) and
``validate_intent`` (end-to-end, confirming Layer 2 runs after Layer 1 and
accumulates into one gating result) are exercised.
"""

from __future__ import annotations

from scripts.validate_intent import (
    IBM_ID_MAX_LENGTH,
    LAYER_SECURITY,
    validate_intent,
    validate_security_invariants,
)


# ---------------------------------------------------------------------------
# Helpers — a fully security-compliant base intent the checks then probe
# ---------------------------------------------------------------------------


def _base_intent() -> dict:
    """A complete, Layer-1-valid AND security-compliant Deployment_Intent."""
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


def _rules(errors) -> set:
    return {e.rule for e in errors}


# ---------------------------------------------------------------------------
# Happy path — a fully compliant intent produces no security findings
# ---------------------------------------------------------------------------


def test_compliant_intent_has_no_security_errors():
    assert validate_security_invariants(_base_intent()) == []


def test_compliant_intent_passes_full_validate_intent():
    result = validate_intent(_base_intent())
    assert result.ok, result.report()


def test_acknowledged_public_access_is_allowed():
    intent = _base_intent()
    intent["publicly_accessible"] = True
    intent["public_access_acknowledged"] = True
    assert "public_access_requires_acknowledgement" not in _rules(
        validate_security_invariants(intent)
    )


# ---------------------------------------------------------------------------
# R6.11 — encryptable-resource keys must be customer-managed CMKs
# ---------------------------------------------------------------------------


def test_aws_owned_storage_key_rejected_naming_resource():
    intent = _base_intent()
    intent["kms_key_id"] = "alias/aws/rds"
    errors = validate_security_invariants(intent)
    assert "cmk_not_aws_owned" in _rules(errors)
    err = next(e for e in errors if e.rule == "cmk_not_aws_owned")
    assert err.field == "kms_key_id"
    assert err.layer == LAYER_SECURITY
    assert "RDS storage" in err.message


def test_aws_managed_secret_key_rejected():
    intent = _base_intent()
    intent["master_user_secret_kms_key_id"] = (
        "arn:aws:kms:us-east-1:111122223333:alias/aws/secretsmanager"
    )
    errors = validate_security_invariants(intent)
    err = next(
        (e for e in errors if e.field == "master_user_secret_kms_key_id"), None
    )
    assert err is not None
    assert err.rule == "cmk_not_aws_owned"
    assert "secret" in err.message.lower()


def test_aws_owned_audit_bucket_key_rejected():
    intent = _base_intent()
    intent["audit_bucket_kms_key_id"] = "aws/s3"
    errors = validate_security_invariants(intent)
    err = next((e for e in errors if e.field == "audit_bucket_kms_key_id"), None)
    assert err is not None
    assert err.rule == "cmk_not_aws_owned"


# ---------------------------------------------------------------------------
# R6.11 / R13.14 — storage CMK / BYOK must be an MRK
# ---------------------------------------------------------------------------


def test_non_mrk_storage_key_rejected():
    intent = _base_intent()
    intent["kms_key_id"] = "arn:aws:kms:us-east-1:111122223333:key/abcd-not-mrk"
    errors = validate_security_invariants(intent)
    assert "byok_key_not_mrk" in _rules(errors)
    err = next(e for e in errors if e.rule == "byok_key_not_mrk")
    assert err.field == "kms_key_id"
    assert err.layer == LAYER_SECURITY


def test_bare_mrk_key_id_accepted():
    intent = _base_intent()
    intent["kms_key_id"] = "mrk-1234567890abcdef"
    assert "byok_key_not_mrk" not in _rules(validate_security_invariants(intent))


def test_aws_owned_key_does_not_also_report_non_mrk():
    # An AWS-owned key is rejected by the CMK rule; the MRK rule should not
    # pile on a second (redundant) finding for the same field.
    intent = _base_intent()
    intent["kms_key_id"] = "alias/aws/rds"
    rules = _rules(validate_security_invariants(intent))
    assert "cmk_not_aws_owned" in rules
    assert "byok_key_not_mrk" not in rules


# ---------------------------------------------------------------------------
# R6.8 — publicly_accessible=true requires acknowledgement
# ---------------------------------------------------------------------------


def test_public_access_without_acknowledgement_rejected():
    intent = _base_intent()
    intent["publicly_accessible"] = True
    errors = validate_security_invariants(intent)
    assert "public_access_requires_acknowledgement" in _rules(errors)
    err = next(
        e for e in errors if e.rule == "public_access_requires_acknowledgement"
    )
    assert err.field == "publicly_accessible"


def test_public_access_with_false_acknowledgement_rejected():
    intent = _base_intent()
    intent["publicly_accessible"] = True
    intent["public_access_acknowledged"] = False
    assert "public_access_requires_acknowledgement" in _rules(
        validate_security_invariants(intent)
    )


# ---------------------------------------------------------------------------
# R6.9 — 0.0.0.0/0 ingress requires acknowledgement
# ---------------------------------------------------------------------------


def test_open_ingress_without_acknowledgement_rejected():
    intent = _base_intent()
    intent["ingress_cidrs"] = ["10.0.0.0/16", "0.0.0.0/0"]
    errors = validate_security_invariants(intent)
    assert "open_ingress_requires_acknowledgement" in _rules(errors)
    err = next(
        e for e in errors if e.rule == "open_ingress_requires_acknowledgement"
    )
    assert err.field == "ingress_cidrs"


def test_open_ingress_with_acknowledgement_allowed():
    intent = _base_intent()
    intent["ingress_cidrs"] = ["0.0.0.0/0"]
    intent["public_access_acknowledged"] = True
    assert "open_ingress_requires_acknowledgement" not in _rules(
        validate_security_invariants(intent)
    )


def test_scoped_ingress_cidrs_allowed_without_acknowledgement():
    intent = _base_intent()
    intent["ingress_cidrs"] = ["10.0.0.0/16", "192.168.1.0/24"]
    assert "open_ingress_requires_acknowledgement" not in _rules(
        validate_security_invariants(intent)
    )


def test_open_ingress_via_synonym_field_rejected():
    intent = _base_intent()
    intent["ingress_cidr_blocks"] = ["0.0.0.0/0"]
    errors = validate_security_invariants(intent)
    err = next(
        (e for e in errors if e.rule == "open_ingress_requires_acknowledgement"),
        None,
    )
    assert err is not None
    assert err.field == "ingress_cidr_blocks"


# ---------------------------------------------------------------------------
# R8.12 — db2-ce + License Manager conflict
# ---------------------------------------------------------------------------


def test_ce_with_license_manager_rejected():
    intent = _base_intent()
    intent["engine"] = "db2-ce"
    intent["license_manager"] = True
    errors = validate_security_invariants(intent)
    assert "ce_license_manager_conflict" in _rules(errors)
    err = next(e for e in errors if e.rule == "ce_license_manager_conflict")
    assert err.field == "license_manager"
    assert err.layer == LAYER_SECURITY


def test_ce_without_license_manager_allowed():
    intent = _base_intent()
    intent["engine"] = "db2-ce"
    intent["license_manager"] = False
    assert "ce_license_manager_conflict" not in _rules(
        validate_security_invariants(intent)
    )


def test_se_with_license_manager_allowed():
    intent = _base_intent()
    intent["engine"] = "db2-se"
    intent["license_manager"] = True
    assert "ce_license_manager_conflict" not in _rules(
        validate_security_invariants(intent)
    )


# ---------------------------------------------------------------------------
# R13.13 — standby/replica + backup_retention_period=0 conflict
# ---------------------------------------------------------------------------


def test_standby_with_zero_backup_retention_rejected():
    intent = _base_intent()
    intent["standby_replica"] = True
    intent["backup_retention_period"] = 0
    errors = validate_security_invariants(intent)
    assert "standby_requires_backups" in _rules(errors)
    err = next(e for e in errors if e.rule == "standby_requires_backups")
    assert err.field == "standby_replica"
    assert err.layer == LAYER_SECURITY


def test_standby_with_positive_backup_retention_allowed():
    intent = _base_intent()
    intent["standby_replica"] = True
    intent["backup_retention_period"] = 7
    assert "standby_requires_backups" not in _rules(
        validate_security_invariants(intent)
    )


def test_zero_backup_retention_without_standby_allowed():
    intent = _base_intent()
    intent["backup_retention_period"] = 0
    assert "standby_requires_backups" not in _rules(
        validate_security_invariants(intent)
    )


# ---------------------------------------------------------------------------
# R7.8 — IBM identifiers required for every edition
# ---------------------------------------------------------------------------


def test_missing_ibm_customer_id_rejected():
    intent = _base_intent()
    del intent["ibm_customer_id"]
    errors = validate_security_invariants(intent)
    err = next((e for e in errors if e.field == "ibm_customer_id"), None)
    assert err is not None
    assert err.rule == "ibm_identifier_required"


def test_missing_ibm_site_id_rejected():
    intent = _base_intent()
    del intent["ibm_site_id"]
    errors = validate_security_invariants(intent)
    err = next((e for e in errors if e.field == "ibm_site_id"), None)
    assert err is not None
    assert err.rule == "ibm_identifier_required"


def test_missing_ibm_ids_rejected_for_each_edition():
    for edition in ("db2-ce", "db2-se", "db2-ae"):
        intent = _base_intent()
        intent["engine"] = edition
        del intent["ibm_customer_id"]
        del intent["ibm_site_id"]
        rules = _rules(validate_security_invariants(intent))
        assert "ibm_identifier_required" in rules, edition


# ---------------------------------------------------------------------------
# R7.9 — IBM identifiers must be well-formed
# ---------------------------------------------------------------------------


def test_blank_ibm_id_rejected_as_malformed():
    intent = _base_intent()
    intent["ibm_customer_id"] = "   "
    errors = validate_security_invariants(intent)
    err = next((e for e in errors if e.field == "ibm_customer_id"), None)
    assert err is not None
    assert err.rule == "ibm_identifier_malformed"


def test_overlong_ibm_id_rejected_as_malformed():
    intent = _base_intent()
    intent["ibm_site_id"] = "x" * (IBM_ID_MAX_LENGTH + 1)
    errors = validate_security_invariants(intent)
    err = next((e for e in errors if e.field == "ibm_site_id"), None)
    assert err is not None
    assert err.rule == "ibm_identifier_malformed"


def test_ibm_id_at_max_length_allowed():
    intent = _base_intent()
    intent["ibm_customer_id"] = "x" * IBM_ID_MAX_LENGTH
    assert "ibm_identifier_malformed" not in _rules(
        validate_security_invariants(intent)
    )


# ---------------------------------------------------------------------------
# Accumulation + layering behaviour
# ---------------------------------------------------------------------------


def test_multiple_security_violations_reported_together():
    intent = _base_intent()
    intent["kms_key_id"] = "alias/aws/rds"  # cmk_not_aws_owned
    intent["publicly_accessible"] = True  # public_access ack
    del intent["ibm_site_id"]  # ibm required
    rules = _rules(validate_security_invariants(intent))
    assert {
        "cmk_not_aws_owned",
        "public_access_requires_acknowledgement",
        "ibm_identifier_required",
    } <= rules


def test_security_violation_fails_full_validate_intent():
    intent = _base_intent()
    intent["kms_key_id"] = "arn:aws:kms:us-east-1:111122223333:key/not-mrk"
    result = validate_intent(intent)
    assert not result.ok
    assert any(
        e.rule == "byok_key_not_mrk" and e.layer == LAYER_SECURITY
        for e in result.errors
    )


def test_layer2_security_skipped_when_layer1_fails():
    # A Layer-1 (schema) failure must short-circuit before Layer-2 runs, so no
    # security errors appear even though they would also fire.
    intent = _base_intent()
    intent["kms_key_id"] = "alias/aws/rds"  # would trip security
    del intent["region"]  # Layer-1 required-field violation
    result = validate_intent(intent)
    assert not result.ok
    assert all(e.layer != LAYER_SECURITY for e in result.errors)


def test_all_security_errors_carry_layer_label():
    intent = _base_intent()
    intent["kms_key_id"] = "alias/aws/rds"
    intent["publicly_accessible"] = True
    errors = validate_security_invariants(intent)
    assert errors
    assert all(e.layer == LAYER_SECURITY for e in errors)
    assert all(e.rule and e.message for e in errors)


# ---------------------------------------------------------------------------
# R14.6 — mandatory customer tags present and non-empty
# ---------------------------------------------------------------------------


def test_compliant_intent_has_no_mandatory_tag_error():
    assert "mandatory_tag_required" not in _rules(
        validate_security_invariants(_base_intent())
    )


def test_missing_project_tag_rejected_naming_it():
    intent = _base_intent()
    del intent["tags"]["Project"]
    errors = validate_security_invariants(intent)
    assert "mandatory_tag_required" in _rules(errors)
    err = next(e for e in errors if e.rule == "mandatory_tag_required")
    assert err.field == "tags.Project"
    assert err.layer == LAYER_SECURITY
    assert "Project" in err.message


def test_empty_owner_tag_rejected():
    intent = _base_intent()
    intent["tags"]["Owner"] = "   "
    errors = validate_security_invariants(intent)
    err = next(e for e in errors if e.rule == "mandatory_tag_required")
    assert err.field == "tags.Owner"


def test_all_three_missing_tags_each_reported():
    intent = _base_intent()
    intent["tags"] = {}
    errors = [e for e in validate_security_invariants(intent) if e.rule == "mandatory_tag_required"]
    reported = {e.field for e in errors}
    assert reported == {"tags.Project", "tags.Environment", "tags.Owner"}


def test_extra_customer_tags_do_not_trigger_rejection():
    """R14.7: extra tags beyond the mandatory five are fine."""
    intent = _base_intent()
    intent["tags"]["CostCenter"] = "CC-1"
    intent["tags"]["Team"] = "payments"
    assert "mandatory_tag_required" not in _rules(
        validate_security_invariants(intent)
    )


def test_missing_mandatory_tag_fails_full_validate_intent():
    intent = _base_intent()
    del intent["tags"]["Environment"]
    result = validate_intent(intent)
    assert not result.ok
    assert any(e.rule == "mandatory_tag_required" for e in result.errors)
