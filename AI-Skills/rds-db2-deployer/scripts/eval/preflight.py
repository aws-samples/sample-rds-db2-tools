"""Six-layer skill-package Preflight_Check + eval gate for ``rds-db2-provision``.

This is the launch/eval-gate realization of the **six-layer preflight** the
design's Testing Strategy calls for ("Skill-package validation: the six-layer
preflight + eval gate used by the sibling ``rds-db2`` skill"). The sibling ships
a concrete runner at ``rds-db2-skills/launch/preflight_check.py`` whose six layers
are documented in ``launch/PREFLIGHT.md``; that runner is hard-wired to the
advisory skill's router format, must-surface section, and metadata vocabulary, so
it cannot be pointed at this composer package directly. This module implements the
**equivalent** six layers, adapted to this package's shape, as a fast, no-network,
no-AWS validator with an importable entry point and a ``__main__`` CLI that exits
non-zero iff any blocking (P0/P1) finding remains.

The six layers (mirroring the sibling's structure)
---------------------------------------------------
* **Layer 1 — SKILL.md frontmatter validity (R1.2/1.3).** The leading fenced
  ``---`` block parses (PyYAML when available, else a dependency-free manual
  parser); each required field (``name``, ``description``, ``version``,
  ``metadata``) is present and non-empty;
  ``version`` is a positive integer; ``name`` is a non-empty string **distinct**
  from the sibling ``rds-db2`` name so the two skills are independently
  addressable.
* **Layer 2 — Reference / cited-path link integrity (R1.1).** Every
  ``references/<file>.md`` and ``schemas/<file>.json`` link in ``SKILL.md``
  resolves to a real file on disk, and every shipped ``references/*.md`` is
  reachable from the Router (no orphan references).
* **Layer 3 — Schema validity (R4.1).** ``schemas/deployment-intent.schema.json``
  is well-formed JSON, declares its dialect (``$schema``), and passes the
  meta-schema check (``Draft202012Validator.check_schema``).
* **Layer 4 — Script importability (R4/R10).** Every ``scripts/*.py`` module
  (and ``scripts/eval/*.py``) imports without error.
* **Layer 5 — Test suite passes.** The ``scripts/tests`` pytest suite (unit +
  Hypothesis property tests, Properties 1–14) passes. Runnable inline
  (``run_tests=True``) or recorded from an external run (``test_result=...``) so
  the harness need not re-run the multi-minute suite on every import.
* **Layer 6 — Packaging / Agent Skills layout (R1.1).** The package root contains
  ``SKILL.md``, ``references/``, at least one of ``scripts/``/``templates/``, and
  ``artifacts/``; the composer-not-engine statement and the ``rds-db2`` xref are
  present; references are one level deep.

Public API
----------
* :func:`run_preflight` ``(skill_dir, *, run_tests, test_result, venv_python) ->
  PreflightReport`` — importable, testable entry point.
* :class:`Finding` / :class:`LayerResult` / :class:`PreflightReport` — records.
* ``__main__`` CLI — runs against the package this file ships in and prints the
  grouped report, exiting non-zero iff a blocking finding exists.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- #
# Package paths. preflight.py -> scripts/eval/ -> scripts/ -> package root.
# --------------------------------------------------------------------------- #
_THIS_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = _THIS_DIR.parents[1]

# The sibling advisory skill's name, which THIS skill's name must NOT equal
# (R1.3 — the two skills are independently addressable).
SIBLING_SKILL_NAME = "rds-db2"

REQUIRED_FRONTMATTER_FIELDS = (
    "name",
    "description",
    "version",
    "metadata",
)

# Agent Skills layout: package root must contain SKILL.md + references/ +
# (scripts/ or templates/) + artifacts/ (R1.1).
REQUIRED_ROOT_ENTRIES = ("SKILL.md", "references", "artifacts")

__all__ = [
    "Finding",
    "LayerResult",
    "PreflightReport",
    "run_preflight",
    "parse_skill_md",
    "main",
]


# =========================================================================== #
# Finding / result records
# =========================================================================== #
@dataclass(frozen=True)
class Finding:
    """A single preflight finding. P0/P1 block; P2+ are informational."""

    layer: int
    priority: str  # P0..P4
    location: str
    detail: str

    @property
    def blocking(self) -> bool:
        return self.priority in ("P0", "P1")


@dataclass
class LayerResult:
    """The outcome of one of the six layers."""

    layer: int
    name: str
    findings: List[Finding] = field(default_factory=list)
    skipped: bool = False
    note: str = ""

    @property
    def passed(self) -> bool:
        return not self.skipped and not any(f.blocking for f in self.findings)


@dataclass
class PreflightReport:
    """The aggregate report across all six layers."""

    layers: List[LayerResult]

    @property
    def findings(self) -> List[Finding]:
        out: List[Finding] = []
        for layer in self.layers:
            out.extend(layer.findings)
        return out

    @property
    def blocking_findings(self) -> List[Finding]:
        return [f for f in self.findings if f.blocking]

    @property
    def cr_ready(self) -> bool:
        """CR-ready iff no layer was forced to skip and no blocking finding remains."""
        return not self.blocking_findings and all(
            not layer.skipped for layer in self.layers
        )


# =========================================================================== #
# SKILL.md frontmatter parsing (PyYAML when available, manual fallback)
# =========================================================================== #
_INLINE_LIST_RE = re.compile(r"^\[(.*)\]$")
_INT_RE = re.compile(r"^-?\d+$")


def _parse_scalar(raw: str) -> object:
    value = raw.strip()
    list_match = _INLINE_LIST_RE.match(value)
    if list_match is not None:
        inner = list_match.group(1).strip()
        if not inner:
            return []
        return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
    if _INT_RE.match(value):
        return int(value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _parse_frontmatter_manual(block: str) -> Dict[str, object]:
    frontmatter: Dict[str, object] = {}
    current_parent: Optional[str] = None
    for raw_line in block.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if ":" not in raw_line:
            continue
        key, _, value = raw_line.strip().partition(":")
        key = key.strip()
        value = value.strip()
        if indent == 0:
            if value == "":
                frontmatter[key] = {}
                current_parent = key
            else:
                frontmatter[key] = _parse_scalar(value)
                current_parent = None
        elif current_parent is not None and isinstance(
            frontmatter.get(current_parent), dict
        ):
            frontmatter[current_parent][key] = _parse_scalar(value)  # type: ignore[index]
    return frontmatter


def _parse_frontmatter(block: str) -> Tuple[Dict[str, object], bool]:
    """Return (frontmatter_dict, parsed_ok)."""
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(block)
        if isinstance(loaded, dict):
            return loaded, True
        return {}, False
    except ImportError:
        parsed = _parse_frontmatter_manual(block)
        return parsed, bool(parsed)
    except Exception:
        return {}, False


def parse_skill_md(text: str) -> Tuple[Dict[str, object], str, bool]:
    """Split SKILL.md into (frontmatter, body, frontmatter_parsed_ok)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, False
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            block = "\n".join(lines[1:idx])
            body = "\n".join(lines[idx + 1:])
            fm, ok = _parse_frontmatter(block)
            return fm, body, ok
    fm, ok = _parse_frontmatter("\n".join(lines[1:]))
    return fm, "", ok


