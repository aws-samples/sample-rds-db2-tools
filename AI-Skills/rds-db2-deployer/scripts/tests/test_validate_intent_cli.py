"""Tests for the ``python -m scripts.validate_intent`` CLI (R4.3/R4.4).

The CLI is the intent-validation leg of the required CI gate, alongside
``python -m scripts.policy_gate`` (R12.3/R12.4). Its contract is the exit code:

* ``0`` — the intent passed both validation layers
* ``1`` — the intent is invalid; every failure is reported
* ``2`` — the intent could not be read or parsed

Regression context: this module previously had no ``_main`` and no
``__main__`` guard, so the CI step ``python -m scripts.validate_intent <file>``
imported the module, ignored its argument, and exited 0 for *any* input — the
gate passed unconditionally. :func:`test_module_execution_enforces_exit_code`
is the guard against that specific failure and MUST run the module as a
subprocess: calling :func:`_main` directly would still pass if the
``__main__`` guard went missing again.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.validate_intent import _main

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def _valid_intent() -> dict:
    """A complete Deployment_Intent that passes both layers.

    Mirrors the fixture in ``test_validate_schema.py`` so the two stay in sync.
    """
    return {
        "deployment_tier": "dev",
        "workload_size": "small",
        "region": "us-east-1",
        "engine": "db2-se",
        "engine_version": "12.1.4",
        "master_username": "admin",
        "db_name": "DB2DEV",
        "port": 8392,
        "license_model": "bring-your-own-license",
        "instance_class": "db.r7i.2xlarge",
        "allocated_storage": 100,
        "storage_type": "gp3",
        "multi_az": False,
        "backup_retention_period": 7,
        "publicly_accessible": False,
        "storage_encrypted": True,
        "kms_key_id": "arn:aws:kms:us-east-1:111122223333:key/mrk-abc",
        "vpc_security_group_ids": ["sg-0123456789abcdef0"],
        "vpc_id": "vpc-0123456789abcdef0",
        "db_subnet_group_name": "db2-subnets",
        "db_parameter_group_name": "",
        "monitoring_interval": 0,
        "enable_cloudwatch_logs_exports": ["diag.log", "notify.log"],
        "deletion_protection": True,
        "tags": {"Project": "p", "Environment": "dev", "Owner": "o"},
        "manage_master_user_password": True,
        "master_user_secret_kms_key_id": (
            "arn:aws:kms:us-east-1:111122223333:key/mrk-sec"
        ),
        "ibm_customer_id": "IBM-CUST-001",
        "ibm_site_id": "IBM-SITE-001",
    }


def _write(tmp_path: Path, payload, *, name: str = "deployment-intent.json") -> Path:
    path = tmp_path / name
    if isinstance(payload, str):
        path.write_text(payload)
    else:
        path.write_text(json.dumps(payload, indent=2))
    return path


# --- exit 0: a valid intent -------------------------------------------------


def test_valid_intent_exits_zero(tmp_path, capsys):
    path = _write(tmp_path, _valid_intent())
    assert _main([str(path)]) == 0
    assert "VALID" in capsys.readouterr().out


# --- exit 1: invalid intents block rendering (R4.4) -------------------------


def test_layer1_failure_exits_one(tmp_path, capsys):
    """A schema (Layer-1) failure must block, naming the field and rule."""
    intent = _valid_intent()
    del intent["engine_version"]
    path = _write(tmp_path, intent)

    assert _main([str(path)]) == 1

    captured = capsys.readouterr()
    assert "engine_version" in captured.out
    assert "required" in captured.out
    assert "rendering is blocked" in captured.err


def test_layer2_security_invariant_failure_exits_one(tmp_path, capsys):
    """A security-invariant (Layer-2) failure must block just as hard.

    This is the case that matters most for the gate: the document is
    structurally valid JSON that satisfies the schema, and is rejected only
    because it violates the CMK-everywhere invariant (R6.11).
    """
    intent = _valid_intent()
    intent["kms_key_id"] = "arn:aws:kms:us-east-1:111122223333:alias/aws/rds"
    path = _write(tmp_path, intent)

    assert _main([str(path)]) == 1

    captured = capsys.readouterr()
    assert "kms_key_id" in captured.out
    assert "cmk_not_aws_owned" in captured.out


def test_every_failure_is_reported_not_just_the_first(tmp_path, capsys):
    """R4.3: the report names every failing field, not only the first."""
    intent = _valid_intent()
    intent["kms_key_id"] = "arn:aws:kms:us-east-1:111122223333:alias/aws/rds"
    intent["tags"] = {"Project": "p", "Environment": "dev"}  # Owner missing
    del intent["ibm_site_id"]
    path = _write(tmp_path, intent)

    assert _main([str(path)]) == 1

    out = capsys.readouterr().out
    assert "kms_key_id" in out
    assert "tags.Owner" in out
    assert "ibm_site_id" in out


# --- exit 2: usage / IO errors ---------------------------------------------


def test_missing_file_exits_two(tmp_path, capsys):
    assert _main([str(tmp_path / "does-not-exist.json")]) == 2
    assert "not found" in capsys.readouterr().err


def test_malformed_json_exits_two(tmp_path, capsys):
    path = _write(tmp_path, '{"deployment_tier": "dev",,}')
    assert _main([str(path)]) == 2
    assert "not valid JSON" in capsys.readouterr().err


@pytest.mark.parametrize("payload", ["[1, 2, 3]", '"a string"', "42"])
def test_non_object_document_exits_two(tmp_path, payload, capsys):
    path = _write(tmp_path, payload)
    assert _main([str(path)]) == 2
    assert "must contain a JSON object" in capsys.readouterr().err


# --- the regression guard --------------------------------------------------


def test_module_execution_enforces_exit_code(tmp_path):
    """``python -m scripts.validate_intent`` must actually enforce the gate.

    Run as a subprocess on purpose. The original defect was a missing
    ``_main``/``__main__`` guard, which made the CI step a no-op that exited 0
    for any input; a direct :func:`_main` call cannot detect that, because the
    function would still exist and work. Only module execution proves the CI
    invocation enforces anything.
    """
    intent = _valid_intent()
    del intent["engine_version"]
    bad = _write(tmp_path, intent, name="bad.json")
    good = _write(tmp_path, _valid_intent(), name="good.json")

    invalid_run = subprocess.run(
        [sys.executable, "-m", "scripts.validate_intent", str(bad)],
        cwd=_PACKAGE_ROOT,
        capture_output=True,
        text=True,
    )
    assert invalid_run.returncode == 1, (
        "python -m scripts.validate_intent exited "
        f"{invalid_run.returncode} for an INVALID intent; the CI gate is not "
        "enforcing. stdout=%r stderr=%r" % (invalid_run.stdout, invalid_run.stderr)
    )
    assert "engine_version" in invalid_run.stdout

    valid_run = subprocess.run(
        [sys.executable, "-m", "scripts.validate_intent", str(good)],
        cwd=_PACKAGE_ROOT,
        capture_output=True,
        text=True,
    )
    assert valid_run.returncode == 0, (
        "python -m scripts.validate_intent exited "
        f"{valid_run.returncode} for a VALID intent. stderr=%r" % valid_run.stderr
    )
