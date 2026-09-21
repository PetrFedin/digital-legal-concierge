from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_activity import CaseActivityService
from app.domain.cases.case_timeline import (
    get_case_progress_percent,
    get_client_visible_status,
)
from app.domain.documents.document_workflow import (
    ACTIONABLE_REVIEW_STATUSES,
    LEGACY_ATTENTION_STATUSES,
    normalize_document_status,
)
from app.domain.messages.message_service import MessageService
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.calculation import Calculation
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.payment import Payment
from app.presentation_time import format_business_datetime


@dataclass(frozen=True)
class ClientAction:
    label: str
    callback: str
    description: str


@dataclass(frozen=True)
class DocumentOverview:
    current_count: int
    archived_count: int
    uploaded_count: int
    review_count: int
    approved_count: int
    replacement_count: int
    summary: str
    blocker: str | None
    latest_updated_at: datetime | None
    legacy_attention_count: int = 0


@dataclass(frozen=True)
class ClientStageProjection:
    status_label: str
    now_text: str
    client_requirement: str
    blocker: str | None
    action: ClientAction | None


@dataclass(frozen=True)
class ClientCaseView:
    case_id: int
    case_number: str
    case_status: str
    route: str | None
    route_label: str
    status_label: str
    progress_percent: int
    now_text: str
    client_requirement: str
    blocker: str | None
    next_action: str
    action: ClientAction | None
    action_key: str
    documents: DocumentOverview
    calculation_summary: str
    consultation_summary: str
    payments_summary: str | None
    history_summary: str
    updated_at: datetime | None
    unread_team_messages: int = 0
    latest_team_message_at: datetime | None = None


