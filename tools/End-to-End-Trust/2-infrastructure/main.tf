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

# Use values from prerequisites module if not explicitly provided
locals {
  certificate_secret_arn = var.certificate_secret_arn != "" ? var.certificate_secret_arn : data.terraform_remote_state.prerequisites.outputs.certificate_secret_arn
  certificate_arn        = var.certificate_arn != "" ? var.certificate_arn : data.terraform_remote_state.prerequisites.outputs.certificate_arn
  domain_name            = var.domain_name != "" ? var.domain_name : data.terraform_remote_state.prerequisites.outputs.domain_name
}

data "aws_ami" "amazon_linux_2023" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }
}

# IAM Role for EC2 Instance
resource "aws_iam_role" "instance_role" {
  name = "proxy-ec2-instance-role-${local.domain_name}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "ec2_minimal_access" {
  name = "EC2MinimalAccess"
  role = aws_iam_role.instance_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["ssm:GetParameter"]
        Resource = ["arn:aws:ssm:*:${data.aws_caller_identity.current.account_id}:parameter/rds/proxy/mappings/${local.domain_name}"]
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [local.certificate_secret_arn]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ssm_full_access" {
  role       = aws_iam_role.instance_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "instance_profile" {
  name = "proxy-ec2-instance-profile-${local.domain_name}"
  role = aws_iam_role.instance_role.name
}

data "aws_caller_identity" "current" {}

# ELB service account — required to grant NLB permission to write access logs to S3
data "aws_elb_service_account" "main" {}

# S3 bucket for NLB access logs
resource "aws_s3_bucket" "nlb_logs" {
  bucket        = var.nlb_logs_bucket_name
  force_destroy = true

  tags = {
    Name        = "nlb-access-logs-${local.domain_name}"
    Environment = "Production"
  }
}

resource "aws_s3_bucket_versioning" "nlb_logs" {
  bucket = aws_s3_bucket.nlb_logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "nlb_logs" {
  bucket = aws_s3_bucket.nlb_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "nlb_logs" {
  bucket                  = aws_s3_bucket.nlb_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Bucket policy — grants the regional ELB service account write access for NLB logs
resource "aws_s3_bucket_policy" "nlb_logs" {
  bucket = aws_s3_bucket.nlb_logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "NLBAccessLogs"
        Effect = "Allow"
        Principal = {
          AWS = data.aws_elb_service_account.main.arn
        }
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.nlb_logs.arn}/nlb-access-logs/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
      },
      {
        Sid    = "AWSLogDeliveryWrite"
        Effect = "Allow"
        Principal = {
          Service = "delivery.logs.amazonaws.com"
        }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.nlb_logs.arn}/nlb-access-logs/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl" = "bucket-owner-full-control"
          }
        }
      },
      {
        Sid    = "AWSLogDeliveryAclCheck"
        Effect = "Allow"
        Principal = {
          Service = "delivery.logs.amazonaws.com"
        }
        Action   = "s3:GetBucketAcl"
        Resource = aws_s3_bucket.nlb_logs.arn
      }
    ]
  })
}

# EC2 Launch Template with IMDSv2
resource "aws_launch_template" "ec2_imdsv2" {
  name = "EC2-IMDSV2-proxy-${local.domain_name}"
  metadata_options {
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 1
    http_tokens                 = "required"
  }
}

# EC2 Proxy Security Group - shell only, no inline ingress rules
# Ingress rules are added separately below to avoid circular dependency with nlb_sg
resource "aws_security_group" "ec2_proxy_sg" {
  name        = "ec2-proxy-sg-${local.domain_name}"
  description = "Security group for EC2 proxy instance - allows inbound from NLB only"
  vpc_id      = var.vpc_id

  # Egress: allow all outbound (RDS connections, SSM, Secrets Manager)
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound"
  }

  tags = {
    Name = "ec2-proxy-sg-${local.domain_name}"
  }

  # Prevent Terraform from managing inline rules so aws_security_group_rule resources
  # below don't conflict with the resource block
  lifecycle {
    ignore_changes = [ingress]
  }
}

# EC2 Instance
resource "aws_instance" "proxy" {
  ami                    = data.aws_ami.amazon_linux_2023.id
  instance_type          = var.ec2_instance_type
  subnet_id              = var.ec2_subnet_id
  vpc_security_group_ids = concat(var.security_group_ids, [aws_security_group.ec2_proxy_sg.id])
  iam_instance_profile   = aws_iam_instance_profile.instance_profile.name
  
  launch_template {
    id      = aws_launch_template.ec2_imdsv2.id
    version = "$Latest"
  }

  root_block_device {
    volume_size           = 30
    volume_type           = "gp3"
    iops                  = 3000
    encrypted             = true
    delete_on_termination = true
  }

  user_data = base64encode(templatefile("${path.module}/user_data.sh", {
    aws_region             = var.aws_region
    certificate_secret_arn = local.certificate_secret_arn
    domain_name            = local.domain_name
  }))

  tags = {
    Name    = "proxy-ec2-${local.domain_name}"
    Project = var.project_tag
  }
}

