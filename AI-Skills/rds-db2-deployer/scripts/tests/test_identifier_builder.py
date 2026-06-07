"""Unit and property tests for the self-describing DB_Instance_Identifier
builder (task 3.4, Requirement 20).

Covers the fixed field order and abbreviations ported from
``build_db_identifier_default`` in ``0cr-ins.sh`` (R20.1), lowercase +
hyphen-normalization (R20.2), RDS-format conformance including truncation of an
over-long identifier (R20.3), the customer override -> user_provided (R20.4),
and derivation determinism (R20.6).

Pure tests, no AWS.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scripts.resolve_intent import (
    RDS_IDENTIFIER_MAX_LENGTH,
    RDS_IDENTIFIER_PATTERN,
    abbreviate_instance_class,
    abbreviate_workload_size,
    apply_db_instance_identifier_to_intent,
    build_db_instance_identifier,
    conform_to_rds_identifier,
    resolve_tier,
)


# --- Field order and abbreviations (R20.1) ----------------------------------


def test_builds_identifier_in_documented_field_order():
    """The identifier assembles engine-major-instance-size-storage-az-iops-tag
    in that fixed order (R20.1)."""
    ident = build_db_instance_identifier(
        engine="db2-se",
        engine_version="12.1.4",
        instance_class="db.t3.xlarge",
        workload_size="xlarge",
        storage_type="gp3",
        multi_az=False,
        tag="acme",
        iops=12000,
    )
    # instance-class size (t3-xl) and the Workload_Size token (xl) both appear,
    # exactly as build_db_identifier_default emits ${inst}-${size_abbr}.
    assert ident == "db2se-12-1-t3-xl-xl-gp3-saz-12k-acme"


def test_engine_prefix_collapsed_and_major_version_hyphenated():
    """``db2-`` collapses to ``db2`` and the major version's dots become hyphens
    (R20.1); the patch component of the engine version is dropped (major only)."""
    ident = build_db_instance_identifier(
        engine="db2-ae",
        engine_version="11.5.9",
        instance_class="db.r7i.large",
        workload_size="large",
        storage_type="io2",
        multi_az=True,
        tag="team",
        iops=8000,
    )
    # db2ae, major 11-5, instance r7i-l, size l, io2, maz, 8k, team
    assert ident == "db2ae-11-5-r7i-l-l-io2-maz-8k-team"


def test_multi_az_uses_maz_single_az_uses_saz():
    """AZ posture abbreviates SAZ for single-AZ and MAZ for Multi-AZ (R20.1),
    lowercased by normalization (R20.2)."""
    common = dict(
        engine="db2-se",
        engine_version="12.1",
        instance_class="db.t3.medium",
        workload_size="medium",
        storage_type="gp3",
        tag="t",
    )
    saz = build_db_instance_identifier(multi_az=False, **common)
    maz = build_db_instance_identifier(multi_az=True, **common)
    assert "-saz-" in saz and "-maz-" not in saz
    assert "-maz-" in maz and "-saz-" not in maz


def test_iops_suffix_only_present_when_iops_set():
    """The ``-{iops//1000}k`` suffix appears only when iops is set (R20.1)."""
    common = dict(
        engine="db2-se",
        engine_version="12.1",
        instance_class="db.t3.small",
        workload_size="small",
        storage_type="gp3",
        multi_az=False,
        tag="t",
    )
    with_iops = build_db_instance_identifier(iops=3000, **common)
    no_iops = build_db_instance_identifier(iops=None, **common)
    assert with_iops == "db2se-12-1-t3-s-s-gp3-saz-3k-t"
    assert no_iops == "db2se-12-1-t3-s-s-gp3-saz-t"


def test_iops_suffix_uses_integer_thousands():
    """IOPS is reported in whole thousands via integer division (e.g. 12500 ->
    12k), matching the bash ``$(( IOPS / 1000 ))`` (R20.1)."""
    ident = build_db_instance_identifier(
        engine="db2-se",
        engine_version="12.1",
        instance_class="db.t3.small",
        workload_size="small",
        storage_type="io2",
        multi_az=False,
        tag="t",
        iops=12500,
    )
    assert ident.endswith("-12k-t")


@pytest.mark.parametrize(
    "instance_class, expected",
    [
        ("db.t3.xlarge", "t3-xl"),
        ("db.r7i.large", "r7i-l"),
        ("db.t3.medium", "t3-m"),
        ("db.t3.small", "t3-s"),
        ("db.m5.2xlarge", "m5-2xl"),
    ],
)
def test_abbreviate_instance_class(instance_class, expected):
    """Instance-class abbreviation strips ``db.``, hyphenates dots, and
    abbreviates size words (R20.1)."""
    assert abbreviate_instance_class(instance_class) == expected


@pytest.mark.parametrize(
    "size, expected",
    [
        ("xsmall", "xs"),
        ("small", "s"),
        ("medium", "m"),
        ("large", "l"),
        ("xlarge", "xl"),
    ],
)
def test_abbreviate_workload_size(size, expected):
    """Each Workload_Size abbreviates to its documented token (R20.1)."""
    assert abbreviate_workload_size(size) == expected


# --- Normalization (R20.2) --------------------------------------------------


def test_normalizes_to_lowercase():
    """A tag with uppercase letters is lowercased in the identifier (R20.2)."""
    ident = build_db_instance_identifier(
        engine="db2-se",
        engine_version="12.1",
        instance_class="db.t3.small",
        workload_size="small",
        storage_type="gp3",
        multi_az=False,
        tag="ACME-Prod",
    )
    assert ident == ident.lower()
    assert "acme-prod" in ident


def test_collapses_consecutive_hyphens_and_strips_edges():
    """Consecutive hyphens collapse to one and leading/trailing hyphens are
    stripped (R20.2)."""
    assert conform_to_rds_identifier("--db2se---12-1--gp3--") == "db2se-12-1-gp3"


def test_empty_tag_does_not_leave_trailing_hyphen():
    """An empty deployment tag must not leave a dangling trailing hyphen (R20.2)."""
    ident = build_db_instance_identifier(
        engine="db2-se",
        engine_version="12.1",
        instance_class="db.t3.small",
        workload_size="small",
        storage_type="gp3",
        multi_az=False,
        tag="",
    )
    assert not ident.endswith("-")
    assert RDS_IDENTIFIER_PATTERN.match(ident)


# --- Format conformance, including truncation (R20.3) -----------------------


def test_derived_identifier_matches_rds_pattern():
    """A normal derivation satisfies the RDS identifier format (R20.3)."""
    ident = build_db_instance_identifier(
        engine="db2-ae",
        engine_version="12.1.4",
        instance_class="db.r7i.xlarge",
        workload_size="xlarge",
        storage_type="io2",
        multi_az=True,
        tag="acme",
        iops=64000,
    )
    assert RDS_IDENTIFIER_PATTERN.match(ident)


def test_over_long_tag_is_truncated_to_conform():
    """An over-long tag is truncated so the identifier stays within 63 chars and
    conforms, with no trailing hyphen left by the cut (R20.3)."""
    ident = build_db_instance_identifier(
        engine="db2-se",
        engine_version="12.1",
        instance_class="db.t3.xlarge",
        workload_size="xlarge",
        storage_type="gp3",
        multi_az=False,
        tag="x" * 200,
    )
    assert len(ident) <= RDS_IDENTIFIER_MAX_LENGTH
    assert RDS_IDENTIFIER_PATTERN.match(ident)
    assert not ident.endswith("-")


def test_conform_strips_invalid_characters():
    """Characters outside [a-z0-9-] are replaced so the result conforms (R20.3)."""
    out = conform_to_rds_identifier("db2_se@12.1/gp3 saz")
    assert RDS_IDENTIFIER_PATTERN.match(out)


def test_conform_drops_leading_non_letters():
    """A leading digit/hyphen is dropped so the first character is a letter
    (R20.3)."""
    assert conform_to_rds_identifier("12-1-db2se") == "db2se"


def test_conform_all_digits_falls_back_to_db2_stem():
    """An input with no letter falls back to a deterministic conforming stem so
    the result is never non-conforming (R20.3)."""
    out = conform_to_rds_identifier("123456")
    assert out == "db2"
    assert RDS_IDENTIFIER_PATTERN.match(out)


# --- Customer override -> user_provided (R20.4) -----------------------------


def _baseline_intent():
    resolved = resolve_tier()
    # Fields the builder reads, which later resolvers normally populate.
    resolved.intent["engine"] = "db2-se"
    resolved.intent["engine_version"] = "12.1.4"
    return resolved


def test_customer_override_used_verbatim_and_marked_user_provided():
    """A customer-supplied identifier overrides the default and is marked
    user_provided (R20.4)."""
    resolved = _baseline_intent()
    apply_db_instance_identifier_to_intent(
        resolved,
        workload_size="xlarge",
        tag="acme",
        customer_identifier="my-custom-db2",
    )
    assert resolved.intent["db_instance_identifier"] == "my-custom-db2"
    assert resolved.provenance["db_instance_identifier"] == "user_provided"
    assert resolved.intent["_provenance"]["db_instance_identifier"] == "user_provided"


def test_default_derived_when_no_override_marked_assumed():
    """Without an override the self-describing default is derived and marked
    assumed (R20.1, R20.4)."""
    resolved = _baseline_intent()
    apply_db_instance_identifier_to_intent(
        resolved,
        workload_size="xlarge",
        tag="acme",
    )
    ident = resolved.intent["db_instance_identifier"]
    assert ident == "db2se-12-1-t3-xl-xl-gp3-saz-acme"
    assert resolved.provenance["db_instance_identifier"] == "assumed"


def test_blank_override_falls_through_to_default():
    """A blank/whitespace override is treated as no override (R20.4)."""
    resolved = _baseline_intent()
    apply_db_instance_identifier_to_intent(
        resolved,
        workload_size="small",
        tag="t",
        customer_identifier="   ",
    )
    assert resolved.provenance["db_instance_identifier"] == "assumed"


# --- Determinism (R20.6) ----------------------------------------------------


def test_identical_inputs_yield_identical_identifier():
    """Same inputs -> same identifier on each derivation (R20.6)."""
    kwargs = dict(
        engine="db2-se",
        engine_version="12.1.4",
        instance_class="db.t3.xlarge",
        workload_size="xlarge",
        storage_type="gp3",
        multi_az=False,
        tag="acme",
        iops=12000,
    )
    first = build_db_instance_identifier(**kwargs)
    second = build_db_instance_identifier(**kwargs)
    assert first == second


# --- Property: conformance + determinism over generated inputs (R20.3/20.6) -


_engines = st.sampled_from(["db2-ce", "db2-se", "db2-ae"])
_versions = st.sampled_from(["12.1", "12.1.4", "11.5", "11.5.9"])
_classes = st.sampled_from(
    ["db.t3.small", "db.t3.medium", "db.t3.xlarge", "db.r7i.large", "db.r7i.xlarge"]
)
_sizes = st.sampled_from(["xsmall", "small", "medium", "large", "xlarge"])
_storage = st.sampled_from(["gp3", "io2"])
_tags = st.text(
    alphabet=st.characters(
        min_codepoint=0x20, max_codepoint=0x7E
    ),
    min_size=0,
    max_size=120,
)
_iops = st.one_of(st.none(), st.integers(min_value=1000, max_value=256000))


@given(
    engine=_engines,
    engine_version=_versions,
    instance_class=_classes,
    workload_size=_sizes,
    storage_type=_storage,
    multi_az=st.booleans(),
    tag=_tags,
    iops=_iops,
)
def test_property_derived_identifier_always_conforms(
    engine,
    engine_version,
    instance_class,
    workload_size,
    storage_type,
    multi_az,
    tag,
    iops,
):
    """For all generated resolved intents, the derived identifier satisfies the
    RDS format, and the same inputs always derive the same value.

    **Validates: Requirements 20.3, 20.6**
    """
    kwargs = dict(
        engine=engine,
        engine_version=engine_version,
        instance_class=instance_class,
        workload_size=workload_size,
        storage_type=storage_type,
        multi_az=multi_az,
        tag=tag,
        iops=iops,
    )
    ident = build_db_instance_identifier(**kwargs)
    assert RDS_IDENTIFIER_PATTERN.match(ident), ident
    assert len(ident) <= RDS_IDENTIFIER_MAX_LENGTH
    # Determinism (R20.6): a second derivation is identical.
    assert build_db_instance_identifier(**kwargs) == ident
