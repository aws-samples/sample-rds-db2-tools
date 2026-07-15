"""Grounded engine-version resolution and parameter-group-family derivation.

This module is the truth-grounded source (task 3.3, Requirement 5) for two
linked decisions the design's ``Tier_Resolver`` / ``Terraform_Composer`` make:

1. **Engine-version resolution (R5.1, R5.6).** Given a resolved ``engine`` and
   target ``region`` (and an optional pinned major version), select the
   *highest available minor* of the major from the live RDS API
   (``aws rds describe-db-engine-versions``). The minor string is NEVER
   hardcoded or fabricated (R5.1): if the API returns nothing for the
   engine+major, resolution halts. When the prompt does not pin a major, the
   major defaults to ``12.1`` (the current latest as of 2026-06, R5.6).

2. **Parameter-group-family derivation (R5.4, R5.5, R5.7, R5.8).** The family is
   ``<engine>-<major>`` where major is the first two dot-separated components of
   the engine version (e.g. ``12.1`` from ``12.1.4.0``). The result MUST exactly
   match one of the five supported families; an unsupported combination is
   rejected with the full supported list, and a fabricated/partial family string
   is never emitted.

Design intent (so the AWS call is testable without real AWS, mirroring
``instance_specs.py``):

* The live query is abstracted behind an injectable callable,
  :data:`EngineVersionLister` -- ``(engine, region) -> list[str]`` returning the
  raw ``EngineVersion`` strings. Production wires :func:`boto3_engine_version_lister`
  (a thin boto3 wrapper); unit tests inject an in-memory stub so NO real AWS call
  is ever made in tests. The resolution/selection logic is pure and lives here.
* The supported parameter-group families are a closed matrix (R5 design "Data
  Models"). Derivation only ever returns an entry from that matrix or raises.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The three (and only three) valid Db2 engine editions (R5.2). Duplicated from
#: resolve_intent to keep this helper importable on its own; the two are kept in
#: sync deliberately.
SUPPORTED_EDITIONS: tuple[str, ...] = ("db2-ce", "db2-se", "db2-ae")

#: The closed set of valid RDS-for-Db2 parameter-group families (R5.4, design
#: "Parameter-group family matrix"). The composer emits exactly one of these and
#: never a fabricated string (R5.8). Note db2-ce is 12.1-only.
SUPPORTED_PARAMETER_GROUP_FAMILIES: tuple[str, ...] = (
    "db2-ce-12.1",
    "db2-se-11.5",
    "db2-se-12.1",
    "db2-ae-11.5",
    "db2-ae-12.1",
)

#: Default Db2 major version when the prompt/intent does not pin one (R5.6); the
#: current latest as of 2026-06.
DEFAULT_MAJOR_VERSION = "12.1"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class EngineVersionResolutionError(Exception):
    """The live API returned no minor version for the resolved engine+major, so
    a concrete version cannot be selected without fabricating one (R5.1).

    Per the truth-grounding requirement the resolver MUST halt rather than
    invent a minor-version string; this surfaces the failure by engine+major so
    the caller can report it (design Error Handling: "Engine version
    unresolvable from API ... no fabricated version").
    """

    def __init__(self, engine: str, major_version: str, region: str) -> None:
        self.engine = engine
        self.major_version = major_version
        self.region = region
        super().__init__(
            f"No RDS engine version found for engine {engine!r} major "
            f"{major_version!r} in region {region!r}. The minor version cannot "
            "be resolved from aws rds describe-db-engine-versions and MUST NOT "
            "be fabricated; halting."
        )


class UnsupportedParameterGroupFamilyError(Exception):
    """The resolved ``engine`` + major-version combination does not map to one
    of the five supported parameter-group families (R5.5, R5.7).

    The message reports the offending combination together with the full list of
    supported families so the caller can correct the input rather than emit a
    fabricated or partially matching family (R5.8).
    """

    def __init__(self, engine: str, major_version: str) -> None:
        self.engine = engine
        self.major_version = major_version
        self.attempted_family = f"{engine}-{major_version}"
        supported = ", ".join(SUPPORTED_PARAMETER_GROUP_FAMILIES)
        super().__init__(
            f"Unsupported parameter-group family {self.attempted_family!r} for "
            f"engine {engine!r} major {major_version!r}. Supported families are: "
            f"{supported}."
        )


# ---------------------------------------------------------------------------
# Injectable AWS query interface
# ---------------------------------------------------------------------------

#: Type of the injectable lister: takes ``(engine, region)`` and returns the raw
#: ``EngineVersion`` strings the API reports for that engine in that region. Kept
#: deliberately narrow (engine + region in, version strings out) so a test can
#: supply a pure in-memory stub and the selection logic stays AWS-free (R5.1).
EngineVersionLister = Callable[[str, str], Iterable[str]]


def boto3_engine_version_lister(session: Optional["object"] = None) -> EngineVersionLister:
    """Build a production :data:`EngineVersionLister` backed by boto3.

    This is the only place that touches AWS. It is NOT used by the unit tests
    (which inject an in-memory stub), so importing this module never requires
    boto3 or credentials. ``boto3`` is imported lazily inside the returned
    closure for the same reason.

    Args:
        session: an optional pre-built ``boto3.Session`` (so the caller's
            ``AWS_Credential_Source`` resolution, R16, is reused). When ``None``
            a default session is created on first use.

    Returns:
        A callable ``(engine, region) -> list[str]`` that queries
        ``describe_db_engine_versions`` filtered by engine + region and returns
        the ``EngineVersion`` strings, paginating fully.
    """

    def _lister(engine: str, region: str) -> list[str]:
        import boto3  # lazy: keeps tests and bare imports boto3-free

        sess = session or boto3.Session()
        client = sess.client("rds", region_name=region)
        paginator = client.get_paginator("describe_db_engine_versions")
        versions: list[str] = []
        for page in paginator.paginate(Engine=engine):
            for dbev in page.get("DBEngineVersions", []):
                ev = dbev.get("EngineVersion")
                if ev:
                    versions.append(ev)
        return versions

    return _lister


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedEngineVersion:
    """The outcome of resolving an engine version and its parameter-group family.

    Attributes:
        engine: the Db2 engine edition the resolution was performed for.
        engine_version: the concrete, API-sourced minor version selected (e.g.
            ``12.1.4.0``) -- never fabricated (R5.1).
        major_version: the first two dot components (e.g. ``12.1``) (R5.4).
        parameter_group_family: the derived ``<engine>-<major>`` family, always
            one entry from :data:`SUPPORTED_PARAMETER_GROUP_FAMILIES` (R5.4/5.8).
        candidates: the full set of API-reported versions for the engine+major
            that the selection chose from, for auditability.
    """

    engine: str
    engine_version: str
    major_version: str
    parameter_group_family: str
    candidates: tuple[str, ...]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def major_version_of(engine_version: str) -> str:
    """Return the major version (first two dot-separated components) of a full
    engine version, e.g. ``12.1.4.0`` -> ``12.1`` (R5.4).

    Raises:
        ValueError: ``engine_version`` does not have at least two dot-separated
            components, so a major cannot be derived without guessing.
    """
    parts = engine_version.strip().split(".")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError(
            f"Cannot derive a major version from {engine_version!r}: expected at "
            "least two dot-separated components (e.g. '12.1.4.0')."
        )
    return f"{parts[0]}.{parts[1]}"


def _version_key(version: str) -> tuple[int, ...]:
    """Return a sortable key for a dotted version string.

    Numeric components sort numerically; a non-numeric component sorts as ``-1``
    so a clean numeric version always outranks a malformed one rather than
    raising. ``12.1.4.0`` -> ``(12, 1, 4, 0)``.
    """
    key: list[int] = []
    for part in version.strip().split("."):
        try:
            key.append(int(part))
        except ValueError:
            key.append(-1)
    return tuple(key)


def derive_parameter_group_family(engine: str, major_version: str) -> str:
    """Derive the parameter-group family ``<engine>-<major>`` and validate it
    against the five-family matrix (R5.4, R5.5, R5.7, R5.8).

    Args:
        engine: the Db2 engine edition (``db2-ce`` / ``db2-se`` / ``db2-ae``).
        major_version: the major version (e.g. ``12.1``).

    Returns:
        The family string, guaranteed to be one of
        :data:`SUPPORTED_PARAMETER_GROUP_FAMILIES`.

    Raises:
        UnsupportedParameterGroupFamilyError: the combination is not one of the
            five supported families (e.g. ``db2-ce-11.5``); the message lists all
            five (R5.5/5.7).
    """
    family = f"{engine}-{major_version}"
    if family not in SUPPORTED_PARAMETER_GROUP_FAMILIES:
        raise UnsupportedParameterGroupFamilyError(engine, major_version)
    return family


def select_highest_minor(versions: Iterable[str], major_version: str) -> Optional[str]:
    """Return the highest full version among ``versions`` whose major equals
    ``major_version``, or ``None`` when none match (R5.1).

    Only versions whose own first-two components equal ``major_version`` are
    considered, so ``11.5.x`` entries are ignored when resolving ``12.1``. The
    highest is chosen by numeric per-component comparison.
    """
    matching = [
        v for v in versions if v and v.strip() and _safe_major(v) == major_version
    ]
    if not matching:
        return None
    return max(matching, key=_version_key)


def _safe_major(version: str) -> Optional[str]:
    """``major_version_of`` that returns ``None`` instead of raising, for use in
    filtering a possibly-noisy API version list."""
    try:
        return major_version_of(version)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Engine-version resolution (R5.1, R5.6)
# ---------------------------------------------------------------------------


def resolve_engine_version(
    *,
    engine: str,
    region: str,
    lister: EngineVersionLister,
    major_version: Optional[str] = None,
) -> ResolvedEngineVersion:
    """Resolve the concrete engine version and parameter-group family for an
    engine in a region (R5.1, R5.4, R5.6).

    Resolution steps:

    1. Default the major to ``12.1`` when ``major_version`` is unpinned (R5.6).
    2. Validate the engine+major maps to a supported parameter-group family
       *before* hitting the API (R5.5/5.7) -- no point querying for a
       combination that can never render (e.g. ``db2-ce-11.5``).
    3. Query the injected ``lister`` for the engine in the region and select the
       highest minor of the major (R5.1). NEVER fabricate: an empty result for
       the engine+major halts with :class:`EngineVersionResolutionError`.

    Args:
        engine: the resolved Db2 engine edition.
        region: the target AWS region.
        lister: the injectable AWS query (see :data:`EngineVersionLister`). Tests
            pass an in-memory stub so no real AWS call is made.
        major_version: an explicit major to pin (e.g. ``11.5``); ``None`` ->
            default ``12.1`` (R5.6).

    Returns:
        A :class:`ResolvedEngineVersion` with the API-sourced minor version and
        the validated parameter-group family.

    Raises:
        UnsupportedParameterGroupFamilyError: engine+major is not a supported
            family (R5.5/5.7).
        EngineVersionResolutionError: the API reports no version for the
            engine+major, so the minor cannot be resolved without fabrication
            (R5.1).
    """
    major = (major_version or DEFAULT_MAJOR_VERSION).strip()

    # Validate the family first so an impossible combination fails fast with the
    # supported list, before any API round-trip (R5.5/5.7).
    family = derive_parameter_group_family(engine, major)

    reported = list(lister(engine, region))
    selected = select_highest_minor(reported, major)
    if selected is None:
        # No real version for this engine+major -> halt, never fabricate (R5.1).
        raise EngineVersionResolutionError(engine, major, region)

    matching = tuple(v for v in reported if _safe_major(v) == major)
    return ResolvedEngineVersion(
        engine=engine,
        engine_version=selected,
        major_version=major,
        parameter_group_family=family,
        candidates=matching,
    )
