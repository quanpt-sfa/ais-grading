import pyodbc
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)

class DatabaseConnection:
    """Quản lý kết nối SQL Server và thực thi truy vấn."""
    
    def __init__(self, host: str, auth: str = "windows", 
                 username: Optional[str] = None, password: Optional[str] = None, 
                 driver: str = "SQL Server Native Client 11.0"):
        self.host = host
        self.auth = auth
        self.username = username
        self.password = password
        self.driver = driver
        
    def _get_connection_string(self, database: str = "master") -> str:
        conn_str = f"Driver={{{self.driver}}};Server={self.host};Database={database};"
        if self.auth.lower() == "windows":
            conn_str += "Trusted_Connection=yes;"
        else:
            conn_str += f"UID={self.username};PWD={self.password};"
        return conn_str

    def connect(self, database: str = "master") -> pyodbc.Connection:
        """Tạo kết nối mới đến database cụ thể."""
        conn_str = self._get_connection_string(database)
        try:
            conn = pyodbc.connect(conn_str, timeout=15)
            return conn
        except pyodbc.Error as e:
            logger.error(f"Lỗi kết nối tới SQL Server {self.host}/{database}: {e}")
            raise

    def execute_query(self, database: str, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """Thực thi câu lệnh SELECT và trả về list dict."""
        with self.connect(database) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            if not cursor.description:
                return []
            columns = [column[0] for column in cursor.description]
            rows = cursor.fetchall()
            return [dict(zip(columns, row)) for row in rows]

    def list_databases(self) -> List[str]:
        """Liệt kê danh sách tất cả database trên SQL Server."""
        query = "SELECT name FROM sys.databases WHERE state_desc = 'ONLINE' ORDER BY name"
        rows = self.execute_query("master", query)
        return [r["name"] for r in rows]

    def database_exists(self, db_name: str) -> bool:
        """Kiểm tra database có tồn tại và online hay không."""
        query = "SELECT 1 FROM sys.databases WHERE name = ? AND state_desc = 'ONLINE'"
        rows = self.execute_query("master", query, (db_name,))
        return len(rows) > 0

    def attach_database(self, db_name: str, mdf_path: str) -> bool:
        """Attach database sinh viên từ file .mdf."""
        sql = f"CREATE DATABASE [{db_name}] ON (FILENAME = '{mdf_path}') FOR ATTACH_REBUILD_LOG;"
        try:
            with self.connect("master") as conn:
                conn.autocommit = True
                cursor = conn.cursor()
                cursor.execute(sql)
            logger.info(f"Đã attach database [{db_name}] từ {mdf_path}")
            return True
        except pyodbc.Error as e:
            logger.error(f"Lỗi attach DB [{db_name}]: {e}")
            return False

    def detach_database(self, db_name: str) -> bool:
        """Detach database sinh viên."""
        sql = f"ALTER DATABASE [{db_name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; EXEC sp_detach_db '{db_name}', 'true';"
        try:
            with self.connect("master") as conn:
                conn.autocommit = True
                cursor = conn.cursor()
                cursor.execute(sql)
            logger.info(f"Đã detach database [{db_name}]")
            return True
        except pyodbc.Error as e:
            logger.error(f"Lỗi detach DB [{db_name}]: {e}")
            return False
