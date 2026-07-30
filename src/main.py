import click
import yaml
import sys
from pathlib import Path
import logging
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from src.db.connection import DatabaseConnection
from src.db.queries import QueryRepository
from src.answer.loader import AnswerLoader
from src.student.manager import StudentManager
from src.grading.engine import GradingEngine
from src.reporting.excel_export import ExcelExporter
from src.reporting.detail_report import DetailReportExporter

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

console = Console(legacy_windows=False)

def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        console.print(f"[bold red]Lỗi:[/bold red] Không tìm thấy file cấu hình {config_path}")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

@click.group()
def cli():
    """Hệ thống chấm điểm bài thi Hệ thống thông tin kế toán (MISA SME.NET)"""
    pass

@cli.command()
@click.option("--config", default="config/grading_config.yaml", help="Đường dẫn file cấu hình YAML")
@click.option("--student", default=None, help="Mã SV cụ thể cần chấm (nếu bỏ trống sẽ chấm tất cả)")
@click.option("--export/--no-export", default=True, help="Xuất file Excel tổng hợp")
@click.option("--detail/--no-detail", default=True, help="Xuất báo cáo chi tiết Markdown")
def grade(config, student, export, detail):
    """Chấm điểm bài thi sinh viên."""
    cfg = load_config(config)
    server_cfg = cfg.get("server", {})
    
    db_conn = DatabaseConnection(
        host=server_cfg.get("host", ".\\MC22"),
        auth=server_cfg.get("auth", "windows"),
        username=server_cfg.get("username"),
        password=server_cfg.get("password"),
        driver=server_cfg.get("driver", "SQL Server Native Client 11.0")
    )
    
    query_repo = QueryRepository("config/queries.yaml")
    student_mgr = StudentManager(db_conn, cfg)
    
    console.print(Panel("[bold green]HỆ THỐNG CHẤM ĐIỂM HTTTKT (PYTHON REFACTOR)[/bold green]", expand=False))
    
    students = student_mgr.get_students()
    if student:
        students = [s for s in students if s.student_id == student]
        if not students:
            console.print(f"[bold red]Không tìm thấy sinh viên với mã:[/bold red] {student}")
            return

    console.print(f"[bold cyan]Tổng số sinh viên cần chấm điểm:[/bold cyan] {len(students)}")
    
    engine = GradingEngine(cfg, db_conn, query_repo)
    reports = engine.grade_all_students(students)
    
    # Print summary table
    table = Table(title="KẾT QUẢ CHẤM ĐIỂM TỔNG HỢP")
    table.add_column("STT", justify="center")
    table.add_column("Mã SV", justify="center", style="cyan")
    table.add_column("Họ và tên", style="magenta")
    table.add_column("Số máy", justify="center")
    table.add_column("Điểm số", justify="right", style="bold green")

    for idx, r in enumerate(reports, 1):
        st = r.student
        final_score = r.score_data.get("final_score", 0.0)
        table.add_row(
            str(idx), st.student_id, f"{st.last_name} {st.first_name}",
            st.computer_name or "N/A", f"{final_score:.2f}"
        )

    console.print(table)

    # Exporters
    if export:
        exporter = ExcelExporter(cfg.get("output", {}).get("directory", "output"))
        outfile = exporter.export(reports)
        console.print(f"[bold green]✔ Bảng điểm Excel đã lưu tại:[/bold green] {outfile}")

    if detail:
        d_exporter = DetailReportExporter(f"{cfg.get('output', {}).get('directory', 'output')}/details")
        for r in reports:
            d_exporter.export_student_detail(r)
        console.print(f"[bold green]✔ Báo cáo chi tiết Markdown đã lưu tại thư mục details.[/bold green]")

@cli.command()
@click.option("--config", default="config/grading_config.yaml", help="File cấu hình")
def load_answer(config):
    """Kiểm tra và hiển thị dữ liệu đáp án."""
    cfg = load_config(config)
    server_cfg = cfg.get("server", {})
    db_conn = DatabaseConnection(
        host=server_cfg.get("host", ".\\MC22"),
        auth=server_cfg.get("auth", "windows")
    )
    query_repo = QueryRepository("config/queries.yaml")
    
    loader = AnswerLoader(db_conn, query_repo, cfg)
    master = loader.load()
    
    console.print("[bold green]Dữ liệu đáp án Master:[/bold green]")
    if master.inventory:
        console.print(f"- **HTK**: {master.inventory.item_count} mặt hàng, tổng SL={master.inventory.total_qty}, tổng ST={master.inventory.total_amount:,.0f} VND")
    if master.fixed_asset:
        console.print(f"- **TSCĐ**: {len(master.fixed_asset.items)} tài sản cố định")
    if master.general_balance:
        console.print(f"- **Số dư sổ cái**: {len(master.general_balance.accounts)} tài khoản")
    if master.transactions:
        console.print(f"- **Nghiệp vụ phát sinh**: {len(master.transactions.entries)} cặp (TaskID, TK)")
    if master.financial_reports:
        console.print(f"- **Báo cáo tài chính**: {len(master.financial_reports.items)} chỉ tiêu BCTC")

@cli.command()
@click.option("--config", default="config/grading_config.yaml", help="File cấu hình")
def list_students(config):
    """Danh sách tất cả sinh viên được phát hiện."""
    cfg = load_config(config)
    server_cfg = cfg.get("server", {})
    db_conn = DatabaseConnection(host=server_cfg.get("host", ".\\MC22"), auth=server_cfg.get("auth", "windows"))
    student_mgr = StudentManager(db_conn, cfg)
    
    students = student_mgr.get_students()
    console.print(f"[bold cyan]Tìm thấy {len(students)} sinh viên:[/bold cyan]")
    for st in students:
        console.print(f"- MSSV: {st.student_id} | Tồn tại DB: {st.is_attached}")

@cli.command()
@click.option("--host", default="127.0.0.1", help="Host IP")
@click.option("--port", default=8000, help="Port")
@click.option("--open-browser/--no-open-browser", default=True, help="Tự động mở trình duyệt web")
def gui(host, port, open_browser):
    """Khởi chạy giao diện Web GUI chuyên nghiệp."""
    import uvicorn
    import webbrowser
    url = f"http://{host}:{port}"
    console.print(f"[bold green]✔ Đã khởi chạy Giao diện Web GUI thành công tại:[/bold green] [bold cyan]{url}[/bold cyan]")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run("src.web.app:app", host=host, port=port, log_level="info")

if __name__ == "__main__":
    cli()
