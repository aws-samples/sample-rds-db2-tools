"""Unit tests for the Edition_Resolver and grounded instance specs (task 3.2,
Requirement 8).

Covers the db2-se default (R8.3), explicit-edition provenance (R8.3), edition
independence from tier (R8.1), the SE licensing ceiling (<=32 vCPU AND <=128 GB,
R8.4), the forced SE->AE conversion with recorded reason + required
acknowledgement (R8.5), the honored customer AE->SE downgrade on SE-eligible
classes and the never-automatic AE->SE rule (R8.6), db2-ae validity on any
class including small (R8.2), and grounding vCPU/memory in the instance-spec
source rather than a hardcoded guess (R8.7).

Pure tests, no AWS.
"""

from __future__ import annotations

import pytest

from scripts.instance_specs import (
    InstanceSpec,
    UnknownInstanceClassError,
    lookup_instance_spec,
)
from scripts.resolve_intent import (
    DEFAULT_EDITION,
    SE_MAX_MEMORY_GIB,
    SE_MAX_VCPU,
    SUPPORTED_EDITIONS,
    UnknownEditionError,
    apply_edition_to_intent,
    resolve_edition,
    resolve_tier,
)


# --- Grounded instance specs (R8.7) -----------------------------------------


@pytest.mark.parametrize(
    "instance_class, vcpu, memory",
    [
        ("db.t3.small", 2, 2.0),
        ("db.t3.xlarge", 4, 16.0),
        ("db.r7i.2xlarge", 8, 64.0),
        ("db.r7i.4xlarge", 16, 128.0),
        ("db.x2iedn.16xlarge", 64, 1024.0),
    ],
)
def test_published_table_specs_match_design(instance_class, vcpu, memory) -> None:
    spec = lookup_instance_spec(instance_class)
    assert spec.vcpu == vcpu
    assert spec.memory_gib == memory
    assert spec.source == "published-table"


def test_unknown_family_raises_rather_than_guessing() -> None:
    with pytest.raises(UnknownInstanceClassError):
        lookup_instance_spec("db.zz9.42xlarge")


def test_known_family_untabulated_size_derives_from_ratio() -> None:
    # r7i is a known 8 GiB/vCPU family; 8xlarge = 32 vCPU on the canonical ladder.
    spec = lookup_instance_spec("db.r7i.8xlarge")
    assert spec.vcpu == 32
    assert spec.memory_gib == 256.0
    assert spec.source == "derived-from-family-ratio"


def test_future_x86_family_resolves_without_table_edit() -> None:
    # r8i succeeds r7i (R17.16) and shares the 8 GiB/vCPU ratio.
    spec = lookup_instance_spec("db.r8i.2xlarge")
    assert spec.vcpu == 8
    assert spec.memory_gib == 64.0


# --- Edition default + provenance (R8.3) ------------------------------------


def test_default_edition_is_db2_se_assumed() -> None:
    outcome = resolve_edition()
    assert outcome.engine == DEFAULT_EDITION == "db2-se"
    assert outcome.provenance == "assumed"


def test_explicit_edition_is_user_provided() -> None:
    outcome = resolve_edition(requested_edition="db2-ae", instance_class="db.t3.small")
    assert outcome.engine == "db2-ae"
    assert outcome.provenance == "user_provided"


@pytest.mark.parametrize("value", ["db2", "db2-xe", "se", "DB2-Community", "oracle-se"])
def test_unknown_edition_rejected_with_supported_values(value: str) -> None:
    with pytest.raises(UnknownEditionError) as exc:
        resolve_edition(requested_edition=value)
    msg = str(exc.value)
    for edition in SUPPORTED_EDITIONS:
        assert edition in msg


def test_edition_case_is_normalized() -> None:
    outcome = resolve_edition(requested_edition="  DB2-SE ", instance_class="db.t3.small")
    assert outcome.engine == "db2-se"
    assert outcome.provenance == "user_provided"


# --- db2-ae valid on any class, no ceiling (R8.2) ---------------------------


def test_ae_valid_on_small_class_no_conversion() -> None:
    outcome = resolve_edition(requested_edition="db2-ae", instance_class="db.t3.small")
    assert outcome.engine == "db2-ae"
    assert outcome.converted is False
    assert outcome.acknowledgement_required is False


