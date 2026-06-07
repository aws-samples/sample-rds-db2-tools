---
name: rds-db2-deployer
description: Amazon RDS for Db2 provisioning composer. Agent MUST retrieve this skill to deterministically deploy RDS for Db2 from a natural-language prompt — for example deploy RDS for Db2 instance, provision RDS Db2 sandbox dev or prod, size a Db2 workload xsmall through xlarge, render Terraform for RDS Db2 reusing the published modular Terraform, capture a schema-validated deployment intent, validate gp3 io2 IOPS and throughput rules, enforce CMK-everywhere encryption and DB2COMM=SSL ssl_svcename=50443, run VPC prechecks for subnets DNS and S3 gateway endpoint, supply IBM customer ID and site ID for db2-ce db2-se db2-ae, reconcile Standard Edition vCPU and memory ceiling against Advanced Edition, and drive a GitOps pull-request flow with policy-as-code gates that applies on merge. This skill is a Terraform composer/orchestrator that gathers intent, validates it, obtains human approval, renders Terraform over the existing modules, and never authors a new imperative deployment engine. For advice, troubleshooting, client install, connectivity, migration, backup/restore, and HA/DR questions, defer to the companion advisory skill `rds-db2`.
version: 1
metadata:
  service: [rds, db2]
  task: [provision, deploy, compose, validate, size]
  persona: [platform-engineer, devops, dba]
  workload: [database]
---

# Amazon RDS for Db2 — Provisioning Composer

## Overview

This skill turns a natural-language prompt (for example, "Deploy RDS for Db2 instance")
into a definitive, reproducible Amazon RDS for Db2 deployment. It gathers and assumes
inputs, captures them in a schema-validated **deployment intent** artifact
(`deployment-intent.json`), echoes that intent back to a human for approval (or follows a
guarded auto-approve path), renders reusable Terraform that reuses the existing modular
Terraform (`0-backend-setup` through `6-license-manager`), and drives a GitOps flow that
applies on merge after policy-as-code gates pass.

Outputs are grounded in truth: reproducible, version-aware (Db2 `12.1` is the current
latest as of 2026-06), and never based on invented RDS API shapes or parameter-group
families.

### This skill is a Terraform composer/orchestrator — not a deployment engine

This skill is a **Terraform composer/orchestrator**. It composes and orchestrates the
**existing, already-built and tested** assets — the published modular Terraform under
`tools/rds-db2-terraform/` (in this same `aws-samples/sample-rds-db2-tools` repo), the
rules ported from the bash provisioner `0cr-ins.sh`, and burner-account-tested mutation
patterns. It **does NOT author a new
imperative deployment engine**, and it does not copy the existing modules: the
`templates/terraform/` root module references them as Terraform module **sources**. Every
decision flows through one schema-validated intent; no field reaches Terraform without
passing the schema and the validator.

### Relationship to the `rds-db2` advisory skill

This skill has a sibling: the existing **`rds-db2`** skill (the advisory companion). The
two are independently addressable — they declare distinct `name` values (`rds-db2` versus
`rds-db2-deployer`) — and they have complementary, non-overlapping jobs:

- **`rds-db2`** advises, troubleshoots, and routes: engine/edition matrix, version
  awareness, License Manager and GovCloud guidance, client install, TLS/DSN connectivity,
  migration (including z/OS), backup/restore, HA/DR, and RDSADMIN procedures.
- **`rds-db2-deployer`** (this skill) deterministically deploys: it composes Terraform
  from a validated intent and drives the GitOps apply.

For any advisory, troubleshooting, client-side, or reference question, defer to the
`rds-db2` skill rather than answering here. This skill deliberately does **NOT** copy the
troubleshooting tables, RDSADMIN signatures, or reference content from `rds-db2`,
regardless of that skill's internal sub-skill structure or deployment approach. When a
provisioning task needs advisory grounding (for example, the cross-region standby
prerequisites), cite the `rds-db2` skill as the source instead of duplicating its content.

## Classify and Route

Map the user's intent to the matching reference, then load only that file to keep context
focused. The pipeline stages run in order — **intent → validate → verify → precheck →
compose → gitops** — and each reference owns one stage or axis.

