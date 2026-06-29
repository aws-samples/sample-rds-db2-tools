# Look up the existing VPC and the two DC subnets. Nothing here is created;
# these data sources drive the security-group CIDR and validate the targets.
data "aws_vpc" "selected" {
  id = var.vpc_id
}

data "aws_subnet" "dc" {
  count = 2
  id    = var.dc_subnet_ids[count.index]
}
