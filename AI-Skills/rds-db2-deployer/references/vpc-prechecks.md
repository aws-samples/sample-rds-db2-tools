# VPC prechecks

Before any `terraform apply`, the skill validates that the target VPC is ready,
using a **severity model** that distinguishes blocking failures from
best-practice warnings. The checks are ported from `0cr-ins.sh` so an apply does
not fail midway due to a missing endpoint or a misconfigured subnet — while still
letting valid deployments (such as a small `db2-ce` instance in a default public
VPC) proceed after the operator acknowledges the trade-offs.

> Grounds: Requirement 11 (VPC prechecks).
> Implemented in `scripts/vpc_precheck.py`.

## Severity model

| Severity | Meaning | Behavior |
|---|---|---|
| `Precheck_Failure` | A condition that would break the deployment | Reported **by name**; **halts** before apply (R11.9) |
| `Precheck_Warning` | A best-practice advisory | Reported **by name** with guidance; proceeds **after acknowledgement** (R11.10) |

When any failure occurs, the deployment halts. When only warnings occur, the
deployment proceeds once the customer acknowledges them.

## The checks

| Check | Severity | Source / rule |
|---|---|---|
| DB subnets span ≥ 2 distinct AZs | Failure | R11.1 |
| `enableDnsSupport` + `enableDnsHostnames` enabled | offer to enable via `modify-vpc-attribute`; Failure only if still off | R11.2 (`0cr-ins.sh` ~4595–4607) |
| S3 gateway endpoint, **when** S3 integration/audit enabled | Failure | R11.3 |
| S3 gateway endpoint, otherwise | Warning | R11.4 |
| Interface endpoint required by an **enabled** feature | Failure | R11.5 |
| Interface endpoint for a **non-enabled** feature | Warning | R11.5 |
| SG permits inbound TCP `50443` from the intent's sources | Failure | R11.6 |
| Public-only VPC (IGW route, no private subnet) | Warning (best practice) | R11.7 |
| `publicly_accessible=false` but no private subnet to place the instance | Failure | R11.8 |
| Target VPC undescribable (absent or describe denied) | Failure | R11.12 |

### DNS attributes (R11.2)

Both `enableDnsSupport` and `enableDnsHostnames` must be on (RDS for Db2 needs
DNS resolution for the instance endpoint). If either is off, the precheck offers
to enable it via `modify-vpc-attribute` and treats it as a failure **only if it
remains disabled**.

### S3 gateway endpoint (R11.3 / R11.4)

When the intent enables S3 integration for backup/restore or routes Db2 audit
data to S3, a missing S3 gateway VPC endpoint is a **failure**. When S3
integration/audit is **not** enabled, the same missing endpoint is only a
**warning** — RDS still functions, but a gateway endpoint is best practice.

### Interface endpoints (R11.5)

The interface-endpoint set ported from `0cr-ins.sh` covers RDS, Lambda,
CloudWatch monitoring, CloudWatch Logs, EC2, SNS, and Secrets Manager. A missing
endpoint **required by an enabled feature** is a failure; a missing endpoint for
a **non-enabled feature** is a warning.

### SSL-50443 ingress (R11.6)

The target security group must permit inbound TCP ingress on the Db2 SSL service
port `50443` from the source CIDR ranges or security groups named in the intent.
Absence of that ingress rule is a failure. (This is the only port that accepts
connections — see `security-invariants.md`.)

### Public-facing VPC (R11.7 / R11.8)

A VPC whose subnets only route to an Internet Gateway, with no private subnets
(for example a default VPC), raises a **warning** that the environment is
public-facing, states the best practice that an RDS for Db2 database should not
be public-facing unless absolutely required, and allows the deployment to
proceed after acknowledgement. But if the intent resolves
`publicly_accessible=false` while there is **no private subnet** to place the
instance in, that mismatch is a **failure** reporting that a private subnet is
required for a non-public instance.

## Offer to create missing resources (R11.11)

When a failure is caused by a missing **creatable** resource — an S3 gateway
endpoint required by an enabled feature, an interface endpoint required by an
enabled feature, or DB subnets across ≥ 2 AZs — the precheck **offers to create**
the missing resource through the `1-networking` module before any apply proceeds.

## Sources

- Bash provisioner `0cr-ins.sh` — VPC/DNS attribute checks (~lines 4595–4607),
  S3 gateway and interface-endpoint checks (the ported source).
- AWS docs, [Amazon RDS for Db2 prerequisites / working with a DB instance in a VPC](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_VPC.html).
- AWS docs, [Gateway endpoints for Amazon S3](https://docs.aws.amazon.com/vpc/latest/privatelink/vpc-endpoints-s3.html)
  and [interface VPC endpoints](https://docs.aws.amazon.com/vpc/latest/privatelink/create-interface-endpoint.html).
- AWS blog, [Deploying Amazon RDS for Db2 using Terraform](https://aws.amazon.com/blogs/database/deploying-amazon-rds-for-db2-using-terraform/).
- `scripts/vpc_precheck.py`.
