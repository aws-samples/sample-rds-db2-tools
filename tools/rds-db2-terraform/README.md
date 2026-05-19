# Amazon RDS for Db2 — Terraform

Enterprise-grade, modular Terraform setup for deploying Amazon RDS for Db2 with full audit trail through Git PRs.

> **Companion to:** [Deploying Amazon RDS for Db2 using Terraform](https://aws.amazon.com/blogs/database/deploying-amazon-rds-for-db2-using-terraform/) (AWS Database Blog)
>
> **Source of truth:** [`aws-samples/sample-rds-db2-tools`](https://github.com/aws-samples/sample-rds-db2-tools/tree/main/tools/rds-db2-terraform)

## Why Terraform?

- All changes reviewed via GitHub PRs — full audit of what, when, and why
- Idempotent — safe to re-run; only changes what has drifted
- Modular — deploy only what you need; skip modules you already have
- State-locked — DynamoDB prevents concurrent modifications

## Architecture

```
0-backend-setup/      S3 + DynamoDB for remote state (one-time)
1-networking/         DB subnet group + S3 gateway endpoint + optional interface endpoints
2-iam/                Monitoring role, S3 restore role, directory service role, audit role
3-kms/                Customer-managed KMS key (multi-region capable)
4-parameter-group/    DB2 parameter group with IBM customer ID + site ID
5-rds/                RDS for Db2 instance + Secrets Manager entry
6-license-manager/    AWS License Manager — self-managed IBM Db2 license tracking
```

## Tag Propagation

Every module uses `default_tags` on the AWS provider. This means **every resource** in every module automatically receives these four tags without any per-resource repetition:

| Tag | Source variable | Example |
|-----|----------------|---------|
| `Project` | `tag` | `MYPROJECT` |
| `ManagedBy` | hardcoded | `Terraform` |
| `Environment` | `environment` | `prod` |
| `Owner` | `owner` | `db-team` |

Set the same values in every module's `terraform.tfvars` to keep tags consistent across all resources. The `configure-modules.sh` script wires the backends — you still need to copy the tag values manually into each tfvars (or use a CI/CD pipeline that injects them as `-var` flags).

## User-Supplied Parameters

| Parameter | Where | Description |
|-----------|-------|-------------|
| `vpc_id` | 1-networking, 5-rds | VPC to deploy into |
| `security_group_id` | 1-networking, 5-rds | Security group |
| `ibm_customer_id` | 4-parameter-group | IBM customer ID for licensing |
| `ibm_site_id` | 4-parameter-group | IBM site ID for licensing |
| `multi_az` | 5-rds | `true` for Multi-AZ, `false` for single-AZ |
| `publicly_accessible` | 1-networking, 5-rds | Public vs private access |
| `engine` | 5-rds | `db2-se` or `db2-ae` |
| `engine_version` | 5-rds | Full version string (e.g. `11.5.9.0`) |
| `instance_class` | 5-rds | e.g. `db.r6i.2xlarge` |
| `storage_type` | 5-rds | `gp3`, `io1`, or `io2` |
| `kms_key_arn` | 3-kms, 5-rds | Leave empty to auto-create |
| `directory_id` | 5-rds | AWS Managed AD directory ID (leave empty for local auth) |
| `restore_from_s3` | 5-rds, 2-iam | `true` to attach S3 restore role |
| `enable_audit` | 5-rds, 2-iam | `true` to enable DB2 audit logging |
| `db2_edition` | 6-license-manager | `SE` or `AE` — must match `engine` in 5-rds |
| `license_count` | 6-license-manager | vCPU count of your instance class |
| `db_instance_arn` | 6-license-manager | ARN from 5-rds output |

## Quick Start

### Step 1 — Backend (one-time per AWS account/region)

```bash
cd 0-backend-setup
cp terraform.tfvars.example terraform.tfvars
# Edit: set state_bucket_name to a globally unique name, set environment/owner
terraform init
terraform apply -auto-approve
```

### Step 2 — Configure all modules

```bash
./configure-modules.sh
```

### Step 3 — Deploy modules in order

```bash
for module in 1-networking 2-iam 3-kms 4-parameter-group 5-rds 6-license-manager; do
  cd $module
  cp terraform.tfvars.example terraform.tfvars
  # Edit terraform.tfvars — fill in your values
  terraform init && terraform apply -auto-approve
  cd ..
done
```

## Module Skip Guide

| Already have | Action |
|---|---|
| Existing subnet group | Set `db_subnet_group_name` in 1-networking |
| Existing KMS key | Set `kms_key_arn` in 3-kms |
| Existing parameter group with IBM IDs | Set `parameter_group_name` in 4-parameter-group |
| Existing monitoring role | Set `monitoring_role_name` in 2-iam |
| No AD/Kerberos | Leave `directory_id = ""` in 5-rds |
| No S3 restore | Leave `restore_from_s3 = false` in 5-rds |
| No audit | Leave `enable_audit = false` in 5-rds |

## Auth Options

### Local auth (default)
Leave `directory_id = ""` in `5-rds/terraform.tfvars`.

### AWS Managed AD
1. Set `create_directory_role = true` in `2-iam/terraform.tfvars`
2. Set `directory_id = "d-xxxxxxxxxx"` and `directory_role_name = "rds-db2-directory-service-access-role"` in `5-rds/terraform.tfvars`

## License Manager (module 6)

Per the [AWS RDS for Db2 licensing docs](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/db2-licensing.html), customers using BYOL track their IBM Db2 licenses in AWS License Manager via a self-managed license.

**One-time prerequisite per AWS account + region** — run once before `terraform apply`:

```bash
cd 6-license-manager
AWS_PROFILE=<yours> AWS_REGION=<yours> ./bootstrap.sh
```

This creates the `AWSServiceRoleForAWSLicenseManagerRole` service-linked role and verifies the License Manager service is reachable. Without this, the first `terraform apply` will fail with `AccessDeniedException: Service role not found`.

Module 6 then creates an `aws_licensemanager_license_configuration` with:
- `LicenseCountingType = vCPU`
- A `product_information_list` filter on `Engine Edition = db2-se` (or `db2-ae`, `db2-ce`) attached post-create via AWS CLI (the AWS Terraform provider doesn't expose this block as of 6.x)

License Manager auto-discovers RDS for Db2 instances matching the product filter. Discovery can take up to 24 hours. No explicit resource association is required (and isn't supported for RDS ARNs).

Set `license_count` to the vCPU count of your instance class:

| Instance class | vCPUs |
|---|---|
| db.r6i.xlarge | 4 |
| db.r6i.2xlarge | 8 |
| db.r6i.4xlarge | 16 |
| db.r6i.8xlarge | 32 |

Deploy module 6 **after** module 5 so the RDS ARN is available.

## Cleanup

```bash
./cleanup.sh
```

## Notes

- This Terraform setup targets **public AWS regions only** (not QA/internal endpoints)
- IBM customer ID and site ID are marked `sensitive = true` — they will not appear in plan output
- When `manage_master_user_password=true` (default), RDS manages the master password in Secrets Manager — the ARN is exported as `managed_master_user_secret_arn`
- `default_tags` on the provider is the single source of truth for tags — no per-resource tag blocks needed
