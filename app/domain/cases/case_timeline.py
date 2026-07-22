from __future__ import annotations

from app.domain.statuses.case_statuses import CaseStatus, RouteCode


def _value(value) -> str:
    return value.value if hasattr(value, "value") else str(value or "")


ROUTE_TITLES = {
    RouteCode.M1.value: "Полное ведение дела",
    RouteCode.M2.value: "Консультация юриста",
}

CLIENT_VISIBLE_STATUS_TITLES = {
    CaseStatus.NEW.value: "Обращение создано",
    CaseStatus.CALCULATOR_STARTED.value: "Заполняется расчёт",
    CaseStatus.CALCULATED.value: "Предварительный расчёт готов",
    CaseStatus.CLIENT_DECISION.value: "Выберите формат помощи",
    CaseStatus.M1_DOCUMENTS_PENDING.value: "Ожидаем документы",
    CaseStatus.M1_DOCUMENTS_RECEIVED.value: "Документы получены",
    CaseStatus.M1_LAWYER_REVIEW.value: "Юрист проверяет материалы",
    CaseStatus.M1_DOCS_REQUESTED.value: "Нужно дополнить документы",
    CaseStatus.M1_ACCEPTED.value: "Дело принято в работу",
    CaseStatus.M1_REJECTED.value: "Ведение дела не подтверждено",
    CaseStatus.M1_CONTRACT_READY.value: "Договор готов",
    CaseStatus.M1_WAITING_PAYMENT_30000.value: "Ожидаем первый платёж",
    CaseStatus.M1_PAYMENT_30000_RECEIVED.value: "Первый платёж получен",
    CaseStatus.M1_POWER_OF_ATTORNEY.value: "Нужно оформить доверенность",
    CaseStatus.M1_POA_RECEIVED.value: "Доверенность получена",
    CaseStatus.M1_CLAIM_PREPARATION.value: "Юрист готовит претензию",
    CaseStatus.M1_CLAIM_SENT.value: "Претензия направлена",
    CaseStatus.M1_WAITING_30_DAYS.value: "Ожидаем ответ на претензию",
    CaseStatus.M1_COURT_STAGE.value: "Подготовка судебного этапа",
    CaseStatus.M1_WAITING_PAYMENT_70000.value: "Ожидаем платёж за судебный этап",
    CaseStatus.M1_PAYMENT_70000_RECEIVED.value: "Судебный этап оплачен",
    CaseStatus.M1_ENFORCEMENT.value: "Исполнение решения",
    CaseStatus.M1_MONEY_RECEIVED.value: "Деньги получены",
    CaseStatus.M1_WAITING_SUCCESS_FEE.value: "Ожидаем оплату вознаграждения",
    CaseStatus.M1_SUCCESS_FEE_RECEIVED.value: "Вознаграждение оплачено",
    CaseStatus.M1_CLOSED.value: "Дело завершено",
    CaseStatus.M2_CONSULTATION_ROUTE.value: "Маршрут консультации выбран",
    CaseStatus.M2_DESCRIPTION_PENDING.value: "Ожидаем описание ситуации",
    CaseStatus.M2_DOCUMENTS_OPTIONAL.value: "Можно приложить документы",
    CaseStatus.M2_SLOT_PENDING.value: "Выберите время консультации",
    CaseStatus.M2_PAYMENT_PENDING.value: "Ожидаем оплату консультации",
    CaseStatus.M2_CONSULTATION_BOOKED.value: "Консультация назначена",
    CaseStatus.M2_CONSULTATION_DONE.value: "Консультация проведена",
    CaseStatus.M2_TO_M1.value: "Предложено полное ведение дела",
    CaseStatus.M2_CLOSED.value: "Обращение завершено",
    CaseStatus.ERROR.value: "Требуется проверка сотрудником",
    CaseStatus.ARCHIVED.value: "Дело в архиве",
}