# NLB Security Group (only for internal NLB) - shell only, no inline egress rules
# Egress rules are added separately below to avoid circular dependency with ec2_proxy_sg
resource "aws_security_group" "nlb_sg" {
  count       = var.nlb_scheme == "internal" ? 1 : 0
  name        = "nlb-sg-${local.domain_name}"
  description = "Security group for internal NLB"
  vpc_id      = var.vpc_id

  # Ingress: allow on all listener ports from VPC CIDR (clients connecting in)
  dynamic "ingress" {
    for_each = var.listener_ports
    content {
      from_port   = ingress.value
      to_port     = ingress.value
      protocol    = "tcp"
      cidr_blocks = [var.nlb_cidr]
      description = "Allow traffic on port ${ingress.value}"
    }
  }

  tags = {
    Name = "nlb-sg-${local.domain_name}"
  }

  # Prevent Terraform from managing inline egress so aws_security_group_rule resources
  # below don't conflict with the resource block
  lifecycle {
    ignore_changes = [egress]
  }
}

# -----------------------------------------------------------------------
# Cross-SG rules added AFTER both SGs exist — breaks the circular dependency
# -----------------------------------------------------------------------

# NLB egress → EC2 proxy (one rule per listener port)
resource "aws_security_group_rule" "nlb_egress_to_ec2" {
  for_each = var.nlb_scheme == "internal" ? toset([for p in var.listener_ports : tostring(p)]) : toset([])

  type                     = "egress"
  security_group_id        = aws_security_group.nlb_sg[0].id
  source_security_group_id = aws_security_group.ec2_proxy_sg.id
  from_port                = tonumber(each.value)
  to_port                  = tonumber(each.value)
  protocol                 = "tcp"
  description              = "NLB forward to EC2 proxy on port ${each.value}"
}

# EC2 proxy ingress ← NLB (one rule per listener port, internal NLB only)
resource "aws_security_group_rule" "ec2_ingress_from_nlb" {
  for_each = var.nlb_scheme == "internal" ? toset([for p in var.listener_ports : tostring(p)]) : toset([])

  type                     = "ingress"
  security_group_id        = aws_security_group.ec2_proxy_sg.id
  source_security_group_id = aws_security_group.nlb_sg[0].id
  from_port                = tonumber(each.value)
  to_port                  = tonumber(each.value)
  protocol                 = "tcp"
  description              = "Allow inbound from NLB on port ${each.value}"
}

# EC2 proxy ingress ← VPC CIDR (internet-facing NLB — no SG to reference)
resource "aws_security_group_rule" "ec2_ingress_from_cidr" {
  for_each = var.nlb_scheme == "internet-facing" ? toset([for p in var.listener_ports : tostring(p)]) : toset([])

  type              = "ingress"
  security_group_id = aws_security_group.ec2_proxy_sg.id
  cidr_blocks       = [var.nlb_cidr]
  from_port         = tonumber(each.value)
  to_port           = tonumber(each.value)
  protocol          = "tcp"
  description       = "Allow inbound on port ${each.value} (internet-facing NLB)"
}

# Network Load Balancer
resource "aws_lb" "nlb" {
  name               = "nlb-${replace(local.domain_name, ".", "-")}"
  load_balancer_type = "network"
  internal           = var.nlb_scheme == "internal"
  subnets            = var.subnet_ids
  security_groups    = var.nlb_scheme == "internal" ? [aws_security_group.nlb_sg[0].id] : null

  idle_timeout = 3600

  access_logs {
    bucket  = aws_s3_bucket.nlb_logs.id
    prefix  = "nlb-access-logs"
    enabled = true
  }

  # Ensure bucket policy is in place before NLB tries to write logs
  depends_on = [aws_s3_bucket_policy.nlb_logs]

  tags = {
    Name        = "nlb-${local.domain_name}"
    Environment = "Production"
  }
}

# NOTE: Target groups, attachments, and listeners are now managed by 3-mappings module
# This allows dynamic port management based on RDS mappings

# Route53 Private Hosted Zone
resource "aws_route53_zone" "private" {
  name = local.domain_name

  vpc {
    vpc_id = var.vpc_id
  }

  comment = "Private hosted zone for ${local.domain_name}"

  tags = {
    Name        = "private-zone-${local.domain_name}"
    Environment = "Production"
  }
}

# Wildcard DNS Record
resource "aws_route53_record" "wildcard" {
  zone_id = aws_route53_zone.private.zone_id
  name    = "*.${local.domain_name}"
  type    = "A"

  alias {
    name                   = aws_lb.nlb.dns_name
    zone_id                = aws_lb.nlb.zone_id
    evaluate_target_health = true
  }
}
