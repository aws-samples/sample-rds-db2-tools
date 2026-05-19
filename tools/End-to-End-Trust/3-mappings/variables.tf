variable "aws_region" {
  description = "AWS region (auto-populated from 0-backend-setup if available)"
  type        = string
  default     = "us-east-1"
}

variable "rds_mappings" {
  description = "Map of client domain names to RDS endpoints"
  type        = map(string)
  default     = {}
}
