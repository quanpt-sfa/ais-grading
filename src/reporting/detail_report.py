from pathlib import Path
from typing import Dict, Any
import logging
from src.grading.engine import StudentGradeReport

logger = logging.getLogger(__name__)

class DetailReportExporter:
    """Xuất báo cáo chi tiết kết quả bài làm của từng sinh viên dưới dạng Markdown."""

    def __init__(self, output_dir: str = "output/details"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def export_student_detail(self, report: StudentGradeReport) -> str:
        st = report.student
        file_path = self.output_dir / f"ChiTiet_{st.student_id}.md"

        md = []
        md.append(f"# Báo cáo Chi tiết Bài thi - MSSV: {st.student_id}")
        md.append(f"- **Họ và tên**: {st.last_name} {st.first_name}")
        md.append(f"- **Máy tính làm bài**: {st.computer_name or 'N/A'}")
        md.append(f"- **Tổng điểm kết luận**: `{report.score_data.get('final_score', 0.0)} / {report.score_data.get('scale', 10)}`")
        md.append(f"- **Tổng trọng số tích lũy**: `{report.score_data.get('weighted_total', 0.0)} / {report.score_data.get('total_weight', 100)}`\n")

        md.append("## 1. Kết quả theo từng phân hệ\n")
        md.append("| Phân hệ | Điểm đạt được | Trọng số tối đa | Tỷ lệ khớp | Trạng thái |")
        md.append("|---|---|---|---|---|")

        for mid, res in report.module_results.items():
            status = "✅ Hoàn hảo" if res.match_ratio >= 0.99 else "⚠️ Sai sót" if res.match_ratio > 0 else "❌ Không có/Sai"
            md.append(f"| {res.display_name} | {res.score:.2f} | {res.max_weight:.1f} | {res.match_ratio * 100:.1f}% | {status} |")

        md.append("\n## 2. Phân tích chi tiết so khớp\n")

        for mid, res in report.module_results.items():
            md.append(f"### 📍 {res.display_name}")
            md.append(f"- Số mục đạt: **{res.matched_items} / {res.total_items}**\n")

            if res.details:
                md.append("```json")
                import json
                md.append(json.dumps(res.details, ensure_ascii=False, indent=2))
                md.append("```\n")

        # Traceability & Audit Section
        if report.traceability_data:
            md.append("## 3. Theo vết quy trình nghiệp vụ (Traceability)\n")
            md.append("```json")
            import json
            md.append(json.dumps(report.traceability_data, ensure_ascii=False, indent=2))
            md.append("```\n")

        with open(file_path, "w", encoding="utf-8") as f:
            f.write("\n".join(md))

        logger.info(f"Đã xuất báo cáo chi tiết cho SV [{st.student_id}] tại: {file_path}")
        return str(file_path)
