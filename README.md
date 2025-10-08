# Amazon RDS for DB2 - Tools, Scripts & Resources

A comprehensive collection of tools, scripts, and resources for working with Amazon RDS for DB2, including migration strategies, performance optimization, and best practices.

## 📚 Blog Articles & Guides

### Migration & Replication
- [Data Migration Strategies to Amazon RDS for DB2](https://aws.amazon.com/blogs/database/data-migration-strategies-to-amazon-rds-for-db2/)
- [Near-Zero Downtime Migrations from Self-Managed DB2 to Amazon RDS for DB2 using IBM Q Replication](https://aws.amazon.com/blogs/database/near-zero-downtime-migrations-from-self-managed-db2-on-aix-or-windows-to-amazon-rds-for-db2-using-ibm-q-replication/)
- [Migrating Tables from IBM DB2 for z/OS to Amazon RDS for DB2](https://aws.amazon.com/blogs/database/migrating-tables-from-ibm-db2-for-z-os-to-amazon-rds-for-db2/)
- [Performance Optimization of Full Load and Ongoing Replication Tasks](https://aws.amazon.com/blogs/database/performance-optimization-of-full-load-and-ongoing-replication-tasks-from-self-managed-db2-to-amazon-rds-for-db2/)

### High Availability & Disaster Recovery
- [Create Self-Managed Replicas for Read Scaling and Disaster Recovery](https://aws.amazon.com/blogs/database/create-self-managed-replicas-for-an-amazon-rds-for-db2-instance-for-read-scaling-and-disaster-recovery/)
- [Configure Amazon RDS for DB2 Standby Replicas for High Availability](https://aws.amazon.com/blogs/database/configure-amazon-rds-for-db2-standby-replicas-for-high-availability-and-faster-disaster-recovery/)

### Security & Authentication
- [Authenticate Amazon RDS for DB2 using On-Premises Microsoft Active Directory and Kerberos](https://aws.amazon.com/blogs/database/authenticate-amazon-rds-for-db2-instances-using-on-premises-microsoft-active-directory-and-kerberos/)
- [Enable Kerberos Authentication with Amazon RDS for DB2](https://aws.amazon.com/blogs/database/enable-kerberos-authentication-with-amazon-rds-for-db2/)
- [Enhance Security with AWS Managed Microsoft AD](https://aws.amazon.com/blogs/modernizing-with-aws/enhance-security-of-your-aws-app-integration-with-aws-managed-microsoft-ad/)

### Networking & Connectivity
- [Best Practices for Creating a VPC for Amazon RDS for DB2](https://aws.amazon.com/blogs/database/best-practices-for-creating-a-vpc-for-amazon-rds-for-db2/)
- [Join Amazon RDS for DB2 Instances Across Accounts to a Single Shared Domain](https://aws.amazon.com/blogs/database/join-your-amazon-rds-for-db2-instances-across-accounts-to-a-single-shared-domain/)
- [Connect to Amazon RDS for DB2 using AWS CloudShell](https://aws.amazon.com/blogs/database/connect-to-amazon-rds-for-db2-using-aws-cloudshell/)

### Performance & Testing
- [Use HammerDB to Run Performance Tests on Amazon RDS for DB2](https://aws.amazon.com/blogs/database/use-hammerdb-to-run-performance-tests-on-amazon-rds-for-db2/)

## 🛠️ Tools & Scripts

### Migration Tools
- **[Db2 Migration Prereqcheck tool](tools/migrationprecheck/)**: Scripts for checking if the self-managed Db2 database on Linux is ready for migration to Amazon RDS for Db2.

### Performance Tools
- **Coming Soon**: HammerDB configuration templates
- **Coming Soon**: Performance monitoring scripts

### Security Tools
- **Coming Soon**: Kerberos configuration helpers
- **Coming Soon**: VPC setup automation

## 🚀 Getting Started

1. Browse the [blog articles](#-blog-articles--guides) for comprehensive guides
2. Check the `tools/` directory for ready-to-use scripts
3. Review the `examples/` directory for configuration samples

## 📁 Repository Structure

```
├── tools/           # Utility scripts and tools
├── examples/        # Configuration examples and templates
├── docs/           # Additional documentation
└── tests/          # Test scripts and validation tools
```

## 🤝 Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## 📄 License

This project is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file for details.

## 📞 Support

For questions about Amazon RDS for DB2, visit the [AWS Database Blog](https://aws.amazon.com/blogs/database/) or reach out through AWS Support.