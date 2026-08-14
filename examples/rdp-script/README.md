# RDP to a Windows EC2 Instance in a Private Subnet from macOS, over AWS Systems Manager

Connect with Microsoft Remote Desktop from a MacBook to a Windows EC2 instance that has **no public IP, no inbound security group rule, no bastion host and no VPN**. RDP (TCP 3389) is tunnelled over an AWS Systems Manager Session Manager port forwarding session, so the only network path is an outbound HTTPS connection from the instance to the SSM service.

This pattern is commonly used to reach a Windows jump host that runs Db2 client tooling (IBM Data Studio, Db2 CLP, Q Replication or IIDR consoles) against an Amazon RDS for Db2 instance in a private subnet.

## Contents

- [`rdp-via-ssm.sh`](rdp-via-ssm.sh) - lists SSM-managed Windows instances, opens the tunnel, generates a locked-down `.rdp` profile, launches the client, and tears everything down on exit.

## How it works

```
MacBook                                    AWS
┌──────────────────────────┐               ┌──────────────────────────────────┐
│ Windows App (RDP client) │               │  Session Manager (regional API)  │
│           │              │               └──────────────┬───────────────────┘
│           ▼              │   HTTPS 443                  │ HTTPS 443
│  127.0.0.1:13389         │◄─────────────────────────────┤ (outbound from
│  session-manager-plugin  │   TLS, IAM-authenticated     │  the instance)
└──────────────────────────┘                              │
                                           ┌──────────────▼───────────────────┐
                                           │ Private subnet                   │
                                           │  Windows EC2 + SSM Agent         │
                                           │  RDP 3389 on loopback only       │
                                           │            │                     │
                                           │            ▼                     │
                                           │  RDS for Db2 (50000/50443)       │
                                           └──────────────────────────────────┘
```

The RDP bytes ride inside the existing TLS session that the SSM Agent already holds open. Nothing is exposed to the internet, and the security group needs no inbound rule at all.

## Prerequisites

**On the MacBook**

| Requirement | Install |
|---|---|
| AWS CLI v2 | `brew install awscli` |
| Session Manager plugin | [Install the Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) |
| Windows App (or Microsoft Remote Desktop) | Mac App Store |

**On the instance**

- SSM Agent installed and running. It is preinstalled on current Windows Server AMIs.
- An instance profile that includes the `AmazonSSMManagedInstanceCore` managed policy.
- Outbound HTTPS (443) reachability to the SSM endpoints. For a fully private subnet with no NAT gateway, create interface VPC endpoints for `ssm`, `ssmmessages` and `ec2messages`.
- No inbound security group rules are needed. If port 3389 is currently open to `0.0.0.0/0`, remove that rule once this path works.

## Usage

```bash
chmod +x rdp-via-ssm.sh

# Interactive picker in the default region and profile
./rdp-via-ssm.sh

# Explicit region and profile
./rdp-via-ssm.sh -r us-west-2 -p my-sso-profile

# Skip the picker and choose a different local port
./rdp-via-ssm.sh -i i-0123456789abcdef0 -l 13390

# Enable clipboard redirection (off by default)
./rdp-via-ssm.sh -c
```

| Flag | Description | Default |
|---|---|---|
| `-r` | AWS region | `us-east-1` |
| `-p` | AWS CLI profile | `default` |
| `-l` | Local loopback port for the tunnel | `13389` |
| `-i` | Target instance ID; skips the interactive picker | - |
| `-c` | Enable clipboard redirection | disabled |
| `-h` | Show help | - |

The picker marks each instance as `SSM online` or `not SSM-managed`, so you can tell up front whether a tunnel is even possible.

Keep the terminal open while you work. Press `Ctrl+C` when you are finished; the script terminates the tunnel process group and deletes the temporary `.rdp` file.

## Security design

This script is written for a public sample repository, so it avoids the shortcuts that usually creep into internal RDP helpers. The notable decisions:

**No secrets anywhere in the repo or on disk.** The generated `.rdp` file contains `prompt for credentials:i:1` and no `username:s:` or `password 51:b:` field, so credentials are typed into the client on every connect and never persisted. There are no account IDs, instance IDs, region-specific endpoints, profile names or hostnames baked into the script.

**Server authentication is not silently bypassed.** Because the client connects to `localhost`, the Windows host certificate name will not match, and many examples set `authentication level:i:0` ("connect without warning"), which accepts any server. This script uses `authentication level:i:2`, which warns and lets you decide. Verify the certificate thumbprint on first connect and expect the same value afterwards.

**Device redirection is off by default.** Clipboard, drive, printer, smart card, COM port and audio capture redirection are all explicitly disabled, so no data crosses the RDP channel unless you ask for it. Clipboard is available through `-c` when you genuinely need it. Drive redirection is intentionally not exposed as a flag.

**The `.rdp` file is created privately and deleted on exit.** It is written into a `mktemp -d` directory with `umask 077`, the file is `chmod 600` before any content is written, and the whole directory is removed by an `EXIT`/`INT`/`TERM` trap. The original version wrote a predictable path (`$TMPDIR/ssm-rdp-<instance-id>.rdp`) with default permissions, which is a pre-creation and information disclosure risk on a shared Mac.

