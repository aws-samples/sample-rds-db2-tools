"""
Lab 4 — Two agents are better than one (agents-as-tools).

WHAT THIS PROVES (Article Part 2):
  - The simplest multi-agent pattern: a COORDINATOR delegates to a SPECIALIST.
  - Each agent has a NARROW job and a SMALL toolset. The Advisor cannot touch
    AWS directly — it can only ask the Inspector. That separation is the
    governance point: blast radius stays small per agent.

The "agents-as-tools" technique: wrap a specialist Agent inside an @tool
function, and hand that tool to the coordinator. To the coordinator it looks
like any other tool; under the hood it's a whole agent.

BEFORE YOU RUN: same read-only AWS setup as Lab 3 (AWS_PROFILE, AWS_REGION).

⚠️ USE A FRONTIER MODEL (Bedrock) FOR THIS LAB. Nested multi-agent orchestration
(a coordinator that must correctly emit a tool call to invoke another agent) is a
genuinely hard reasoning task. In testing, local Ollama models — including larger
ones — tend to print the tool call as TEXT (e.g. {"name": "inspector_agent",...})
instead of actually invoking it, so the delegation never happens. Single-tool
labs (Lab 3) work fine locally; this one needs the stronger model. That mirrors
the series' own thesis: local-first for routine work, escalate to frontier for
the hard reasoning. Run this with the default Bedrock path (USE_OLLAMA unset).

Run:
    python3 lab4_multi_agent.py
"""

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from strands import Agent, tool

from common import build_model, banner, use_ollama


# ---------- Specialist's underlying capability (read-only) ----------
@tool
def describe_rds_instances(engine_filter: str = "") -> list:
    """List RDS instances (READ-ONLY). engine_filter is matched as a SUBSTRING, so
    'db2' matches db2-ce/db2-se/db2-ae; '' returns all engines."""
    try:
        rds = boto3.client("rds")
        out = []
        needle = engine_filter.strip().lower()
        for page in rds.get_paginator("describe_db_instances").paginate():
            for db in page.get("DBInstances", []):
                # Substring match — RDS reports Db2 as "db2-ce" etc., so an exact
                # "db2" comparison would wrongly return nothing.
                if needle and needle not in db.get("Engine", "").lower():
                    continue
                out.append(
                    {
                        "instance_id": db.get("DBInstanceIdentifier"),
                        "engine": db.get("Engine"),
                        "engine_version": db.get("EngineVersion"),
                        "status": db.get("DBInstanceStatus"),
                        "instance_class": db.get("DBInstanceClass"),
                        "allocated_storage_gb": db.get("AllocatedStorage"),
                        "multi_az": db.get("MultiAZ"),
                    }
                )
        return out or [{"note": "No RDS instances found."}]
    except (BotoCoreError, ClientError) as e:
        return [{"error": f"AWS call failed: {e}"}]


# ---------- The Inspector, wrapped as a tool for the coordinator ----------
@tool
def inspector_agent(request: str) -> str:
    """Delegate a fact-finding request about the RDS fleet to the Inspector.

    Use this whenever you need factual data about the current RDS instances.
    The Inspector is read-only and returns findings as text.

    Args:
        request: A plain-English description of what to find out.
    Returns:
        The Inspector's findings.
    """
    inspector = Agent(
        model=build_model(),
        tools=[describe_rds_instances],
        system_prompt=(
            "You are a read-only RDS Inspector. Use your tools to gather facts "
            "and report them plainly. Do not give recommendations — just facts."
        ),
        # Silence the sub-agent's own streaming. Without this, the Inspector streams
        # its tokens to stdout at the same time the coordinator is streaming, and the
        # two interleave into garbled text. The coordinator still shows a
        # "Tool #N: inspector_agent" marker for each delegation, so the lesson stays
        # visible; we just don't want the inner monologue mixed in.
        callback_handler=None,
    )
    return str(inspector(request))


def main():
    banner("Lab 4: Coordinator + Inspector (agents-as-tools)")

    if use_ollama():
        print(
            "⚠️  USE_OLLAMA is set. Nested multi-agent delegation is unreliable on\n"
            "    local models — the coordinator often prints the tool call as text\n"
            "    instead of invoking the Inspector, so no delegation happens. If the\n"
            "    run below looks broken, that's why. Re-run on Bedrock for this lab:\n"
            "        unset USE_OLLAMA && python3 lab4_multi_agent.py\n"
        )

    # The coordinator/advisor has NO direct AWS access. Its only tool is the
    # Inspector. It reasons over the Inspector's findings to give advice.
    advisor = Agent(
        model=build_model(),
        tools=[inspector_agent],
        system_prompt=(
            "You are a senior database advisor for a bank. To learn facts about "
            "the RDS fleet, delegate to the inspector_agent tool. Then reason "
            "over the findings and give prioritized, actionable recommendations "
            "(e.g., resilience, storage, version hygiene). Be concise."
        ),
    )

    question = (
        "Review my RDS Db2 fleet and tell me if anything looks misconfigured or "
        "risky for a production bank environment. Prioritize your advice."
    )
    print(f"\nUser: {question}\n")
    # The coordinator streams its answer (and a "Tool #: inspector_agent" marker per
    # delegation) to stdout as it runs, so we don't re-print the returned text — that
    # would show the whole report twice.
    advisor(question)

    print(
        "\nNotice: the Advisor never called AWS. It asked the Inspector, which "
        "has the only (read-only) AWS tool. Two small blast radii, not one big one."
    )

    # EXERCISE: add a CostAgent specialist (mock, or via Cost Explorer read APIs),
    # wrap it as a tool too, and have the Advisor fold cost into its advice.


if __name__ == "__main__":
    main()
