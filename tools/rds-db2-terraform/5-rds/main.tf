terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = var.tag
      ManagedBy   = "Terraform"
      Environment = var.environment
      Owner       = var.owner
    }
  }
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
  _eng_abbr    = replace(var.engine, "-", "")                 # db2-se -> db2se
  _ver_abbr    = replace(var.engine_major_version, ".", "-")  # 11.5   -> 11-5
  _inst_abbr   = replace(
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
  _auto_id = lower("${local._eng_abbr}-${local._ver_abbr}-${local._inst_abbr}-${var.db_size_label}-${var.storage_type}-${local._az_abbr}${local._iops_suffix}-${var.tag}")

  db_identifier = var.db_instance_identifier != "" ? var.db_instance_identifier : local._auto_id

  # IOPS/throughput rules:
  #   gp3  → allowed only when allocated_storage ≥ 400 GiB (baseline 3000/125 below that)
  #   io1/io2 → IOPS is required; always allowed
  can_set_iops = (
    var.storage_type == "gp3" && var.allocated_storage >= 400
    ) || (
    contains(["io1", "io2"], var.storage_type)
  )
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

  allocated_storage  = var.allocated_storage
  storage_type       = var.storage_type
  # gp3 quirk: IOPS/throughput may only be set when storage ≥ 400 GiB.
  # Below that threshold RDS uses baseline 3000 IOPS / 125 MB/s automatically.
  iops               = local.can_set_iops ? var.iops : null
  storage_throughput = local.can_set_iops && var.storage_throughput > 0 ? var.storage_throughput : null
  storage_encrypted  = var.storage_encrypted
  kms_key_id         = var.storage_encrypted ? var.kms_key_arn : null

  db_name  = var.db_name
  username = var.master_username
  password                    = var.manage_master_user_password ? null : var.master_password
  manage_master_user_password = var.manage_master_user_password ? true : null
  port                        = var.db2_port

  db_subnet_group_name   = var.db_subnet_group_name
  vpc_security_group_ids = [var.security_group_id]
  publicly_accessible    = var.publicly_accessible
  availability_zone      = var.multi_az ? null : var.availability_zone
  multi_az               = var.multi_az

  parameter_group_name = var.parameter_group_name
  option_group_name    = var.enable_audit ? aws_db_option_group.audit[0].name : null

  backup_retention_period = var.backup_retention_period
  skip_final_snapshot     = true
  deletion_protection     = false

  monitoring_interval = 15
  monitoring_role_arn = var.monitoring_role_arn

  enabled_cloudwatch_logs_exports = ["diag.log", "notify.log"]

  domain               = var.directory_id != "" ? var.directory_id : null
  domain_iam_role_name = var.directory_id != "" ? var.directory_role_name : null

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
