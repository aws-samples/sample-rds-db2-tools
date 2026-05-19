output "parameter_name" {
  description = "SSM parameter name"
  value       = aws_ssm_parameter.rds_mappings.name
}

output "parameter_arn" {
  description = "SSM parameter ARN"
  value       = aws_ssm_parameter.rds_mappings.arn
}

output "mappings" {
  description = "Current RDS mappings"
  value       = var.rds_mappings
}

output "managed_ports" {
  description = "Ports dynamically managed by this module"
  value       = local.client_ports
}

output "target_groups" {
  description = "Dynamically created target groups"
  value       = { for k, v in aws_lb_target_group.dynamic : k => v.arn }
}

output "next_steps" {
  description = "Next steps after mappings deployment"
  value       = <<-EOT

==========================================
Mappings Deployment Complete!
==========================================

Configured:
  ✓ RDS mappings: ${length(var.rds_mappings)} entries
  ✓ Managed ports: ${join(", ", local.client_ports)}
  ✓ SSM parameter: ${aws_ssm_parameter.rds_mappings.name}
  ✓ NLB listeners: ${length(local.client_ports)} ports
  ✓ Target groups: ${length(local.client_ports)} groups

Mappings:
%{for domain, endpoint in var.rds_mappings~}
  ${domain} -> ${endpoint}
%{endfor~}

NLB DNS (for testing):
  ${data.terraform_remote_state.infrastructure.outputs.nlb_dns_name}

Next steps:

  1. Wait 5 minutes for cron job to update proxy configuration

  2. Validate deployment:
     cd ../4-health-check
     terraform init
     terraform apply --auto-approve

  3. Test connection from client:
     nslookup <your-domain>
     db2 connect to <dsn-alias> user <user> using <pass>

For help: See 4-health-check/README.md
==========================================

  EOT
}
