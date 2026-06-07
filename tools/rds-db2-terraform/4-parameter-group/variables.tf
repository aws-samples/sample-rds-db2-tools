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
