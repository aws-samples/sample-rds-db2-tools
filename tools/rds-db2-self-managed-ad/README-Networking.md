# Networking considerations for RDS for Db2 self-managed AD

This document covers the network connectivity requirements between Amazon
RDS for Db2 and a customer-managed Active Directory domain, across three
common deployment topologies.

---

## 1. AD and RDS for Db2 in the same VPC (same AWS account)

This is the simplest topology. Two security groups are involved:

- **AD security group** — attached to the EC2 instances running your domain
  controllers.
- **RDS security group** — attached to your RDS for Db2 DB instance.

### Rules to add

**On the AD security group (inbound) — allow from the RDS security group:**

| Protocol | Port(s) | Purpose |
|---|---|---|
| TCP + UDP | 53 | DNS |
| TCP + UDP | 88 | Kerberos authentication |
| TCP + UDP | 389 | LDAP |
| TCP | 636 | LDAPS (if using secure LDAP) |
| TCP + UDP | 464 | Kerberos password change |
| TCP | 3268 | Global Catalog LDAP |
| TCP | 3269 | Global Catalog LDAPS (if using secure LDAP) |
| TCP + UDP | 49152–65535 | RPC dynamic ports (required for AD replication and domain join) |

**On the RDS security group (outbound) — allow to the AD security group:**

Add outbound rules for the same ports listed above, with the AD security
group as the destination.

> **Tip:** Reference security groups by ID rather than CIDR ranges wherever
> possible. This avoids having to update rules when IP addresses change and
> is the AWS recommended practice for same-VPC rules.

### DNS

RDS for Db2 must be able to resolve your AD domain name. Set the **DHCP
option set** on the VPC to point to your domain controller IP addresses as
DNS servers, or ensure the VPC's DNS resolver can forward queries for your
AD domain to the domain controllers.

---

## 2. AD hosted in Microsoft Azure (cross-cloud)

When your domain controllers run in Azure, connectivity is established over
a site-to-site VPN or AWS Direct Connect with Azure ExpressRoute.

### Network path

```
RDS for Db2 (AWS VPC)
    ↓  VPN Gateway / Direct Connect
Azure Virtual Network Gateway / ExpressRoute
    ↓
Azure VNet → Domain Controllers
```

### Steps

1. **Establish connectivity** between the AWS VPC and the Azure VNet using
   one of:
   - **AWS Site-to-Site VPN** ↔ **Azure VPN Gateway** (IPsec/IKE)
   - **AWS Direct Connect** ↔ **Azure ExpressRoute** (for production,
     lower latency, more predictable throughput)

2. **Routing:** Ensure route tables in both the AWS VPC and the Azure VNet
   include routes for each other's CIDR ranges. The RDS subnet must have a
   route to the Azure VNet CIDR via the VPN/DX attachment.

3. **Security groups (AWS side):** Add inbound rules on the AD security
   group (or a dedicated security group on the VPN endpoint) to allow the
   AD ports listed in Section 1 from the RDS for Db2 subnet CIDR.

4. **Azure NSG (Azure side):** Add inbound rules on the Network Security
   Group attached to the Azure subnet hosting the domain controllers to
   allow the same AD ports from the AWS VPC CIDR.

5. **DNS forwarding:**
   - Configure a **conditional DNS forwarder** on your AWS VPC (using Route
     53 Resolver outbound endpoints) to forward queries for your AD domain
     (e.g. `company.com`) to the Azure domain controller IP addresses.
   - Alternatively, configure the VPC DHCP option set to point directly to
     the Azure DC IPs if the VPN/DX path is always available.

6. **Latency:** Kerberos authentication is latency-sensitive. Keep
   round-trip time between the RDS subnet and the Azure DCs below ~100ms.
   Use Direct Connect + ExpressRoute for production workloads.

### Key difference from same-VPC

There is no AWS security group on the Azure side — Azure NSGs serve that
role. You must mirror the port rules in both AWS security groups and Azure
NSGs.

---

## 3. AD in a different VPC or different AWS account

