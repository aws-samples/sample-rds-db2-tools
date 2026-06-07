# Deployment intent, tiers, and workload sizes

This reference explains the four orthogonal axes the skill composes, how a
natural-language prompt becomes a schema-validated `deployment-intent.json`, and
the provenance model that makes every field auditable.

> Grounds: Requirements 2 (NL intent capture), 3 (tiers ↔ Environment tag),
> 8 (edition/licensing reconciliation), 17 (workload sizing).
> Implemented in `scripts/resolve_intent.py`.

## The intent is the contract

Everything flows through one schema-validated artifact, `deployment-intent.json`.
Natural-language gathering writes it, validation gates it, and Terraform
rendering consumes it. No field reaches Terraform without passing both the JSON
Schema and the code validator. See `intent-schema.md` for the schema itself.

Each field resolves to exactly one of three states (R2.1):

- a value taken from the prompt → provenance `user_provided` (R2.3),
- a value taken from the selected tier default → provenance `assumed` (R2.2),
- an explicit unset marker when there is no default and the prompt is silent (R2.4).

When a prompt value differs from the tier default, the resolver applies the
prompt value, marks it `user_provided`, and records **both** the applied value
and the superseded default (R2.5). If a required field cannot be derived from
the prompt or any tier default, the collector lists every missing field by name
in one consolidated request and refuses to fabricate values or emit an intent
until they are supplied (R2.6).

## Four orthogonal axes

| Axis | Values | Determines | Independent of |
|---|---|---|---|
| `Deployment_Tier` | `sandbox`, `dev`, `prod` | governance baseline (HA, backups, deletion protection) | size, edition, credentials |
| `Workload_Size` | `xsmall`…`xlarge` | compute + storage capacity | tier, edition, credentials |
| Edition (`engine`) | `db2-ce`, `db2-se`, `db2-ae` | licensing | tier, size (subject to SE ceiling) |
| `AWS_Credential_Source` | profile / env / default chain | identity | tier |

These compose freely: a `prod` tier can carry a `small` `db2-se` workload from a
named profile. None is hard-mapped to another.

## Deployment tiers driven by the Environment tag

The tier **is** the `Environment` tag value — there is no separate "profile"
concept (the word "profile" is reserved for AWS credentials). The
`Tier_Resolver`:

- Selects the tier from the `Environment` tag when present; if the prompt names
  a tier **and** an `Environment` tag value that differ, it rejects the prompt
  and reports the conflict by name rather than silently choosing one (R3.2).
- Defaults to `sandbox` and sets `Environment=sandbox` when neither is given (R3.3).
- Rejects any value that is not one of the three supported tiers, reporting the
  unrecognized value together with the supported set (R3.8).
- Applies tier defaults first, then prompt overrides (R3.6).
- Records the resolved tier and sets the `Environment` tag to match (R3.7).
- For every tier, resolves all required fields, and the same prompt against the
  same tier yields identical values on each resolution (R3.9, determinism).

### Baseline prompt → resolved intent (R3.4)

The bare prompt **"Deploy RDS for Db2 instance"** (no further detail) resolves to:

| Field | Value |
|---|---|
| `engine_version` (major) | `12.1` |
| `allocated_storage` / `storage_type` | `40` GiB `gp3` |
| `multi_az` | single-AZ (`false`) |
| `instance_class` | `db.t3.xlarge` |
| `backup_retention_period` | `1` day |
| `db_name` | `DB2DB` |
| db2diag.log → CloudWatch | enabled |
| enhanced monitoring | enabled |
| `port` (TCP listener) | `8392` (dormant — see below) |
| SSL service port | `50443` (`DB2COMM=SSL`, `ssl_svcename=50443`) |
| subnet group / SG / KMS key | existing-or-new; KMS is an MRK CMK |

### `prod` tier posture (R3.5)

Selecting `prod` resolves Multi-AZ enabled, a single deterministic instance
class in the `r` (memory-optimized) family, backup retention of at least `7`
days, and deletion protection enabled.

### TCP listener vs SSL service port

`port` (default `8392`) is the non-SSL TCP listener. RDS requires a `port` value
at create time, so the field is always defined — but because the security
invariant sets `DB2COMM=SSL` (not `tcpip,ssl`), the listener is **dormant**: it
accepts no connections and is never opened in the security group. The only port
that accepts client connections, and the only port opened for ingress, is the
SSL service port `50443` set via `ssl_svcename`. See `security-invariants.md`.

## Workload sizes

`Workload_Size` is a t-shirt selector that the `Sizing_Resolver` maps
deterministically to `instance_class`, `storage_type`, `allocated_storage`,
`iops`, and (gp3 only) `storage_throughput`. The full map and its storage rules
live in `storage-iops-throughput.md`; the edition impact of an instance-class
override lives in `ibm-licensing.md`.

The map gives **one prescriptive default class per size** from a memory-optimized
x86 family (`r7i` for `medium`/`large`, `x2iedn` for `xlarge`); this is
prescriptive guidance, not a hard constraint (R17.13). The customer may override
with another memory-optimized **x86** class — Intel (e.g. a future `r8i`), AMD
(e.g. `r8a`), or a newer high-memory family (e.g. `x8i` succeeding `x2iedn`)
(R17.14). The override is `user_provided`; the other sizing fields stay from the
map unless they too are overridden.

### x86-only instance constraint (R17.15)

RDS for Db2 runs on **x86 instance classes only** (Intel or AMD). Graviton/ARM
classes — `r8g` or any `*g` family — are **rejected**: the `Intent_Validator`
refuses the intent, reports that RDS for Db2 does not run on Graviton/ARM, and
halts before Terraform rendering. The guard is implemented in
`resolve_intent.py` (`assert_x86_instance_class` / `is_graviton_instance_class`),
which accepts future x86 families (`r8i`, `r8a`, `x8i`) while refusing any ARM
family. If an override crosses the Standard Edition vCPU/memory ceiling, the
edition reconciliation in `ibm-licensing.md` fires (R17.16).

## Provenance and the self-describing identifier

`_provenance` records `user_provided` / `assumed` for every field listed in the
schema (R4.10), so the Verification_Step can label each value and an auditor can
trace every decision back to the prompt or a tier default.

When no `db_instance_identifier` is supplied, the resolver derives a
self-describing default from the resolved fields (engine, major version,
instance class, workload size, storage type, AZ posture, IOPS suffix, tag),
normalized to the RDS identifier format `^[a-zA-Z][a-zA-Z0-9-]{0,62}$` (R20).
The customer may override it, and the override is marked `user_provided` and
stays exposed as a Terraform variable.

## Sources

- AWS blog, [Deploying Amazon RDS for Db2 using Terraform](https://aws.amazon.com/blogs/database/deploying-amazon-rds-for-db2-using-terraform/).
- Bash provisioner `0cr-ins.sh` (tier defaults, identifier builder, port handling).
- `scripts/resolve_intent.py`, `scripts/instance_specs.py` (implemented behavior).