CLIENT_STATUS_DESCRIPTIONS = {
    CaseStatus.NEW.value: "Мы сохранили обращение и готовы продолжить оформление.",
    CaseStatus.CALCULATOR_STARTED.value: "Заполните данные, чтобы получить предварительный расчёт требований.",
    CaseStatus.CALCULATED.value: "Расчёт подготовлен. Проверьте результат и выберите дальнейший формат помощи.",
    CaseStatus.CLIENT_DECISION.value: "Можно передать дело на полное ведение или записаться на консультацию.",
    CaseStatus.M1_DOCUMENTS_PENDING.value: "Для юридической оценки нужны документы и подтверждения по ситуации.",
    CaseStatus.M1_DOCUMENTS_RECEIVED.value: "Материалы загружены и переданы на первичную проверку.",
    CaseStatus.M1_LAWYER_REVIEW.value: "Юрист изучает документы, оценивает перспективу и проверяет комплектность.",
    CaseStatus.M1_DOCS_REQUESTED.value: "Для продолжения не хватает отдельных документов или уточнений.",
    CaseStatus.M1_ACCEPTED.value: "Юрист подтвердил возможность ведения дела и готовит договорные документы.",
    CaseStatus.M1_REJECTED.value: "По итогам проверки полное ведение дела не подтверждено. Сотрудник пояснит доступные варианты.",
    CaseStatus.M1_CONTRACT_READY.value: "Договор подготовлен. Ознакомьтесь с условиями перед оплатой первого этапа.",
    CaseStatus.M1_WAITING_PAYMENT_30000.value: "После первого платежа откроется оформление доверенности и претензионная работа.",
    CaseStatus.M1_PAYMENT_30000_RECEIVED.value: "Платёж принят, система открывает следующий этап дела.",
    CaseStatus.M1_POWER_OF_ATTORNEY.value: "Доверенность нужна, чтобы юрист мог официально представлять ваши интересы.",
    CaseStatus.M1_POA_RECEIVED.value: "Доверенность получена. Юрист может переходить к подготовке требований.",
    CaseStatus.M1_CLAIM_PREPARATION.value: "Юрист формирует правовую позицию, требования и приложения к претензии.",
    CaseStatus.M1_CLAIM_SENT.value: "Претензия отправлена адресату. Фиксируются дата отправки и срок ответа.",
    CaseStatus.M1_WAITING_30_DAYS.value: "Идёт установленный срок для добровольного ответа или исполнения требований.",
    CaseStatus.M1_COURT_STAGE.value: "Юрист оценивает ответ, готовит иск и комплект материалов для суда.",
    CaseStatus.M1_WAITING_PAYMENT_70000.value: "Для начала судебного этапа необходимо оплатить соответствующий этап договора.",
    CaseStatus.M1_PAYMENT_70000_RECEIVED.value: "Оплата судебного этапа подтверждена, работа продолжается автоматически.",
    CaseStatus.M1_ENFORCEMENT.value: "Контролируется фактическое исполнение решения и получение присуждённых средств.",
    CaseStatus.M1_MONEY_RECEIVED.value: "Результат по делу получен. Осталось завершить финансовые и закрывающие действия.",
    CaseStatus.M1_WAITING_SUCCESS_FEE.value: "Ожидается оплата итогового вознаграждения согласно условиям договора.",
    CaseStatus.M1_SUCCESS_FEE_RECEIVED.value: "Итоговое вознаграждение оплачено. Дело готовится к закрытию.",
    CaseStatus.M1_CLOSED.value: "Все основные действия завершены. Материалы и история дела остаются доступными.",
    CaseStatus.M2_CONSULTATION_ROUTE.value: "Начато оформление консультации с профильным юристом.",
    CaseStatus.M2_DESCRIPTION_PENDING.value: "Кратко опишите ситуацию и сформулируйте главный вопрос к юристу.",
    CaseStatus.M2_DOCUMENTS_OPTIONAL.value: "Приложите материалы, которые помогут юристу подготовиться, или пропустите шаг.",
    CaseStatus.M2_SLOT_PENDING.value: "Выберите свободную дату и время. Слот будет временно закреплён за вами.",
    CaseStatus.M2_PAYMENT_PENDING.value: "Оплатите консультацию до окончания срока удержания выбранного времени.",
    CaseStatus.M2_CONSULTATION_BOOKED.value: "Время закреплено. В карточке консультации доступны дата, формат и дальнейшие инструкции.",
    CaseStatus.M2_CONSULTATION_DONE.value: "Консультация состоялась. При необходимости юрист предложит дальнейший план действий.",
    CaseStatus.M2_TO_M1.value: "После консультации юрист рекомендует перейти к полному ведению дела.",
    CaseStatus.M2_CLOSED.value: "Консультационный маршрут завершён.",
    CaseStatus.ERROR.value: "Автоматическое продолжение остановлено, чтобы сотрудник безопасно проверил данные.",
    CaseStatus.ARCHIVED.value: "Активная работа по делу завершена, запись сохранена в архиве.",
}

