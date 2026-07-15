"""
Lab 1 — Your first custom tool (the Db2-on-RDS assistant begins).

WHAT THIS PROVES (Article Part 1):
  - A tool is just a Python function marked with @tool.
  - The function's docstring is the CONTRACT the model reads to decide when and
    how to call it. Write it like you're briefing a smart new analyst.
  - The tool list IS the agent's blast radius. This agent can only do what its
    tools allow — here, read MOCK status. It cannot touch anything real.

Run:
    python3 lab1_custom_tool.py

No AWS needed — the data is faked so you can focus on the mechanics.
"""

from strands import Agent, tool

from common import build_model, banner


# ---- A custom tool. Note: no cloud calls. Pure mock data. ----
@tool
def get_db2_instance_status(instance_id: str) -> dict:
    """Get the current status and key attributes of an RDS for Db2 instance.

    Args:
        instance_id: The RDS DB instance identifier, e.g. "db2-dev-01".

    Returns:
        A dict with engine, status, storage, and endpoint details.
    """
    # In a real system this would call AWS. Here we return a believable mock.
    fake_fleet = {
        "db2-dev-01": {
            "engine": "db2",
            "engine_version": "12.1.4.0",
            "status": "available",
            "allocated_storage_gb": 100,
            "multi_az": False,
            "endpoint": "db2-dev-01.abc123.us-east-1.rds.amazonaws.com:50000",
        },
        "db2-prod-01": {
            "engine": "db2",
            "engine_version": "12.1.4.0",
            "status": "available",
            "allocated_storage_gb": 500,
            "multi_az": True,
            "endpoint": "db2-prod-01.abc123.us-east-1.rds.amazonaws.com:50000",
        },
    }
    return fake_fleet.get(
        instance_id, {"error": f"No Db2 instance named '{instance_id}' found."}
    )


# ===========================================================================
#  EXERCISE (Step 1 of 3): give the agent a SECOND tool, then watch it choose
#  the right one based on the question.
#
#  UNCOMMENT the tool below: select the lines from "@tool" through the closing
#  "]" and remove the leading "# " from each. (Do NOT uncomment this header.)
# ===========================================================================
# @tool
# def list_db2_backups(instance_id: str) -> list:
#     """List recent automated backups for an RDS for Db2 instance.
#
#     Args:
#         instance_id: The RDS DB instance identifier, e.g. "db2-dev-01".
#     Returns:
#         A list of {snapshot_id, created_at, size_gb} dicts, newest first.
#     """
#     return [
#         {"snapshot_id": "rds:db2-dev-01-2026-07-04", "created_at": "2026-07-04T03:00Z", "size_gb": 100},
#         {"snapshot_id": "rds:db2-dev-01-2026-07-03", "created_at": "2026-07-03T03:00Z", "size_gb": 100},
#     ]


def main():
    banner("Lab 1: A custom Db2 status tool (mock)")

    agent = Agent(
        model=build_model(),
        # EXERCISE (Step 2 of 3): add the new tool to this list, i.e. change it to
        #     tools=[get_db2_instance_status, list_db2_backups]
        tools=[get_db2_instance_status],
    )

    # EXERCISE (Step 3 of 3): change the question to a backup one and re-run:
    #     question = "When was my last backup for db2-dev-01?"
    question = "Is my Db2 instance db2-dev-01 healthy, and how much storage does it have?"
    print(f"\nUser: {question}\n")

    result = agent(question)

    print("\n--- Final answer ---")
    print(result)

    print(
        "\nNotice: the model read your tools' docstrings and, on its own, chose "
        "which tool to call for this question. Add the backups tool (the 3-step "
        "exercise above) and watch it route to list_db2_backups instead."
    )


if __name__ == "__main__":
    main()
