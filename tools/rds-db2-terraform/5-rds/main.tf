terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
      # The default `aws` provider drives the primary instance (configured by the
      # provider block below). `aws.replica` is a cross-region provider alias that
      # callers MUST pass when create_standby_replica=true so the mounted standby
      # replica is created in the target (DR) region. Declared here as a
      # configuration alias so the module can accept the passed provider; it has no
      # effect when create_standby_replica is false (the default).
      configuration_aliases = [aws.replica]
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = local.default_resource_tags
  }
}

# Mandatory tag set (R14) applied to every created resource via default_tags.
# Customer-supplied extra_tags are merged FIRST so the mandatory keys, merged
# last, always win — an extra tag can never override a mandatory key (R14.4).
locals {
  default_resource_tags = merge(
    var.extra_tags,
    {
      Project     = var.tag
      ManagedBy   = "Terraform"
      Environment = var.environment
      Owner       = var.owner
    },
    var.created_by != "" ? { created_by = var.created_by } : {},
    var.generation_model != "" ? { generation_model = var.generation_model } : {},
  )
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

# Auto-resolve the latest engine version when engine_version is blank.
# `latest = true` collapses multiple 11.5.x matches to the newest.
data "aws_rds_engine_version" "latest" {
  count   = var.engine_version == "" ? 1 : 0
  engine  = var.engine
  version = var.engine_major_version
  latest  = true
}

locals {
  resolved_engine_version = var.engine_version != "" ? var.engine_version : data.aws_rds_engine_version.latest[0].version

  # Build identifier mirroring 0cr-ins.sh build_db_identifier_default:
  #   {eng}-{major-ver}-{instance-abbr}-{size}-{storage}-{az-abbr}-{iops}k-{tag}
  _eng_abbr = replace(var.engine, "-", "")                # db2-se -> db2se
  _ver_abbr = replace(var.engine_major_version, ".", "-") # 11.5   -> 11-5
  _inst_abbr = replace(
    replace(
      replace(
        replace(
          replace(var.instance_class, "db.", ""),
          "xlarge", "xl"
        ),
        "large", "l"
      ),
      "medium", "m"
    ),
    ".", "-"
  )
  _az_abbr     = var.multi_az ? "maz" : "saz"
  _iops_suffix = var.iops > 0 ? "-${floor(var.iops / 1000)}k" : ""
  _auto_id     = lower("${local._eng_abbr}-${local._ver_abbr}-${local._inst_abbr}-${var.db_size_label}-${var.storage_type}-${local._az_abbr}${local._iops_suffix}-${var.tag}")

  db_identifier = var.db_instance_identifier != "" ? var.db_instance_identifier : local._auto_id

  # IOPS/throughput rules:
  #   gp3  → allowed only when allocated_storage ≥ 400 GiB (baseline 3000/125 below that)
  #   io2  → IOPS is required; always allowed
  can_set_iops = (
    var.storage_type == "gp3" && var.allocated_storage >= 400
    ) || (
    var.storage_type == "io2"
  )

  # Directory authentication mode (mutually exclusive):
  #   AWS Managed AD  → directory_id set, wired via domain/domain_iam_role_name
  #   self-managed AD → domain_fqdn set, wired via domain_fqdn/domain_ou/
  #                     domain_auth_secret_arn/domain_dns_ips (+ domain_iam_role_name)
  use_aws_managed_ad  = var.directory_id != ""
  use_self_managed_ad = var.domain_fqdn != ""
}

# ── Option Group for DB2 Audit (optional) ────────────────────────────────────

resource "aws_db_option_group" "audit" {
  count                    = var.enable_audit ? 1 : 0
  name                     = "rds-db2-audit-og-${lower(var.tag)}"
  engine_name              = var.engine
  major_engine_version     = var.engine_major_version
  option_group_description = "DB2 Audit option group - ${var.tag}"

  option {
    option_name = "DB2_AUDIT"
    option_settings {
      name  = "IAM_ROLE_ARN"
      value = var.audit_role_arn
    }
    option_settings {
      name  = "S3_BUCKET_ARN"
      value = "arn:${data.aws_partition.current.partition}:s3:::${var.audit_bucket_name}"
    }
  }
}

# ── RDS for Db2 Instance ─────────────────────────────────────────────────────

resource "aws_db_instance" "this" {
  identifier     = local.db_identifier
  engine         = var.engine
  engine_version = local.resolved_engine_version
  instance_class = var.instance_class
  license_model  = "bring-your-own-license"

  allocated_storage = var.allocated_storage
  storage_type      = var.storage_type
  # gp3 quirk: IOPS/throughput may only be set when storage ≥ 400 GiB.
  # Below that threshold RDS uses baseline 3000 IOPS / 125 MB/s automatically.
  iops               = local.can_set_iops ? var.iops : null
  storage_throughput = local.can_set_iops && var.storage_throughput > 0 ? var.storage_throughput : null
  storage_encrypted  = var.storage_encrypted
  kms_key_id         = var.storage_encrypted ? var.kms_key_arn : null

  db_name                     = var.db_name
  username                    = var.master_username
  password                    = var.manage_master_user_password ? null : var.master_password
  manage_master_user_password = var.manage_master_user_password ? true : null
  # Encrypt the RDS-managed master-user secret with a customer-managed CMK rather
  # than the aws/secretsmanager AWS-managed default key (R6.10/R6.12). Only valid
  # when RDS manages the secret and a CMK is supplied; null otherwise so direct
  # callers that omit a CMK keep the prior behavior.
  master_user_secret_kms_key_id = var.manage_master_user_password && var.master_user_secret_kms_key_id != "" ? var.master_user_secret_kms_key_id : null
  port                          = var.db2_port

  db_subnet_group_name   = var.db_subnet_group_name
  vpc_security_group_ids = [var.security_group_id]
  publicly_accessible    = var.publicly_accessible
  availability_zone      = var.multi_az ? null : var.availability_zone
  multi_az               = var.multi_az

  parameter_group_name = var.parameter_group_name
  option_group_name    = var.enable_audit ? aws_db_option_group.audit[0].name : null

  backup_retention_period = var.backup_retention_period
  skip_final_snapshot     = true
  deletion_protection     = var.deletion_protection

  monitoring_interval = 15
  monitoring_role_arn = var.monitoring_role_arn

  enabled_cloudwatch_logs_exports = ["diag.log", "notify.log"]

  # AWS Managed AD (existing behavior). domain_iam_role_name is shared with the
  # self-managed path below; only one mode is active at a time.
  domain               = local.use_aws_managed_ad ? var.directory_id : null
  domain_iam_role_name = (local.use_aws_managed_ad || local.use_self_managed_ad) && var.directory_role_name != "" ? var.directory_role_name : null

  # Customer self-managed AD / Kerberos.
  domain_fqdn            = local.use_self_managed_ad ? var.domain_fqdn : null
  domain_ou              = local.use_self_managed_ad ? var.domain_ou : null
  domain_auth_secret_arn = local.use_self_managed_ad ? var.domain_auth_secret_arn : null
  domain_dns_ips         = local.use_self_managed_ad ? var.domain_dns_ips : null

  tags = { Name = local.db_identifier }

  timeouts {
    create = "120m"
    update = "90m"
    delete = "60m"
  }
}

# ── S3 Integration Role Attachment (for backup restore) ──────────────────────

resource "aws_db_instance_role_association" "s3" {
  count                  = var.restore_from_s3 && var.s3_integration_role_arn != "" ? 1 : 0
  db_instance_identifier = aws_db_instance.this.identifier
  feature_name           = "S3_INTEGRATION"
  role_arn               = var.s3_integration_role_arn
}

# ── Cross-Region Mounted Standby Replica (optional, DR) ───────────────────────
#
# Creates a cross-region replica of the primary RDS for Db2 instance in a second
# region, using RDS for Db2 "mounted" replica mode. This resource is fully gated
# by var.create_standby_replica and renders nothing when that flag is false
# (the default), so existing configurations are unaffected.
#
# Prerequisites enforced/assumed by the composer before this is rendered:
#   1. Source automated backups MUST be enabled — the primary's
#      backup_retention_period MUST be > 0. RDS cannot create a replica from a
#      source that has no automated backups. (The Intent_Validator rejects the
#      standby + backup_retention_period=0 combination per R13.13; the precondition
#      block below also fails fast at plan/apply time as defense in depth.)
#   2. A target-region DB parameter group MUST already exist in the replica region
#      and be passed via var.standby_parameter_group_name. A parameter group is
#      region-scoped, so the primary's parameter group cannot be reused here.
#   3. A target-region customer-managed MRK CMK MUST exist in the replica region
#      and be passed via var.standby_kms_key_arn. Cross-region encrypted replicas
#      require a KMS key in the destination region (an MRK replica key satisfies
#      the CMK-everywhere security invariant).
#
# The provider = aws.replica meta-argument places this instance in the target
# region; the caller supplies that aliased provider configured for the DR region.

resource "aws_db_instance" "standby_replica" {
  count    = var.create_standby_replica ? 1 : 0
  provider = aws.replica

  # Cross-region replica source must be the full ARN of the primary instance.
  replicate_source_db = aws_db_instance.this.arn

  identifier     = var.standby_replica_identifier != "" ? var.standby_replica_identifier : "${local.db_identifier}-standby"
  instance_class = var.standby_instance_class != "" ? var.standby_instance_class : var.instance_class

  # RDS for Db2 cross-region replicas are created in mounted (standby) mode:
  # the replica database is mounted for recovery/DR and is not open for read
  # traffic. This is distinct from a same-region read replica.
  replica_mode = "mounted"

  # Target-region parameter group (region-scoped; cannot reuse the primary's).
  parameter_group_name = var.standby_parameter_group_name

  # Cross-region encrypted replica requires a KMS key in the destination region.
  # An MRK replica key keeps the deployment CMK-everywhere compliant.
  storage_encrypted = true
  kms_key_id        = var.standby_kms_key_arn

  # license_model is inherited from the source for Db2 BYOL; set explicitly to
  # keep the replica consistent with the primary.
  license_model = "bring-your-own-license"

  publicly_accessible = var.publicly_accessible
  skip_final_snapshot = true
  deletion_protection = var.deletion_protection

  # Fail fast if prerequisites are not met, rather than surfacing an opaque RDS
  # API error mid-apply.
  lifecycle {
    precondition {
      condition     = var.backup_retention_period > 0
      error_message = "Cross-region standby replica requires automated backups on the source: backup_retention_period must be > 0 (R13.2, R13.13)."
    }
    precondition {
      condition     = var.standby_parameter_group_name != ""
      error_message = "Cross-region standby replica requires a target-region parameter group: set standby_parameter_group_name (R13.2)."
    }
    precondition {
      condition     = var.standby_kms_key_arn != ""
      error_message = "Cross-region standby replica requires a target-region MRK CMK: set standby_kms_key_arn (R13.2)."
    }
  }

  tags = {
    Name = var.standby_replica_identifier != "" ? var.standby_replica_identifier : "${local.db_identifier}-standby"
    Role = "cross-region-mounted-standby"
  }

  timeouts {
    create = "120m"
    update = "90m"
    delete = "60m"
  }
}

# ── Same-Region Read Replica (optional) ───────────────────────────────────────
#
# Creates a same-region read replica of the primary RDS for Db2 instance. This
# resource is fully gated by var.create_read_replica and renders nothing when that
# flag is false (the default), so existing configurations are unaffected.
#
# Unlike the cross-region mounted standby replica above, this replica:
#   - Uses the DEFAULT `aws` provider (same region as the primary).
#   - References the source via the primary's DB instance identifier (same-region
#     replicas use replicate_source_db = <identifier>, not the ARN).
#   - Inherits the source's encryption, parameter group, and engine settings where
#     applicable, so those are not re-specified here.
#
# Prerequisite enforced by the precondition below (R13.15):
#   - Source automated backups MUST be enabled (backup_retention_period > 0). RDS
#     cannot create a replica from a source that has no automated backups.

resource "aws_db_instance" "read_replica" {
  count = var.create_read_replica ? 1 : 0

  # Same-region replica source is the primary's DB instance identifier (not ARN).
  replicate_source_db = aws_db_instance.this.identifier

  identifier     = var.read_replica_identifier != "" ? var.read_replica_identifier : "${local.db_identifier}-read"
  instance_class = var.read_replica_instance_class != "" ? var.read_replica_instance_class : var.instance_class

  publicly_accessible = var.publicly_accessible
  skip_final_snapshot = true
  deletion_protection = var.deletion_protection

  # Fail fast if the source has no automated backups, rather than surfacing an
  # opaque RDS API error mid-apply.
  lifecycle {
    precondition {
      condition     = var.backup_retention_period > 0
      error_message = "Same-region read replica requires automated backups on the source: backup_retention_period must be > 0 (R13.15)."
    }
  }

  tags = {
    Name = var.read_replica_identifier != "" ? var.read_replica_identifier : "${local.db_identifier}-read"
    Role = "same-region-read-replica"
  }

  timeouts {
    create = "120m"
    update = "90m"
    delete = "60m"
  }
}
