variable "aws_region" {
  type = string
}

variable "tag" {
  description = "Project tag for resource grouping and naming"
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

variable "db_instance_identifier" {
  description = "Unique RDS DB instance identifier (lowercase, letters/numbers/hyphens). Leave empty to auto-build from engine/version/class/tag."
  type        = string
  default     = ""
}

variable "db_size_label" {
  description = "Shorthand size label embedded in the auto-built identifier (xs, s, m, l, xl). Has no effect on storage."
  type        = string
  default     = "xs"
}

variable "engine" {
  description = "DB2 engine: db2-ce (12.1 only), db2-se, or db2-ae"
  type        = string
  default     = "db2-se"
  validation {
    condition     = contains(["db2-ce", "db2-se", "db2-ae"], var.engine)
    error_message = "engine must be db2-ce, db2-se, or db2-ae"
  }
}

variable "engine_version" {
  description = "Full engine version string (e.g. 11.5.9.0). Leave empty to auto-resolve latest for engine + engine_major_version."
  type        = string
  default     = ""
}

variable "engine_major_version" {
  description = "Major engine version for option group (e.g. 11.5)"
  type        = string
  default     = "11.5"
}

variable "instance_class" {
  description = "RDS instance class (e.g. db.r6i.2xlarge)"
  type        = string
}

variable "master_username" {
  type    = string
  default = "admin"
}

variable "manage_master_user_password" {
  description = "Let RDS manage the master user password in Secrets Manager (recommended). If true, master_password is ignored."
  type        = bool
  default     = true
}

variable "master_password" {
  description = "Only used when manage_master_user_password=false"
  type        = string
  default     = ""
  sensitive   = true
}

variable "master_user_secret_kms_key_id" {
  description = <<-EOT
    Customer-managed KMS CMK (key id or ARN) used to encrypt the RDS-managed
    master-user secret in Secrets Manager. Only applies when
    manage_master_user_password=true. Leave empty to preserve the prior behavior
    (RDS encrypts the managed secret with the aws/secretsmanager AWS-managed key).

    NOTE: The provisioning composer ALWAYS supplies a customer-managed CMK here to
    satisfy the CMK-everywhere security invariant (R6.10/R6.12) — no resource the
    composed deployment creates may rely on an AWS-owned/managed default key. The
    empty default exists only for backward compatibility with direct module callers.
  EOT
  type        = string
  default     = ""
}

variable "db_name" {
  description = "Initial database name (max 8 chars, uppercase)"
  type        = string
  default     = "DB2DB"
  validation {
    condition     = length(var.db_name) <= 8
    error_message = "DB2 database name must be 8 characters or fewer"
  }
}

variable "db2_port" {
  type    = number
  default = 8392
}

variable "vpc_id" {
  type = string
}

variable "security_group_id" {
  type = string
}

variable "db_subnet_group_name" {
  description = "DB subnet group name (from 1-networking module)"
  type        = string
}

variable "publicly_accessible" {
  type    = bool
  default = false
}

variable "availability_zone" {
  description = "AZ for single-AZ deployment (ignored when multi_az=true)"
  type        = string
  default     = ""
}

variable "multi_az" {
  type    = bool
  default = false
}

variable "storage_type" {
  type    = string
  default = "gp3"
  validation {
    condition     = contains(["gp3", "io2"], var.storage_type)
    error_message = "storage_type must be gp3 or io2"
  }
}

variable "allocated_storage" {
  type    = number
  default = 400
}

variable "iops" {
  type    = number
  default = 12000
}

variable "storage_throughput" {
  type    = number
  default = 0
}

variable "storage_encrypted" {
  type    = bool
  default = true
}

variable "kms_key_arn" {
  type    = string
  default = ""
}

variable "parameter_group_name" {
  description = "DB parameter group name (from 4-parameter-group module)"
  type        = string
}

variable "backup_retention_period" {
  type    = number
  default = 1
  validation {
    condition     = var.backup_retention_period >= 0 && var.backup_retention_period <= 35
    error_message = "backup_retention_period must be between 0 and 35"
  }
}

variable "deletion_protection" {
  description = "Enable RDS deletion protection. Defaults to false to preserve existing behavior."
  type        = bool
  default     = false
}

variable "monitoring_role_arn" {
  description = "Enhanced monitoring IAM role ARN (from 2-iam module)"
  type        = string
}

variable "directory_id" {
  description = "AWS Managed AD directory ID to join. Mutually exclusive with the self-managed AD inputs (domain_fqdn, etc.)."
  type        = string
  default     = ""

  validation {
    # AWS Managed AD (directory_id) and customer self-managed AD (domain_fqdn)
    # are mutually exclusive — only one directory mode may be rendered.
    condition     = var.directory_id == "" || var.domain_fqdn == ""
    error_message = "directory_id (AWS Managed AD) and domain_fqdn (self-managed AD) are mutually exclusive; set only one."
  }
}

variable "directory_role_name" {
  description = "IAM role name RDS assumes for directory access. Required for both AWS Managed AD and self-managed AD (from 2-iam module)."
  type        = string
  default     = ""
}

# ── Customer self-managed AD / Kerberos (mutually exclusive with directory_id) ─

variable "domain_fqdn" {
  description = "Fully qualified domain name of the customer self-managed AD (e.g. company.com). Leave empty to disable self-managed AD."
  type        = string
  default     = ""
}

variable "domain_ou" {
  description = "Organizational unit DN for the self-managed AD (e.g. OU=RDSDb2,DC=company,DC=com)."
  type        = string
  default     = ""
}

variable "domain_auth_secret_arn" {
  description = "Secrets Manager ARN whose secret holds SELF_MANAGED_ACTIVE_DIRECTORY_USERNAME and SELF_MANAGED_ACTIVE_DIRECTORY_PASSWORD for the self-managed AD."
  type        = string
  default     = ""
}

variable "domain_dns_ips" {
  description = "DNS server IPs for the self-managed AD domain (1-2 entries)."
  type        = list(string)
  default     = []
}

variable "restore_from_s3" {
  type    = bool
  default = false
}

variable "s3_integration_role_arn" {
  type    = string
  default = ""
}

variable "enable_audit" {
  type    = bool
  default = false
}

variable "audit_role_arn" {
  type    = string
  default = ""
}

variable "audit_bucket_name" {
  type    = string
  default = ""
}

# ── Cross-Region Mounted Standby Replica (optional, DR) ───────────────────────
#
# All defaulted off/empty for backward compatibility: when create_standby_replica
# is false (the default), no replica resource is rendered and existing configs are
# unaffected. When enabled, the caller MUST also pass the aws.replica provider
# alias (configured for the target region) into this module.
#
# Prerequisites (see aws_db_instance.standby_replica in main.tf):
#   - Source automated backups enabled (backup_retention_period > 0).
#   - A target-region DB parameter group (standby_parameter_group_name).
#   - A target-region MRK CMK (standby_kms_key_arn).

variable "create_standby_replica" {
  description = "Create a cross-region mounted standby (DR) replica of the primary instance. When true, the caller must pass the aws.replica provider alias and the standby_* prerequisites."
  type        = bool
  default     = false
}

variable "standby_replica_region" {
  description = "Target (DR) region for the cross-region mounted standby replica. Informational for this module; the actual region is determined by the aws.replica provider alias the caller configures."
  type        = string
  default     = ""
}

variable "standby_replica_identifier" {
  description = "Identifier for the cross-region standby replica. Leave empty to derive '<primary-identifier>-standby'."
  type        = string
  default     = ""
}

variable "standby_instance_class" {
  description = "Instance class for the standby replica. Leave empty to reuse the primary instance_class."
  type        = string
  default     = ""
}

variable "standby_parameter_group_name" {
  description = "Target-region DB parameter group name for the standby replica. Parameter groups are region-scoped, so the primary's group cannot be reused. Required when create_standby_replica=true."
  type        = string
  default     = ""
}

variable "standby_kms_key_arn" {
  description = "Target-region customer-managed MRK CMK ARN used to encrypt the cross-region standby replica. Required when create_standby_replica=true."
  type        = string
  default     = ""
}

# ── Same-Region Read Replica (optional) ───────────────────────────────────────
#
# All defaulted off/empty for backward compatibility: when create_read_replica is
# false (the default), no read-replica resource is rendered and existing configs
# are unaffected. Unlike the cross-region standby replica, the same-region read
# replica uses the default `aws` provider (same region as the primary) and
# references the source by DB instance identifier rather than ARN.
#
# Prerequisite (see aws_db_instance.read_replica in main.tf):
#   - Source automated backups enabled (backup_retention_period > 0).

variable "create_read_replica" {
  description = "Create a same-region read replica of the primary instance. Requires automated backups on the source (backup_retention_period > 0)."
  type        = bool
  default     = false
}

variable "read_replica_identifier" {
  description = "Identifier for the same-region read replica. Leave empty to derive '<primary-identifier>-read'."
  type        = string
  default     = ""
}

variable "read_replica_instance_class" {
  description = "Instance class for the read replica. Leave empty to reuse the primary instance_class."
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
