variable "aws_region" {
  description = "AWS region (auto-populated from 0-backend-setup if available)"
  type        = string
  default     = "us-east-1"
}

variable "domain_name" {
  description = "Domain name for certificate (e.g., db.mydomain.com)"
  type        = string
}

variable "organization" {
  description = "Organization name for certificate"
  type        = string
  default     = "MyOrganization"
}