**All input is allow-listed before it reaches the AWS CLI.** Region, profile, instance ID and port are validated with strict regular expressions. The port matters most: it is interpolated into the `--parameters` JSON document, and without validation a value such as `1"],"portNumber":["22` would rewrite the document and forward a different remote port. Validating the profile name also prevents a value beginning with `-` from being parsed as a CLI option.

**The tunnel cannot be left running.** The session is started with job control enabled so it gets its own process group, and cleanup signals the group. Killing only the `aws` wrapper can leave `session-manager-plugin` alive and the local port still listening.

**Loopback binding is verified.** After the tunnel comes up, the script checks the listener with `lsof` and warns if it is bound to `0.0.0.0` or `*` instead of `127.0.0.1`. A tunnel on all interfaces lets anyone who can reach your Mac on that port hop into the private subnet.

**Failure modes are explicit rather than silent.** The script preflights the AWS CLI and the Session Manager plugin, detects an early session exit while waiting for the tunnel, and uses `set -euo pipefail` so a failed AWS call does not fall through to a later step.

**No identity output.** The script never calls `aws sts get-caller-identity`, so your AWS account ID is not printed. Instance IDs and Name tags do appear in the picker output. Redact them before pasting terminal output into a ticket, chat or public issue.

### Least-privilege IAM policy for the operator

Grant only what the tunnel needs, and scope it with tags rather than allowing `ssm:StartSession` on every instance. Replace `<region>`, `<account-id>` and the tag values.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "DiscoverTargets",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances",
        "ssm:DescribeInstanceInformation"
      ],
      "Resource": "*"
    },
    {
      "Sid": "StartPortForwardingOnTaggedInstances",
      "Effect": "Allow",
      "Action": "ssm:StartSession",
      "Resource": "arn:aws:ec2:<region>:<account-id>:instance/*",
      "Condition": {
        "StringEquals": {
          "ssm:resourceTag/RdpAccess": "allowed"
        },
        "ArnEquals": {
          "ssm:DocumentArn": "arn:aws:ssm:<region>::document/AWS-StartPortForwardingSession"
        }
      }
    },
    {
      "Sid": "ManageOwnSessions",
      "Effect": "Allow",
      "Action": [
        "ssm:TerminateSession",
        "ssm:ResumeSession"
      ],
      "Resource": "arn:aws:ssm:<region>:<account-id>:session/${aws:username}-*"
    }
  ]
}
```

Restricting `ssm:DocumentArn` to `AWS-StartPortForwardingSession` is the important part: it prevents the same permission from being used to open an interactive shell on the instance. To enforce that document restriction consistently, also enable [`ssm:SessionDocumentAccessCheck`](https://docs.aws.amazon.com/systems-manager/latest/userguide/getting-started-configure-preferences-cli.html) in your Session Manager preferences.

### Recommended account-level controls

- **Audit the sessions.** Session Manager logs `StartSession` to AWS CloudTrail. Turn on [session logging](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-logging-auditing.html) to Amazon CloudWatch Logs or Amazon S3 with a customer managed KMS key so you have a durable record of who tunnelled where and when.
- **Encrypt the session.** Set a KMS key in Session Manager preferences to add KMS encryption on top of the transport TLS.
- **Keep RDP off the network.** With this pattern in place, the Windows security group needs no inbound rule. Remove any leftover `3389` ingress, especially from `0.0.0.0/0`.
- **Do not extract the local Administrator password into scripts.** `aws ec2 get-password-data` prints a plaintext credential to your terminal and shell history. Prefer domain credentials, AWS Managed Microsoft AD, or a password stored in AWS Secrets Manager.
- **Consider Fleet Manager Remote Desktop** for occasional browser-based access. It needs no local client and no tunnel, though it does not give you the local RDP client experience.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `TargetNotConnected` | SSM Agent is not running, or the instance profile is missing `AmazonSSMManagedInstanceCore`. |
| Instance shows `not SSM-managed` | Same as above, or no route to the SSM endpoints. Check the interface VPC endpoints for `ssm`, `ssmmessages` and `ec2messages`. |
| `session-manager-plugin not found` | Install the plugin, then reopen the terminal. |
| Tunnel comes up, client times out | Remote Desktop is disabled on the instance, or the Windows firewall is blocking 3389 locally. |
| Certificate warning on connect | Expected. The host certificate name does not match `localhost`. Confirm the thumbprint, then continue. |
| `AccessDeniedException` on `StartSession` | The instance is missing the tag required by your IAM policy condition. |

## References

- [Session Manager port forwarding](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-sessions-port-forwarding.html)
- [Install the Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
- [Session Manager logging and auditing](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-logging-auditing.html)
- [Setting up Systems Manager for EC2 instances](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-setting-up-ec2.html)
- [Supported RDP file settings](https://learn.microsoft.com/en-us/windows-server/remote/remote-desktop-services/clients/rdp-files)
- [Best practices for creating a VPC for Amazon RDS for Db2](https://aws.amazon.com/blogs/database/best-practices-for-creating-a-vpc-for-amazon-rds-for-db2/)

## License

MIT-0. See the [LICENSE](../../LICENSE) file at the repository root.
