from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from uuid import UUID


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _hash(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AnswerSnapshot:
    snapshot_id: str
    answer_version: str
    source_database: str
    extracted_at: str
    scope: Dict[str, Any]
    query_manifest: Dict[str, str]
    config_hash: str
    data_hash: str
    validation_report: Dict[str, Any]
    raw_data: Dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        answer_version: str,
        source_database: str,
        scope: Mapping[str, Any],
        queries: Mapping[str, str],
        config: Mapping[str, Any],
        validation_report: Mapping[str, Any],
        raw_data: Mapping[str, Any],
        extracted_at: Optional[datetime] = None,
    ) -> "AnswerSnapshot":
        query_manifest = {
            query_id: sha256(sql.encode("utf-8")).hexdigest()
            for query_id, sql in sorted(queries.items())
        }
        normalized_scope = _jsonable(dict(scope))
        normalized_raw = _jsonable(dict(raw_data))
        config_hash = _hash(dict(config))
        data_hash = _hash(normalized_raw)
        identity_payload = {
            "answer_version": answer_version,
            "source_database": source_database,
            "scope": normalized_scope,
            "query_manifest": query_manifest,
            "config_hash": config_hash,
            "data_hash": data_hash,
        }
        snapshot_id = sha256(
            _canonical_json(identity_payload).encode("utf-8")
        ).hexdigest()
        timestamp = extracted_at or datetime.now(timezone.utc)
        return cls(
            snapshot_id=snapshot_id,
            answer_version=answer_version,
            source_database=source_database,
            extracted_at=timestamp.isoformat(),
            scope=normalized_scope,
            query_manifest=query_manifest,
            config_hash=config_hash,
            data_hash=data_hash,
            validation_report=_jsonable(dict(validation_report)),
            raw_data=normalized_raw,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AnswerSnapshotStore:
    """Lưu snapshot theo content hash và tuyệt đối không ghi đè."""

    def __init__(self, directory: str = "data/answer_snapshots"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, snapshot: AnswerSnapshot) -> Path:
        path = self.directory / f"{snapshot.snapshot_id}.json"
        payload = json.dumps(
            snapshot.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            expected = snapshot.to_dict()
            # extracted_at is provenance, not part of the content-addressed ID.
            # Re-extracting identical data reuses the original immutable file.
            existing_identity = dict(existing)
            expected_identity = dict(expected)
            existing_identity.pop("extracted_at", None)
            expected_identity.pop("extracted_at", None)
            if existing_identity != expected_identity:
                raise RuntimeError(
                    f"Immutable answer snapshot differs from existing file: {path}"
                )
            return path
        with path.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
        return path
