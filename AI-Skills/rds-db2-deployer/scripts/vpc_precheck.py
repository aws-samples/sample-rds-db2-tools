"""The severity-tiered VPC_Precheck for the rds-db2-provision-skill (R11).

The ``VPC_Precheck`` validates that a target VPC is ready to host the RDS for
Db2 instance *before* rendering proceeds to ``terraform apply``. It is ported
faithfully from the bash provisioner ``0cr-ins.sh`` (the DNS-attribute check at
lines 4595-4607, the S3 gateway + interface-endpoint provisioning at 4648-4705,
and the public/private route-table logic at 3265-3362).

Design (matching the design doc's "VPC_Precheck" severity table):

* Every finding is a :class:`PrecheckFinding` carrying a stable ``name``, a
  :class:`PrecheckSeverity` (``FAILURE`` halts; ``WARNING`` proceeds after the
  customer acknowledges it), and a human-readable ``message`` (R11.9/11.10).
  When the missing thing is creatable, the finding records an offer to create
  it through the ``1-networking`` module (R11.11).
* :func:`run_prechecks` runs every check and returns a :class:`PrecheckReport`.
  ``report.ok`` is ``False`` when *any* ``FAILURE`` is present, so the caller
  halts before apply (R11.9); warnings alone leave ``ok`` ``True`` and are
  surfaced for acknowledgement (R11.10).

Testability — **no real AWS in the checks** (per the task's hard requirement):

* The AWS ``describe_*`` calls are abstracted behind a plain data snapshot,
  :class:`VpcFacts`, gathered by a :class:`VpcFactsProvider`. The checks are
  pure functions of ``(intent, facts)`` so unit tests construct a ``VpcFacts``
  in memory and never touch AWS.
* Production wires :class:`Boto3VpcFactsProvider`, a thin wrapper over an
  injected ``boto3`` EC2 client (so the caller's ``AWS_Credential_Source``
  resolution, R16, is reused). ``boto3`` is imported lazily and is never needed
  by the unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from enum import Enum
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

# ---------------------------------------------------------------------------
# Constants (ported from 0cr-ins.sh)
# ---------------------------------------------------------------------------

#: The Db2 SSL service port — the only port that accepts client connections and
#: the only port opened in the security-group ingress rule (R6.5, R11.6).
SSL_SERVICE_PORT = 50443

#: The Terraform module that creates missing networking resources (R11.11).
NETWORKING_MODULE = "1-networking"

#: Minimum number of distinct AZs the DB subnets must span (R11.1).
MIN_AVAILABILITY_ZONES = 2

#: The short names of the interface-endpoint services the deployment may need,
#: drawn from the set ported from ``0cr-ins.sh`` (line 4614-4625) and named in
#: R11.5: RDS, Lambda, CloudWatch monitoring, CloudWatch Logs, EC2, SNS,
#: Secrets Manager. Each maps its short key to the human-readable feature name
#: used in finding messages.
INTERFACE_ENDPOINT_FEATURES: dict[str, str] = {
    "rds": "RDS",
    "lambda": "Lambda",
    "monitoring": "CloudWatch monitoring",
    "logs": "CloudWatch Logs",
    "ec2": "EC2",
    "sns": "SNS",
    "secretsmanager": "Secrets Manager",
}


# ---------------------------------------------------------------------------
# Severity model and findings
# ---------------------------------------------------------------------------


class PrecheckSeverity(str, Enum):
    """The two-tier severity of a precheck finding (design "VPC_Precheck").

    ``FAILURE`` is a ``Precheck_Failure``: it blocks the deployment and halts
    before ``terraform apply`` (R11.9). ``WARNING`` is a ``Precheck_Warning``:
    best-practice advisory that the customer may acknowledge and proceed past
    (R11.10).
    """

    FAILURE = "failure"
    WARNING = "warning"


@dataclass(frozen=True)
class PrecheckFinding:
    """A single VPC_Precheck finding, reported by name (R11.9/11.10).

    Attributes:
        name: a stable, human-meaningful check name (e.g.
            ``subnets_span_two_azs``) used when reporting the finding by name.
        severity: ``FAILURE`` (halts) or ``WARNING`` (proceed after ack).
        message: a human-readable explanation, including best-practice guidance
            for warnings (R11.10).
        creatable: ``True`` when the missing resource can be created through the
            ``1-networking`` module, so the precheck can offer to create it
            before any apply (R11.11).
        create_module: the Terraform module that would create it (only
            meaningful when ``creatable`` is ``True``).
    """

    name: str
    severity: PrecheckSeverity
    message: str
    creatable: bool = False
    create_module: Optional[str] = None

    @property
    def is_failure(self) -> bool:
        return self.severity is PrecheckSeverity.FAILURE

    @property
    def is_warning(self) -> bool:
        return self.severity is PrecheckSeverity.WARNING

    def __str__(self) -> str:  # pragma: no cover - convenience formatting
        offer = (
            f" (creatable via {self.create_module})"
            if self.creatable and self.create_module
            else ""
        )
        return f"[{self.severity.value}] {self.name}: {self.message}{offer}"


@dataclass
class PrecheckReport:
    """Accumulates every VPC_Precheck finding across all checks (R11.9/11.10).

    ``ok`` is ``False`` when any ``FAILURE`` is present, so the caller halts
    before ``terraform apply`` (R11.9). Warnings alone keep ``ok`` ``True`` and
    are surfaced for acknowledgement (R11.10).
    """

    findings: list[PrecheckFinding] = dataclass_field(default_factory=list)

    @property
    def failures(self) -> list[PrecheckFinding]:
        """Every ``FAILURE`` finding, in first-seen order."""
        return [f for f in self.findings if f.is_failure]

    @property
    def warnings(self) -> list[PrecheckFinding]:
        """Every ``WARNING`` finding, in first-seen order."""
        return [f for f in self.findings if f.is_warning]

    @property
    def ok(self) -> bool:
        """``True`` when no ``FAILURE`` finding was recorded (R11.9).

        A report with only warnings is ``ok`` — the deployment proceeds after
        the customer acknowledges the warnings (R11.10).
        """
        return not any(f.is_failure for f in self.findings)

    @property
    def offers(self) -> list[PrecheckFinding]:
        """Findings that offer to create a missing resource via ``1-networking``
        (R11.11)."""
        return [f for f in self.findings if f.creatable]

    def add(self, finding: Optional[PrecheckFinding]) -> None:
        """Append a finding (ignoring ``None`` so checks can return ``None``)."""
        if finding is not None:
            self.findings.append(finding)

    def extend(self, findings: Sequence[PrecheckFinding]) -> None:
        self.findings.extend(findings)

    def names(self) -> list[str]:
        """The distinct finding names, in first-seen order (R11.9)."""
        seen: dict[str, None] = {}
        for f in self.findings:
            seen.setdefault(f.name, None)
        return list(seen)

    def report(self) -> str:
        """A multi-line, name + severity report of every finding (R11.9/11.10)."""
        if not self.findings:
            return "VPC_Precheck passed: no findings."
        lines: list[str] = []
        if self.failures:
            lines.append(
                f"VPC_Precheck FAILED with {len(self.failures)} blocking "
                "finding(s); halting before terraform apply:"
            )
            for f in self.failures:
                offer = (
                    f" -- offer to create via {f.create_module}"
                    if f.creatable
                    else ""
                )
                lines.append(f"  - {f.name}: {f.message}{offer}")
        if self.warnings:
            lines.append(
                f"VPC_Precheck warnings ({len(self.warnings)}); proceed after "
                "acknowledgement:"
            )
            for f in self.warnings:
                lines.append(f"  - {f.name}: {f.message}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# VPC facts snapshot (the AWS-call abstraction)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubnetFact:
    """A subnet considered for DB placement.

    ``is_public`` is ``True`` when the subnet's route table has a default route
    to an Internet Gateway (ported from the public/private determination in
    ``0cr-ins.sh`` 3265-3362).
    """

    subnet_id: str
    availability_zone: str
    is_public: bool


@dataclass(frozen=True)
class SgIngressRule:
    """A single inbound rule on a security group.

    ``protocol`` is the IP protocol (``"tcp"``, ``"udp"``, or ``"-1"`` for all).
    ``from_port``/``to_port`` are the inclusive port range. ``cidrs`` are the
    IPv4 CIDR sources; ``source_security_group_ids`` are SG sources.
    """

    protocol: str
    from_port: Optional[int]
    to_port: Optional[int]
    cidrs: tuple[str, ...] = ()
    source_security_group_ids: tuple[str, ...] = ()

    def permits_tcp_port(self, port: int) -> bool:
        """True when this rule allows TCP ``port`` (covers ``-1``/all-traffic)."""
        if self.protocol not in ("tcp", "-1", "all", "6"):
            return False
        if self.protocol in ("-1", "all"):
            return True
        if self.from_port is None or self.to_port is None:
            return False
        return self.from_port <= port <= self.to_port


@dataclass(frozen=True)
class VpcFacts:
    """An in-memory snapshot of the VPC state the checks read.

    This is the single seam between the (pure, testable) checks and AWS: a
    :class:`VpcFactsProvider` gathers it via ``describe_*`` calls in production,
    while unit tests construct it directly so no real AWS call is made.

    Attributes:
        vpc_id: the target VPC id.
        describable: ``False`` when the VPC cannot be described (does not exist
            or the describe call was denied), which is a ``FAILURE`` (R11.12).
        describe_error: the reason the VPC was undescribable, for the message.
        dns_support: value of the ``enableDnsSupport`` VPC attribute (R11.2).
        dns_hostnames: value of the ``enableDnsHostnames`` VPC attribute (R11.2).
        subnets: the candidate DB subnets (those in the subnet group / VPC).
        has_internet_gateway: ``True`` when the VPC has an attached IGW (R11.7).
        gateway_endpoint_services: short service names of the Gateway endpoints
            present (e.g. ``{"s3"}``) (R11.3/11.4).
        interface_endpoint_services: short service names of the Interface
            endpoints present (e.g. ``{"rds", "logs"}``) (R11.5).
        security_groups: map of SG id -> its inbound rules, for the SSL-50443
            ingress check (R11.6).
    """

    vpc_id: str
    describable: bool = True
    describe_error: Optional[str] = None
    dns_support: bool = True
    dns_hostnames: bool = True
    subnets: tuple[SubnetFact, ...] = ()
    has_internet_gateway: bool = False
    gateway_endpoint_services: frozenset[str] = frozenset()
    interface_endpoint_services: frozenset[str] = frozenset()
    security_groups: Mapping[str, tuple[SgIngressRule, ...]] = dataclass_field(
        default_factory=dict
    )

    @property
    def distinct_azs(self) -> set[str]:
        return {s.availability_zone for s in self.subnets}

    @property
    def has_private_subnet(self) -> bool:
        return any(not s.is_public for s in self.subnets)

    @property
    def has_public_subnet(self) -> bool:
        return any(s.is_public for s in self.subnets)


class VpcFactsProvider(Protocol):
    """The interface the runner uses to obtain a :class:`VpcFacts` snapshot.

    Production wires :class:`Boto3VpcFactsProvider`; tests inject a stub (or
    construct :class:`VpcFacts` directly), so the checks never call AWS.
    """

    def gather(self, intent: Mapping[str, Any]) -> VpcFacts:  # pragma: no cover
        ...


# ---------------------------------------------------------------------------
# Enabled-feature derivation (which interface endpoints the deployment needs)
# ---------------------------------------------------------------------------


def _truthy(value: Any) -> bool:
    """Mirror the validator/composer ``_truthy`` so "enabled" agrees across the
    skill (R13)."""
    return bool(value)


def _nonempty(value: Any) -> bool:
    """True when a list/string field is present and non-empty."""
    if value is None:
        return False
    if isinstance(value, (list, tuple, set, str)):
        return len(value) > 0
    return bool(value)


def s3_required(intent: Mapping[str, Any]) -> bool:
    """True when the deployment uses S3 — S3 restore integration or routing Db2
    audit data to S3 (R11.3). An S3 gateway endpoint is then a ``FAILURE`` if
    missing; otherwise its absence is a ``WARNING`` (R11.4)."""
    return _truthy(intent.get("restore_from_s3")) or _truthy(
        intent.get("enable_audit")
    )


def enabled_interface_features(intent: Mapping[str, Any]) -> dict[str, bool]:
    """Map each interface-endpoint short name to whether the deployment's
    enabled features require it (R11.5).

    A required-but-missing endpoint is a ``FAILURE``; a missing endpoint for a
    feature the deployment does not enable is a ``WARNING`` (R11.5). The mapping
    is grounded in how ``0cr-ins.sh`` uses each service:

    * ``rds`` — always required (the RDS control-plane access the instance and
      its management need).
    * ``secretsmanager`` — required when the managed master-user secret is used
      (``manage_master_user_password``, default true).
    * ``monitoring`` — required when enhanced monitoring is on
      (``monitoring_interval`` > 0).
    * ``logs`` — required when CloudWatch Logs exports are configured
      (``enable_cloudwatch_logs_exports`` non-empty).
    * ``lambda`` — required when the S3 restore / audit Lambda path is used.
    * ``ec2`` — required alongside enhanced monitoring (the monitoring path).
    * ``sns`` — required when event notifications are requested
      (``sns_notifications``); off by default.
    """
    monitoring_on = _to_int(intent.get("monitoring_interval")) > 0
    return {
        "rds": True,
        "secretsmanager": _truthy(intent.get("manage_master_user_password", True)),
        "monitoring": monitoring_on,
        "logs": _nonempty(intent.get("enable_cloudwatch_logs_exports")),
        "lambda": s3_required(intent),
        "ec2": monitoring_on,
        "sns": _truthy(intent.get("sns_notifications")),
    }


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def specified_ingress_sources(intent: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return ``(cidrs, security_group_ids)`` the SG must permit on 50443 (R11.6).

    Source CIDRs come from ``ingress_cidrs`` (or the synonym
    ``ingress_cidr_blocks``); source SGs come from ``ingress_security_group_ids``.
    Either or both may be empty, in which case the check only requires that
    *some* 50443 ingress exists.
    """
    cidrs = intent.get("ingress_cidrs")
    if not cidrs:
        cidrs = intent.get("ingress_cidr_blocks")
    source_sgs = intent.get("ingress_security_group_ids") or []
    cidrs = list(cidrs) if cidrs else []
    source_sgs = list(source_sgs) if source_sgs else []
    return cidrs, source_sgs


