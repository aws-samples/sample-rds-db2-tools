"""Validator property-based tests (task 5.4).

Implements the four design Correctness Properties whose invariants the
Intent_Validator and the parameter-group-family derivation must hold for *all*
inputs:

* **Property 1 — Intent round-trip idempotence (R4.9).** For every generated
  valid intent, ``validate -> serialize(json) -> re-validate`` produces a
  document that still validates, and the document is byte-for-byte unchanged by
  the round trip.
* **Property 2 — Security invariants always hold (R6.7, R6.12).** For every
  generated valid intent the Layer-2 security invariants hold (the storage CMK
  is a customer-managed MRK, the managed-secret CMK is customer-managed,
  ``publicly_accessible`` is false absent an acknowledgement, ingress is
  scoped); and when an invariant is deliberately violated the validator reports
  the corresponding finding.
* **Property 11 — IBM IDs required for every edition (R7.8).** An intent missing
  either IBM identifier is rejected for ``db2-ce``, ``db2-se``, and ``db2-ae``
  alike.
* **Property 3 — No fabricated parameter-group family (R5.8).** For any
  ``engine`` + major-version pair, ``derive_parameter_group_family`` either
  returns a member of ``SUPPORTED_PARAMETER_GROUP_FAMILIES`` or raises; it never
  emits a fabricated string.

The generators are *smart*: they constrain the input space to schema-valid,
security-compliant deployment intents (respecting the gp3/io2 storage rules,
the gp3>=400 ratio/throughput derivation, the managed-password oneOf, and the
multi-AZ backup-retention rule) so a rejection a property observes is
attributable to the property's own mutation, not a malformed seed.

**Validates: Requirements 4.9, 5.8, 6.7, 6.12, 7.8**
"""

from __future__ import annotations

import json
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scripts.engine_versions import (
    SUPPORTED_EDITIONS,
    SUPPORTED_PARAMETER_GROUP_FAMILIES,
    UnsupportedParameterGroupFamilyError,
    derive_parameter_group_family,
)
from scripts.resolve_intent import derive_gp3_storage_throughput
from scripts.validate_intent import (
    LAYER_SECURITY,
    validate_intent,
    validate_security_invariants,
)

# A customer-managed multi-region key (MRK) ARN — the only storage-CMK shape the
# security invariants accept (R6.1/6.11/13.14).
_MRK_STORAGE_KEY = "arn:aws:kms:us-east-1:111122223333:key/mrk-0123abcd"
# A customer-managed MRK for the RDS-managed master-user secret (R6.10).
_MRK_SECRET_KEY = "arn:aws:kms:us-east-1:111122223333:key/mrk-secret01"


# ---------------------------------------------------------------------------
# Smart generators: schema-valid AND security-compliant deployment intents
# ---------------------------------------------------------------------------


@st.composite
def _storage_config(draw: st.DrawFn) -> dict:
    """Generate one of the three valid storage postures.

    Each posture respects the schema's presence rules AND the Layer-2 arithmetic
    so the resulting intent passes full ``validate_intent``:

    * ``gp3 < 400``: no ``iops`` / ``storage_throughput`` (RDS baseline).
    * ``gp3 >= 400``: ``iops`` in [12000, 64000] with ratio in (0, 500] and
      ``storage_throughput == min(floor(iops/4), 4000)``.
    * ``io2``: ``iops`` with ratio in [0.5, 1000], no ``storage_throughput``.
    """
    kind = draw(st.sampled_from(["gp3_small", "gp3_large", "io2"]))

    if kind == "gp3_small":
        allocated = draw(st.integers(min_value=20, max_value=399))
        return {"storage_type": "gp3", "allocated_storage": allocated}

    if kind == "gp3_large":
        iops = draw(st.integers(min_value=12000, max_value=64000))
        # ratio iops/allocated <= 500  =>  allocated >= ceil(iops / 500);
        # also allocated >= 400 for this branch, and < 64000.
        lower = max(400, math.ceil(iops / 500))
        allocated = draw(st.integers(min_value=lower, max_value=63999))
        return {
            "storage_type": "gp3",
            "allocated_storage": allocated,
            "iops": iops,
            "storage_throughput": derive_gp3_storage_throughput(iops),
        }

    # io2: ratio iops/allocated in [0.5, 1000]
    iops = draw(st.integers(min_value=100, max_value=64000))
    # allocated in [ceil(iops/1000), floor(iops/0.5)] intersected with [100, 63999]
    lower = max(100, math.ceil(iops / 1000))
    upper = min(63999, iops * 2)
    allocated = draw(st.integers(min_value=lower, max_value=upper))
    return {"storage_type": "io2", "allocated_storage": allocated, "iops": iops}


