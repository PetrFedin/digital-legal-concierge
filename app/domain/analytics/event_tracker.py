from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics_event import AnalyticsEvent


class AnalyticsEventName:
    BOT_START = "bot_start"
    CALCULATOR_START = "calculator_start"
    CALCULATOR_FINISH = "calculator_finish"
    CALC_CONTINUE_M1 = "calc_continue_m1"
    CALC_TO_M2 = "calc_to_m2"
    DOCUMENT_UPLOAD = "document_upload"
    DOCUMENTS_SENT_TO_REVIEW = "documents_sent_to_review"
    PAYMENT_CREATED = "payment_created"
    PAYMENT_SUCCESS = "payment_success"
    CONSULTATION_DESCRIPTION = "consultation_description"
    CONSULTATION_SLOT_SELECTED = "consultation_slot_selected"
    CONSULTATION_BOOKED = "consultation_booked"
    CASE_STATUS_CHANGED = "case_status_changed"
    CASE_CLOSED = "case_closed"


class AnalyticsTracker:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def track(
        self,
        *,
        event_name: str,
        user_id: int | None = None,
        case_id: int | None = None,
        route: str | None = None,
        payload: dict | None = None,
    ) -> AnalyticsEvent:
        event = AnalyticsEvent(
            event_name=event_name,
            user_id=user_id,
            case_id=case_id,
            route=route,
            source="telegram_bot",
            payload=payload or {},
        )
        self.db.add(event)
        await self.db.flush()
        return event