CLIENT_STATUS_OWNERS = {
    CaseStatus.NEW.value: "Клиент",
    CaseStatus.CALCULATOR_STARTED.value: "Клиент",
    CaseStatus.CALCULATED.value: "Клиент",
    CaseStatus.CLIENT_DECISION.value: "Клиент",
    CaseStatus.M1_DOCUMENTS_PENDING.value: "Клиент",
    CaseStatus.M1_DOCUMENTS_RECEIVED.value: "Менеджер",
    CaseStatus.M1_LAWYER_REVIEW.value: "Юрист",
    CaseStatus.M1_DOCS_REQUESTED.value: "Клиент",
    CaseStatus.M1_ACCEPTED.value: "Юрист",
    CaseStatus.M1_REJECTED.value: "Менеджер",
    CaseStatus.M1_CONTRACT_READY.value: "Клиент",
    CaseStatus.M1_WAITING_PAYMENT_30000.value: "Клиент",
    CaseStatus.M1_PAYMENT_30000_RECEIVED.value: "Система",
    CaseStatus.M1_POWER_OF_ATTORNEY.value: "Клиент",
    CaseStatus.M1_POA_RECEIVED.value: "Юрист",
    CaseStatus.M1_CLAIM_PREPARATION.value: "Юрист",
    CaseStatus.M1_CLAIM_SENT.value: "Юрист",
    CaseStatus.M1_WAITING_30_DAYS.value: "Система контролирует срок",
    CaseStatus.M1_COURT_STAGE.value: "Юрист",
    CaseStatus.M1_WAITING_PAYMENT_70000.value: "Клиент",
    CaseStatus.M1_PAYMENT_70000_RECEIVED.value: "Система",
    CaseStatus.M1_ENFORCEMENT.value: "Юрист",
    CaseStatus.M1_MONEY_RECEIVED.value: "Менеджер",
    CaseStatus.M1_WAITING_SUCCESS_FEE.value: "Клиент",
    CaseStatus.M1_SUCCESS_FEE_RECEIVED.value: "Система",
    CaseStatus.M1_CLOSED.value: "Действий не требуется",
    CaseStatus.M2_CONSULTATION_ROUTE.value: "Клиент",
    CaseStatus.M2_DESCRIPTION_PENDING.value: "Клиент",
    CaseStatus.M2_DOCUMENTS_OPTIONAL.value: "Клиент",
    CaseStatus.M2_SLOT_PENDING.value: "Клиент",
    CaseStatus.M2_PAYMENT_PENDING.value: "Клиент",
    CaseStatus.M2_CONSULTATION_BOOKED.value: "Юрист",
    CaseStatus.M2_CONSULTATION_DONE.value: "Юрист",
    CaseStatus.M2_TO_M1.value: "Клиент",
    CaseStatus.M2_CLOSED.value: "Действий не требуется",
    CaseStatus.ERROR.value: "Менеджер",
    CaseStatus.ARCHIVED.value: "Действий не требуется",
}

