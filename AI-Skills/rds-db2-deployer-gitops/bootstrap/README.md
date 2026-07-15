# bootstrap/ — GitHub OIDC + deploy role (one-time, per account)

This Terraform stands up what the CI's **plan/apply** jobs need to reach AWS
**without long-lived keys**:

- a **GitHub OIDC provider** (`token.actions.githubusercontent.com`), and
- an **IAM role** whose trust policy only lets *this* repo's Actions assume it —
  scoped to `pull_request` events (the `plan` job) and pushes to the default
  branch (the `apply` job).

You run it **once per AWS account**, with your **own admin credentials**, outside
CI. The CI deploy role it creates cannot modify this bootstrap (local state).

## Use

```bash
cd bootstrap
cp terraform.tfvars.example terraform.tfvars   # then edit it
terraform init
terraform apply                                 # review the plan, then approve
```

Then wire the repo (Settings → Secrets and variables → Actions):

| Name | Kind | Value |
|---|---|---|
| `RDS_DB2_DEPLOY_ROLE_ARN` | secret | the `deploy_role_arn` output |
| `RDS_DB2_ENABLE_PLAN` | variable | `true` |
| `AWS_REGION` | variable | your region |
| `RDS_DB2_STATE_BUCKET` | variable | your Terraform state bucket |
| `RDS_DB2_MODULE_REF` | variable | a release tag, e.g. `rds-db2-deployer-v0.3.3` |

Now a PR that adds a `deployments/<id>/` folder runs **`terraform plan`**
(read-only); merging runs **`terraform apply`**.

## Recommended: gate apply with a manual approval

Add a GitHub **Environment** (e.g. `production`) with a required reviewer and put
the `apply` job in it, so a merge does not silently create billable resources —
you click approve first. See the runbook's CI step.

## Notes

- **Personal/sandbox accounts:** this is the standard AWS customer pattern. Keep
  it to an account you own; the role's policy is broad-for-test (it can reuse or
  create networking/KMS/monitoring) — **tighten for production** (pin ARNs, split
  a read-only plan role from the apply role, drop create rights you don't use).
- **Existing OIDC provider:** if the account already has the GitHub provider, set
  `create_oidc_provider = false` and pass `existing_oidc_provider_arn`.
- This root keeps **local** Terraform state on purpose; don't commit it
  (`*.tfstate` is gitignored).
