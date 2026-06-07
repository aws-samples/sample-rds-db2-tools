"""Verification_Step for the rds-db2-provision-skill (R9, plus R8.5 and R11.7).

This module is the human-in-the-loop gate that sits between the
``Intent_Validator`` and the ``Terraform_Composer`` / ``GitOps_Orchestrator``.
It is deliberately pure (no AWS, no I/O) so it is unit-testable in isolation:
it takes an already-validated ``Deployment_Intent`` (the dict produced by the
resolvers) plus a decision and returns a recorded :class:`ApprovalState`.

Responsibilities (Requirement 9):

* **Echo (R9.1).** :func:`echo_intent` renders every intent field with its
  provenance label (``user_provided`` / ``assumed``) and masks every
  ``Sensitive_Value`` (the fields tracked in
  :data:`render_terraform.SENSITIVE_INTENT_FIELDS`).
* **Recorded approval, default not approved (R9.2, R9.6).** Nothing proceeds
  without a recorded decision; the default :class:`ApprovalState` is *not
  approved*.
* **Auto-approve guardrail (R9.3, R9.4).** Auto-approve is permitted only for
  the ``sandbox`` / ``dev`` tiers and records a justification together with the
  resolved tier. ``prod`` always rejects auto-approve and requires a recorded
  interactive decision.
* **Rejection returns to collection (R9.5).** A rejected echo records the
  feedback and routes back to the ``Intent_Collector``; no Terraform is
  rendered or applied.

Cross-cutting acknowledgements gate approval:

* **SE->AE forced conversion (R8.5).** When the edition resolver recorded an
  ``_edition_conversion`` with ``acknowledgement_required``, the conversion
  warning must be acknowledged before the intent can be approved.
* **Public-facing (R11.7 / R6.4).** When the intent resolves
  ``publicly_accessible=true`` (or the VPC_Precheck surfaced the public-only
  VPC warning), the public-facing warning must be acknowledged before approval.

The verification is never "approved" while a required acknowledgement is
outstanding -- even on the auto-approve path -- which keeps the guardrail honest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

try:  # Prefer the package-qualified module so the sensitive-field set is the
    # same object the composer uses (single source of truth).
    from scripts.render_terraform import SENSITIVE_INTENT_FIELDS
except ImportError:  # Fall back when scripts/ is directly on sys.path.
    from render_terraform import SENSITIVE_INTENT_FIELDS


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The tiers for which the Auto_Approve guardrail may grant approval (R9.3).
#: ``prod`` is deliberately excluded -- it always requires an interactive
#: decision (R9.4).
AUTO_APPROVE_TIERS: tuple[str, ...] = ("sandbox", "dev")

#: The mask substituted for every Sensitive_Value in the echo (R9.1).
SENSITIVE_MASK = "***"

#: Acknowledgement names. Stable identifiers so callers (and tests) can record
#: the specific acknowledgement that gates approval.
ACK_EDITION_SE_TO_AE = "edition_se_to_ae_conversion"
ACK_PUBLIC_FACING = "public_facing"

#: Decision tokens for the recorded :class:`ApprovalState`.
DECISION_PENDING = "pending"
DECISION_AUTO_APPROVED = "auto_approved"
DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"
DECISION_AUTO_APPROVE_DENIED = "auto_approve_denied_prod"

#: Intent keys that are internal bookkeeping (provenance, conversion records,
#: superseded defaults, resolution traces) rather than schema fields; they are
#: not echoed as deployment fields (their content is surfaced separately).
_INTERNAL_KEYS_PREFIX = "_"
_PROVENANCE_KEY = "_provenance"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class VerificationError(Exception):
    """Raised for a malformed verification request (e.g. an unrecognized
    interactive decision token)."""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Acknowledgement:
    """A warning that must be acknowledged before the intent can be approved.

    ``name`` is a stable identifier the caller records to satisfy the gate;
    ``message`` is the human-readable text shown in the Verification_Step.
    """

    name: str
    message: str


@dataclass(frozen=True)
class ApprovalState:
    """The recorded outcome of the Verification_Step for one intent.

    The default instance is the *not approved* state (R9.6): no decision has
    been recorded, so nothing may render or apply.

    Attributes:
        approved: ``True`` only when an affirmative decision is recorded AND all
            required acknowledgements are present.
        tier: the resolved ``deployment_tier`` the decision was recorded for.
        decision: one of the ``DECISION_*`` tokens describing what happened.
        justification: the recorded auto-approve justification (R9.3), or ``None``.
        rejection_feedback: feedback captured when the human rejects (R9.5).
        return_to_collection: ``True`` when the flow must go back to the
            Intent_Collector (a rejection) (R9.5).
        acknowledged: the acknowledgement names the caller supplied.
        missing_acknowledgements: required acknowledgements still outstanding;
            non-empty implies ``approved`` is ``False``.
    """

    approved: bool = False
    tier: Optional[str] = None
    decision: str = DECISION_PENDING
    justification: Optional[str] = None
    rejection_feedback: Optional[str] = None
    return_to_collection: bool = False
    acknowledged: tuple[str, ...] = ()
    missing_acknowledgements: tuple[str, ...] = ()

    @property
    def may_proceed(self) -> bool:
        """Whether render/apply may proceed. Identical to ``approved`` today;
        exposed as intent so call sites read clearly."""
        return self.approved


# ---------------------------------------------------------------------------
# Masking + echo (R9.1)
# ---------------------------------------------------------------------------


def mask_sensitive_value(field_name: str, value: Any) -> Any:
    """Return ``value`` unchanged, or :data:`SENSITIVE_MASK` when ``field_name``
    is a Sensitive_Value (R9.1).

    Masking is keyed on the field name (not the value) so an empty or
    placeholder sensitive value is still masked.
    """
    if field_name in SENSITIVE_INTENT_FIELDS:
        return SENSITIVE_MASK
    return value


def _provenance_label(provenance: Mapping[str, str], field_name: str) -> str:
    """Human-readable provenance label for a field. Fields with no recorded
    provenance (e.g. fields with no tier default that were left set by another
    layer) are labeled ``unmarked`` so the echo never silently drops a field."""
    label = provenance.get(field_name)
    if label in ("user_provided", "assumed"):
        return label
    return "unmarked"


def echo_intent(intent: Mapping[str, Any]) -> str:
    """Render a human-readable echo of every Deployment_Intent field, labeling
    each as ``user_provided`` / ``assumed`` and masking every Sensitive_Value
    (R9.1).

    Internal bookkeeping keys (those beginning with ``_``) are not echoed as
    deployment fields; instead, the superseded tier defaults, the SE->AE
    conversion warning, and the public-facing warning are surfaced as dedicated
    sections so the approver sees exactly what needs acknowledgement.

    Returns a multi-line string suitable for presenting to the approver.
    """
    provenance: Mapping[str, str] = intent.get(_PROVENANCE_KEY, {}) or {}

    lines: list[str] = ["Deployment intent for review:", ""]

    for field_name in sorted(k for k in intent if not k.startswith(_INTERNAL_KEYS_PREFIX)):
        value = mask_sensitive_value(field_name, intent[field_name])
        label = _provenance_label(provenance, field_name)
        lines.append(f"  {field_name} = {value!r}  [{label}]")

    # Surface a prompt value that superseded a tier default (R2.5) so the
    # approver sees what changed.
    superseded = intent.get("_superseded_tier_defaults")
    if superseded:
        lines.append("")
        lines.append("Overridden tier defaults (prompt value applied):")
        for field_name in sorted(superseded):
            shown = mask_sensitive_value(field_name, superseded[field_name])
            lines.append(f"  {field_name}: tier default was {shown!r}")

    # Surface every warning that must be acknowledged before approval.
    acks = required_acknowledgements(intent)
    if acks:
        lines.append("")
        lines.append("Acknowledgement required before approval:")
        for ack in acks:
            lines.append(f"  [{ack.name}] {ack.message}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Required acknowledgements (R8.5, R11.7)
# ---------------------------------------------------------------------------


def _truthy(value: Any) -> bool:
    """Interpret an intent value as a boolean without treating the string
    ``"false"`` as truthy."""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def required_acknowledgements(
    intent: Mapping[str, Any],
    precheck_warnings: Optional[Iterable[Any]] = None,
) -> list[Acknowledgement]:
    """Return the warnings that MUST be acknowledged before this intent can be
    approved.

    Two sources, per the task scope:

    * **SE->AE forced conversion (R8.5).** When the edition resolver recorded an
      ``_edition_conversion`` whose ``acknowledgement_required`` is true, the
      conversion warning is required.
    * **Public-facing (R11.7 / R6.4).** When the intent resolves
      ``publicly_accessible=true``, or the VPC_Precheck surfaced a public-only
      VPC warning, the public-facing warning is required.

    Args:
        intent: the resolved intent.
        precheck_warnings: optional VPC_Precheck warnings (objects with a
            ``name`` attribute, e.g. :class:`vpc_precheck.PrecheckFinding`). A
            ``public_only_vpc`` warning also triggers the public-facing ack.

    Returns:
        A de-duplicated list of :class:`Acknowledgement`, stable in order.
    """
    acks: list[Acknowledgement] = []

    conversion = intent.get("_edition_conversion")
    if isinstance(conversion, Mapping) and conversion.get("acknowledgement_required"):
        reason = conversion.get("reason") or (
            f"Engine converted {conversion.get('from')!r} -> "
            f"{conversion.get('to')!r} to satisfy the IBM Db2 Standard Edition "
            "licensing ceiling."
        )
        acks.append(Acknowledgement(name=ACK_EDITION_SE_TO_AE, message=str(reason)))

    public_required = _truthy(intent.get("publicly_accessible"))
    if not public_required and precheck_warnings:
        public_required = any(
            getattr(w, "name", None) == "public_only_vpc" for w in precheck_warnings
        )
    if public_required:
        acks.append(
            Acknowledgement(
                name=ACK_PUBLIC_FACING,
                message=(
                    "This deployment is public-facing (publicly_accessible=true "
                    "or a public-only target VPC). Best practice is that an RDS "
                    "for Db2 database should not be public-facing unless "
                    "absolutely required. Acknowledge to proceed."
                ),
            )
        )

    return acks


# ---------------------------------------------------------------------------
# Approval resolution (R9.2-R9.6)
# ---------------------------------------------------------------------------


def _normalize_decision(decision: Any) -> Optional[str]:
    """Normalize an interactive decision to ``"approve"`` / ``"reject"`` /
    ``None``.

    Accepts booleans (``True`` -> approve, ``False`` -> reject) and the strings
    ``approve``/``approved``/``yes`` and ``reject``/``rejected``/``no``. ``None``
    means no decision has been recorded yet (R9.6).
    """
    if decision is None:
        return None
    if isinstance(decision, bool):
        return "approve" if decision else "reject"
    if isinstance(decision, str):
        token = decision.strip().lower()
        if token in ("approve", "approved", "yes", "y"):
            return "approve"
        if token in ("reject", "rejected", "no", "n"):
            return "reject"
    raise VerificationError(
        f"Unrecognized interactive decision {decision!r}; expected an approve/"
        "reject decision or None."
    )


def resolve_approval(
    intent: Mapping[str, Any],
    *,
    interactive_decision: Any = None,
    auto_approve: bool = False,
    justification: Optional[str] = None,
    acknowledgements: Optional[Sequence[str]] = None,
    rejection_feedback: Optional[str] = None,
    precheck_warnings: Optional[Iterable[Any]] = None,
) -> ApprovalState:
    """Resolve the recorded approval state for a validated intent (R9.2-R9.6).

    The default (no auto-approve, no interactive decision) is *not approved*
    (R9.6). An approval -- by either path -- is only granted once every required
    acknowledgement (R8.5, R11.7) is present.

    Args:
        intent: the validated Deployment_Intent.
        interactive_decision: a recorded human decision -- ``"approve"`` /
            ``"reject"`` / a bool / ``None`` (no decision yet).
        auto_approve: whether the Auto_Approve path was requested.
        justification: the auto-approve justification to record (R9.3). A
            default is synthesized when omitted.
        acknowledgements: the acknowledgement names the caller has recorded.
        rejection_feedback: feedback to capture on a rejection (R9.5).
        precheck_warnings: optional VPC_Precheck warnings that may add a
            required acknowledgement (public-only VPC).

    Returns:
        The recorded :class:`ApprovalState`.
    """
    tier = intent.get("deployment_tier")
    provided: set[str] = set(acknowledgements or ())
    required = [a.name for a in required_acknowledgements(intent, precheck_warnings)]
    missing = tuple(name for name in required if name not in provided)
    acknowledged = tuple(sorted(provided))

    decision = _normalize_decision(interactive_decision)

    # --- Auto-approve path (only when no interactive decision overrides it) ---
    if auto_approve and decision is None:
        if tier in AUTO_APPROVE_TIERS:
            # Even auto-approve cannot proceed while an acknowledgement is
            # outstanding -- the guardrail stays honest.
            if missing:
                return ApprovalState(
                    approved=False,
                    tier=tier,
                    decision=DECISION_PENDING,
                    acknowledged=acknowledged,
                    missing_acknowledgements=missing,
                )
            recorded_justification = justification or (
                f"Auto-approved under the Auto_Approve guardrail for the "
                f"{tier!r} tier."
            )
            return ApprovalState(
                approved=True,
                tier=tier,
                decision=DECISION_AUTO_APPROVED,
                justification=recorded_justification,
                acknowledged=acknowledged,
            )
        # prod (or any non-auto-approvable tier): reject auto-approve and
        # require a recorded interactive decision (R9.4).
        return ApprovalState(
            approved=False,
            tier=tier,
            decision=DECISION_AUTO_APPROVE_DENIED,
            acknowledged=acknowledged,
            missing_acknowledgements=missing,
        )

    # --- Interactive path ---
    if decision is None:
        # No decision recorded -> not approved (R9.6).
        return ApprovalState(
            approved=False,
            tier=tier,
            decision=DECISION_PENDING,
            acknowledged=acknowledged,
            missing_acknowledgements=missing,
        )

    if decision == "reject":
        # Rejection routes back to the Intent_Collector (R9.5).
        return ApprovalState(
            approved=False,
            tier=tier,
            decision=DECISION_REJECTED,
            rejection_feedback=rejection_feedback,
            return_to_collection=True,
            acknowledged=acknowledged,
            missing_acknowledgements=missing,
        )

    # decision == "approve": affirmative, but acknowledgements gate it.
    if missing:
        return ApprovalState(
            approved=False,
            tier=tier,
            decision=DECISION_PENDING,
            acknowledged=acknowledged,
            missing_acknowledgements=missing,
        )
    return ApprovalState(
        approved=True,
        tier=tier,
        decision=DECISION_APPROVED,
        justification=justification,
        acknowledged=acknowledged,
    )
