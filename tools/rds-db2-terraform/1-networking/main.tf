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

data "aws_vpc" "selected" {
  id = var.vpc_id
}

# ── Subnet classification ────────────────────────────────────────────────────

data "aws_subnets" "all" {
  filter {
    name   = "vpc-id"
    values = [var.vpc_id]
  }
}

data "aws_subnet" "each" {
  for_each = toset(data.aws_subnets.all.ids)
  id       = each.value
}

data "aws_route_tables" "vpc" {
  filter {
    name   = "vpc-id"
    values = [var.vpc_id]
  }
}

# Identify public subnets (those whose route table has an IGW default route)
data "aws_route_table" "each" {
  for_each  = toset(data.aws_subnets.all.ids)
  subnet_id = each.value
}

locals {
  common_tags = {
    Project     = var.tag
    ManagedBy   = "Terraform"
    Environment = var.environment
    Owner       = var.owner
  }
  public_subnet_ids = [
    for sid, rt in data.aws_route_table.each :
    sid if anytrue([
      for r in rt.routes : can(regex("^igw-", r.gateway_id))
    ])
  ]
  private_subnet_ids = [
    for sid in data.aws_subnets.all.ids :
    sid if !contains(local.public_subnet_ids, sid)
  ]
  chosen_subnet_ids = (
    var.publicly_accessible
    ? (length(local.public_subnet_ids) > 0 ? local.public_subnet_ids : local.private_subnet_ids)
    : (length(local.private_subnet_ids) > 0 ? local.private_subnet_ids : local.public_subnet_ids)
  )
}

# ── DB Subnet Group ──────────────────────────────────────────────────────────

resource "aws_db_subnet_group" "this" {
  count       = var.db_subnet_group_name == "" ? 1 : 0
  name        = "rds-db2-${lower(var.tag)}-${var.publicly_accessible ? "public" : "private"}"
  subnet_ids  = local.chosen_subnet_ids
  description = "RDS for Db2 subnet group - ${var.tag}"

  tags = { Name = "rds-db2-subnet-group-${lower(var.tag)}" }
}

locals {
  subnet_group_name = var.db_subnet_group_name != "" ? var.db_subnet_group_name : aws_db_subnet_group.this[0].name
}

# ── S3 Gateway Endpoint ──────────────────────────────────────────────────────

data "aws_vpc_endpoint" "s3_existing" {
  count        = 1
  vpc_id       = var.vpc_id
  service_name = "com.amazonaws.${var.aws_region}.s3"
  state        = "available"
}

resource "aws_vpc_endpoint" "s3" {
  count        = length(data.aws_vpc_endpoint.s3_existing) == 0 ? 1 : 0
  vpc_id       = var.vpc_id
  service_name = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"

  route_table_ids = data.aws_route_tables.vpc.ids

  tags = { Name = "s3-gateway-${var.tag}" }
}

# ── Interface Endpoints (optional, for private VPCs) ─────────────────────────

locals {
  interface_services = var.create_interface_endpoints ? {
    rds              = "com.amazonaws.${var.aws_region}.rds"
    secretsmanager   = "com.amazonaws.${var.aws_region}.secretsmanager"
    monitoring       = "com.amazonaws.${var.aws_region}.monitoring"
    logs             = "com.amazonaws.${var.aws_region}.logs"
  } : {}
}

resource "aws_vpc_endpoint" "interface" {
  for_each            = local.interface_services
  vpc_id              = var.vpc_id
  service_name        = each.value
  vpc_endpoint_type   = "Interface"
  subnet_ids          = local.private_subnet_ids
  security_group_ids  = [var.security_group_id]
  private_dns_enabled = true

  tags = { Name = "${each.key}-endpoint-${var.tag}" }
}
