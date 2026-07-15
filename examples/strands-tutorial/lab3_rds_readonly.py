"""
Lab 3 — Real AWS, READ-ONLY (describe RDS instances).

WHAT THIS PROVES (Article Parts 1 & 3):
  - A tool can safely touch a bank's real systems IF it only calls read APIs.
  - This agent is STRUCTURALLY incapable of changing anything: the only AWS
    tool it has is rds.describe_db_instances(). Control by construction.

BEFORE YOU RUN:
  - Use a READ-ONLY AWS profile:  export AWS_PROFILE=your-readonly-profile
  - Set a region:                 export AWS_REGION=us-east-1
  - Confirm identity:             aws sts get-caller-identity
  - If you have no RDS instances, the tool returns an empty list. That's fine;
    the wiring is still demonstrated.

Run:
    python3 lab3_rds_readonly.py
"""

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from strands import Agent, tool

from common import build_model, banner


@tool
def describe_rds_instances(engine_filter: str = "") -> list:
    """List RDS database instances in the current AWS account/region (READ-ONLY).

    Calls rds.describe_db_instances(). Does not modify anything.

    Args:
        engine_filter: Optional engine name to filter by. Matched as a substring,
                       so "db2" matches all Db2 editions ("db2-ce", "db2-se",
                       "db2-ae"), and "postgres" matches "postgres". Leave empty to
                       return all engines.
    Returns:
        A list of dicts with key attributes for each instance.
    """
    try:
        rds = boto3.client("rds")
        paginator = rds.get_paginator("describe_db_instances")
        instances = []
        needle = engine_filter.strip().lower()
        for page in paginator.paginate():
            for db in page.get("DBInstances", []):
                # Substring match: RDS reports Db2 as "db2-ce"/"db2-se"/"db2-ae",
                # so an exact "db2" comparison would wrongly match nothing.
                if needle and needle not in db.get("Engine", "").lower():
                    continue
                instances.append(
                    {
                        "instance_id": db.get("DBInstanceIdentifier"),
                        "engine": db.get("Engine"),
                        "engine_version": db.get("EngineVersion"),
                        "status": db.get("DBInstanceStatus"),
                        "instance_class": db.get("DBInstanceClass"),
                        "allocated_storage_gb": db.get("AllocatedStorage"),
                        "multi_az": db.get("MultiAZ"),
                        "endpoint": (db.get("Endpoint") or {}).get("Address"),
                    }
                )
        if not instances:
            return [{"note": "No RDS instances found in this region (or none match the filter)."}]
        return instances
    except (BotoCoreError, ClientError) as e:
        # Return the error as data so the agent can explain it, rather than crashing.
        return [{"error": f"AWS call failed: {e}"}]


def main():
    banner("Lab 3: Real read-only RDS inspection")

    agent = Agent(
        model=build_model(),
        tools=[describe_rds_instances],
        system_prompt=(
            "You are a read-only cloud database inspector. Report clearly and "
            "concisely on RDS instances. State counts and highlight anything "
            "notable (e.g., single-AZ instances, unusual status). You cannot "
            "make changes."
        ),
    )

    question = (
        "Summarize the RDS instances in my account. Are any of them Db2? "
        "Flag any that are single-AZ."
    )
    print(f"\nUser: {question}\n")
    print(agent(question))

    print(
        "\nNotice: the agent hit a REAL AWS API, but only a Describe call. "
        "It could not have deleted or modified anything even if asked."
    )

    # EXERCISE: add a read-only describe_db2_parameter_groups() tool:
    #   rds.describe_db_parameter_groups() + rds.describe_db_parameters(...)
    # and ask the agent to summarize your Db2 configuration.


if __name__ == "__main__":
    main()