CLIENT_ACTIONS: dict[str, ClientAction] = {
    "CALCULATED": ClientAction(
        "Выбрать дальнейший путь",
        "calc_decision_open",
        "Выберите: продолжить ведение дела, перейти к консультации или пока ничего не менять.",
    ),
    "CLIENT_DECISION": ClientAction(
        "Подтвердить согласие",
        "consent_open",
        "Вы выбрали ведение дела. Подтвердите согласие, чтобы безопасно передать документы юристу.",
    ),
    "M1_DOCUMENTS_PENDING": ClientAction(
        "Загрузить документы",
        "documents_open",
        "Загрузите актуальный ДДУ и остальные материалы по делу.",
    ),
    "M1_DOCUMENTS_RECEIVED": ClientAction(
        "Открыть документы",
        "documents_open",
        "Документы уже переданы. Откройте список, чтобы проверить состав и текущий статус файлов.",
    ),
    "M1_LAWYER_REVIEW": ClientAction(
        "Открыть документы",
        "documents_open",
        "Юрист проверяет документы. Здесь можно увидеть актуальные версии и замечания без повторной отправки.",
    ),
    "M1_DOCS_REQUESTED": ClientAction(
        "Добавить документы",
        "documents_open",
        "Добавьте документы или исправьте файл по замечанию юриста.",
    ),
    "M1_ACCEPTED": ClientAction(
        "Посмотреть ход дела",
        "case_history_open",
        "Дело принято юристом. Следующий рабочий этап откроется командой; история уже доступна для просмотра.",
    ),
    "M1_CONTRACT_READY": ClientAction(
        "Открыть договор",
        "contract_open",
        "Ознакомьтесь с договором и подтвердите продолжение работы.",
    ),
    "M1_WAITING_PAYMENT_30000": ClientAction(
        "Продолжить оформление",
        "pay_start_30000",
        "Продолжите к этапу оформления доверенности.",
    ),
    "M1_PAYMENT_30000_RECEIVED": ClientAction(
        "Проверить оплаты",
        "payments_open",
        "Первый платёж получен. Откройте историю оплат; следующий этап появится после системной обработки платежа.",
    ),
    "M1_POWER_OF_ATTORNEY": ClientAction(
        "Оформить доверенность",
        "poa_instruction",
        "Откройте инструкцию по оформлению доверенности.",
    ),
    "M1_POA_RECEIVED": ClientAction(
        "Посмотреть ход дела",
        "case_history_open",
        "Доверенность передана юристу. От вас сейчас ничего не требуется; ход подготовки претензии доступен в истории.",
    ),
    "M1_CLAIM_PREPARATION": ClientAction(
        "Посмотреть ход дела",
        "case_history_open",
        "Юрист готовит претензию. Откройте историю, чтобы видеть зафиксированные события без лишних повторных действий.",
    ),
    "M1_CLAIM_SENT": ClientAction(
        "Посмотреть ход дела",
        "case_history_open",
        "Претензия направлена. Откройте историю дела; следующий процессуальный этап будет открыт юристом по фактическим событиям.",
    ),
    "M1_WAITING_30_DAYS": ClientAction(
        "Открыть срок ожидания",
        "court_status",
        "Проверьте текущий срок после претензии. Этот экран только показывает состояние и сам не открывает судебный этап.",
    ),
    "M1_COURT_STAGE": ClientAction(
        "Открыть судебный статус",
        "court_status",
        "Откройте судебный этап и актуальные безопасные действия по делу.",
    ),
    "M1_WAITING_PAYMENT_70000": ClientAction(
        "Продолжить исполнение",
        "pay_court_70000",
        "Продолжите к этапу исполнения решения.",
    ),
    "M1_PAYMENT_70000_RECEIVED": ClientAction(
        "Проверить оплаты",
        "payments_open",
        "Второй платёж получен. Откройте оплаты; исполнительный этап откроется по штатной логике после подтверждения.",
    ),
    "M1_ENFORCEMENT": ClientAction(
        "Следить за исполнением",
        "case_history_open",
        "Исполнительное производство идёт. Значимые события фиксируются в истории дела; дополнительных действий сейчас не требуется.",
    ),
    "M1_MONEY_RECEIVED": ClientAction(
        "Завершить финансовый этап",
        "pay_success_fee",
        "Подтвердите финальный финансовый этап сопровождения.",
    ),
    "M1_WAITING_SUCCESS_FEE": ClientAction(
        "Завершить финансовый этап",
        "pay_success_fee",
        "Завершите финальный финансовый этап сопровождения.",
    ),
    "M1_SUCCESS_FEE_RECEIVED": ClientAction(
        "Проверить оплаты",
        "payments_open",
        "Финальный платёж получен. Откройте историю оплат; закрытие дела выполняется системой после подтверждённого финансового события.",
    ),
    "M1_REJECTED": ClientAction(
        "Выбрать, что делать дальше",
        "contact_lawyer",
        "Выберите следующий шаг после решения юриста: перейти к консультации, завершить обращение или сначала написать команде.",
    ),
    "M2_CONSULTATION_ROUTE": ClientAction(
        "Описать вопрос",
        "consult_description_start",
        "Вы выбрали консультацию. Кратко опишите ситуацию и вопрос, чтобы продолжить без возврата в главное меню.",
    ),
    "M2_DESCRIPTION_PENDING": ClientAction(
        "Описать вопрос",
        "consult_description_start",
        "Кратко опишите ситуацию, чтобы юрист смог подготовиться.",
    ),
    "M2_DOCUMENTS_OPTIONAL": ClientAction(
        "Выбрать время",
        "consult_booking_start",
        "Вопрос сохранён. Документы для консультации необязательны; выберите удобное время или добавьте материалы отдельно.",
    ),
    "M2_SLOT_PENDING": ClientAction(
        "Выбрать время",
        "consult_slot_open",
        "Выберите доступную дату и время консультации.",
    ),
    "M2_PAYMENT_PENDING": ClientAction(
        "Подтвердить запись",
        "consult_pay",
        "Подтвердите выбранное время консультации.",
    ),
    "M2_CONSULTATION_BOOKED": ClientAction(
        "Открыть запись",
        "consultation_booked_open",
        "Проверьте дату, время и данные подтверждённой консультации.",
    ),
    "M2_CONSULTATION_DONE": ClientAction(
        "Открыть итог консультации",
        "consultation_result_open",
        "Консультация проведена. Откройте сохранённый итог и дальнейшие рекомендации юриста.",
    ),
    "M2_TO_M1": ClientAction(
        "Обновить дело",
        "my_case_open",
        "Юрист переводит обращение в стандартное ведение. Обновите карточку, чтобы увидеть подтверждённый этап M1.",
    ),
    "ERROR": ClientAction(
        "Связаться с юристом",
        "contact_lawyer",
        "Не удалось определить следующий автоматический этап. Напишите юристу.",
    ),
}

