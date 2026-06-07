"""Error-handling wiring and long-form artifact output (task 12, Requirement 15).

This module ties together the definitive failure paths that the individual
components already detect, and writes the long-form artifacts the skill must
persist whether a deployment **completes** or **halts on failure** (R15.5):

    artifacts/<deployment-name>/
        resolved-intent.json     — the resolved Deployment_Intent (masked)
        plan-summary.txt         — the rendered terraform plan summary
        precheck-report.txt      — the VPC_Precheck report
        outcome.json             — the deployment outcome + any error report

Two cross-cutting guarantees:

* **Write on completion AND on failure (R15.5).** :func:`write_artifacts` is
  called from both the success path and every halt path. The status the caller
  passes (``"completed"`` / ``"failed"``) only changes ``outcome.json``; the
  intent, plan, and precheck artifacts are written either way (whatever the
  caller has so far — a halt before rendering simply has no plan summary).

* **Sensitive_Values masked everywhere (R15.6).** The heavy lifting reuses the
  existing masking primitives from ``gitops``/``render_terraform`` — there is no
  second masking implementation here:

  - the resolved intent is masked by field name with
    :func:`gitops.mask_intent` (so a sensitive field is masked even when empty),
    and
  - every artifact's serialized text is then passed through value-based
    :func:`gitops.mask_text` using the union of Sensitive_Values gathered by
    :func:`gitops.collect_sensitive_values` (the intent's
    :data:`render_terraform.SENSITIVE_INTENT_FIELDS` plus every rendered
    module's ``sensitive_variables``), so a raw IBM ID / master password cannot
    leak through the plan summary, the precheck report, or anywhere else.

The error-path reporting helpers (:func:`report_engine_version_error`,
:func:`report_terraform_validate_errors`, :func:`report_apply_failure`) are
small, pure formatters that the orchestrator calls to surface the R15 failures
by name — the errors themselves are raised/detected by the components
(``engine_versions``, ``render_terraform``/``terraform validate``, the apply
runner). They never fabricate a version, never open a PR, and always name the
information the requirement asks for.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

try:  # Reuse the single masking implementation; never reinvent it here.
    from scripts.gitops import collect_sensitive_values, mask_intent, mask_text
    from scripts.render_terraform import SENSITIVE_INTENT_FIELDS
except ImportError:  # Fall back when scripts/ is directly on sys.path.
    from gitops import collect_sensitive_values, mask_intent, mask_text  # type: ignore
    from render_terraform import SENSITIVE_INTENT_FIELDS  # type: ignore

try:
    from scripts.engine_versions import EngineVersionResolutionError
except ImportError:  # pragma: no cover - direct-path fallback
    from engine_versions import EngineVersionResolutionError  # type: ignore


# ---------------------------------------------------------------------------
# Where artifacts live
# ---------------------------------------------------------------------------

#: ``scripts/artifacts.py`` -> ``scripts/`` -> package root -> ``artifacts/``.
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACTS_DIR = _PACKAGE_ROOT / "artifacts"

#: Stable artifact file names so callers/tests reference them by constant.
INTENT_ARTIFACT = "resolved-intent.json"
PLAN_ARTIFACT = "plan-summary.txt"
PRECHECK_ARTIFACT = "precheck-report.txt"
OUTCOME_ARTIFACT = "outcome.json"

#: The two terminal deployment outcomes (R15.5: "completes or halts on failure").
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

#: A conservative directory-name sanitizer so a deployment name can never escape
#: the ``artifacts/`` tree or contain a path separator (R15.5 writes under
#: ``artifacts/<deployment-name>/``).
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_deployment_dirname(deployment_name: str) -> str:
    """Return a filesystem-safe single path component for ``deployment_name``.

    Collapses any run of characters outside ``[A-Za-z0-9._-]`` to a single
    hyphen, strips leading/trailing separators, and rejects the traversal names
    ``.``/``..``. An empty/blank name falls back to ``deployment`` so a directory
    is always produced (the artifacts MUST be written, R15.5).
    """
    cleaned = _UNSAFE_NAME.sub("-", (deployment_name or "").strip()).strip("-._")
    if not cleaned or cleaned in {".", ".."}:
        return "deployment"
    return cleaned


# ---------------------------------------------------------------------------
# Error-path reporting helpers (R15.1, R15.2, R15.3/15.4)
# ---------------------------------------------------------------------------


def report_engine_version_error(error: EngineVersionResolutionError) -> str:
    """Format the unresolvable-engine-version failure (R15.1).

    Names the requested ``engine`` and ``major version`` from the raised
    :class:`engine_versions.EngineVersionResolutionError`, states that the agent
    halts before any Terraform rendering, and explicitly does NOT substitute a
    fabricated version string. No version is invented here — only the engine and
    major the caller already attempted are echoed back.
    """
    return (
        "ERROR: could not resolve a valid engine version from "
        "`aws rds describe-db-engine-versions` for engine "
        f"{error.engine!r} major version {error.major_version!r} in region "
        f"{error.region!r}. Halting before any Terraform rendering begins; no "
        "minor-version string will be fabricated (R15.1)."
    )


def report_terraform_validate_errors(validate_errors: Sequence[str]) -> str:
    """Format the ``terraform validate`` failure (R15.2).

    Reports every validation error returned by ``terraform validate``, states
    that no pull request will be opened, and halts before the PR step. Each
    error is reported on its own line so none is dropped.
    """
    errors = [str(e).strip() for e in validate_errors if str(e).strip()]
    if not errors:
        # Defensive: a caller should only invoke this on a real failure, but if
        # the error list is empty we still report the halt rather than imply
        # success.
        return (
            "ERROR: `terraform validate` reported a failure but no error detail "
            "was captured. No pull request will be opened (R15.2)."
        )
    lines = [
        f"ERROR: `terraform validate` reported {len(errors)} error(s); no pull "
        "request will be opened and rendering halts (R15.2):"
    ]
    lines.extend(f"  - {err}" for err in errors)
    return "\n".join(lines)


def report_apply_failure(
    *,
    failing_module: str,
    failing_step: str,
    state_location: str,
) -> str:
    """Format an apply-time failure (R15.3/R15.4).

    After ``terraform apply`` begins, a failure must report which
    ``Terraform_Module`` failed, the failing apply step, and the externalized
    Terraform state location so the customer can recover. All three are named
    explicitly.
    """
    return (
        "ERROR: terraform apply failed after it began (R15.4).\n"
        f"  - failing module: {failing_module}\n"
        f"  - failing step:   {failing_step}\n"
        f"  - state location: {state_location}\n"
        "Use the externalized state above to inspect and recover the partially "
        "applied deployment."
    )


# ---------------------------------------------------------------------------
# Plan / precheck rendering to text (tolerant of several input shapes)
# ---------------------------------------------------------------------------


def _plan_summary_text(plan_summary: Any) -> str:
    """Render a plan summary to text, accepting a ``PlanSummary``, a plain
    string, a mapping, or ``None``.

    The composer/policy-gate produce a structured ``policy_gate.PlanSummary``;
    a caller may also pass the raw masked plan text or a dict. We render
    whatever is given without binding to one concrete type, so a halt that only
    has partial information still writes a useful artifact.
    """
    if plan_summary is None:
        return "No plan summary was produced before the deployment halted."
    if isinstance(plan_summary, str):
        return plan_summary
    # policy_gate.PlanSummary (and similar dataclasses) expose add/change/destroy
    # counts, resources, and merged attribute/tag/parameter maps.
    add = getattr(plan_summary, "add", None)
    change = getattr(plan_summary, "change", None)
    destroy = getattr(plan_summary, "destroy", None)
    if add is not None or change is not None or destroy is not None:
        lines = [
            "Terraform plan summary:",
            f"  to add:     {add if add is not None else 'unknown'}",
            f"  to change:  {change if change is not None else 'unknown'}",
            f"  to destroy: {destroy if destroy is not None else 'unknown'}",
        ]
        resources = getattr(plan_summary, "resources", None) or []
        if resources:
            lines.append(f"  resources ({len(resources)}):")
            for res in resources:
                rtype = res.get("type") if isinstance(res, Mapping) else None
                rname = res.get("name") if isinstance(res, Mapping) else None
                lines.append(f"    - {rtype or '?'}.{rname or '?'}")
        return "\n".join(lines)
    if isinstance(plan_summary, Mapping):
        return json.dumps(dict(plan_summary), indent=2, sort_keys=True, default=str)
    return str(plan_summary)


def _precheck_report_text(precheck_report: Any) -> str:
    """Render a precheck report to text, accepting a ``PrecheckReport`` (which
    exposes ``.report()``), a plain string, or ``None``."""
    if precheck_report is None:
        return "No VPC_Precheck report was produced before the deployment halted."
    report_fn = getattr(precheck_report, "report", None)
    if callable(report_fn):
        return report_fn()
    return str(precheck_report)


# ---------------------------------------------------------------------------
# The artifact writer (R15.5, R15.6)
# ---------------------------------------------------------------------------


@dataclass
class ArtifactWriteResult:
    """What :func:`write_artifacts` wrote, for assertions and logging.

    Attributes:
        directory: the ``artifacts/<deployment-name>/`` directory written to.
        files: ``{artifact name: absolute path}`` for every file written.
        status: the recorded terminal outcome (``completed`` / ``failed``).
        sensitive_values: the literal Sensitive_Values that were masked out of
            every written artifact (for auditing; never written to disk).
    """

    directory: Path
    files: dict[str, Path] = field(default_factory=dict)
    status: str = STATUS_COMPLETED
    sensitive_values: set[str] = field(default_factory=set)


def write_artifacts(
    deployment_name: str,
    *,
    intent: Mapping[str, Any],
    status: str,
    plan_summary: Any = None,
    precheck_report: Any = None,
    rendered: Any = None,
    error: Optional[Union[str, BaseException]] = None,
    base_dir: Union[str, Path, None] = None,
) -> ArtifactWriteResult:
    """Write the long-form artifacts to ``artifacts/<deployment-name>/`` (R15.5).

    Called on BOTH a successful completion and a halt on failure: the resolved
    intent, the plan summary, and the precheck report are written with whatever
    the caller has so far. Every artifact is masked so no Sensitive_Value
    reaches disk (R15.6).

    Args:
        deployment_name: the deployment name; sanitized into a single safe path
            component under ``artifacts/``.
        intent: the resolved ``Deployment_Intent`` (masked by field name before
            writing).
        status: the terminal outcome — :data:`STATUS_COMPLETED` or
            :data:`STATUS_FAILED`.
        plan_summary: the rendered plan summary (a ``policy_gate.PlanSummary``,
            masked plan text, mapping, or ``None`` when the halt preceded plan).
        precheck_report: the ``vpc_precheck.PrecheckReport`` (or text/``None``).
        rendered: the composer's ``RenderResult`` (used only to widen the
            Sensitive_Value set from the rendered modules; optional).
        error: an error report string or exception to record in ``outcome.json``
            on the failure path.
        base_dir: override for the artifacts root (defaults to the package's
            ``artifacts/``); used by tests.

    Returns:
        An :class:`ArtifactWriteResult` describing the directory and files
        written.
    """
    root = Path(base_dir) if base_dir is not None else DEFAULT_ARTIFACTS_DIR
    directory = root / safe_deployment_dirname(deployment_name)
    directory.mkdir(parents=True, exist_ok=True)

    # Gather every Sensitive_Value once (intent fields + rendered module
    # secrets) so value-based masking covers all written surfaces (R15.6).
    sensitive_values = collect_sensitive_values(rendered, intent)

    result = ArtifactWriteResult(
        directory=directory,
        status=status,
        sensitive_values=set(sensitive_values),
    )

    # 1) Resolved intent — masked by field name first (so an empty sensitive
    #    field is still masked), then value-masked as a belt-and-braces pass.
    masked_intent = mask_intent(intent, sensitive_values)
    intent_text = json.dumps(masked_intent, indent=2, sort_keys=True, default=str)
    intent_text = mask_text(intent_text, sensitive_values)
    _write(directory, INTENT_ARTIFACT, intent_text, result)

    # 2) Plan summary — rendered to text then value-masked.
    plan_text = mask_text(_plan_summary_text(plan_summary), sensitive_values)
    _write(directory, PLAN_ARTIFACT, plan_text, result)

    # 3) Precheck report — rendered to text then value-masked.
    precheck_text = mask_text(_precheck_report_text(precheck_report), sensitive_values)
    _write(directory, PRECHECK_ARTIFACT, precheck_text, result)

    # 4) Outcome — the terminal status and any error report (also masked).
    error_text: Optional[str] = None
    if error is not None:
        error_text = mask_text(str(error), sensitive_values)
    outcome = {
        "deployment_name": deployment_name,
        "status": status,
        "error": error_text,
    }
    outcome_text = mask_text(
        json.dumps(outcome, indent=2, sort_keys=True, default=str), sensitive_values
    )
    _write(directory, OUTCOME_ARTIFACT, outcome_text, result)

    return result


def _write(
    directory: Path,
    name: str,
    text: str,
    result: ArtifactWriteResult,
) -> None:
    """Write one artifact file and record its path on ``result``."""
    target = directory / name
    target.write_text(text)
    result.files[name] = target
