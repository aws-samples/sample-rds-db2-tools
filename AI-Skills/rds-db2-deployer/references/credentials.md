# AWS credentials and identity resolution

The skill works with however an organization supplies AWS credentials — a named
AWS profile, environment variables, or an ambient instance/container role — so an
operator is never forced into one credential style and is **never asked to paste
secrets**. The credential source is independent of the deployment tier (R16.5):
any source composes with any tier.

> Grounds: Requirement 16 (AWS credential and identity resolution).
> Implemented in `scripts/credentials.py`.

## The three (and only three) credential sources (R16.1)

| `CredentialSourceKind` | When used | Behavior |
|---|---|---|
| `PROFILE` (`named_profile`) | a named AWS CLI/SDK profile is specified (R16.2) | passed through to **every** API call; no env-var credentials required |
| `ENVIRONMENT` (`environment_variables`) | no profile named; env vars present | part of the ambient default chain (R16.3) |
| `DEFAULT_CHAIN` (`default_credential_chain`) | no profile named; ambient instance/container role | part of the ambient default chain (R16.3) |

When a profile is named, it drives every subsequent client and no environment
variables are required (R16.2). When no profile is named, the skill relies on the
default credential chain — environment variables first, then the instance/
container role — and does not require a named profile to exist (R16.3).

## Never paste secrets (R16.4)

This is the security spine of the credential model. The skill:

- **only ever accepts a profile name** — it has no parameter, code path, or
  storage for an access key, secret key, or session token;
- **never prompts** the customer to paste an access key, secret key, or session
  token; and
- **never writes** any such value into the `Deployment_Intent`, rendered
  Terraform, logs, artifacts, or PR text.

The only secret-bearing object is the live `boto3.Session`, held in memory and
marked non-printable so it never leaks via `repr` into logs.

## Resolve and report identity before any mutation (R16.6)

When a deployment begins, the agent calls `sts get-caller-identity` and reports
the active **account ID** and **region** (plus the caller ARN) for confirmation,
**before any mutating API call**. The `identity_report()` is a single-line,
secret-free summary; `masked_dict()` provides a secret-free descriptor for
artifacts and logs.

## Halt, never silently fall back (R16.7 / R16.8)

| Condition | Error | Behavior |
|---|---|---|
| No credentials resolvable from any source | `CredentialsUnresolvedError` | reports that no credentials could be resolved, **names the sources attempted**, and halts before any AWS API call (R16.7) |
| Named profile specified but missing/unloadable | `ProfileNotResolvedError` | reports the named profile as unresolved and halts — **never** silently drops to the default chain (R16.8) |

## Testability

The only AWS-touching step is creating a session and calling STS. Both are
injected via a `SessionFactory` — `(profile, region) -> session-like`. Production
wires `boto3_session_factory`; unit tests inject an in-memory fake session whose
STS client returns a canned `get_caller_identity` (or raises the relevant error),
so credential resolution is fully testable without real AWS credentials.

## Sources

- AWS docs, [Configuration and credential file settings](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html)
  and [credential provider chain](https://docs.aws.amazon.com/sdkref/latest/guide/standardized-credentials.html).
- AWS CLI reference, [`sts get-caller-identity`](https://docs.aws.amazon.com/cli/latest/reference/sts/get-caller-identity.html).
- `scripts/credentials.py`.
