from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.answer.loader import AnswerLoader


class AdminConfigUpdate(BaseModel):
    server_host: str
    server_auth: str = "windows"
    server_username: Optional[str] = None
    server_password: Optional[str] = None
    clear_server_password: bool = False
    server_driver: str = "SQL Server Native Client 11.0"
    scope_start: Optional[str] = None
    scope_end: Optional[str] = None
    transaction_isolation: str = "SERIALIZABLE"
    restore_enabled: bool = True
    max_upload_gb: float = Field(default=20.0, gt=0, le=1024)
    keep_backup: bool = True
    default_read_only: bool = True
    staging_directory: str = "data/answer_backups"
    source_directory: str = "data/answer_sources"
    data_directory: Optional[str] = None
    log_directory: Optional[str] = None


ADMIN_PAGE = r"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Quản trị hệ thống chấm điểm</title>
  <style>
    :root { color-scheme:dark; --bg:#0f172a; --card:#1e293b; --line:#334155; --text:#e2e8f0; --muted:#94a3b8; --blue:#38bdf8; --green:#34d399; --red:#fb7185; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:system-ui,sans-serif; background:var(--bg); color:var(--text); }
    header { position:sticky; top:0; z-index:10; display:flex; justify-content:space-between; align-items:center; gap:16px; padding:16px 24px; background:rgba(15,23,42,.96); border-bottom:1px solid var(--line); }
    header h1 { margin:0; font-size:22px; }
    nav { display:flex; gap:10px; flex-wrap:wrap; }
    a, button { color:white; }
    a.button, button { display:inline-flex; align-items:center; justify-content:center; padding:9px 13px; border:0; border-radius:8px; background:#0284c7; text-decoration:none; cursor:pointer; font-weight:650; }
    button.secondary, a.secondary { background:#334155; }
    button.danger { background:#be123c; }
    button:disabled { opacity:.55; cursor:wait; }
    main { max-width:1280px; margin:0 auto; padding:24px; display:grid; gap:20px; }
    .grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:20px; }
    .card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:20px; min-width:0; }
    .card h2 { margin:0 0 14px; font-size:18px; }
    .wide { grid-column:1/-1; }
    .form-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
    label { display:block; margin-bottom:5px; color:var(--muted); font-size:13px; }
    input, select { width:100%; padding:9px 10px; border-radius:7px; border:1px solid #475569; background:#0f172a; color:var(--text); }
    input[type=checkbox] { width:auto; }
    .check { display:flex; align-items:center; gap:8px; margin-top:8px; }
    .actions { display:flex; flex-wrap:wrap; gap:10px; margin-top:15px; }
    .hint { color:var(--muted); font-size:13px; line-height:1.5; }
    pre { margin:10px 0 0; padding:12px; background:#020617; border-radius:8px; white-space:pre-wrap; word-break:break-word; max-height:360px; overflow:auto; }
    table { width:100%; border-collapse:collapse; font-size:13px; }
    th, td { padding:9px 8px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }
    th { color:var(--muted); }
    .scroll { overflow:auto; max-height:420px; }
    .status-ok { color:var(--green); font-weight:700; }
    .status-fail { color:var(--red); font-weight:700; }
    .pill { display:inline-block; padding:2px 7px; border-radius:999px; background:#334155; font-size:12px; }
    @media(max-width:850px){ .grid,.form-grid{grid-template-columns:1fr} header{align-items:flex-start;flex-direction:column} }
  </style>
</head>
<body>
<header>
  <div><h1>Quản trị vận hành</h1><div class="hint">Đáp án, SQL Server, chấm riêng và lịch sử kỳ thi</div></div>
  <nav><a class="button secondary" href="/">Màn hình chấm điểm</a><button class="secondary" onclick="refreshAll()">Làm mới</button></nav>
</header>
<main>
  <section class="grid">
    <div class="card">
      <h2>Đáp án đang hoạt động</h2>
      <div id="answer-summary" class="hint">Đang tải...</div>
      <pre id="answer-detail"></pre>
    </div>

    <div class="card">
      <h2>Phục hồi .BAK / .MBK</h2>
      <form id="restore-form">
        <label>File backup SQL Server</label>
        <input type="file" name="file" accept=".bak,.mbk" required>
        <div class="form-grid">
          <div><label>Tên bộ đáp án</label><input name="answer_name" required placeholder="HTTTKT_Ca3_2026"></div>
          <div><label>Backup set position (tùy chọn)</label><input name="backup_set_position" type="number" min="1" placeholder="Tự chọn full backup mới nhất"></div>
          <div><label>Từ ngày</label><input name="scope_start" type="date"></div>
          <div><label>Đến ngày</label><input name="scope_end" type="date"></div>
        </div>
        <label class="check"><input id="restore-read-only" type="checkbox" checked> Đặt database thành READ_ONLY sau khi khóa snapshot</label>
        <div class="hint" id="upload-limit"></div>
        <div class="actions"><button id="restore-submit" type="submit">Phục hồi và kích hoạt</button></div>
      </form>
      <pre id="restore-status">Chưa thực hiện.</pre>
    </div>
  </section>

  <section class="card">
    <h2>Cấu hình SQL Server và restore</h2>
    <form id="config-form">
      <div class="form-grid">
        <div><label>SQL Server host / instance</label><input id="server-host" required></div>
        <div><label>Driver ODBC</label><input id="server-driver" required></div>
        <div><label>Xác thực</label><select id="server-auth"><option value="windows">Windows Authentication</option><option value="sql">SQL Server Authentication</option></select></div>
        <div><label>Username SQL</label><input id="server-username" autocomplete="username"></div>
        <div><label>Password SQL mới</label><input id="server-password" type="password" autocomplete="new-password" placeholder="Để trống để giữ mật khẩu hiện tại"></div>
        <div><label class="check"><input id="clear-password" type="checkbox"> Xóa mật khẩu đã lưu</label></div>
        <div><label>Phạm vi từ ngày</label><input id="scope-start" type="date"></div>
        <div><label>Phạm vi đến ngày</label><input id="scope-end" type="date"></div>
        <div><label>Transaction isolation</label><select id="transaction-isolation"><option>READ COMMITTED</option><option>REPEATABLE READ</option><option>SNAPSHOT</option><option>SERIALIZABLE</option></select></div>
        <div><label>Giới hạn upload (GB)</label><input id="max-upload-gb" type="number" min="0.1" max="1024" step="0.1"></div>
        <div><label>Thư mục staging</label><input id="staging-directory"></div>
        <div><label>Thư mục audit source</label><input id="source-directory"></div>
        <div><label>SQL data directory override</label><input id="data-directory" placeholder="Để trống để SQL Server tự xác định"></div>
        <div><label>SQL log directory override</label><input id="log-directory" placeholder="Để trống để SQL Server tự xác định"></div>
      </div>
      <label class="check"><input id="restore-enabled" type="checkbox"> Cho phép phục hồi đáp án từ giao diện</label>
      <label class="check"><input id="keep-backup" type="checkbox"> Giữ file backup sau khi tạo snapshot</label>
      <label class="check"><input id="default-read-only" type="checkbox"> Mặc định đặt database đáp án READ_ONLY</label>
      <div class="actions"><button type="submit">Kiểm tra kết nối và lưu</button></div>
    </form>
    <pre id="config-status"></pre>
  </section>

  <section class="card">
    <h2>Nguồn đáp án và audit restore</h2>
    <div class="scroll"><table><thead><tr><th>Thời điểm</th><th>Tên đáp án</th><th>File</th><th>Database</th><th>Trạng thái</th><th>Snapshot</th><th>Lỗi</th></tr></thead><tbody id="sources-body"></tbody></table></div>
  </section>

  <section class="grid">
    <div class="card">
      <h2>Chấm riêng một sinh viên</h2>
      <div class="scroll"><table><thead><tr><th>MSSV</th><th>Họ tên</th><th>Database</th><th></th></tr></thead><tbody id="students-body"></tbody></table></div>
      <pre id="single-grade-status"></pre>
    </div>
    <div class="card">
      <h2>Lịch sử kỳ chấm</h2>
      <div class="scroll"><table><thead><tr><th>ID</th><th>Tên kỳ</th><th>Số SV</th><th>Điểm TB</th><th></th></tr></thead><tbody id="history-body"></tbody></table></div>
      <pre id="history-detail"></pre>
    </div>
  </section>
</main>
<script>
const byId = id => document.getElementById(id);
const text = value => value === null || value === undefined ? '' : String(value);
const html = value => text(value).replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));

async function getJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.message || `HTTP ${response.status}`);
  return data;
}

async function loadAdminConfig() {
  const data = await getJson('/api/admin/config');
  byId('server-host').value = data.server.host || '';
  byId('server-driver').value = data.server.driver || '';
  byId('server-auth').value = data.server.auth || 'windows';
  byId('server-username').value = data.server.username || '';
  byId('server-password').placeholder = data.server.has_password ? 'Đã lưu mật khẩu; để trống để giữ nguyên' : 'Chưa có mật khẩu';
  byId('scope-start').value = data.answer.scope.start || '';
  byId('scope-end').value = data.answer.scope.end || '';
  byId('transaction-isolation').value = data.answer.transaction_isolation || 'SERIALIZABLE';
  byId('restore-enabled').checked = data.answer_restore.enabled;
  byId('keep-backup').checked = data.answer_restore.keep_backup;
  byId('default-read-only').checked = data.answer_restore.default_read_only;
  byId('restore-read-only').checked = data.answer_restore.default_read_only;
  byId('max-upload-gb').value = data.answer_restore.max_upload_gb;
  byId('staging-directory').value = data.answer_restore.staging_directory || '';
  byId('source-directory').value = data.answer_restore.source_directory || '';
  byId('data-directory').value = data.answer_restore.data_directory || '';
  byId('log-directory').value = data.answer_restore.log_directory || '';
  byId('upload-limit').textContent = `Giới hạn upload hiện tại: ${data.answer_restore.max_upload_gb} GB`;
}

async function loadAnswerStatus() {
  try {
    const data = await getJson('/api/admin/answer-status');
    byId('answer-summary').innerHTML = `<span class="status-ok">READY</span> · ${html(data.database)} · snapshot ${html(data.snapshot_id || '-')}`;
    byId('answer-detail').textContent = JSON.stringify(data, null, 2);
  } catch (error) {
    byId('answer-summary').innerHTML = `<span class="status-fail">ERROR</span> ${html(error.message)}`;
    byId('answer-detail').textContent = error.message;
  }
}

async function loadSources() {
  const data = await getJson('/api/admin/answer-sources');
  byId('sources-body').innerHTML = data.sources.length ? data.sources.map(r => `<tr><td>${html(r.uploaded_at)}</td><td>${html(r.answer_name)}</td><td>${html(r.original_filename)}</td><td>${html(r.restored_database_name)}</td><td><span class="pill">${html(r.status)}</span></td><td>${html(r.answer_snapshot_id || '')}</td><td>${html(r.error || '')}</td></tr>`).join('') : '<tr><td colspan="7" class="hint">Chưa có lần restore nào.</td></tr>';
}

async function loadStudents() {
  const data = await getJson('/api/students');
  byId('students-body').innerHTML = data.students.length ? data.students.map(s => `<tr><td>${html(s.student_id)}</td><td>${html(s.full_name)}</td><td>${s.is_attached ? '<span class="status-ok">Sẵn sàng</span>' : '<span class="status-fail">Chưa attach</span>'}</td><td><button ${s.is_attached ? '' : 'disabled'} onclick="gradeStudent('${html(s.student_id)}')">Chấm riêng</button></td></tr>`).join('') : '<tr><td colspan="4" class="hint">Không có sinh viên.</td></tr>';
}

async function gradeStudent(studentId) {
  const box = byId('single-grade-status');
  box.textContent = `Đang chấm ${studentId}...`;
  try {
    const data = await getJson('/api/grade', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({student_id:studentId})});
    box.textContent = JSON.stringify(data, null, 2);
  } catch (error) { box.textContent = `LỖI: ${error.message}`; }
}

