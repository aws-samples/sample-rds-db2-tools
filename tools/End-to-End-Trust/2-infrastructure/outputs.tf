output "ec2_instance_id" {
  description = "ID of the created EC2 instance"
  value       = aws_instance.proxy.id
}

output "ec2_private_ip" {
  description = "Private IP address of the EC2 instance"
  value       = aws_instance.proxy.private_ip
}

output "nlb_dns_name" {
  description = "DNS name of the NLB"
  value       = aws_lb.nlb.dns_name
}

output "nlb_arn" {
  description = "ARN of the NLB"
  value       = aws_lb.nlb.arn
}

output "nlb_zone_id" {
  description = "Canonical Hosted Zone ID of the NLB"
  value       = aws_lb.nlb.zone_id
}

output "nlb_security_group_id" {
  description = "Security Group ID of the NLB (only for internal NLB)"
  value       = var.nlb_scheme == "internal" ? aws_security_group.nlb_sg[0].id : null
}

output "ec2_proxy_security_group_id" {
  description = "Security Group ID of the EC2 proxy instance (scoped NLB egress target)"
  value       = aws_security_group.ec2_proxy_sg.id
}

output "nlb_logs_bucket_name" {
  description = "Name of the S3 bucket storing NLB access logs"
  value       = aws_s3_bucket.nlb_logs.id
}

output "hosted_zone_id" {
  description = "ID of the private hosted zone"
  value       = aws_route53_zone.private.zone_id
}

output "hosted_zone_name_servers" {
  description = "Name servers for the private hosted zone"
  value       = aws_route53_zone.private.name_servers
}

output "vpc_id" {
  description = "VPC ID for target group creation"
  value       = var.vpc_id
}

output "domain_name" {
  description = "Domain name for naming resources"
  value       = local.domain_name
}

output "next_steps" {
  description = "Next steps after infrastructure deployment"
  value       = <<-EOT

==========================================
Infrastructure Deployment Complete!
==========================================

Created:
  ✓ EC2 proxy instance: ${aws_instance.proxy.id}
  ✓ Private IP: ${aws_instance.proxy.private_ip}
  ✓ Network Load Balancer: ${aws_lb.nlb.dns_name}
  ✓ Route53 private zone: ${aws_route53_zone.private.name}
  ✓ Wildcard DNS: *.${local.domain_name} -> NLB

Next steps:

  1. Configure RDS mappings:
     cd ../3-mappings
     cp terraform.tfvars.example terraform.tfvars

  2. Edit terraform.tfvars with your RDS endpoints:
     rds_mappings = {
       "proddb.${local.domain_name}:1443" = "mydb.region.rds.amazonaws.com:50000"
     }

  3. Deploy mappings:
     terraform init
     terraform apply --auto-approve

  4. Wait 5 minutes for cron job to update proxy config

For help: See 3-mappings/README.md
==========================================

  EOT
}
