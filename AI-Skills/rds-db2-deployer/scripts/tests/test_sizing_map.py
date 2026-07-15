"""Unit tests for the Workload_Sizing_Map and Sizing_Resolver (task 4.1,
Requirements 17.1-17.5, 17.7, 19.7).

Covers the exact per-size mapped fields encoded from the design table (with the
two reconciliations: ``large`` is io2, ``xsmall`` carries no IOPS/throughput),
the gp3 ``storage_throughput`` derivation ``min(floor(iops/4), 4000)`` (R19.7),
the io2 "iops, no throughput" rule (R17.4), the gp3 < 400 "neither" rule
(R19.4), and the unknown-size rejection (R17.8).

Pure tests, no AWS.
"""

from __future__ import annotations

import pytest

from scripts.resolve_intent import (
    GP3_MAX_STORAGE_THROUGHPUT,
    SUPPORTED_WORKLOAD_SIZES,
    UnknownWorkloadSizeError,
    WORKLOAD_SIZING_MAP,
    apply_sizing_to_intent,
    derive_gp3_storage_throughput,
    resolve_tier,
    resolve_workload_size,
)

# Expected sizing fields per size, encoded directly from the design's
# Workload_Sizing_Map table (the authoritative expectation for this task).
EXPECTED_SIZING: dict[str, dict] = {
    "xsmall": {
        "instance_class": "db.t3.small",
        "storage_type": "gp3",
        "allocated_storage": 40,
        # gp3 < 400 -> no iops, no storage_throughput (R19.4).
    },
    "small": {
        "instance_class": "db.t3.xlarge",
        "storage_type": "gp3",
        "allocated_storage": 400,
        "iops": 20000,
        "storage_throughput": 4000,  # min(20000//4=5000, 4000) -> 4000
    },
    "medium": {
        "instance_class": "db.r7i.2xlarge",
        "storage_type": "gp3",
        "allocated_storage": 3000,
        "iops": 64000,
        "storage_throughput": 4000,  # min(64000//4=16000, 4000) -> 4000
    },
    "large": {
        "instance_class": "db.r7i.4xlarge",
        "storage_type": "io2",  # reconciliation 1: io2, not gp3
        "allocated_storage": 16000,
        "iops": 130000,
        # io2 -> no storage_throughput (R17.4)
    },
    "xlarge": {
        "instance_class": "db.x2iedn.16xlarge",
        "storage_type": "io2",
        "allocated_storage": 35000,
        "iops": 200000,
        # io2 -> no storage_throughput (R17.4)
    },
}


class TestWorkloadSizingMapEncoding:
    """The map encodes exactly the five sizes with the design's values (R17.2)."""

    def test_map_has_exactly_the_five_supported_sizes(self) -> None:
        assert set(WORKLOAD_SIZING_MAP) == set(SUPPORTED_WORKLOAD_SIZES)
        assert set(SUPPORTED_WORKLOAD_SIZES) == {
            "xsmall",
            "small",
            "medium",
            "large",
            "xlarge",
        }

    @pytest.mark.parametrize("size", sorted(EXPECTED_SIZING))
    def test_each_size_resolves_to_expected_fields(self, size: str) -> None:
        assert resolve_workload_size(size) == EXPECTED_SIZING[size]

    def test_storage_type_is_only_gp3_or_io2(self) -> None:
        for size in SUPPORTED_WORKLOAD_SIZES:
            assert resolve_workload_size(size)["storage_type"] in {"gp3", "io2"}


class TestReconciliations:
    """The two deliberate corrections applied to the raw source data."""

    def test_large_is_io2_not_gp3(self) -> None:
        # 130000 IOPS only valid under io2's ratio bound, never gp3 (R19.5/19.6).
        fields = resolve_workload_size("large")
        assert fields["storage_type"] == "io2"
        assert fields["iops"] == 130000
        assert "storage_throughput" not in fields

    def test_xsmall_carries_no_iops_or_throughput(self) -> None:
        # gp3 < 400 GiB -> RDS baseline, no IOPS or throughput (R19.4).
        fields = resolve_workload_size("xsmall")
        assert fields["storage_type"] == "gp3"
        assert fields["allocated_storage"] < 400
        assert "iops" not in fields
        assert "storage_throughput" not in fields