async function loadHistory() {
  const data = await getJson('/api/history/exams');
  byId('history-body').innerHTML = data.exams.length ? data.exams.map(e => `<tr><td>${e.id}</td><td>${html(e.exam_name)}</td><td>${e.student_count}</td><td>${Number(e.avg_score || 0).toFixed(2)}</td><td><button onclick="loadHistoryDetail(${e.id})">Xem</button></td></tr>`).join('') : '<tr><td colspan="5" class="hint">Chưa có lịch sử.</td></tr>';
}
async function loadHistoryDetail(id) {
  const data = await getJson(`/api/history/exams/${id}`);
  byId('history-detail').textContent = JSON.stringify(data.results, null, 2);
}

byId('restore-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = byId('restore-submit');
  const box = byId('restore-status');
  const formData = new FormData(event.currentTarget);
  formData.set('set_read_only', byId('restore-read-only').checked ? 'true' : 'false');
  if (!formData.get('backup_set_position')) formData.delete('backup_set_position');
  button.disabled = true;
  box.textContent = 'Đang upload, kiểm tra backup, restore, validate và tạo snapshot...';
  try {
    const data = await getJson('/api/answer/restore', {method:'POST', body:formData});
    box.textContent = JSON.stringify(data, null, 2);
    await Promise.all([loadAnswerStatus(), loadSources(), loadAdminConfig()]);
  } catch (error) { box.textContent = `LỖI: ${error.message}`; }
  finally { button.disabled = false; }
});

