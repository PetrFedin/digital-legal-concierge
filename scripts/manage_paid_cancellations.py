from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.session import AsyncSessionLocal
from app.domain.consultations.cancellation_resolution_service import (
    CancellationResolutionDecision,
    CancellationResolutionError,
    ConsultationCancellationResolutionService,
)
from app.domain.payments.refund_service import PaymentRefundError, PaymentRefundService
from app.models.case import Case
from app.models.payment import Payment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Manage paid consultation cancellation requests without direct "
            "database status edits."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List unresolved requests")
    list_parser.add_argument("--limit", type=int, default=100)

    refund_parser = subparsers.add_parser(
        "refund",
        help="Record a completed full external refund",
    )
    refund_parser.add_argument("--case-id", type=int, required=True)
    refund_parser.add_argument("--payment-id", type=int, required=True)
    refund_parser.add_argument("--actor-id", type=int, required=True)
    refund_parser.add_argument("--reference", required=True)
    refund_parser.add_argument("--comment", required=True)
    refund_parser.add_argument("--amount", type=Decimal)

    keep_parser = subparsers.add_parser(
        "keep",
        help="Resolve the request and preserve the booking",
    )
    keep_parser.add_argument("--case-id", type=int, required=True)
    keep_parser.add_argument("--actor-id", type=int, required=True)
    keep_parser.add_argument("--comment", required=True)

    close_parser = subparsers.add_parser(
        "close",
        help="Close the consultation after refund status is confirmed",
    )
    close_parser.add_argument("--case-id", type=int, required=True)
    close_parser.add_argument("--actor-id", type=int, required=True)
    close_parser.add_argument("--comment", required=True)

    return parser


async def list_pending(limit: int) -> int:
    async with AsyncSessionLocal() as db:
        rows = await ConsultationCancellationResolutionService(db).list_pending(
            limit=limit,
        )
        print(
            json.dumps(
                [
                    {
                        "request_id": row.request_id,
                        "case_id": row.case_id,
                        "case_number": row.case_number,
                        "client_id": row.client_id,
                        "consultation_id": row.consultation_id,
                        "slot_id": row.slot_id,
                        "created_at": (
                            row.created_at.isoformat()
                            if hasattr(row.created_at, "isoformat")
                            else str(row.created_at)
                        ),
                    }
                    for row in rows
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


async def record_refund(args) -> int:
    async with AsyncSessionLocal() as db:
        case = await db.get(Case, args.case_id)
        payment = await db.get(Payment, args.payment_id)
        if case is None or payment is None:
            raise PaymentRefundError("Дело или платёж не найден.")
        event = await PaymentRefundService(db).confirm_external_refund(
            payment=payment,
            case=case,
            actor_id=args.actor_id,
            refund_reference=args.reference,
            comment=args.comment,
            amount=args.amount,
            source="admin_cli",
        )
        await db.commit()
        print(
            json.dumps(
                {
                    "result": "refund_recorded",
                    "case_id": case.id,
                    "payment_id": payment.id,
                    "audit_id": event.id,
                },
                ensure_ascii=False,
            )
        )
    return 0


async def resolve_request(args, decision: CancellationResolutionDecision) -> int:
    async with AsyncSessionLocal() as db:
        event = await ConsultationCancellationResolutionService(db).resolve(
            case_id=args.case_id,
            decision=decision,
            actor_id=args.actor_id,
            comment=args.comment,
            source="admin_cli",
        )
        await db.commit()
        print(
            json.dumps(
                {
                    "result": "cancellation_request_resolved",
                    "case_id": args.case_id,
                    "decision": decision.value,
                    "audit_id": event.id,
                },
                ensure_ascii=False,
            )
        )
    return 0


async def run(args) -> int:
    if args.command == "list":
        return await list_pending(args.limit)
    if args.command == "refund":
        return await record_refund(args)
    if args.command == "keep":
        return await resolve_request(
            args,
            CancellationResolutionDecision.KEEP_BOOKING,
        )
    if args.command == "close":
        return await resolve_request(
            args,
            CancellationResolutionDecision.REFUND_CONFIRMED,
        )
    raise RuntimeError("Unsupported command")


def main() -> int:
    args = build_parser().parse_args()
    try:
        return asyncio.run(run(args))
    except (PaymentRefundError, CancellationResolutionError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
