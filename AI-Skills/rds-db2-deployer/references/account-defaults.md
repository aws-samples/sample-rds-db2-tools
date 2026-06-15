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
| `db_subnet_group_name` | RDS → Subnet groups | the group spanning ≥ 2 AZs; bootstrap via `1-networking` if absent |
| `vpc_security_group_ids` | VPC → Security groups | the SG(s) for the DB; skill opens only TCP 50443 from your ingress |
| `kms_key_id` | KMS → Customer managed keys | must be **multi-region** (key id starts with `mrk-`); bootstrap via `3-kms` if absent |
| `master_user_secret_kms_key_id` | KMS → Customer managed keys | MRK CMK for the managed master-user secret |
| `monitoring_role_arn` | IAM → Roles | Enhanced Monitoring role; bootstrap via `2-iam` if absent |
| `ingress_cidrs` | your network team | private CIDR allowed to reach 50443 (e.g. `10.0.0.0/16`) |
| `ibm_customer_id` / `ibm_site_id` | IBM Passport Advantage (not AWS) | required for every Db2 edition |
| `tags.Project` / `tags.Owner` | your standard | `Environment` is set automatically from the tier |

## Reuse existing resources; bootstrap once if absent

A per-deployment intent **reuses** existing networking, KMS, and monitoring (the
rendered root is a single, self-contained plan; it does not create these and
wire their outputs into the instance). So these resources must already exist and
their identifiers must be recorded here:

- `db_subnet_group_name`, `kms_key_id`, `master_user_secret_kms_key_id`,
  `monitoring_role_arn`, `vpc_security_group_ids` → reused as supplied.

If your account does not have them yet, the **gitops/CI account** (full access)
creates them **once** by applying the foundational modules, then you record the
results in this file:

- subnet group + security group → `1-networking`
- MRK CMK(s) → `3-kms`
- Enhanced Monitoring role → `2-iam`

This is a one-time bootstrap (foundational infra), separate from the
per-deployment flow (instance infra). After it, every deployment is reuse-only
and the agent can render a complete, single-`apply` plan for CI.

> Future enhancement: a one-shot mode where a single deployment PR also creates
> the networking/KMS/role and wires their outputs into the instance. Not enabled
> today — the composer renders reuse, so supply these values.

## What the skill cannot assume (always asked if absent)

`region`, `ibm_customer_id`, and `ibm_site_id` have no safe default and the skill
**never fabricates** the IBM identifiers. If they are missing from both the
prompt and the defaults file, the agent lists them and asks before building an
intent. `missing_required_account_fields()` reports exactly this set.

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
