"""Smoke tests for the VPC_Precheck severity model (task 8.1, R11).

These exercise the runner end-to-end with an in-memory :class:`VpcFacts`
snapshot — no real AWS — confirming the severity wiring, halt-on-failure,
proceed-on-warning, and offer-to-create behaviour. The exhaustive
per-check / per-severity table-driven coverage is task 8.2.
"""

from __future__ import annotations

from scripts.vpc_precheck import (
    PrecheckSeverity,
    SgIngressRule,
    SubnetFact,
    VpcFacts,
    run_prechecks,
)


# ---------------------------------------------------------------------------
# Builders for a fully-ready VPC and a security-compliant intent
# ---------------------------------------------------------------------------


def _ready_facts(**overrides) -> VpcFacts:
    """A VPC that passes every check: 2 private AZs, DNS on, S3 gateway + all
    interface endpoints present, an SG permitting TCP 50443."""
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
        interface_endpoint_services=frozenset(
            {"rds", "lambda", "monitoring", "logs", "ec2", "sns", "secretsmanager"}
        ),
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


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_ready_vpc_passes_with_no_findings():
    report = run_prechecks(_intent(), _ready_facts())
    assert report.ok, report.report()
    assert report.findings == []


# ---------------------------------------------------------------------------
# Failures halt (ok == False) and are reported by name
# ---------------------------------------------------------------------------


def test_single_az_subnets_is_a_failure_and_halts():
    facts = _ready_facts(
        subnets=(
            SubnetFact("subnet-a", "us-east-1a", is_public=False),
            SubnetFact("subnet-b", "us-east-1a", is_public=False),
        )
    )
    report = run_prechecks(_intent(), facts)
    assert not report.ok
    assert "subnets_span_two_azs" in report.names()
    finding = next(f for f in report.failures if f.name == "subnets_span_two_azs")
    assert finding.creatable and finding.create_module == "1-networking"


def test_missing_ssl_ingress_is_a_failure():
    facts = _ready_facts(security_groups={"sg-0123456789abcdef0": ()})
    report = run_prechecks(_intent(), facts)
    assert not report.ok
    assert "sg_ssl_50443_ingress" in report.names()


def test_undescribable_vpc_short_circuits_to_single_failure():
    facts = _ready_facts(describable=False, describe_error="AccessDenied")
    report = run_prechecks(_intent(), facts)
    assert not report.ok
    assert report.names() == ["vpc_describable"]


def test_non_public_without_private_subnet_is_a_failure():
    facts = _ready_facts(
        has_internet_gateway=True,
        subnets=(
            SubnetFact("subnet-a", "us-east-1a", is_public=True),
            SubnetFact("subnet-b", "us-east-1b", is_public=True),
        ),
    )
    report = run_prechecks(_intent(publicly_accessible=False), facts)
    assert not report.ok
    assert "non_public_requires_private_subnet" in report.names()


# ---------------------------------------------------------------------------
# Warnings proceed (ok == True) after acknowledgement
# ---------------------------------------------------------------------------


def test_public_only_vpc_is_a_warning_that_proceeds():
    facts = _ready_facts(
        has_internet_gateway=True,
        subnets=(
            SubnetFact("subnet-a", "us-east-1a", is_public=True),
            SubnetFact("subnet-b", "us-east-1b", is_public=True),
        ),
    )
    # publicly_accessible=true so the non-public/private failure does not fire.
    report = run_prechecks(
        _intent(publicly_accessible=True, public_access_acknowledged=True), facts
    )
    assert report.ok, report.report()
    warning = next(f for f in report.warnings if f.name == "public_only_vpc")
    assert warning.severity is PrecheckSeverity.WARNING


def test_missing_s3_gateway_is_warning_when_s3_not_used():
    facts = _ready_facts(gateway_endpoint_services=frozenset())
    report = run_prechecks(_intent(restore_from_s3=False, enable_audit=False), facts)
    assert report.ok, report.report()
    s3 = next(f for f in report.findings if f.name == "s3_gateway_endpoint")
    assert s3.severity is PrecheckSeverity.WARNING


def test_missing_s3_gateway_is_failure_when_audit_enabled():
    facts = _ready_facts(gateway_endpoint_services=frozenset())
    report = run_prechecks(_intent(enable_audit=True), facts)
    assert not report.ok
    s3 = next(f for f in report.findings if f.name == "s3_gateway_endpoint")
    assert s3.severity is PrecheckSeverity.FAILURE
    assert s3.creatable


# ---------------------------------------------------------------------------
# Interface endpoints: failure for enabled feature, warning otherwise
# ---------------------------------------------------------------------------


def test_missing_endpoint_for_enabled_feature_is_failure():
    # logs is enabled (cloudwatch exports configured) but its endpoint is absent.
    facts = _ready_facts(
        interface_endpoint_services=frozenset(
            {"rds", "monitoring", "ec2", "secretsmanager"}
        )
    )
    report = run_prechecks(_intent(enable_cloudwatch_logs_exports=["diag.log"]), facts)
    assert not report.ok
    logs = next(f for f in report.findings if f.name == "interface_endpoint_logs")
    assert logs.severity is PrecheckSeverity.FAILURE


def test_missing_endpoint_for_disabled_feature_is_warning():
    # sns is not enabled; its missing endpoint is only a warning.
    facts = _ready_facts(
        interface_endpoint_services=frozenset(
            {"rds", "lambda", "monitoring", "logs", "ec2", "secretsmanager"}
        )
    )
    report = run_prechecks(_intent(sns_notifications=False), facts)
    assert report.ok, report.report()
    sns = next(f for f in report.findings if f.name == "interface_endpoint_sns")
    assert sns.severity is PrecheckSeverity.WARNING


# ---------------------------------------------------------------------------
# DNS attributes: offer to enable; failure only if still off
# ---------------------------------------------------------------------------


def test_dns_off_without_enabler_is_failure_offering_create():
    facts = _ready_facts(dns_support=False, dns_hostnames=True)
    report = run_prechecks(_intent(), facts)
    assert not report.ok
    dns = next(f for f in report.findings if f.name == "vpc_dns_attributes")
    assert dns.creatable and dns.create_module == "1-networking"


def test_dns_off_but_enabler_succeeds_clears_finding():
    facts = _ready_facts(dns_support=False, dns_hostnames=False)
    report = run_prechecks(_intent(), facts, dns_enabler=lambda vpc_id: (True, True))
    assert report.ok, report.report()
    assert "vpc_dns_attributes" not in report.names()


def test_dns_off_and_enabler_fails_remains_failure():
    facts = _ready_facts(dns_support=False, dns_hostnames=False)
    report = run_prechecks(_intent(), facts, dns_enabler=lambda vpc_id: (True, False))
    assert not report.ok
    assert "vpc_dns_attributes" in report.names()


# ---------------------------------------------------------------------------
# Multiple findings accumulate; offers collected
# ---------------------------------------------------------------------------


def test_multiple_failures_all_reported_and_offers_collected():
    facts = _ready_facts(
        subnets=(SubnetFact("subnet-a", "us-east-1a", is_public=False),),
        gateway_endpoint_services=frozenset(),
    )
    report = run_prechecks(_intent(enable_audit=True), facts)
    assert not report.ok
    names = report.names()
    assert "subnets_span_two_azs" in names
    assert "s3_gateway_endpoint" in names
    # both are creatable -> appear in the offers list
    offer_names = {f.name for f in report.offers}
    assert {"subnets_span_two_azs", "s3_gateway_endpoint"} <= offer_names
