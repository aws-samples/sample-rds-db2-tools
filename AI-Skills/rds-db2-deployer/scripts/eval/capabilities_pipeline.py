"""Optional-capability + edition-reconciliation pipeline driver (task 15.2).

This is the AWS-mutation-free core shared by the always-on pytest
(:mod:`scripts.tests.test_eval_capabilities`) and the burner-account live driver
(:mod:`scripts.eval.live_capabilities`). It drives the skill's full *local*
pipeline (resolve -> validate -> render) for each task-15.2 scenario and exposes
the resolved-intent evidence the task asks us to assert:

GROUP A — edition reconciliation (R8.5 / R8.6), verifiable WITHOUT a slow RDS
apply because they are about the resolver's edition decision, not AWS runtime:

* ``se_to_ae_oversized`` (R8.5): engine db2-se requested on an oversized class
  (``db.x2iedn.16xlarge``) -> forced SE->AE conversion, ``_edition_conversion``
  recorded (from db2-se, to db2-ae, acknowledgement_required=true), and the
  derived parameter-group family becomes ``db2-ae-12.1``.
* ``ae_then_se_downgrade`` (R8.6): db2-ae explicitly on an SE-eligible class
  (``db.r7i.2xlarge``) is honored as db2-ae with at most *advisory* downgrade
  guidance (never auto-changed); a customer-initiated db2-se on the same class
  is honored as db2-se with NO conversion.

GROUP B — optional capabilities (R13) that change the RENDERED Terraform. Each
scenario resolves a full, schema-valid intent, validates it (must pass), and
renders it; the live driver then runs ``terraform validate`` per enabled module
(R10.8). The capabilities covered: prod Multi-AZ (R13.1-posture), self-managed
AD (R13.4), audit-to-S3 (R13.5), BYOK MRK (R13.6), cross-region standby replica
(R13.2), same-region read replica (R13.15).

The engine-version resolution uses an injected lister (live boto3 in the live
driver, a RECORDED grounded snapshot in the pytest) so a real ``12.1.x`` minor
is always used and never fabricated (R5.1).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

try:  # package-qualified first so identities match the test imports
    from scripts.resolve_intent import (
        ResolvedIntent,
        apply_db_instance_identifier_to_intent,
        apply_default_sizing_to_intent,
        apply_edition_to_intent,
        apply_engine_version_to_intent,
        apply_sizing_to_intent,
        resolve_tier,
    )
    from scripts.engine_versions import EngineVersionLister, boto3_engine_version_lister
    from scripts.validate_intent import ValidationResult, validate_intent
    from scripts.render_terraform import RenderResult, render_terraform
    from scripts.eval.baseline_pipeline import BaselineEnvironment
except ImportError:  # pragma: no cover - bare import fallback
    from resolve_intent import (  # type: ignore
        ResolvedIntent,
        apply_db_instance_identifier_to_intent,
        apply_default_sizing_to_intent,
        apply_edition_to_intent,
        apply_engine_version_to_intent,
        apply_sizing_to_intent,
        resolve_tier,
    )
    from engine_versions import EngineVersionLister, boto3_engine_version_lister  # type: ignore
    from validate_intent import ValidationResult, validate_intent  # type: ignore
    from render_terraform import RenderResult, render_terraform  # type: ignore
    from eval.baseline_pipeline import BaselineEnvironment  # type: ignore


DEFAULT_REGION = "us-east-1"

#: Well-formed PLACEHOLDER IBM customer/site IDs (the same values the baseline
#: pipeline uses). These are NON-REAL but correctly shaped so RDS accepts the
#: ``rds.ibm_customer_id`` / ``rds.ibm_site_id`` parameters and a deployment
#: succeeds; the customer is responsible for supplying their real Passport
#: Advantage IDs (the skill trusts the values and does not validate them). For a
#: live burner run that needs RDS-valid IDs, supply real values at runtime via
#: the ``RDS_DB2_EVAL_IBM_CUSTOMER_ID`` / ``RDS_DB2_EVAL_IBM_SITE_ID`` env vars;
#: nothing real is committed to source. Treated as Sensitive_Values and masked
#: in artifacts (R7.6/R15.6).
EVAL_IBM_CUSTOMER_ID = os.environ.get("RDS_DB2_EVAL_IBM_CUSTOMER_ID", "1234567")
EVAL_IBM_SITE_ID = os.environ.get("RDS_DB2_EVAL_IBM_SITE_ID", "1234567890")

#: The oversized class that exceeds the SE ceiling (64 vCPU / 1024 GiB), forcing
#: the SE->AE conversion (R8.5). It is the ``xlarge`` Workload_Sizing_Map row.
OVERSIZED_CLASS = "db.x2iedn.16xlarge"

#: An SE-eligible class (8 vCPU / 64 GiB, within the <=32 vCPU / <=128 GiB SE
#: ceiling) used for the AE->SE downgrade scenarios (R8.6). The ``medium`` row.
SE_ELIGIBLE_CLASS = "db.r7i.2xlarge"


# ---------------------------------------------------------------------------
# Scenario result
# ---------------------------------------------------------------------------


@dataclass
class ScenarioResult:
    """The resolve -> validate -> render outcome for one task-15.2 scenario.

    Attributes:
        name: the scenario key.
        group: ``"A"`` (edition reconciliation) or ``"B"`` (optional capability).
        intent: the fully resolved Deployment_Intent.
        validation: the two-layer Intent_Validator result (must be ok).
        render: the composer RenderResult (``None`` only if resolve/validate
            halted first).
        edition_conversion: the ``_edition_conversion`` record, if any (R8.5).
        downgrade_guidance: the ``_edition_downgrade_guidance`` string, if any
            (R8.6 advisory).
        proof: how the scenario is proven on the burner — ``"real-apply"`` for
            the fast param-group / bucket / key applies, or ``"validate-only"``
            when a full Db2 instance apply would be prohibitively slow.
        notes: human-readable evidence lines.
    """

    name: str
    group: str
    intent: dict[str, Any]
    validation: ValidationResult
    render: Optional[RenderResult] = None
    edition_conversion: Optional[dict[str, Any]] = None
    downgrade_guidance: Optional[str] = None
    proof: str = "validate-only"
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Environment + base-intent assembly
# ---------------------------------------------------------------------------


def capability_environment(
    region: str = DEFAULT_REGION,
    *,
    kms_key_id: str = "",
) -> BaselineEnvironment:
    """The environment-specific "existing-or-new" inputs for a capability intent.

    Mirrors the baseline eval environment: MRK CMK ids carry the ``mrk-`` prefix
    so the storage-encryption Security_Invariant (R6.11) accepts them, the IBM
    IDs are the grounded burner-tested values (masked in artifacts), and a
    ``kms_key_id`` may be supplied to drive the BYOK-reuse scenario (R13.6).
    """
    acct = "384621379288"
    storage_key = kms_key_id or (
        f"arn:aws:kms:{region}:{acct}:key/mrk-0000capability0000"
    )
    return BaselineEnvironment(
        region=region,
        kms_key_id=storage_key,
        master_user_secret_kms_key_id=(
            f"arn:aws:kms:{region}:{acct}:key/mrk-0000secret00000"
        ),
        vpc_id="vpc-0123456789abcdef0",
        vpc_security_group_ids=["sg-0123456789abcdef0"],
        db_subnet_group_name="rds-db2-skill-eval-subnets",
        monitoring_role_arn=f"arn:aws:iam::{acct}:role/rds-db2-monitoring-role-eval",
        ibm_customer_id=EVAL_IBM_CUSTOMER_ID,
        ibm_site_id=EVAL_IBM_SITE_ID,
        project_tag="eval-capabilities-15-2",
        owner_tag="eval-capabilities-15-2",
    )


def build_capability_intent(
    *,
    named_tier: Optional[str] = None,
    workload_size: Optional[str] = None,
    instance_class_override: Optional[str] = None,
    requested_edition: Optional[str] = None,
    region: str = DEFAULT_REGION,
    lister: Optional[EngineVersionLister] = None,
    environment: Optional[BaselineEnvironment] = None,
    extra_fields: Optional[Mapping[str, Any]] = None,
) -> ResolvedIntent:
    """Run the full resolver pipeline and return a schema-complete intent.

    Pipeline order mirrors :func:`scripts.eval.baseline_pipeline.resolve_baseline_intent`:
    tier -> sizing -> edition (reconciliation) -> engine version -> environment
    inputs -> identifier, then any ``extra_fields`` (the optional-capability
    flags) are merged as ``user_provided``.

    Args:
        named_tier: the tier to resolve (``None`` -> sandbox baseline).
        workload_size: a Workload_Sizing_Map size to apply; ``None`` -> the R3.4
            baseline sizing.
        instance_class_override: force a specific instance class (used to drive
            the SE-ceiling reconciliation scenarios).
        requested_edition: an explicitly requested engine edition (``None`` ->
            db2-se default).
        region: target region.
        lister: engine-version lister (live boto3 by default).
        environment: environment inputs (defaults to :func:`capability_environment`).
        extra_fields: optional-capability intent fields merged after resolution.

    Returns:
        The resolved :class:`ResolvedIntent` (its ``intent`` carries the
        ``_edition_conversion`` / ``_edition_downgrade_guidance`` metadata).
    """
    env = environment if environment is not None else capability_environment(region)
    version_lister = lister if lister is not None else boto3_engine_version_lister()

    resolved = resolve_tier(
        named_tier=named_tier,
        tags={"Project": env.project_tag, "Owner": env.owner_tag},
    )

    # Sizing: a named workload_size routes through the map; otherwise the R3.4
    # baseline sizing. An instance-class override is layered on top.
    overrides = (
        {"instance_class": instance_class_override}
        if instance_class_override
        else None
    )
    if workload_size:
        apply_sizing_to_intent(resolved, workload_size=workload_size, overrides=overrides)
    else:
        apply_default_sizing_to_intent(resolved, overrides=overrides)

    # Edition reconciliation against the (possibly oversized) instance class.
    apply_edition_to_intent(resolved, requested_edition=requested_edition)

    # Engine version + parameter-group family from the live/recorded lister.
    apply_engine_version_to_intent(resolved, region=region, lister=version_lister)

    # Environment-specific "existing-or-new" inputs (R3.4) so the intent is
    # schema-complete and renderable.
    for key, value in env.as_intent_fields().items():
        resolved.intent[key] = value
        resolved.provenance[key] = "user_provided"
    resolved.intent["_provenance"] = resolved.provenance

    # The schema requires workload_size; record one when sizing did not (the
    # baseline-sizing path leaves it unset, matching the baseline pipeline).
    resolved.intent.setdefault("workload_size", workload_size or "xsmall")
    resolved.provenance.setdefault("workload_size", "assumed")

    # Self-describing identifier (R20).
    apply_db_instance_identifier_to_intent(
        resolved,
        workload_size=resolved.intent.get("workload_size", "small"),
        tag=env.project_tag,
    )

    # Merge the optional-capability fields last (user_provided).
    for key, value in (extra_fields or {}).items():
        resolved.intent[key] = value
        resolved.provenance[key] = "user_provided"
    resolved.intent["_provenance"] = resolved.provenance

    return resolved


# ---------------------------------------------------------------------------
# GROUP A — edition reconciliation scenarios (R8.5 / R8.6)
# ---------------------------------------------------------------------------


def scenario_se_to_ae_oversized(
    *, region: str = DEFAULT_REGION, lister: Optional[EngineVersionLister] = None
) -> ScenarioResult:
    """R8.5: db2-se on ``db.x2iedn.16xlarge`` is force-converted to db2-ae.

    Asserts (via the returned result's fields) the recorded ``_edition_conversion``
    (from db2-se, to db2-ae, acknowledgement_required=true) and that the derived
    parameter-group family is ``db2-ae-12.1``.
    """
    resolved = build_capability_intent(
        named_tier="prod",
        workload_size="xlarge",  # db.x2iedn.16xlarge / io2 — the oversized row
        requested_edition="db2-se",
        region=region,
        lister=lister,
    )
    intent = resolved.intent
    validation = validate_intent(intent)
    render = render_terraform(intent) if validation.ok else None

    conv = intent.get("_edition_conversion")
    notes = [
        f"requested engine=db2-se on {intent.get('instance_class')} "
        f"(oversized: 64 vCPU / 1024 GiB > SE ceiling 32 vCPU / 128 GiB)",
        f"resolved engine={intent.get('engine')}",
        f"_edition_conversion={conv}",
        f"db_parameter_group_family={intent.get('db_parameter_group_family')}",
    ]
    return ScenarioResult(
        name="se_to_ae_oversized",
        group="A",
        intent=intent,
        validation=validation,
        render=render,
        edition_conversion=conv,
        proof="real-apply",  # the db2-ae-12.1 param group applies on the burner
        notes=notes,
    )


def scenario_ae_on_se_eligible(
    *, region: str = DEFAULT_REGION, lister: Optional[EngineVersionLister] = None
) -> ScenarioResult:
    """R8.6: db2-ae kept on the SE-eligible ``db.r7i.2xlarge`` stays db2-ae with
    at most advisory downgrade guidance (never auto-changed)."""
    resolved = build_capability_intent(
        named_tier="prod",
        workload_size="medium",  # db.r7i.2xlarge / gp3 — SE-eligible
        requested_edition="db2-ae",
        region=region,
        lister=lister,
    )
    intent = resolved.intent
    validation = validate_intent(intent)
    render = render_terraform(intent) if validation.ok else None
    notes = [
        f"requested engine=db2-ae on {intent.get('instance_class')} "
        "(SE-eligible: 8 vCPU / 64 GiB)",
        f"resolved engine={intent.get('engine')} (must stay db2-ae)",
        f"_edition_conversion={intent.get('_edition_conversion')} (must be None)",
        f"downgrade_guidance present={bool(intent.get('_edition_downgrade_guidance'))}",
    ]
    return ScenarioResult(
        name="ae_on_se_eligible",
        group="A",
        intent=intent,
        validation=validation,
        render=render,
        edition_conversion=intent.get("_edition_conversion"),
        downgrade_guidance=intent.get("_edition_downgrade_guidance"),
        proof="real-apply",  # the db2-ae-12.1 param group applies on the burner
        notes=notes,
    )


def scenario_ae_to_se_downgrade(
    *, region: str = DEFAULT_REGION, lister: Optional[EngineVersionLister] = None
) -> ScenarioResult:
    """R8.6: a customer-initiated db2-se on the SE-eligible ``db.r7i.2xlarge``
    (the AE->SE downgrade after rightsizing) is honored as db2-se with NO
    auto-conversion."""
    resolved = build_capability_intent(
        named_tier="prod",
        workload_size="medium",  # db.r7i.2xlarge / gp3 — SE-eligible
        requested_edition="db2-se",
        region=region,
        lister=lister,
    )
    intent = resolved.intent
    validation = validate_intent(intent)
    render = render_terraform(intent) if validation.ok else None
    notes = [
        f"customer-initiated engine=db2-se on {intent.get('instance_class')} "
        "(SE-eligible) — the AE->SE downgrade",
        f"resolved engine={intent.get('engine')} (must stay db2-se)",
        f"_edition_conversion={intent.get('_edition_conversion')} (must be None)",
        f"db_parameter_group_family={intent.get('db_parameter_group_family')}",
    ]
    return ScenarioResult(
        name="ae_to_se_downgrade",
        group="A",
        intent=intent,
        validation=validation,
        render=render,
        edition_conversion=intent.get("_edition_conversion"),
        proof="real-apply",  # the db2-se-12.1 param group applies on the burner
        notes=notes,
    )


# ---------------------------------------------------------------------------
# GROUP B — optional-capability scenarios (R13)
# ---------------------------------------------------------------------------


def _self_managed_ad_fields() -> dict[str, Any]:
    return {
        "domain_fqdn": "company.com",
        "domain_ou": "OU=RDSDb2,DC=company,DC=com",
        "domain_auth_secret_arn": (
            "arn:aws:secretsmanager:us-east-1:384621379288:secret:rds-db2-ad-join-abc"
        ),
        "domain_dns_ips": ["10.0.16.150", "10.0.28.150"],
        "domain_iam_role_name": "rds-db2-directory-service-access-role",
    }


def _audit_fields() -> dict[str, Any]:
    return {
        "enable_audit": True,
        "audit_role_arn": "arn:aws:iam::384621379288:role/rds-db2-audit",
        "audit_bucket_name": "eval-capabilities-15-2-audit",
    }


def _standby_fields() -> dict[str, Any]:
    return {
        "create_standby_replica": True,
        "standby_replica_region": "us-west-2",
        "standby_replica_identifier": "db2db-standby",
        "standby_parameter_group_name": "rds-db2-prod-pg-west",
        "standby_kms_key_arn": (
            "arn:aws:kms:us-west-2:384621379288:key/mrk-0000west00000"
        ),
    }


def _read_replica_fields() -> dict[str, Any]:
    return {
        "create_read_replica": True,
        "read_replica_identifier": "db2db-read",
        "read_replica_instance_class": "db.r7i.2xlarge",
    }


def _group_b_scenario(
    name: str,
    *,
    workload_size: str,
    extra_fields: Mapping[str, Any],
    region: str,
    lister: Optional[EngineVersionLister],
    byok_key: str = "",
    notes_builder=None,
) -> ScenarioResult:
    env = capability_environment(region, kms_key_id=byok_key)
    resolved = build_capability_intent(
        named_tier="prod",
        workload_size=workload_size,
        region=region,
        lister=lister,
        environment=env,
        extra_fields=extra_fields,
    )
    intent = resolved.intent
    validation = validate_intent(intent)
    render = render_terraform(intent) if validation.ok else None
    notes = notes_builder(intent, render) if notes_builder else []
    return ScenarioResult(
        name=name,
        group="B",
        intent=intent,
        validation=validation,
        render=render,
        proof="validate-only",
        notes=notes,
    )


def scenario_prod_multi_az(
    *, region: str = DEFAULT_REGION, lister: Optional[EngineVersionLister] = None
) -> ScenarioResult:
    """R13.1/prod posture: prod tier -> multi_az true, r-family class, >=7-day
    backup, deletion_protection true."""

    def notes(intent, render):
        rds = render.modules["5-rds"].variables if render else {}
        return [
            f"deployment_tier={intent.get('deployment_tier')}",
            f"multi_az={rds.get('multi_az')}",
            f"instance_class={intent.get('instance_class')} (r-family)",
            f"backup_retention_period={intent.get('backup_retention_period')}",
            f"deletion_protection={rds.get('deletion_protection')}",
        ]

    # medium == db.r7i.2xlarge (r-family) so the prod r-family posture holds.
    return _group_b_scenario(
        "prod_multi_az",
        workload_size="medium",
        extra_fields={"multi_az": True},
        region=region,
        lister=lister,
        notes_builder=notes,
    )


def scenario_self_managed_ad(
    *, region: str = DEFAULT_REGION, lister: Optional[EngineVersionLister] = None
) -> ScenarioResult:
    """R13.4: self-managed AD renders the 5 self-managed AD args on 5-rds and the
    directory-role wiring + join-secret grant on 2-iam."""

    def notes(intent, render):
        rds = render.modules["5-rds"].variables if render else {}
        iam = render.modules.get("2-iam").variables if render and "2-iam" in render.modules else {}
        return [
            f"domain_fqdn={rds.get('domain_fqdn')}",
            f"domain_ou={rds.get('domain_ou')}",
            f"domain_dns_ips={rds.get('domain_dns_ips')}",
            f"domain_auth_secret_arn set={bool(rds.get('domain_auth_secret_arn'))}",
            f"2-iam create_directory_role={iam.get('create_directory_role')}",
            f"2-iam self_managed_ad_secret_arn set={bool(iam.get('self_managed_ad_secret_arn'))}",
        ]

    return _group_b_scenario(
        "self_managed_ad",
        workload_size="medium",
        extra_fields=_self_managed_ad_fields(),
        region=region,
        lister=lister,
        notes_builder=notes,
    )


def scenario_audit_to_s3(
    *, region: str = DEFAULT_REGION, lister: Optional[EngineVersionLister] = None
) -> ScenarioResult:
    """R13.5: audit renders enable_audit + audit_role_arn + audit_bucket_name on
    5-rds and create_audit_role + bucket reference on 2-iam (DB2_AUDIT option)."""

    def notes(intent, render):
        rds = render.modules["5-rds"].variables if render else {}
        iam = render.modules.get("2-iam").variables if render and "2-iam" in render.modules else {}
        return [
            f"5-rds enable_audit={rds.get('enable_audit')}",
            f"5-rds audit_role_arn set={bool(rds.get('audit_role_arn'))}",
            f"5-rds audit_bucket_name={rds.get('audit_bucket_name')}",
            f"2-iam create_audit_role={iam.get('create_audit_role')}",
            f"2-iam audit_bucket_name={iam.get('audit_bucket_name')}",
        ]

    return _group_b_scenario(
        "audit_to_s3",
        workload_size="medium",
        extra_fields=_audit_fields(),
        region=region,
        lister=lister,
        notes_builder=notes,
    )


def scenario_byok_mrk(
    *,
    region: str = DEFAULT_REGION,
    lister: Optional[EngineVersionLister] = None,
    byok_key: str = "",
) -> ScenarioResult:
    """R13.6: a supplied MRK CMK is reused (3-kms skipped) and 5-rds kms_key_arn
    is the BYOK key. ``byok_key`` defaults to a placeholder MRK ARN; the live
    driver passes a real burner-created MRK key."""
    acct = "384621379288"
    key = byok_key or f"arn:aws:kms:{region}:{acct}:key/mrk-0000byok00000"

    def notes(intent, render):
        rds = render.modules["5-rds"].variables if render else {}
        return [
            f"supplied BYOK kms_key_id={intent.get('kms_key_id')}",
            f"5-rds kms_key_arn={rds.get('kms_key_arn')}",
            f"3-kms enabled={'3-kms' in (render.enabled_modules if render else [])} "
            "(must be False — reuse)",
        ]

    return _group_b_scenario(
        "byok_mrk",
        workload_size="medium",
        extra_fields={},
        region=region,
        lister=lister,
        byok_key=key,
        notes_builder=notes,
    )


def scenario_standby_replica(
    *, region: str = DEFAULT_REGION, lister: Optional[EngineVersionLister] = None
) -> ScenarioResult:
    """R13.2: cross-region mounted standby renders the standby args + the
    aws.replica-backed resource on 5-rds."""

    def notes(intent, render):
        rds = render.modules["5-rds"].variables if render else {}
        return [
            f"create_standby_replica={rds.get('create_standby_replica')}",
            f"standby_parameter_group_name={rds.get('standby_parameter_group_name')}",
            f"standby_kms_key_arn set={bool(rds.get('standby_kms_key_arn'))}",
            f"backup_retention_period={intent.get('backup_retention_period')} (>0 required)",
        ]

    return _group_b_scenario(
        "standby_replica",
        workload_size="medium",
        extra_fields=_standby_fields(),
        region=region,
        lister=lister,
        notes_builder=notes,
    )


def scenario_read_replica(
    *, region: str = DEFAULT_REGION, lister: Optional[EngineVersionLister] = None
) -> ScenarioResult:
    """R13.15: same-region read replica renders the read-replica resource on
    5-rds."""

    def notes(intent, render):
        rds = render.modules["5-rds"].variables if render else {}
        return [
            f"create_read_replica={rds.get('create_read_replica')}",
            f"read_replica_identifier={rds.get('read_replica_identifier')}",
            f"read_replica_instance_class={rds.get('read_replica_instance_class')}",
        ]

    return _group_b_scenario(
        "read_replica",
        workload_size="medium",
        extra_fields=_read_replica_fields(),
        region=region,
        lister=lister,
        notes_builder=notes,
    )


#: All Group A scenario builders (edition reconciliation).
GROUP_A_BUILDERS = (
    scenario_se_to_ae_oversized,
    scenario_ae_on_se_eligible,
    scenario_ae_to_se_downgrade,
)

#: All Group B scenario builders (optional capabilities).
GROUP_B_BUILDERS = (
    scenario_prod_multi_az,
    scenario_self_managed_ad,
    scenario_audit_to_s3,
    scenario_byok_mrk,
    scenario_standby_replica,
    scenario_read_replica,
)
