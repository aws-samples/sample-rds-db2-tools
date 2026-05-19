terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# Extract unique ports from mappings
locals {
  # Extract client ports from mapping keys (format: "domain:port")
  client_ports = distinct([for key in keys(var.rds_mappings) : tonumber(split(":", key)[1])])
}

# Get infrastructure outputs
data "terraform_remote_state" "infrastructure" {
  backend = "s3"
  config = {
    bucket = data.terraform_remote_state.backend_setup.outputs.s3_bucket_name
    key    = "rdsdb2-proxy/2-infrastructure/terraform.tfstate"
    region = data.terraform_remote_state.backend_setup.outputs.aws_region
  }
}

# SSM Parameter for RDS mappings
resource "aws_ssm_parameter" "rds_mappings" {
  name        = "/rds/proxy/mappings/${data.terraform_remote_state.infrastructure.outputs.domain_name}"
  description = "RDS domain to endpoint mappings for ${data.terraform_remote_state.infrastructure.outputs.domain_name}"
  type        = "String"
  value       = jsonencode(var.rds_mappings)

  tags = {
    Name        = "rds-proxy-mappings-${replace(data.terraform_remote_state.infrastructure.outputs.domain_name, ".", "-")}"
    Domain      = data.terraform_remote_state.infrastructure.outputs.domain_name
    ManagedBy   = "Terraform"
    LastUpdated = timestamp()
  }

  lifecycle {
    ignore_changes = [tags["LastUpdated"]]
  }
}

# Dynamically create target groups for each unique port
resource "aws_lb_target_group" "dynamic" {
  for_each = toset([for port in local.client_ports : tostring(port)])
  
  name        = "tg-${replace(data.terraform_remote_state.infrastructure.outputs.domain_name, ".", "-")}-${each.value}"
  port        = tonumber(each.value)
  protocol    = "TCP"
  vpc_id      = data.terraform_remote_state.infrastructure.outputs.vpc_id
  target_type = "instance"

  health_check {
    protocol = "TCP"
    port     = tonumber(each.value)
  }

  tags = {
    Name        = "tg-dynamic-${each.value}"
    ManagedBy   = "Terraform-3-mappings"
    Port        = each.value
  }
}

# Register EC2 instance to each target group
resource "aws_lb_target_group_attachment" "dynamic" {
  for_each = toset([for port in local.client_ports : tostring(port)])
  
  target_group_arn = aws_lb_target_group.dynamic[each.value].arn
  target_id        = data.terraform_remote_state.infrastructure.outputs.ec2_instance_id
  port             = tonumber(each.value)
}

# Create NLB listeners for each port
resource "aws_lb_listener" "dynamic" {
  for_each = toset([for port in local.client_ports : tostring(port)])
  
  load_balancer_arn = data.terraform_remote_state.infrastructure.outputs.nlb_arn
  port              = tonumber(each.value)
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.dynamic[each.value].arn
  }

  tags = {
    Port      = each.value
    ManagedBy = "Terraform-3-mappings"
  }
}
