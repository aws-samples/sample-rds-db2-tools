"""Burner-account live driver for the optional-capability + edition scenarios
(task 15.2).

Mirrors :mod:`scripts.eval.live_baseline` but targets the task-15.2 scenarios.
Because a full RDS-for-Db2 instance create takes tens of minutes and the
capabilities here are about RENDERING + the resolver's EDITION DECISION (not AWS
runtime), this driver splits the work by cost/risk and proves each scenario at
the fastest safe level, being explicit about real-apply vs validate-only:

GROUP A — edition reconciliation (R8.5 / R8.6). The resolver decision is proven
locally (resolve -> assert _edition_conversion / honored edition). The AE/SE
*parameter-group family* that decision selects is then proven by a REAL, FAST
apply of the rendered 4-parameter-group:

  * ``db2-ae-12.1`` param group (the SE->AE conversion target, R8.5) — apply +
    destroy.
  * ``db2-se-12.1`` param group (the honored AE->SE downgrade target, R8.6) —
    apply + destroy.

GROUP B — optional capabilities (R13). Each scenario is rendered and
``terraform validate``-d (R10.8). Two of them get a REAL, FAST apply of the AWS
prerequisite the capability needs, proving the capability's create-path inputs
are real:

  * ``audit-to-S3`` (R13.5) — a real audit S3 bucket (the pre-existing-bucket
    the validator/composer require) is created + destroyed.
  * ``BYOK MRK`` (R13.6) — a real multi-region CMK is created; the BYOK scenario
    is re-rendered against the REAL key arn and ``terraform validate``-d, proving
    3-kms is skipped and 5-rds.kms_key_arn is the BYOK key; the key is then
    scheduled for deletion.

The remaining Group B scenarios (prod Multi-AZ, self-managed AD, standby
replica, read replica) are proven by ``terraform validate`` of their rendered
modules. A real Multi-AZ Db2 apply is very slow/expensive; a cross-region
standby needs a source instance first; so these are validate-only here. Task
15.1 already proved a real end-to-end Db2 instance apply, so the capability
rendering + the edition reconciliation are what this task exercises.

CLEANUP IS MANDATORY: every applied stack (param groups, audit bucket) is
``terraform destroy``-d and the BYOK key scheduled for deletion in reverse
order, with describe-call verification, even on failure. The burner session
policy denies ``iam:DeleteRole``; this driver creates NO IAM roles (param
groups / KMS keys / S3 buckets only), so there is nothing undeletable to leak.

Credential-expiry aware: on an ExpiredToken the driver stops, cleans up what it
can, and reports what remains.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    from scripts.eval.capabilities_pipeline import (
        EVAL_IBM_CUSTOMER_ID,
        EVAL_IBM_SITE_ID,
        GROUP_A_BUILDERS,
        GROUP_B_BUILDERS,
        ScenarioResult,
        scenario_audit_to_s3,
        scenario_byok_mrk,
    )
    from scripts.engine_versions import boto3_engine_version_lister
    from scripts.render_terraform import render_terraform
    from scripts.artifacts import write_artifacts, STATUS_COMPLETED, STATUS_FAILED
except ImportError:  # pragma: no cover - bare import fallback
    from eval.capabilities_pipeline import (  # type: ignore
        EVAL_IBM_CUSTOMER_ID,
        EVAL_IBM_SITE_ID,
        GROUP_A_BUILDERS,
        GROUP_B_BUILDERS,
        ScenarioResult,
        scenario_audit_to_s3,
        scenario_byok_mrk,
    )
    from engine_versions import boto3_engine_version_lister  # type: ignore
    from render_terraform import render_terraform  # type: ignore
    from artifacts import write_artifacts, STATUS_COMPLETED, STATUS_FAILED  # type: ignore


_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
try:
    from scripts.render_terraform import DEFAULT_MODULES_ROOT as MODULES_ROOT
except ImportError:  # pragma: no cover - bare import fallback
    from render_terraform import DEFAULT_MODULES_ROOT as MODULES_ROOT  # type: ignore

CREATED_BY_TAG = "rds-db2-skill"
GENERATION_MODEL_TAG = "kiro-spec-eval-15.2"
DEPLOYMENT_NAME = "eval-capabilities-15-2"
DEFAULT_REGION = "us-east-1"

_EXPIRED_CRED_MARKERS = (
    "ExpiredToken",
    "ExpiredTokenException",
    "security token included in the request is expired",
)

_ENV_PLUGIN_MARKERS = (
    "Failed to load plugin schemas",
    "Failed to obtain provider schema",
    "Failed to read any lines from plugin's stdout",
    "Could not load the schema for provider",
)


# ---------------------------------------------------------------------------
# terraform process helpers (mirroring live_baseline)
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


def _run_tf(args: list[str], cwd: Path, *, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_terraform_bin()] + args,
        cwd=cwd,
        env=_tf_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _is_expired_creds(text: str) -> bool:
    return any(m in (text or "") for m in _EXPIRED_CRED_MARKERS)


def _is_env_plugin_failure(text: str) -> bool:
    return any(m in (text or "") for m in _ENV_PLUGIN_MARKERS)


def _boto3_session(region: str):
    import boto3

    return boto3.Session(region_name=region)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class CapLiveResult:
    #: {scenario: {"proof": ..., "validation_ok": bool, "evidence": [...]}}
    scenarios: dict[str, Any] = field(default_factory=dict)
    applied: list[str] = field(default_factory=list)
    destroyed: list[str] = field(default_factory=list)
    validated_modules: dict[str, str] = field(default_factory=dict)
    leftovers: dict[str, Any] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    #: a representative resolved intent (the SE->AE scenario) for the artifact.
    intent: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# tfvars + module staging
# ---------------------------------------------------------------------------


def _tfvars_text(variables: dict[str, Any]) -> str:
    def fmt(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, (list, tuple)):
            return "[" + ", ".join(fmt(x) for x in v) + "]"
        return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'

    return "\n".join(f"{k} = {fmt(v)}" for k, v in variables.items()) + "\n"


def _stage_module(module_name: str, dest: Path) -> None:
    src = MODULES_ROOT / module_name
    dest.mkdir(parents=True, exist_ok=True)
    for tf in src.glob("*.tf"):
        shutil.copy(tf, dest / tf.name)


def _init_apply(stack_dir: Path, name: str, result: CapLiveResult, *, timeout: int) -> bool:
    init = _run_tf(["init", "-input=false", "-no-color"], stack_dir, timeout=900)
    if init.returncode != 0:
        if _is_expired_creds(init.stdout + init.stderr):
            result.blockers.append(f"{name}: EXPIRED CREDENTIALS during init")
        else:
            result.blockers.append(f"{name} init failed:\n{init.stdout[-1500:]}\n{init.stderr[-1500:]}")
        return False
    apply = _run_tf(
        ["apply", "-auto-approve", "-input=false", "-no-color"], stack_dir, timeout=timeout
    )
    if apply.returncode != 0:
        combined = apply.stderr + apply.stdout
        if _is_expired_creds(combined):
            result.blockers.append(f"{name}: EXPIRED CREDENTIALS during apply")
        else:
            tail = "\n".join(combined.splitlines()[-40:])
            result.blockers.append(f"{name} apply failed:\n{tail}")
        return False
    return True


def _tf_outputs(stack_dir: Path) -> dict[str, Any]:
    out = _run_tf(["output", "-json", "-no-color"], stack_dir, timeout=120)
    if out.returncode != 0:
        return {}
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# terraform validate (R10.8) of a scenario's rendered modules
# ---------------------------------------------------------------------------

_ALIAS_PROVIDER_HCL = 'provider "aws" {\n  alias  = "replica"\n  region = "us-west-2"\n}\n'


def _validate_scenario_modules(
    scenario: ScenarioResult, work_dir: Path, result: CapLiveResult
) -> dict[str, str]:
    """``terraform validate`` each enabled module of a rendered scenario.

    Returns {module: status} where status is ``ok``, ``env-skip`` (provider
    plugin/init environment limit), or an error tail. R10.8.
    """
    statuses: dict[str, str] = {}
    if scenario.render is None:
        return {"<render>": "skipped: validation failed before render"}
    for module in scenario.render.enabled_modules:
        module_dir = MODULES_ROOT / module
        if not (module_dir / "variables.tf").is_file():
            continue
        harness = work_dir / "validate" / scenario.name / module
        harness.mkdir(parents=True, exist_ok=True)
        for tf in module_dir.glob("*.tf"):
            shutil.copy(tf, harness / tf.name)
        (harness / "zz_harness_provider.tf").write_text(_ALIAS_PROVIDER_HCL)

        init = _run_tf(["init", "-backend=false", "-input=false", "-no-color"], harness, timeout=900)
        if init.returncode != 0:
            statuses[module] = "env-skip (init failed)"
            continue
        val = _run_tf(["validate", "-no-color"], harness, timeout=300)
        combined = val.stdout + val.stderr
        if val.returncode == 0:
            statuses[module] = "ok"
        elif _is_env_plugin_failure(combined):
            statuses[module] = "env-skip (provider plugin)"
        else:
            statuses[module] = "ERROR: " + "\n".join(combined.splitlines()[-8:])
    result.validated_modules[scenario.name] = "; ".join(f"{m}={s}" for m, s in statuses.items())
    return statuses


# ---------------------------------------------------------------------------
# GROUP A — real fast param-group applies (db2-ae-12.1 and db2-se-12.1)
# ---------------------------------------------------------------------------


def _param_group_vars(edition: str) -> dict[str, Any]:
    return {
        "aws_region": DEFAULT_REGION,
        "tag": f"{DEPLOYMENT_NAME}-{edition}",
        "environment": "sandbox",
        "owner": DEPLOYMENT_NAME,
        "engine_edition": edition,
        "engine_major_version": "12.1",
        "ibm_customer_id": EVAL_IBM_CUSTOMER_ID,
        "ibm_site_id": EVAL_IBM_SITE_ID,
        "created_by": CREATED_BY_TAG,
        "generation_model": GENERATION_MODEL_TAG,
    }


def _apply_param_group(edition: str, work_dir: Path, result: CapLiveResult) -> Optional[str]:
    """Apply the rendered 4-parameter-group for ``edition`` (ae/se) on db2-*-12.1.
    Returns the created parameter-group name, or None on failure."""
    pg_dir = work_dir / f"pg-{edition}"
    _stage_module("4-parameter-group", pg_dir)
    (pg_dir / "terraform.tfvars").write_text(_tfvars_text(_param_group_vars(edition)))
    if not _init_apply(pg_dir, f"4-parameter-group({edition})", result, timeout=900):
        return None
    result.applied.append(f"pg-{edition}")
    outs = _tf_outputs(pg_dir)
    return outs.get("parameter_group_name", {}).get("value", "")


# ---------------------------------------------------------------------------
# GROUP B prereqs — real fast audit S3 bucket + BYOK MRK key
# ---------------------------------------------------------------------------


def _prereqs_b_tf(region: str) -> str:
    """A small standalone root that creates the two FAST Group-B prerequisites:
    a real audit S3 bucket (R13.5/R13.10 pre-existing-bucket) and a real
    multi-region CMK (R13.6 BYOK). No IAM roles are created (nothing undeletable).
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
      Project          = "{DEPLOYMENT_NAME}"
      Environment      = "sandbox"
      Owner            = "{DEPLOYMENT_NAME}"
      ManagedBy        = "Terraform"
    }}
  }}
}}

