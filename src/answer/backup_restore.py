from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.db.connection import DatabaseConnection


class BackupRestoreError(RuntimeError):
    """Raised when a SQL Server backup cannot be inspected or restored safely."""


@dataclass(frozen=True)
class BackupSetInfo:
    position: int
    database_name: str
    backup_type: int
    backup_start_date: Any = None
    backup_finish_date: Any = None
    server_version: Optional[str] = None


@dataclass(frozen=True)
class BackupFileInfo:
    logical_name: str
    physical_name: str
    file_type: str
    file_id: Optional[int] = None
    size: Optional[int] = None


@dataclass(frozen=True)
class BackupInspection:
    backup_path: str
    file_sha256: str
    file_size: int
    selected_set: BackupSetInfo
    files: Tuple[BackupFileInfo, ...]


@dataclass(frozen=True)
class RestoreResult:
    database_name: str
    source_database_name: str
    backup_path: str
    file_sha256: str
    file_size: int
    backup_set_position: int
    data_files: Tuple[str, ...]
    log_files: Tuple[str, ...]
    restored_at: str


@dataclass
class AnswerSourceRecord:
    source_id: str
    original_filename: str
    stored_backup_path: str
    extension: str
    file_sha256: str
    file_size: int
    answer_name: str
    restored_database_name: str
    source_database_name: str
    backup_set_position: int
    status: str
    uploaded_at: str
    restored_at: Optional[str] = None
    answer_snapshot_id: Optional[str] = None
    answer_data_hash: Optional[str] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AnswerSourceStore:
    """Small append/update store for backup-to-snapshot audit metadata."""

    def __init__(self, directory: str | Path = "data/answer_sources"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, record: AnswerSourceRecord) -> Path:
        path = self.directory / f"{record.source_id}.json"
        payload = json.dumps(
            record.to_dict(),
            ensure_ascii=False,
            indent=2,
            default=self._json_default,
            sort_keys=True,
        )
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
        return path

    @staticmethod
    def _json_default(value: Any) -> str:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        return str(value)


