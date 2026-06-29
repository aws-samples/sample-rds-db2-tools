# Partition-agnostic provider. Works in both GovCloud (aws-us-gov) and
# commercial (aws) partitions. The region and named profile are supplied
# via variables so the same code runs against us-gov-east-1 or any
# commercial region without edits.
provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = var.tags
  }
}

# Resolved at plan time from the active credentials/region.
data "aws_partition" "current" {}
data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# Latest Windows Server AMI for the target region. The SSM public parameter
# resolves correctly in both GovCloud and commercial partitions.
data "aws_ssm_parameter" "windows_ami" {
  name = var.windows_ami_ssm_parameter
}