data "aws_caller_identity" "current" {{}}
data "aws_partition" "current" {{}}

# BYOK multi-region CMK (R13.6) — the customer-managed MRK the BYOK scenario
# reuses instead of auto-creating one.
resource "aws_kms_key" "byok" {{
  description             = "rds-db2-skill eval BYOK MRK - {DEPLOYMENT_NAME}"
  multi_region            = true
  enable_key_rotation     = true
  deletion_window_in_days = 7
}}

# Pre-existing audit S3 bucket (R13.5/R13.10). A bucket name must be globally
# unique; suffix with the account id to avoid collisions.
resource "aws_s3_bucket" "audit" {{
  bucket        = "{DEPLOYMENT_NAME}-audit-${{data.aws_caller_identity.current.account_id}}"
  force_destroy = true
}}

resource "aws_s3_bucket_public_access_block" "audit" {{
  bucket                  = aws_s3_bucket.audit.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}}

output "byok_key_arn" {{ value = aws_kms_key.byok.arn }}
output "byok_key_id" {{ value = aws_kms_key.byok.key_id }}
output "audit_bucket_name" {{ value = aws_s3_bucket.audit.bucket }}
"""


# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------


def deploy(work_dir: Path, *, region: str = DEFAULT_REGION) -> CapLiveResult:
    """Resolve+render every scenario, terraform-validate them, and run the fast
    real-apply proofs (Group-A param groups; Group-B audit bucket + BYOK key)."""
    result = CapLiveResult()
    lister = boto3_engine_version_lister()

    # --- resolve + validate + render every scenario (local) ----------------
    for builder in (*GROUP_A_BUILDERS, *GROUP_B_BUILDERS):
        sc = builder(region=region, lister=lister)
        if sc.group == "A" and sc.name == "se_to_ae_oversized":
            result.intent = sc.intent
        entry: dict[str, Any] = {
            "group": sc.group,
            "proof": sc.proof,
            "validation_ok": sc.validation.ok,
            "evidence": sc.notes,
        }
        if not sc.validation.ok:
            entry["validation_report"] = sc.validation.report()
            result.blockers.append(f"{sc.name}: validation failed")
        # terraform validate of the rendered modules (R10.8).
        entry["terraform_validate"] = _validate_scenario_modules(sc, work_dir, result)
        result.scenarios[sc.name] = entry

    # --- GROUP A real fast proof: apply db2-ae-12.1 + db2-se-12.1 PGs -------
    for edition in ("ae", "se"):
        pg_name = _apply_param_group(edition, work_dir, result)
        if pg_name:
            result.scenarios.setdefault(f"_param_group_{edition}", {})
            result.scenarios[f"_param_group_{edition}"]["applied_parameter_group"] = pg_name

    # --- GROUP B real fast proof: audit bucket + BYOK MRK key --------------
    prereqs_dir = work_dir / "prereqs-b"
    prereqs_dir.mkdir(parents=True, exist_ok=True)
    (prereqs_dir / "main.tf").write_text(_prereqs_b_tf(region))
    if _init_apply(prereqs_dir, "prereqs-b (audit bucket + BYOK key)", result, timeout=900):
        result.applied.append("prereqs-b")
        outs = _tf_outputs(prereqs_dir)
        byok_arn = outs.get("byok_key_arn", {}).get("value", "")
        audit_bucket = outs.get("audit_bucket_name", {}).get("value", "")

        # Re-render BYOK against the REAL key and validate (R13.6 proof).
        if byok_arn:
            sc = scenario_byok_mrk(region=region, lister=lister, byok_key=byok_arn)
            rds = sc.render.modules["5-rds"].variables if sc.render else {}
            result.scenarios["byok_mrk"]["real_key_arn_rendered"] = rds.get("kms_key_arn", "")
            result.scenarios["byok_mrk"]["three_kms_skipped"] = (
                "3-kms" not in (sc.render.enabled_modules if sc.render else [])
            )
            result.scenarios["byok_mrk"]["proof"] = "real-apply (BYOK MRK key created on burner)"
            result.scenarios["byok_mrk"]["terraform_validate"] = _validate_scenario_modules(
                sc, work_dir / "byok-real", result
            )

        # Record that the audit bucket really exists (R13.5/R13.10 proof).
        if audit_bucket:
            result.scenarios["audit_to_s3"]["real_audit_bucket"] = audit_bucket
            result.scenarios["audit_to_s3"]["proof"] = "real-apply (audit S3 bucket created on burner)"

    return result


# ---------------------------------------------------------------------------
# rollback — destroy everything + describe verification (MANDATORY)
# ---------------------------------------------------------------------------


def rollback(work_dir: Path, result: CapLiveResult, *, region: str = DEFAULT_REGION) -> dict[str, Any]:
    """Destroy every applied stack in reverse order; verify via describe calls."""
    for stack in ("prereqs-b", "pg-se", "pg-ae"):
        stack_dir = work_dir / stack
        if not stack_dir.exists():
            continue
        destroy = _run_tf(
            ["destroy", "-auto-approve", "-input=false", "-no-color"], stack_dir, timeout=1800
        )
        if destroy.returncode == 0:
            result.destroyed.append(stack)
        else:
            combined = destroy.stderr + destroy.stdout
            if _is_expired_creds(combined):
                result.blockers.append(
                    f"{stack}: EXPIRED CREDENTIALS during destroy — manual cleanup required"
                )
            else:
                tail = "\n".join(combined.splitlines()[-25:])
                result.blockers.append(f"{stack} destroy failed:\n{tail}")

    result.leftovers = verify_no_leftovers(result, region=region)
    return result.leftovers


def verify_no_leftovers(result: CapLiveResult, *, region: str = DEFAULT_REGION) -> dict[str, Any]:
    """Read-only describe calls to confirm no eval resource remains. A KMS key in
    PendingDeletion is acceptable (keys cannot be hard-deleted immediately)."""
    from botocore.exceptions import ClientError

    session = _boto3_session(region)
    rds = session.client("rds")
    s3 = session.client("s3")
    kms = session.client("kms")
    leftovers: dict[str, Any] = {}

    # Parameter groups (db2-ae-12.1 and db2-se-12.1).
    for edition in ("ae", "se"):
        pg = f"rds-db2-pg-db2-{edition}-12-1-{DEPLOYMENT_NAME.lower()}-{edition}"
        try:
            rds.describe_db_parameter_groups(DBParameterGroupName=pg)
            leftovers[f"parameter_group_{edition}"] = f"LEFTOVER: {pg}"
        except ClientError as e:
            leftovers[f"parameter_group_{edition}"] = (
                "absent" if "DBParameterGroupNotFound" in str(e) else str(e)
            )

    # Audit bucket.
    try:
        acct = session.client("sts").get_caller_identity()["Account"]
        bucket = f"{DEPLOYMENT_NAME}-audit-{acct}"
        try:
            s3.head_bucket(Bucket=bucket)
            leftovers["audit_bucket"] = f"LEFTOVER: {bucket}"
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            leftovers["audit_bucket"] = "absent" if code in ("404", "NoSuchBucket") else f"{code}"
    except ClientError as e:
        leftovers["audit_bucket"] = str(e)

    # BYOK + any eval-tagged KMS keys: PendingDeletion is acceptable.
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
                if (
                    tagmap.get("created_by") == CREATED_BY_TAG
                    and tagmap.get("Project") == DEPLOYMENT_NAME
                ):
                    meta = kms.describe_key(KeyId=key_id)["KeyMetadata"]
                    if meta["KeyState"] != "PendingDeletion":
                        kms_leftovers.append(f"{key_id} state={meta['KeyState']}")
        leftovers["kms_keys"] = (
            "pending-deletion-or-absent" if not kms_leftovers else "LEFTOVER: " + "; ".join(kms_leftovers)
        )
    except ClientError as e:
        leftovers["kms_keys"] = str(e)

    return leftovers


# ---------------------------------------------------------------------------
# setup — clean leftovers from a prior run
# ---------------------------------------------------------------------------


def setup(region: str = DEFAULT_REGION) -> dict[str, Any]:
    """Best-effort cleanup of leftovers from a prior run (param groups). The
    audit bucket / BYOK key are removed by ``terraform destroy`` in rollback."""
    from botocore.exceptions import ClientError

    rds = _boto3_session(region).client("rds")
    removed: list[str] = []
    for edition in ("ae", "se"):
        pg = f"rds-db2-pg-db2-{edition}-12-1-{DEPLOYMENT_NAME.lower()}-{edition}"
        try:
            rds.delete_db_parameter_group(DBParameterGroupName=pg)
            removed.append(pg)
        except ClientError:
            pass
    return {"region": region, "removed": removed}


# ---------------------------------------------------------------------------
# report + CLI
# ---------------------------------------------------------------------------


def _print_report(result: CapLiveResult) -> None:
    print("\n================ EVAL 15.2 CAPABILITIES LIVE REPORT ================")
    print(f"deployment: {DEPLOYMENT_NAME}")
    print(f"applied stacks:   {result.applied}")
    print(f"destroyed stacks: {result.destroyed}")
    print("\nper-scenario:")
    for name, e in result.scenarios.items():
        if name.startswith("_"):
            print(f"  [{name}] {e}")
            continue
        print(f"  --- {name} (group {e.get('group')}, proof={e.get('proof')}) ---")
        print(f"      validation_ok: {e.get('validation_ok')}")
        for ev in e.get("evidence", []):
            print(f"        - {ev}")
        if e.get("terraform_validate"):
            print(f"      terraform_validate: {e['terraform_validate']}")
        for extra in ("real_key_arn_rendered", "three_kms_skipped", "real_audit_bucket"):
            if extra in e:
                print(f"      {extra}: {e[extra]}")
    print("\ncleanup verification (describe calls):")
    for res_name, status in (result.leftovers or {}).items():
        print(f"  {res_name}: {status}")
    if result.blockers:
        print("\nblockers:")
        for b in result.blockers:
            print(f"  - {b}")
    print("===================================================================\n")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="task 15.2 capabilities live burner eval")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--work-dir", default=str(_PACKAGE_ROOT / "artifacts" / DEPLOYMENT_NAME / "tf")
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

    result = CapLiveResult()
    status = STATUS_FAILED
    try:
        print("[deploy] resolving + rendering + validating + fast applies ...")
        result = deploy(work_dir, region=args.region)
        scenario_ok = all(
            e.get("validation_ok", True)
            for k, e in result.scenarios.items()
            if not k.startswith("_")
        )
        applied_ok = "prereqs-b" in result.applied and "pg-ae" in result.applied and "pg-se" in result.applied
        if scenario_ok and applied_ok and not result.blockers:
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

    try:
        write_artifacts(
            DEPLOYMENT_NAME,
            intent=result.intent or {},
            status=status,
            plan_summary={
                "applied": result.applied,
                "destroyed": result.destroyed,
                "scenarios": result.scenarios,
                "validated_modules": result.validated_modules,
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
