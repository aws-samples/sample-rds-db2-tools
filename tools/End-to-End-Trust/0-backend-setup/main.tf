terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------
# Customer-managed KMS key for state bucket encryption
#
# Using CMK instead of SSE-S3 (AES256) means:
#   - Decryption requires both s3:GetObject AND kms:Decrypt
#   - Key policy controls who can decrypt — scoped to the Terraform
#     service account principal only
#   - All key usage is logged in CloudTrail independently of S3 access logs
# -----------------------------------------------------------------------
resource "aws_kms_key" "terraform_state" {
  description             = "CMK for Terraform state bucket — restricts who can decrypt state files"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Root account retains full key administration rights
        Sid    = "RootAdminAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        # Only the Terraform service account can use the key to encrypt/decrypt
        # state files. Replace the principal ARN with your actual service
        # account ARN (IAM user, role, or SSO role session).
        Sid    = "TerraformServiceAccountDecrypt"
        Effect = "Allow"
        Principal = {
          AWS = var.terraform_principal_arn
        }
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt",
          "kms:DescribeKey"
        ]
        Resource = "*"
      }
    ]
  })

  tags = {
    Name      = "terraform-state-key"
    ManagedBy = "Terraform"
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_kms_alias" "terraform_state" {
  name          = "alias/terraform-state-${var.state_bucket_name}"
  target_key_id = aws_kms_key.terraform_state.key_id
}

# S3 bucket for Terraform state
resource "aws_s3_bucket" "terraform_state" {
  bucket = var.state_bucket_name

  tags = {
    Name        = "Terraform State Bucket"
    Purpose     = "terraform-state"
    ManagedBy   = "Terraform"
  }

  lifecycle {
    prevent_destroy = true
  }
}

# Enable versioning
resource "aws_s3_bucket_versioning" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Enable SSE-KMS encryption using the customer-managed key
# SSE-KMS requires kms:Decrypt in addition to s3:GetObject, so the key
# policy above is the primary access control for state file decryption
resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.terraform_state.arn
    }
    bucket_key_enabled = true  # reduces KMS API call costs
  }
}

# Block public access
resource "aws_s3_bucket_public_access_block" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Bucket policy:
#   1. Deny all non-TLS requests (protects data in transit)
#   2. Deny s3:GetObject to anyone except the Terraform service account
#      (protects TLS private keys and secrets stored in state files)
resource "aws_s3_bucket_policy" "terraform_state_secure_transport" {
  bucket = aws_s3_bucket.terraform_state.id

  depends_on = [aws_s3_bucket_public_access_block.terraform_state]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyNonTLS"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.terraform_state.arn,
          "${aws_s3_bucket.terraform_state.arn}/*"
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      },
      {
        # Deny s3:GetObject to everyone except the Terraform service account.
        # This prevents anyone with broad S3 access from reading state files
        # and extracting the TLS private key or secret ARNs stored in them.
        Sid       = "DenyStateReadExceptServiceAccount"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.terraform_state.arn}/*"
        Condition = {
          StringNotEquals = {
            "aws:PrincipalArn" = var.terraform_principal_arn
          }
        }
      }
    ]
  })
}

# DynamoDB table for state locking
resource "aws_dynamodb_table" "terraform_lock" {
  name         = var.lock_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = {
    Name      = "Terraform State Lock Table"
    Purpose   = "terraform-state-lock"
    ManagedBy = "Terraform"
  }

  lifecycle {
    prevent_destroy = true
    ignore_changes  = [tags]
  }
}
