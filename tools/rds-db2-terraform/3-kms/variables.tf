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