@st.composite
def valid_intents(draw: st.DrawFn) -> dict:
    """Generate a complete, schema-valid, security-compliant Deployment_Intent."""
    engine = draw(st.sampled_from(SUPPORTED_EDITIONS))
    multi_az = draw(st.booleans())
    # multi_az=true requires backup_retention_period >= 1 (R18.10); otherwise 0-35.
    backup_retention = draw(
        st.integers(min_value=1 if multi_az else 0, max_value=35)
    )

    intent: dict = {
        "deployment_tier": draw(st.sampled_from(["sandbox", "dev", "prod"])),
        "workload_size": draw(
            st.sampled_from(["xsmall", "small", "medium", "large", "xlarge"])
        ),
        "region": "us-east-1",
        "engine": engine,
        "engine_version": "12.1.4",
        "master_username": "admin",
        "db_name": "DB2DB",
        "port": draw(
            st.integers(min_value=1, max_value=65535).filter(lambda p: p != 50443)
        ),
        "license_model": "bring-your-own-license",
        "instance_class": "db.r7i.2xlarge",
        "multi_az": multi_az,
        "backup_retention_period": backup_retention,
        "publicly_accessible": False,
        "storage_encrypted": True,
        "kms_key_id": _MRK_STORAGE_KEY,
        "vpc_security_group_ids": ["sg-0123456789abcdef0"],
        "db_subnet_group_name": "db2-subnets",
        "db_parameter_group_name": "",
        "monitoring_interval": 0,
        "enable_cloudwatch_logs_exports": ["diag.log", "notify.log"],
        "deletion_protection": True,
        "tags": {"Project": "p", "Environment": "dev", "Owner": "o"},
        "manage_master_user_password": True,
        "master_user_secret_kms_key_id": _MRK_SECRET_KEY,
        "ibm_customer_id": "IBM-CUST-001",
        "ibm_site_id": "IBM-SITE-001",
    }
    intent.update(draw(_storage_config()))
    return intent


# ---------------------------------------------------------------------------
# Sanity: the generator only emits intents that actually validate
# ---------------------------------------------------------------------------


@given(intent=valid_intents())
def test_generated_intents_are_valid(intent: dict) -> None:
    """Every generated intent passes full two-layer validation, so any rejection
    a property below observes is attributable to that property's mutation."""
    result = validate_intent(intent)
    assert result.ok, result.report()


# ---------------------------------------------------------------------------
# Property 1: Intent round-trip idempotence (R4.9)
# ---------------------------------------------------------------------------


@given(intent=valid_intents())
def test_property1_round_trip_idempotence(intent: dict) -> None:
    """validate -> serialize(json) -> re-validate stays valid and unchanged."""
    first = validate_intent(intent)
    assert first.ok, first.report()

    serialized = json.dumps(intent, sort_keys=True)
    reloaded = json.loads(serialized)

    second = validate_intent(reloaded)
    assert second.ok, second.report()

    # The round trip is lossless: re-serializing the reloaded document yields
    # the identical JSON, so validation is genuinely idempotent.
    assert json.dumps(reloaded, sort_keys=True) == serialized


# ---------------------------------------------------------------------------
# Property 2: Security invariants always hold (R6.7, R6.12)
# ---------------------------------------------------------------------------


