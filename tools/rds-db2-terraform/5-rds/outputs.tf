output "db_instance_identifier" {
  value = aws_db_instance.this.identifier
}

output "resolved_engine_version" {
  description = "Engine version actually used (auto-resolved if input was blank)"
  value       = aws_db_instance.this.engine_version
}

output "db_endpoint" {
  value = aws_db_instance.this.address
}

output "db_port" {
  value = aws_db_instance.this.port
}

output "db_arn" {
  value = aws_db_instance.this.arn
}

output "managed_master_user_secret_arn" {
  description = "ARN of the RDS-managed master user password secret (empty when manage_master_user_password=false)"
  value       = var.manage_master_user_password ? try(aws_db_instance.this.master_user_secret[0].secret_arn, "") : ""
}
