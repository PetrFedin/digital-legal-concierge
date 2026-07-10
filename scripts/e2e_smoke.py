import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import select

from app.db.session import AsyncSessionLocal, engine
from app.models import Base
from app.models.lawyer import Lawyer
from app.domain.users.user_service import UserService
from app.domain.cases.case_service import CaseService
from app.domain.calculator.calculator_service import CalculatorService
from app.domain.documents.document_service import DocumentService
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.statuses.case_statuses import CaseStatus
from app.system.settings_service import SettingsService


async def ensure_schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def ensure_lawyer(db):
    result = await db.execute(select(Lawyer).where(Lawyer.email == "lawyer@example.com"))
    lawyer = result.scalars().first()
    if not lawyer:
        lawyer = Lawyer(full_name="Дежурный юрист", email="lawyer@example.com", specialization="ДДУ 214-ФЗ")
        db.add(lawyer)
        await db.flush()
    return lawyer


async def run_m1(db):
    user = await UserService(db).get_or_create_from_telegram(
        telegram_id=10001,
        telegram_username="m1_client",
        full_name="Клиент М1",
    )
    case_service = CaseService(db)
    case = await case_service.create_case(client=user, status=CaseStatus.NEW, title="Smoke M1")

    await CalculatorService(db).calculate_and_save(
        case=case,
        contract_price=Decimal("8500000"),
        planned_transfer_date=date(2023, 9, 15),
        object_transferred=False,
    )
    await case_service.change_status(case=case, next_status=CaseStatus.M1_DOCUMENTS_PENDING, actor_type="client", actor_id=user.id, force=True)

    docs = DocumentService(db)
    await docs.create_document(case=case, uploaded_by_user_id=user.id, document_type="DDU", file_name="ddu.pdf", file_path="demo/ddu.pdf", mime_type="application/pdf", file_size=1000)
    await docs.send_documents_to_review(case=case, actor_id=user.id)
    await case_service.change_status(case=case, next_status=CaseStatus.M1_LAWYER_REVIEW, actor_type="system", actor_id=None, force=True)
    await case_service.change_status(case=case, next_status=CaseStatus.M1_CONTRACT_READY, actor_type="lawyer", actor_id=1, force=True)
    await case_service.change_status(case=case, next_status=CaseStatus.M1_WAITING_PAYMENT_30000, actor_type="system", actor_id=None, force=True)

    payment_service = PaymentService(db)
    payment = await payment_service.get_or_create_payment(case=case, payment_code=PaymentCode.M1_INITIAL_PAYMENT)
    await payment_service.create_payment_link(payment)
    await PaymentWebhookService(db).process_successful_payment(payment=payment, case=case, provider_payload={"source": "e2e_smoke"})
    return case


async def run_m2(db):
    user = await UserService(db).get_or_create_from_telegram(
        telegram_id=10002,
        telegram_username="m2_client",
        full_name="Клиент М2",
    )
    case_service = CaseService(db)
    case = await case_service.create_case(client=user, route="M2", status=CaseStatus.M2_DESCRIPTION_PENDING, title="Smoke M2")
    consultation_service = ConsultationService(db)
    consultation = await consultation_service.get_or_create_for_case(case)
    await consultation_service.save_description(
        consultation=consultation,
        case=case,
        client_id=user.id,
        description="Не знаю дату передачи и есть дополнительное соглашение. Нужна консультация.",
    )
    await case_service.change_status(case=case, next_status=CaseStatus.M2_SLOT_PENDING, actor_type="client", actor_id=user.id, force=True)
    await consultation_service.reserve_slot(
        consultation=consultation,
        case=case,
        client_id=user.id,
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    await case_service.change_status(case=case, next_status=CaseStatus.M2_PAYMENT_PENDING, actor_type="client", actor_id=user.id, force=True)
    payment_service = PaymentService(db)
    payment = await payment_service.get_or_create_payment(case=case, payment_code=PaymentCode.M2_CONSULTATION_PAYMENT)
    await payment_service.create_payment_link(payment)
    await PaymentWebhookService(db).process_successful_payment(payment=payment, case=case, provider_payload={"source": "e2e_smoke"})
    return case


async def main():
    await ensure_schema()
    async with AsyncSessionLocal() as db:
        await SettingsService(db).bootstrap_defaults()
        await ensure_lawyer(db)
        m1 = await run_m1(db)
        m2 = await run_m2(db)
        await db.commit()
        print("OK")
        print(f"M1: {m1.case_number} status={m1.status} route={m1.route}")
        print(f"M2: {m2.case_number} status={m2.status} route={m2.route}")


if __name__ == "__main__":
    asyncio.run(main())
