from decimal import Decimal
from typing import Any, Dict, Tuple
from src.grading.base import BaseModuleComparator, CompareResult
from src.answer.models import MasterAnswerData, FinancialReportAnswer

class FinancialReportsComparator(BaseModuleComparator):
    """Phân hệ 5: Chấm Báo cáo tài chính (FRReportDetail ReportType IN (1, 2) FormulaType = 0)."""

    @property
    def module_id(self) -> str:
        return "financial_reports"

    @property
    def display_name(self) -> str:
        return "Báo cáo tài chính (CĐKT & KQKD)"

    def compare(self, master_answer: MasterAnswerData, student_data: Dict[str, Any], tolerance: float = 1.0) -> CompareResult:
        ans = master_answer.financial_reports or FinancialReportAnswer()
        st_rows = student_data.get("financial_reports", [])

        # Index student entries by (ReportType, ItemCode)
        st_items: Dict[Tuple[str, str], Decimal] = {}
        for r in st_rows:
            rtype = str(r["ReportType"]).strip()
            icode = str(r["ItemCode"]).strip()
            st_items[(rtype, icode)] = Decimal(str(r.get("Amount", 0) or 0))

        details = []
        total_items = len(ans.items) if ans.items else 1
        matched_items = 0

        if not ans.items:
            no_st = (len(st_items) == 0)
            return CompareResult(
                module_id=self.module_id,
                display_name=self.display_name,
                total_items=1,
                matched_items=1 if no_st else 0,
                match_ratio=1.0 if no_st else 0.0,
                details=[{"field": "Không có dữ liệu báo cáo tài chính", "match": no_st}]
            )

        tol = Decimal(str(tolerance))

        for key, ans_amt in ans.items.items():
            rtype, icode = key
            st_amt = st_items.get(key)
            if st_amt is None:
                details.append({
                    "report_type": rtype,
                    "item_code": icode,
                    "match": False,
                    "reason": f"Thiếu chỉ tiêu [{icode}] trên báo cáo [{rtype}]"
                })
                continue

            amt_ok = abs(st_amt - ans_amt) <= tol
            if amt_ok:
                matched_items += 1

            details.append({
                "report_type": rtype,
                "item_code": icode,
                "answer_amount": float(ans_amt),
                "student_amount": float(st_amt),
                "match": amt_ok
            })

        ratio = matched_items / total_items

        return CompareResult(
            module_id=self.module_id,
            display_name=self.display_name,
            total_items=total_items,
            matched_items=matched_items,
            match_ratio=ratio,
            details=details,
            raw_answer=ans,
            raw_student=st_rows
        )
