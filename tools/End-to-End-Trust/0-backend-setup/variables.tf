variable "aws_region" {
  description = "AWS region for backend resources"
  type        = string
  default     = "us-east-1"
}

variable "state_bucket_name" {
  description = "Name of S3 bucket for Terraform state"
  type        = string
}

variable "lock_table_name" {
  description = "Name of DynamoDB table for state locking"
  type        = string
  default     = "terraform-state-lock"
}

variable "terraform_principal_arn" {
  description = <<-EOT
    ARN of the IAM principal (user, role, or SSO role) used to run Terraform.
    This principal is granted exclusive s3:GetObject access to the state bucket
    and kms:Decrypt access to the state encryption key.
    Examples:
      IAM user : arn:aws:iam::123456789012:user/terraform-rds-proxy
      IAM role : arn:aws:iam::123456789012:role/TerraformRDSProxyRole
      SSO role : arn:aws:iam::123456789012:role/aws-reserved/sso.amazonaws.com/AWSReservedSSO_TerraformRDSProxy_xxxx
  EOT
  type        = string
}
