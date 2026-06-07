terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = local.common_tags
  }
}

data "aws_caller_identity" "current" {}

locals {
  common_tags = merge(
    var.extra_tags,
    {
      Project     = var.tag
      ManagedBy   = "Terraform"
      Environment = var.environment
      Owner       = var.owner
    },
    var.created_by != "" ? { created_by = var.created_by } : {},
    var.generation_model != "" ? { generation_model = var.generation_model } : {},
  )
}

# ── Self-managed IBM Db2 license in AWS License Manager ──────────────────────
# Reference: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/db2-licensing.html
#
# License Manager auto-discovers RDS for Db2 instances that match a
# `ProductInformationList` filter on "Engine Edition" = db2-se|db2-ae|db2-ce.
# The Terraform AWS provider's aws_licensemanager_license_configuration does
# NOT expose product_information_list (as of provider 5.x/6.x), so we apply it
# via AWS CLI in a post-create null_resource. Discovery can take up to 24h.

locals {
  engine_edition_map = {
    CE = "db2-ce"
    SE = "db2-se"
    AE = "db2-ae"
  }
  product_filter_value = local.engine_edition_map[var.db2_edition]
}

resource "aws_licensemanager_license_configuration" "db2" {
  name                     = "IBM-Db2-${var.db2_edition}-${var.tag}"
  description              = "Self-managed IBM Db2 ${var.db2_edition} license - ${var.tag}"
  license_counting_type    = "vCPU"
  license_count            = var.license_count
  license_count_hard_limit = false

  tags = local.common_tags
}

# Attach the RDS engine-edition product filter so License Manager auto-discovers
# matching RDS for Db2 instances. Runs on create and whenever license_count or
# edition change.
resource "null_resource" "attach_product_filter" {
  triggers = {
    license_arn    = aws_licensemanager_license_configuration.db2.arn
    edition        = var.db2_edition
    engine_edition = local.product_filter_value
    license_count  = var.license_count
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      aws license-manager update-license-configuration \
        --region ${var.aws_region} \
        --license-configuration-arn "${aws_licensemanager_license_configuration.db2.arn}" \
        --product-information-list '[{
          "ResourceType": "RDS",
          "ProductInformationFilterList": [{
            "ProductInformationFilterName": "Engine Edition",
            "ProductInformationFilterValue": ["${local.product_filter_value}"],
            "ProductInformationFilterComparator": "EQUALS"
          }]
        }]'
    EOT
  }
}
