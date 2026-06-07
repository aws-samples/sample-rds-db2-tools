"""Unit tests for AWS_Credential_Source resolution (task 10, Requirement 16).

Covers: the three credential-source kinds (named profile / env / default chain)
and their tier-independence (R16.1, R16.2, R16.3, R16.5); the never-paste-secrets
rule (R16.2/16.3/16.4); resolve-and-report account id + region via
``sts get-caller-identity`` before any mutating call (R16.6); and the two halt
paths — unresolvable credentials naming attempted sources (R16.7) and a
named-but-missing profile with no silent fallback (R16.8).

The boto3 ``Session`` and STS client are abstracted behind an injectable
``session_factory``, so NO real AWS call is made here: every test injects an
in-memory fake session whose STS client returns a canned identity or raises the
relevant botocore-shaped error. ``boto3_session_factory`` is the production
wiring and is intentionally not exercised against AWS.

Pure tests, no AWS.
"""

from __future__ import annotations

import inspect

import pytest

from scripts.credentials import (
    DEFAULT_CHAIN_SOURCES,
    CredentialSourceKind,
    CredentialsUnresolvedError,
    ProfileNotResolvedError,
    ResolvedCredentials,
    boto3_session_factory,
    resolve_credentials,
)


# ---------------------------------------------------------------------------
# In-memory fakes (mock boto3 Session + STS client; no AWS)
# ---------------------------------------------------------------------------


class FakeStsClient:
    """A stand-in for the boto3 STS client.

    ``get_caller_identity`` returns ``identity`` or, when ``error`` is set,
    raises it — letting tests simulate both a successful identity resolution and
    the missing-credentials path.
    """

    def __init__(self, identity=None, error=None):
        self._identity = identity or {}
        self._error = error
        self.calls = 0

    def get_caller_identity(self):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._identity


class FakeSession:
    """A stand-in for ``boto3.Session`` recording how it was built and used."""

    def __init__(self, *, region_name=None, sts_client=None):
        self.region_name = region_name
        self._sts = sts_client or FakeStsClient(
            identity={
                "Account": "123456789012",
                "Arn": "arn:aws:iam::123456789012:user/db2-operator",
                "UserId": "AIDEXAMPLEUSERID",
            }
        )
        self.client_calls = []

    def client(self, service, region_name=None):
        self.client_calls.append((service, region_name))
        if service == "sts":
            return self._sts
        raise AssertionError(f"unexpected client requested: {service}")


# Botocore-shaped errors reproduced by class name (the resolver matches by name
# so tests need not depend on botocore being importable).


class NoCredentialsError(Exception):
    """Mirrors botocore.exceptions.NoCredentialsError by class name."""


class ProfileNotFound(Exception):
    """Mirrors botocore.exceptions.ProfileNotFound by class name."""


def make_factory(session=None, *, raise_profile_not_found=False):
    """Build an injectable session_factory that records its (profile, region)
    calls and returns ``session`` (or raises ProfileNotResolvedError-equivalent).
    """
    calls = []

    def _factory(profile, region):
        calls.append((profile, region))
        if raise_profile_not_found:
            # The production factory converts ProfileNotFound -> the skill error;
            # the fake does the same so the resolver sees the halt error.
            raise ProfileNotResolvedError(profile or "", "profile (sandbox) not found")
        sess = session or FakeSession(region_name=region)
        return sess

    _factory.calls = calls
    return _factory


# ---------------------------------------------------------------------------
# R16.1 / R16.6: resolve + report identity via STS before any mutating call
# ---------------------------------------------------------------------------


def test_resolve_reports_account_id_and_region_via_sts():
    session = FakeSession(region_name="us-east-1")
    factory = make_factory(session)

    resolved = resolve_credentials(region="us-east-1", session_factory=factory)

    assert isinstance(resolved, ResolvedCredentials)
    assert resolved.account_id == "123456789012"
    assert resolved.region == "us-east-1"
    assert resolved.arn == "arn:aws:iam::123456789012:user/db2-operator"
    assert resolved.user_id == "AIDEXAMPLEUSERID"
    # get_caller_identity was actually called (identity resolved up front, R16.6).
    assert session._sts.calls == 1
    assert ("sts", "us-east-1") in session.client_calls


def test_identity_report_is_human_readable_and_secret_free():
    session = FakeSession(region_name="eu-west-1")
    resolved = resolve_credentials(
        region="eu-west-1", session_factory=make_factory(session)
    )
    report = resolved.identity_report()
    assert "123456789012" in report
    assert "eu-west-1" in report
    assert "default credential chain" in report


# ---------------------------------------------------------------------------
# R16.2: named profile passed through; no env credentials required
# ---------------------------------------------------------------------------


def test_named_profile_is_passed_through_to_factory():
    session = FakeSession(region_name="us-west-2")
    factory = make_factory(session)

    resolved = resolve_credentials(
        "prod-admin", region="us-west-2", session_factory=factory
    )

    assert resolved.source_kind is CredentialSourceKind.PROFILE
    assert resolved.profile == "prod-admin"
    # The profile name reached the factory verbatim (R16.2).
    assert factory.calls == [("prod-admin", "us-west-2")]


def test_named_profile_identity_report_names_the_profile():
    resolved = resolve_credentials(
        "prod-admin", region="us-west-2", session_factory=make_factory()
    )
    assert "named profile 'prod-admin'" in resolved.identity_report()