CLIENT_STAGE_COPY: dict[str, tuple[str, str]] = {
    "NEW": (
        "Обращение создано. Данные для предварительного расчёта ещё не собраны полностью.",
        "Завершите предварительный расчёт, чтобы система смогла показать доступные варианты продолжения.",
    ),
    "CALCULATOR_STARTED": (
        "Предварительный расчёт ещё не завершён.",
        "Продолжите ввод данных расчёта или вернитесь к нему позже — уже сохранённые данные не должны теряться.",
    ),
    "CALCULATED": (
        "Предварительный расчёт готов и сохранён в обращении.",
        "Выберите дальнейший формат работы: стандартное ведение, консультацию или паузу без изменения дела.",
    ),
    "CLIENT_DECISION": (
        "Выбран стандартный маршрут ведения дела; передача документов ещё не подтверждена.",
        "Подтвердите согласие, после чего можно будет передать документы юристу.",
    ),
    "M1_DOCUMENTS_PENDING": (
        "Для стандартного ведения дела нужен комплект документов.",
        "Загрузите актуальный ДДУ и остальные имеющиеся материалы.",
    ),
    "M1_DOCUMENTS_RECEIVED": (
        "Документы получены системой; новые файлы могут ожидать передачи юристу.",
        "Проверьте состав документов и передайте новые файлы на проверку, если они ещё не переданы.",
    ),
    "M1_LAWYER_REVIEW": (
        "Юрист проверяет переданные документы и их достаточность для дальнейшей работы.",
        "Если юрист не запросил новую версию, от вас сейчас дополнительных действий не требуется.",
    ),
    "M1_DOCS_REQUESTED": (
        "После проверки требуется дополнить комплект или заменить один из файлов.",
        "Откройте документы и загрузите требуемую актуальную версию.",
    ),
    "M1_ACCEPTED": (
        "Юрист подтвердил возможность стандартного ведения дела.",
        "От вас сейчас дополнительных действий не требуется; ожидайте открытия договорного этапа.",
    ),
    "M1_REJECTED": (
        "Стандартное ведение по результатам юридической проверки не продолжено.",
        "Выберите дальнейший вариант: консультация, сообщение команде или завершение обращения.",
    ),
    "M1_CONTRACT_READY": (
        "Договор подготовлен и доступен для ознакомления.",
        "Откройте договор и подтвердите продолжение работы только после ознакомления с текущей версией.",
    ),
    "M1_WAITING_PAYMENT_30000": (
        "Договорный этап завершён, ожидается подтверждение первого платежа.",
        "Откройте финансовый этап и выполните доступное действие по первому платежу.",
    ),
    "M1_PAYMENT_30000_RECEIVED": (
        "Первый платёж получен и зафиксирован в истории дела.",
        "От вас сейчас дополнительных действий не требуется; следующий этап откроется после подтверждённой обработки платежа.",
    ),
    "M1_POWER_OF_ATTORNEY": (
        "Для дальнейшей юридической работы требуется доверенность.",
        "Откройте инструкцию и оформите доверенность по указанным требованиям.",
    ),
    "M1_POA_RECEIVED": (
        "Доверенность получена юридической командой.",
        "От вас сейчас ничего не требуется; команда продолжает подготовку претензии.",
    ),
    "M1_CLAIM_PREPARATION": (
        "Юрист готовит претензию по материалам дела.",
        "От вас сейчас ничего не требуется; значимые события будут отражаться в истории.",
    ),
    "M1_CLAIM_SENT": (
        "Претензия направлена адресату.",
        "От вас сейчас ничего не требуется; ожидается дальнейшее процессуальное событие.",
    ),
    "M1_WAITING_30_DAYS": (
        "Идёт установленный этап ожидания после направления претензии.",
        "От вас сейчас ничего не требуется; откройте срок ожидания для актуальной информации.",
    ),
    "M1_COURT_STAGE": (
        "Дело находится на судебном этапе.",
        "Откройте судебный статус; новые действия появляются только после подтверждённых событий по делу.",
    ),
    "M1_WAITING_PAYMENT_70000": (
        "Для перехода к исполнению ожидается подтверждение второго платежа.",
        "Откройте финансовый этап и выполните доступное действие по второму платежу.",
    ),
    "M1_PAYMENT_70000_RECEIVED": (
        "Платёж судебного этапа получен и зафиксирован.",
        "От вас сейчас дополнительных действий не требуется; исполнительный этап откроется после подтверждённой обработки.",
    ),
    "M1_ENFORCEMENT": (
        "Идёт исполнительное производство.",
        "От вас сейчас ничего не требуется; следите за подтверждёнными событиями в истории дела.",
    ),
    "M1_MONEY_RECEIVED": (
        "Получение денежных средств по делу зафиксировано; остаётся финальный финансовый этап.",
        "Откройте финальный финансовый этап и выполните доступное действие.",
    ),
    "M1_WAITING_SUCCESS_FEE": (
        "Ожидается завершение финального финансового этапа сопровождения.",
        "Откройте финальный финансовый этап и выполните доступное действие.",
    ),
    "M1_SUCCESS_FEE_RECEIVED": (
        "Финальный платёж получен и зафиксирован.",
        "От вас дополнительных действий не требуется; дело будет закрыто после подтверждённой обработки финансового события.",
    ),
    "M1_CLOSED": (
        "Стандартное дело завершено и доступно в режиме просмотра.",
        "Действий по закрытому делу не требуется; документы, оплаты и история остаются доступны в архиве.",
    ),
    "M2_CONSULTATION_ROUTE": (
        "Выбран консультационный маршрут; описание ситуации ещё не сохранено.",
        "Кратко опишите ситуацию и конкретный вопрос для юриста.",
    ),
    "M2_DESCRIPTION_PENDING": (
        "Консультационное обращение создано, но вопрос ещё нужно описать.",
        "Сохраните описание ситуации и вопрос, чтобы перейти к подготовке консультации.",
    ),
    "M2_DOCUMENTS_OPTIONAL": (
        "Вопрос для консультации сохранён. Документы можно добавить при необходимости.",
        "Выберите время консультации; документы остаются дополнительным материалом и не должны блокировать запись.",
    ),
    "M2_SLOT_PENDING": (
        "Описание сохранено; время консультации ещё не выбрано.",
        "Выберите доступную дату и время консультации.",
    ),
    "M2_PAYMENT_PENDING": (
        "Время консультации выбрано, но запись ещё не подтверждена.",
        "Подтвердите запись доступным способом; до подтверждения слот может оставаться временно зарезервированным.",
    ),
    "M2_CONSULTATION_BOOKED": (
        "Консультация подтверждена и назначена.",
        "Проверьте дату, время и подготовленные материалы. При необходимости задайте вопрос команде.",
    ),
    "M2_CONSULTATION_DONE": (
        "Консультация проведена; результат сохранён в обращении.",
        "Откройте итог консультации и дальнейшие рекомендации юриста.",
    ),
    "M2_TO_M1": (
        "Юрист подтвердил переход из консультации к стандартному ведению.",
        "Обновите карточку обращения: следующий подтверждённый этап будет показан уже в M1.",
    ),
    "M2_CLOSED": (
        "Консультационное обращение завершено и доступно в режиме просмотра.",
        "Действий по закрытому обращению не требуется; итог, документы, оплаты и история сохранены.",
    ),
    "ERROR": (
        "Автоматически определить безопасный следующий этап сейчас не удалось.",
        "Не повторяйте старые действия вслепую; свяжитесь с юридической командой для уточнения.",
    ),
    "ARCHIVED": (
        "Обращение находится в архиве и доступно только для просмотра.",
        "Новых действий по этому обращению не требуется.",
    ),
}

