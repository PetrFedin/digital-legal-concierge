DEFAULT_SETTINGS = {
    "payments.m1_initial_payment": {
        "title": "Первый платеж М1",
        "value": 30000,
        "type": "money",
        "editable": True,
    },
    "payments.m1_court_payment": {
        "title": "Второй платеж М1",
        "value": 70000,
        "type": "money",
        "editable": True,
    },
    "payments.m1_self_filing_package": {
        "title": "Подготовка пакета документов для самостоятельной подачи",
        "value": 15000,
        "type": "money",
        "editable": True,
    },
    "self_filing.sla_business_days": {
        "title": "Срок подготовки пакета, рабочих дней",
        "value": 2,
        "type": "integer",
        "editable": True,
    },
    "self_filing.business_calendar_coverage_through": {
        "title": "Календарь рабочих дней подтверждён по",
        "value": "",
        "type": "date",
        "editable": True,
    },
    "self_filing.non_working_dates": {
        "title": "Дополнительные нерабочие даты YYYY-MM-DD",
        "value": [],
        "type": "date_list",
        "editable": True,
    },
    "self_filing.additional_working_dates": {
        "title": "Дополнительные рабочие даты YYYY-MM-DD",
        "value": [],
        "type": "date_list",
        "editable": True,
    },
    "payments.m1_success_fee_percent": {
        "title": "Success fee %",
        "value": 10,
        "type": "percent",
        "editable": True,
    },
    "payments.m2_consultation_payment": {
        "title": "Стоимость консультации",
        "value": 5000,
        "type": "money",
        "editable": True,
    },
    "deadlines.claim_waiting_days": {
        "title": "Ожидание после претензии",
        "value": 30,
        "type": "integer",
        "editable": True,
    },
    "sla.first_lawyer_response_hours": {
        "title": "SLA первой реакции юриста, часов",
        "value": 4,
        "type": "integer",
        "editable": True,
    },
    "sla.next_lawyer_action_hours": {
        "title": "SLA следующего действия юриста, часов",
        "value": 48,
        "type": "integer",
        "editable": True,
    },
    "sla.escalation_repeat_hours": {
        "title": "Повторная эскалация просрочки, часов",
        "value": 4,
        "type": "integer",
        "editable": True,
    },
    "consultations.slot_hold_minutes": {
        "title": "Удержание слота консультации",
        "value": 30,
        "type": "integer",
        "editable": True,
    },
    "notifications.payment_reminder_hours": {
        "title": "Напоминания по оплате",
        "value": [3, 24, 72],
        "type": "list",
        "editable": True,
    },
    "texts.bot_welcome": {
        "title": "Приветственный текст",
        "value": (
            "Я помогу предварительно рассчитать неустойку по ДДУ "
            "и передать документы юристу."
        ),
        "type": "text",
        "editable": True,
    },
    "texts.legal_disclaimer": {
        "title": "Юридический дисклеймер",
        "value": (
            "Расчет предварительный и не является юридическим заключением."
        ),
        "type": "text",
        "editable": True,
    },
}
