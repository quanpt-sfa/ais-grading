import re
import openpyxl
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import logging
from pathlib import Path
from src.db.connection import DatabaseConnection

logger = logging.getLogger(__name__)

@dataclass
class StudentInfo:
    student_id: str                          # MSSV (VD: 23729351)
    last_name: str = ""                       # Họ & tên lót (VD: Phan Hữu)
    first_name: str = ""                      # Tên (VD: Thoại)
    class_name: str = ""                      # Lớp (VD: DHKTKT18A / Mr Quân)
    exam_room: str = ""                       # Phòng thi (VD: B17, B15)
    assigned_pc: str = ""                     # Máy phân công (VD: B17M05 / TTMP,12\TH)
    computer_name: str = ""                   # Số máy làm bài thực tế (từ audit log)
    db_name: str = ""                         # Tên database SQL (thường = MSSV)
    mdf_path: Optional[str] = None            # Đường dẫn file .mdf nếu attach ngoài
    is_attached: bool = True                  # Trạng thái DB có trên SQL Server hay chưa

    @property
    def full_name(self) -> str:
        return f"{self.last_name} {self.first_name}".strip() or self.student_id

    @property
    def has_pc_mismatch(self) -> bool:
        """Kiểm tra sinh viên có ngồi làm bài lệch máy phân công hay không."""
        if not self.assigned_pc or not self.computer_name:
            return False
        # Normalize strings for comparison
        clean_assigned = re.sub(r'[^a-zA-Z0-9]', '', self.assigned_pc).upper()
        clean_actual = re.sub(r'[^a-zA-Z0-9]', '', self.computer_name).upper()
        return clean_assigned not in clean_actual and clean_actual not in clean_assigned

class StudentManager:
    """Quản lý danh sách, phân công phòng thi, máy làm bài và database sinh viên."""

    def __init__(self, db_conn: DatabaseConnection, config: Dict[str, Any]):
        self.db_conn = db_conn
        self.config = config
        self.student_config = config.get("student", {})
        self.source = self.student_config.get("source", "scan")
        self.excel_file = self.student_config.get("excel_file", "d12.xlsm")
        self.excel_sheet = self.student_config.get("excel_sheet_list", "DanhSach")
        self.db_pattern = re.compile(self.student_config.get("db_name_pattern", r"^\d{8}$"))
        self._custom_students: Optional[List[StudentInfo]] = None

    def set_custom_student_list(self, students: List[StudentInfo]):
        """Thiết lập danh sách sinh viên thủ công hoặc từ Excel upload qua GUI."""
        self._custom_students = students

    def get_students(self, custom_excel_path: Optional[str] = None) -> List[StudentInfo]:
        """Lấy danh sách sinh viên cùng phân công phòng thi và số máy."""
        if self._custom_students is not None:
            return self._custom_students

        target_excel = custom_excel_path or self.excel_file

        if self.source == "excel" and Path(target_excel).exists():
            return self._get_students_from_excel(target_excel)
        else:
            students = self._scan_students_from_sql()
            if not students and Path(target_excel).exists():
                logger.info(f"Không tìm thấy DB sinh viên trên SQL Server. Tự động đọc từ Excel: {target_excel}")
                return self._get_students_from_excel(target_excel)
            return students

    def _scan_students_from_sql(self) -> List[StudentInfo]:
        """Quét tất cả database có tên là MSSV (8 chữ số) trên SQL Server."""
        logger.info("Quét danh sách DB sinh viên trên SQL Server...")
        all_dbs = self.db_conn.list_databases()
        students = []
        for db in all_dbs:
            if self.db_pattern.match(db):
                st = StudentInfo(
                    student_id=db,
                    db_name=db,
                    is_attached=True
                )
                students.append(st)
        logger.info(f"Tìm thấy {len(students)} DB sinh viên khớp mẫu {self.db_pattern.pattern}")
        return students

    def _get_students_from_excel(self, excel_path: str) -> List[StudentInfo]:
        """Đọc danh sách sinh viên, phòng thi, và máy phân công từ file Excel."""
        logger.info(f"Đọc danh sách sinh viên từ Excel: {excel_path} (sheet '{self.excel_sheet}')")
        wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
        if self.excel_sheet not in wb.sheetnames:
            wb.close()
            return self._scan_students_from_sql()

        ws = wb[self.excel_sheet]
        
        # Read header info (Lớp học)
        class_name = ""
        c3_val = ws['C3'].value
        if c3_val:
            class_name = str(c3_val).strip()

        students = []
        for r in range(7, ws.max_row + 1):
            mssv = ws[f'B{r}'].value
            if mssv and str(mssv).strip().isdigit():
                sid = str(mssv).strip()
                last_name = str(ws[f'C{r}'].value or "").strip()
                first_name = str(ws[f'D{r}'].value or "").strip()
                assigned_pc = str(ws[f'E{r}'].value or "").strip()

                students.append(StudentInfo(
                    student_id=sid,
                    last_name=last_name,
                    first_name=first_name,
                    class_name=class_name,
                    assigned_pc=assigned_pc,
                    db_name=sid,
                    is_attached=self.db_conn.database_exists(sid)
                ))
        wb.close()
        return students

    def ensure_database_ready(self, student: StudentInfo) -> bool:
        """Đảm bảo database của SV có sẵn để truy vấn."""
        if self.db_conn.database_exists(student.db_name):
            student.is_attached = True
            return True
            
        if student.mdf_path and Path(student.mdf_path).exists():
            ok = self.db_conn.attach_database(student.db_name, student.mdf_path)
            student.is_attached = ok
            return ok
            
        logger.warning(f"Database sinh viên [{student.db_name}] không tồn tại trên máy chủ.")
        return False
