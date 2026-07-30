"""Public answer API with lazy imports to avoid package import cycles."""

__all__ = [
    "MasterAnswerData",
    "InventoryAnswer",
    "FixedAssetAnswer",
    "FixedAssetItem",
    "GeneralBalanceAnswer",
    "AccountBalanceItem",
    "TransactionAnswer",
    "TransactionItem",
    "FinancialReportAnswer",
    "AnswerLoader",
    "AnswerSnapshot",
    "AnswerSnapshotStore",
    "AnswerValidator",
]


def __getattr__(name):
    model_names = {
        "MasterAnswerData",
        "InventoryAnswer",
        "FixedAssetAnswer",
        "FixedAssetItem",
        "GeneralBalanceAnswer",
        "AccountBalanceItem",
        "TransactionAnswer",
        "TransactionItem",
        "FinancialReportAnswer",
    }
    if name in model_names:
        from src.answer import models

        return getattr(models, name)
    if name == "AnswerLoader":
        from src.answer.loader import AnswerLoader

        return AnswerLoader
    if name in {"AnswerSnapshot", "AnswerSnapshotStore"}:
        from src.answer import snapshot

        return getattr(snapshot, name)
    if name == "AnswerValidator":
        from src.answer.validator import AnswerValidator

        return AnswerValidator
    raise AttributeError(name)
