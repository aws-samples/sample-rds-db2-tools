terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# Generate private key - ECDSA P-256 provides equivalent security to RSA 3072 with better performance
resource "tls_private_key" "proxy" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

# Generate self-signed certificate
resource "tls_self_signed_cert" "proxy" {
  private_key_pem = tls_private_key.proxy.private_key_pem

  subject {
    common_name  = "*.${var.domain_name}"
    organization = var.organization
  }

  validity_period_hours = 8760 # 1 year

  allowed_uses = [
    "key_agreement",
    "digital_signature",
    "server_auth",
  ]

  dns_names = ["*.${var.domain_name}"]
}

# Store certificate in Secrets Manager
resource "aws_secretsmanager_secret" "proxy_cert" {
  name                    = "/rdx/proxy/certificate/${var.domain_name}"
  # recovery_window_in_days controls how long the secret is retained after deletion.
  # For production/shipping: use 7 (minimum allowed by AWS) to protect against accidental deletion.
  # For dev/test cycles: set to 0 to allow immediate deletion and recreation with the same name.
  # WARNING: 0 disables deletion protection — do not use in production.
  recovery_window_in_days = 7
  description             = "Proxy certificate for ${var.domain_name}"

  lifecycle {
    ignore_changes = [recovery_window_in_days]
  }
}

resource "aws_secretsmanager_secret_version" "proxy_cert" {
  secret_id = aws_secretsmanager_secret.proxy_cert.id
  secret_string = jsonencode({
    certificate = tls_self_signed_cert.proxy.cert_pem
    privateKey  = tls_private_key.proxy.private_key_pem
  })
}

# Import certificate to ACM
resource "aws_acm_certificate" "proxy" {
  private_key      = tls_private_key.proxy.private_key_pem
  certificate_body = tls_self_signed_cert.proxy.cert_pem

  tags = {
    Name = "proxy-cert-${var.domain_name}"
  }
}
