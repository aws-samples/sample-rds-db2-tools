"""
Lab 5 — The human gate + a REAL two-step provision (RDS for Db2).

WHAT THIS PROVES (Article Part 3 — the control envelope):
  - Human-in-the-loop BEFORE a consequential, real action.
  - The agent does the thinking (understand the request, assemble a concrete
    plan); the human owns the irreversible approval.
  - Real infrastructure has real dependencies. RDS for Db2 will NOT start from a
    plain create_db_instance call: Db2 is bring-your-own-license (BYOL) and
    requires a CUSTOM DB PARAMETER GROUP carrying your IBM licensing IDs
    (rds.ibm_customer_id and rds.ibm_site_id). So provisioning is two steps:
        1) create the custom parameter group (family db2-ce-12.1) + set IBM IDs
        2) create the instance and ATTACH that parameter group
    This lab models that dependency honestly.

  ⚠️ PLACEHOLDER IBM IDs — READ THIS:
     This lab uses Db2 Community Edition (db2-ce) v12.1, whose license is free
     from IBM. The rds.ibm_customer_id / rds.ibm_site_id below are TEST
     PLACEHOLDERS, sufficient only to illustrate the flow. THEY ARE NOT VALID
     LICENSING IDs. Get your own free Community Edition IDs from IBM and replace
     them: https://www.ibm.com/account/reg/us-en/signup?formid=urx-54367

  ⚠️ THIS LAB CAN CREATE A BILLABLE RESOURCE.
     DRY_RUN defaults to True — it prints the exact calls it would make and
     creates nothing. To actually provision (in a NON-PROD account), either edit
     DRY_RUN_DEFAULT = False below, or set the DRY_RUN env var to a false-like
     value. Then ALWAYS run lab7_cleanup.py afterwards to delete the instance and
     parameter group so you stop being billed.

Run:
    python3 lab5_provision_gated.py            # safe dry-run (default)
    DRY_RUN=false python3 lab5_provision_gated.py   # go live (non-prod!)

Cleanup (after a live run):
    python3 lab7_cleanup.py
"""

import json
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from strands import Agent, tool

from common import (
    build_model,
    banner,
    resolve_db2_engine,
    resolve_db2_networking,
    suggest_subnet_group_command,
)

# ============================================================================
#  SAFETY FLAG. True = simulate only (nothing created). Set to False ONLY in a
#  non-prod account when you deliberately want to create a real Db2 instance,
#  and remember to run lab7_cleanup.py afterwards.
#
#  Two ways to go live (both equivalent):
#    1. Edit the default below:            DRY_RUN_DEFAULT = False
#    2. Set an environment variable:       export DRY_RUN=false   (then run)
#
#  SAFE BY DESIGN: going live requires an EXPLICIT false-like value
#  ("false", "0", "no", "off"). Anything unset, empty, or unrecognized stays in
#  dry-run — so a typo can never accidentally create billable resources.
# ============================================================================
DRY_RUN_DEFAULT = True  # <-- change to False here, or use the DRY_RUN env var

_FALSEY = {"false", "0", "no", "off"}
_TRUEY = {"true", "1", "yes", "on"}


def _resolve_dry_run() -> bool:
    raw = os.environ.get("DRY_RUN")
    if raw is None or raw.strip() == "":
        return DRY_RUN_DEFAULT
    val = raw.strip().lower()
    if val in _FALSEY:
        return False   # explicit opt-in to live
    if val in _TRUEY:
        return True
    # Unrecognized value -> stay safe (dry-run).
    print(f"⚠️  DRY_RUN={raw!r} not understood; staying in DRY-RUN for safety.")
    return True


DRY_RUN = _resolve_dry_run()

# ---- Shared config (kept in sync with lab7_cleanup.py) ----
REGION = "us-east-1"
INSTANCE_ID = "strands-tutorial-db2-01"
PARAM_GROUP_NAME = "strands-tutorial-db2-params"
ENGINE = "db2-ce"                            # Db2 Community Edition (free license)

# Engine version + parameter-group family are DISCOVERED at runtime, not hardcoded.
# RDS versions are fully-qualified builds (e.g. "12.1.4.0.sb00085812.r1"), they
# change over time, and the version must match the parameter group's family — so
# we resolve both together and stay in sync automatically. (Override with env vars
# DB2_ENGINE_VERSION / DB2_PARAM_GROUP_FAMILY; see resolve_db2_engine in common.py.)
_DB2 = resolve_db2_engine(REGION, ENGINE)
ENGINE_VERSION = _DB2["engine_version"]      # e.g. "12.1.4.0.sb00085812.r1" (or None -> RDS default)
PARAM_GROUP_FAMILY = _DB2["param_group_family"]  # e.g. "db2-ce-12.1", matched to the version

