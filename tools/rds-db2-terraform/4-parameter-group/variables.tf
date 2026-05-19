variable "aws_region" {
  type = string
}

variable "tag" {
  type = string
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

variable "engine_edition" {
  description = "DB2 engine edition. Valid: ce (12.1 only), se, ae. Must match the engine chosen in 5-rds."
  type        = string
  default     = "se"
  validation {
    condition     = contains(["ce", "se", "ae"], var.engine_edition)
    error_message = "engine_edition must be 'ce', 'se', or 'ae'."
  }
}

variable "engine_major_version" {
  description = "DB2 engine major version. Valid: 11.5, 12.1."
  type        = string
  default     = "11.5"
  validation {
    condition     = contains(["11.5", "12.1"], var.engine_major_version)
    error_message = "engine_major_version must be '11.5' or '12.1'."
  }
}

variable "parameter_group_name" {
  description = "Existing parameter group name to use. Leave empty to create a new one."
  type        = string
  default     = ""
}

variable "ibm_customer_id" {
  description = "IBM customer ID (rds.ibm_customer_id). Required for RDS for Db2 licensing."
  type        = string
  sensitive   = true
}

variable "ibm_site_id" {
  description = "IBM site ID (rds.ibm_site_id). Required for RDS for Db2 licensing."
  type        = string
  sensitive   = true
}
