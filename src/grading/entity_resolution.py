from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from typing import Dict, List, Optional, Sequence, Tuple

from src.answer.models import AccountingGraph


@dataclass(frozen=True)
class InventoryFingerprint:
    entity_id: str
    item_type: Optional[int]
    unit_name: Optional[str]
    opening_quantity: Decimal
    opening_unit_price: Decimal
    opening_amount: Decimal
    event_signature: Tuple[Tuple[str, Decimal, Decimal, Decimal], ...]


@dataclass
class EntityResolution:
    mapping: Dict[str, str] = field(default_factory=dict)
    missing_answer_entities: List[str] = field(default_factory=list)
    extra_student_entities: List[str] = field(default_factory=list)
    ambiguous_candidates: Dict[str, List[str]] = field(default_factory=dict)
    costs: Dict[Tuple[str, str], float] = field(default_factory=dict)


class EntityResolver:
    """Ghép mặt hàng đáp án với mặt hàng sinh viên bằng dấu vân tay kinh tế."""

    MISSING_COST = 250.0

    def __init__(self, config: Optional[Dict] = None):
        cfg = config or {}
        self.max_cost = float(cfg.get("max_cost", 80.0))
        self.ambiguity_margin = float(cfg.get("ambiguity_margin", 0.5))
        self.max_exact_assignment_size = int(
            cfg.get("max_exact_assignment_size", 18)
        )

    def resolve(
        self,
        answer_graph: AccountingGraph,
        student_graph: AccountingGraph,
    ) -> EntityResolution:
        answer_fp = self._fingerprints(answer_graph)
        student_fp = self._fingerprints(student_graph)
        answer_ids = sorted(answer_fp)
        student_ids = sorted(student_fp)

        result = EntityResolution()
        for answer_id in answer_ids:
            for student_id in student_ids:
                result.costs[(answer_id, student_id)] = self._cost(
                    answer_fp[answer_id], student_fp[student_id]
                )

        assignment = self._assign(answer_ids, student_ids, result.costs)
        used_students = set()
        for answer_id, student_id in assignment.items():
            if (
                student_id is None
                or result.costs[(answer_id, student_id)] > self.max_cost
            ):
                result.missing_answer_entities.append(answer_id)
                continue

            result.mapping[answer_id] = student_id
            used_students.add(student_id)
            ranked = sorted(
                (
                    (result.costs[(answer_id, candidate)], candidate)
                    for candidate in student_ids
                ),
                key=lambda item: (item[0], item[1]),
            )
            if len(ranked) >= 2 and (
                ranked[1][0] - ranked[0][0] <= self.ambiguity_margin
            ):
                result.ambiguous_candidates[answer_id] = [
                    candidate
                    for cost, candidate in ranked
                    if cost - ranked[0][0] <= self.ambiguity_margin
                ]

        result.extra_student_entities = [
            student_id
            for student_id in student_ids
            if student_id not in used_students
        ]
        return result

    def _fingerprints(
        self,
        graph: AccountingGraph,
    ) -> Dict[str, InventoryFingerprint]:
        # Chỉ ghép các đối tượng thực sự tham gia bài thi. Danh mục MISA có thể
        # chứa nhiều mã hệ thống/không sử dụng; đưa toàn bộ catalog vào sẽ tạo
        # ánh xạ và dòng thừa giả.
        entity_ids = {
            row.entity_id for row in graph.opening_balances if row.entity_id
        }
        entity_ids.update(
            event.entity_id for event in graph.events if event.entity_id
        )
        if not entity_ids:
            entity_ids = set(graph.entities)

        result: Dict[str, InventoryFingerprint] = {}
        for entity_id in sorted(entity_ids):
            opening_rows = [
                row
                for row in graph.opening_balances
                if row.entity_id == entity_id
            ]
            events = [
                event for event in graph.events if event.entity_id == entity_id
            ]
            opening_quantity = sum(
                (row.quantity for row in opening_rows), Decimal("0")
            )
            opening_amount = sum(
                (row.amount for row in opening_rows), Decimal("0")
            )
            opening_unit_price = (
                opening_amount / opening_quantity
                if opening_quantity != 0
                else Decimal("0")
            )
            event_signature = tuple(
                sorted(
                    (
                        event.module_role,
                        event.quantity,
                        event.unit_price,
                        event.amount,
                    )
                    for event in events
                )
            )
            entity = graph.entities.get(entity_id)
            result[entity_id] = InventoryFingerprint(
                entity_id=entity_id,
                item_type=entity.item_type if entity else None,
                unit_name=entity.unit_name if entity else None,
                opening_quantity=opening_quantity,
                opening_unit_price=opening_unit_price,
                opening_amount=opening_amount,
                event_signature=event_signature,
            )
        return result

    @staticmethod
    def _relative_error(left: Decimal, right: Decimal) -> float:
        scale = max(abs(left), abs(right), Decimal("1"))
        return float(abs(left - right) / scale)

    def _event_distance(
        self,
        left: Sequence[Tuple[str, Decimal, Decimal, Decimal]],
        right: Sequence[Tuple[str, Decimal, Decimal, Decimal]],
    ) -> float:
        roles = sorted({row[0] for row in left} | {row[0] for row in right})
        total = 0.0
        for role in roles:
            left_rows = sorted(row[1:] for row in left if row[0] == role)
            right_rows = sorted(row[1:] for row in right if row[0] == role)
            common = min(len(left_rows), len(right_rows))
            total += abs(len(left_rows) - len(right_rows)) * 12.0
            for index in range(common):
                left_qty, left_price, left_amount = left_rows[index]
                right_qty, right_price, right_amount = right_rows[index]
                total += self._relative_error(left_qty, right_qty) * 16.0
                total += self._relative_error(left_price, right_price) * 8.0
                total += self._relative_error(left_amount, right_amount) * 8.0
        return total

    def _cost(
        self,
        answer: InventoryFingerprint,
        student: InventoryFingerprint,
    ) -> float:
        cost = 0.0
        if (
            answer.item_type is not None
            and student.item_type is not None
            and answer.item_type != student.item_type
        ):
            cost += 30.0
        if (
            answer.unit_name
            and student.unit_name
            and answer.unit_name != student.unit_name
        ):
            cost += 8.0

        cost += self._relative_error(
            answer.opening_quantity, student.opening_quantity
        ) * 25.0
        cost += self._relative_error(
            answer.opening_unit_price, student.opening_unit_price
        ) * 15.0
        cost += self._relative_error(
            answer.opening_amount, student.opening_amount
        ) * 10.0
        cost += self._event_distance(
            answer.event_signature, student.event_signature
        )
        return cost

    def _assign(
        self,
        answer_ids: List[str],
        student_ids: List[str],
        costs: Dict[Tuple[str, str], float],
    ) -> Dict[str, Optional[str]]:
        if len(student_ids) <= self.max_exact_assignment_size:
            return self._assign_exact(answer_ids, student_ids, costs)
        return self._assign_greedy(answer_ids, student_ids, costs)

    def _assign_exact(
        self,
        answer_ids: List[str],
        student_ids: List[str],
        costs: Dict[Tuple[str, str], float],
    ) -> Dict[str, Optional[str]]:
        @lru_cache(maxsize=None)
        def solve(index: int, used_mask: int):
            if index >= len(answer_ids):
                return 0.0, ()

            answer_id = answer_ids[index]
            best_cost, best_tail = solve(index + 1, used_mask)
            best_cost += self.MISSING_COST
            best_choice: Tuple[Optional[str], ...] = (None,) + best_tail

            for student_index, student_id in enumerate(student_ids):
                if used_mask & (1 << student_index):
                    continue
                tail_cost, tail = solve(
                    index + 1, used_mask | (1 << student_index)
                )
                candidate_cost = costs[(answer_id, student_id)] + tail_cost
                if candidate_cost < best_cost:
                    best_cost = candidate_cost
                    best_choice = (student_id,) + tail
            return best_cost, best_choice

        _, choices = solve(0, 0)
        return {
            answer_id: choices[index] if index < len(choices) else None
            for index, answer_id in enumerate(answer_ids)
        }

    def _assign_greedy(
        self,
        answer_ids: List[str],
        student_ids: List[str],
        costs: Dict[Tuple[str, str], float],
    ) -> Dict[str, Optional[str]]:
        assignment: Dict[str, Optional[str]] = {
            answer_id: None for answer_id in answer_ids
        }
        used_answers = set()
        used_students = set()
        pairs = sorted(
            (
                (costs[(answer_id, student_id)], answer_id, student_id)
                for answer_id in answer_ids
                for student_id in student_ids
            ),
            key=lambda row: (row[0], row[1], row[2]),
        )
        for cost, answer_id, student_id in pairs:
            if answer_id in used_answers or student_id in used_students:
                continue
            if cost > self.max_cost:
                continue
            assignment[answer_id] = student_id
            used_answers.add(answer_id)
            used_students.add(student_id)
        return assignment
