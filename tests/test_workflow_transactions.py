from datetime import datetime
from decimal import Decimal

from src.answer.models import (
    AccountingEventLine,
    AccountingGraph,
    InventoryEntity,
    MasterAnswerData,
)
from src.grading.context import GradingContext
from src.grading.entity_resolution import EntityResolution
from src.grading.modules.transactions import TransactionsComparator


def line(role, entity, ref, detail, qty, source_detail=None, posted=True):
    return AccountingEventLine(
        module_role=role,
        ref_type=301,
        ref_id=ref,
        ref_detail_id=detail,
        ref_date=datetime(2024, 1, 10),
        posted_date=datetime(2024, 1, 10),
        entity_id=entity,
        account_object_id=None,
        stock_id=None,
        unit_id=None,
        quantity=Decimal(str(qty)),
        unit_price=Decimal("100000"),
        amount=Decimal(str(qty * 100000)),
        debit_account="1561" if role != "PU_ORDER" else None,
        credit_account="331" if role != "PU_ORDER" else None,
        source_ref_detail_id=source_detail,
        is_posted_finance=posted if role != "PU_ORDER" else None,
        is_posted_inventory=posted if role != "PU_ORDER" else None,
    )


def posting(role, entity, ref, detail):
    return AccountingEventLine(
        module_role=role,
        ref_type=301,
        ref_id=ref,
        ref_detail_id=detail,
        ref_date=datetime(2024, 1, 10),
        posted_date=datetime(2024, 1, 10),
        entity_id=entity,
        account_object_id=None,
        stock_id=None,
        unit_id=None,
        quantity=Decimal("5"),
        unit_price=Decimal("100000"),
        amount=Decimal("500000"),
    )


def build_graph(entity, prefix, with_link=True, extra=False):
    graph = AccountingGraph(
        entities={
            entity: InventoryEntity(
                entity_id=entity,
                item_type=1,
                unit_name="cái",
            )
        }
    )
    order = line(
        "PU_ORDER", entity, f"{prefix}-po", f"{prefix}-po-d", 5
    )
    voucher = line(
        "PU_VOUCHER",
        entity,
        f"{prefix}-pv",
        f"{prefix}-pv-d",
        5,
        source_detail=(f"{prefix}-po-d" if with_link else None),
    )
    graph.events.extend([order, voucher])
    graph.inventory_ledger.append(
        posting(
            "INVENTORY_LEDGER",
            entity,
            voucher.ref_id,
            voucher.ref_detail_id,
        )
    )
    graph.general_ledger.append(
        posting(
            "GENERAL_LEDGER",
            entity,
            voucher.ref_id,
            voucher.ref_detail_id,
        )
    )
    if extra:
        graph.events.append(
            line(
                "IN_INWARD",
                entity,
                f"{prefix}-extra",
                f"{prefix}-extra-d",
                1,
            )
        )
    return graph


def test_workflow_comparator_follows_internal_item_id_and_explicit_link():
    master_graph = build_graph("answer-item", "a")
    student_graph = build_graph("student-item", "s")
    context = GradingContext(
        master_graph=master_graph,
        student_graph=student_graph,
        entity_resolution=EntityResolution(
            mapping={"answer-item": "student-item"}
        ),
    )

    result = TransactionsComparator().compare(
        MasterAnswerData(accounting_graph=master_graph),
        {"_grading_context": context},
    )

    assert result.match_ratio == 1.0
    assert all(detail["match"] for detail in result.details)


def test_missing_inheritance_link_is_detected_when_amount_is_correct():
    master_graph = build_graph("answer-item", "a")
    student_graph = build_graph("student-item", "s", with_link=False)
    context = GradingContext(
        master_graph=master_graph,
        student_graph=student_graph,
        entity_resolution=EntityResolution(
            mapping={"answer-item": "student-item"}
        ),
    )

    result = TransactionsComparator().compare(
        MasterAnswerData(accounting_graph=master_graph),
        {"_grading_context": context},
    )

    voucher_detail = next(
        detail
        for detail in result.details
        if detail.get("module_role") == "PU_VOUCHER"
    )
    assert voucher_detail["checks"]["explicit_source_link"] is False
    assert result.match_ratio < 1.0


def test_extra_workflow_event_is_penalized():
    master_graph = build_graph("answer-item", "a")
    student_graph = build_graph("student-item", "s", extra=True)
    context = GradingContext(
        master_graph=master_graph,
        student_graph=student_graph,
        entity_resolution=EntityResolution(
            mapping={"answer-item": "student-item"}
        ),
    )

    result = TransactionsComparator().compare(
        MasterAnswerData(accounting_graph=master_graph),
        {"_grading_context": context},
    )

    assert any(
        detail.get("checks") == {"extra_event": False}
        for detail in result.details
    )
    assert result.match_ratio < 1.0