byId('config-form').addEventListener('submit', async event => {
  event.preventDefault();
  const payload = {
    server_host: byId('server-host').value.trim(), server_auth: byId('server-auth').value,
    server_username: byId('server-username').value.trim() || null, server_password: byId('server-password').value || null,
    clear_server_password: byId('clear-password').checked, server_driver: byId('server-driver').value.trim(),
    scope_start: byId('scope-start').value || null, scope_end: byId('scope-end').value || null,
    transaction_isolation: byId('transaction-isolation').value, restore_enabled: byId('restore-enabled').checked,
    max_upload_gb: Number(byId('max-upload-gb').value), keep_backup: byId('keep-backup').checked,
    default_read_only: byId('default-read-only').checked, staging_directory: byId('staging-directory').value.trim(),
    source_directory: byId('source-directory').value.trim(), data_directory: byId('data-directory').value.trim() || null,
    log_directory: byId('log-directory').value.trim() || null
  };
  const box = byId('config-status'); box.textContent = 'Đang kiểm tra kết nối...';
  try {
    const data = await getJson('/api/admin/config', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
    box.textContent = JSON.stringify(data, null, 2); byId('server-password').value = ''; byId('clear-password').checked = false;
    await loadAdminConfig();
  } catch (error) { box.textContent = `LỖI: ${error.message}`; }
});

async function refreshAll() {
  await Promise.allSettled([loadAdminConfig(), loadAnswerStatus(), loadSources(), loadStudents(), loadHistory()]);
}
refreshAll();
</script>
</body>
</html>"""


def install_admin_routes(
    app: Any,
    *,
    get_config: Callable[[], Dict[str, Any]],
    save_config: Callable[[Dict[str, Any]], None],
    get_db_and_engine: Callable[[Dict[str, Any]], Any],
    index_html: str,
) -> None:
    if getattr(app.state, "admin_routes_installed", False):
        return
    app.state.admin_routes_installed = True

    enhanced_index = _enhance_index_html(index_html)
    _replace_root_route(app, enhanced_index)

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page() -> HTMLResponse:
        return HTMLResponse(ADMIN_PAGE)

    # Installed before the legacy restore page, so this richer UI is the route
    # users actually receive while the existing POST restore endpoint is reused.
    @app.get("/answer/restore", response_class=HTMLResponse)
    def answer_restore_page() -> HTMLResponse:
        return HTMLResponse(ADMIN_PAGE)

    @app.get("/api/admin/config")
    def get_admin_config() -> Dict[str, Any]:
        cfg = get_config()
        server = dict(cfg.get("server", {}) or {})
        answer = dict(cfg.get("answer", {}) or {})
        restore = dict(cfg.get("answer_restore", {}) or {})
        return {
            "server": {
                "host": server.get("host", ".\\SQLEXPRESS"),
                "auth": server.get("auth", "windows"),
                "username": server.get("username"),
                "has_password": bool(server.get("password")),
                "driver": server.get("driver", "SQL Server Native Client 11.0"),
            },
            "answer": {
                "database": answer.get("database"),
                "version": answer.get("version"),
                "scope": dict(answer.get("scope", {}) or {}),
                "transaction_isolation": answer.get("transaction_isolation", "SERIALIZABLE"),
            },
            "answer_restore": {
                "enabled": bool(restore.get("enabled", True)),
                "max_upload_gb": round(int(restore.get("max_upload_bytes", 20 * 1024**3)) / 1024**3, 3),
                "keep_backup": bool(restore.get("keep_backup", True)),
                "default_read_only": bool(restore.get("set_read_only", True)),
                "staging_directory": restore.get("staging_directory", "data/answer_backups"),
                "source_directory": restore.get("source_directory", "data/answer_sources"),
                "data_directory": restore.get("data_directory"),
                "log_directory": restore.get("log_directory"),
            },
        }

    @app.post("/api/admin/config")
    def update_admin_config(model: AdminConfigUpdate) -> Dict[str, Any]:
        auth = model.server_auth.strip().lower()
        if auth not in {"windows", "sql"}:
            raise HTTPException(status_code=400, detail="server_auth must be windows or sql")
        isolation = model.transaction_isolation.strip().upper()
        if isolation not in {"READ COMMITTED", "REPEATABLE READ", "SNAPSHOT", "SERIALIZABLE"}:
            raise HTTPException(status_code=400, detail="Unsupported transaction isolation")
        if model.scope_start and model.scope_end and model.scope_start > model.scope_end:
            raise HTTPException(status_code=400, detail="scope_start must not be after scope_end")

        current = get_config()
        candidate = deepcopy(current)
        server = candidate.setdefault("server", {})
        server.update({"host": model.server_host.strip(), "auth": auth, "driver": model.server_driver.strip()})
        if model.server_username:
            server["username"] = model.server_username.strip()
        elif auth == "windows":
            server.pop("username", None)
        if model.clear_server_password:
            server.pop("password", None)
        elif model.server_password:
            server["password"] = model.server_password
        if auth == "sql" and not server.get("username"):
            raise HTTPException(status_code=400, detail="SQL authentication requires username")

        answer = candidate.setdefault("answer", {})
        answer["transaction_isolation"] = isolation
        scope = answer.setdefault("scope", {})
        if model.scope_start:
            scope["start"] = model.scope_start
        else:
            scope.pop("start", None)
        if model.scope_end:
            scope["end"] = model.scope_end
        else:
            scope.pop("end", None)

        restore = candidate.setdefault("answer_restore", {})
        restore.update({
            "enabled": model.restore_enabled,
            "max_upload_bytes": int(model.max_upload_gb * 1024**3),
            "keep_backup": model.keep_backup,
            "set_read_only": model.default_read_only,
            "staging_directory": model.staging_directory.strip() or "data/answer_backups",
            "source_directory": model.source_directory.strip() or "data/answer_sources",
            "data_directory": model.data_directory.strip() if model.data_directory else None,
            "log_directory": model.log_directory.strip() if model.log_directory else None,
        })

        try:
            db_conn, _, _ = get_db_and_engine(candidate)
            databases = db_conn.list_databases()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"SQL Server connection failed: {exc}") from exc
        save_config(candidate)
        return {"status": "success", "online_databases": len(databases)}

    @app.get("/api/admin/answer-status")
    def answer_status() -> Dict[str, Any]:
        cfg = get_config()
        answer_cfg = dict(cfg.get("answer", {}) or {})
        try:
            db_conn, query_repo, _ = get_db_and_engine(cfg)
            master = AnswerLoader(db_conn, query_repo, cfg).load()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {
            "database": answer_cfg.get("database"),
            "version": master.answer_version,
            "snapshot_id": master.answer_snapshot_id,
            "data_hash": master.answer_data_hash,
            "snapshot_path": master.answer_snapshot_path,
            "scope": answer_cfg.get("scope", {}),
            "backup_source": answer_cfg.get("backup_source"),
            "validation": master.validation_report,
            "counts": {
                "inventory_items": master.inventory.item_count if master.inventory else 0,
                "fixed_assets": len(master.fixed_asset.items) if master.fixed_asset else 0,
                "general_accounts": len(master.general_balance.accounts) if master.general_balance else 0,
                "transactions": len(master.transactions.entries) if master.transactions else 0,
                "financial_report_lines": len(master.financial_reports.lines) if master.financial_reports else 0,
            },
        }

    @app.get("/api/admin/answer-sources")
    def answer_sources() -> Dict[str, List[Dict[str, Any]]]:
        cfg = get_config()
        directory = Path((cfg.get("answer_restore", {}) or {}).get("source_directory", "data/answer_sources"))
        records: List[Dict[str, Any]] = []
        if directory.exists():
            for path in directory.glob("*.json"):
                try:
                    records.append(json.loads(path.read_text(encoding="utf-8")))
                except Exception:
                    records.append({"source_id": path.stem, "status": "CORRUPT", "error": f"Cannot read {path}"})
        records.sort(key=lambda item: str(item.get("uploaded_at", "")), reverse=True)
        return {"sources": records}


def _replace_root_route(app: Any, enhanced_html: str) -> None:
    for route in list(app.router.routes):
        if getattr(route, "path", None) == "/" and "GET" in (getattr(route, "methods", set()) or set()):
            app.router.routes.remove(route)
            break

    @app.get("/", response_class=HTMLResponse)
    def enhanced_index_page() -> HTMLResponse:
        return HTMLResponse(enhanced_html)


def _enhance_index_html(index_html: str) -> str:
    html_text = index_html
    html_text = html_text.replace(
        '<div class="header-actions">',
        '<div class="header-actions"><a class="btn btn-secondary" href="/admin"><i class="fa-solid fa-shield-halved"></i> Quản trị</a>',
        1,
    )
    legacy_answer = '''                        <div class="form-group">
                            <label>File Excel Đáp Án (Fallback)</label>
                            <input type="text" id="cfg-excel-fallback" value="d12.xlsm">
                        </div>

                        <div class="form-group">
                            <label>Sheet Đáp Án trong Excel</label>
                            <input type="text" id="cfg-excel-sheet" value="DapAn">
                        </div>'''
    strict_answer = '''                        <div class="form-group" style="grid-column: span 2;">
                            <label>Nguồn đáp án strict</label>
                            <a class="btn btn-secondary" href="/admin" style="justify-content:center;">Phục hồi BAK/MBK, xem snapshot và audit</a>
                        </div>'''
    html_text = html_text.replace(legacy_answer, strict_answer, 1)
    html_text = html_text.replace(
        "                    document.getElementById('cfg-excel-fallback').value = data.answer.excel_fallback || 'd12.xlsm';\n                    document.getElementById('cfg-excel-sheet').value = data.answer.excel_sheet_answer || 'DapAn';\n",
        "",
        1,
    )
    html_text = html_text.replace(
        "                excel_fallback: document.getElementById('cfg-excel-fallback').value.trim(),\n                excel_sheet_answer: document.getElementById('cfg-excel-sheet').value.trim(),\n",
        "",
        1,
    )
    delete_button = '''                            <button class="btn btn-secondary" style="padding: 0.3rem 0.6rem; color: var(--accent-rose);" onclick="deleteStudent('${s.student_id}')">
                                <i class="fa-solid fa-trash"></i>
                            </button>'''
    action_buttons = '''                            <button class="btn btn-secondary" style="padding: 0.3rem 0.6rem; margin-right:0.3rem; color: var(--accent-blue);" onclick="gradeSingleStudent('${s.student_id}')" ${s.is_attached ? '' : 'disabled'} title="Chấm riêng sinh viên này">
                                <i class="fa-solid fa-play"></i>
                            </button>
                            <button class="btn btn-secondary" style="padding: 0.3rem 0.6rem; color: var(--accent-rose);" onclick="deleteStudent('${s.student_id}')">
                                <i class="fa-solid fa-trash"></i>
                            </button>'''
    html_text = html_text.replace(delete_button, action_buttons, 1)
    single_grade_function = '''        async function gradeSingleStudent(studentId) {
            const confirmed = confirm(`Chấm riêng sinh viên ${studentId}?`);
            if (!confirmed) return;
            try {
                const res = await fetch('/api/grade', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({student_id: studentId})
                });
                const data = await res.json();
                if (data.status === 'success') {
                    await fetchResults();
                    await fetchStudents();
                } else {
                    alert('Lỗi chấm riêng: ' + data.message);
                }
            } catch (e) {
                alert('Lỗi chấm riêng: ' + e);
            }
        }

'''
    html_text = html_text.replace("        async function deleteStudent(studentId) {", single_grade_function + "        async function deleteStudent(studentId) {", 1)
    return html_text
