from decimal import Decimal
from src.answer.models import (
    MasterAnswerData,
    InventoryAnswer,
    FixedAssetAnswer,
    FixedAssetItem,
    GeneralBalanceAnswer,
    AccountBalanceItem,
    TransactionAnswer,
    TransactionItem,
    FinancialReportAnswer,
    FinancialReportLine,
)
from src.grading.modules.inventory_balance import InventoryBalanceComparator
from src.grading.modules.fixed_asset_balance import FixedAssetBalanceComparator
from src.grading.modules.general_balance import GeneralBalanceComparator
from src.grading.modules.transactions import TransactionsComparator
from src.grading.modules.financial_reports import FinancialReportsComparator


def test_inventory_comparator_match():
    master = MasterAnswerData(
        inventory=InventoryAnswer(
            item_count=8,
            total_qty=Decimal("45180"),
            total_amount=Decimal("3043750000"),
        )
    )
    st_data = {
        "inventory_balance": [
            {
                "item_count": 8,
                "total_qty": 45180,
                "total_amount": 3043750000,
            }
        ]
    }
    comp = InventoryBalanceComparator()
    res = comp.compare(master, st_data)
    assert res.match_ratio == 1.0
    assert res.matched_items == 3


def test_general_balance_comparator_match():
    master = MasterAnswerData(
        general_balance=GeneralBalanceAnswer(
            accounts={
                "111": AccountBalanceItem(
                    account_code="111",
                    debit_amount=Decimal("1000"),
                    credit_amount=Decimal("0"),
                ),
                "112": AccountBalanceItem(
                    account_code="112",
                    debit_amount=Decimal("2000"),
                    credit_amount=Decimal("0"),
                ),
            }
        )
    )
    st_data = {
        "general_balance": [
            {"AccountCode": "111", "DebitAmount": 1000, "CreditAmount": 0},
            {"AccountCode": "112", "DebitAmount": 2000, "CreditAmount": 0},
        ]
    }
    comp = GeneralBalanceComparator()
    res = comp.compare(master, st_data)
    assert res.match_ratio == 1.0
    assert res.matched_items == 2


def test_transactions_comparator_mismatch():
    master = MasterAnswerData(
        transactions=TransactionAnswer(
            entries={
                (103, "133"): TransactionItem(
                    task_id=103,
                    account_code="133",
                    amount=Decimal("38840000"),
                )
            }
        )
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


def report_line(detail_id, amount):
    return FinancialReportLine(
        report_detail_id=detail_id,
        report_ref_id="answer-report",
        report_type="1",
        item_id=None,
        item_code="100",
        item_index=1,
        sort_order=1,
        category=0,
        formula_type=0,
        amount=Decimal(str(amount)),
        prev_amount=Decimal("0"),
        report_ref_type=100,
        display_on_book=0,
        period=12,
        year=2024,
        from_date="2024-01-01",
        to_date="2024-12-31",
        currency_id="VND",
    )


def test_financial_report_duplicate_codes_are_preserved_as_multiset():
    master = MasterAnswerData(
        financial_reports=FinancialReportAnswer(
            lines=[
                report_line("answer-detail-1", 10),
                report_line("answer-detail-2", 20),
            ]
        )
    )
    student = {
        "financial_reports": [
            {
                "ReportDetailID": "student-detail-2",
                "RefID": "student-report",
                "ReportType": "1",
                "ItemCode": "100",
                "ItemIndex": 1,
                "SortOrder": 1,
                "Category": 0,
                "FormulaType": 0,
                "Amount": 20,
                "ReportRefType": 100,
                "DisplayOnBook": 0,
                "Period": 12,
                "Year": 2024,
                "FromDate": "2024-01-01",
                "ToDate": "2024-12-31",
                "CurrencyID": "VND",
            },
            {
                "ReportDetailID": "student-detail-1",
                "RefID": "student-report",
                "ReportType": "1",
                "ItemCode": "100",
                "ItemIndex": 1,
                "SortOrder": 1,
                "Category": 0,
                "FormulaType": 0,
                "Amount": 10,
                "ReportRefType": 100,
                "DisplayOnBook": 0,
                "Period": 12,
                "Year": 2024,
                "FromDate": "2024-01-01",
                "ToDate": "2024-12-31",
                "CurrencyID": "VND",
            },
        ]
    }

    result = FinancialReportsComparator().compare(master, student)

    assert result.total_items == 2
    assert result.matched_items == 2
    assert result.match_ratio == 1.0
