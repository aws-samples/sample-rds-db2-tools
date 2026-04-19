# Amazon RDS for Db2 — Kiro AI Skill

This folder contains a Kiro skill that gives the Kiro AI assistant deep, accurate knowledge of Amazon RDS for Db2. Once installed, you can ask Kiro questions in plain language and get precise answers with exact commands, stored procedure syntax, and AWS CLI examples — without having to search documentation yourself.

## What is a Kiro skill?

A Kiro skill is a knowledge package that loads into the [Kiro](https://kiro.dev) AI assistant. When you ask a question that matches the skill's domain, Kiro automatically activates it and uses its reference content to give you a better, more specific answer than it could from general knowledge alone.

## What this skill covers

Ask Kiro anything about RDS for Db2, including:

**Connectivity**
- Connect from CloudShell, EC2, or your laptop using the Db2 CLP
- SSL connections from CLP, Python, and Java
- VPC and security group requirements
- Kerberos authentication with Microsoft Active Directory (AWS Managed AD, self-managed AD, cross-account)

**Migration**
- Rehost from Linux using Db2 backup/restore or Db2MT
- Replatform from AIX, Windows, z/OS, or AS/400
- Near-zero downtime migration using IBM Q Replication (IIDR)
- AWS DMS for full load and CDC
- Migration precheck tool to validate readiness before backup
- Zero downtime upgrade using online restore and rollforward

**Backup and Restore**
- Automated backups and manual snapshots
- Database backup to Amazon S3 using storage access aliases
- Restore using `rdsadmin.restore_database` stored procedure
- Online restore with rollforward and `rdsadmin.complete_rollforward`
- Point-in-Time Restore (PiTR), including cross-region

**High Availability and Disaster Recovery**
- Multi-AZ deployments (synchronous, in-region)
- Standby replicas (cross-region, HADR SUPERASYNC)
- Read replicas
- Failover, promotion, RPO/RTO guidance

**Operations**
- Scale compute and storage
- Parameter groups and Db2 registry variables
- RDSADMIN stored procedures reference
- Load data directly from Amazon S3
- Enable and download db2diag logs
- CloudWatch and Enhanced Monitoring
- Audit configuration
- Performance benchmarks with HammerDB

**Mainframe Migration (z/OS)**
- DDL conversion from Db2 for z/OS using ADB2GEN and the Python conversion script
- Code page and collation selection (ISO-8859-1, ISO-8859-15, UTF-8) for zero data loss
- Migration tools: AWS DMS, Qlik Replicate, Precisely, IBM Q Replication, Db2 Federation
- EBCDIC collation preservation

## Installation

### Option 1 — Install from the `.skill` file (recommended)

```bash
# Create the Kiro global skills directory if it doesn't exist
mkdir -p ~/.kiro/skills

# Unzip the skill into it
unzip rds-db2.skill -d ~/.kiro/skills/
```

Kiro picks up the skill automatically. No restart required in most cases — if Kiro is already open, reload the window once.

### Option 2 — Install into a specific workspace

If you want the skill available only within a particular project:

```bash
mkdir -p /path/to/your/project/.kiro/skills
unzip rds-db2.skill -d /path/to/your/project/.kiro/skills/
```

### Verify installation

In Kiro chat, type:

```
/context show
```

You should see `rds-db2` listed under loaded skills.

## How to use it

Just ask Kiro questions naturally. The skill activates automatically when your question is about RDS for Db2. You do not need to mention the skill by name.

**Example prompts to try:**

```
How do I connect to my RDS for Db2 instance from CloudShell?
```
```
My EC2 is in a private subnet with no internet. How do I install the Db2 client?
```
```
How do I restore a self-managed Db2 Linux backup into RDS for Db2?
```
```
What is the right code page to use when migrating from mainframe Db2 CCSID 37?
```
```
How do I set up a standby replica in another AWS region?
```
```
How do I enable db2diag logs to go to CloudWatch?
```
```
What migration strategy should I use to move from AIX Db2 to RDS for Db2 with near-zero downtime?
```
```
Does RDS for Db2 support Java stored procedures?
```
```
How do I load data into RDS for Db2 directly from S3?
```
```
What RDSADMIN stored procedures are available?
```

## Updating the skill

When a new version of `rds-db2.skill` is released, reinstall it:

```bash
rm -rf ~/.kiro/skills/rds-db2
unzip rds-db2.skill -d ~/.kiro/skills/
```

## Source

This skill is maintained as part of the [aws-samples/sample-rds-db2-tools](https://github.com/aws-samples/sample-rds-db2-tools) repository. Open an issue there to report inaccuracies or request additional topics.
