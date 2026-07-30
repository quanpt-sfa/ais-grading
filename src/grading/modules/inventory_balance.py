from decimal import Decimal
from typing import Any, Dict
from src.grading.base import BaseModuleComparator, CompareResult
from src.answer.models import MasterAnswerData, InventoryAnswer

class InventoryBalanceComparator(BaseModuleComparator):
    """Phân hệ 1: Chấm số dư hàng tồn kho mở đầu (InventoryLedger WHERE RefNo='opn')."""

    @property
    def module_id(self) -> str:
        return "inventory_balance"

    @property
    def display_name(self) -> str:
        return "Số dư hàng tồn kho"

    def compare(self, master_answer: MasterAnswerData, student_data: Dict[str, Any], tolerance: float = 1.0) -> CompareResult:
        ans = master_answer.inventory or InventoryAnswer()
        
        # Student raw data for inventory_balance
        st_rows = student_data.get("inventory_balance", [])
        st_count = st_rows[0].get("item_count", 0) if st_rows else 0
        st_qty = Decimal(str(st_rows[0].get("total_qty", 0) or 0)) if st_rows else Decimal('0')
        st_amt = Decimal(str(st_rows[0].get("total_amount", 0) or 0)) if st_rows else Decimal('0')

        details = []
        matched = 0
        total_checks = 3

        # Check 1: Count
        count_ok = (st_count == ans.item_count)
        if count_ok: matched += 1
        details.append({
            "field": "Số lượng mặt hàng",
            "answer": ans.item_count,
            "student": st_count,
            "match": count_ok
        })

        # Check 2: Total Qty
        qty_ok = abs(st_qty - ans.total_qty) <= Decimal(str(tolerance))
        if qty_ok: matched += 1
        details.append({
            "field": "Tổng số lượng tồn kho",
            "answer": float(ans.total_qty),
            "student": float(st_qty),
            "match": qty_ok
        })

        # Check 3: Total Amount
        amt_ok = abs(st_amt - ans.total_amount) <= Decimal(str(tolerance))
        if amt_ok: matched += 1
        details.append({
            "field": "Tổng giá trị tồn kho (VND)",
            "answer": float(ans.total_amount),
            "student": float(st_amt),
            "match": amt_ok
        })

        ratio = matched / total_checks

        return CompareResult(
            module_id=self.module_id,
            display_name=self.display_name,
            total_items=total_checks,
            matched_items=matched,
            match_ratio=ratio,
            details=details,
            raw_answer=ans,
            raw_student={"item_count": st_count, "total_qty": float(st_qty), "total_amount": float(st_amt)}
        )
