"""Unit tests for mandatory resource tagging via default_tags (task 7.4).

These cover Requirement 14:

* R14.1/R14.2 - ``created_by`` and ``generation_model`` provenance tags are
  emitted with non-empty values; ``created_by`` equals the existing rds-db2
  skill's value.
* R14.3/R14.5 - the customer-supplied Project/Environment/Owner are present,
  each non-empty, on every created resource (via the modules' tag variables).
* R14.4/R14.7 - customer extra tags are appended WITHOUT overriding any
  mandatory key, and the total tag count is capped at 50.
* R14.6 - a missing or empty Project/Environment/Owner halts rendering and is
  reported by name (and is rejected by the Intent_Validator).

Rendering is pure text/dict generation: the module tag variables are parsed
from the real ``variables.tf`` files, so the tests confirm the composer wires
into genuine module variables (no fabricated names).
"""

from __future__ import annotations

import copy

import pytest

from scripts.render_terraform import (
    CREATED_BY_TAG_VALUE,
    DEFAULT_GENERATION_MODEL,
    MANDATORY_TAG_KEYS,
    MAX_TAGS_PER_RESOURCE,
    MandatoryTagError,
    apply_mandatory_tags,
    collect_module_variables,
    compose_mandatory_tags,
    extra_tags_for_modules,
    load_module_variable_index,
    render_terraform,
    resolve_generation_model,
)


def _base_intent() -> dict:
    """A prod, large, db2-se intent satisfying the schema's required set."""
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


@pytest.fixture
def index(terraform_modules_root):
    return load_module_variable_index(terraform_modules_root)


# ---------------------------------------------------------------------------
# compose_mandatory_tags: the five mandatory tags + extras (R14.1-14.5)
# ---------------------------------------------------------------------------


def test_all_five_mandatory_tags_present_and_non_empty(intent):
    """R14.5: the composed tag set includes all five mandatory keys, non-empty."""
    composed = compose_mandatory_tags(intent)
    for key in MANDATORY_TAG_KEYS:
        assert key in composed, f"mandatory tag '{key}' missing"
        assert composed[key].strip(), f"mandatory tag '{key}' is empty"


def test_created_by_matches_existing_skill_value(intent):
    """R14.2: created_by is the fixed value used by the existing rds-db2 skill."""
    composed = compose_mandatory_tags(intent)
    assert composed["created_by"] == CREATED_BY_TAG_VALUE == "rds-db2-skill"


def test_generation_model_non_empty_default(intent, monkeypatch):
    """R14.1/R14.2: generation_model is a non-empty model identifier."""
    monkeypatch.delenv("GENERATION_MODEL", raising=False)
    composed = compose_mandatory_tags(intent)
    assert composed["generation_model"] == DEFAULT_GENERATION_MODEL
    assert composed["generation_model"].strip()


def test_generation_model_honours_env_override(intent, monkeypatch):
    monkeypatch.setenv("GENERATION_MODEL", "claude-sonnet-4-5")
    assert resolve_generation_model() == "claude-sonnet-4-5"
    composed = compose_mandatory_tags(intent)
    assert composed["generation_model"] == "claude-sonnet-4-5"


def test_customer_mandatory_tags_carried_through(intent):
    """R14.3: Project/Environment/Owner come from the intent's tags."""
    composed = compose_mandatory_tags(intent)
    assert composed["Project"] == "ACME"
    assert composed["Environment"] == "prod"
    assert composed["Owner"] == "db-team"


# ---------------------------------------------------------------------------
# Customer extras appended WITHOUT overriding mandatory keys (R14.4/R14.7)
# ---------------------------------------------------------------------------


def test_customer_extra_tags_are_appended(intent):
    """R14.7: arbitrary customer tags are kept alongside the mandatory set."""
    intent["tags"]["CostCenter"] = "CC-42"
    intent["tags"]["Team"] = "payments"
    composed = compose_mandatory_tags(intent)
    assert composed["CostCenter"] == "CC-42"
    assert composed["Team"] == "payments"
    # The five mandatory keys still present.
    for key in MANDATORY_TAG_KEYS:
        assert key in composed


def test_extra_tag_cannot_override_mandatory_key(intent):
    """R14.4: a customer tag colliding with a mandatory key must NOT win."""
    # Customer attempts to override every mandatory key.
    intent["tags"]["created_by"] = "attacker"
    intent["tags"]["generation_model"] = "fake-model"
    intent["tags"]["Project"] = "ACME"  # legitimate (also a mandatory)
    composed = compose_mandatory_tags(intent)
    # Provenance keys keep the skill-set values, not the customer's.
    assert composed["created_by"] == CREATED_BY_TAG_VALUE
    assert composed["generation_model"] != "fake-model"


