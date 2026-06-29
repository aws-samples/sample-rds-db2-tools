variable "aws_region" {
  description = "Target AWS region. Default targets GovCloud us-gov-east-1; override for commercial."
  type        = string
  default     = "us-gov-east-1"
}

variable "aws_profile" {
  description = "Named AWS CLI/SDK profile to authenticate with."
  type        = string
  default     = "govcloud"
}

variable "name_prefix" {
  description = "Prefix applied to all resource names."
  type        = string
  default     = "selfmanaged-ad"
}

variable "tags" {
  description = "Tags applied to every resource via provider default_tags."
  type        = map(string)
  default = {
    Project    = "self-managed-ad"
    ManagedBy  = "terraform"
    created_by = "rds-db2-skill"
  }
}

# ---------------------------------------------------------------------------
# Networking (uses an EXISTING VPC + subnets - nothing is created)
# ---------------------------------------------------------------------------
variable "vpc_id" {
  description = "ID of the existing VPC to deploy the domain controllers into."
  type        = string
}

variable "dc_subnet_ids" {
  description = "Two existing private subnet IDs for DC1 and DC2 (in that order), one per AZ. Each must have outbound internet (NAT) for bootstrap."
  type        = list(string)

  validation {
    condition     = length(var.dc_subnet_ids) == 2
    error_message = "Provide exactly two DC subnet IDs (DC1 first, DC2 second)."
  }
}

variable "dc1_private_ip" {
  description = "Static primary private IP for DC1 (must be inside dc_subnet_ids[0]'s CIDR)."
  type        = string
  default     = "10.0.128.10"
}

variable "dc2_private_ip" {
  description = "Static primary private IP for DC2 (must be inside dc_subnet_ids[1]'s CIDR)."
  type        = string
  default     = "10.0.144.10"
}

variable "rdp_ingress_cidr" {
  description = "Optional CIDR allowed to RDP (3389) to the DCs. Empty string disables the rule (use SSM Fleet Manager instead). Never set to 0.0.0.0/0."
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Active Directory
# ---------------------------------------------------------------------------
variable "domain_fqdn" {
  description = "Fully-qualified AD domain name, e.g. corp.example.com."
  type        = string
  default     = "corp.example.com"
}

variable "domain_netbios_name" {
  description = "NetBIOS (short) domain name, e.g. CORP. Max 15 chars, uppercase."
  type        = string
  default     = "CORP"
}

# ---------------------------------------------------------------------------
# RDS for Db2 self-managed AD integration
# ---------------------------------------------------------------------------
variable "ou_name" {
  description = "Name of the OU created for RDS for Db2 principals (under the domain root)."
  type        = string
  default     = "RDSDb2"
}

variable "svc_account_name" {
  description = "sAMAccountName of the delegated AD service account RDS for Db2 uses to join the domain. No domain prefix."
  type        = string
  default     = "rdsdb2svc"
}

variable "rds_db_arn_pattern" {
  description = "Optional aws:SourceArn pattern for the secret resource policy. Empty string => arn:<partition>:rds:<region>:<account>:db:* (all DB instances in this account/region)."
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------
variable "instance_type" {
  description = "EC2 instance type for the domain controllers."
  type        = string
  default     = "t3.large"
}

variable "root_volume_size" {
  description = "Root EBS volume size (GiB) for each DC."
  type        = number
  default     = 60
}

variable "windows_ami_ssm_parameter" {
  description = "SSM public parameter resolving to the Windows Server AMI. Partition-agnostic."
  type        = string
  default     = "/aws/service/ami-windows-latest/Windows_Server-2022-English-Full-Base"
}
