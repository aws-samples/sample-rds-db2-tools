# Storage capacity, IOPS, and throughput rules

This reference documents the exact allocated-storage, IOPS, and throughput rules
the skill enforces. Every rule is ported faithfully from the proven bash
provisioner `0cr-ins.sh` (`get_allocated_storage`, `get_iops`,
`get_default_iops_per_size`, `get_storage_throughput`) so a rendered deployment
never violates an RDS for Db2 storage constraint.

> Grounds: Requirement 19 (storage capacity, IOPS, throughput rules).
> Implemented across `schemas/deployment-intent.schema.json` (Layer 1,
> `Schema_Constraint`) and `scripts/validate_intent.py` (Layer 2,
> `Cross_Field_Rule`). The gp3-throughput derivation lives in
> `scripts/resolve_intent.py` (`derive_gp3_storage_throughput`).

## Only two storage types

`storage_type` is constrained to **`gp3` or `io2`** (R18.1). `io1`, `gp2`, and
`standard` are rejected. The two supported types have different rule sets,
summarized below and detailed after.

| Concern | gp3 | io2 |
|---|---|---|
| `allocated_storage` floor | ≥ 20 GiB (R19.2) | ≥ 100 GiB (R19.3) |
| `allocated_storage` ceiling | < 64000 GiB (R19.1) | < 64000 GiB (R19.1) |
| `iops` | only at ≥ 400 GiB; range `[12000, 64000]` (R19.5) | always required (R18.3) |
| `storage_throughput` | only at ≥ 400 GiB; **derived** (R19.7) | forbidden (R18.3) |
| ratio `iops / allocated_storage` | `(0, 500]` (R19.6) | `[0.5, 1000]` (R19.8) |

## Which layer enforces what

Validation is two-layer (see `intent-schema.md`). Storage rules split as:

- **Layer 1 (`Schema_Constraint`, JSON Schema):** single-field ranges and
  presence conditionals — the `allocated_storage` floors/ceiling, the gp3 IOPS
  `[12000, 64000]` range, and the presence/absence of `iops` and
  `storage_throughput` by `storage_type` and the 400-GiB threshold.
- **Layer 2 (`Cross_Field_Rule`, code validator):** arithmetic JSON Schema
  cannot express — the IOPS-to-storage ratios and the derived-throughput
  equality. Each failure reports the **computed** value and the allowed bound by
  name, then halts before rendering.

## gp3 rules

1. **Floor / ceiling.** `allocated_storage` must be ≥ 20 GiB (R19.2) and
   < 64000 GiB (R19.1).
2. **Below 400 GiB → baseline only.** When `allocated_storage` < 400 GiB, the
   schema **forbids both** `iops` and `storage_throughput` (R19.4). RDS applies
   its gp3 baseline; specifying either is rejected with a message saying gp3
   below 400 GiB uses the RDS baseline. (This is why the `xsmall` workload size,
   40 GiB gp3, carries no `iops`/`storage_throughput`.)
3. **At or above 400 GiB → both required.** When `allocated_storage` ≥ 400 GiB,
   the schema **requires both** `iops` and `storage_throughput` (R19.5), with
   `iops` in the inclusive range `[12000, 64000]`.
4. **Ratio bound (Layer 2).** For gp3 at ≥ 400 GiB, the ratio
   `iops / allocated_storage` must be **> 0 and ≤ 500** (R19.6).
5. **Throughput is derived, never free-set (Layer 2).** For gp3 at ≥ 400 GiB,
   `storage_throughput` must equal:

   ```
   storage_throughput = min(floor(iops / 4), 4000)
   ```

   The validator rejects any intent whose `storage_throughput` differs from this
   derived value (R19.7). The 4000 cap means any `iops` ≥ 16000 lands at the
   maximum throughput of 4000 MiB/s.

## io2 rules

1. **Floor / ceiling.** `allocated_storage` must be ≥ 100 GiB (R19.3) and
   < 64000 GiB (R19.1).
2. **`iops` required, `storage_throughput` forbidden.** io2 always requires
   `iops` and forbids `storage_throughput` (R18.3); io2 throughput scales with
   provisioned IOPS automatically and is not a separate input.
3. **Ratio bound (Layer 2).** The ratio `iops / allocated_storage` must be in
   the inclusive range `[0.5, 1000]` (R19.8).

## Worked examples (from the Workload_Sizing_Map)

These are the default sizes the `Sizing_Resolver` resolves; each satisfies the
rule for the `storage_type` it maps to (R19.9). See `intent-and-tiers.md` for
the full map and the two source-data reconciliations.

| Size | type | storage | iops | ratio | throughput |
|---|---|---|---|---|---|
| `xsmall` | gp3 | 40 GiB | (unset) | n/a (< 400 GiB) | (unset) |
| `small` | gp3 | 400 GiB | 20000 | 50 ✓ (≤ 500) | `min(floor(20000/4),4000)` = 4000 |
| `medium` | gp3 | 3000 GiB | 64000 | 21.3 ✓ | `min(floor(64000/4),4000)` = 4000 |
| `large` | io2 | 16000 GiB | 130000 | 8.1 ✓ ([0.5,1000]) | n/a (io2) |
| `xlarge` | io2 | 35000 GiB | 200000 | 5.7 ✓ | n/a (io2) |

> Note: the source `large.env` set `gp3` with `IOPS=130000`, but 130000 exceeds
> the gp3 ceiling (64000) and ratio cap (500); it is only valid under io2's
> `[0.5, 1000]` ratio. The map classifies `large` as `io2` (the env file's `gp3`
> is treated as a copy-paste error). This reconciliation is documented in the
> design's Sizing_Resolver section.

## Sources

- Bash provisioner `0cr-ins.sh` — `get_allocated_storage`, `get_iops`,
  `get_default_iops_per_size`, `get_storage_throughput` (the ported source).
- AWS docs, [Amazon RDS DB instance storage](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_Storage.html)
  (gp3/io2 provisioned IOPS and throughput characteristics).
- AWS blog, [Deploying Amazon RDS for Db2 using Terraform](https://aws.amazon.com/blogs/database/deploying-amazon-rds-for-db2-using-terraform/).
- `scripts/validate_intent.py`, `scripts/resolve_intent.py`.
