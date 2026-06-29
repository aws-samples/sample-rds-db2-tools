locals {
  windows_ami_id = data.aws_ssm_parameter.windows_ami.value
}

# --- DC1: first domain controller (creates the forest) ---
resource "aws_instance" "dc1" {
  ami                         = local.windows_ami_id
  instance_type               = var.instance_type
  monitoring                  = true
  ebs_optimized               = true
  subnet_id                   = var.dc_subnet_ids[0]
  private_ip                  = var.dc1_private_ip
  vpc_security_group_ids      = [aws_security_group.dc.id]
  iam_instance_profile        = aws_iam_instance_profile.dc.name
  associate_public_ip_address = false

  user_data = templatefile("${path.module}/templates/dc1_userdata.ps1.tpl", {
    domain_fqdn          = var.domain_fqdn
    domain_netbios       = var.domain_netbios_name
    secret_id            = aws_secretsmanager_secret.ad.arn
    region               = data.aws_region.current.name
    configure_script_b64 = base64encode(local.configure_script)
  })

  metadata_options {
    http_tokens   = "required" # IMDSv2 only
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_size = var.root_volume_size
    volume_type = "gp3"
    encrypted   = true
  }

  tags = {
    Name = "${var.name_prefix}-dc1"
    Role = "primary-domain-controller"
  }

  # Secret version must exist before the instance boots and reads it.
  depends_on = [aws_secretsmanager_secret_version.ad]
}

# --- DC2: additional domain controller (joins the forest) ---
resource "aws_instance" "dc2" {
  ami                         = local.windows_ami_id
  instance_type               = var.instance_type
  monitoring                  = true
  ebs_optimized               = true
  subnet_id                   = var.dc_subnet_ids[1]
  private_ip                  = var.dc2_private_ip
  vpc_security_group_ids      = [aws_security_group.dc.id]
  iam_instance_profile        = aws_iam_instance_profile.dc.name
  associate_public_ip_address = false

  user_data = templatefile("${path.module}/templates/dc2_userdata.ps1.tpl", {
    domain_fqdn    = var.domain_fqdn
    domain_netbios = var.domain_netbios_name
    secret_id      = aws_secretsmanager_secret.ad.arn
    region         = data.aws_region.current.name
    dc1_ip         = var.dc1_private_ip
  })

  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_size = var.root_volume_size
    volume_type = "gp3"
    encrypted   = true
  }

  tags = {
    Name = "${var.name_prefix}-dc2"
    Role = "additional-domain-controller"
  }

  # DC2 waits on DC1 inside user_data, but order the create for clarity.
  depends_on = [aws_instance.dc1, aws_secretsmanager_secret_version.ad]
}
