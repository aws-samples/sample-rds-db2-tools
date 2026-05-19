# Tools

This directory contains utility scripts and tools for Amazon RDS for DB2.

## Available Tools

- **[Install Db2 driver in AWS Cloud Shell](db2client/)**: Script for installing Db2 runtime client in either AWS CloudShell or in Amazon EC2
- **[Migration Precheck Tools](migrationprecheck/)**: Script for database migration prereq check
- **[RDS Db2 Monitoring](RDS-Db2-Dashboard/)**: Create RDS for Db2 Dashboard to monitior  
- **[RDS for Db2 Terraform Template](rds-db2-terraform/)**: Modular Terraform template to provision RDS for Db2 with remote state, KMS, IAM, parameter group, and AWS License Manager BYOL tracking
- **[End-to-End Trust Proxy](End-to-End-Trust/)**: Modular Terraform template for an SNI-based TLS pass-through proxy (OpenResty on EC2 behind an NLB) that routes multiple RDS for Db2 endpoints through a single set of client ports
- **Security Tools**: Authentication and security configuration helpers
- **Automation Scripts**: Infrastructure and deployment automation

## Usage

Each tool includes its own documentation and usage instructions.