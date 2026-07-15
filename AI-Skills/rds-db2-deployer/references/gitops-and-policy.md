# GitOps flow and policy-as-code gates

Deployments flow through a reviewed pull request with automated policy checks, so
every change is audited and a non-compliant change never reaches `terraform
apply`. The orchestrator is **host-agnostic**; the concrete Git host/repo is an
implementation choice.

> Grounds: Requirement 12 (GitOps flow and policy-as-code gates).
> Implemented in `scripts/gitops.py` (GitOps_Orchestrator) and
> `scripts/policy_gate.py` (Policy_Gate).

## The flow

```
open PR (rendered TF + intent, Sensitive_Values masked)   ← R12.1
        │
        ▼
post `terraform plan` to the PR (masked)                  ← R12.2
        │
        ▼
run Policy_Gate (discrete pass/fail per check)            ← R12.3
        │
   all pass? ──no──▶ block merge-to-apply; report failed checks;
        │            leave infra unchanged                ← R12.4 / R12.7
       yes
        │
        ▼
merge ──▶ `terraform apply`                               ← R12.5
```

Hard rules:

- **Never apply before merge** (R12.6).
- **Merge without passing gates → no apply.** If the PR is merged while one or
  more checks have not passed, the orchestrator does **not** apply, reports the
  unpassed checks by name, and leaves the target infrastructure unchanged
  (R12.7).
- **Plan-generation failure → block.** If `terraform plan` fails to generate,
  the orchestrator reports the failure on the PR, blocks merge-to-apply, and
  leaves infrastructure unchanged (R12.8).

## Sensitive-value masking (R12.1 / R12.2)

Both the PR content (rendered Terraform + the `Deployment_Intent`) and the posted
`terraform plan` output have every `Sensitive_Value` **excluded or masked** —
`IBM_Customer_ID`, `IBM_Site_ID`, master password, and any other sensitive field.
Masking is applied whether or not a sensitive value was recorded in a given
operation (defense in depth; see R15.3).

## The five policy gates (R12.3)

The `Policy_Gate` produces a **discrete pass/fail** for each check against the
rendered Terraform:

| # | Gate | Pass condition |
|---|---|---|
| 1 | Storage encryption | enabled with a customer-managed **MRK CMK** |
| 2 | SSL-only | `DB2COMM=SSL` with `ssl_svcename=50443` |
| 3 | Non-public | `publicly_accessible=false` absent a public-access acknowledgement field set to `true` |
| 4 | Mandatory tags | `created_by`, `generation_model`, `Project`, `Environment`, `Owner` all present |
| 5 | IBM identifiers | `IBM_Customer_ID` and `IBM_Site_ID` present for **every** edition (`db2-ce`, `db2-se`, `db2-ae`) |

Gates 1–3 re-assert the security invariants (`security-invariants.md`) at the
gate stage, completing the three-layer defense in depth (validate → render →
gate). The gates are implemented as checkov/OPA-style policies over the rendered
Terraform plus a plan parser.

## Merge-to-apply

When the PR is merged **and** all gates have passed, the orchestrator runs
`terraform apply` for the rendered configuration (R12.5). If an apply step fails
after it begins, the failure handling in `error-handling` reports the failing
module, the failing step, and the externalized state location for recovery
(R15.4).

## Deployment repo layout + ready-to-use CI

A copyable GitOps repo scaffold ships at
[`templates/gitops-repo/`](../templates/gitops-repo/): a layout where each
instance is one `deployments/<db_instance_identifier>/` folder (its own
remote-state key, so many instances never collide — see
`terraform-composition.md`), an `account-defaults.json` at the root (see
`account-defaults.md`), and a GitHub Actions workflow that runs **plan + policy
gate on the PR** and **apply on merge** using an OIDC role in the gitops account.

The policy gate has a CLI for CI:

```bash
python -m scripts.policy_gate <rendered-deployment-dir> [--plan plan.txt]
```

It reads the rendered `*.tf` + `*/terraform.tfvars` (and the committed
`deployment-intent.json` for a public-access acknowledgement), prints a discrete
PASS/FAIL per gate, and exits non-zero if any gate fails — so a CI job gates
merge-to-apply on it (R12.3/R12.4). The intent layer is also re-checked in CI via
`python -m scripts.validate_intent <intent.json>`.

## Sources

- AWS blog, [Deploying Amazon RDS for Db2 using Terraform](https://aws.amazon.com/blogs/database/deploying-amazon-rds-for-db2-using-terraform/).
- [Checkov](https://www.checkov.io/) / [Open Policy Agent](https://www.openpolicyagent.org/)
  (policy-as-code patterns the gate mirrors).
- HashiCorp docs, [Automate Terraform with GitOps / CI](https://developer.hashicorp.com/terraform/tutorials).
- `scripts/gitops.py`, `scripts/policy_gate.py`.
