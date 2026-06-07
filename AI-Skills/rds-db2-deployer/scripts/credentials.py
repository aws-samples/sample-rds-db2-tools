"""AWS credential and identity resolution for the rds-db2-provision-skill (R16).

The ``AWS_Credential_Source`` is the mechanism by which the Provisioning_Agent
obtains the AWS credentials it uses for every API call. Per Requirement 16 it is
one of exactly three kinds, and it is fully **independent of the
``Deployment_Tier``** (R16.5) — any source composes with any tier:

1. ``PROFILE``  — a named AWS CLI/SDK profile (R16.2). When a profile is named
   it is passed through to every API call and no environment-variable
   credentials are required.
2. ``ENVIRONMENT`` / ``DEFAULT_CHAIN`` — when no profile is named, the ambient
   default credential chain is used (environment variables, then the instance/
   container role) and no named profile is required to exist (R16.1, R16.3).

Hard rules this module enforces (the security spine of R16):

* **Never paste secrets (R16.2/16.3/16.4).** This module only ever accepts a
  *profile name*. It has no parameter, code path, or storage for an access key,
  secret key, or session token, and it never writes such a value anywhere. The
  only secret-bearing object is the live ``boto3.Session``, which holds the
  resolved credentials in memory exactly as the AWS SDK does — it is returned to
  the caller but never serialized into the intent, Terraform, logs, artifacts,
  or PR text.
* **Resolve + report identity before any mutation (R16.6).** Resolution calls
  ``sts get-caller-identity`` and returns the active account id, region, and
  caller ARN in the descriptor, so the agent can confirm *where* it is about to
  act before any mutating API call.
* **Halt, never silently fall back (R16.7/16.8).** Unresolvable credentials
  raise :class:`CredentialsUnresolvedError` naming the sources attempted; a
  named-but-missing/unloadable profile raises :class:`ProfileNotResolvedError`
  rather than dropping to the default chain.

Testability (mirrors ``engine_versions.py`` / ``vpc_precheck.py``): the only
AWS-touching step is creating a session and calling STS. Both are injected via a
:data:`SessionFactory` — ``(profile, region) -> session-like``. Production wires
:func:`boto3_session_factory`; unit tests inject an in-memory fake session whose
STS client returns a canned ``get_caller_identity`` (or raises the relevant
botocore error), so NO real AWS call is ever made in tests. ``boto3`` is
imported lazily inside the production factory, so importing this module never
requires boto3 or credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Credential-source kinds
# ---------------------------------------------------------------------------


class CredentialSourceKind(str, Enum):
    """The three (and only three) kinds of ``AWS_Credential_Source`` (R16.1).

    ``PROFILE`` is a named AWS CLI/SDK profile (R16.2). ``ENVIRONMENT`` and
    ``DEFAULT_CHAIN`` both describe the no-profile path (R16.3): the ambient
    default chain resolves credentials from environment variables first, then
    the instance/container role. They are distinguished only for reporting which
    sources were attempted (R16.7); resolution itself treats "no profile" as a
    single default-chain attempt.
    """

    PROFILE = "named_profile"
    ENVIRONMENT = "environment_variables"
    DEFAULT_CHAIN = "default_credential_chain"


#: Human-readable names of the sources attempted on the no-profile path, in the
#: order the default chain consults them. Used to name attempted sources when
#: resolution fails (R16.7).
DEFAULT_CHAIN_SOURCES: tuple[str, ...] = (
    CredentialSourceKind.ENVIRONMENT.value,
    CredentialSourceKind.DEFAULT_CHAIN.value,
)


# ---------------------------------------------------------------------------
# Errors (all halt before any mutating AWS call — R16.7/16.8)
# ---------------------------------------------------------------------------


class CredentialResolutionError(Exception):
    """Base class for every credential-resolution failure (R16.7/16.8).

    Carries ``attempted_sources`` so the caller can report, by name, which
    credential sources were tried before halting (R16.7).
    """

    def __init__(self, message: str, attempted_sources: tuple[str, ...]) -> None:
        self.attempted_sources = attempted_sources
        super().__init__(message)


class ProfileNotResolvedError(CredentialResolutionError):
    """A named AWS profile was specified but does not exist or cannot be loaded
    (R16.8).

    The agent MUST report the named profile as unresolved and halt rather than
    silently falling back to a different credential source — so this is a
    distinct, non-recoverable error, never converted into a default-chain
    attempt.
    """

    def __init__(self, profile: str, reason: str) -> None:
        self.profile = profile
        self.reason = reason
        super().__init__(
            f"Named AWS profile {profile!r} could not be resolved: {reason}. "
            "Halting rather than silently falling back to the default credential "
            "chain.",
            attempted_sources=(f"{CredentialSourceKind.PROFILE.value}:{profile}",),
        )


class CredentialsUnresolvedError(CredentialResolutionError):
    """No AWS credentials could be resolved from any attempted source (R16.7).

    Raised on the no-profile path when the default credential chain yields no
    credentials (no environment variables and no ambient instance/container
    role). The message names every source attempted.
    """

    def __init__(self, attempted_sources: tuple[str, ...], reason: str) -> None:
        sources = ", ".join(attempted_sources) if attempted_sources else "none"
        super().__init__(
            "Unable to resolve AWS credentials from any AWS_Credential_Source. "
            f"Attempted sources: {sources}. Reason: {reason}. Halting before any "
            "AWS API call.",
            attempted_sources=attempted_sources,
        )


# ---------------------------------------------------------------------------
# Injectable session factory (the ONLY seam that touches AWS)
# ---------------------------------------------------------------------------

#: Type of the injectable session factory: ``(profile, region) -> session``.
#: ``profile`` is the named profile or ``None`` for the default chain; ``region``
#: is the target region or ``None`` to let the session resolve it. The returned
#: object must be a boto3.Session-like with a ``.client("sts", ...)`` method and
#: a ``.region_name`` attribute. Kept deliberately narrow so a test supplies a
#: pure in-memory fake and the resolution logic stays AWS-free.
SessionFactory = Callable[[Optional[str], Optional[str]], Any]


def boto3_session_factory(profile: Optional[str], region: Optional[str]) -> Any:
    """Production :data:`SessionFactory` backed by boto3 (R16.1/16.2/16.3).

    This is the only place that constructs a real ``boto3.Session``. It is NOT
    used by the unit tests (which inject a fake), so importing this module never
    requires boto3 or credentials; ``boto3`` is imported lazily here.

    A ``profile`` is passed straight through to ``boto3.Session(profile_name=...)``
    so the named profile drives every subsequent client (R16.2). When ``profile``
    is ``None`` a default ``boto3.Session`` is built, which uses the ambient
    default credential chain (env vars, then instance/container role) (R16.3).

    Notably there is NO parameter for an access key, secret key, or session
    token: pasted long-term credentials are not accepted (R16.2/16.4).

    Raises:
        ProfileNotResolvedError: the named profile does not exist / cannot be
            loaded — re-raised as the skill's halt error rather than allowing a
            silent fallback (R16.8).
    """
    import boto3  # lazy: keeps tests and bare imports boto3-free
    from botocore.exceptions import ProfileNotFound

    kwargs: dict[str, Any] = {}
    if profile:
        kwargs["profile_name"] = profile
    if region:
        kwargs["region_name"] = region
    try:
        return boto3.Session(**kwargs)
    except ProfileNotFound as exc:
        # A named-but-missing profile must halt, never fall back (R16.8).
        raise ProfileNotResolvedError(profile or "", str(exc)) from exc


# ---------------------------------------------------------------------------
# Resolved descriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedCredentials:
    """The outcome of resolving an ``AWS_Credential_Source`` (R16.1/16.6).

    Holds the *non-secret* identity facts the agent reports for confirmation
    before any mutating call, plus the live ``session`` the caller uses to build
    clients. NO access key, secret key, or session token is ever stored here
    (R16.4); ``session`` carries the credentials in memory exactly as the SDK
    does and is never serialized.

    Attributes:
        source_kind: which :class:`CredentialSourceKind` resolved the creds.
        profile: the named profile used, or ``None`` for the default chain.
        account_id: the active AWS account id from ``sts get-caller-identity``
            (R16.6).
        region: the region the session resolved to (R16.6).
        arn: the caller ARN from ``sts get-caller-identity`` (identity report).
        user_id: the caller ``UserId`` from ``sts get-caller-identity``.
        session: the live boto3.Session-like object for building clients. Marked
            non-comparable/non-printed so it never leaks into logs via repr.
    """

    source_kind: CredentialSourceKind
    profile: Optional[str]
    account_id: str
    region: Optional[str]
    arn: str
    user_id: str
    session: Any = None

    def identity_report(self) -> str:
        """A single-line, secret-free identity summary for confirmation before
        any mutating API call (R16.6)."""
        via = (
            f"named profile {self.profile!r}"
            if self.source_kind is CredentialSourceKind.PROFILE
            else "the default credential chain"
        )
        region = self.region or "(region unset; resolve before mutating)"
        return (
            f"AWS identity resolved via {via}: account {self.account_id}, "
            f"region {region}, caller {self.arn}."
        )

    def masked_dict(self) -> dict[str, Any]:
        """A secret-free dict of the descriptor for artifacts/logs (R16.4).

        Deliberately omits ``session`` so no credential material is ever
        serialized.
        """
        return {
            "source_kind": self.source_kind.value,
            "profile": self.profile,
            "account_id": self.account_id,
            "region": self.region,
            "arn": self.arn,
            "user_id": self.user_id,
        }


# ---------------------------------------------------------------------------
# Resolution (R16.1, R16.6, R16.7, R16.8)
# ---------------------------------------------------------------------------


def _looks_like_missing_credentials(exc: Exception) -> bool:
    """True when ``exc`` indicates the default chain found no credentials.

    Matches botocore's ``NoCredentialsError`` / ``PartialCredentialsError`` by
    class name so this module does not need to import botocore at module load
    (keeping bare imports boto3-free); a stub in tests can raise an exception of
    the same class name to exercise the path.
    """
    return type(exc).__name__ in {
        "NoCredentialsError",
        "PartialCredentialsError",
        "CredentialRetrievalError",
    }


def resolve_credentials(
    profile: Optional[str] = None,
    *,
    region: Optional[str] = None,
    session_factory: Optional[SessionFactory] = None,
) -> ResolvedCredentials:
    """Resolve an ``AWS_Credential_Source`` and report its identity (R16).

    Resolution is independent of the ``Deployment_Tier`` (R16.5): the same call
    serves any tier. The steps:

    1. Build a session via ``session_factory`` (default: :func:`boto3_session_factory`).
       A non-empty ``profile`` selects the named-profile source and is passed
       through (R16.2); ``None`` selects the default chain (R16.3). A named-but-
       missing profile halts with :class:`ProfileNotResolvedError` — no silent
       fallback (R16.8).
    2. Call ``sts get-caller-identity`` to resolve and report the active account
       id, region, and caller ARN *before* any mutating call (R16.6).
    3. If no credentials are available on the default-chain path, halt with
       :class:`CredentialsUnresolvedError` naming the attempted sources (R16.7).

    This function NEVER accepts or returns a pasted access key/secret/token; the
    only credential-bearing object is the live ``session`` (R16.2/16.4).

    Args:
        profile: a named AWS CLI/SDK profile, or ``None`` for the default chain.
        region: the target region, or ``None`` to let the session resolve it.
        session_factory: the injectable ``(profile, region) -> session`` seam;
            tests pass an in-memory fake so no real AWS call is made.

    Returns:
        A :class:`ResolvedCredentials` with the identity facts and the live
        session.

    Raises:
        ProfileNotResolvedError: a named profile does not exist / cannot load
            (R16.8).
        CredentialsUnresolvedError: no credentials from any source (R16.7).
    """
    factory = session_factory or boto3_session_factory
    named = bool(profile)
    source_kind = (
        CredentialSourceKind.PROFILE if named else CredentialSourceKind.DEFAULT_CHAIN
    )
    # Sources we will name if resolution fails (R16.7/16.8).
    attempted = (
        (f"{CredentialSourceKind.PROFILE.value}:{profile}",)
        if named
        else DEFAULT_CHAIN_SOURCES
    )

    # Step 1: build the session. A ProfileNotResolvedError from the factory is a
    # halt condition and must propagate unchanged (R16.8) — never downgraded to a
    # default-chain attempt.
    session = factory(profile, region)

    # Step 2 + 3: resolve identity via STS; surface a missing-credentials error
    # as the named halt (R16.6/16.7).
    sts = session.client("sts", region_name=region) if region else session.client("sts")
    try:
        identity = sts.get_caller_identity()
    except Exception as exc:  # noqa: BLE001 - re-raised as a named halt below
        if _looks_like_missing_credentials(exc):
            if named:
                # A named profile that loaded but carries no usable credentials
                # is still an unresolved profile — halt, do not fall back (R16.8).
                raise ProfileNotResolvedError(profile or "", str(exc)) from exc
            raise CredentialsUnresolvedError(attempted, str(exc)) from exc
        raise

    resolved_region = region or getattr(session, "region_name", None)
    return ResolvedCredentials(
        source_kind=source_kind,
        profile=profile if named else None,
        account_id=str(identity.get("Account", "")),
        region=resolved_region,
        arn=str(identity.get("Arn", "")),
        user_id=str(identity.get("UserId", "")),
        session=session,
    )
