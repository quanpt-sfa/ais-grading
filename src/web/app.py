from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from pydantic import BaseModel
import yaml
import uvicorn
from pathlib import Path
from typing import Dict, Any, Optional, List
import logging
import shutil
import tempfile

from src.db.connection import DatabaseConnection
from src.db.queries import QueryRepository
from src.answer.loader import AnswerLoader
from src.student.manager import StudentManager, StudentInfo
from src.grading.engine import GradingEngine, StudentGradeReport
from src.reporting.excel_export import ExcelExporter
from src.reporting.detail_report import DetailReportExporter

logger = logging.getLogger("chamdiem_web")

app = FastAPI(title="Hệ thống Chấm điểm HTTTKT & Phân Công Máy Thi", version="1.2.0")

CONFIG_PATH = Path("config/grading_config.yaml")
QUERIES_PATH = Path("config/queries.yaml")

# Store in-memory state
latest_reports: List[StudentGradeReport] = []
custom_student_list: Optional[List[StudentInfo]] = None
is_grading_running = False

def get_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)

def get_db_and_engine(cfg: dict):
    server_cfg = cfg.get("server", {})
    db_conn = DatabaseConnection(
        host=server_cfg.get("host", ".\\MC22"),
        auth=server_cfg.get("auth", "windows"),
        username=server_cfg.get("username"),
        password=server_cfg.get("password"),
        driver=server_cfg.get("driver", "SQL Server Native Client 11.0")
    )
    query_repo = QueryRepository(str(QUERIES_PATH))
    engine = GradingEngine(cfg, db_conn, query_repo)
    return db_conn, query_repo, engine

class ConfigUpdateModel(BaseModel):
    server_host: str = ".\\MC22"
    server_auth: str = "windows"
    answer_db: str = "HungBinh2024"
    excel_fallback: str = "d12.xlsm"
    excel_sheet_answer: str = "DapAn"
    scale: float = 10.0
    tolerance: float = 1.0
    source: str = "scan"
    weights: Dict[str, float]

class GradeRequestModel(BaseModel):
    student_id: Optional[str] = None

class AddStudentModel(BaseModel):
    student_id: str
    last_name: str = ""
    first_name: str = ""
    class_name: str = ""
    exam_room: str = ""
    assigned_pc: str = ""

@app.get("/api/config")
def api_get_config():
    return get_config()

@app.post("/api/config")
def api_update_config(model: ConfigUpdateModel):
    cfg = get_config()
    
    # Update Server
    if "server" not in cfg:
        cfg["server"] = {}
    cfg["server"]["host"] = model.server_host
    cfg["server"]["auth"] = model.server_auth

    # Update Answer
    if "answer" not in cfg:
        cfg["answer"] = {}
    cfg["answer"]["database"] = model.answer_db
    cfg["answer"]["excel_fallback"] = model.excel_fallback
    cfg["answer"]["excel_sheet_answer"] = model.excel_sheet_answer

    # Update Scoring
    if "scoring" not in cfg:
        cfg["scoring"] = {}
    cfg["scoring"]["weights"] = model.weights
    cfg["scoring"]["scale"] = model.scale
    cfg["scoring"]["tolerance"] = model.tolerance

    # Update Student
    if "student" not in cfg:
        cfg["student"] = {}
    cfg["student"]["source"] = model.source

    save_config(cfg)
    return {"status": "success", "config": cfg}

