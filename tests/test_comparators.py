from decimal import Decimal
from src.answer.models import (
    MasterAnswerData, InventoryAnswer, FixedAssetAnswer, FixedAssetItem,
    GeneralBalanceAnswer, AccountBalanceItem, TransactionAnswer, TransactionItem,
    FinancialReportAnswer
)
from src.grading.modules.inventory_balance import InventoryBalanceComparator
from src.grading.modules.fixed_asset_balance import FixedAssetBalanceComparator
from src.grading.modules.general_balance import GeneralBalanceComparator
from src.grading.modules.transactions import TransactionsComparator
from src.grading.modules.financial_reports import FinancialReportsComparator

def test_inventory_comparator_match():
    master = MasterAnswerData(
        inventory=InventoryAnswer(item_count=8, total_qty=Decimal('45180'), total_amount=Decimal('3043750000'))
    )
    st_data = {
        "inventory_balance": [{"item_count": 8, "total_qty": 45180, "total_amount": 3043750000}]
    }
    comp = InventoryBalanceComparator()
    res = comp.compare(master, st_data)
    assert res.match_ratio == 1.0
    assert res.matched_items == 3

def test_general_balance_comparator_match():
    master = MasterAnswerData(
        general_balance=GeneralBalanceAnswer(accounts={
            "111": AccountBalanceItem(account_code="111", debit_amount=Decimal("1000"), credit_amount=Decimal("0")),
            "112": AccountBalanceItem(account_code="112", debit_amount=Decimal("2000"), credit_amount=Decimal("0"))
        })
    )
    st_data = {
        "general_balance": [
            {"AccountCode": "111", "DebitAmount": 1000, "CreditAmount": 0},
            {"AccountCode": "112", "DebitAmount": 2000, "CreditAmount": 0}
        ]
    }
    comp = GeneralBalanceComparator()
    res = comp.compare(master, st_data)
    assert res.match_ratio == 1.0
    assert res.matched_items == 2

def test_transactions_comparator_mismatch():
    master = MasterAnswerData(
        transactions=TransactionAnswer(entries={
            (103, "133"): TransactionItem(task_id=103, account_code="133", amount=Decimal("38840000"))
        })
    )
    st_data = {
        "transactions": [
            {"TaskID": 103, "AccountCode": "133", "Amount": 1000000}
        ]
    }
    comp = TransactionsComparator()
    res = comp.compare(master, st_data)
    assert res.match_ratio == 0.0
    assert res.matched_items == 0
