from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Set, Tuple

from src.grading.base import BaseModuleComparator, CompareResult
from src.answer.models import (
    FinancialReportAnswer,
    FinancialReportLine,
    MasterAnswerData,
)


class FinancialReportsComparator(BaseModuleComparator):
    """Chấm BCTC theo report instance và multiset dòng báo cáo.

    ``ReportType + ItemCode`` không phải khóa duy nhất. Các dòng có cùng cấu
    trúc được giữ thành multiset và ghép một-một theo giá trị. GUID cục bộ
    (RefID/ReportDetailID) chỉ phục vụ audit, không được so trực tiếp giữa hai
    database.
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

        tolerance_decimal = Decimal(str(tolerance))
        total_items = 0
        matched_items = 0
        details: List[Dict[str, Any]] = []

        for key in sorted(
            set(answer_groups) | set(student_groups),
            key=repr,
        ):
            expected = list(answer_groups.get(key, []))
            observed = list(student_groups.get(key, []))
            group_result = self._compare_group(
                key,
                expected,
                observed,
                tolerance_decimal,
            )
            total_items += group_result["total_items"]
            matched_items += group_result["matched_items"]
            details.extend(group_result["details"])

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

    def _compare_group(
        self,
        key: Tuple[Any, ...],
        expected: List[FinancialReportLine],
        observed: List[FinancialReportLine],
        tolerance: Decimal,
    ) -> Dict[str, Any]:
        compatible_pairs = self._maximum_compatible_pairs(
            expected,
            observed,
            tolerance,
        )
        used_answer = {answer_index for answer_index, _ in compatible_pairs}
        used_student = {student_index for _, student_index in compatible_pairs}

        # Sau khi khóa số cặp đúng tối đa, ghép các dòng sai còn lại theo tổng
        # sai lệch nhỏ nhất. Điều này cho một mismatch mỗi dòng thay vì đồng thời
        # tạo một dòng thiếu và một dòng thừa giả.
        remaining_pairs = self._minimum_distance_pairs(
            expected,
            observed,
            used_answer,
            used_student,
        )
        used_answer.update(answer_index for answer_index, _ in remaining_pairs)
        used_student.update(student_index for _, student_index in remaining_pairs)

        details: List[Dict[str, Any]] = []
        occurrence = 0
        for answer_index, student_index in sorted(
            compatible_pairs + remaining_pairs,
            key=lambda pair: (pair[0], pair[1]),
        ):
            occurrence += 1
            answer_line = expected[answer_index]
            student_line = observed[student_index]
            checks = self._line_checks(answer_line, student_line, tolerance)
            line_ok = all(checks.values())
            details.append(
                self._detail(
                    key,
                    answer_line,
                    student_line,
                    checks,
                    line_ok,
                    occurrence=occurrence,
                )
            )

        for answer_index, answer_line in enumerate(expected):
            if answer_index in used_answer:
                continue
            occurrence += 1
            details.append(
                self._detail(
                    key,
                    answer_line,
                    None,
                    {"line_exists": False},
                    False,
                    occurrence=occurrence,
                    reason="Thiếu dòng báo cáo trong bài sinh viên",
                )
            )

        for student_index, student_line in enumerate(observed):
            if student_index in used_student:
                continue
            occurrence += 1
            details.append(
                self._detail(
                    key,
                    None,
                    student_line,
                    {"extra_line": False},
                    False,
                    occurrence=occurrence,
                    reason="Dòng báo cáo thừa trong bài sinh viên",
                )
            )

        return {
            "total_items": len(details),
            "matched_items": sum(1 for detail in details if detail["match"]),
            "details": details,
        }

    def _maximum_compatible_pairs(
        self,
        expected: List[FinancialReportLine],
        observed: List[FinancialReportLine],
        tolerance: Decimal,
    ) -> List[Tuple[int, int]]:
        candidates: Dict[int, List[int]] = {}
        for answer_index, answer_line in enumerate(expected):
            compatible = [
                student_index
                for student_index, student_line in enumerate(observed)
                if all(
                    self._line_checks(
                        answer_line,
                        student_line,
                        tolerance,
                    ).values()
                )
            ]
            candidates[answer_index] = sorted(
                compatible,
                key=lambda student_index: self._line_distance(
                    answer_line,
                    observed[student_index],
                ),
            )

        student_to_answer: Dict[int, int] = {}

        def augment(answer_index: int, seen: Set[int]) -> bool:
            for student_index in candidates.get(answer_index, []):
                if student_index in seen:
                    continue
                seen.add(student_index)
                previous_answer = student_to_answer.get(student_index)
                if previous_answer is None or augment(previous_answer, seen):
                    student_to_answer[student_index] = answer_index
                    return True
            return False

        for answer_index in sorted(
            range(len(expected)),
            key=lambda index: len(candidates.get(index, [])),
        ):
            augment(answer_index, set())

        return sorted(
            (
                (answer_index, student_index)
                for student_index, answer_index in student_to_answer.items()
            ),
            key=lambda pair: (pair[0], pair[1]),
        )

    def _minimum_distance_pairs(
        self,
        expected: List[FinancialReportLine],
        observed: List[FinancialReportLine],
        used_answer: Set[int],
        used_student: Set[int],
    ) -> List[Tuple[int, int]]:
        pairs = sorted(
            (
                (
                    self._line_distance(answer_line, student_line),
                    answer_index,
                    student_index,
                )
                for answer_index, answer_line in enumerate(expected)
                if answer_index not in used_answer
                for student_index, student_line in enumerate(observed)
                if student_index not in used_student
            ),
            key=lambda item: (item[0], item[1], item[2]),
        )
        result: List[Tuple[int, int]] = []
        assigned_answers: Set[int] = set()
        assigned_students: Set[int] = set()
        for _, answer_index, student_index in pairs:
            if answer_index in assigned_answers or student_index in assigned_students:
                continue
            result.append((answer_index, student_index))
            assigned_answers.add(answer_index)
            assigned_students.add(student_index)
        return result

    @staticmethod
    def _line_checks(
        answer: FinancialReportLine,
        student: FinancialReportLine,
        tolerance: Decimal,
    ) -> Dict[str, bool]:
        return {
            "amount": abs(student.amount - answer.amount) <= tolerance,
            "prev_amount": abs(student.prev_amount - answer.prev_amount)
            <= tolerance,
            "other_amount": abs(student.other_amount - answer.other_amount)
            <= tolerance,
            "other_prev_amount": abs(
                student.other_prev_amount - answer.other_prev_amount
            )
            <= tolerance,
        }

    @staticmethod
    def _line_distance(
        answer: FinancialReportLine,
        student: FinancialReportLine,
    ) -> Decimal:
        return sum(
            (
                abs(student.amount - answer.amount),
                abs(student.prev_amount - answer.prev_amount),
                abs(student.other_amount - answer.other_amount),
                abs(student.other_prev_amount - answer.other_prev_amount),
            ),
            Decimal("0"),
        )

    @classmethod
    def _group_lines(
        cls,
        lines: Iterable[FinancialReportLine],
        *,
        use_instance_metadata: bool,
        use_line_position: bool,
    ) -> DefaultDict[Tuple[Any, ...], List[FinancialReportLine]]:
        groups: DefaultDict[Tuple[Any, ...], List[FinancialReportLine]] = (
            defaultdict(list)
        )
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
