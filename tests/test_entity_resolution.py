from datetime import datetime
from decimal import Decimal

from src.answer.models import (
    AccountingEventLine,
    AccountingGraph,
    InventoryEntity,
    OpeningInventoryLine,
)
from src.grading.entity_resolution import EntityResolver


def event(role, entity, qty, price, ref_id):
    return AccountingEventLine(
        module_role=role,
        ref_type=1,
        ref_id=ref_id,
        ref_detail_id=f"{ref_id}-detail",
        ref_date=datetime(2024, 1, 1),
        posted_date=None,
        entity_id=entity,
        account_object_id=None,
        stock_id=None,
        unit_id=None,
        quantity=Decimal(str(qty)),
        unit_price=Decimal(str(price)),
        amount=Decimal(str(qty * price)),
    )


def graph(prefix, flows):
    result = AccountingGraph()
    for item, ordered_qty in flows.items():
        entity_id = f"{prefix}-{item}"
        result.entities[entity_id] = InventoryEntity(
            entity_id=entity_id,
            item_type=1,
            unit_name="cái",
            display_code=f"CODE-{item}",
            display_name=f"Tên {item}",
        )
        result.opening_balances.append(
            OpeningInventoryLine(
                entity_id=entity_id,
                stock_id=None,
                unit_id=None,
                quantity=Decimal("10"),
                unit_price=Decimal("100000"),
                amount=Decimal("1000000"),
            )
        )
        result.events.append(
            event(
                "PU_ORDER",
                entity_id,
                ordered_qty,
                110000,
                f"{entity_id}-po",
            )
        )
        result.events.append(
            event(
                "PU_VOUCHER",
                entity_id,
                ordered_qty,
                110000,
                f"{entity_id}-pv",
            )
        )
    return result


def test_same_opening_balance_is_disambiguated_by_workflow_vector():
    answer = graph("answer", {"A": 5, "B": 8})
    student = graph("student", {"X": 8, "Y": 5})

    result = EntityResolver().resolve(answer, student)

    assert result.mapping["answer-A"] == "student-Y"
    assert result.mapping["answer-B"] == "student-X"


def test_code_and_name_do_not_control_matching():
    answer = graph("answer", {"A": 5})
    student = graph("student", {"ZZZ": 5})
    student.entities["student-ZZZ"] = InventoryEntity(
        entity_id="student-ZZZ",
        item_type=1,
        unit_name="cái",
        display_code="MÃ-TỰ-ĐẶT",
        display_name="Tên hoàn toàn khác",
    )

    result = EntityResolver().resolve(answer, student)

    assert result.mapping == {"answer-A": "student-ZZZ"}