class TestStorageTypePerformanceFields:
    """io2 sets iops/no throughput; gp3>=400 sets iops + derived throughput;
    gp3<400 sets neither (R17.3, R17.4, R19.4, R19.7)."""

    @pytest.mark.parametrize("size", ["large", "xlarge"])
    def test_io2_sizes_set_iops_without_throughput(self, size: str) -> None:
        fields = resolve_workload_size(size)
        assert fields["storage_type"] == "io2"
        assert "iops" in fields
        assert "storage_throughput" not in fields

    @pytest.mark.parametrize("size", ["small", "medium"])
    def test_gp3_large_sizes_set_iops_and_derived_throughput(self, size: str) -> None:
        fields = resolve_workload_size(size)
        assert fields["storage_type"] == "gp3"
        assert fields["allocated_storage"] >= 400
        assert "iops" in fields
        assert fields["storage_throughput"] == derive_gp3_storage_throughput(
            fields["iops"]
        )


class TestThroughputDerivation:
    """The gp3 throughput derivation min(floor(iops/4), 4000) (R19.7)."""

    @pytest.mark.parametrize(
        "iops,expected",
        [
            (12000, 3000),   # 12000//4 = 3000, under the cap
            (16000, 4000),   # 16000//4 = 4000, exactly the cap
            (20000, 4000),   # 20000//4 = 5000 -> capped to 4000
            (64000, 4000),   # 64000//4 = 16000 -> capped to 4000
            (13, 3),         # floor behaviour: 13//4 = 3
            (4000, 1000),    # 4000//4 = 1000
        ],
    )
    def test_derivation_floors_and_caps(self, iops: int, expected: int) -> None:
        assert derive_gp3_storage_throughput(iops) == expected

    def test_derivation_never_exceeds_cap(self) -> None:
        for iops in (4001, 100000, 256000):
            assert derive_gp3_storage_throughput(iops) <= GP3_MAX_STORAGE_THROUGHPUT


class TestResolveWorkloadSizeBehaviour:
    def test_unknown_size_raises_with_supported_list(self) -> None:
        with pytest.raises(UnknownWorkloadSizeError) as exc:
            resolve_workload_size("humongous")
        msg = str(exc.value)
        for size in SUPPORTED_WORKLOAD_SIZES:
            assert size in msg

    def test_case_and_whitespace_insensitive(self) -> None:
        assert resolve_workload_size("  MEDIUM  ") == resolve_workload_size("medium")

    def test_resolution_is_deterministic_and_unshared(self) -> None:
        first = resolve_workload_size("small")
        second = resolve_workload_size("small")
        assert first == second
        # Distinct objects: mutating one must not affect the next resolution.
        first["allocated_storage"] = 9999
        assert resolve_workload_size("small")["allocated_storage"] == 400


class TestApplySizingToIntent:
    """Applying a size onto a tier-resolved intent marks fields assumed and
    clears sizing fields the size does not set (R17.5)."""

    def test_io2_size_clears_baseline_throughput_and_marks_assumed(self) -> None:
        resolved = resolve_tier()  # sandbox baseline: gp3/40, no throughput
        # Seed a stray baseline throughput to prove the io2 size clears it.
        resolved.intent["storage_throughput"] = 4000
        resolved.provenance["storage_throughput"] = "assumed"

        apply_sizing_to_intent(resolved, workload_size="large")

        assert resolved.intent["instance_class"] == "db.r7i.4xlarge"
        assert resolved.intent["storage_type"] == "io2"
        assert resolved.intent["allocated_storage"] == 16000
        assert resolved.intent["iops"] == 130000
        assert "storage_throughput" not in resolved.intent
        assert "storage_throughput" not in resolved.provenance
        assert resolved.intent["workload_size"] == "large"
        for field in ("instance_class", "storage_type", "allocated_storage", "iops"):
            assert resolved.provenance[field] == "assumed"
        assert resolved.provenance["workload_size"] == "assumed"

    def test_xsmall_clears_iops_and_throughput(self) -> None:
        resolved = resolve_tier()
        resolved.intent["iops"] = 5000
        resolved.provenance["iops"] = "assumed"

        apply_sizing_to_intent(resolved, workload_size="xsmall")

        assert resolved.intent["instance_class"] == "db.t3.small"
        assert resolved.intent["storage_type"] == "gp3"
        assert resolved.intent["allocated_storage"] == 40
        assert "iops" not in resolved.intent
        assert "iops" not in resolved.provenance
        assert "storage_throughput" not in resolved.intent

    def test_gp3_size_sets_derived_throughput_in_intent(self) -> None:
        resolved = resolve_tier()
        apply_sizing_to_intent(resolved, workload_size="medium")
        assert resolved.intent["storage_type"] == "gp3"
        assert resolved.intent["iops"] == 64000
        assert resolved.intent["storage_throughput"] == 4000
        assert resolved.provenance["storage_throughput"] == "assumed"

    def test_provenance_embedded_in_intent(self) -> None:
        resolved = resolve_tier()
        apply_sizing_to_intent(resolved, workload_size="small")
        assert resolved.intent["_provenance"] is resolved.provenance