# ---------------------------------------------------------------------------
# R16.3: no profile -> default chain; no named profile required to exist
# ---------------------------------------------------------------------------


def test_no_profile_uses_default_chain():
    factory = make_factory(FakeSession(region_name="us-east-1"))
    resolved = resolve_credentials(session_factory=factory)
    assert resolved.source_kind is CredentialSourceKind.DEFAULT_CHAIN
    assert resolved.profile is None
    # profile passed to the factory was None (default chain, R16.3).
    assert factory.calls == [(None, None)]


def test_region_falls_back_to_session_region_when_unspecified():
    # Caller did not pass region; the session resolved one (e.g. from config).
    session = FakeSession(region_name="ap-southeast-1")
    resolved = resolve_credentials(session_factory=make_factory(session))
    assert resolved.region == "ap-southeast-1"
    # With no explicit region, the STS client is built without one.
    assert session.client_calls == [("sts", None)]


# ---------------------------------------------------------------------------
# R16.7: unresolvable credentials -> halt naming attempted sources
# ---------------------------------------------------------------------------


def test_unresolvable_default_chain_halts_naming_sources():
    sts = FakeStsClient(error=NoCredentialsError("Unable to locate credentials"))
    session = FakeSession(region_name=None, sts_client=sts)
    factory = make_factory(session)

    with pytest.raises(CredentialsUnresolvedError) as exc:
        resolve_credentials(session_factory=factory)

    # Every default-chain source is named in the error (R16.7).
    for src in DEFAULT_CHAIN_SOURCES:
        assert src in str(exc.value)
    assert exc.value.attempted_sources == DEFAULT_CHAIN_SOURCES


# ---------------------------------------------------------------------------
# R16.8: named-but-missing profile -> halt, NO silent fallback
# ---------------------------------------------------------------------------


def test_missing_named_profile_halts_no_fallback():
    factory = make_factory(raise_profile_not_found=True)

    with pytest.raises(ProfileNotResolvedError) as exc:
        resolve_credentials("does-not-exist", session_factory=factory)

    assert exc.value.profile == "does-not-exist"
    # The factory was called exactly once (for the named profile) and resolution
    # never tried the default chain afterwards (no silent fallback, R16.8).
    assert factory.calls == [("does-not-exist", None)]


def test_named_profile_loaded_but_no_credentials_is_profile_error_not_fallback():
    # Profile exists/loads, but yields no usable credentials when STS is called.
    sts = FakeStsClient(error=NoCredentialsError("partial credentials"))
    session = FakeSession(region_name="us-east-1", sts_client=sts)
    factory = make_factory(session)

    with pytest.raises(ProfileNotResolvedError) as exc:
        resolve_credentials("half-configured", session_factory=factory)

    # Reported as the named profile unresolved (R16.8), not a generic
    # default-chain failure.
    assert exc.value.profile == "half-configured"


def test_non_credential_sts_error_propagates_unchanged():
    # An unrelated STS error (e.g. throttling) must not be masked as a
    # credential-resolution halt.
    class ThrottlingException(Exception):
        pass

    sts = FakeStsClient(error=ThrottlingException("Rate exceeded"))
    session = FakeSession(region_name="us-east-1", sts_client=sts)

    with pytest.raises(ThrottlingException):
        resolve_credentials(session_factory=make_factory(session))


# ---------------------------------------------------------------------------
# R16.2 / R16.4: never prompt for or store pasted keys
# ---------------------------------------------------------------------------


def test_resolve_credentials_has_no_secret_parameters():
    # The public API must not accept pasted access key / secret / token (R16.4):
    # only a profile name, a region, and the injectable factory.
    params = set(inspect.signature(resolve_credentials).parameters)
    forbidden = {
        "access_key",
        "aws_access_key_id",
        "secret_key",
        "aws_secret_access_key",
        "session_token",
        "aws_session_token",
        "password",
    }
    assert params.isdisjoint(forbidden)
    assert params == {"profile", "region", "session_factory"}


def test_production_factory_has_no_secret_parameters():
    # The production boto3 factory likewise accepts only profile + region.
    params = set(inspect.signature(boto3_session_factory).parameters)
    assert params == {"profile", "region"}


def test_masked_dict_excludes_session_and_secrets():
    resolved = resolve_credentials(
        "prod-admin",
        region="us-east-1",
        session_factory=make_factory(FakeSession(region_name="us-east-1")),
    )
    masked = resolved.masked_dict()
    assert "session" not in masked
    # Only the non-secret identity facts are present.
    assert set(masked) == {
        "source_kind",
        "profile",
        "account_id",
        "region",
        "arn",
        "user_id",
    }


# ---------------------------------------------------------------------------
# R16.5: credential source is independent of Deployment_Tier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", ["sandbox", "dev", "prod"])
@pytest.mark.parametrize("profile", [None, "named-profile"])
def test_credential_source_independent_of_tier(tier, profile):
    # Resolution takes no tier argument at all and behaves identically whatever
    # tier the surrounding deployment uses (R16.5): any source × any tier.
    session = FakeSession(region_name="us-east-1")
    resolved = resolve_credentials(
        profile, region="us-east-1", session_factory=make_factory(session)
    )
    assert resolved.account_id == "123456789012"
    expected_kind = (
        CredentialSourceKind.PROFILE if profile else CredentialSourceKind.DEFAULT_CHAIN
    )
    assert resolved.source_kind is expected_kind
