"""Tests for the six-layer skill-package Preflight_Check + eval gate.

Exercises :mod:`scripts.eval.preflight` against the real shipping package (the
positive path: every layer passes and the gate is CR-ready when the test layer is
recorded passed) and against deliberately broken in-memory packages (each of the
six layers must produce a blocking finding for its own defect). No AWS, no network.

These cover the design Testing Strategy item "Skill-package validation: the
six-layer preflight + eval gate used by the sibling ``rds-db2`` skill" and the
packaging requirements R1.1/1.2/1.3/1.4/1.5 it enforces.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from scripts.eval import preflight  # noqa: E402


# --------------------------------------------------------------------------- #
# Positive path: the real shipping package
# --------------------------------------------------------------------------- #
def test_real_package_passes_layers_1_to_4_and_6():
    """Layers 1-4 and 6 run live against the shipping package and pass; the
    gate is CR-ready once Layer 5 is recorded passed (R1.1-1.5, R4.1)."""
    report = preflight.run_preflight(
        PACKAGE_ROOT,
        venv_python=sys.executable,
        test_result=(True, "recorded"),
    )
    by_layer = {layer.layer: layer for layer in report.layers}
    for n in (1, 2, 3, 4, 5, 6):
        assert by_layer[n].passed, (n, by_layer[n].findings)
    assert report.cr_ready
    assert report.blocking_findings == []


def test_distinct_name_from_sibling():
    """Layer 1 confirms this skill's name is not the sibling 'rds-db2' (R1.3)."""
    text = (PACKAGE_ROOT / "SKILL.md").read_text(encoding="utf-8")
    fm, _body, ok = preflight.parse_skill_md(text)
    assert ok
    assert fm["name"] != preflight.SIBLING_SKILL_NAME
    assert isinstance(fm["name"], str) and fm["name"]


def test_layer5_skipped_makes_report_not_cr_ready():
    """With no recorded result and run_tests=False, Layer 5 skips and the gate
    is not CR-ready (the suite must be run or recorded to pass the gate)."""
    report = preflight.run_preflight(
        PACKAGE_ROOT, venv_python=sys.executable, run_tests=False
    )
    layer5 = next(layer for layer in report.layers if layer.layer == 5)
    assert layer5.skipped
    assert not report.cr_ready


# --------------------------------------------------------------------------- #
# Negative paths: a broken copy of the package fails the relevant layer
# --------------------------------------------------------------------------- #
@pytest.fixture
def package_copy(tmp_path: Path) -> Path:
    dest = tmp_path / "skill"
    shutil.copytree(
        PACKAGE_ROOT,
        dest,
        ignore=shutil.ignore_patterns(
            ".venv", ".git", ".hypothesis", ".pytest_cache",
            "__pycache__", "*.egg-info",
        ),
    )
    return dest


def _layer(report: preflight.PreflightReport, n: int) -> preflight.LayerResult:
    return next(layer for layer in report.layers if layer.layer == n)


def test_layer1_flags_missing_frontmatter_field(package_copy: Path):
    text = (package_copy / "SKILL.md").read_text(encoding="utf-8")
    # Drop the owner_team line from the frontmatter.
    broken = "\n".join(
        line for line in text.splitlines() if not line.startswith("owner_team:")
    )
    (package_copy / "SKILL.md").write_text(broken, encoding="utf-8")
    report = preflight.run_preflight(
        package_copy, venv_python=sys.executable, test_result=(True, "x")
    )
    assert not _layer(report, 1).passed
    assert any("owner_team" in f.location for f in _layer(report, 1).findings)


def test_layer1_flags_name_equal_to_sibling(package_copy: Path):
    text = (package_copy / "SKILL.md").read_text(encoding="utf-8")
    broken = text.replace("name: rds-db2-deployer", "name: rds-db2")
    (package_copy / "SKILL.md").write_text(broken, encoding="utf-8")
    report = preflight.run_preflight(
        package_copy, venv_python=sys.executable, test_result=(True, "x")
    )
    assert not _layer(report, 1).passed


def test_layer2_flags_broken_reference_link(package_copy: Path):
    (package_copy / "references" / "intent-and-tiers.md").unlink()
    report = preflight.run_preflight(
        package_copy, venv_python=sys.executable, test_result=(True, "x")
    )
    assert not _layer(report, 2).passed


def test_layer3_flags_malformed_schema(package_copy: Path):
    schema = package_copy / "schemas" / "deployment-intent.schema.json"
    schema.write_text("{ not valid json", encoding="utf-8")
    report = preflight.run_preflight(
        package_copy, venv_python=sys.executable, test_result=(True, "x")
    )
    assert not _layer(report, 3).passed


def test_layer3_flags_invalid_meta_schema(package_copy: Path):
    schema = package_copy / "schemas" / "deployment-intent.schema.json"
    schema.write_text(
        json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "not-a-real-type"}),
        encoding="utf-8",
    )
    report = preflight.run_preflight(
        package_copy, venv_python=sys.executable, test_result=(True, "x")
    )
    assert not _layer(report, 3).passed


def test_layer4_flags_unimportable_script(package_copy: Path):
    (package_copy / "scripts" / "broken_probe.py").write_text(
        "import this_module_does_not_exist_xyz\n", encoding="utf-8"
    )
    report = preflight.run_preflight(
        package_copy, venv_python=sys.executable, test_result=(True, "x")
    )
    assert not _layer(report, 4).passed


def test_layer6_flags_missing_artifacts_dir(package_copy: Path):
    shutil.rmtree(package_copy / "artifacts")
    report = preflight.run_preflight(
        package_copy, venv_python=sys.executable, test_result=(True, "x")
    )
    assert not _layer(report, 6).passed
