from __future__ import annotations

from typing import Any, Callable, Dict

from fastapi import HTTPException


def install_legacy_config_adapter(
    app: Any,
    *,
    get_config: Callable[[], Dict[str, Any]],
    save_config: Callable[[Dict[str, Any]], None],
) -> None:
    """Replace the old config endpoint with a strict-answer compatible adapter.

    The original UI required Excel fallback fields that are no longer part of
    the production answer path. The adapter accepts the remaining legacy UI
    payload without reintroducing Excel as a second ground truth.
    """

    if getattr(app.state, "legacy_config_adapter_installed", False):
        return
    app.state.legacy_config_adapter_installed = True

    for route in list(app.router.routes):
        if (
            getattr(route, "path", None) == "/api/config"
            and "POST" in (getattr(route, "methods", set()) or set())
        ):
            app.router.routes.remove(route)
            break

    @app.post("/api/config")
    def update_legacy_config(payload: Dict[str, Any]) -> Dict[str, Any]:
        weights = payload.get("weights")
        if not isinstance(weights, dict):
            raise HTTPException(status_code=400, detail="weights must be an object")

        cfg = get_config()
        server = cfg.setdefault("server", {})
        if payload.get("server_host") is not None:
            server["host"] = str(payload["server_host"]).strip()
        if payload.get("server_auth") is not None:
            auth = str(payload["server_auth"]).strip().lower()
            if auth not in {"windows", "sql"}:
                raise HTTPException(status_code=400, detail="Invalid server_auth")
            server["auth"] = auth

        answer = cfg.setdefault("answer", {})
        if payload.get("answer_db"):
            answer["database"] = str(payload["answer_db"]).strip()
        answer["source"] = "database"
        answer["strict"] = True
        # Deliberately ignore excel_fallback / excel_sheet_answer if an older
        # browser still sends them. They must never become answer inputs again.

        scoring = cfg.setdefault("scoring", {})
        scoring["weights"] = {
            str(key): float(value) for key, value in weights.items()
        }
        if payload.get("scale") is not None:
            scoring["scale"] = float(payload["scale"])
        if payload.get("tolerance") is not None:
            scoring["tolerance"] = float(payload["tolerance"])

        student = cfg.setdefault("student", {})
        if payload.get("source") is not None:
            source = str(payload["source"]).strip().lower()
            if source not in {"scan", "excel"}:
                raise HTTPException(status_code=400, detail="Invalid student source")
            student["source"] = source

        save_config(cfg)
        return {"status": "success", "config": cfg}