_DOCUMENT_PRIMARY_STATUSES = frozenset(
    {
        "M1_DOCUMENTS_PENDING",
        "M1_DOCUMENTS_RECEIVED",
        "M1_LAWYER_REVIEW",
        "M1_DOCS_REQUESTED",
    }
)

_DOCUMENT_REPLACEMENT = {"REJECTED", "NEEDS_REUPLOAD"}
_DOCUMENT_APPROVED = {"APPROVED", "ACCEPTED", "VERIFIED"}
_CONSULTATION_LABELS = {
    ConsultationStatus.DESCRIPTION_PENDING: "Нужно описание вопроса",
    ConsultationStatus.DOCUMENTS_OPTIONAL: "Можно добавить документы",
    ConsultationStatus.SLOT_PENDING: "Нужно выбрать время",
    ConsultationStatus.SLOT_RESERVED: "Время временно зарезервировано",
    ConsultationStatus.PAYMENT_PENDING: "Нужно подтвердить запись",
    ConsultationStatus.BOOKED: "Запись подтверждена",
    ConsultationStatus.DONE: "Консультация проведена",
    ConsultationStatus.CLIENT_NO_SHOW: "Клиент не подключился",
    ConsultationStatus.LAWYER_NO_SHOW: "Юрист не подключился",
    ConsultationStatus.CANCELLED: "Запись отменена",
    ConsultationStatus.RESCHEDULED: "Запись перенесена",
    ConsultationStatus.CLOSED: "Консультация закрыта",
}


