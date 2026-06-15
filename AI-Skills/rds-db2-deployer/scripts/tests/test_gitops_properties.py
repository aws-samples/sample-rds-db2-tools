"""Property-based tests for the GitOps_Orchestrator + Policy_Gate (task 11.3).

These implement the two named correctness properties that govern the reviewed
PR/plan/gate/merge/apply flow, plus the gate's discrete-invariant guarantee, all
with fakes for the Git host and Terraform runner — no real Git, Terraform, or
AWS:

* **Property 14 — No apply without merge and passing gates (R12.6/R12.7):**
  across a generated matrix of flow scenarios (merge requested or not,
  externally merged or not, gate passing or failing, plan succeeding or
  failing), ``terraform apply`` NEVER runs (``tf.apply_calls == 0``,
  ``result.applied`` is ``False``, the infra is unchanged) unless the PR is
  merged AND every Policy_Gate check passed. Conversely, whenever apply *is*
  invoked the PR was merged, the gate passed, and the plan succeeded.

* **Property 12 — Sensitive_Value never leaks (R12.1/R12.2):** for generated
  valid intents carrying arbitrary non-empty Sensitive_Values
  (``ibm_customer_id`` / ``ibm_site_id`` / ``master_password``), none of those
  literal values appears in the PR body, any posted comment, or the masked
  ``terraform plan`` output.

* **Gate fails when any invariant is violated (R12.3, supports R12.4):** for each
  Security_Invariant the gate enforces (non-MRK CMK, AWS-owned CMK, missing IBM
  ID, missing mandatory tag, public-without-ack, missing DB2COMM/SSL), starting
  from a compliant baseline and violating exactly that one invariant makes
  ``evaluate_policies(...).ok`` ``False``.

**Validates: Requirements 12.6, 12.7, 12.1, 12.2**
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from scripts.gitops import (
    ApplyExecution,
    GitOpsOrchestrator,
    PlanExecution,
    build_pr_body,
    collect_sensitive_values,
)
from scripts.policy_gate import (
    CHECK_DB2COMM_SSL,
    CHECK_IBM_IDS,
    CHECK_MANDATORY_TAGS,
    CHECK_MRK_CMK,
    CHECK_NON_PUBLIC,
    evaluate_policies,
)
from scripts.render_terraform import (
    SENSITIVE_INTENT_FIELDS,
    render_terraform,
)
from scripts.verify import SENSITIVE_MASK


# ---------------------------------------------------------------------------
# Module-level paths / fixtures (computed once; rendering is expensive)
# ---------------------------------------------------------------------------

# scripts/tests/test_*.py -> scripts/ -> package root -> 04-db2-client/.
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
# Reuse the composer's layout-aware discovery so this works both in local dev
# (sibling RDS-Db2-Terraform) and the published GitHub layout (tools/rds-db2-terraform).
from scripts.render_terraform import DEFAULT_MODULES_ROOT as MODULES_ROOT


def _compliant_intent() -> dict:
    """A prod intent that renders to a fully gate-compliant configuration
    (mirrors the policy-gate / gitops unit-test fixtures)."""
    return {
        "schema_version": "1.0",
        "deployment_tier": "prod",
        "workload_size": "large",
        "region": "us-east-1",
        "engine": "db2-se",
        "engine_version": "12.1.4.0",
        "master_username": "admin",
        "manage_master_user_password": True,
        "master_user_secret_kms_key_id": "arn:aws:kms:us-east-1:111122223333:key/mrk-secret",
        "db_name": "DB2DB",
        "port": 8392,
        "license_model": "bring-your-own-license",
        "instance_class": "db.r7i.4xlarge",
        "allocated_storage": 16000,
        "storage_type": "io2",
        "iops": 16000,
        "multi_az": True,
        "backup_retention_period": 7,
        "publicly_accessible": False,
        "storage_encrypted": True,
        "kms_key_id": "arn:aws:kms:us-east-1:111122223333:key/mrk-1234",
        "vpc_security_group_ids": ["sg-0123456789abcdef0"],
        "vpc_id": "vpc-0123456789abcdef0",
        "db_subnet_group_name": "rds-db2-prod-subnets",
        "db_parameter_group_name": "",
        "monitoring_interval": 15,
        "monitoring_role_arn": "arn:aws:iam::111122223333:role/rds-db2-monitoring",
        "enable_cloudwatch_logs_exports": ["diag.log", "notify.log"],
        "deletion_protection": True,
        "ibm_customer_id": "1234567",
        "ibm_site_id": "1234567890",
        "db_instance_identifier": "",
        "tags": {"Project": "ACME", "Environment": "prod", "Owner": "db-team"},
        "ingress_cidrs": ["10.0.0.0/16"],
        "db_parameter_group_family": "db2-se-12.1",
        "_provenance": {"engine": "user_provided"},
    }


# A neutral plan that adds no attribute/tag/parameter evidence of its own, so a
# gate-failing rendered configuration cannot be "rescued" by the plan text.
NEUTRAL_PLAN = "Plan: 1 to add, 0 to change, 0 to destroy.\n"


# ---------------------------------------------------------------------------
# Fakes for the injectable protocols (same shape as test_gitops.py)
# ---------------------------------------------------------------------------


class FakePR:
    def __init__(self, number: int, title: str, body: str):
        self.number = number
        self.title = title
        self.body = body
        self.comments: list[str] = []
        self.merged = False


class FakeGitProvider:
    def __init__(self):
        self.prs: list[FakePR] = []
        self.merge_calls = 0

    def open_pr(self, *, title: str, body: str) -> FakePR:
        pr = FakePR(number=len(self.prs) + 1, title=title, body=body)
        self.prs.append(pr)
        return pr

    def post_comment(self, pr: FakePR, comment: str) -> None:
        pr.comments.append(comment)

    def merge(self, pr: FakePR) -> None:
        pr.merged = True
        self.merge_calls += 1


class FakeTerraformRunner:
    def __init__(self, plan: PlanExecution, apply: ApplyExecution):
        self._plan = plan
        self._apply = apply
        self.plan_calls = 0
        self.apply_calls = 0

    def plan(self, rendered):
        self.plan_calls += 1
        return self._plan

    def apply(self, rendered):
        self.apply_calls += 1
        return self._apply


# ---------------------------------------------------------------------------
# Configurations under test: one compliant RenderResult + gate-failing mappings
# ---------------------------------------------------------------------------

# Rendered once at import; reused as the "gate passes" arm of the matrix.
COMPLIANT_INTENT = _compliant_intent()
COMPLIANT_RENDER = render_terraform(COMPLIANT_INTENT, modules_root=MODULES_ROOT)


def _compliant_files() -> dict:
    """A plain ``{path: content}`` mapping that passes every Policy_Gate check —
    the baseline the per-invariant violations below are derived from."""
    return {
        "5-rds/terraform.tfvars": (
            "storage_encrypted = true\n"
            'kms_key_arn = "arn:aws:kms:us-east-1:111122223333:key/mrk-1234"\n'
            "publicly_accessible = false\n"
            'created_by = "rds-db2-skill"\n'
            'generation_model = "kiro-spec-composer"\n'
            'tag = "ACME"\n'
            'owner = "db-team"\n'
            'environment = "prod"\n'
        ),
        "4-parameter-group/terraform.tfvars": (
            'ibm_customer_id = "1234567"\n'
            'ibm_site_id = "1234567890"\n'
        ),
        "security.tf": "# DB2COMM=SSL and ssl_svcename=50443 enforced.\n",
    }


def _violate_non_mrk_key(files: dict) -> dict:
    files["5-rds/terraform.tfvars"] = files["5-rds/terraform.tfvars"].replace(
        "arn:aws:kms:us-east-1:111122223333:key/mrk-1234",
        "arn:aws:kms:us-east-1:111122223333:key/1234abcd-non-mrk",
    )
    return files


def _violate_aws_owned_key(files: dict) -> dict:
    files["5-rds/terraform.tfvars"] = files["5-rds/terraform.tfvars"].replace(
        "arn:aws:kms:us-east-1:111122223333:key/mrk-1234",
        "alias/aws/rds",
    )
    return files


def _violate_missing_ibm_id(files: dict) -> dict:
    files["4-parameter-group/terraform.tfvars"] = 'engine_edition = "se"\n'
    return files


def _violate_missing_tag(files: dict) -> dict:
    files["5-rds/terraform.tfvars"] = files["5-rds/terraform.tfvars"].replace(
        'environment = "prod"\n', ""
    )
    return files


def _violate_public_no_ack(files: dict) -> dict:
    files["5-rds/terraform.tfvars"] = files["5-rds/terraform.tfvars"].replace(
        "publicly_accessible = false\n", "publicly_accessible = true\n"
    )
    return files


def _violate_missing_db2comm(files: dict) -> dict:
    files.pop("security.tf", None)
    return files


# (check name, violation mutator) — one per Security_Invariant the gate enforces.
INVARIANT_VIOLATIONS = [
    (CHECK_MRK_CMK, _violate_non_mrk_key),
    (CHECK_MRK_CMK, _violate_aws_owned_key),
    (CHECK_IBM_IDS, _violate_missing_ibm_id),
    (CHECK_MANDATORY_TAGS, _violate_missing_tag),
    (CHECK_NON_PUBLIC, _violate_public_no_ack),
    (CHECK_DB2COMM_SSL, _violate_missing_db2comm),
]


# A list of gate-FAILING configs (plain mappings) for the Property 14 matrix.
def _gate_failing_configs() -> list[dict]:
    return [mutate(_compliant_files()) for _check, mutate in INVARIANT_VIOLATIONS]


# index 0 == the compliant RenderResult; indices >=1 == gate-failing mappings.
def _config_for_index(idx: int):
    if idx == 0:
        return COMPLIANT_RENDER
    return _gate_failing_configs()[idx - 1]


_NUM_CONFIGS = 1 + len(INVARIANT_VIOLATIONS)


# ---------------------------------------------------------------------------
# Property 14: No apply without merge and passing gates (R12.6 / R12.7)
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(
    config_index=st.integers(min_value=0, max_value=_NUM_CONFIGS - 1),
    request_merge=st.booleans(),
    externally_merged=st.booleans(),
    plan_succeeds=st.booleans(),
    apply_succeeds=st.booleans(),
    public_ack=st.booleans(),
)
def test_property14_no_apply_without_merge_and_passing_gates(
    config_index: int,
    request_merge: bool,
    externally_merged: bool,
    plan_succeeds: bool,
    apply_succeeds: bool,
    public_ack: bool,
) -> None:
    """Apply never runs before merge or with a failed policy gate (Property 14).

    **Validates: Requirements 12.6, 12.7**
    """
    rendered = _config_for_index(config_index)

    plan = (
        PlanExecution(succeeded=True, output=NEUTRAL_PLAN)
        if plan_succeeds
        else PlanExecution(succeeded=False, error="plan generation failed")
    )
    git = FakeGitProvider()
    tf = FakeTerraformRunner(
        plan,
        ApplyExecution(succeeded=apply_succeeds, output="done", error="boom"),
    )

    result = GitOpsOrchestrator(git, tf).run(
        rendered,
        COMPLIANT_INTENT,
        request_merge=request_merge,
        externally_merged=externally_merged,
        public_access_acknowledged=public_ack,
    )

    # Core safety invariant (Property 14): without BOTH a merge AND a passing
    # gate, apply must never be invoked and the infra must be left unchanged.
    if not (result.merged and result.gate_passed):
        assert tf.apply_calls == 0
        assert result.applied is False
        assert result.infra_changed is False

    # Converse: whenever apply was invoked at all, the PR was merged, every gate
    # passed, and the plan had succeeded first.
    if tf.apply_calls > 0:
        assert result.merged is True
        assert result.gate_passed is True
        assert result.plan_succeeded is True

    # A change to the infra implies a successful apply actually ran.
    if result.infra_changed:
        assert result.applied is True
        assert tf.apply_calls == 1

    # A failed plan short-circuits before the gate/merge/apply entirely.
    if not plan_succeeds:
        assert tf.apply_calls == 0
        assert result.applied is False
        assert result.infra_changed is False
        assert result.merged is False


# ---------------------------------------------------------------------------
# Property 12: Sensitive_Value never leaks into PR / comments / plan (R12.1/12.2)
# ---------------------------------------------------------------------------

# Sensitive-value generators: non-empty, non-whitespace literals. IBM IDs are
# digit strings; the master password is a mixed alphanumeric secret.
_id_values = st.text(alphabet="0123456789", min_size=5, max_size=12)
_password_values = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd"),
        max_codepoint=122,
    ),
    min_size=8,
    max_size=24,
)


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    customer_id=_id_values,
    site_id=_id_values,
    master_password=_password_values,
)
def test_property12_sensitive_values_never_leak(
    customer_id: str,
    site_id: str,
    master_password: str,
) -> None:
    """No Sensitive_Value appears in the PR body, any posted comment, or the
    masked plan output (Property 12).

    **Validates: Requirements 12.1, 12.2**
    """
    intent = _compliant_intent()
    intent["ibm_customer_id"] = customer_id
    intent["ibm_site_id"] = site_id
    intent["master_password"] = master_password

    rendered = render_terraform(intent, modules_root=MODULES_ROOT)

    secrets = {customer_id, site_id, master_password}
    # Every sensitive intent field carries one of our generated secrets.
    collected = collect_sensitive_values(rendered, intent)
    for field in SENSITIVE_INTENT_FIELDS:
        if intent.get(field):
            assert intent[field] in collected

    # Drive the full flow; the plan output deliberately echoes the secrets so we
    # prove the orchestrator masks them on the way to the PR (R12.2).
    leaky_plan = (
        f"{NEUTRAL_PLAN}"
        f"  rds.ibm_customer_id = {customer_id}\n"
        f"  rds.ibm_site_id = {site_id}\n"
        f"  master_password = {master_password}\n"
    )
    git = FakeGitProvider()
    tf = FakeTerraformRunner(
        PlanExecution(succeeded=True, output=leaky_plan),
        ApplyExecution(succeeded=True, output="Apply complete!"),
    )
    result = GitOpsOrchestrator(git, tf).run(
        rendered, intent, request_merge=True
    )

    # No literal Sensitive_Value survives in any surfaced text.
    surfaces = [result.pr_body_masked or "", result.plan_output_masked or ""]
    surfaces.extend(git.prs[0].comments)
    for secret in secrets:
        for surface in surfaces:
            assert secret not in surface, (
                f"sensitive value {secret!r} leaked into surfaced text"
            )

    # The PR body still carries its sections (masked), and the mask token shows.
    assert "Deployment intent" in (result.pr_body_masked or "")
    assert SENSITIVE_MASK in (result.pr_body_masked or "")


def test_property12_build_pr_body_masks_directly() -> None:
    """A direct ``build_pr_body`` check (no flow) — the assembled body never
    contains a Sensitive_Value literal.

    **Validates: Requirements 12.1**
    """
    intent = _compliant_intent()
    intent["master_password"] = "manual-mode-placeholder"
    rendered = render_terraform(intent, modules_root=MODULES_ROOT)
    values = collect_sensitive_values(rendered, intent)
    body = build_pr_body(rendered, intent, values)
    for secret in values:
        assert secret not in body


# ---------------------------------------------------------------------------
# Gate fails when any Security_Invariant is violated (R12.3, supports R12.4)
# ---------------------------------------------------------------------------


def test_baseline_compliant_files_pass_the_gate() -> None:
    """Sanity: the plain-mapping baseline the violations derive from passes
    every check, so a failure below is attributable to the single violation."""
    report = evaluate_policies(_compliant_files())
    assert report.ok, [f"{r.check}: {r.message}" for r in report.failures]


@pytest.mark.parametrize(
    "check_name,mutate",
    INVARIANT_VIOLATIONS,
    ids=[
        "non_mrk_key",
        "aws_owned_key",
        "missing_ibm_id",
        "missing_mandatory_tag",
        "public_without_ack",
        "missing_db2comm_ssl",
    ],
)
def test_gate_fails_when_invariant_violated(check_name, mutate) -> None:
    """Violating exactly one Security_Invariant makes the gate fail, and the
    specific check is among the failures (R12.3).

    **Validates: Requirements 12.3, 12.4**
    """
    files = mutate(_compliant_files())
    report = evaluate_policies(files)
    assert report.ok is False
    failed = {r.check for r in report.failures}
    assert check_name in failed, (
        f"expected {check_name} to fail; failures were {failed}"
    )


@given(
    key_suffix=st.text(
        alphabet="0123456789abcdef-", min_size=4, max_size=20
    ).filter(lambda s: "mrk-" not in s and "aws" not in s)
)
@settings(max_examples=50, deadline=None)
def test_gate_fails_for_any_non_mrk_cmk(key_suffix: str) -> None:
    """For any customer CMK that is neither an MRK nor AWS-owned, the MRK/CMK
    check fails (R6.1 restated by the gate, R12.3).

    **Validates: Requirements 12.3**
    """
    files = _compliant_files()
    files["5-rds/terraform.tfvars"] = files["5-rds/terraform.tfvars"].replace(
        "arn:aws:kms:us-east-1:111122223333:key/mrk-1234",
        f"arn:aws:kms:us-east-1:111122223333:key/{key_suffix}",
    )
    report = evaluate_policies(files)
    r = report.by_name(CHECK_MRK_CMK)
    assert r.passed is False
    assert report.ok is False
