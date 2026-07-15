"""Layer-2 cross-field arithmetic tests for the Intent_Validator (task 5.2).

Covers the ``Cross_Field_Rule`` storage arithmetic JSON Schema cannot express
(R19): the gp3 ratio (0, 500] (R19.6), io2 ratio [0.5, 1000] (R19.8), the gp3
``storage_throughput`` derivation ``min(floor(iops/4), 4000)`` (R19.7), and the
allocated-storage bounds (< 64000 R19.1; gp3 >= 20 R19.2; io2 >= 100 R19.3).
R19.10 requires the violated rule to be reported by name with the computed
value and the bound it broke.

Two angles are exercised:

* ``validate_storage_arithmetic`` directly — the pure Layer-2 unit, including
  bounds the schema would otherwise pre-empt (e.g. the 64000 ceiling, or a gp3
  ratio that the schema's 12000-64000 iops cap keeps unreachable end-to-end).
* ``validate_intent`` end-to-end — confirming Layer 2 runs only after Layer 1
  passes and its findings accumulate into the same result and gate rendering.
"""

from __future__ import annotations

from scripts.resolve_intent import derive_gp3_storage_throughput
from scripts.validate_intent import (
    LAYER_CROSS_FIELD,
    validate_intent,
    validate_storage_arithmetic,
)


# ---------------------------------------------------------------------------
# Helpers — schema-valid base intents the Layer-2 rules then probe
# ---------------------------------------------------------------------------


def _base_intent() -> dict:
    """A complete, Layer-1-valid Deployment_Intent (gp3 < 400 baseline)."""
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


def _gp3_large_intent(allocated_storage: int, iops: int, throughput: int) -> dict:
    """A Layer-1-valid gp3 >= 400 GiB intent with explicit perf fields."""
    intent = _base_intent()
    intent["storage_type"] = "gp3"
    intent["allocated_storage"] = allocated_storage
    intent["iops"] = iops
    intent["storage_throughput"] = throughput
    return intent


def _io2_intent(allocated_storage: int, iops: int) -> dict:
    """A Layer-1-valid io2 intent (iops required, no throughput)."""
    intent = _base_intent()
    intent["storage_type"] = "io2"
    intent["allocated_storage"] = allocated_storage
    intent["iops"] = iops
    return intent


def _rules(errors) -> set:
    return {e.rule for e in errors}


# ---------------------------------------------------------------------------
# Happy path — well-formed intents produce no Layer-2 findings
# ---------------------------------------------------------------------------


def test_gp3_below_threshold_baseline_has_no_arithmetic_errors():
    # gp3 < 400 GiB carries no iops/throughput; no ratio/throughput rule runs.
    assert validate_storage_arithmetic(_base_intent()) == []


def test_gp3_large_valid_passes_all_arithmetic_rules():
    iops = 20000
    intent = _gp3_large_intent(400, iops, derive_gp3_storage_throughput(iops))
    assert validate_storage_arithmetic(intent) == []


def test_io2_valid_ratio_passes():
    # 50000 / 100 = 500, within [0.5, 1000].
    assert validate_storage_arithmetic(_io2_intent(100, 50000)) == []


def test_valid_gp3_large_passes_full_validate_intent():
    iops = 12000
    intent = _gp3_large_intent(1000, iops, derive_gp3_storage_throughput(iops))
    result = validate_intent(intent)
    assert result.ok, result.report()


# ---------------------------------------------------------------------------
# R19.1 — allocated_storage < 64000 (any storage type)
# ---------------------------------------------------------------------------


def test_allocated_storage_at_ceiling_is_rejected():
    intent = _io2_intent(64000, 32000)
    errors = validate_storage_arithmetic(intent)
    assert "allocated_storage_max" in _rules(errors)
    err = next(e for e in errors if e.rule == "allocated_storage_max")
    assert err.field == "allocated_storage"
    assert err.layer == LAYER_CROSS_FIELD
    # Reports the computed value and the bound (R19.10).
    assert "64000" in err.message


def test_allocated_storage_just_below_ceiling_ok_for_bound():
    # 63999 is below the ceiling, so the max rule does not fire.
    errors = validate_storage_arithmetic(_io2_intent(63999, 32000))
    assert "allocated_storage_max" not in _rules(errors)


# ---------------------------------------------------------------------------
# R19.2 / R19.3 — per-storage-type allocated_storage minimums
# ---------------------------------------------------------------------------


def test_gp3_below_min_allocated_storage_rejected():
    intent = _base_intent()
    intent["allocated_storage"] = 19
    errors = validate_storage_arithmetic(intent)
    assert "gp3_allocated_storage_min" in _rules(errors)
    err = next(e for e in errors if e.rule == "gp3_allocated_storage_min")
    assert "20" in err.message and "19" in err.message


def test_io2_below_min_allocated_storage_rejected():
    errors = validate_storage_arithmetic(_io2_intent(99, 50))
    assert "io2_allocated_storage_min" in _rules(errors)
    err = next(e for e in errors if e.rule == "io2_allocated_storage_min")
    assert "100" in err.message and "99" in err.message


# ---------------------------------------------------------------------------
# R19.6 — gp3 ratio in (0, 500]
# ---------------------------------------------------------------------------


