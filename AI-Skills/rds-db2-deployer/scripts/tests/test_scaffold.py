"""Smoke tests for the skill package scaffold (task 1).

These verify the directory tree and developer tooling exist and import cleanly,
so later tasks build on a known-good layout. They intentionally do not assert on
content authored by later tasks (schema, resolvers, etc.).
"""

from __future__ import annotations

from pathlib import Path


def test_package_directory_tree_exists(package_root: Path) -> None:
    for sub in ("references", "schemas", "scripts", "templates/terraform", "artifacts"):
        assert (package_root / sub).is_dir(), f"missing directory: {sub}"


def test_scripts_is_an_importable_package(package_root: Path) -> None:
    assert (package_root / "scripts" / "__init__.py").is_file()
    import scripts  # noqa: F401  (importable via conftest path setup)


def test_python_project_config_present(package_root: Path) -> None:
    assert (package_root / "pyproject.toml").is_file()


def test_readme_present(package_root: Path) -> None:
    assert (package_root / "README.md").is_file()


def test_terraform_modules_root_resolves(terraform_modules_root: Path) -> None:
    # The composer (task 7) references these existing modules as sources. The
    # directory is named "RDS-Db2-Terraform" in local dev and "rds-db2-terraform"
    # (under tools/) in the published GitHub layout; accept either.
    assert terraform_modules_root.name in {"RDS-Db2-Terraform", "rds-db2-terraform"}
    assert (terraform_modules_root / "5-rds" / "variables.tf").is_file()
