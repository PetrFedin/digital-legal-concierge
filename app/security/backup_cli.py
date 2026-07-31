from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from app.security.backup_encryption import (
    BackupSecurityError,
    create_encrypted_backup,
    extract_encrypted_backup,
    verify_encrypted_backup,
)
from app.security.backup_restore_fence import (
    BackupRestoreFenceError,
    assert_backup_not_revoked,
    backup_maintenance_lock,
    purge_revoked_backups,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Encrypted backup and staging-only recovery for Digital Legal Concierge"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create and immediately verify backup")
    create.add_argument("--backup-dir", default=None)
    create.add_argument("--storage-dir", default=None)
    create.add_argument("--database-url", default=None)

    inspect = subparsers.add_parser(
        "inspect",
        help="Read a non-revoked backup header",
    )
    inspect.add_argument("archive")

    verify = subparsers.add_parser("verify", help="Decrypt and fully verify backup")
    verify.add_argument("archive")

    extract = subparsers.add_parser(
        "extract",
        help="Decrypt and extract into an empty staging directory",
    )
    extract.add_argument("archive")
    extract.add_argument("destination")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "create":
            with backup_maintenance_lock(args.backup_dir):
                purge_revoked_backups(args.backup_dir)
                result = create_encrypted_backup(
                    database_url=args.database_url,
                    storage_dir=args.storage_dir,
                    backup_dir=args.backup_dir,
                )
            payload = {"ok": True, "operation": "create", **asdict(result)}
        elif args.command == "inspect":
            with backup_maintenance_lock():
                metadata = assert_backup_not_revoked(args.archive)
            payload = {"ok": True, "operation": "inspect", **asdict(metadata)}
        elif args.command == "verify":
            with backup_maintenance_lock():
                assert_backup_not_revoked(args.archive)
                metadata = verify_encrypted_backup(args.archive)
            payload = {"ok": True, "operation": "verify", **asdict(metadata)}
        else:
            with backup_maintenance_lock():
                assert_backup_not_revoked(args.archive)
                metadata = extract_encrypted_backup(args.archive, args.destination)
            payload = {
                "ok": True,
                "operation": "extract",
                "destination": args.destination,
                **asdict(metadata),
            }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    except (BackupSecurityError, BackupRestoreFenceError) as error:
        print(
            json.dumps(
                {"ok": False, "error": str(error)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
