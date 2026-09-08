from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _module_exists(module: str) -> bool:
    parts = module.split(".")
    file_path = ROOT.joinpath(*parts).with_suffix(".py")
    package_init = ROOT.joinpath(*parts, "__init__.py")
    return file_path.exists() or package_init.exists()


def _imports(path: str, prefixes: tuple[str, ...]) -> set[str]:
    source = (ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            base = node.module
            if base.startswith(prefixes):
                result.add(base)
                for alias in node.names:
                    candidate = f"{base}.{alias.name}"
                    if _module_exists(candidate):
                        result.add(candidate)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(prefixes):
                    result.add(alias.name)
    return result


def test_main_api_imports_resolve_to_real_modules_or_packages():
    modules = _imports("app/main.py", ("app.api",))
    missing = sorted(module for module in modules if not _module_exists(module))
    assert not missing, f"app/main.py references missing API modules: {missing}"


def test_bot_runtime_imports_resolve_to_real_modules_or_packages():
    modules = _imports(
        "app/bot/bot.py",
        (
            "app.bot",
            "app.bot.screens",
        ),
    )
    missing = sorted(module for module in modules if not _module_exists(module))
    assert not missing, f"app/bot/bot.py references missing bot modules: {missing}"


def test_new_provenance_safety_modules_are_actually_mounted():
    source = (ROOT / "app/bot/bot.py").read_text(encoding="utf-8")

    assert "ConsultationBookingProvenanceMiddleware" in source
    assert "document_upload_binding_guard.router," in source
    assert "document_mutation_guard.router," in source
    assert "telegram_safety_composite.router," in source

    composite = (ROOT / "app/bot/screens/telegram_safety_composite.py").read_text(
        encoding="utf-8"
    )
    assert "payment_stage_binding_guard" in composite
    assert "router.include_router(payment_stage_binding_router)" in composite
