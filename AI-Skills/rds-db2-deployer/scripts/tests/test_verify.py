"""Unit tests for the Verification_Step (task 9, R9 + R8.5 + R11.7).

Covers the approval gates and Sensitive_Value masking:

* masking of sensitive fields in the echo (R9.1);
* default state is not approved (R9.6);
* sandbox/dev auto-approve records a justification + tier (R9.3);
* prod rejects auto-approve and requires an interactive approval (R9.4);
* rejection returns to the Intent_Collector (R9.5);
* the SE->AE forced-conversion and public-facing acknowledgements gate approval
  (R8.5, R11.7).

No real AWS and no LLM: every intent is constructed in memory.

Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 8.5, 11.7
"""

from __future__ import annotations

import pytest

from scripts.render_terraform import SENSITIVE_INTENT_FIELDS
from scripts.verify import (
    ACK_EDITION_SE_TO_AE,
    ACK_PUBLIC_FACING,
    AUTO_APPROVE_TIERS,
    DECISION_APPROVED,
    DECISION_AUTO_APPROVED,
    DECISION_AUTO_APPROVE_DENIED,
    DECISION_PENDING,
    DECISION_REJECTED,
    SENSITIVE_MASK,
    Acknowledgement,
    ApprovalState,
    VerificationError,
    echo_intent,
    mask_sensitive_value,
    required_acknowledgements,
    resolve_approval,
)


# ---------------------------------------------------------------------------
# Intent fixtures
# ---------------------------------------------------------------------------


def _base_intent(tier: str = "sandbox", **overrides):
    intent = {
        "deployment_tier": tier,
        "engine": "db2-se",
        "engine_version": "12.1.4",
        "instance_class": "db.t3.xlarge",
        "allocated_storage": 40,
        "storage_type": "gp3",
        "multi_az": False,
        "db_name": "DB2DB",
        "master_username": "admin",
        "port": 8392,
        "publicly_accessible": False,
        "ibm_customer_id": "CUST-123456",
        "ibm_site_id": "SITE-987654",
        "master_password": "hunter2-secret",
        "_provenance": {
            "deployment_tier": "assumed",
            "engine": "assumed",
            "instance_class": "assumed",
            "ibm_customer_id": "user_provided",
            "ibm_site_id": "user_provided",
            "master_password": "user_provided",
        },
    }
    intent.update(overrides)
    return intent


def _se_to_ae_intent(tier: str = "dev"):
    intent = _base_intent(tier=tier, engine="db2-ae", instance_class="db.x2iedn.16xlarge")
    intent["_edition_conversion"] = {
        "from": "db2-se",
        "to": "db2-ae",
        "reason": "64 vCPU exceeds the SE maximum of 32.",
        "acknowledgement_required": True,
    }
    return intent


# ---------------------------------------------------------------------------
# Masking + echo (R9.1)
# ---------------------------------------------------------------------------


def test_sensitive_field_set_is_nonempty():
    # Guard: the masking depends on this set being populated.
    assert SENSITIVE_INTENT_FIELDS


@pytest.mark.parametrize("field_name", sorted(SENSITIVE_INTENT_FIELDS))
def test_mask_sensitive_value_masks_each_sensitive_field(field_name):
    assert mask_sensitive_value(field_name, "real-secret-value") == SENSITIVE_MASK


def test_mask_sensitive_value_passes_through_non_sensitive():
    assert mask_sensitive_value("instance_class", "db.t3.xlarge") == "db.t3.xlarge"


def test_echo_masks_every_sensitive_value_and_never_leaks():
    intent = _base_intent()
    echo = echo_intent(intent)
    # Each sensitive raw value must not appear; the mask must.
    assert "CUST-123456" not in echo
    assert "SITE-987654" not in echo
    assert "hunter2-secret" not in echo
    assert SENSITIVE_MASK in echo


def test_echo_labels_provenance_for_fields():
    intent = _base_intent()
    echo = echo_intent(intent)
    # A user_provided field and an assumed field are both labeled.
    assert "[assumed]" in echo
    assert "[user_provided]" in echo
    # A representative non-sensitive field is echoed with its value.
    assert "instance_class" in echo
    assert "db.t3.xlarge" in echo


def test_echo_surfaces_required_acknowledgements():
    intent = _se_to_ae_intent()
    echo = echo_intent(intent)
    assert "Acknowledgement required before approval" in echo
    assert ACK_EDITION_SE_TO_AE in echo


# ---------------------------------------------------------------------------
# Default state: not approved (R9.6)
# ---------------------------------------------------------------------------


def test_default_approval_state_is_not_approved():
    assert ApprovalState().approved is False
    assert ApprovalState().decision == DECISION_PENDING
    assert ApprovalState().may_proceed is False


def test_no_decision_no_auto_approve_is_not_approved():
    state = resolve_approval(_base_intent())
    assert state.approved is False
    assert state.decision == DECISION_PENDING
    assert state.return_to_collection is False


# ---------------------------------------------------------------------------
# Auto-approve guardrail (R9.3 / R9.4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", AUTO_APPROVE_TIERS)
def test_auto_approve_grants_for_sandbox_and_dev_and_records_justification(tier):
    state = resolve_approval(_base_intent(tier=tier), auto_approve=True)
    assert state.approved is True
    assert state.decision == DECISION_AUTO_APPROVED
    assert state.tier == tier
    assert state.justification  # non-empty justification recorded
    assert tier in state.justification


