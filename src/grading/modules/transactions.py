from decimal import Decimal
from typing import Any, Dict, Tuple
from src.grading.base import BaseModuleComparator, CompareResult
from src.answer.models import MasterAnswerData, TransactionAnswer, TransactionItem

class TransactionsComparator(BaseModuleComparator):
    """Phân hệ 4: Chấm nghiệp vụ kinh tế phát sinh trong kỳ (GeneralLedger WHERE RefNo <> 'OPN')."""

    @property
    def module_id(self) -> str:
        return "transactions"

    @property
    def display_name(self) -> str:
        return "Nghiệp vụ phát sinh trong kỳ"

    def compare(self, master_answer: MasterAnswerData, student_data: Dict[str, Any], tolerance: float = 1.0) -> CompareResult:
        ans = master_answer.transactions or TransactionAnswer()
        st_rows = student_data.get("transactions", [])

        # Index student rows by (task_id, account_code)
        st_entries: Dict[Tuple[int, str], TransactionItem] = {}
        for r in st_rows:
            try:
                task_id = int(r["TaskID"])
                code = str(r["AccountCode"]).strip()
                st_entries[(task_id, code)] = TransactionItem(
                    task_id=task_id,
                    account_code=code,
                    amount=Decimal(str(r.get("Amount", 0) or 0)),
                    amount_oc=Decimal(str(r.get("AmountOC", 0) or 0)),
                    quantity=Decimal(str(r.get("Quantity", 0) or 0))
                )
            except (ValueError, KeyError, TypeError):
                continue

        details = []
        total_items = len(ans.entries) if ans.entries else 1
        matched_items = 0

        if not ans.entries:
            no_st = (len(st_entries) == 0)
            return CompareResult(
                module_id=self.module_id,
                display_name=self.display_name,
                total_items=1,
                matched_items=1 if no_st else 0,
                match_ratio=1.0 if no_st else 0.0,
                details=[{"field": "Không có nghiệp vụ phát sinh", "match": no_st}]
            )

        tol = Decimal(str(tolerance))

        for key, ans_tx in ans.entries.items():
            task_id, code = key
            st_tx = st_entries.get(key)
            if not st_tx:
                details.append({
                    "task_id": task_id,
                    "account_code": code,
                    "match": False,
                    "reason": f"Thiếu nghiệp vụ Ngày/Tháng [{task_id}] TK [{code}]"
                })
                continue

            amt_ok = abs(st_tx.amount - ans_tx.amount) <= tol
            amt_oc_ok = abs(st_tx.amount_oc - ans_tx.amount_oc) <= tol
            qty_ok = abs(st_tx.quantity - ans_tx.quantity) <= tol

            tx_ok = amt_ok and amt_oc_ok and qty_ok
            if tx_ok:
                matched_items += 1

            details.append({
                "task_id": task_id,
                "account_code": code,
                "amount": {"ans": float(ans_tx.amount), "st": float(st_tx.amount), "ok": amt_ok},
                "amount_oc": {"ans": float(ans_tx.amount_oc), "st": float(st_tx.amount_oc), "ok": amt_oc_ok},
                "quantity": {"ans": float(ans_tx.quantity), "st": float(st_tx.quantity), "ok": qty_ok},
                "match": tx_ok
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
