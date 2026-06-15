"""Unit tests for the GitOps_Orchestrator PR/plan/merge/apply flow (task 11.2).

Covers Requirement 12 with fakes for the Git host and the Terraform runner — no
real Git, Terraform, or AWS:

* masking of every Sensitive_Value in the PR body and the posted plan (R12.1/R12.2);
* never apply before merge (R12.6);
* no apply when a Policy_Gate check fails (R12.4);
* no apply when the PR is merged without passing gates (R12.7);
* plan-generation failure is reported, blocks merge-to-apply, leaves infra
  unchanged (R12.8);
* the happy path: apply after merge AND all gates pass (R12.5).
"""

from __future__ import annotations

import pytest

from scripts.gitops import (
    BLOCK_GATE_FAILED,
    BLOCK_MERGED_WITHOUT_GATES,
    BLOCK_PLAN_FAILED,
    ApplyExecution,
    GitOpsOrchestrator,
    PlanExecution,
    build_pr_body,
    collect_sensitive_values,
    mask_intent,
    mask_text,
    run_gitops_flow,
)
from scripts.render_terraform import render_terraform
from scripts.verify import SENSITIVE_MASK


# ---------------------------------------------------------------------------
# Fakes for the injectable protocols
# ---------------------------------------------------------------------------


class FakePR:
    def __init__(self, number: int, title: str, body: str):
        self.number = number
        self.title = title
        self.body = body
        self.comments: list[str] = []
        self.merged = False


class FakeGitProvider:
    """Records every host operation so tests can assert ordering/masking."""

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
    """Returns scripted plan/apply outcomes and counts apply invocations."""

    def __init__(self, plan: PlanExecution, apply: ApplyExecution | None = None):
        self._plan = plan
        self._apply = apply or ApplyExecution(succeeded=True, output="Apply complete!")
        self.plan_calls = 0
        self.apply_calls = 0

    def plan(self, rendered):
        self.plan_calls += 1
        return self._plan

    def apply(self, rendered):
        self.apply_calls += 1
        return self._apply


# ---------------------------------------------------------------------------
# Intent fixtures (mirror the policy-gate tests' compliant intents)
# ---------------------------------------------------------------------------


def _compliant_intent() -> dict:
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


def _non_compliant_intent() -> dict:
    """Public without acknowledgement (kept for masking parity)."""
    intent = _compliant_intent()
    intent["publicly_accessible"] = True
    return intent


def _gate_failing_files() -> dict:
    """A rendered-files mapping (plain {path: content}) that fails the gate:
    publicly_accessible=true with no acknowledgement (R6.3). The orchestrator
    and the Policy_Gate both accept a plain files mapping, so this exercises the
    gate-failure guards without depending on the composer (which always forces
    publicly_accessible=false)."""
    return {
        "5-rds/terraform.tfvars": (
            "storage_encrypted = true\n"
            'kms_key_arn = "arn:aws:kms:us-east-1:111122223333:key/mrk-1234"\n'
            "publicly_accessible = true\n"
        ),
    }


@pytest.fixture
def compliant_intent() -> dict:
    return _compliant_intent()


@pytest.fixture
def compliant_render(compliant_intent, terraform_modules_root):
    return render_terraform(compliant_intent, modules_root=terraform_modules_root)


SAMPLE_PLAN_OK = """
  # module.rds.aws_db_instance.this will be created
  + resource "aws_db_instance" "this" {
      + storage_encrypted   = true
      + kms_key_id          = "arn:aws:kms:us-east-1:111122223333:key/mrk-1234"
      + publicly_accessible = false
    }
Plan: 1 to add, 0 to change, 0 to destroy.
"""


# ---------------------------------------------------------------------------
# 1. Masking (R12.1 / R12.2)
# ---------------------------------------------------------------------------


def test_collect_sensitive_values_from_intent_and_modules(compliant_render, compliant_intent):
    values = collect_sensitive_values(compliant_render, compliant_intent)
    # IBM IDs come from the intent's SENSITIVE_INTENT_FIELDS.
    assert "1234567" in values
    assert "1234567890" in values


def test_mask_text_replaces_every_occurrence():
    secret = "1234567"
    text = f"customer={secret} again {secret}"
    masked = mask_text(text, {secret})
    assert secret not in masked
    assert masked.count(SENSITIVE_MASK) == 2


def test_mask_text_longest_first_no_partial_leak():
    masked = mask_text("abcdef and abc", {"abc", "abcdef"})
    assert "abcdef" not in masked
    assert "abc" not in masked


def test_mask_intent_masks_sensitive_fields_by_name():
    masked = mask_intent({"ibm_customer_id": "1234567", "region": "us-east-1"})
    assert masked["ibm_customer_id"] == SENSITIVE_MASK
    assert masked["region"] == "us-east-1"


def test_mask_intent_masks_even_empty_sensitive_field():
    masked = mask_intent({"master_password": ""})
    assert masked["master_password"] == SENSITIVE_MASK


def test_pr_body_carries_no_sensitive_value(compliant_render, compliant_intent):
    values = collect_sensitive_values(compliant_render, compliant_intent)
    body = build_pr_body(compliant_render, compliant_intent, values)
    for secret in values:
        assert secret not in body
    # The intent JSON is present (masked) and the rendered files are embedded.
    assert "Deployment intent" in body
    assert "Rendered Terraform" in body