INSTANCE_CLASS = "db.t3.small"               # small/dev class; see note if unsupported
ALLOCATED_STORAGE_GB = 100
MULTI_AZ = False                             # single-AZ for a cheap dev instance

# ⚠️ PLACEHOLDERS — replace with your own IBM IDs. Db2 Community Edition IDs are
# free from IBM: https://www.ibm.com/account/reg/us-en/signup?formid=urx-54367
IBM_CUSTOMER_ID = "1234567"        # <-- placeholder only
IBM_SITE_ID = "1234567890"         # <-- placeholder only

# A tag so you can always find/identify what this tutorial created.
TUTORIAL_TAG = {"Key": "created_by", "Value": "strands-tutorial"}


# ---------------------------------------------------------------------------
#  Policy check ("validation hook" in spirit) — enforced regardless of the model
# ---------------------------------------------------------------------------
def validate_plan(plan: dict) -> tuple[bool, str]:
    """Enforce tutorial policy on a provisioning plan."""
    if plan.get("Engine") != ENGINE:
        return False, f"This workflow only provisions {ENGINE}."
    if plan.get("AllocatedStorage", 0) < 20:
        return False, "Allocated storage must be at least 20 GB."
    if not plan.get("ibm_customer_id") or not plan.get("ibm_site_id"):
        return False, "IBM customer/site IDs are required for Db2 (BYOL)."
    # EXERCISE: reject prod plans that are single-AZ:
    # if plan.get("_environment") == "prod" and not plan.get("MultiAZ"):
    #     return False, "Production Db2 instances must be Multi-AZ."
    return True, "Plan passes policy checks."


# ---------------------------------------------------------------------------
#  The agent's only tool: assemble a plan. It does NOT create anything.
# ---------------------------------------------------------------------------
@tool
def prepare_db2_provisioning_plan(
    instance_id: str,
    allocated_storage_gb: int,
    instance_class: str = INSTANCE_CLASS,
    region: str = REGION,
    multi_az: bool = MULTI_AZ,
) -> dict:
    """Assemble a concrete plan to provision an RDS for Db2 instance.

    This ONLY builds the parameter set for review. It creates nothing. Note that
    Db2 requires a custom parameter group with IBM licensing IDs, so the plan
    includes both the parameter-group step and the instance step.

    Args:
        instance_id: Desired DB instance identifier.
        allocated_storage_gb: Storage size in GB (minimum 20).
        instance_class: RDS instance class.
        region: AWS region.
        multi_az: Whether to deploy Multi-AZ for high availability.
    Returns:
        A dict describing the two-step provisioning plan.
    """
    return {
        # Step 1 — parameter group
        "ParameterGroupName": PARAM_GROUP_NAME,
        "ParameterGroupFamily": PARAM_GROUP_FAMILY,
        "ibm_customer_id": IBM_CUSTOMER_ID,
        "ibm_site_id": IBM_SITE_ID,
        # Step 2 — instance
        "DBInstanceIdentifier": instance_id,
        "Engine": ENGINE,
        "EngineVersion": ENGINE_VERSION,
        "DBInstanceClass": instance_class,
        "AllocatedStorage": allocated_storage_gb,
        "StorageType": "gp3",
        "StorageEncrypted": True,
        "MultiAZ": multi_az,
        "LicenseModel": "bring-your-own-license",
        "ManageMasterUserPassword": True,  # Secrets Manager — no hardcoded secret
        "_region": region,
    }


# ---------------------------------------------------------------------------
#  Human approval gate — this IS the interrupt / human-in-the-loop control
# ---------------------------------------------------------------------------
def human_approval_gate(plan: dict) -> bool:
    print("\n" + "!" * 70)
    print("  HUMAN APPROVAL REQUIRED — the agent prepared this two-step plan:")
    print("!" * 70)
    print(json.dumps(plan, indent=2))
    print(
        "\n  NOTE: ibm_customer_id / ibm_site_id above are TEST PLACEHOLDERS."
        "\n        Real customers must use their own IBM-issued IDs."
    )

    ok, reason = validate_plan(plan)
    print(f"\n  Policy check: {'PASS' if ok else 'FAIL'} — {reason}")
    if not ok:
        print("  Auto-rejected by policy. Nothing will be executed.")
        return False

    if DRY_RUN:
        print("\n  (DRY_RUN is True — approval will trigger a SIMULATION only.)")
    else:
        print("\n  (DRY_RUN is False — approval will CREATE REAL, BILLABLE resources.)")

    answer = input("\n  Type 'yes' to approve (anything else cancels): ").strip().lower()
    return answer == "yes"


