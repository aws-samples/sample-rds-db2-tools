variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID where RDS for Db2 will be deployed"
  type        = string
}

variable "security_group_id" {
  description = "Security group ID for the RDS instance and endpoints"
  type        = string
}

variable "publicly_accessible" {
  description = "Whether the RDS instance is publicly accessible"
  type        = bool
  default     = false
}

variable "db_subnet_group_name" {
  description = "Existing DB subnet group name. Leave empty to create one."
  type        = string
  default     = ""
}

variable "create_interface_endpoints" {
  description = "Create VPC interface endpoints for private VPC access (RDS, Secrets Manager, CloudWatch)"
  type        = bool
  default     = false
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
