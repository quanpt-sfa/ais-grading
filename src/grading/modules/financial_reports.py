from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Tuple

from src.grading.base import BaseModuleComparator, CompareResult
from src.answer.models import (
    FinancialReportAnswer,
    FinancialReportLine,
    MasterAnswerData,
)


class FinancialReportsComparator(BaseModuleComparator):
    """Chấm BCTC theo report instance và multiset dòng báo cáo.

    ``ReportType + ItemCode`` không phải khóa duy nhất. Các dòng có cùng cấu
    trúc được giữ thành multiset và ghép một-một theo giá trị; dòng thiếu/thừa
    đều làm giảm điểm. GUID cục bộ (RefID/ReportDetailID) không được so trực
    tiếp giữa database đáp án và database sinh viên.
    """

    @property
    def module_id(self) -> str:
        return "financial_reports"

    @property
    def display_name(self) -> str:
        return "Báo cáo tài chính (CĐKT & KQKD)"

    def compare(
        self,
        master_answer: MasterAnswerData,
        student_data: Dict[str, Any],
        tolerance: float = 1.0,
    ) -> CompareResult:
        answer = master_answer.financial_reports or FinancialReportAnswer()
        answer_lines = list(answer.lines)
        student_lines = [
            self._line_from_row(row)
            for row in student_data.get("financial_reports", [])
        ]

        if not answer_lines:
            no_student = not student_lines
            return CompareResult(
                module_id=self.module_id,
                display_name=self.display_name,
                total_items=1,
                matched_items=1 if no_student else 0,
                match_ratio=1.0 if no_student else 0.0,
                details=[
                    {
                        "field": "Không có dữ liệu báo cáo tài chính",
                        "match": no_student,
                    }
                ],
                raw_answer=answer,
                raw_student=student_data.get("financial_reports", []),
            )

        use_instance_metadata = (
            self._has_instance_metadata(answer_lines)
            and self._has_instance_metadata(student_lines)
        )
        use_line_position = (
            self._has_line_position(answer_lines)
            and self._has_line_position(student_lines)
        )

        answer_groups = self._group_lines(
            answer_lines,
            use_instance_metadata=use_instance_metadata,
            use_line_position=use_line_position,
        )
        student_groups = self._group_lines(
            student_lines,
            use_instance_metadata=use_instance_metadata,
            use_line_position=use_line_position,
        )

        tol = Decimal(str(tolerance))
        total_items = 0
        matched_items = 0
        details: List[Dict[str, Any]] = []

        for key in sorted(
            set(answer_groups) | set(student_groups),
            key=repr,
        ):
            expected = sorted(
                answer_groups.get(key, []),
                key=self._amount_vector,
            )
            observed = sorted(
                student_groups.get(key, []),
                key=self._amount_vector,
            )
            common = min(len(expected), len(observed))

            for index in range(common):
                answer_line = expected[index]
                student_line = observed[index]
                checks = {
                    "amount": abs(
                        student_line.amount - answer_line.amount
                    )
                    <= tol,
                    "prev_amount": abs(
                        student_line.prev_amount - answer_line.prev_amount
                    )
                    <= tol,
                    "other_amount": abs(
                        student_line.other_amount - answer_line.other_amount
                    )
                    <= tol,
                    "other_prev_amount": abs(
                        student_line.other_prev_amount
                        - answer_line.other_prev_amount
                    )
                    <= tol,
                }
                line_ok = all(checks.values())
                total_items += 1
                if line_ok:
                    matched_items += 1
                details.append(
                    self._detail(
                        key,
                        answer_line,
                        student_line,
                        checks,
                        line_ok,
                        occurrence=index + 1,
                    )
                )

            for index, answer_line in enumerate(expected[common:], start=common + 1):
                total_items += 1
                details.append(
                    self._detail(
                        key,
                        answer_line,
                        None,
                        {"line_exists": False},
                        False,
                        occurrence=index,
                        reason="Thiếu dòng báo cáo trong bài sinh viên",
                    )
                )

            for index, student_line in enumerate(observed[common:], start=common + 1):
                total_items += 1
                details.append(
                    self._detail(
                        key,
                        None,
                        student_line,
                        {"extra_line": False},
                        False,
                        occurrence=index,
                        reason="Dòng báo cáo thừa trong bài sinh viên",
                    )
                )

        if total_items == 0:
            total_items = 1

        return CompareResult(
            module_id=self.module_id,
            display_name=self.display_name,
            total_items=total_items,
            matched_items=matched_items,
            match_ratio=matched_items / total_items,
            details=details,
            raw_answer=answer,
            raw_student=student_data.get("financial_reports", []),
        )

    @classmethod
    def _group_lines(
        cls,
        lines: Iterable[FinancialReportLine],
        *,
        use_instance_metadata: bool,
        use_line_position: bool,
    ) -> DefaultDict[Tuple[Any, ...], List[FinancialReportLine]]:
        groups: DefaultDict[Tuple[Any, ...], List[FinancialReportLine]] = defaultdict(list)
        for line in lines:
            key: Tuple[Any, ...] = (line.report_type, line.item_code)
            if use_instance_metadata:
                key = cls._instance_key(line) + key
            if use_line_position:
                key = key + (
                    line.item_index,
                    line.sort_order,
                    line.category,
                    line.formula_type,
                )
            groups[key].append(line)
        return groups

    @classmethod
    def _instance_key(cls, line: FinancialReportLine) -> Tuple[Any, ...]:
        return (
            line.report_ref_type,
            line.year,
            line.period,
            cls._date_token(line.from_date),
            cls._date_token(line.to_date),
            line.display_on_book,
            line.currency_id or "",
        )

    @staticmethod
    def _has_instance_metadata(lines: Iterable[FinancialReportLine]) -> bool:
        return any(
            line.report_ref_type is not None
            or line.year is not None
            or line.period is not None
            or line.from_date is not None
            or line.to_date is not None
            for line in lines
        )

    @staticmethod
    def _has_line_position(lines: Iterable[FinancialReportLine]) -> bool:
        return any(
            line.item_index is not None
            or line.sort_order is not None
            or line.category is not None
            for line in lines
        )

    @staticmethod
    def _amount_vector(line: FinancialReportLine) -> Tuple[Decimal, ...]:
        return (
            line.amount,
            line.prev_amount,
            line.other_amount,
            line.other_prev_amount,
        )

    @classmethod
    def _detail(
        cls,
        key: Tuple[Any, ...],
        answer: Optional[FinancialReportLine],
        student: Optional[FinancialReportLine],
        checks: Dict[str, bool],
        match: bool,
        *,
        occurrence: int,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        source = answer or student
        result: Dict[str, Any] = {
            "structural_key": [str(value) for value in key],
            "report_type": source.report_type if source else None,
            "item_code": source.item_code if source else None,
            "item_index": source.item_index if source else None,
            "sort_order": source.sort_order if source else None,
            "category": source.category if source else None,
            "occurrence": occurrence,
            "answer_report_ref_id": answer.report_ref_id if answer else None,
            "student_report_ref_id": student.report_ref_id if student else None,
            "answer_report_detail_id": (
                answer.report_detail_id if answer else None
            ),
            "student_report_detail_id": (
                student.report_detail_id if student else None
            ),
            "answer_amount": float(answer.amount) if answer else None,
            "student_amount": float(student.amount) if student else None,
            "checks": checks,
            "match": match,
        }
        if reason:
            result["reason"] = reason
        return result

    @staticmethod
    def _line_from_row(row: Dict[str, Any]) -> FinancialReportLine:
        return FinancialReportLine(
            report_detail_id=FinancialReportsComparator._text(
                row.get("ReportDetailID")
            ),
            report_ref_id=FinancialReportsComparator._text(row.get("RefID")),
            report_type=str(row.get("ReportType") or "").strip(),
            item_id=FinancialReportsComparator._text(row.get("ItemID")),
            item_code=str(row.get("ItemCode") or "").strip(),
            item_index=FinancialReportsComparator._int_or_none(
                row.get("ItemIndex")
            ),
            sort_order=FinancialReportsComparator._int_or_none(
                row.get("SortOrder")
            ),
            category=FinancialReportsComparator._int_or_none(
                row.get("Category")
            ),
            formula_type=FinancialReportsComparator._int_or_none(
                row.get("FormulaType")
            ),
            amount=Decimal(str(row.get("Amount") or 0)),
            prev_amount=Decimal(str(row.get("PrevAmount") or 0)),
            other_amount=Decimal(str(row.get("OtherAmount") or 0)),
            other_prev_amount=Decimal(
                str(row.get("OtherPrevAmount") or 0)
            ),
            report_ref_type=FinancialReportsComparator._int_or_none(
                row.get("ReportRefType")
            ),
            display_on_book=FinancialReportsComparator._int_or_none(
                row.get("DisplayOnBook")
            ),
            branch_id=FinancialReportsComparator._text(row.get("BranchID")),
            period=FinancialReportsComparator._int_or_none(row.get("Period")),
            year=FinancialReportsComparator._int_or_none(row.get("Year")),
            period_name=FinancialReportsComparator._text(
                row.get("PeriodName")
            ),
            report_name=FinancialReportsComparator._text(row.get("ReportName")),
            from_date=row.get("FromDate"),
            to_date=row.get("ToDate"),
            currency_id=FinancialReportsComparator._text(
                row.get("CurrencyID")
            ),
            is_report_finance_audit=(
                bool(row.get("IsReportFinanceAudit"))
                if row.get("IsReportFinanceAudit") is not None
                else None
            ),
        )

    @staticmethod
    def _text(value: Any) -> Optional[str]:
        if value in (None, ""):
            return None
        return str(value)

    @staticmethod
    def _int_or_none(value: Any) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _date_token(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)[:10]
