"""Layer-1 JSON Schema property-based tests (task 2.3).

Uses Hypothesis to generate Deployment_Intent documents and asserts the schema
enforces the always-required set and the storage/port constraints that the
design's Testing Strategy calls out as property targets:

  * omitting ANY always-required field => the intent is rejected (R4.6);
  * any storage_type in {io1, gp2, standard} => rejected (R18.1);
  * port == 50443 => rejected (R18.13);
  * gp3 with allocated_storage < 400 GiB that carries iops or
    storage_throughput => rejected (R19.4).

These complement the example-based pass/fail pairs in
test_schema_conditionals.py: the examples pin specific cases, the properties
assert the rules hold across a generated input space.

**Validates: Requirements 4.6, 18.1, 18.13, 19.4**
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from jsonschema.validators import Draft202012Validator

# The always-required set from R4.6 / the schema's top-level "required" array.
ALWAYS_REQUIRED = [
    "deployment_tier",
    "workload_size",
    "region",
    "engine",
    "engine_version",
    "master_username",
    "db_name",
    "port",
    "license_model",
    "instance_class",
    "allocated_storage",
    "storage_type",
    "multi_az",
    "backup_retention_period",
    "publicly_accessible",
    "storage_encrypted",
    "vpc_id",
    "vpc_security_group_ids",
    "db_parameter_group_name",
    "monitoring_interval",
    "enable_cloudwatch_logs_exports",
    "deletion_protection",
    "tags",
]

# storage_type values RDS-for-Db2 no longer offers; the schema enum is gp3/io2
# only, so each of these must be rejected (R18.1).
REJECTED_STORAGE_TYPES = ["io1", "gp2", "standard"]


@pytest.fixture(scope="module")
def validator(schema_path: Path) -> Draft202012Validator:
    schema = json.loads(schema_path.read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _base_intent() -> dict:
    """A minimal valid Deployment_Intent (gp3 < 400 baseline, managed password,
    single-AZ, monitoring off) used as the seed each property mutates."""
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
        "manage_master_user_password": True,
        "master_user_secret_kms_key_id": "arn:aws:kms:us-east-1:111122223333:key/sec",
    }


def test_base_intent_is_valid(validator: Draft202012Validator) -> None:
    """Sanity check: the unmutated seed validates, so any rejection a property
    observes is attributable to that property's mutation, not a broken seed."""
    assert validator.is_valid(_base_intent())


# --- R4.6: omitting ANY always-required field => rejected -------------------

@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(field=st.sampled_from(ALWAYS_REQUIRED))
def test_omitting_any_required_field_is_rejected(
    validator: Draft202012Validator, field: str
) -> None:
    doc = _base_intent()
    del doc[field]
    assert not validator.is_valid(doc), f"missing required field accepted: {field}"


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(field=st.sampled_from(ALWAYS_REQUIRED))
def test_nulling_any_required_field_is_rejected(
    validator: Draft202012Validator, field: str
) -> None:
    """R4.6 rejects an always-required field that is absent OR null. A typed
    field set to null violates its declared type, so the intent is rejected."""
    doc = _base_intent()
    doc[field] = None
    assert not validator.is_valid(doc), f"null required field accepted: {field}"


# --- R18.1: io1 / gp2 / standard storage_type always rejected ---------------

@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    storage_type=st.sampled_from(REJECTED_STORAGE_TYPES),
    allocated_storage=st.integers(min_value=1, max_value=63999),
)
def test_disallowed_storage_type_is_rejected(
    validator: Draft202012Validator, storage_type: str, allocated_storage: int
) -> None:
    doc = _base_intent()
    doc["storage_type"] = storage_type
    doc["allocated_storage"] = allocated_storage
    assert not validator.is_valid(doc), f"storage_type accepted: {storage_type}"


# --- R18.13: port == 50443 (the SSL service port) is rejected ---------------

@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    other=st.dictionaries(
        keys=st.sampled_from(["master_username", "db_name", "region"]),
        values=st.text(min_size=1, max_size=8).filter(lambda s: s.strip() != ""),
        max_size=3,
    )
)
def test_port_equal_ssl_service_port_is_rejected(
    validator: Draft202012Validator, other: dict
) -> None:
    """port=50443 must be rejected regardless of the other field values."""
    doc = _base_intent()
    doc["db_name"] = "DB2DB"  # keep <=8 chars baseline; `other` may override
    doc.update(other)
    doc["db_name"] = doc["db_name"][:8] or "DB2DB"
    doc["port"] = 50443
    assert not validator.is_valid(doc), "port 50443 accepted"


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(port=st.integers(min_value=1, max_value=65535).filter(lambda p: p != 50443))
def test_valid_tcp_port_is_accepted(
    validator: Draft202012Validator, port: int
) -> None:
    """Any in-range TCP port other than 50443 keeps the baseline valid."""
    doc = _base_intent()
    doc["port"] = port
    assert validator.is_valid(doc), f"valid port rejected: {port}"


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    port=st.one_of(
        st.integers(max_value=0),
        st.integers(min_value=65536),
    )
)
def test_out_of_range_port_is_rejected(
    validator: Draft202012Validator, port: int
) -> None:
    doc = _base_intent()
    doc["port"] = port
    assert not validator.is_valid(doc), f"out-of-range port accepted: {port}"


# --- R19.4: gp3 < 400 GiB with iops or storage_throughput is rejected -------

@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    allocated_storage=st.integers(min_value=20, max_value=399),
    iops=st.integers(min_value=1, max_value=64000),
    throughput=st.one_of(st.none(), st.integers(min_value=1, max_value=4000)),
)
def test_gp3_below_400_with_iops_is_rejected(
    validator: Draft202012Validator,
    allocated_storage: int,
    iops: int,
    throughput: int | None,
) -> None:
    doc = _base_intent()
    doc["storage_type"] = "gp3"
    doc["allocated_storage"] = allocated_storage
    doc["iops"] = iops
    if throughput is not None:
        doc["storage_throughput"] = throughput
    assert not validator.is_valid(doc), (
        f"gp3<400 with iops accepted: storage={allocated_storage} iops={iops}"
    )


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    allocated_storage=st.integers(min_value=20, max_value=399),
    throughput=st.integers(min_value=1, max_value=4000),
)
def test_gp3_below_400_with_throughput_is_rejected(
    validator: Draft202012Validator, allocated_storage: int, throughput: int
) -> None:
    doc = _base_intent()
    doc["storage_type"] = "gp3"
    doc["allocated_storage"] = allocated_storage
    doc["storage_throughput"] = throughput
    assert not validator.is_valid(doc), (
        f"gp3<400 with throughput accepted: storage={allocated_storage} "
        f"throughput={throughput}"
    )


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(allocated_storage=st.integers(min_value=20, max_value=399))
def test_gp3_below_400_baseline_without_iops_is_accepted(
    validator: Draft202012Validator, allocated_storage: int
) -> None:
    """The complement of R19.4: gp3<400 carrying neither field is valid (RDS
    applies the gp3 baseline), confirming the rejection above is specific to
    the presence of iops/throughput, not to gp3<400 itself."""
    doc = _base_intent()
    doc["storage_type"] = "gp3"
    doc["allocated_storage"] = allocated_storage
    doc.pop("iops", None)
    doc.pop("storage_throughput", None)
    assert validator.is_valid(doc), (
        f"gp3<400 baseline rejected: storage={allocated_storage}"
    )
