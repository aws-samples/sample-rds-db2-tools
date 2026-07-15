"""Property-based tests for the Workload_Sizing_Map and the storage-rule
derivations (task 4.3 — design Properties 4, 5, and 6).

These properties exercise the deterministic ``Sizing_Resolver`` surface that
exists today — :data:`WORKLOAD_SIZING_MAP`, :func:`resolve_workload_size`,
:func:`derive_gp3_storage_throughput`, and :data:`GP3_MAX_STORAGE_THROUGHPUT` —
and assert that the prescriptive per-size capacity is internally consistent with
the storage-type rules ported from ``0cr-ins.sh`` (R19) and that resolution is
deterministic with a restricted storage-type vocabulary (R17.11).

Properties implemented (and only these — design Properties 4, 5, and 6):

* **Property 4 (sizing determinism + storage-type vocabulary, R17.11):** for
  every supported `Workload_Size`, :func:`resolve_workload_size` yields an
  identical dict on each resolution and its ``storage_type`` is exactly one of
  ``gp3`` or ``io2``.
* **Property 5 (gp3 throughput is derived, never free, R19.7):** whenever a gp3
  size sets ``storage_throughput`` it equals ``min(floor(iops/4), 4000)``, and
  the derivation itself satisfies that identity across generated IOPS.
* **Property 6 (IOPS ratio bounds, R19.6/19.8):** every gp3 size's
  ``iops / allocated_storage`` is in ``(0, 500]`` with ``iops`` in the inclusive
  ``[12000, 64000]`` range (R19.5), and every io2 size's ratio is in the
  inclusive ``[0.5, 1000]`` range; each size's default IOPS satisfies the rule
  applicable to its resolved storage type (R19.9).

Pure tests, no AWS.

**Validates: Requirements 17.11, 19.6, 19.7, 19.8, 19.9**
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from scripts.resolve_intent import (
    GP3_MAX_STORAGE_THROUGHPUT,
    SUPPORTED_WORKLOAD_SIZES,
    WORKLOAD_SIZING_MAP,
    derive_gp3_storage_throughput,
    resolve_workload_size,
)

# gp3 IOPS range (inclusive) required for gp3 sizes >= 400 GiB (R19.5).
GP3_MIN_IOPS = 12000
GP3_MAX_IOPS = 64000

# gp3 ratio bound: 0 < iops/allocated_storage <= 500 (R19.6).
GP3_MAX_RATIO = 500
# io2 ratio bounds: 0.5 <= iops/allocated_storage <= 1000 (R19.8).
IO2_MIN_RATIO = 0.5
IO2_MAX_RATIO = 1000

# The supported sizes as a Hypothesis strategy (the only valid size inputs).
_sizes = st.sampled_from(SUPPORTED_WORKLOAD_SIZES)


# ---------------------------------------------------------------------------
# Property 4: sizing determinism + storage-type vocabulary (R17.11)
# ---------------------------------------------------------------------------


@given(size=_sizes)
def test_property4_resolution_is_deterministic(size: str) -> None:
    """Resolving the same Workload_Size yields an identical dict every time, on
    distinct (unshared) objects so a later mutation cannot perturb a resolution.

    **Validates: Requirements 17.11**
    """
    first = resolve_workload_size(size)
    second = resolve_workload_size(size)
    assert first == second
    # Distinct objects: mutating one resolution must not affect the next.
    first["allocated_storage"] = -1
    assert resolve_workload_size(size)["allocated_storage"] != -1


@given(size=_sizes)
def test_property4_storage_type_is_gp3_or_io2(size: str) -> None:
    """Every resolved storage_type is exactly one of gp3 or io2 (R17.11).

    **Validates: Requirements 17.11**
    """
    assert resolve_workload_size(size)["storage_type"] in {"gp3", "io2"}


def test_property4_map_covers_exactly_the_supported_sizes() -> None:
    """The map's keys are exactly the five supported sizes — so iterating the
    map below covers the whole input space for the per-size assertions.

    **Validates: Requirements 17.11**
    """
    assert set(WORKLOAD_SIZING_MAP) == set(SUPPORTED_WORKLOAD_SIZES)


# ---------------------------------------------------------------------------
# Property 5: gp3 throughput is derived, never free (R19.7)
# ---------------------------------------------------------------------------


@given(iops=st.integers(min_value=0, max_value=2_000_000))
def test_property5_throughput_derivation_identity(iops: int) -> None:
    """For any IOPS, derive_gp3_storage_throughput == min(floor(iops/4), 4000),
    and the result never exceeds the gp3 throughput cap.

    **Validates: Requirements 19.7**
    """
    derived = derive_gp3_storage_throughput(iops)
    assert derived == min(iops // 4, GP3_MAX_STORAGE_THROUGHPUT)
    assert derived <= GP3_MAX_STORAGE_THROUGHPUT


@given(size=_sizes)
def test_property5_gp3_sizes_set_derived_throughput(size: str) -> None:
    """Every gp3 size that carries IOPS sets storage_throughput to exactly the
    derived value; io2 sizes never set storage_throughput (R19.7, and the io2
    no-throughput rule it pairs with).

    **Validates: Requirements 19.7**
    """
    fields = resolve_workload_size(size)
    if fields["storage_type"] == "gp3" and "iops" in fields:
        assert fields["storage_throughput"] == derive_gp3_storage_throughput(
            fields["iops"]
        )
    if fields["storage_type"] == "io2":
        assert "storage_throughput" not in fields


# ---------------------------------------------------------------------------
# Property 6: IOPS ratio bounds + gp3 IOPS range (R19.6, 19.8, 19.9)
# ---------------------------------------------------------------------------


@given(size=_sizes)
def test_property6_per_size_iops_satisfies_storage_rules(size: str) -> None:
    """Each size's default IOPS satisfies the rule applicable to its resolved
    storage type (R19.9):

    * gp3 with IOPS: ``iops`` in the inclusive [12000, 64000] range (R19.5) and
      ``0 < iops/allocated_storage <= 500`` (R19.6);
    * io2: ``0.5 <= iops/allocated_storage <= 1000`` (R19.8).

    **Validates: Requirements 19.6, 19.8, 19.9**
    """
    fields = resolve_workload_size(size)
    storage_type = fields["storage_type"]
    allocated = fields["allocated_storage"]

    if "iops" not in fields:
        # Only gp3 below 400 GiB carries no IOPS; nothing to bound here.
        assert storage_type == "gp3"
        assert allocated < 400
        return

    iops = fields["iops"]
    ratio = iops / allocated

    if storage_type == "gp3":
        assert GP3_MIN_IOPS <= iops <= GP3_MAX_IOPS, (
            f"{size}: gp3 iops {iops} outside [{GP3_MIN_IOPS}, {GP3_MAX_IOPS}]"
        )
        assert 0 < ratio <= GP3_MAX_RATIO, (
            f"{size}: gp3 ratio {ratio} outside (0, {GP3_MAX_RATIO}]"
        )
    else:  # io2
        assert IO2_MIN_RATIO <= ratio <= IO2_MAX_RATIO, (
            f"{size}: io2 ratio {ratio} outside "
            f"[{IO2_MIN_RATIO}, {IO2_MAX_RATIO}]"
        )


def test_property6_every_map_row_satisfies_its_storage_rule() -> None:
    """Exhaustively iterate the map and assert the ratio/range bounds for every
    row — the deterministic complement to the generated per-size property above.

    **Validates: Requirements 19.6, 19.8, 19.9**
    """
    for size in SUPPORTED_WORKLOAD_SIZES:
        fields = resolve_workload_size(size)
        if "iops" not in fields:
            continue
        iops = fields["iops"]
        ratio = iops / fields["allocated_storage"]
        if fields["storage_type"] == "gp3":
            assert GP3_MIN_IOPS <= iops <= GP3_MAX_IOPS
            assert 0 < ratio <= GP3_MAX_RATIO
        else:
            assert IO2_MIN_RATIO <= ratio <= IO2_MAX_RATIO
