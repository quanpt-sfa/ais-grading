from decimal import Decimal
from typing import Any, Dict
from src.grading.base import BaseModuleComparator, CompareResult
from src.answer.models import MasterAnswerData, GeneralBalanceAnswer, AccountBalanceItem

class GeneralBalanceComparator(BaseModuleComparator):
    """Phân hệ 3: Chấm số dư tổng hợp sổ cái (GeneralLedger CorrespondingAccountNumber IS NULL)."""

    @property
    def module_id(self) -> str:
        return "general_balance"

    @property
    def display_name(self) -> str:
        return "Số dư tổng hợp sổ cái"

    def compare(self, master_answer: MasterAnswerData, student_data: Dict[str, Any], tolerance: float = 1.0) -> CompareResult:
        ans = master_answer.general_balance or GeneralBalanceAnswer()
        st_rows = student_data.get("general_balance", [])

        # Index student rows by 3-digit AccountCode
        st_accounts: Dict[str, AccountBalanceItem] = {}
        for r in st_rows:
            code = str(r["AccountCode"]).strip()
            st_accounts[code] = AccountBalanceItem(
                account_code=code,
                debit_amount=Decimal(str(r.get("DebitAmount", 0) or 0)),
                debit_amount_oc=Decimal(str(r.get("DebitAmountOC", 0) or 0)),
                credit_amount=Decimal(str(r.get("CreditAmount", 0) or 0)),
                credit_amount_oc=Decimal(str(r.get("CreditAmountOC", 0) or 0)),
                quantity=Decimal(str(r.get("Quantity", 0) or 0))
            )

        details = []
        total_items = len(ans.accounts) if ans.accounts else 1
        matched_items = 0

        if not ans.accounts:
            no_st = (len(st_accounts) == 0)
            return CompareResult(
                module_id=self.module_id,
                display_name=self.display_name,
                total_items=1,
                matched_items=1 if no_st else 0,
                match_ratio=1.0 if no_st else 0.0,
                details=[{"field": "Không có số dư TK mở đầu", "match": no_st}]
            )

        tol = Decimal(str(tolerance))

        for code, ans_acc in ans.accounts.items():
            st_acc = st_accounts.get(code)
            if not st_acc:
                details.append({
                    "account_code": code,
                    "match": False,
                    "reason": f"Thiếu TK {code} trong số dư mở đầu"
                })
                continue

            debit_ok = abs(st_acc.debit_amount - ans_acc.debit_amount) <= tol
            credit_ok = abs(st_acc.credit_amount - ans_acc.credit_amount) <= tol
            debit_oc_ok = abs(st_acc.debit_amount_oc - ans_acc.debit_amount_oc) <= tol
            credit_oc_ok = abs(st_acc.credit_amount_oc - ans_acc.credit_amount_oc) <= tol
            qty_ok = abs(st_acc.quantity - ans_acc.quantity) <= tol

            acc_ok = debit_ok and credit_ok and debit_oc_ok and credit_oc_ok and qty_ok
            if acc_ok:
                matched_items += 1

            details.append({
                "account_code": code,
                "debit": {"ans": float(ans_acc.debit_amount), "st": float(st_acc.debit_amount), "ok": debit_ok},
                "credit": {"ans": float(ans_acc.credit_amount), "st": float(st_acc.credit_amount), "ok": credit_ok},
                "quantity": {"ans": float(ans_acc.quantity), "st": float(st_acc.quantity), "ok": qty_ok},
                "match": acc_ok
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
