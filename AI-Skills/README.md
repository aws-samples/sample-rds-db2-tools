# AI Skills for Amazon RDS for Db2

This folder is a catalog of AI agent skills for working with Amazon RDS for Db2.
Each skill lives in its own subfolder with a self-contained `SKILL.md` manifest
and a `README.md` that documents what it does and how to use it.

## What is an agent skill?

A skill is a knowledge-and-capability package an AI agent loads on demand. These
skills use the portable [Agent Skills](https://www.anthropic.com/news/skills)
format (a `SKILL.md` manifest plus supporting references and scripts), so they
work with any agent tool that supports that format — including Anthropic Claude
(Claude Code / Claude Desktop), [Kiro](https://kiro.dev), and other compatible
assistants. When your prompt matches a skill's domain, the agent activates it
automatically and uses its content to give a more specific, accurate answer — or,
for an action skill, to drive a concrete workflow.

## Available skills

| Skill | What it does | Details |
|---|---|---|
| [`rds-db2-deployer`](rds-db2-deployer/) | Turns a natural-language prompt into a reproducible Amazon RDS for Db2 deployment. A Terraform composer/orchestrator that captures a schema-validated intent, validates it, gets human approval, renders Terraform over the published modules, and drives a GitOps apply. | [README](rds-db2-deployer/README.md) |

> Adding a new skill? Drop it in its own subfolder with a `SKILL.md` and a
> `README.md`, then add one row to the table above pointing at its README. Keep
> the per-skill detail in that skill's README so this index stays a thin catalog.

## Installing a skill

Each skill is a directory you copy into the location your agent reads skills from.
For example, with Kiro:

```bash
# Global install
mkdir -p ~/.kiro/skills
cp -R <skill-folder> ~/.kiro/skills/

# Or scope it to a single project
mkdir -p /path/to/your/project/.kiro/skills
cp -R <skill-folder> /path/to/your/project/.kiro/skills/
```

For Anthropic Claude or other tools, copy the skill folder into that tool's
skills directory instead (see your agent's documentation for the exact path). The
agent picks the skill up automatically. See the skill's own README for any
skill-specific prerequisites or configuration.

## Source

Maintained as part of
[aws-samples/sample-rds-db2-tools](https://github.com/aws-samples/sample-rds-db2-tools).
Open an issue there to report inaccuracies or request additional topics.
