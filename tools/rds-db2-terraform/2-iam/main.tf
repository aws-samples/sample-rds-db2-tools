terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
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

data "aws_partition" "current" {}

# ── Optional S3 Buckets ──────────────────────────────────────────────────────

resource "aws_s3_bucket" "backup" {
  count  = var.create_s3_backup_bucket ? 1 : 0
  bucket = var.s3_backup_bucket_name
  tags   = { Name = var.s3_backup_bucket_name }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backup" {
  count  = var.create_s3_backup_bucket ? 1 : 0
  bucket = aws_s3_bucket.backup[0].id
  rule {
    # Use SSE-KMS with a customer-managed CMK when one is supplied (R6.10/R6.12);
    # fall back to SSE-S3 (AES256) only for backward-compatible direct callers that
    # omit a CMK. The composer always supplies s3_backup_bucket_kms_key_arn.
    apply_server_side_encryption_by_default {
      sse_algorithm     = var.s3_backup_bucket_kms_key_arn != "" ? "aws:kms" : "AES256"
      kms_master_key_id = var.s3_backup_bucket_kms_key_arn != "" ? var.s3_backup_bucket_kms_key_arn : null
    }
    # Reduce KMS request cost/throttling for SSE-KMS buckets; no-op for SSE-S3.
    bucket_key_enabled = var.s3_backup_bucket_kms_key_arn != "" ? true : null
  }
}

