from typing import Dict, Any, List, Optional
import logging
from src.db.connection import DatabaseConnection
from src.db.queries import QueryRepository

logger = logging.getLogger(__name__)

class TraceabilityChecker:
    """Kiểm tra tính liên tục và theo vết (traceability) của các chuỗi nghiệp vụ."""

    def __init__(self, db_conn: DatabaseConnection, query_repo: QueryRepository):
        self.db_conn = db_conn
        self.query_repo = query_repo

    def trace_student_vouchers(self, db_name: str) -> Dict[str, Any]:
        """Trích xuất và theo vết các chuỗi chứng từ mua hàng & bán hàng."""
        trace_data = {}

        try:
            pu_orders = self.query_repo.execute(self.db_conn, db_name, "trace_purchase_orders")
            pu_vouchers = self.query_repo.execute(self.db_conn, db_name, "trace_purchase_vouchers")
            sa_orders = self.query_repo.execute(self.db_conn, db_name, "trace_sales_orders")
            sa_vouchers = self.query_repo.execute(self.db_conn, db_name, "trace_sales_vouchers")
            links = self.query_repo.execute(self.db_conn, db_name, "trace_voucher_links")

            trace_data["purchase_chain"] = {
                "orders": len(pu_orders),
                "vouchers": len(pu_vouchers),
                "linked_count": len([l for l in links if l.get("RefType1") in [302, 3520] or l.get("RefType2") in [302, 3520]])
            }

            trace_data["sales_chain"] = {
                "orders": len(sa_orders),
                "vouchers": len(sa_vouchers),
                "linked_count": len([l for l in links if l.get("RefType1") in [3520, 3530] or l.get("RefType2") in [3520, 3530]])
            }

            trace_data["total_links"] = len(links)

        except Exception as e:
            logger.warning(f"Lỗi theo vết chứng từ trên DB [{db_name}]: {e}")
            trace_data["error"] = str(e)

        return trace_data
