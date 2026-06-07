# IBM licensing: editions, identifiers, and reconciliation

This reference covers everything the skill needs to license Db2 correctly: the
mandatory IBM identifiers and how to obtain them per edition, the edition
matrix, the Community Edition resource caps, the Standard Edition instance
ceiling and the bidirectional edition handling, and the x86-only instance
constraint.

> Grounds: Requirement 7 (IBM licensing identifiers — 7.1–7.9, especially
> 7.3/7.4/7.5), Requirement 8 (edition/licensing reconciliation — especially
> 8.4/8.5/8.6/8.7/8.8), and Requirement 17.15 (x86-only constraint).
> Implemented in `scripts/resolve_intent.py` (`resolve_edition`,
> `assert_x86_instance_class`) and `scripts/validate_intent.py`.

## IBM identifiers are required for every edition

`IBM_Customer_ID` (RDS parameter `rds.ibm_customer_id`) and `IBM_Site_ID`
(`rds.ibm_site_id`) are **customer-supplied** and required for **all three**
editions — `db2-ce`, `db2-se`, and `db2-ae` (R7.2, R8.10). The composer renders
both into the `4-parameter-group` module inputs for every edition (R7.7), and
stores them as `Sensitive_Value`s (Terraform `sensitive` variables, Secrets
Manager, or SSM Parameter Store — R7.6). They are masked in every echo, log, PR,
and artifact.

Validation:

- If any edition is selected and either identifier is absent, the validator
  rejects the intent and reports which identifier is missing (R7.8).
- If a supplied identifier is empty after trimming whitespace, or exceeds 255
  characters, the validator rejects the intent, reports which is malformed, and
  halts before rendering (R7.9).

### Never scrape — a deliberate de-scope (R7.1 / R7.5)

The skill treats both identifiers as inputs from the customer only. It **never**
scrapes, derives, or infers them from any other source, and it **never** attempts
to retrieve them from behind an IBM login. This prohibition is a deliberate
de-scope for **terms-of-service, compliance, and credential-safety** reasons
(R7.5). The skill asks the customer for the values; it does not log in to IBM on
their behalf.

## Acquisition paths by edition

When `IBM_Customer_ID` or `IBM_Site_ID` has not been provided, the agent
requests the missing identifier (R7.2) and, if the customer does not have one,
directs them to the **edition-appropriate** source:

| Edition | If the customer has no IBM IDs | Source |
|---|---|---|
| `db2-ce` (Community) | **Self-service** — no IBM sales interaction (R7.3) | Create an IBM ID at `https://www.ibm.com/account/reg/us-en/signup?formid=urx-54367` for Db2 12.1 Community Edition |
| `db2-se` (Standard) | IBM Passport Advantage or IBM sales rep (R7.4) | IBM Passport Advantage site / IBM sales representative |
| `db2-ae` (Advanced) | IBM Passport Advantage or IBM sales rep (R7.4) | IBM Passport Advantage site / IBM sales representative |

The agent **must not** offer the Community Edition self-service signup link for
`db2-se` or `db2-ae` (R7.4). After the customer provides the values via the
correct path, the agent captures them as `Sensitive_Value`s.

## Edition matrix

| Edition | `engine` | Versions | License model | Instance ceiling |
|---|---|---|---|---|
| Community | `db2-ce` | 12.1 only | no commercial license charge (dev/test) | CE caps (below) |
| Standard | `db2-se` | 11.5, 12.1 | `bring-your-own-license` | ≤ 32 vCPU **and** ≤ 128 GB |
| Advanced | `db2-ae` | 11.5, 12.1 | `bring-your-own-license` | **none** (any class) |

Edition is **independent of the tier** (R8.1): any edition may combine with any
tier — `db2-se` or `db2-ae` may back a `dev` or `prod` tier. When the prompt
names no edition, the resolver defaults `engine` to `db2-se` (the most common
customer edition), marks it `assumed`, and records it (R8.3). `db2-se`/`db2-ae`
render `license-model = bring-your-own-license` (R8.9).

## Community Edition (`db2-ce`) resource caps (R8.8)

Db2 Community Edition is a free, full-feature edition intended for development
and test workloads. It provides all the features available in Standard and
Advanced Editions with no commercial software licensing charge, but it enforces
**resource caps** on the running engine:

- **Maximum 4 cores (vCPU) used by the Db2 engine.**
- **Maximum 16 GB of memory used by the Db2 engine.**

(IBM Db2 Community Edition historically also caps usable data at roughly 100 GB.
The engine ignores capacity beyond its caps, so provisioning a larger instance
class for `db2-ce` wastes resources the engine will not use.) The skill
**documents** these caps so a customer choosing `db2-ce` understands that a class
larger than ~4 vCPU / 16 GB will not be fully used by the engine. CE requires
`IBM_Customer_ID` and `IBM_Site_ID` obtained through the self-service IBM ID
signup, with no IBM sales interaction (R8.8, per R7.3).

