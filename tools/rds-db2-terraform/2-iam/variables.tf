variable "aws_region" {
  type = string
}

variable "tag" {
  description = "Project tag"
  type        = string
}

variable "environment" {
  description = "Environment label (e.g. dev, staging, prod)"
  type        = string
  default     = "prod"
}

variable "owner" {
  description = "Team or individual owning these resources"
  type        = string
  default     = ""
}

variable "monitoring_role_name" {
  description = "Existing enhanced monitoring role name. Leave empty to create one."
  type        = string
  default     = ""
}

variable "create_s3_role" {
  description = "Create IAM role for S3 backup restore integration"
  type        = bool
  default     = false
}

variable "s3_backup_bucket_name" {
  description = "S3 bucket name for DB2 backup images (required when create_s3_role=true)"
  type        = string
  default     = ""
}

variable "create_s3_backup_bucket" {
  description = "Create the S3 backup bucket. Set false if it already exists."
  type        = bool
  default     = false
}

variable "s3_backup_bucket_kms_key_arn" {
  description = <<-EOT
    Customer-managed KMS CMK ARN used for SSE-KMS on the S3 backup bucket this
    module creates. When set, the bucket's default encryption uses aws:kms with
    this CMK instead of the AWS-managed/owned SSE-S3 (AES256) default key.

    NOTE: The provisioning composer ALWAYS supplies a customer-managed CMK here to
    satisfy the CMK-everywhere security invariant (R6.10/R6.12) — no bucket the
    composed deployment creates may rely on an AWS-owned/managed default key. The
    empty default exists only for backward compatibility with direct module callers.
  EOT
  type        = string
  default     = ""
}

variable "create_directory_role" {
  description = "Create IAM role for AWS Directory Service / AD authentication"
  type        = bool
  default     = false
}

variable "directory_role_exists" {
  description = "Set true if rds-db2-directory-service-access-role already exists — skips creation and uses the existing role"
  type        = bool
  default     = false
}

variable "self_managed_ad_secret_arn" {
  description = "Secrets Manager ARN holding self-managed AD join credentials. When set (and the directory role is created), grants the directory role read access to this secret. Leave empty for AWS Managed AD."
  type        = string
  default     = ""
}

variable "create_audit_role" {
  description = "Create IAM role and policy for DB2 audit logging to S3"
  type        = bool
  default     = false
}

variable "audit_bucket_name" {
  description = "S3 bucket name for DB2 audit logs (required when create_audit_role=true)"
  type        = string
  default     = ""
}

variable "create_audit_bucket" {
  description = "Create the S3 audit bucket. Set false if it already exists."
  type        = bool
  default     = false
}

variable "audit_bucket_kms_key_arn" {
  description = <<-EOT
    Customer-managed KMS CMK ARN used for SSE-KMS on the S3 audit bucket this
    module creates. When set, the bucket's default encryption uses aws:kms with
    this CMK instead of the AWS-managed/owned SSE-S3 (AES256) default key.

    NOTE: The provisioning composer ALWAYS supplies a customer-managed CMK here to
    satisfy the CMK-everywhere security invariant (R6.10/R6.12) — no bucket the
    composed deployment creates may rely on an AWS-owned/managed default key. The
    empty default exists only for backward compatibility with direct module callers.
  EOT
  type        = string
  default     = ""
}

# ── Mandatory provenance + customer tags (R14) ───────────────────────────────
# Emitted through the provider default_tags block alongside Project/Environment/
# Owner. created_by/generation_model are skill-set provenance (R14.1/14.2);
# extra_tags carries any customer-supplied tags, merged so the mandatory keys
# always win (R14.4). All default to a no-op so the module stays backward
# compatible when driven outside the composer.

variable "created_by" {
  description = "Provenance tag identifying the skill that created the resources (R14.1/14.2)."
  type        = string
  default     = ""
}

variable "generation_model" {
  description = "Provenance tag identifying the generation model that produced the configuration (R14.1/14.2)."
  type        = string
  default     = ""
}

variable "extra_tags" {
  description = "Additional customer-supplied tags appended via default_tags. Mandatory tag keys (Project/Environment/Owner/created_by/generation_model) always win over any colliding key (R14.4)."
  type        = map(string)
  default     = {}
}
