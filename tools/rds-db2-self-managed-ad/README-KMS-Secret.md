# Step 4 & 5 — KMS key and Secrets Manager entry for RDS for Db2 self-managed AD

These two steps create the AWS-side resources that RDS for Db2 uses to
authenticate against your self-managed Active Directory. They follow the AD
delegation steps in [`README-UI.md`](./README-UI.md) or
[`README-PowerShell.md`](./README-PowerShell.md).

Two methods are provided for each step:

- **Method 1** — AWS Management Console (UI)
- **Method 2** — AWS CLI

> **Replace example values before running any command.**
>
> | Example value | What to replace it with |
> |---|---|
> | `123456789012` | Your 12-digit AWS account ID |
> | `us-east-1` | The AWS Region where your RDS for Db2 instance lives |
> | `rds-db2-self-managed-ad-key` | Your chosen KMS key alias |
> | `rds-db2-self-managed-ad-secret` | Your chosen Secrets Manager secret name |
> | `my-admin-user` | The IAM user who will administer the KMS key |
> | `rdsdb2svc` | The sAMAccountName of the AD service account (no domain prefix) |
> | `arn:aws:rds:us-east-1:123456789012:db:*` | The ARN of your RDS for Db2 instance (or `db:*` to cover all instances in the account/region) |

---

## Step 4 — Create the AWS KMS key

The KMS key encrypts the Secrets Manager secret that holds the AD service
account credentials.

> **Important:** Do not use the AWS default KMS key. Create a dedicated key
> in the same AWS account and Region as your RDS for Db2 instance.

### Method 1 — Console

