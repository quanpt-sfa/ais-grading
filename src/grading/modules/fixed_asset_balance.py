from decimal import Decimal
from typing import Any, Dict
from src.grading.base import BaseModuleComparator, CompareResult
from src.answer.models import MasterAnswerData, FixedAssetAnswer

class FixedAssetBalanceComparator(BaseModuleComparator):
    """Phân hệ 2: Chấm số dư tài sản cố định mở đầu (FixedAsset WHERE RefNo='opn')."""

    @property
    def module_id(self) -> str:
        return "fixed_asset_balance"

    @property
    def display_name(self) -> str:
        return "Số dư tài sản cố định"

    def compare(self, master_answer: MasterAnswerData, student_data: Dict[str, Any], tolerance: float = 1.0) -> CompareResult:
        ans = master_answer.fixed_asset or FixedAssetAnswer()
        st_rows = student_data.get("fixed_asset_balance", [])

        details = []
        total_items = len(ans.items) if ans.items else 1
        matched_items = 0

        if not ans.items:
            # If no answer fixed assets specified, verify student has none either
            no_st_fa = (len(st_rows) == 0)
            return CompareResult(
                module_id=self.module_id,
                display_name=self.display_name,
                total_items=1,
                matched_items=1 if no_st_fa else 0,
                match_ratio=1.0 if no_st_fa else 0.0,
                details=[{"field": "Không phát sinh TSCĐ", "match": no_st_fa}]
            )

        # Compare per fixed asset item
        for idx, item in enumerate(ans.items):
            # Try to match by code or position
            st_match = None
            if idx < len(st_rows):
                st_match = st_rows[idx]
            
            if not st_match:
                details.append({
                    "asset_index": idx + 1,
                    "code": item.code,
                    "match": False,
                    "reason": "Thiếu thông tin TSCĐ trong bài làm"
                })
                continue

            st_org = Decimal(str(st_match.get("OrgPrice", 0) or 0))
            st_accum = Decimal(str(st_match.get("AccumDepreciationAmount", 0) or 0))
            st_lifetime = int(st_match.get("LifeTimeInMonth", 0) or 0)
            st_rem = int(st_match.get("LifeTimeRemainingInMonth", 0) or 0)

            org_ok = abs(st_org - item.org_price) <= Decimal(str(tolerance))
            accum_ok = abs(st_accum - item.accum_depreciation_amount) <= Decimal(str(tolerance))
            life_ok = (st_lifetime == item.lifetime_months) if item.lifetime_months > 0 else True
            rem_ok = (st_rem == item.remaining_months) if item.remaining_months > 0 else True

            item_ok = org_ok and accum_ok and life_ok and rem_ok
            if item_ok:
                matched_items += 1

            details.append({
                "asset_index": idx + 1,
                "code": item.code,
                "org_price": {"answer": float(item.org_price), "student": float(st_org), "ok": org_ok},
                "accum_depreciation": {"answer": float(item.accum_depreciation_amount), "student": float(st_accum), "ok": accum_ok},
                "match": item_ok
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