# ---------------------------------------------------------------------------
#  Execution — the real two-step create (or a dry-run print)
# ---------------------------------------------------------------------------
def create_parameter_group(rds, plan: dict):
    """Step 1: create the custom parameter group and set the IBM licensing IDs.

    Idempotent: if the group already exists (e.g. a prior run created it but the
    instance step then failed), we reuse it instead of erroring — and still
    (re)apply the IBM IDs so the values are correct.
    """
    try:
        rds.create_db_parameter_group(
            DBParameterGroupName=plan["ParameterGroupName"],
            DBParameterGroupFamily=plan["ParameterGroupFamily"],
            Description="Strands tutorial Db2 parameter group (IBM licensing IDs)",
            Tags=[TUTORIAL_TAG],
        )
        print(f"  [1/2] Parameter group '{plan['ParameterGroupName']}' created.")
    except ClientError as e:
        if "DBParameterGroupAlreadyExists" in str(e):
            print(f"  [1/2] Parameter group '{plan['ParameterGroupName']}' already exists — reusing it.")
        else:
            raise
    rds.modify_db_parameter_group(
        DBParameterGroupName=plan["ParameterGroupName"],
        Parameters=[
            {
                "ParameterName": "rds.ibm_customer_id",
                "ParameterValue": plan["ibm_customer_id"],
                "ApplyMethod": "pending-reboot",
            },
            {
                "ParameterName": "rds.ibm_site_id",
                "ParameterValue": plan["ibm_site_id"],
                "ApplyMethod": "pending-reboot",
            },
        ],
    )
    print(f"        IBM licensing IDs applied to '{plan['ParameterGroupName']}'.")


def create_instance(rds, plan: dict):
    """Step 2: create the Db2 instance, attaching the custom parameter group."""
    kwargs = dict(
        DBInstanceIdentifier=plan["DBInstanceIdentifier"],
        Engine=plan["Engine"],
        DBInstanceClass=plan["DBInstanceClass"],
        AllocatedStorage=plan["AllocatedStorage"],
        StorageType=plan["StorageType"],
        StorageEncrypted=plan["StorageEncrypted"],
        MultiAZ=plan["MultiAZ"],
        LicenseModel=plan["LicenseModel"],
        ManageMasterUserPassword=plan["ManageMasterUserPassword"],
        MasterUsername="db2admin",
        DBParameterGroupName=plan["ParameterGroupName"],  # <-- the dependency
        Tags=[TUTORIAL_TAG],
    )
    # Only pin EngineVersion if we resolved one; otherwise let RDS use its default.
    if plan.get("EngineVersion"):
        kwargs["EngineVersion"] = plan["EngineVersion"]
    # Place the instance explicitly. Required in accounts WITHOUT a default VPC
    # (otherwise: "InvalidSubnet: No default subnet detected in VPC"). In accounts
    # WITH a default VPC these may be empty, and RDS uses the defaults.
    if plan.get("DBSubnetGroupName"):
        kwargs["DBSubnetGroupName"] = plan["DBSubnetGroupName"]
    if plan.get("VpcSecurityGroupIds"):
        kwargs["VpcSecurityGroupIds"] = plan["VpcSecurityGroupIds"]
    rds.create_db_instance(**kwargs)
    print(f"  [2/2] Instance '{plan['DBInstanceIdentifier']}' creation initiated.")


