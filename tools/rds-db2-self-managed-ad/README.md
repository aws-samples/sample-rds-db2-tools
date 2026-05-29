# Self-managed AD delegation for Amazon RDS for Db2

Follow these four steps to configure Kerberos authentication for your
RDS for Db2 instance using a customer-managed Active Directory domain.

---

## Step 1 — Grant AD domain join privileges

Delegate the minimum Active Directory permissions RDS for Db2 needs to
manage its principals inside a dedicated OU. Choose either method:

| Method | Doc |
|---|---|
| UI (ADUC Delegation Wizard + ADSI Edit) | [`README-UI.md`](./README-UI.md) |
| PowerShell (`Grant-ADDomainJoinPrivileges.ps1`) | [`README-PowerShell.md`](./README-PowerShell.md) |

Both methods produce equivalent ACLs — pick whichever fits your operations
model.

---

## Step 2 — Create the AWS KMS key for the secret

Create a symmetric KMS key in the same AWS account and Region as your
RDS for Db2 instance. This key encrypts the Secrets Manager secret in
Step 3.

→ [README-KMS-Secret.md — Step 4: Create the AWS KMS key](./README-KMS-Secret.md#step-4--create-the-aws-kms-key)

---

## Step 3 — Store the service account credentials in AWS Secrets Manager

Store the AD service account username and password as a Secrets Manager
secret encrypted with the KMS key from Step 2.

→ [README-KMS-Secret.md — Step 5: Create the AWS Secrets Manager secret](./README-KMS-Secret.md#step-5--create-the-aws-secrets-manager-secret)

---

## Step 4 — Create or modify the RDS for Db2 instance and select self-managed AD

Supply the Secret ARN from Step 3 when creating or modifying the RDS for
Db2 instance to join it to your domain.

→ [README-RDS-Db2.md](./README-RDS-Db2.md)

---

## Networking

If your AD domain controllers are not in the same VPC as RDS for Db2,
see [`README-Networking.md`](./README-Networking.md) for port requirements
and topology options (same VPC, Azure AD, cross-account VPC).
