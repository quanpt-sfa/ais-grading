from decimal import Decimal
from typing import Any, Dict

from src.grading.base import BaseModuleComparator, CompareResult
from src.answer.models import InventoryAnswer, MasterAnswerData
from src.grading.context import GradingContext


class InventoryBalanceComparator(BaseModuleComparator):
    """Chấm số dư đầu kỳ từ cùng canonical line model với entity resolver."""

    @property
    def module_id(self) -> str:
        return "inventory_balance"

    @property
    def display_name(self) -> str:
        return "Số dư hàng tồn kho"

    def compare(
        self,
        master_answer: MasterAnswerData,
        student_data: Dict[str, Any],
        tolerance: float = 1.0,
    ) -> CompareResult:
        answer = master_answer.inventory or InventoryAnswer()
        context = student_data.get("_grading_context")

        if isinstance(context, GradingContext):
            rows = context.student_graph.opening_balances
            student_count = len(
                {row.entity_id for row in rows if row.entity_id}
            )
            student_quantity = sum(
                (row.quantity for row in rows), Decimal("0")
            )
            student_amount = sum(
                (row.amount for row in rows), Decimal("0")
            )
        else:
            raw_rows = student_data.get("inventory_balance", [])
            first = raw_rows[0] if raw_rows else {}
            student_count = int(first.get("item_count", 0) or 0)
            student_quantity = Decimal(
                str(first.get("total_qty", 0) or 0)
            )
            student_amount = Decimal(
                str(first.get("total_amount", 0) or 0)
            )

        tol = Decimal(str(tolerance))
        checks = {
            "distinct_item_count": student_count == answer.item_count,
            "total_quantity": abs(student_quantity - answer.total_qty) <= tol,
            "total_amount": abs(student_amount - answer.total_amount) <= tol,
        }
        details = [
            {
                "field": "Số mặt hàng phân biệt",
                "answer": answer.item_count,
                "student": student_count,
                "match": checks["distinct_item_count"],
            },
            {
                "field": "Tổng số lượng tồn kho",
                "answer": float(answer.total_qty),
                "student": float(student_quantity),
                "match": checks["total_quantity"],
            },
            {
                "field": "Tổng giá trị tồn kho",
                "answer": float(answer.total_amount),
                "student": float(student_amount),
                "match": checks["total_amount"],
            },
        ]
        matched = sum(1 for value in checks.values() if value)
        return CompareResult(
            module_id=self.module_id,
            display_name=self.display_name,
            total_items=len(checks),
            matched_items=matched,
            match_ratio=matched / len(checks),
            details=details,
            raw_answer=answer,
            raw_student={
                "item_count": student_count,
                "total_qty": float(student_quantity),
                "total_amount": float(student_amount),
            },
        )
