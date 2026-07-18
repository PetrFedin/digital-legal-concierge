from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.consultations.slot_service import SlotService
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.consultation import Consultation


class ConsultationService:
    def __init__(self, db: AsyncSession):