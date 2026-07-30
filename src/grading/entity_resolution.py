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
    # costs là chi phí xếp hạng: dấu neo định danh + tie-break nghiệp vụ có giới hạn.
    costs: Dict[Tuple[str, str], float] = field(default_factory=dict)
    # anchor_costs chỉ chứa bằng chứng dùng để quyết định có nhận diện được hay không.
    anchor_costs: Dict[Tuple[str, str], float] = field(default_factory=dict)


class EntityResolver:
    """Ghép mặt hàng đáp án với mặt hàng sinh viên theo hai tầng.

    Tầng định danh chỉ dùng loại đối tượng, đơn vị và số dư đầu kỳ. Nghiệp vụ
    phát sinh chỉ là tie-break nhẹ khi nhiều đối tượng có cùng dấu neo. Vì vậy,
    sinh viên làm đúng đầu kỳ nhưng sai các nghiệp vụ sau vẫn được nhận diện;
    các sai sót nghiệp vụ được trừ ở comparator thay vì biến thành ``MISSING``.
    """

    MISSING_COST = 250.0

    def __init__(self, config: Optional[Dict] = None):
        cfg = config or {}
        # Tương thích với cấu hình cũ dùng max_cost.
        self.max_anchor_cost = float(
            cfg.get("max_anchor_cost", cfg.get("max_cost", 35.0))
        )
        self.ambiguity_margin = float(cfg.get("ambiguity_margin", 0.5))
        self.event_tiebreak_weight = float(
            cfg.get("event_tiebreak_weight", 0.20)
        )
        self.event_tiebreak_cap = float(cfg.get("event_tiebreak_cap", 25.0))
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
                answer = answer_fp[answer_id]
                student = student_fp[student_id]
                anchor_cost = self._anchor_cost(answer, student)
                event_cost = self._event_distance(
                    answer.event_signature,
                    student.event_signature,
                )
                # Nghiệp vụ chỉ hỗ trợ chọn giữa các ứng viên cùng dấu neo. Nó
                # không được tăng vô hạn và không được làm mất nhận diện.
                ranking_cost = anchor_cost + min(
                    event_cost * self.event_tiebreak_weight,
                    self.event_tiebreak_cap,
                )
                result.anchor_costs[(answer_id, student_id)] = anchor_cost
                result.costs[(answer_id, student_id)] = ranking_cost

        assignment = self._assign(answer_ids, student_ids, result.costs)
        used_students = set()
        for answer_id, student_id in assignment.items():
            if student_id is None:
                result.missing_answer_entities.append(answer_id)
                continue

            anchor_cost = result.anchor_costs[(answer_id, student_id)]
            if anchor_cost > self.max_anchor_cost:
                result.missing_answer_entities.append(answer_id)
                continue

            result.mapping[answer_id] = student_id
            used_students.add(student_id)

            # Ambiguity được xác định từ dấu neo, không phải từ mức độ làm đúng
            # nghiệp vụ. Hai mặt hàng cùng đầu kỳ vẫn là một nhóm tương đương
            # dù một trong hai có chuỗi nghiệp vụ gần đáp án hơn.
            ranked_anchors = sorted(
                (
                    (result.anchor_costs[(answer_id, candidate)], candidate)
                    for candidate in student_ids
                ),
                key=lambda item: (item[0], item[1]),
            )
            if ranked_anchors:
                best_anchor = ranked_anchors[0][0]
                candidates = [
                    candidate
                    for cost, candidate in ranked_anchors
                    if cost <= self.max_anchor_cost
                    and cost - best_anchor <= self.ambiguity_margin
                ]
                if len(candidates) >= 2:
                    result.ambiguous_candidates[answer_id] = candidates

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

    def _anchor_cost(
        self,
        answer: InventoryFingerprint,
        student: InventoryFingerprint,
    ) -> float:
        """Chi phí định danh; tuyệt đối không chứa nghiệp vụ phát sinh."""
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
            and answer.unit_name.strip().casefold()
            != student.unit_name.strip().casefold()
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
        return cost

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
            anchor_cost = self._last_anchor_cost_placeholder(
                answer_id, student_id
            )
            if anchor_cost is not None and anchor_cost > self.max_anchor_cost:
                continue
            assignment[answer_id] = student_id
            used_answers.add(answer_id)
            used_students.add(student_id)
        return assignment

    @staticmethod
    def _last_anchor_cost_placeholder(
        answer_id: str,
        student_id: str,
    ) -> Optional[float]:
        # Greedy assignment is only used for unusually large exams. Eligibility
        # is rechecked in resolve() against EntityResolution.anchor_costs.
        return None