def execute_plan(plan: dict):
    """Run the two-step create, or simulate it under DRY_RUN."""
    # Resolve networking (DB subnet group + security group) so the instance can be
    # placed even in accounts without a default VPC. Done here so DRY_RUN shows it
    # too. In an account with a default VPC these may be empty (RDS uses defaults).
    net = resolve_db2_networking(plan.get("_region", REGION))
    plan["DBSubnetGroupName"] = net.get("db_subnet_group")
    plan["VpcSecurityGroupIds"] = net.get("vpc_security_group_ids") or []

    if DRY_RUN:
        print("\n--- DRY RUN: the following would run (nothing is created) ---")
        print("STEP 1: rds.create_db_parameter_group(...) + modify_db_parameter_group(")
        print(f"          rds.ibm_customer_id={plan['ibm_customer_id']!r},")
        print(f"          rds.ibm_site_id={plan['ibm_site_id']!r})")
        print("STEP 2: rds.create_db_instance(")
        print(json.dumps(
            {k: v for k, v in plan.items()
             if k not in ("ibm_customer_id", "ibm_site_id") and not k.startswith("_")},
            indent=2,
        ))
        print(f"          DBParameterGroupName={plan['ParameterGroupName']!r})")
        if not plan["DBSubnetGroupName"]:
            print(
                "        (No DB subnet group discovered. A live run will use the default VPC "
                "if one exists,\n         or stop with a create-db-subnet-group command if not.)"
            )
        print("\nNo resources created. DRY_RUN is True. (Set it False to go live.)")
        return

    region = plan.get("_region", REGION)

    # Preflight: if we found no DB subnet group AND there's no default VPC, the
    # create would fail with InvalidSubnet. Stop BEFORE creating anything (so we
    # don't orphan a parameter group) and hand the user the exact fix.
    if not plan["DBSubnetGroupName"]:
        try:
            ec2 = boto3.client("ec2", region_name=region)
            has_default_vpc = bool(
                ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
                .get("Vpcs", [])
            )
        except (BotoCoreError, ClientError):
            has_default_vpc = False
        if not has_default_vpc:
            print(
                "\n  ✋ Cannot place the instance: this account has no default VPC and "
                "no existing DB subnet group was found.\n"
                "  RDS needs a DB subnet group spanning two AZs. Create one, then re-run.\n\n"
                "  Ready-to-paste command (subnets auto-filled from your account):\n"
            )
            print("    " + suggest_subnet_group_command(region, PARAM_GROUP_NAME.replace("params", "subnets")))
            print("\n  Nothing was created.")
            return

    print("\n--- LIVE: creating real resources (run lab7_cleanup.py afterwards!) ---")
    try:
        rds = boto3.client("rds", region_name=region)
        create_parameter_group(rds, plan)
        create_instance(rds, plan)
        print(
            "\n  Instance is provisioning (takes several minutes). Check status:\n"
            f"    aws rds describe-db-instances --db-instance-identifier {plan['DBInstanceIdentifier']} "
            f"--region {plan.get('_region', REGION)} --query 'DBInstances[0].DBInstanceStatus'\n"
            "  When you're done, delete it:\n"
            "    python3 lab7_cleanup.py"
        )
    except (BotoCoreError, ClientError) as e:
        print(f"  AWS call failed: {e}")
        print(
            "  Common causes: instance class not supported for Db2 in this region "
            "(try db.m6i.large), engine version no longer offered (list with "
            "`aws rds describe-db-engine-versions --engine db2-ce`), or missing VPC/"
            "subnet-group setup. See the RDS for Db2 prerequisites in the docs."
        )


def main():
    banner("Lab 5: Human-gated, two-step Db2 provisioning")
    _src = "env DRY_RUN" if os.environ.get("DRY_RUN", "").strip() else "file default"
    print(
        f"DRY_RUN = {DRY_RUN}  (from {_src}; True = simulate only, "
        "False = create real resources)"
    )
    print(
        f"Resolved Db2 engine: version="
        f"{ENGINE_VERSION or '(RDS default)'}, family={PARAM_GROUP_FAMILY}\n"
    )

    planner = Agent(
        model=build_model(),
        tools=[prepare_db2_provisioning_plan],
        system_prompt=(
            "You are a provisioning assistant for RDS for Db2. Turn the user's "
            "plain-English request into a concrete plan by calling "
            "prepare_db2_provisioning_plan with sensible parameters. Remember Db2 "
            "is bring-your-own-license and needs a custom parameter group with IBM "
            "IDs — the plan already models that. Do not claim to have created "
            "anything; a human approves and executes separately."
        ),
    )

    request = (
        "I need a small Db2 dev instance in us-east-1, about 100 GB, single-AZ is "
        f"fine. Call it {INSTANCE_ID}."
    )
    print(f"User: {request}\n")

    agent_result = planner(request)
    print("\n--- Agent's summary of the prepared plan ---")
    print(agent_result)

    # Rebuild the plan deterministically so the approval gate has clean, complete data.
    plan = prepare_db2_provisioning_plan(
        instance_id=INSTANCE_ID,
        allocated_storage_gb=ALLOCATED_STORAGE_GB,
        instance_class=INSTANCE_CLASS,
        region=REGION,
        multi_az=MULTI_AZ,
    )

    if human_approval_gate(plan):
        execute_plan(plan)
    else:
        print("\nCancelled. No action taken.")

    print(
        "\nNotice: the powerful action was gated. The agent planned; a human "
        "decided; and the real work correctly handled Db2's parameter-group "
        "dependency before creating the instance."
    )


if __name__ == "__main__":
    main()
