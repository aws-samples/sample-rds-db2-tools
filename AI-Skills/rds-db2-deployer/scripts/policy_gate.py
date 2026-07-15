"""The Policy_Gate for the rds-db2-provision-skill (R12.3).

The Policy_Gate is the policy-as-code stage of the GitOps flow: after the
``Terraform_Composer`` renders the configuration and ``terraform plan`` posts
its (masked) plan, the gate runs a small set of **discrete pass/fail** checks
over the rendered Terraform (and, optionally, over a parsed ``terraform plan``
text) that MUST all pass before merge-to-apply (R12.3). The checks are the
defense-in-depth restatement of the security/governance invariants the
validator and composer already enforce — re-asserted here so a drifted or
hand-edited config can never reach ``apply``:

1. :func:`check_mrk_cmk_encryption` — storage encryption is on AND the storage
   CMK is a customer-managed multi-region key (MRK), never an AWS-owned/managed
   default key (R6.1).
2. :func:`check_db2comm_ssl` — ``DB2COMM=SSL`` and ``ssl_svcename=50443`` are
   present (R6.2).
3. :func:`check_non_public_absent_ack` — ``publicly_accessible=false`` unless a
   public-access acknowledgement signal is present (R6.3).
4. :func:`check_mandatory_tags` — all five mandatory tags
   (``created_by``, ``generation_model``, ``Project``, ``Environment``,
   ``Owner``) are present and non-empty (R14).
5. :func:`check_ibm_ids_present` — ``ibm_customer_id`` and ``ibm_site_id`` are
   present for every edition (R7/R8).

Design notes:

* Each check returns a :class:`PolicyResult` with a stable ``check`` name, a
  ``passed`` boolean, and a human-readable ``message`` — the same shape for
  every check so the PR-posting/merge-gating logic in task 11.2 and the
  property tests in task 11.3 can treat the gate uniformly.
* :func:`evaluate_policies` accepts either a :class:`RenderResult` from the
  composer or a plain ``{relative path: file content}`` mapping, so the gate is
  testable without invoking the composer. It optionally takes ``plan_output``
  (the stdout of ``terraform plan``); :func:`parse_terraform_plan` turns that
  text into a :class:`PlanSummary` so the same checks can also operate on a
  plan with **no real Terraform/AWS required**.
* The MRK / AWS-owned-key markers are imported from the validator so the gate
  and the validator agree on what "MRK" and "AWS-owned key" mean.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Iterable, Mapping, Optional, Union

try:  # Share the MRK / AWS-owned-key definitions with the validator (R6.11).
    from scripts.validate_intent import AWS_OWNED_KEY_MARKERS, MRK_KEY_ID_PREFIX
except ImportError:  # Fall back when scripts/ is directly on sys.path.
    from validate_intent import AWS_OWNED_KEY_MARKERS, MRK_KEY_ID_PREFIX


# ---------------------------------------------------------------------------
# Invariant constants (mirrors of the composer/validator, restated here)
# ---------------------------------------------------------------------------

#: The Db2 SSL service port (R6.2) — the only port that accepts connections.
SSL_SERVICE_PORT = 50443

#: The Db2 communication-protocol parameter and its fixed SSL-only value (R6.2).
DB2COMM_PARAMETER = "DB2COMM"
DB2COMM_SSL_VALUE = "SSL"

#: The Db2 SSL service-name parameter, fixed to the SSL service port (R6.2).
SSL_SVCENAME_PARAMETER = "ssl_svcename"

#: The five mandatory tags, by their canonical (rendered) tag key (R14). The
#: composer surfaces ``Project``/``Owner``/``Environment`` through the module
#: ``tag``/``owner``/``environment`` variables; the mapping from those variable
#: names to these canonical keys is :data:`_TAG_VARIABLE_TO_KEY`.
MANDATORY_TAG_KEYS: tuple[str, ...] = (
    "created_by",
    "generation_model",
    "Project",
    "Environment",
    "Owner",
)

#: Maps a 5-rds module tag *variable* name to the canonical mandatory tag key it
#: carries, so the gate can read the mandatory tags out of the rendered tfvars
#: (which use the module's own variable names, not the tag keys).
_TAG_VARIABLE_TO_KEY: dict[str, str] = {
    "created_by": "created_by",
    "generation_model": "generation_model",
    "tag": "Project",
    "owner": "Owner",
    "environment": "Environment",
}

#: The check names — stable identifiers so callers (task 11.2/11.3) can key on a
#: specific gate result without string-matching the message.
CHECK_MRK_CMK = "mrk_cmk_encryption"
CHECK_DB2COMM_SSL = "db2comm_ssl"
CHECK_NON_PUBLIC = "non_public_absent_ack"
CHECK_MANDATORY_TAGS = "mandatory_tags"
CHECK_IBM_IDS = "ibm_ids_present"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyResult:
    """The outcome of a single policy check (R12.3, discrete pass/fail).

    Attributes:
        check: the stable check name (one of the ``CHECK_*`` constants).
        passed: ``True`` when the check is satisfied, ``False`` otherwise.
        message: a human-readable explanation of the pass/fail, naming the
            invariant and the evidence that drove the decision.
    """

    check: str
    passed: bool
    message: str


@dataclass
class PolicyGateReport:
    """The aggregate of every policy check over one rendered configuration.

    ``ok`` is ``True`` only when *every* check passed (R12.3: all gates must
    pass before merge-to-apply). The report keeps the individual results so the
    PR/merge logic can post a per-check breakdown.
    """

    results: list[PolicyResult] = dataclass_field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[PolicyResult]:
        return [r for r in self.results if not r.passed]

    def by_name(self, check: str) -> Optional[PolicyResult]:
        for r in self.results:
            if r.check == check:
                return r
        return None


# ---------------------------------------------------------------------------
# terraform plan parsing (no real Terraform/AWS required)
# ---------------------------------------------------------------------------


@dataclass
class PlanSummary:
    """A structured view of ``terraform plan`` stdout text.

    Captures the parts the gate needs without binding to a live plan:

    Attributes:
        add/change/destroy: the ``Plan: X to add, Y to change, Z to destroy``
            counts (``None`` when the summary line is absent).
        resources: one entry per planned resource — ``{"type", "name",
            "attributes", "tags"}``.
        attributes: a merged ``{attribute: value}`` view across all resources
            (last write wins) for simple single-instance lookups like
            ``storage_encrypted`` / ``publicly_accessible`` / ``kms_key_id``.
        tags: a merged tag map (from every ``tags``/``tags_all`` block).
        parameters: a merged DB-parameter-group ``{name: value}`` map (from the
            ``parameter { name = .. value = .. }`` blocks), e.g.
            ``{"DB2COMM": "SSL", "ssl_svcename": "50443"}``.
    """

    add: Optional[int] = None
    change: Optional[int] = None
    destroy: Optional[int] = None
    resources: list[dict] = dataclass_field(default_factory=list)
    attributes: dict[str, Any] = dataclass_field(default_factory=dict)
    tags: dict[str, str] = dataclass_field(default_factory=dict)
    parameters: dict[str, str] = dataclass_field(default_factory=dict)

    def attribute(self, name: str) -> Any:
        return self.attributes.get(name)


# A line declaring a resource block: `+ resource "aws_db_instance" "this" {`
_RESOURCE_RE = re.compile(r'resource\s+"([^"]+)"\s+"([^"]+)"\s*\{')

# An `name = value` attribute line, tolerating the leading +/-/~ change marker
# and an optional `-> newvalue` (we keep the right-hand/new value).
_ATTR_RE = re.compile(
    r'^\s*[+\-~]?\s*"?([A-Za-z0-9_.\-]+)"?\s*=\s*(.+?)\s*$'
)

# The plan summary line.
_SUMMARY_RE = re.compile(
    r'Plan:\s*(\d+)\s+to add,\s*(\d+)\s+to change,\s*(\d+)\s+to destroy'
)


def _unquote(token: str) -> str:
    """Strip a trailing inline comment, surrounding quotes, and a trailing
    comma from a plan/tfvars value token, returning the bare scalar text."""
    token = token.strip()
    # Drop a `-> newvalue` arrow (keep the new value side already handled by the
    # regex's non-greedy capture; here we just tidy any residue).
    token = token.rstrip(",").strip()
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1]
    return token


def parse_terraform_plan(plan_text: str) -> PlanSummary:
    """Parse ``terraform plan`` stdout into a :class:`PlanSummary` (R12.3).

    The parser is intentionally tolerant: it walks the plan line by line,
    tracking whether it is inside a resource body, a ``tags``/``tags_all``
    block, or a ``parameter`` block, and extracts the attribute/tag/parameter
    values plus the plan summary counts. It does not require real Terraform —
    it operates on the textual plan, so the gate is testable with sample plan
    text.
    """
    summary = PlanSummary()

    current_resource: Optional[dict] = None
    # A small context stack of ("tags", indent) / ("parameter", indent, buffer)
    # so nested tag and parameter blocks are captured precisely.
    in_tags = False
    in_parameter = False
    param_name: Optional[str] = None
    param_value: Optional[str] = None

    for raw_line in plan_text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        # Plan summary counts.
        m = _SUMMARY_RE.search(line)
        if m:
            summary.add = int(m.group(1))
            summary.change = int(m.group(2))
            summary.destroy = int(m.group(3))
            continue

        # Resource header — open a new resource record.
        rm = _RESOURCE_RE.search(line)
        if rm:
            current_resource = {
                "type": rm.group(1),
                "name": rm.group(2),
                "attributes": {},
                "tags": {},
            }
            summary.resources.append(current_resource)
            in_tags = False
            in_parameter = False
            continue

        # Enter / exit a tags block.
        if re.match(r'^\s*[+\-~]?\s*(tags|tags_all)\s*=\s*\{', line):
            in_tags = True
            continue
        # Enter a parameter block.
        if re.match(r'^\s*[+\-~]?\s*parameter\s*\{', line):
            in_parameter = True
            param_name = None
            param_value = None
            continue

        # Close a block on a lone `}`.
        if stripped in ("}", "},"):
            if in_parameter:
                if param_name is not None:
                    summary.parameters[param_name] = param_value or ""
                in_parameter = False
                param_name = None
                param_value = None
            elif in_tags:
                in_tags = False
            continue

        am = _ATTR_RE.match(line)
        if not am:
            continue
        key = am.group(1)
        value = _unquote(am.group(2))

        if in_parameter:
            if key == "name":
                param_name = value
            elif key == "value":
                param_value = value
            continue

        if in_tags:
            summary.tags[key] = value
            if current_resource is not None:
                current_resource["tags"][key] = value
            continue

        # Plain attribute on the current resource (and the merged view).
        if current_resource is not None:
            current_resource["attributes"][key] = value
        summary.attributes[key] = value

    return summary


# ---------------------------------------------------------------------------
# Evidence assembly (rendered TF and/or a parsed plan)
# ---------------------------------------------------------------------------


@dataclass
class PolicyEvidence:
    """Everything a policy check needs about one rendered configuration.

    Attributes:
        text: every rendered Terraform file concatenated, for substring/token
            checks (e.g. the Db2 SSL parameters documented in ``security.tf``).
        module_vars: ``{module: {variable: value}}`` parsed from each rendered
            ``<module>/terraform.tfvars`` (or taken structurally from a
            :class:`RenderResult`).
        tags: the resolved mandatory-tag map keyed by canonical key
            (``created_by``/``generation_model``/``Project``/``Environment``/
            ``Owner``), derived from the 5-rds tag variables and/or the plan.
        plan: the parsed :class:`PlanSummary`, or ``None`` when no plan text was
            supplied.
        public_access_acknowledged: the out-of-band public-access acknowledgement
            signal (R6.3) — only consulted when ``publicly_accessible`` is true.
    """

    text: str
    module_vars: dict[str, dict[str, Any]]
    tags: dict[str, str]
    plan: Optional[PlanSummary]
    public_access_acknowledged: bool = False


_TFVARS_ASSIGN_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$')


def _parse_tfvars(text: str) -> dict[str, Any]:
    """Parse a rendered ``terraform.tfvars`` text into ``{variable: value}``.

    Comment lines (``#``) and blank lines are skipped; a trailing ``# sensitive``
    annotation is stripped; bare ``true``/``false`` become booleans and bare
    integers become ints, so the gate's value checks are type-aware.
    """
    out: dict[str, Any] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _TFVARS_ASSIGN_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        rhs = m.group(2)
        # Strip a trailing inline comment (e.g. the `# sensitive` annotation),
        # but not a `#` inside a quoted string.
        if not rhs.lstrip().startswith('"'):
            rhs = rhs.split("#", 1)[0].strip()
        out[name] = _coerce_scalar(rhs)
    return out


def _coerce_scalar(token: str) -> Any:
    token = token.strip()
    if token in ("true", "false"):
        return token == "true"
    if re.fullmatch(r'-?\d+', token):
        return int(token)
    return _unquote(token)


def _build_evidence(
    rendered: Union["RenderResultLike", Mapping[str, str]],
    plan_output: Optional[str],
    public_access_acknowledged: bool,
) -> PolicyEvidence:
    """Normalize a :class:`RenderResult` or a ``{path: content}`` files mapping
    (plus an optional plan) into a :class:`PolicyEvidence`."""
    files: Mapping[str, str]
    module_vars: dict[str, dict[str, Any]] = {}

    # A RenderResult exposes `.files` and `.modules`; a plain mapping is the
    # files map itself. Duck-type on `.files` to support both without importing
    # the composer (keeps the gate decoupled and testable).
    structured_modules = getattr(rendered, "modules", None)
    files = getattr(rendered, "files", None) or rendered  # type: ignore[assignment]

    if structured_modules:
        for name, rendered_module in structured_modules.items():
            module_vars[name] = dict(getattr(rendered_module, "variables", {}) or {})

    # Always also parse the tfvars text (covers the plain-mapping case, and is a
    # no-op overwrite-with-equal for the structured case).
    for path, content in files.items():
        if path.endswith("terraform.tfvars"):
            module = path.rsplit("/", 1)[0]
            parsed = _parse_tfvars(content)
            module_vars.setdefault(module, {})
            for k, v in parsed.items():
                module_vars[module].setdefault(k, v)

    text = "\n".join(files.values())

    plan = parse_terraform_plan(plan_output) if plan_output else None

    tags = _resolve_tags(module_vars, plan)

    return PolicyEvidence(
        text=text,
        module_vars=module_vars,
        tags=tags,
        plan=plan,
        public_access_acknowledged=public_access_acknowledged,
    )


def _resolve_tags(
    module_vars: Mapping[str, Mapping[str, Any]],
    plan: Optional[PlanSummary],
) -> dict[str, str]:
    """Derive the canonical mandatory-tag map from the 5-rds tag variables
    and/or the plan tag block."""
    tags: dict[str, str] = {}
    rds = module_vars.get("5-rds", {})
    for var_name, canonical in _TAG_VARIABLE_TO_KEY.items():
        if var_name in rds and rds[var_name] not in (None, ""):
            tags[canonical] = str(rds[var_name])
    if plan:
        for key, value in plan.tags.items():
            if key in MANDATORY_TAG_KEYS and value not in (None, ""):
                tags.setdefault(key, value)
    return tags


# ---------------------------------------------------------------------------
# Small value helpers
# ---------------------------------------------------------------------------


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def _is_supplied(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple)):
        return len(value) > 0
    return True


def _lookup(
    evidence: PolicyEvidence,
    names: Iterable[str],
    *,
    modules: Optional[Iterable[str]] = None,
) -> Any:
    """Return the first supplied value for any of ``names``, searching the
    rendered module variables (optionally restricted to ``modules``) and then
    the merged plan attributes."""
    module_filter = set(modules) if modules is not None else None
    for module, variables in evidence.module_vars.items():
        if module_filter is not None and module not in module_filter:
            continue
        for name in names:
            if name in variables and _is_supplied(variables[name]):
                return variables[name]
    if evidence.plan:
        for name in names:
            if name in evidence.plan.attributes and _is_supplied(
                evidence.plan.attributes[name]
            ):
                return evidence.plan.attributes[name]
    return None


def _is_mrk(key: str) -> bool:
    return MRK_KEY_ID_PREFIX in key.lower()


def _is_aws_owned_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in AWS_OWNED_KEY_MARKERS)


# ---------------------------------------------------------------------------
# The discrete policy checks (R12.3)
# ---------------------------------------------------------------------------


def check_mrk_cmk_encryption(evidence: PolicyEvidence) -> PolicyResult:
    """Storage encryption on AND the storage CMK is a customer-managed MRK,
    never an AWS-owned/managed default key (R6.1)."""
    encrypted = _as_bool(_lookup(evidence, ("storage_encrypted",), modules=["5-rds"]))
    if not encrypted and evidence.plan:
        encrypted = _as_bool(evidence.plan.attribute("storage_encrypted"))
    if not encrypted:
        return PolicyResult(
            CHECK_MRK_CMK,
            False,
            "storage_encrypted is not true; the storage-encryption "
            "Security_Invariant (R6.1) requires encryption with a customer-managed "
            "MRK CMK.",
        )

    # The 5-rds variable is `kms_key_arn`; the RDS plan attribute is `kms_key_id`.
    key = _lookup(evidence, ("kms_key_arn", "kms_key_id"), modules=["5-rds"])
    if not _is_supplied(key) and evidence.plan:
        key = evidence.plan.attribute("kms_key_id")

    if _is_supplied(key):
        key_str = str(key)
        if _is_aws_owned_key(key_str):
            return PolicyResult(
                CHECK_MRK_CMK,
                False,
                f"the storage CMK '{key_str}' is an AWS-owned/managed default key; "
                "R6.1 forbids the default AWS-owned RDS key — a customer-managed "
                "MRK CMK is required.",
            )
        if not _is_mrk(key_str):
            return PolicyResult(
                CHECK_MRK_CMK,
                False,
                f"the storage CMK '{key_str}' is not a multi-region key (MRK); "
                "R6.1 requires a customer-managed MRK CMK (key-id begins with "
                f"'{MRK_KEY_ID_PREFIX}').",
            )
        return PolicyResult(
            CHECK_MRK_CMK,
            True,
            f"storage encryption is on with a customer-managed MRK CMK ('{key_str}').",
        )

    # No CMK supplied on the instance: the composer is on the create path, where
    # 3-kms builds the key. It must be multi-region (MRK) for R6.1.
    creates_mrk = _as_bool(
        _lookup(evidence, ("multi_region_key",), modules=["3-kms"])
    )
    if creates_mrk:
        return PolicyResult(
            CHECK_MRK_CMK,
            True,
            "storage encryption is on and the composer creates a multi-region "
            "(MRK) customer-managed CMK via the 3-kms module (multi_region_key=true).",
        )
    return PolicyResult(
        CHECK_MRK_CMK,
        False,
        "storage encryption is on but no storage CMK is supplied and the 3-kms "
        "create path does not set multi_region_key=true; R6.1 requires a "
        "customer-managed MRK CMK.",
    )


def check_db2comm_ssl(evidence: PolicyEvidence) -> PolicyResult:
    """``DB2COMM=SSL`` and ``ssl_svcename=50443`` are present (R6.2)."""
    text = evidence.text

    # Rendered-TF evidence: security.tf documents `DB2COMM=SSL` and
    # `ssl_svcename=50443`; the parameter group carries the same values.
    db2comm_ok = bool(re.search(r'DB2COMM\s*=?\s*"?SSL"?', text))
    svcename_ok = bool(
        re.search(rf'ssl_svcename\s*=?\s*"?{SSL_SERVICE_PORT}"?', text)
    )

    # Plan evidence: the parameter-group `parameter { name = .. value = .. }`
    # blocks parsed into PlanSummary.parameters.
    if evidence.plan:
        params = evidence.plan.parameters
        if params.get(DB2COMM_PARAMETER) == DB2COMM_SSL_VALUE:
            db2comm_ok = True
        if params.get(SSL_SVCENAME_PARAMETER) == str(SSL_SERVICE_PORT):
            svcename_ok = True

    if db2comm_ok and svcename_ok:
        return PolicyResult(
            CHECK_DB2COMM_SSL,
            True,
            f"DB2COMM=SSL and ssl_svcename={SSL_SERVICE_PORT} are present (R6.2).",
        )
    missing = []
    if not db2comm_ok:
        missing.append("DB2COMM=SSL")
    if not svcename_ok:
        missing.append(f"ssl_svcename={SSL_SERVICE_PORT}")
    return PolicyResult(
        CHECK_DB2COMM_SSL,
        False,
        "the SSL-only Db2 communication invariant (R6.2) is not satisfied; "
        f"missing from the rendered Terraform/plan: {', '.join(missing)}.",
    )


def check_non_public_absent_ack(evidence: PolicyEvidence) -> PolicyResult:
    """``publicly_accessible=false`` unless a public-access acknowledgement
    signal is present (R6.3)."""
    pub = _lookup(evidence, ("publicly_accessible",), modules=["5-rds"])
    if pub is None and evidence.plan:
        pub = evidence.plan.attribute("publicly_accessible")
    publicly_accessible = _as_bool(pub)

    if not publicly_accessible:
        return PolicyResult(
            CHECK_NON_PUBLIC,
            True,
            "publicly_accessible=false (R6.3): the instance is not publicly "
            "exposed.",
        )

    if evidence.public_access_acknowledged:
        return PolicyResult(
            CHECK_NON_PUBLIC,
            True,
            "publicly_accessible=true is permitted: a public-access "
            "acknowledgement signal is present (R6.3/R6.4).",
        )

    return PolicyResult(
        CHECK_NON_PUBLIC,
        False,
        "publicly_accessible=true without a public-access acknowledgement; "
        "R6.3 requires publicly_accessible=false absent an explicit "
        "acknowledgement.",
    )


def check_mandatory_tags(evidence: PolicyEvidence) -> PolicyResult:
    """All five mandatory tags are present and non-empty (R14)."""
    missing: list[str] = []
    for key in MANDATORY_TAG_KEYS:
        value = evidence.tags.get(key)
        if value is None or str(value).strip() == "":
            missing.append(key)
    if missing:
        return PolicyResult(
            CHECK_MANDATORY_TAGS,
            False,
            "missing or empty mandatory tag(s) "
            f"{', '.join(missing)}; R14 requires all five of "
            f"{', '.join(MANDATORY_TAG_KEYS)} to be present and non-empty.",
        )
    return PolicyResult(
        CHECK_MANDATORY_TAGS,
        True,
        "all five mandatory tags "
        f"({', '.join(MANDATORY_TAG_KEYS)}) are present and non-empty (R14).",
    )


def check_ibm_ids_present(evidence: PolicyEvidence) -> PolicyResult:
    """``ibm_customer_id`` and ``ibm_site_id`` are present for every edition
    (R7/R8)."""
    customer = _lookup(
        evidence,
        ("ibm_customer_id", "rds.ibm_customer_id", "ibm_customer_id_ssm"),
        modules=["4-parameter-group"],
    )
    site = _lookup(
        evidence,
        ("ibm_site_id", "rds.ibm_site_id", "ibm_site_id_ssm"),
        modules=["4-parameter-group"],
    )

    # Plan evidence: IBM IDs ride in the parameter group as `rds.ibm_customer_id`
    # / `rds.ibm_site_id` parameters.
    if evidence.plan:
        if not _is_supplied(customer):
            customer = evidence.plan.parameters.get(
                "rds.ibm_customer_id"
            ) or evidence.plan.parameters.get("ibm_customer_id")
        if not _is_supplied(site):
            site = evidence.plan.parameters.get(
                "rds.ibm_site_id"
            ) or evidence.plan.parameters.get("ibm_site_id")

    missing: list[str] = []
    if not _is_supplied(customer):
        missing.append("ibm_customer_id")
    if not _is_supplied(site):
        missing.append("ibm_site_id")

    if missing:
        return PolicyResult(
            CHECK_IBM_IDS,
            False,
            f"missing IBM licensing identifier(s) {', '.join(missing)}; R7/R8 "
            "require both ibm_customer_id and ibm_site_id for every Db2 edition.",
        )
    return PolicyResult(
        CHECK_IBM_IDS,
        True,
        "ibm_customer_id and ibm_site_id are present for the edition (R7/R8).",
    )


#: The ordered list of every gate check (R12.3). Task 11.2/11.3 iterate this so
#: a new check is added in one place.
ALL_CHECKS = (
    check_mrk_cmk_encryption,
    check_db2comm_ssl,
    check_non_public_absent_ack,
    check_mandatory_tags,
    check_ibm_ids_present,
)


def evaluate_policies(
    rendered: Union["RenderResultLike", Mapping[str, str]],
    plan_output: Optional[str] = None,
    *,
    public_access_acknowledged: bool = False,
) -> PolicyGateReport:
    """Run every policy check over the rendered Terraform (and optional plan).

    Args:
        rendered: either a :class:`RenderResult` from the composer (duck-typed
            on ``.files`` / ``.modules``) or a plain ``{relative path: file
            content}`` mapping of rendered Terraform.
        plan_output: optional ``terraform plan`` stdout text; when supplied it is
            parsed (:func:`parse_terraform_plan`) and the checks also consult the
            plan's attributes/tags/parameters.
        public_access_acknowledged: the out-of-band public-access acknowledgement
            signal (R6.3/R6.4); only consulted when ``publicly_accessible`` is
            true.

    Returns:
        A :class:`PolicyGateReport` whose ``ok`` is ``True`` only when every
        check passed (R12.3: all gates must pass before merge-to-apply).
    """
    evidence = _build_evidence(rendered, plan_output, public_access_acknowledged)
    return PolicyGateReport(results=[check(evidence) for check in ALL_CHECKS])


# A purely-documentary alias for the duck-typed RenderResult input (the gate
# never imports the composer, to stay decoupled and independently testable).
RenderResultLike = Any


# ---------------------------------------------------------------------------
# CLI — run the gate over a rendered deployment directory (for CI)
# ---------------------------------------------------------------------------


def _read_rendered_dir(path: "Path") -> dict[str, str]:
    """Read a rendered deployment dir into a ``{relative path: content}`` map.

    Collects the root ``*.tf`` files and every ``<module>/terraform.tfvars`` so
    :func:`evaluate_policies` sees the same surface the composer produced.
    """
    files: dict[str, str] = {}
    for p in sorted(path.rglob("*")):
        if p.is_file() and (p.suffix == ".tf" or p.name == "terraform.tfvars"):
            files[str(p.relative_to(path))] = p.read_text()
    return files


def _main(argv: "list[str] | None" = None) -> int:
    """CLI: run the five policy gates over a rendered deployment directory.

    Usage:
        python -m scripts.policy_gate <rendered-dir> [--plan plan.txt]

    Reads the rendered ``*.tf`` + ``*/terraform.tfvars`` (and, if present, the
    deployment-intent.json in the dir to honor a public-access acknowledgement).
    Prints a discrete PASS/FAIL per check. Exit 0 only when ALL gates pass, so a
    CI job can gate merge-to-apply on this command (R12.3/R12.4).
    """
    import argparse
    import json
    import sys
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description="Run the rds-db2-deployer policy gate over a rendered dir."
    )
    parser.add_argument("rendered_dir", help="path to the rendered deployment directory")
    parser.add_argument(
        "--plan", default=None, help="optional path to `terraform plan` stdout to also check"
    )
    args = parser.parse_args(argv)

    rendered = Path(args.rendered_dir)
    if not rendered.is_dir():
        print(f"error: rendered dir not found: {rendered}", file=sys.stderr)
        return 2

    files = _read_rendered_dir(rendered)
    if not files:
        print(f"error: no *.tf or terraform.tfvars under {rendered}", file=sys.stderr)
        return 2

    plan_output = None
    if args.plan:
        plan_output = Path(args.plan).read_text()

    # Honor a public-access acknowledgement recorded in the committed intent.
    ack = False
    intent_file = rendered / "deployment-intent.json"
    if intent_file.is_file():
        try:
            ack = json.loads(intent_file.read_text()).get("public_access_acknowledged") is True
        except json.JSONDecodeError:
            ack = False

    report = evaluate_policies(files, plan_output, public_access_acknowledged=ack)

    print(f"Policy gate over {rendered} ({len(report.results)} checks):")
    for r in report.results:
        print(f"  [{'PASS' if r.passed else 'FAIL'}] {r.check}: {r.message}")
    if report.ok:
        print("ALL POLICY GATES PASSED ✅")
        return 0
    print(f"{len(report.failures)} gate(s) FAILED — merge-to-apply is blocked.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