# ---------------------------------------------------------------------------
# Individual checks (pure: (intent, facts) -> finding(s))
# ---------------------------------------------------------------------------


def check_describable(facts: VpcFacts) -> Optional[PrecheckFinding]:
    """R11.12: an undescribable VPC (absent or describe denied) is a FAILURE."""
    if facts.describable:
        return None
    reason = facts.describe_error or "the VPC does not exist or the describe call was denied"
    return PrecheckFinding(
        name="vpc_describable",
        severity=PrecheckSeverity.FAILURE,
        message=(
            f"Target VPC {facts.vpc_id!r} could not be described: {reason}. "
            "Halting before terraform apply."
        ),
    )


def check_subnet_azs(facts: VpcFacts) -> Optional[PrecheckFinding]:
    """R11.1/11.11: DB subnets must span >=2 distinct AZs; fewer is a FAILURE,
    and the missing multi-AZ subnets are creatable via ``1-networking``."""
    azs = facts.distinct_azs
    if len(azs) >= MIN_AVAILABILITY_ZONES:
        return None
    found = ", ".join(sorted(azs)) if azs else "none"
    return PrecheckFinding(
        name="subnets_span_two_azs",
        severity=PrecheckSeverity.FAILURE,
        message=(
            f"DB subnets must span at least {MIN_AVAILABILITY_ZONES} "
            f"Availability Zones; found {len(azs)} ({found}). RDS for Db2 "
            "requires a subnet group across two or more AZs."
        ),
        creatable=True,
        create_module=NETWORKING_MODULE,
    )


