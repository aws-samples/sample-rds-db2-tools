"""Shared pytest fixtures and path helpers for the provision-skill test suite.

Makes the package root importable so tests can `import scripts.resolve_intent`
etc. regardless of the working directory pytest is launched from, and exposes a
couple of path fixtures pointing at the schema and the existing Terraform
modules used across the suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/tests/conftest.py -> scripts/ -> package root
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PACKAGE_ROOT / "scripts"

# Ensure both the package root (for `import scripts...`) and the scripts dir
# (for direct `import resolve_intent`) are importable.
for _p in (PACKAGE_ROOT, SCRIPTS_DIR):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)


@pytest.fixture(scope="session")
def package_root() -> Path:
    """Absolute path to the skill package root."""
    return PACKAGE_ROOT


@pytest.fixture(scope="session")
def schema_path() -> Path:
    """Absolute path to the deployment-intent JSON Schema (authored in task 2)."""
    return PACKAGE_ROOT / "schemas" / "deployment-intent.schema.json"


@pytest.fixture(scope="session")
def terraform_modules_root() -> Path:
    """Absolute path to the existing modular Terraform reused by the composer.

    Auto-discovers across the supported layouts so the suite passes both in
    local development (modules a sibling of the package) and when published in
    aws-samples/sample-rds-db2-tools (skill at ``AI-Skills/<skill>/``, modules at
    ``tools/rds-db2-terraform/``). ``RDS_DB2_MODULES_ROOT`` overrides discovery.
    """
    import os

    env = os.environ.get("RDS_DB2_MODULES_ROOT")
    candidates = []
    if env:
        candidates.append(Path(env))
    candidates.append(PACKAGE_ROOT.parent / "RDS-Db2-Terraform")
    candidates.append(PACKAGE_ROOT.parent.parent / "tools" / "rds-db2-terraform")
    for candidate in candidates:
        if (candidate / "5-rds" / "variables.tf").is_file():
            return candidate.resolve()
    return PACKAGE_ROOT.parent / "RDS-Db2-Terraform"
