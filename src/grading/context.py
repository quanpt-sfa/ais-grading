from dataclasses import dataclass

from src.answer.models import AccountingGraph
from src.grading.entity_resolution import EntityResolution


@dataclass(frozen=True)
class GradingContext:
    master_graph: AccountingGraph
    student_graph: AccountingGraph
    entity_resolution: EntityResolution
