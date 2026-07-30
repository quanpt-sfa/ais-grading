from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import uuid
from typing import Any, Callable, Dict, Optional

from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from src.answer.backup_restore import (
    AnswerSourceRecord,
    AnswerSourceStore,
    BackupRestoreError,
    SqlServerBackupRestorer,
)
from src.answer.loader import AnswerLoader


RESTORE_PAGE = """<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Phục hồi database đáp án</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 820px; margin: 40px auto; padding: 0 20px; background:#0f172a; color:#e2e8f0; }
    .card { background:#1e293b; border:1px solid #334155; border-radius:16px; padding:24px; }
    h1 { margin-top:0; font-size:24px; }
    label { display:block; margin-top:16px; margin-bottom:6px; font-weight:600; }
    input { width:100%; box-sizing:border-box; padding:10px 12px; border-radius:8px; border:1px solid #475569; background:#0f172a; color:#e2e8f0; }
    input[type=checkbox] { width:auto; }
    button { margin-top:22px; padding:11px 18px; border:0; border-radius:9px; background:#0284c7; color:white; font-weight:700; cursor:pointer; }
    button:disabled { opacity:.55; cursor:wait; }
    .row { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
    .hint { color:#94a3b8; font-size:14px; line-height:1.5; }
    pre { white-space:pre-wrap; word-break:break-word; background:#020617; padding:14px; border-radius:10px; min-height:48px; }
    a { color:#38bdf8; }
  </style>
</head>
<body>
  <p><a href="/">← Quay lại hệ thống chấm điểm</a></p>
  <div class="card">
    <h1>Phục hồi file backup làm đáp án</h1>
    <p class="hint">Chấp nhận <strong>.bak</strong> và <strong>.mbk</strong>. Cả hai được kiểm tra và phục hồi trực tiếp bằng SQL Server. Đáp án chỉ được kích hoạt sau khi trích xuất strict và tạo AnswerSnapshot thành công.</p>
    <form id="restore-form">
      <label>File backup SQL Server</label>
      <input type="file" name="file" accept=".bak,.mbk" required>

      <label>Tên bộ đáp án</label>
      <input type="text" name="answer_name" placeholder="Ví dụ: HTTTKT_Ca3_2026" required>

      <div class="row">
        <div>
          <label>Từ ngày</label>
          <input type="date" name="scope_start">
        </div>
        <div>
          <label>Đến ngày</label>
          <input type="date" name="scope_end">
        </div>
      </div>

      <label><input type="checkbox" name="set_read_only" value="true" checked> Đặt database đáp án thành READ_ONLY sau khi khóa snapshot</label>
      <button id="submit" type="submit">Phục hồi và khóa đáp án</button>
    </form>
    <pre id="status">Chưa thực hiện.</pre>
  </div>
<script>
const form = document.getElementById('restore-form');
const statusBox = document.getElementById('status');
const submit = document.getElementById('submit');
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  submit.disabled = true;
  statusBox.textContent = 'Đang tải file, kiểm tra backup, phục hồi database và tạo snapshot...';
  try {
    const response = await fetch('/api/answer/restore', { method:'POST', body:new FormData(form) });
    const data = await response.json();
    statusBox.textContent = JSON.stringify(data, null, 2);
    if (!response.ok) throw new Error(data.detail || 'Restore failed');
  } catch (error) {
    statusBox.textContent += '\n\nLỖI: ' + error.message;
  } finally {
    submit.disabled = false;
  }
});
</script>
</body>
</html>"""


