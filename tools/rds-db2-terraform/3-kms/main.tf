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

locals {
  kms_alias_name = "alias/rds-db2-${lower(var.tag)}"
  # Decide which branch to take:
  #   a) arn given → use it verbatim
  #   b) alias exists → look up via data source
  #   c) otherwise → create new key + alias
  use_arn    = var.kms_key_arn != ""
  use_alias  = !local.use_arn && var.kms_alias_exists
  do_create  = !local.use_arn && !local.use_alias
}

data "aws_kms_alias" "existing" {
  count = local.use_alias ? 1 : 0
  name  = local.kms_alias_name
}

resource "aws_kms_key" "rds_db2" {
  count                   = local.do_create ? 1 : 0
  description             = "Customer-managed KMS key for RDS for Db2 - ${var.tag}"
  enable_key_rotation     = true
  multi_region            = var.multi_region_key
  deletion_window_in_days = 30

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Enable IAM User Permissions"
        Effect = "Allow"
        Principal = {
          AWS = "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "Allow RDS to use the key"
        Effect = "Allow"
        Principal = { Service = "rds.amazonaws.com" }
        Action = [
          "kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*",
          "kms:GenerateDataKey*", "kms:DescribeKey"
        ]
        Resource = "*"
      }
    ]
  })

  tags = { Name = "rds-db2-kms-${var.tag}" }
}

resource "aws_kms_alias" "rds_db2" {
  count         = local.do_create ? 1 : 0
  name          = local.kms_alias_name
  target_key_id = aws_kms_key.rds_db2[0].key_id
}

locals {
  kms_key_arn = (
    local.use_arn   ? var.kms_key_arn :
    local.use_alias ? data.aws_kms_alias.existing[0].target_key_arn :
                      aws_kms_key.rds_db2[0].arn
  )
  kms_key_id = (
    local.use_arn   ? var.kms_key_arn :
    local.use_alias ? data.aws_kms_alias.existing[0].target_key_id :
                      aws_kms_key.rds_db2[0].key_id
  )
}
