terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = var.tag
      ManagedBy   = "Terraform"
      Environment = var.environment
      Owner       = var.owner
    }
  }
}

locals {
  pg_family = "db2-${var.engine_edition}-${var.engine_major_version}"
  pg_name   = var.parameter_group_name != "" ? var.parameter_group_name : "rds-db2-pg-${replace(local.pg_family, ".", "-")}-${lower(var.tag)}"

  valid_combos = {
    "11.5" = ["se", "ae"]
    "12.1" = ["ce", "se", "ae"]
  }
}

resource "aws_db_parameter_group" "this" {
  name        = local.pg_name
  family      = local.pg_family
  description = "RDS for Db2 parameter group - ${var.tag}"

  parameter {
    name         = "rds.ibm_customer_id"
    value        = var.ibm_customer_id
    apply_method = "immediate"
  }

  parameter {
    name         = "rds.ibm_site_id"
    value        = var.ibm_site_id
    apply_method = "immediate"
  }

  tags = { Name = local.pg_name }

  lifecycle {
    ignore_changes = [description]
    precondition {
      condition     = contains(local.valid_combos[var.engine_major_version], var.engine_edition)
      error_message = "engine_edition '${var.engine_edition}' is not valid for engine_major_version '${var.engine_major_version}'. Valid editions: ${join(", ", local.valid_combos[var.engine_major_version])}."
    }
  }
}
