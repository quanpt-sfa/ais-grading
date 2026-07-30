from typing import Dict, Any, List
import logging
from dataclasses import dataclass, field

from src.db.connection import DatabaseConnection
from src.db.queries import QueryRepository
from src.answer.loader import AnswerLoader
from src.answer.models import MasterAnswerData
from src.student.manager import StudentInfo, StudentManager
from src.student.extractor import StudentDataExtractor
from src.grading.registry import ModuleRegistry
from src.grading.scoring import ScoringEngine
from src.grading.traceability import TraceabilityChecker
from src.grading.base import CompareResult

logger = logging.getLogger(__name__)

@dataclass
class StudentGradeReport:
    student: StudentInfo
    score_data: Dict[str, Any]
    traceability_data: Dict[str, Any]
    module_results: Dict[str, CompareResult]

class GradingEngine:
    """Engine chính thực hiện quá trình chấm điểm toàn bộ sinh viên."""

    def __init__(self, config: Dict[str, Any], db_conn: DatabaseConnection, query_repo: QueryRepository):
        self.config = config
        self.db_conn = db_conn
        self.query_repo = query_repo
        
        # Initialize registry & auto-discover modules
        ModuleRegistry.auto_discover()
        
        self.weights = config.get("scoring", {}).get("weights", {})
        self.active_modules = ModuleRegistry.get_active_modules(self.weights)
        self.tolerance = float(config.get("scoring", {}).get("tolerance", 1.0))

        self.answer_loader = AnswerLoader(db_conn, query_repo, config)
        self.extractor = StudentDataExtractor(db_conn, query_repo)
        self.scorer = ScoringEngine(config)
        self.traceability = TraceabilityChecker(db_conn, query_repo)

        self.master_answer: MasterAnswerData = None

    def initialize(self):
        """Load đáp án trước khi thực hiện chấm."""
        logger.info("Khởi tạo GradingEngine và load đáp án master...")
        self.master_answer = self.answer_loader.load()

    def grade_student(self, student: StudentInfo) -> StudentGradeReport:
        """Chấm điểm bài làm của 1 sinh viên."""
        if not self.master_answer:
            self.initialize()

        logger.info(f"Đang chấm điểm sinh viên: {student.student_id} ({student.last_name} {student.first_name})...")
        
        # 1. Extract student data
        st_data = self.extractor.extract_all_data(student)

        # 2. Run active module comparators
        module_results = {}
        for comp in self.active_modules:
            try:
                res = comp.compare(self.master_answer, st_data, self.tolerance)
                module_results[comp.module_id] = res
            except Exception as e:
                logger.error(f"Lỗi khi so khớp module [{comp.module_id}] cho SV [{student.student_id}]: {e}")
                module_results[comp.module_id] = CompareResult(
                    module_id=comp.module_id,
                    display_name=comp.display_name,
                    total_items=1,
                    matched_items=0,
                    match_ratio=0.0,
                    details=[{"error": str(e)}]
                )

        # 3. Calculate score
        score_data = self.scorer.calculate_scores(module_results)

        # 4. Traceability check
        trace_data = {}
        if self.config.get("traceability", {}).get("enabled", True):
            trace_data = self.traceability.trace_student_vouchers(student.db_name)

        return StudentGradeReport(
            student=student,
            score_data=score_data,
            traceability_data=trace_data,
            module_results=module_results
        )

    def grade_all_students(self, students: List[StudentInfo]) -> List[StudentGradeReport]:
        """Chấm điểm danh sách tất cả sinh viên."""
        self.initialize()
        reports = []
        for st in students:
            report = self.grade_student(st)
            reports.append(report)
        return reports
