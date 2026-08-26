from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 1
DEFAULT_REQUIRED_COMPONENTS = (
    "postgres",
    "redis",
    "telegram",
    "browser",
    "provider-sandbox",
)


def _required(value: str | None, *, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise RuntimeError(f"{label} is required for LIVE_REQUIRED evidence")
    return normalized


def _github_metadata() -> dict[str, str | None]:
    server_url = str(os.getenv("GITHUB_SERVER_URL") or "").rstrip("/")
    repository = str(os.getenv("GITHUB_REPOSITORY") or "").strip()
    run_id = str(os.getenv("GITHUB_RUN_ID") or "").strip()
    run_url = (
        f"{server_url}/{repository}/actions/runs/{run_id}"
        if server_url and repository and run_id
        else None
    )
    return {
        "repository": repository or None,
        "workflow": str(os.getenv("GITHUB_WORKFLOW") or "").strip() or None,
        "ref": str(os.getenv("GITHUB_REF") or "").strip() or None,
        "run_id": run_id or None,
        "run_attempt": str(os.getenv("GITHUB_RUN_ATTEMPT") or "").strip() or None,
        "run_url": run_url,
    }


def _write_json(path: str | Path, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_component_evidence(
    *,
    component: str,
    sha: str,
    output: str | Path,
    detail: str,
) -> dict:
    component = _required(component, label="component")
    sha = _required(sha, label="sha")
    detail = _required(detail, label="detail")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "live_required_component",
        "component": component,
        "status": "success",
        "sha": sha,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "detail": detail,
        **_github_metadata(),
    }
    _write_json(output, payload)
    return payload


def _component_payloads(input_dir: str | Path) -> dict[str, dict]:
    root = Path(input_dir)
    if not root.exists():
        raise RuntimeError(f"LIVE_REQUIRED evidence directory does not exist: {root}")

    components: dict[str, dict] = {}
    for path in sorted(root.rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("kind") != "live_required_component":
            continue
        component = _required(payload.get("component"), label=f"component in {path}")
        if component in components:
            raise RuntimeError(
                f"Duplicate LIVE_REQUIRED evidence for component {component!r}"
            )
        if payload.get("status") != "success":
            raise RuntimeError(
                f"Component {component!r} evidence is not successful"
            )
        components[component] = payload
    return components


def aggregate_evidence(
    *,
    input_dir: str | Path,
    sha: str,
    output: str | Path,
    required_components: tuple[str, ...] = DEFAULT_REQUIRED_COMPONENTS,
) -> dict:
    sha = _required(sha, label="sha")
    required = tuple(_required(item, label="required component") for item in required_components)
    if len(set(required)) != len(required):
        raise RuntimeError("LIVE_REQUIRED required component list contains duplicates")

    components = _component_payloads(input_dir)
    missing = [component for component in required if component not in components]
    if missing:
        raise RuntimeError(
            "Missing LIVE_REQUIRED component evidence: " + ", ".join(missing)
        )

    expected_run_id = str(os.getenv("GITHUB_RUN_ID") or "").strip() or None
    for component in required:
        payload = components[component]
        if str(payload.get("sha") or "") != sha:
            raise RuntimeError(
                f"Component {component!r} belongs to SHA {payload.get('sha')!r}, expected {sha!r}"
            )
        if int(payload.get("schema_version") or 0) != SCHEMA_VERSION:
            raise RuntimeError(
                f"Component {component!r} has unsupported evidence schema"
            )
        component_run_id = str(payload.get("run_id") or "").strip() or None
        if expected_run_id and component_run_id != expected_run_id:
            raise RuntimeError(
                f"Component {component!r} belongs to workflow run {component_run_id!r}, "
                f"expected {expected_run_id!r}"
            )

    metadata = _github_metadata()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": "live_required_manifest",
        "status": "LIVE_PASS",
        "sha": sha,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "required_components": list(required),
        "components": {component: components[component] for component in required},
        **metadata,
    }
    _write_json(output, manifest)
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and verify fail-closed LIVE_REQUIRED release evidence"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    write = subparsers.add_parser("write", help="write one successful component record")
    write.add_argument("--component", required=True)
    write.add_argument("--sha", default=os.getenv("GITHUB_SHA", ""))
    write.add_argument("--output", required=True)
    write.add_argument("--detail", required=True)

    aggregate = subparsers.add_parser(
        "aggregate",
        help="verify all component records and write one release manifest",
    )
    aggregate.add_argument("--input-dir", required=True)
    aggregate.add_argument("--sha", default=os.getenv("GITHUB_SHA", ""))
    aggregate.add_argument("--output", required=True)
    aggregate.add_argument(
        "--require",
        nargs="+",
        default=list(DEFAULT_REQUIRED_COMPONENTS),
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
        if args.command == "write":
            payload = write_component_evidence(
                component=args.component,
                sha=args.sha,
                output=args.output,
                detail=args.detail,
            )
            print(
                "LIVE_REQUIRED component evidence written: "
                f"{payload['component']} sha={payload['sha']}"
            )
            return

        manifest = aggregate_evidence(
            input_dir=args.input_dir,
            sha=args.sha,
            output=args.output,
            required_components=tuple(args.require),
        )
        print(
            "LIVE_REQUIRED evidence manifest verified: "
            f"components={len(manifest['required_components'])}; sha={manifest['sha']}"
        )
    except (RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"LIVE_REQUIRED evidence error: {error}", file=sys.stderr)
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
