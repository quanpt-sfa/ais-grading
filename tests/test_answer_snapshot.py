from datetime import datetime, timezone
import json

import pytest

from src.answer.snapshot import AnswerSnapshot, AnswerSnapshotStore


def test_snapshot_id_is_content_addressed_and_stable(tmp_path):
    kwargs = {
        "answer_version": "v1",
        "source_database": "AnswerDB",
        "scope": {"start": "2024-01-01", "end": "2024-12-31"},
        "queries": {"q": "SELECT 1"},
        "config": {"strict": True},
        "validation_report": {"is_valid": True},
        "raw_data": {"q": [{"value": 1}]},
    }
    first = AnswerSnapshot.create(
        **kwargs,
        extracted_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    second = AnswerSnapshot.create(
        **kwargs,
        extracted_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )

    assert first.snapshot_id == second.snapshot_id
    assert first.data_hash == second.data_hash

    store = AnswerSnapshotStore(str(tmp_path))
    path = store.save(first)
    assert store.save(second) == path
    assert json.loads(path.read_text(encoding="utf-8"))["snapshot_id"] == (
        first.snapshot_id
    )


def test_snapshot_store_rejects_mutated_existing_file(tmp_path):
    snapshot = AnswerSnapshot.create(
        answer_version="v1",
        source_database="AnswerDB",
        scope={"start": "2024-01-01", "end": "2024-12-31"},
        queries={"q": "SELECT 1"},
        config={"strict": True},
        validation_report={"is_valid": True},
        raw_data={"q": [{"value": 1}]},
    )
    store = AnswerSnapshotStore(str(tmp_path))
    path = store.save(snapshot)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["answer_version"] = "tampered"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Immutable answer snapshot"):
        store.save(snapshot)
