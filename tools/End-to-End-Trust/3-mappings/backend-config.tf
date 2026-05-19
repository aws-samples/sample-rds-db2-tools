# Fetch configuration from backend-setup module
# Auto-configured by 0-backend-setup/configure-modules.sh

data "terraform_remote_state" "backend_setup" {
  backend = "s3"

  config = {
    bucket = "REPLACE_BUCKET_NAME"
    key    = "rdsdb2-proxy/0-backend-setup/terraform.tfstate"
    region = "REPLACE_REGION"
  }
}
