output "monitoring_role_arn" {
  value = local.monitoring_role_arn
}

output "s3_integration_role_arn" {
  value = var.create_s3_role ? aws_iam_role.s3_integration[0].arn : ""
}

output "directory_service_role_name" {
  value = local.directory_service_role_name
}

output "directory_service_role_arn" {
  value = local.directory_service_role_arn
}

output "audit_role_arn" {
  value = var.create_audit_role ? aws_iam_role.db2_audit[0].arn : ""
}
