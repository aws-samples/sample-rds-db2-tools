"""The Terraform_Composer for the rds-db2-provision-skill (R10).

This module renders Terraform that *reuses* the existing modular Terraform
(``RDS-Db2-Terraform/0-backend-setup`` .. ``6-license-manager``) from a
schema-validated, resolved ``Deployment_Intent``. It never authors a new
imperative deployer (R10.1).

Task 7.1 scope (this file's first slice):

* Define the **intent -> module-variable mapping** table from the design's
  "Intent -> module-variable mapping" section. The table is the single source
  of truth: every intent field maps to one of
  - one or more concrete ``(module, module_variable)`` targets whose names are
    the *real* variables declared in the modules' ``variables.tf`` (R10.3), or
  - an explicit non-variable handling marker (a module literal, the provider
    ``default_tags``, or resolver-internal metadata) so the field is known to be
    *intentionally* not rendered as a variable rather than silently dropped.
* Render the **root module** under ``templates/terraform/`` as a ``main.tf``
  that references modules 0..6 by relative ``source`` path (R10.1).
* Emit a **``terraform.tfvars``** per enabled module, populated from the intent
  via the mapping (R10.2).
* **Halt + report** on any intent field that has no mapping entry at all, naming
  the field, and never fabricate a variable name (R10.4).

Design for clean extension (so tasks 7.2-7.5 plug in, not rewrite):

* The mapping table (:data:`INTENT_FIELD_MAPPING`) is data, not code: 7.3's
  optional-capability fields already have targets here, so enabling them is a
  matter of the intent carrying the field, not new mapping code.
* :func:`select_enabled_modules` is the single seam task 7.2 refines for the
  reuse/create selection; today it returns a deterministic core set plus any
  module the intent populates.
* Every emitted variable name is checked against the module's *actual* declared
  variables (parsed from ``variables.tf``) before it is written, so a drifted or
  mistyped mapping target fails loudly here rather than producing a fabricated
  name downstream (defense in depth for R10.3/10.4).
* Sensitive values (IBM IDs, master password) are rendered into the module
  tfvars because Terraform needs them, but they are tracked in
  :data:`SENSITIVE_INTENT_FIELDS` so tasks 7.5/11/12 can assert/mask them in the
  PR/plan/artifact surfaces.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Union

try:  # Prefer package-qualified import so helpers share identity with callers.
    from scripts.engine_versions import major_version_of
    from scripts.resolve_intent import abbreviate_workload_size
except ImportError:  # Fall back when scripts/ is directly on sys.path.
    from engine_versions import major_version_of
    from resolve_intent import abbreviate_workload_size


# ---------------------------------------------------------------------------
# Module locations
# ---------------------------------------------------------------------------

# scripts/render_terraform.py -> scripts/ -> package root (rds-db2-provision-skill)
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]

#: The existing modular Terraform reused by the composer. Resolved absolutely so
#: variable parsing works from any CWD.
#:
#: This local path is used for two distinct purposes that must not be conflated:
#:   1. ALWAYS: parsing each module's ``variables.tf`` to ground the mapping
#:      table (R10.3) — the composer reads the real variable names from disk
#:      regardless of how the emitted ``source`` is wired.
#:   2. AIRGAP FALLBACK ONLY: as the emitted module ``source`` when
#:      ``source_mode="local"`` is selected (no-egress / vendored environments).
#: The default emitted ``source`` is the pinned git ref below (``source_mode``
#: defaults to ``"git"``), so a rendered root is reproducible from GitHub.
#:
#: The directory is auto-discovered so the skill works both in local development
#: (modules a sibling of the skill package) AND when published in
#: aws-samples/sample-rds-db2-tools (skill at ``AI-Skills/<skill>/``, modules at
#: ``tools/rds-db2-terraform/``). ``RDS_DB2_MODULES_ROOT`` overrides discovery.
_MODULES_ROOT_ENV = "RDS_DB2_MODULES_ROOT"


def _discover_modules_root() -> Path:
    """Locate the reused Terraform modules across the supported layouts.

    Tries, in order:
      1. the ``RDS_DB2_MODULES_ROOT`` environment override (explicit operator
         control / vendored airgap trees);
      2. ``<package>/../RDS-Db2-Terraform`` — the local-development sibling
         layout used by this repo;
      3. ``<package>/../../tools/rds-db2-terraform`` — the published GitHub
         layout (``AI-Skills/<skill>/`` alongside ``tools/rds-db2-terraform/``).
    The first candidate that actually contains the modules (``5-rds`` with a
    ``variables.tf``) wins. When none is found the sibling layout is returned so
    error messages point at the conventional location.
    """
    env = os.environ.get(_MODULES_ROOT_ENV)
    candidates = []
    if env:
        candidates.append(Path(env))
    candidates.append(_PACKAGE_ROOT.parent / "RDS-Db2-Terraform")
    candidates.append(_PACKAGE_ROOT.parent.parent / "tools" / "rds-db2-terraform")
    for candidate in candidates:
        if (candidate / "5-rds" / "variables.tf").is_file():
            return candidate.resolve()
    return (_PACKAGE_ROOT.parent / "RDS-Db2-Terraform").resolve()


DEFAULT_MODULES_ROOT = _discover_modules_root()


# ---------------------------------------------------------------------------
# Module source wiring (#1: git-pinned source of truth + airgap fallback)
# ---------------------------------------------------------------------------

#: The GitHub repository that is the source of truth for the reused Terraform
#: modules. The rendered root references the modules from this repo by a pinned
#: git ref so a rendered configuration is reproducible and stays in sync with
#: upstream fixes. Mirror of the local dev copy at
#: ``/Users/viz/sample-rds-db2-tools``.
DEFAULT_GIT_REPO = "https://github.com/aws-samples/sample-rds-db2-tools.git"

#: The subdirectory within :data:`DEFAULT_GIT_REPO` that holds the module set
#: (``0-backend-setup`` .. ``6-license-manager``). Combined with the repo and a
#: module name it yields a Terraform git source of the form
#: ``git::<repo>//<subdir>/<module>?ref=<ref>``.
DEFAULT_GIT_SUBDIR = "tools/rds-db2-terraform"

#: The git ref the emitted module sources are pinned to. This MUST be a TAG, not
#: a branch, so rendering is reproducible (a branch can move under a rendered
#: config). Overridable via the ``RDS_DB2_MODULE_REF`` environment variable or
#: the ``module_ref`` render argument.
#:
#: TODO(publish): set this to the real release tag cut in
#: aws-samples/sample-rds-db2-tools before the skill is published. ``v0.0.0`` is
#: a deliberately invalid placeholder so an unset ref fails loudly at
#: ``terraform init`` rather than silently pulling a wrong revision.
DEFAULT_MODULE_REF = os.environ.get("RDS_DB2_MODULE_REF", "rds-db2-deployer-v0.3.1")

#: The two supported module-source modes. ``"git"`` (default) emits the pinned
#: git ref above — the production source of truth. ``"local"`` emits a relative
#: path to a vendored/sibling module tree — the AIRGAP / no-egress fallback.
SOURCE_MODE_GIT = "git"
SOURCE_MODE_LOCAL = "local"
DEFAULT_SOURCE_MODE = os.environ.get("RDS_DB2_SOURCE_MODE", SOURCE_MODE_GIT)


# ---------------------------------------------------------------------------
# Per-deployment remote state (#2: one state key per deployment, idempotency)
# ---------------------------------------------------------------------------

#: The DynamoDB lock table created by ``0-backend-setup`` (its ``lock_table_name``
#: default). Used as the ``dynamodb_table`` in the rendered ``backend "s3"``
#: block so concurrent applies of the SAME deployment are serialized.
DEFAULT_LOCK_TABLE = "rds-db2-terraform-lock"

#: Prefix for the per-deployment S3 state key. The full key is
#: ``rds-db2/<deployment-name>/terraform.tfstate`` so every distinct deployment
#: (distinct ``db_instance_identifier``) gets an isolated state object and N
#: instances never collide on one state file (idempotency, #2).
STATE_KEY_PREFIX = "rds-db2"

#: A deliberately obvious placeholder for the S3 state bucket name. The bucket is
#: account/region-specific (created by ``0-backend-setup``) and is NOT part of a
#: deployment intent, so it cannot be derived; the operator supplies the real
#: name via :class:`BackendConfig`, the ``RDS_DB2_STATE_BUCKET`` environment
#: variable, or ``terraform init -backend-config="bucket=..."``. The placeholder
#: is rendered with an inline TODO so an unset bucket is impossible to miss.
DEFAULT_STATE_BUCKET = os.environ.get(
    "RDS_DB2_STATE_BUCKET", "REPLACE-WITH-rds-db2-terraform-state-bucket"
)

#: Where the rendered root module + per-module tfvars are written by default
#: (R10: "render the root module under templates/terraform/").
DEFAULT_OUTPUT_DIR = _PACKAGE_ROOT / "templates" / "terraform"

#: The ordered module set, by directory name, that the composition wires
#: together (R10.1). 0-backend-setup is the remote-state bootstrap and is not
#: driven by the per-deployment intent, so it is excluded from the intent-driven
#: enabled set (see :func:`select_enabled_modules`).
ALL_MODULES: tuple[str, ...] = (
    "0-backend-setup",
    "1-networking",
    "2-iam",
    "3-kms",
    "4-parameter-group",
    "5-rds",
    "6-license-manager",
)

#: The modules driven by the per-deployment intent. ``0-backend-setup`` is the
#: one-time remote-state bootstrap: it is referenced by the root module (R10.1)
#: but its inputs (state bucket / lock table names) are not part of a deployment
#: intent, so it is never populated from the intent nor emitted as an
#: intent-driven tfvars.
INTENT_DRIVEN_MODULES: tuple[str, ...] = (
    "1-networking",
    "2-iam",
    "3-kms",
    "4-parameter-group",
    "5-rds",
    "6-license-manager",
)

#: The core modules every produced deployment always renders tfvars for: the
#: parameter group (IBM IDs + family) and the RDS instance itself. When an
#: existing parameter group is supplied for reuse (R10.5), 4-parameter-group is
#: dropped from the enabled set by :func:`select_enabled_modules`; 5-rds is
#: always rendered.
CORE_MODULES: tuple[str, ...] = ("4-parameter-group", "5-rds")


# ---------------------------------------------------------------------------
# Security invariants (R6) — always rendered regardless of prompt wording
# ---------------------------------------------------------------------------

#: The Db2 SSL service port. This is the ONLY port that accepts client
#: connections and the only port opened in the security-group ingress rule
#: (R6.2/R6.5). The non-SSL TCP listener ``port`` (5-rds ``db2_port``) is dormant
#: under ``DB2COMM=SSL`` and is never opened to ingress.
SSL_SERVICE_PORT = 50443

#: The Db2 communication-protocol parameter and its fixed SSL-only value (R6.2).
#: ``DB2COMM=SSL`` (not ``tcpip,ssl``) keeps the TCP listener dormant.
DB2COMM_PARAMETER = "DB2COMM"
DB2COMM_SSL_VALUE = "SSL"

#: The Db2 SSL service-name parameter, fixed to the SSL service port (R6.2).
SSL_SVCENAME_PARAMETER = "ssl_svcename"

#: The Db2 security parameters the composer always renders into the parameter
#: group (R6.2). These belong to the ``4-parameter-group`` module's parameter
#: group; that module does not (yet) expose arbitrary parameters as variables,
#: so the composer renders them in a dedicated ``security.tf`` supplement rather
#: than fabricating a non-existent module variable (R10.3/R10.4).
DB2_SECURITY_PARAMETERS: dict[str, str] = {
    DB2COMM_PARAMETER: DB2COMM_SSL_VALUE,
    SSL_SVCENAME_PARAMETER: str(SSL_SERVICE_PORT),
}

#: The acknowledgement field that must be exactly ``True`` to permit public
#: exposure (R6.3/R6.4). Absent or non-``True`` forces ``publicly_accessible``
#: to ``false`` at render time. Mirrors the validator's field name so the two
#: layers agree.
PUBLIC_ACCESS_ACK_FIELD = "public_access_acknowledged"

#: Intent fields that may carry security-group ingress source CIDRs (R6.5). The
#: skill's intent uses ``ingress_cidrs``; ``ingress_cidr_blocks`` is accepted as
#: a synonym so a rename does not silently change the rendered ingress.
INGRESS_CIDR_FIELDS: tuple[str, ...] = ("ingress_cidrs", "ingress_cidr_blocks")

#: Intent fields that may carry source security groups for ingress (R6.5).
INGRESS_SOURCE_SG_FIELDS: tuple[str, ...] = (
    "ingress_source_security_group_ids",
    "source_security_group_ids",
)


# ---------------------------------------------------------------------------
# Mandatory tagging (R14) — emitted via the provider default_tags
# ---------------------------------------------------------------------------

#: The fixed ``created_by`` tag value (R14.2): a non-empty string equal to the
#: ``created_by`` value used by the existing ``rds-db2`` skill so resources
#: created by either skill are attributable to the same provenance.
CREATED_BY_TAG_VALUE = "rds-db2-skill"

#: The ``generation_model`` tag value (R14.1/R14.2): a non-empty identifier of
#: the generation model that produced the configuration. Overridable via the
#: ``GENERATION_MODEL`` environment variable so the actual runtime model id is
#: recorded; falls back to a non-empty default so the tag is never empty.
DEFAULT_GENERATION_MODEL = "kiro-spec-composer"

#: The two skill-set provenance tag keys (R14.1).
PROVENANCE_TAG_KEYS: tuple[str, ...] = ("created_by", "generation_model")

#: The three customer-supplied mandatory tag keys (R14.3). They live in the
#: intent's ``tags`` object (Environment is also mirrored from deployment_tier).
CUSTOMER_MANDATORY_TAG_KEYS: tuple[str, ...] = ("Project", "Environment", "Owner")

#: All five mandatory tag keys whose values may never be overridden by a
#: customer-supplied extra tag (R14.4/R14.5).
MANDATORY_TAG_KEYS: tuple[str, ...] = PROVENANCE_TAG_KEYS + CUSTOMER_MANDATORY_TAG_KEYS

#: ``ManagedBy`` is emitted by the modules' default_tags as a literal; it counts
#: toward the per-resource tag total alongside the five mandatory keys (R14.4).
MANAGED_BY_TAG_KEY = "ManagedBy"

#: Maximum number of tags per resource (R14.4). AWS itself caps tags at 50.
MAX_TAGS_PER_RESOURCE = 50

#: The module variables that carry the provenance + customer tag set into each
#: module's default_tags block (extended on the modules in this task). Project
#: and Owner flow through the pre-existing ``tag`` / ``owner`` variables;
#: Environment flows from ``deployment_tier`` -> ``environment``.
CREATED_BY_VARIABLE = "created_by"
GENERATION_MODEL_VARIABLE = "generation_model"
EXTRA_TAGS_VARIABLE = "extra_tags"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class RenderingError(Exception):
    """Base class for any failure that must halt rendering before artifacts are
    produced (no partial output, no fabricated names)."""


class UnmappedIntentFieldError(RenderingError):
    """An intent field has no entry in :data:`INTENT_FIELD_MAPPING` (R10.4).

    The composer halts and names the offending field rather than guessing a
    module-variable name. ``target_module`` is reported as unknown because, by
    definition, an unmapped field has no associated module.
    """

    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(
            f"Deployment_Intent field '{field}' has no intent->module-variable "
            "mapping entry; the Terraform_Composer halts rather than fabricate a "
            "variable name (R10.4). Add an explicit mapping target (or a "
            "non-variable handling marker) for this field before rendering."
        )


class MandatoryTagError(RenderingError):
    """A mandatory tag (R14.5/R14.6) is missing, empty, or the per-resource tag
    limit (R14.4) is exceeded.

    The composer halts before producing any tfvars: a deployment that cannot
    carry the five mandatory tags (``created_by``, ``generation_model``,
    ``Project``, ``Environment``, ``Owner``) — each with a non-empty value — or
    that would exceed 50 tags per resource is not rendered (R14.4/R14.6).
    """

    def __init__(self, message: str, *, fields: Optional[list[str]] = None) -> None:
        self.fields = fields or []
        super().__init__(message)


class FabricatedVariableError(RenderingError):
    """A mapping target names a module variable that does not exist in that
    module's ``variables.tf`` (R10.3/10.4 defense-in-depth).

    This is a programming error in the mapping table, surfaced loudly so a
    drifted or mistyped variable name can never reach a rendered tfvars file.
    """

    def __init__(self, module: str, variable: str, intent_field: str) -> None:
        self.module = module
        self.variable = variable
        self.intent_field = intent_field
        super().__init__(
            f"mapping for intent field '{intent_field}' targets variable "
            f"'{variable}' in module '{module}', but that module's variables.tf "
            "declares no such variable; refusing to emit a fabricated variable "
            "name (R10.3/R10.4)."
        )


# ---------------------------------------------------------------------------
# Intent -> module-variable mapping (the source of truth, R10.3)
# ---------------------------------------------------------------------------


#: A transform converting an intent value into the value a module variable
#: expects (e.g. ``db2-se`` -> ``se`` for 4-parameter-group's ``engine_edition``).
Transform = Callable[[Any], Any]


@dataclass(frozen=True)
class VarTarget:
    """One concrete destination for an intent field: a real variable in a real
    module, optionally transformed.

    Attributes:
        module: the module directory name (e.g. ``5-rds``).
        variable: the variable name as declared in that module's
            ``variables.tf`` -- never a fabricated name (R10.3).
        transform: optional value transform; identity when ``None``.
    """

    module: str
    variable: str
    transform: Optional[Transform] = None

    def value_for(self, intent_value: Any) -> Any:
        return self.transform(intent_value) if self.transform else intent_value


# Non-variable handling markers. An intent field mapped to one of these is
# *known* and intentionally not rendered as a module variable, so it does NOT
# trigger the unmapped-field halt (R10.4) -- only a field with no entry at all
# does.

#: The module hardcodes this value as a literal (e.g. 5-rds sets
#: ``license_model = "bring-your-own-license"`` and ``monitoring_interval = 15``
#: directly in main.tf), so there is no variable to populate.
HANDLED_AS_LITERAL = "literal"

#: Surfaced through the provider ``default_tags`` block via the ``tag`` /
#: ``environment`` / ``owner`` variables rather than a single ``tags`` variable
#: (design: "tags -> provider default_tags"). Full tag handling is task 7.4.
HANDLED_AS_DEFAULT_TAGS = "default_tags"

#: Resolver-emitted metadata (provenance, conversion notes, the resolved family)
#: that documents the intent but is not itself a module input.
HANDLED_AS_INTERNAL = "internal"

#: Consumed by the optional-capability rendering (task 7.3) to GATE a capability
#: or derive create/reuse flags, rather than mapped 1:1 to a module variable.
#: The field is *known* (so it never triggers the unmapped-field halt, R10.4),
#: but its effect is computed in :func:`render_optional_capabilities` /
#: :func:`capability_required_modules` (e.g. ``license_manager`` gates whether
#: ``6-license-manager`` renders; ``audit_bucket_exists`` decides reuse vs
#: create of the audit bucket).
HANDLED_AS_CAPABILITY = "capability"

#: Surfaced by the security-invariant rendering rather than a module variable:
#: the SSL-only ingress (R6.5) is rendered in ``security.tf`` from these fields,
#: and the public-access acknowledgement (R6.3) gates the forced
#: ``publicly_accessible=false``. They are consumed by the composer directly
#: (see :func:`render_security_supplement` / :func:`enforce_security_invariants`),
#: so they are *known* and intentionally not module variables (R10.4), but they
#: are NOT module inputs.
HANDLED_BY_SECURITY_RENDERING = "security_rendering"


# Small, named transforms (kept module-level so they are testable and reused).

def _edition_short(engine: str) -> str:
    """``db2-se`` -> ``se`` for 4-parameter-group's ``engine_edition`` (design)."""
    return str(engine).removeprefix("db2-")


