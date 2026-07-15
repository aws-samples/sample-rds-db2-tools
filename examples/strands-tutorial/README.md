# Hands-On Strands: An RDS for Db2 Operations Assistant

*A progressive, runnable tutorial for learning [Strands Agents](https://strandsagents.com) — the open-source AI agent SDK from AWS — by building a real operations assistant for Amazon RDS for Db2.*

> This tutorial is the hands-on companion to a [LinkedIn article series](https://www.linkedin.com/pulse/agent-standard-why-global-bank-should-bet-strands-vikram-khatri-edfec) on standardizing an enterprise agentic-AI layer on Strands. You don't need the articles to follow along — but each lab is designed to make one concept from them concrete, so you learn by running, not just reading.

---

## What you'll build

By the end you'll have built an AI agent that can inspect, advise on, and (with a human in the loop) **provision** RDS for Db2 — and you'll understand every moving part.

| Lab | You build | Concept it teaches |
|---|---|---|
| **Lab 0** | Environment + first agent (`calculator`) | The 3-line agent; the agentic loop |
| **Lab 1** | A custom `@tool`; a mock Db2 status assistant | Tools = an agent's capabilities & blast radius |
| **Lab 2** | The loop reasoning + a system-prompt guardrail | Model-driven behavior; prompt as a leash |
| **Lab 3** | Real **read-only** AWS: describe RDS instances | Read-only tools are structurally safe |
| **Lab 4** | Multi-agent: inspector + advisor (agents-as-tools) | Multi-agent coordination patterns |
| **Lab 5** | A **human-gated**, real two-step Db2 provision | Human-in-the-loop; real-world dependencies |
| **Lab 6** | Turn on tracing; read the decision trail | Observability by default (OpenTelemetry) |
| **Lab 7** | Clean up the resources Lab 5 created | Reversibility; don't leave billable resources |
| **Lab 8** | Evaluate the agent: LLM-as-judge, trajectory, chaos | Proving it before it ships (evals / red-teaming) |

**Progression principle:** start at **zero cost and zero risk** (local model + mock data), add **read-only** AWS, and only reach a real mutating action once — Lab 5 — behind an explicit human approval gate and a dry-run default. Lab 7 tears it all back down.

---

## Prerequisites

- **Python 3.10+** — check with `python3 --version`.
- **A model provider** — pick one:
  - **[Amazon Bedrock](https://aws.amazon.com/bedrock/) (default, matches the articles).** Enable access to a model (e.g., a Claude model) in the Bedrock console for your region. Strands defaults to Bedrock.
  - **Ollama (fully local, free).** Install [Ollama](https://ollama.com), then pull a model. Run the server in one shell and pull the model in another:
    ```bash
    ollama serve                  # shell 1: start the local Ollama server
    ollama pull llama3.1:latest   # shell 2: download the model
    ```
    Best for practicing Labs 0–2 with zero API cost. To pick a different or newer model, browse the [Ollama Library](https://ollama.com/library) (see the Ollama model-selection note under [Setup](#setup)).
- **An [AWS account](https://aws.amazon.com/free/)** — needed for Labs 3–7. Run `aws sts get-caller-identity` to confirm credentials.

### 🔒 Safety first

This tutorial teaches an operations mindset, so it practices one:

- **Labs 0–2** need no AWS at all (local model + mock data).
- **Labs 3–4** only call read-only `Describe*` / `List*` APIs. Use a **least-privilege / ReadOnly** profile for them.
- **Lab 5 can create real, billable resources**, but defaults to a **dry-run** (`DRY_RUN = True`) that creates nothing and just prints the calls it would make. Going live is a deliberate one-line change, and you should do it in a **non-production account**.
- **Lab 7 deletes** whatever Lab 5 created. Always run it after a live Lab 5 so you stop being billed.

### ⚠️ IBM licensing IDs — you must change these

Amazon RDS for Db2 is **bring-your-own-license (BYOL)**. You will not be able to create an RDS for Db2 instance unless a **custom DB parameter group** is attached carrying two IBM licensing values:

- `rds.ibm_customer_id`
- `rds.ibm_site_id`

This tutorial uses **Db2 Community Edition (`db2-ce`) v12.1**, whose license is available from IBM at **no additional cost** — you pay only for the Amazon RDS infrastructure. For using IBM Db2 12.1 Community Edition, you can obtain your IBM customer ID and site ID free of charge on the [Create an IBMid](https://www.ibm.com/account/reg/us-en/signup?formid=urx-54367) page.

**This tutorial ships with placeholder values (`1234567` and `1234567890`) that are only sufficient for practice.** They are **not** valid licensing IDs — replace them with your own (free) Community Edition IDs before any real run. The placeholders are clearly marked in `lab5_provision_gated.py`.

---

## Get the code

Download this tutorial from GitHub into a folder of your choice. The whole tutorial lives under `examples/strands-tutorial`, so grab just that.

**Option A — clone the whole repo (simplest):**

```bash
cd ~                                  # or any folder you like
git clone https://github.com/aws-samples/sample-rds-db2-tools.git
cd sample-rds-db2-tools/examples/strands-tutorial
```

**Option B — download only this folder (sparse checkout):**

```bash
cd ~                                  # or any folder you like
git clone --no-checkout --depth 1 https://github.com/aws-samples/sample-rds-db2-tools.git
cd sample-rds-db2-tools
git sparse-checkout set examples/strands-tutorial
git checkout
cd examples/strands-tutorial
```

You should now be inside `strands-tutorial/` and see `README.md`, `requirements.txt`, `preflight.py`, and the `lab*.py` files. All commands below are run from here.

---

## Setup

```bash
# Run from inside the strands-tutorial/ folder (see "Get the code" above)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt      # installs Strands + the Ollama extra + boto3

# If using Bedrock (default): set your creds + region, and enable a model in the Bedrock console
export AWS_PROFILE=your-profile
export AWS_REGION=us-east-1

# If using Ollama instead (fully local, free):
#   export USE_OLLAMA=1
#   ollama serve                  # in one shell
#   ollama pull llama3.1:latest   # in another shell
```

**Run the preflight check first** — it verifies your environment and prints the exact model-provider class names your installed Strands version exposes, so you never get stuck on an import name:

```bash
python3 preflight.py
```

> **Model selection:** every lab uses the `build_model()` helper in `common.py`. It defaults to Bedrock; set `USE_OLLAMA=1` to switch to local Ollama. The variable is read by *value*, not just presence — `1`, `true`, `yes`, or `on` select Ollama, while `0`, `false`, an empty value, or leaving it unset all mean Bedrock. So `export USE_OLLAMA=0` correctly falls back to Bedrock. The agent code never changes — that's the model-agnostic point, in practice.

> **Bedrock model auto-discovery:** model IDs go end-of-life over time, so the tutorial does **not** hardcode one. On the Bedrock path, `build_model()` asks Bedrock which Claude models are currently active in your region and picks a sensible tutorial default (Sonnet family — balanced and cost-effective — resolved through an inference profile, since the newest models are invoke-only that way). `preflight.py` prints the model it resolved. To pin a specific model instead, set `BEDROCK_MODEL_ID`, e.g.:
> ```bash
> export BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0
> ```
> If auto-discovery can't reach Bedrock (no creds/permissions), it falls back to a default and the first real call will show an actionable error.

> **Ollama model selection:** Ollama has no API to tell you which model is "latest," so there's nothing to auto-discover — you choose and pull a model yourself. The tutorial defaults to `llama3.1:latest`. If that tag is ever renamed, deprecated, or you simply want a different/newer model, browse the full official Ollama repository and find the exact tags (sizes, parameter counts, and quantized versions) on the [Ollama Library](https://ollama.com/library). Pick a tag, pull it (`ollama pull <model>:<tag>`), confirm it downloaded (`ollama list`), then point the tutorial at it:
> ```bash
> export OLLAMA_MODEL=<model>:<tag>   # e.g. llama3.2:3b, qwen2.5:7b, mistral:latest
> ```
> Smaller/quantized tags run faster on modest hardware; larger ones follow instructions and use tools more reliably (which matters for the agent labs). If a pull 404s, the tag no longer exists — check the Library page for the current one.

---

## Running the labs

Work through them **in order**. Read each file *before* running it — the comments explain what to watch for — then run the command shown at the top of each lab section below.

---

## Lab 0 — Your first agent (the 3-line claim, proven)

```bash
python3 lab0_hello_agent.py
```

Gives an agent a `calculator` tool and asks a math question. The agent doesn't compute the answer itself — it **decides to call the tool**, reads the result, and responds. That decision *is* the agentic loop.

**Notice:** you never told it *when* to use the calculator. The model decided. That's "model-driven."

**Exercise:** make the agent chain tool calls. In `lab0_hello_agent.py`, change the question line from:

```python
question = "What is the square root of 1764?"
```

to a two-step question:

```python
question = "What is the square root of 1764, then multiply that by 3?"
```

Re-run `python3 lab0_hello_agent.py` and watch it call the calculator more than once.

---

## Lab 1 — Your first custom tool (the Db2 assistant begins)

```bash
python3 lab1_custom_tool.py
```

Defines a **mock** `get_db2_instance_status()` tool (fake data, no AWS). The agent answers "is my Db2 instance healthy?" by calling your function.

**Notice:** the `@tool` decorator plus the docstring is *all* the model needs to know when and how to call it. The docstring is the contract. The tool list is the agent's blast radius.

**Exercise:** give the agent a second tool and watch it pick the right one. `lab1_custom_tool.py` already contains everything you need as a **3-step exercise** marked in the code:

1. **Uncomment** the `list_db2_backups` tool block above `main()` (remove the leading `# ` from each line).
2. **Register** it — change the agent's tool list to include it:
   ```python
   tools=[get_db2_instance_status, list_db2_backups]
   ```
3. **Change the question** to a backup one:
   ```python
   question = "When was my last backup for db2-dev-01?"
   ```

Re-run `python3 lab1_custom_tool.py` and watch the output show `Tool #1: list_db2_backups` — the model routed to the *backups* tool, not the status tool, purely from the question and the docstrings. (Before you do the exercise, the backup question makes the agent honestly say it has no backup tool — good agent behavior, and the reason Step 2 matters.)

---

## Lab 2 — Watch the loop reason, and put it on a leash

```bash
python3 lab2_the_loop.py
```

Adds a **system prompt** making the assistant strictly read-only: *"You never recommend destructive actions. If asked to delete or modify, refuse and explain."*

**Notice:** ask it to "delete the database" and it refuses — because the prompt said so. The cheapest guardrail you have, and the first one.

**Exercise:** tighten the guardrail and watch behavior change. In `lab2_the_loop.py`, add a rule to the `SYSTEM_PROMPT` string requiring it to name the exact tool it called — for example add this line inside the prompt:

```python
- End every answer with a line: "Source: <exact tool name you called>".
```

Re-run `python3 lab2_the_loop.py` and confirm the answers now end with, e.g., `Source: get_db2_instance_status`. You changed behavior by editing the prompt only — no code logic changed.

---

## Lab 3 — Real AWS, read-only (describe RDS instances)

```bash
python3 lab3_rds_readonly.py      # needs AWS read-only creds
```

A `describe_rds_instances()` tool calls `rds.describe_db_instances()` — a pure read. The agent answers questions about your actual RDS fleet.

**Notice:** the agent is *structurally incapable* of changing anything, because its only AWS tool is a `Describe` call. Control by construction.

**Exercise:** add a read-only `describe_db2_parameter_groups()` tool.

---

## Lab 4 — Two agents are better than one (agents-as-tools)

```bash
python3 lab4_multi_agent.py       # needs AWS read-only creds
```

An **Advisor** (coordinator) treats an **Inspector** (read-only RDS specialist) as a tool. Ask the Advisor to review your fleet; it delegates fact-finding to the Inspector, then reasons over the findings.

**Notice:** each agent has a narrow job and a small toolset. The Advisor can't touch AWS directly — only the Inspector can. Two small blast radii instead of one big one.

**⚠️ Run this lab on Bedrock, not Ollama.** Nested multi-agent orchestration — a coordinator that must correctly *emit a tool call* to invoke another agent — is a genuinely hard reasoning task. In testing, local Ollama models (even a 32B one) print the tool call as **text** (e.g. `{"name": "inspector_agent", ...}`) instead of actually invoking it, so the delegation silently never happens. The single-tool labs (Lab 3, Lab 5) work fine locally; this one needs the stronger model. That's not a bug — it's the series' thesis in miniature: **local-first for routine work, escalate to frontier for the hard reasoning.** Run with `USE_OLLAMA` unset. (The script prints a warning if you run it on Ollama anyway.)

**Exercise:** add a `CostAgent` specialist and fold cost into the advice.

---

## Lab 5 — The human gate + a real two-step provision

```bash
python3 lab5_provision_gated.py   # dry-run by default; can create real resources
```

This is the centerpiece. The agent turns a plain-English request into a concrete **provisioning plan**, then **stops for human approval**. On approval it performs the real, correct two-step create that RDS for Db2 requires:

1. **Create a custom DB parameter group** (family discovered at runtime, e.g. `db2-ce-12.1`) and set `rds.ibm_customer_id` + `rds.ibm_site_id`.
2. **Create the Db2 Community Edition instance**, attaching that parameter group, with encryption, gp3 storage, BYOL, and a Secrets Manager–managed master password (no hardcoded secrets).

**Notice:** the agent plans; a human approves; and the execution respects a real-world dependency (the instance won't provision without the licensed parameter group). This is what "human-in-the-loop before a consequential action" looks like against real infrastructure.

**Engine version is auto-resolved (no stale IDs):** RDS Db2 versions are fully-qualified build strings like `12.1.4.0.sb00085812.r1` — not a short `12.1.4` — and they change over time, each tied to a specific parameter-group family. So Lab 5 does **not** hardcode them: on startup it calls `describe_db_engine_versions` (via `resolve_db2_engine` in `common.py`), picks the newest build, and reads *its* matching family — keeping the two in sync automatically. The script prints what it resolved (`Resolved Db2 engine: version=…, family=…`). Pin specific values with `export DB2_ENGINE_VERSION=…` / `export DB2_PARAM_GROUP_FAMILY=…`, or if discovery can't reach RDS it omits the version so RDS uses its default for the family.

**Networking is auto-resolved too:** creating an RDS instance needs a place to put it. Accounts **without a default VPC** fail with `InvalidSubnet: No default subnet detected in VPC` unless you pass an explicit **DB subnet group** and **security group**. So on a live run Lab 5 calls `resolve_db2_networking` (in `common.py`) to find a usable DB subnet group (one spanning ≥ 2 AZs, preferring a `default*`-named one) and that VPC's `default` security group. In an account that *does* have a default VPC, these come back empty and RDS uses its defaults. Override with `export DB2_DB_SUBNET_GROUP=…` / `export DB2_SECURITY_GROUP_IDS=sg-a,sg-b`.

  **If no subnet group exists** — and there's no default VPC to fall back on — Lab 5 does **not** create one for you. It stops *before* creating anything (so no orphaned parameter group) and prints a ready-to-paste `aws rds create-db-subnet-group` command with two of your subnets (from different AZs) already filled in, plus the `export DB2_DB_SUBNET_GROUP=…` line to set afterward. Create the group once, re-run, and you're set. (This is why the labs "just work" once you've created any RDS instance via the console — RDS auto-creates a subnet group for that VPC, which Lab 5 then reuses.)

**Idempotent:** the parameter-group step is safe to re-run — if the group already exists (e.g. a prior run created it but the instance step then failed), Lab 5 reuses it instead of erroring.

**⚠️ Cost & safety:**
- Defaults to `DRY_RUN = True` — prints the exact calls, creates nothing.
- To provision for real **in a non-prod account**, either edit `DRY_RUN_DEFAULT = False` in the script, or set the environment variable:
  ```bash
  DRY_RUN=false python3 lab5_provision_gated.py
  ```
  Going live requires an explicit false-like value (`false`/`0`/`no`/`off`); anything unset or unrecognized stays in dry-run, so a typo can't accidentally create billable resources. The startup banner shows the mode and where it came from. Even in live mode, nothing is created until you approve at the human gate.
- After any live run, **run `lab7_cleanup.py`** to delete the instance + parameter group so you stop being billed.
- Replace the placeholder IBM IDs with your own before any real use.

**Exercise:** extend `validate_plan()` to reject a single-AZ plan tagged as `prod` — enforcing policy in code, regardless of what the model chose.

---

## Lab 6 — Turn on the lights (observability)

```bash
python3 lab6_observability.py
```

Runs an agent with a `callback_handler` that captures the decision trail — every tool choice, its arguments, and its result. In production you'd export this via **OpenTelemetry (OTEL)** to a backend (Langfuse, Phoenix, CloudWatch, Datadog). Here you print it locally to see what an audit trail actually contains.

**This lab's tool is a MOCK** — it never calls AWS and always reports `available` / single-AZ, echoing back whatever instance name you pass (so "`<name>` is running" proves nothing about your real fleet). That's deliberate: Lab 6 is about the **observability trail**, not the lookup (real lookups are Lab 3). Keeping the tool trivial keeps the captured trace the focus.

**How to read the trace:** the events walk the whole loop — the model *decides* to call a tool (a `toolUse` event), *streams the arguments* token by token (`{"instance_id": ...`), the tool *returns* (a `toolResult` event), then the model *streams the final answer* and stops (`end_turn`), with token/latency `metadata` along the way. That "decided X → called Y with Z → got result → answered" chain, produced for free by attaching one callback, is exactly what an auditor needs. To ship it for real, set the OTEL env vars shown at the bottom of the file — **the agent code doesn't change.**

**Notice:** you're not bolting on logging after the fact — the framework produces the trace.

**Exercise:** capture the Lab 4 multi-agent run and confirm you can see which agent did what, in order.

---

## Lab 7 — Clean up

```bash
python3 lab7_cleanup.py           # delete and wait for completion
python3 lab7_cleanup.py --no-wait # start deletion, don't block; re-run later for the PG
```

Deletes the instance and parameter group Lab 5 created, in the right order (instance first, then parameter group), verifying the `created_by=strands-tutorial` tag before deleting so it only ever removes the tutorial's own resources.

**Run this after any live Lab 5 run** so you stop being billed.

**On timing:** RDS instance deletion takes **several minutes**, so by default the script *blocks while it waits* — that's expected, not a hang. Use `--no-wait` to kick off deletion and exit; then re-run the script once the instance is gone to remove the leftover parameter group (it can't be deleted while an instance still references it). **Re-running is always safe** — it just deletes whatever remains and reports "nothing to delete" for what's already gone.

---

## Lab 8 — Prove it before it ships (evals + chaos)

```bash
pip install strands-agents-evals    # once (or: pip install -r requirements.txt)
python3 lab8_evals.py               # needs Bedrock creds — the judge runs on Bedrock
```

You don't validate an agent by eyeballing one demo — you **measure** it. Lab 8 evaluates the *same* read-only Db2 inspector from Lab 3 using [Strands Evals](https://github.com/strands-agents/evals):

- **8a — Output + trajectory.** An LLM-as-judge scores the answer against a **faithfulness** rubric (did it stay grounded in the tool output, or invent instances?), and a trajectory check confirms it actually *called* the tool instead of answering from thin air.
- **8b — Chaos.** It injects a **tool timeout** and checks the agent **fails closed** — telling the user it couldn't retrieve the data rather than fabricating an answer.

**Notice:** this is the fourth wall of the control envelope — the agent-era analogue of how a bank validates a model. In a real pipeline these become **CI/CD gates** (fail the build if the score drops); on production traces they become **drift detection**. This lab is the hands-on companion to the series' evals deep dive.

> **Notes:** the built-in judge evaluators run on **Bedrock** by default, so use the Bedrock path (not Ollama) and have read-only AWS creds set. Strands Evals is young and evolving — if a class/import name differs from your installed version, the file's header comment shows the two `dir(...)` commands to find the current names; the *shape* (Case → Experiment → evaluators → run → report) is stable.

---

## Where to go next

- Swap the Bedrock model for another (or Ollama) and confirm the agent code doesn't change — the model-agnostic claim, proven.
- Replace a mock tool with a real **MCP server** (e.g., an AWS MCP server) to feel "build once, reuse everywhere."
- Deploy Lab 3's agent behind a small API on **AWS Lambda / Fargate**, or try **Amazon Bedrock AgentCore**, to experience "same code, laptop to production."

---

## Troubleshooting

- `ModuleNotFoundError: strands` → activate the venv and re-run `pip install -r requirements.txt`.
- Bedrock `AccessDeniedException` / model not found → enable model access in the Bedrock console for your region, confirm `AWS_REGION`, or set `USE_OLLAMA=1` to go fully local.
- `NoCredentialsError` (Labs 3–7) → set `AWS_PROFILE` / `AWS_REGION`; confirm with `aws sts get-caller-identity`.
- Lab 5 instance class error → some classes aren't offered for Db2 in every region; try `db.m6i.large`, or list options with `aws rds describe-orderable-db-instance-options --engine db2-ce`.
- Lab 5 engine version error → list current versions with `aws rds describe-db-engine-versions --engine db2-ce` and update `ENGINE_VERSION` / `PARAM_GROUP_FAMILY` in the script to match.
- Lab 7 parameter-group delete fails → the instance deletion is still propagating; wait a minute and re-run.
- Ollama connection refused → run `ollama serve` and confirm the model is pulled (`ollama list`).

---

## License

Provided as a sample under the terms of the repository that hosts it. See the repository root for license details.
