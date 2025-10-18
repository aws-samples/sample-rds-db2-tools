# Connect to Amazon RDS for Db2 using AWS CloudShell
by Vikram Khatri, Ashish Saraswat, Sumit Kumar, and Rajib Sarkar

Connecting to an [Amazon Relational Database Service (Amazon RDS) for Db2](https://aws.amazon.com/rds/db2/) instance has traditionally required spinning up an [Amazon Elastic Compute Cloud](https://aws.amazon.com/ec2) (Amazon EC2) bastion host or running Db2 clients locally. With the new [AWS CloudShell](https://aws.amazon.com/cloudshell/) virtual private cloud (VPC) integrated environments, you can now securely connect—with no Amazon EC2 required, no local installs, and no cost beyond normal Amazon RDS and AWS networking.

In this post, we show you how to connect to Amazon RDS for Db2 using CloudShell.

## Solution overview

CloudShell offers the following benefits:

- **Zero-cost client** – CloudShell is free; you only pay standard network and Amazon RDS charges
- **Same subnet** – CloudShell offers minimal latency—your CloudShell session sits alongside your RDS database in the VPC
- **No Amazon EC2** – You don’t have to provision, patch, or manage a bastion host
- **Preinstalled AWS CLI** – The [AWS Command Line Interface](https://aws.amazon.com/cli) (AWS CLI) comes configured in CloudShell by default, and CloudShell now fully supports custom VPC networking

The solution consists of the following steps:

1. Launch CloudShell in your VPC.
2. Download and install the [IBM Data Server Driver](https://www.ibm.com/support/pages/download-initial-version-115-clients-and-drivers) thin client.
3. Configure both plain-text (TCP/IP) and SSL connections.
4. Test connectivity with IBM’s [Command line processor plus](https://www.ibm.com/docs/en/db2/11.5.x?topic=commands-command-line-processor-plus-clpplus) (CLPPlus).
Prerequisites

You should have the following prerequisites:

- An existing RDS for Db2 instance, reachable in a VPC
- A VPC subnet and security group that allows inbound access on your Db2 ports (default TCP 50000+ or SSL 50xxx)
- Access to [Amazon CloudShell](https://console.aws.amazon.com/cloudshell/)

## Launch CloudShell in your VPC

Complete the following steps to launch CloudShell in your VPC:

1. Sign in to the [AWS Management Console](https://aws.amazon.com/console) and choose **CloudShell** in the menu bar.
2. In the CloudShell window, choose **Actions** and **Create VPC Environment**.
3. For **Name**, enter a name (for example, `PRIVATE`).
4. For VPC, choose the VPC hosting your RDS for Db2 database.
5. For **Subnet**, choose the subnet ID of the availability zone of the Amazon RDS for Db2 instance.
6. For **Security group(s)**, pick up to five, including rules for TCP and SSL ports.
7. Choose **Create**.

CloudShell will restart inside your private subnet.

CloudShell sessions time out after 30 minutes of inactivity. You can recreate the Db2 client since it is just a single script install.

## How to install Db2 client in AWS CloudShell for Amazon RDS for Db2

**Direct run**

```
curl -sL https://bit.ly/getdb2driver | bash
```

**Download and run**

```
curl -sL https://bit.ly/getdb2driver -o db2-driver.sh
chmod +x db2-driver.sh
./db2-driver.sh
```

**Note**: The above short URL points to - https://aws-blogs-artifacts-public.s3.us-east-1.amazonaws.com/artifacts/DBBLOG-4900/db2-driver.sh

The above script prepares your AWS CloudShell to connect to Amazon RDS for Db2

You must run two commands shown in the output of the tool.

Complete the DSN creation process to connect to DB2 instance:

1. Switch to the db2inst1 user: `sudo su - db2inst1`
2. Run the script: `source db2-driver.sh`

The script does the following when you run it in user `db2inst1`.

- Lists Amazon RDS for Db2 instances and select one that you want to connect
- Catalogs discovered databases in your RDS for Db2 instance in the `db2dsdriver.cfg` file.
- If SSL is enabled, the script also registers SSL connections for each database in your `db2dsdriver.cfg` file.

Now you can use db2 command line processor to connect to `RDSADMIN` database to perform administrative tasks and connect to user defined databases to perform regular Db2 activities.

Run the same script in your Amazon EC2 instance to install the Db2 client to connect to Amazon RDS for Db2 instance. The advantage of using Amazon EC2 is to have persistence of the client, which is not the case with AWS CloudShell.

## Troubleshooting

When you run the curl command to run the script directly and the script does not show any output, it is an indication that your VPC is not set up properly for internet access. For the script to run successfully, you must have internet access available, proper IAM permissions, use the proper subnet ID, and proper security group that has inbound traffic enabled for Db2.

The script might fail if there are no proper IAM permissions available to the user running the script. Check the permissions required to run the script by using the following command:

```
curl -sL https://bit.ly/getdb2driver | bash -s -- --check-permissions
```

or

```
./db2-driver.sh --check-permissions 
```

If you are using the master user password in Amazon Secrets Manager, you can use helper functions such as `get_master_user_password` available in `functions.sh` to populate `MASTER_USER_PASSWORD` environment variable. The script `functions.sh` is installed and sourced for the `db2inst1` user.

If you're not sure which name to use for connecting to the Amazon RDS for Db2 database, you can look at the file `CONN_HELP_README.txt`, which has the db2 command syntax to connect to Amazon RDS for Db2.

CloudShell provides quick connectivity to Amazon RDS for Db2. However, it does not replace standard Db2 clients required for application servers or Amazon EC2 instances that use either full or lightweight Db2 client installations.

If you run into the 30-minute inactivity timeout, you can run the script again to install and register your RDS for Db2 database to connect again.

## Enhancements to the tool

The source code of this tool is available in the [GitHub](https://github.com/aws-samples/sample-rds-db2-tools/tree/main/tools/db2client) repository. [Open an issue](https://github.com/aws-samples/sample-rds-db2-tools/issues) to submit your enhancements request or submit a [pull request](https://github.com/aws-samples/sample-rds-db2-tools/pulls) with your suggested changes.

## Conclusion

In this post, we demonstrated how, with just a few commands, you can run Db2 Command Line Processor against Amazon RDS for Db2 entirely inside CloudShell. No EC2 instance or local installs are required—just a clean, serverless-style workflow. Try out this solution for your own use case, and share your thoughts in the comments. Alternatively, you can replicate the same script on your Amazon EC2 instance to install a Db2 client for connecting to an Amazon RDS instance for Db2.

## About the authors
- [Vikram S Khatri](https://www.linkedin.com/in/viz7/) is a Sr. DBE for Amazon RDS for Db2. Vikram has over 20 years of experience in Db2. He enjoys developing new products from the ground up. In his spare time, he practices meditation and enjoys listening to podcasts.
- [Sumit Kumar](https://www.linkedin.com/in/sumitkumarsangerpal/) is a Senior Solutions Architect at AWS, and enjoys solving complex problems. He has been helping customers across various industries to build and design their workloads on the AWS Cloud. He enjoys cooking, playing chess, and spending time with his family.
- [Rajib Sarkar](https://www.linkedin.com/in/rajibsarkaribm/) is a Senior Database Engineer for Amazon RDS for Db2. Rajib has over 20 years of Db2 experience.
- [Ashish Saraswat](https://www.linkedin.com/in/ashish-saraswat-86029417/) is Sr. Software Development Engineer for Amazon RDS for Db2. Ashish has 10+ years of software development experience.