> **Further reading:** For a detailed walkthrough of joining RDS for Db2
> instances in multiple AWS accounts to a single shared AD domain, see the
> AWS Database Blog:
> [Join your Amazon RDS for Db2 instances across accounts to a single shared domain](https://aws.amazon.com/blogs/database/join-your-amazon-rds-for-db2-instances-across-accounts-to-a-single-shared-domain/).

### Option A — VPC Peering

1. Create a **VPC peering connection** between the RDS VPC and the AD VPC.
2. Accept the peering request from the AD VPC owner.
3. Add routes in both VPCs' route tables pointing to each other's CIDR via
   the peering connection.
4. Update security groups as described in Section 1, using CIDR ranges
   instead of security group IDs (cross-account security group references
   require Resource Access Manager sharing).

> **Limitation:** VPC peering does not support transitive routing. If your
> AD VPC peers with other VPCs, those VPCs cannot reach the RDS VPC through
> the AD VPC.

### Option B — AWS Transit Gateway (recommended for multi-account)

Transit Gateway is the preferred solution when multiple accounts or VPCs
need to reach the same AD infrastructure.

1. Create a **Transit Gateway** (or use an existing one) in the network
   account.
2. Attach both the RDS VPC and the AD VPC to the Transit Gateway.
3. For cross-account attachments, share the Transit Gateway using **AWS
   Resource Access Manager (RAM)**.
4. Update route tables in both VPCs to route AD-bound traffic via the
   Transit Gateway attachment.
5. Update security groups to allow the AD ports from the RDS VPC CIDR.

### Option C — AWS Managed Microsoft AD with trust (alternative)

If you don't want to expose your on-premises or cross-account AD directly,
create an **AWS Managed Microsoft AD** in the same account/VPC as RDS for
Db2 and establish a **forest trust** with your existing AD. RDS for Db2
joins the AWS Managed AD; Kerberos authentication flows through the trust
to your existing domain.

This avoids direct network connectivity between RDS and your AD domain
controllers at the cost of an additional AWS Managed AD directory.

### Security group rules for cross-account

When the AD security group is in a different account, you cannot reference
it by ID in the RDS security group rules. Use CIDR ranges for the AD VPC
subnets instead:

**RDS security group (outbound):**
- Allow AD ports (see Section 1) to the AD VPC subnet CIDR range.

**AD security group (inbound):**
- Allow AD ports from the RDS VPC subnet CIDR range.

### DNS for cross-VPC

Configure **Route 53 Resolver** in the RDS VPC:

1. Create a **Route 53 Resolver outbound endpoint** in the RDS VPC.
2. Create a **forwarding rule** for your AD domain name pointing to the
   domain controller IP addresses in the AD VPC.
3. Associate the rule with the RDS VPC.

This ensures RDS for Db2 can resolve AD hostnames without changing the
VPC's default DNS resolver.

---

## Port reference summary

| Protocol | Port | Service | Required |
|---|---|---|---|
| TCP + UDP | 53 | DNS | Yes |
| TCP + UDP | 88 | Kerberos | Yes |
| TCP + UDP | 389 | LDAP | Yes |
| TCP | 636 | LDAPS | Only if using secure LDAP |
| TCP + UDP | 464 | Kerberos password change | Yes |
| TCP | 3268 | Global Catalog | Yes |
| TCP | 3269 | Global Catalog LDAPS | Only if using secure LDAP |
| TCP + UDP | 49152–65535 | RPC dynamic ports | Yes |

---

## Common pitfalls

- **Missing RPC dynamic ports.** Domain join and Kerberos ticket renewal
  use RPC over dynamic ports (49152–65535). Blocking these is the most
  common cause of intermittent AD connectivity failures.
- **DNS not resolving the AD domain.** RDS for Db2 uses DNS to locate
  domain controllers. If the VPC DNS cannot resolve your AD domain, the
  join will fail even if all other ports are open.
- **Asymmetric routes.** In multi-VPC topologies, ensure return traffic
  follows the same path as outbound traffic. Asymmetric routing causes
  stateful security group rules to drop return packets.
- **NTP/time skew.** Kerberos requires clocks to be within 5 minutes of
  each other. Ensure RDS and your domain controllers use the same NTP
  source.
