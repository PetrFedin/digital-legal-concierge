from __future__ import annotations

from sqlalchemy import select

from app.domain.cases.case_service import CaseService
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.document_statuses import DocumentStatus
from app.models.document import Document


class LawyerDecisionService:
    def __init__(self, db):
        self.db = db
        self.cases = CaseService(db)

    async def assert_documents_ready_for_acceptance(self, *, case) -> None:
        documents = (
            await self.db.execute(
                select(Document)
                .where(Document.case_id == case.id)
                .order_by(
                    Document.document_type.asc(),
                    Document.version.desc(),
                    Document.created_at.desc(),
                )
            )
        ).scalars().all()

        latest_by_type: dict[str, Document] = {}
        for document in documents:
            latest_by_type.setdefault(document.document_type, document)

        ddu = latest_by_type.get("DDU")
        if not ddu:
            raise ValueError(
                "Нельзя принять дело: актуальная версия ДДУ не загружена"
            )
        if DocumentStatus(str(ddu.status)) != DocumentStatus.APPROVED:
            raise ValueError(
                "Нельзя принять дело: актуальная версия ДДУ ещё не принята юристом"
            )

        unresolved = [
            document.title
            for document in latest_by_type.values()
            if DocumentStatus(str(document.status))
            not in {DocumentStatus.APPROVED, DocumentStatus.ARCHIVED}
        ]
        if unresolved:
            labels = ", ".join(sorted(set(unresolved)))
            raise ValueError(
                "Нельзя принять дело: завершите проверку документов — " + labels
            )

    async def accept_m1_case(self, *, case, lawyer_id: int, comment=None):
        await self.assert_documents_ready_for_acceptance(case=case)
        await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_ACCEPTED,
            actor_type="lawyer",
            actor_id=lawyer_id,
            comment=comment,
        )
        return await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_CONTRACT_READY,
            actor_type="lawyer",
            actor_id=lawyer_id,
            comment="Открыт этап договора",
        )

    async def request_more_documents(self, *, case, lawyer_id: int, comment: str):
        return await self.cases.change_status(
            case=case,
            next_status=CaseStatus.M1_DOCS_REQUESTED,
            actor_type="lawyer",
            actor_id=lawyer_id,
            comment=comment,
        )

    async def transfer_m1_to_m2(self, *, case, lawyer_id: int, reason: str):
        return await self.cases.transfer_to_m2(
            case=case,
            actor_type="lawyer",
            actor_id=lawyer_id,
            reason=reason,
        )
