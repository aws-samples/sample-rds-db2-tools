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

variable "kms_key_arn" {
  description = "Existing KMS key ARN. Leave empty to create (or lookup) via alias."
  type        = string
  default     = ""
}

variable "kms_alias_exists" {
  description = "Set true if alias/rds-db2-<tag> already exists — reuses the existing key. When false and kms_key_arn is empty, a new key + alias are created."
  type        = bool
  default     = false
}

variable "multi_region_key" {
  description = "Create a multi-region KMS key (required for cross-region standby replicas). GovCloud supports this."
  type        = bool
  default     = false
}
