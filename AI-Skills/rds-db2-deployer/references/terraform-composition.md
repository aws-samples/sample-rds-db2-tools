# Terraform composition over the existing modules

The skill is a **Terraform composer/orchestrator**, not a fourth deployment
engine. It renders `*.tf` and `terraform.tfvars` that **reuse** the existing
modular Terraform (`0-backend-setup` through `6-license-manager`) published as
the AWS blog "Deploying Amazon RDS for Db2 using Terraform". It never authors a
parallel imperative deployer (R10.1).

> Grounds: Requirement 10 (Terraform composition over existing modules) and
> Requirement 13 (optional capabilities for verbose prompts).
> Implemented in `scripts/render_terraform.py`; the root module and tfvars
> templates live under `templates/terraform/`.

## How composition works

The `templates/terraform/` root module references the existing
`RDS-Db2-Terraform/` modules as Terraform **module sources** — it does not copy
them. For each module whose resources the resolved intent enables, the composer
produces a `terraform.tfvars` populated from the intent (R10.2). Every intent
field maps to a **real** module variable defined in that module's `variables.tf`
(R10.3). If a field intended for a module has **no** corresponding variable, the
composer halts, reports the unmapped field and target module by name, and never
fabricates a variable name (R10.4).

## Intent → module-variable mapping

Grounded in the actual `variables.tf` files. (The edition string differs per
module: `engine` / `engine_edition` (ce/se/ae) / `db2_edition` (CE/SE/AE).)

| Intent field | Module(s) | Module variable | Notes |
|---|---|---|---|
| `engine` | 5-rds, 4-parameter-group, 6-license-manager | `engine`; `engine_edition`; `db2_edition` | edition string per module |
| `engine_version` | 5-rds | `engine_version` | blank → module auto-resolves via `aws_rds_engine_version` |
| major version | 5-rds, 4-parameter-group | `engine_major_version` | drives param-group family |
| `instance_class` | 5-rds | `instance_class` | |
| `workload_size` | 5-rds | `db_size_label` (xs/s/m/l/xl) | feeds the identifier builder |
| `allocated_storage` | 5-rds | `allocated_storage` | |
| `storage_type` | 5-rds | `storage_type` | composer emits only gp3/io2 |
| `iops` | 5-rds | `iops` | module gates via `can_set_iops` |
| `storage_throughput` | 5-rds | `storage_throughput` | gp3 ≥ 400 only |
| `multi_az` | 5-rds | `multi_az` | |
| `availability_zone` | 5-rds | `availability_zone` | single-AZ only |
| `db_name` | 5-rds | `db_name` | ≤ 8 chars |
| `master_username` | 5-rds | `master_username` | |
| managed password | 5-rds | `manage_master_user_password` | true by default |
| `port` | 5-rds | `db2_port` | TCP listener (dormant) |
| `backup_retention_period` | 5-rds | `backup_retention_period` | |
| `publicly_accessible` | 5-rds, 1-networking | `publicly_accessible` | |
| `storage_encrypted` | 5-rds | `storage_encrypted` | always true |
| `kms_key_id` | 5-rds, 3-kms | `kms_key_arn` | MRK CMK |
| `db_subnet_group_name` | 5-rds, 1-networking | `db_subnet_group_name` | |
| `db_parameter_group_name` | 5-rds, 4-parameter-group | `parameter_group_name` | |
| `monitoring_interval` | 5-rds | (literal in module = 15) | enhanced monitoring on |
| `monitoring_role_arn` | 5-rds, 2-iam | `monitoring_role_arn` / `monitoring_role_name` | |
| `ibm_customer_id` / `ibm_site_id` | 4-parameter-group | `ibm_customer_id` / `ibm_site_id` | sensitive; all editions |
| audit | 5-rds, 2-iam | `enable_audit`, `audit_role_arn`, `audit_bucket_name`, `create_audit_role` | option group |
| S3 restore | 5-rds, 2-iam | `restore_from_s3`, `s3_integration_role_arn`, `create_s3_role` | |
| AWS Managed AD | 5-rds, 2-iam | `directory_id`, `directory_role_name`, `create_directory_role` | |
| tags | provider `default_tags` | `tag`, `environment`, `owner` | + `created_by`, `generation_model` |

