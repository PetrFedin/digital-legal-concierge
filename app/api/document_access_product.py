"""Single runtime owner for protected document access and review endpoints.

Historically ``document_access`` nested the review router while a staff UI guard
published the same review UI first. This product router keeps the already
shipped URLs but gives them one explicit FastAPI owner.
"""

from fastapi import APIRouter

from app.api.document_access import (
    create_document_download_grant,
    download_document_once,
    list_authorized_case_documents,
    revoke_document_grant,
)
from app.api.document_review import review_document, review_queue
from app.api.staff_ui_guards import protected_document_review_ui

router = APIRouter(prefix="/document-access", tags=["document-access-product"])

router.add_api_route(
    "/cases/{case_id}/documents",
    list_authorized_case_documents,
    methods=["GET"],
    name="list_authorized_case_documents",
)
router.add_api_route(
    "/documents/{document_id}/grant",
    create_document_download_grant,
    methods=["POST"],
    name="create_document_download_grant",
)
router.add_api_route(
    "/grants/{public_id}/download",
    download_document_once,
    methods=["GET"],
    name="download_document_once",
)
router.add_api_route(
    "/grants/{public_id}",
    revoke_document_grant,
    methods=["DELETE"],
    name="revoke_document_grant",
)
router.add_api_route(
    "/review/queue",
    review_queue,
    methods=["GET"],
    name="review_queue",
)
router.add_api_route(
    "/review/documents/{document_id}/decision",
    review_document,
    methods=["POST"],
    name="review_document",
)
router.add_api_route(
    "/review/ui",
    protected_document_review_ui,
    methods=["GET"],
    name="document_review_ui",
)

__all__ = ["router"]
