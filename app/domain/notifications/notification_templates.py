TEMPLATES = {
    "case_created": "🆕 Новое обращение {case_number}.",
    "lawyer_assigned": "👨‍⚖ Вам назначено дело {case_number}.",
    "document_uploaded": "📄 Загружен новый документ по делу {case_number}.",
    "document_status_changed": "📄 Статус документа изменен: {status}.",
    "payment_created": "💳 Выставлен платеж по делу {case_number}: {amount} ₽.",
    "payment_paid": "✅ Оплата подтверждена по делу {case_number}.",
    "payment_reminder": "⏰ Напоминание: по делу {case_number} ожидается оплата.",
    "consultation_booked": "👨‍⚖ Консультация подтверждена: {date}.",
    "consultation_rescheduled": (
        "🔄 Консультация по делу {case_number} перенесена: "
        "{old_date} → {new_date}."
    ),
    "consultation_cancellation_requested": (
        "🧾 Консультация по делу {case_number} отменена. "
        "Заявка на возврат платежа #{payment_id} на сумму {amount} ₽ "
        "передана администратору."
    ),
    "consultation_refunded": (
        "✅ Возврат по платежу #{payment_id} на сумму {amount} ₽ "
        "по делу {case_number} отмечен выполненным. {comment}"
    ),
    "consultation_refund_declined": (
        "⚠️ По заявке на возврат платежа #{payment_id} по делу "
        "{case_number} принято решение об отказе. {comment}"
    ),
    "consultation_payment_review": (
        "⚠️ По делу {case_number} получен платеж #{payment_id}, "
        "но слот не подтвержден автоматически. Причина: {reason}."
    ),
    "claim_30_days_expired": "📨 По делу {case_number} истекли 30 дней после претензии. Проверьте следующий шаг.",
    "court_stage_started": "🏛 Дело {case_number} перешло в судебный этап.",
}
