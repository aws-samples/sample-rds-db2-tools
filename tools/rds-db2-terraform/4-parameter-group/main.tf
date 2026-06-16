terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = local.default_resource_tags
  }
}

# Mandatory tag set (R14) applied to every created resource via default_tags.
# Customer-supplied extra_tags are merged FIRST so the mandatory keys, merged
# last, always win — an extra tag can never override a mandatory key (R14.4).
locals {
  default_resource_tags = merge(
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

locals {
  pg_family = "db2-${var.engine_edition}-${var.engine_major_version}"
  pg_name   = var.parameter_group_name != "" ? var.parameter_group_name : "rds-db2-pg-${replace(local.pg_family, ".", "-")}-${lower(var.tag)}"

  valid_combos = {
    "11.5" = ["se", "ae"]
    "12.1" = ["ce", "se", "ae"]
  }
}

# Optional: read the IBM IDs from SSM Parameter Store (SecureString) at apply, so
# the values never live in the deployment repo. When the *_ssm name is set, the
# decrypted SSM value is used; otherwise the literal var is used (backward
# compatible with callers that pass ibm_customer_id / ibm_site_id directly).
data "aws_ssm_parameter" "ibm_customer_id" {
  count           = var.ibm_customer_id_ssm != "" ? 1 : 0
  name            = var.ibm_customer_id_ssm
  with_decryption = true
}

data "aws_ssm_parameter" "ibm_site_id" {
  count           = var.ibm_site_id_ssm != "" ? 1 : 0
  name            = var.ibm_site_id_ssm
  with_decryption = true
}

locals {
  ibm_customer_id_value = var.ibm_customer_id_ssm != "" ? data.aws_ssm_parameter.ibm_customer_id[0].value : var.ibm_customer_id
  ibm_site_id_value     = var.ibm_site_id_ssm != "" ? data.aws_ssm_parameter.ibm_site_id[0].value : var.ibm_site_id
}

resource "aws_db_parameter_group" "this" {
  name        = local.pg_name
  family      = local.pg_family
  description = "RDS for Db2 parameter group - ${var.tag}"

  parameter {
    name         = "rds.ibm_customer_id"
    value        = local.ibm_customer_id_value
    apply_method = "immediate"
  }

  parameter {
    name         = "rds.ibm_site_id"
    value        = local.ibm_site_id_value
    apply_method = "immediate"
  }

  # ── Security invariants (R6.2): SSL-only Db2 communication ────────────────
  # DB2COMM=SSL (NOT "tcpip,ssl") keeps the non-SSL TCP listener dormant, and
  # ssl_svcename pins the SSL service to port 50443. These are rendered for
  # EVERY produced deployment regardless of prompt wording (R6.2/R6.7): they are
  # constants, not customer inputs, so they are hardcoded here (mirroring the
  # structure of the IBM-ID parameters above) rather than exposed as variables.
  # The Terraform_Composer (render_terraform.py) relies on this module always
  # carrying them; see DB2_SECURITY_PARAMETERS there.
  parameter {
    name         = "DB2COMM"
    value        = "SSL"
    apply_method = "pending-reboot"
  }

  parameter {
    name         = "ssl_svcename"
    value        = "50443"
    apply_method = "pending-reboot"
  }

  tags = { Name = local.pg_name }

  lifecycle {
    ignore_changes = [description]
    precondition {
      condition     = contains(local.valid_combos[var.engine_major_version], var.engine_edition)
      error_message = "engine_edition '${var.engine_edition}' is not valid for engine_major_version '${var.engine_major_version}'. Valid editions: ${join(", ", local.valid_combos[var.engine_major_version])}."
    }
    precondition {
      condition     = (var.ibm_customer_id != "") != (var.ibm_customer_id_ssm != "")
      error_message = "Provide exactly one of ibm_customer_id or ibm_customer_id_ssm (a literal value OR an SSM parameter name)."
    }
    precondition {
      condition     = (var.ibm_site_id != "") != (var.ibm_site_id_ssm != "")
      error_message = "Provide exactly one of ibm_site_id or ibm_site_id_ssm (a literal value OR an SSM parameter name)."
    }
  }
}
