from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source() -> str:
    return (ROOT / "app/bot/screens/consultations.py").read_text(encoding="utf-8")


def test_slot_selection_only_previews_reschedule_and_does_not_mutate_booking():
    source = _source()
    preview = source[
        source.index("async def choose_reschedule_slot") : source.index(
            "async def confirm_reschedule_slot"
        )
    ]

    assert "Подтвердить перенос консультации?" in preview
    assert "До подтверждения текущая запись остаётся без изменений" in preview
    assert "consult_reschedule_confirm:" in preview
    assert ".reschedule_booked(" not in preview


def test_reschedule_confirmation_uses_current_booking_snapshot_before_mutation():
    source = _source()
    confirm = source[
        source.index("async def confirm_reschedule_slot") : source.index(
            "async def consult_cancel"
        )
    ]

    snapshot_check = confirm.index("consultation.id != expected_consultation_id")
    old_slot_check = confirm.index("int(consultation.slot_id or 0) != expected_old_slot_id")
    mutation = confirm.index(".reschedule_booked(")
    commit = confirm.index("await db.commit()", mutation)

    assert snapshot_check < mutation
    assert old_slot_check < mutation
    assert mutation < commit
    assert "Старая кнопка подтверждения больше не действует" in confirm
    assert "Повторный перенос не выполнялся" in confirm


def test_reschedule_callback_carries_consultation_old_slot_and_new_slot_snapshot():
    source = _source()
    preview = source[
        source.index("async def choose_reschedule_slot") : source.index(
            "async def confirm_reschedule_slot"
        )
    ]

    assert "consultation.id" in preview
    assert "old_slot_id = int(consultation.slot_id or 0)" in preview
    assert "new_slot.id" in preview
    assert (
        'f"consult_reschedule_confirm:{consultation.id}:{old_slot_id}:{new_slot.id}"'
        in preview
    )


def test_cancel_still_requires_explicit_confirmation_before_service_call():
    source = _source()
    ask = source[source.index("async def consult_cancel") : source.index("async def consult_cancel_confirm")]
    confirm = source[source.index("async def consult_cancel_confirm") :]

    assert '("Да, отменить текущую запись", "consult_cancel_confirm")' in ask
    assert "cancel_and_prepare_rebooking" not in ask
    assert "cancel_and_prepare_rebooking" in confirm
    assert "await db.commit()" in confirm
