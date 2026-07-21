from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.consultations.state_machine import ConsultationStateMachine
from app.domain.statuses.case_statuses import Case