def install_answer_backup_routes(
    app: Any,
    *,
    get_config: Callable[[], Dict[str, Any]],
    save_config: Callable[[Dict[str, Any]], None],
    get_db_and_engine: Callable[[Dict[str, Any]], Any],
) -> None:
    """Install backup restore routes after the main FastAPI app is constructed."""

    if getattr(app.state, "answer_backup_routes_installed", False):
        return
    app.state.answer_backup_routes_installed = True

    @app.get("/answer/restore", response_class=HTMLResponse)
    def answer_restore_page() -> HTMLResponse:
        return HTMLResponse(RESTORE_PAGE)

    @app.post("/api/answer/restore")
    async def restore_answer_backup(
        file: UploadFile = File(...),
        answer_name: str = Form(...),
        scope_start: Optional[str] = Form(None),
        scope_end: Optional[str] = Form(None),
        backup_set_position: Optional[int] = Form(None),
        set_read_only: bool = Form(True),
    ) -> Dict[str, Any]:
        cfg = get_config()
        restore_cfg = dict(cfg.get("answer_restore", {}) or {})
        if not bool(restore_cfg.get("enabled", True)):
            raise HTTPException(status_code=403, detail="Answer backup restore is disabled")

        original_filename = Path(file.filename or "answer.bak").name
        try:
            extension = SqlServerBackupRestorer.validate_extension(original_filename)
        except BackupRestoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        answer_name = answer_name.strip()
        if not answer_name:
            raise HTTPException(status_code=400, detail="Tên bộ đáp án không được để trống")

        staging_directory = Path(
            restore_cfg.get("staging_directory", "data/answer_backups")
        ).expanduser().resolve()
        staging_directory.mkdir(parents=True, exist_ok=True)
        maximum_bytes = int(
            restore_cfg.get("max_upload_bytes", 20 * 1024 * 1024 * 1024)
        )
        temporary_path = staging_directory / f".upload-{uuid.uuid4().hex}{extension}"
        stored_path: Optional[Path] = None
        restore_result = None
        source_record: Optional[AnswerSourceRecord] = None

        source_store = AnswerSourceStore(
            restore_cfg.get("source_directory", "data/answer_sources")
        )

        try:
            uploaded_bytes = await _save_upload(
                file,
                temporary_path,
                maximum_bytes=maximum_bytes,
            )
            file_hash = await run_in_threadpool(
                SqlServerBackupRestorer.sha256,
                temporary_path,
            )
            stored_path = staging_directory / f"{file_hash}{extension}"
            if stored_path.exists():
                temporary_path.unlink(missing_ok=True)
            else:
                temporary_path.replace(stored_path)

            db_conn, query_repo, _ = get_db_and_engine(cfg)
            restorer = SqlServerBackupRestorer(
                db_conn,
                data_directory=restore_cfg.get("data_directory"),
                log_directory=restore_cfg.get("log_directory"),
            )
            source_id = file_hash[:24]
            source_record = AnswerSourceRecord(
                source_id=source_id,
                original_filename=original_filename,
                stored_backup_path=str(stored_path),
                extension=extension,
                file_sha256=file_hash,
                file_size=uploaded_bytes,
                answer_name=answer_name,
                restored_database_name="",
                source_database_name="",
                backup_set_position=int(backup_set_position or 0),
                status="UPLOADED",
                uploaded_at=datetime.now().astimezone().isoformat(),
            )
            source_store.save(source_record)

            restore_result = await run_in_threadpool(
                restorer.restore,
                stored_path,
                answer_name=answer_name,
                backup_set_position=backup_set_position,
            )
            source_record.restored_database_name = restore_result.database_name
            source_record.source_database_name = restore_result.source_database_name
            source_record.backup_set_position = restore_result.backup_set_position
            source_record.restored_at = restore_result.restored_at
            source_record.status = "RESTORED"
            source_record.metadata.update(
                {
                    "data_files": list(restore_result.data_files),
                    "log_files": list(restore_result.log_files),
                }
            )
            source_store.save(source_record)

            candidate_cfg = deepcopy(cfg)
            answer_cfg = candidate_cfg.setdefault("answer", {})
            answer_cfg["source"] = "database"
            answer_cfg["database"] = restore_result.database_name
            answer_cfg["version"] = (
                f"{SqlServerBackupRestorer.database_name(answer_name, file_hash)}-snapshot"
            )
            answer_cfg["backup_source"] = {
                "source_id": source_id,
                "original_filename": original_filename,
                "stored_path": str(stored_path),
                "file_sha256": file_hash,
                "backup_set_position": restore_result.backup_set_position,
                "source_database_name": restore_result.source_database_name,
            }
            scope = answer_cfg.setdefault("scope", {})
            if scope_start:
                scope["start"] = scope_start
            if scope_end:
                scope["end"] = scope_end

            master = await run_in_threadpool(
                AnswerLoader(db_conn, query_repo, candidate_cfg).load
            )
            if set_read_only:
                await run_in_threadpool(
                    restorer.set_read_only,
                    restore_result.database_name,
                )

            save_config(candidate_cfg)
            source_record.status = "READY"
            source_record.answer_snapshot_id = master.answer_snapshot_id
            source_record.answer_data_hash = master.answer_data_hash
            source_record.metadata["set_read_only"] = bool(set_read_only)
            source_store.save(source_record)

            if not bool(restore_cfg.get("keep_backup", True)):
                stored_path.unlink(missing_ok=True)
                source_record.stored_backup_path = ""
                source_store.save(source_record)

            return {
                "status": "success",
                "answer_name": answer_name,
                "database": restore_result.database_name,
                "source_database": restore_result.source_database_name,
                "backup_extension": extension,
                "file_sha256": file_hash,
                "backup_set_position": restore_result.backup_set_position,
                "answer_snapshot_id": master.answer_snapshot_id,
                "answer_data_hash": master.answer_data_hash,
                "read_only": bool(set_read_only),
                "source_record": str(
                    source_store.directory / f"{source_record.source_id}.json"
                ),
            }
        except HTTPException:
            raise
        except Exception as exc:
            if source_record is not None:
                source_record.status = "FAILED"
                source_record.error = str(exc)
                source_store.save(source_record)
            if restore_result is not None:
                try:
                    db_conn, _, _ = get_db_and_engine(cfg)
                    await run_in_threadpool(
                        SqlServerBackupRestorer(db_conn).drop_database,
                        restore_result.database_name,
                    )
                except Exception:
                    pass
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            await file.close()
            temporary_path.unlink(missing_ok=True)


async def _save_upload(
    upload: UploadFile,
    target: Path,
    *,
    maximum_bytes: int,
    chunk_size: int = 1024 * 1024,
) -> int:
    total = 0
    with target.open("wb") as stream:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise BackupRestoreError(
                    f"Backup exceeds maximum upload size ({maximum_bytes} bytes)"
                )
            stream.write(chunk)
    if total == 0:
        raise BackupRestoreError("Backup upload is empty")
    return total
