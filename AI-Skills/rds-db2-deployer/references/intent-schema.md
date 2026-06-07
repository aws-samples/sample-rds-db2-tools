# Intent schema: dialect, required fields, conditional dependencies

The skill publishes exactly one `Intent_Schema` —
`schemas/deployment-intent.schema.json` — that every `deployment-intent.json`
must validate against. This reference documents the schema's dialect, its
always-required fields, and the conditional dependencies it encodes.

> Grounds: Requirements 4 (schema and validation), 18 (conditional parameter
> dependencies grounded in `create-db-instance`).
> Implemented in `schemas/deployment-intent.schema.json` (Layer 1) and
> `scripts/validate_intent.py` (Layer-2 cross-field rules; see
> `storage-iops-throughput.md` and `security-invariants.md`).

## Two-layer validation

"Definitive validation" is split deliberately:

- **Layer 1 — JSON Schema (`Schema_Constraint`):** single-field ranges, enums,
  presence, and presence conditionals expressible in JSON Schema (`if`/`then`/
  `else`, `dependentRequired`, `dependentSchemas`, `oneOf`, `allOf`, `not`).
- **Layer 2 — code validator (`Cross_Field_Rule`):** arithmetic across two or
  more fields that JSON Schema cannot express (IOPS-to-storage ratios, derived
  throughput, security cross-checks).

Do **not** move arithmetic ratio/throughput checks into the schema — JSON Schema
cannot do cross-field arithmetic. Both layers report every failing field/rule by
name and halt before any Terraform is rendered; no partial artifacts (R4.3/4.4).

## Dialect

The schema declares the JSON Schema dialect it conforms to via `$schema`, so
validation results are identical across any conformant validator (R4.1). There
is exactly one schema file in the package.

## Naming and boolean convention

- One canonical naming convention: **snake_case** (R4.5).
- Each on/off setting is **one boolean** field (`multi_az`,
  `publicly_accessible`, `deletion_protection`, `storage_encrypted`), never a
  CLI-style `flag | no-flag` pair (R4.5). Mapping these canonical fields to AWS
  CLI flags or Terraform module variables is a rendering concern handled by the
  composer (see `terraform-composition.md`).
- `_provenance` is an object whose every value is exactly `user_provided` or
  `assumed` (R4.10).

## Always-required fields (R4.6)

The schema rejects any intent in which any of these is absent or null:

```
deployment_tier, workload_size, region, engine, engine_version,
master_username, db_name, port, license_model, instance_class,
allocated_storage, storage_type, multi_az, backup_retention_period,
publicly_accessible, storage_encrypted, kms_key_id, vpc_security_group_ids,
db_subnet_group_name, db_parameter_group_name, monitoring_interval,
enable_cloudwatch_logs_exports, deletion_protection, tags
```

## Enums

- `engine`: `db2-ce`, `db2-se`, `db2-ae` (R5.2). Any other value is rejected
  with the three permitted values (R5.3).
- `storage_type`: **`gp3` or `io2` only** (R18.1). `io1`, `gp2`, and `standard`
  are rejected with a message that only `gp3` and `io2` are supported.
- `deployment_tier`: `sandbox`, `dev`, `prod`.
- `workload_size`: `xsmall`, `small`, `medium`, `large`, `xlarge`.
- `license_model`: `bring-your-own-license` (R18.11).

## Conditional dependencies (R18, encoded with JSON Schema constructs — R4.8)

| Dependency | Construct | Req |
|---|---|---|
| `io2` requires `iops`, forbids `storage_throughput` | `if`/`then` + `not` | 18.3, 4.7 |
| `gp3` ≥ 400 GiB requires both `iops` and `storage_throughput` | `if`/`then` + `dependentRequired` | 4.7, 19.5 |
| `gp3` < 400 GiB forbids both | `if`/`then` + `not` | 4.7, 19.4 |
| `gp3` ≥ 400 GiB: `iops` in `[12000, 64000]` | range under `if`/`then` | 19.5 |
| `monitoring_interval` > 0 requires `monitoring_role_arn` | `if`/`then` | 18.4 |
| managed password XOR manual password | `oneOf` | 18.5 |
| AWS Managed AD requires `domain` + `domain_iam_role_name` | `if`/`then` | 18.6 |
| self-managed AD requires `domain_fqdn`, `domain_ou`, `domain_auth_secret_arn`, `domain_dns_ips`, `domain_iam_role_name` | `if`/`then` | 18.7 |
| AWS Managed AD and self-managed AD mutually exclusive | `oneOf` / `not` | 18.8 |
| `multi_az=true` forbids a pinned `availability_zone` | `not` | 18.9 |
| Multi-AZ or standby requires `backup_retention_period` ≥ 1 | `if`/`then` | 18.10 |
| `license_model` = `bring-your-own-license`; required for `db2-se`/`db2-ae` | enum + `if`/`then` | 18.11 |
| `enable_cloudwatch_logs_exports` ⊆ supported Db2 log types (`diag.log`, `notify.log`) | `items`/`enum` | 18.12 |
| `port` in valid TCP range and **≠ 50443** | range + `not` | 18.13 |
| `backup_retention_period` in `0`–`35` | range | 18.10 |
| identifier length/format `^[a-zA-Z][a-zA-Z0-9-]{0,62}$` | `pattern` | 20.3 |

The TCP listener `port` (default `8392`) and the fixed SSL service port `50443`
are two distinct ports; `port` must never equal `50443` (R18.13). See
`security-invariants.md`.

## Round-trip property (R4.9)

For all valid intents, **validate → serialize → re-validate** produces a
document that still validates. This idempotence is a property-based test target
(see `scripts/tests/`).

## Validation flow

1. The complete intent is validated against the JSON Schema (Layer 1).
2. On any failure, every failing field and the specific rule it violated are
   reported, and the process halts before producing any Terraform artifact
   (R4.2/4.3/4.4).
3. Valid intents proceed to the Layer-2 cross-field rules.

## Sources

- AWS CLI reference, [`create-db-instance`](https://docs.aws.amazon.com/cli/latest/reference/rds/create-db-instance.html) (R18 grounding).
- `schemas/deployment-intent.schema.json`, `scripts/validate_intent.py`.