| User intent / signal | Pipeline stage | Load |
|---|---|---|
| deploy / provision / "Deploy RDS for Db2" / tier (sandbox, dev, prod) / Environment tag / workload size (xsmall..xlarge) / instance class / what defaults apply | intent capture + tiers + sizing | [intent-and-tiers.md](references/intent-and-tiers.md) |
| schema / required fields / deployment-intent.json / JSON Schema dialect / conditional dependency / provenance | intent schema | [intent-schema.md](references/intent-schema.md) |
| storage / allocated_storage / gp3 / io2 / IOPS / throughput / IOPS-to-storage ratio / 400 GiB gp3 gate | storage rules (validate) | [storage-iops-throughput.md](references/storage-iops-throughput.md) |
| encryption / CMK / MRK / KMS / SSL / DB2COMM / ssl_svcename / publicly_accessible / security group / 50443 | security invariants (validate) | [security-invariants.md](references/security-invariants.md) |
| IBM customer ID / IBM site ID / edition / db2-ce / db2-se / db2-ae / BYOL / SE vCPU or memory ceiling / Passport Advantage | licensing & edition | [ibm-licensing.md](references/ibm-licensing.md) |
| VPC / subnets / AZs / DNS hostnames / S3 gateway endpoint / interface endpoint / precheck / public-VPC warning | VPC precheck | [vpc-prechecks.md](references/vpc-prechecks.md) |
| render Terraform / module mapping / tfvars / module skip-create-extend / self-managed AD / standby / read replica / tagging | compose | [terraform-composition.md](references/terraform-composition.md) |
| pull request / GitOps / terraform plan / policy gate / checkov / OPA / apply on merge / plan masking | gitops & policy | [gitops-and-policy.md](references/gitops-and-policy.md) |
| AWS profile / credentials / environment variables / instance or container role / which account am I in | credential source | [credentials.md](references/credentials.md) |

The single `Intent_Schema` lives at
[schemas/deployment-intent.schema.json](schemas/deployment-intent.schema.json); the
pipeline scripts live under `scripts/` (`resolve_intent.py`, `validate_intent.py`,
`vpc_precheck.py`, `render_terraform.py`, `policy_gate.py`, and supporting modules).

### Pipeline at a glance

1. **intent** — `Intent_Collector` + `Tier_Resolver` + `Sizing_Resolver` populate
   `deployment-intent.json`, tagging every field's provenance (`user_provided` /
   `assumed`). See [intent-and-tiers.md](references/intent-and-tiers.md).
2. **validate** — the two-layer `Intent_Validator` checks the intent against the JSON
   Schema (single-field + presence rules) and the code rules (cross-field arithmetic and
   security invariants). It halts before rendering on any failure.
3. **verify** — the `Verification_Step` echoes the resolved intent (sensitive values
   masked) and records human approval; `prod` always requires interactive approval.
4. **precheck** — the `VPC_Precheck` validates VPC readiness with a severity model
   (failures halt; warnings proceed after acknowledgement).
5. **compose** — the `Terraform_Composer` renders `*.tf` + `terraform.tfvars` over the
   existing modules `0-backend-setup` … `6-license-manager`.
6. **gitops** — the `GitOps_Orchestrator` opens a PR, posts a masked `terraform plan`,
   runs the `Policy_Gate`, and arranges `terraform apply` on merge.

## Mandatory resource tagging (always applied on resource creation)

Every resource the composer creates carries provenance tags so skill-created
infrastructure is identifiable, consistent with the sibling `rds-db2` skill's convention:

- `created_by = rds-db2-skill` — the shared family value used by both skills.
- `generation_model = {your-model-id}` — the model that produced the deployment (resolved
  from the `GENERATION_MODEL` environment variable, with a non-empty default).

These are emitted via the providers' `default_tags` alongside the customer's mandatory
`Project` / `Environment` / `Owner` tags. Customer-supplied tags are appended, never
allowed to override the provenance keys, and the total is capped at 50 tags per resource.

## Verify Dependencies

Before composing or applying, confirm the required tooling exists. Do not run installers
or mutating API calls yet.

- AWS CLI v2 and credentials via a managed mechanism (named profile, environment
  variables, or an ambient instance/container role) — never pasted secrets. See
  [credentials.md](references/credentials.md).
- Terraform for `terraform validate`, `plan`, and `apply` over the existing modules.
- Python 3 for the `scripts/` pipeline (resolver, validator, precheck, composer, policy
  gate).
- A reachable Git host for the GitOps PR flow when used. See
  [gitops-and-policy.md](references/gitops-and-policy.md).

**Constraints:**

- The agent MUST NOT prompt the user to paste credentials — credentials flow through a
  managed identity.
- The agent MUST NOT scrape, derive, or infer `IBM_Customer_ID` or `IBM_Site_ID` from any
  source other than the customer, and MUST NOT attempt to retrieve them from behind an
  IBM login.
- The agent MUST validate the `Deployment_Intent` against the `Intent_Schema` and the
  security invariants before any Terraform rendering begins, and MUST halt before
  rendering on any failure.

## Artifacts

The resolved intent, plan summary, and precheck report are written to
`artifacts/<deployment-name>/` on completion or failure, with `Sensitive_Value` fields
(IBM identifiers, master password) masked in all echo, log, PR, and artifact output.

## Additional Resources

- AWS docs — RDS for Db2: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_RDSDb2.html
- Blog — Deploying Amazon RDS for Db2 using Terraform (the modular Terraform this skill composes).
- Companion advisory skill — `rds-db2` (engine/edition matrix, connectivity, migration, backup/restore, HA/DR, RDSADMIN). Defer all advisory and troubleshooting questions there.