@given(intent=valid_intents())
def test_property2_security_invariants_hold_for_valid_intents(
    intent: dict,
) -> None:
    """For all generated compliant intents the security cross-checks are clean,
    and the compliant posture is what the invariants describe (R6.7/6.12)."""
    assert validate_security_invariants(intent) == []

    # The properties the invariant set guarantees are actually present.
    assert intent["storage_encrypted"] is True
    assert "mrk-" in intent["kms_key_id"].lower()  # customer-managed MRK CMK
    assert "mrk-" in intent["master_user_secret_kms_key_id"].lower()
    assert intent["publicly_accessible"] is False  # absent acknowledgement


@given(intent=valid_intents())
def test_property2_aws_owned_storage_key_is_rejected(intent: dict) -> None:
    """Violating the CMK invariant (AWS-owned key) is always flagged."""
    intent["kms_key_id"] = "alias/aws/rds"
    rules = {e.rule for e in validate_security_invariants(intent)}
    assert "cmk_not_aws_owned" in rules


@given(intent=valid_intents())
def test_property2_non_mrk_storage_key_is_rejected(intent: dict) -> None:
    """A customer-managed but non-MRK storage CMK is always flagged (R6.11)."""
    intent["kms_key_id"] = "arn:aws:kms:us-east-1:111122223333:key/abcd-not-mrk"
    errors = validate_security_invariants(intent)
    assert any(
        e.rule == "byok_key_not_mrk" and e.layer == LAYER_SECURITY for e in errors
    )


@given(intent=valid_intents())
def test_property2_public_access_without_ack_is_rejected(intent: dict) -> None:
    """publicly_accessible=true without acknowledgement is always flagged."""
    intent["publicly_accessible"] = True
    intent.pop("public_access_acknowledged", None)
    rules = {e.rule for e in validate_security_invariants(intent)}
    assert "public_access_requires_acknowledgement" in rules


# ---------------------------------------------------------------------------
# Property 11: IBM IDs required for every edition (R7.8)
# ---------------------------------------------------------------------------


@given(
    intent=valid_intents(),
    edition=st.sampled_from(SUPPORTED_EDITIONS),
    drop=st.sampled_from(
        [("ibm_customer_id",), ("ibm_site_id",), ("ibm_customer_id", "ibm_site_id")]
    ),
)
def test_property11_missing_ibm_id_rejected_for_every_edition(
    intent: dict, edition: str, drop: tuple
) -> None:
    """An intent missing either IBM identifier is rejected for ce/se/ae alike."""
    intent["engine"] = edition
    for field in drop:
        intent.pop(field, None)

    errors = validate_security_invariants(intent)
    missing = {e.field for e in errors if e.rule == "ibm_identifier_required"}
    assert set(drop) <= missing, (edition, drop, missing)

    # And it fails the full gate, halting before any artifact (R4.4/7.8).
    result = validate_intent(intent)
    assert not result.ok


# ---------------------------------------------------------------------------
# Property 3: No fabricated parameter-group family (R5.8)
# ---------------------------------------------------------------------------


@given(
    engine=st.sampled_from(SUPPORTED_EDITIONS),
    major=st.sampled_from(["11.5", "12.1", "10.5", "12.2", "11.1", "9.7"]),
)
def test_property3_family_is_matrix_entry_or_raises(
    engine: str, major: str
) -> None:
    """For any engine+major, derivation returns a matrix entry or raises — it
    never emits a fabricated/partial family string (R5.8)."""
    try:
        family = derive_parameter_group_family(engine, major)
    except UnsupportedParameterGroupFamilyError:
        return  # rejecting an unsupported combination is the correct behaviour
    assert family in SUPPORTED_PARAMETER_GROUP_FAMILIES


@given(
    engine=st.text(min_size=0, max_size=12),
    major=st.text(min_size=0, max_size=8),
)
def test_property3_arbitrary_inputs_never_fabricate(
    engine: str, major: str
) -> None:
    """Even for arbitrary (possibly nonsensical) engine/major strings, the only
    success path returns a supported family; anything else raises."""
    try:
        family = derive_parameter_group_family(engine, major)
    except UnsupportedParameterGroupFamilyError:
        return
    assert family in SUPPORTED_PARAMETER_GROUP_FAMILIES


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
