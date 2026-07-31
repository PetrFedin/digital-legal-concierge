from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict

from app.security.backup_encryption import (
    BackupSecurityError,
    extract_encrypted_backup,
    verify_encrypted_backup,
)
from app.security.backup_restore_fence import (
    BackupRestoreFenceError,
    assert_backup_not_revoked,
    backup_maintenance_lock,
    purge_revoked_backups,
)
from app.security.backup_service import create_provider_encrypted_backup
from app.security.postgresql_restore import restore_postgresql_staging


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

    restore_postgres = subparsers.add_parser(
        "restore-postgresql-staging",
        help=(
            "Verify, extract and restore a PostgreSQL backup into an empty "
            "staging database from STAGING_DATABASE_URL"
        ),
    )
    restore_postgres.add_argument("archive")
    restore_postgres.add_argument("destination")
    restore_postgres.add_argument(
        "--confirm-database",
        required=True,
        help="Exact staging database name from STAGING_DATABASE_URL",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "create":
            with backup_maintenance_lock(args.backup_dir):
                purge_revoked_backups(args.backup_dir)
                result = create_provider_encrypted_backup(
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
        elif args.command == "extract":
            with backup_maintenance_lock():
                assert_backup_not_revoked(args.archive)
                metadata = extract_encrypted_backup(args.archive, args.destination)
            payload = {
                "ok": True,
                "operation": "extract",
                "destination": args.destination,
                **asdict(metadata),
            }
        else:
            staging_database_url = os.getenv("STAGING_DATABASE_URL", "").strip()
            if not staging_database_url:
                raise BackupSecurityError(
                    "Для staging restore требуется переменная STAGING_DATABASE_URL"
                )
            result = restore_postgresql_staging(
                args.archive,
                args.destination,
                target_database_url=staging_database_url,
                confirmed_database=args.confirm_database,
            )
            payload = {
                "ok": True,
                "operation": "restore-postgresql-staging",
                **result.as_dict(),
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
