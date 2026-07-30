from typing import Dict, Any, Optional
import logging

from src.db.connection import DatabaseConnection
from src.db.queries import QueryRepository
from src.student.manager import StudentInfo
from src.grading.accounting_graph import AccountingGraphBuilder

logger = logging.getLogger(__name__)


class StudentDataExtractor:
    """Trích xuất dữ liệu bài làm của từng sinh viên.

    Các query cấp dòng và báo cáo phải dùng cùng canonical query manifest với
    đáp án. Query tổng hợp/audit cũ vẫn đọc từ ``config/queries.yaml``.
    """

    def __init__(
        self,
        db_conn: DatabaseConnection,
        query_repo: QueryRepository,
        canonical_query_repo: Optional[QueryRepository] = None,
    ):
        self.db_conn = db_conn
        self.query_repo = query_repo
        self.canonical_query_repo = canonical_query_repo
        if self.canonical_query_repo is None:
            try:
                self.canonical_query_repo = QueryRepository(
                    "config/answer_queries.yaml"
                )
            except Exception as exc:
                logger.warning(
                    "Không load được canonical query manifest cho sinh viên: %s",
                    exc,
                )
        self.graph_builder = AccountingGraphBuilder()

    def _repository_for(self, query_id: str) -> QueryRepository:
        canonical_ids = {
            *AccountingGraphBuilder.QUERY_IDS,
            "financial_reports",
        }
        if (
            query_id in canonical_ids
            and self.canonical_query_repo is not None
            and query_id in self.canonical_query_repo.queries
        ):
            return self.canonical_query_repo
        return self.query_repo

    def extract_all_data(self, student: StudentInfo) -> Dict[str, Any]:
        """Trích xuất cả dữ liệu tương thích cũ và dữ liệu cấp dòng."""
        db_name = student.db_name
        data: Dict[str, Any] = {}

        try:
            user_info = self.query_repo.execute(
                self.db_conn, db_name, "student_info"
            )
            if user_info:
                user = user_info[0]
                if not student.first_name and not student.last_name:
                    student.first_name = user.get("FirstName", "")
                    student.last_name = user.get("LastName", "")
        except Exception as exc:
            logger.debug(
                "Không lấy được student_info từ DB [%s]: %s",
                db_name,
                exc,
            )

        try:
            comp_info = self.query_repo.execute(
                self.db_conn, db_name, "computer_name"
            )
            if comp_info:
                student.computer_name = ", ".join(
                    row["ComputerName"]
                    for row in comp_info
                    if row.get("ComputerName")
                )
        except Exception as exc:
            logger.debug(
                "Không lấy được computer_name từ DB [%s]: %s",
                db_name,
                exc,
            )

        query_ids = [
            "inventory_balance",
            "fixed_asset_balance",
            "general_balance",
            "transactions",
            "financial_reports",
            *AccountingGraphBuilder.QUERY_IDS,
            "audit_timeline",
            "audit_workflow_steps",
            "audit_computer_forensics",
            "audit_process_compliance",
        ]

        for query_id in dict.fromkeys(query_ids):
            try:
                repository = self._repository_for(query_id)
                data[query_id] = repository.execute(
                    self.db_conn, db_name, query_id
                )
            except Exception as exc:
                logger.warning(
                    "Lỗi chạy query [%s] trên DB [%s]: %s",
                    query_id,
                    db_name,
                    exc,
                )
                data[query_id] = []

        return data
