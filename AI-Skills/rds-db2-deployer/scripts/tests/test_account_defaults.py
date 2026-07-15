"""Tests for the account-defaults loader/schema changes:

* engine_major_version is an optional META field (11.5/12.1), surfaced via
  engine_major_version_from_defaults, and NOT merged into the intent (the
  composer would halt on an unmapped field).
* tags (with Project and Owner) are required and non-empty.
* the create-on-blank reusable fields still accept "" (create-on-first-deploy).
"""

from __future__ import annotations

import json

import pytest

from scripts.account_defaults import (
    AccountDefaultsError,
    engine_major_version_from_defaults,
    intent_fields_from_defaults,
    load_account_defaults,
)


def _write(tmp_path, doc) -> str:
    p = tmp_path / "account-defaults.json"
    p.write_text(json.dumps(doc))
    return str(p)


def _minimal() -> dict:
    return {"tags": {"Project": "ACME", "Owner": "db-team"}}


def test_minimal_with_tags_is_valid(tmp_path):
    d = load_account_defaults(_write(tmp_path, _minimal()))
    assert d["tags"]["Project"] == "ACME"


def test_tags_required(tmp_path):
    with pytest.raises(AccountDefaultsError):
        load_account_defaults(_write(tmp_path, {"region": "us-east-1"}))


def test_tags_project_must_be_non_empty(tmp_path):
    with pytest.raises(AccountDefaultsError):
        load_account_defaults(_write(tmp_path, {"tags": {"Project": "", "Owner": "x"}}))


@pytest.mark.parametrize("major", ["11.5", "12.1"])
def test_engine_major_version_accepted_and_surfaced(tmp_path, major):
    doc = _minimal(); doc["engine_major_version"] = major
    d = load_account_defaults(_write(tmp_path, doc))
    assert engine_major_version_from_defaults(d) == major


def test_engine_major_version_rejects_invalid(tmp_path):
    doc = _minimal(); doc["engine_major_version"] = "10.5"
    with pytest.raises(AccountDefaultsError):
        load_account_defaults(_write(tmp_path, doc))


def test_engine_major_version_absent_is_none(tmp_path):
    d = load_account_defaults(_write(tmp_path, _minimal()))
    assert engine_major_version_from_defaults(d) is None


def test_engine_major_version_is_meta_not_merged_into_intent(tmp_path):
    doc = _minimal(); doc["engine_major_version"] = "11.5"
    d = load_account_defaults(_write(tmp_path, doc))
    assert "engine_major_version" not in intent_fields_from_defaults(d)


def test_create_on_blank_fields_accept_empty(tmp_path):
    doc = _minimal()
    doc.update({
        "db_subnet_group_name": "",
        "kms_key_id": "",
        "master_user_secret_kms_key_id": "",
        "monitoring_role_arn": "",
    })
    d = load_account_defaults(_write(tmp_path, doc))
    assert d["kms_key_id"] == ""


# --- aws_profile (META) ----------------------------------------------------

from scripts.account_defaults import aws_profile_from_defaults


def test_aws_profile_surfaced_and_is_meta(tmp_path):
    doc = _minimal(); doc["aws_profile"] = "burner"
    d = load_account_defaults(_write(tmp_path, doc))
    assert aws_profile_from_defaults(d) == "burner"
    assert "aws_profile" not in intent_fields_from_defaults(d)  # never an intent field


def test_aws_profile_empty_or_absent_is_none(tmp_path):
    d1 = load_account_defaults(_write(tmp_path, _minimal()))
    assert aws_profile_from_defaults(d1) is None
    doc = _minimal(); doc["aws_profile"] = ""
    d2 = load_account_defaults(_write(tmp_path, doc))
    assert aws_profile_from_defaults(d2) is None


# --- db_instance_identifier (optional, merged intent field) ----------------


def test_db_instance_identifier_valid_is_merged(tmp_path):
    doc = _minimal(); doc["db_instance_identifier"] = "db2-dev-1"
    d = load_account_defaults(_write(tmp_path, doc))
    assert intent_fields_from_defaults(d)["db_instance_identifier"] == "db2-dev-1"


def test_db_instance_identifier_rejects_bad_format(tmp_path):
    doc = _minimal(); doc["db_instance_identifier"] = "1-bad-start"  # must start with a letter
    with pytest.raises(AccountDefaultsError):
        load_account_defaults(_write(tmp_path, doc))


def test_db_instance_identifier_rejects_empty(tmp_path):
    # empty is rejected so an omitted field (auto-derive) is the only "no name" path
    doc = _minimal(); doc["db_instance_identifier"] = ""
    with pytest.raises(AccountDefaultsError):
        load_account_defaults(_write(tmp_path, doc))
