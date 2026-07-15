# One-time GitOps CI bootstrap: GitHub OIDC -> a repo-scoped IAM role this repo's
# Actions assume (no long-lived keys) to run `terraform plan` (on PRs) and
# `terraform apply` (on merge) for RDS for Db2 deployments.
#
# Run this ONCE per AWS account, with your own admin credentials, OUTSIDE CI:
#   cd bootstrap
#   terraform init
#   terraform apply        # review, then approve
# Then copy the `deploy_role_arn` output into the repo SECRET
# RDS_DB2_DEPLOY_ROLE_ARN (see ../README.md / step 12).
#
# This root uses LOCAL state by design (it's a one-time, account-level setup that
# the CI deploy role itself must not be able to alter). Keep the local state file
# safe or re-import if you re-run.

terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
    tls = { source = "hashicorp/tls", version = "~> 4.0" }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      ManagedBy = "Terraform"
      Project   = "rds-db2-deployer-gitops-bootstrap"
      Purpose   = "github-oidc-ci"
    }
  }
}

# --- GitHub OIDC provider (account-global) ----------------------------------
# Fetch the issuer's TLS thumbprint dynamically so we never hardcode a value that
# can rotate. AWS still requires a thumbprint in the provider resource.
data "tls_certificate" "github" {
  count = var.create_oidc_provider ? 1 : 0
  url   = "https://token.actions.githubusercontent.com/.well-known/openid-configuration"
}

resource "aws_iam_openid_connect_provider" "github" {
  count           = var.create_oidc_provider ? 1 : 0
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github[0].certificates[0].sha1_fingerprint]
}

locals {
  # Use the created provider, or an existing one if create_oidc_provider=false.
  oidc_provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : var.existing_oidc_provider_arn

  # The repo subject claims we allow to assume the role:
  #  - pull_request events (the `plan` job), and
  #  - pushes to the default branch (the `apply` job).
  allowed_subs = [
    "repo:${var.github_owner}/${var.github_repo}:pull_request",
    "repo:${var.github_owner}/${var.github_repo}:ref:refs/heads/${var.default_branch}",
  ]
}

# --- The role GitHub Actions assumes -----------------------------------------
data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    effect  = "Allow"
    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.allowed_subs
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = var.role_name
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
  description          = "GitHub Actions OIDC role for rds-db2-deployer-gitops (plan/apply)."
}

# --- Permissions the deploy role needs --------------------------------------
# Scoped where it's cheap to scope (the Terraform remote state); service-level
# elsewhere so a first deploy can reuse OR create the subnet group / KMS MRK /
# monitoring role / parameter group and stand up the instance. This is a
# pragmatic test posture — TIGHTEN for production (pin ARNs, drop create rights
# you don't use, split plan-only vs apply roles).
data "aws_iam_policy_document" "deploy" {
  # Terraform S3 remote state + DynamoDB lock (bootstrapped by 0-backend-setup).
  statement {
    sid     = "TerraformState"
    effect  = "Allow"
    actions = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:DeleteObject"]
    resources = [
      "arn:aws:s3:::${var.state_bucket}",
      "arn:aws:s3:::${var.state_bucket}/*",
    ]
  }
  statement {
    sid       = "TerraformLock"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
    resources = ["arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.me.account_id}:table/${var.lock_table}"]
  }

  # RDS instance + parameter group + subnet group lifecycle.
  statement {
    sid       = "RdsAndSupporting"
    effect    = "Allow"
    actions   = ["rds:*", "ec2:Describe*", "ec2:CreateTags", "ec2:DeleteTags", "logs:Describe*", "logs:CreateLogGroup", "logs:PutRetentionPolicy"]
    resources = ["*"]
  }

  # Security group ingress for the SSL service port (create-on-blank or reuse).
  statement {
    sid       = "SecurityGroupIngress"
    effect    = "Allow"
    actions   = ["ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress", "ec2:CreateSecurityGroup", "ec2:DeleteSecurityGroup"]
    resources = ["*"]
  }

  # KMS for storage + managed-secret encryption (reuse an MRK, or create one).
  statement {
    sid       = "Kms"
    effect    = "Allow"
    actions   = ["kms:CreateKey", "kms:CreateAlias", "kms:DeleteAlias", "kms:DescribeKey", "kms:ListAliases", "kms:TagResource", "kms:CreateGrant", "kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey*", "kms:ReplicateKey", "kms:PutKeyPolicy"]
    resources = ["*"]
  }

  # Enhanced-monitoring role (reuse or create) + PassRole to RDS.
  statement {
    sid       = "Iam"
    effect    = "Allow"
    actions   = ["iam:GetRole", "iam:CreateRole", "iam:DeleteRole", "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:TagRole", "iam:PassRole"]
    resources = ["*"]
  }

  # Managed master password (Secrets Manager) + IBM IDs (SSM SecureString).
  statement {
    sid       = "SecretsAndSsm"
    effect    = "Allow"
    actions   = ["secretsmanager:CreateSecret", "secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue", "secretsmanager:TagResource", "secretsmanager:DeleteSecret", "ssm:GetParameter", "ssm:GetParameters"]
    resources = ["*"]
  }
}

data "aws_caller_identity" "me" {}

resource "aws_iam_role_policy" "deploy" {
  name   = "${var.role_name}-policy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}
