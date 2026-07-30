from pathlib import Path

import yaml

from src.answer.loader import AnswerLoader


QUERY_IDS = AnswerLoader.CANONICAL_QUERY_IDS


class FakeDatabaseConnection:
    def __init__(self, raw):
        self.raw = raw
        self.batch_calls = 0

    def database_exists(self, database):
        return True

    def execute_query_batch(self, database, queries, isolation_level):
        self.batch_calls += 1
        assert set(queries) == set(QUERY_IDS)
        assert isolation_level == "SERIALIZABLE"
        return self.raw


class UnusedStudentQueryRepository:
    pass


def raw_answer():
    empty = {query_id: [] for query_id in QUERY_IDS}
    empty.update(
        {
            "catalog_inventory_items": [
                {
                    "InventoryItemID": "item-1",
                    "InventoryItemType": 1,
                    "UnitName": "cái",
                    "InventoryItemCode": "A",
                    "InventoryItemName": "Hàng A",
                }
            ],
            "opening_inventory_lines": [
                {
                    "InventoryItemID": "item-1",
                    "StockID": None,
                    "CanonicalUnitID": None,
                    "OpeningQuantity": 10,
                    "OpeningUnitPrice": 100,
                    "OpeningAmount": 1000,
                }
            ],
            "purchase_order_lines": [
                {
                    "ModuleRole": "PU_ORDER",
                    "RefType": 1,
                    "RefID": "po",
                    "RefDetailID": "po-line",
                    "RefDate": "2024-01-01",
                    "InventoryItemID": "item-1",
                    "Quantity": 5,
                    "UnitPrice": 100,
                    "Amount": 500,
                }
            ],
            "purchase_voucher_lines": [
                {
                    "ModuleRole": "PU_VOUCHER",
                    "RefType": 2,
                    "RefID": "pv",
                    "RefDetailID": "pv-line",
                    "RefDate": "2024-01-02",
                    "InventoryItemID": "item-1",
                    "Quantity": 5,
                    "UnitPrice": 100,
                    "Amount": 500,
                    "SourceRefID": "po",
                    "SourceRefDetailID": "po-line",
                    "IsPostedFinance": False,
                    "IsPostedInventory": False,
                }
            ],
            "financial_reports": [
                {
                    "ReportDetailID": "report-detail-1",
                    "RefID": "report-1",
                    "ReportType": "1",
                    "ItemID": "item-report-1",
                    "ItemCode": "100",
                    "ItemIndex": 1,
                    "SortOrder": 1,
                    "Category": 0,
                    "FormulaType": 0,
                    "Amount": 1000,
                    "PrevAmount": 900,
                    "OtherAmount": 0,
                    "OtherPrevAmount": 0,
                    "ReportRefType": 100,
                    "DisplayOnBook": 0,
                    "Period": 12,
                    "Year": 2024,
                    "FromDate": "2024-01-01",
                    "ToDate": "2024-12-31",
                    "CurrencyID": "VND",
                }
            ],
        }
    )
    return empty


def write_queries(path: Path):
    path.write_text(
        yaml.safe_dump(
            {query_id: {"query": "SELECT 1"} for query_id in QUERY_IDS}
        ),
        encoding="utf-8",
    )


def test_master_is_extracted_once_and_gets_snapshot_id(tmp_path):
    query_file = tmp_path / "answer_queries.yaml"
    write_queries(query_file)
    db = FakeDatabaseConnection(raw_answer())
    config = {
        "answer": {
            "source": "database",
            "database": "AnswerDB",
            "version": "v1",
            "strict": True,
            "query_file": str(query_file),
            "snapshot_directory": str(tmp_path / "snapshots"),
            "transaction_isolation": "SERIALIZABLE",
            "scope": {"start": "2024-01-01", "end": "2024-12-31"},
            "validation": {
                "required_workflow_roles": ["PU_ORDER", "PU_VOUCHER"],
                "require_postings": True,
                "require_financial_reports": True,
            },
        }
    }

    master = AnswerLoader(db, UnusedStudentQueryRepository(), config).load()

    assert db.batch_calls == 1
    assert master.answer_snapshot_id
    assert master.answer_data_hash
    assert master.inventory.item_count == 1
    assert master.inventory.total_qty == 10
    assert len(master.financial_reports.lines) == 1
    assert master.financial_reports.lines[0].report_detail_id == "report-detail-1"
    assert Path(master.answer_snapshot_path).exists()