def test_extra_tags_for_modules_excludes_mandatory_and_managed_by(intent):
    intent["tags"]["CostCenter"] = "CC-42"
    composed = compose_mandatory_tags(intent)
    extras = extra_tags_for_modules(composed)
    assert extras == {"CostCenter": "CC-42"}
    for key in MANDATORY_TAG_KEYS:
        assert key not in extras
    assert "ManagedBy" not in extras


# ---------------------------------------------------------------------------
# <= 50 tags per resource (R14.4)
# ---------------------------------------------------------------------------


def test_tag_count_at_limit_is_accepted(intent):
    """Exactly 50 tags total is allowed (R14.4)."""
    # Already 3 customer mandatory + ManagedBy + 2 provenance = 6 distinct keys
    # after composition. Add extras to reach exactly 50.
    composed_base = compose_mandatory_tags(intent)
    headroom = MAX_TAGS_PER_RESOURCE - len(composed_base)
    for i in range(headroom):
        intent["tags"][f"extra_{i}"] = str(i)
    composed = compose_mandatory_tags(intent)
    assert len(composed) == MAX_TAGS_PER_RESOURCE  # no raise


def test_tag_count_over_limit_is_rejected(intent):
    """R14.4: more than 50 tags total halts with a clear error."""
    for i in range(60):
        intent["tags"][f"extra_{i}"] = str(i)
    with pytest.raises(MandatoryTagError) as exc:
        compose_mandatory_tags(intent)
    assert "50" in str(exc.value)


# ---------------------------------------------------------------------------
# Missing / empty mandatory tag halts + names it (R14.6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_key", ["Project", "Environment", "Owner"])
def test_missing_mandatory_tag_halts_and_names_it(intent, missing_key):
    del intent["tags"][missing_key]
    with pytest.raises(MandatoryTagError) as exc:
        compose_mandatory_tags(intent)
    assert missing_key in str(exc.value)
    assert missing_key in exc.value.fields


@pytest.mark.parametrize("empty_key", ["Project", "Environment", "Owner"])
def test_empty_mandatory_tag_halts_and_names_it(intent, empty_key):
    intent["tags"][empty_key] = "   "
    with pytest.raises(MandatoryTagError) as exc:
        compose_mandatory_tags(intent)
    assert empty_key in str(exc.value)


def test_missing_tags_object_halts(intent):
    del intent["tags"]
    with pytest.raises(MandatoryTagError):
        compose_mandatory_tags(intent)


def test_render_terraform_halts_on_missing_mandatory_tag(intent, terraform_modules_root):
    """Full render path halts and emits no files for a missing mandatory tag."""
    del intent["tags"]["Owner"]
    with pytest.raises(MandatoryTagError) as exc:
        render_terraform(intent, modules_root=terraform_modules_root)
    assert "Owner" in str(exc.value)


# ---------------------------------------------------------------------------
# Wiring into the real module tag variables (R14.1/R14.3, no fabricated names)
# ---------------------------------------------------------------------------


def test_tag_variables_wired_into_every_intent_module(intent, index):
    composed = apply_mandatory_tags(
        intent,
        modules := collect_module_variables(intent, variable_index=index),
        variable_index=index,
    )
    # 5-rds carries the provenance + customer tag variables.
    rds = modules["5-rds"].variables
    assert rds["tag"] == "ACME"
    assert rds["owner"] == "db-team"
    assert rds["created_by"] == CREATED_BY_TAG_VALUE
    assert rds["generation_model"] == composed["generation_model"]
    assert rds["environment"] == "prod"  # from deployment_tier
    assert isinstance(rds["extra_tags"], dict)


def test_extra_tags_variable_rendered_for_modules(intent, index):
    intent["tags"]["CostCenter"] = "CC-42"
    modules = collect_module_variables(intent, variable_index=index)
    apply_mandatory_tags(intent, modules, variable_index=index)
    assert modules["5-rds"].variables["extra_tags"] == {"CostCenter": "CC-42"}


def test_rendered_tfvars_use_real_tag_variable_names(intent, terraform_modules_root):
    result = render_terraform(intent, modules_root=terraform_modules_root)
    index = load_module_variable_index(terraform_modules_root)
    for module, rendered in result.modules.items():
        declared = index[module]
        for name in rendered.variables:
            assert name in declared, f"{module}: {name} is not a real variable"
    # The provenance tags actually appear in the rendered RDS tfvars text.
    rds_text = result.files["5-rds/terraform.tfvars"]
    assert 'created_by = "rds-db2-skill"' in rds_text
    assert "generation_model =" in rds_text


def test_collect_module_variables_composes_tags(intent, index):
    """collect_module_variables now runs the mandatory-tag composition (R14)."""
    modules = collect_module_variables(intent, variable_index=index)
    rds = modules["5-rds"].variables
    assert rds["created_by"] == CREATED_BY_TAG_VALUE
    assert rds["tag"] == "ACME"
    assert rds["owner"] == "db-team"
