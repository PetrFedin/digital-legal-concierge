from app.domain.cases.case_service import CaseService
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.domain.cases.case_history import add_case_history_event


class PaymentWebhookService:
    def __init__(self, db):
        self.db = db
        self.payments = PaymentService(db)
        self.cases = CaseService(db)

    async def process_successful_payment(self, *, payment, case, provider_payload=None):
        if payment.status != PaymentStatus.PAID:
            await self.payments.mark_paid(payment=payment, case=case, actor_type='payment_provider')

        mapping = {
            PaymentCode.M1_INITIAL_PAYMENT: [CaseStatus.M1_PAYMENT_30000_RECEIVED, CaseStatus.M1_POWER_OF_ATTORNEY],
            PaymentCode.M1_COURT_PAYMENT: [CaseStatus.M1_PAYMENT_70000_RECEIVED, CaseStatus.M1_ENFORCEMENT],
            PaymentCode.M1_SUCCESS_FEE: [CaseStatus.M1_SUCCESS_FEE_RECEIVED, CaseStatus.M1_CLOSED],
            PaymentCode.M2_CONSULTATION_PAYMENT: [CaseStatus.M2_CONSULTATION_BOOKED],
        }

        for st in mapping.get(payment.payment_code, []):
            await self.cases.change_status(case=case, next_status=st, actor_type='system', actor_id=None, force=True, comment=f'Автопереход после оплаты {payment.payment_code}')

        if payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT:
            consultation_service = ConsultationService(self.db)
            consultation = await consultation_service.get_or_create_for_case(case)
            await consultation_service.mark_booked_after_payment(consultation=consultation, case=case)

        await add_case_history_event(self.db, actor_type='payment_provider', actor_id=None, case_id=case.id, action='PAYMENT_WEBHOOK_PROCESSED', new_value={'payment_id': payment.id, 'payment_code': payment.payment_code, 'payload': provider_payload or {}})
        await self.db.flush()
        return payment

    async def process_failed_payment(self, *, payment, case, provider_payload=None):
        old = payment.status
        payment.status = PaymentStatus.FAILED
        await add_case_history_event(self.db, actor_type='payment_provider', actor_id=None, case_id=case.id, action='PAYMENT_FAILED', old_value={'status': old}, new_value={'payment_id': payment.id, 'payment_code': payment.payment_code, 'payload': provider_payload or {}})
        await self.db.flush()
        return payment
