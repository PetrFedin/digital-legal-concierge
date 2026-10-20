"""Canonical, typed and auditable runtime settings authority."""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_setting import SystemSetting
from app.security.security_events import record_security_event
from app.system.settings_defaults import DEFAULT_SETTINGS

# Do not silently accept arbitrary keys or change the types consumed by
# PaymentService, CaseSLAService, consultation slots and reminder scheduler.
INTEGER_BOUNDS = {
    "deadlines.claim_waiting_days": (1, 365),
    "sla.first_lawyer_response_hours": (1, 720),
    "sla.next_lawyer_action_hours": (1, 720),
    "sla.escalation_repeat_hours": (1, 720),
    "consultations.slot_hold_minutes": (1, 10080),
}


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("Укажите числовое значение")
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError("Недопустимое числовое значение") from None
    if not number.is_finite():
        raise ValueError("Значение должно быть конечным числом")
    return number


def validate_setting_value(key: str, value: object):
    """Normalize only the documented settings into stable JSON-safe types."""
    definition = DEFAULT_SETTINGS.get(key)
    if not definition:
        raise ValueError("Неизвестная системная настройка")
    if not definition.get("editable", False):
        raise ValueError("Настройка недоступна для редактирования")

    value_type = definition["type"]
    if value_type == "money":
        amount = _decimal(value)
        if amount < 0 or amount > 10_000_000:
            raise ValueError("Сумма должна быть от 0 до 10 000 000 ₽")
        if amount != amount.quantize(Decimal("0.01")):
            raise ValueError("Допускается не более двух знаков после запятой")
        return int(amount) if amount == amount.to_integral_value() else str(amount)

    if value_type == "percent":
        percentage = _decimal(value)
        if percentage < 0 or percentage > 100:
            raise ValueError("Процент должен быть от 0 до 100")
        if percentage != percentage.quantize(Decimal("0.01")):
            raise ValueError("Допускается не более двух знаков после запятой")
        return int(percentage) if percentage == percentage.to_integral_value() else float(percentage)

    if value_type == "integer":
        number = _decimal(value)
        if number != number.to_integral_value():
            raise ValueError("Требуется целое число")
        low, high = INTEGER_BOUNDS.get(key, (1, 10080))
        if number < low or number > high:
            raise ValueError(f"Допустимый диапазон: {low}–{high}")
        return int(number)

    if value_type == "list":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                raise ValueError("Введите JSON-массив чисел, например [24, 72]") from None
        if not isinstance(value, list) or not 1 <= len(value) <= 12:
            raise ValueError("Укажите от 1 до 12 значений в массиве")
        result = []
        for item in value:
            number = _decimal(item)
            if number != number.to_integral_value() or number < 1 or number > 8760:
                raise ValueError("Интервалы напоминаний должны быть целыми часами от 1 до 8760")
            result.append(int(number))
        if result != sorted(set(result)):
            raise ValueError("Интервалы должны быть уникальными и по возрастанию")
        return result

    if value_type == "text":
        if not isinstance(value, str):
            raise ValueError("Требуется текст")
        text = value.strip()
        if not text or len(text) > 4000:
            raise ValueError("Текст должен содержать от 1 до 4000 символов")
        return text

    raise ValueError("Недопустимый тип настройки")


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
        normalized_value = validate_setting_value(key, value)
        default = DEFAULT_SETTINGS[key]
        setting = (
            await self.db.execute(
                select(SystemSetting)
                .where(SystemSetting.key == key)
                .with_for_update()
            )
        ).scalars().first()
        if not setting:
            if expected_updated_at is not None:
                raise ValueError(
                    "Настройка изменилась или была удалена после загрузки экрана"
                )
            setting = SystemSetting(
                key=key,
                title=default["title"],
                value={"value": normalized_value, "type": default["type"]},
                is_editable_in_admin=default.get("editable", False),
            )
            self.db.add(setting)
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
            setting.value = {
                **(setting.value or {}),
                "value": normalized_value,
                "type": default["type"],
            }
        await self.db.flush()
        if actor_id:
            # Retain actor and key only. Never store sensitive setting text,
            # previous/new values or a plaintext copy in historical audit.
            await record_security_event(
                self.db,
                action="security.system_setting_updated",
                severity="info",
                source="settings_service",
                actor_id=actor_id,
                resource_type="system_setting",
                resource_id=setting.id,
                details={"key": key, "setting_type": default["type"]},
                comment="Администратор обновил системную настройку",
            )
        return setting