def money(value) -> str:
    return "—" if value is None else f"{value:,.2f}".replace(",", " ") + " ₽"


def route_label(route: str | None) -> str:
    return {
        "M1": "Ведение дела",
        "M2": "Консультация",
    }.get(str(route or ""), "Юридическое обращение")


def effective_client_route(case) -> str | None:
    route = str(case.route or "").strip()
    if route:
        return route
    # Historical CLIENT_DECISION records were created only after the client
    # explicitly chose M1, before route persistence was introduced.
    if str(case.status) == "CLIENT_DECISION":
        return "M1"
    return None


def client_action_for(case) -> ClientAction | None:
    return CLIENT_ACTIONS.get(str(case.status))


def next_action_text(case) -> str:
    action = client_action_for(case)
    if action:
        return action.description
    return "От вас сейчас ничего не требуется. Ожидайте подтверждённого обновления от юридической команды."


def format_consultation_time(consultation: Consultation | None) -> str | None:
    if not consultation or not consultation.scheduled_at:
        return None
    return format_business_datetime(
        consultation.scheduled_at,
        pattern="%d.%m.%Y в %H:%M",
    )


def format_updated_at(value: datetime | None) -> str:
    return format_business_datetime(value, empty="—")


def progress_bar(percent: int) -> str:
    bounded = min(max(int(percent), 0), 100)
    completed = min(10, max(0, round(bounded / 10)))
    return "●" * completed + "○" * (10 - completed) + f" {bounded}%"


