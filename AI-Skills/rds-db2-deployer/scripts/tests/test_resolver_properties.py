"""Property-based tests for resolver determinism and axis orthogonality
(task 3.5 — design Properties 8 and 9).

These properties exercise the resolver pipeline that exists today — the
``Tier_Resolver`` (R3), the ``Edition_Resolver`` SE/AE reconciliation (R8), and
the self-describing identifier builder (R20) — over the cross product of the
orthogonal axes the design names: ``Deployment_Tier`` × capacity (the sizing
``instance_class``) × edition × ``AWS_Credential_Source`` (R16). The
``Sizing_Resolver`` proper arrives in task 4, so the capacity axis is driven
here through an explicit ``instance_class`` override (the same field the sizing
map populates) chosen to span the Standard Edition licensing ceiling, and the
identifier's ``workload_size`` token is generated alongside it.

The ``AWS_Credential_Source`` is, by design, external to intent resolution
(R16, glossary): how the agent obtains AWS credentials never feeds the resolved
``Deployment_Intent``. Including it as a generated axis and asserting it has no
effect on the resolved intent is precisely the orthogonality claim of
Property 8 for that axis.

Properties implemented (and only these — Properties 8 and 9):

* **Property 9 (determinism, R3.9 / R20.6):** the same inputs resolve to an
  identical intent every time, every tier resolves the full required field set,
  and the derived identifier is deterministic.
* **Property 8 (orthogonality, R3.9 / R8.1 / R16.5 / R17.12):** any tier × any
  capacity × any edition × any credential source resolves to a valid intent;
  the tier never constrains the edition (and vice versa); the SE→AE forced
  conversion fires exactly when the ceiling is exceeded (R8.4) and ``db2-ae`` is
  never auto-downgraded to ``db2-se`` (R8.6); the identifier is regex-conformant.

Pure tests, no AWS.

**Validates: Requirements 3.9, 8.4, 8.6, 17.12, 20.6**
"""

from __future__ import annotations

import copy

from hypothesis import given
from hypothesis import strategies as st

from scripts.instance_specs import lookup_instance_spec
from scripts.resolve_intent import (
    DEFAULT_EDITION,
    RDS_IDENTIFIER_MAX_LENGTH,
    RDS_IDENTIFIER_PATTERN,
    SE_MAX_MEMORY_GIB,
    SE_MAX_VCPU,
    SUPPORTED_EDITIONS,
    SUPPORTED_TIERS,
    _BASELINE_DEFAULTS,
    apply_db_instance_identifier_to_intent,
    apply_edition_to_intent,
    resolve_tier,
)

# ---------------------------------------------------------------------------
# The required field set every tier must resolve (Property 9 completeness).
# The Tier_Resolver seeds the baseline defaults plus the tier marker and tags;
# editions/identifier are layered on by the downstream resolvers below.
# ---------------------------------------------------------------------------

REQUIRED_TIER_FIELDS: tuple[str, ...] = (
    *(_BASELINE_DEFAULTS.keys()),
    "deployment_tier",
    "tags",
)


# ---------------------------------------------------------------------------
# Axis generators
# ---------------------------------------------------------------------------

# Tier axis (governance) — the three supported tiers (R3.1).
_tiers = st.sampled_from(SUPPORTED_TIERS)

# Edition axis (licensing). ``None`` models "prompt names no edition" -> the
# db2-se default (R8.3); the three explicit editions cover the named cases.
_editions = st.sampled_from([None, "db2-ce", "db2-se", "db2-ae"])

# Capacity axis (sizing). A set of groundable instance classes chosen to span
# the SE ceiling: eligible (<=32 vCPU AND <=128 GB) and over-ceiling. Driven via
# an instance_class override here because the Sizing_Resolver (task 4) is not
# built yet; this is the same field the Workload_Sizing_Map will populate.
_SE_ELIGIBLE_CLASSES = [
    "db.t3.small",      # 2 vCPU / 2 GB
    "db.t3.xlarge",     # 4 vCPU / 16 GB
    "db.r7i.xlarge",    # 4 vCPU / 32 GB
    "db.r7i.2xlarge",   # 8 vCPU / 64 GB
    "db.r7i.4xlarge",   # 16 vCPU / 128 GB (memory at the inclusive ceiling)
    "db.m6i.8xlarge",   # 32 vCPU / 128 GB (vCPU at the inclusive ceiling)
]
_SE_OVER_CEILING_CLASSES = [
    "db.r7i.8xlarge",     # 32 vCPU / 256 GB (memory over)
    "db.x2iedn.16xlarge",  # 64 vCPU / 1024 GB (both over)
]
_instance_classes = st.sampled_from(_SE_ELIGIBLE_CLASSES + _SE_OVER_CEILING_CLASSES)

