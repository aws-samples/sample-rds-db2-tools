"""Intent resolvers for the rds-db2-provision-skill.

This module owns the pure, AWS-free resolution of a natural-language prompt's
structured signals into the tier-governed defaults of a ``Deployment_Intent``.
It is the home of the ``Tier_Resolver`` (this task, R3) and will grow the
edition reconciliation (task 3.2, R8), engine-version / parameter-group-family
resolution (task 3.3, R5), the self-describing identifier builder (task 3.4,
R20), and the ``Sizing_Resolver`` / ``Workload_Sizing_Map`` (task 4, R17/R19).

Design notes (so later tasks extend cleanly, not rewrite):

* Each resolver writes only its own fields and tags each one's provenance
  (``user_provided`` / ``assumed``) into the ``_provenance`` object, matching
  the schema's contract (R4.10, R2.2, R2.3).
* Tier defaults are applied first, then explicit prompt overrides win (R3.6).
  A prompt value that supersedes a tier default is recorded so the
  Verification_Step can show "applied X (tier default was Y)" (R2.5).
* The ``Tier_Resolver`` is deliberately a thin, deterministic function over
  already-extracted signals (named tier, ``Environment`` tag value, override
  fields). Turning free text into those signals is the ``Intent_Collector``'s
  job (R2); keeping that separate keeps this layer unit-testable without an LLM.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

try:  # Prefer the package-qualified module so its classes are identical to
    # the ones tests import via ``scripts.instance_specs`` (single identity).
    from scripts.instance_specs import (  # noqa: F401 - re-exported
        InstanceSpec,
        UnknownInstanceClassError,
        lookup_instance_spec,
    )
    from scripts.engine_versions import (  # noqa: F401 - re-exported
        EngineVersionLister,
        EngineVersionResolutionError,
        ResolvedEngineVersion,
        SUPPORTED_PARAMETER_GROUP_FAMILIES,
        UnsupportedParameterGroupFamilyError,
        derive_parameter_group_family,
        resolve_engine_version,
    )
except ImportError:  # Fall back to a bare import when scripts/ is on sys.path.
    from instance_specs import (  # noqa: F401 - re-exported
        InstanceSpec,
        UnknownInstanceClassError,
        lookup_instance_spec,
    )
    from engine_versions import (  # noqa: F401 - re-exported
        EngineVersionLister,
        EngineVersionResolutionError,
        ResolvedEngineVersion,
        SUPPORTED_PARAMETER_GROUP_FAMILIES,
        UnsupportedParameterGroupFamilyError,
        derive_parameter_group_family,
        resolve_engine_version,
    )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The three (and only three) Deployment_Tier values; identical to the set the
#: mandatory ``Environment`` tag uses (R3.1).
SUPPORTED_TIERS: tuple[str, ...] = ("sandbox", "dev", "prod")

#: The three (and only three) valid Db2 engine editions (R5.2).
SUPPORTED_EDITIONS: tuple[str, ...] = ("db2-ce", "db2-se", "db2-ae")

#: Edition selected when the prompt names none. Db2 Standard Edition is the most
#: common customer edition, so it is the assumed default (R8.3). The default is
#: independent of the tier (R8.1).
DEFAULT_EDITION = "db2-se"

#: IBM Db2 Standard Edition licensing ceiling (R8.4). SE is permitted only on an
#: instance class with AT MOST this many vCPUs AND AT MOST this much memory
#: (both inclusive). Exceeding either bound requires db2-ae.
SE_MAX_VCPU = 32
SE_MAX_MEMORY_GIB = 128

#: Tier selected when a prompt names no tier and supplies no Environment tag
#: (R3.3).
DEFAULT_TIER = "sandbox"

#: Default Db2 major version when the prompt does not pin one (R5.6). The full
#: minor (e.g. ``12.1.4``) is resolved against the live API in task 3.3; the
#: tier layer only seeds the major.
DEFAULT_MAJOR_VERSION = "12.1"

#: Db2 non-SSL TCP listener port. Present because RDS requires a ``port`` at
#: create time, but dormant under ``DB2COMM=SSL`` (R3.4, glossary).
DEFAULT_TCP_LISTENER_PORT = 8392

#: The only port that accepts client connections, fixed via ``ssl_svcename``.
#: Not an intent-schema field (it is a security-invariant rendering concern,
#: R6.2); exposed here as the grounded constant the baseline references (R3.4).
SSL_SERVICE_PORT = 50443

#: db2diag.log CloudWatch export key (R3.4: db2diag.log publishing enabled).
DB2DIAG_LOG_EXPORT = "diag.log"

#: Enhanced monitoring interval (seconds) used when monitoring is enabled
#: (R3.4: enhanced monitoring enabled). Matches the ``5-rds`` module literal.
ENHANCED_MONITORING_INTERVAL = 15


# ---------------------------------------------------------------------------
# Tier default field sets
# ---------------------------------------------------------------------------

# The baseline "Deploy RDS for Db2 instance" field set (R3.4). This is the
# sandbox tier's posture and the common base every tier starts from. Fields
# that are environment-specific identifiers (kms_key_id, vpc_security_group_ids,
# db_subnet_group_name) are intentionally NOT tier defaults: R3.4 calls them
# "existing-or-new", i.e. resolved against the target account by the collector /
# composer, not baked into a tier. Sizing fields here are the baseline defaults
# the Sizing_Resolver (task 4) overrides when a workload_size is given.
_BASELINE_DEFAULTS: dict[str, Any] = {
    "engine_version": DEFAULT_MAJOR_VERSION,  # task 3.3 refines to a real minor
    "instance_class": "db.t3.xlarge",
    "allocated_storage": 40,
    "storage_type": "gp3",
    "multi_az": False,
    "backup_retention_period": 1,
    "db_name": "DB2DB",
    "port": DEFAULT_TCP_LISTENER_PORT,
    "publicly_accessible": False,
    "storage_encrypted": True,
    "deletion_protection": False,
    "license_model": "bring-your-own-license",
    "monitoring_interval": ENHANCED_MONITORING_INTERVAL,
    "enable_cloudwatch_logs_exports": [DB2DIAG_LOG_EXPORT],
}

# Per-tier overlays applied on top of the baseline. Only deltas live here so the
# baseline stays the single source of truth for shared values.
_TIER_OVERLAYS: dict[str, dict[str, Any]] = {
    # sandbox == the bare R3.4 baseline.
    "sandbox": {},
    # dev: still single-AZ and cheap-to-run, but a week of backups so a dev
    # mishap is recoverable.
    "dev": {
        "backup_retention_period": 7,
    },
    # prod posture (R3.5): Multi-AZ, a deterministic r-family class, >=7-day
    # backups, deletion protection on.
    "prod": {
        "multi_az": True,
        "instance_class": "db.r7i.xlarge",
        "backup_retention_period": 7,
        "deletion_protection": True,
    },
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class IntentResolutionError(Exception):
    """Base class for any failure that must halt resolution before an intent is
    produced (no fabrication, no partial intent)."""


class UnknownTierError(IntentResolutionError):
    """A named tier or ``Environment`` tag value is not one of the three
    supported tiers (R3.8). The message names the offending value and lists the
    three supported values."""

    def __init__(self, source: str, value: str) -> None:
        self.source = source
        self.value = value
        supported = ", ".join(SUPPORTED_TIERS)
        super().__init__(
            f"Unrecognized {source} value {value!r}. "
            f"Supported tiers are: {supported}."
        )


class TierConflictError(IntentResolutionError):
    """A prompt names a tier and supplies an ``Environment`` tag value that
    differ (R3.2). The message names both conflicting values rather than
    silently choosing one."""

    def __init__(self, named_tier: str, environment_tag: str) -> None:
        self.named_tier = named_tier
        self.environment_tag = environment_tag
        super().__init__(
            "Conflicting deployment tier: the prompt names tier "
            f"{named_tier!r} but the Environment tag is {environment_tag!r}. "
            "Resolve the conflict by making them match or specifying only one."
        )


class UnknownEditionError(IntentResolutionError):
    """A prompt names an ``engine`` edition that is not one of the three valid
    editions (R5.2/5.3). The message names the offending value and lists the
    three supported editions."""

    def __init__(self, value: str) -> None:
        self.value = value
        supported = ", ".join(SUPPORTED_EDITIONS)
        super().__init__(
            f"Unrecognized engine edition {value!r}. "
            f"Supported editions are: {supported}."
        )


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class ResolvedIntent:
    """The output of a resolver stage.

    ``intent`` carries the resolved fields (including the embedded
    ``_provenance`` object so it round-trips as one document). ``provenance``
    is the same mapping exposed directly for convenience. ``resolved_tier`` and
    ``superseded_tier_defaults`` support the Verification_Step echo (R2.5, R3.7).
    """

    intent: dict[str, Any]
    provenance: dict[str, str]
    resolved_tier: str
    superseded_tier_defaults: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_tier(value: Optional[str]) -> Optional[str]:
    """Trim and lower-case a tier/Environment value; ``None``/blank -> ``None``."""
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def instance_class_family(instance_class: str) -> str:
    """Return the family letter group of an RDS instance class.

    ``db.r7i.xlarge`` -> ``r``. Used to assert the prod posture's r-family class
    (R3.5) and reused by later edition/sizing logic. Returns an empty string for
    a malformed class rather than raising, so callers can decide.
    """
    parts = instance_class.split(".")
    if len(parts) < 2 or not parts[1]:
        return ""
    return parts[1][0]


# ---------------------------------------------------------------------------
# Tier_Resolver (R3)
# ---------------------------------------------------------------------------


def select_tier(
    named_tier: Optional[str] = None,
    environment_tag: Optional[str] = None,
) -> tuple[str, str]:
    """Select the Deployment_Tier from the prompt's tier signal and Environment
    tag value.

    Returns ``(tier, provenance)`` where provenance is ``user_provided`` when
    the tier came from the prompt/tag and ``assumed`` when it defaulted to
    ``sandbox``.

    Raises:
        UnknownTierError: a supplied value is not one of the three tiers (R3.8).
        TierConflictError: a named tier and Environment tag differ (R3.2).
    """
    nt = _normalize_tier(named_tier)
    et = _normalize_tier(environment_tag)

    # Unknown values are rejected first, with the three supported values (R3.8).
    if nt is not None and nt not in SUPPORTED_TIERS:
        raise UnknownTierError("named tier", nt)
    if et is not None and et not in SUPPORTED_TIERS:
        raise UnknownTierError("Environment tag", et)

    # A named tier that disagrees with the Environment tag is a conflict (R3.2).
    if nt is not None and et is not None and nt != et:
        raise TierConflictError(nt, et)

    if nt is not None:
        return nt, "user_provided"
    if et is not None:
        return et, "user_provided"

    # Neither present -> sandbox default (R3.3).
    return DEFAULT_TIER, "assumed"


def resolve_tier(
    *,
    named_tier: Optional[str] = None,
    environment_tag: Optional[str] = None,
    overrides: Optional[Mapping[str, Any]] = None,
    tags: Optional[Mapping[str, str]] = None,
) -> ResolvedIntent:
    """Resolve the Deployment_Tier and apply its baseline defaults, then layer
    explicit prompt overrides on top (R3).

    Args:
        named_tier: a tier named explicitly in the prompt (e.g. "prod"), if any.
        environment_tag: the ``Environment`` tag value supplied in the prompt,
            if any. Tier == Environment tag (R3.1), so these must agree.
        overrides: field values the prompt specified explicitly. Applied after
            tier defaults (R3.6) and marked ``user_provided``.
        tags: any additional resource tags from the prompt. The ``Environment``
            tag is always forced to the resolved tier (R3.7).

    Returns:
        A :class:`ResolvedIntent` whose ``intent`` carries the tier-governed
        fields, the embedded ``_provenance`` object, the recorded
        ``deployment_tier``, and the ``Environment`` tag set to match (R3.7).

    Raises:
        UnknownTierError, TierConflictError: see :func:`select_tier`.
    """
    tier, tier_provenance = select_tier(named_tier, environment_tag)

    intent: dict[str, Any] = {}
    provenance: dict[str, str] = {}

    # 1) Apply tier defaults first (R3.6). Deep-copy so mutable defaults (lists)
    #    are never shared across resolutions -> determinism (R3.9).
    defaults = {**_BASELINE_DEFAULTS, **_TIER_OVERLAYS[tier]}
    for key, value in defaults.items():
        intent[key] = copy.deepcopy(value)
        provenance[key] = "assumed"

    # Record the resolved tier and force the Environment tag to match (R3.7).
    intent["deployment_tier"] = tier
    provenance["deployment_tier"] = tier_provenance

    merged_tags: dict[str, str] = dict(tags or {})
    merged_tags["Environment"] = tier
    intent["tags"] = merged_tags
    provenance["tags"] = tier_provenance

    # 2) Apply explicit prompt overrides on top of the tier defaults (R3.6).
    superseded: dict[str, Any] = {}
    for key, value in (overrides or {}).items():
        if key == "tags":
            # Merge tag overrides but never let them displace the tier-derived
            # Environment tag (R3.7).
            if isinstance(value, Mapping):
                merged = dict(intent["tags"])
                merged.update(value)
                merged["Environment"] = tier
                intent["tags"] = merged
                provenance["tags"] = "user_provided"
            continue

        # An override that differs from a tier default supersedes it; record
        # both so the Verification_Step can surface the change (R2.5).
        if (
            key in intent
            and provenance.get(key) == "assumed"
            and intent[key] != value
        ):
            superseded[key] = intent[key]

        intent[key] = copy.deepcopy(value)
        provenance[key] = "user_provided"

    intent["_provenance"] = provenance
    if superseded:
        intent["_superseded_tier_defaults"] = superseded

    return ResolvedIntent(
        intent=intent,
        provenance=provenance,
        resolved_tier=tier,
        superseded_tier_defaults=superseded,
    )


# ---------------------------------------------------------------------------
# Edition_Resolver (R8)
# ---------------------------------------------------------------------------


@dataclass
class EditionResolution:
    """The outcome of reconciling an edition with the SE licensing ceiling.

    Attributes:
        engine: the resolved edition after reconciliation (what the intent gets).
        provenance: ``user_provided`` when the prompt named the edition,
            ``assumed`` when it defaulted to ``db2-se`` (R8.3).
        requested_engine: the edition before any forced conversion -- the
            prompt's edition, or the ``db2-se`` default.
        converted: ``True`` when a forced SE->AE conversion was applied (R8.5).
        conversion_reason: human-readable reason for the conversion (the
            exceeded vCPU/memory ceiling), or ``None`` when no conversion.
        instance_spec: the grounded vCPU/memory spec used for the decision, or
            ``None`` when no instance class was supplied to check.
        acknowledgement_required: ``True`` when the Verification_Step must obtain
            an explicit acknowledgement before rendering proceeds -- always set
            alongside a forced conversion so it is never applied silently (R8.5).
        downgrade_guidance: optional AE->SE cost-saving hint surfaced when the
            customer keeps ``db2-ae`` on an SE-eligible class (R8.6); advisory
            only, never auto-applied.
    """

    engine: str
    provenance: str
    requested_engine: str
    converted: bool = False
    conversion_reason: Optional[str] = None
    instance_spec: Optional[InstanceSpec] = None
    acknowledgement_required: bool = False
    downgrade_guidance: Optional[str] = None


def _within_se_ceiling(spec: InstanceSpec) -> bool:
    """Return ``True`` when ``spec`` is within the SE licensing ceiling: at most
    32 vCPU AND at most 128 GB memory, both inclusive (R8.4)."""
    return spec.vcpu <= SE_MAX_VCPU and spec.memory_gib <= SE_MAX_MEMORY_GIB


def resolve_edition(
    *,
    requested_edition: Optional[str] = None,
    instance_class: Optional[str] = None,
) -> EditionResolution:
    """Resolve the Db2 ``engine`` edition and reconcile it with the SE ceiling.

    The edition is independent of the tier (R8.1): any edition may be combined
    with any tier, so this function takes only the edition signal and the
    resolved instance class.

    Rules implemented:

    * No edition named -> default ``db2-se`` (assumed) (R8.3); an explicit
      edition is ``user_provided`` (R8.3). Unknown edition -> reject (R5.3).
    * ``db2-ae`` is valid on ANY instance class with no vCPU/memory ceiling,
      including small classes (R8.2) -- never converted or second-guessed.
    * ``db2-ce`` carries no SE ceiling here (its own caps are handled
      elsewhere); it is returned unchanged.
    * ``db2-se`` on a class that EXCEEDS the SE ceiling (>32 vCPU OR >128 GB)
      is force-converted to ``db2-ae`` with a recorded reason and a required
      acknowledgement -- never silent (R8.5). This holds whether the SE was the
      assumed default or explicitly user_provided.
    * ``db2-se`` on an SE-eligible class is honored as-is (R8.6), including the
      customer-initiated AE->SE downgrade after rightsizing.
    * ``db2-ae`` kept on an SE-eligible class MAY surface AE->SE downgrade cost
      guidance but is never auto-downgraded (R8.6).

    vCPU/memory come from the grounded instance-spec source, never a hardcoded
    guess (R8.7).

    Args:
        requested_edition: the edition named in the prompt, if any.
        instance_class: the resolved instance class to check against the SE
            ceiling. When ``None``, no ceiling check is performed (the edition
            is returned as requested/defaulted).

    Returns:
        An :class:`EditionResolution` describing the resolved edition and any
        forced conversion or advisory guidance.

    Raises:
        UnknownEditionError: ``requested_edition`` is not one of the three
            supported editions (R5.3).
        UnknownInstanceClassError: ``instance_class`` cannot be grounded in the
            instance-spec source, so the ceiling cannot be checked without
            fabricating a vCPU/memory number (R8.7).
    """
    # 1) Resolve the edition and its provenance (R8.3).
    if requested_edition is None:
        edition = DEFAULT_EDITION
        provenance = "assumed"
    else:
        normalized = requested_edition.strip().lower()
        if normalized not in SUPPORTED_EDITIONS:
            raise UnknownEditionError(requested_edition)
        edition = normalized
        provenance = "user_provided"

    # Without an instance class we cannot check the ceiling; return as resolved.
    if instance_class is None:
        return EditionResolution(
            engine=edition,
            provenance=provenance,
            requested_engine=edition,
        )

    # 2) Ground the vCPU/memory of the instance class (R8.7). A non-groundable
    #    class raises rather than letting us guess.
    spec = lookup_instance_spec(instance_class)

    # 3) db2-ae has no ceiling on any class (R8.2): always valid, never touched.
    if edition == "db2-ae":
        guidance: Optional[str] = None
        if _within_se_ceiling(spec):
            # Cost guidance only -- never auto-downgrade (R8.6).
            guidance = (
                f"{instance_class} ({spec.vcpu} vCPU / {spec.memory_gib:g} GB) "
                "is within the Db2 Standard Edition ceiling (<=32 vCPU and "
                "<=128 GB). If this workload does not need Advanced Edition "
                "features, downgrading db2-ae -> db2-se may reduce license "
                "cost. This is advisory; the edition is left as db2-ae."
            )
        return EditionResolution(
            engine="db2-ae",
            provenance=provenance,
            requested_engine="db2-ae",
            instance_spec=spec,
            downgrade_guidance=guidance,
        )

    # 4) db2-ce carries no SE ceiling here; return unchanged.
    if edition == "db2-ce":
        return EditionResolution(
            engine="db2-ce",
            provenance=provenance,
            requested_engine="db2-ce",
            instance_spec=spec,
        )

    # 5) db2-se: enforce the SE ceiling (R8.4/8.5/8.6).
    if _within_se_ceiling(spec):
        # SE-eligible class -> honor the db2-se choice as-is (R8.6).
        return EditionResolution(
            engine="db2-se",
            provenance=provenance,
            requested_engine="db2-se",
            instance_spec=spec,
        )

    # SE on a class that exceeds the ceiling -> forced conversion to db2-ae,
    # never silent: record the reason and require acknowledgement (R8.5).
    exceeded: list[str] = []
    if spec.vcpu > SE_MAX_VCPU:
        exceeded.append(f"{spec.vcpu} vCPU exceeds the SE maximum of {SE_MAX_VCPU}")
    if spec.memory_gib > SE_MAX_MEMORY_GIB:
        exceeded.append(
            f"{spec.memory_gib:g} GB memory exceeds the SE maximum of "
            f"{SE_MAX_MEMORY_GIB} GB"
        )
    reason = (
        f"Instance class {instance_class} ({', '.join(exceeded)}) exceeds the "
        "IBM Db2 Standard Edition licensing ceiling, so the engine was "
        "converted db2-se -> db2-ae. Standard Edition cannot legally run on "
        "this class."
    )
    return EditionResolution(
        engine="db2-ae",
        provenance=provenance,
        requested_engine="db2-se",
        converted=True,
        conversion_reason=reason,
        instance_spec=spec,
        acknowledgement_required=True,
    )


def apply_edition_to_intent(
    resolved: ResolvedIntent,
    *,
    requested_edition: Optional[str] = None,
) -> ResolvedIntent:
    """Resolve the edition against the intent's instance class and write the
    ``engine``, its provenance, and any forced-conversion / guidance metadata
    into the intent in place (R8).

    The instance class is read from the already-resolved intent (set by the
    Tier_Resolver baseline or a sizing/override), keeping edition resolution
    downstream of sizing as the pipeline orders it. The forced SE->AE conversion
    and its reason are recorded in the intent so the Verification_Step can
    surface the warning and require acknowledgement (R8.5).

    Returns the same :class:`ResolvedIntent` for convenience.
    """
    instance_class = resolved.intent.get("instance_class")
    outcome = resolve_edition(
        requested_edition=requested_edition,
        instance_class=instance_class,
    )

    resolved.intent["engine"] = outcome.engine
    resolved.provenance["engine"] = outcome.provenance
    resolved.intent["_provenance"] = resolved.provenance

    # db2-se/db2-ae are BYOL (R8.9); the baseline already sets
    # license_model=bring-your-own-license, which is correct for all three.

    if outcome.converted:
        resolved.intent["_edition_conversion"] = {
            "from": outcome.requested_engine,
            "to": outcome.engine,
            "reason": outcome.conversion_reason,
            "acknowledgement_required": outcome.acknowledgement_required,
        }
    if outcome.downgrade_guidance:
        resolved.intent["_edition_downgrade_guidance"] = outcome.downgrade_guidance

    return resolved


# ---------------------------------------------------------------------------
# Engine-version resolution + parameter-group family (R5; task 3.3)
# ---------------------------------------------------------------------------


def apply_engine_version_to_intent(
    resolved: ResolvedIntent,
    *,
    region: str,
    lister: EngineVersionLister,
    pinned_major_version: Optional[str] = None,
) -> ResolvedIntent:
    """Resolve the concrete ``engine_version`` and parameter-group family for an
    already-edition-resolved intent and write them in place (R5).

    This runs downstream of :func:`apply_edition_to_intent`, which sets
    ``engine``. The major version is taken from ``pinned_major_version`` when the
    prompt pinned one, otherwise it defaults to ``12.1`` (R5.6) inside
    :func:`resolve_engine_version`. The selected minor is sourced live from the
    injected ``lister`` and never fabricated (R5.1); an unresolvable engine+major
    or an unsupported family halts (R5.1, R5.5/5.7).

    The provenance of ``engine_version`` is recorded as ``user_provided`` when
    the major was pinned by the prompt, otherwise ``assumed`` (defaulted major).

    Args:
        resolved: the intent to mutate; must already carry ``engine``.
        region: the target AWS region the version is resolved against.
        lister: the injectable engine-version query (boto3-backed in production,
            an in-memory stub in tests) -- keeps this AWS-free to unit-test.
        pinned_major_version: a major version explicitly pinned by the prompt
            (e.g. ``11.5``), or ``None`` to default ``12.1`` (R5.6).

    Returns:
        The same :class:`ResolvedIntent` for convenience, with ``engine_version``
        and ``db_parameter_group_name`` set and their provenance recorded.

    Raises:
        KeyError: ``engine`` has not been resolved into the intent yet.
        UnsupportedParameterGroupFamilyError, EngineVersionResolutionError:
            see :func:`resolve_engine_version`.
    """
    engine = resolved.intent.get("engine")
    if not engine:
        raise KeyError(
            "engine must be resolved (apply_edition_to_intent) before resolving "
            "the engine version"
        )

    outcome = resolve_engine_version(
        engine=engine,
        region=region,
        lister=lister,
        major_version=pinned_major_version,
    )

    provenance = "user_provided" if pinned_major_version else "assumed"

    resolved.intent["engine_version"] = outcome.engine_version
    resolved.provenance["engine_version"] = provenance

    # The composer derives the param-group family from engine+major; record the
    # resolved family so the rendering layer emits exactly this matrix entry
    # (R5.4/5.8) rather than re-deriving and risking drift.
    resolved.intent["db_parameter_group_family"] = outcome.parameter_group_family
    resolved.provenance["db_parameter_group_family"] = provenance

    resolved.intent["_provenance"] = resolved.provenance
    resolved.intent["_engine_version_resolution"] = {
        "engine": outcome.engine,
        "major_version": outcome.major_version,
        "engine_version": outcome.engine_version,
        "parameter_group_family": outcome.parameter_group_family,
        "candidates": list(outcome.candidates),
    }

    return resolved


# ---------------------------------------------------------------------------
# Self-describing DB_Instance_Identifier builder (R20; task 3.4)
# ---------------------------------------------------------------------------

#: The RDS db-instance-identifier format (R20.3). 1 leading ASCII letter, then
#: up to 62 letters/digits/hyphens, for a maximum total length of 63.
RDS_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9-]{0,62}$")

#: Maximum total length permitted by ``RDS_IDENTIFIER_PATTERN`` (1 + 62).
RDS_IDENTIFIER_MAX_LENGTH = 63

#: Workload_Size abbreviations used in the identifier. Mirrors the ``size_abbr``
#: sed substitutions in ``build_db_identifier_default`` (xlarge->xl, xsmall->xs,
#: large->l, medium->m, small->s) (R20.1).
_WORKLOAD_SIZE_ABBR: dict[str, str] = {
    "xsmall": "xs",
    "small": "s",
    "medium": "m",
    "large": "l",
    "xlarge": "xl",
}


def abbreviate_instance_class(instance_class: str) -> str:
    """Abbreviate an instance class for the identifier, porting the ``inst``
    pipeline in ``build_db_identifier_default`` exactly (R20.1).

    The bash original is::

        sed 's/^db\\.//; s/\\./-/g; s/xlarge/xl/g; s/large/l/g; s/medium/m/g;
             s/xsmall/xm/g; s/small/s/g'

    i.e. strip the leading ``db.`` prefix, turn remaining dots into hyphens,
    then abbreviate the size words in that fixed order. ``db.t3.xlarge`` ->
    ``t3-xl``; ``db.r7i.xlarge`` -> ``r7i-xl``.

    The order is significant: ``xlarge`` is collapsed before ``large`` and
    ``xsmall`` (-> ``xm`` here, deliberately distinct from the Workload_Size
    ``xs``) before ``small`` so the longer word wins.
    """
    s = instance_class
    if s.startswith("db."):
        s = s[len("db.") :]
    s = s.replace(".", "-")
    # Ordered to match the sed pipeline: longer tokens first.
    for word, abbr in (
        ("xlarge", "xl"),
        ("large", "l"),
        ("medium", "m"),
        ("xsmall", "xm"),
        ("small", "s"),
    ):
        s = s.replace(word, abbr)
    return s


def abbreviate_workload_size(workload_size: str) -> str:
    """Abbreviate a Workload_Size for the identifier (R20.1).

    Mirrors the ``size_abbr`` sed substitutions: xlarge->xl, xsmall->xs,
    large->l, medium->m, small->s. An unrecognized value is passed through after
    the same ordered substitutions so a future size still produces a stable
    token rather than raising.
    """
    s = (workload_size or "").strip().lower()
    if s in _WORKLOAD_SIZE_ABBR:
        return _WORKLOAD_SIZE_ABBR[s]
    for word, abbr in (
        ("xlarge", "xl"),
        ("xsmall", "xs"),
        ("large", "l"),
        ("medium", "m"),
        ("small", "s"),
    ):
        s = s.replace(word, abbr)
    return s


def _major_version(engine_version: str) -> str:
    """Return the major version (first two dot components) of an engine version.

    ``12.1.4`` -> ``12.1``; ``11.5`` -> ``11.5``. Matches the major-version
    derivation the composer uses (R5.4) so the identifier and the param-group
    family agree on the same major.
    """
    parts = (engine_version or "").split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return engine_version or ""


def conform_to_rds_identifier(raw: str) -> str:
    """Normalize and adjust ``raw`` so it satisfies ``RDS_IDENTIFIER_PATTERN``
    without ever producing a non-conforming identifier (R20.2, R20.3).

    The steps, in order:

    1. lowercase, and replace any character outside ``[a-z0-9-]`` with a hyphen
       so stray characters in a tag cannot break the format;
    2. collapse runs of hyphens to one and strip leading/trailing hyphens
       (R20.2);
    3. drop leading characters until the first ASCII letter, because the format
       requires a leading letter -- a digit or hyphen first would be invalid;
    4. truncate to 63 characters and strip any hyphen the cut left dangling so
       truncation never yields a trailing hyphen (R20.3).

    If nothing conforming remains (e.g. an all-digit input), falls back to the
    deterministic ``db2`` stem so the result is always valid.
    """
    s = (raw or "").lower()
    # 1) Replace disallowed characters with a hyphen.
    s = re.sub(r"[^a-z0-9-]", "-", s)
    # 2) Collapse consecutive hyphens and strip leading/trailing ones (R20.2).
    s = re.sub(r"-{2,}", "-", s).strip("-")
    # 3) The first character must be a letter; drop any leading digits/hyphens.
    s = re.sub(r"^[^a-z]+", "", s)
    if not s:
        # No letter to anchor on -> deterministic, conforming fallback.
        return "db2"
    # 4) Enforce the maximum length, then re-strip any trailing hyphen the
    #    truncation may have exposed (R20.3).
    if len(s) > RDS_IDENTIFIER_MAX_LENGTH:
        s = s[:RDS_IDENTIFIER_MAX_LENGTH].rstrip("-")
    return s


def build_db_instance_identifier(
    *,
    engine: str,
    engine_version: str,
    instance_class: str,
    workload_size: str,
    storage_type: str,
    multi_az: bool,
    tag: str,
    iops: Optional[int] = None,
) -> str:
    """Build the self-describing default DB_Instance_Identifier (R20.1-20.3, 20.6).

    Faithfully ports ``build_db_identifier_default`` from ``0cr-ins.sh``,
    assembling the resolved fields in the fixed order:

        ``{engine}-{major}-{instance}-{size}-{storage}-{az}{iops}-{tag}``

    where:

    * ``engine`` has its ``db2-`` prefix collapsed to ``db2`` (``db2-se`` ->
      ``db2se``);
    * ``major`` is the major version with dots replaced by hyphens
      (``12.1.4`` -> ``12-1``);
    * ``instance`` is :func:`abbreviate_instance_class`;
    * ``size`` is :func:`abbreviate_workload_size`;
    * ``az`` is ``saz`` for single-AZ or ``maz`` for Multi-AZ;
    * the IOPS suffix ``-{iops//1000}k`` is appended only when ``iops`` is set
      (integer division, e.g. ``12000`` -> ``-12k``);
    * ``tag`` is the deployment tag.

    The assembled string is then passed through :func:`conform_to_rds_identifier`
    so the result is lowercased, hyphen-normalized, and guaranteed to satisfy
    ``RDS_IDENTIFIER_PATTERN`` (R20.2, R20.3).

    Deterministic: identical inputs always yield an identical identifier
    (R20.6) -- the function is pure with no clock/randomness.
    """
    eng = engine.replace("db2-", "db2", 1)
    ver = _major_version(engine_version).replace(".", "-")
    inst = abbreviate_instance_class(instance_class)
    size = abbreviate_workload_size(workload_size)
    az = "maz" if multi_az else "saz"
    iops_suffix = ""
    if iops:
        iops_suffix = f"-{int(iops) // 1000}k"

    raw = f"{eng}-{ver}-{inst}-{size}-{storage_type}-{az}{iops_suffix}-{tag}"
    return conform_to_rds_identifier(raw)


def apply_db_instance_identifier_to_intent(
    resolved: ResolvedIntent,
    *,
    workload_size: str,
    tag: str,
    customer_identifier: Optional[str] = None,
) -> ResolvedIntent:
    """Resolve ``db_instance_identifier`` into an already-populated intent in
    place and record its provenance (R20.1-20.4, 20.6).

    Reads the resolved ``engine``, ``engine_version``, ``instance_class``,
    ``storage_type``, ``multi_az``, and ``iops`` from the intent so the
    identifier is built downstream of edition/version/sizing resolution. When
    the customer supplied an explicit identifier it is used verbatim as an
    override and marked ``user_provided`` (R20.4); otherwise the self-describing
    default is derived and marked ``assumed``.

    Returns the same :class:`ResolvedIntent` for convenience.
    """
    if customer_identifier is not None and customer_identifier.strip():
        # Customer override wins verbatim; provenance user_provided (R20.4).
        resolved.intent["db_instance_identifier"] = customer_identifier
        resolved.provenance["db_instance_identifier"] = "user_provided"
    else:
        identifier = build_db_instance_identifier(
            engine=resolved.intent["engine"],
            engine_version=resolved.intent["engine_version"],
            instance_class=resolved.intent["instance_class"],
            workload_size=workload_size,
            storage_type=resolved.intent["storage_type"],
            multi_az=resolved.intent.get("multi_az", False),
            tag=tag,
            iops=resolved.intent.get("iops"),
        )
        resolved.intent["db_instance_identifier"] = identifier
        resolved.provenance["db_instance_identifier"] = "assumed"

    resolved.intent["_provenance"] = resolved.provenance
    return resolved


# ---------------------------------------------------------------------------
# Sizing_Resolver + Workload_Sizing_Map (R17, R19; task 4.1)
# ---------------------------------------------------------------------------

#: The five (and only five) Workload_Size t-shirt values (R17.1).
SUPPORTED_WORKLOAD_SIZES: tuple[str, ...] = (
    "xsmall",
    "small",
    "medium",
    "large",
    "xlarge",
)

#: The maximum gp3 storage throughput (MiBps) RDS for Db2 allows; the derived
#: value is capped here (R19.7).
GP3_MAX_STORAGE_THROUGHPUT = 4000


def derive_gp3_storage_throughput(iops: int) -> int:
    """Derive the gp3 ``storage_throughput`` from ``iops`` (R19.7).

    Ports ``get_storage_throughput`` from ``0cr-ins.sh``: throughput is the
    floor of ``iops / 4``, capped at :data:`GP3_MAX_STORAGE_THROUGHPUT` (4000).
    This is the single derivation the Sizing_Resolver uses for every gp3 size
    that carries IOPS, and the same value the Intent_Validator re-derives to
    reject a free-set throughput (R19.7). Pure and deterministic.

    Examples:
        ``20000`` -> ``min(5000, 4000)`` -> ``4000``
        ``64000`` -> ``min(16000, 4000)`` -> ``4000``
        ``12000`` -> ``min(3000, 4000)`` -> ``3000``
    """
    return min(iops // 4, GP3_MAX_STORAGE_THROUGHPUT)


@dataclass(frozen=True)
class WorkloadSizeEntry:
    """One row of the :data:`WORKLOAD_SIZING_MAP`.

    Carries only the *prescriptive* per-size capacity (R17.2): the instance
    class, storage type, allocated storage, and the raw IOPS (``None`` when the
    size carries no IOPS -- only gp3 below 400 GiB, i.e. ``xsmall``). The gp3
    ``storage_throughput`` is never stored here; it is always *derived* from
    ``iops`` via :func:`derive_gp3_storage_throughput` so the map cannot drift
    from the R19.7 rule.
    """

    instance_class: str
    storage_type: str
    allocated_storage: int
    iops: Optional[int] = None


#: The deterministic Workload_Sizing_Map (R17.2), encoded exactly from the
#: design's table with its two reconciliations already applied:
#:
#: 1. ``large`` is ``io2`` (not ``gp3``): the source ``large.env`` set
#:    ``gp3``/130000, but 130000 exceeds the gp3 ceiling (64000, R19.5) and the
#:    gp3 ratio cap (500, R19.6); it is only valid under io2's [0.5, 1000] ratio
#:    (130000/16000 = 8.1). The env file's own inline comment says ``# io2``, so
#:    the ``gp3`` is treated as a copy-paste error and the map classifies
#:    ``large`` as ``io2``.
#: 2. ``xsmall`` carries no IOPS/throughput: ``xsmall.env`` set ``IOPS=3000`` at
#:    40 GiB gp3 but noted "IOPS not applied by RDS". Per R19.4 gp3 below 400
#:    GiB must not set IOPS or throughput -- RDS applies the baseline -- so these
#:    fields stay unset for ``xsmall``.
#:
#: Storage-type-dependent performance fields are resolved by
#: :func:`resolve_workload_size`: io2 sets ``iops`` and no throughput (R17.4);
#: gp3 >= 400 GiB sets ``iops`` and a *derived* ``storage_throughput`` (R17.3,
#: R19.7); gp3 < 400 GiB sets neither (R19.4).
WORKLOAD_SIZING_MAP: dict[str, WorkloadSizeEntry] = {
    # ~10 GB DB. gp3 at 40 GiB (< 400) -> no iops, no throughput (R19.4).
    "xsmall": WorkloadSizeEntry(
        instance_class="db.t3.small",
        storage_type="gp3",
        allocated_storage=40,
        iops=None,
    ),
    # ~100 GB DB. gp3 at 400 GiB -> iops + derived throughput.
    # 20000/400 = 50 ratio (<= 500, R19.6); throughput = min(5000, 4000) = 4000.
    "small": WorkloadSizeEntry(
        instance_class="db.t3.xlarge",
        storage_type="gp3",
        allocated_storage=400,
        iops=20000,
    ),
    # ~1 TB DB. gp3 at 3000 GiB -> iops at the 64000 ceiling (R19.5);
    # 64000/3000 = 21.3 ratio (<= 500); throughput = min(16000, 4000) = 4000.
    "medium": WorkloadSizeEntry(
        instance_class="db.r7i.2xlarge",
        storage_type="gp3",
        allocated_storage=3000,
        iops=64000,
    ),
    # ~10 TB DB. io2 (reconciliation 1) at 16000 GiB -> iops 130000, no
    # throughput; 130000/16000 = 8.1 ratio (within io2 [0.5, 1000], R19.8).
    "large": WorkloadSizeEntry(
        instance_class="db.r7i.4xlarge",
        storage_type="io2",
        allocated_storage=16000,
        iops=130000,
    ),
    # ~25 TB DB. io2 at 35000 GiB -> iops 200000, no throughput;
    # 200000/35000 = 5.7 ratio (within io2 [0.5, 1000], R19.8).
    "xlarge": WorkloadSizeEntry(
        instance_class="db.x2iedn.16xlarge",
        storage_type="io2",
        allocated_storage=35000,
        iops=200000,
    ),
}


class UnknownWorkloadSizeError(IntentResolutionError):
    """A named ``workload_size`` is not one of the five supported sizes (R17.8).
    The message names the offending value and lists the five supported sizes."""

    def __init__(self, value: str) -> None:
        self.value = value
        supported = ", ".join(SUPPORTED_WORKLOAD_SIZES)
        super().__init__(
            f"Unrecognized workload_size {value!r}. "
            f"Supported sizes are: {supported}."
        )


# ---------------------------------------------------------------------------
# x86-only instance-class guard (R17.13-17.16; task 4.2)
# ---------------------------------------------------------------------------

#: An RDS instance-class family token decomposed as ``<prefix><generation>``
#: optionally followed by a capabilities suffix, e.g. ``r7i`` -> ``r`` / ``7`` /
#: ``i``; ``x2iedn`` -> ``x`` / ``2`` / ``iedn``; ``r8g`` -> ``r`` / ``8`` /
#: ``g``; ``x2gd`` -> ``x`` / ``2`` / ``gd``. The capabilities suffix is where
#: AWS encodes the processor: ``i`` Intel, ``a`` AMD, ``g`` AWS Graviton (ARM).
_INSTANCE_FAMILY_RE = re.compile(
    r"^(?P<prefix>[a-z]+)(?P<gen>\d+)(?P<caps>[a-z]*)$"
)


class NonX86InstanceClassError(IntentResolutionError):
    """A requested ``instance_class`` is a Graviton/ARM class. RDS for Db2 runs
    only on x86 (Intel or AMD) instance classes (R17.15), so an ARM family
    (``r8g``, ``m7g``, ``c7g``, ``x2gd`` -- any family whose processor suffix is
    ``g``) is rejected before any Terraform rendering. The message states that
    RDS for Db2 does not run on Graviton/ARM and names the offending class."""

    def __init__(self, instance_class: str, family: Optional[str] = None) -> None:
        self.instance_class = instance_class
        self.family = family
        fam = f" (family {family!r})" if family else ""
        super().__init__(
            f"Instance class {instance_class!r}{fam} is a Graviton/ARM-based "
            "class. RDS for Db2 does not run on Graviton/ARM -- choose an x86 "
            "(Intel or AMD) memory-optimized class such as db.r7i.*, db.r8i.*, "
            "db.r8a.*, or db.x2iedn.* instead."
        )


def instance_class_family_token(instance_class: str) -> str:
    """Return the family token of an instance class (``db.r7i.2xlarge`` ->
    ``r7i``); empty string for a malformed class.

    Distinct from :func:`instance_class_family`, which returns only the leading
    family *letter* (``r``); this returns the full family token including the
    generation and processor suffix, which the x86 guard inspects.
    """
    parts = (instance_class or "").split(".")
    if len(parts) < 2 or not parts[1]:
        return ""
    return parts[1].strip().lower()


def is_graviton_instance_class(instance_class: str) -> bool:
    """Return ``True`` when ``instance_class`` belongs to an AWS Graviton/ARM
    family (R17.15).

    AWS encodes the processor in the family's capabilities suffix -- the letters
    after the generation digits: ``i`` = Intel, ``a`` = AMD, ``g`` = Graviton
    (ARM). A class is Graviton when that suffix begins with ``g`` -- covering
    ``r8g``/``r7g``/``m7g``/``c7g`` (suffix ``g``) as well as ``x2gd``/``r6gd``/
    ``c7gn`` (suffix ``gd``/``gn``). x86 families never start their suffix with
    ``g``: ``r8i``/``x8i`` (``i``), ``r8a`` (``a``), ``x2iedn`` (``iedn``), and
    ``t3`` (no suffix) are all x86.

    A family token that does not match the standard ``<prefix><gen><caps>``
    shape (so its processor cannot be determined) is treated as NOT Graviton --
    the guard only rejects what it can positively identify as ARM, leaving any
    genuinely unsupported class to downstream grounding (e.g. the instance-spec
    lookup) rather than fabricating an ARM verdict.
    """
    family = instance_class_family_token(instance_class)
    match = _INSTANCE_FAMILY_RE.match(family)
    if match is None:
        return False
    return match.group("caps").startswith("g")


def is_x86_instance_class(instance_class: str) -> bool:
    """Return ``True`` when ``instance_class`` is an x86 (Intel/AMD) class, i.e.
    not Graviton/ARM (R17.15). Convenience negation of
    :func:`is_graviton_instance_class`."""
    return not is_graviton_instance_class(instance_class)


def assert_x86_instance_class(instance_class: str) -> None:
    """Raise :class:`NonX86InstanceClassError` when ``instance_class`` is a
    Graviton/ARM class (R17.15); return ``None`` for an x86 class.

    This is the single guard the Sizing_Resolver applies to the final resolved
    ``instance_class`` -- whether it came from the Workload_Sizing_Map default or
    a customer override -- so a Graviton class can never reach Terraform
    rendering regardless of how it was supplied (R17.6, R17.14, R17.15)."""
    if is_graviton_instance_class(instance_class):
        raise NonX86InstanceClassError(
            instance_class, instance_class_family_token(instance_class)
        )


#: The R3.4 baseline sizing (``db.t3.xlarge`` / ``gp3`` / 40 GiB). Used as the
#: default resolved sizing when the prompt names neither a Workload_Size nor any
#: sizing field (R17.7). Note: no Workload_Sizing_Map row resolves to these
#: exact three values (the map's ``small`` shares the instance class but uses
#: 400 GiB; ``xsmall`` shares the storage but uses ``db.t3.small``), so the
#: default deliberately keeps the tier baseline rather than selecting a map row.
#: gp3 below 400 GiB carries no IOPS/throughput (R19.4).
_BASELINE_SIZING: dict[str, Any] = {
    "instance_class": "db.t3.xlarge",
    "storage_type": "gp3",
    "allocated_storage": 40,
}


def resolve_workload_size(workload_size: str) -> dict[str, Any]:
    """Resolve a ``Workload_Size`` into its sizing fields via the
    :data:`WORKLOAD_SIZING_MAP` (R17.2-17.5, R19.7).

    Returns a dict carrying exactly the sizing fields the size sets:

    * ``instance_class``, ``storage_type``, ``allocated_storage`` -- always
      present (R17.2);
    * ``iops`` -- present for every size whose map entry carries IOPS (the io2
      sizes and the gp3 >= 400 GiB sizes); absent for gp3 < 400 GiB
      (``xsmall``) per R19.4;
    * ``storage_throughput`` -- present ONLY for gp3 sizes that carry IOPS, and
      always the *derived* value ``min(floor(iops/4), 4000)`` (R17.3, R19.7);
      never present for io2 (R17.4) and never for gp3 < 400 GiB (R19.4).

    The returned dict is fresh on every call (no shared mutable state), so
    resolving the same size is deterministic and side-effect free (R17.11).

    Raises:
        UnknownWorkloadSizeError: ``workload_size`` is not one of the five
            supported sizes (R17.8).
    """
    key = (workload_size or "").strip().lower()
    entry = WORKLOAD_SIZING_MAP.get(key)
    if entry is None:
        raise UnknownWorkloadSizeError(workload_size)

    fields: dict[str, Any] = {
        "instance_class": entry.instance_class,
        "storage_type": entry.storage_type,
        "allocated_storage": entry.allocated_storage,
    }

    if entry.iops is not None:
        fields["iops"] = entry.iops
        # gp3 with IOPS gets a DERIVED throughput; io2 never sets throughput
        # (R17.4); gp3 < 400 GiB never reaches here (its entry has iops=None).
        if entry.storage_type == "gp3":
            fields["storage_throughput"] = derive_gp3_storage_throughput(entry.iops)

    return fields


#: The sizing fields the Sizing_Resolver owns, in canonical order. Used when
#: applying a resolution to an intent so a size that does not set a field clears
#: any baseline value the Tier_Resolver left behind (e.g. an io2 size must not
#: inherit a baseline ``storage_throughput``).
_SIZING_FIELDS: tuple[str, ...] = (
    "instance_class",
    "storage_type",
    "allocated_storage",
    "iops",
    "storage_throughput",
)


def _normalized_sizing_overrides(
    overrides: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return only the sizing-field overrides from ``overrides`` (R17.6).

    Filters an arbitrary override mapping down to the five sizing fields the
    Sizing_Resolver owns, dropping any whose value is ``None`` (an explicit
    "unset" signal, not an override). Non-sizing keys are ignored here -- the
    Tier_Resolver handles those.
    """
    if not overrides:
        return {}
    return {
        key: value
        for key, value in overrides.items()
        if key in _SIZING_FIELDS and value is not None
    }


def apply_sizing_to_intent(
    resolved: ResolvedIntent,
    *,
    workload_size: str,
    overrides: Optional[Mapping[str, Any]] = None,
) -> ResolvedIntent:
    """Resolve ``workload_size`` through the :data:`WORKLOAD_SIZING_MAP`, layer
    any explicit sizing-field overrides on top, and write the result into
    ``resolved.intent`` in place (R17.5, R17.6, R17.14, R17.15).

    Resolution order per field:

    1. The Workload_Sizing_Map value for ``workload_size`` is applied and marked
       ``assumed`` (R17.5); a field the size does not set is removed so a
       baseline value left by the Tier_Resolver cannot leak in (e.g. an io2
       size's absent ``storage_throughput``).
    2. Any field present in ``overrides`` (one of ``instance_class``,
       ``storage_type``, ``allocated_storage``, ``iops``, ``storage_throughput``)
       replaces the mapped value, is marked ``user_provided``, and the remaining
       sizing fields stay as resolved from the map (R17.6, R17.14).

    After overrides are applied, the *final* resolved ``instance_class`` is run
    through the x86-only guard, so a Graviton/ARM class is rejected whether it
    came from an override or (defensively) the map (R17.15). The cross-field
    consistency of an override (e.g. ``storage_throughput`` set while
    ``storage_type`` is io2) is the Intent_Validator's concern (R17.9), not this
    resolver's.

    ``workload_size`` itself is recorded on the intent and marked ``assumed``.

    Returns the same :class:`ResolvedIntent` for convenience.

    Raises:
        UnknownWorkloadSizeError: ``workload_size`` is not supported (R17.8).
        NonX86InstanceClassError: the resolved ``instance_class`` is Graviton/ARM
            (R17.15).
    """
    fields = resolve_workload_size(workload_size)
    sizing_overrides = _normalized_sizing_overrides(overrides)

    for key in _SIZING_FIELDS:
        if key in sizing_overrides:
            # Explicit override wins; provenance user_provided (R17.6).
            resolved.intent[key] = copy.deepcopy(sizing_overrides[key])
            resolved.provenance[key] = "user_provided"
        elif key in fields:
            resolved.intent[key] = fields[key]
            resolved.provenance[key] = "assumed"
        else:
            # Neither overridden nor set by the size -> clear any baseline value.
            resolved.intent.pop(key, None)
            resolved.provenance.pop(key, None)

    # Guard the FINAL instance class (override or mapped) before it can reach
    # rendering (R17.15).
    assert_x86_instance_class(resolved.intent["instance_class"])

    resolved.intent["workload_size"] = (workload_size or "").strip().lower()
    resolved.provenance["workload_size"] = "assumed"

    resolved.intent["_provenance"] = resolved.provenance
    return resolved


def apply_default_sizing_to_intent(
    resolved: ResolvedIntent,
    *,
    overrides: Optional[Mapping[str, Any]] = None,
) -> ResolvedIntent:
    """Resolve sizing when the prompt names neither a ``Workload_Size`` nor any
    sizing field, producing the R3.4 baseline sizing (R17.7).

    The resolved ``instance_class`` / ``storage_type`` / ``allocated_storage``
    equal the baseline ``db.t3.xlarge`` / ``gp3`` / ``40`` GiB (R17.7). Because
    that is gp3 below 400 GiB, no ``iops`` or ``storage_throughput`` is set
    (R19.4); any such field left by an earlier stage is cleared. The baseline
    fields are marked ``assumed`` (they were not user-provided).

    If, despite the "no sizing field" precondition, the caller passes sizing
    ``overrides``, they are honored over the baseline and marked
    ``user_provided`` -- this keeps the function total and lets the
    Intent_Collector route a "no size but one explicit field" prompt here
    without a special case. ``workload_size`` is left unset, since the prompt
    named none.

    The resolved ``instance_class`` is run through the x86-only guard (R17.15).

    Returns the same :class:`ResolvedIntent` for convenience.

    Raises:
        NonX86InstanceClassError: the resolved ``instance_class`` is Graviton/ARM
            (R17.15).
    """
    sizing_overrides = _normalized_sizing_overrides(overrides)

    for key in _SIZING_FIELDS:
        if key in sizing_overrides:
            resolved.intent[key] = copy.deepcopy(sizing_overrides[key])
            resolved.provenance[key] = "user_provided"
        elif key in _BASELINE_SIZING:
            resolved.intent[key] = _BASELINE_SIZING[key]
            resolved.provenance[key] = "assumed"
        else:
            # Baseline is gp3 < 400 GiB -> clear iops/storage_throughput (R19.4).
            resolved.intent.pop(key, None)
            resolved.provenance.pop(key, None)

    assert_x86_instance_class(resolved.intent["instance_class"])

    resolved.intent["_provenance"] = resolved.provenance
    return resolved
