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
