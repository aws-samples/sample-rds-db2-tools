"""The GitOps_Orchestrator for the rds-db2-provision-skill (R12).

This module drives the reviewed pull-request flow that sits between the
``Terraform_Composer`` (which produces the rendered configuration) and a real
``terraform apply``:

    open PR (rendered TF + intent, Sensitive_Values masked)   — R12.1
        -> post `terraform plan` (masked)                     — R12.2
            -> run the Policy_Gate over rendered TF + plan     — R12.3 (task 11.1)
                -> block merge-to-apply on ANY gate failure    — R12.4
                    -> apply ONLY after merge AND all gates pass — R12.5
                       never apply before merge                  — R12.6
                       merge without passing gates -> no apply    — R12.7
                       plan-generation failure -> report, block   — R12.8

The whole flow is unit-testable **without real Git, Terraform, or AWS**: the Git
host operations and the ``terraform plan``/``apply`` executions are abstracted
behind two injectable protocols, :class:`GitProvider` and
:class:`TerraformRunner`. The orchestrator never touches a network or a
subprocess directly; it only sequences the steps and enforces the guards.

Design notes:

* **Masking is a first-class function (R12.1/R12.2).** :func:`collect_sensitive_values`
  gathers every ``Sensitive_Value`` from the resolved intent
  (:data:`render_terraform.SENSITIVE_INTENT_FIELDS` — IBM IDs, master password)
  *and* from every ``RenderedModule.sensitive_variables`` value, and
  :func:`mask_text` substitutes :data:`SENSITIVE_MASK` for each of them in
  arbitrary text (the PR body, the intent JSON, the plan output). Masking is by
  literal value so a secret cannot leak through any rendered surface.

* **Discrete, inspectable steps (R12).** :func:`run_gitops_flow` returns a
  :class:`GitOpsResult` that records exactly what happened — PR opened, plan
  posted, plan succeeded, the :class:`PolicyGateReport`, whether the PR merged,
  whether merge-to-apply was blocked, and whether ``apply`` ran — so tests
  assert each guard independently. The infra is reported ``unchanged`` on every
  non-apply path (R12.4/R12.7/R12.8).

* **The gate is shared with task 11.1.** The orchestrator calls
  :func:`policy_gate.evaluate_policies` (the same discrete checks the validator
  and composer already enforce) so the merge-to-apply gate is the policy-as-code
  gate, not a re-implementation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

try:  # Single source of truth for the sensitive-field set and the mask token.
    from scripts.render_terraform import SENSITIVE_INTENT_FIELDS
    from scripts.policy_gate import PolicyGateReport, evaluate_policies
except ImportError:  # Fall back when scripts/ is directly on sys.path.
    from render_terraform import SENSITIVE_INTENT_FIELDS  # type: ignore
    from policy_gate import PolicyGateReport, evaluate_policies  # type: ignore

try:  # Reuse the Verification_Step mask token so every surface masks identically.
    from scripts.verify import SENSITIVE_MASK
except ImportError:  # pragma: no cover - trivial fallback
    try:
        from verify import SENSITIVE_MASK  # type: ignore
    except ImportError:  # pragma: no cover
        SENSITIVE_MASK = "***"


# ---------------------------------------------------------------------------
# Injectable host/runner protocols (no real Git / Terraform / AWS)
# ---------------------------------------------------------------------------


@dataclass
class PlanExecution:
    """The outcome of a ``terraform plan`` invocation (R12.2/R12.8).

    Attributes:
        succeeded: ``True`` when the plan generated successfully; ``False`` when
            plan generation failed (R12.8).
        output: the plan stdout text (parsed by the Policy_Gate, posted — masked
            — to the PR). On failure this MAY be empty.
        error: the failure message when ``succeeded`` is ``False``.
    """

    succeeded: bool
    output: str = ""
    error: str = ""


@dataclass
class ApplyExecution:
    """The outcome of a ``terraform apply`` invocation (R12.5).

    Attributes:
        succeeded: ``True`` when the apply completed.
        output: the apply stdout text.
        error: the failure message when ``succeeded`` is ``False``.
    """

    succeeded: bool
    output: str = ""
    error: str = ""


@runtime_checkable
class GitProvider(Protocol):
    """The Git-host operations the orchestrator needs, host-agnostic (R12.1).

    A concrete implementation wraps a real Git host (GitHub/GitLab/CodeCommit);
    the orchestrator only depends on this surface, so tests inject a fake.
    """

    def open_pr(self, *, title: str, body: str) -> Any:
        """Open a pull request with ``body`` (already masked) and return an
        opaque PR handle the other methods accept."""
        ...

    def post_comment(self, pr: Any, comment: str) -> Any:
        """Post a comment (already masked) to ``pr``."""
        ...

    def merge(self, pr: Any) -> Any:
        """Merge ``pr``. Called by the orchestrator only on the gated
        merge-to-apply path (R12.4 blocks this on any gate failure)."""
        ...


@runtime_checkable
class TerraformRunner(Protocol):
    """The Terraform executions the orchestrator needs (R12.2/R12.5)."""

    def plan(self, rendered: Any) -> PlanExecution:
        """Run ``terraform plan`` for the rendered configuration."""
        ...

    def apply(self, rendered: Any) -> ApplyExecution:
        """Run ``terraform apply`` for the rendered configuration. Invoked only
        after merge AND all gates passed (R12.5); never before merge (R12.6)."""
        ...


# ---------------------------------------------------------------------------
# Sensitive-value masking (R12.1, R12.2)
# ---------------------------------------------------------------------------


def _scalar_str(value: Any) -> Optional[str]:
    """Return a non-empty string form of a scalar Sensitive_Value, or ``None``
    when there is nothing maskable (empty/whitespace/None/containers)."""
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple, set)):
        return None
    text = value if isinstance(value, str) else str(value)
    return text if text.strip() != "" else None


def collect_sensitive_values(
    rendered: Any,
    intent: Optional[Mapping[str, Any]] = None,
) -> set[str]:
    """Gather every literal ``Sensitive_Value`` to mask out of PR/plan surfaces.

    Two sources, unioned (R12.1/R12.2):

    * the resolved ``intent``'s :data:`render_terraform.SENSITIVE_INTENT_FIELDS`
      (``ibm_customer_id``, ``ibm_site_id``, ``master_password``), and
    * every ``RenderedModule.sensitive_variables`` value tracked by the composer
      (the same secrets, rendered into the module tfvars).

    Empty / whitespace-only values are skipped (nothing to leak), so masking
    never collapses unrelated text to the mask token.
    """
    values: set[str] = set()

    if intent:
        for field_name in SENSITIVE_INTENT_FIELDS:
            scalar = _scalar_str(intent.get(field_name))
            if scalar is not None:
                values.add(scalar)

    modules = getattr(rendered, "modules", None) or {}
    for module in modules.values():
        sensitive_vars = getattr(module, "sensitive_variables", None) or set()
        variables = getattr(module, "variables", None) or {}
        for var_name in sensitive_vars:
            scalar = _scalar_str(variables.get(var_name))
            if scalar is not None:
                values.add(scalar)

    return values


def mask_text(text: str, sensitive_values: set[str]) -> str:
    """Replace every occurrence of each Sensitive_Value in ``text`` with
    :data:`SENSITIVE_MASK` (R12.1/R12.2).

    Values are masked longest-first so a value that is a substring of another
    cannot leave a partial secret behind.
    """
    masked = text
    for value in sorted(sensitive_values, key=len, reverse=True):
        if value:
            masked = masked.replace(value, SENSITIVE_MASK)
    return masked


def mask_intent(
    intent: Mapping[str, Any],
    sensitive_values: Optional[set[str]] = None,
) -> dict:
    """Return a deep-ish copy of ``intent`` with every Sensitive_Value field
    replaced by :data:`SENSITIVE_MASK` (R12.1).

    Field-name masking (not value matching) guarantees a sensitive field is
    masked even when its value is empty or a placeholder; the value-based
    :func:`mask_text` then covers any sensitive value that appears elsewhere
    (e.g. inside the rendered Terraform).
    """
    out: dict = {}
    for key, value in intent.items():
        if key in SENSITIVE_INTENT_FIELDS:
            out[key] = SENSITIVE_MASK
        else:
            out[key] = value
    return out


def build_pr_body(
    rendered: Any,
    intent: Mapping[str, Any],
    sensitive_values: set[str],
) -> str:
    """Build the PR body containing the rendered Terraform and the intent, with
    every Sensitive_Value masked (R12.1).

    The intent is masked by field name first (so empty sensitive fields are
    still masked), then the whole assembled body is run through value-based
    :func:`mask_text` so any secret that also appears in the rendered Terraform
    is masked there too.
    """
    masked_intent = mask_intent(intent, sensitive_values)

    parts: list[str] = [
        "## RDS for Db2 deployment — review",
        "",
        "### Deployment intent",
        "```json",
        json.dumps(masked_intent, indent=2, sort_keys=True, default=str),
        "```",
        "",
        "### Rendered Terraform",
    ]
    files = getattr(rendered, "files", None) or {}
    for path in sorted(files):
        parts.append("")
        parts.append(f"#### {path}")
        parts.append("```hcl")
        parts.append(files[path])
        parts.append("```")

    body = "\n".join(parts)
    return mask_text(body, sensitive_values)


# ---------------------------------------------------------------------------
# Flow result
# ---------------------------------------------------------------------------


# Reasons the merge-to-apply path is blocked (stable tokens for tests/callers).
BLOCK_PLAN_FAILED = "plan_generation_failed"
BLOCK_GATE_FAILED = "policy_gate_failed"
BLOCK_NOT_MERGED = "not_merged"
BLOCK_MERGED_WITHOUT_GATES = "merged_without_passing_gates"


@dataclass
class GitOpsResult:
    """A record of exactly what the GitOps flow did, for guard assertions (R12).

    Attributes:
        pr_opened / pr: whether a PR was opened and its handle (R12.1).
        pr_body_masked: the masked PR body that was posted (R12.1).
        plan_succeeded: whether ``terraform plan`` generated (R12.2/R12.8).
        plan_posted: whether the (masked) plan / plan-failure was posted (R12.2).
        plan_output_masked: the masked plan text posted to the PR (R12.2).
        gate_report: the :class:`PolicyGateReport` (``None`` if the gate never
            ran because plan generation failed).
        gate_passed: whether every Policy_Gate check passed (R12.3/R12.4).
        failed_checks: the names of the failed gate checks (R12.4/R12.7).
        merged: whether the PR ended up merged (by the gated path or out-of-band).
        merged_to_apply: whether the orchestrator performed the gated merge.
        applied: whether ``terraform apply`` ran (R12.5).
        infra_changed: ``True`` only when ``applied`` — every other path leaves
            the infra unchanged (R12.4/R12.7/R12.8).
        blocked / blocked_reason: whether (and why) merge-to-apply was blocked.
        comments: the masked comments posted to the PR, in order.
    """

    pr_opened: bool = False
    pr: Any = None
    pr_body_masked: Optional[str] = None
    plan_succeeded: bool = False
    plan_posted: bool = False
    plan_output_masked: Optional[str] = None
    gate_report: Optional[PolicyGateReport] = None
    gate_passed: bool = False
    failed_checks: list[str] = field(default_factory=list)
    merged: bool = False
    merged_to_apply: bool = False
    applied: bool = False
    infra_changed: bool = False
    blocked: bool = False
    blocked_reason: Optional[str] = None
    comments: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The orchestrator
# ---------------------------------------------------------------------------


class GitOpsOrchestrator:
    """Sequences the PR/plan/gate/merge/apply flow behind injectable host and
    runner protocols (R12)."""

    def __init__(self, git_provider: GitProvider, terraform_runner: TerraformRunner):
        self.git = git_provider
        self.tf = terraform_runner

    # -- internal helpers --------------------------------------------------

    def _comment(self, result: GitOpsResult, sensitive_values: set[str], text: str) -> None:
        masked = mask_text(text, sensitive_values)
        self.git.post_comment(result.pr, masked)
        result.comments.append(masked)

    # -- the flow ----------------------------------------------------------

    def run(
        self,
        rendered: Any,
        intent: Mapping[str, Any],
        *,
        request_merge: bool = False,
        externally_merged: bool = False,
        public_access_acknowledged: bool = False,
        pr_title: str = "Deploy RDS for Db2",
    ) -> GitOpsResult:
        """Run the full GitOps flow over a rendered configuration (R12).

        Args:
            rendered: the composer's ``RenderResult`` (duck-typed on ``.files`` /
                ``.modules``).
            intent: the resolved ``Deployment_Intent``.
            request_merge: the reviewer asked the orchestrator to merge-to-apply.
                The orchestrator performs the merge ONLY when every gate passes
                and the plan generated (R12.4 blocks it otherwise).
            externally_merged: the PR was merged out-of-band in the Git host
                (models R12.7 — a merge that bypassed the gated path).
            public_access_acknowledged: the out-of-band public-access
                acknowledgement passed through to the Policy_Gate (R6.3/R6.4).
            pr_title: the PR title.

        Returns:
            A :class:`GitOpsResult` recording every step and guard outcome. The
            infra is left unchanged on every path except a successful apply.
        """
        result = GitOpsResult()
        sensitive_values = collect_sensitive_values(rendered, intent)

        # 1) Open the PR with the rendered TF + intent, masked (R12.1).
        body = build_pr_body(rendered, intent, sensitive_values)
        result.pr = self.git.open_pr(title=pr_title, body=body)
        result.pr_opened = True
        result.pr_body_masked = body

        # 2) Generate the plan; post it masked (R12.2) or handle failure (R12.8).
        plan = self.tf.plan(rendered)
        if not plan.succeeded:
            # Plan-generation failure: report on the PR, block merge-to-apply,
            # leave the infra unchanged (R12.8). The gate never runs (no plan).
            failure_text = (
                "terraform plan generation FAILED — merge-to-apply is blocked "
                "and the target infrastructure is left unchanged (R12.8).\n\n"
                f"{plan.error or plan.output}"
            )
            self._comment(result, sensitive_values, failure_text)
            result.plan_posted = True
            result.plan_succeeded = False
            result.blocked = True
            result.blocked_reason = BLOCK_PLAN_FAILED
            result.infra_changed = False
            return result

        masked_plan = mask_text(plan.output, sensitive_values)
        self.git.post_comment(result.pr, f"### terraform plan\n```\n{masked_plan}\n```")
        result.comments.append(masked_plan)
        result.plan_posted = True
        result.plan_succeeded = True
        result.plan_output_masked = masked_plan

        # 3) Run the Policy_Gate over the rendered TF + plan (R12.3, task 11.1).
        report = evaluate_policies(
            rendered,
            plan.output,
            public_access_acknowledged=public_access_acknowledged,
        )
        result.gate_report = report
        result.gate_passed = report.ok
        result.failed_checks = [r.check for r in report.failures]

        # Always report the gate outcome on the PR; name each failed check (R12.4).
        if report.ok:
            self._comment(
                result,
                sensitive_values,
                "Policy_Gate: all checks PASSED.",
            )
        else:
            failed = "\n".join(f"  - {r.check}: {r.message}" for r in report.failures)
            self._comment(
                result,
                sensitive_values,
                "Policy_Gate: one or more checks FAILED — merge-to-apply is "
                "blocked and the target infrastructure is left unchanged "
                f"(R12.4).\n{failed}",
            )

        # 4) Merge decision. The orchestrator performs the gated merge-to-apply
        #    ONLY when every gate passed (R12.4). An out-of-band merge is honored
        #    as a merged state but never triggers an ungated apply (R12.7).
        if request_merge and report.ok:
            self.git.merge(result.pr)
            result.merged = True
            result.merged_to_apply = True
        elif externally_merged:
            result.merged = True
        elif request_merge and not report.ok:
            # Merge-to-apply requested but blocked by the gate (R12.4).
            result.blocked = True
            result.blocked_reason = BLOCK_GATE_FAILED

        # 5) Apply guard (R12.5/R12.6/R12.7):
        #    apply ONLY after merge AND all gates passed; never before merge.
        if not result.merged:
            # Never apply before merge (R12.6).
            result.applied = False
            result.infra_changed = False
            if not result.blocked and not report.ok:
                result.blocked = True
                result.blocked_reason = BLOCK_GATE_FAILED
            elif not result.blocked:
                result.blocked_reason = result.blocked_reason or BLOCK_NOT_MERGED
            return result

        if not report.ok:
            # Merged while one or more gates have not passed -> no apply, report
            # the unpassed checks, leave the infra unchanged (R12.7).
            self._comment(
                result,
                sensitive_values,
                "The pull request was merged while one or more Policy_Gate "
                "checks have NOT passed — terraform apply will NOT run and the "
                "target infrastructure is left unchanged (R12.7). Unpassed "
                f"checks: {', '.join(result.failed_checks)}.",
            )
            result.applied = False
            result.infra_changed = False
            result.blocked = True
            result.blocked_reason = BLOCK_MERGED_WITHOUT_GATES
            return result

        # Merged AND all gates passed -> apply (R12.5).
        apply_result = self.tf.apply(rendered)
        result.applied = bool(apply_result.succeeded)
        result.infra_changed = bool(apply_result.succeeded)
        self._comment(
            result,
            sensitive_values,
            "terraform apply completed (R12.5)."
            if apply_result.succeeded
            else f"terraform apply FAILED: {apply_result.error or apply_result.output}",
        )
        return result


def run_gitops_flow(
    rendered: Any,
    intent: Mapping[str, Any],
    *,
    git_provider: GitProvider,
    terraform_runner: TerraformRunner,
    request_merge: bool = False,
    externally_merged: bool = False,
    public_access_acknowledged: bool = False,
    pr_title: str = "Deploy RDS for Db2",
) -> GitOpsResult:
    """Convenience wrapper constructing a :class:`GitOpsOrchestrator` and running
    the flow once (R12)."""
    orchestrator = GitOpsOrchestrator(git_provider, terraform_runner)
    return orchestrator.run(
        rendered,
        intent,
        request_merge=request_merge,
        externally_merged=externally_merged,
        public_access_acknowledged=public_access_acknowledged,
        pr_title=pr_title,
    )