def check_dns_attributes(
    facts: VpcFacts,
    enabler: Optional[Callable[[str], tuple[bool, bool]]] = None,
) -> Optional[PrecheckFinding]:
    """R11.2: ``enableDnsSupport`` and ``enableDnsHostnames`` must both be on.

    Ported from ``0cr-ins.sh`` 4595-4607: when either is off, offer to enable it
    via ``modify-vpc-attribute`` and treat it as a ``FAILURE`` *only if it
    remains disabled* after the attempt. ``enabler`` is the injectable
    remediation (``vpc_id -> (dns_support, dns_hostnames)`` after enabling);
    when ``None`` the offer is recorded as a blocking FAILURE without acting
    (the caller can re-run after the customer accepts the offer)."""
    support, hostnames = facts.dns_support, facts.dns_hostnames
    if support and hostnames:
        return None

    if enabler is not None:
        support, hostnames = enabler(facts.vpc_id)
        if support and hostnames:
            return None  # remediation succeeded; no finding

    disabled = []
    if not support:
        disabled.append("enableDnsSupport")
    if not hostnames:
        disabled.append("enableDnsHostnames")
    return PrecheckFinding(
        name="vpc_dns_attributes",
        severity=PrecheckSeverity.FAILURE,
        message=(
            f"VPC {facts.vpc_id!r} has {' and '.join(disabled)} disabled; "
            "interface endpoints with private DNS require both enabled. Offer "
            "to enable via modify-vpc-attribute; this is a failure only if it "
            "remains disabled."
        ),
        creatable=True,
        create_module=NETWORKING_MODULE,
    )


