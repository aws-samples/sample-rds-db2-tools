output "deploy_role_arn" {
  description = "Paste this into the repo SECRET RDS_DB2_DEPLOY_ROLE_ARN."
  value       = aws_iam_role.deploy.arn
}

output "oidc_provider_arn" {
  description = "ARN of the GitHub OIDC provider used by the trust policy."
  value       = local.oidc_provider_arn
}

output "next_steps" {
  description = "What to do after apply."
  value = join("\n", [
    "1. Set repo SECRET  RDS_DB2_DEPLOY_ROLE_ARN = ${aws_iam_role.deploy.arn}",
    "2. Set repo VARIABLES: RDS_DB2_ENABLE_PLAN=true, AWS_REGION=${var.aws_region}, RDS_DB2_STATE_BUCKET=${var.state_bucket}, RDS_DB2_MODULE_REF=<release tag>",
    "3. Open a PR that adds a deployments/<id>/ folder -> the 'plan' job runs (read-only).",
    "4. Merge -> the 'apply' job runs (gate it with a GitHub Environment reviewer if you want a manual approval).",
  ])
}
