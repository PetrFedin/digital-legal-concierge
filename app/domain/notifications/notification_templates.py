TEMPLATES = {
    "case_created": "Новое обращение {case_number}.",
    "lawyer_assigned": "Вам назначено дело {case_number}.",
    "staff_message_reply": (
        "💬 Ответ юридической команды\n\n"
        "Дело: {case_number}\n\n"
        "{text}\n\n"
        "Ответ сохранён в переписке по делу."
    ),
    "client_message_received": (
        "💬 Новый вопрос клиента\n\n"
        "Дело: {case_number}\n"
        "Тема: {category}\n"
        "Срочность: {urgency}\n"
        "Назначение: {assignment}\n\n"
        "{text}\n\n"
        "Откройте центр сообщений и ответьте в контексте этого дела."
    ),
    "m1_case_accepted": (
        "Дело {case_number} принято юристом в работу. "
        "Следующий шаг: {next_action}."
    ),
    "m1_documents_requested": (
        "По делу {case_number} юрист запросил дополнительные документы: "
        "{request}. Следующий шаг: {next_action}."
    ),
    "m1_case_transferred_to_m2": (
        "По делу {case_number} выбран консультационный маршрут. "
        "Следующий шаг: {next_action}."
    ),
    "case_sla_first_response_overdue": (
        "Просрочена первая реакция по делу {case_number}. "
        "Юрист: {lawyer}. Срок был {due_at}. Уровень эскалации: {level}."
    ),
    "case_sla_action_overdue": (
        "Просрочено следующее действие по делу {case_number}. "
        "Юрист: {lawyer}. Срок был {due_at}. Уровень эскалации: {level}."
    ),
    "case_sla_acknowledged": (
        "Просрочка по делу {case_number} принята в работу. "
        "Новый контрольный срок: {due_at}."
    ),
    "document_uploaded": "Загружен новый документ по делу {case_number}.",
    "document_status_changed": "Статус документа изменен: {status}.",
    "document_approved": (
        "По делу {case_number} документ «{document}» принят юристом. "
        "Комментарий: {comment}."
    ),
    "document_reupload_requested": (
        "По делу {case_number} нужно загрузить новую версию документа "
        "«{document}». Причина: {comment}."
    ),
    "document_rejected": (
        "По делу {case_number} документ «{document}» отклонён. "
        "Причина: {comment}."
    ),
    "payment_created": "Выставлен платеж по делу {case_number}: {amount} ₽.",
    "payment_paid": "Оплата подтверждена по делу {case_number}.",
    "payment_reminder": "Напоминание: по делу {case_number} ожидается оплата.",
    "consultation_booked": "Консультация подтверждена: {date}.",
    "consultation_rescheduled": (
        "Консультация по делу {case_number} перенесена: "
        "{old_date} → {new_date}."
    ),
    "consultation_reminder_24h": (
        "Напоминание: консультация по делу {case_number} состоится "
        "{date}. До встречи осталось менее 24 часов."
    ),
    "consultation_reminder_2h": (
        "Консультация по делу {case_number} начнётся {date}. "
        "До встречи осталось менее 2 часов."
    ),
    "consultation_completion_overdue": (
        "Консультация по делу {case_number}, назначенная на {date}, "
        "не закрыта результатом. Укажите итог встречи или зафиксируйте неявку."
    ),
    "consultation_completed": (
        "Результат консультации по делу {case_number} зафиксирован. "
        "Решение: {decision}."
    ),
    "consultation_client_no_show": (
        "По делу {case_number} зафиксирована неявка клиента на консультацию. "
        "Администратор свяжется с клиентом для определения следующего шага."
    ),
    "consultation_lawyer_no_show": (
        "По делу {case_number} зафиксирована неявка юриста. "
        "Клиенту должен быть предложен бесплатный перенос или возврат."
    ),
    "consultation_lawyer_no_show_rebooked": (
        "Консультация по делу {case_number} бесплатно перенесена на {date}. "
        "Повторная оплата не требуется."
    ),
    "consultation_cancellation_requested": (
        "Консультация по делу {case_number} отменена. "
        "Заявка на возврат платежа #{payment_id} на сумму {amount} ₽ "
        "передана администратору."
    ),
    "consultation_refunded": (
        "Возврат по платежу #{payment_id} на сумму {amount} ₽ "
        "по делу {case_number} отмечен выполненным. {comment}"
    ),
    "consultation_refund_declined": (
        "По заявке на возврат платежа #{payment_id} по делу "
        "{case_number} принято решение об отказе. {comment}"
    ),
    "consultation_payment_review": (
        "По делу {case_number} получен платеж #{payment_id}, "
        "но слот не подтвержден автоматически. Причина: {reason}."
    ),
    "consultation_payment_review_resolved": (
        "Платёж #{payment_id} по делу {case_number} проверен. "
        "Консультация подтверждена на {date}; повторная оплата не требуется."
    ),
    "consultation_payment_review_refund_pending": (
        "По платежу #{payment_id} на сумму {amount} ₽ по делу "
        "{case_number} создана заявка на возврат. Администратор обработает её "
        "в центре возвратов."
    ),
    "claim_30_days_expired": (
        "По делу {case_number} истекли 30 дней после претензии. "
        "Проверьте следующий шаг."
    ),
    "court_stage_started": "Дело {case_number} перешло в судебный этап.",
}