def check_s3_gateway_endpoint(
    intent: Mapping[str, Any], facts: VpcFacts
) -> Optional[PrecheckFinding]:
    """R11.3/11.4/11.11: an S3 gateway endpoint is required (FAILURE) when S3
    integration/audit is enabled, otherwise its absence is a WARNING. Missing
    endpoint is creatable via ``1-networking``."""
    if "s3" in facts.gateway_endpoint_services:
        return None

    if s3_required(intent):
        return PrecheckFinding(
            name="s3_gateway_endpoint",
            severity=PrecheckSeverity.FAILURE,
            message=(
                "S3 integration/audit is enabled but no S3 gateway VPC endpoint "
                f"exists in {facts.vpc_id!r}; backups/restore/audit to S3 will "
                "fail without it."
            ),
            creatable=True,
            create_module=NETWORKING_MODULE,
        )
    return PrecheckFinding(
        name="s3_gateway_endpoint",
        severity=PrecheckSeverity.WARNING,
        message=(
            f"No S3 gateway VPC endpoint in {facts.vpc_id!r}. Not required by "
            "this deployment, but recommended so future S3 integration/audit "
            "stays on the AWS network. Proceed after acknowledgement."
        ),
        creatable=True,
        create_module=NETWORKING_MODULE,
    )