def _short_comment(value: str | None, limit: int = 180) -> str | None:
    clean = " ".join(str(value or "").split())
    if not clean:
        return None
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def _document_overview(documents: list[Document]) -> DocumentOverview:
    statuses = {
        id(item): normalize_document_status(item.status)
        for item in documents
    }
    current = [item for item in documents if statuses[id(item)] != "ARCHIVED"]
    archived = [item for item in documents if statuses[id(item)] == "ARCHIVED"]
    uploaded = [item for item in current if statuses[id(item)] == "UPLOADED"]
    review = [
        item
        for item in current
        if statuses[id(item)] in ACTIONABLE_REVIEW_STATUSES
    ]
    legacy_attention = [
        item
        for item in current
        if statuses[id(item)] in LEGACY_ATTENTION_STATUSES
    ]
    approved = [
        item for item in current if statuses[id(item)] in _DOCUMENT_APPROVED
    ]
    replacement = [
        item for item in current if statuses[id(item)] in _DOCUMENT_REPLACEMENT
    ]

    blocker = None
    if replacement:
        blocker = (
            "Требуется новая версия одного или нескольких документов. "
            "Откройте раздел документов, чтобы увидеть клиентские инструкции по файлам."
        )
        summary = (
            f"{len(current)} актуальных · {len(replacement)} нужно заменить"
        )
    elif uploaded:
        summary = f"{len(current)} актуальных · {len(uploaded)} готовы к передаче"
    elif review:
        summary = f"{len(current)} актуальных · {len(review)} проверяет юрист"
        if legacy_attention:
            summary += f" · {len(legacy_attention)} статус уточняется"
    elif legacy_attention:
        blocker = (
            f"Статус {len(legacy_attention)} документов требует уточнения "
            "у юридической команды"
        )
        summary = (
            f"{len(current)} актуальных · "
            f"{len(legacy_attention)} статус уточняется"
        )
    elif current and len(approved) == len(current):
        summary = f"{len(current)} актуальных · все приняты"
    elif current:
        summary = f"{len(current)} актуальных"
    else:
        summary = "Пока документов нет"

    latest_updated_at = max(
        (item.updated_at for item in current if item.updated_at is not None),
        default=None,
    )
    return DocumentOverview(
        current_count=len(current),
        archived_count=len(archived),
        uploaded_count=len(uploaded),
        review_count=len(review),
        approved_count=len(approved),
        replacement_count=len(replacement),
        summary=summary,
        blocker=blocker,
        latest_updated_at=latest_updated_at,
        legacy_attention_count=len(legacy_attention),
    )


def _priority_action(case, documents: DocumentOverview) -> ClientAction | None:
    status = str(case.status)

    # Document facts may own the primary action only while the Case itself is
    # inside the M1 document collection/review contour. An old/rejected file
    # must never pull a court, payment or consultation Case backwards.
    if status in _DOCUMENT_PRIMARY_STATUSES:
        if documents.replacement_count:
            return ClientAction(
                "Загрузить новую версию",
                "documents_open",
                "Откройте документы и загрузите новую версию файла, который требует замены.",
            )
        if documents.uploaded_count:
            return ClientAction(
                "Передать документы юристу",
                "doc_finish_upload",
                "Передайте безопасно загруженные новые файлы юристу на проверку.",
            )
        if documents.legacy_attention_count and not documents.review_count:
            return ClientAction(
                "Уточнить статус документов",
                "message_create",
                "Статус части документов требует уточнения. Напишите команде по делу.",
            )

    return client_action_for(case)


def _route_projection_is_consistent(case) -> bool:
    status = str(case.status)
    route = effective_client_route(case)
    if status.startswith("M1_"):
        return route in {None, "M1"}
    if status.startswith("M2_") and status != "M2_TO_M1":
        return route in {None, "M2"}
    if status == "M2_TO_M1":
        return route in {None, "M1", "M2"}
    return route in {None, "M1", "M2"}


def _stage_blocker(status: str, documents: DocumentOverview) -> str | None:
    if status not in _DOCUMENT_PRIMARY_STATUSES:
        return None
    if documents.replacement_count:
        return (
            "Продолжение документального этапа ожидает новую версию одного или "
            "нескольких файлов."
        )
    if documents.legacy_attention_count and not documents.review_count:
        return (
            "Статус части документов требует уточнения юридической командой. "
            "Не загружайте дубликаты до уточнения."
        )
    return None


