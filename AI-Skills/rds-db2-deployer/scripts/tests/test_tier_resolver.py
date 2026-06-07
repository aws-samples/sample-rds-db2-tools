"""Unit tests for the Tier_Resolver (task 3.1, Requirement 3).

Covers tier selection from the Environment tag and/or a named tier, conflict
and unknown-value rejection, the sandbox default, the baseline "Deploy RDS for
Db2 instance" field set (R3.4), the prod posture (R3.5), override layering
(R3.6), and the recorded tier / forced Environment tag (R3.7).

These are pure (no AWS); engine-version minor resolution and the identifier
builder land in tasks 3.3/3.4, so the baseline here asserts the major-version
seed (12.1) only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema.validators import Draft202012Validator

from scripts.resolve_intent import (
    DEFAULT_MAJOR_VERSION,
    DEFAULT_TCP_LISTENER_PORT,
    DEFAULT_TIER,
    ENHANCED_MONITORING_INTERVAL,
    SSL_SERVICE_PORT,
    SUPPORTED_TIERS,
    IntentResolutionError,
    TierConflictError,
    UnknownTierError,
    instance_class_family,
    resolve_tier,
    select_tier,
)


# --- R3.1 / R3.2 / R3.3 / R3.8: tier selection -----------------------------


def test_three_supported_tiers() -> None:
    assert SUPPORTED_TIERS == ("sandbox", "dev", "prod")


def test_named_tier_selected_as_user_provided() -> None:
    tier, prov = select_tier(named_tier="prod")
    assert tier == "prod"
    assert prov == "user_provided"


def test_environment_tag_selected_as_user_provided() -> None:
    tier, prov = select_tier(environment_tag="dev")
    assert tier == "dev"
    assert prov == "user_provided"


def test_matching_named_tier_and_environment_tag_is_fine() -> None:
    tier, prov = select_tier(named_tier="prod", environment_tag="prod")
    assert tier == "prod"
    assert prov == "user_provided"


def test_neither_present_defaults_to_sandbox_assumed() -> None:
    tier, prov = select_tier()
    assert tier == DEFAULT_TIER == "sandbox"
    assert prov == "assumed"


@pytest.mark.parametrize("value", ["staging", "production", "PROD-1", "qa"])
def test_unknown_named_tier_is_rejected_with_supported_values(value: str) -> None:
    with pytest.raises(UnknownTierError) as exc:
        select_tier(named_tier=value)
    msg = str(exc.value)
    for supported in SUPPORTED_TIERS:
        assert supported in msg
    assert value.strip().lower() in msg


def test_unknown_environment_tag_is_rejected() -> None:
    with pytest.raises(UnknownTierError) as exc:
        select_tier(environment_tag="staging")
    assert "Environment tag" in str(exc.value)


def test_conflicting_named_tier_and_environment_tag_is_rejected_by_name() -> None:
    with pytest.raises(TierConflictError) as exc:
        select_tier(named_tier="prod", environment_tag="dev")
    msg = str(exc.value)
    assert "prod" in msg and "dev" in msg


def test_resolution_errors_share_a_base_class() -> None:
    assert issubclass(UnknownTierError, IntentResolutionError)
    assert issubclass(TierConflictError, IntentResolutionError)


def test_case_and_whitespace_are_normalized() -> None:
    tier, prov = select_tier(named_tier="  PROD ")
    assert tier == "prod"
    assert prov == "user_provided"


# --- R3.4: baseline "Deploy RDS for Db2 instance" field set -----------------


def test_baseline_field_set_matches_r3_4() -> None:
    result = resolve_tier()  # bare prompt -> sandbox baseline
    intent = result.intent

    assert result.resolved_tier == "sandbox"
    assert intent["engine_version"] == DEFAULT_MAJOR_VERSION == "12.1"
    assert intent["allocated_storage"] == 40
    assert intent["storage_type"] == "gp3"
    assert intent["multi_az"] is False
    assert intent["instance_class"] == "db.t3.xlarge"
    assert intent["backup_retention_period"] == 1
    assert intent["db_name"] == "DB2DB"
    assert intent["enable_cloudwatch_logs_exports"] == ["diag.log"]
    assert intent["monitoring_interval"] == ENHANCED_MONITORING_INTERVAL > 0
    assert intent["port"] == DEFAULT_TCP_LISTENER_PORT == 8392
    assert SSL_SERVICE_PORT == 50443
    assert intent["publicly_accessible"] is False
    assert intent["storage_encrypted"] is True


def test_baseline_fields_are_all_assumed_provenance() -> None:
    result = resolve_tier()
    for key in (
        "instance_class",
        "allocated_storage",
        "storage_type",
        "multi_az",
        "backup_retention_period",
        "db_name",
        "port",
    ):
        assert result.provenance[key] == "assumed"


# --- R3.5: prod posture ------------------------------------------------------


def test_prod_posture_matches_r3_5() -> None:
    result = resolve_tier(named_tier="prod")
    intent = result.intent

    assert result.resolved_tier == "prod"
    assert intent["multi_az"] is True
    assert instance_class_family(intent["instance_class"]) == "r"
    assert intent["backup_retention_period"] >= 7
    assert intent["deletion_protection"] is True


def test_sandbox_and_dev_do_not_force_prod_posture() -> None:
    sandbox = resolve_tier(named_tier="sandbox").intent
    assert sandbox["multi_az"] is False
    assert sandbox["deletion_protection"] is False

    dev = resolve_tier(named_tier="dev").intent
    assert dev["multi_az"] is False
    assert dev["backup_retention_period"] == 7


# --- R3.6: tier defaults first, then prompt overrides -----------------------


def test_override_supersedes_tier_default_and_is_recorded() -> None:
    result = resolve_tier(
        named_tier="prod",
        overrides={"backup_retention_period": 14, "instance_class": "db.r7i.2xlarge"},
    )
    intent = result.intent

    assert intent["backup_retention_period"] == 14
    assert intent["instance_class"] == "db.r7i.2xlarge"
    assert result.provenance["backup_retention_period"] == "user_provided"
    assert result.provenance["instance_class"] == "user_provided"

    # The superseded prod defaults are recorded for the Verification_Step (R2.5).
    assert result.superseded_tier_defaults["backup_retention_period"] == 7
    assert result.superseded_tier_defaults["instance_class"] == "db.r7i.xlarge"


def test_override_equal_to_default_is_not_recorded_as_superseded() -> None:
    # Overriding with the same value the tier already had is still user_provided
    # but is not a "superseded" change.
    result = resolve_tier(named_tier="prod", overrides={"multi_az": True})
    assert result.provenance["multi_az"] == "user_provided"
    assert "multi_az" not in result.superseded_tier_defaults


def test_new_field_override_has_no_superseded_entry() -> None:
    result = resolve_tier(overrides={"region": "us-east-1"})
    assert result.intent["region"] == "us-east-1"
    assert result.provenance["region"] == "user_provided"
    assert "region" not in result.superseded_tier_defaults


# --- R3.7: recorded tier and forced Environment tag -------------------------


def test_resolved_tier_and_environment_tag_match() -> None:
    result = resolve_tier(named_tier="prod")
    assert result.intent["deployment_tier"] == "prod"
    assert result.intent["tags"]["Environment"] == "prod"


def test_environment_tag_forced_even_when_user_tags_disagree() -> None:
    # A user tag block that tries to set a different Environment must not win
    # over the resolved tier (R3.7).
    result = resolve_tier(
        named_tier="dev",
        overrides={"tags": {"Project": "ACME", "Environment": "sandbox"}},
    )
    assert result.intent["tags"]["Environment"] == "dev"
    assert result.intent["tags"]["Project"] == "ACME"


def test_default_tier_sets_environment_tag_to_sandbox() -> None:
    result = resolve_tier()
    assert result.intent["tags"]["Environment"] == "sandbox"


# --- R3.9: determinism -------------------------------------------------------


@pytest.mark.parametrize("tier", SUPPORTED_TIERS)
def test_same_tier_resolves_identically_each_time(tier: str) -> None:
    first = resolve_tier(named_tier=tier).intent
    second = resolve_tier(named_tier=tier).intent
    assert first == second


def test_mutating_one_result_does_not_leak_into_the_next() -> None:
    # Guards the deep-copy of mutable defaults (lists) across resolutions.
    first = resolve_tier()
    first.intent["enable_cloudwatch_logs_exports"].append("notify.log")
    second = resolve_tier()
    assert second.intent["enable_cloudwatch_logs_exports"] == ["diag.log"]


# --- Schema alignment: resolved tier fields validate against the schema -----


def test_resolved_tier_fields_satisfy_schema_for_their_keys(schema_path: Path) -> None:
    """The fields the tier layer sets must each be individually schema-valid.

    The tier layer does not produce a *complete* intent (region, engine,
    kms_key_id, etc. come from other resolvers), so we assert per-field validity
    against the schema's property subschemas rather than whole-document validity.
    """
    schema = json.loads(schema_path.read_text())
    Draft202012Validator.check_schema(schema)
    props = schema["properties"]
    intent = resolve_tier(named_tier="prod").intent

    for key, value in intent.items():
        if key in ("_provenance", "_superseded_tier_defaults"):
            continue
        if key not in props:
            continue
        sub = Draft202012Validator(props[key])
        errors = sorted(sub.iter_errors(value), key=str)
        assert errors == [], f"{key}={value!r}: {[e.message for e in errors]}"