def test_auto_approve_records_custom_justification():
    state = resolve_approval(
        _base_intent(tier="dev"),
        auto_approve=True,
        justification="CI nightly smoke deploy",
    )
    assert state.approved is True
    assert state.justification == "CI nightly smoke deploy"


def test_prod_rejects_auto_approve_and_requires_interactive():
    state = resolve_approval(_base_intent(tier="prod"), auto_approve=True)
    assert state.approved is False
    assert state.decision == DECISION_AUTO_APPROVE_DENIED

    # An explicit interactive approval is then honored for prod.
    approved = resolve_approval(
        _base_intent(tier="prod"), interactive_decision="approve"
    )
    assert approved.approved is True
    assert approved.decision == DECISION_APPROVED


def test_prod_interactive_decision_overrides_auto_approve_request():
    # Even if auto_approve is requested, a recorded prod interactive approval
    # is what counts.
    state = resolve_approval(
        _base_intent(tier="prod"),
        auto_approve=True,
        interactive_decision="approve",
    )
    assert state.approved is True
    assert state.decision == DECISION_APPROVED


# ---------------------------------------------------------------------------
# Interactive approve / reject (R9.2 / R9.5)
# ---------------------------------------------------------------------------


def test_interactive_approve_grants():
    state = resolve_approval(_base_intent(), interactive_decision=True)
    assert state.approved is True
    assert state.decision == DECISION_APPROVED


def test_rejection_returns_to_collection():
    state = resolve_approval(
        _base_intent(),
        interactive_decision="reject",
        rejection_feedback="wrong instance class",
    )
    assert state.approved is False
    assert state.decision == DECISION_REJECTED
    assert state.return_to_collection is True
    assert state.rejection_feedback == "wrong instance class"


def test_unrecognized_decision_raises():
    with pytest.raises(VerificationError):
        resolve_approval(_base_intent(), interactive_decision="maybe")


# ---------------------------------------------------------------------------
# Acknowledgement gating: SE->AE (R8.5)
# ---------------------------------------------------------------------------


def test_se_to_ae_acknowledgement_required():
    intent = _se_to_ae_intent()
    names = [a.name for a in required_acknowledgements(intent)]
    assert ACK_EDITION_SE_TO_AE in names


def test_se_to_ae_blocks_interactive_approval_until_acknowledged():
    intent = _se_to_ae_intent()
    # Approve without acknowledging -> not approved, ack still outstanding.
    blocked = resolve_approval(intent, interactive_decision="approve")
    assert blocked.approved is False
    assert ACK_EDITION_SE_TO_AE in blocked.missing_acknowledgements

    # Approve WITH the acknowledgement -> approved.
    ok = resolve_approval(
        intent,
        interactive_decision="approve",
        acknowledgements=[ACK_EDITION_SE_TO_AE],
    )
    assert ok.approved is True


def test_se_to_ae_blocks_auto_approve_until_acknowledged():
    intent = _se_to_ae_intent(tier="dev")
    blocked = resolve_approval(intent, auto_approve=True)
    assert blocked.approved is False
    assert ACK_EDITION_SE_TO_AE in blocked.missing_acknowledgements

    ok = resolve_approval(
        intent, auto_approve=True, acknowledgements=[ACK_EDITION_SE_TO_AE]
    )
    assert ok.approved is True
    assert ok.decision == DECISION_AUTO_APPROVED


# ---------------------------------------------------------------------------
# Acknowledgement gating: public-facing (R11.7 / R6.4)
# ---------------------------------------------------------------------------


def test_public_facing_acknowledgement_required_when_publicly_accessible():
    intent = _base_intent(publicly_accessible=True)
    names = [a.name for a in required_acknowledgements(intent)]
    assert ACK_PUBLIC_FACING in names


def test_public_facing_blocks_approval_until_acknowledged():
    intent = _base_intent(publicly_accessible=True)
    blocked = resolve_approval(intent, interactive_decision="approve")
    assert blocked.approved is False
    assert ACK_PUBLIC_FACING in blocked.missing_acknowledgements

    ok = resolve_approval(
        intent,
        interactive_decision="approve",
        acknowledgements=[ACK_PUBLIC_FACING],
    )
    assert ok.approved is True


def test_public_only_vpc_precheck_warning_triggers_public_ack():
    intent = _base_intent(publicly_accessible=False)

    class _Warning:
        name = "public_only_vpc"

    names = [
        a.name
        for a in required_acknowledgements(intent, precheck_warnings=[_Warning()])
    ]
    assert ACK_PUBLIC_FACING in names


def test_no_acknowledgements_for_plain_private_intent():
    assert required_acknowledgements(_base_intent()) == []


def test_both_acknowledgements_required_together():
    intent = _se_to_ae_intent(tier="dev")
    intent["publicly_accessible"] = True
    names = {a.name for a in required_acknowledgements(intent)}
    assert names == {ACK_EDITION_SE_TO_AE, ACK_PUBLIC_FACING}

    # Acknowledging only one is insufficient.
    partial = resolve_approval(
        intent, auto_approve=True, acknowledgements=[ACK_PUBLIC_FACING]
    )
    assert partial.approved is False
    assert ACK_EDITION_SE_TO_AE in partial.missing_acknowledgements

    full = resolve_approval(
        intent,
        auto_approve=True,
        acknowledgements=[ACK_PUBLIC_FACING, ACK_EDITION_SE_TO_AE],
    )
    assert full.approved is True
