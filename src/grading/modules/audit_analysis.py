from typing import Any, Dict, List
from src.grading.base import BaseModuleComparator, CompareResult
from src.answer.models import MasterAnswerData

class AuditAnalysisComparator(BaseModuleComparator):
    """Phân hệ 6: Phân tích nhật ký thao tác (MSC_AudittingLog) của sinh viên."""

    @property
    def module_id(self) -> str:
        return "audit_analysis"

    @property
    def display_name(self) -> str:
        return "Phân tích nhật ký thao tác (Audit Log)"

    def compare(self, master_answer: MasterAnswerData, student_data: Dict[str, Any], tolerance: float = 1.0) -> CompareResult:
        timeline = student_data.get("audit_timeline", [])
        workflow = student_data.get("audit_workflow_steps", [])
        forensics = student_data.get("audit_computer_forensics", [])
        compliance = student_data.get("audit_process_compliance", [])

        total_actions = len(timeline)
        total_voucher_actions = len(workflow)
        computers = [f"{f.get('ComputerName')} ({f.get('ComputerIP')})" for f in forensics if f.get('ComputerName')]

        # Check basic compliance metrics
        has_login = any(t.get("PermissionTypeAlias") == "Đăng nhập" for t in timeline)
        has_post = any(t.get("PermissionTypeAlias") in ["Ghi sổ", "Thêm"] for t in workflow)
        single_pc = (len(forensics) <= 1)

        details = [
            {
                "metric": "Tổng số thao tác đã ghi nhận",
                "value": total_actions,
                "info": f"Gồm {total_voucher_actions} thao tác chứng từ (thêm/sửa/ghi sổ)"
            },
            {
                "metric": "Danh sách máy tính làm bài",
                "value": len(computers),
                "details": computers,
                "normal": single_pc
            },
            {
                "metric": "Kiểm tra luồng tạo chứng từ",
                "value": "Hợp lệ" if has_post else "Thiếu chứng từ ghi sổ",
                "normal": has_post
            }
        ]

        # Calculate a quality/compliance score ratio (1.0 if actions exist and single PC used)
        ratio = 1.0 if (total_actions > 0 and has_post) else 0.5 if total_actions > 0 else 0.0

        return CompareResult(
            module_id=self.module_id,
            display_name=self.display_name,
            total_items=max(total_actions, 1),
            matched_items=total_actions if ratio > 0 else 0,
            match_ratio=ratio,
            details=details,
            raw_answer=None,
            raw_student={"action_count": total_actions, "computers": computers}
        )
