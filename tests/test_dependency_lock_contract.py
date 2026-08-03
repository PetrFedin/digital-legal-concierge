from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from scripts.verify_constraints import load_constraints


ROOT = Path(__file__).resolve().parents[1]


def test_every_direct_dependency_has_a_compatible_strict_pin():
    constraints = load_constraints(ROOT / "constraints.txt")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    requirements = list(project["dependencies"])
    for values in project.get("optional-dependencies", {}).values():
        requirements.extend(values)

    for raw_requirement in requirements:
        requirement = Requirement(raw_requirement)
        locked = constraints.get(canonicalize_name(requirement.name))
        assert locked is not None, requirement.name
        assert Version(locked[1]) in requirement.specifier, raw_requirement


def test_production_and_test_images_use_the_same_constraint_file():
    production = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    tests = (ROOT / "Dockerfile.test").read_text(encoding="utf-8")

    for source in (production, tests):
        assert "PIP_CONSTRAINT=/app/constraints.txt" in source
        assert "COPY pyproject.toml constraints.txt README.md alembic.ini ./" in source


def test_constraint_verifier_is_available_in_both_images():
    production = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    tests = (ROOT / "Dockerfile.test").read_text(encoding="utf-8")
    verifier = (ROOT / "scripts/verify_constraints.py").read_text(encoding="utf-8")

    assert "COPY scripts ./scripts" in production
    assert "COPY scripts ./scripts" in tests
    assert "metadata.version" in verifier
    assert "--require-all" in verifier
