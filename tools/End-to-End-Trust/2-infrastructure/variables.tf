variable "aws_region" {
  description = "AWS region (auto-populated from 0-backend-setup if available)"
  type        = string
  default     = "us-east-1"
}

variable "vpc_id" {
  description = "VPC ID for resources"
  type        = string
}

variable "subnet_ids" {
  description = "List of subnet IDs (at least two for NLB high availability)"
  type        = list(string)
}

variable "ec2_subnet_id" {
  description = "Subnet ID for EC2 instance"
  type        = string
}

variable "security_group_ids" {
  description = "List of security group IDs for EC2 instance"
  type        = list(string)
}

variable "certificate_secret_arn" {
  description = "ARN of Secrets Manager secret containing certificate and private key (auto-populated from prerequisites)"
  type        = string
  default     = ""
}

variable "certificate_arn" {
  description = "ARN of ACM certificate for NLB (auto-populated from prerequisites)"
  type        = string
  default     = ""
}

variable "domain_name" {
  description = "Domain name for Route53 private hosted zone (auto-populated from prerequisites)"
  type        = string
  default     = ""
}

variable "project_tag" {
  description = "Project tag for EC2 instance"
  type        = string
}

variable "nlb_scheme" {
  description = "NLB scheme - internal or internet-facing"
  type        = string
  default     = "internal"
  validation {
    condition     = contains(["internal", "internet-facing"], var.nlb_scheme)
    error_message = "NLB scheme must be either 'internal' or 'internet-facing'."
  }
}

variable "ec2_instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.small"
}

variable "nlb_cidr" {
  description = "CIDR block for NLB security group ingress/egress"
  type        = string
  default     = "10.0.0.0/16"
}

variable "listener_ports" {
  description = "List of ports for NLB listeners and EC2 proxy (e.g., [443, 50001, 1443])"
  type        = list(number)
  default     = [443, 50001, 1443, 50443]
}

variable "nlb_logs_bucket_name" {
  description = "Name of the S3 bucket for NLB access logs. Must be globally unique."
  type        = string
}