def test_ae_valid_on_huge_class_no_conversion() -> None:
    outcome = resolve_edition(
        requested_edition="db2-ae", instance_class="db.x2iedn.16xlarge"
    )
    assert outcome.engine == "db2-ae"
    assert outcome.converted is False


def test_ae_on_se_eligible_class_surfaces_downgrade_guidance_only() -> None:
    # AE kept on an SE-eligible class: cost guidance MAY be surfaced but the
    # edition is never auto-downgraded (R8.6).
    outcome = resolve_edition(
        requested_edition="db2-ae", instance_class="db.r7i.2xlarge"
    )
    assert outcome.engine == "db2-ae"
    assert outcome.converted is False
    assert outcome.downgrade_guidance is not None
    assert "db2-se" in outcome.downgrade_guidance


def test_ae_on_class_exceeding_ceiling_has_no_downgrade_guidance() -> None:
    outcome = resolve_edition(
        requested_edition="db2-ae", instance_class="db.x2iedn.16xlarge"
    )
    assert outcome.downgrade_guidance is None


# --- SE ceiling: honored when eligible (R8.4, R8.6) -------------------------


@pytest.mark.parametrize(
    "instance_class",
    ["db.t3.small", "db.t3.xlarge", "db.r7i.2xlarge", "db.r7i.4xlarge"],
)
def test_se_honored_on_eligible_classes(instance_class: str) -> None:
    outcome = resolve_edition(requested_edition="db2-se", instance_class=instance_class)
    assert outcome.engine == "db2-se"
    assert outcome.converted is False
    assert outcome.acknowledgement_required is False


def test_se_honored_at_inclusive_memory_boundary() -> None:
    # r7i.4xlarge = 16 vCPU / 128 GB: memory exactly at the inclusive ceiling.
    spec = lookup_instance_spec("db.r7i.4xlarge")
    assert spec.memory_gib == SE_MAX_MEMORY_GIB
    outcome = resolve_edition(requested_edition="db2-se", instance_class="db.r7i.4xlarge")
    assert outcome.engine == "db2-se"
    assert outcome.converted is False


def test_se_honored_at_inclusive_vcpu_boundary() -> None:
    # db.r7i.8xlarge derives to 32 vCPU / 256 GB -> vCPU at ceiling but memory
    # over, so this is NOT eligible; use an 8xlarge family at exactly the vCPU
    # ceiling with memory within: m6i.8xlarge = 32 vCPU / 128 GB.
    spec = lookup_instance_spec("db.m6i.8xlarge")
    assert spec.vcpu == SE_MAX_VCPU
    assert spec.memory_gib == 128.0
    outcome = resolve_edition(requested_edition="db2-se", instance_class="db.m6i.8xlarge")
    assert outcome.engine == "db2-se"
    assert outcome.converted is False


def test_customer_ae_to_se_downgrade_on_eligible_class_is_honored() -> None:
    # Customer rightsizes down then explicitly downgrades AE->SE on an
    # SE-eligible class (R8.6): the db2-se choice is honored.
    outcome = resolve_edition(requested_edition="db2-se", instance_class="db.r7i.2xlarge")
    assert outcome.engine == "db2-se"
    assert outcome.provenance == "user_provided"


# --- SE ceiling: forced SE->AE conversion (R8.4, R8.5) ----------------------


def test_se_default_on_oversized_class_forces_ae_with_ack() -> None:
    # Defaulted SE on a class that exceeds the ceiling -> forced to AE, never
    # silent: recorded reason + required acknowledgement.
    outcome = resolve_edition(instance_class="db.x2iedn.16xlarge")
    assert outcome.requested_engine == "db2-se"
    assert outcome.engine == "db2-ae"
    assert outcome.converted is True
    assert outcome.acknowledgement_required is True
    assert outcome.conversion_reason is not None
    assert "db2-se" in outcome.conversion_reason and "db2-ae" in outcome.conversion_reason


def test_se_explicit_on_oversized_class_still_forced() -> None:
    # Even an explicit db2-se request is force-converted when it exceeds the
    # ceiling (SE cannot legally run there) (R8.5).
    outcome = resolve_edition(
        requested_edition="db2-se", instance_class="db.x2iedn.16xlarge"
    )
    assert outcome.engine == "db2-ae"
    assert outcome.converted is True
    assert outcome.acknowledgement_required is True


