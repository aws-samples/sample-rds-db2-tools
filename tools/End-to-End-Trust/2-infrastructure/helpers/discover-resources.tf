# Helper module to discover available AWS resources
# Run: terraform init && terraform apply
# This uses Terraform service account credentials (same as deployment)

terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

variable "aws_region" {
  description = "AWS region to query"
  type        = string
  default     = "us-east-1"
}

provider "aws" {
  region = var.aws_region
}

# Discover all VPCs
data "aws_vpcs" "available" {}

data "aws_vpc" "all" {
  for_each = toset(data.aws_vpcs.available.ids)
  id       = each.value
}

# Discover all subnets
data "aws_subnets" "available" {}

data "aws_subnet" "all" {
  for_each = toset(data.aws_subnets.available.ids)
  id       = each.value
}

# Discover all security groups
data "aws_security_groups" "available" {}

data "aws_security_group" "all" {
  for_each = toset(data.aws_security_groups.available.ids)
  id       = each.value
}

# Simplified output for script consumption
output "vpc_list" {
  description = "VPC_ID|CIDR|NAME"
  value = [
    for vpc_id, vpc in data.aws_vpc.all : 
    "${vpc_id}|${vpc.cidr_block}|${try(vpc.tags["Name"], "(no name)")}"
  ]
}

output "subnet_list" {
  description = "SUBNET_ID|VPC_ID|AZ|CIDR|NAME"
  value = [
    for subnet_id, subnet in data.aws_subnet.all :
    "${subnet_id}|${subnet.vpc_id}|${subnet.availability_zone}|${subnet.cidr_block}|${try(subnet.tags["Name"], "(no name)")}"
  ]
}

output "sg_list" {
  description = "SG_ID|VPC_ID|NAME|DESCRIPTION"
  value = [
    for sg_id, sg in data.aws_security_group.all :
    "${sg_id}|${sg.vpc_id}|${sg.name}|${sg.description}"
  ]
}
