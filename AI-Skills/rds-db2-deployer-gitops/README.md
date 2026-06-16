# rds-db2-deployer-gitops — your RDS for Db2 deployment repo

This is a **ready-to-use GitOps repository template** for deploying Amazon RDS for
Db2 with the `rds-db2-deployer` skill. You do **not** build this from scratch —
you copy it once into your own (enterprise) Git, fill one config file, and from
then on the agent renders deployments into it as pull requests.

## How to use it (one-time setup)

1. **Get a copy into your own Git.** Clone (or fork) this folder, then push it to
   your enterprise GitHub/GitLab as a new repo, e.g. `rds-db2-deployments`:
   ```bash
   # copy just this template out of the skill repo
   git clone https://github.com/aws-samples/sample-rds-db2-tools.git
   cp -R sample-rds-db2-tools/AI-Skills/rds-db2-deployer-gitops my-rds-db2-deployments
   cd my-rds-db2-deployments
   git init && git add -A && git commit -m "Initialize RDS for Db2 GitOps repo"
   git remote add origin https://<your-git-host>/<you>/rds-db2-deployments.git
   git push -u origin main
   ```
2. **Fill `account-defaults.json`** once from the AWS console (view access is
   enough). See the skill's `references/account-defaults.md` and the runbook
   prerequisites step. Real IBM Passport Advantage IDs are required for a real
   apply.
3. **Set the CI variables/secrets** (table below) when you want apply-on-merge.

That's it. From then on you ask the agent ("deploy a dev sandbox"); it renders a
`deployments/<id>/` folder here and opens a PR.

## Layout

```
.
├── account-defaults.json          # the account basics, filled ONCE
├── deployments/
│   └── <db_instance_identifier>/  # ONE folder per instance (agent-rendered)
│       ├── main.tf  security.tf
│       ├── 5-rds/terraform.tfvars  4-parameter-group/terraform.tfvars
│       ├── terraform.tfvars        # sensitive root vars (IBM IDs, etc.)
│       └── deployment-intent.json  # full provenance / audit trail
└── .github/workflows/rds-db2.yml  # gate on PR; plan+apply on merge (opt-in)
```

## The flow

1. Ask the agent → it renders `deployments/<id>/` and opens a **PR**.
2. CI runs the **gate** (`validate_intent` + `policy_gate`, no AWS) on the PR.
3. A human reviews and **merges**.
4. With `RDS_DB2_ENABLE_PLAN=true` + the secrets below, CI runs `terraform apply`
   on merge. (Otherwise apply is run by your operator/CI of choice.)

Each `deployments/<id>/` is an independent Terraform root with its own remote
state key (`rds-db2/<id>/terraform.tfstate`), so many instances never collide.
Modules are pulled from the published release tag — nothing is vendored here.

## CI variables / secrets

| Name | Kind | Purpose |
|---|---|---|
| `AWS_REGION` | variable | deploy region |
| `RDS_DB2_STATE_BUCKET` | variable | S3 state bucket (from `0-backend-setup`) |
| `RDS_DB2_MODULE_REF` | variable | release tag the modules + skill are pinned to (defaults to the published tag) |
| `RDS_DB2_ENABLE_PLAN` | variable | `true` to enable the AWS `plan`/`apply` jobs (Phase B); unset = gate-only |
| `RDS_DB2_DEPLOY_ROLE_ARN` | secret | OIDC role CI assumes to plan/apply |

## Notes

- This template carries **no secrets**. `account-defaults.json` holds account
  identifiers (region, VPC/subnet group/SG, KMS MRK, monitoring role, IBM IDs);
  the master password is RDS-managed in Secrets Manager.
- On a corporate-network Git host, use a self-hosted CI runner that can reach both
  your Git host and AWS. See the runbook's GitOps step.
