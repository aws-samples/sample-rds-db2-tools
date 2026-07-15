# rds-db2-deployer — Amazon RDS for Db2 provisioning skill

A `SKILL.md`-driven agent skill that turns a natural-language prompt (for example,
"Deploy an RDS for Db2 prod instance") into a definitive, reproducible Amazon RDS
for Db2 deployment.

It uses the portable [Agent Skills](https://www.anthropic.com/news/skills) format
(a `SKILL.md` manifest plus supporting references and scripts), so it works with
any agent tool that supports that format — including Anthropic Claude (Claude Code
/ Claude Desktop), Kiro, and other compatible assistants. The examples below use
Kiro, but the skill is not Kiro-specific.

It is a **Terraform composer/orchestrator**: it gathers a deployment intent,
validates it against a published JSON Schema, enforces security invariants,
obtains human approval, and renders Terraform that **reuses the existing modular
Terraform** published in this same repository under
[`tools/rds-db2-terraform/`](../../tools/rds-db2-terraform) (`0-backend-setup`
through `6-license-manager`). It never authors a new imperative deployer.

For advisory, troubleshooting, connectivity, migration, backup/restore, and
HA/DR questions, this skill defers to its companion advisory skill, `rds-db2`.

## What it does

1. **intent** — captures and resolves a schema-validated `deployment-intent.json`
   (tier defaults, workload sizing, engine/edition, identifier), tagging every
   field's provenance (`user_provided` / `assumed`).
2. **validate** — a two-layer validator checks the intent against the JSON Schema
   and the cross-field arithmetic + security invariants (CMK-everywhere,
   `DB2COMM=SSL`, `ssl_svcename=50443`, SSL-only ingress). It halts before
   rendering on any failure.
3. **verify** — echoes the resolved intent (sensitive values masked) and records
   human approval; `prod` always requires interactive approval.
4. **precheck** — validates VPC readiness (subnets across AZs, DNS attributes, S3
   gateway / interface endpoints) with a severity model.
5. **compose** — renders the root module + per-module `terraform.tfvars` over the
   reused modules, pinned to a git tag for reproducibility.
6. **gitops** — opens a pull request, posts a masked `terraform plan`, runs the
   policy-as-code gate, and applies on merge.

## Package layout

```
rds-db2-deployer/
├── SKILL.md                 # skill manifest + router (the entry point Kiro loads)
├── references/              # focused reference docs, one per pipeline stage/axis
├── schemas/                 # deployment-intent.schema.json (JSON Schema 2020-12)
├── scripts/                 # resolvers, validator, precheck, composer, policy gate
│   ├── eval/                # end-to-end + burner-account evaluation drivers
│   └── tests/               # pytest unit + Hypothesis property tests
├── templates/terraform/     # rendered root module + tfvars land here at runtime
└── artifacts/               # per-deployment outputs at runtime: artifacts/<name>/
```

The scripts and their components:

| Script | Component |
|---|---|
| `scripts/resolve_intent.py` | Tier_Resolver + Sizing_Resolver + identifier builder |
| `scripts/validate_intent.py` | two-layer Intent_Validator (schema + cross-field) |
| `scripts/vpc_precheck.py` | VPC_Precheck |
| `scripts/render_terraform.py` | Terraform_Composer |
| `scripts/policy_gate.py` | GitOps Policy_Gate |
| `scripts/gitops.py` | GitOps_Orchestrator (PR + masked plan + apply-on-merge) |
| `scripts/credentials.py` | AWS credential / identity source |
| `scripts/artifacts.py` | per-deployment artifact writer (sensitive values masked) |

## Using the skill

Install the skill into your agent's skills directory, then ask in plain language.
The skill follows the portable Agent Skills format, so it works with any
compatible agent tool (Anthropic Claude, Kiro, and others). Installation is just
copying the folder to the location your tool reads skills from. For example:

```bash
# Kiro — global install
mkdir -p ~/.kiro/skills
cp -R rds-db2-deployer ~/.kiro/skills/

# Kiro — workspace-scoped
mkdir -p /path/to/your/project/.kiro/skills
cp -R rds-db2-deployer /path/to/your/project/.kiro/skills/

# Claude (Claude Code / Desktop) and other tools — copy into that tool's
# skills directory instead (see your agent's documentation for the path).
```

The agent activates the skill automatically when your prompt is about
provisioning RDS for Db2. Example prompts:

```
Deploy an RDS for Db2 prod instance in us-east-1.
Provision a Db2 sandbox for dev, smallest viable size.
Render the Terraform for a multi-AZ db2-ae instance with a cross-region standby.
```

### Configuration before a live apply

The composer pins the reused Terraform modules to a git **tag** and writes a
per-deployment S3 remote-state backend. Two values must be set for a real
`terraform apply`:

- **Module ref** — the git tag the module sources are pinned to. Set
  `DEFAULT_MODULE_REF` in `scripts/render_terraform.py` (or export
  `RDS_DB2_MODULE_REF=<tag>`) to a real release tag of this repository.
- **State bucket** — the S3 bucket bootstrapped by `0-backend-setup`. Supply it
  via `RDS_DB2_STATE_BUCKET=<bucket>` or
  `terraform init -backend-config="bucket=<bucket>"`.

For airgapped / no-egress environments, render with `source_mode="local"` to emit
relative paths to a vendored copy of the modules instead of the git source.

You provide your own IBM Passport Advantage `ibm_customer_id` / `ibm_site_id`; the
skill trusts the values and does not validate them (the shipped example values are
well-formed placeholders, not real IDs).

## Local development

Python 3.11+. From this directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

This installs the runtime deps (`jsonschema`, `boto3`) plus the dev deps
(`hypothesis`, `pytest`), pinned to exact versions in `pyproject.toml`.

### Running the tests

```bash
pytest                                   # full suite
pytest scripts/tests/test_render_terraform.py   # a single file
pytest -k resolve                        # filter by name
pytest -q                                # quiet
```

> Property-based tests run many generated examples and can take longer than
> ordinary unit tests. The terraform-validate integration tests require the
> `terraform` binary on `PATH`.

### Running the pipeline entry points

The pure stages (resolution, schema validation, Terraform rendering,
`terraform validate`/`plan`) run without AWS:

```bash
python -m scripts.resolve_intent --help
python -m scripts.validate_intent path/to/deployment-intent.json
python -m scripts.render_terraform path/to/deployment-intent.json
```

## Notes

- **No new deployment engine.** All execution composes the reused Terraform
  modules under `tools/rds-db2-terraform/`; module extensions are backward
  compatible.
- **Sensitive values** (IBM customer/site IDs, manual master password) are masked
  in all echo, log, PR, and artifact output.

## Source

Maintained as part of
[aws-samples/sample-rds-db2-tools](https://github.com/aws-samples/sample-rds-db2-tools).
Open an issue there to report problems or request topics.