def _edition_upper(engine: str) -> str:
    """``db2-se`` -> ``SE`` for 6-license-manager's ``db2_edition`` (design)."""
    return _edition_short(engine).upper()


def _first(seq: Any) -> Any:
    """First element of a list/tuple (the intent carries
    ``vpc_security_group_ids`` as a list; the modules take a single
    ``security_group_id`` string)."""
    if isinstance(seq, (list, tuple)) and seq:
        return seq[0]
    return seq


#: The intent fields whose values are Sensitive_Values: they are still rendered
#: into the module tfvars (Terraform needs them) but tracked here so the PR /
#: plan / artifact surfaces (tasks 7.5, 11, 12) can mask them.
SENSITIVE_INTENT_FIELDS: frozenset[str] = frozenset(
    {"ibm_customer_id", "ibm_site_id", "master_password"}
)


#: A field maps to either a list of concrete :class:`VarTarget`s or a
#: non-variable handling marker. This table is the single source of truth for
#: R10.3 and is grounded in the modules' actual ``variables.tf`` (verified at
#: load by :func:`_assert_targets_grounded`).
FieldMapping = Union[list[VarTarget], str]

INTENT_FIELD_MAPPING: dict[str, FieldMapping] = {
    # --- governance / sizing axes ------------------------------------------
    # deployment_tier == the Environment tag value; surfaced as each module's
    # `environment` variable (which feeds default_tags Environment).
    "deployment_tier": [
        VarTarget("1-networking", "environment"),
        VarTarget("2-iam", "environment"),
        VarTarget("3-kms", "environment"),
        VarTarget("4-parameter-group", "environment"),
        VarTarget("5-rds", "environment"),
        VarTarget("6-license-manager", "environment"),
    ],
    "workload_size": [VarTarget("5-rds", "db_size_label", abbreviate_workload_size)],
    "region": [
        VarTarget("1-networking", "aws_region"),
        VarTarget("2-iam", "aws_region"),
        VarTarget("3-kms", "aws_region"),
        VarTarget("4-parameter-group", "aws_region"),
        VarTarget("5-rds", "aws_region"),
        VarTarget("6-license-manager", "aws_region"),
    ],
    # --- engine / edition / version ----------------------------------------
    # One intent field, three module destinations with different edition strings
    # (design note: "edition string differs per module").
    "engine": [
        VarTarget("5-rds", "engine"),
        VarTarget("4-parameter-group", "engine_edition", _edition_short),
        VarTarget("6-license-manager", "db2_edition", _edition_upper),
    ],
    # engine_version sets the full version AND seeds the major version that
    # drives the parameter-group family in both 5-rds and 4-parameter-group.
    "engine_version": [
        VarTarget("5-rds", "engine_version"),
        VarTarget("5-rds", "engine_major_version", major_version_of),
        VarTarget("4-parameter-group", "engine_major_version", major_version_of),
    ],
    # --- compute / storage --------------------------------------------------
    "instance_class": [VarTarget("5-rds", "instance_class")],
    "allocated_storage": [VarTarget("5-rds", "allocated_storage")],
    "storage_type": [VarTarget("5-rds", "storage_type")],
    "iops": [VarTarget("5-rds", "iops")],
    "storage_throughput": [VarTarget("5-rds", "storage_throughput")],
    "multi_az": [VarTarget("5-rds", "multi_az")],
    "availability_zone": [VarTarget("5-rds", "availability_zone")],
    # --- database / connection ---------------------------------------------
    "db_name": [VarTarget("5-rds", "db_name")],
    "master_username": [VarTarget("5-rds", "master_username")],
    "manage_master_user_password": [
        VarTarget("5-rds", "manage_master_user_password")
    ],
    "master_password": [VarTarget("5-rds", "master_password")],
    "master_user_secret_kms_key_id": [
        VarTarget("5-rds", "master_user_secret_kms_key_id")
    ],
    "port": [VarTarget("5-rds", "db2_port")],
    # --- backup / protection ------------------------------------------------
    "backup_retention_period": [VarTarget("5-rds", "backup_retention_period")],
    "deletion_protection": [VarTarget("5-rds", "deletion_protection")],
    # --- networking / access ------------------------------------------------
    "vpc_id": [
        VarTarget("5-rds", "vpc_id"),
        VarTarget("1-networking", "vpc_id"),
    ],
    "publicly_accessible": [
        VarTarget("5-rds", "publicly_accessible"),
        VarTarget("1-networking", "publicly_accessible"),
    ],
    "vpc_security_group_ids": [
        VarTarget("5-rds", "security_group_id", _first),
        VarTarget("1-networking", "security_group_id", _first),
    ],
    "db_subnet_group_name": [
        VarTarget("5-rds", "db_subnet_group_name"),
        VarTarget("1-networking", "db_subnet_group_name"),
    ],
    # --- encryption ---------------------------------------------------------
    "storage_encrypted": [VarTarget("5-rds", "storage_encrypted")],
    "kms_key_id": [
        VarTarget("5-rds", "kms_key_arn"),
        VarTarget("3-kms", "kms_key_arn"),
    ],
    # --- parameter group ----------------------------------------------------
    "db_parameter_group_name": [
        VarTarget("5-rds", "parameter_group_name"),
        VarTarget("4-parameter-group", "parameter_group_name"),
    ],
    # --- monitoring ---------------------------------------------------------
    # monitoring_interval is a literal (=15) in 5-rds/main.tf (design note).
    "monitoring_interval": HANDLED_AS_LITERAL,
    "monitoring_role_arn": [VarTarget("5-rds", "monitoring_role_arn")],
    # enabled_cloudwatch_logs_exports is a literal (["diag.log","notify.log"])
    # in 5-rds/main.tf today; tracked as a literal so it is not flagged unmapped.
    "enable_cloudwatch_logs_exports": HANDLED_AS_LITERAL,
    # --- IBM licensing identifiers (sensitive; all editions) ---------------
    "ibm_customer_id": [VarTarget("4-parameter-group", "ibm_customer_id")],
    "ibm_site_id": [VarTarget("4-parameter-group", "ibm_site_id")],
    # SSM-backed IBM IDs: the parameter NAMES (not the values) are mapped to the
    # module's *_ssm vars; the module reads the decrypted values from SSM at apply
    # so the IDs never live in the deployment repo. Names are not sensitive.
    "ibm_customer_id_ssm": [VarTarget("4-parameter-group", "ibm_customer_id_ssm")],
    "ibm_site_id_ssm": [VarTarget("4-parameter-group", "ibm_site_id_ssm")],
    # --- directory service (AWS Managed AD + self-managed AD) --------------
    "domain": [VarTarget("5-rds", "directory_id")],
    "domain_iam_role_name": [VarTarget("5-rds", "directory_role_name")],
    "domain_fqdn": [VarTarget("5-rds", "domain_fqdn")],
    "domain_ou": [VarTarget("5-rds", "domain_ou")],
    # The self-managed AD join secret is consumed by 5-rds (the instance join)
    # AND granted to the directory IAM role in 2-iam (self_managed_ad_secret_arn),
    # so the created role can read the join credentials (R13.4).
    "domain_auth_secret_arn": [
        VarTarget("5-rds", "domain_auth_secret_arn"),
        VarTarget("2-iam", "self_managed_ad_secret_arn"),
    ],
    "domain_dns_ips": [VarTarget("5-rds", "domain_dns_ips")],
    # Pre-existing directory-role flag (R13.3/R13.4): consumed by the composer to
    # set 2-iam directory_role_exists (reuse) vs create_directory_role (create).
    "directory_role_exists": HANDLED_AS_CAPABILITY,
    # --- optional capability: Db2 audit (R13.5, R13.10) --------------------
    # enable_audit gates the 5-rds DB2_AUDIT option group + role association;
    # the audit IAM role/policy and bucket wiring live in 2-iam. The composer
    # derives 2-iam's create_audit_role / create_audit_bucket flags from the
    # request + pre-existing-bucket check (see render_optional_capabilities).
    "enable_audit": [VarTarget("5-rds", "enable_audit")],
    "audit_role_arn": [VarTarget("5-rds", "audit_role_arn")],
    "audit_bucket_name": [
        VarTarget("5-rds", "audit_bucket_name"),
        VarTarget("2-iam", "audit_bucket_name"),
    ],
    # CMK for the audit bucket (R6.10 CMK-everywhere): 2-iam variable name is
    # audit_bucket_kms_key_arn; the intent carries it as audit_bucket_kms_key_id.
    "audit_bucket_kms_key_id": [VarTarget("2-iam", "audit_bucket_kms_key_arn")],
    # Pre-existing-bucket check result (R13.10): consumed by the composer to
    # decide 2-iam create_audit_bucket (reuse when it already exists).
    "audit_bucket_exists": HANDLED_AS_CAPABILITY,
    # --- optional capability: S3 restore integration (R13.7) ---------------
    "restore_from_s3": [VarTarget("5-rds", "restore_from_s3")],
    "s3_integration_role_arn": [VarTarget("5-rds", "s3_integration_role_arn")],
    "s3_backup_bucket_name": [VarTarget("2-iam", "s3_backup_bucket_name")],
    "s3_backup_bucket_kms_key_id": [
        VarTarget("2-iam", "s3_backup_bucket_kms_key_arn")
    ],
    "s3_backup_bucket_exists": HANDLED_AS_CAPABILITY,
    # --- optional capability: License Manager tracking (R13.8) -------------
    # license_manager GATES whether 6-license-manager renders at all (otherwise
    # the edition alone would always enable it). license_count is the vCPU count
    # the module tracks; db_instance_arn is informational.
    "license_manager": HANDLED_AS_CAPABILITY,
    "license_count": [VarTarget("6-license-manager", "license_count")],
    "db_instance_arn": [VarTarget("6-license-manager", "db_instance_arn")],
    # --- optional capability: cross-region mounted standby replica (R13.2) -
    "create_standby_replica": [VarTarget("5-rds", "create_standby_replica")],
    "standby_replica_region": [VarTarget("5-rds", "standby_replica_region")],
    "standby_replica_identifier": [
        VarTarget("5-rds", "standby_replica_identifier")
    ],
    "standby_instance_class": [VarTarget("5-rds", "standby_instance_class")],
    "standby_parameter_group_name": [
        VarTarget("5-rds", "standby_parameter_group_name")
    ],
    "standby_kms_key_arn": [VarTarget("5-rds", "standby_kms_key_arn")],
    # --- optional capability: same-region read replica (R13.15) ------------
    "create_read_replica": [VarTarget("5-rds", "create_read_replica")],
    "read_replica_identifier": [VarTarget("5-rds", "read_replica_identifier")],
    "read_replica_instance_class": [
        VarTarget("5-rds", "read_replica_instance_class")
    ],
    # --- identifier / licensing model --------------------------------------
    "db_instance_identifier": [VarTarget("5-rds", "db_instance_identifier")],
    # license_model is hardcoded "bring-your-own-license" in 5-rds/main.tf and
    # is a const in the schema; no module variable to populate.
    "license_model": HANDLED_AS_LITERAL,
    # --- tags ---------------------------------------------------------------
    "tags": HANDLED_AS_DEFAULT_TAGS,
    # --- resolver metadata (not module inputs) -----------------------------
    "schema_version": HANDLED_AS_INTERNAL,
    "db_parameter_group_family": HANDLED_AS_INTERNAL,
    "_provenance": HANDLED_AS_INTERNAL,
    # --- security-invariant rendering inputs (not module variables) --------
    # The SSL-only ingress (R6.5) is rendered into security.tf from these source
    # fields; the public-access acknowledgement (R6.3) gates the forced
    # publicly_accessible=false. They are consumed by the composer's security
    # rendering, not mapped to any module variable.
    "public_access_acknowledged": HANDLED_BY_SECURITY_RENDERING,
    "ingress_cidrs": HANDLED_BY_SECURITY_RENDERING,
    "ingress_cidr_blocks": HANDLED_BY_SECURITY_RENDERING,
    "ingress_source_security_group_ids": HANDLED_BY_SECURITY_RENDERING,
    "source_security_group_ids": HANDLED_BY_SECURITY_RENDERING,
}


