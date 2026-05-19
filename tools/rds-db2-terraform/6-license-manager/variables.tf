variable "aws_region" {
  type = string
}

variable "tag" {
  description = "Project tag — propagated to all resources via default_tags"
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

variable "db2_edition" {
  description = "IBM Db2 edition: CE (Community, 12.1 only), SE (Standard), or AE (Advanced)"
  type        = string
  default     = "SE"
  validation {
    condition     = contains(["CE", "SE", "AE"], var.db2_edition)
    error_message = "db2_edition must be CE, SE, or AE"
  }
}

variable "license_count" {
  description = "Number of vCPU licenses to track. Set to the vCPU count of your RDS instance class."
  type        = number
}

variable "db_instance_arn" {
  description = "ARN of the RDS for Db2 instance (informational only — License Manager auto-discovers matching RDS instances via product_information_filter)"
  type        = string
  default     = ""
}