# Workload-size token axis (feeds the identifier's size segment, R20.1).
_workload_sizes = st.sampled_from(["xsmall", "small", "medium", "large", "xlarge"])

# Credential-source axis (identity) — external to intent resolution (R16).
_credential_sources = st.sampled_from(
    ["profile:acme", "env", "default-chain", "instance-profile"]
)

# Deployment tag for the identifier (kept to identifier-safe-ish text; the
# builder conforms it regardless).
_tags = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E),
    min_size=0,
    max_size=40,
)


# ---------------------------------------------------------------------------
# Pipeline helper (composes the resolvers that exist today)
# ---------------------------------------------------------------------------


def _resolve(tier, edition, instance_class, workload_size, tag):
    """Run tier -> edition -> identifier resolution for one axis combination.

    The capacity is applied as an ``instance_class`` override so it wins over
    the tier default on every tier (exercising the capacity axis independently
    of the tier). ``engine_version`` is set deterministically (the live version
    resolver, task 3.3, is injected with a lister elsewhere) so the identifier
    can be built without AWS.
    """
    resolved = resolve_tier(
        named_tier=tier, overrides={"instance_class": instance_class}
    )
    apply_edition_to_intent(resolved, requested_edition=edition)
    resolved.intent["engine_version"] = "12.1.4"
    apply_db_instance_identifier_to_intent(
        resolved, workload_size=workload_size, tag=tag
    )
    return resolved


def _within_se_ceiling(instance_class: str) -> bool:
    """Grounded SE-ceiling check: <=32 vCPU AND <=128 GB (R8.4)."""
    spec = lookup_instance_spec(instance_class)
    return spec.vcpu <= SE_MAX_VCPU and spec.memory_gib <= SE_MAX_MEMORY_GIB


# ---------------------------------------------------------------------------
# Property 9: Resolution determinism (R3.9, R20.6)
# ---------------------------------------------------------------------------


@given(
    tier=_tiers,
    edition=_editions,
    instance_class=_instance_classes,
    workload_size=_workload_sizes,
    tag=_tags,
)
def test_property9_resolution_is_deterministic(
    tier, edition, instance_class, workload_size, tag
):
    """Same inputs -> byte-for-byte identical resolved intent, every time.

    **Validates: Requirements 3.9, 20.6**
    """
    first = _resolve(tier, edition, instance_class, workload_size, tag).intent
    second = _resolve(tier, edition, instance_class, workload_size, tag).intent
    assert first == second


@given(
    tier=_tiers,
    edition=_editions,
    instance_class=_instance_classes,
    workload_size=_workload_sizes,
    tag=_tags,
)
def test_property9_every_tier_resolves_all_required_fields(
    tier, edition, instance_class, workload_size, tag
):
    """Each tier resolves the full required field set with recorded provenance.

    **Validates: Requirements 3.9**
    """
    resolved = _resolve(tier, edition, instance_class, workload_size, tag)
    intent = resolved.intent
    for field in REQUIRED_TIER_FIELDS:
        assert field in intent, f"tier {tier!r} left required field {field!r} unset"
        assert resolved.provenance.get(field) in ("assumed", "user_provided")
    # The downstream resolvers complete the intent's identity fields.
    for field in ("engine", "db_instance_identifier"):
        assert field in intent


@given(
    tier=_tiers,
    edition=_editions,
    instance_class=_instance_classes,
    workload_size=_workload_sizes,
    tag=_tags,
)
def test_property9_identifier_is_deterministic_and_conformant(
    tier, edition, instance_class, workload_size, tag
):
    """The derived identifier is RDS-format-conformant and deterministic.

    **Validates: Requirements 20.6**
    """
    first = _resolve(tier, edition, instance_class, workload_size, tag)
    ident = first.intent["db_instance_identifier"]
    assert RDS_IDENTIFIER_PATTERN.match(ident), ident
    assert len(ident) <= RDS_IDENTIFIER_MAX_LENGTH
    # Identical inputs derive an identical identifier.
    second = _resolve(tier, edition, instance_class, workload_size, tag)
    assert second.intent["db_instance_identifier"] == ident


# ---------------------------------------------------------------------------
# Property 8: Axis orthogonality (R3.9, R8.1, R16.5, R17.12)
# ---------------------------------------------------------------------------


