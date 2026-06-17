# Account defaults — define the basics once, in one small file

Most of what an RDS for Db2 deployment needs is the same for every instance in a
given account/environment: the region, the network it lives in, the encryption
key, the IBM licensing identifiers, and a couple of tags. Rather than re-collect
those on every deployment, the customer records them **once** in a small JSON
file and the skill reuses them for every intent.

> Grounds: minimum-required-params pattern (region, VPC/subnet, SG, KMS MRK,
> monitoring role, ingress, IBM IDs, tags). Implemented in
> `scripts/account_defaults.py`; validated by
> `schemas/account-defaults.schema.json`; template in
> `account-defaults.example.json`.

## The pattern (no scripts, console view access is enough)

1. Copy `account-defaults.example.json` into your gitops repo as
   `account-defaults.json`.
2. Fill each value by **reading it from the AWS console** — view access is all
   you need (see the lookup table below). You do **not** need the AWS CLI.
3. Commit it. From then on, just tell the agent what you want
   ("deploy a dev sandbox") — it loads `account-defaults.json`, merges it into
   the intent, and only asks for anything still missing.

Anything you name in the prompt **overrides** the matching default (provenance
stays correct: `user_provided` for both, since you supplied the default ahead of
time). A field you leave out of the file is simply asked for, or routed to the
create-path module (see below).

This file holds **no secrets** — no master password, no AWS access keys. The IBM
identifiers are low-sensitivity licensing IDs and are masked by the skill in all
logs, PRs, and artifacts. The AWS account that actually runs `terraform apply`
is the **gitops/CI account** (full access); its credentials live in CI, never in
this file. `gitops_aws_account_id` is recorded only so the agent can sanity-check
which account a deployment targets.

## Where to find each value in the console (view access)

| Field | Console location | Notes |
|---|---|---|
| `region` | top-right region selector | e.g. `us-east-1` |
| `vpc_id` | VPC console → Your VPCs | always yours — the skill never creates a VPC |
| `db_subnet_group_name` | RDS → Subnet groups | the group spanning ≥ 2 AZs; **leave blank to create** via `1-networking`, or name an existing one to reuse |
| `vpc_security_group_ids` | VPC → Security groups | the SG(s) for the DB (always yours); skill opens only TCP 50443 from your ingress |
| `kms_key_id` | KMS → Customer managed keys | a **multi-region** CMK (key id starts `mrk-`); **leave blank to create** an MRK via `3-kms`, or name one to reuse |
| `master_user_secret_kms_key_id` | KMS → Customer managed keys | MRK CMK for the managed master-user secret; **leave blank to mirror** the storage CMK |
| `monitoring_role_arn` | IAM → Roles | Enhanced Monitoring role; **leave blank to create** via `2-iam`, or name one to reuse |
| `ingress_cidrs` | your network team | private CIDR allowed to reach 50443 (e.g. `10.0.0.0/16`) |
| `ibm_customer_id` / `ibm_site_id` | IBM Passport Advantage (not AWS) | required for every Db2 edition |
| `tags.Project` / `tags.Owner` | your standard | `Environment` is set automatically from the tier |

## Blank to create, value to reuse (R10.5/10.6)

For the three reusable account resources — the **DB subnet group**, the **MRK
CMK**, and the **Enhanced-Monitoring role** — each field is now optional and
behaves as a switch:

- **Leave it blank (or omit it)** → the composer renders the matching
  foundational module and **creates** the resource on the first `apply`, wiring
  its output straight into the instance:
  - `db_subnet_group_name` blank → `1-networking` creates a subnet group from
    your VPC's subnets → fed to `5-rds.db_subnet_group_name`.
  - `kms_key_id` blank → `3-kms` creates a **multi-region** customer-managed CMK
    (`multi_region_key=true`, so the storage-encryption invariant holds) → fed to
    `5-rds.kms_key_arn`.
  - `monitoring_role_arn` blank → `2-iam` creates the Enhanced-Monitoring role →
    fed to `5-rds.monitoring_role_arn`.
  - `master_user_secret_kms_key_id` blank → mirrors the storage CMK (the created
    MRK, or the supplied storage key) so the managed-secret is CMK-encrypted
    without a second key.
- **Supply an existing identifier** → the create module is skipped and the
  instance **reuses** the resource you named.

What does NOT auto-create (always supply): `vpc_id` (the skill never creates a
VPC) and `vpc_security_group_ids` (the security group is always yours; the skill
only opens TCP 50443 on it).

### Create once, then record and reuse

These are **account-level, shared** resources — you want one subnet group, one
CMK, and one monitoring role for the account, not one per database. The created
names are deterministic and keyed on your `Project` tag, so two *blank*
deployments with the same tag would collide. The intended workflow:

1. **First deployment** in the account: leave the three fields blank → the apply
   creates them.
2. **Record** the created identifiers (from the Terraform outputs / console) back
   into `account-defaults.json`.
3. **Every subsequent deployment** reuses them (values now present).

So "blank = create" is really a **one-shot bootstrap built into your first
deployment**; steady state is reuse. (Concurrent first-time blank deployments in
the same account/tag are not supported — bootstrap once, then fan out.)

## What the skill always needs (asked if absent)

`region`, `vpc_id`, `vpc_security_group_ids`, and the IBM identifiers
(`ibm_customer_id`/`ibm_site_id`, or their `*_ssm` names) have no safe default —
the skill **never fabricates** them. If any is missing from both the prompt and
the defaults file, the agent lists it and asks before building an intent.
`missing_required_account_fields()` reports exactly this set (the reusable
subnet-group/KMS/monitoring fields are NOT in it — blank just means create).

## Keeping IBM IDs out of the repo (SSM Parameter Store)

The IBM customer/site IDs are confidential licensing identifiers. Rather than
committing them to the gitops repo, you can store them in **SSM Parameter Store**
(SecureString) and reference them by **name**:

```json
"ibm_customer_id_ssm": "/rds-db2/ibm-customer-id",
"ibm_site_id_ssm": "/rds-db2/ibm-site-id"
```

When set, the `4-parameter-group` module reads the decrypted values from SSM at
apply (via `data "aws_ssm_parameter"`), so only the parameter **names** — not the
values — live in `account-defaults.json`, the rendered tfvars, and the intent.
Supply **exactly one** form per ID: the literal `ibm_customer_id` / `ibm_site_id`,
**or** the `ibm_customer_id_ssm` / `ibm_site_id_ssm` name (the validator and the
module both reject supplying both or neither).

Prerequisite + permissions:

- Create the two SSM **SecureString** parameters once (any KMS key you control).
- The deploy identity (CI OIDC role, or your operator) needs `ssm:GetParameter`
  on those parameters and `kms:Decrypt` on their KMS key.
- The values still land in Terraform **state** (an `aws_db_parameter_group`
  attribute), which is encrypted + access-controlled in S3 and not version
  controlled — the goal here is keeping them out of **git**.

## Validate the file (optional, in CI)

The gitops/CI account can validate the file as a pre-merge check:

```bash
python -m scripts.account_defaults path/to/account-defaults.json
```

Exit 0 = valid; a non-zero exit names the offending field. Customers without CLI
access can skip this — the agent validates the file when it loads it.

## Sources

- `scripts/account_defaults.py`, `schemas/account-defaults.schema.json`,
  `account-defaults.example.json`.
- Reuse-vs-create rule: `terraform-composition.md`.
- Security invariants (MRK CMK, 50443-only ingress): `security-invariants.md`.