# =========================================================================== #
# Layer 1 — SKILL.md frontmatter validity (R1.2/1.3)
# =========================================================================== #
def _check_layer1(skill_md_text: str) -> LayerResult:
    result = LayerResult(layer=1, name="SKILL.md frontmatter validity")
    fm, _body, parsed_ok = parse_skill_md(skill_md_text)

    if not skill_md_text:
        result.findings.append(
            Finding(1, "P0", "SKILL.md", "SKILL.md is missing or empty.")
        )
        return result
    if not parsed_ok:
        result.findings.append(
            Finding(1, "P0", "SKILL.md:frontmatter",
                    "Frontmatter block did not parse as a YAML mapping.")
        )
        return result

    for fieldname in REQUIRED_FRONTMATTER_FIELDS:
        if fieldname not in fm:
            result.findings.append(
                Finding(1, "P0", f"SKILL.md:frontmatter.{fieldname}",
                        f"required frontmatter field '{fieldname}' is absent.")
            )
            continue
        value = fm[fieldname]
        empty = value is None or (
            isinstance(value, (str, list, dict)) and len(value) == 0
        )
        if empty:
            result.findings.append(
                Finding(1, "P0", f"SKILL.md:frontmatter.{fieldname}",
                        f"required frontmatter field '{fieldname}' is empty.")
            )

    version = fm.get("version")
    if version is not None and not (isinstance(version, int) and version > 0):
        result.findings.append(
            Finding(1, "P0", "SKILL.md:frontmatter.version",
                    f"version must be a positive integer (got {version!r}).")
        )

    name = fm.get("name")
    if isinstance(name, str):
        if name == SIBLING_SKILL_NAME:
            result.findings.append(
                Finding(1, "P0", "SKILL.md:frontmatter.name",
                        f"name '{name}' equals the sibling skill name "
                        f"'{SIBLING_SKILL_NAME}'; the two skills must be "
                        "independently addressable (R1.3).")
            )
    elif "name" in fm:
        result.findings.append(
            Finding(1, "P0", "SKILL.md:frontmatter.name",
                    "name must be a string.")
        )

    result.note = f"name={fm.get('name')!r} version={fm.get('version')!r}"
    return result


