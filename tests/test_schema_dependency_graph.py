from __future__ import annotations

import warnings

from sqlalchemy.exc import SAWarning

from app.models import Base


def test_metadata_has_no_unresolved_table_dependency_cycles():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", SAWarning)
        ordered = [table.name for table in Base.metadata.sorted_tables]

    unresolved = [
        str(warning.message)
        for warning in caught
        if issubclass(warning.category, SAWarning)
        and "unresolvable cycles" in str(warning.message).lower()
    ]
    assert unresolved == []
    assert set(ordered) == set(Base.metadata.tables)
    # The active FK is consultations.slot_id -> consultation_slots.id; the
    # reverse edge is the deferred one. Therefore slots must be created first.
    assert ordered.index("consultation_slots") < ordered.index("consultations")


def test_consultation_bidirectional_foreign_keys_remain_declared():
    consultations = Base.metadata.tables["consultations"]
    slots = Base.metadata.tables["consultation_slots"]

    consultation_targets = {
        foreign_key.target_fullname
        for foreign_key in consultations.c.slot_id.foreign_keys
    }
    slot_targets = {
        foreign_key.target_fullname
        for foreign_key in slots.c.consultation_id.foreign_keys
    }

    assert consultation_targets == {"consultation_slots.id"}
    assert slot_targets == {"consultations.id"}
    deferred = next(iter(slots.c.consultation_id.foreign_keys))
    assert deferred.use_alter is True
