output "s3_bucket_name" {
  description = "Name of the S3 bucket for Terraform state"
  value       = aws_s3_bucket.terraform_state.id
}

output "s3_bucket_arn" {
  description = "ARN of the S3 bucket"
  value       = aws_s3_bucket.terraform_state.arn
}

output "kms_key_arn" {
  description = "ARN of the KMS key used to encrypt state files"
  value       = aws_kms_key.terraform_state.arn
}

output "kms_key_alias" {
  description = "Alias of the KMS key"
  value       = aws_kms_alias.terraform_state.name
}

output "dynamodb_table_name" {
  description = "Name of the DynamoDB table for state locking"
  value       = aws_dynamodb_table.terraform_lock.name
}

output "aws_region" {
  description = "AWS region for all modules"
  value       = var.aws_region
}

output "backend_config" {
  description = "Next steps after backend setup"
  value = <<-EOT
    ✅ Backend setup complete!
    
    Next step: Run the configuration script
    
      ./configure-modules.sh
    
    This will automatically update all modules with:
      - Bucket: ${aws_s3_bucket.terraform_state.id}
      - Region: ${var.aws_region}
      - DynamoDB: ${aws_dynamodb_table.terraform_lock.name}
      - Prefix: rdsdb2-proxy/
  EOT
}
