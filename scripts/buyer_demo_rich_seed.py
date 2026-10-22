"""Isolated demo-only sample cases for authentic browser screenshots.

Never run with production DB, bot polling, real customers or real payments.
All names, identifiers, stages and amounts are synthetic. No file bytes.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.court_event import CourtEvent
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.user import User
from scripts.buyer_demo_capture import check_environment


async def seed_rich_cases() -> None:
    check_environment()
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        lawyer = (
            await db.execute(
                select(Lawyer).where(Lawyer.email == "browser-lawyer@example.test")
            )
        ).scalar_one()
        lawyer.full_name = "Елена Романова (демо)"

        examples = (
            (988810001, "Анна Смирнова (демо)", "DEMO-DDU-001", "M1",
             "M1_CLAIM_SENT", "Претензия застройщику · просрочка передачи",
             "Ожидать ответа застройщика; проверить срок", lawyer.id, "ACTION_PENDING", now + timedelta(days=3)),
            (988810002, "Михаил Иванов (демо)", "DEMO-DDU-002", "M1",
             "M1_WAITING_PAYMENT_70000", "Решение суда · следующий платёж",
             "Проверить судебное решение и оплату этапа", lawyer.id, "ACTION_PENDING", now + timedelta(days=2)),
            (988810003, "Анастасия Алексеева (демо)", "DEMO-DDU-003", "M1",
             "M1_DOCUMENTS_RECEIVED", "Новое дело · первичная проверка",
             "Назначить юриста и проверить материалы", None, "NOT_STARTED", None),
            (988810004, "Дмитрий Орлов (демо)", "DEMO-DDU-004", "M2",
             "M2_CONSULTATION_BOOKED", "Консультация по условиям ДДУ",
             "Изучить описание вопроса перед консультацией", lawyer.id, "ACTION_PENDING", now + timedelta(hours=4)),
            (988810005, "Вера Кузнецова (демо)", "DEMO-DDU-005", "M1",
             "M1_POWER_OF_ATTORNEY", "Доверенность · контроль срока",
             "Связаться с клиентом: ожидается доверенность", lawyer.id, "ACTION_OVERDUE", now - timedelta(hours=4)),
            (988810006, "Никита Соколов (демо)", "DEMO-DDU-006", "M1",
             "M1_ENFORCEMENT", "Исполнительное производство · контроль",
             "Проверить исполнение и подтвердить поступления", lawyer.id, "ACTION_PENDING", now + timedelta(days=1)),
        )
        records: dict[str, Case] = {}
        for tg, name, code, route, status, title, action, lawyer_id, sla, due in examples:
            user = User(telegram_id=tg, full_name=name)
            db.add(user)
            await db.flush()
            case = Case(
                case_number=code, client_id=user.id, route=route, status=status,
                title=title, next_action=action, assigned_lawyer_id=lawyer_id,
                assigned_at=now-timedelta(days=20) if lawyer_id else None,
                sla_status=sla, sla_due_at=due, source="ci_buyer_demo",
            )
            db.add(case)
            await db.flush()
            records[code] = case

        claim = records["DEMO-DDU-001"]
        claim.contract_signed_at = now - timedelta(days=40)
        claim.claim_sent_at = now - timedelta(days=12)
        claim.claim_waiting_until = now + timedelta(days=18)
        judgment = records["DEMO-DDU-002"]
        judgment.contract_signed_at = now - timedelta(days=110)
        judgment.lawsuit_filed_at = now - timedelta(days=70)
        judgment.decision_date = now - timedelta(days=5)
        enforcement = records["DEMO-DDU-006"]
        enforcement.enforcement_status = "IN_PROGRESS"
        enforcement.enforcement_number = "DEMO-IP-006"
        enforcement.enforcement_started_at = now - timedelta(days=12)

        db.add_all([
            Payment(
                case_id=claim.id, payment_code="M1_INITIAL_PAYMENT",
                title="Первый этап сопровождения · демо",
                amount=Decimal("30000.00"), currency="RUB",
                status="PAID", provider="fake", provider_payment_id="demo-paid-001",
            ),
            Payment(
                case_id=judgment.id, payment_code="M1_INITIAL_PAYMENT",
                title="Первый этап сопровождения · демо",
                amount=Decimal("30000.00"), currency="RUB",
                status="PAID", provider="fake", provider_payment_id="demo-paid-002",
            ),
            Payment(
                case_id=judgment.id, payment_code="M1_COURT_PAYMENT",
                title="Этап после судебного решения · демо",
                amount=Decimal("70000.00"), currency="RUB",
                status="PENDING", provider="fake", provider_payment_id="demo-pending-002",
            ),
            Payment(
                case_id=enforcement.id, payment_code="M1_COURT_PAYMENT",
                title="Судебный этап · демо",
                amount=Decimal("70000.00"), currency="RUB",
                status="PAID", provider="fake", provider_payment_id="demo-paid-006",
            ),
            Consultation(
                case_id=records["DEMO-DDU-004"].id, lawyer_id=lawyer.id,
                status="BOOKED", consultation_type="online",
                subject_type="ddu", scheduled_at=now + timedelta(hours=2),
                client_description="Уточнение условий передачи квартиры по ДДУ. Данные вымышлены.",
            ),
            CourtEvent(
                case_id=judgment.id, lawyer_id=lawyer.id,
                event_type="Судебное решение (демо)",
                event_date=now - timedelta(days=5),
                court_name="Учебный пример суда", court_number="DEMO-2026-002",
                result="Результат внесён в тестовый сценарий; не реальное судебное дело",
                client_comment="Переход к этапу оплаты юридического сопровождения",
            ),
        ])
        await db.commit()


if __name__ == "__main__":
    asyncio.run(seed_rich_cases())
