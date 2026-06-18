"""Packaging shim.

This file exists ONLY so the ``schemas`` directory (which holds the skill's JSON
Schemas, e.g. ``account-defaults.schema.json`` and ``deployment-intent.schema.json``)
is shipped as a top-level data package by ``pip install``. The skill resolves
these schemas at ``<package_root>/schemas/<name>.json`` (next to the ``scripts``
package); without packaging them, an installed wheel — as used by the gitops CI
(`pip install git+...#subdirectory=AI-Skills/rds-db2-deployer`) — could import
``scripts`` but not find the schemas, raising FileNotFoundError at validation
time. The directory deliberately stays at the skill top level so the Agent
Skills layout and the eval preflight (which expect ``schemas/`` there) keep
working. There is no importable code here.
"""
