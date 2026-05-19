# Data source to fetch outputs from prerequisites module
# Auto-configured by 0-backend-setup/configure-modules.sh

data "terraform_remote_state" "prerequisites" {
  backend = "s3"

  config = {
    bucket = "REPLACE_BUCKET_NAME"
    key    = "rdsdb2-proxy/1-prerequisites/terraform.tfstate"
    region = "REPLACE_REGION"
  }
}
