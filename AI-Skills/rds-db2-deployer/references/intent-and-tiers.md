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

### Present the consolidated request as a short numbered menu

Per the SKILL.md interaction style, surface that consolidated request as a
**low-typing menu**, not an interrogation. After loading `account-defaults.json`
and applying tier/sizing defaults, propose the complete intent and then ask only
for what is genuinely missing — each as a short numbered choice with a "type
another" escape. Selecting an option is identical to typing it (same value, same
`user_provided` provenance). Concrete per-field option sets:

- **Region?** `us-east-1` · `us-west-2` · `eu-west-1` · _type another_
- **Tier?** ▶ `dev` · `sandbox` · `prod` (prod adds Multi-AZ, r-family,
  deletion protection, and a mandatory explicit approval)
- **Size?** ▶ `xsmall` · `small` · `medium` · `large` · `xlarge`
- **Edition?** ▶ `db2-se` · `db2-ae` · `db2-ce`
- **Db2 major version?** ▶ `12.1` (latest) · `11.5` — defaults to the
  account's `engine_major_version` if set, else `12.1`. A prompt value wins.
  Note `db2-ce` is **12.1-only**; the skill resolves the highest available minor
  of the chosen major **live** from the RDS API (never fabricated).
- **Master credentials?** ▶ Managed in Secrets Manager (recommended) · Supply a
  password manually
- **Networking / KMS / monitoring?** ▶ Reuse the values in `account-defaults.json`
  · Reuse different existing ones (paste names/ARNs) · Leave blank to create them
  on the first deploy, then record + reuse (see `account-defaults.md`)
- **Ingress CIDR for SSL 50443?** the account-default (e.g. `10.0.0.0/16`) ·
  _type another_
- **IBM customer / site ID?** use the values in `account-defaults.json` · _type
  the real Passport Advantage IDs_ — **never** offer an option that invents them.

Always include a single recommended fast path (e.g. "▶ Proceed with these") that
reaches the next gate (validate → verify → render) in one selection.

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

### Engine major version (`engine_major_version`)

The major version is resolved from, in precedence order: the **prompt** (e.g.
"deploy an 11.5 dev sandbox"), then the account default `engine_major_version`
in `account-defaults.json`, then `12.1` (current latest). The agent passes the
chosen major to the resolver as the pinned major; the concrete **minor** is then
read **live** from the RDS API (`resolve_engine_version`) and never fabricated.
Valid families are a closed set — `db2-se-11.5`, `db2-se-12.1`, `db2-ae-11.5`,
`db2-ae-12.1`, and `db2-ce-12.1` — so `db2-ce` with `11.5` is rejected with the
supported list.

### Making the identifier unique (the `Project` tag)

The self-describing identifier ends with the **`Project` tag** (lower-cased,
hyphen-normalized) — e.g. `…-gp3-saz-acme` for `Project=acme`. `Project` is
**required and non-empty** in `account-defaults.json` (an empty tag would leave a
trailing hyphen, which is stripped, losing the distinguishing token). Two
deployments of the same shape under the same `Project` therefore produce the same
identifier; to make them distinct, either change `Project` or supply an explicit
name (see below). The created **parameter group** name is derived from the
resolved identifier, so it stays unique per deployment automatically.

### Choosing your own instance name (`db_instance_identifier`)

If you'd rather name the instance yourself, set `db_instance_identifier`. It is
resolved in precedence order: the **prompt** ("deploy a dev sandbox named
`db2-dev-1`") wins, then an account default `db_instance_identifier` in
`account-defaults.json`, then the **auto-derived** self-describing name. Any
supplied name must match the RDS format (1 leading letter, then letters/digits/
hyphens, ≤ 63 chars) and is used verbatim (`user_provided`). Because
`account-defaults.json` is shared across deployments, a single fixed name there
suits a one-instance repo; to run several instances, **omit it** (keep the unique
auto-derived names) or set the name per deployment in the prompt.

## Sources

- AWS blog, [Deploying Amazon RDS for Db2 using Terraform](https://aws.amazon.com/blogs/database/deploying-amazon-rds-for-db2-using-terraform/).
- Bash provisioner `0cr-ins.sh` (tier defaults, identifier builder, port handling).
- `scripts/resolve_intent.py`, `scripts/instance_specs.py` (implemented behavior).
