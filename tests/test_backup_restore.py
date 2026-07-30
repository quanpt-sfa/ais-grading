from pathlib import Path

import pytest

from src.answer.backup_restore import BackupRestoreError, SqlServerBackupRestorer


class FakeCursor:
    def __init__(self, statements):
        self.statements = statements
        self.description = None
        self._rows = []

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        normalized = " ".join(str(sql).split()).upper()

        if normalized.startswith("RESTORE HEADERONLY"):
            self._set_rows(
                [
                    "DatabaseName",
                    "Position",
                    "BackupType",
                    "BackupStartDate",
                    "BackupFinishDate",
                    "SoftwareVersionMajor",
                    "SoftwareVersionMinor",
                    "SoftwareVersionBuild",
                ],
                [("MISA_ANSWER", 1, 1, None, None, 15, 0, 4312)],
            )
        elif normalized.startswith("RESTORE FILELISTONLY"):
            self._set_rows(
                ["LogicalName", "PhysicalName", "Type", "FileId", "Size"],
                [
                    ("MISA_Data", r"D:\Old\MISA.mdf", "D", 1, 1000),
                    ("MISA_Log", r"D:\Old\MISA_log.ldf", "L", 2, 500),
                    ("MISA_Archive", r"D:\Old\MISA_2.ndf", "D", 3, 250),
                ],
            )
        elif "SELECT 1 AS FOUND FROM SYS.DATABASES" in normalized:
            self._set_rows(["found"], [])
        elif "INSTANCEDEFAULTDATAPATH" in normalized:
            self._set_rows(
                ["DataPath", "LogPath"],
                [(r"C:\SQL\Data", r"C:\SQL\Log")],
            )
        elif "SELECT STATE_DESC FROM SYS.DATABASES" in normalized:
            self._set_rows(["state_desc"], [("ONLINE",)])
        else:
            self.description = None
            self._rows = []
        return self

    def _set_rows(self, columns, rows):
        self.description = [(column,) for column in columns]
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    def __init__(self, statements):
        self.statements = statements
        self.autocommit = False
        self._cursor = FakeCursor(statements)

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeDatabaseConnection:
    def __init__(self):
        self.statements = []

    def connect(self, database="master"):
        assert database == "master"
        return FakeConnection(self.statements)


def test_bak_and_mbk_use_the_same_sql_server_restore_path(tmp_path):
    backup = tmp_path / "answer.mbk"
    backup.write_bytes(b"sql-server-backup-placeholder")
    db = FakeDatabaseConnection()

    result = SqlServerBackupRestorer(db).restore(
        backup,
        answer_name="Ca 3; DROP DATABASE master",
    )

    assert result.source_database_name == "MISA_ANSWER"
    assert result.database_name.startswith("ANSWER_Ca_3_DROP_DATABASE_master_")
    assert ";" not in result.database_name

    restore_statements = [
        sql for sql, _ in db.statements if str(sql).lstrip().upper().startswith("RESTORE DATABASE")
    ]
    assert len(restore_statements) == 1
    restore_sql = restore_statements[0]
    assert "WITH REPLACE" not in restore_sql.upper()
    assert restore_sql.upper().count("MOVE N'") == 3
    assert "MISA_Data" in restore_sql
    assert "MISA_Log" in restore_sql
    assert "MISA_Archive" in restore_sql
    assert result.data_files[0].endswith(".mdf")
    assert result.data_files[1].endswith(".ndf")
    assert result.log_files[0].endswith(".ldf")


@pytest.mark.parametrize("filename", ["answer.bak", "answer.BAK", "answer.mbk", "answer.MBK"])
def test_supported_backup_extensions(filename):
    assert SqlServerBackupRestorer.validate_extension(filename) in {".bak", ".mbk"}


def test_unsupported_backup_extension_is_rejected():
    with pytest.raises(BackupRestoreError, match="Unsupported answer backup extension"):
        SqlServerBackupRestorer.validate_extension("answer.zip")


def test_generated_database_name_is_safe_and_bounded():
    name = SqlServerBackupRestorer.database_name("Đáp án / Ca 3 " + "X" * 300, "a" * 64)
    assert len(name) <= 128
    assert name.startswith("ANSWER_")
    assert all(character.isalnum() or character == "_" for character in name)