def check_interface_endpoints(
    intent: Mapping[str, Any], facts: VpcFacts
) -> list[PrecheckFinding]:
    """R11.5/11.11: for each interface-endpoint service, a missing endpoint is a
    FAILURE when the corresponding feature is enabled and a WARNING otherwise.
    Each missing endpoint is creatable via ``1-networking``."""
    enabled = enabled_interface_features(intent)
    present = facts.interface_endpoint_services
    findings: list[PrecheckFinding] = []
    for short_name, feature_label in INTERFACE_ENDPOINT_FEATURES.items():
        if short_name in present:
            continue
        if enabled.get(short_name):
            findings.append(
                PrecheckFinding(
                    name=f"interface_endpoint_{short_name}",
                    severity=PrecheckSeverity.FAILURE,
                    message=(
                        f"{feature_label} is an enabled feature but its "
                        f"interface VPC endpoint is missing in {facts.vpc_id!r}; "
                        "the feature will fail in a private VPC without it."
                    ),
                    creatable=True,
                    create_module=NETWORKING_MODULE,
                )
            )
        else:
            findings.append(
                PrecheckFinding(
                    name=f"interface_endpoint_{short_name}",
                    severity=PrecheckSeverity.WARNING,
                    message=(
                        f"{feature_label} interface VPC endpoint is missing in "
                        f"{facts.vpc_id!r}. Not required (feature not enabled), "
                        "but recommended. Proceed after acknowledgement."
                    ),
                    creatable=True,
                    create_module=NETWORKING_MODULE,
                )
            )
    return findings


