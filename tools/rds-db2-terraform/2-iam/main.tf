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
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
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
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
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