def test_posted_plan_carries_no_sensitive_value(compliant_render, compliant_intent):
    secret = "1234567"
    plan_text = SAMPLE_PLAN_OK + f"\n# leaked rds.ibm_customer_id = {secret}\n"
    git = FakeGitProvider()
    tf = FakeTerraformRunner(PlanExecution(succeeded=True, output=plan_text))
    result = GitOpsOrchestrator(git, tf).run(
        compliant_render, compliant_intent, request_merge=True
    )
    assert result.plan_posted
    assert secret not in result.plan_output_masked
    for comment in git.prs[0].comments:
        assert secret not in comment


# ---------------------------------------------------------------------------
# 2. never apply before merge (R12.6)
# ---------------------------------------------------------------------------


def test_no_apply_before_merge(compliant_render, compliant_intent):
    git = FakeGitProvider()
    tf = FakeTerraformRunner(PlanExecution(succeeded=True, output=SAMPLE_PLAN_OK))
    # request_merge=False and not externally merged -> never merges, never applies.
    result = GitOpsOrchestrator(git, tf).run(
        compliant_render, compliant_intent, request_merge=False
    )
    assert result.pr_opened
    assert result.gate_passed
    assert not result.merged
    assert not result.applied
    assert tf.apply_calls == 0
    assert git.merge_calls == 0
    assert not result.infra_changed


# ---------------------------------------------------------------------------
# 3. no apply on gate failure (R12.4)
# ---------------------------------------------------------------------------


def test_no_apply_on_gate_failure_blocks_merge(compliant_intent):
    rendered = _gate_failing_files()

    git = FakeGitProvider()
    tf = FakeTerraformRunner(PlanExecution(succeeded=True, output=SAMPLE_PLAN_OK))
    result = GitOpsOrchestrator(git, tf).run(rendered, compliant_intent, request_merge=True)

    assert not result.gate_passed
    assert result.failed_checks  # at least the non_public check
    assert not result.merged
    assert git.merge_calls == 0
    assert not result.applied
    assert tf.apply_calls == 0
    assert result.blocked
    assert result.blocked_reason == BLOCK_GATE_FAILED
    assert not result.infra_changed
    # The failed check is named on the PR.
    assert any("FAILED" in c for c in git.prs[0].comments)


# ---------------------------------------------------------------------------
# 4. no apply on merge-without-passing-gates (R12.7)
# ---------------------------------------------------------------------------


def test_no_apply_when_merged_without_passing_gates(compliant_intent):
    rendered = _gate_failing_files()

    git = FakeGitProvider()
    tf = FakeTerraformRunner(PlanExecution(succeeded=True, output=SAMPLE_PLAN_OK))
    # The PR was merged out-of-band while the gate is failing (R12.7).
    result = GitOpsOrchestrator(git, tf).run(
        rendered, compliant_intent, externally_merged=True
    )

    assert result.merged
    assert not result.gate_passed
    assert not result.applied
    assert tf.apply_calls == 0
    assert result.blocked
    assert result.blocked_reason == BLOCK_MERGED_WITHOUT_GATES
    assert not result.infra_changed
    assert any("NOT run" in c or "NOT passed" in c for c in git.prs[0].comments)


# ---------------------------------------------------------------------------
# 5. plan-generation failure (R12.8)
# ---------------------------------------------------------------------------


def test_plan_failure_reports_blocks_and_leaves_infra_unchanged(
    compliant_render, compliant_intent
):
    git = FakeGitProvider()
    tf = FakeTerraformRunner(
        PlanExecution(succeeded=False, error="Error: provider misconfigured")
    )
    result = GitOpsOrchestrator(git, tf).run(
        compliant_render, compliant_intent, request_merge=True
    )

    assert result.pr_opened
    assert not result.plan_succeeded
    assert result.plan_posted
    assert result.gate_report is None  # gate never ran (no plan)
    assert not result.merged
    assert git.merge_calls == 0
    assert not result.applied
    assert tf.apply_calls == 0
    assert result.blocked
    assert result.blocked_reason == BLOCK_PLAN_FAILED
    assert not result.infra_changed
    assert any("FAILED" in c for c in git.prs[0].comments)


# ---------------------------------------------------------------------------
# 6. happy path: apply after merge AND gates pass (R12.5)
# ---------------------------------------------------------------------------


def test_happy_path_applies_after_merge_and_gates_pass(compliant_render, compliant_intent):
    git = FakeGitProvider()
    tf = FakeTerraformRunner(
        PlanExecution(succeeded=True, output=SAMPLE_PLAN_OK),
        ApplyExecution(succeeded=True, output="Apply complete! Resources: 1 added."),
    )
    result = run_gitops_flow(
        compliant_render,
        compliant_intent,
        git_provider=git,
        terraform_runner=tf,
        request_merge=True,
    )

    assert result.pr_opened
    assert result.plan_succeeded
    assert result.gate_passed
    assert not result.failed_checks
    assert result.merged
    assert result.merged_to_apply
    assert git.merge_calls == 1
    assert result.applied
    assert tf.apply_calls == 1
    assert result.infra_changed
    assert not result.blocked


def test_apply_failure_leaves_infra_unchanged(compliant_render, compliant_intent):
    git = FakeGitProvider()
    tf = FakeTerraformRunner(
        PlanExecution(succeeded=True, output=SAMPLE_PLAN_OK),
        ApplyExecution(succeeded=False, error="apply error mid-module"),
    )
    result = GitOpsOrchestrator(git, tf).run(
        compliant_render, compliant_intent, request_merge=True
    )
    assert result.merged
    assert tf.apply_calls == 1
    assert not result.applied
    assert not result.infra_changed
