"""Layer-1 schema-validation invocation tests for the Intent_Validator (task 5.1).

Covers R4.2 (validate the complete intent against the Intent_Schema before any
rendering), R4.3 (report EVERY failing field together with the specific schema
rule it violated — never stop at the first), and R4.4 (a failed result the
caller checks and halts on before producing any artifact).

These exercise ``scripts.validate_intent`` — the validation *invocation and
failure reporting* — not the schema content itself (that lives in
``test_schema_conditionals.py`` and ``test_schema_property.py``). The Layer-2
cross-field arithmetic (5.2) and security cross-checks (5.3) are added later and
are deliberately not asserted here.
"""

from __future__ import annotations

from scripts.validate_intent import (
    LAYER_SCHEMA,
    ValidationError,
    ValidationResult,
    validate_intent,
    validate_schema,
)


def _valid_intent() -> dict:
    """A complete Deployment_Intent that satisfies the always-required set and
    every Layer-1 conditional (gp3 < 400 baseline, managed password, single-AZ).
    Mirrors the base used by the schema-conditional tests so the two stay in
    sync.
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
        "kms_key_id": "arn:aws:kms:us-east-1:111122223333:key/mrk-abc",
        "vpc_security_group_ids": ["sg-0123456789abcdef0"],
        "vpc_id": "vpc-0123456789abcdef0",
        "db_subnet_group_name": "db2-subnets",
        "db_parameter_group_name": "",
        "monitoring_interval": 0,
        "enable_cloudwatch_logs_exports": ["diag.log", "notify.log"],
        "deletion_protection": True,
        "tags": {"Project": "p", "Environment": "dev", "Owner": "o"},
        "manage_master_user_password": True,
        "master_user_secret_kms_key_id": "arn:aws:kms:us-east-1:111122223333:key/mrk-sec",
        "ibm_customer_id": "IBM-CUST-001",
        "ibm_site_id": "IBM-SITE-001",
    }


# --- R4.2: a complete valid intent passes -----------------------------------


def test_valid_intent_passes_schema():
    result = validate_schema(_valid_intent())
    assert result.ok, result.report()
    assert result.errors == []


def test_valid_intent_passes_top_level_validate():
    result = validate_intent(_valid_intent())
    assert result.ok, result.report()


# --- R4.3: every failing field + rule is reported (not just the first) ------


def test_reports_every_missing_required_field():
    intent = _valid_intent()
    # Remove three always-required fields at once.
    for f in ("region", "engine", "kms_key_id"):
        del intent[f]

    result = validate_schema(intent)

    assert not result.ok
    failed_fields = set(result.fields())
    assert {"region", "engine", "kms_key_id"} <= failed_fields
    # Each was reported against the JSON Schema 'required' rule by name.
    required_failures = {
        e.field for e in result.errors if e.rule == "required"
    }
    assert {"region", "engine", "kms_key_id"} <= required_failures


def test_reports_multiple_distinct_violations_together():
    intent = _valid_intent()
    intent["engine"] = "postgres"          # enum violation (R5.2)
    intent["port"] = 50443                 # reserved SSL service port (R18.13)
    intent["storage_type"] = "io1"         # enum: gp3/io2 only (R18.1)
    del intent["db_name"]                  # required violation (R4.6)

    result = validate_intent(intent)

    assert not result.ok
    # All four independent problems surface in one pass, not just the first.
    failed_fields = set(result.fields())
    assert {"engine", "port", "storage_type", "db_name"} <= failed_fields
    # And every error carries the schema layer label + a non-empty rule name.
    for err in result.errors:
        assert err.layer == LAYER_SCHEMA
        assert err.rule
        assert err.message


def test_missing_required_field_named_not_container():
    intent = _valid_intent()
    del intent["allocated_storage"]

    result = validate_schema(intent)

    assert not result.ok
    # The report names the missing field itself, not "<root>".
    assert "allocated_storage" in result.fields()
    assert "<root>" not in result.fields()


def test_enum_violation_reports_field_and_enum_rule():
    intent = _valid_intent()
    intent["deployment_tier"] = "staging"  # not sandbox/dev/prod

    result = validate_schema(intent)

    assert not result.ok
    tier_errors = [e for e in result.errors if e.field == "deployment_tier"]
    assert tier_errors
    assert any(e.rule == "enum" for e in tier_errors)


# --- R4.4: failure is a checkable result that halts before artifacts --------


def test_failed_result_is_not_ok_and_has_report():
    intent = _valid_intent()
    del intent["region"]

    result = validate_intent(intent)

    assert not result.ok
    report = result.report()
    assert "region" in report
    assert "no Terraform artifacts" in report


def test_validate_intent_skips_layer2_when_schema_fails():
    # When Layer 1 fails, the top-level validator returns the Layer-1 findings
    # and does not attempt Layer-2 arithmetic on a structurally-unsound intent.
    intent = _valid_intent()
    del intent["storage_type"]

    result = validate_intent(intent)

    assert not result.ok
    assert all(e.layer == LAYER_SCHEMA for e in result.errors)


# --- ValidationResult / ValidationError behaviour ---------------------------


def test_validation_result_accumulates_and_dedupes_fields():
    result = ValidationResult()
    assert result.ok
    result.add(ValidationError("iops", "minimum", "too small"))
    result.add(ValidationError("iops", "maximum", "too big"))
    result.add(ValidationError("port", "const", "reserved"))

    assert not result.ok
    # fields() preserves first-seen order and de-duplicates.
    assert result.fields() == ["iops", "port"]
    assert len(result.errors) == 3
