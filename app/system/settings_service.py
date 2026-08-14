from __future__ import annotations

import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.system_setting import SystemSetting
from app.system.settings_defaults import DEFAULT_SETTINGS


_INTEGER_BOUNDS: dict[str, tuple[int, int]] = {
    "deadlines.claim_waiting_days": (1, 365),
    "sla.first_lawyer_response_hours": (1, 168),
    "sla.next_lawyer_action_hours": (1, 24 * 90),
    "sla.escalation_repeat_hours": (1, 24 * 30),
    "consultations.slot_hold_minutes": (5, 24 * 60),
}
_SENSITIVE_KEY_MARKERS = ("secret", "token", "password", "credential", "api_key")


def _number(value, *, title: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{title}: требуется число")
    raw = str(value).strip().replace(" ", "").replace(",", ".")
    try:
        parsed = float(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{title}: требуется число") from error
    if not math.isfinite(parsed):
        raise ValueError(f"{title}: число должно быть конечным")
    return parsed


def _coerce_value(key: str, value):
    meta = DEFAULT_SETTINGS.get(key)
    if meta is None:
        raise ValueError("Неизвестная настройка. Добавьте её в DEFAULT_SETTINGS до использования")
    title = str(meta.get("title") or key)
    kind = str(meta.get("type") or "string")

    if kind == "money":
        parsed = _number(value, title=title)
        if parsed <= 0 or parsed > 100_000_000:
            raise ValueError(f"{title}: сумма должна быть больше 0 и не выше 100 000 000")
        return int(parsed) if parsed.is_integer() else round(parsed, 2)

    if kind == "percent":
        parsed = _number(value, title=title)
        if parsed <= 0 or parsed > 100:
            raise ValueError(f"{title}: процент должен быть больше 0 и не выше 100")
        return int(parsed) if parsed.is_integer() else round(parsed, 4)

    if kind == "integer":
        parsed = _number(value, title=title)
        if not parsed.is_integer():
            raise ValueError(f"{title}: требуется целое число")
        result = int(parsed)
        lower, upper = _INTEGER_BOUNDS.get(key, (1, 100_000))
        if result < lower or result > upper:
            raise ValueError(f"{title}: допустимый диапазон {lower}–{upper}")
        return result

    if kind == "list":
        if isinstance(value, (list, tuple)):
            raw_items = list(value)
        else:
            raw_items = [
                item.strip()
                for item in str(value or "").replace(";", ",").split(",")
                if item.strip()
            ]
        if not raw_items:
            raise ValueError(f"{title}: список не может быть пустым")
        result: list[int] = []
        for raw_item in raw_items:
            parsed = _number(raw_item, title=title)
            if not parsed.is_integer():
                raise ValueError(f"{title}: каждый интервал должен быть целым числом часов")
            hours = int(parsed)
            if hours < 1 or hours > 24 * 365:
                raise ValueError(f"{title}: каждый интервал должен быть от 1 до 8760 часов")
            if hours not in result:
                result.append(hours)
        if len(result) > 24:
            raise ValueError(f"{title}: не более 24 интервалов")
        return sorted(result)

    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{title}: значение не может быть пустым")
    limit = 4000 if kind == "text" else 1000
    if len(clean) > limit:
        raise ValueError(f"{title}: значение длиннее {limit} символов")
    return clean


def _audit_value(key: str, value):
    lowered = key.lower()
    if any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS):
        return "[REDACTED]"
    return value


class SettingsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def bootstrap_defaults(self) -> None:
        for key, data in DEFAULT_SETTINGS.items():
            result = await self.db.execute(
                select(SystemSetting).where(SystemSetting.key == key)
            )
            if result.scalars().first():
                continue
            self.db.add(
                SystemSetting(
                    key=key,
                    title=data["title"],
                    value={"value": data["value"], "type": data["type"]},
                    is_editable_in_admin=data.get("editable", True),
                )
            )
        await self.db.flush()

    async def list_settings(self) -> list[SystemSetting]:
        await self.bootstrap_defaults()
        result = await self.db.execute(
            select(SystemSetting).order_by(SystemSetting.key.asc())
        )
        return list(result.scalars().all())

    async def get_value(self, key: str):
        result = await self.db.execute(
            select(SystemSetting).where(SystemSetting.key == key)
        )
        setting = result.scalars().first()
        if setting:
            return setting.value.get("value")
        default = DEFAULT_SETTINGS.get(key)
        if default:
            return default["value"]
        raise KeyError(f"Настройка не найдена: {key}")

    async def set_value(
        self,
        *,
        key: str,
        value,
        actor_id: int | None = None,
        expected_updated_at: str | None = None,
    ) -> SystemSetting:
        default = DEFAULT_SETTINGS.get(key)
        if default is None:
            raise ValueError("Неизвестная настройка")
        if not bool(default.get("editable", True)):
            raise ValueError("Настройка недоступна для редактирования")
        normalized_value = _coerce_value(key, value)

        setting = (
            await self.db.execute(
                select(SystemSetting)
                .where(SystemSetting.key == key)
                .with_for_update()
            )
        ).scalars().first()
        old_value = None
        if not setting:
            if expected_updated_at is not None:
                raise ValueError(
                    "Настройка изменилась или была удалена после загрузки экрана"
                )
            setting = SystemSetting(
                key=key,
                title=default.get("title", key),
                value={
                    "value": normalized_value,
                    "type": default.get("type", "string"),
                },
                is_editable_in_admin=True,
            )
            self.db.add(setting)
            await self.db.flush()
        else:
            if not setting.is_editable_in_admin:
                raise ValueError("Настройка недоступна для редактирования")
            if (
                expected_updated_at is not None
                and setting.updated_at.isoformat() != expected_updated_at
            ):
                raise ValueError(
                    "Настройка уже изменена другим пользователем. Обновите список"
                )
            old_value = (setting.value or {}).get("value")
            setting.value = {
                **(setting.value or {}),
                "value": normalized_value,
                "type": default.get("type", "string"),
            }
            await self.db.flush()

        self.db.add(
            AuditLog(
                actor_type="admin",
                actor_id=actor_id,
                action="SYSTEM_SETTING_UPDATED",
                entity_type="system_setting",
                entity_id=setting.id,
                old_value={
                    "key": key,
                    "value": _audit_value(key, old_value),
                },
                new_value={
                    "key": key,
                    "value": _audit_value(key, normalized_value),
                    "type": default.get("type", "string"),
                },
                comment=f"Изменена настройка: {default.get('title', key)}",
            )
        )
        await self.db.flush()
        return setting
