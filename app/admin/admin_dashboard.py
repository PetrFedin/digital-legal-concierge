from sqlalchemy import func, select

from app.models.case import Case
from app.models.consultation import Consultation
from app.models.payment import Payment


class AdminDashboardService:
    def __init__(self, db):
        self.db = db

    async def build(self):
        async def count(stmt):
            result = await self.db.execute(stmt)
            return result.scalar_one()

        return {
            "new_cases": await count(
                select(func.count(Case.id)).where(Case.status == "NEW")
            ),
            "active_cases": await count(
                select(func.count(Case.id)).where(
                    Case.status.notin_(
                        ["M1_CLOSED", "M2_CLOSED", "ARCHIVED"]
                    )
                )
            ),
            "waiting_payment": await count(
                select(func.count(Payment.id)).where(
                    Payment.status.in_(["PENDING", "WAITING_CONFIRMATION"])
                )
            ),
            "payment_reviews": await count(
                select(func.count(Payment.id)).where(
                    Payment.status == "PAID_REVIEW"
                )
            ),
            "sla_overdue": await count(
                select(func.count(Case.id)).where(
                    Case.sla_status.in_(
                        ["FIRST_RESPONSE_OVERDUE", "ACTION_OVERDUE"]
                    )
                )
            ),
            "consultations_booked": await count(
                select(func.count(Consultation.id)).where(
                    Consultation.status == "BOOKED"
                )
            ),
            "closed_cases": await count(
                select(func.count(Case.id)).where(
                    Case.status.in_(
                        ["M1_CLOSED", "M2_CLOSED", "ARCHIVED"]
                    )
                )
            ),
        }