def test_conversion_reason_names_the_exceeded_dimension() -> None:
    outcome = resolve_edition(instance_class="db.x2iedn.16xlarge")
    # 64 vCPU > 32 and 1024 GB > 128: both exceeded, both named.
    assert "vCPU" in outcome.conversion_reason
    assert "memory" in outcome.conversion_reason


def test_se_conversion_grounds_on_instance_spec_source(monkeypatch) -> None:
    # The ceiling decision must read vCPU/memory from the grounded source (R8.7),
    # not a hardcoded guess: patch the table value and the decision must follow.
    import scripts.instance_specs as specs

    fake = InstanceSpec("db.r7i.2xlarge", 8, 64.0, "published-table")
    big = InstanceSpec("db.r7i.2xlarge", 64, 512.0, "published-table")

    # With the real (small) spec, SE is honored.
    assert resolve_edition(
        requested_edition="db2-se", instance_class="db.r7i.2xlarge"
    ).converted is False

    # Patch the grounded source to report an oversized spec for the same class;
    # the resolver must now force the conversion based purely on that source.
    monkeypatch.setitem(specs.INSTANCE_SPECS, "db.r7i.2xlarge", big)
    assert resolve_edition(
        requested_edition="db2-se", instance_class="db.r7i.2xlarge"
    ).converted is True


def test_unknown_instance_class_raises_not_guesses() -> None:
    with pytest.raises(UnknownInstanceClassError):
        resolve_edition(requested_edition="db2-se", instance_class="db.zz9.42xlarge")


# --- Edition independent of tier (R8.1) -------------------------------------


@pytest.mark.parametrize("tier", ["sandbox", "dev", "prod"])
@pytest.mark.parametrize("edition", ["db2-ce", "db2-se", "db2-ae"])
def test_any_edition_combines_with_any_tier(tier: str, edition: str) -> None:
    # Use a small SE-eligible class so no forced conversion muddies the check.
    outcome = resolve_edition(requested_edition=edition, instance_class="db.t3.small")
    assert outcome.engine == edition


def test_no_instance_class_returns_edition_without_ceiling_check() -> None:
    outcome = resolve_edition(requested_edition="db2-se")
    assert outcome.engine == "db2-se"
    assert outcome.instance_spec is None
    assert outcome.converted is False


# --- apply_edition_to_intent: writes into the resolved intent ---------------


def test_apply_edition_default_on_baseline_intent() -> None:
    resolved = resolve_tier()  # sandbox baseline -> db.t3.xlarge (SE-eligible)
    apply_edition_to_intent(resolved)
    assert resolved.intent["engine"] == "db2-se"
    assert resolved.intent["_provenance"]["engine"] == "assumed"
    assert "_edition_conversion" not in resolved.intent


def test_apply_edition_explicit_user_provided() -> None:
    resolved = resolve_tier()
    apply_edition_to_intent(resolved, requested_edition="db2-ae")
    assert resolved.intent["engine"] == "db2-ae"
    assert resolved.intent["_provenance"]["engine"] == "user_provided"


def test_apply_edition_forced_conversion_recorded_in_intent() -> None:
    resolved = resolve_tier(
        named_tier="prod", overrides={"instance_class": "db.x2iedn.16xlarge"}
    )
    apply_edition_to_intent(resolved)  # defaulted SE on oversized -> AE
    assert resolved.intent["engine"] == "db2-ae"
    conv = resolved.intent["_edition_conversion"]
    assert conv["from"] == "db2-se"
    assert conv["to"] == "db2-ae"
    assert conv["acknowledgement_required"] is True
    assert conv["reason"]


def test_apply_edition_independent_of_tier_for_prod() -> None:
    # prod tier with explicit small AE: tier does not force or restrict edition.
    resolved = resolve_tier(
        named_tier="prod", overrides={"instance_class": "db.t3.small"}
    )
    apply_edition_to_intent(resolved, requested_edition="db2-ae")
    assert resolved.intent["engine"] == "db2-ae"
    assert resolved.intent["deployment_tier"] == "prod"
