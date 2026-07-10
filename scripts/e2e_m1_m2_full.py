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


async def schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def lawyer(db):
    res = await db.execute(select(Lawyer).where(Lawyer.email == 'full-lawyer@example.com'))
    l = res.scalars().first()
    if not l:
        l = Lawyer(full_name='Юрист полного теста', email='full-lawyer@example.com', specialization='ДДУ 214-ФЗ')
        db.add(l); await db.flush()
    return l

async def pay(db, case, code):
    service = PaymentService(db)
    p = await service.get_or_create_payment(case=case, payment_code=code)
    await service.create_payment_link(p)
    await PaymentWebhookService(db).process_successful_payment(payment=p, case=case, provider_payload={'source':'full_e2e'})
    return p

async def full_m1(db):
    user = await UserService(db).get_or_create_from_telegram(telegram_id=20001, telegram_username='full_m1', full_name='Полный клиент М1')
    cs = CaseService(db)
    case = await cs.create_case(client=user, status=CaseStatus.NEW, title='Full M1')
    await CalculatorService(db).calculate_and_save(case=case, contract_price=Decimal('9000000'), planned_transfer_date=date(2023,1,1), object_transferred=False)
    await cs.change_status(case=case, next_status=CaseStatus.M1_DOCUMENTS_PENDING, actor_type='client', actor_id=user.id, force=True)
    ds = DocumentService(db)
    await ds.create_document(case=case, uploaded_by_user_id=user.id, document_type='DDU', file_name='ddu.pdf', file_path='storage/test/ddu.pdf', mime_type='application/pdf', file_size=1000)
    await ds.send_documents_to_review(case=case, actor_id=user.id)
    for st in [CaseStatus.M1_LAWYER_REVIEW, CaseStatus.M1_CONTRACT_READY, CaseStatus.M1_WAITING_PAYMENT_30000]:
        await cs.change_status(case=case, next_status=st, actor_type='system', actor_id=None, force=True)
    await pay(db, case, PaymentCode.M1_INITIAL_PAYMENT)
    for st in [CaseStatus.M1_POA_RECEIVED, CaseStatus.M1_CLAIM_PREPARATION, CaseStatus.M1_CLAIM_SENT, CaseStatus.M1_WAITING_30_DAYS, CaseStatus.M1_COURT_STAGE, CaseStatus.M1_WAITING_PAYMENT_70000]:
        await cs.change_status(case=case, next_status=st, actor_type='lawyer', actor_id=1, force=True)
    await pay(db, case, PaymentCode.M1_COURT_PAYMENT)
    await cs.change_status(case=case, next_status=CaseStatus.M1_MONEY_RECEIVED, actor_type='lawyer', actor_id=1, force=True)
    await pay(db, case, PaymentCode.M1_SUCCESS_FEE)
    return case

async def full_m2(db):
    user = await UserService(db).get_or_create_from_telegram(telegram_id=20002, telegram_username='full_m2', full_name='Полный клиент М2')
    cs = CaseService(db)
    case = await cs.create_case(client=user, route='M2', status=CaseStatus.M2_DESCRIPTION_PENDING, title='Full M2')
    svc = ConsultationService(db)
    c = await svc.get_or_create_for_case(case)
    await svc.save_description(consultation=c, case=case, client_id=user.id, description='Есть допсоглашение, нужна консультация.')
    await cs.change_status(case=case, next_status=CaseStatus.M2_SLOT_PENDING, actor_type='client', actor_id=user.id, force=True)
    await svc.reserve_slot(consultation=c, case=case, client_id=user.id, scheduled_at=datetime.now(timezone.utc)+timedelta(days=1))
    await cs.change_status(case=case, next_status=CaseStatus.M2_PAYMENT_PENDING, actor_type='client', actor_id=user.id, force=True)
    await pay(db, case, PaymentCode.M2_CONSULTATION_PAYMENT)
    await svc.mark_done(consultation=c, case=case, lawyer_id=1, result='Можно переходить к стандартному взысканию.', decision='ACCEPT_TO_M1')
    await cs.change_status(case=case, next_status=CaseStatus.M2_CONSULTATION_DONE, actor_type='lawyer', actor_id=1, force=True)
    await cs.transfer_to_m1(case=case, actor_type='lawyer', actor_id=1, comment='Перевод из М2 в М1 по итогам консультации')
    return case

async def main():
    await schema()
    async with AsyncSessionLocal() as db:
        await SettingsService(db).bootstrap_defaults()
        await lawyer(db)
        m1 = await full_m1(db)
        m2 = await full_m2(db)
        await db.commit()
        print('OK FULL')
        print(f'M1 final: {m1.case_number} {m1.status} {m1.route}')
        print(f'M2->M1 final: {m2.case_number} {m2.status} {m2.route}')

if __name__ == '__main__':
    asyncio.run(main())
