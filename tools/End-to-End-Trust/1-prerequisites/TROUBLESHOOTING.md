# 1-Prerequisites Troubleshooting

## Certificate Generation Issues

### Error: "Error generating private key"

**Cause**: OpenSSL not available or insufficient entropy

**Solution**:
```bash
# Verify OpenSSL installed
openssl version

# On Linux, check entropy
cat /proc/sys/kernel/random/entropy_avail
# Should be > 100
```

### Issue: Certificate validation fails

**Symptom**: "Error: invalid certificate"

**Cause**: Domain name format incorrect

**Solution**:
```bash
# Edit terraform.tfvars
domain_name = "db.mydomain.com"  # Correct
# NOT: *.db.mydomain.com (wildcard added automatically)
# NOT: db.mydomain.com. (no trailing dot)
```

## Secrets Manager Issues

### Error: "ResourceExistsException"

**Cause**: Secret already exists (possibly in deletion pending state)

**Solution**:
```bash
# Check secret status
aws secretsmanager describe-secret --secret-id rdsdb2-proxy-certificate

# If in deletion, restore it
aws secretsmanager restore-secret --secret-id rdsdb2-proxy-certificate

# Or wait for deletion to complete (7-30 days by default)
# Or force immediate deletion (if recovery_window_in_days = 0)
```

### Issue: Secret deleted but can't recreate

**Cause**: Secret in deletion pending state

**Solution**: Module configured with `recovery_window_in_days = 0` for immediate deletion

**If you changed this**:
```bash
# Wait for deletion window
aws secretsmanager describe-secret --secret-id rdsdb2-proxy-certificate
# Check DeletionDate

# Or restore and delete with 0 recovery window
aws secretsmanager restore-secret --secret-id rdsdb2-proxy-certificate
# Update terraform.tfvars: recovery_window_in_days = 0
terraform apply --auto-approve
terraform destroy
```

## ACM Certificate Issues

### Error: "Certificate already exists"

**Cause**: ACM certificate with same domain exists

**Solution**:
```bash
# List certificates
aws acm list-certificates

# Delete old certificate
aws acm delete-certificate --certificate-arn arn:aws:acm:...

# Or import existing
terraform import aws_acm_certificate.proxy arn:aws:acm:...
```

### Issue: Certificate not trusted

**Explanation**: Self-signed certificates not trusted by default
- Normal for internal proxy
- Client applications must trust the certificate
- Or use CA-signed certificates

**For production**: Replace with CA-signed certificate
```bash
# Update Secrets Manager with CA-signed cert
aws secretsmanager update-secret \
  --secret-id rdsdb2-proxy-certificate \
  --secret-string '{"certificate":"-----BEGIN CERTIFICATE-----\n...","private_key":"-----BEGIN PRIVATE KEY-----\n..."}'
```

## Domain Name Issues

### Issue: Wildcard not working

**Symptom**: Only exact domain works, not subdomains

**Cause**: Certificate generated for specific domain, not wildcard

**Verify**:
```bash
# Check certificate subject
terraform output certificate_arn
aws acm describe-certificate --certificate-arn ARN | jq -r '.Certificate.DomainName'
# Should show: *.db.mydomain.com
```

**Solution**: Ensure domain_name in terraform.tfvars is correct
```hcl
domain_name = "db.mydomain.com"  # Generates *.db.mydomain.com
```

## Backend Configuration Issues

### Error: "Backend not configured"

**Symptom**: "Error: Backend initialization required"

**Cause**: configure-modules.sh not run

**Solution**:
```bash
cd ../0-backend-setup
./configure-modules.sh
cd ../1-prerequisites
terraform init
```

### Issue: Can't read backend outputs

**Symptom**: "Error: Unsupported attribute"

**Cause**: Module 0 not deployed

**Solution**:
```bash
cd ../0-backend-setup
terraform apply --auto-approve
cd ../1-prerequisites
terraform init
```

## Permission Issues

### Error: "AccessDenied" creating secret

**Required permissions**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:CreateSecret",
        "secretsmanager:PutSecretValue",
        "secretsmanager:DescribeSecret",
        "secretsmanager:DeleteSecret"
      ],
      "Resource": "arn:aws:secretsmanager:*:*:secret:rdsdb2-proxy-*"
    }
  ]
}
```

### Error: "AccessDenied" importing certificate to ACM

**Required permissions**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "acm:ImportCertificate",
        "acm:DescribeCertificate",
        "acm:DeleteCertificate"
      ],
      "Resource": "*"
    }
  ]
}
```

## Output Issues

### Issue: Outputs not available for next module

**Symptom**: Module 2 can't find certificate ARN

**Verify outputs**:
```bash
terraform output
# Should show:
# certificate_arn
# certificate_secret_arn
# domain_name
```

**Solution**: Re-apply if outputs missing
```bash
terraform refresh
terraform output
```

## Cleanup Issues

### Issue: Certificate still exists after destroy

**Cause**: ACM certificate in use by NLB

**Solution**: Destroy in correct order
```bash
cd ../3-mappings
terraform destroy

cd ../2-infrastructure
terraform destroy

cd ../1-prerequisites
terraform destroy
```

### Issue: Secret in deletion pending

**Explanation**: Normal if recovery_window_in_days > 0

**Solution**: Module configured with recovery_window_in_days = 0
- Immediate deletion
- No waiting period
- Can recreate immediately

## Verification

### Verify certificate created successfully

```bash
# Check Terraform outputs
terraform output

# Verify secret in Secrets Manager
SECRET_ARN=$(terraform output -raw certificate_secret_arn)
aws secretsmanager get-secret-value --secret-id $SECRET_ARN

# Verify ACM certificate
CERT_ARN=$(terraform output -raw certificate_arn)
aws acm describe-certificate --certificate-arn $CERT_ARN

# Check certificate details
aws acm describe-certificate --certificate-arn $CERT_ARN | jq -r '.Certificate | {DomainName, Status, NotAfter}'
```

### Test certificate validity

```bash
# Extract certificate from secret
SECRET_ARN=$(terraform output -raw certificate_secret_arn)
aws secretsmanager get-secret-value --secret-id $SECRET_ARN \
  | jq -r '.SecretString | fromjson | .certificate' > /tmp/cert.pem

# Verify certificate
openssl x509 -in /tmp/cert.pem -text -noout

# Check expiration
openssl x509 -in /tmp/cert.pem -noout -enddate
```
