"""Unit + property tests for engine-version resolution and parameter-group
family derivation (task 3.3, Requirement 5).

Covers: highest-minor selection from the live API surface (R5.1), the
never-fabricate halt when the API returns nothing (R5.1), the default major
12.1 when unpinned (R5.6), the five-family matrix derivation (R5.4) and
rejection of unsupported combinations with the supported list (R5.5/5.7/5.8).

The ``aws rds describe-db-engine-versions`` query is abstracted behind an
injectable lister, so NO real AWS call is made here: every test passes an
in-memory stub. (`boto3_engine_version_lister` is the production wiring and is
intentionally not exercised against AWS.)

Pure tests, no AWS.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scripts.engine_versions import (
    DEFAULT_MAJOR_VERSION,
    SUPPORTED_PARAMETER_GROUP_FAMILIES,
    EngineVersionResolutionError,
    UnsupportedParameterGroupFamilyError,
    derive_parameter_group_family,
    major_version_of,
    resolve_engine_version,
    select_highest_minor,
)
from scripts.resolve_intent import (
    apply_edition_to_intent,
    apply_engine_version_to_intent,
    resolve_tier,
)


# ---------------------------------------------------------------------------
# Stub lister helpers (mock engine-version data; no AWS)
# ---------------------------------------------------------------------------


def make_lister(version_map):
    """Build an in-memory EngineVersionLister from ``{engine: [versions]}``.

    Records the calls it received so tests can assert it was queried with the
    resolved engine + region (R5.1).
    """
    calls = []

    def _lister(engine, region):
        calls.append((engine, region))
        return list(version_map.get(engine, []))

    _lister.calls = calls
    return _lister


# Realistic-looking API surface across both supported majors for several
# editions. Deliberately unsorted to prove selection does not rely on order.
SAMPLE_VERSIONS = {
    "db2-se": ["11.5.9.0", "12.1.2.0", "11.5.8.0", "12.1.4.0", "12.1.3.0"],
    "db2-ae": ["11.5.9.0", "12.1.4.0", "12.1.2.0"],
    "db2-ce": ["12.1.2.0", "12.1.4.0", "12.1.1.0"],
}


# ---------------------------------------------------------------------------
# major_version_of (R5.4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "version, expected",
    [
        ("12.1.4.0", "12.1"),
        ("12.1.4", "12.1"),
        ("11.5.9.0", "11.5"),
        ("12.1", "12.1"),
    ],
)
def test_major_version_of(version, expected):
    assert major_version_of(version) == expected


@pytest.mark.parametrize("bad", ["12", "", "  ", "x"])
def test_major_version_of_rejects_malformed(bad):
    with pytest.raises(ValueError):
        major_version_of(bad)


# ---------------------------------------------------------------------------
# select_highest_minor (R5.1)
# ---------------------------------------------------------------------------


def test_select_highest_minor_picks_max_of_major():
    versions = ["12.1.2.0", "12.1.4.0", "12.1.3.0", "11.5.9.0"]
    assert select_highest_minor(versions, "12.1") == "12.1.4.0"


def test_select_highest_minor_ignores_other_majors():
    versions = ["11.5.9.0", "11.5.8.0"]
    # Asking for 12.1 must ignore the 11.5 entries entirely.
    assert select_highest_minor(versions, "12.1") is None


def test_select_highest_minor_numeric_not_lexicographic():
    # Lexicographically "12.1.10.0" < "12.1.9.0"; numerically 10 > 9.
    versions = ["12.1.9.0", "12.1.10.0"]
    assert select_highest_minor(versions, "12.1") == "12.1.10.0"


def test_select_highest_minor_empty():
    assert select_highest_minor([], "12.1") is None


# ---------------------------------------------------------------------------
# derive_parameter_group_family (R5.4, R5.5, R5.7, R5.8)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "engine, major, family",
    [
        ("db2-ce", "12.1", "db2-ce-12.1"),
        ("db2-se", "11.5", "db2-se-11.5"),
        ("db2-se", "12.1", "db2-se-12.1"),
        ("db2-ae", "11.5", "db2-ae-11.5"),
        ("db2-ae", "12.1", "db2-ae-12.1"),
    ],
)
def test_derive_family_supported_matrix(engine, major, family):
    assert derive_parameter_group_family(engine, major) == family


@pytest.mark.parametrize(
    "engine, major",
    [
        ("db2-ce", "11.5"),  # CE is 12.1-only
        ("db2-se", "10.5"),
        ("db2-ae", "13.0"),
        ("db2-ce", "12.0"),
    ],
)
def test_derive_family_rejects_unsupported_with_supported_list(engine, major):
    with pytest.raises(UnsupportedParameterGroupFamilyError) as exc:
        derive_parameter_group_family(engine, major)
    msg = str(exc.value)
    # Every supported family is reported (R5.5/5.7).
    for fam in SUPPORTED_PARAMETER_GROUP_FAMILIES:
        assert fam in msg


def test_derive_family_never_emits_fabricated_string():
    # db2-ce-11.5 looks plausible but is not in the matrix; must raise, never
    # return the partial/fabricated string (R5.8).
    with pytest.raises(UnsupportedParameterGroupFamilyError):
        derive_parameter_group_family("db2-ce", "11.5")


# ---------------------------------------------------------------------------
# resolve_engine_version (R5.1, R5.6)
# ---------------------------------------------------------------------------


def test_resolve_selects_highest_minor_of_default_major():
    lister = make_lister(SAMPLE_VERSIONS)
    out = resolve_engine_version(engine="db2-se", region="us-east-1", lister=lister)
    assert out.engine_version == "12.1.4.0"
    assert out.major_version == DEFAULT_MAJOR_VERSION == "12.1"
    assert out.parameter_group_family == "db2-se-12.1"


def test_resolve_queries_lister_with_engine_and_region():
    lister = make_lister(SAMPLE_VERSIONS)
    resolve_engine_version(engine="db2-ae", region="eu-west-1", lister=lister)
    assert lister.calls == [("db2-ae", "eu-west-1")]


def test_resolve_pinned_major_115():
    lister = make_lister(SAMPLE_VERSIONS)
    out = resolve_engine_version(
        engine="db2-se", region="us-east-1", lister=lister, major_version="11.5"
    )
    assert out.engine_version == "11.5.9.0"
    assert out.major_version == "11.5"
    assert out.parameter_group_family == "db2-se-11.5"


def test_resolve_halts_when_api_returns_nothing_for_engine_major():
    # API has no 12.1 for this (hypothetical empty) engine listing -> never
    # fabricate, must halt (R5.1).
    lister = make_lister({"db2-se": ["11.5.9.0"]})  # only 11.5 available
    with pytest.raises(EngineVersionResolutionError) as exc:
        resolve_engine_version(engine="db2-se", region="us-east-1", lister=lister)
    assert "db2-se" in str(exc.value)
    assert "12.1" in str(exc.value)


def test_resolve_halts_on_empty_api_result():
    lister = make_lister({})  # nothing at all
    with pytest.raises(EngineVersionResolutionError):
        resolve_engine_version(engine="db2-ae", region="us-east-1", lister=lister)


def test_resolve_unsupported_family_fails_before_api_call():
    # db2-ce + 11.5 is not a supported family; must reject and NOT query AWS
    # (fail fast with the supported list, R5.5/5.7).
    lister = make_lister(SAMPLE_VERSIONS)
    with pytest.raises(UnsupportedParameterGroupFamilyError):
        resolve_engine_version(
            engine="db2-ce", region="us-east-1", lister=lister, major_version="11.5"
        )
    assert lister.calls == []


def test_resolve_records_candidates_for_audit():
    lister = make_lister(SAMPLE_VERSIONS)
    out = resolve_engine_version(engine="db2-se", region="us-east-1", lister=lister)
    # Only 12.1.x candidates are recorded.
    assert set(out.candidates) == {"12.1.2.0", "12.1.4.0", "12.1.3.0"}


# ---------------------------------------------------------------------------
# apply_engine_version_to_intent: writes into a resolved intent (R5)
# ---------------------------------------------------------------------------


def test_apply_engine_version_on_baseline_intent():
    resolved = resolve_tier()  # sandbox baseline
    apply_edition_to_intent(resolved)  # -> db2-se on db.t3.xlarge
    lister = make_lister(SAMPLE_VERSIONS)
    apply_engine_version_to_intent(resolved, region="us-east-1", lister=lister)

    assert resolved.intent["engine_version"] == "12.1.4.0"
    assert resolved.intent["db_parameter_group_family"] == "db2-se-12.1"
    # Default (unpinned) major -> assumed provenance (R5.6).
    assert resolved.intent["_provenance"]["engine_version"] == "assumed"


def test_apply_engine_version_pinned_major_is_user_provided():
    resolved = resolve_tier()
    apply_edition_to_intent(resolved)
    lister = make_lister(SAMPLE_VERSIONS)
    apply_engine_version_to_intent(
        resolved, region="us-east-1", lister=lister, pinned_major_version="11.5"
    )
    assert resolved.intent["engine_version"] == "11.5.9.0"
    assert resolved.intent["db_parameter_group_family"] == "db2-se-11.5"
    assert resolved.intent["_provenance"]["engine_version"] == "user_provided"


def test_apply_engine_version_requires_resolved_engine():
    resolved = resolve_tier()  # no edition applied -> no engine yet
    lister = make_lister(SAMPLE_VERSIONS)
    with pytest.raises(KeyError):
        apply_engine_version_to_intent(resolved, region="us-east-1", lister=lister)


def test_apply_engine_version_ce_only_supports_121():
    resolved = resolve_tier()
    apply_edition_to_intent(resolved, requested_edition="db2-ce")
    lister = make_lister(SAMPLE_VERSIONS)
    # CE defaults to 12.1 (supported) and resolves.
    apply_engine_version_to_intent(resolved, region="us-east-1", lister=lister)
    assert resolved.intent["db_parameter_group_family"] == "db2-ce-12.1"
    # But CE pinned to 11.5 is unsupported and must reject.
    resolved2 = resolve_tier()
    apply_edition_to_intent(resolved2, requested_edition="db2-ce")
    with pytest.raises(UnsupportedParameterGroupFamilyError):
        apply_engine_version_to_intent(
            resolved2, region="us-east-1", lister=lister, pinned_major_version="11.5"
        )


# ---------------------------------------------------------------------------
# Property: derived family is always a matrix entry, never fabricated (R5.8)
# ---------------------------------------------------------------------------


@given(
    engine=st.sampled_from(["db2-ce", "db2-se", "db2-ae"]),
    major=st.sampled_from(["11.5", "12.1", "10.5", "12.0", "13.0"]),
)
def test_property_family_is_matrix_entry_or_raises(engine, major):
    """For all engine+major combinations, derivation either returns an exact
    matrix entry or raises -- it never emits a fabricated/partial string."""
    try:
        family = derive_parameter_group_family(engine, major)
    except UnsupportedParameterGroupFamilyError:
        return  # rejected, as required for unsupported combos
    assert family in SUPPORTED_PARAMETER_GROUP_FAMILIES
    assert family == f"{engine}-{major}"


@given(
    minors=st.lists(st.integers(min_value=0, max_value=99), min_size=1, max_size=8),
)
def test_property_highest_minor_is_the_max(minors):
    """select_highest_minor always returns the numerically greatest 12.1.x."""
    versions = [f"12.1.{m}.0" for m in minors]
    selected = select_highest_minor(versions, "12.1")
    assert selected == f"12.1.{max(minors)}.0"