# =========================================================================== #
# Layer 2 — Reference / cited-path link integrity (R1.1)
# =========================================================================== #
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\((references/[^)#]+|schemas/[^)#]+)\)")


def _check_layer2(skill_dir: Path, skill_md_text: str) -> LayerResult:
    result = LayerResult(layer=2, name="Reference link integrity")
    cited: set[str] = set()
    for match in _MD_LINK_RE.finditer(skill_md_text):
        rel = match.group(1).strip()
        cited.add(rel)
        target = skill_dir / rel
        if not target.is_file():
            result.findings.append(
                Finding(2, "P1", f"SKILL.md -> {rel}",
                        f"cited path '{rel}' does not resolve to a file.")
            )

    # Orphan check: every shipped references/*.md should be cited in SKILL.md.
    refs_dir = skill_dir / "references"
    cited_ref_basenames = {
        os.path.basename(c) for c in cited if c.startswith("references/")
    }
    if refs_dir.is_dir():
        for ref in sorted(refs_dir.glob("*.md")):
            if ref.name not in cited_ref_basenames:
                result.findings.append(
                    Finding(2, "P2", f"references/{ref.name}",
                            "reference file is not linked from SKILL.md (orphan).")
                )
    result.note = f"{len(cited)} cited paths checked"
    return result


# =========================================================================== #
# Layer 3 — Schema validity (R4.1)
# =========================================================================== #
def _check_layer3(skill_dir: Path) -> LayerResult:
    result = LayerResult(layer=3, name="Intent schema validity")
    schema_path = skill_dir / "schemas" / "deployment-intent.schema.json"
    if not schema_path.is_file():
        result.findings.append(
            Finding(3, "P0", "schemas/deployment-intent.schema.json",
                    "Intent_Schema file is missing.")
        )
        return result
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        result.findings.append(
            Finding(3, "P0", "schemas/deployment-intent.schema.json",
                    f"schema is not well-formed JSON: {exc}")
        )
        return result

    if "$schema" not in schema:
        result.findings.append(
            Finding(3, "P1", "schemas/deployment-intent.schema.json:$schema",
                    "schema does not declare its JSON Schema dialect ($schema).")
        )
    try:
        from jsonschema.validators import validator_for

        validator_cls = validator_for(schema)
        validator_cls.check_schema(schema)
        result.note = f"valid {validator_cls.__name__} schema"
    except Exception as exc:  # noqa: BLE001 — surface any meta-schema failure
        result.findings.append(
            Finding(3, "P0", "schemas/deployment-intent.schema.json",
                    f"schema failed meta-schema validation: {exc}")
        )
    return result


# =========================================================================== #
# Layer 4 — Script importability (R4/R10)
# =========================================================================== #
def _check_layer4(skill_dir: Path, venv_python: Optional[str]) -> LayerResult:
    result = LayerResult(layer=4, name="Script importability")
    scripts_dir = skill_dir / "scripts"
    if not scripts_dir.is_dir():
        result.findings.append(
            Finding(4, "P1", "scripts/", "scripts/ directory is missing.")
        )
        return result

    modules: List[str] = []
    for py in sorted(scripts_dir.glob("*.py")):
        if py.name == "__init__.py":
            continue
        modules.append(f"scripts.{py.stem}")
    eval_dir = scripts_dir / "eval"
    if eval_dir.is_dir():
        for py in sorted(eval_dir.glob("*.py")):
            if py.name == "__init__.py":
                continue
            modules.append(f"scripts.eval.{py.stem}")

    python = venv_python or sys.executable
    # Import in a subprocess so a heavy import cannot perturb this process and so
    # the package-root sys.path entry matches how the scripts are used.
    code = (
        "import importlib, sys; sys.path.insert(0, %r)\n"
        "fail=[]\n"
        "for m in %r:\n"
        "    try:\n"
        "        importlib.import_module(m)\n"
        "    except Exception as e:\n"
        "        fail.append((m, repr(e)))\n"
        "import json; print(json.dumps(fail))\n"
    ) % (str(skill_dir), modules)
    proc = subprocess.run(
        [python, "-c", code], capture_output=True, text=True, cwd=str(skill_dir)
    )
    if proc.returncode != 0:
        result.findings.append(
            Finding(4, "P1", "scripts/",
                    f"import probe failed: {proc.stderr.strip()[:500]}")
        )
        return result
    try:
        failures = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        result.findings.append(
            Finding(4, "P1", "scripts/",
                    f"could not parse import probe output: {proc.stdout[:300]}")
        )
        return result
    for module, err in failures:
        result.findings.append(
            Finding(4, "P1", module, f"failed to import: {err}")
        )
    result.note = f"{len(modules)} modules probed"
    return result


# =========================================================================== #
# Layer 5 — Test suite passes
# =========================================================================== #
def _check_layer5(
    skill_dir: Path,
    run_tests: bool,
    test_result: Optional[Tuple[bool, str]],
    venv_python: Optional[str],
) -> LayerResult:
    result = LayerResult(layer=5, name="Test suite passes")
    if test_result is not None:
        passed, note = test_result
        result.note = note
        if not passed:
            result.findings.append(
                Finding(5, "P0", "scripts/tests", f"recorded test run failed: {note}")
            )
        return result
    if not run_tests:
        result.skipped = True
        result.note = "skipped (run_tests=False, no recorded result supplied)"
        return result

    python = venv_python or sys.executable
    proc = subprocess.run(
        [python, "-m", "pytest", "scripts/tests", "-q"],
        capture_output=True, text=True, cwd=str(skill_dir),
    )
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    result.note = tail
    if proc.returncode != 0:
        result.findings.append(
            Finding(5, "P0", "scripts/tests",
                    f"pytest exited {proc.returncode}: {tail}")
        )
    return result


# =========================================================================== #
# Layer 6 — Packaging / Agent Skills layout (R1.1, R1.4, R1.5)
# =========================================================================== #
def _check_layer6(skill_dir: Path, skill_md_text: str) -> LayerResult:
    result = LayerResult(layer=6, name="Packaging / Agent Skills layout")

    for entry in REQUIRED_ROOT_ENTRIES:
        if not (skill_dir / entry).exists():
            result.findings.append(
                Finding(6, "P1", entry, f"required package entry '{entry}' is missing.")
            )
    if not ((skill_dir / "scripts").is_dir() or (skill_dir / "templates").is_dir()):
        result.findings.append(
            Finding(6, "P1", "scripts|templates",
                    "package must contain at least one of scripts/ or templates/.")
        )

    # Composer-not-engine statement (R1.4) and rds-db2 xref (R1.5).
    lowered = skill_md_text.lower()
    if "composer" not in lowered or "orchestrator" not in lowered:
        result.findings.append(
            Finding(6, "P2", "SKILL.md",
                    "missing the Terraform composer/orchestrator statement (R1.4).")
        )
    if "imperative deployment engine" not in lowered:
        result.findings.append(
            Finding(6, "P2", "SKILL.md",
                    "missing the explicit not-a-deployment-engine statement (R1.4).")
        )
    if SIBLING_SKILL_NAME not in skill_md_text:
        result.findings.append(
            Finding(6, "P2", "SKILL.md",
                    f"missing the cross-reference to the '{SIBLING_SKILL_NAME}' "
                    "advisory companion (R1.5).")
        )

    # References one level deep (Agent Skills spec).
    refs_dir = skill_dir / "references"
    if refs_dir.is_dir():
        for child in refs_dir.iterdir():
            if child.is_dir():
                result.findings.append(
                    Finding(6, "P3", f"references/{child.name}/",
                            "references/ should be one level deep (nested dir found).")
                )
    result.note = "layout, composer statement, xref, reference depth"
    return result


# =========================================================================== #
# Orchestration
# =========================================================================== #
def run_preflight(
    skill_dir: os.PathLike | str = PACKAGE_ROOT,
    *,
    run_tests: bool = False,
    test_result: Optional[Tuple[bool, str]] = None,
    venv_python: Optional[str] = None,
) -> PreflightReport:
    """Run all six layers over ``skill_dir`` and return a :class:`PreflightReport`.

    Parameters
    ----------
    skill_dir:
        Skill package root (defaults to the package this module ships in).
    run_tests:
        When True, Layer 5 runs the pytest suite inline. When False and no
        ``test_result`` is supplied, Layer 5 is recorded as skipped (which makes
        the report not CR-ready).
    test_result:
        ``(passed, note)`` recorded from an external pytest run; lets the harness
        record the Layer-5 outcome without re-running the multi-minute suite.
    venv_python:
        Python interpreter used for the import probe and inline pytest (defaults
        to the current interpreter).
    """
    skill_dir = Path(skill_dir).resolve()
    skill_md_text = ""
    skill_md_path = skill_dir / "SKILL.md"
    if skill_md_path.is_file():
        skill_md_text = skill_md_path.read_text(encoding="utf-8")

    layers = [
        _check_layer1(skill_md_text),
        _check_layer2(skill_dir, skill_md_text),
        _check_layer3(skill_dir),
        _check_layer4(skill_dir, venv_python),
        _check_layer5(skill_dir, run_tests, test_result, venv_python),
        _check_layer6(skill_dir, skill_md_text),
    ]
    return PreflightReport(layers=layers)


def format_report(report: PreflightReport, skill_name: str = "rds-db2-deployer") -> str:
    lines = [f"## Preflight: {skill_name}", ""]
    for layer in report.layers:
        if layer.skipped:
            status = "SKIPPED"
        elif layer.passed:
            status = "PASS"
        else:
            status = "FAIL"
        note = f" — {layer.note}" if layer.note else ""
        lines.append(f"- Layer {layer.layer} {layer.name}: {status}{note}")
        for f in layer.findings:
            lines.append(f"    [{f.priority}] {f.location}: {f.detail}")
    lines.append("")
    blockers = report.blocking_findings
    lines.append(
        f"**Summary**: {'CR-READY' if report.cr_ready else 'NOT CR-READY'} — "
        f"{len(blockers)} blocking finding(s), "
        f"{len(report.findings) - len(blockers)} informational."
    )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Six-layer skill preflight + eval gate.")
    parser.add_argument("--skill-dir", default=str(PACKAGE_ROOT))
    parser.add_argument("--run-tests", action="store_true",
                        help="run the pytest suite inline as Layer 5")
    parser.add_argument("--tests-passed", action="store_true",
                        help="record Layer 5 as passed from an external run")
    parser.add_argument("--tests-note", default="recorded external pytest run: passed")
    parser.add_argument("--venv-python", default=None)
    args = parser.parse_args(argv)

    test_result = (True, args.tests_note) if args.tests_passed else None
    report = run_preflight(
        args.skill_dir,
        run_tests=args.run_tests,
        test_result=test_result,
        venv_python=args.venv_python,
    )
    print(format_report(report))
    return 0 if report.cr_ready else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
