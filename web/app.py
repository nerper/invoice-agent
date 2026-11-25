"""FastAPI routes. No business logic: everything calls agent.pipeline."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from agent import pipeline
from agent.config import Settings, load_settings
from agent.llm import LLMError, OpenRouterLLM
from agent.workspace import RunLocked

HERE = Path(__file__).parent
MAX_UPLOAD = 10 * 1024 * 1024
FOLDER_LABELS = [
    ("inbox", "Inbox"),
    ("processing", "Processing"),
    ("approved", "Approved"),
    ("flagged", "Flagged"),
    ("needs_approval", "Needs approval"),
    ("rejected", "Rejected"),
]


class HumanBody(BaseModel):
    note: str | None = None
    reason: str | None = None


class WatchBody(BaseModel):
    enabled: bool


def peso(centavos: int | None) -> str:
    return "" if centavos is None else f"₱{centavos / 100:,.2f}"


def require_header(request: Request) -> None:
    if request.headers.get("x-requested-with") != "invoice-agent":
        raise HTTPException(403, "missing X-Requested-With header")


def create_app(settings: Settings | None = None, llm_factory: Any = None) -> FastAPI:
    s = settings or load_settings()
    ctx = pipeline.make_context(s)
    app = FastAPI(title="Invoice Agent")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["peso"] = peso
    state: dict[str, Any] = {
        "watch": False,
        "thread": None,
        "watch_thread": None,
        "error": None,
    }
    make_llm = llm_factory or (lambda: OpenRouterLLM(s))

    def db_rows(sql: str, *args: Any) -> list[dict[str, Any]]:
        return [dict(r) for r in ctx.conn.execute(sql, args).fetchall()]

    def running() -> bool:
        t = state["thread"]
        return bool((t and t.is_alive()) or ctx.ws.lock_path.exists())

    def run_job(trigger: str) -> None:
        try:
            ctx.llm = make_llm()
            pipeline.run_inbox(ctx, trigger)
            state["error"] = None
        except (LLMError, RunLocked) as e:
            state["error"] = str(e)

    def watch_loop() -> None:
        while state["watch"]:
            if ctx.ws.inbox_files() and not running():
                run_job("watch")
            threading.Event().wait(s.watch_interval_s)

    # ---- pages -----------------------------------------------------------
    @app.get("/")
    def index(request: Request):
        rows = db_rows("SELECT * FROM processed_invoices ORDER BY created_at DESC")
        inbox = [{"file_name": p.name} for p in ctx.ws.inbox_files()]
        groups = {"inbox": inbox}
        for key, _ in FOLDER_LABELS[1:]:
            groups[key] = [r for r in rows if r["current_path"].startswith(key + "/")]
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "labels": FOLDER_LABELS,
                "groups": groups,
                "counts": ctx.ws.counts(),
                "running": running(),
                "watch": state["watch"],
                "page": "home",
            },
        )

    @app.get("/invoice/{fp}")
    def invoice(request: Request, fp: str):
        row = _row(fp)
        events = ctx.log.read(fp=row["fingerprint"])
        return templates.TemplateResponse(
            request,
            "invoice.html",
            {
                "r": row,
                "findings": json.loads(row["findings_json"] or "[]"),
                "events": events,
                "page": "home",
            },
        )

    @app.get("/log")
    def log_page(request: Request, run_id: str = "", invoice: str = "", event: str = ""):
        recs = ctx.log.read(run_id or None, invoice or None, event or None)[::-1][:200]
        return templates.TemplateResponse(
            request,
            "log.html",
            {
                "recs": recs,
                "run_id": run_id,
                "invoice": invoice,
                "event": event,
                "page": "log",
                "runs": db_rows("SELECT run_id, started_at FROM runs ORDER BY started_at DESC LIMIT 20"),
            },
        )

    @app.get("/pos")
    def pos_page(request: Request):
        from agent import db

        return templates.TemplateResponse(request, "pos.html", {"pos": db.list_pos(ctx.conn), "page": "pos"})

    # ---- helpers ---------------------------------------------------------
    def _row(fp: str) -> dict[str, Any]:
        if len(fp) < 6:
            raise HTTPException(404, "unknown invoice")
        rows = db_rows("SELECT * FROM processed_invoices WHERE fingerprint LIKE ?", fp + "%")
        if len(rows) != 1:
            raise HTTPException(404, "unknown invoice")
        return rows[0]

    # ---- API -------------------------------------------------------------
    @app.get("/api/state")
    def api_state():
        recent = ctx.log.read()[-20:][::-1]
        return {
            "counts": ctx.ws.counts(),
            "running": running(),
            "current": ctx.log.current,
            "watch": state["watch"],
            "error": state["error"],
            "events": recent,
        }

    @app.post("/api/run", dependencies=[Depends(require_header)])
    def api_run():
        if running():
            raise HTTPException(409, "a run is already in progress")
        t = threading.Thread(target=run_job, args=("ui",), daemon=True)
        state["thread"] = t
        t.start()
        return {"started": True}

    @app.post("/api/watch", dependencies=[Depends(require_header)])
    def api_watch(body: WatchBody):
        state["watch"] = body.enabled
        if body.enabled and not (state["watch_thread"] and state["watch_thread"].is_alive()):
            state["watch_thread"] = threading.Thread(target=watch_loop, daemon=True)
            state["watch_thread"].start()
        return {"watch": state["watch"]}

    @app.post("/api/upload", dependencies=[Depends(require_header)])
    async def api_upload(file: UploadFile = File(...)):
        name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(file.filename or "").name)
        if not name.lower().endswith(".pdf"):
            raise HTTPException(400, "only .pdf files are accepted")
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "file larger than 10 MB")
        if not data.startswith(b"%PDF-"):
            raise HTTPException(400, "not a PDF (bad magic bytes)")
        inbox = ctx.ws.dir("inbox")
        final, n = inbox / name, 0
        while final.exists():
            n += 1
            final = inbox / f"{Path(name).stem}-{n}.pdf"
        tmp = inbox / (final.name + ".part")  # .part is ignored by the inbox scan
        tmp.write_bytes(data)
        tmp.replace(final)
        return {"saved": final.name}

    def _human(fp: str, approve: bool, text: str):
        row = _row(fp)
        try:
            return pipeline.apply_human_decision(ctx, row["fingerprint"], approve, text)
        except pipeline.NotAwaiting as e:
            raise HTTPException(409, str(e)) from e

    @app.post("/api/invoices/{fp}/approve", dependencies=[Depends(require_header)])
    def api_approve(fp: str, body: HumanBody | None = None):
        return _human(fp, True, (body.note if body else "") or "")

    @app.post("/api/invoices/{fp}/reject", dependencies=[Depends(require_header)])
    def api_reject(fp: str, body: HumanBody):
        if not (body.reason or "").strip():
            raise HTTPException(422, "reason is required")
        return _human(fp, False, body.reason.strip())

    @app.post("/api/invoices/{fp}/reveal", dependencies=[Depends(require_header)])
    def api_reveal(fp: str):
        if s.ui.host != "127.0.0.1":
            raise HTTPException(403, "reveal only works when bound to 127.0.0.1")
        path = ctx.ws.abs(_row(fp)["current_path"])
        cmd = (
            ["explorer", f"/select,{path}"]
            if sys.platform.startswith("win")
            else ["open", "-R", str(path)]
            if sys.platform == "darwin"
            else ["xdg-open", str(path.parent)]
        )
        subprocess.run(cmd, shell=False, check=False)
        return {"opened": str(path.parent)}

    @app.get("/files/{fp}")
    def files(fp: str):
        path = ctx.ws.abs(_row(fp)["current_path"])
        if not path.exists():
            raise HTTPException(404, "file missing")
        return FileResponse(path, media_type="application/pdf", content_disposition_type="inline")

    @app.exception_handler(HTTPException)
    async def _http_err(_: Request, exc: HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    app.state.ctx = ctx
    return app
