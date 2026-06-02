# Step 5 — Connect to RDS for Db2 using a domain-joined EC2 client

This step installs the Db2 Runtime Client on an Amazon Linux 2023 EC2
instance, joins it to your self-managed Active Directory domain, and
configures DSN entries for both local-user and Kerberos authentication.

> **Prerequisites**
> - Steps 1–4 complete: RDS for Db2 instance joined to your AD domain
> - An EC2 instance running Amazon Linux 2023 (AMD64) in the same VPC as
>   the RDS instance, with outbound internet access or a VPC endpoint
> - IAM instance profile with `rds:Describe*` and `secretsmanager:GetSecretValue`
> - The EC2 security group allows outbound TCP 50000 (Db2) and 50443 (SSL)
>   to the RDS security group

---

## 5.1 — Launch an Amazon Linux 2023 EC2 instance

Launch an AL2023 AMD64 instance into the same VPC and subnet as your RDS
for Db2 instance. Recommended minimum: `t3.medium`.

Ensure the instance profile has at minimum:

```json
{
  "Effect": "Allow",
  "Action": [
    "rds:DescribeDBInstances",
    "rds:DescribeDBParameters",
    "secretsmanager:GetSecretValue"
  ],
  "Resource": "*"
}
```

---

## 5.2 — Join the EC2 instance to the AD domain

The domain-join script installs the required packages (`realmd`, `sssd`,
`adcli`, `krb5-workstation`, and related tools) and downloads the
interactive join script.

```bash
curl -sL https://bit.ly/domainjoin | bash
```

Once the packages are installed the script prints:

```
Copy/Paste and Run command 'source joindomain.sh' to domain join AD instance
```

Run the interactive join:

```bash
source joindomain.sh
```

The script prompts for your domain FQDN, OU, and a domain account with
join privileges (the service account from Step 1), then calls `realm join`.

> **AWS documentation reference:** For the full list of ports required
> between the EC2 instance and your domain controllers (DNS 53, Kerberos
> 88/464, LDAP 389/3268, RPC 49152–65535) and topology options, see
> [Setting up Kerberos authentication for RDS for Db2](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/db2-kerberos-setting-up.html)
> and the networking notes in [`README-Networking.md`](./README-Networking.md).

Verify the join:

```bash
realm list
```

Expected output includes:

```
  configured: kerberos-member
  server-software: active-directory
  client-software: sssd
```

---

## 5.3 — Obtain a Kerberos ticket

```bash
kinit your.username@COMPANY.COM
klist          # confirm the TGT is present and not expired
```

> The ticket must belong to an AD user that has been granted `CONNECT`
> privilege (or higher) on the Db2 databases you want to reach. The RDS
> master user (`admin`) is a local account — it cannot obtain a Kerberos
> ticket and is used only for local-auth DSNs.

---

## 5.4 — Install the Db2 Runtime Client

Run as `root` or `ec2-user` (requires sudo):

```bash
# Db2 11.5 (default):
REGION=<region> ./db2-driver.sh

# Db2 12.1:
DB2_VER=12.1 REGION=<region> ./db2-driver.sh
```

The installer downloads automatically in online mode. For private subnets
without internet access, use the airgap mode documented in
[`DB2-Driver/README.txt`](../DB2-Driver/README.txt).

Full installation walkthrough:
[Connect to Amazon RDS for Db2 using AWS CloudShell](https://aws.amazon.com/blogs/database/connect-to-amazon-rds-for-db2-using-aws-cloudshell/)

---

## 5.5 — Configure DSN entries

Switch to the `db2inst1` user and run the configure script. Because the
host is domain-joined, the script automatically detects the AD realm,
verifies the TGT, and writes both local-auth and Kerberos DSN entries.

```bash
sudo su - db2inst1
REGION=<region> source db2client-configure.sh
```

If your databases are not discoverable via RDSADMIN (AD users are not
granted CONNECT on the RDSADMIN system database by default), provide the
database names explicitly:

```bash
DB_NAMES=DB2DB,MYDB REGION=<region> source db2client-configure.sh
```

For custom RDS endpoints (non-standard domain suffix), add
the `E_URL` variable so the script uses the correct API endpoint and
extracts the right root CA from the server's TLS chain:

```bash
E_URL="--endpoint-url https://<custom-rds-endpoint> --no-verify-ssl" \
REGION=<region> source db2client-configure.sh
```

### DSN entries created

| Alias | Transport | Auth | Use case |
|---|---|---|---|
| `RDSAT` | TCP | Local (password) | Admin access, db2comm includes TCPIP |
| `RDSAS` | SSL | Local (password) | Admin access via SSL |
| `RDSAKS` | SSL | Kerberos | Admin access, no password needed |
| `<DB>T` | TCP | Local | User database via TCP (e.g. `DB2DBT`) |
| `<DB>S` | SSL | Local | User database via SSL (e.g. `DB2DBS`) |
| `<DB>SK` | SSL | Kerberos | User database via Kerberos (e.g. `DB2DBSK`) |

Which entries are written depends on `db2comm` in the parameter group:

| `db2comm` value | DSNs written |
|---|---|
| `TCPIP` | `RDSAT`, `<DB>T` |
| `SSL` | `RDSAS`, `RDSAKS`, `<DB>S`, `<DB>SK` |
| `TCPIP,SSL` | all of the above |

---

## 5.6 — Test the connections

```bash
db2 terminate

# Local auth over SSL (uses master user password):
db2 "connect to RDSAS user admin using '$MASTER_USER_PASSWORD'"

# Kerberos over SSL (uses TGT — no password):
db2 "connect to RDSAKS"

# User database — local auth:
db2 "connect to DB2DBS user admin using '$MASTER_USER_PASSWORD'"

# User database — Kerberos:
db2 "connect to DB2DBSK"
```

Run a quick query to confirm connectivity:

```bash
db2 "select * from sysibm.sysdummy1"
db2 connect reset
```

Use the built-in diagnostics helper if a connection fails:

```bash
db2_test_connection RDSAS
db2_test_connection RDSAKS
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `realm list` shows no domain | Join did not complete | Re-run `source joindomain.sh`; check DC connectivity on port 88 and 389 |
| `klist` empty | No TGT | `kinit user@REALM.COM` |
| GSKit error 414 on SSL connect | Wrong or untrusted cert | For custom endpoints set `E_URL`; the script extracts the root CA automatically |
| `DB_NAMES` prompt appears | AD user lacks CONNECT on RDSADMIN | Set `DB_NAMES=DB2DB,...` before running the script |
| Kerberos connect succeeds but query fails | AD user not granted DB privileges | Connect as `admin` (local auth) and `GRANT CONNECT ON DATABASE TO USER domain\user` |

Full troubleshooting reference: [`DB2-Driver/README.txt`](../DB2-Driver/README.txt)
