"""
Lab 7 — Clean up (delete what Lab 5 created).

WHY THIS EXISTS:
  If you ran Lab 5 with DRY_RUN = False, you created a REAL, BILLABLE RDS for
  Db2 instance and a custom parameter group. This script deletes both so you
  stop being billed. ALWAYS run this after a live Lab 5 run.

WHAT IT DOES (order matters — the parameter group can't be deleted while the
instance still references it):
  1) Disable deletion protection on the instance (if set).
  2) Delete the instance (skip final snapshot, delete automated backups).
  3) Wait for the instance to finish deleting (several minutes — this is normal).
  4) Delete the custom parameter group.

NOTE ON TIMING: RDS instance deletion takes several minutes, so by default this
script BLOCKS while it waits — that's expected, not a hang. If you don't want to
wait, run with --no-wait: it kicks off the instance deletion and exits, and you
re-run the script once the instance is gone to remove the parameter group.
Re-running is always safe: it just deletes whatever is left.

SAFETY:
  - It only ever targets the tutorial's own named resources (INSTANCE_ID /
    PARAM_GROUP_NAME below), which match Lab 5. It will not touch anything else.
  - It asks for confirmation before deleting, and it verifies the resource was
    created by this tutorial (created_by=strands-tutorial tag) before deleting
    the instance.
  - CONFIRM defaults to requiring you to type 'delete'. There is no dry-run here
    on purpose — cleanup should actually clean up.

Run:
    python3 lab7_cleanup.py             # delete and wait for completion
    python3 lab7_cleanup.py --no-wait   # start deletion, don't block; re-run later
"""

import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from common import banner

# Keep these in sync with lab5_provision_gated.py
REGION = "us-east-1"
INSTANCE_ID = "strands-tutorial-db2-01"
PARAM_GROUP_NAME = "strands-tutorial-db2-params"
EXPECTED_TAG = ("created_by", "strands-tutorial")


def _instance_is_ours(rds, arn: str) -> bool:
    """Verify the instance carries the tutorial's tag before we delete it."""
    try:
        tags = {t["Key"]: t["Value"] for t in
                rds.list_tags_for_resource(ResourceName=arn).get("TagList", [])}
        return tags.get(EXPECTED_TAG[0]) == EXPECTED_TAG[1]
    except ClientError:
        return False


def delete_instance(rds, wait: bool = True) -> bool:
    """Delete the tutorial instance. Returns True if gone (or already absent).

    If wait is False, initiate the delete and return False (not yet gone) so the
    caller knows the parameter group can't be removed just yet.
    """
    try:
        resp = rds.describe_db_instances(DBInstanceIdentifier=INSTANCE_ID)
    except ClientError as e:
        if "DBInstanceNotFound" in str(e):
            print(f"  Instance '{INSTANCE_ID}' not found — nothing to delete.")
            return True
        print(f"  Could not describe instance: {e}")
        return False

    inst = resp["DBInstances"][0]
    arn = inst.get("DBInstanceArn", "")
    if arn and not _instance_is_ours(rds, arn):
        print(
            f"  REFUSING to delete '{INSTANCE_ID}': it is missing the tutorial tag "
            f"{EXPECTED_TAG[0]}={EXPECTED_TAG[1]}. Delete it manually if you're sure."
        )
        return False

    # Disable deletion protection if enabled, then delete.
    try:
        if inst.get("DeletionProtection"):
            rds.modify_db_instance(
                DBInstanceIdentifier=INSTANCE_ID,
                DeletionProtection=False,
                ApplyImmediately=True,
            )
            print("  Disabled deletion protection.")
    except ClientError as e:
        print(f"  (Could not disable deletion protection: {e})")

    try:
        rds.delete_db_instance(
            DBInstanceIdentifier=INSTANCE_ID,
            SkipFinalSnapshot=True,
            DeleteAutomatedBackups=True,
        )
        if not wait:
            print(
                f"  Deletion of instance '{INSTANCE_ID}' started (not waiting).\n"
                "  It takes several minutes. Re-run this script once it's gone to "
                "remove the parameter group."
            )
            return False
        print(f"  Deleting instance '{INSTANCE_ID}' (waiting for completion, ~several minutes)...")
        rds.get_waiter("db_instance_deleted").wait(
            DBInstanceIdentifier=INSTANCE_ID,
            WaiterConfig={"Delay": 30, "MaxAttempts": 60},
        )
        print("  Instance deleted.")
        return True
    except ClientError as e:
        if "DBInstanceNotFound" in str(e):
            return True
        print(f"  Instance delete error: {e}")
        return False


def delete_parameter_group(rds) -> bool:
    """Delete the tutorial parameter group. Returns True if gone (or absent)."""
    try:
        rds.delete_db_parameter_group(DBParameterGroupName=PARAM_GROUP_NAME)
        print(f"  Parameter group '{PARAM_GROUP_NAME}' deleted.")
        return True
    except ClientError as e:
        if "DBParameterGroupNotFound" in str(e):
            print(f"  Parameter group '{PARAM_GROUP_NAME}' not found — nothing to delete.")
            return True
        # Frequently transient right after instance deletion — RDS still holds a ref.
        print(f"  Parameter group delete error (may need a moment, retry): {e}")
        return False


def main():
    wait = "--no-wait" not in sys.argv
    banner("Lab 7: Cleanup — delete the tutorial's Db2 instance + parameter group")
    print(f"  Region:          {REGION}")
    print(f"  Instance:        {INSTANCE_ID}")
    print(f"  Parameter group: {PARAM_GROUP_NAME}")
    print(f"  Mode:            {'wait for deletion' if wait else 'no-wait (re-run later for the PG)'}\n")

    answer = input("  Type 'delete' to confirm deletion (anything else aborts): ").strip().lower()
    if answer != "delete":
        print("  Aborted. Nothing deleted.")
        return

    try:
        rds = boto3.client("rds", region_name=REGION)
    except (BotoCoreError, ClientError) as e:
        print(f"  Could not create RDS client: {e}")
        return

    inst_gone = delete_instance(rds, wait=wait)

    # The parameter group can only be deleted once no instance references it. If we
    # didn't wait (or the instance is still deleting), skip the PG this pass.
    if inst_gone:
        pg_ok = delete_parameter_group(rds)
    else:
        pg_ok = False
        print("  Skipping parameter group for now (instance still deleting).")

    print("\n--- Cleanup summary ---")
    print(f"  Instance removed:        {inst_gone}")
    print(f"  Parameter group removed: {pg_ok}")
    if inst_gone and pg_ok:
        print("\n  All clean. You are no longer being billed for these resources.")
    else:
        print(
            "\n  Not fully clean yet. Once the instance finishes deleting (a few "
            "minutes), re-run:  python3 lab7_cleanup.py  — it will remove the "
            "leftover parameter group. Re-running is always safe."
        )


if __name__ == "__main__":
    main()
