"""Burner-account live driver for the baseline scenario (task 15.1).

Adapts the sibling ``rds-db2`` manipulation-eval ``setup`` / ``deploy`` /
``validate`` / ``rollback`` discipline to the **composer** skill: it drives the
local pipeline (via :mod:`scripts.eval.baseline_pipeline`), stages the rendered
Terraform plus the environment prerequisites the R3.4 baseline calls
"existing-or-new" into a scratch dir, ``terraform apply`` them against a burner
account in dependency order, confirms the RDS for Db2 instance (and supporting
resources) created, and then ``terraform destroy`` + describe-call cleanup
verification.

Dependency order applied (each a standalone Terraform root with LOCAL state in
the scratch dir, mirroring the modules' intended sequence
1-networking -> 2-iam -> 3-kms -> 4-parameter-group -> 5-rds, with the
SG/subnet-group/MRK-key prerequisites the modules expect supplied by a small
eval-authored ``prereqs`` root):

    prereqs        -> MRK CMK (storage) + MRK CMK (managed secret) + enhanced
                      monitoring role + security group (50443 ingress only) +
                      DB subnet group spanning >= 2 AZs   (created_by tag)
    parameter-group-> the rendered 4-parameter-group (family + IBM IDs +
                      DB2COMM=SSL / ssl_svcename=50443)
    rds            -> the rendered 5-rds instance, wired to the prereq outputs

Guarantees (task 15.1):

* **Real apply.** ``deploy`` runs ``terraform init`` + ``apply`` for real
  against the burner. The prereq + parameter-group stacks apply quickly and
  prove the create-paths; the RDS instance is the slow, may-fail stage.
* **Mandatory cleanup.** :func:`rollback` ``terraform destroy``s every stack in
  reverse order and then issues describe calls to PROVE no leftover RDS
  instance / KMS key (pending deletion is acceptable) / subnet group / SG /
  parameter group remains. It runs even when ``deploy`` fails partway.
* **Tag everything** ``created_by=rds-db2-skill`` so any leftover is
  identifiable.
* **Credential-expiry aware.** Long applies may outlive ``ada --once`` creds;
  on an ``ExpiredToken``/``ExpiredTokenException`` the driver stops, attempts as
  much cleanup as it can, and reports what remains.

This module is intentionally NOT a pytest (it mutates a real account and takes
tens of minutes). The orchestrator runs it out-of-band; the always-on
assertions live in :mod:`scripts.tests.test_eval_baseline`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    from scripts.eval.baseline_pipeline import (
        BaselineEnvironment,
        check_baseline_fields,
        render_baseline,
        resolve_baseline_intent,
        SSL_SERVICE_PORT,
    )
    from scripts.engine_versions import boto3_engine_version_lister
    from scripts.artifacts import write_artifacts, STATUS_COMPLETED, STATUS_FAILED
except ImportError:  # pragma: no cover - bare import fallback
    from eval.baseline_pipeline import (  # type: ignore
        BaselineEnvironment,
        check_baseline_fields,
        render_baseline,
        resolve_baseline_intent,
        SSL_SERVICE_PORT,
    )
    from engine_versions import boto3_engine_version_lister  # type: ignore
    from artifacts import write_artifacts, STATUS_COMPLETED, STATUS_FAILED  # type: ignore


_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
try:
    from scripts.render_terraform import DEFAULT_MODULES_ROOT as MODULES_ROOT
except ImportError:  # pragma: no cover - bare import fallback
    from render_terraform import DEFAULT_MODULES_ROOT as MODULES_ROOT  # type: ignore

#: Identifies everything this eval creates so leftovers are attributable (R14).
CREATED_BY_TAG = "rds-db2-skill"
GENERATION_MODEL_TAG = "kiro-spec-eval-15.1"

#: A short, unique-ish deployment name for the eval run. Kept stable so a
#: re-run reuses/cleans the same named resources.
DEPLOYMENT_NAME = "eval-baseline-15-1"

DEFAULT_REGION = "us-east-1"

#: Markers in an AWS error that mean the ada --once credentials expired
#: mid-apply (operational note: stop, clean up what we can, report).
_EXPIRED_CRED_MARKERS = ("ExpiredToken", "ExpiredTokenException", "security token included in the request is expired")


# ---------------------------------------------------------------------------
# Terraform process helpers
# ---------------------------------------------------------------------------


def _tf_env() -> dict:
    import os

    env = dict(os.environ)
    cache = Path("/tmp/rds_db2_provision_tf_plugin_cache")
    cache.mkdir(parents=True, exist_ok=True)
    env["TF_PLUGIN_CACHE_DIR"] = str(cache)
    env["TF_IN_AUTOMATION"] = "1"
    env["TF_INPUT"] = "0"
    return env


def _terraform_bin() -> str:
    tf = shutil.which("terraform")
    if not tf:
        raise RuntimeError("terraform binary not on PATH; the live eval needs it")
    return tf


def _run_tf(args: list[str], cwd: Path, *, timeout: int = 3600) -> subprocess.CompletedProcess:
    """Run a terraform command in ``cwd`` capturing output."""
    cmd = [_terraform_bin()] + args
    return subprocess.run(
        cmd, cwd=cwd, env=_tf_env(), capture_output=True, text=True, timeout=timeout
    )


def _is_expired_creds(text: str) -> bool:
    return any(m in (text or "") for m in _EXPIRED_CRED_MARKERS)


# ---------------------------------------------------------------------------
# Eval prereq Terraform (environment "existing-or-new" inputs, R3.4)
# ---------------------------------------------------------------------------


def _prereqs_tf(
    *,
    region: str,
    vpc_id: str,
    subnet_ids: list[str],
    tag: str,
    existing_monitoring_role_name: str = "",
) -> str:
    """Author the eval ``prereqs`` root that creates the environment-specific
    inputs the R3.4 baseline calls "existing-or-new": two MRK CMKs (storage +
    managed secret), the enhanced-monitoring IAM role, a security group whose
    ONLY ingress is the Db2 SSL service port 50443 (R6.5), and a DB subnet group
    spanning the supplied subnets (>= 2 AZs, R11.1). Everything carries the
    ``created_by`` provenance tag (R14).

    When ``existing_monitoring_role_name`` is supplied the monitoring role is
    REUSED (data source) rather than created. This is important on burner
    accounts whose session policy denies ``iam:DeleteRole`` (a role created by a
    prior run cannot be destroyed): reusing it keeps the eval idempotent and
    leaves no new undeletable IAM role behind."""
    subnet_hcl = ", ".join(f'"{s}"' for s in subnet_ids)
    if existing_monitoring_role_name:
        monitoring_block = f"""
