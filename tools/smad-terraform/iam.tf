# Instance role allowing SSM management (Session/Fleet Manager) and reading
# the AD credentials secret. Partition-agnostic via aws_partition.
data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dc" {
  name               = "${var.name_prefix}-dc-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
  tags               = { Name = "${var.name_prefix}-dc-role" }
}

# Managed policy for SSM agent connectivity (works in aws and aws-us-gov).
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.dc.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Least-privilege read of just the AD credentials secret.
data "aws_iam_policy_document" "secret_read" {
  statement {
    sid       = "ReadADSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.ad.arn]
  }
  # Required because the bootstrap secret is encrypted with the customer-managed CMK.
  statement {
    sid       = "DecryptADSecret"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.ad_secret.arn]
  }
}

resource "aws_iam_role_policy" "secret_read" {
  name   = "${var.name_prefix}-secret-read"
  role   = aws_iam_role.dc.id
  policy = data.aws_iam_policy_document.secret_read.json
}

resource "aws_iam_instance_profile" "dc" {
  name = "${var.name_prefix}-dc-profile"
  role = aws_iam_role.dc.name
}
