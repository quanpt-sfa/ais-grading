from decimal import Decimal

import pytest

from src.answer.models import (
    AccountingEventLine,
    AccountingGraph,
    InventoryEntity,
    OpeningInventoryLine,
)
from src.answer.validator import AnswerValidationError, AnswerValidator


def valid_graph():
    graph = AccountingGraph()
    graph.entities["item-1"] = InventoryEntity(entity_id="item-1")
    graph.opening_balances.append(
        OpeningInventoryLine(
            entity_id="item-1",
            stock_id=None,
            unit_id=None,
            quantity=Decimal("10"),
            unit_price=Decimal("100"),
            amount=Decimal("1000"),
        )
    )
    graph.events.extend(
        [
            AccountingEventLine(
                module_role="PU_ORDER",
                ref_type=1,
                ref_id="po",
                ref_detail_id="po-line",
                ref_date="2024-01-01",
                posted_date=None,
                entity_id="item-1",
                account_object_id=None,
                stock_id=None,
                unit_id=None,
                quantity=Decimal("5"),
                unit_price=Decimal("100"),
                amount=Decimal("500"),
            ),
            AccountingEventLine(
                module_role="PU_VOUCHER",
                ref_type=2,
                ref_id="pv",
                ref_detail_id="pv-line",
                ref_date="2024-01-02",
                posted_date=None,
                entity_id="item-1",
                account_object_id=None,
                stock_id=None,
                unit_id=None,
                quantity=Decimal("5"),
                unit_price=Decimal("100"),
                amount=Decimal("500"),
                source_ref_id="po",
                source_ref_detail_id="po-line",
                is_posted_finance=False,
                is_posted_inventory=False,
            ),
        ]
    )
    return graph


def test_partial_answer_query_set_is_rejected():
    validator = AnswerValidator(
        {
            "required_queries": ["catalog_inventory_items", "financial_reports"],
            "required_workflow_roles": ["PU_ORDER", "PU_VOUCHER"],
        }
    )
    raw = {"catalog_inventory_items": [{"InventoryItemID": "item-1"}]}

    with pytest.raises(AnswerValidationError, match="required answer query"):
        validator.assert_valid(
            raw,
            valid_graph(),
            {"start": "2024-01-01", "end": "2024-12-31"},
        )


def test_duplicate_report_key_is_rejected():
    validator = AnswerValidator(
        {
            "required_queries": ["financial_reports"],
            "required_workflow_roles": ["PU_ORDER", "PU_VOUCHER"],
        }
    )
    raw = {
        "financial_reports": [
            {"ReportType": 1, "ItemCode": "A", "Amount": 10},
            {"ReportType": 1, "ItemCode": "A", "Amount": 20},
        ]
    }

    with pytest.raises(AnswerValidationError, match="duplicate canonical key"):
        validator.assert_valid(
            raw,
            valid_graph(),
            {"start": "2024-01-01", "end": "2024-12-31"},
        )