resource "aws_s3_bucket_public_access_block" "backup" {
  count                   = var.create_s3_backup_bucket ? 1 : 0
  bucket                  = aws_s3_bucket.backup[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket" "audit" {
  count  = var.create_audit_bucket ? 1 : 0
  bucket = var.audit_bucket_name
  tags   = { Name = var.audit_bucket_name }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "audit" {
  count  = var.create_audit_bucket ? 1 : 0
  bucket = aws_s3_bucket.audit[0].id
  rule {
    # Use SSE-KMS with a customer-managed CMK when one is supplied (R6.10/R6.12);
    # fall back to SSE-S3 (AES256) only for backward-compatible direct callers that
    # omit a CMK. The composer always supplies audit_bucket_kms_key_arn.
    apply_server_side_encryption_by_default {
      sse_algorithm     = var.audit_bucket_kms_key_arn != "" ? "aws:kms" : "AES256"
      kms_master_key_id = var.audit_bucket_kms_key_arn != "" ? var.audit_bucket_kms_key_arn : null
    }
    # Reduce KMS request cost/throttling for SSE-KMS buckets; no-op for SSE-S3.
    bucket_key_enabled = var.audit_bucket_kms_key_arn != "" ? true : null
  }
}

resource "aws_s3_bucket_public_access_block" "audit" {
  count                   = var.create_audit_bucket ? 1 : 0
  bucket                  = aws_s3_bucket.audit[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── Enhanced Monitoring Role ─────────────────────────────────────────────────

data "aws_iam_role" "monitoring_existing" {
  count = var.monitoring_role_name != "" ? 1 : 0
  name  = var.monitoring_role_name
}

resource "aws_iam_role" "monitoring" {
  count = var.monitoring_role_name == "" ? 1 : 0
  name  = "rds-db2-monitoring-role-${var.tag}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "monitoring.rds.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "monitoring" {
  count      = var.monitoring_role_name == "" ? 1 : 0
  role       = aws_iam_role.monitoring[0].name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

locals {
  monitoring_role_arn = (
    var.monitoring_role_name != ""
    ? data.aws_iam_role.monitoring_existing[0].arn
    : aws_iam_role.monitoring[0].arn
  )
}

# ── S3 Integration Role (for backup restore) ─────────────────────────────────

resource "aws_iam_role" "s3_integration" {
  count = var.create_s3_role ? 1 : 0
  name  = "rds-db2-s3-role-${var.tag}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "rds.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "s3_integration" {
  count = var.create_s3_role ? 1 : 0
  name  = "S3AccessPolicy"
  role  = aws_iam_role.s3_integration[0].id

  # Mirrors dtw-orchestrator/0cr-ins.sh:
  #  - ListAllMyBuckets MUST be account-level ("*") for RDS Db2 backup_database
  #  - KMS actions are key-level and MUST be "*" (tighten to specific CMK ARN if desired)
  #  - Bucket-level: ListBucket + GetBucketAcl + GetBucketLocation
  #  - Object-level: PutObject, GetObject, GetObjectVersion, AbortMultipartUpload, ListMultipartUploadParts
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListAllBuckets"
        Effect   = "Allow"
        Action   = ["s3:ListAllMyBuckets"]
        Resource = "*"
      },
      {
        Sid      = "KmsForBackupEncryption"
        Effect   = "Allow"
        Action   = ["kms:GenerateDataKey", "kms:Decrypt"]
        Resource = "*"
      },
      {
        Sid    = "BucketLevelAccess"
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketAcl",
          "s3:GetBucketLocation"
        ]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::${var.s3_backup_bucket_name}"
      },
      {
        Sid    = "ObjectLevelAccess"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:GetObjectVersion",
          "s3:AbortMultipartUpload",
          "s3:ListMultipartUploadParts"
        ]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::${var.s3_backup_bucket_name}/*"
      }
    ]
  })
}

# ── Directory Service Role (for AD/Kerberos auth) ────────────────────────────

data "aws_iam_role" "directory_service_existing" {
  count = var.create_directory_role && var.directory_role_exists ? 1 : 0
  name  = "rds-db2-directory-service-access-role"
}

resource "aws_iam_role" "directory_service" {
  count = var.create_directory_role && !var.directory_role_exists ? 1 : 0
  name  = "rds-db2-directory-service-access-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = [
          "rds.amazonaws.com",
          "directoryservice.rds.amazonaws.com"
        ]
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "directory_service" {
  count      = var.create_directory_role && !var.directory_role_exists ? 1 : 0
  role       = aws_iam_role.directory_service[0].name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonRDSDirectoryServiceAccess"
}

# Self-managed AD additionally requires RDS to read the join credentials from
# Secrets Manager (the secret holding SELF_MANAGED_ACTIVE_DIRECTORY_USERNAME /
# SELF_MANAGED_ACTIVE_DIRECTORY_PASSWORD). Scoped to the supplied secret ARN.
resource "aws_iam_role_policy" "directory_service_self_managed" {
  count = var.create_directory_role && !var.directory_role_exists && var.self_managed_ad_secret_arn != "" ? 1 : 0
  name  = "SelfManagedADSecretAccess"
  role  = aws_iam_role.directory_service[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "ReadSelfManagedADSecret"
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
      Resource = var.self_managed_ad_secret_arn
    }]
  })
}

locals {
  directory_service_role_arn = (
    !var.create_directory_role
    ? ""
    : (
      var.directory_role_exists
      ? data.aws_iam_role.directory_service_existing[0].arn
      : aws_iam_role.directory_service[0].arn
    )
  )
  directory_service_role_name = (
    !var.create_directory_role
    ? ""
    : (
      var.directory_role_exists
      ? data.aws_iam_role.directory_service_existing[0].name
      : aws_iam_role.directory_service[0].name
    )
  )
}

# ── DB2 Audit Role ───────────────────────────────────────────────────────────

resource "aws_iam_policy" "db2_audit" {
  count = var.create_audit_role ? 1 : 0
  name  = "rds-db2-audit-policy-${var.tag}"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation"]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::${var.audit_bucket_name}"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::${var.audit_bucket_name}/*"
      }
    ]
  })
}

resource "aws_iam_role" "db2_audit" {
  count = var.create_audit_role ? 1 : 0
  name  = "rds-db2-audit-role-${var.tag}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "rds.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "db2_audit" {
  count      = var.create_audit_role ? 1 : 0
  role       = aws_iam_role.db2_audit[0].name
  policy_arn = aws_iam_policy.db2_audit[0].arn
}
