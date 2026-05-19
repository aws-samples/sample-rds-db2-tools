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
    condition     = contains(["gp3", "io1", "io2"], var.storage_type)
    error_message = "storage_type must be gp3, io1, or io2"
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

variable "monitoring_role_arn" {
  description = "Enhanced monitoring IAM role ARN (from 2-iam module)"
  type        = string
}

variable "directory_id" {
  type    = string
  default = ""
}

variable "directory_role_name" {
  type    = string
  default = ""
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
