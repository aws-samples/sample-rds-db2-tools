# Backend configuration for Terraform state
# Auto-configured by 0-backend-setup/configure-modules.sh

terraform {
  backend "s3" {
    bucket         = "REPLACE_BUCKET_NAME"
    key            = "rdsdb2-proxy/3-mappings/terraform.tfstate"
    region         = "REPLACE_REGION"
    encrypt        = true
    dynamodb_table = "REPLACE_DYNAMODB_TABLE"
  }
}
