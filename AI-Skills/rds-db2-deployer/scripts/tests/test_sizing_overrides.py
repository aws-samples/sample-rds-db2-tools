"""Unit tests for sizing overrides, the x86-only instance-class guard, and the
default-size baseline (task 4.2, Requirements 17.6, 17.8, 17.10, 17.13-17.16).

These cover the three behaviours task 4.2 adds on top of the task-4.1
Workload_Sizing_Map:

* Explicit sizing-field overrides win over the map value and are marked
  ``user_provided`` while the rest stay ``assumed`` from the map (R17.6, R17.14);
  an unknown ``workload_size`` is rejected (R17.8).
* ``instance_class`` is restricted to x86 (Intel/AMD): Graviton/ARM classes
  (``r8g`` and any ``*g`` family) are rejected with a clear message, while future
  x86 families (``r8i``/``r8a``/``x8i``) are accepted (R17.13, R17.15, R17.16).
* When neither a size nor a sizing field is given, the resolved sizing equals
  the R3.4 baseline ``db.t3.xlarge``/``gp3``/40 (R17.7/R17.10).

Pure tests, no AWS.
"""

from __future__ import annotations

import pytest

from scripts.resolve_intent import (
    NonX86InstanceClassError,
    UnknownWorkloadSizeError,
    apply_default_sizing_to_intent,
    apply_sizing_to_intent,
    assert_x86_instance_class,
    is_graviton_instance_class,
    is_x86_instance_class,
    instance_class_family_token,
    resolve_tier,
)


# ---------------------------------------------------------------------------
# x86-only guard (R17.15, R17.16)
# ---------------------------------------------------------------------------


class TestInstanceClassFamilyToken:
    @pytest.mark.parametrize(
        "instance_class,expected",
        [
            ("db.r7i.2xlarge", "r7i"),
            ("db.x2iedn.16xlarge", "x2iedn"),
            ("db.r8g.large", "r8g"),
            ("db.t3.small", "t3"),
            ("DB.R7I.LARGE", "r7i"),  # case-normalized
            ("malformed", ""),
            ("", ""),
        ],
    )
    def test_family_token(self, instance_class: str, expected: str) -> None:
        assert instance_class_family_token(instance_class) == expected


class TestGravitonDetection:
    @pytest.mark.parametrize(
        "instance_class",
        [
            "db.r8g.large",
            "db.r7g.2xlarge",
            "db.m7g.xlarge",
            "db.c7g.large",
            "db.x2gd.16xlarge",  # graviton high-memory (suffix 'gd')
            "db.c7gn.large",     # graviton network-optimized (suffix 'gn')
        ],
    )
    def test_graviton_classes_detected(self, instance_class: str) -> None:
        assert is_graviton_instance_class(instance_class) is True
        assert is_x86_instance_class(instance_class) is False

    @pytest.mark.parametrize(
        "instance_class",
        [
            "db.r7i.2xlarge",     # Intel
            "db.r8i.4xlarge",     # future Intel
            "db.r8a.4xlarge",     # future AMD
            "db.x8i.24xlarge",    # future high-memory Intel
            "db.x2iedn.16xlarge", # high-memory Intel (suffix 'iedn')
            "db.t3.xlarge",       # burstable, no processor suffix
            "db.r6a.large",       # AMD
            "db.m6i.large",       # Intel
        ],
    )
    def test_x86_classes_accepted(self, instance_class: str) -> None:
        assert is_graviton_instance_class(instance_class) is False
        assert is_x86_instance_class(instance_class) is True

    def test_malformed_family_is_not_graviton(self) -> None:
        # Cannot positively identify ARM -> not rejected here (R17.15 only
        # rejects what it can identify as Graviton).
        assert is_graviton_instance_class("db.weird") is False
        assert is_graviton_instance_class("garbage") is False


class TestAssertX86:
    def test_graviton_raises_with_clear_message(self) -> None:
        with pytest.raises(NonX86InstanceClassError) as exc:
            assert_x86_instance_class("db.r8g.4xlarge")
        msg = str(exc.value)
        assert "db.r8g.4xlarge" in msg
        assert "Graviton" in msg or "ARM" in msg

    def test_x86_passes(self) -> None:
        assert assert_x86_instance_class("db.r8i.4xlarge") is None


# ---------------------------------------------------------------------------
# Sizing overrides (R17.6, R17.14)
# ---------------------------------------------------------------------------


