from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_setting import SystemSetting
from app.system.settings_defaults import DEFAULT_SETTINGS


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
        del actor_id
        setting = (
            await self.db.execute(
                select(SystemSetting)
                .where(SystemSetting.key == key)
                .with_for_update()
            )
        ).scalars().first()
        default = DEFAULT_SETTINGS.get(
            key,
            {"title": key, "type": "string", "editable": True},
        )
        if not setting:
            if expected_updated_at is not None:
                raise ValueError(
                    "Настройка изменилась или была удалена после загрузки экрана"
                )
            setting = SystemSetting(
                key=key,
                title=default.get("title", key),
                value={"value": value, "type": default.get("type", "string")},
                is_editable_in_admin=default.get("editable", True),
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
            setting.value = {**(setting.value or {}), "value": value}
        await self.db.flush()
        return setting