1. Open the [AWS KMS console](https://console.aws.amazon.com/kms) and choose **Create key**.
2. **Key type** → **Symmetric**.
3. **Key usage** → **Encrypt and decrypt**.
4. **Advanced options**:
   - **Key material origin** → **KMS**
   - **Regionality** → **Multi-Region key**
   - Click **Next**.
5. **Alias** → enter a name (e.g. `rds-db2-self-managed-ad-key`).
6. *(Optional)* **Description** → describe the key's purpose.
7. *(Optional)* **Tags** → add tags. Click **Next**.
8. **Key administrators** → search for and select your IAM admin user to manage this key in future.
9. **Key deletion** → keep **Allow key administrators to delete this key** selected. Click **Next**.
10. **Define key usage permissions** → Optional or select the same IAM user. Click **Next**.
11. **Review** the configuration.
12. **Key policy** → click **Edit** and add the following statement inside the
    existing `Statement` array (after the last existing entry, add a comma and paste the following):

    ```json
    {
        "Sid": "Allow use of the KMS key on behalf of RDS",
        "Effect": "Allow",
        "Principal": {
            "Service": [
                "rds.amazonaws.com"
            ]
        },
        "Action": "kms:Decrypt",
        "Resource": "*"
    }
    ```

13. Click **Next** and **Finish**.
14. Copy the **Key ARN** — you will need it in Step 5.

### Method 2 — AWS CLI

```bash
# Replace my-admin-user and 123456789012 with your IAM user and account ID
# Replace us-east-1 with your Region

ACCOUNT_ID="123456789012"
REGION="us-east-1"
ADMIN_USER="my-admin-user"
KEY_ALIAS="rds-db2-self-managed-ad-key"

# Get the IAM user ARN
ADMIN_ARN=$(aws iam get-user --user-name "$ADMIN_USER" \
    --query 'User.Arn' --output text)

# Build the key policy
KEY_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "Enable IAM User Permissions",
      "Effect": "Allow",
      "Principal": { "AWS": "arn:aws:iam::${ACCOUNT_ID}:root" },
      "Action": "kms:*",
      "Resource": "*"
    },
    {
      "Sid": "Allow key administration",
      "Effect": "Allow",
      "Principal": { "AWS": "${ADMIN_ARN}" },
      "Action": [
        "kms:Create*", "kms:Describe*", "kms:Enable*", "kms:List*",
        "kms:Put*", "kms:Update*", "kms:Revoke*", "kms:Disable*",
        "kms:Get*", "kms:Delete*", "kms:ScheduleKeyDeletion",
        "kms:CancelKeyDeletion"
      ],
      "Resource": "*"
    },
    {
      "Sid": "Allow key use by admin",
      "Effect": "Allow",
      "Principal": { "AWS": "${ADMIN_ARN}" },
      "Action": [ "kms:Decrypt", "kms:GenerateDataKey*", "kms:DescribeKey" ],
      "Resource": "*"
    },
    {
      "Sid": "Allow use of the KMS key on behalf of RDS",
      "Effect": "Allow",
      "Principal": { "Service": "rds.amazonaws.com" },
      "Action": "kms:Decrypt",
      "Resource": "*"
    }
  ]
}
EOF
)

# Create the key
KMS_KEY_ID=$(aws kms create-key \
    --description "KMS key for RDS for Db2 self-managed AD secret" \
    --key-usage ENCRYPT_DECRYPT \
    --key-spec SYMMETRIC_DEFAULT \
    --multi-region \
    --policy "$KEY_POLICY" \
    --region "$REGION" \
    --query 'KeyMetadata.KeyId' --output text)

echo "KMS Key ID: $KMS_KEY_ID"

# Create a human-readable alias
aws kms create-alias \
    --alias-name "alias/${KEY_ALIAS}" \
    --target-key-id "$KMS_KEY_ID" \
    --region "$REGION"

# Get the full Key ARN (needed for Step 5)
KMS_KEY_ARN=$(aws kms describe-key \
    --key-id "$KMS_KEY_ID" \
    --region "$REGION" \
    --query 'KeyMetadata.Arn' --output text)

echo "KMS Key ARN: $KMS_KEY_ARN"
```

---

## Step 5 — Create the AWS Secrets Manager secret

The secret stores the AD service account username and password. RDS for Db2
reads this secret when joining your domain.

> **Important:** Create the secret in the same AWS account and Region as
> your RDS for Db2 instance.
>
> **Username format:** Enter only the sAMAccountName (e.g. `rdsdb2svc`).
> Do **not** include the domain prefix (`COMPANY\rdsdb2svc`) — this causes
> instance creation to fail.

### Method 1 — Console

1. Open [AWS Secrets Manager](https://console.aws.amazon.com/secretsmanager)
   and choose **Store a new secret**.
2. **Secret type** → **Other type of secret**.
3. **Key/value pairs** → add two entries:
   - Key: `SELF_MANAGED_ACTIVE_DIRECTORY_USERNAME`
     Value: the sAMAccountName only, e.g. `rdsdb2svc` (no domain prefix)
   - Key: `SELF_MANAGED_ACTIVE_DIRECTORY_PASSWORD`
     Value: the password you set for the AD service account
4. **Encryption key** → select the KMS key created in Step 4. Click **Next**.
5. **Secret name** → enter a descriptive name (e.g. `rds-db2-self-managed-ad-secret`).
6. *(Optional)* **Description** → describe the secret's purpose.
7. **Resource permissions** → click **Edit** and replace the policy with:

    ```json
    {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Service": "rds.amazonaws.com"
                },
                "Action": "secretsmanager:GetSecretValue",
                "Resource": "*",
                "Condition": {
                    "StringEquals": {
                        "aws:sourceAccount": "123456789012"
                    },
                    "ArnLike": {
                        "aws:sourceArn": "arn:aws:rds:us-east-1:123456789012:db:*"
                    }
                }
            }
        ]
    }
    ```

    > Replace `123456789012` with your AWS account ID and `us-east-1` with
    > your Region. The `aws:sourceAccount` and `aws:sourceArn` conditions
    > prevent the [confused deputy problem](https://docs.aws.amazon.com/IAM/latest/UserGuide/confused-deputy.html).
    > Use a specific DB instance ARN instead of `db:*` if you want to
    > restrict access to a single instance.

8. Click **Save**, then **Next**.
9. **Configure rotation** → keep defaults. Click **Next**.
10. **Review** and click **Store**.
11. Open the secret you just created and copy the **Secret ARN** — you will
    need it when configuring the RDS for Db2 instance.

### Method 2 — AWS CLI

```bash
# Replace all example values at the top of this block before running

ACCOUNT_ID="123456789012"
REGION="us-east-1"
SECRET_NAME="rds-db2-self-managed-ad-secret"
AD_USERNAME="rdsdb2svc"          # sAMAccountName only — no domain prefix
KMS_KEY_ARN="arn:aws:kms:us-east-1:123456789012:key/your-key-id"
                                 # use the KMS_KEY_ARN output from Step 4

# Prompt for password securely — input is not echoed to the terminal
# zsh:
read -s "AD_PASSWORD?Enter AD service account password: "
# bash (if running in bash instead of zsh):
# read -s -p "Enter AD service account password: " AD_PASSWORD
echo

# Resource policy — prevents confused deputy attacks
SECRET_POLICY=$(cat <<EOF
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": { "Service": "rds.amazonaws.com" },
            "Action": "secretsmanager:GetSecretValue",
            "Resource": "*",
            "Condition": {
                "StringEquals": {
                    "aws:sourceAccount": "${ACCOUNT_ID}"
                },
                "ArnLike": {
                    "aws:sourceArn": "arn:aws:rds:${REGION}:${ACCOUNT_ID}:db:*"
                }
            }
        }
    ]
}
EOF
)

# Create the secret
SECRET_ARN=$(aws secretsmanager create-secret \
    --name "$SECRET_NAME" \
    --description "AD service account credentials for RDS for Db2 self-managed AD" \
    --kms-key-id "$KMS_KEY_ARN" \
    --secret-string "{
        \"SELF_MANAGED_ACTIVE_DIRECTORY_USERNAME\": \"${AD_USERNAME}\",
        \"SELF_MANAGED_ACTIVE_DIRECTORY_PASSWORD\": \"${AD_PASSWORD}\"
    }" \
    --region "$REGION" \
    --query 'ARN' --output text)

echo "Secret ARN: $SECRET_ARN"

# Attach the resource policy
aws secretsmanager put-resource-policy \
    --secret-id "$SECRET_ARN" \
    --resource-policy "$SECRET_POLICY" \
    --region "$REGION"

echo "Resource policy attached."
echo ""
echo "Copy this Secret ARN for the next step (RDS for Db2 instance configuration):"
echo "$SECRET_ARN"
```

---

## Verify

### KMS key

```bash
# Confirm the key exists and is enabled
aws kms describe-key \
    --key-id "alias/rds-db2-self-managed-ad-key" \
    --region "us-east-1" \
    --query 'KeyMetadata.{KeyId:KeyId,Enabled:Enabled,KeyState:KeyState}'
```

### Secret

```bash
# Confirm the secret exists and uses the correct KMS key
# Replace rds-db2-self-managed-ad-secret with your secret name
aws secretsmanager describe-secret \
    --secret-id "rds-db2-self-managed-ad-secret" \
    --region "us-east-1" \
    --query '{Name:Name,ARN:ARN,KmsKeyId:KmsKeyId}'
```

---

## Next step

With the Secret ARN in hand, proceed to configure the RDS for Db2 instance
to use self-managed AD:

- Console: modify or create the DB instance and supply the Secret ARN in the
  **Directory** section.
- CLI: pass `--domain-fqdn`, `--domain-ou`, and `--domain-auth-secret-arn`
  to `aws rds create-db-instance` or `aws rds modify-db-instance`.