def client_stage_projection(case, documents: DocumentOverview) -> ClientStageProjection:
    status = str(case.status)
    safe_copy = CLIENT_STAGE_COPY.get(status)

    if safe_copy is None or not _route_projection_is_consistent(case):
        return ClientStageProjection(
            status_label="Статус уточняется",
            now_text=(
                "Текущее состояние обращения нельзя однозначно отобразить клиенту "
                "без дополнительной проверки."
            ),
            client_requirement=(
                "Не выполняйте повторные действия по старым кнопкам до уточнения состояния."
            ),
            blocker=(
                "Клиентская проекция не совпадает с подтверждённым маршрутом или "
                "использует неподдерживаемое состояние."
            ),
            action=ClientAction(
                "Связаться с юридической командой",
                "contact_lawyer",
                "Свяжитесь с юридической командой: текущее состояние требует уточнения.",
            ),
        )

    now_text, client_requirement = safe_copy
    return ClientStageProjection(
        status_label=get_client_visible_status(status),
        now_text=now_text,
        client_requirement=client_requirement,
        blocker=_stage_blocker(status, documents),
        action=_priority_action(case, documents),
    )


def _consultation_summary(consultation: Consultation | None) -> str:
    if not consultation:
        return "Не назначена"
    try:
        status = ConsultationStatus(str(consultation.status))
    except ValueError:
        status_label = "Статус уточняется"
    else:
        status_label = _CONSULTATION_LABELS.get(status, "Статус уточняется")
    scheduled = format_consultation_time(consultation)
    return f"{status_label} · {scheduled}" if scheduled else status_label


async def _client_history_summary(
    db: AsyncSession,
    *,
    case_id: int,
) -> tuple[str, int | None]:
    page = await CaseActivityService(db).page(
        case_id=case_id,
        audience="client",
        limit=1,
    )
    items = list(page.get("items") or [])
    if not items:
        return "Клиентских событий пока нет.", None
    item = items[0]
    title = " ".join(str(item.get("title") or "").split())
    if not title:
        return "История дела доступна для просмотра.", int(item.get("id") or 0) or None
    return f"Последнее событие: {title}", int(item.get("id") or 0) or None


def _calculation_summary(case, calculation: Calculation | None) -> str:
    if str(case.route or "") == "M2":
        return "Не требуется для консультации"
    if not calculation:
        return "Расчёт ещё не завершён"
    return f"{money(calculation.penalty_amount)} · просрочка {calculation.delay_days} дн."


def _payments_summary(payments: list[Payment]) -> str:
    """Describe persisted payment history independently from provider mode."""

    if not payments:
        return "Платежей по обращению нет"

    statuses = {str(payment.status) for payment in payments}
    if str(PaymentStatus.PAID_REVIEW) in statuses:
        return "Есть платёж, который проверяет команда"
    if str(PaymentStatus.REFUND_PENDING) in statuses:
        return "Возврат денежных средств обрабатывается"
    if str(PaymentStatus.REFUND_DECLINED) in statuses:
        return "По возврату требуется уточнение команды"

    pending_count = sum(
        1
        for payment in payments
        if str(payment.status)
        in {
            str(PaymentStatus.PENDING),
            str(PaymentStatus.WAITING_CONFIRMATION),
        }
    )
    if pending_count:
        return f"Ожидают подтверждения: {pending_count}"

    return f"Платежей в истории: {len(payments)} · активных действий по оплате нет"


