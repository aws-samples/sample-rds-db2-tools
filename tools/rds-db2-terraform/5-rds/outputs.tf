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

output "standby_replica_arn" {
  description = "ARN of the cross-region mounted standby replica (empty when create_standby_replica=false)"
  value       = var.create_standby_replica ? aws_db_instance.standby_replica[0].arn : ""
}

output "standby_replica_endpoint" {
  description = "Endpoint address of the cross-region mounted standby replica (empty when create_standby_replica=false)"
  value       = var.create_standby_replica ? aws_db_instance.standby_replica[0].address : ""
}

output "read_replica_arn" {
  description = "ARN of the same-region read replica (empty when create_read_replica=false)"
  value       = var.create_read_replica ? aws_db_instance.read_replica[0].arn : ""
}

output "read_replica_endpoint" {
  description = "Endpoint address of the same-region read replica (empty when create_read_replica=false)"
  value       = var.create_read_replica ? aws_db_instance.read_replica[0].address : ""
}