# ---------------------------------------------------------------------------
# Module variable discovery (grounding against the real variables.tf, R10.3)
# ---------------------------------------------------------------------------

_VARIABLE_DECL_RE = re.compile(r'^\s*variable\s+"([^"]+)"', re.MULTILINE)


def parse_module_variables(module_dir: Path) -> set[str]:
    """Return the set of variable names declared in ``module_dir/variables.tf``.

    Parses the real module so the composer can verify every name it emits is a
    genuine module variable (R10.3) and never fabricates one (R10.4). A module
    without a ``variables.tf`` returns an empty set.
    """
    variables_file = module_dir / "variables.tf"
    if not variables_file.is_file():
        return set()
    text = variables_file.read_text()
    return set(_VARIABLE_DECL_RE.findall(text))


def load_module_variable_index(
    modules_root: Union[str, Path, None] = None,
) -> dict[str, set[str]]:
    """Build ``{module_name: {declared variable names}}`` for every module.

    The index grounds the mapping table: :func:`_assert_targets_grounded` checks
    every :class:`VarTarget` against it so a drifted variable name fails loudly.
    """
    root = Path(modules_root) if modules_root is not None else DEFAULT_MODULES_ROOT
    return {module: parse_module_variables(root / module) for module in ALL_MODULES}