def check_sg_ssl_ingress(
    intent: Mapping[str, Any], facts: VpcFacts
) -> Optional[PrecheckFinding]:
    """R11.6: the target security group must permit inbound TCP on the Db2 SSL
    port 50443 from the specified source CIDRs/SGs; absence is a FAILURE."""
    target_sgs = list(intent.get("vpc_security_group_ids") or [])
    cidrs, source_sgs = specified_ingress_sources(intent)

    # Gather all 50443 TCP ingress rules across the intent's target SGs.
    matching_rules: list[SgIngressRule] = []
    for sg_id in target_sgs:
        for rule in facts.security_groups.get(sg_id, ()):  # type: ignore[arg-type]
            if rule.permits_tcp_port(SSL_SERVICE_PORT):
                matching_rules.append(rule)

    if not matching_rules:
        return PrecheckFinding(
            name="sg_ssl_50443_ingress",
            severity=PrecheckSeverity.FAILURE,
            message=(
                "No security-group ingress rule permits inbound TCP on the Db2 "
                f"SSL port {SSL_SERVICE_PORT}. Clients cannot reach the "
                "instance without it."
            ),
        )

    # If specific sources were named, ensure every one is covered on 50443.
    covered_cidrs = {c for r in matching_rules for c in r.cidrs}
    covered_sgs = {s for r in matching_rules for s in r.source_security_group_ids}
    # An all-traffic CIDR (0.0.0.0/0) on 50443 covers any named CIDR source.
    any_open_cidr = "0.0.0.0/0" in covered_cidrs

    missing = []
    for c in cidrs:
        if not any_open_cidr and c not in covered_cidrs:
            missing.append(c)
    for s in source_sgs:
        if s not in covered_sgs:
            missing.append(s)

    if missing:
        return PrecheckFinding(
            name="sg_ssl_50443_ingress",
            severity=PrecheckSeverity.FAILURE,
            message=(
                f"Security group permits TCP {SSL_SERVICE_PORT} but not from all "
                f"specified sources; missing ingress for: {', '.join(missing)}."
            ),
        )
    return None


def check_public_only_vpc(facts: VpcFacts) -> Optional[PrecheckFinding]:
    """R11.7: a VPC with only public (IGW-routed) subnets and no private subnet
    raises a best-practice WARNING that the environment is public-facing, and
    allows the deployment to proceed after acknowledgement."""
    if not facts.has_internet_gateway:
        return None
    if facts.has_private_subnet:
        return None
    if not facts.subnets:
        return None
    return PrecheckFinding(
        name="public_only_vpc",
        severity=PrecheckSeverity.WARNING,
        message=(
            f"VPC {facts.vpc_id!r} has only public subnets (routed to an "
            "Internet Gateway) and no private subnet. Best practice is that an "
            "RDS for Db2 database should not be public-facing unless absolutely "
            "required. Proceed after acknowledgement."
        ),
    )


