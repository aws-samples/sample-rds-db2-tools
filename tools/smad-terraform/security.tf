# Security group for the domain controllers. Allows full DC-to-DC traffic
# (self-referencing) for AD replication, the documented AD client port matrix
# from within the VPC, and optional RDP from a specific admin CIDR.
resource "aws_security_group" "dc" {
  name        = "${var.name_prefix}-dc-sg"
  description = "Self-managed AD domain controllers"
  vpc_id      = data.aws_vpc.selected.id

  tags = { Name = "${var.name_prefix}-dc-sg" }
}

# DC-to-DC: allow everything between domain controllers (replication, RPC, etc.)
resource "aws_security_group_rule" "dc_self" {
  type                     = "ingress"
  security_group_id        = aws_security_group.dc.id
  from_port                = 0
  to_port                  = 0
  protocol                 = "-1"
  source_security_group_id = aws_security_group.dc.id
  description              = "All traffic between domain controllers"
}

# AD client/service ports from within the VPC (TCP + UDP where applicable).
locals {
  ad_tcp_ports = [53, 88, 135, 389, 445, 464, 636, 3268, 3269, 9389]
  ad_udp_ports = [53, 88, 123, 389, 464]
}

resource "aws_security_group_rule" "ad_tcp" {
  for_each          = toset([for p in local.ad_tcp_ports : tostring(p)])
  type              = "ingress"
  security_group_id = aws_security_group.dc.id
  from_port         = tonumber(each.value)
  to_port           = tonumber(each.value)
  protocol          = "tcp"
  cidr_blocks       = [data.aws_vpc.selected.cidr_block]
  description       = "AD TCP ${each.value} from VPC"
}

resource "aws_security_group_rule" "ad_udp" {
  for_each          = toset([for p in local.ad_udp_ports : tostring(p)])
  type              = "ingress"
  security_group_id = aws_security_group.dc.id
  from_port         = tonumber(each.value)
  to_port           = tonumber(each.value)
  protocol          = "udp"
  cidr_blocks       = [data.aws_vpc.selected.cidr_block]
  description       = "AD UDP ${each.value} from VPC"
}

# RPC dynamic port range from within the VPC (TCP).
resource "aws_security_group_rule" "ad_rpc_dynamic" {
  type              = "ingress"
  security_group_id = aws_security_group.dc.id
  from_port         = 49152
  to_port           = 65535
  protocol          = "tcp"
  cidr_blocks       = [data.aws_vpc.selected.cidr_block]
  description       = "RPC dynamic ports from VPC"
}

# Optional RDP from a specific admin CIDR (disabled when rdp_ingress_cidr == "").
resource "aws_security_group_rule" "rdp" {
  count             = var.rdp_ingress_cidr == "" ? 0 : 1
  type              = "ingress"
  security_group_id = aws_security_group.dc.id
  from_port         = 3389
  to_port           = 3389
  protocol          = "tcp"
  cidr_blocks       = [var.rdp_ingress_cidr]
  description       = "RDP from admin CIDR"
}

resource "aws_security_group_rule" "egress_all" {
  #checkov:skip=CKV_AWS_382:Domain controllers require outbound internet via NAT during bootstrap (PowerShell Gallery, Secrets Manager, Windows activation). Restrict with VPC endpoints in locked-down environments.
  type              = "egress"
  security_group_id = aws_security_group.dc.id
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  description       = "All outbound"
}
