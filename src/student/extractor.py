from typing import Dict, Any, List
import logging
from src.db.connection import DatabaseConnection
from src.db.queries import QueryRepository
from src.student.manager import StudentInfo

logger = logging.getLogger(__name__)

class StudentDataExtractor:
    """Trích xuất dữ liệu bài làm của từng sinh viên."""

    def __init__(self, db_conn: DatabaseConnection, query_repo: QueryRepository):
        self.db_conn = db_conn
        self.query_repo = query_repo

    def extract_all_data(self, student: StudentInfo) -> Dict[str, Any]:
        """Trích xuất toàn bộ dữ liệu từ database của 1 sinh viên."""
        db_name = student.db_name
        data = {}

        # 1. Thông tin sinh viên & số máy làm bài
        try:
            user_info = self.query_repo.execute(self.db_conn, db_name, "student_info")
            if user_info:
                u = user_info[0]
                if not student.first_name and not student.last_name:
                    student.first_name = u.get("FirstName", "")
                    student.last_name = u.get("LastName", "")
        except Exception as e:
            logger.debug(f"Không lấy được student_info từ DB [{db_name}]: {e}")

        try:
            comp_info = self.query_repo.execute(self.db_conn, db_name, "computer_name")
            if comp_info:
                student.computer_name = ", ".join([c["ComputerName"] for c in comp_info if c.get("ComputerName")])
        except Exception as e:
            logger.debug(f"Không lấy được computer_name từ DB [{db_name}]: {e}")

        # 2. Extract data cho các phân hệ
        query_ids = [
            "inventory_balance", "fixed_asset_balance", "general_balance",
            "transactions", "financial_reports",
            "audit_timeline", "audit_workflow_steps", "audit_computer_forensics", "audit_process_compliance"
        ]

        for qid in query_ids:
            try:
                data[qid] = self.query_repo.execute(self.db_conn, db_name, qid)
            except Exception as e:
                logger.warning(f"Lỗi chạy query [{qid}] trên DB [{db_name}]: {e}")
                data[qid] = []

        return data
