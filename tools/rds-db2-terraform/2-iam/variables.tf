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
