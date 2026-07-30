from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List

@dataclass
class CompareResult:
    """Kết quả so khớp của 1 phân hệ."""
    module_id: str             # Mã phân hệ ("inventory_balance")
    display_name: str          # Tên hiển thị ("Số dư hàng tồn kho")
    total_items: int           # Tổng số mục cần so khớp
    matched_items: int         # Số mục khớp đúng
    match_ratio: float         # Tỷ lệ đúng (0.0 đến 1.0)
    score: float = 0.0         # Điểm số quy đổi
    max_weight: float = 0.0    # Trọng số tối đa của phân hệ này
    details: List[Dict[str, Any]] = field(default_factory=list) # Chi tiết so sánh từng mục
    raw_answer: Any = None
    raw_student: Any = None

class BaseModuleComparator(ABC):
    """Base class cho tất cả các phân hệ chấm điểm (Plugin Pattern)."""

    @property
    @abstractmethod
    def module_id(self) -> str:
        """ID duy nhất của phân hệ (khớp với key trong yaml/config)."""
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Tên tiếng Việt hiển thị của phân hệ."""
        pass

    @abstractmethod
    def compare(self, master_answer: Any, student_data: Any, tolerance: float = 1.0) -> CompareResult:
        """So khớp dữ liệu bài làm sinh viên với đáp án."""
        pass
