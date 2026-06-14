# RDS for Db2 GitOps deployment repo (template)

Copy this folder to start the repository your team uses to deploy RDS for Db2
instances the GitOps way: **the agent proposes (opens a PR), a human disposes
(reviews + merges), and CI executes (`terraform apply` on merge).**

This repo holds **rendered deployments + config only** — never the Terraform
modules. Each rendered `main.tf` references the published modules by a pinned
git tag, so CI pulls them at `terraform init`; you do not vendor them here.

## Layout

```
.
├── account-defaults.json          # the account basics, filled ONCE (see the skill's account-defaults.md)
├── deployments/
│   └── <db_instance_identifier>/  # ONE folder per instance (this is how multi-instance works)
│       ├── main.tf                # module blocks (sources pinned to a git tag) + S3 backend
│       ├── security.tf            # SSL-only 50443 ingress
│       ├── 5-rds/terraform.tfvars
│       ├── 4-parameter-group/terraform.tfvars
│       └── deployment-intent.json # the resolved intent (audit trail; sensitive values masked elsewhere)
└── .github/workflows/rds-db2.yml  # plan+gate on PR, apply on merge
```

## Why one folder per instance (multi-instance)

Every deployment has its own remote-state key, set in its `main.tf` backend
block:

```
key = "rds-db2/<db_instance_identifier>/terraform.tfstate"
```

So instances never collide. "Deploy another" = the agent renders a **new**
`deployments/<id>/` folder in a new PR. CI plans/applies only the deployment
folders that changed in a given PR, so unrelated instances are untouched.

## The flow

1. You ask the agent to deploy (e.g. "deploy a dev sandbox"). It loads
   `account-defaults.json`, resolves + validates the intent, and renders a new
   `deployments/<id>/` folder.
2. The agent opens a **PR** with those files and a **masked** `terraform plan`.
3. CI runs on the PR: `validate_intent` → `terraform plan` → the **policy gate**
   (5 discrete checks). All must pass.
4. A human reviews and **merges**.
5. CI on merge to `main` runs `terraform apply` for each changed deployment
   folder, using the gitops account's OIDC role.

Never applied before merge; a merge without passing gates does not apply.

## One-time setup (gitops/CI account, full access)

These are prerequisites the **gitops account** does once — view-only engineers
never need the CLI:

1. **State backend** — apply `0-backend-setup` once to create the S3 state
   bucket + DynamoDB lock table. Put the bucket name in the repo variable
   `RDS_DB2_STATE_BUCKET`.
2. **Foundational infra** — if your account has no subnet group / MRK KMS key /
   monitoring role yet, apply `1-networking` / `3-kms` / `2-iam` once, then
   record the resulting names/ARNs in `account-defaults.json` (see the skill's
   `references/account-defaults.md`). Per-deployment PRs reuse these.
3. **OIDC role** — create the IAM role CI assumes (full access in this account)
   and set its ARN in the repo secret `RDS_DB2_DEPLOY_ROLE_ARN`. Set the region
   in the repo variable `AWS_REGION`.
4. **Module tag** — ensure the rendered `source` lines point at a real release
   tag of `sample-rds-db2-tools` (set `RDS_DB2_MODULE_REF` when rendering), not
   the `v0.0.0` placeholder.

## Repo variables / secrets the workflow expects

| Name | Kind | Purpose |
|---|---|---|
| `AWS_REGION` | variable | region for the deploy account |
| `RDS_DB2_STATE_BUCKET` | variable | S3 bucket from `0-backend-setup` |
| `RDS_DB2_DEPLOY_ROLE_ARN` | secret | OIDC role CI assumes to plan/apply |
| `RDS_DB2_MODULE_REF` | variable | git tag the module sources + skill install are pinned to (falls back to `main` if unset) |
| `RDS_DB2_ENABLE_PLAN` | variable | set to `true` to enable the AWS `plan`/`apply` jobs (Phase B). Unset = gate-only (Phase A, no AWS) |

## Two phases

- **Phase A (no AWS):** PRs run `discover` + an AWS-free **`gate`** job
  (`validate_intent` + `policy_gate`). This is the required green check and needs
  no secrets — just the published skill. Leave `RDS_DB2_ENABLE_PLAN` unset.
- **Phase B (AWS):** set `RDS_DB2_ENABLE_PLAN=true` and the secrets/variables
  above; PRs also run `terraform plan` (masked, posted to the PR) and merges run
  `terraform apply`.