@app.get("/api/answer")
def api_get_answer():
    try:
        cfg = get_config()
        db_conn, query_repo, _ = get_db_and_engine(cfg)
        loader = AnswerLoader(db_conn, query_repo, cfg)
        master = loader.load()
        return {
            "status": "success",
            "answer_db": cfg.get("answer", {}).get("database", "HungBinh2024"),
            "inventory": {
                "item_count": master.inventory.item_count if master.inventory else 0,
                "total_qty": float(master.inventory.total_qty) if master.inventory else 0,
                "total_amount": float(master.inventory.total_amount) if master.inventory else 0,
            },
            "fixed_assets_count": len(master.fixed_asset.items) if master.fixed_asset else 0,
            "general_accounts_count": len(master.general_balance.accounts) if master.general_balance else 0,
            "transactions_count": len(master.transactions.entries) if master.transactions else 0,
            "reports_count": len(master.financial_reports.items) if master.financial_reports else 0
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/students")
def api_get_students():
    try:
        cfg = get_config()
        db_conn, _, _ = get_db_and_engine(cfg)
        mgr = StudentManager(db_conn, cfg)
        if custom_student_list is not None:
            mgr.set_custom_student_list(custom_student_list)

        students = mgr.get_students()
        report_map = {r.student.student_id: r.student for r in latest_reports}

        student_data = []
        for s in students:
            actual_pc = s.computer_name
            if s.student_id in report_map:
                actual_pc = report_map[s.student_id].computer_name or actual_pc

            temp_st = StudentInfo(
                student_id=s.student_id,
                last_name=s.last_name,
                first_name=s.first_name,
                class_name=s.class_name,
                exam_room=s.exam_room,
                assigned_pc=s.assigned_pc,
                computer_name=actual_pc,
                db_name=s.db_name,
                is_attached=s.is_attached
            )

            student_data.append({
                "student_id": s.student_id,
                "full_name": s.full_name,
                "last_name": s.last_name,
                "first_name": s.first_name,
                "class_name": s.class_name or "N/A",
                "exam_room": s.exam_room or "N/A",
                "assigned_pc": s.assigned_pc or "N/A",
                "computer_name": actual_pc or "Chưa chấm",
                "has_pc_mismatch": temp_st.has_pc_mismatch,
                "is_attached": s.is_attached
            })

        return {"status": "success", "students": student_data}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/students/import")
async def api_import_students_excel(file: UploadFile = File(...)):
    global custom_student_list
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name

        cfg = get_config()
        db_conn, _, _ = get_db_and_engine(cfg)
        mgr = StudentManager(db_conn, cfg)
        students = mgr._get_students_from_excel(tmp_path)
        custom_student_list = students
        return {"status": "success", "count": len(students), "students": [s.__dict__ for s in students]}
    except Exception as e:
        return {"status": "error", "message": f"Lỗi đọc file Excel: {e}"}

@app.post("/api/students/add")
def api_add_student(model: AddStudentModel):
    global custom_student_list
    if custom_student_list is None:
        cfg = get_config()
        db_conn, _, _ = get_db_and_engine(cfg)
        mgr = StudentManager(db_conn, cfg)
        custom_student_list = mgr.get_students()

    st = StudentInfo(
        student_id=model.student_id,
        last_name=model.last_name,
        first_name=model.first_name,
        class_name=model.class_name,
        exam_room=model.exam_room,
        assigned_pc=model.assigned_pc,
        db_name=model.student_id
    )
    custom_student_list.append(st)
    return {"status": "success", "student": st.__dict__}

@app.delete("/api/students/{student_id}")
def api_delete_student(student_id: str):
    global custom_student_list
    if custom_student_list is not None:
        custom_student_list = [s for s in custom_student_list if s.student_id != student_id]
    return {"status": "success"}

@app.post("/api/grade")
def api_run_grading(req: GradeRequestModel = None):
    global latest_reports, is_grading_running
    is_grading_running = True
    try:
        cfg = get_config()
        db_conn, query_repo, engine = get_db_and_engine(cfg)
        mgr = StudentManager(db_conn, cfg)
        if custom_student_list is not None:
            mgr.set_custom_student_list(custom_student_list)

        students = mgr.get_students()

        if req and req.student_id:
            students = [s for s in students if s.student_id == req.student_id]

        reports = engine.grade_all_students(students)
        latest_reports = reports

        # Export detail markdown
        detail_exp = DetailReportExporter(f"{cfg.get('output', {}).get('directory', 'output')}/details")
        for r in reports:
            detail_exp.export_student_detail(r)

        # Save session to SQLite Database for long-term storage
        from src.db.sqlite_storage import SQLiteStorage
        storage = SQLiteStorage()
        exam_id = storage.save_grading_session(reports, config=cfg)

        is_grading_running = False
        return {"status": "success", "count": len(reports), "exam_id": exam_id}
    except Exception as e:
        is_grading_running = False
        logger.error(f"Lỗi chấm điểm: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/api/history/exams")
def api_get_exam_history():
    try:
        from src.db.sqlite_storage import SQLiteStorage
        storage = SQLiteStorage()
        exams = storage.list_exams()
        return {"status": "success", "exams": exams}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/history/exams/{exam_id}")
def api_get_exam_detail(exam_id: int):
    try:
        from src.db.sqlite_storage import SQLiteStorage
        storage = SQLiteStorage()
        results = storage.get_exam_results(exam_id)
        return {"status": "success", "results": results}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def make_json_serializable(obj):
    if isinstance(obj, dict):
        return {k: make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [make_json_serializable(i) for i in obj]
    elif hasattr(obj, '__dict__'):
        return make_json_serializable(obj.__dict__)
    elif isinstance(obj, (int, float, str, bool)) or obj is None:
        return obj
    else:
        return str(obj)

@app.get("/api/results")
def api_get_results():
    results = []
    for r in latest_reports:
        st = r.student
        scores = r.score_data.get("module_results", {})
        
        mod_summary = {}
        for mid, res in r.module_results.items():
            mod_summary[mid] = {
                "display_name": res.display_name,
                "score": res.score,
                "max_weight": res.max_weight,
                "match_ratio": round(res.match_ratio * 100, 1),
                "matched_items": res.matched_items,
                "total_items": res.total_items
            }

        results.append({
            "student_id": st.student_id,
            "name": st.full_name,
            "class_name": st.class_name or "N/A",
            "assigned_pc": st.assigned_pc or "N/A",
            "computer_name": st.computer_name or "N/A",
            "has_pc_mismatch": st.has_pc_mismatch,
            "final_score": r.score_data.get("final_score", 0.0),
            "weighted_total": r.score_data.get("weighted_total", 0.0),
            "scale": r.score_data.get("scale", 10.0),
            "modules": mod_summary,
            "traceability": make_json_serializable(r.traceability_data)
        })
    return {"status": "success", "results": results, "is_running": is_grading_running}

@app.get("/api/export/excel")
def api_export_excel():
    if not latest_reports:
        raise HTTPException(status_code=400, detail="Chưa có kết quả chấm điểm để xuất Excel.")
    cfg = get_config()
    exporter = ExcelExporter(cfg.get("output", {}).get("directory", "output"))
    file_path = exporter.export(latest_reports)
    return FileResponse(file_path, filename=Path(file_path).name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@app.get("/api/student/{student_id}/detail")
def api_get_student_detail(student_id: str):
    for r in latest_reports:
        if r.student.student_id == student_id:
            st = r.student
            mod_details = {}
            for mid, res in r.module_results.items():
                mod_details[mid] = {
                    "display_name": res.display_name,
                    "score": res.score,
                    "max_weight": res.max_weight,
                    "match_ratio": round(res.match_ratio * 100, 1),
                    "matched_items": res.matched_items,
                    "total_items": res.total_items,
                    "details": make_json_serializable(res.details)
                }
            clean_score_data = {
                "final_score": r.score_data.get("final_score", 0.0),
                "scale": r.score_data.get("scale", 10.0),
                "weighted_total": r.score_data.get("weighted_total", 0.0),
                "total_weight": r.score_data.get("total_weight", 100.0)
            }
            return {
                "status": "success",
                "student_id": st.student_id,
                "name": st.full_name,
                "class_name": st.class_name or "N/A",
                "assigned_pc": st.assigned_pc or "N/A",
                "computer_name": st.computer_name or "N/A",
                "has_pc_mismatch": st.has_pc_mismatch,
                "final_score": r.score_data.get("final_score", 0.0),
                "score_data": clean_score_data,
                "modules": mod_details,
                "traceability": make_json_serializable(r.traceability_data)
            }
    raise HTTPException(status_code=404, detail="Không tìm thấy sinh viên trong kết quả chấm.")

@app.get("/", response_class=HTMLResponse)
def index_page():
    return HTMLResponse(content=INDEX_HTML, status_code=200)

INDEX_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hệ Thống Chấm Điểm HTTTKT & Phân Công Máy Thi — MISA SME.NET</title>
    <!-- Google Fonts Inter & Outfit -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@500;600;700;800&display=swap" rel="stylesheet">
    <!-- FontAwesome Icons -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    
    <style>
        :root {
            --bg-dark: #0f172a;
            --bg-card: #1e293b;
            --bg-card-hover: #334155;
            --accent-blue: #38bdf8;
            --accent-purple: #818cf8;
            --accent-emerald: #34d399;
            --accent-rose: #fb7185;
            --accent-amber: #fbbf24;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border-color: rgba(255, 255, 255, 0.08);
            --glass-bg: rgba(30, 41, 59, 0.75);
            --glass-border: rgba(255, 255, 255, 0.12);
        }

        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; }

        body {
            background-color: var(--bg-dark); color: var(--text-main); min-height: 100vh;
            display: flex; flex-direction: column;
            background-image: 
                radial-gradient(circle at 15% 15%, rgba(56, 189, 248, 0.12) 0%, transparent 45%),
                radial-gradient(circle at 85% 85%, rgba(129, 140, 248, 0.12) 0%, transparent 45%);
            background-attachment: fixed;
        }

        header {
            background: var(--glass-bg); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
            border-bottom: 1px solid var(--glass-border); padding: 1rem 2rem;
            display: flex; justify-content: space-between; align-items: center; position: sticky; top: 0; z-index: 100;
        }

        .brand { display: flex; align-items: center; gap: 0.75rem; }
        .brand-icon {
            width: 42px; height: 42px; border-radius: 12px;
            background: linear-gradient(135deg, var(--accent-blue), var(--accent-purple));
            display: flex; align-items: center; justify-content: center; font-size: 1.25rem; color: #fff;
            box-shadow: 0 4px 15px rgba(56, 189, 248, 0.3);
        }
        .brand-text h1 {
            font-family: 'Outfit', sans-serif; font-size: 1.3rem; font-weight: 700;
            background: linear-gradient(to right, #38bdf8, #818cf8);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .brand-text p { font-size: 0.75rem; color: var(--text-muted); }

        .nav-tabs {
            display: flex; gap: 0.5rem; background: rgba(15, 23, 42, 0.6);
            padding: 0.35rem; border-radius: 12px; border: 1px solid var(--border-color);
        }
        .tab-btn {
            padding: 0.5rem 1rem; border-radius: 8px; font-size: 0.85rem; font-weight: 600;
            color: var(--text-muted); background: transparent; border: none; cursor: pointer;
            display: flex; align-items: center; gap: 0.5rem; transition: all 0.2s ease;
        }
        .tab-btn.active { background: var(--bg-card); color: var(--accent-blue); box-shadow: 0 2px 8px rgba(0,0,0,0.3); }
        .tab-btn:hover:not(.active) { color: var(--text-main); }

        .header-actions { display: flex; align-items: center; gap: 0.75rem; }

        .btn {
            padding: 0.6rem 1.2rem; border-radius: 10px; font-weight: 600; font-size: 0.85rem;
            cursor: pointer; border: none; display: flex; align-items: center; gap: 0.5rem;
            transition: all 0.25s ease; text-decoration: none;
        }
        .btn-primary { background: linear-gradient(135deg, #0284c7, #4f46e5); color: #fff; box-shadow: 0 4px 14px rgba(79, 70, 229, 0.35); }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 6px 20px rgba(79, 70, 229, 0.5); }
        .btn-secondary { background: var(--bg-card); color: var(--text-main); border: 1px solid var(--border-color); }
        .btn-secondary:hover { background: var(--bg-card-hover); border-color: var(--accent-blue); }
        .btn-emerald { background: linear-gradient(135deg, #059669, #10b981); color: #fff; box-shadow: 0 4px 14px rgba(16, 185, 129, 0.3); }
        .btn-emerald:hover { transform: translateY(-2px); box-shadow: 0 6px 20px rgba(16, 185, 129, 0.45); }

        main { max-width: 1400px; margin: 0 auto; width: 100%; padding: 2rem; display: flex; flex-direction: column; gap: 1.75rem; }

        .tab-content { display: none; flex-direction: column; gap: 1.5rem; }
        .tab-content.active { display: flex; }

        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1.25rem; }
        .stat-card {
            background: var(--glass-bg); backdrop-filter: blur(8px); border: 1px solid var(--border-color);
            border-radius: 16px; padding: 1.25rem 1.5rem; display: flex; align-items: center; justify-content: space-between;
            transition: transform 0.2s ease, border-color 0.2s ease;
        }
        .stat-card:hover { transform: translateY(-3px); border-color: rgba(255, 255, 255, 0.2); }
        .stat-info p { font-size: 0.75rem; color: var(--text-muted); font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; }
        .stat-info h3 { font-family: 'Outfit', sans-serif; font-size: 1.65rem; font-weight: 700; margin-top: 0.25rem; }
        .stat-icon { width: 48px; height: 48px; border-radius: 14px; display: flex; align-items: center; justify-content: center; font-size: 1.2rem; }
        .stat-icon.blue { background: rgba(56, 189, 248, 0.15); color: var(--accent-blue); }
        .stat-icon.purple { background: rgba(129, 140, 248, 0.15); color: var(--accent-purple); }
        .stat-icon.emerald { background: rgba(52, 211, 153, 0.15); color: var(--accent-emerald); }
        .stat-icon.rose { background: rgba(251, 113, 133, 0.15); color: var(--accent-rose); }

        .panel-card {
            background: var(--glass-bg); backdrop-filter: blur(8px); border: 1px solid var(--border-color);
            border-radius: 20px; padding: 1.5rem; display: flex; flex-direction: column; gap: 1.25rem;
        }
        .panel-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-color); padding-bottom: 1rem; }
        .panel-header h2 { font-family: 'Outfit', sans-serif; font-size: 1.15rem; font-weight: 600; display: flex; align-items: center; gap: 0.6rem; }

        .content-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }
        @media (max-width: 992px) { .content-grid { grid-template-columns: 1fr; } }

        .weight-slider-group { display: flex; flex-direction: column; gap: 0.9rem; }
        .weight-row { display: grid; grid-template-columns: 180px 1fr 60px; align-items: center; gap: 1rem; }
        .weight-label { font-size: 0.85rem; color: var(--text-main); font-weight: 500; }
        input[type="range"] { width: 100%; height: 6px; border-radius: 3px; background: #334155; outline: none; accent-color: var(--accent-blue); }
        .weight-value { font-size: 0.85rem; font-weight: 700; color: var(--accent-blue); text-align: right; }

        .total-weight-box {
            display: flex; justify-content: space-between; align-items: center; padding: 0.75rem 1rem;
            background: rgba(15, 23, 42, 0.6); border-radius: 10px; border: 1px solid var(--border-color);
            font-size: 0.875rem; font-weight: 600;
        }

        .answer-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0.9rem; }
        .answer-item {
            background: rgba(15, 23, 42, 0.5); border: 1px solid var(--border-color);
            border-radius: 12px; padding: 1rem; display: flex; flex-direction: column; gap: 0.4rem;
        }
        .answer-item span { font-size: 0.75rem; color: var(--text-muted); }
        .answer-item strong { font-family: 'Outfit', sans-serif; font-size: 1.05rem; color: var(--accent-emerald); }

        .table-card {
            background: var(--glass-bg); backdrop-filter: blur(8px);
            border: 1px solid var(--border-color); border-radius: 20px; padding: 1.5rem;
            display: flex; flex-direction: column; gap: 1.25rem;
        }
        .table-controls { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; }

        .search-box { position: relative; min-width: 280px; }
        .search-box i { position: absolute; left: 1rem; top: 50%; transform: translateY(-50%); color: var(--text-muted); }
        .search-box input {
            width: 100%; padding: 0.6rem 1rem 0.6rem 2.5rem; background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--border-color); border-radius: 10px; color: var(--text-main); outline: none; font-size: 0.85rem;
        }

        .table-container { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; text-align: left; font-size: 0.85rem; }
        th {
            background: rgba(15, 23, 42, 0.8); padding: 0.85rem 1rem; color: var(--text-muted);
            font-weight: 600; text-transform: uppercase; font-size: 0.75rem; letter-spacing: 0.5px;
            border-bottom: 1px solid var(--border-color);
        }
        td { padding: 0.85rem 1rem; border-bottom: 1px solid var(--border-color); vertical-align: middle; }
        tr:hover td { background: rgba(51, 65, 85, 0.4); }

        .badge-score { font-family: 'Outfit', sans-serif; font-size: 1.05rem; font-weight: 700; padding: 0.25rem 0.6rem; border-radius: 8px; }
        .score-high { background: rgba(52, 211, 153, 0.15); color: var(--accent-emerald); }
        .score-mid { background: rgba(251, 191, 36, 0.15); color: var(--accent-amber); }
        .score-low { background: rgba(251, 113, 133, 0.15); color: var(--accent-rose); }

        .badge-warning {
            background: rgba(251, 113, 133, 0.2); color: var(--accent-rose); border: 1px solid rgba(251, 113, 133, 0.4);
            padding: 0.2rem 0.5rem; border-radius: 6px; font-size: 0.75rem; font-weight: 600; display: inline-flex; align-items: center; gap: 0.3rem;
        }
        .badge-ok {
            background: rgba(52, 211, 153, 0.15); color: var(--accent-emerald);
            padding: 0.2rem 0.5rem; border-radius: 6px; font-size: 0.75rem; font-weight: 600;
        }

        .progress-bar-wrap { width: 70px; height: 6px; background: #334155; border-radius: 3px; overflow: hidden; display: inline-block; vertical-align: middle; margin-right: 0.4rem; }
        .progress-bar-fill { height: 100%; border-radius: 3px; background: linear-gradient(90deg, var(--accent-blue), var(--accent-purple)); }

        .upload-area {
            border: 2px dashed var(--border-color); border-radius: 16px; padding: 2rem;
            text-align: center; cursor: pointer; transition: all 0.25s ease; background: rgba(15, 23, 42, 0.4);
        }
        .upload-area:hover { border-color: var(--accent-blue); background: rgba(56, 189, 248, 0.05); }

        .modal-overlay {
            position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(15, 23, 42, 0.8); backdrop-filter: blur(8px);
            z-index: 1000; display: none; align-items: center; justify-content: center; padding: 2rem;
        }
        .modal-overlay.active { display: flex; }
        .modal-box {
            background: var(--bg-card); border: 1px solid var(--glass-border); border-radius: 24px;
            max-width: 850px; width: 100%; max-height: 85vh; display: flex; flex-direction: column;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5); overflow: hidden;
        }
        .modal-header { padding: 1.25rem 1.75rem; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; }
        .modal-header h3 { font-family: 'Outfit', sans-serif; font-size: 1.2rem; font-weight: 700; color: var(--accent-blue); }
        .modal-close { background: transparent; border: none; color: var(--text-muted); font-size: 1.25rem; cursor: pointer; }
        .modal-close:hover { color: #fff; }
        .modal-body { padding: 1.75rem; overflow-y: auto; display: flex; flex-direction: column; gap: 1.5rem; }

        .detail-section { background: rgba(15, 23, 42, 0.5); border: 1px solid var(--border-color); border-radius: 14px; padding: 1.25rem; }
        .detail-section h4 { font-size: 0.95rem; margin-bottom: 0.75rem; color: var(--accent-purple); display: flex; align-items: center; gap: 0.5rem; }
        pre { background: #090d16; padding: 1rem; border-radius: 10px; font-family: monospace; font-size: 0.8rem; overflow-x: auto; color: #e2e8f0; }

        .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
        .form-group { display: flex; flex-direction: column; gap: 0.4rem; }
        .form-group label { font-size: 0.8rem; color: var(--text-muted); }
        .form-group input, .form-group select {
            padding: 0.6rem 0.8rem; background: rgba(15, 23, 42, 0.8); border: 1px solid var(--border-color);
            border-radius: 8px; color: var(--text-main); outline: none; font-size: 0.85rem;
        }
    </style>
</head>
<body>

    <header>
        <div class="brand">
            <div class="brand-icon">
                <i class="fa-solid fa-graduation-cap"></i>
            </div>
            <div class="brand-text">
                <h1>Chấm Điểm HTTTKT</h1>
                <p>MISA SME.NET & Config Management</p>
            </div>
        </div>

        <div class="nav-tabs">
            <button class="tab-btn active" onclick="switchTab('tab-dashboard')">
                <i class="fa-solid fa-chart-pie"></i> Bảng Điểm & Báo Cáo
            </button>
            <button class="tab-btn" onclick="switchTab('tab-students')">
                <i class="fa-solid fa-users-viewfinder"></i> Phòng Thi & Máy Thi
            </button>
            <button class="tab-btn" onclick="switchTab('tab-settings')">
                <i class="fa-solid fa-gear"></i> Cấu Hình Máy Chủ & Trọng Số
            </button>
        </div>

        <div class="header-actions">
            <button class="btn btn-emerald" onclick="exportExcel()">
                <i class="fa-solid fa-file-excel"></i> Xuất Excel
            </button>
            <button class="btn btn-primary" id="btn-run-grade" onclick="runGrading()">
                <i class="fa-solid fa-play"></i> Bắt Đầu Chấm
            </button>
        </div>
    </header>

    <main>
        <!-- TAB 1: DASHBOARD & RESULTS -->
        <div class="tab-content active" id="tab-dashboard">
            <!-- Top Stats Row -->
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-info">
                        <p>Tổng sinh viên</p>
                        <h3 id="stat-total-students">0</h3>
                    </div>
                    <div class="stat-icon blue"><i class="fa-solid fa-users"></i></div>
                </div>

                <div class="stat-card">
                    <div class="stat-info">
                        <p>Điểm trung bình</p>
                        <h3 id="stat-avg-score">0.0</h3>
                    </div>
                    <div class="stat-icon purple"><i class="fa-solid fa-chart-line"></i></div>
                </div>

                <div class="stat-card">
                    <div class="stat-info">
                        <p>Điểm cao nhất</p>
                        <h3 id="stat-max-score">0.0</h3>
                    </div>
                    <div class="stat-icon emerald"><i class="fa-solid fa-trophy"></i></div>
                </div>

                <div class="stat-card">
                    <div class="stat-info">
                        <p>Cảnh báo ngồi lệch máy</p>
                        <h3 id="stat-mismatch-count" style="color: var(--accent-rose);">0</h3>
                    </div>
                    <div class="stat-icon rose"><i class="fa-solid fa-triangle-exclamation"></i></div>
                </div>
            </div>

            <!-- Table Results Card -->
            <div class="table-card">
                <div class="table-controls">
                    <h2><i class="fa-solid fa-list-check" style="color: var(--accent-emerald);"></i> Kết Quả Chấm Điểm Bài Thi Sinh Viên</h2>
                    <div class="search-box">
                        <i class="fa-solid fa-magnifying-glass"></i>
                        <input type="text" id="search-input" placeholder="Tìm MSSV, Tên hoặc Máy thi..." oninput="filterResults()">
                    </div>
                </div>

                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th style="width: 50px;">STT</th>
                                <th>MSSV</th>
                                <th>Họ và tên</th>
                                <th>Máy phân công</th>
                                <th>Số máy làm bài (Log)</th>
                                <th>Cảnh báo máy</th>
                                <th style="text-align: right;">Điểm số</th>
                                <th style="text-align: center;">Thao tác</th>
                            </tr>
                        </thead>
                        <tbody id="table-body">
                            <tr>
                                <td colspan="8" style="text-align: center; color: var(--text-muted); padding: 2rem;">
                                    Chưa có dữ liệu chấm điểm. Nhấn "Bắt Đầu Chấm" để thực hiện.
                                </td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- TAB 2: STUDENT & COMPUTER ASSIGNMENT MANAGEMENT -->
        <div class="tab-content" id="tab-students">
            <div class="content-grid">
                <!-- Import Excel Panel -->
                <div class="panel-card">
                    <div class="panel-header">
                        <h2><i class="fa-solid fa-file-import" style="color: var(--accent-blue);"></i> Nhập Danh Sách & Phân Công Từ Excel</h2>
                    </div>

                    <div class="upload-area" onclick="document.getElementById('excel-file-input').click()">
                        <i class="fa-solid fa-cloud-arrow-up" style="font-size: 2.5rem; color: var(--accent-blue); margin-bottom: 0.75rem;"></i>
                        <p style="font-weight: 600;">Nhấp để tải file Excel danh sách phòng thi (.xlsx, .xlsm)</p>
                        <p style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.25rem;">Hỗ trợ cột MSSV, Họ, Tên, Lớp, Máy phân công</p>
                        <input type="file" id="excel-file-input" accept=".xlsx, .xlsm" style="display: none;" onchange="uploadStudentExcel(event)">
                    </div>
                </div>

                <!-- Add Single Student Panel -->
                <div class="panel-card">
                    <div class="panel-header">
                        <h2><i class="fa-solid fa-user-plus" style="color: var(--accent-purple);"></i> Thêm Sinh Viên Thủ Công</h2>
                        <button class="btn btn-secondary" style="padding: 0.4rem 0.8rem; font-size: 0.75rem;" onclick="addSingleStudent()">Thêm ngay</button>
                    </div>

                    <div class="form-grid">
                        <div class="form-group">
                            <label>Mã Sinh Viên (MSSV)*</label>
                            <input type="text" id="add-mssv" placeholder="VD: 23729351">
                        </div>
                        <div class="form-group">
                            <label>Họ & Tên lót</label>
                            <input type="text" id="add-lastname" placeholder="VD: Phan Hữu">
                        </div>
                        <div class="form-group">
                            <label>Tên sinh viên</label>
                            <input type="text" id="add-firstname" placeholder="VD: Thoại">
                        </div>
                        <div class="form-group">
                            <label>Lớp học / Phòng thi</label>
                            <input type="text" id="add-class" placeholder="VD: DHKTKT18A / B17">
                        </div>
                        <div class="form-group" style="grid-column: span 2;">
                            <label>Máy tính được phân công (Assigned PC)</label>
                            <input type="text" id="add-assigned-pc" placeholder="VD: B17M05 hoặc TTMP,12\\TH">
                        </div>
                    </div>
                </div>
            </div>

            <!-- Student List Table -->
            <div class="table-card">
                <div class="panel-header">
                    <h2><i class="fa-solid fa-id-card" style="color: var(--accent-emerald);"></i> Danh Sách Sinh Viên & Đối Chiếu Máy Thi</h2>
                    <span style="font-size: 0.8rem; color: var(--text-muted);" id="student-count-badge">Tổng: 0 SV</span>
                </div>

                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th style="width: 50px;">STT</th>
                                <th>MSSV</th>
                                <th>Họ và tên</th>
                                <th>Lớp / Phòng</th>
                                <th>Máy phân công</th>
                                <th>Máy thực tế (Log)</th>
                                <th>Đối chiếu vị trí</th>
                                <th>Trạng thái DB</th>
                                <th style="text-align: center;">Xóa</th>
                            </tr>
                        </thead>
                        <tbody id="student-table-body">
                            <!-- Dynamic -->
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- TAB 3: SYSTEM CONFIG & WEIGHTS SETTINGS -->
        <div class="tab-content" id="tab-settings">
            <div style="display: flex; justify-content: space-between; align-items: center; background: rgba(15,23,42,0.6); padding: 1rem 1.5rem; border-radius: 16px; border: 1px solid var(--border-color);">
                <div>
                    <h2 style="font-family: Outfit; font-size: 1.25rem; color: var(--accent-blue);"><i class="fa-solid fa-gears"></i> Quản Lý Cấu Hình Hệ Thống</h2>
                    <p style="font-size: 0.8rem; color: var(--text-muted);">Thay đổi máy chủ SQL Server, Database Đáp Án Master, Thang Điểm & Trọng Số</p>
                </div>
                <button class="btn btn-primary" onclick="saveConfig()"><i class="fa-solid fa-floppy-disk"></i> Lưu Toàn Bộ Cấu Hình</button>
            </div>

            <div class="content-grid">
                <!-- Server & Answer DB Settings -->
                <div class="panel-card">
                    <div class="panel-header">
                        <h2><i class="fa-solid fa-server" style="color: var(--accent-blue);"></i> Máy Chủ & Database Đáp Án</h2>
                    </div>

                    <div class="form-grid">
                        <div class="form-group">
                            <label>SQL Server Host Instance</label>
                            <input type="text" id="cfg-server-host" value=".\\MC22" placeholder="VD: .\\MC22 hoặc localhost">
                        </div>

                        <div class="form-group">
                            <label>Kiểu Xác Thực SQL</label>
                            <select id="cfg-server-auth">
                                <option value="windows">Windows Authentication</option>
                                <option value="sql">SQL Server Authentication</option>
                            </select>
                        </div>

                        <div class="form-group" style="grid-column: span 2;">
                            <label>Database Đáp Án Master trên Server</label>
                            <input type="text" id="cfg-answer-db" value="HungBinh2024" placeholder="VD: HungBinh2024 hoặc DapAn_Ca1">
                        </div>

                        <div class="form-group">
                            <label>File Excel Đáp Án (Fallback)</label>
                            <input type="text" id="cfg-excel-fallback" value="d12.xlsm">
                        </div>

                        <div class="form-group">
                            <label>Sheet Đáp Án trong Excel</label>
                            <input type="text" id="cfg-excel-sheet" value="DapAn">
                        </div>
                    </div>
                </div>

                <!-- Scoring Rules & Student Source -->
                <div class="panel-card">
                    <div class="panel-header">
                        <h2><i class="fa-solid fa-scale-balanced" style="color: var(--accent-purple);"></i> Quy Tắc Chấm Điểm & Nguồn SV</h2>
                    </div>

                    <div class="form-grid">
                        <div class="form-group">
                            <label>Thang Điểm Quy Đổi</label>
                            <select id="cfg-scale">
                                <option value="10">Thang điểm 10</option>
                                <option value="100">Thang điểm 100</option>
                            </select>
                        </div>

                        <div class="form-group">
                            <label>Sai Số Cho Phép (VND)</label>
                            <input type="number" id="cfg-tolerance" value="1.0" step="0.1">
                        </div>

                        <div class="form-group" style="grid-column: span 2;">
                            <label>Nguồn Phát Hiện Sinh Viên</label>
                            <select id="cfg-student-source">
                                <option value="scan">Tự động Quét tất cả DB 8 chữ số trên SQL Server</option>
                                <option value="excel">Đọc từ File Excel danh sách phân công</option>
                            </select>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Panel Config Sliders -->
            <div class="panel-card">
                <div class="panel-header">
                    <h2><i class="fa-solid fa-sliders" style="color: var(--accent-emerald);"></i> Điều Chỉnh Trọng Số 6 Phân Hệ (Tổng = 100%)</h2>
                </div>

                <div class="weight-slider-group">
                    <div class="weight-row">
                        <span class="weight-label">1. Số dư Hàng tồn kho</span>
                        <input type="range" id="w-inventory" min="0" max="100" value="10" oninput="updateWeightLabels()">
                        <span class="weight-value" id="val-inventory">10%</span>
                    </div>

                    <div class="weight-row">
                        <span class="weight-label">2. Số dư Tài sản cố định</span>
                        <input type="range" id="w-fa" min="0" max="100" value="5" oninput="updateWeightLabels()">
                        <span class="weight-value" id="val-fa">5%</span>
                    </div>

                    <div class="weight-row">
                        <span class="weight-label">3. Số dư Sổ cái tổng hợp</span>
                        <input type="range" id="w-gb" min="0" max="100" value="25" oninput="updateWeightLabels()">
                        <span class="weight-value" id="val-gb">25%</span>
                    </div>

                    <div class="weight-row">
                        <span class="weight-label">4. Nghiệp vụ phát sinh</span>
                        <input type="range" id="w-tx" min="0" max="100" value="55" oninput="updateWeightLabels()">
                        <span class="weight-value" id="val-tx">55%</span>
                    </div>

                    <div class="weight-row">
                        <span class="weight-label">5. Báo cáo tài chính</span>
                        <input type="range" id="w-fr" min="0" max="100" value="5" oninput="updateWeightLabels()">
                        <span class="weight-value" id="val-fr">5%</span>
                    </div>

                    <div class="weight-row">
                        <span class="weight-label">6. Audit Log Nhật ký</span>
                        <input type="range" id="w-audit" min="0" max="100" value="0" oninput="updateWeightLabels()">
                        <span class="weight-value" id="val-audit">0%</span>
                    </div>
                </div>

                <div class="total-weight-box">
                    <span>Tổng trọng số:</span>
                    <span id="total-weight-sum" style="color: var(--accent-emerald);">100%</span>
                </div>
            </div>
        </div>
    </main>

    <!-- Student Detail Modal -->
    <div class="modal-overlay" id="modal-detail">
        <div class="modal-box">
            <div class="modal-header">
                <h3 id="modal-title">Chi tiết bài làm sinh viên</h3>
                <button class="modal-close" onclick="closeModal()"><i class="fa-solid fa-xmark"></i></button>
            </div>
            <div class="modal-body" id="modal-body">
                <!-- Dynamic Content -->
            </div>
        </div>
    </div>

    <script>
        let allResults = [];
        let allStudentsList = [];

        document.addEventListener('DOMContentLoaded', () => {
            loadConfig();
            loadAnswerData();
            fetchStudents();
            fetchResults();
        });

        function switchTab(tabId) {
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

            event.currentTarget.classList.add('active');
            document.getElementById(tabId).classList.add('active');
        }

        async function loadConfig() {
            try {
                const res = await fetch('/api/config');
                const data = await res.json();
                
                // Populate server & answer DB settings
                if (data.server) {
                    document.getElementById('cfg-server-host').value = data.server.host || '.\\\\MC22';
                    document.getElementById('cfg-server-auth').value = data.server.auth || 'windows';
                }
                if (data.answer) {
                    document.getElementById('cfg-answer-db').value = data.answer.database || 'HungBinh2024';
                    document.getElementById('cfg-excel-fallback').value = data.answer.excel_fallback || 'd12.xlsm';
                    document.getElementById('cfg-excel-sheet').value = data.answer.excel_sheet_answer || 'DapAn';
                }
                if (data.scoring) {
                    document.getElementById('cfg-scale').value = data.scoring.scale || 10;
                    document.getElementById('cfg-tolerance').value = data.scoring.tolerance || 1.0;
                    if (data.scoring.weights) {
                        const w = data.scoring.weights;
                        document.getElementById('w-inventory').value = w.inventory_balance || 0;
                        document.getElementById('w-fa').value = w.fixed_asset_balance || 0;
                        document.getElementById('w-gb').value = w.general_balance || 0;
                        document.getElementById('w-tx').value = w.transactions || 0;
                        document.getElementById('w-fr').value = w.financial_reports || 0;
                        document.getElementById('w-audit').value = w.audit_analysis || 0;
                        updateWeightLabels();
                    }
                }
                if (data.student) {
                    document.getElementById('cfg-student-source').value = data.student.source || 'scan';
                }
            } catch (e) {
                console.error(e);
            }
        }

        function updateWeightLabels() {
            const inv = parseInt(document.getElementById('w-inventory').value);
            const fa = parseInt(document.getElementById('w-fa').value);
            const gb = parseInt(document.getElementById('w-gb').value);
            const tx = parseInt(document.getElementById('w-tx').value);
            const fr = parseInt(document.getElementById('w-fr').value);
            const audit = parseInt(document.getElementById('w-audit').value);

            document.getElementById('val-inventory').innerText = inv + '%';
            document.getElementById('val-fa').innerText = fa + '%';
            document.getElementById('val-gb').innerText = gb + '%';
            document.getElementById('val-tx').innerText = tx + '%';
            document.getElementById('val-fr').innerText = fr + '%';
            document.getElementById('val-audit').innerText = audit + '%';

            const sum = inv + fa + gb + tx + fr + audit;
            const sumEl = document.getElementById('total-weight-sum');
            sumEl.innerText = sum + '%';
            sumEl.style.color = (sum === 100) ? 'var(--accent-emerald)' : 'var(--accent-rose)';
        }

        async function saveConfig() {
            const weights = {
                inventory_balance: parseFloat(document.getElementById('w-inventory').value),
                fixed_asset_balance: parseFloat(document.getElementById('w-fa').value),
                general_balance: parseFloat(document.getElementById('w-gb').value),
                transactions: parseFloat(document.getElementById('w-tx').value),
                financial_reports: parseFloat(document.getElementById('w-fr').value),
                audit_analysis: parseFloat(document.getElementById('w-audit').value),
            };

            const payload = {
                server_host: document.getElementById('cfg-server-host').value.trim(),
                server_auth: document.getElementById('cfg-server-auth').value,
                answer_db: document.getElementById('cfg-answer-db').value.trim(),
                excel_fallback: document.getElementById('cfg-excel-fallback').value.trim(),
                excel_sheet_answer: document.getElementById('cfg-excel-sheet').value.trim(),
                scale: parseFloat(document.getElementById('cfg-scale').value),
                tolerance: parseFloat(document.getElementById('cfg-tolerance').value),
                source: document.getElementById('cfg-student-source').value,
                weights: weights
            };

            try {
                const res = await fetch('/api/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (data.status === 'success') {
                    alert('Đã lưu thành công toàn bộ cấu hình hệ thống & máy chủ!');
                    await loadAnswerData();
                }
            } catch(e) {
                alert('Lỗi lưu cấu hình: ' + e);
            }
        }

        async function loadAnswerData() {
            try {
                const res = await fetch('/api/answer');
                const data = await res.json();
                if (data.status === 'success') {
                    document.getElementById('ans-inv').innerText = `${data.inventory.item_count} mặt hàng (${(data.inventory.total_amount/1e9).toFixed(2)} Tỷ)`;
                    document.getElementById('ans-fa').innerText = `${data.fixed_assets_count} tài sản cố định`;
                    document.getElementById('ans-gb').innerText = `${data.general_accounts_count} tài khoản`;
                    document.getElementById('ans-tx').innerText = `${data.transactions_count.toLocaleString()} cặp (Task, TK)`;
                    document.getElementById('ans-fr').innerText = `${data.reports_count} chỉ tiêu BCTC`;
                }
            } catch (e) {
                console.error(e);
            }
        }

        async function fetchStudents() {
            try {
                const res = await fetch('/api/students');
                const data = await res.json();
                if (data.status === 'success') {
                    allStudentsList = data.students;
                    renderStudentManagerTable(allStudentsList);
                }
            } catch (e) {
                console.error(e);
            }
        }

        function renderStudentManagerTable(students) {
            document.getElementById('student-count-badge').innerText = `Tổng: ${students.length} SV`;
            const tbody = document.getElementById('student-table-body');
            if (students.length === 0) {
                tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 2rem;">Chưa có sinh viên nào trong danh sách. Hãy import từ Excel hoặc thêm thủ công.</td></tr>`;
                return;
            }

            tbody.innerHTML = students.map((s, idx) => {
                const mismatchBadge = s.has_pc_mismatch 
                    ? `<span class="badge-warning"><i class="fa-solid fa-triangle-exclamation"></i> Lệch máy</span>` 
                    : `<span class="badge-ok"><i class="fa-solid fa-circle-check"></i> Khớp</span>`;

                const dbBadge = s.is_attached 
                    ? `<span style="color: var(--accent-emerald); font-weight: 600;"><i class="fa-solid fa-database"></i> Sẵn sàng</span>` 
                    : `<span style="color: var(--text-muted);"><i class="fa-solid fa-database"></i> Chưa attach</span>`;

                return `
                    <tr>
                        <td style="text-align: center; color: var(--text-muted);">${idx + 1}</td>
                        <td><strong style="color: var(--accent-blue);">${s.student_id}</strong></td>
                        <td><strong>${s.full_name}</strong></td>
                        <td>${s.class_name} / ${s.exam_room}</td>
                        <td style="color: var(--accent-purple); font-weight: 600;">${s.assigned_pc}</td>
                        <td>${s.computer_name}</td>
                        <td>${mismatchBadge}</td>
                        <td>${dbBadge}</td>
                        <td style="text-align: center;">
                            <button class="btn btn-secondary" style="padding: 0.3rem 0.6rem; color: var(--accent-rose);" onclick="deleteStudent('${s.student_id}')">
                                <i class="fa-solid fa-trash"></i>
                            </button>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        async function uploadStudentExcel(event) {
            const file = event.target.files[0];
            if (!file) return;

            const formData = new FormData();
            formData.append('file', file);

            try {
                const res = await fetch('/api/students/import', { method: 'POST', body: formData });
                const data = await res.json();
                if (data.status === 'success') {
                    alert(`Đã tải thành công ${data.count} sinh viên từ file Excel!`);
                    await fetchStudents();
                } else {
                    alert('Lỗi import: ' + data.message);
                }
            } catch (e) {
                alert('Lỗi kết nối upload: ' + e);
            }
        }

        async function addSingleStudent() {
            const sid = document.getElementById('add-mssv').value.trim();
            if (!sid) {
                alert('Vui lòng nhập MSSV!');
                return;
            }

            const payload = {
                student_id: sid,
                last_name: document.getElementById('add-lastname').value.trim(),
                first_name: document.getElementById('add-firstname').value.trim(),
                class_name: document.getElementById('add-class').value.trim(),
                assigned_pc: document.getElementById('add-assigned-pc').value.trim()
            };

            try {
                const res = await fetch('/api/students/add', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (data.status === 'success') {
                    alert('Đã thêm sinh viên thành công!');
                    document.getElementById('add-mssv').value = '';
                    await fetchStudents();
                }
            } catch (e) {
                alert('Lỗi thêm sinh viên: ' + e);
            }
        }

        async function deleteStudent(studentId) {
            if (!confirm(`Bạn có chắc muốn xóa SV ${studentId} khỏi danh sách?`)) return;
            try {
                await fetch(`/api/students/${studentId}`, { method: 'DELETE' });
                await fetchStudents();
            } catch (e) {
                alert('Lỗi xóa sinh viên: ' + e);
            }
        }

        async function runGrading() {
            const btn = document.getElementById('btn-run-grade');
            btn.disabled = true;
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Đang chấm điểm...';

            try {
                const res = await fetch('/api/grade', { method: 'POST' });
                const data = await res.json();
                if (data.status === 'success') {
                    await fetchResults();
                    await fetchStudents();
                } else {
                    alert('Lỗi chấm điểm: ' + data.message);
                }
            } catch (e) {
                alert('Lỗi thực thi: ' + e);
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<i class="fa-solid fa-play"></i> Bắt Đầu Chấm';
            }
        }

        async function fetchResults() {
            try {
                const res = await fetch('/api/results');
                const data = await res.json();
                if (data.status === 'success') {
                    allResults = data.results;
                    renderTable(allResults);
                    updateStats(allResults);
                }
            } catch (e) {
                console.error(e);
            }
        }

        function updateStats(results) {
            document.getElementById('stat-total-students').innerText = results.length;
            if (results.length === 0) return;

            const scores = results.map(r => r.final_score);
            const avg = scores.reduce((a, b) => a + b, 0) / scores.length;
            const max = Math.max(...scores);
            const mismatchCount = results.filter(r => r.has_pc_mismatch).length;

            document.getElementById('stat-avg-score').innerText = avg.toFixed(2);
            document.getElementById('stat-max-score').innerText = max.toFixed(2);
            document.getElementById('stat-mismatch-count').innerText = mismatchCount;
        }

        function renderTable(results) {
            const tbody = document.getElementById('table-body');
            if (results.length === 0) {
                tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 2rem;">Chưa có dữ liệu. Nhấn "Bắt Đầu Chấm" để thực hiện.</td></tr>`;
                return;
            }

            tbody.innerHTML = results.map((r, idx) => {
                const ratio = Math.round((r.final_score / r.scale) * 100);
                const scoreClass = r.final_score >= 8.0 ? 'score-high' : r.final_score >= 5.0 ? 'score-mid' : 'score-low';

                const pcBadge = r.has_pc_mismatch 
                    ? `<span class="badge-warning"><i class="fa-solid fa-triangle-exclamation"></i> Lệch máy</span>` 
                    : `<span class="badge-ok"><i class="fa-solid fa-circle-check"></i> Khớp máy</span>`;

                return `
                    <tr>
                        <td style="text-align: center; color: var(--text-muted);">${idx + 1}</td>
                        <td><strong style="color: var(--accent-blue);">${r.student_id}</strong></td>
                        <td><strong>${r.name}</strong></td>
                        <td style="color: var(--accent-purple); font-weight: 600;">${r.assigned_pc}</td>
                        <td>${r.computer_name}</td>
                        <td>${pcBadge}</td>
                        <td style="text-align: right;">
                            <span class="badge-score ${scoreClass}">${r.final_score.toFixed(2)}</span>
                        </td>
                        <td style="text-align: center;">
                            <button class="btn btn-secondary" style="padding: 0.35rem 0.75rem; font-size: 0.75rem;" onclick="viewDetail('${r.student_id}')">
                                <i class="fa-solid fa-eye"></i> Chi tiết
                            </button>
                        </td>
                    </tr>
                `;
            }).join('');
        }

        function filterResults() {
            const q = document.getElementById('search-input').value.toLowerCase().trim();
            if (!q) {
                renderTable(allResults);
                return;
            }
            const filtered = allResults.filter(r => 
                r.student_id.toLowerCase().includes(q) || r.name.toLowerCase().includes(q) || r.computer_name.toLowerCase().includes(q)
            );
            renderTable(filtered);
        }

        async function viewDetail(studentId) {
            try {
                const res = await fetch(`/api/student/${studentId}/detail`);
                const data = await res.json();
                if (data.status === 'success') {
                    document.getElementById('modal-title').innerText = `Chi tiết bài làm: ${data.name} (${data.student_id})`;
                    
                    let html = `
                        <div style="display: flex; gap: 1rem; align-items: center; justify-content: space-between; background: rgba(15, 23, 42, 0.6); padding: 1rem 1.5rem; border-radius: 14px; border: 1px solid var(--border-color);">
                            <div>
                                <span style="font-size: 0.8rem; color: var(--text-muted);">MSSV / Lớp</span>
                                <h3 style="font-family: Outfit; color: var(--accent-blue);">${data.student_id} (${data.class_name})</h3>
                            </div>
                            <div>
                                <span style="font-size: 0.8rem; color: var(--text-muted);">Máy phân công vs Thực tế</span>
                                <h4>${data.assigned_pc} → <span style="color: var(--accent-purple);">${data.computer_name}</span></h4>
                            </div>
                            <div style="text-align: right;">
                                <span style="font-size: 0.8rem; color: var(--text-muted);">Tổng điểm kết luận</span>
                                <h2 style="font-family: Outfit; color: var(--accent-emerald);">${data.final_score.toFixed(2)} / ${data.score_data.scale}</h2>
                            </div>
                        </div>

                    // Render Module Breakdown + Item-by-Item Side-by-Side Comparison Tables
                    Object.entries(data.modules).forEach(([mid, m]) => {
                        let detailsHtml = '';
                        if (m.details && m.details.length > 0) {
                            let rowsHtml = '';
                            m.details.slice(0, 50).forEach(d => {
                                const isMatch = d.match === true || d.status === 'OK' || d.ok === true;
                                const statusBadge = isMatch 
                                    ? `<span style="color: var(--accent-emerald); font-weight: 600;"><i class="fa-solid fa-circle-check"></i> Khớp</span>`
                                    : `<span style="color: var(--accent-rose); font-weight: 600;"><i class="fa-solid fa-circle-xmark"></i> Sai</span>`;

                                let itemLabel = d.field || d.account_code || d.code || (d.task_id ? `Task ${d.task_id} (TK ${d.account_code})` : d.item_code || `Mục ${d.asset_index || ''}`);
                                let ansVal = '-';
                                let stVal = '-';

                                if (d.answer !== undefined) ansVal = typeof d.answer === 'number' ? d.answer.toLocaleString() : d.answer;
                                if (d.student !== undefined) stVal = typeof d.student === 'number' ? d.student.toLocaleString() : d.student;
                                
                                if (d.debit) {
                                    ansVal = `Nợ: ${d.debit.ans.toLocaleString()}`;
                                    stVal = `Nợ: ${d.debit.st.toLocaleString()}`;
                                } else if (d.amount) {
                                    ansVal = `${d.amount.ans.toLocaleString()}đ`;
                                    stVal = `${d.amount.st.toLocaleString()}đ`;
                                } else if (d.answer_amount) {
                                    ansVal = `${d.answer_amount.toLocaleString()}đ`;
                                    stVal = `${d.student_amount.toLocaleString()}đ`;
                                } else if (d.org_price) {
                                    ansVal = `${d.org_price.answer.toLocaleString()}đ`;
                                    stVal = `${d.org_price.student.toLocaleString()}đ`;
                                }

                                rowsHtml += `
                                    <tr>
                                        <td><strong>${itemLabel}</strong></td>
                                        <td style="color: var(--accent-blue);">${ansVal}</td>
                                        <td style="color: var(--text-main);">${stVal}</td>
                                        <td style="text-align: center;">${statusBadge}</td>
                                    </tr>
                                `;
                            });

                            detailsHtml = `
                                <table style="margin-top: 0.5rem; font-size: 0.8rem;">
                                    <thead>
                                        <tr>
                                            <th>Mục / Chỉ tiêu / TK</th>
                                            <th>Đáp án chuẩn</th>
                                            <th>Bài làm SV</th>
                                            <th style="text-align: center;">So khớp</th>
                                        </tr>
                                    </thead>
                                    <tbody>${rowsHtml}</tbody>
                                </table>
                                ${m.details.length > 50 ? `<p style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.4rem;">... và ${m.details.length - 50} mục khác (Xem file Markdown chi tiết để hiển thị đầy đủ)</p>` : ''}
                            `;
                        }

                        html += `
                            <div class="detail-section">
                                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
                                    <h4 style="margin: 0;"><i class="fa-solid fa-list-check"></i> ${m.display_name}</h4>
                                    <span style="font-weight: 700; color: var(--accent-emerald);">${m.score.toFixed(2)} / ${m.max_weight} điểm (${m.match_ratio}%)</span>
                                </div>
                                ${detailsHtml}
                            </div>
                        `;
                    });

                    if (data.traceability && Object.keys(data.traceability).length > 0) {
                        html += `
                            <div class="detail-section">
                                <h4><i class="fa-solid fa-diagram-project"></i> Theo vết chứng từ (Traceability)</h4>
                                <pre>${JSON.stringify(data.traceability, null, 2)}</pre>
                            </div>
                        `;
                    }

                    document.getElementById('modal-body').innerHTML = html;
                    document.getElementById('modal-detail').classList.add('active');
                }
            } catch (e) {
                alert('Lỗi tải chi tiết: ' + e);
            }
        }

        function closeModal() {
            document.getElementById('modal-detail').classList.remove('active');
        }

        async function exportExcel() {
            window.location.href = '/api/export/excel';
        }
    </script>
</body>
</html>
"""