> Note on the SE ceiling vs CE caps: the resolver's `resolve_edition` enforces
> the **SE** instance ceiling in code; CE caps are an engine-side
> characteristic documented here for guidance (they do not trigger an automatic
> edition conversion).

## Standard Edition ceiling and bidirectional handling

The license **ceiling is asymmetric**: IBM Db2 **Standard Edition is the only
edition with an instance ceiling** — licensed up to **32 vCPU AND 128 GB memory,
both inclusive** (`SE_MAX_VCPU = 32`, `SE_MAX_MEMORY_GIB = 128` in
`resolve_intent.py`). Beyond that (vCPU > 32 **or** memory > 128 GB) SE is not
permitted and `db2-ae` is required (R8.4). **Advanced Edition has no instance
ceiling** — `db2-ae` is valid on *any* class, from `db.t3.small` up (R8.2), and
is never converted or second-guessed.

vCPU and memory are read from a **grounded source** (the RDS-published
instance-class specifications in `scripts/instance_specs.py`), never a hardcoded
guess (R8.7). A class that cannot be grounded raises rather than letting the
skill fabricate a number.

There are **two distinct edition movements, with opposite automation**:

### Forced SE → AE (automatic, never silent) — R8.5

If a resolved `instance_class` exceeds the SE ceiling while `engine=db2-se`
(whether SE was defaulted or `user_provided`), the resolver **converts the engine
to `db2-ae`**, records the conversion and its reason (the exceeded vCPU/memory
ceiling) in the intent, raises a warning surfaced in the Verification_Step, and
**requires acknowledgement** before rendering proceeds. The conversion is forced
because Standard Edition cannot legally run on that class — but it is **never
applied silently**.

### Customer-initiated AE → SE (supported, never automatic) — R8.6

When a customer rightsizes down to a class that fits the SE ceiling, they often
also downgrade `db2-ae` → `db2-se` to cut license cost. The skill **honors** an
explicit `db2-se` choice on an SE-eligible class. If the customer keeps `db2-ae`
on such an SE-eligible class, the skill **may surface the AE → SE downgrade as
cost guidance** but **never changes the edition automatically**. A customer may
deliberately keep `db2-ae` on a small instance — a business decision (a single
negotiated enterprise AE license applied to all workloads); the skill treats an
explicit `db2-ae` choice as always valid.

If a customer requests `db2-se` on a class that **exceeds** the SE ceiling, the
forced SE → AE rule applies instead.

### Campaign classes checked against the SE ceiling

| Size | class | vCPU / memory | vs SE ceiling |
|---|---|---|---|
| `medium` | `r7i.2xlarge` | 8 / 64 GB | within SE |
| `large` | `r7i.4xlarge` | 16 / 128 GB | within SE (memory exactly at the inclusive ceiling) |
| `xlarge` | `x2iedn.16xlarge` | 64 / 1024 GB | exceeds SE → AE required |

## x86-only instance constraint (R17.15)

RDS for Db2 runs on **x86 instance classes only** (Intel or AMD). Graviton/ARM
classes — `r8g` or any `*g` family — are **rejected**: the validator refuses the
intent, reports that RDS for Db2 does not run on Graviton/ARM, and halts before
rendering. The guard (`assert_x86_instance_class` /
`is_graviton_instance_class` in `resolve_intent.py`) accepts future x86 families
(`r8i` Intel, `r8a` AMD, `x8i` high-memory) while refusing any ARM family. See
`intent-and-tiers.md` for the instance-family override model.

## License Manager (R8.11 / R8.12)

For `db2-se`/`db2-ae` with License Manager tracking requested, the composer
renders the `6-license-manager` module with a `vCPU` license-counting type and a
product-information filter on the matching Engine Edition. Requesting `db2-ce`
together with BYOL-specific License Manager tracking is a **conflict**: the
validator flags it by name (`ce_license_manager_conflict`), explains that
Community Edition does not carry BYOL the way SE/AE do, and halts (R8.12).

## Sources

- AWS, [Amazon RDS for Db2 launches support for IBM Db2 v12.1 and Db2 Community Edition](https://aws.amazon.com/about-aws/whats-new/2026/06/amazon-rds-db2-v12-community-edition).
- AWS, [Amazon RDS for Db2 pricing](https://aws.amazon.com/rds/db2/pricing/) and
  [RDS for Db2 FAQs](https://aws.amazon.com/rds/db2/faqs/) (editions, IBM ID/Site
  ID acquisition).
- IBM Db2 product documentation (edition feature/resource limits). Content
  rephrased for compliance with licensing restrictions.
- AWS blog, [Deploying Amazon RDS for Db2 using Terraform](https://aws.amazon.com/blogs/database/deploying-amazon-rds-for-db2-using-terraform/).
- `scripts/resolve_intent.py` (`resolve_edition`, `SE_MAX_VCPU`,
  `SE_MAX_MEMORY_GIB`), `scripts/instance_specs.py`, `scripts/validate_intent.py`.