def test_gp3_ratio_above_bound_rejected_reports_ratio_and_bound():
    # 400 GiB, iops 240000 -> ratio 600 (> 500). (Constructed directly: the
    # schema's 12000-64000 iops cap keeps this unreachable end-to-end, but the
    # Layer-2 rule must still enforce it.)
    intent = _gp3_large_intent(400, 240000, derive_gp3_storage_throughput(240000))
    errors = validate_storage_arithmetic(intent)
    assert "gp3_iops_ratio" in _rules(errors)
    err = next(e for e in errors if e.rule == "gp3_iops_ratio")
    assert err.field == "iops"
    assert err.layer == LAYER_CROSS_FIELD
    assert "600" in err.message  # computed ratio
    assert "500" in err.message  # bound


def test_gp3_ratio_at_upper_bound_is_allowed():
    # ratio exactly 500 is within (0, 500].
    intent = _gp3_large_intent(400, 200000, derive_gp3_storage_throughput(200000))
    errors = validate_storage_arithmetic(intent)
    assert "gp3_iops_ratio" not in _rules(errors)


# ---------------------------------------------------------------------------
# R19.7 — gp3 storage_throughput == min(floor(iops/4), 4000)
# ---------------------------------------------------------------------------


def test_gp3_throughput_mismatch_rejected_reports_derived():
    iops = 12000  # derived throughput = 3000
    intent = _gp3_large_intent(1000, iops, 2999)  # off by one
    errors = validate_storage_arithmetic(intent)
    assert "gp3_throughput_derivation" in _rules(errors)
    err = next(e for e in errors if e.rule == "gp3_throughput_derivation")
    assert err.field == "storage_throughput"
    assert "3000" in err.message  # derived value
    assert "2999" in err.message  # supplied value


def test_gp3_throughput_capped_at_4000_accepted():
    # iops 64000 -> floor(16000) capped at 4000.
    intent = _gp3_large_intent(1000, 64000, 4000)
    errors = validate_storage_arithmetic(intent)
    assert "gp3_throughput_derivation" not in _rules(errors)


def test_gp3_throughput_mismatch_fails_full_validate_intent():
    iops = 12000
    intent = _gp3_large_intent(1000, iops, 3500)  # should be 3000
    result = validate_intent(intent)
    assert not result.ok
    assert any(
        e.rule == "gp3_throughput_derivation" and e.layer == LAYER_CROSS_FIELD
        for e in result.errors
    )


# ---------------------------------------------------------------------------
# R19.8 — io2 ratio in [0.5, 1000]
# ---------------------------------------------------------------------------


def test_io2_ratio_above_bound_rejected():
    # 100 GiB, iops 200000 -> ratio 2000 (> 1000).
    intent = _io2_intent(100, 200000)
    errors = validate_storage_arithmetic(intent)
    assert "io2_iops_ratio" in _rules(errors)
    err = next(e for e in errors if e.rule == "io2_iops_ratio")
    assert err.field == "iops"
    assert "2000" in err.message  # computed ratio
    assert "1000" in err.message  # bound


def test_io2_ratio_below_bound_rejected():
    # 1000 GiB, iops 400 -> ratio 0.4 (< 0.5).
    intent = _io2_intent(1000, 400)
    errors = validate_storage_arithmetic(intent)
    assert "io2_iops_ratio" in _rules(errors)
    err = next(e for e in errors if e.rule == "io2_iops_ratio")
    assert "0.4" in err.message


def test_io2_ratio_at_lower_bound_allowed():
    # 1000 GiB, iops 500 -> ratio 0.5 exactly.
    errors = validate_storage_arithmetic(_io2_intent(1000, 500))
    assert "io2_iops_ratio" not in _rules(errors)


def test_io2_ratio_violation_fails_full_validate_intent():
    intent = _io2_intent(100, 200000)
    result = validate_intent(intent)
    assert not result.ok
    assert any(e.rule == "io2_iops_ratio" for e in result.errors)


# ---------------------------------------------------------------------------
# Accumulation + layering behaviour
# ---------------------------------------------------------------------------


def test_multiple_arithmetic_violations_reported_together():
    # io2 below the 100 GiB minimum AND ratio out of range, in one pass.
    intent = _io2_intent(50, 100000)  # ratio 2000 > 1000, allocated < 100
    errors = validate_storage_arithmetic(intent)
    assert {"io2_allocated_storage_min", "io2_iops_ratio"} <= _rules(errors)


def test_layer2_skipped_when_layer1_fails():
    # A Layer-1 (schema) failure must short-circuit before Layer-2 runs, so no
    # cross_field errors appear even though the arithmetic would also fail.
    intent = _io2_intent(50, 100000)
    del intent["region"]  # Layer-1 required-field violation
    result = validate_intent(intent)
    assert not result.ok
    assert all(e.layer != LAYER_CROSS_FIELD for e in result.errors)


def test_all_cross_field_errors_carry_layer_label():
    intent = _io2_intent(64000, 200000)  # ceiling + ratio violations
    errors = validate_storage_arithmetic(intent)
    assert errors
    assert all(e.layer == LAYER_CROSS_FIELD for e in errors)
    assert all(e.rule and e.message for e in errors)
