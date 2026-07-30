import yaml
from pathlib import Path
from typing import Dict, Any, List
import logging
from src.db.connection import DatabaseConnection

logger = logging.getLogger(__name__)

class QueryRepository:
    """Quản lý và thực thi các câu lệnh SQL từ queries.yaml."""

    def __init__(self, yaml_path: str = "config/queries.yaml"):
        self.yaml_path = Path(yaml_path)
        self.queries: Dict[str, Dict[str, Any]] = {}
        self.load_queries()

    def load_queries(self):
        if not self.yaml_path.exists():
            raise FileNotFoundError(f"Không tìm thấy file truy vấn: {self.yaml_path}")
        with open(self.yaml_path, "r", encoding="utf-8") as f:
            self.queries = yaml.safe_load(f) or {}
        logger.info(f"Đã load {len(self.queries)} câu truy vấn từ {self.yaml_path}")

    def get_query(self, query_id: str) -> str:
        if query_id not in self.queries:
            raise KeyError(f"Không tìm thấy query_id '{query_id}' trong {self.yaml_path}")
        qdata = self.queries[query_id]
        if isinstance(qdata, dict) and "query" in qdata:
            return qdata["query"]
        elif isinstance(qdata, str):
            return qdata
        else:
            raise ValueError(f"Dữ liệu query_id '{query_id}' không hợp lệ")

    def execute(self, db_conn: DatabaseConnection, db_name: str, query_id: str, params: tuple = ()) -> List[Dict[str, Any]]:
        query_sql = self.get_query(query_id)
        return db_conn.execute_query(db_name, query_sql, params)
