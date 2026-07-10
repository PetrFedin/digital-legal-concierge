from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.models.audit_log import AuditLog

router = Router()

CLIENT_ACTION_TITLES = {
    'CASE_CREATED': 'Обращение создано',
    'CALCULATION_COMPLETED': 'Расчет выполнен',
    'CASE_STATUS_CHANGED': 'Статус обновлен',
    'DOCUMENT_UPLOADED': 'Документ загружен',
    'DOCUMENTS_SENT_TO_REVIEW': 'Документы переданы на проверку',
    'PAYMENT_CREATED': 'Платеж создан',
    'PAYMENT_PAID': 'Оплата подтверждена',
    'CONSULTATION_CREATED': 'Консультация создана',
    'CONSULTATION_DESCRIPTION_SAVED': 'Описание ситуации сохранено',
    'CONSULTATION_SLOT_RESERVED': 'Слот консультации выбран',
    'CLIENT_MESSAGE_CREATED': 'Сообщение юристу отправлено',
}

@router.callback_query(lambda c: c.data == 'case_history_open')
async def case_history(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text('Нет активного дела.', reply_markup=one(('🏠 Главная', 'nav_home')))
        return
    res = await db.execute(select(AuditLog).where(AuditLog.entity_type == 'case').where(AuditLog.entity_id == case.id).order_by(AuditLog.created_at.desc()).limit(15))
    events = list(res.scalars().all())
    if not events:
        text = '🕘 История дела\n\nПока событий нет.'
    else:
        lines = ['🕘 История дела\n']
        for e in reversed(events):
            title = CLIENT_ACTION_TITLES.get(e.action, 'Событие по делу')
            dt = e.created_at.strftime('%d.%m.%Y %H:%M') if e.created_at else ''
            lines.append(f'{dt} — {title}')
        text = '\n'.join(lines)
    await callback.message.edit_text(text, reply_markup=one(('📁 Мое дело', 'my_case_open'), ('🏠 Главная', 'nav_home')))