CASE_PROGRESS_PERCENT = {
    CaseStatus.NEW.value: 0,
    CaseStatus.CALCULATOR_STARTED.value: 5,
    CaseStatus.CALCULATED.value: 10,
    CaseStatus.CLIENT_DECISION.value: 12,
    CaseStatus.M1_DOCUMENTS_PENDING.value: 18,
    CaseStatus.M1_DOCUMENTS_RECEIVED.value: 23,
    CaseStatus.M1_LAWYER_REVIEW.value: 28,
    CaseStatus.M1_DOCS_REQUESTED.value: 25,
    CaseStatus.M1_ACCEPTED.value: 35,
    CaseStatus.M1_REJECTED.value: 100,
    CaseStatus.M1_CONTRACT_READY.value: 40,
    CaseStatus.M1_WAITING_PAYMENT_30000.value: 44,
    CaseStatus.M1_PAYMENT_30000_RECEIVED.value: 48,
    CaseStatus.M1_POWER_OF_ATTORNEY.value: 52,
    CaseStatus.M1_POA_RECEIVED.value: 56,
    CaseStatus.M1_CLAIM_PREPARATION.value: 60,
    CaseStatus.M1_CLAIM_SENT.value: 65,
    CaseStatus.M1_WAITING_30_DAYS.value: 70,
    CaseStatus.M1_COURT_STAGE.value: 76,
    CaseStatus.M1_WAITING_PAYMENT_70000.value: 80,
    CaseStatus.M1_PAYMENT_70000_RECEIVED.value: 83,
    CaseStatus.M1_ENFORCEMENT.value: 88,
    CaseStatus.M1_MONEY_RECEIVED.value: 94,
    CaseStatus.M1_WAITING_SUCCESS_FEE.value: 96,
    CaseStatus.M1_SUCCESS_FEE_RECEIVED.value: 99,
    CaseStatus.M1_CLOSED.value: 100,
    CaseStatus.M2_CONSULTATION_ROUTE.value: 10,
    CaseStatus.M2_DESCRIPTION_PENDING.value: 20,
    CaseStatus.M2_DOCUMENTS_OPTIONAL.value: 30,
    CaseStatus.M2_SLOT_PENDING.value: 45,
    CaseStatus.M2_PAYMENT_PENDING.value: 60,
    CaseStatus.M2_CONSULTATION_BOOKED.value: 80,
    CaseStatus.M2_CONSULTATION_DONE.value: 95,
    CaseStatus.M2_TO_M1.value: 15,
    CaseStatus.M2_CLOSED.value: 100,
    CaseStatus.ERROR.value: 0,
    CaseStatus.ARCHIVED.value: 100,
}

M1_ROADMAP = (
    (10, "Расчёт и выбор формата"),
    (30, "Документы и юридическая проверка"),
    (45, "Договор и первый платёж"),
    (56, "Оформление доверенности"),
    (70, "Претензия и ожидание ответа"),
    (83, "Судебный этап"),
    (96, "Исполнение и получение денег"),
    (100, "Завершение дела"),
)

M2_ROADMAP = (
    (20, "Описание ситуации"),
    (30, "Материалы для юриста"),
    (50, "Выбор даты и времени"),
    (65, "Оплата консультации"),
    (80, "Подтверждение и бронирование"),
    (95, "Проведение консультации"),
    (100, "Завершение обращения"),
)


def get_route_title(route) -> str:
    return ROUTE_TITLES.get(_value(route), "Маршрут ещё не выбран")


def get_client_visible_status(status) -> str:
    return CLIENT_VISIBLE_STATUS_TITLES.get(_value(status), "Статус обновляется")


def get_client_status_description(status) -> str:
    return CLIENT_STATUS_DESCRIPTIONS.get(
        _value(status),
        "Мы уточняем состояние дела. При необходимости сотрудник свяжется с вами.",
    )


def get_client_status_owner(status) -> str:
    return CLIENT_STATUS_OWNERS.get(_value(status), "Менеджер")


def get_case_progress_percent(status) -> int:
    return CASE_PROGRESS_PERCENT.get(_value(status), 0)


def get_case_progress_bar(status, width: int = 10) -> str:
    width = max(5, min(int(width), 20))
    progress = get_case_progress_percent(status)
    filled = min(width, max(0, round(progress * width / 100)))
    return "█" * filled + "░" * (width - filled)


def get_route_roadmap(route, progress: int) -> list[dict[str, str | int]]:
    roadmap = M2_ROADMAP if _value(route) == RouteCode.M2.value else M1_ROADMAP
    progress = max(0, min(int(progress), 100))
    result: list[dict[str, str | int]] = []
    current_marked = False

    for threshold, title in roadmap:
        if progress >= 100 or progress > threshold:
            state = "done"
        elif not current_marked:
            state = "current"
            current_marked = True
        else:
            state = "upcoming"
        result.append({"threshold": threshold, "title": title, "state": state})

    return result
