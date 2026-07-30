from typing import Dict, Any, List, Optional
import logging
from dataclasses import dataclass

from src.db.connection import DatabaseConnection
from src.db.queries import QueryRepository
from src.answer.loader import AnswerLoader
from src.answer.models import MasterAnswerData
from src.student.manager import StudentInfo
from src.student.extractor import StudentDataExtractor
from src.grading.registry import ModuleRegistry
from src.grading.scoring import ScoringEngine
from src.grading.traceability import TraceabilityChecker
from src.grading.base import CompareResult
from src.grading.accounting_graph import AccountingGraphBuilder
from src.grading.context import GradingContext
from src.grading.entity_resolution import EntityResolution, EntityResolver

logger = logging.getLogger(__name__)


@dataclass
class StudentGradeReport:
    student: StudentInfo
    score_data: Dict[str, Any]
    traceability_data: Dict[str, Any]
    module_results: Dict[str, CompareResult]
    answer_snapshot_id: Optional[str] = None


class GradingEngine:
    """Engine chính thực hiện quá trình chấm điểm toàn bộ sinh viên."""

    def __init__(
        self,
        config: Dict[str, Any],
        db_conn: DatabaseConnection,
        query_repo: QueryRepository,
    ):
        self.config = config
        self.db_conn = db_conn
        self.query_repo = query_repo

        ModuleRegistry.auto_discover()

        self.weights = config.get("scoring", {}).get("weights", {})
        self.active_modules = ModuleRegistry.get_active_modules(self.weights)
        self.tolerance = float(
            config.get("scoring", {}).get("tolerance", 1.0)
        )

        self.answer_loader = AnswerLoader(db_conn, query_repo, config)
        self.extractor = StudentDataExtractor(db_conn, query_repo)
        self.scorer = ScoringEngine(config)
        self.traceability = TraceabilityChecker(db_conn, query_repo)
        self.graph_builder = AccountingGraphBuilder()
        self.entity_resolver = EntityResolver(
            config.get("entity_matching", {})
        )

        self.master_answer: Optional[MasterAnswerData] = None

    def initialize(self):
        """Load và khóa một answer snapshot trước khi chấm."""
        logger.info("Khởi tạo GradingEngine và khóa answer snapshot...")
        self.master_answer = self.answer_loader.load()
        if not self.master_answer.answer_snapshot_id:
            raise RuntimeError(
                "AnswerLoader returned no immutable answer_snapshot_id"
            )
        if not self.master_answer.accounting_graph:
            raise RuntimeError(
                "Canonical answer snapshot contains no AccountingGraph"
            )

    def grade_student(self, student: StudentInfo) -> StudentGradeReport:
        if not self.master_answer:
            self.initialize()
        assert self.master_answer is not None

        logger.info(
            "Đang chấm điểm sinh viên: %s (%s %s) với snapshot %s...",
            student.student_id,
            student.last_name,
            student.first_name,
            self.master_answer.answer_snapshot_id,
        )

        student_data = self.extractor.extract_all_data(student)
        student_graph = self.graph_builder.build(student_data)
        resolution: EntityResolution = self.entity_resolver.resolve(
            self.master_answer.accounting_graph,
            student_graph,
        )
        student_data["_grading_context"] = GradingContext(
            master_graph=self.master_answer.accounting_graph,
            student_graph=student_graph,
            entity_resolution=resolution,
        )

        module_results = {}
        for comparator in self.active_modules:
            try:
                result = comparator.compare(
                    self.master_answer,
                    student_data,
                    self.tolerance,
                )
                module_results[comparator.module_id] = result
            except Exception as exc:
                logger.error(
                    "Lỗi khi so khớp module [%s] cho SV [%s]: %s",
                    comparator.module_id,
                    student.student_id,
                    exc,
                )
                module_results[comparator.module_id] = CompareResult(
                    module_id=comparator.module_id,
                    display_name=comparator.display_name,
                    total_items=1,
                    matched_items=0,
                    match_ratio=0.0,
                    details=[{"error": str(exc)}],
                )

        score_data = self.scorer.calculate_scores(module_results)

        trace_data: Dict[str, Any] = {}
        if self.config.get("traceability", {}).get("enabled", True):
            trace_data = self.traceability.trace_student_vouchers(
                student.db_name
            )

        trace_data["answer_snapshot"] = {
            "snapshot_id": self.master_answer.answer_snapshot_id,
            "answer_version": self.master_answer.answer_version,
            "data_hash": self.master_answer.answer_data_hash,
            "snapshot_path": self.master_answer.answer_snapshot_path,
            "validation": self.master_answer.validation_report,
        }
        trace_data["entity_resolution"] = {
            "mapped_count": len(resolution.mapping),
            "missing_answer_entities": resolution.missing_answer_entities,
            "extra_student_entities": resolution.extra_student_entities,
            "ambiguous_candidates": resolution.ambiguous_candidates,
            "mapping": resolution.mapping,
        }

        return StudentGradeReport(
            student=student,
            score_data=score_data,
            traceability_data=trace_data,
            module_results=module_results,
            answer_snapshot_id=self.master_answer.answer_snapshot_id,
        )

    def grade_all_students(
        self,
        students: List[StudentInfo],
    ) -> List[StudentGradeReport]:
        self.initialize()
        return [self.grade_student(student) for student in students]
