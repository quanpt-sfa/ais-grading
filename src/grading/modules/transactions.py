from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from src.answer.models import (
    AccountingEventLine,
    AccountingGraph,
    MasterAnswerData,
    TransactionAnswer,
    TransactionItem,
)
from src.grading.base import BaseModuleComparator, CompareResult
from src.grading.context import GradingContext


class TransactionsComparator(BaseModuleComparator):
    """Chấm nghiệp vụ theo dòng và chuỗi phân hệ.

    Nếu dữ liệu chi tiết chưa khả dụng, comparator tự động quay về logic tổng
    hợp cũ để giữ tương thích ngược.
    """

    WORKFLOW_ROLES = {"PU_ORDER", "PU_VOUCHER", "IN_INWARD"}

    @property
    def module_id(self) -> str:
        return "transactions"

    @property
    def display_name(self) -> str:
        return "Nghiệp vụ và luân chuyển phân hệ"

    def compare(
        self,
        master_answer: MasterAnswerData,
        student_data: Dict[str, Any],
        tolerance: float = 1.0,
    ) -> CompareResult:
        context = student_data.get("_grading_context")
        if not isinstance(context, GradingContext):
            return self._compare_legacy(master_answer, student_data, tolerance)
        if not context.master_graph.has_workflow_data:
            return self._compare_legacy(master_answer, student_data, tolerance)
        return self._compare_workflow(context, tolerance)

    def _compare_workflow(
        self,
        context: GradingContext,
        tolerance: float,
    ) -> CompareResult:
        answer_events = [
            event
            for event in context.master_graph.events
            if event.module_role in self.WORKFLOW_ROLES
        ]
        student_events = [
            event
            for event in context.student_graph.events
            if event.module_role in self.WORKFLOW_ROLES
        ]
        tol = Decimal(str(tolerance))
        used_student_indexes: Set[int] = set()
        total_checks = 0
        matched_checks = 0
        details: List[Dict[str, Any]] = []

        for answer_event in answer_events:
            mapped_entity = context.entity_resolution.mapping.get(
                answer_event.entity_id or ""
            )
            candidate_index = self._best_candidate_index(
                answer_event,
                mapped_entity,
                student_events,
                used_student_indexes,
            )
            student_event = (
                student_events[candidate_index]
                if candidate_index is not None
                else None
            )
            if candidate_index is not None:
                used_student_indexes.add(candidate_index)

            checks = self._event_checks(
                answer_event,
                student_event,
                context.master_graph,
                context.student_graph,
                mapped_entity,
                tol,
            )
            total_checks += len(checks)
            matched_checks += sum(1 for ok in checks.values() if ok)
            details.append(
                {
                    "answer_entity_id": answer_event.entity_id,
                    "student_entity_id": mapped_entity,
                    "module_role": answer_event.module_role,
                    "answer_ref_type": answer_event.ref_type,
                    "student_ref_type": (
                        student_event.ref_type if student_event else None
                    ),
                    "answer_ref_id": answer_event.ref_id,
                    "student_ref_id": (
                        student_event.ref_id if student_event else None
                    ),
                    "checks": checks,
                    "match": all(checks.values()),
                }
            )

        # Full outer comparison: dòng phát sinh thừa cũng làm giảm điểm.
        for index, event in enumerate(student_events):
            if index in used_student_indexes:
                continue
            total_checks += 1
            details.append(
                {
                    "student_entity_id": event.entity_id,
                    "module_role": event.module_role,
                    "student_ref_id": event.ref_id,
                    "checks": {"extra_event": False},
                    "match": False,
                    "reason": "Nghiệp vụ có trong bài sinh viên nhưng không có đối ứng trong đáp án",
                }
            )

        if total_checks == 0:
            total_checks = 1
            matched_checks = 1 if not student_events else 0
            details.append(
                {
                    "field": "Không có nghiệp vụ phát sinh",
                    "match": not student_events,
                }
            )

        return CompareResult(
            module_id=self.module_id,
            display_name=self.display_name,
            total_items=total_checks,
            matched_items=matched_checks,
            match_ratio=matched_checks / total_checks,
            details=details,
            raw_answer=context.master_graph,
            raw_student=context.student_graph,
        )

    def _best_candidate_index(
        self,
        answer_event: AccountingEventLine,
        mapped_entity: Optional[str],
        student_events: List[AccountingEventLine],
        used_indexes: Set[int],
    ) -> Optional[int]:
        if not mapped_entity:
            return None
        candidates: List[Tuple[float, int]] = []
        for index, student_event in enumerate(student_events):
            if index in used_indexes:
                continue
            if student_event.entity_id != mapped_entity:
                continue
            if student_event.module_role != answer_event.module_role:
                continue
            candidates.append(
                (self._event_distance(answer_event, student_event), index)
            )
        if not candidates:
            return None
        return min(candidates, key=lambda item: (item[0], item[1]))[1]

    def _event_distance(
        self,
        answer: AccountingEventLine,
        student: AccountingEventLine,
    ) -> float:
        cost = 0.0
        cost += self._relative_error(answer.quantity, student.quantity) * 30
        cost += self._relative_error(answer.unit_price, student.unit_price) * 15
        cost += self._relative_error(answer.amount, student.amount) * 20
        if answer.ref_type != student.ref_type:
            cost += 20
        if self._date_value(answer.ref_date) != self._date_value(student.ref_date):
            cost += 5
        if answer.debit_account and answer.debit_account != student.debit_account:
            cost += 5
        if answer.credit_account and answer.credit_account != student.credit_account:
            cost += 5
        return cost

    def _event_checks(
        self,
        answer: AccountingEventLine,
        student: Optional[AccountingEventLine],
        answer_graph: AccountingGraph,
        student_graph: AccountingGraph,
        mapped_entity: Optional[str],
        tolerance: Decimal,
    ) -> Dict[str, bool]:
        checks: Dict[str, bool] = {"event_exists": student is not None}
        if student is None:
            checks.update(
                {
                    "ref_type": False,
                    "quantity": False,
                    "unit_price": False,
                    "amount": False,
                    "date": False,
                }
            )
        else:
            checks.update(
                {
                    "ref_type": answer.ref_type == student.ref_type,
                    "quantity": abs(answer.quantity - student.quantity)
                    <= tolerance,
                    "unit_price": abs(answer.unit_price - student.unit_price)
                    <= tolerance,
                    "amount": abs(answer.amount - student.amount) <= tolerance,
                    "date": self._date_value(answer.ref_date)
                    == self._date_value(student.ref_date),
                }
            )

        if answer.debit_account:
            checks["debit_account"] = bool(
                student and student.debit_account == answer.debit_account
            )
        if answer.credit_account:
            checks["credit_account"] = bool(
                student and student.credit_account == answer.credit_account
            )

        if answer.source_ref_id or answer.source_ref_detail_id:
            checks["explicit_source_link"] = bool(
                student
                and self._has_explicit_source(
                    student_graph, student, mapped_entity
                )
            )

        if answer.is_posted_finance is True:
            checks["posted_finance"] = bool(
                student and student.is_posted_finance is True
            )
        if answer.is_posted_inventory is True:
            checks["posted_inventory"] = bool(
                student and student.is_posted_inventory is True
            )

        if self._has_posting(
            answer_graph.inventory_ledger, answer, answer.entity_id
        ):
            checks["inventory_ledger_posting"] = bool(
                student
                and self._has_posting(
                    student_graph.inventory_ledger,
                    student,
                    mapped_entity,
                )
            )
        if self._has_posting(
            answer_graph.general_ledger, answer, answer.entity_id
        ):
            checks["general_ledger_posting"] = bool(
                student
                and self._has_posting(
                    student_graph.general_ledger,
                    student,
                    mapped_entity,
                )
            )
        return checks

    def _has_explicit_source(
        self,
        graph: AccountingGraph,
        event: AccountingEventLine,
        entity_id: Optional[str],
    ) -> bool:
        if event.source_ref_detail_id:
            if any(
                source.ref_detail_id == event.source_ref_detail_id
                and source.entity_id == entity_id
                for source in graph.events
            ):
                return True
        if event.source_ref_id:
            if any(
                source.ref_id == event.source_ref_id
                and source.entity_id == entity_id
                for source in graph.events
            ):
                return True
            if any(
                {edge.source_ref_id, edge.target_ref_id}
                == {event.source_ref_id, event.ref_id}
                for edge in graph.edges
            ):
                return True
        return False

    @staticmethod
    def _has_posting(
        postings: Iterable[AccountingEventLine],
        event: AccountingEventLine,
        entity_id: Optional[str],
    ) -> bool:
        for posting in postings:
            if posting.ref_id != event.ref_id:
                continue
            if entity_id and posting.entity_id not in (None, entity_id):
                continue
            if (
                event.ref_detail_id
                and posting.ref_detail_id
                and posting.ref_detail_id != event.ref_detail_id
            ):
                continue
            return True
        return False

    @staticmethod
    def _relative_error(left: Decimal, right: Decimal) -> float:
        scale = max(abs(left), abs(right), Decimal("1"))
        return float(abs(left - right) / scale)

    @staticmethod
    def _date_value(value: Any):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return str(value)[:10]

    def _compare_legacy(
        self,
        master_answer: MasterAnswerData,
        student_data: Dict[str, Any],
        tolerance: float,
    ) -> CompareResult:
        ans = master_answer.transactions or TransactionAnswer()
        st_rows = student_data.get("transactions", [])
        st_entries: Dict[Tuple[int, str], TransactionItem] = {}
        duplicate_keys = set()
        for row in st_rows:
            try:
                task_id = int(row["TaskID"])
                code = str(row["AccountCode"]).strip()
                key = (task_id, code)
                if key in st_entries:
                    duplicate_keys.add(key)
                st_entries[key] = TransactionItem(
                    task_id=task_id,
                    account_code=code,
                    amount=Decimal(str(row.get("Amount", 0) or 0)),
                    amount_oc=Decimal(str(row.get("AmountOC", 0) or 0)),
                    quantity=Decimal(str(row.get("Quantity", 0) or 0)),
                )
            except (ValueError, KeyError, TypeError):
                continue

        details = []
        all_keys = set(ans.entries) | set(st_entries)
        total_items = len(all_keys) + len(duplicate_keys)
        matched_items = 0
        tol = Decimal(str(tolerance))

        for key in sorted(all_keys):
            answer_tx = ans.entries.get(key)
            student_tx = st_entries.get(key)
            if answer_tx is None:
                details.append(
                    {
                        "task_id": key[0],
                        "account_code": key[1],
                        "match": False,
                        "reason": "Nghiệp vụ thừa trong bài sinh viên",
                    }
                )
                continue
            if student_tx is None:
                details.append(
                    {
                        "task_id": key[0],
                        "account_code": key[1],
                        "match": False,
                        "reason": "Thiếu nghiệp vụ trong bài sinh viên",
                    }
                )
                continue
            amount_ok = abs(student_tx.amount - answer_tx.amount) <= tol
            amount_oc_ok = abs(student_tx.amount_oc - answer_tx.amount_oc) <= tol
            quantity_ok = abs(student_tx.quantity - answer_tx.quantity) <= tol
            matched = amount_ok and amount_oc_ok and quantity_ok
            matched_items += int(matched)
            details.append(
                {
                    "task_id": key[0],
                    "account_code": key[1],
                    "amount": {
                        "answer": float(answer_tx.amount),
                        "student": float(student_tx.amount),
                        "ok": amount_ok,
                    },
                    "amount_oc": {
                        "answer": float(answer_tx.amount_oc),
                        "student": float(student_tx.amount_oc),
                        "ok": amount_oc_ok,
                    },
                    "quantity": {
                        "answer": float(answer_tx.quantity),
                        "student": float(student_tx.quantity),
                        "ok": quantity_ok,
                    },
                    "match": matched,
                }
            )

        for key in sorted(duplicate_keys):
            details.append(
                {
                    "task_id": key[0],
                    "account_code": key[1],
                    "match": False,
                    "reason": "Khóa nghiệp vụ bị trùng trong bài sinh viên",
                }
            )

        if total_items == 0:
            total_items = 1
            matched_items = 1
            details.append(
                {"field": "Không có nghiệp vụ phát sinh", "match": True}
            )

        return CompareResult(
            module_id=self.module_id,
            display_name=self.display_name,
            total_items=total_items,
            matched_items=matched_items,
            match_ratio=matched_items / total_items,
            details=details,
            raw_answer=ans,
            raw_student=st_rows,
        )