def _assert_targets_grounded(
    variable_index: Mapping[str, set[str]],
) -> None:
    """Verify every mapping :class:`VarTarget` names a real module variable.

    Raises :class:`FabricatedVariableError` for the first target whose variable
    is not declared in its module's ``variables.tf`` (R10.3/10.4). This guards
    the *table itself*, independent of any particular intent.
    """
    for intent_field, mapping in INTENT_FIELD_MAPPING.items():
        if isinstance(mapping, str):
            continue  # a non-variable handling marker
        for target in mapping:
            declared = variable_index.get(target.module, set())
            if target.variable not in declared:
                raise FabricatedVariableError(
                    target.module, target.variable, intent_field
                )


# ---------------------------------------------------------------------------
# HCL value formatting
# ---------------------------------------------------------------------------


def format_hcl_value(value: Any) -> str:
    """Format a Python value as an HCL literal for a ``terraform.tfvars`` file.

    Handles the scalar/list shapes the intent uses: bool -> ``true``/``false``;
    int/float -> bare number; str -> double-quoted (escaped); list -> bracketed
    list of formatted items. Mappings are emitted as HCL objects (used by the
    default_tags handling).
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(format_hcl_value(item) for item in value) + "]"
    if isinstance(value, Mapping):
        inner = ", ".join(
            f'"{k}" = {format_hcl_value(v)}' for k, v in value.items()
        )
        return "{ " + inner + " }"
    # Fall back to a quoted string for anything unexpected rather than emitting
    # an invalid bare token.
    return format_hcl_value(str(value))


# ---------------------------------------------------------------------------
# Per-module tfvars collection
# ---------------------------------------------------------------------------


@dataclass
class RenderedModule:
    """The tfvars rendered for one module.

    Attributes:
        module: the module directory name.
        variables: ``{variable_name: value}`` to write into terraform.tfvars.
        sensitive_variables: the subset of ``variables`` whose values are
            Sensitive_Values (for masking in PR/plan/artifact surfaces).
    """

    module: str
    variables: dict[str, Any]
    sensitive_variables: set[str] = dataclass_field(default_factory=set)


def _iter_field_targets(intent: Mapping[str, Any]):
    """Yield ``(intent_field, value, VarTarget)`` for every present intent field
    that maps to concrete variable targets, halting on any unmapped field.

    Raises:
        UnmappedIntentFieldError: an intent field has no mapping entry (R10.4).
    """
    for field_name, value in intent.items():
        # Resolver-internal underscored keys are metadata, never module inputs;
        # they are exempt from the unmapped-field halt.
        if field_name.startswith("_"):
            continue
        if field_name not in INTENT_FIELD_MAPPING:
            raise UnmappedIntentFieldError(field_name)
        mapping = INTENT_FIELD_MAPPING[field_name]
        if isinstance(mapping, str):
            continue  # handled as literal / default_tags / internal
        for target in mapping:
            yield field_name, value, target


def collect_module_variables(
    intent: Mapping[str, Any],
    *,
    variable_index: Optional[Mapping[str, set[str]]] = None,
) -> dict[str, RenderedModule]:
    """Map the intent into ``{module: RenderedModule}`` via the mapping table.

    For each present, mapped intent field, the transformed value is written to
    every target module/variable. The ``tags`` field is composed into the full
    mandatory tag set (``created_by``, ``generation_model``, ``Project``,
    ``Environment``, ``Owner``) plus customer extras and written through the
    provider ``default_tags`` convention via each module's ``tag`` (Project),
    ``owner`` (Owner), ``created_by``, ``generation_model``, and ``extra_tags``
    variables (R14); ``Environment`` flows via ``deployment_tier`` ->
    ``environment``. See :func:`apply_mandatory_tags`.

    Every emitted variable name is checked against the module's real declared
    variables, so a fabricated name halts here (R10.3/10.4).

    Raises:
        UnmappedIntentFieldError: an intent field has no mapping entry (R10.4).
        FabricatedVariableError: a mapped target names a non-existent variable.
    """
    index = variable_index if variable_index is not None else load_module_variable_index()

    modules: dict[str, RenderedModule] = {}

    def _module(name: str) -> RenderedModule:
        return modules.setdefault(name, RenderedModule(module=name, variables={}))

    def _set(module_name: str, variable: str, value: Any, intent_field: str) -> None:
        declared = index.get(module_name, set())
        if variable not in declared:
            raise FabricatedVariableError(module_name, variable, intent_field)
        rendered = _module(module_name)
        rendered.variables[variable] = value
        if intent_field in SENSITIVE_INTENT_FIELDS:
            rendered.sensitive_variables.add(variable)

    # 1) Direct field -> target writes (this is also where the unmapped-field
    #    halt fires, via _iter_field_targets).
    for field_name, value, target in _iter_field_targets(intent):
        _set(target.module, target.variable, target.value_for(value), field_name)

    # 2) tags -> provider default_tags (R14): the five mandatory tags plus any
    #    customer extras are composed and written into each intent-driven
    #    module's tag variables. This halts on a missing/empty mandatory tag
    #    (R14.5/R14.6) or a >50 tag count (R14.4) before any tfvars is produced.
    apply_mandatory_tags(intent, modules, variable_index=index)

    return modules


# ---------------------------------------------------------------------------
# Mandatory tagging composition (R14)
# ---------------------------------------------------------------------------


def resolve_generation_model() -> str:
    """Return the non-empty ``generation_model`` tag value (R14.1/R14.2).

    Prefers the ``GENERATION_MODEL`` environment variable so the actual runtime
    model id is recorded; falls back to :data:`DEFAULT_GENERATION_MODEL` so the
    tag is never empty (R14.1 requires a non-empty value).
    """
    env_value = os.environ.get("GENERATION_MODEL", "").strip()
    return env_value or DEFAULT_GENERATION_MODEL


def compose_mandatory_tags(intent: Mapping[str, Any]) -> dict[str, str]:
    """Compose the full tag set for every created resource from the intent (R14).

    Returns the merged ``{key: value}`` map that every resource carries via the
    provider ``default_tags``:

    * the five mandatory tags — ``created_by`` (fixed, R14.2),
      ``generation_model`` (R14.1/R14.2), and the customer-supplied ``Project``,
      ``Environment``, ``Owner`` (R14.3) — each with a non-empty value;
    * plus ``ManagedBy`` (a module literal) and every additional customer tag
      from the intent's ``tags`` object, appended WITHOUT overriding any
      mandatory key (R14.4/R14.7).

    The mandatory keys are applied LAST so a colliding customer tag can never
    displace a mandatory value (R14.4).

    Raises:
        MandatoryTagError: a mandatory customer tag (``Project``/``Environment``
            /``Owner``) is missing or empty (R14.5/R14.6), or the composed tag
            count exceeds :data:`MAX_TAGS_PER_RESOURCE` (R14.4).
    """
    tags = intent.get("tags")
    if not isinstance(tags, Mapping):
        raise MandatoryTagError(
            "the Deployment_Intent carries no 'tags' object; the mandatory "
            "Project/Environment/Owner tags cannot be resolved (R14.6).",
            fields=list(CUSTOMER_MANDATORY_TAG_KEYS),
        )

    # R14.5/R14.6: each customer-supplied mandatory tag must be present and
    # non-empty; report every offender by name before halting.
    missing: list[str] = []
    for key in CUSTOMER_MANDATORY_TAG_KEYS:
        value = tags.get(key)
        if not isinstance(value, str) or not value.strip():
            missing.append(key)
    if missing:
        raise MandatoryTagError(
            "the Deployment_Intent is missing or has an empty value for the "
            f"mandatory tag(s): {', '.join(missing)}; each of "
            f"{', '.join(CUSTOMER_MANDATORY_TAG_KEYS)} must be present with a "
            "non-empty value (R14.5/R14.6).",
            fields=missing,
        )

    # Start from the customer extras (everything in tags), then layer the
    # module literal and the mandatory keys LAST so they always win (R14.4).
    composed: dict[str, str] = {str(k): str(v) for k, v in tags.items()}
    composed[MANAGED_BY_TAG_KEY] = "Terraform"
    composed["created_by"] = CREATED_BY_TAG_VALUE
    composed["generation_model"] = resolve_generation_model()
    composed["Project"] = str(tags["Project"])
    composed["Environment"] = str(tags["Environment"])
    composed["Owner"] = str(tags["Owner"])

    # R14.4: cap the total tag count per resource at 50.
    if len(composed) > MAX_TAGS_PER_RESOURCE:
        raise MandatoryTagError(
            f"the composed tag set has {len(composed)} tags, exceeding the "
            f"per-resource maximum of {MAX_TAGS_PER_RESOURCE} (R14.4); reduce "
            "the number of customer-supplied tags.",
        )

    return composed


def extra_tags_for_modules(composed: Mapping[str, str]) -> dict[str, str]:
    """Return the customer-extra tags to pass to the modules' ``extra_tags``
    variable: every composed tag except the keys the modules already emit
    natively (the five mandatory keys + ``ManagedBy``).

    The modules' default_tags block re-applies Project/Environment/Owner/
    created_by/generation_model/ManagedBy from their own variables and merges
    ``extra_tags`` UNDER them, so passing the mandatory keys again is redundant
    and (by the module's merge order) can never override them. Keeping
    ``extra_tags`` to only the genuine extras keeps the rendered tfvars clean.
    """
    reserved = set(MANDATORY_TAG_KEYS) | {MANAGED_BY_TAG_KEY}
    return {k: v for k, v in composed.items() if k not in reserved}


def apply_mandatory_tags(
    intent: Mapping[str, Any],
    modules: dict[str, RenderedModule],
    *,
    variable_index: Mapping[str, set[str]],
) -> dict[str, str]:
    """Write the composed mandatory + customer tag set into every intent-driven
    module's tag variables (R14), via the provider ``default_tags``.

    For each intent-driven module that declares them, this sets:
      * ``tag`` <- Project, ``owner`` <- Owner (pre-existing variables);
      * ``created_by`` / ``generation_model`` <- the provenance values;
      * ``extra_tags`` <- the genuine customer extras (mandatory keys excluded).
    ``environment`` (the Environment tag) is sourced from ``deployment_tier``
    in :func:`collect_module_variables` step 1, so it is not re-set here.

    Returns the composed tag map (for inspection/policy-gate reuse).

    Raises:
        MandatoryTagError: propagated from :func:`compose_mandatory_tags`.
        FabricatedVariableError: a tag variable is absent from a module that the
            composition expects to carry it (defense in depth, R10.3).
    """
    composed = compose_mandatory_tags(intent)
    extras = extra_tags_for_modules(composed)

    def _module(name: str) -> RenderedModule:
        return modules.setdefault(name, RenderedModule(module=name, variables={}))

    def _set(module_name: str, variable: str, value: Any) -> None:
        declared = variable_index.get(module_name, set())
        if variable not in declared:
            raise FabricatedVariableError(module_name, variable, "tags")
        _module(module_name).variables[variable] = value

    for module_name in INTENT_DRIVEN_MODULES:
        declared = variable_index.get(module_name, set())
        if "tag" in declared:
            _set(module_name, "tag", composed["Project"])
        if "owner" in declared:
            _set(module_name, "owner", composed["Owner"])
        if CREATED_BY_VARIABLE in declared:
            _set(module_name, CREATED_BY_VARIABLE, composed["created_by"])
        if GENERATION_MODEL_VARIABLE in declared:
            _set(module_name, GENERATION_MODEL_VARIABLE, composed["generation_model"])
        if EXTRA_TAGS_VARIABLE in declared:
            _set(module_name, EXTRA_TAGS_VARIABLE, extras)

    return composed


# ---------------------------------------------------------------------------
# Reuse vs create selection (R10.5/10.6)
# ---------------------------------------------------------------------------

#: Disposition of a create-path module: either the customer supplied an existing
#: resource (``reuse`` -> skip the create module) or none was supplied
#: (``create`` -> render the module to create it).
REUSE = "reuse"
CREATE = "create"


@dataclass(frozen=True)
class ReuseRule:
    """One reusable-resource decision (R10.5/10.6).

    Attributes:
        module: the create-path module that would build the resource when no
            existing one is supplied (e.g. ``3-kms`` builds the CMK).
        intent_field: the intent field that, when populated, names the existing
            resource and switches the decision to :data:`REUSE`.
        resource: a human label for the resource (for messages/inspection).
    """

    module: str
    intent_field: str
    resource: str


#: The four reusable resources from the design's "Module skip / create / extend
#: logic" (R10.5/10.6), each grounded in the create-path module's real reuse
#: variable:
#:  * 1-networking creates the DB subnet group unless ``db_subnet_group_name``
#:    is supplied (module var ``db_subnet_group_name``).
#:  * 3-kms creates the MRK CMK unless ``kms_key_id`` is supplied (module var
#:    ``kms_key_arn``).
#:  * 4-parameter-group creates the parameter group unless
#:    ``db_parameter_group_name`` is supplied (module var ``parameter_group_name``).
#:  * 2-iam creates the enhanced-monitoring role unless an existing role is
#:    supplied via ``monitoring_role_arn`` (module reuse var ``monitoring_role_name``).
REUSE_RULES: tuple[ReuseRule, ...] = (
    ReuseRule("1-networking", "db_subnet_group_name", "DB subnet group"),
    ReuseRule("3-kms", "kms_key_id", "KMS CMK"),
    ReuseRule("4-parameter-group", "db_parameter_group_name", "DB parameter group"),
    ReuseRule("2-iam", "monitoring_role_arn", "enhanced-monitoring IAM role"),
)


def _is_supplied(value: Any) -> bool:
    """True when an intent value names an existing resource.

    A non-empty string (or non-empty list) means the customer supplied an
    existing resource identifier; an empty string / empty list / missing value
    means "create one" (R10.5/10.6).
    """
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple)):
        return len(value) > 0
    return True


def resolve_module_dispositions(intent: Mapping[str, Any]) -> dict[str, str]:
    """Decide, per reusable resource, whether to reuse an existing one or create.

    Returns ``{module: REUSE|CREATE}`` for every module named in
    :data:`REUSE_RULES`. ``REUSE`` when the intent supplies the existing
    resource identifier (R10.5); ``CREATE`` otherwise (R10.6).
    """
    return {
        rule.module: (REUSE if _is_supplied(intent.get(rule.intent_field)) else CREATE)
        for rule in REUSE_RULES
    }


# ---------------------------------------------------------------------------
# Module enablement (reuse/create selection — R10.5/10.6)
# ---------------------------------------------------------------------------


def select_enabled_modules(
    intent: Mapping[str, Any],
    populated_modules: Mapping[str, RenderedModule],
) -> list[str]:
    """Return, in apply order, the modules to emit create-path tfvars for.

    Reuse vs create (R10.5/10.6): for each reusable resource
    (:data:`REUSE_RULES`), when the intent supplies an existing identifier the
    create-path module is **skipped** (the existing resource is referenced via
    the 5-rds consuming variable instead); when none is supplied the create-path
    module is **enabled** so it builds the resource.

    On top of that, ``5-rds`` is always enabled (it is the instance itself, never
    reused), and any other intent-driven module the intent actually populated
    (e.g. ``2-iam`` when audit/S3/directory roles are requested by task 7.3)
    stays enabled. ``6-license-manager`` is enabled ONLY when License Manager
    tracking is explicitly requested (R13.8), even though the resolved edition
    maps to it for value rendering.

    ``0-backend-setup`` is never included here (the remote-state bootstrap is not
    intent-driven), though the root module still references it (R10.1).
    """
    dispositions = resolve_module_dispositions(intent)

    enabled: set[str] = {"5-rds"}  # the instance is always rendered, never reused.

    # Any intent-driven module the intent actually populated stays enabled,
    # UNLESS a reuse rule for it fired (existing resource supplied -> skip).
    for name, rendered in populated_modules.items():
        if name in INTENT_DRIVEN_MODULES and rendered.variables:
            if dispositions.get(name) == REUSE:
                continue
            enabled.add(name)

    # Create-path modules whose resource was not supplied are enabled to create
    # it, even if no other variable populated them.
    for module, disposition in dispositions.items():
        if disposition == CREATE:
            enabled.add(module)
        else:
            enabled.discard(module)

    # 2-iam's reuse rule keys on the monitoring role only, but 2-iam also builds
    # the audit / S3 / directory roles (task 7.3). When any of those optional
    # capabilities is requested, 2-iam MUST render even if the monitoring role is
    # being reused (R13.3/13.4/13.5/13.7), so force it back on here.
    if (
        audit_requested(intent)
        or s3_restore_requested(intent)
        or aws_managed_ad_requested(intent)
        or self_managed_ad_requested(intent)
    ):
        enabled.add("2-iam")

    # 6-license-manager is GATED on an explicit License Manager request (R13.8):
    # the resolved `engine` maps to 6-license-manager for value rendering
    # (db2_edition), which would otherwise populate it for every deployment, so
    # a plain deployment must not silently stand up License Manager resources.
    if not license_manager_requested(intent):
        enabled.discard("6-license-manager")

    # Preserve the canonical apply order.
    return [m for m in INTENT_DRIVEN_MODULES if m in enabled]


# ---------------------------------------------------------------------------
# Security-invariant rendering (R6.1, R6.2, R6.3, R6.5) — always on
# ---------------------------------------------------------------------------


def public_access_acknowledged(intent: Mapping[str, Any]) -> bool:
    """True only when the public-access acknowledgement is explicitly ``True``
    (R6.3/R6.4). Absent or any non-``True`` value means not acknowledged, so the
    composer forces ``publicly_accessible=false``."""
    return intent.get(PUBLIC_ACCESS_ACK_FIELD) is True


def enforce_security_invariants(
    intent: Mapping[str, Any],
    modules: Mapping[str, RenderedModule],
    dispositions: Mapping[str, str],
    *,
    variable_index: Mapping[str, set[str]],
) -> None:
    """Force the always-on security invariants into the collected module
    variables, regardless of tier or prompt wording (R6.1/R6.3/R6.7).

    * **R6.1** — storage encryption is always on, with the customer-managed MRK
      CMK. ``5-rds.storage_encrypted`` is pinned ``true``; when the CMK is
      created here (3-kms create path) ``3-kms.multi_region_key`` is pinned
      ``true`` so the key is an MRK.
    * **R6.3** — absent ``public_access_acknowledged=true`` the composer renders
      ``publicly_accessible=false`` on every module that exposes it
      (``5-rds``, ``1-networking``), overriding any prompt value.

    The DB2COMM=SSL/ssl_svcename=50443 parameters (R6.2) live in the
    4-parameter-group module (always rendered into its parameter group) and the
    SSL-only ingress (R6.5) is rendered as a ``security.tf`` supplement, both
    outside this per-variable enforcement; see :func:`render_security_supplement`.

    Every variable written here is verified against the module's real declared
    variables, so a drifted name halts (R10.3/10.4).
    """

    def _force(module_name: str, variable: str, value: Any) -> None:
        declared = variable_index.get(module_name, set())
        if variable not in declared:
            raise FabricatedVariableError(module_name, variable, "security_invariant")
        rendered = modules.setdefault(
            module_name, RenderedModule(module=module_name, variables={})
        )
        rendered.variables[variable] = value

    # R6.1: storage encryption always on with the MRK CMK.
    _force("5-rds", "storage_encrypted", True)
    if dispositions.get("3-kms") == CREATE:
        # The composer is creating the CMK; make it multi-region (MRK) so the
        # storage-encryption invariant uses a customer-managed MRK CMK (R6.1).
        _force("3-kms", "multi_region_key", True)

    # R6.3: non-public by default unless explicitly acknowledged.
    if not public_access_acknowledged(intent):
        _force("5-rds", "publicly_accessible", False)
        # Only pin 1-networking's flag when that module is being rendered
        # (create path); when the subnet group is reused, 1-networking is skipped.
        if "1-networking" in modules:
            _force("1-networking", "publicly_accessible", False)


# ---------------------------------------------------------------------------
# Optional-capability rendering (R13) — each gated on the resolved intent
# ---------------------------------------------------------------------------


def _truthy(value: Any) -> bool:
    """A capability is "requested" when its gate value is a truthy scalar.

    Mirrors the validator's ``_truthy`` so the composer and validator agree on
    what "requested" means for an optional capability (R13).
    """
    return bool(value)


def audit_requested(intent: Mapping[str, Any]) -> bool:
    """True when the intent requests Db2 audit (R13.5/R13.10).

    Audit is requested by ``enable_audit`` being truthy; a bare
    ``audit_bucket_name`` without the flag is not treated as a request so the
    capability stays strictly gated.
    """
    return _truthy(intent.get("enable_audit"))


def s3_restore_requested(intent: Mapping[str, Any]) -> bool:
    """True when the intent requests S3 restore integration (R13.7)."""
    return _truthy(intent.get("restore_from_s3"))


def license_manager_requested(intent: Mapping[str, Any]) -> bool:
    """True when the intent requests License Manager tracking (R13.8).

    Gated on the explicit ``license_manager`` flag, NOT on the presence of an
    edition: the edition maps to ``6-license-manager`` for value rendering, but
    the module is only ENABLED when tracking is explicitly requested, so a plain
    deployment does not silently stand up License Manager resources.
    """
    return _truthy(intent.get("license_manager"))


def aws_managed_ad_requested(intent: Mapping[str, Any]) -> bool:
    """True when the intent requests AWS Managed AD (R13.3): a ``domain``
    (directory id) is supplied."""
    return _is_supplied(intent.get("domain"))


def self_managed_ad_requested(intent: Mapping[str, Any]) -> bool:
    """True when the intent requests customer self-managed AD / Kerberos
    (R13.4): a ``domain_fqdn`` is supplied."""
    return _is_supplied(intent.get("domain_fqdn"))


def standby_replica_requested(intent: Mapping[str, Any]) -> bool:
    """True when the intent requests a cross-region mounted standby (R13.2)."""
    return _truthy(intent.get("create_standby_replica"))


def read_replica_requested(intent: Mapping[str, Any]) -> bool:
    """True when the intent requests a same-region read replica (R13.15)."""
    return _truthy(intent.get("create_read_replica"))


def render_optional_capabilities(
    intent: Mapping[str, Any],
    modules: dict[str, RenderedModule],
    *,
    variable_index: Mapping[str, set[str]],
) -> None:
    """Gate + complete the optional capabilities into the collected variables
    (R13). Each capability renders ONLY when the resolved intent requests it.

    The direct field->variable writes (``enable_audit``, ``restore_from_s3``,
    ``create_standby_replica``, the self-managed AD / replica fields, etc.)
    already happened in :func:`collect_module_variables` via the mapping table.
    This function adds the bits the mapping table cannot express on its own:

    * **Audit (R13.5/R13.10):** when requested, set 2-iam ``create_audit_role``
      so the audit IAM role/policy (scoped to the bucket, ported from
      ``0cr-ins.sh``) is created, and derive ``create_audit_bucket`` from the
      pre-existing-bucket check: reuse (``false``) when the bucket already
      exists (``audit_bucket_exists`` truthy / R13.10 ``head-bucket``), else
      create it.
    * **S3 restore (R13.7):** when requested, set 2-iam ``create_s3_role`` and
      derive ``create_s3_backup_bucket`` from ``s3_backup_bucket_exists``.
    * **AWS Managed AD (R13.3) / self-managed AD (R13.4):** when either is
      requested, set 2-iam ``create_directory_role`` so the directory IAM role
      exists; for self-managed AD the join-secret grant
      (``self_managed_ad_secret_arn``) was already mapped onto 2-iam.

    Multi-AZ (R13.1), BYOK MRK key (R13.6), standby replica (R13.2), and read
    replica (R13.15) need no extra wiring here: their gate fields map straight
    to the consuming ``5-rds`` / ``3-kms`` variables, so they render exactly
    when the intent carries them. License Manager enablement (R13.8) is handled
    in :func:`select_enabled_modules` (the module is included only when
    requested); its values are mapped in :data:`INTENT_FIELD_MAPPING`.

    Every variable written here is verified against the module's real declared
    variables, so a drifted name halts (R10.3/10.4).
    """

    def _force(module_name: str, variable: str, value: Any) -> None:
        declared = variable_index.get(module_name, set())
        if variable not in declared:
            raise FabricatedVariableError(module_name, variable, "optional_capability")
        rendered = modules.setdefault(
            module_name, RenderedModule(module=module_name, variables={})
        )
        rendered.variables[variable] = value

    # --- Db2 audit (R13.5/R13.10) ------------------------------------------
    if audit_requested(intent):
        # Create the audit IAM role + policy (scoped to the bucket) in 2-iam.
        _force("2-iam", "create_audit_role", True)
        # Pre-existing-bucket check (R13.10): the bucket MUST already exist, so
        # the composer reuses it (create_audit_bucket=false). The flag is left
        # explicit for the rare direct-module caller; the missing-bucket case is
        # rejected by the Intent_Validator before rendering (R13.10).
        bucket_exists = intent.get("audit_bucket_exists")
        # Default to reuse (False) — R13.10 requires the audit bucket to pre-exist.
        _force("2-iam", "create_audit_bucket", not _truthy_default_true(bucket_exists))

    # --- S3 restore integration (R13.7) ------------------------------------
    if s3_restore_requested(intent):
        _force("2-iam", "create_s3_role", True)
        bucket_exists = intent.get("s3_backup_bucket_exists")
        _force(
            "2-iam", "create_s3_backup_bucket", not _truthy_default_true(bucket_exists)
        )

    # --- Directory service: AWS Managed AD (R13.3) / self-managed AD (R13.4) -
    if aws_managed_ad_requested(intent) or self_managed_ad_requested(intent):
        # The directory IAM role RDS assumes is created in 2-iam for either mode
        # unless the customer says it already exists.
        if _truthy(intent.get("directory_role_exists")):
            _force("2-iam", "directory_role_exists", True)
        else:
            _force("2-iam", "create_directory_role", True)


def _truthy_default_true(value: Any) -> bool:
    """Interpret a pre-existing-bucket check result, defaulting to "exists".

    R13.10 requires the audit bucket to already exist (the validator rejects a
    missing one before rendering), so when the check result is absent the
    composer assumes the bucket pre-exists and reuses it. An explicit ``False``
    (the check ran and the bucket is absent) is honored as "does not exist".
    """
    if value is None:
        return True
    return _truthy(value)


# ---------------------------------------------------------------------------
# tfvars + root-module text rendering
# ---------------------------------------------------------------------------
def render_tfvars(rendered: RenderedModule) -> str:
    """Render a module's ``terraform.tfvars`` text from its collected variables.

    Variables are emitted in sorted order for deterministic output (R10:
    reproducible artifacts). Sensitive variables are annotated with a trailing
    ``# sensitive`` comment so a human reviewing the file is reminded, without
    changing the value Terraform reads.
    """
    lines = [
        f"# terraform.tfvars for module '{rendered.module}'",
        "# Rendered by rds-db2-deployer Terraform_Composer from the "
        "resolved Deployment_Intent.",
        "# Variable names are the module's own (tools/rds-db2-terraform/"
        f"{rendered.module}/variables.tf); none are fabricated (R10.3).",
        "",
    ]
    for name in sorted(rendered.variables):
        suffix = "  # sensitive" if name in rendered.sensitive_variables else ""
        lines.append(f"{name} = {format_hcl_value(rendered.variables[name])}{suffix}")
    return "\n".join(lines) + "\n"


def render_security_supplement(
    intent: Mapping[str, Any],
) -> str:
    """Render the ``security.tf`` supplement carrying the SSL-only ingress rule
    (R6.5) and documenting the always-on Db2 SSL parameters (R6.2).

    Why a supplement and not a module variable: none of the existing modules
    create the instance's security group or expose its ingress as a variable, and
    none expose arbitrary Db2 parameters as variables. Per R10.7 the composer
    renders these invariants directly here rather than fabricate a non-existent
    module variable (R10.3/10.4). The rule attaches to the existing security
    group named by the intent's ``vpc_security_group_ids`` and opens ONLY the SSL
    service port 50443 (TCP) from the intent's specified sources; the non-SSL TCP
    listener ``port`` is never opened, and no other port is opened (R6.5).

    Ingress sources come from the intent's ``ingress_cidrs`` /
    ``ingress_cidr_blocks`` (CIDR ranges) and ``ingress_source_security_group_ids``
    / ``source_security_group_ids`` (source SGs). When neither is supplied, no
    ingress rule is emitted (no source = no open port), the most restrictive
    posture, which never widens access.
    """
    sg_ids = intent.get("vpc_security_group_ids")
    security_group_id = _first(sg_ids) if _is_supplied(sg_ids) else None

    cidrs: list[str] = []
    for field_name in INGRESS_CIDR_FIELDS:
        value = intent.get(field_name)
        if isinstance(value, (list, tuple)):
            cidrs.extend(str(c) for c in value)

    source_sgs: list[str] = []
    for field_name in INGRESS_SOURCE_SG_FIELDS:
        value = intent.get(field_name)
        if isinstance(value, (list, tuple)):
            source_sgs.extend(str(s) for s in value)

    lines = [
        "# security.tf - SSL-only ingress for RDS for Db2 (R6.5), rendered by the",
        "# rds-db2-deployer Terraform_Composer for EVERY deployment.",
        "#",
        f"# Only the Db2 SSL service port {SSL_SERVICE_PORT} (TCP) is opened, and only",
        "# from the sources named in the Deployment_Intent. The non-SSL TCP listener",
        "# 'port' (db2_port, dormant under DB2COMM=SSL) is intentionally NOT opened,",
        "# and no other port is opened (R6.5).",
        "#",
        f"# The Db2 SSL parameters themselves - DB2COMM=SSL and ssl_svcename="
        f"{SSL_SERVICE_PORT}",
        "# (R6.2) - are rendered into the 4-parameter-group module's parameter group",
        "# (always present), not here.",
        "",
    ]

    if security_group_id is None:
        lines.append(
            "# No security group id supplied in the Deployment_Intent "
            "(vpc_security_group_ids);"
        )
        lines.append(
            "# the SSL-only ingress rule cannot be attached. No ingress is opened."
        )
        return "\n".join(lines) + "\n"

    if not cidrs and not source_sgs:
        lines.append(
            "# No ingress sources supplied (ingress_cidrs / "
            "ingress_source_security_group_ids);"
        )
        lines.append(
            "# the most restrictive posture is rendered: no ingress rule, no open "
            "port (R6.5)."
        )
        return "\n".join(lines) + "\n"

    if cidrs:
        lines.append('resource "aws_vpc_security_group_ingress_rule" "db2_ssl_cidr" {')
        lines.append(f"  for_each          = toset({format_hcl_value(cidrs)})")
        lines.append(f"  security_group_id = {format_hcl_value(security_group_id)}")
        lines.append("  cidr_ipv4         = each.value")
        lines.append(f"  from_port         = {SSL_SERVICE_PORT}")
        lines.append(f"  to_port           = {SSL_SERVICE_PORT}")
        lines.append('  ip_protocol       = "tcp"')
        lines.append(
            '  description       = "Db2 SSL service port (DB2COMM=SSL); SSL-only (R6.5)"'
        )
        lines.append("}")
        lines.append("")

    if source_sgs:
        lines.append('resource "aws_vpc_security_group_ingress_rule" "db2_ssl_sg" {')
        lines.append(
            f"  for_each                     = toset({format_hcl_value(source_sgs)})"
        )
        lines.append(
            f"  security_group_id            = {format_hcl_value(security_group_id)}"
        )
        lines.append("  referenced_security_group_id = each.value")
        lines.append(f"  from_port                    = {SSL_SERVICE_PORT}")
        lines.append(f"  to_port                      = {SSL_SERVICE_PORT}")
        lines.append('  ip_protocol                  = "tcp"')
        lines.append(
            '  description                  = "Db2 SSL service port (DB2COMM=SSL); SSL-only (R6.5)"'
        )
        lines.append("}")
        lines.append("")

    return "\n".join(lines) + "\n"


def _relative_source(output_dir: Path, modules_root: Path, module: str) -> str:
    """Compute the relative ``source`` path from the rendered root module to a
    reused module directory.

    This is the AIRGAP / no-egress fallback (``source_mode="local"``): it wires
    the emitted module ``source`` to a vendored/sibling module tree on local
    disk (R10.1: reference modules by source). The production default emits a
    pinned git ref instead (see :func:`_git_source`).
    """
    target = (modules_root / module).resolve()
    rel = os.path.relpath(target, output_dir.resolve())
    # Terraform module sources must start with ./ or ../ to be treated as a
    # local path rather than a registry address.
    if not rel.startswith((".", os.sep)):
        rel = "./" + rel
    return rel.replace(os.sep, "/")


def _git_source(module: str, *, repo: str, subdir: str, ref: str) -> str:
    """Compute the pinned git ``source`` for a reused module (#1, the production
    source of truth).

    Emits a Terraform git module source of the form
    ``git::<repo>//<subdir>/<module>?ref=<ref>`` so the rendered root pulls the
    module from GitHub at an immutable tag. ``ref`` MUST be a tag (not a branch)
    for reproducibility; the caller is responsible for passing a tag.
    """
    subdir = subdir.strip("/")
    module_path = f"{subdir}/{module}" if subdir else module
    return f"git::{repo}//{module_path}?ref={ref}"


def _module_source(
    module: str,
    *,
    source_mode: str,
    output_dir: Path,
    modules_root: Path,
    git_repo: str,
    git_subdir: str,
    module_ref: str,
) -> str:
    """Resolve the emitted ``source`` for ``module`` per the selected source mode.

    ``source_mode="git"`` (default) -> pinned git ref (production source of
    truth); ``source_mode="local"`` -> relative local path (airgap fallback).
    """
    if source_mode == SOURCE_MODE_LOCAL:
        return _relative_source(output_dir, modules_root, module)
    if source_mode == SOURCE_MODE_GIT:
        return _git_source(
            module, repo=git_repo, subdir=git_subdir, ref=module_ref
        )
    raise RenderingError(
        f"unknown source_mode {source_mode!r}; expected "
        f"{SOURCE_MODE_GIT!r} (pinned git ref) or {SOURCE_MODE_LOCAL!r} "
        "(relative local/vendored path, airgap fallback)."
    )


@dataclass(frozen=True)
class BackendConfig:
    """Per-deployment remote-state backend wiring (#2).

    Each deployment gets its OWN S3 state key derived from the deployment name
    (the ``db_instance_identifier``), so provisioning N instances never collides
    on a single state file and re-applying the same deployment is idempotent
    against its own prior state. The S3 bucket + DynamoDB lock table are the
    ones bootstrapped by ``0-backend-setup``.

    Attributes:
        bucket: the S3 state bucket name (from ``0-backend-setup``). Not part of
            an intent; supplied by the operator (defaults to a TODO placeholder).
        region: the bucket's region (defaults to the deployment region).
        lock_table: the DynamoDB lock table name (``0-backend-setup`` default).
        key: the per-deployment state key. Derived from the deployment name when
            built via :meth:`for_deployment`.
        encrypt: whether to encrypt the state object at rest (always True).
    """

    bucket: str
    region: str
    key: str
    lock_table: str = DEFAULT_LOCK_TABLE
    encrypt: bool = True

    @staticmethod
    def state_key_for(deployment_name: str) -> str:
        """Build the per-deployment state key ``rds-db2/<name>/terraform.tfstate``.

        Falls back to ``unnamed`` when the deployment name is empty so the key is
        always well-formed; a real deployment always carries a resolved
        ``db_instance_identifier`` by the time it is rendered.
        """
        name = (deployment_name or "").strip() or "unnamed"
        return f"{STATE_KEY_PREFIX}/{name}/terraform.tfstate"

    @classmethod
    def for_deployment(
        cls,
        deployment_name: str,
        *,
        region: str,
        bucket: Optional[str] = None,
        lock_table: Optional[str] = None,
    ) -> "BackendConfig":
        """Construct a backend config whose ``key`` is unique to this deployment."""
        return cls(
            bucket=bucket or DEFAULT_STATE_BUCKET,
            region=region,
            key=cls.state_key_for(deployment_name),
            lock_table=lock_table or DEFAULT_LOCK_TABLE,
        )


#: 5-rds inputs that are satisfied by a freshly created sibling module's output
#: when the corresponding resource is on the CREATE path (the intent field was
#: left blank, R10.6). Maps the 5-rds variable -> (root module block name, that
#: module's output name, the create-path module directory). When the create
#: module is referenced in the rendered root AND the 5-rds value is not supplied,
#: the instance consumes ``module.<block>.<output>`` instead of an empty literal
#: (without this the single-root apply would leave 5-rds with a blank required
#: input — e.g. db_subnet_group_name/monitoring_role_arn have no module default).
#: When the resource is reused (the intent supplied an existing identifier) the
#: create module is skipped, the value is a non-empty literal, and no wiring is
#: emitted — so reuse and create are both correct from one table.
RDS_CREATED_INPUT_WIRING: dict[str, tuple[str, str, str]] = {
    "kms_key_arn": ("kms", "kms_key_arn", "3-kms"),
    "db_subnet_group_name": ("networking", "db_subnet_group_name", "1-networking"),
    "monitoring_role_arn": ("iam", "monitoring_role_arn", "2-iam"),
    "parameter_group_name": (
        "parameter_group",
        "parameter_group_name",
        "4-parameter-group",
    ),
}

#: The 5-rds variable carrying the managed master-user-secret CMK. Left blank it
#: mirrors the storage CMK (created or reused) so the CMK-everywhere invariant
#: (R6.10) holds without a second key.
MASTER_SECRET_CMK_VAR = "master_user_secret_kms_key_id"


def rds_created_input_overrides(
    rendered: Optional["RenderedModule"], referenced: list[str]
) -> dict[str, str]:
    """Raw-HCL wiring expressions for 5-rds inputs that consume freshly created
    sibling-module outputs on the create path (R10.6).

    Returns ``{5-rds variable: raw HCL expression}`` (e.g.
    ``{"kms_key_arn": "module.kms.kms_key_arn"}``). An entry is produced only
    when the create module is referenced in the rendered root AND the intent did
    not supply an existing identifier for that resource (``_is_supplied`` is
    False). Reuse (a supplied value) yields no entry, so the value is rendered as
    its literal instead. The expressions are emitted verbatim (never passed
    through :func:`format_hcl_value`, which would quote them).
    """
    vars_ = (rendered.variables if rendered else {}) or {}
    overrides: dict[str, str] = {}
    for var_name, (block, output, create_dir) in RDS_CREATED_INPUT_WIRING.items():
        if create_dir in referenced and not _is_supplied(vars_.get(var_name)):
            overrides[var_name] = f"module.{block}.{output}"
    # master_user_secret_kms_key_id mirrors the storage CMK when left blank and
    # the master password is RDS-managed (the only mode that uses the secret).
    managed = vars_.get("manage_master_user_password", True)
    if managed and not _is_supplied(vars_.get(MASTER_SECRET_CMK_VAR)):
        if "3-kms" in referenced:
            overrides[MASTER_SECRET_CMK_VAR] = "module.kms.kms_key_arn"
        elif _is_supplied(vars_.get("kms_key_arn")):
            overrides[MASTER_SECRET_CMK_VAR] = format_hcl_value(vars_["kms_key_arn"])
    return overrides


def render_root_module(
    enabled_modules: list[str],
    *,
    output_dir: Path,
    modules_root: Path,
    source_mode: str = DEFAULT_SOURCE_MODE,
    git_repo: str = DEFAULT_GIT_REPO,
    git_subdir: str = DEFAULT_GIT_SUBDIR,
    module_ref: str = DEFAULT_MODULE_REF,
    backend: Optional[BackendConfig] = None,
    region: str = "us-east-1",
    replica_region: Optional[str] = None,
    module_variables: Optional[Mapping[str, "RenderedModule"]] = None,
    sensitive_root_names: Optional[Mapping[tuple, str]] = None,
) -> str:
    """Render the root-module ``main.tf`` that references the existing modules.

    Per R10.1 the root references the modules ``0-backend-setup`` through
    ``6-license-manager``: ``0-backend-setup`` (the remote-state bootstrap) is
    always referenced, followed by every intent-driven enabled module. The body
    intentionally keeps each module block minimal: a ``source`` and a pointer to
    the per-module ``terraform.tfvars`` that carries the inputs.

    Two render-time wirings layer onto that composition:

    * **#1 module source.** ``source_mode="git"`` (default) emits a pinned git
      ref (``git::<repo>//<subdir>/<module>?ref=<tag>``) so the rendered root
      pulls the reused modules from GitHub at an immutable tag — the source of
      truth that stays in sync with upstream fixes. ``source_mode="local"`` emits
      a relative path to a vendored/sibling module tree for AIRGAP / no-egress
      environments.
    * **#2 per-deployment remote state.** When a :class:`BackendConfig` is
      supplied a ``backend "s3"`` block is rendered with a state ``key`` unique
      to this deployment, so multiple instances never share one state file and
      re-applying a deployment is idempotent against its own state.

    It remains a composition over the existing modules, never a parallel
    imperative deployer (R10.1).
    """
    # The deployment root CONSUMES the remote-state backend (the `backend "s3"`
    # block below), which 0-backend-setup bootstraps ONCE. It must NOT reference
    # 0-backend-setup as a child module — doing so would try to re-create the
    # state bucket and requires a state_bucket_name that is not part of a
    # deployment intent. So 0-backend-setup is excluded from the referenced set.
    referenced = [m for m in enabled_modules if m != "0-backend-setup"]
    lines = [
        "# Root module rendered by rds-db2-deployer Terraform_Composer.",
        "# It REUSES the existing RDS-Db2-Terraform modules (R10.1); it is not a",
        "# new imperative deployer. Each module's inputs are supplied by the",
        "# sibling <module>/terraform.tfvars rendered alongside this file from the",
        "# resolved Deployment_Intent.",
        "",
        "terraform {",
        '  required_version = ">= 1.0"',
        "  required_providers {",
        '    aws = {',
        '      source  = "hashicorp/aws"',
        '      version = "~> 5.0"',
        "    }",
        "  }",
    ]
    if backend is not None:
        # #2: per-deployment S3 remote state. The bucket/lock table come from
        # 0-backend-setup; the key is unique per deployment so N instances each
        # get isolated state and re-apply is idempotent. Operator may still
        # override any field via `terraform init -backend-config=...`.
        lines.extend(
            [
                "",
                "  # Per-deployment remote state (#2): a unique state key per",
                "  # deployment so multiple instances never collide on one state",
                "  # file and re-applying this deployment is idempotent. Bucket +",
                "  # lock table are bootstrapped by 0-backend-setup.",
                '  backend "s3" {',
                f'    bucket         = "{backend.bucket}"',
                f'    key            = "{backend.key}"',
                f'    region         = "{backend.region}"',
                f'    dynamodb_table = "{backend.lock_table}"',
                f"    encrypt        = {format_hcl_value(backend.encrypt)}",
                "  }",
            ]
        )
        if backend.bucket == DEFAULT_STATE_BUCKET:
            lines.append(
                "  # TODO(operator): replace the placeholder bucket above with the"
            )
            lines.append(
                "  # real 0-backend-setup state bucket (or pass via -backend-config)."
            )
    lines.append("}")
    lines.append("")
    # Provider configuration the rendered root must supply for a real apply / CI.
    # The reused modules self-configure their default `aws` provider, but 5-rds
    # ALSO declares `configuration_aliases = [aws.replica]`, so the caller must
    # always pass an `aws.replica` provider — even with no standby (it then
    # harmlessly mirrors the primary region). Without this the root fails
    # `terraform init` with "Missing required provider configuration".
    _replica_region = replica_region or region
    lines.extend([
        'provider "aws" {',
        f'  region = "{region}"',
        "}",
        "",
        'provider "aws" {',
        '  alias  = "replica"',
        f'  region = "{_replica_region}"',
        "}",
        "",
    ])
    if source_mode == SOURCE_MODE_GIT:
        lines.append(
            f"# Module sources: pinned git ref {module_ref!r} on {git_repo}"
        )
        lines.append(
            "# (#1 source of truth). For airgap/no-egress, re-render with"
        )
        lines.append("# source_mode='local' to emit relative vendored paths.")
        lines.append("")
    else:
        lines.append(
            "# Module sources: relative local/vendored paths (#1 airgap fallback)."
        )
        lines.append("")
    for module in referenced:
        source = _module_source(
            module,
            source_mode=source_mode,
            output_dir=output_dir,
            modules_root=modules_root,
            git_repo=git_repo,
            git_subdir=git_subdir,
            module_ref=module_ref,
        )
        block_name = module.split("-", 1)[1].replace("-", "_")
        lines.append(f'module "{block_name}" {{')
        lines.append(f'  source = "{source}"')
        if module == "5-rds":
            # 5-rds requires the caller to pass the aws.replica configuration
            # alias it declares (used only for a cross-region standby).
            lines.append("  providers = {")
            lines.append("    aws.replica = aws.replica")
            lines.append("  }")
        # Wire the module's inputs directly as block arguments so a single
        # `terraform apply` of this root drives the child modules (Terraform does
        # not auto-load a child module's sibling terraform.tfvars). The same
        # values are also written to <module>/terraform.tfvars as the committed
        # record and for the policy gate.
        rendered = module_variables.get(module) if module_variables else None
        # Create-path wiring (R10.6): 5-rds inputs whose resource the intent left
        # blank consume the freshly created sibling module's output instead of an
        # empty literal. Reused resources (supplied) produce no override.
        overrides = (
            rds_created_input_overrides(rendered, referenced)
            if module == "5-rds"
            else {}
        )
        if rendered is not None and rendered.variables:
            for name in sorted(rendered.variables):
                value = rendered.variables[name]
                if name in overrides:
                    # Raw HCL expression (e.g. module.kms.kms_key_arn) — emitted
                    # verbatim, never quoted by format_hcl_value.
                    lines.append(f"  {name} = {overrides[name]}")
                    continue
                root = (sensitive_root_names or {}).get((module, name))
                if root is not None:
                    # Sensitive input -> reference a sensitive root variable; the
                    # literal value lives in the root terraform.tfvars, never here.
                    lines.append(f"  {name} = var.{root}")
                else:
                    lines.append(f"  {name} = {format_hcl_value(value)}")
            # Wire create-path inputs that 5-rds requires but were ABSENT from the
            # collected variables (no literal emitted above), so a required input
            # with no module default (db_subnet_group_name/monitoring_role_arn) is
            # never left unset on the create path.
            for name in sorted(overrides):
                if name not in rendered.variables:
                    lines.append(f"  {name} = {overrides[name]}")
        else:
            lines.append(
                f"  # inputs: see {module}/terraform.tfvars "
                "(rendered from the Deployment_Intent)"
            )
        lines.append("}")
        lines.append("")
    # Declare the sensitive root variables referenced above (values supplied by
    # the root terraform.tfvars), so no Sensitive_Value appears literally in
    # main.tf (R12.1 / R15).
    if sensitive_root_names:
        lines.append("# Sensitive inputs are passed via these root variables; their")
        lines.append("# values live in the root terraform.tfvars, not in this file.")
        for root in sorted(set(sensitive_root_names.values())):
            lines.append(f'variable "{root}" {{')
            lines.append("  type      = string")
            lines.append("  sensitive = true")
            lines.append("}")
            lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


@dataclass
class RenderResult:
    """The complete set of rendered Terraform artifacts.

    Attributes:
        files: ``{relative path: file content}`` for every rendered file
            (the root ``main.tf`` and each ``<module>/terraform.tfvars``).
        enabled_modules: the modules tfvars were emitted for, in apply order.
        modules: the collected per-module variables (for assertions/inspection).
    """

    files: dict[str, str]
    enabled_modules: list[str]
    modules: dict[str, RenderedModule]


def render_terraform(
    intent: Mapping[str, Any],
    *,
    modules_root: Union[str, Path, None] = None,
    output_dir: Union[str, Path, None] = None,
    source_mode: str = DEFAULT_SOURCE_MODE,
    git_repo: str = DEFAULT_GIT_REPO,
    git_subdir: str = DEFAULT_GIT_SUBDIR,
    module_ref: str = DEFAULT_MODULE_REF,
    backend: Optional[BackendConfig] = None,
    emit_backend: bool = True,
    state_bucket: Optional[str] = None,
    lock_table: Optional[str] = None,
) -> RenderResult:
    """Render the root module + per-module tfvars from a resolved intent (R10).

    Steps:
      1. Ground the mapping table against the modules' real ``variables.tf``
         (R10.3) so a drifted target fails before any file is produced.
      2. Map the intent into per-module variables, halting on any unmapped field
         (R10.4) or fabricated variable name (R10.3).
      3. Select the enabled modules (core + populated).
      4. Render the root ``main.tf`` and a ``terraform.tfvars`` per enabled
         module (R10.2). The root wires:
           * **#1 module source** — a pinned git ref by default
             (``source_mode="git"``), the source of truth that stays in sync with
             upstream fixes; ``source_mode="local"`` emits relative vendored
             paths for airgap/no-egress.
           * **#2 per-deployment remote state** — a ``backend "s3"`` block whose
             ``key`` is unique to this deployment (derived from
             ``db_instance_identifier``) so N instances never share state and
             re-apply is idempotent.

    Args:
        intent: a resolved, schema-valid ``Deployment_Intent``.
        modules_root: path to the RDS-Db2-Terraform modules. ALWAYS used to parse
            ``variables.tf`` for grounding; also used as the emitted ``source``
            when ``source_mode="local"``. Defaults to the sibling directory.
        output_dir: where the root module is rooted, used to compute relative
            module ``source`` paths in local mode (defaults to
            ``templates/terraform/``).
        source_mode: ``"git"`` (default) for the pinned git source of truth or
            ``"local"`` for the airgap/vendored relative-path fallback (#1).
        git_repo, git_subdir, module_ref: the git source of truth and the TAG it
            is pinned to (#1). ``module_ref`` MUST be a tag, never a branch.
        backend: an explicit :class:`BackendConfig` for the rendered ``backend
            "s3"`` block (#2). When omitted and ``emit_backend`` is True, one is
            derived per deployment from the intent's ``db_instance_identifier``.
        emit_backend: when False, no backend block is rendered (the deployment
            uses local/default state — used by standalone validate harnesses).
        state_bucket, lock_table: overrides for the derived backend's S3 bucket
            and DynamoDB lock table (both bootstrapped by ``0-backend-setup``).

    Returns:
        A :class:`RenderResult` whose ``files`` map is ready to write to disk.

    Raises:
        UnmappedIntentFieldError, FabricatedVariableError, RenderingError.
    """
    root = Path(modules_root) if modules_root is not None else DEFAULT_MODULES_ROOT
    out = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR

    variable_index = load_module_variable_index(root)
    _assert_targets_grounded(variable_index)

    populated = collect_module_variables(intent, variable_index=variable_index)

    # Reuse/create dispositions (R10.5/10.6) drive both the security-invariant
    # enforcement (which only pins create-path module flags that are rendered)
    # and the enabled-module selection below.
    dispositions = resolve_module_dispositions(intent)

    # Force the always-on security invariants into the collected variables
    # (R6.1/R6.3) before selecting enabled modules, so a create-path module that
    # only the invariants populate (e.g. 1-networking publicly_accessible) is
    # still rendered.
    enforce_security_invariants(
        intent, populated, dispositions, variable_index=variable_index
    )

    # Gate + complete the optional capabilities (R13): each capability's extra
    # wiring (audit/S3 role + bucket create-vs-reuse flags, directory role) is
    # added only when the resolved intent requests it. The direct gate fields
    # (multi_az, enable_audit, create_standby_replica, ...) were already mapped
    # in collect_module_variables; this fills in what the table cannot express.
    render_optional_capabilities(intent, populated, variable_index=variable_index)

    enabled = select_enabled_modules(intent, populated)

    # Single-root apply needs each child module's inputs wired as block arguments
    # (Terraform does not auto-load a child module's sibling tfvars). Sensitive
    # inputs are referenced via root variables (declared sensitive) whose values
    # live ONLY in the root terraform.tfvars, so no Sensitive_Value appears as a
    # literal in main.tf (R12.1 / R15).
    sensitive_root_names: dict[tuple, str] = {}
    sensitive_root_values: dict[str, Any] = {}
    for _mname, _rm in populated.items():
        if _mname == "0-backend-setup":
            continue
        _block = _mname.split("-", 1)[1].replace("-", "_")
        for _vname in _rm.sensitive_variables:
            if _vname in _rm.variables:
                _root = f"{_block}_{_vname}"
                sensitive_root_names[(_mname, _vname)] = _root
                sensitive_root_values[_root] = _rm.variables[_vname]

    # #2: derive a per-deployment backend keyed on the deployment identifier so
    # multiple instances get isolated state. An explicit `backend` wins; set
    # `emit_backend=False` to omit the block entirely (e.g. validate harnesses).
    if backend is None and emit_backend:
        deployment_name = str(intent.get("db_instance_identifier") or "")
        backend = BackendConfig.for_deployment(
            deployment_name,
            region=str(intent.get("region") or ""),
            bucket=state_bucket,
            lock_table=lock_table,
        )

    files: dict[str, str] = {
        "main.tf": render_root_module(
            enabled,
            output_dir=out,
            modules_root=root,
            source_mode=source_mode,
            git_repo=git_repo,
            git_subdir=git_subdir,
            module_ref=module_ref,
            backend=backend,
            region=str(intent.get("region") or "us-east-1"),
            replica_region=(intent.get("standby_replica_region") or None),
            module_variables=populated,
            sensitive_root_names=sensitive_root_names,
        ),
        # The SSL-only ingress (R6.5) is rendered for every deployment as a
        # supplement alongside the root module; the Db2 SSL parameters (R6.2)
        # ride in the always-present 4-parameter-group parameter group.
        "security.tf": render_security_supplement(intent),
    }
    for module in enabled:
        rendered = populated.get(module) or RenderedModule(module=module, variables={})
        files[f"{module}/terraform.tfvars"] = render_tfvars(rendered)

    # Root terraform.tfvars: supplies the sensitive root variables referenced in
    # main.tf. Auto-loaded by Terraform; a tfvars surface so it may carry the
    # Sensitive_Values that must never appear in main.tf (R12.1 / R15).
    if sensitive_root_values:
        sv_lines = [
            "# Sensitive deployment inputs (auto-loaded). Referenced as sensitive",
            "# root variables in main.tf so they never appear there as literals.",
            "",
        ]
        for root in sorted(sensitive_root_values):
            sv_lines.append(
                f"{root} = {format_hcl_value(sensitive_root_values[root])}  # sensitive"
            )
        files["terraform.tfvars"] = "\n".join(sv_lines) + "\n"

    return RenderResult(files=files, enabled_modules=enabled, modules=populated)


def write_render_result(result: RenderResult, output_dir: Union[str, Path]) -> list[Path]:
    """Write a :class:`RenderResult` to ``output_dir`` and return the paths
    written. Creates parent directories as needed.
    """
    out = Path(output_dir)
    written: list[Path] = []
    for rel_path, content in result.files.items():
        target = out / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        written.append(target)
    return written
