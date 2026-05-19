output "certificate_secret_arn" {
  description = "ARN of the Secrets Manager secret containing the certificate"
  value       = aws_secretsmanager_secret.proxy_cert.arn
}

output "certificate_arn" {
  description = "ARN of the ACM certificate"
  value       = aws_acm_certificate.proxy.arn
}

output "domain_name" {
  description = "Domain name"
  value       = var.domain_name
}

output "next_steps" {
  description = "Next steps after prerequisites deployment"
  value       = <<-EOT

==========================================
Prerequisites Deployment Complete!
==========================================

Created:
  ✓ Self-signed SSL certificate for ${var.domain_name}
  ✓ Secrets Manager secret: ${aws_secretsmanager_secret.proxy_cert.name}
  ✓ ACM certificate: ${aws_acm_certificate.proxy.domain_name}

Next steps:

  1. Deploy infrastructure (EC2, NLB, Route53):
     cd ../2-infrastructure
     ./configure-infrastructure.sh  # Interactive setup
     terraform init
     terraform apply --auto-approve --auto-approve

  2. Or manually create terraform.tfvars:
     cp terraform.tfvars.example terraform.tfvars
     # Edit with your VPC, subnets, security groups
     terraform init
     terraform apply --auto-approve

For help: See 2-infrastructure/README.md
==========================================

  EOT
}
