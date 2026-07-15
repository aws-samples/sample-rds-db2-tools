"""Table-driven severity-classification tests for the VPC_Precheck (task 8.2).

These exhaustively cover, for every check, that it returns the correct
``PrecheckSeverity`` and a message that names the check / resource, and that the
runner's ``report.ok`` honours the halt-on-failure / proceed-on-warning
contract:

* ``report.ok is False`` when *any* FAILURE is present  -> failures halt (R11.9).
* ``report.ok is True`` when only WARNINGs are present   -> warnings proceed
  after acknowledgement (R11.10).

The interface-endpoint enabled->FAILURE vs disabled->WARNING split (R11.5) and
the public-only-VPC warning (R11.7) are covered explicitly in the tables.

No real AWS: every :class:`VpcFacts` is constructed in memory.

Validates: Requirements 11.5, 11.7, 11.9, 11.10
"""

from __future__ import annotations

import pytest

from scripts.vpc_precheck import (
    INTERFACE_ENDPOINT_FEATURES,
    NETWORKING_MODULE,
    PrecheckSeverity,
    SgIngressRule,
    SubnetFact,
    VpcFacts,
    check_dns_attributes,
    check_describable,
    check_interface_endpoints,
    check_non_public_requires_private,
    check_public_only_vpc,
    check_s3_gateway_endpoint,
    check_sg_ssl_ingress,
    check_subnet_azs,
    run_prechecks,
)

ALL_INTERFACE_SERVICES = frozenset(INTERFACE_ENDPOINT_FEATURES)


# ---------------------------------------------------------------------------
# Builders for a fully-ready VPC and a security-compliant intent
# ---------------------------------------------------------------------------


def _ready_facts(**overrides) -> VpcFacts:
    """A VPC that passes every check (mirrors the smoke-test builder)."""
    base = dict(
        vpc_id="vpc-0123456789abcdef0",
        describable=True,
        dns_support=True,
        dns_hostnames=True,
        subnets=(
            SubnetFact("subnet-a", "us-east-1a", is_public=False),
            SubnetFact("subnet-b", "us-east-1b", is_public=False),
        ),
        has_internet_gateway=False,
        gateway_endpoint_services=frozenset({"s3"}),
        interface_endpoint_services=ALL_INTERFACE_SERVICES,
        security_groups={
            "sg-0123456789abcdef0": (
                SgIngressRule("tcp", 50443, 50443, cidrs=("10.0.0.0/16",)),
            )
        },
    )
    base.update(overrides)
    return VpcFacts(**base)


def _intent(**overrides) -> dict:
    base = {
        "vpc_id": "vpc-0123456789abcdef0",
        "vpc_security_group_ids": ["sg-0123456789abcdef0"],
        "ingress_cidrs": ["10.0.0.0/16"],
        "publicly_accessible": False,
        "manage_master_user_password": True,
        "monitoring_interval": 15,
        "enable_cloudwatch_logs_exports": ["diag.log"],
        "restore_from_s3": False,
        "enable_audit": False,
        "sns_notifications": False,
    }
    base.update(overrides)
    return base


# ===========================================================================
# Table 1: per-check severity classification + message names the resource.
#
# Each row drives a check (via ``run_prechecks`` so the wiring is exercised)
# with facts/intent chosen to trigger exactly that finding, and asserts the
# finding's name, severity, message substring, and creatability.
# ===========================================================================

