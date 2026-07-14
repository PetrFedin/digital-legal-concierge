from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.admin_access import admin_slot_for, is_allowed_admin
from app.bot.states import AdminLoginStates
from app.config import settings
from app.models.audit_log import AuditLog
from app.security.access_control import