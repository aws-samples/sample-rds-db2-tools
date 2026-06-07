# Security invariants

These are the non-negotiable security properties that hold on **every** produced
deployment, regardless of the prompt wording or the selected tier. No prompt can
silently produce an insecure RDS for Db2 instance. The invariants are enforced
with defense in depth: at intent validation, at Terraform rendering, and at the
policy gate.

> Grounds: Requirement 6 (security invariants).
> Enforced in `scripts/validate_intent.py` (Layer-2 cross-checks),
> `scripts/render_terraform.py` (Terraform_Composer), and
> `scripts/policy_gate.py` (Policy_Gate).

## The four invariants

### 1. CMK-everywhere encryption (MRK)

Storage encryption is always enabled with a **customer-managed MRK CMK**
(multi-region key) — never the default AWS-owned RDS key (R6.1). The invariant
extends to **every encryptable resource** the deployment creates (R6.10):

- RDS storage
- the master-user secret in Secrets Manager
- any S3 bucket used for restore or audit
- the Db2 audit log destination
- Performance Insights data, when enabled

None of these may use an AWS-owned or AWS-managed default key (`aws/rds`,
`aws/secretsmanager`, `aws/s3`). If an intent specifies a default key for any
encryptable resource, or omits a required CMK, the validator rejects it, names
the resource and the non-compliant key, and halts before rendering (R6.11). A
BYOK key must itself be an MRK CMK (R13.14).

### 2. SSL-only (`DB2COMM=SSL`, `ssl_svcename=50443`)

Every deployment renders `DB2COMM=SSL` with `ssl_svcename=50443` (R6.2). Db2
accepts client connections **only** over the SSL service port `50443`.

The non-SSL TCP listener `port` (default `8392`) is required by RDS at create
time but stays **dormant**: because `DB2COMM` is `SSL` (not `tcpip,ssl`), the
listener accepts no connections and is never opened in the security group. The
two ports are distinct; `port` must never equal `50443` (R18.13). See
`intent-and-tiers.md` for the listener-vs-service-port distinction.

### 3. Non-public by default

Absent a public-access acknowledgement field set to `true`, the composer renders
`publicly_accessible=false` (R6.3). Going public is a guarded, explicit decision:

- A prompt requesting `publicly_accessible=true` requires
  `public_access_acknowledged=true` in the intent before rendering proceeds
  (R6.4). Without it, the validator rejects the intent and halts (R6.8).
- Security-group ingress from `0.0.0.0/0` likewise requires
  `public_access_acknowledged=true`; otherwise the intent is rejected (R6.9).

### 4. Least-privilege security group (50443 only)

The security group is rendered with inbound ingress limited to the Db2 SSL
service port `50443` (TCP), from **only** the source CIDR ranges or security
groups named in the intent (R6.5). The composer:

- does **not** open the non-SSL TCP listener `port` to ingress, and
- does **not** render ingress on any other port.

## Enforcement is defense-in-depth

Each invariant is checked at three independent stages, so a gap in any one stage
cannot produce an insecure deployment:

| Stage | Component | Behavior |
|---|---|---|
| Validation | `Intent_Validator` (Layer 2) | Flags the violated invariant **by name** and halts before rendering (R6.6). |
| Rendering | `Terraform_Composer` | Emits CMK encryption, SSL-only, non-public, 50443-only SG. |
| Policy gate | `Policy_Gate` | Re-checks MRK CMK, `DB2COMM=SSL`/`ssl_svcename=50443`, non-public-absent-ack against the rendered Terraform (R12.3). |

## Universality (R6.7 / R6.12)

For **all** produced deployments, regardless of tier or prompt wording:

- storage encryption uses a customer-managed MRK CMK,
- `DB2COMM=SSL` with `ssl_svcename=50443` holds,
- `publicly_accessible=false` holds absent an explicit acknowledgement, and
- every encryptable resource is CMK-encrypted.

These are property-based test targets: generate many intents and assert the
invariant never breaks.

## Sources

- AWS docs, [Encrypting Amazon RDS resources](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Overview.Encryption.html)
  and [AWS KMS multi-Region keys](https://docs.aws.amazon.com/kms/latest/developerguide/multi-region-keys-overview.html).
- AWS docs, [Using SSL/TLS with RDS for Db2](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/db2-ssl-connections.html).
- AWS blog, [Deploying Amazon RDS for Db2 using Terraform](https://aws.amazon.com/blogs/database/deploying-amazon-rds-for-db2-using-terraform/).
- `scripts/validate_intent.py`, `scripts/render_terraform.py`, `scripts/policy_gate.py`.