def check_non_public_requires_private(
    intent: Mapping[str, Any], facts: VpcFacts
) -> Optional[PrecheckFinding]:
    """R11.8: if ``publicly_accessible=false`` but the VPC has no private subnet
    to place the instance in, that mismatch is a FAILURE."""
    publicly_accessible = _truthy(intent.get("publicly_accessible"))
    if publicly_accessible:
        return None
    if not facts.subnets:
        return None  # subnet-AZ check already covers an empty subnet set
    if facts.has_private_subnet:
        return None
    return PrecheckFinding(
        name="non_public_requires_private_subnet",
        severity=PrecheckSeverity.FAILURE,
        message=(
            "publicly_accessible=false but the target VPC has no private subnet "
            "to place the instance in. A private subnet is required for a "
            "non-public instance."
        ),
        creatable=True,
        create_module=NETWORKING_MODULE,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_prechecks(
    intent: Mapping[str, Any],
    facts: VpcFacts,
    *,
    dns_enabler: Optional[Callable[[str], tuple[bool, bool]]] = None,
) -> PrecheckReport:
    """Run every VPC_Precheck against ``facts`` and return a report (R11).

    The checks are pure functions of ``(intent, facts)``; ``facts`` is gathered
    once by a :class:`VpcFactsProvider` (or constructed directly in tests).
    ``report.ok`` is ``False`` when any ``FAILURE`` is present so the caller
    halts before ``terraform apply`` (R11.9); warnings alone leave it ``True``
    and are surfaced for acknowledgement (R11.10).

    Args:
        intent: the resolved ``Deployment_Intent``.
        facts: the VPC snapshot.
        dns_enabler: optional remediation for the DNS-attribute check (R11.2);
            ``vpc_id -> (dns_support, dns_hostnames)`` after enabling.

    Returns:
        A :class:`PrecheckReport` with every finding.
    """
    report = PrecheckReport()

    # An undescribable VPC short-circuits — no other fact is trustworthy (R11.12).
    describable = check_describable(facts)
    if describable is not None:
        report.add(describable)
        return report

    report.add(check_subnet_azs(facts))
    report.add(check_dns_attributes(facts, enabler=dns_enabler))
    report.add(check_s3_gateway_endpoint(intent, facts))
    report.extend(check_interface_endpoints(intent, facts))
    report.add(check_sg_ssl_ingress(intent, facts))
    report.add(check_public_only_vpc(facts))
    report.add(check_non_public_requires_private(intent, facts))
    return report


# ---------------------------------------------------------------------------
# Production boto3-backed facts provider (the ONLY place that touches AWS)
# ---------------------------------------------------------------------------


class Boto3VpcFactsProvider:
    """Gather :class:`VpcFacts` from AWS via an injected boto3 EC2 client.

    This is the only AWS-touching code in the module and is NOT used by the unit
    tests (which construct :class:`VpcFacts` directly), so importing this module
    never requires boto3 or credentials. The EC2 client is injected so the
    caller's ``AWS_Credential_Source`` resolution (R16) is reused and so this
    class itself stays mockable.
    """

    def __init__(self, ec2_client: Any, region: str) -> None:
        self._ec2 = ec2_client
        self._region = region

    @classmethod
    def from_session(cls, session: Any, region: str) -> "Boto3VpcFactsProvider":
        """Build a provider from a ``boto3.Session`` (lazy boto3 import)."""
        client = session.client("ec2", region_name=region)
        return cls(client, region)

    def gather(self, intent: Mapping[str, Any]) -> VpcFacts:
        """Describe the target VPC and assemble a :class:`VpcFacts`.

        On any describe error for the VPC itself, returns a snapshot with
        ``describable=False`` so the runner reports R11.12 and halts.
        """
        vpc_id = intent.get("vpc_id") or ""
        try:
            self._ec2.describe_vpcs(VpcIds=[vpc_id])
        except Exception as exc:  # noqa: BLE001 - surfaced as a precheck failure
            return VpcFacts(
                vpc_id=vpc_id,
                describable=False,
                describe_error=str(exc),
            )

        dns_support = self._vpc_attribute(vpc_id, "enableDnsSupport")
        dns_hostnames = self._vpc_attribute(vpc_id, "enableDnsHostnames")
        subnets = self._gather_subnets(intent, vpc_id)
        gateway_services, interface_services = self._gather_endpoints(vpc_id)
        security_groups = self._gather_security_groups(intent)
        has_igw = self._has_internet_gateway(vpc_id)

        return VpcFacts(
            vpc_id=vpc_id,
            describable=True,
            dns_support=dns_support,
            dns_hostnames=dns_hostnames,
            subnets=tuple(subnets),
            has_internet_gateway=has_igw,
            gateway_endpoint_services=frozenset(gateway_services),
            interface_endpoint_services=frozenset(interface_services),
            security_groups=security_groups,
        )

    # -- helpers (thin describe wrappers; no logic worth unit-testing here) --

    def _vpc_attribute(self, vpc_id: str, attribute: str) -> bool:
        resp = self._ec2.describe_vpc_attribute(VpcId=vpc_id, Attribute=attribute)
        key = "EnableDnsSupport" if attribute == "enableDnsSupport" else "EnableDnsHostnames"
        return bool(resp.get(key, {}).get("Value", False))

    def _gather_subnets(self, intent: Mapping[str, Any], vpc_id: str) -> list[SubnetFact]:
        resp = self._ec2.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        )
        facts: list[SubnetFact] = []
        for sn in resp.get("Subnets", []):
            subnet_id = sn.get("SubnetId", "")
            facts.append(
                SubnetFact(
                    subnet_id=subnet_id,
                    availability_zone=sn.get("AvailabilityZone", ""),
                    is_public=self._subnet_is_public(vpc_id, subnet_id),
                )
            )
        return facts

    def _subnet_is_public(self, vpc_id: str, subnet_id: str) -> bool:
        # A subnet is public when its route table has a default route to an IGW
        # (ported from 0cr-ins.sh 3265-3362). Explicit association first, then
        # the VPC main route table as the implicit association.
        resp = self._ec2.describe_route_tables(
            Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
        )
        tables = resp.get("RouteTables", [])
        if not tables:
            resp = self._ec2.describe_route_tables(
                Filters=[
                    {"Name": "vpc-id", "Values": [vpc_id]},
                    {"Name": "association.main", "Values": ["true"]},
                ]
            )
            tables = resp.get("RouteTables", [])
        for table in tables:
            for route in table.get("Routes", []):
                gw = route.get("GatewayId", "") or ""
                if gw.startswith("igw-"):
                    return True
        return False

    def _gather_endpoints(self, vpc_id: str) -> tuple[set[str], set[str]]:
        resp = self._ec2.describe_vpc_endpoints(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        )
        gateway: set[str] = set()
        interface: set[str] = set()
        for ep in resp.get("VpcEndpoints", []):
            service = ep.get("ServiceName", "")
            short = service.rsplit(".", 1)[-1] if service else ""
            if ep.get("VpcEndpointType") == "Gateway":
                gateway.add(short)
            elif ep.get("VpcEndpointType") == "Interface":
                interface.add(short)
        return gateway, interface

    def _gather_security_groups(
        self, intent: Mapping[str, Any]
    ) -> dict[str, tuple[SgIngressRule, ...]]:
        sg_ids = list(intent.get("vpc_security_group_ids") or [])
        if not sg_ids:
            return {}
        resp = self._ec2.describe_security_groups(GroupIds=sg_ids)
        out: dict[str, tuple[SgIngressRule, ...]] = {}
        for sg in resp.get("SecurityGroups", []):
            rules: list[SgIngressRule] = []
            for perm in sg.get("IpPermissions", []):
                rules.append(
                    SgIngressRule(
                        protocol=str(perm.get("IpProtocol", "")),
                        from_port=perm.get("FromPort"),
                        to_port=perm.get("ToPort"),
                        cidrs=tuple(
                            r.get("CidrIp", "") for r in perm.get("IpRanges", [])
                        ),
                        source_security_group_ids=tuple(
                            g.get("GroupId", "")
                            for g in perm.get("UserIdGroupPairs", [])
                        ),
                    )
                )
            out[sg.get("GroupId", "")] = tuple(rules)
        return out

    def _has_internet_gateway(self, vpc_id: str) -> bool:
        resp = self._ec2.describe_internet_gateways(
            Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
        )
        return len(resp.get("InternetGateways", [])) > 0
