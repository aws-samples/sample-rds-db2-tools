"""Baseline "Deploy RDS for Db2 instance" pipeline driver (task 15.1).

Drives the skill's full *local* pipeline for the baseline sandbox prompt
"Deploy RDS for Db2 instance" and asserts the resolved intent matches the R3.4
baseline field set. This is the AWS-mutation-free core shared by both the
always-on pytest (:mod:`scripts.tests.test_eval_baseline`) and the burner-account
live driver (:mod:`scripts.eval.live_baseline`).

Pipeline stages, in order (mirroring the design's resolver pipeline):

1. ``Tier_Resolver`` — :func:`resolve_tier` with no named tier / Environment tag
   resolves the sandbox baseline (R3.3, R3.4).
2. ``Sizing_Resolver`` — :func:`apply_default_sizing_to_intent` applies the R3.4
   baseline sizing (db.t3.xlarge / gp3 / 40 GiB, no iops/throughput) (R17.7).
3. ``Edition_Resolver`` — :func:`apply_edition_to_intent` defaults engine to
   db2-se (R8.3).
4. ``engine_version`` — :func:`apply_engine_version_to_intent` resolves the
   highest 12.1 minor from the LIVE RDS API (R5.1) — never fabricated.
5. identifier — :func:`apply_db_instance_identifier_to_intent` builds the
   self-describing default (R20).
6. environment-specific inputs — the R3.4 "existing-or-new" fields the tier
   does NOT bake in (subnet group, security group, MRK CMK, monitoring role,
   IBM IDs, master-user secret CMK) are merged from the caller-supplied
   ``environment`` so the intent is schema-complete and renderable.

The result is then validated through the full two-layer
:func:`validate_intent` and rendered through :func:`render_terraform`. The
``terraform validate`` gate (R10.8) is driven by the live/test layer that has a
``terraform`` binary; this module only produces the rendered files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

try:  # package-qualified first so identities match the test imports
    from scripts.resolve_intent import (
        ResolvedIntent,
        apply_db_instance_identifier_to_intent,
        apply_default_sizing_to_intent,
        apply_edition_to_intent,
        apply_engine_version_to_intent,
        resolve_tier,
    )
    from scripts.engine_versions import (
        EngineVersionLister,
        boto3_engine_version_lister,
    )
    from scripts.validate_intent import ValidationResult, validate_intent
    from scripts.render_terraform import RenderResult, render_terraform
except ImportError:  # pragma: no cover - bare import fallback
    from resolve_intent import (  # type: ignore
        ResolvedIntent,
        apply_db_instance_identifier_to_intent,
        apply_default_sizing_to_intent,
        apply_edition_to_intent,
        apply_engine_version_to_intent,
        resolve_tier,
    )
    from engine_versions import (  # type: ignore
        EngineVersionLister,
        boto3_engine_version_lister,
    )
    from validate_intent import ValidationResult, validate_intent  # type: ignore
    from render_terraform import RenderResult, render_terraform  # type: ignore


# ---------------------------------------------------------------------------
# Constants for the baseline scenario
# ---------------------------------------------------------------------------

#: The baseline natural-language prompt this driver evaluates (R3.4).
BASELINE_PROMPT = "Deploy RDS for Db2 instance"

#: The baseline resolves to the sandbox tier with no further detail (R3.3/3.4).
BASELINE_TIER = "sandbox"

#: The default region the eval targets (overridable). The burner account
#: credentials are valid for us-east-1.
DEFAULT_REGION = "us-east-1"

#: The expected R3.4 baseline field set, asserted by both the pytest and the
#: live driver. Values that R3.4 fixes exactly; engine/version are checked
#: structurally (engine default db2-se, engine_version major 12.1) since the
#: minor is resolved live and must not be hardcoded (R5.1).
EXPECTED_BASELINE_FIELDS: dict[str, Any] = {
    "deployment_tier": "sandbox",
    "engine": "db2-se",
    "instance_class": "db.t3.xlarge",
    "allocated_storage": 40,
    "storage_type": "gp3",
    "multi_az": False,
    "backup_retention_period": 1,
    "db_name": "DB2DB",
    "port": 8392,
    "publicly_accessible": False,
    "storage_encrypted": True,
    "monitoring_interval": 15,
    "license_model": "bring-your-own-license",
    "deletion_protection": False,
}

#: The major version R3.4 pins for the baseline (the live API supplies the
#: minor). engine_version must START with this major (R3.4, R5.6).
EXPECTED_ENGINE_MAJOR = "12.1"

#: db2diag.log publishing to CloudWatch must be enabled (R3.4). The intent field
#: carries the RDS log-type key ``diag.log``.
EXPECTED_CW_LOG_EXPORT = "diag.log"

#: SSL service port the baseline fixes (R3.4); rendered into the parameter group
#: as a security invariant, not an intent field, so the live driver asserts it
#: on the rendered parameter group rather than the intent.
SSL_SERVICE_PORT = 50443


@dataclass
class BaselineEnvironment:
    """The environment-specific "existing-or-new" inputs the R3.4 baseline does
    NOT bake into the tier (subnet group, SG, MRK CMK, monitoring role, IBM IDs).

    These are resolved against the target account by the Intent_Collector /
    composer in production; for the eval they are supplied explicitly so the
    resolved baseline intent is schema-complete and renderable. The IBM IDs are
    test placeholders (R7: customer-supplied; the eval uses non-secret stand-ins
    and masks them in artifacts).
    """

    region: str = DEFAULT_REGION
    kms_key_id: str = ""
    master_user_secret_kms_key_id: str = ""
    vpc_security_group_ids: list[str] = field(default_factory=list)
    db_subnet_group_name: str = ""
    monitoring_role_arn: str = ""
    master_username: str = "admin"
    ibm_customer_id: str = ""
    ibm_site_id: str = ""
    #: Project/Owner tags (Environment is forced to the tier by the resolver).
    project_tag: str = "rds-db2-skill-eval"
    owner_tag: str = "rds-db2-skill-eval"

    def as_intent_fields(self) -> dict[str, Any]:
        """The environment inputs as intent fields to merge after resolution."""
        return {
            "region": self.region,
            "kms_key_id": self.kms_key_id,
            "master_user_secret_kms_key_id": self.master_user_secret_kms_key_id,
            "manage_master_user_password": True,
            "vpc_security_group_ids": list(self.vpc_security_group_ids),
            "db_subnet_group_name": self.db_subnet_group_name,
            "db_parameter_group_name": "",
            "monitoring_role_arn": self.monitoring_role_arn,
            "master_username": self.master_username,
            "ibm_customer_id": self.ibm_customer_id,
            "ibm_site_id": self.ibm_site_id,
        }


@dataclass
class BaselineResult:
    """The output of :func:`resolve_baseline_intent`.

    Attributes:
        intent: the fully resolved, schema-complete baseline ``Deployment_Intent``.
        validation: the two-layer :class:`ValidationResult` for the intent.
        resolved: the underlying :class:`ResolvedIntent` (provenance, tier).
    """

    intent: dict[str, Any]
    validation: ValidationResult
    resolved: ResolvedIntent


def resolve_baseline_intent(
    environment: BaselineEnvironment,
    *,
    lister: Optional[EngineVersionLister] = None,
) -> BaselineResult:
    """Resolve the baseline "Deploy RDS for Db2 instance" (sandbox) intent.

    Runs the full resolver pipeline for the terse baseline prompt (no named
    tier, no Environment tag -> sandbox per R3.3), applies the R3.4 baseline
    sizing/edition, resolves the engine version from the LIVE API (R5.1) via
    ``lister`` (defaults to the boto3-backed lister; pass an in-memory stub in
    unit tests to stay AWS-free), builds the self-describing identifier (R20),
    and merges the environment-specific inputs so the intent is schema-complete.

    The merged intent is then run through the full two-layer
    :func:`validate_intent`; the returned :class:`BaselineResult` carries the
    intent and its validation so the caller decides how to surface failures.

    Args:
        environment: the environment-specific "existing-or-new" inputs (R3.4).
        lister: the engine-version lister (live boto3 by default). The live
            lister makes a read-only ``describe-db-engine-versions`` call; it is
            NOT a mutation.

    Returns:
        A :class:`BaselineResult`.
    """
    region = environment.region
    version_lister = lister if lister is not None else boto3_engine_version_lister()

    # 1) Tier_Resolver: terse baseline -> sandbox (R3.3/3.4). Carry the
    #    Project/Owner tags so the mandatory tag set is complete (Environment is
    #    forced to the tier by the resolver, R3.7).
    resolved = resolve_tier(
        tags={"Project": environment.project_tag, "Owner": environment.owner_tag},
    )

    # 2) Sizing_Resolver: no size named -> R3.4 baseline sizing (R17.7).
    apply_default_sizing_to_intent(resolved)

    # 3) Edition_Resolver: no edition named -> db2-se (R8.3).
    apply_edition_to_intent(resolved)

    # 4) engine_version: highest 12.1 minor from the LIVE API (R5.1, R5.6).
    apply_engine_version_to_intent(resolved, region=region, lister=version_lister)

    # 5) environment-specific inputs the tier does not bake in (R3.4
    #    "existing-or-new"). Merge before the identifier so region is present.
    for key, value in environment.as_intent_fields().items():
        resolved.intent[key] = value
        resolved.provenance[key] = "user_provided"
    resolved.intent["_provenance"] = resolved.provenance

    # 6) self-describing identifier (R20). Baseline workload size is the
    #    R3.4-equivalent "small" label; tag is the Project tag.
    apply_db_instance_identifier_to_intent(
        resolved,
        workload_size="small",
        tag=environment.project_tag,
    )

    # The baseline is gp3 < 400 GiB so it carries no workload_size from sizing;
    # the schema requires workload_size, so record the baseline-equivalent size.
    resolved.intent.setdefault("workload_size", "xsmall")
    resolved.provenance.setdefault("workload_size", "assumed")
    resolved.intent["_provenance"] = resolved.provenance

    validation = validate_intent(resolved.intent)

    return BaselineResult(
        intent=resolved.intent,
        validation=validation,
        resolved=resolved,
    )


def check_baseline_fields(intent: Mapping[str, Any]) -> list[str]:
    """Return a list of human-readable mismatches between ``intent`` and the
    R3.4 baseline field set (R3.4). Empty list == every expected field matches.

    Checks the exact-valued fields in :data:`EXPECTED_BASELINE_FIELDS`, the
    engine-version major (``12.1``, minor resolved live so not pinned), and that
    db2diag.log publishing is enabled.
    """
    mismatches: list[str] = []

    for field_name, expected in EXPECTED_BASELINE_FIELDS.items():
        actual = intent.get(field_name)
        if actual != expected:
            mismatches.append(
                f"{field_name}: expected {expected!r}, got {actual!r}"
            )

    engine_version = str(intent.get("engine_version", ""))
    if not engine_version.startswith(EXPECTED_ENGINE_MAJOR + "."):
        mismatches.append(
            f"engine_version: expected major {EXPECTED_ENGINE_MAJOR}.x, "
            f"got {engine_version!r}"
        )

    cw_exports = intent.get("enable_cloudwatch_logs_exports") or []
    if EXPECTED_CW_LOG_EXPORT not in cw_exports:
        mismatches.append(
            f"enable_cloudwatch_logs_exports: expected to include "
            f"{EXPECTED_CW_LOG_EXPORT!r}, got {cw_exports!r}"
        )

    # monitoring enhanced (interval > 0) requires a monitoring role (R18.4) and
    # R3.4 enhanced monitoring enabled.
    if not intent.get("monitoring_interval", 0) > 0:
        mismatches.append(
            "monitoring_interval: expected > 0 (enhanced monitoring enabled per "
            f"R3.4), got {intent.get('monitoring_interval')!r}"
        )

    return mismatches


def render_baseline(
    intent: Mapping[str, Any],
    *,
    modules_root: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> RenderResult:
    """Render the baseline intent's Terraform via the composer (R10).

    Thin wrapper over :func:`render_terraform` so the live driver and the pytest
    share one render entry point.
    """
    return render_terraform(intent, modules_root=modules_root, output_dir=output_dir)