# Reuse an existing enhanced-monitoring role (the burner session policy denies
# iam:DeleteRole, so a role from a prior run is reused rather than recreated).
data "aws_iam_role" "monitoring" {{
  name = "{existing_monitoring_role_name}"
}}

locals {{ monitoring_role_arn = data.aws_iam_role.monitoring.arn }}
"""
    else:
        monitoring_block = f"""
# Enhanced-monitoring IAM role (R3.4 enhanced monitoring enabled).
resource "aws_iam_role" "monitoring" {{
  name = "rds-db2-skill-eval-monitoring-{tag}"
  assume_role_policy = jsonencode({{
    Version = "2012-10-17"
    Statement = [{{
      Effect    = "Allow"
      Principal = {{ Service = "monitoring.rds.amazonaws.com" }}
      Action    = "sts:AssumeRole"
    }}]
  }})
}}

resource "aws_iam_role_policy_attachment" "monitoring" {{
  role       = aws_iam_role.monitoring.name
  policy_arn = "arn:${{data.aws_partition.current.partition}}:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}}

locals {{ monitoring_role_arn = aws_iam_role.monitoring.arn }}
"""
    return f"""terraform {{
  required_version = ">= 1.0"
  required_providers {{
    aws = {{ source = "hashicorp/aws", version = "~> 5.0" }}
  }}
}}

provider "aws" {{
  region = "{region}"
  default_tags {{
    tags = {{
      created_by       = "{CREATED_BY_TAG}"
      generation_model = "{GENERATION_MODEL_TAG}"
      Project          = "{tag}"
      Environment      = "sandbox"
      Owner            = "{tag}"
      ManagedBy        = "Terraform"
    }}
  }}
}}

data "aws_caller_identity" "current" {{}}
data "aws_partition" "current" {{}}

# MRK CMK for RDS storage encryption (R6.1 — customer-managed multi-region key).
resource "aws_kms_key" "storage" {{
  description             = "rds-db2-skill eval storage CMK - {tag}"
  multi_region            = true
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy = jsonencode({{
    Version = "2012-10-17"
    Statement = [
      {{
        Sid       = "EnableIAMUserPermissions"
        Effect    = "Allow"
        Principal = {{ AWS = "arn:${{data.aws_partition.current.partition}}:iam::${{data.aws_caller_identity.current.account_id}}:root" }}
        Action    = "kms:*"
        Resource  = "*"
      }},
      {{
        Sid       = "AllowRDS"
        Effect    = "Allow"
        Principal = {{ Service = "rds.amazonaws.com" }}
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey", "kms:CreateGrant"]
        Resource  = "*"
      }}
    ]
  }})
}}

# MRK CMK for the RDS-managed master-user secret (R6.10 CMK-everywhere).
resource "aws_kms_key" "secret" {{
  description             = "rds-db2-skill eval managed-secret CMK - {tag}"
  multi_region            = true
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy = jsonencode({{
    Version = "2012-10-17"
    Statement = [
      {{
        Sid       = "EnableIAMUserPermissions"
        Effect    = "Allow"
        Principal = {{ AWS = "arn:${{data.aws_partition.current.partition}}:iam::${{data.aws_caller_identity.current.account_id}}:root" }}
        Action    = "kms:*"
        Resource  = "*"
      }},
      {{
        Sid       = "AllowSecretsManager"
        Effect    = "Allow"
        Principal = {{ Service = "secretsmanager.amazonaws.com" }}
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey", "kms:CreateGrant"]
        Resource  = "*"
      }}
    ]
  }})
}}
{monitoring_block}
# Security group: ONLY ingress is Db2 SSL service port 50443 (R6.5).
resource "aws_security_group" "db2" {{
  name        = "rds-db2-skill-eval-{tag}"
  description = "rds-db2-skill eval SG - SSL 50443 ingress only"
  vpc_id      = "{vpc_id}"

  ingress {{
    description = "Db2 SSL service port"
    from_port   = {SSL_SERVICE_PORT}
    to_port     = {SSL_SERVICE_PORT}
    protocol    = "tcp"
    cidr_blocks = ["{_vpc_cidr_placeholder}"]
  }}

  egress {{
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }}

  tags = {{ Name = "rds-db2-skill-eval-{tag}" }}
}}

# DB subnet group spanning >= 2 AZs (R11.1).
resource "aws_db_subnet_group" "this" {{
  name        = "rds-db2-skill-eval-{tag}"
  subnet_ids  = [{subnet_hcl}]
  description = "rds-db2-skill eval subnet group"
  tags        = {{ Name = "rds-db2-skill-eval-{tag}" }}
}}

output "storage_kms_key_arn" {{ value = aws_kms_key.storage.arn }}
output "secret_kms_key_arn" {{ value = aws_kms_key.secret.arn }}
output "monitoring_role_arn" {{ value = local.monitoring_role_arn }}
output "security_group_id" {{ value = aws_security_group.db2.id }}
output "db_subnet_group_name" {{ value = aws_db_subnet_group.this.name }}
"""


# A literal placeholder substituted at render time with the real VPC CIDR so the
# SG ingress is scoped to the VPC rather than the world (least privilege, R6.5).
_vpc_cidr_placeholder = "VPC_CIDR_PLACEHOLDER"


# ---------------------------------------------------------------------------
# Module staging helpers
# ---------------------------------------------------------------------------


def _stage_module(module_name: str, dest: Path) -> None:
    """Copy a real RDS-Db2-Terraform module's ``*.tf`` into ``dest`` (a scratch
    standalone root with local state), plus the aws.replica alias provider the
    5-rds module declares so it can apply standalone."""
    src = MODULES_ROOT / module_name
    dest.mkdir(parents=True, exist_ok=True)
    for tf in src.glob("*.tf"):
        shutil.copy(tf, dest / tf.name)
    if module_name == "5-rds":
        (dest / "zz_replica_provider.tf").write_text(
            'provider "aws" {\n  alias  = "replica"\n  region = "us-west-2"\n}\n'
        )


def _tfvars_text(variables: dict[str, Any]) -> str:
    """Render a simple terraform.tfvars from a flat variable map."""
    def fmt(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, (list, tuple)):
            return "[" + ", ".join(fmt(x) for x in v) + "]"
        return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'

    return "\n".join(f"{k} = {fmt(v)}" for k, v in variables.items()) + "\n"


# ---------------------------------------------------------------------------
# Live driver result
# ---------------------------------------------------------------------------


@dataclass
class StackOutputs:
    storage_kms_key_arn: str = ""
    secret_kms_key_arn: str = ""
    monitoring_role_arn: str = ""
    security_group_id: str = ""
    db_subnet_group_name: str = ""
    parameter_group_name: str = ""
    db_instance_identifier: str = ""


@dataclass
class LiveResult:
    applied: list[str] = field(default_factory=list)
    validated: dict[str, Any] = field(default_factory=dict)
    destroyed: list[str] = field(default_factory=list)
    leftovers: dict[str, Any] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    intent: dict[str, Any] = field(default_factory=dict)
    outputs: StackOutputs = field(default_factory=StackOutputs)


# ---------------------------------------------------------------------------
# AWS discovery (read-only)
# ---------------------------------------------------------------------------


def _boto3_session(region: str):
    import boto3

    return boto3.Session(region_name=region)


def discover_vpc(region: str) -> dict[str, Any]:
    """Discover a usable VPC, two subnets in distinct AZs, and the VPC CIDR for
    the SG ingress scope. Prefers the default VPC; falls back to the first VPC.

    Returns ``{vpc_id, cidr, subnet_ids}`` (subnet_ids spanning >= 2 AZs).
    Raises when no VPC or fewer than two AZs are available (a Precheck_Failure
    analogue, R11.1)."""
    ec2 = _boto3_session(region).client("ec2")
    vpcs = ec2.describe_vpcs().get("Vpcs", [])
    if not vpcs:
        raise RuntimeError(f"no VPC found in {region}; cannot run the live eval")
    default = next((v for v in vpcs if v.get("IsDefault")), None)
    vpc = default or vpcs[0]
    vpc_id = vpc["VpcId"]
    cidr = vpc["CidrBlock"]

    subnets = ec2.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    ).get("Subnets", [])
    by_az: dict[str, str] = {}
    for s in subnets:
        by_az.setdefault(s["AvailabilityZone"], s["SubnetId"])
    if len(by_az) < 2:
        raise RuntimeError(
            f"VPC {vpc_id} has subnets in only {len(by_az)} AZ(s); RDS DB subnet "
            "group needs >= 2 (R11.1)"
        )
    # Take one subnet from each of the first two AZs (deterministic order).
    chosen = [by_az[az] for az in sorted(by_az)[:2]]
    return {"vpc_id": vpc_id, "cidr": cidr, "subnet_ids": chosen}


# ---------------------------------------------------------------------------
# setup — clean any leftovers from a prior run
# ---------------------------------------------------------------------------


def _rds_client(region: str):
    return _boto3_session(region).client("rds")


def _find_existing_monitoring_role(region: str) -> str:
    """Return the name of a reusable eval enhanced-monitoring role if one exists
    (left by a prior run that could not delete it under the burner session
    policy), else "". The role must have the AmazonRDSEnhancedMonitoringRole
    policy attached so it is usable for enhanced monitoring."""
    from botocore.exceptions import ClientError

    iam = _boto3_session(region).client("iam")
    name = f"rds-db2-skill-eval-monitoring-{DEPLOYMENT_NAME}"
    try:
        iam.get_role(RoleName=name)
    except ClientError:
        return ""
    # Ensure the managed policy is attached (re-attach if a prior destroy
    # detached it); attaching is idempotent.
    try:
        policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
        attached = iam.list_attached_role_policies(RoleName=name).get("AttachedPolicies", [])
        if not any(p["PolicyArn"] == policy_arn for p in attached):
            iam.attach_role_policy(RoleName=name, PolicyArn=policy_arn)
    except ClientError:
        pass
    return name


def setup(region: str = DEFAULT_REGION) -> dict[str, Any]:
    """Best-effort cleanup of leftovers from a prior eval run, mirroring the
    sibling manipulation eval's ``setup`` (delete a leftover instance + parameter
    group). Non-fatal: a clean account simply finds nothing."""
    from botocore.exceptions import ClientError

    rds = _rds_client(region)
    removed: list[str] = []

    # The baseline identifier is deterministic; derive it the same way deploy
    # does so setup can target it.
    intent = _baseline_intent(region)
    ident = intent["db_instance_identifier"]
    try:
        rds.describe_db_instances(DBInstanceIdentifier=ident)
        try:
            rds.modify_db_instance(
                DBInstanceIdentifier=ident,
                DeletionProtection=False,
                ApplyImmediately=True,
            )
        except ClientError:
            pass
        rds.delete_db_instance(
            DBInstanceIdentifier=ident,
            SkipFinalSnapshot=True,
            DeleteAutomatedBackups=True,
        )
        removed.append(f"rds:{ident}")
    except ClientError as e:
        if "DBInstanceNotFound" not in str(e):
            pass

    return {"region": region, "removed": removed, "instance_identifier": ident}


# ---------------------------------------------------------------------------
# Intent + render
# ---------------------------------------------------------------------------


#: Well-formed PLACEHOLDER IBM customer/site IDs. These are NON-REAL but
#: correctly shaped so RDS accepts the ``rds.ibm_customer_id`` /
#: ``rds.ibm_site_id`` parameters and a deployment succeeds (a free-form string
#: like "EVAL-CUST-PLACEHOLDER" is rejected by RDS with ``InvalidParameterValue``,
#: and all-zero values are also rejected). The customer supplies their real
#: Passport Advantage IDs; the skill trusts the values and does not validate
#: them. For a live burner run that needs RDS-valid IDs, supply real values at
#: runtime via the ``RDS_DB2_EVAL_IBM_CUSTOMER_ID`` / ``RDS_DB2_EVAL_IBM_SITE_ID``
#: env vars; nothing real is committed to source. Treated as Sensitive_Values
#: and masked in artifacts (R7.6/R15.6).
EVAL_IBM_CUSTOMER_ID = os.environ.get("RDS_DB2_EVAL_IBM_CUSTOMER_ID", "1234567")
EVAL_IBM_SITE_ID = os.environ.get("RDS_DB2_EVAL_IBM_SITE_ID", "1234567890")


def _baseline_environment(region: str) -> BaselineEnvironment:
    """The eval environment with PLACEHOLDER MRK CMK ids (real ones are wired in
    after the prereq apply). IBM IDs are the grounded burner-tested values from
    the sibling eval (masked in artifacts), so RDS accepts the parameter group.
    """
    acct = "384621379288"
    return BaselineEnvironment(
        region=region,
        kms_key_id=f"arn:aws:kms:{region}:{acct}:key/mrk-00000000000000000000000000000000",
        master_user_secret_kms_key_id=f"arn:aws:kms:{region}:{acct}:key/mrk-00000000000000000000000000000000",
        vpc_security_group_ids=["sg-placeholder"],
        db_subnet_group_name="placeholder",
        monitoring_role_arn=f"arn:aws:iam::{acct}:role/placeholder",
        ibm_customer_id=EVAL_IBM_CUSTOMER_ID,
        ibm_site_id=EVAL_IBM_SITE_ID,
        project_tag=DEPLOYMENT_NAME,
        owner_tag=DEPLOYMENT_NAME,
    )


def _baseline_intent(region: str) -> dict[str, Any]:
    res = resolve_baseline_intent(
        _baseline_environment(region), lister=boto3_engine_version_lister()
    )
    return res.intent


# ---------------------------------------------------------------------------
# deploy — real apply against the burner
# ---------------------------------------------------------------------------


def deploy(
    work_dir: Path,
    *,
    region: str = DEFAULT_REGION,
    apply_rds: bool = True,
    rds_timeout: int = 5400,
) -> LiveResult:
    """Resolve + render the baseline, stage the prereqs/param-group/rds stacks
    into ``work_dir`` with local state, and ``terraform apply`` them in order.

    Args:
        work_dir: scratch directory for the staged stacks + local state.
        region: target region.
        apply_rds: when False, applies only the prereqs + parameter group (the
            fast create-paths) and skips the slow RDS instance — useful to prove
            the supporting resources without the tens-of-minutes Db2 create.
        rds_timeout: seconds to allow the RDS apply (Db2 creation is slow).

    Returns:
        A :class:`LiveResult` recording applied stacks, outputs, and blockers.
        On any apply failure it returns with the blocker recorded so the caller
        can run :func:`rollback` unconditionally.
    """
    result = LiveResult()
    env = _baseline_environment(region)
    res = resolve_baseline_intent(env, lister=boto3_engine_version_lister())
    result.intent = res.intent
    result.outputs.db_instance_identifier = res.intent["db_instance_identifier"]

    if not res.validation.ok:
        result.blockers.append("intent validation failed: " + res.validation.report())
        return result
    mism = check_baseline_fields(res.intent)
    if mism:
        result.blockers.append("R3.4 mismatch: " + "; ".join(mism))
        return result

    # --- discover VPC/subnets/CIDR -----------------------------------------
    try:
        vpc = discover_vpc(region)
    except Exception as e:  # pragma: no cover - environment dependent
        result.blockers.append(f"VPC discovery failed: {e}")
        return result

    # --- stage + apply prereqs ---------------------------------------------
    prereqs_dir = work_dir / "prereqs"
    prereqs_dir.mkdir(parents=True, exist_ok=True)
    # Reuse an existing eval monitoring role when one is present: the burner
    # session policy denies iam:DeleteRole, so a role left by a prior run is
    # reused rather than recreated (keeps the eval idempotent + leak-free).
    existing_role = _find_existing_monitoring_role(region)
    prereq_tf = _prereqs_tf(
        region=region,
        vpc_id=vpc["vpc_id"],
        subnet_ids=vpc["subnet_ids"],
        tag=DEPLOYMENT_NAME,
        existing_monitoring_role_name=existing_role,
    ).replace(_vpc_cidr_placeholder, vpc["cidr"])
    (prereqs_dir / "main.tf").write_text(prereq_tf)

    ok, blocker = _init_apply(prereqs_dir, "prereqs", result, timeout=1800)
    if not ok:
        result.blockers.append(blocker or "prereqs apply failed")
        return result
    result.applied.append("prereqs")

    outs = _tf_outputs(prereqs_dir)
    result.outputs.storage_kms_key_arn = outs.get("storage_kms_key_arn", {}).get("value", "")
    result.outputs.secret_kms_key_arn = outs.get("secret_kms_key_arn", {}).get("value", "")
    result.outputs.monitoring_role_arn = outs.get("monitoring_role_arn", {}).get("value", "")
    result.outputs.security_group_id = outs.get("security_group_id", {}).get("value", "")
    result.outputs.db_subnet_group_name = outs.get("db_subnet_group_name", {}).get("value", "")

    # --- stage + apply the rendered 4-parameter-group ----------------------
    pg_dir = work_dir / "4-parameter-group"
    _stage_module("4-parameter-group", pg_dir)
    pg_vars = {
        "aws_region": region,
        "tag": DEPLOYMENT_NAME,
        "environment": "sandbox",
        "owner": DEPLOYMENT_NAME,
        "engine_edition": "se",
        "engine_major_version": "12.1",
        "ibm_customer_id": env.ibm_customer_id,
        "ibm_site_id": env.ibm_site_id,
        "created_by": CREATED_BY_TAG,
        "generation_model": GENERATION_MODEL_TAG,
    }
    (pg_dir / "terraform.tfvars").write_text(_tfvars_text(pg_vars))

    ok, blocker = _init_apply(pg_dir, "4-parameter-group", result, timeout=1200)
    if not ok:
        result.blockers.append(blocker or "parameter-group apply failed")
        return result
    result.applied.append("4-parameter-group")
    pg_outs = _tf_outputs(pg_dir)
    result.outputs.parameter_group_name = pg_outs.get("parameter_group_name", {}).get("value", "")

    if not apply_rds:
        result.blockers.append("apply_rds=False: RDS instance stage skipped by request")
        return result

    # --- stage + apply the rendered 5-rds instance -------------------------
    rds_dir = work_dir / "5-rds"
    _stage_module("5-rds", rds_dir)
    intent = res.intent
    rds_vars = {
        "aws_region": region,
        "tag": DEPLOYMENT_NAME,
        "environment": "sandbox",
        "owner": DEPLOYMENT_NAME,
        "db_instance_identifier": intent["db_instance_identifier"],
        "db_size_label": "s",
        "engine": intent["engine"],
        "engine_version": intent["engine_version"],
        "engine_major_version": "12.1",
        "instance_class": intent["instance_class"],
        "master_username": intent["master_username"],
        "manage_master_user_password": True,
        "master_user_secret_kms_key_id": result.outputs.secret_kms_key_arn,
        "db_name": intent["db_name"],
        "db2_port": intent["port"],
        "vpc_id": vpc["vpc_id"],
        "security_group_id": result.outputs.security_group_id,
        "db_subnet_group_name": result.outputs.db_subnet_group_name,
        "publicly_accessible": False,
        "multi_az": intent["multi_az"],
        "storage_type": intent["storage_type"],
        "allocated_storage": intent["allocated_storage"],
        "storage_encrypted": True,
        "kms_key_arn": result.outputs.storage_kms_key_arn,
        "parameter_group_name": result.outputs.parameter_group_name,
        "backup_retention_period": intent["backup_retention_period"],
        "deletion_protection": False,
        "monitoring_role_arn": result.outputs.monitoring_role_arn,
        "created_by": CREATED_BY_TAG,
        "generation_model": GENERATION_MODEL_TAG,
    }
    (rds_dir / "terraform.tfvars").write_text(_tfvars_text(rds_vars))

    ok, blocker = _init_apply(rds_dir, "5-rds", result, timeout=rds_timeout)
    if not ok:
        result.blockers.append(blocker or "5-rds apply failed")
        return result
    result.applied.append("5-rds")
    return result


def _init_apply(
    stack_dir: Path, name: str, result: LiveResult, *, timeout: int
) -> tuple[bool, Optional[str]]:
    """``terraform init`` then ``apply -auto-approve`` a stack. Returns
    ``(ok, blocker)``; classifies expired-credential failures explicitly."""
    init = _run_tf(["init", "-input=false", "-no-color"], stack_dir, timeout=900)
    if init.returncode != 0:
        if _is_expired_creds(init.stderr + init.stdout):
            return False, f"{name}: EXPIRED CREDENTIALS during init"
        return False, f"{name} init failed:\n{init.stdout}\n{init.stderr}"

    apply = _run_tf(
        ["apply", "-auto-approve", "-input=false", "-no-color"], stack_dir, timeout=timeout
    )
    if apply.returncode != 0:
        combined = apply.stderr + apply.stdout
        if _is_expired_creds(combined):
            return False, f"{name}: EXPIRED CREDENTIALS during apply"
        # Keep the tail of the error so the report is actionable but bounded.
        tail = "\n".join(combined.splitlines()[-40:])
        return False, f"{name} apply failed:\n{tail}"
    return True, None


def _tf_outputs(stack_dir: Path) -> dict[str, Any]:
    out = _run_tf(["output", "-json", "-no-color"], stack_dir, timeout=120)
    if out.returncode != 0:
        return {}
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# validate — confirm the created resources match the resolved intent
# ---------------------------------------------------------------------------


def validate(result: LiveResult, *, region: str = DEFAULT_REGION) -> dict[str, Any]:
    """Confirm the RDS for Db2 instance (and supporting resources) created and
    match the resolved intent. Returns a {check: {passed, reason}} map."""
    from botocore.exceptions import ClientError

    checks: dict[str, Any] = {}
    intent = result.intent
    ident = result.outputs.db_instance_identifier

    if "5-rds" not in result.applied:
        checks["rds_instance_applied"] = {
            "passed": False,
            "reason": "5-rds stage did not apply (see blockers)",
        }
        result.validated = checks
        return checks

    rds = _rds_client(region)
    try:
        resp = rds.describe_db_instances(DBInstanceIdentifier=ident)
        inst = resp["DBInstances"][0]
    except ClientError as e:
        checks["instance_exists"] = {"passed": False, "reason": str(e)}
        result.validated = checks
        return checks

    checks["instance_exists"] = {"passed": True, "reason": f"status={inst['DBInstanceStatus']}"}
    checks["engine_is_db2"] = {
        "passed": "db2" in inst.get("Engine", "").lower(),
        "reason": f"engine={inst.get('Engine')}",
    }
    checks["engine_version_matches"] = {
        "passed": inst.get("EngineVersion", "").startswith("12.1"),
        "reason": f"version={inst.get('EngineVersion')}",
    }
    checks["instance_class"] = {
        "passed": inst.get("DBInstanceClass") == intent["instance_class"],
        "reason": f"{inst.get('DBInstanceClass')} (expected {intent['instance_class']})",
    }
    checks["storage_gp3"] = {
        "passed": inst.get("StorageType") == "gp3",
        "reason": f"storage_type={inst.get('StorageType')}",
    }
    checks["allocated_storage_40"] = {
        "passed": inst.get("AllocatedStorage") == 40,
        "reason": f"allocated={inst.get('AllocatedStorage')}",
    }
    checks["single_az"] = {
        "passed": inst.get("MultiAZ") is False,
        "reason": f"multi_az={inst.get('MultiAZ')}",
    }
    checks["storage_encrypted_cmk"] = {
        "passed": inst.get("StorageEncrypted") is True and bool(inst.get("KmsKeyId")),
        "reason": f"encrypted={inst.get('StorageEncrypted')} kms={inst.get('KmsKeyId')}",
    }
    checks["managed_master_secret"] = {
        "passed": inst.get("MasterUserSecret") is not None,
        "reason": "MasterUserSecret present" if inst.get("MasterUserSecret") else "absent",
    }
    checks["backup_retention_1"] = {
        "passed": inst.get("BackupRetentionPeriod") == 1,
        "reason": f"retention={inst.get('BackupRetentionPeriod')}",
    }
    checks["not_public"] = {
        "passed": inst.get("PubliclyAccessible") is False,
        "reason": f"public={inst.get('PubliclyAccessible')}",
    }
    # created_by tag guardrail.
    try:
        arn = inst.get("DBInstanceArn")
        tags = {t["Key"]: t["Value"] for t in rds.list_tags_for_resource(ResourceName=arn).get("TagList", [])}
        checks["created_by_tag"] = {
            "passed": tags.get("created_by") == CREATED_BY_TAG,
            "reason": f"created_by={tags.get('created_by')}",
        }
    except ClientError as e:
        checks["created_by_tag"] = {"passed": False, "reason": str(e)}

    result.validated = checks
    return checks


# ---------------------------------------------------------------------------
# rollback — destroy everything + describe-call cleanup verification (MANDATORY)
# ---------------------------------------------------------------------------


def rollback(
    work_dir: Path, result: LiveResult, *, region: str = DEFAULT_REGION
) -> dict[str, Any]:
    """``terraform destroy`` every staged stack in REVERSE apply order, then
    issue describe calls to PROVE no leftover resource remains (task 15.1).

    Runs unconditionally — even when :func:`deploy` failed partway — so a
    partial apply never leaks burner resources. A KMS key in
    ``PendingDeletion`` counts as cleaned (keys cannot be hard-deleted
    immediately). Records destroyed stacks and any leftover into ``result``."""
    # Reverse dependency order: 5-rds -> 4-parameter-group -> prereqs.
    for stack in ("5-rds", "4-parameter-group", "prereqs"):
        stack_dir = work_dir / stack
        if not (stack_dir / "main.tf").exists() and not (stack_dir / "terraform.tfvars").exists():
            # Not staged (e.g. apply stopped before this stage); nothing to destroy.
            if not stack_dir.exists():
                continue
        destroy = _run_tf(
            ["destroy", "-auto-approve", "-input=false", "-no-color"],
            stack_dir,
            timeout=5400,
        )
        if destroy.returncode == 0:
            result.destroyed.append(stack)
        else:
            combined = destroy.stderr + destroy.stdout
            if _is_expired_creds(combined):
                result.blockers.append(
                    f"{stack}: EXPIRED CREDENTIALS during destroy — manual cleanup "
                    "required after refreshing creds"
                )
            else:
                tail = "\n".join(combined.splitlines()[-30:])
                result.blockers.append(f"{stack} destroy failed:\n{tail}")

    # --- describe-call cleanup verification (proof of no leftovers) --------
    result.leftovers = verify_no_leftovers(result, region=region)
    return result.leftovers


def verify_no_leftovers(result: LiveResult, *, region: str = DEFAULT_REGION) -> dict[str, Any]:
    """Issue read-only describe calls to confirm none of the eval's resources
    remain. Returns a {resource: status} map; ``status`` is ``"absent"`` (good),
    ``"pending-deletion"`` (acceptable for KMS), or a description of a leftover.
    """
    from botocore.exceptions import ClientError

    session = _boto3_session(region)
    rds = session.client("rds")
    ec2 = session.client("ec2")
    iam = session.client("iam")
    kms = session.client("kms")

    leftovers: dict[str, Any] = {}

    # RDS instance.
    ident = result.outputs.db_instance_identifier
    try:
        resp = rds.describe_db_instances(DBInstanceIdentifier=ident)
        status = resp["DBInstances"][0]["DBInstanceStatus"]
        leftovers["rds_instance"] = f"LEFTOVER: {ident} status={status}"
    except ClientError as e:
        leftovers["rds_instance"] = "absent" if "DBInstanceNotFound" in str(e) else str(e)

    # Parameter group.
    pg = result.outputs.parameter_group_name or f"rds-db2-pg-db2-se-12-1-{DEPLOYMENT_NAME.lower()}"
    try:
        rds.describe_db_parameter_groups(DBParameterGroupName=pg)
        leftovers["parameter_group"] = f"LEFTOVER: {pg}"
    except ClientError as e:
        leftovers["parameter_group"] = "absent" if "DBParameterGroupNotFound" in str(e) else str(e)

    # DB subnet group.
    sng = result.outputs.db_subnet_group_name or f"rds-db2-skill-eval-{DEPLOYMENT_NAME}"
    try:
        rds.describe_db_subnet_groups(DBSubnetGroupName=sng)
        leftovers["db_subnet_group"] = f"LEFTOVER: {sng}"
    except ClientError as e:
        leftovers["db_subnet_group"] = "absent" if "DBSubnetGroupNotFoundFault" in str(e) else str(e)

    # Security group.
    sg = result.outputs.security_group_id
    if sg:
        try:
            ec2.describe_security_groups(GroupIds=[sg])
            leftovers["security_group"] = f"LEFTOVER: {sg}"
        except ClientError as e:
            leftovers["security_group"] = "absent" if "InvalidGroup" in str(e) else str(e)
    else:
        # Find by name tag in case the output was lost.
        try:
            resp = ec2.describe_security_groups(
                Filters=[{"Name": "group-name", "Values": [f"rds-db2-skill-eval-{DEPLOYMENT_NAME}"]}]
            )
            groups = resp.get("SecurityGroups", [])
            leftovers["security_group"] = (
                "absent" if not groups else f"LEFTOVER: {groups[0]['GroupId']}"
            )
        except ClientError as e:
            leftovers["security_group"] = str(e)

    # Monitoring IAM role. The burner session policy denies iam:DeleteRole, so a
    # role created by a prior run persists and is REUSED on subsequent runs
    # rather than recreated. Its presence is therefore acceptable (it is an
    # empty, reused role, not a per-run leak); flagged as 'reused' for audit.
    role_name = f"rds-db2-skill-eval-monitoring-{DEPLOYMENT_NAME}"
    try:
        iam.get_role(RoleName=role_name)
        leftovers["monitoring_role"] = (
            f"reused (not deletable under burner session policy): {role_name}"
        )
    except ClientError as e:
        leftovers["monitoring_role"] = "absent" if "NoSuchEntity" in str(e) else str(e)

    # KMS keys (by tag created_by). Pending-deletion is acceptable.
    try:
        kms_leftovers = []
        paginator = kms.get_paginator("list_keys")
        for page in paginator.paginate():
            for k in page.get("Keys", []):
                key_id = k["KeyId"]
                try:
                    tags = kms.list_resource_tags(KeyId=key_id).get("Tags", [])
                except ClientError:
                    continue
                tagmap = {t["TagKey"]: t["TagValue"] for t in tags}
                if tagmap.get("created_by") == CREATED_BY_TAG and tagmap.get("Project") == DEPLOYMENT_NAME:
                    meta = kms.describe_key(KeyId=key_id)["KeyMetadata"]
                    state = meta["KeyState"]
                    if state != "PendingDeletion":
                        kms_leftovers.append(f"{key_id} state={state}")
        leftovers["kms_keys"] = "pending-deletion-or-absent" if not kms_leftovers else (
            "LEFTOVER: " + "; ".join(kms_leftovers)
        )
    except ClientError as e:
        leftovers["kms_keys"] = str(e)

    return leftovers


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_report(result: LiveResult) -> None:
    print("\n==================== EVAL 15.1 BASELINE LIVE REPORT ====================")
    print(f"deployment: {DEPLOYMENT_NAME}")
    print(f"resolved db_instance_identifier: {result.outputs.db_instance_identifier}")
    print(f"engine_version: {result.intent.get('engine_version')}")
    print(f"applied stacks:   {result.applied}")
    print(f"destroyed stacks: {result.destroyed}")
    print("\nintent validation (R3.4): " + ("MATCH" if not check_baseline_fields(result.intent) else "MISMATCH"))
    if result.validated:
        print("\ninstance validations:")
        for name, v in result.validated.items():
            mark = "PASS" if v["passed"] else "FAIL"
            print(f"  [{mark}] {name}: {v['reason']}")
    print("\ncleanup verification (describe calls):")
    for res_name, status in (result.leftovers or {}).items():
        print(f"  {res_name}: {status}")
    if result.blockers:
        print("\nblockers:")
        for b in result.blockers:
            print(f"  - {b}")
    print("========================================================================\n")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="task 15.1 baseline live burner eval")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--work-dir",
        default=str(_PACKAGE_ROOT / "artifacts" / DEPLOYMENT_NAME / "tf"),
    )
    parser.add_argument(
        "--no-rds",
        action="store_true",
        help="apply only prereqs + parameter group (skip the slow RDS instance)",
    )
    parser.add_argument(
        "--rds-timeout", type=int, default=5400, help="seconds for the RDS apply"
    )
    parser.add_argument(
        "--skip-destroy",
        action="store_true",
        help="DANGER: skip cleanup (debugging only); cleanup is normally mandatory",
    )
    args = parser.parse_args(argv)

    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    print(f"[setup] cleaning leftovers from prior runs in {args.region} ...")
    try:
        setup(region=args.region)
    except Exception as e:  # pragma: no cover
        print(f"[setup] warning: {e}")

    result = LiveResult()
    status = STATUS_FAILED
    try:
        print("[deploy] resolving + rendering + applying ...")
        result = deploy(
            work_dir,
            region=args.region,
            apply_rds=not args.no_rds,
            rds_timeout=args.rds_timeout,
        )
        if "5-rds" in result.applied:
            print("[validate] confirming created resources ...")
            validate(result, region=args.region)
            if all(v["passed"] for v in result.validated.values()):
                status = STATUS_COMPLETED
    except Exception as e:  # capture, then ALWAYS clean up
        result.blockers.append(f"unhandled error: {e}")
    finally:
        if not args.skip_destroy:
            print("[rollback] destroying everything + verifying no leftovers ...")
            try:
                rollback(work_dir, result, region=args.region)
            except Exception as e:  # pragma: no cover
                result.blockers.append(f"rollback error: {e}")

    # Write masked artifacts (R15).
    try:
        write_artifacts(
            DEPLOYMENT_NAME,
            intent=result.intent or {},
            status=status,
            plan_summary={
                "applied": result.applied,
                "destroyed": result.destroyed,
                "validations": result.validated,
                "leftovers": result.leftovers,
                "blockers": result.blockers,
            },
            error="; ".join(result.blockers) if result.blockers else None,
        )
    except Exception as e:  # pragma: no cover
        print(f"[artifacts] warning: {e}")

    _print_report(result)
    return 0 if status == STATUS_COMPLETED else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