@given(
    tier=_tiers,
    edition=_editions,
    instance_class=_instance_classes,
    workload_size=_workload_sizes,
    tag=_tags,
    credential_source=_credential_sources,
)
def test_property8_any_axis_combination_resolves_to_valid_intent(
    tier, edition, instance_class, workload_size, tag, credential_source
):
    """Any tier × capacity × edition × credential source resolves to a valid
    intent: the tier and capacity are honored as given, the engine is one of the
    three supported editions, provenance is recorded, and the identifier
    conforms. The credential source is external to resolution and never leaks
    into the intent (orthogonal identity axis, R16).

    **Validates: Requirements 3.9, 8.1, 16.5, 17.12**
    """
    resolved = _resolve(tier, edition, instance_class, workload_size, tag)
    intent = resolved.intent

    # Tier and capacity are honored exactly (axes don't override each other).
    assert intent["deployment_tier"] == tier
    assert intent["tags"]["Environment"] == tier
    assert intent["instance_class"] == instance_class

    # Edition always resolves to one of the three supported editions.
    assert intent["engine"] in SUPPORTED_EDITIONS

    # Identifier conforms.
    assert RDS_IDENTIFIER_PATTERN.match(intent["db_instance_identifier"])

    # The credential source is not part of intent resolution (R16): it must not
    # appear anywhere in the resolved intent.
    assert credential_source not in repr(intent)


@given(
    edition=_editions,
    instance_class=_instance_classes,
    workload_size=_workload_sizes,
    tag=_tags,
)
def test_property8_tier_does_not_constrain_edition_or_capacity(
    edition, instance_class, workload_size, tag
):
    """For a fixed (edition, capacity), the resolved engine and its forced-
    conversion outcome are identical across all three tiers — the tier neither
    forces nor restricts the edition (R8.1). Conversely the requested capacity
    survives on every tier, so the tier does not constrain capacity either.

    **Validates: Requirements 8.1, 17.12**
    """
    engines = set()
    conversions = set()
    for tier in SUPPORTED_TIERS:
        resolved = _resolve(tier, edition, instance_class, workload_size, tag)
        engines.add(resolved.intent["engine"])
        conversions.add("_edition_conversion" in resolved.intent)
        # Capacity override is honored regardless of tier.
        assert resolved.intent["instance_class"] == instance_class
    # The edition outcome is identical on every tier.
    assert len(engines) == 1, f"tier changed the resolved edition: {engines}"
    assert len(conversions) == 1, "tier changed the forced-conversion outcome"


@given(
    tier=_tiers,
    edition=_editions,
    instance_class=_instance_classes,
    workload_size=_workload_sizes,
    tag=_tags,
)
def test_property8_se_to_ae_fires_iff_ceiling_exceeded(
    tier, edition, instance_class, workload_size, tag
):
    """The SE→AE forced conversion fires exactly when a db2-se request (the
    db2-se default or an explicit db2-se) lands on a class that exceeds the SE
    ceiling, and never otherwise. db2-ae and db2-ce are never converted, and
    db2-ae is never auto-downgraded to db2-se on an SE-eligible class (R8.4,
    R8.6).

    **Validates: Requirements 8.4, 8.6**
    """
    resolved = _resolve(tier, edition, instance_class, workload_size, tag)
    intent = resolved.intent

    requested = DEFAULT_EDITION if edition is None else edition
    eligible = _within_se_ceiling(instance_class)
    converted = "_edition_conversion" in intent

    if requested == "db2-se" and not eligible:
        # Over-ceiling SE -> forced to AE, recorded, never silent (R8.5).
        assert converted, "SE over the ceiling was not converted"
        assert intent["engine"] == "db2-ae"
        conv = intent["_edition_conversion"]
        assert conv["from"] == "db2-se"
        assert conv["to"] == "db2-ae"
        assert conv["acknowledgement_required"] is True
        assert conv["reason"]
    else:
        # Every other case: no forced conversion.
        assert not converted, (
            f"unexpected conversion for edition={requested} "
            f"class={instance_class} eligible={eligible}"
        )
        if requested == "db2-se":
            assert intent["engine"] == "db2-se"
        else:
            # db2-ae / db2-ce are returned unchanged — AE is never auto-
            # downgraded to SE even when the class is SE-eligible (R8.6).
            assert intent["engine"] == requested


@given(
    tier=_tiers,
    instance_class=st.sampled_from(_SE_ELIGIBLE_CLASSES),
    workload_size=_workload_sizes,
    tag=_tags,
)
def test_property8_ae_never_auto_downgraded_on_eligible_class(
    tier, instance_class, workload_size, tag
):
    """db2-ae on an SE-eligible class stays db2-ae — the reverse (AE→SE)
    conversion is never applied automatically; at most advisory guidance is
    surfaced (R8.6).

    **Validates: Requirements 8.6**
    """
    resolved = _resolve(tier, "db2-ae", instance_class, workload_size, tag)
    intent = resolved.intent
    assert intent["engine"] == "db2-ae"
    assert "_edition_conversion" not in intent
    # Any guidance is advisory only; the edition is unchanged.
    guidance = intent.get("_edition_downgrade_guidance")
    if guidance is not None:
        assert "db2-se" in guidance
