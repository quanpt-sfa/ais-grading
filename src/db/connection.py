import pyodbc
from typing import Optional, List, Dict, Any, Mapping, Tuple
import logging

logger = logging.getLogger(__name__)


class DatabaseConnection:
    """Quản lý kết nối SQL Server và thực thi truy vấn."""

    ALLOWED_ISOLATION_LEVELS = {
        "READ COMMITTED",
        "REPEATABLE READ",
        "SNAPSHOT",
        "SERIALIZABLE",
    }

    def __init__(
        self,
        host: str,
        auth: str = "windows",
        username: Optional[str] = None,
        password: Optional[str] = None,
        driver: str = "SQL Server Native Client 11.0",
    ):
        self.host = host
        self.auth = auth
        self.username = username
        self.password = password
        self.driver = driver

    def _get_connection_string(self, database: str = "master") -> str:
        conn_str = (
            f"Driver={{{self.driver}}};Server={self.host};Database={database};"
        )
        if self.auth.lower() == "windows":
            conn_str += "Trusted_Connection=yes;"
        else:
            conn_str += f"UID={self.username};PWD={self.password};"
        return conn_str

    def connect(self, database: str = "master") -> pyodbc.Connection:
        conn_str = self._get_connection_string(database)
        try:
            return pyodbc.connect(conn_str, timeout=15)
        except pyodbc.Error as exc:
            logger.error(
                "Lỗi kết nối tới SQL Server %s/%s: %s",
                self.host,
                database,
                exc,
            )
            raise

    @staticmethod
    def _fetch_rows(cursor: pyodbc.Cursor) -> List[Dict[str, Any]]:
        if not cursor.description:
            return []
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def execute_query(
        self,
        database: str,
        query: str,
        params: tuple = (),
    ) -> List[Dict[str, Any]]:
        with self.connect(database) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return self._fetch_rows(cursor)

    def execute_query_batch(
        self,
        database: str,
        queries: Mapping[str, Tuple[str, tuple]],
        isolation_level: str = "SERIALIZABLE",
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Chạy toàn bộ query trong cùng connection và transaction.

        Đây là đường bắt buộc cho đáp án master. Không query nào được phép đọc
        một trạng thái database khác với các query còn lại.
        """
        level = isolation_level.strip().upper()
        if level not in self.ALLOWED_ISOLATION_LEVELS:
            raise ValueError(f"Unsupported isolation level: {isolation_level}")

        conn = self.connect(database)
        try:
            conn.autocommit = False
            cursor = conn.cursor()
            cursor.execute(f"SET TRANSACTION ISOLATION LEVEL {level}")
            cursor.execute("BEGIN TRANSACTION")
            results: Dict[str, List[Dict[str, Any]]] = {}
            for query_id, (query, params) in queries.items():
                try:
                    cursor.execute(query, params)
                    results[query_id] = self._fetch_rows(cursor)
                except Exception as exc:
                    raise RuntimeError(
                        f"Answer query failed [{query_id}]: {exc}"
                    ) from exc
            conn.commit()
            return results
        except Exception:
            try:
                conn.rollback()
            except Exception:
                logger.exception("Không rollback được transaction đáp án")
            raise
        finally:
            conn.close()

    def list_databases(self) -> List[str]:
        query = (
            "SELECT name FROM sys.databases "
            "WHERE state_desc = 'ONLINE' ORDER BY name"
        )
        rows = self.execute_query("master", query)
        return [row["name"] for row in rows]

    def database_exists(self, db_name: str) -> bool:
        query = (
            "SELECT 1 FROM sys.databases "
            "WHERE name = ? AND state_desc = 'ONLINE'"
        )
        return bool(self.execute_query("master", query, (db_name,)))

    def attach_database(self, db_name: str, mdf_path: str) -> bool:
        sql = (
            f"CREATE DATABASE [{db_name}] ON (FILENAME = '{mdf_path}') "
            "FOR ATTACH_REBUILD_LOG;"
        )
        try:
            with self.connect("master") as conn:
                conn.autocommit = True
                conn.cursor().execute(sql)
            logger.info("Đã attach database [%s] từ %s", db_name, mdf_path)
            return True
        except pyodbc.Error as exc:
            logger.error("Lỗi attach DB [%s]: %s", db_name, exc)
            return False

    def detach_database(self, db_name: str) -> bool:
        sql = (
            f"ALTER DATABASE [{db_name}] SET SINGLE_USER "
            "WITH ROLLBACK IMMEDIATE; "
            f"EXEC sp_detach_db '{db_name}', 'true';"
        )
        try:
            with self.connect("master") as conn:
                conn.autocommit = True
                conn.cursor().execute(sql)
            logger.info("Đã detach database [%s]", db_name)
            return True
        except pyodbc.Error as exc:
            logger.error("Lỗi detach DB [%s]: %s", db_name, exc)
            return False
