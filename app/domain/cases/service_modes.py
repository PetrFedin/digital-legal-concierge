from __future__ import annotations

from enum import StrEnum


class M1ServiceMode(StrEnum):
    FULL_REPRESENTATION = "FULL_REPRESENTATION"
    SELF_FILING_PACKAGE = "SELF_FILING_PACKAGE"


__all__ = ["M1ServiceMode"]
