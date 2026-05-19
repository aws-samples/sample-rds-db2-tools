output "state_bucket_name" {
  value = aws_s3_bucket.terraform_state.bucket
}

output "lock_table_name" {
  value = aws_dynamodb_table.terraform_lock.name
}

output "aws_region" {
  value = var.aws_region
}
