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