## Module skip / create / extend guide (R10.5/10.6/10.7)

For each reusable resource — subnet group, KMS key, parameter group, monitoring
role:

- **Reuse (skip create):** the intent names an existing resource → set the reuse
  variable (`db_subnet_group_name`, `kms_key_arn`, `parameter_group_name`,
  `monitoring_role_name`) and **skip** rendering the module to create it (R10.5).
- **Create:** the resource does not yet exist → render the module to create it
  (R10.6).
- **Extend:** the resolved capability needs a variable or resource the module
  does not yet define → **extend the module in place**, backward-compatibly, so
  intents that do not use the new capability render unchanged (R10.7, R13.16).

### Gaps requiring module extension (confirmed against `5-rds/variables.tf`)

1. **Self-managed AD (R13.4):** add `domain_fqdn`, `domain_ou`,
   `domain_auth_secret_arn`, `domain_dns_ips` to `5-rds` + IAM role wiring in
   `2-iam` (the module only has `directory_id`/`directory_role_name` for AWS
   Managed AD).
2. **Cross-region mounted standby replica (R13.2):** add an `aws_db_instance`
   replica (mounted mode) + cross-region provider alias, target-region param
   group, and MRK key prerequisites.
3. **Read replica (R13.15):** add a same-region read-replica resource referencing
   the source.
4. **`storage_type` validation:** the module allows `gp3/io1/io2`; the composer
   must never emit `io1` (R18.1) — tighten module validation to `gp3/io2`.
5. **`deletion_protection`:** module hardcodes `false`; expose as a variable so
   the `prod` tier can set `true` (R3.5).
6. **`db2_port` default:** module default is `8392`; the composer passes the
   resolved `port` explicitly.
7. **CMK-everywhere (R6.10):** master-user-secret and audit/S3 buckets must take
   a CMK; add `master_user_secret_kms_key_id` and bucket CMK wiring where missing.

These three capabilities — self-managed AD, cross-region standby, read replica —
must be added to the modules **before** the corresponding optional capability is
rendered, consistent with the unmapped-variable halt in R10.4 (R13.16).

## Optional capabilities (R13)

Verbose prompts can request Multi-AZ (R13.1), cross-region mounted standby
(R13.2, requires backups + target-region param group + target-region KMS),
AWS Managed AD (R13.3) or self-managed AD/Kerberos (R13.4, all four
`domain_*` params + IAM role), Db2 audit to S3 (R13.5/13.10 — `DB2_AUDIT` option
group with `IAM_ROLE_ARN`/`S3_BUCKET_ARN`, scoped IAM role, pre-existing bucket
required), BYOK (R13.6, MRK CMK only), S3 restore (R13.7), License Manager
(R13.8), and read replica (R13.15). A capability that conflicts with another
field or a security invariant is flagged by name and halts — never silently
skipped (R13.9).

## Self-describing identifier (R20)

The `5-rds` module already builds the self-describing identifier (`_auto_id` in
`main.tf`). The composer passes `db_instance_identifier=""` to use it, or the
customer's override; the variable stays exposed for a tf-level override (R20.5).
See `intent-and-tiers.md` for the identifier components.

## Verification gates

Every rendered config must pass:

- **`terraform validate`** with a zero exit status and zero errors, for all valid
  intents (R10.8).
- **`terraform plan` idempotence** — a second plan against an unchanged applied
  state reports 0 to add, 0 to change, 0 to destroy (R10.9).

## Sources

- AWS blog, [Deploying Amazon RDS for Db2 using Terraform](https://aws.amazon.com/blogs/database/deploying-amazon-rds-for-db2-using-terraform/)
  (the published modular Terraform this composer reuses).
- The module `variables.tf` files under `04-db2-client/RDS-Db2-Terraform/`
  (`0-backend-setup` … `6-license-manager`).
- Bash provisioner `0cr-ins.sh` (audit option group, identifier builder).
- `scripts/render_terraform.py`, `templates/terraform/`.
