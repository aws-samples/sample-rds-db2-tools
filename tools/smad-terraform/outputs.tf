output "partition" {
  description = "Active AWS partition (aws or aws-us-gov)."
  value       = data.aws_partition.current.partition
}

output "region" {
  value = data.aws_region.current.name
}

output "vpc_id" {
  value = data.aws_vpc.selected.id
}

output "dc_subnet_ids" {
  value = var.dc_subnet_ids
}

output "domain_fqdn" {
  value = var.domain_fqdn
}

output "dc1_private_ip" {
  value = aws_instance.dc1.private_ip
}

output "dc2_private_ip" {
  value = aws_instance.dc2.private_ip
}

output "dc1_instance_id" {
  value = aws_instance.dc1.id
}

output "dc2_instance_id" {
  value = aws_instance.dc2.id
}

output "dc_security_group_id" {
  value = aws_security_group.dc.id
}

output "ad_credentials_secret_arn" {
  description = "Secrets Manager ARN holding the domain admin + DSRM passwords."
  value       = aws_secretsmanager_secret.ad.arn
}

output "domain_dns_ips" {
  description = "Use these as --domain-dns-ips when joining RDS for Db2 to the domain."
  value       = [aws_instance.dc1.private_ip, aws_instance.dc2.private_ip]
}

# --- RDS for Db2 self-managed AD integration ---
output "domain_ou" {
  description = "Distinguished name of the OU created for RDS for Db2 principals. Pass as --domain-ou."
  value       = local.ou_dn
}

output "service_account_name" {
  description = "sAMAccountName of the delegated AD service account."
  value       = var.svc_account_name
}

output "rds_self_managed_ad_secret_arn" {
  description = "Secret ARN to pass as --domain-auth-secret-arn when joining RDS for Db2."
  value       = aws_secretsmanager_secret.rds_self_managed_ad.arn
}

output "ad_secret_kms_key_arn" {
  description = "KMS key encrypting the RDS self-managed AD secret."
  value       = aws_kms_key.ad_secret.arn
}

output "rds_join_command_hint" {
  description = "Template for joining an existing RDS for Db2 instance to the domain."
  value       = "aws rds modify-db-instance --db-instance-identifier <id> --domain-fqdn ${var.domain_fqdn} --domain-ou '${local.ou_dn}' --domain-auth-secret-arn ${aws_secretsmanager_secret.rds_self_managed_ad.arn} --domain-dns-ips ${var.dc1_private_ip} ${var.dc2_private_ip} --apply-immediately --region ${data.aws_region.current.name}"
}