class TestSizingOverrides:
    def test_instance_class_override_keeps_rest_from_map(self) -> None:
        resolved = resolve_tier()
        apply_sizing_to_intent(
            resolved,
            workload_size="medium",
            overrides={"instance_class": "db.r8i.2xlarge"},
        )
        # Override applied + marked user_provided.
        assert resolved.intent["instance_class"] == "db.r8i.2xlarge"
        assert resolved.provenance["instance_class"] == "user_provided"
        # Remaining sizing fields stay from the map, marked assumed.
        assert resolved.intent["storage_type"] == "gp3"
        assert resolved.intent["allocated_storage"] == 3000
        assert resolved.intent["iops"] == 64000
        assert resolved.intent["storage_throughput"] == 4000
        for field in ("storage_type", "allocated_storage", "iops", "storage_throughput"):
            assert resolved.provenance[field] == "assumed"

    def test_multiple_overrides_marked_user_provided(self) -> None:
        resolved = resolve_tier()
        apply_sizing_to_intent(
            resolved,
            workload_size="small",
            overrides={"allocated_storage": 800, "iops": 30000},
        )
        assert resolved.intent["allocated_storage"] == 800
        assert resolved.intent["iops"] == 30000
        assert resolved.provenance["allocated_storage"] == "user_provided"
        assert resolved.provenance["iops"] == "user_provided"
        # Unoverridden field stays mapped/assumed.
        assert resolved.intent["instance_class"] == "db.t3.xlarge"
        assert resolved.provenance["instance_class"] == "assumed"

    def test_override_to_future_x86_family_accepted(self) -> None:
        resolved = resolve_tier()
        apply_sizing_to_intent(
            resolved,
            workload_size="xlarge",
            overrides={"instance_class": "db.x8i.24xlarge"},
        )
        assert resolved.intent["instance_class"] == "db.x8i.24xlarge"
        assert resolved.provenance["instance_class"] == "user_provided"

    def test_graviton_override_rejected(self) -> None:
        resolved = resolve_tier()
        with pytest.raises(NonX86InstanceClassError):
            apply_sizing_to_intent(
                resolved,
                workload_size="large",
                overrides={"instance_class": "db.r8g.4xlarge"},
            )

    def test_none_override_is_ignored(self) -> None:
        # An explicit None means "no override", so the map value stands.
        resolved = resolve_tier()
        apply_sizing_to_intent(
            resolved,
            workload_size="medium",
            overrides={"instance_class": None},
        )
        assert resolved.intent["instance_class"] == "db.r7i.2xlarge"
        assert resolved.provenance["instance_class"] == "assumed"

    def test_unknown_size_rejected_with_supported_list(self) -> None:
        resolved = resolve_tier()
        with pytest.raises(UnknownWorkloadSizeError) as exc:
            apply_sizing_to_intent(resolved, workload_size="ginormous")
        assert "ginormous" in str(exc.value)

    def test_storage_type_override_to_io2_keeps_other_fields(self) -> None:
        # Cross-field consistency is the validator's job (R17.9); the resolver
        # just records the override faithfully.
        resolved = resolve_tier()
        apply_sizing_to_intent(
            resolved,
            workload_size="medium",
            overrides={"storage_type": "io2"},
        )
        assert resolved.intent["storage_type"] == "io2"
        assert resolved.provenance["storage_type"] == "user_provided"
        assert resolved.intent["allocated_storage"] == 3000


# ---------------------------------------------------------------------------
# Default sizing baseline (R17.7, R17.10)
# ---------------------------------------------------------------------------


class TestDefaultSizing:
    def test_default_equals_r34_baseline(self) -> None:
        resolved = resolve_tier()
        apply_default_sizing_to_intent(resolved)
        assert resolved.intent["instance_class"] == "db.t3.xlarge"
        assert resolved.intent["storage_type"] == "gp3"
        assert resolved.intent["allocated_storage"] == 40

    def test_default_carries_no_iops_or_throughput(self) -> None:
        # gp3 < 400 GiB -> RDS baseline, no IOPS/throughput (R19.4).
        resolved = resolve_tier()
        # Seed stray performance fields to prove they are cleared.
        resolved.intent["iops"] = 9999
        resolved.provenance["iops"] = "assumed"
        resolved.intent["storage_throughput"] = 4000
        resolved.provenance["storage_throughput"] = "assumed"

        apply_default_sizing_to_intent(resolved)

        assert "iops" not in resolved.intent
        assert "iops" not in resolved.provenance
        assert "storage_throughput" not in resolved.intent
        assert "storage_throughput" not in resolved.provenance

    def test_default_fields_marked_assumed(self) -> None:
        resolved = resolve_tier()
        apply_default_sizing_to_intent(resolved)
        for field in ("instance_class", "storage_type", "allocated_storage"):
            assert resolved.provenance[field] == "assumed"

    def test_default_does_not_set_workload_size(self) -> None:
        resolved = resolve_tier()
        apply_default_sizing_to_intent(resolved)
        assert "workload_size" not in resolved.intent

    def test_default_with_single_field_override(self) -> None:
        # "No size but one explicit sizing field" routes here; the field wins.
        resolved = resolve_tier()
        apply_default_sizing_to_intent(
            resolved, overrides={"instance_class": "db.r8a.2xlarge"}
        )
        assert resolved.intent["instance_class"] == "db.r8a.2xlarge"
        assert resolved.provenance["instance_class"] == "user_provided"
        # Other baseline fields stay assumed.
        assert resolved.intent["storage_type"] == "gp3"
        assert resolved.intent["allocated_storage"] == 40

    def test_default_graviton_override_rejected(self) -> None:
        resolved = resolve_tier()
        with pytest.raises(NonX86InstanceClassError):
            apply_default_sizing_to_intent(
                resolved, overrides={"instance_class": "db.m7g.large"}
            )

    def test_provenance_embedded_in_intent(self) -> None:
        resolved = resolve_tier()
        apply_default_sizing_to_intent(resolved)
        assert resolved.intent["_provenance"] is resolved.provenance
