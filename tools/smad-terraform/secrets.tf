# ===========================================================================
# Passwords
# ===========================================================================
# Domain admin and DSRM (safe-mode) passwords. The DCs fetch these from
# Secrets Manager at boot using their instance role - never embedded in user_data.
resource "random_password" "admin" {
  length           = 24
  special          = true
  override_special = "!#$%*-_=+"
  min_lower        = 2
  min_upper        = 2
  min_numeric      = 2
  min_special      = 2
}

resource "random_password" "dsrm" {
  length           = 24
  special          = true
  override_special = "!#$%*-_=+"
  min_lower        = 2
  min_upper        = 2
  min_numeric      = 2
  min_special      = 2
}

# Delegated AD service account password. Same value is written to the bootstrap
# secret (so DC1 can create the account) and the RDS self-managed AD secret
# (so RDS for Db2 authenticates with it). AD-friendly special set.
resource "random_password" "svc" {
  length           = 24
  special          = true
  override_special = "!#%*-_=+"
  min_lower        = 2
  min_upper        = 2
  min_numeric      = 2
  min_special      = 2
}

# ===========================================================================
# Bootstrap secret (read by the domain controllers during promotion/config)
# ===========================================================================
resource "aws_secretsmanager_secret" "ad" {
  #checkov:skip=CKV2_AWS_57:Domain admin/DSRM/service-account passwords; AD-aware rotation is out of scope for this sample (would require a custom AD-integrated rotation Lambda).
  name                    = "${var.name_prefix}/ad-credentials"
  description             = "Domain admin, DSRM and service-account credentials for ${var.domain_fqdn}"
  kms_key_id              = aws_kms_key.ad_secret.arn
  recovery_window_in_days = 0 # allow immediate re-create on destroy/apply cycles
  tags                    = { Name = "${var.name_prefix}-ad-credentials" }
}

resource "aws_secretsmanager_secret_version" "ad" {
  secret_id = aws_secretsmanager_secret.ad.id
  secret_string = jsonencode({
    domain_fqdn      = var.domain_fqdn
    netbios          = var.domain_netbios_name
    admin_user       = "${var.domain_netbios_name}\\Administrator"
    admin_password   = random_password.admin.result
    dsrm_password    = random_password.dsrm.result
    ou_name          = var.ou_name
    ou_dn            = local.ou_dn
    svc_account_name = var.svc_account_name
    svc_password     = random_password.svc.result
  })
}

# ===========================================================================
# RDS for Db2 self-managed AD: dedicated KMS key + secret (the blog pattern)
# ===========================================================================
data "aws_iam_policy_document" "ad_secret_kms" {
  #checkov:skip=CKV_AWS_111:Canonical KMS key policy - account-root "kms:*" on the key is the AWS-recommended baseline and prevents key lockout.
  #checkov:skip=CKV_AWS_356:For KMS key policies, Resource "*" refers to the key itself; it cannot be further constrained.
  #checkov:skip=CKV_AWS_109:Account-root key administration is the AWS-recommended baseline; the service decrypt grant is scoped by aws:SourceAccount.
  # Account root retains full control (admins manage the key).
  statement {
    sid       = "EnableIAMUserPermissions"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }

  # RDS can decrypt the secret on your behalf (scoped to this account).
  statement {
    sid       = "AllowRDSDecrypt"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["rds.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_kms_key" "ad_secret" {
  description             = "RDS for Db2 self-managed AD secret encryption key"
  multi_region            = true
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.ad_secret_kms.json
  tags                    = { Name = "${var.name_prefix}-ad-secret-key" }
}

resource "aws_kms_alias" "ad_secret" {
  name          = "alias/${var.name_prefix}-ad-secret"
  target_key_id = aws_kms_key.ad_secret.key_id
}

resource "aws_secretsmanager_secret" "rds_self_managed_ad" {
  #checkov:skip=CKV2_AWS_57:AD service-account secret; rotation requires a custom AD-integrated rotation Lambda, out of scope for this sample.
  name                    = "${var.name_prefix}/rds-self-managed-ad"
  description             = "RDS for Db2 self-managed AD service account (SELF_MANAGED_ACTIVE_DIRECTORY_*)"
  kms_key_id              = aws_kms_key.ad_secret.arn
  recovery_window_in_days = 0
  tags                    = { Name = "${var.name_prefix}-rds-self-managed-ad" }
}

resource "aws_secretsmanager_secret_version" "rds_self_managed_ad" {
  secret_id = aws_secretsmanager_secret.rds_self_managed_ad.id
  # Keys/format required by RDS for Db2. Username is the sAMAccountName only.
  secret_string = jsonencode({
    SELF_MANAGED_ACTIVE_DIRECTORY_USERNAME = var.svc_account_name
    SELF_MANAGED_ACTIVE_DIRECTORY_PASSWORD = random_password.svc.result
  })
}

# Resource policy: only RDS may read it, guarded against the confused-deputy problem.
data "aws_iam_policy_document" "rds_secret_resource" {
  statement {
    sid       = "AllowRDSReadADSecret"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["rds.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:sourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:sourceArn"
      values   = [local.rds_source_arn]
    }
  }
}

resource "aws_secretsmanager_secret_policy" "rds_self_managed_ad" {
  secret_arn = aws_secretsmanager_secret.rds_self_managed_ad.arn
  policy     = data.aws_iam_policy_document.rds_secret_resource.json
}
