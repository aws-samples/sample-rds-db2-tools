variable "aws_region" {
  description = "AWS region the deployments target (matches the repo AWS_REGION variable)."
  type        = string
}

variable "github_owner" {
  description = "GitHub org/user that owns the gitops repo (e.g. your-org or your-username)."
  type        = string
}

variable "github_repo" {
  description = "The gitops repository name (e.g. rds-db2-deployer-gitops)."
  type        = string
}

variable "default_branch" {
  description = "Default branch that applies on merge (the apply job runs on push to this branch)."
  type        = string
  default     = "main"
}

variable "role_name" {
  description = "Name of the IAM role GitHub Actions assumes via OIDC."
  type        = string
  default     = "rds-db2-gitops-deploy"
}

variable "state_bucket" {
  description = "S3 bucket holding Terraform remote state (matches RDS_DB2_STATE_BUCKET)."
  type        = string
}

variable "lock_table" {
  description = "DynamoDB table used for Terraform state locking (0-backend-setup default)."
  type        = string
  default     = "rds-db2-terraform-lock"
}

variable "create_oidc_provider" {
  description = "Create the GitHub OIDC provider. Set false if the account already has one and pass existing_oidc_provider_arn."
  type        = bool
  default     = true
}

variable "existing_oidc_provider_arn" {
  description = "ARN of a pre-existing GitHub OIDC provider (used only when create_oidc_provider=false)."
  type        = string
  default     = ""
}
