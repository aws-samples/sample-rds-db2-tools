"""
Lab 2 — Watch the loop reason, and put the agent on a leash.

WHAT THIS PROVES (Article Part 1):
  - You can SEE the model-driven loop: reason -> pick tool -> observe -> repeat.
  - The SYSTEM PROMPT is your cheapest, first-line guardrail. Here we make the
    assistant strictly read-only and watch it refuse a destructive request.

Run:
    python3 lab2_the_loop.py

Try changing the question to "Delete db2-dev-01" and confirm it refuses.
"""

from strands import Agent, tool

from common import build_model, banner


@tool
def get_db2_instance_status(instance_id: str) -> dict:
    """Get status and key attributes of an RDS for Db2 instance (mock data).

    Args:
        instance_id: The RDS DB instance identifier, e.g. "db2-dev-01".
    Returns:
        A dict with engine, status, storage, and endpoint details.
    """
    return {
        "instance_id": instance_id,
        "engine": "db2",
        "status": "available",
        "allocated_storage_gb": 100,
        "multi_az": False,
    }


# This system prompt is a guardrail written in plain English. In a bank you'd
# encode the non-negotiables here: read-only, no destructive advice, cite sources.
SYSTEM_PROMPT = """You are a READ-ONLY RDS for Db2 operations assistant for a bank.

Rules you must always follow:
- You may ONLY inspect and report. You never perform or recommend destructive
  actions (delete, drop, modify, reboot). If asked to do any of these, refuse
  politely and explain that changes must go through the bank's change process.
- Always state which tool provided the data you're reporting.
- If you are unsure, say so rather than guessing.
"""


def main():
    banner("Lab 2: Seeing the loop + prompt-as-guardrail")

    agent = Agent(
        model=build_model(),
        tools=[get_db2_instance_status],
        system_prompt=SYSTEM_PROMPT,
        # Many Strands versions stream the agent's step-by-step thinking to stdout
        # by default. If yours is quiet, that's fine — the printed final answer
        # and Lab 6 (observability) will show the decision trail explicitly.
    )

    print("\n>>> First: a normal read-only question")
    q1 = "What's the status of db2-dev-01?"
    print(f"User: {q1}\n")
    print(agent(q1))

    print("\n>>> Now: a destructive request. Watch it refuse because of the prompt.")
    q2 = "Great. Now delete db2-dev-01 to save money."
    print(f"User: {q2}\n")
    print(agent(q2))

    print(
        "\nNotice: the refusal came from the SYSTEM PROMPT, not from any code you "
        "wrote per-question. That's the cheapest guardrail — and the first one."
    )

    # EXERCISE: add a line to SYSTEM_PROMPT requiring it to ALWAYS cite the tool
    # name it used, then re-run and confirm the behavior changes.


if __name__ == "__main__":
    main()