def _action_key(
    *,
    case,
    action: ClientAction | None,
    documents: DocumentOverview,
    consultation: Consultation | None,
    payments: list[Payment],
    history_event_id: int | None,
    unread_team_messages: int = 0,
    latest_team_message_at: datetime | None = None,
) -> str:
    parts = [
        str(case.id),
        str(case.status),
        str(getattr(case, "version", "") or ""),
        case.updated_at.isoformat() if case.updated_at else "",
        action.callback if action else "wait",
        action.label if action else "",
        action.description if action else "",
        str(documents.current_count),
        str(documents.uploaded_count),
        str(documents.review_count),
        str(documents.legacy_attention_count),
        str(documents.replacement_count),
        documents.latest_updated_at.isoformat() if documents.latest_updated_at else "",
        str(consultation.status) if consultation else "",
        consultation.updated_at.isoformat()
        if consultation and consultation.updated_at
        else "",
        str(unread_team_messages),
        latest_team_message_at.isoformat() if latest_team_message_at else "",
        str(history_event_id or ""),
        *[
            ":".join(
                (
                    str(payment.id),
                    str(payment.status),
                    payment.updated_at.isoformat() if payment.updated_at else "",
                )
            )
            for payment in payments
        ],
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _latest_activity(*values: datetime | None) -> datetime | None:
    present = [value for value in values if value is not None]
    if not present:
        return None

    def sort_value(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    return max(present, key=sort_value)


async def load_client_case_view(
    db: AsyncSession,
    case,
) -> ClientCaseView:
    calculation = (
        await db.execute(
            select(Calculation)
            .where(Calculation.case_id == case.id)
            .order_by(Calculation.created_at.desc(), Calculation.id.desc())
            .limit(1)
        )
    ).scalars().first()
    documents = list(
        (
            await db.execute(
                select(Document)
                .where(Document.case_id == case.id)
                .order_by(Document.created_at.desc(), Document.id.desc())
            )
        ).scalars().all()
    )
    consultation = (
        await db.execute(
            select(Consultation)
            .where(Consultation.case_id == case.id)
            .order_by(Consultation.created_at.desc(), Consultation.id.desc())
            .limit(1)
        )
    ).scalars().first()
    payments = list(
        (
            await db.execute(
                select(Payment)
                .where(Payment.case_id == case.id)
                .order_by(Payment.created_at.desc(), Payment.id.desc())
            )
        ).scalars().all()
    )
    unread_team_messages, latest_team_message_at = (
        await MessageService(db).unread_lawyer_summary(case.id)
    )

    document_overview = _document_overview(documents)
    projection = client_stage_projection(case, document_overview)
    action = projection.action
    next_action = (
        action.description
        if action
        else projection.client_requirement
    )
    payments_summary = _payments_summary(payments)
    history_summary, history_event_id = await _client_history_summary(
        db,
        case_id=int(case.id),
    )

    consultation_updated_at = (
        consultation.updated_at if consultation and consultation.updated_at else None
    )
    payment_updated_at = max(
        (payment.updated_at for payment in payments if payment.updated_at is not None),
        default=None,
    )
    updated_at = _latest_activity(
        case.updated_at,
        document_overview.latest_updated_at,
        consultation_updated_at,
        latest_team_message_at,
        payment_updated_at,
    )
    effective_route = effective_client_route(case)

    return ClientCaseView(
        case_id=case.id,
        case_number=case.case_number,
        case_status=str(case.status),
        route=effective_route,
        route_label=route_label(effective_route),
        status_label=projection.status_label,
        progress_percent=get_case_progress_percent(case.status),
        now_text=projection.now_text,
        client_requirement=projection.client_requirement,
        blocker=projection.blocker,
        next_action=next_action,
        action=action,
        action_key=_action_key(
            case=case,
            action=action,
            documents=document_overview,
            consultation=consultation,
            payments=payments,
            history_event_id=history_event_id,
            unread_team_messages=unread_team_messages,
            latest_team_message_at=latest_team_message_at,
        ),
        documents=document_overview,
        calculation_summary=_calculation_summary(case, calculation),
        consultation_summary=_consultation_summary(consultation),
        payments_summary=payments_summary,
        history_summary=history_summary,
        updated_at=updated_at,
        unread_team_messages=unread_team_messages,
        latest_team_message_at=latest_team_message_at,
    )