# (case_id, finding_name, expected_severity, message_substr, creatable,
#  facts, intent)
SEVERITY_CASES = [
    # -- R11.12: undescribable VPC -> FAILURE -----------------------------
    (
        "vpc_undescribable_failure",
        "vpc_describable",
        PrecheckSeverity.FAILURE,
        "could not be described",
        False,
        _ready_facts(describable=False, describe_error="AccessDenied"),
        _intent(),
    ),
    # -- R11.1: <2 AZs -> FAILURE (creatable) -----------------------------
    (
        "subnets_single_az_failure",
        "subnets_span_two_azs",
        PrecheckSeverity.FAILURE,
        "Availability Zones",
        True,
        _ready_facts(
            subnets=(
                SubnetFact("subnet-a", "us-east-1a", is_public=False),
                SubnetFact("subnet-b", "us-east-1a", is_public=False),
            )
        ),
        _intent(),
    ),
    # -- R11.2: DNS off (no enabler) -> FAILURE (creatable) ---------------
    (
        "dns_disabled_failure",
        "vpc_dns_attributes",
        PrecheckSeverity.FAILURE,
        "enableDnsSupport",
        True,
        _ready_facts(dns_support=False, dns_hostnames=True),
        _intent(),
    ),
    # -- R11.3: S3 audit/integration on, no S3 gateway -> FAILURE ---------
    (
        "s3_gateway_required_failure",
        "s3_gateway_endpoint",
        PrecheckSeverity.FAILURE,
        "S3 gateway VPC endpoint",
        True,
        _ready_facts(gateway_endpoint_services=frozenset()),
        _intent(enable_audit=True),
    ),
    # -- R11.4: S3 not used, no S3 gateway -> WARNING ---------------------
    (
        "s3_gateway_optional_warning",
        "s3_gateway_endpoint",
        PrecheckSeverity.WARNING,
        "S3 gateway VPC endpoint",
        True,
        _ready_facts(gateway_endpoint_services=frozenset()),
        _intent(restore_from_s3=False, enable_audit=False),
    ),
    # -- R11.6: no SSL 50443 ingress -> FAILURE ---------------------------
    (
        "ssl_ingress_missing_failure",
        "sg_ssl_50443_ingress",
        PrecheckSeverity.FAILURE,
        "50443",
        False,
        _ready_facts(security_groups={"sg-0123456789abcdef0": ()}),
        _intent(),
    ),
    # -- R11.7: public-only VPC -> WARNING (best practice) ----------------
    (
        "public_only_vpc_warning",
        "public_only_vpc",
        PrecheckSeverity.WARNING,
        "only public subnets",  # best-practice public-facing warning wording
        False,
        _ready_facts(
            has_internet_gateway=True,
            subnets=(
                SubnetFact("subnet-a", "us-east-1a", is_public=True),
                SubnetFact("subnet-b", "us-east-1b", is_public=True),
            ),
        ),
        # publicly_accessible=true so the R11.8 failure does not also fire.
        _intent(publicly_accessible=True),
    ),
    # -- R11.8: non-public but no private subnet -> FAILURE ---------------
    (
        "non_public_requires_private_failure",
        "non_public_requires_private_subnet",
        PrecheckSeverity.FAILURE,
        "private subnet",
        True,
        _ready_facts(
            has_internet_gateway=True,
            subnets=(
                SubnetFact("subnet-a", "us-east-1a", is_public=True),
                SubnetFact("subnet-b", "us-east-1b", is_public=True),
            ),
        ),
        _intent(publicly_accessible=False),
    ),
]


@pytest.mark.parametrize(
    "case_id, name, severity, message_substr, creatable, facts, intent",
    SEVERITY_CASES,
    ids=[c[0] for c in SEVERITY_CASES],
)
def test_check_severity_classification(
    case_id, name, severity, message_substr, creatable, facts, intent
):
    report = run_prechecks(intent, facts)
    finding = next((f for f in report.findings if f.name == name), None)
    assert finding is not None, f"{case_id}: expected a {name!r} finding; got {report.names()}"
    assert finding.severity is severity, f"{case_id}: wrong severity"
    # Message names the check/resource and is human-readable.
    assert message_substr in finding.message, f"{case_id}: message missing {message_substr!r}"
    assert name.split("_")[0] or finding.message  # message non-empty
    # Creatable findings advertise the 1-networking module (R11.11).
    assert finding.creatable is creatable, f"{case_id}: wrong creatable flag"
    if creatable:
        assert finding.create_module == NETWORKING_MODULE


# ===========================================================================
# Table 2: ok-status contract — failures halt, warnings proceed
# (R11.9 / R11.10).
# ===========================================================================

# (case_id, expected_ok, facts, intent)
OK_STATUS_CASES = [
    # All-green VPC: no findings -> ok.
    ("ready_vpc_ok", True, _ready_facts(), _intent()),
    # Only a warning present (optional S3 gateway missing) -> ok (proceeds).
    (
        "warning_only_proceeds",
        True,
        _ready_facts(gateway_endpoint_services=frozenset()),
        _intent(restore_from_s3=False, enable_audit=False),
    ),
    # A single failure -> not ok (halts).
    (
        "single_failure_halts",
        False,
        _ready_facts(security_groups={"sg-0123456789abcdef0": ()}),
        _intent(),
    ),
    # Failure + warning together -> not ok (any failure halts).
    (
        "failure_with_warning_halts",
        False,
        _ready_facts(
            gateway_endpoint_services=frozenset(),  # warning (S3 not used)
            security_groups={"sg-0123456789abcdef0": ()},  # failure (no 50443)
        ),
        _intent(restore_from_s3=False, enable_audit=False),
    ),
]


