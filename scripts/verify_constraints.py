from __future__ import annotations

import argparse
import json
import re
import tomllib
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


ROOT = Path(__file__).resolve().parents[1]
CONSTRAINTS_PATH = ROOT / "constraints.txt"
PYPROJECT_PATH = ROOT / "pyproject.toml"
PIN_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)$")


def load_constraints(path: Path = CONSTRAINTS_PATH) -> dict[str, tuple[str, str]]:
    constraints: dict[str, tuple[str, str]] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = PIN_PATTERN.fullmatch(line)
        if not match:
            raise ValueError(
                f"{path}:{line_number}: ожидается строгий pin package==version"
            )
        display_name, version = match.groups()
        canonical = canonicalize_name(display_name)
        if canonical in constraints:
            raise ValueError(f"Дублирующий constraint для {display_name}")
        constraints[canonical] = (display_name, version)
    if not constraints:
        raise ValueError("constraints.txt пуст")
    return constraints


def project_requirements(
    path: Path = PYPROJECT_PATH,
    *,
    include_optional: bool,
) -> list[Requirement]:
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    project = config["project"]
    values = list(project.get("dependencies", []))
    if include_optional:
        for extra_values in project.get("optional-dependencies", {}).values():
            values.extend(extra_values)
    return [Requirement(value) for value in values]


def verify(*, require_all_constraints: bool) -> dict[str, object]:
    constraints = load_constraints()
    requirements = project_requirements(
        include_optional=require_all_constraints,
    )
    errors: list[str] = []
    checked_installed = 0
    missing_constraints: list[str] = []

    for _canonical, (display_name, expected_version) in sorted(constraints.items()):
        try:
            actual_version = metadata.version(display_name)
        except metadata.PackageNotFoundError:
            missing_constraints.append(display_name)
            if require_all_constraints:
                errors.append(f"{display_name} отсутствует в environment")
            continue
        checked_installed += 1
        if Version(actual_version) != Version(expected_version):
            errors.append(
                f"{display_name}: установлено {actual_version}, ожидается {expected_version}"
            )

    for requirement in requirements:
        canonical = canonicalize_name(requirement.name)
        locked = constraints.get(canonical)
        if locked is None:
            errors.append(f"Нет constraint для прямой зависимости {requirement.name}")
            continue
        locked_version = Version(locked[1])
        if requirement.specifier and locked_version not in requirement.specifier:
            errors.append(
                f"{requirement.name}=={locked_version} не соответствует "
                f"{requirement.specifier}"
            )
        try:
            metadata.version(requirement.name)
        except metadata.PackageNotFoundError:
            errors.append(f"Прямая зависимость {requirement.name} не установлена")

    result = {
        "ok": not errors,
        "mode": "full" if require_all_constraints else "production",
        "constraints": len(constraints),
        "installed_constraints_checked": checked_installed,
        "missing_optional_constraints": (
            [] if require_all_constraints else sorted(missing_constraints)
        ),
        "direct_requirements_checked": len(requirements),
        "errors": errors,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--require-all",
        action="store_true",
        help="Require every production and test constraint to be installed.",
    )
    arguments = parser.parse_args()
    result = verify(require_all_constraints=arguments.require_all)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
