locals {
  # Build the domain base DN from the FQDN: corp.example.com -> DC=corp,DC=example,DC=com
  base_dn = join(",", [for part in split(".", var.domain_fqdn) : "DC=${part}"])

  # Distinguished name of the OU that holds RDS for Db2 principals.
  ou_dn = "OU=${var.ou_name},${local.base_dn}"

  # aws:SourceArn used in the secret resource policy (confused-deputy guard).
  rds_source_arn = var.rds_db_arn_pattern != "" ? var.rds_db_arn_pattern : "arn:${data.aws_partition.current.partition}:rds:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:db:*"

  # DC1 post-promotion configuration script (OU + service account + ACLs),
  # rendered here and injected into DC1 user_data as opaque base64.
  configure_script = templatefile("${path.module}/templates/configure-rds-ad.ps1.tpl", {
    region    = data.aws_region.current.name
    secret_id = aws_secretsmanager_secret.ad.arn
  })
}
