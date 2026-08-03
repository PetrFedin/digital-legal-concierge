from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

from sqlalchemy import func, or_, select

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.session import AsyncSessionLocal
from app.models.admin_user import AdminUser
from app.security.access_control import hash_password
from app.security.login_throttle import LoginThrottleService
from app.security.security_events import record_security_event

MIN_PASSWORD_LENGTH = 12


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Safely reset an existing administrative account password, revoke "
            "its sessions and clear the matching login throttle state."
        )
    )
    parser.add_argument(
        "identity",
        help="Existing username or email of the administrative account",
    )
    parser.add_argument(
        "--client-address",
        help=(
            "Optional client IP whose principal+address throttle state should "
            "also be cleared"
        ),
    )
    parser.add_argument(
        "--activate",
        action="store_true",
        help="Explicitly reactivate the account if it is disabled",
    )
    return parser


def read_new_password() -> str:
    password = getpass.getpass("Новый пароль: ")
    confirmation = getpass.getpass("Повторите новый пароль: ")
    if password != confirmation:
        raise ValueError("Пароли не совпадают")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Пароль должен содержать не менее {MIN_PASSWORD_LENGTH} символов"
        )
    # Reuse the application password validator before opening a transaction.
    hash_password(password)
    return password


async def reset_admin_password(
    *,
    identity: str,
    password: str,
    client_address: str | None = None,
    activate: bool = False,
) -> dict[str, object]:
    normalized_identity = identity.strip().lower()
    if not normalized_identity:
        raise ValueError("Укажите логин или email администратора")

    async with AsyncSessionLocal() as db:
        try:
            account = (
                await db.execute(
                    select(AdminUser)
                    .where(
                        or_(
                            func.lower(AdminUser.username) == normalized_identity,
                            func.lower(AdminUser.email) == normalized_identity,
                        )
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if account is None:
                raise LookupError("Административная учётная запись не найдена")

            was_active = bool(account.is_active)
            account.password_hash = hash_password(password)
            account.session_version = int(account.session_version or 1) + 1
            if activate:
                account.is_active = True

            throttle = LoginThrottleService(db)
            principals = {
                str(account.username or "").strip().lower(),
                str(account.email or "").strip().lower(),
            }
            principals.discard("")
            for principal in principals:
                await throttle.register_success(
                    principal=principal,
                    client_address=client_address or "unknown",
                )

            await record_security_event(
                db,
                action="security.admin_password_reset_cli",
                severity="critical",
                source="admin_account_recovery_cli",
                actor_id=account.id,
                principal=account.username or account.email,
                client_address=client_address,
                resource_type="admin_user",
                resource_id=account.id,
                details={
                    "sessions_revoked": True,
                    "session_version": account.session_version,
                    "client_throttle_cleared": bool(client_address),
                    "account_was_active": was_active,
                    "account_is_active": bool(account.is_active),
                },
                comment="Пароль административной учётной записи сброшен через защищённую CLI",
            )
            await db.commit()
            return {
                "account_id": account.id,
                "username": account.username,
                "email": account.email,
                "active": bool(account.is_active),
                "sessions_revoked": True,
                "client_throttle_cleared": bool(client_address),
            }
        except Exception:
            await db.rollback()
            raise


def main() -> int:
    args = build_parser().parse_args()
    try:
        password = read_new_password()
        result = asyncio.run(
            reset_admin_password(
                identity=args.identity,
                password=password,
                client_address=(args.client_address or "").strip() or None,
                activate=bool(args.activate),
            )
        )
    except (ValueError, LookupError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"Сброс пароля не выполнен: {type(error).__name__}", file=sys.stderr)
        return 1

    label = result.get("username") or result.get("email") or result["account_id"]
    print(f"Пароль учётной записи {label} обновлён.")
    print("Все ранее выданные сессии отозваны.")
    if not result["active"]:
        print("Учётная запись остаётся отключённой. Используйте --activate только осознанно.")
    if not result["client_throttle_cleared"]:
        print(
            "Блокировка конкретного IP не очищалась. При необходимости повторите "
            "команду с --client-address."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
