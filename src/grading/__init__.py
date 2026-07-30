"""Public grading API with lazy imports to avoid circular dependencies."""

__all__ = [
    "BaseModuleComparator",
    "CompareResult",
    "ModuleRegistry",
    "ScoringEngine",
    "TraceabilityChecker",
    "GradingEngine",
    "StudentGradeReport",
]


def __getattr__(name):
    if name in {"BaseModuleComparator", "CompareResult"}:
        from src.grading import base

        return getattr(base, name)
    if name == "ModuleRegistry":
        from src.grading.registry import ModuleRegistry

        return ModuleRegistry
    if name == "ScoringEngine":
        from src.grading.scoring import ScoringEngine

        return ScoringEngine
    if name == "TraceabilityChecker":
        from src.grading.traceability import TraceabilityChecker

        return TraceabilityChecker
    if name in {"GradingEngine", "StudentGradeReport"}:
        from src.grading import engine

        return getattr(engine, name)
    raise AttributeError(name)