class SqlServerBackupRestorer:
    """Inspect and restore SQL Server full backups used as answer databases.

    Both ``.bak`` and MISA ``.mbk`` files follow the same SQL Server restore
    path. The extension is only an upload filter; SQL Server remains the source
    of truth for whether the file is a valid database backup.
    """

    SUPPORTED_EXTENSIONS = {".bak", ".mbk"}
    MAX_DATABASE_NAME_LENGTH = 128
    SAFE_NAME = re.compile(r"[^A-Za-z0-9_]+")

    def __init__(
        self,
        db_connection: DatabaseConnection,
        *,
        data_directory: Optional[str] = None,
        log_directory: Optional[str] = None,
    ):
        self.db = db_connection
        self.data_directory = data_directory
        self.log_directory = log_directory

    @classmethod
    def validate_extension(cls, filename: str) -> str:
        extension = Path(filename).suffix.lower()
        if extension not in cls.SUPPORTED_EXTENSIONS:
            allowed = ", ".join(sorted(cls.SUPPORTED_EXTENSIONS))
            raise BackupRestoreError(
                f"Unsupported answer backup extension {extension!r}; allowed: {allowed}"
            )
        return extension

    @staticmethod
    def sha256(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as stream:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def inspect(
        self,
        backup_path: str | Path,
        *,
        backup_set_position: Optional[int] = None,
    ) -> BackupInspection:
        path = self._validated_path(backup_path)
        file_hash = self.sha256(path)
        file_size = path.stat().st_size
        disk_literal = self._sql_literal(str(path))

        with self.db.connect("master") as connection:
            connection.autocommit = True
            cursor = connection.cursor()
            cursor.execute(f"RESTORE HEADERONLY FROM DISK = {disk_literal};")
            headers = self._fetch_rows(cursor)
            selected = self._select_backup_set(headers, backup_set_position)

            cursor.execute(
                "RESTORE FILELISTONLY FROM DISK = "
                f"{disk_literal} WITH FILE = {selected.position};"
            )
            file_rows = self._fetch_rows(cursor)
            files = tuple(self._parse_backup_files(file_rows))
            if not files:
                raise BackupRestoreError("RESTORE FILELISTONLY returned no files")

            cursor.execute(
                "RESTORE VERIFYONLY FROM DISK = "
                f"{disk_literal} WITH FILE = {selected.position};"
            )

        return BackupInspection(
            backup_path=str(path),
            file_sha256=file_hash,
            file_size=file_size,
            selected_set=selected,
            files=files,
        )

    def restore(
        self,
        backup_path: str | Path,
        *,
        answer_name: str,
        backup_set_position: Optional[int] = None,
    ) -> RestoreResult:
        inspection = self.inspect(
            backup_path,
            backup_set_position=backup_set_position,
        )

        with self.db.connect("master") as connection:
            connection.autocommit = True
            cursor = connection.cursor()
            database_name = self._available_database_name(
                cursor,
                answer_name=answer_name,
                file_hash=inspection.file_sha256,
            )
            data_directory, log_directory = self._resolve_server_directories(cursor)
            moves, data_files, log_files = self._build_moves(
                inspection.files,
                database_name=database_name,
                data_directory=data_directory,
                log_directory=log_directory,
            )
            restore_sql = self._build_restore_sql(
                database_name=database_name,
                backup_path=inspection.backup_path,
                backup_set_position=inspection.selected_set.position,
                moves=moves,
            )

            try:
                cursor.execute(restore_sql)
                cursor.execute(
                    "SELECT state_desc FROM sys.databases WHERE name = ?;",
                    (database_name,),
                )
                rows = self._fetch_rows(cursor)
                if not rows or str(rows[0].get("state_desc", "")).upper() != "ONLINE":
                    raise BackupRestoreError(
                        f"Restored database [{database_name}] is not ONLINE"
                    )
            except Exception as exc:
                self._drop_database_with_cursor(cursor, database_name)
                if isinstance(exc, BackupRestoreError):
                    raise
                raise BackupRestoreError(
                    f"Could not restore answer database [{database_name}]: {exc}"
                ) from exc

        return RestoreResult(
            database_name=database_name,
            source_database_name=inspection.selected_set.database_name,
            backup_path=inspection.backup_path,
            file_sha256=inspection.file_sha256,
            file_size=inspection.file_size,
            backup_set_position=inspection.selected_set.position,
            data_files=tuple(data_files),
            log_files=tuple(log_files),
            restored_at=datetime.now().astimezone().isoformat(),
        )

    def set_read_only(self, database_name: str) -> None:
        quoted = self._quote_identifier(database_name)
        with self.db.connect("master") as connection:
            connection.autocommit = True
            connection.cursor().execute(
                f"ALTER DATABASE {quoted} SET READ_ONLY WITH ROLLBACK IMMEDIATE;"
            )

    def drop_database(self, database_name: str) -> None:
        with self.db.connect("master") as connection:
            connection.autocommit = True
            self._drop_database_with_cursor(connection.cursor(), database_name)

    @classmethod
    def database_name(cls, answer_name: str, file_hash: str) -> str:
        base = cls.SAFE_NAME.sub("_", answer_name.strip()).strip("_")
        if not base:
            base = "ANSWER"
        prefix = "ANSWER_"
        suffix = f"_{file_hash[:10].upper()}"
        available = cls.MAX_DATABASE_NAME_LENGTH - len(prefix) - len(suffix)
        return f"{prefix}{base[:available]}{suffix}"

    @classmethod
    def _build_restore_sql(
        cls,
        *,
        database_name: str,
        backup_path: str,
        backup_set_position: int,
        moves: Sequence[Tuple[str, str]],
    ) -> str:
        if not moves:
            raise BackupRestoreError("A restore requires at least one MOVE target")
        move_sql = ",\n    ".join(
            f"MOVE {cls._sql_literal(logical)} TO {cls._sql_literal(target)}"
            for logical, target in moves
        )
        return (
            f"RESTORE DATABASE {cls._quote_identifier(database_name)}\n"
            f"FROM DISK = {cls._sql_literal(backup_path)}\n"
            "WITH\n"
            f"    FILE = {int(backup_set_position)},\n"
            f"    {move_sql},\n"
            "    RECOVERY,\n"
            "    STATS = 5;"
        )

    def _available_database_name(
        self,
        cursor: Any,
        *,
        answer_name: str,
        file_hash: str,
    ) -> str:
        root = self.database_name(answer_name, file_hash)
        candidate = root
        counter = 1
        while self._database_exists(cursor, candidate):
            counter += 1
            suffix = f"_{counter}"
            candidate = f"{root[: self.MAX_DATABASE_NAME_LENGTH - len(suffix)]}{suffix}"
        return candidate

    @staticmethod
    def _database_exists(cursor: Any, database_name: str) -> bool:
        cursor.execute("SELECT 1 AS found FROM sys.databases WHERE name = ?;", (database_name,))
        return bool(SqlServerBackupRestorer._fetch_rows(cursor))

    def _resolve_server_directories(self, cursor: Any) -> Tuple[str, str]:
        data_directory = self.data_directory
        log_directory = self.log_directory
        if data_directory and log_directory:
            return data_directory, log_directory

        cursor.execute(
            "SELECT "
            "CAST(SERVERPROPERTY('InstanceDefaultDataPath') AS nvarchar(4000)) AS DataPath, "
            "CAST(SERVERPROPERTY('InstanceDefaultLogPath') AS nvarchar(4000)) AS LogPath;"
        )
        rows = self._fetch_rows(cursor)
        if rows:
            data_directory = data_directory or rows[0].get("DataPath")
            log_directory = log_directory or rows[0].get("LogPath")

        if not data_directory or not log_directory:
            cursor.execute(
                "SELECT physical_name, type_desc "
                "FROM master.sys.database_files WHERE file_id IN (1, 2);"
            )
            for row in self._fetch_rows(cursor):
                parent = ntpath.dirname(str(row.get("physical_name") or ""))
                if str(row.get("type_desc", "")).upper() == "ROWS":
                    data_directory = data_directory or parent
                elif str(row.get("type_desc", "")).upper() == "LOG":
                    log_directory = log_directory or parent

        if not data_directory or not log_directory:
            raise BackupRestoreError(
                "SQL Server data/log directories could not be determined; "
                "configure answer_restore.data_directory and log_directory"
            )
        return str(data_directory), str(log_directory)

    @classmethod
    def _build_moves(
        cls,
        files: Sequence[BackupFileInfo],
        *,
        database_name: str,
        data_directory: str,
        log_directory: str,
    ) -> Tuple[List[Tuple[str, str]], List[str], List[str]]:
        moves: List[Tuple[str, str]] = []
        data_files: List[str] = []
        log_files: List[str] = []
        data_index = 0
        log_index = 0

        for backup_file in files:
            file_type = backup_file.file_type.upper()
            if file_type == "L":
                log_index += 1
                filename = (
                    f"{database_name}_log.ldf"
                    if log_index == 1
                    else f"{database_name}_log{log_index}.ldf"
                )
                target = cls._join_server_path(log_directory, filename)
                log_files.append(target)
            else:
                data_index += 1
                extension = ".mdf" if data_index == 1 else ".ndf"
                filename = (
                    f"{database_name}{extension}"
                    if data_index == 1
                    else f"{database_name}_{data_index}{extension}"
                )
                target = cls._join_server_path(data_directory, filename)
                data_files.append(target)
            moves.append((backup_file.logical_name, target))

        if not data_files:
            raise BackupRestoreError("Backup contains no SQL Server data file")
        return moves, data_files, log_files

    @staticmethod
    def _join_server_path(directory: str, filename: str) -> str:
        if "\\" in directory or ":" in directory:
            return ntpath.join(directory, filename)
        return os.path.join(directory, filename)

    @classmethod
    def _select_backup_set(
        cls,
        headers: Sequence[Mapping[str, Any]],
        requested_position: Optional[int],
    ) -> BackupSetInfo:
        full_backups = []
        for row in headers:
            try:
                backup_type = int(row.get("BackupType") or 0)
                position = int(row.get("Position") or 0)
            except (TypeError, ValueError):
                continue
            if backup_type != 1 or position <= 0:
                continue
            full_backups.append((position, row))

        if not full_backups:
            raise BackupRestoreError("Backup file contains no full database backup set")

        if requested_position is None:
            position, row = max(full_backups, key=lambda item: item[0])
        else:
            matches = [item for item in full_backups if item[0] == requested_position]
            if not matches:
                raise BackupRestoreError(
                    f"Full backup set position {requested_position} was not found"
                )
            position, row = matches[0]

        version_parts = [
            row.get("SoftwareVersionMajor"),
            row.get("SoftwareVersionMinor"),
            row.get("SoftwareVersionBuild"),
        ]
        version = ".".join(str(part) for part in version_parts if part is not None)
        return BackupSetInfo(
            position=position,
            database_name=str(row.get("DatabaseName") or ""),
            backup_type=int(row.get("BackupType") or 0),
            backup_start_date=row.get("BackupStartDate"),
            backup_finish_date=row.get("BackupFinishDate"),
            server_version=version or None,
        )

    @staticmethod
    def _parse_backup_files(
        rows: Iterable[Mapping[str, Any]],
    ) -> Iterable[BackupFileInfo]:
        for row in rows:
            logical_name = str(row.get("LogicalName") or "").strip()
            if not logical_name:
                continue
            file_type = str(row.get("Type") or "D").upper()
            yield BackupFileInfo(
                logical_name=logical_name,
                physical_name=str(row.get("PhysicalName") or ""),
                file_type=file_type,
                file_id=SqlServerBackupRestorer._int_or_none(row.get("FileId")),
                size=SqlServerBackupRestorer._int_or_none(row.get("Size")),
            )

    @staticmethod
    def _fetch_rows(cursor: Any) -> List[Dict[str, Any]]:
        if not cursor.description:
            return []
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    @classmethod
    def _validated_path(cls, value: str | Path) -> Path:
        path = Path(value).expanduser().resolve()
        cls.validate_extension(path.name)
        if not path.is_file():
            raise BackupRestoreError(f"Answer backup does not exist: {path}")
        if path.stat().st_size <= 0:
            raise BackupRestoreError("Answer backup file is empty")
        if "\x00" in str(path) or "\n" in str(path) or "\r" in str(path):
            raise BackupRestoreError("Answer backup path contains invalid characters")
        return path

    @staticmethod
    def _quote_identifier(value: str) -> str:
        return f"[{value.replace(']', ']]')}]"

    @staticmethod
    def _sql_literal(value: str) -> str:
        return "N'" + value.replace("'", "''") + "'"

    @staticmethod
    def _int_or_none(value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _drop_database_with_cursor(cls, cursor: Any, database_name: str) -> None:
        try:
            quoted = cls._quote_identifier(database_name)
            cursor.execute(
                "IF DB_ID(?) IS NOT NULL BEGIN "
                f"ALTER DATABASE {quoted} SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
                f"DROP DATABASE {quoted}; END;",
                (database_name,),
            )
        except Exception:
            # Preserve the original restore/validation failure.
            pass