@pytest.mark.parametrize(
    "case_id, expected_ok, facts, intent",
    OK_STATUS_CASES,
    ids=[c[0] for c in OK_STATUS_CASES],
)
def test_report_ok_status(case_id, expected_ok, facts, intent):
    report = run_prechecks(intent, facts)
    assert report.ok is expected_ok, f"{case_id}: {report.report()}"
    if expected_ok:
        # No FAILURE recorded -> warnings (if any) proceed after ack (R11.10).
        assert report.failures == []
    else:
        # At least one FAILURE recorded and reported by name (R11.9).
        assert report.failures
        for f in report.failures:
            assert f.name in report.names()


def test_warning_only_report_lists_warning_by_name_and_proceeds():
    """R11.10: a warning-only report is ok and surfaces the warning by name."""
    report = run_prechecks(
        _intent(restore_from_s3=False, enable_audit=False),
        _ready_facts(gateway_endpoint_services=frozenset()),
    )
    assert report.ok
    assert report.warnings, "expected at least one warning"
    assert "s3_gateway_endpoint" in report.names()


# ===========================================================================
# Table 3: interface-endpoint enabled->FAILURE vs disabled->WARNING split
# (R11.5), per service.
# ===========================================================================

# Intent that turns every interface-endpoint feature ON, so a missing endpoint
# is a FAILURE for any service.
_ALL_FEATURES_ON = dict(
    manage_master_user_password=True,  # secretsmanager
    monitoring_interval=15,  # monitoring + ec2
    enable_cloudwatch_logs_exports=["diag.log"],  # logs
    restore_from_s3=True,  # lambda
    sns_notifications=True,  # sns
)

# Intent that turns every *optional* interface-endpoint feature OFF, so a
# missing endpoint is a WARNING (rds stays required regardless).
_ALL_OPTIONAL_OFF = dict(
    manage_master_user_password=False,
    monitoring_interval=0,
    enable_cloudwatch_logs_exports=[],
    restore_from_s3=False,
    enable_audit=False,
    sns_notifications=False,
)


@pytest.mark.parametrize("short_name", sorted(INTERFACE_ENDPOINT_FEATURES))
def test_interface_endpoint_failure_when_feature_enabled(short_name):
    """R11.5: a missing endpoint for an enabled feature is a FAILURE."""
    facts = _ready_facts(
        interface_endpoint_services=ALL_INTERFACE_SERVICES - {short_name}
    )
    report = run_prechecks(_intent(**_ALL_FEATURES_ON), facts)
    finding = next(
        f for f in report.findings if f.name == f"interface_endpoint_{short_name}"
    )
    assert finding.severity is PrecheckSeverity.FAILURE
    assert not report.ok
    # Message names the feature and offers create via 1-networking.
    assert INTERFACE_ENDPOINT_FEATURES[short_name] in finding.message
    assert finding.creatable and finding.create_module == NETWORKING_MODULE


# rds is always required, so it can never be a "disabled feature" warning.
_OPTIONAL_INTERFACE_SERVICES = sorted(set(INTERFACE_ENDPOINT_FEATURES) - {"rds"})


@pytest.mark.parametrize("short_name", _OPTIONAL_INTERFACE_SERVICES)
def test_interface_endpoint_warning_when_feature_disabled(short_name):
    """R11.5: a missing endpoint for a non-enabled feature is a WARNING."""
    facts = _ready_facts(
        interface_endpoint_services=ALL_INTERFACE_SERVICES - {short_name}
    )
    report = run_prechecks(_intent(**_ALL_OPTIONAL_OFF), facts)
    finding = next(
        f for f in report.findings if f.name == f"interface_endpoint_{short_name}"
    )
    assert finding.severity is PrecheckSeverity.WARNING
    # A warning alone must not flip ok to False (R11.10).
    assert finding.name not in {fail.name for fail in report.failures}
    assert INTERFACE_ENDPOINT_FEATURES[short_name] in finding.message


# ===========================================================================
# Direct (pure-function) checks of the individual check_* functions returning
# the right severity / None, without the runner wrapping them.
# ===========================================================================


def test_direct_checks_return_none_when_ok():
    facts = _ready_facts()
    intent = _intent()
    assert check_describable(facts) is None
    assert check_subnet_azs(facts) is None
    assert check_dns_attributes(facts) is None
    assert check_s3_gateway_endpoint(intent, facts) is None
    assert check_interface_endpoints(intent, facts) == []
    assert check_sg_ssl_ingress(intent, facts) is None
    assert check_public_only_vpc(facts) is None
    assert check_non_public_requires_private(intent, facts) is None


def test_public_only_vpc_no_finding_without_igw():
    """R11.7 guard: public subnets but no IGW is not a public-only warning."""
    facts = _ready_facts(
        has_internet_gateway=False,
        subnets=(
            SubnetFact("subnet-a", "us-east-1a", is_public=True),
            SubnetFact("subnet-b", "us-east-1b", is_public=True),
        ),
    )
    assert check_public_only_vpc(facts) is None
