from __future__ import annotations

from datetime import date


class ConsultationBookingCallbacks:
    """Central callback contract for read-only slot selection navigation."""

    PREFIX = "consult_select"

    @classmethod
    def mode(cls, value: str) -> str:
        return cls._pack("mode", value)

    @classmethod
    def lawyer(cls, reference: int) -> str:
        return cls._pack("lawyer", str(reference))

    @classmethod
    def lawyer_dates(cls, reference: int) -> str:
        return cls._pack("lawyer_dates", str(reference))

    @classmethod
    def dates(
        cls,
        *,
        page: int = 0,
        lawyer_reference: int | None = None,
    ) -> str:
        return cls._pack(
            "dates",
            str(max(0, page)),
            str(lawyer_reference or 0),
        )

    @classmethod
    def date(
        cls,
        *,
        value: date,
        lawyer_reference: int | None = None,
    ) -> str:
        return cls._pack(
            "date",
            value.strftime("%Y%m%d"),
            str(lawyer_reference or 0),
        )

    @classmethod
    def _pack(cls, *parts: str) -> str:
        value = ":".join((cls.PREFIX, *parts))
        if len(value.encode("utf-8")) > 64:
            raise ValueError("Telegram callback_data exceeds 64 bytes.")
        return value
