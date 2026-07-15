"""
Lab 6 — Turn on the lights (observability).

WHAT THIS PROVES (Article Part 3):
  - "Traces every decision by default" is real. The loop can emit a structured
    record of every step: which tool was chosen, with what arguments, and what
    it returned. This is the raw material for the examiner's audit trail.

Two ways Strands exposes this:
  1. Callbacks/hooks you attach in code (what this lab uses — works everywhere
     and is easy to SEE).
  2. OpenTelemetry (OTEL) export to a backend (Langfuse, Phoenix, CloudWatch,
     Datadog...) for production. Sketched at the bottom.

Run:
    python3 lab6_observability.py
"""

from strands import Agent, tool

from common import build_model, banner


# A simple in-memory "trace" so you can SEE what a decision trail contains.
TRACE = []


@tool
def get_db2_instance_status(instance_id: str) -> dict:
    """Get status of an RDS for Db2 instance (MOCK — no AWS call).

    Args:
        instance_id: The RDS DB instance identifier.
    Returns:
        A dict of status attributes.
    """
    # NOTE: This is a MOCK. It does NOT query AWS — it echoes back whatever
    # instance_id you pass and ALWAYS reports status "available" / multi_az False.
    # So "<name> is running" here proves nothing about your real fleet; it would
    # say the same for a name that doesn't exist. That's intentional: Lab 6 is
    # about OBSERVABILITY (the decision trail), not real lookups (that's Lab 3).
    # Keeping the tool trivial lets the captured trace stay the star of the show.
    return {"instance_id": instance_id, "engine": "db2", "status": "available", "multi_az": False}


def trace_callback(**kwargs):
    """A callback invoked as the agent runs. Strands passes event data as kwargs.

    Different SDK versions surface slightly different keys, so we record the whole
    event defensively. The POINT is that the loop hands you every step — you're
    not scraping logs after the fact.
    """
    # Record a compact view of whatever the loop emitted this step.
    interesting = {k: v for k, v in kwargs.items() if k in (
        "current_tool_use", "tool", "tool_name", "message", "event", "delta"
    )}
    if interesting:
        TRACE.append(interesting)


def main():
    banner("Lab 6: Observability — the decision trail")

    # callback_handler is the common hook point across Strands versions. If your
    # version names it differently, run:  python3 -c "from strands import Agent; help(Agent.__init__)"
    # to see the exact kwarg, and adjust.
    agent = Agent(
        model=build_model(),
        tools=[get_db2_instance_status],
        system_prompt="You are a read-only Db2 assistant. Be brief.",
        callback_handler=trace_callback,
    )

    question = "Is db2-prod-01 running, and is it Multi-AZ?"
    print(f"\nUser: {question}\n")
    answer = agent(question)

    print("\n--- Final answer ---")
    print(answer)

    print("\n--- Captured decision trail (what an auditor would consume) ---")
    if TRACE:
        for i, step in enumerate(TRACE, 1):
            print(f"[{i}] {step}")
    else:
        print(
            "(No steps captured via this callback name in your SDK version. "
            "See the note in the code to find the correct hook kwarg — the "
            "capability exists; only the name may differ.)"
        )

    print(
        "\nNotice: every tool choice and result flowed through your callback — "
        "that's the observability lesson. (The status itself is MOCK: the tool "
        "always says 'available' and never calls AWS, so it proves nothing about "
        "a real instance. For a live status check, that's Lab 3's job.)"
        "\nIn production you'd export this trail via OpenTelemetry instead of "
        "printing it."
    )

    # ---- Production sketch: OTEL export (not run here) ----
    # Strands emits OpenTelemetry spans. To ship them, you typically set standard
    # OTEL env vars before running, e.g.:
    #   export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318"
    #   export OTEL_SERVICE_NAME="db2-ops-assistant"
    # and point them at Langfuse / Arize Phoenix / CloudWatch / Datadog.
    # The agent code does not change — that's the "observability by default" point.


if __name__ == "__main__":
    main()
