"""Pipeline: the single place with business logic. CLI and web UI both call this."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent import db, guardrails
from agent.actionlog import ActionLog
from agent.config import Settings
from agent.llm import LLMError
from agent.loop import run_agent
from agent.tools import InvoiceState, ToolContext
from agent.workspace import RunLocked, Workspace

DEST = {
    "approved": "approved",
    "flagged": "flagged",
    "needs_approval": "needs_approval",
    "rejected": "rejected",
}


class PipelineError(Exception):
    code = 1


class NotAwaiting(PipelineError):
    code = 3


class NotFound(PipelineError):
    code = 1


class Ambiguous(PipelineError):
    code = 1


@dataclass
class Context:
    settings: Settings
    ws: Workspace
    conn: sqlite3.Connection
    log: ActionLog
    llm: Any = None


@dataclass
class RunSummary:
    run_id: str
    results: list[dict[str, Any]] = field(default_factory=list)
    cost_usd: float = 0.0
    cost_limit_hit: bool = False


def make_context(settings: Settings, llm: Any = None, log: ActionLog | None = None) -> Context:
    ws = Workspace(settings.workspace)
    conn = db.connect(ws.db_path)
    if conn.execute("SELECT count(*) FROM purchase_orders").fetchone()[0] == 0:
        from agent.config import ROOT

        db.seed(conn, ROOT / "data")  # first run: load POs from data/*.csv
    return Context(settings, ws, conn, log or ActionLog(ws.log_path), llm)


def fingerprint_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _note_text(
    file: str,
    decision: str,
    reason: str,
    codes: list[str],
    by: str,
    steps: int,
    cost: float,
) -> str:
    return (
        f"File: {file}\nDecision: {decision.upper()}\nDecided by: {by}\nReason: {reason}\n"
        f"Codes: {', '.join(codes) or '-'}\nSteps: {steps}  Cost: ${cost:.4f}"
    )


def recover_processing(ctx: Context) -> int:
    n = 0
    for p in ctx.ws.dir("processing").glob("*.pdf"):
        fp = fingerprint_of(p)
        ctx.conn.execute(
            "DELETE FROM processed_invoices WHERE fingerprint=? AND status='processing'",
            (fp,),
        )
        ctx.conn.commit()
        ctx.ws.safe_move(p, "inbox")
        ctx.log.log("error", p.name, fp, message="recovered file left in processing/")
        n += 1
    return n


def _insert_row(ctx: Context, fp: str, name: str, path: Path) -> None:
    with ctx.conn:
        ctx.conn.execute(
            "INSERT INTO processed_invoices (fingerprint,file_name,current_path,status,model,created_at)"
            " VALUES (?,?,?,?,?,?)",
            (
                fp,
                name,
                ctx.ws.rel(path),
                "processing",
                ctx.settings.model,
                db.now_iso(),
            ),
        )


def process_file(ctx: Context, path: Path) -> dict[str, Any]:
    """Process one PDF from inbox/. Returns a result dict (also used for the CLI table)."""
    name, log = path.name, ctx.log
    fp = fingerprint_of(path)
    row = ctx.conn.execute("SELECT * FROM processed_invoices WHERE fingerprint=?", (fp,)).fetchone()
    if row and row["decision"] != "error" and row["status"] in ("done", "awaiting_human"):
        new = ctx.ws.safe_move(path, "flagged")
        ctx.ws.write_note(
            new,
            "Decision: FLAGGED\nReason: EXACT_DUPLICATE_FILE - this exact file was already processed "
            f"as {row['file_name']}.",
        )
        log.log(
            "invoice_skipped",
            name,
            fp,
            code="EXACT_DUPLICATE_FILE",
            original=row["file_name"],
        )
        log.log("file_moved", name, fp, to=ctx.ws.rel(new))
        return {
            "file": name,
            "decision": "flagged",
            "reason": "EXACT_DUPLICATE_FILE",
            "steps": 0,
            "cost_usd": 0.0,
            "decided_by": "guardrail",
            "skipped": True,
        }
    if row:  # previous error: retry cleanly
        with ctx.conn:
            ctx.conn.execute("DELETE FROM processed_invoices WHERE fingerprint=?", (fp,))
    log.log("invoice_started", name, fp)
    proc = ctx.ws.safe_move(path, "processing")
    _insert_row(ctx, fp, name, proc)
    state = InvoiceState(file_name=proc.name, file_path=proc, fingerprint=fp)
    try:
        final = run_agent(ctx.llm, ToolContext(ctx.settings, ctx.conn, state), log)
    except LLMError as e:
        back = ctx.ws.safe_move(proc, "inbox")
        with ctx.conn:
            ctx.conn.execute(
                "UPDATE processed_invoices SET decision='error', status='done', reason=?, current_path=?,"
                " steps=?, cost_usd=? WHERE fingerprint=?",
                (str(e), ctx.ws.rel(back), state.step, state.cost_usd, fp),
            )
        log.log("error", name, fp, state.step, message=str(e))
        return {
            "file": name,
            "decision": "error",
            "reason": str(e),
            "steps": state.step,
            "cost_usd": state.cost_usd,
            "decided_by": None,
        }
    return _finalize(ctx, proc, fp, state, final)


def _finalize(
    ctx: Context, proc: Path, fp: str, state: InvoiceState, final: dict[str, Any]
) -> dict[str, Any]:
    decision = final["decision"]
    inv = final.get("invoice")
    if inv is None and state.compare_invoice:
        from agent.models import InvoiceData

        inv = InvoiceData.model_validate(state.compare_invoice)
    log = ctx.log
    log.log(
        "decision",
        proc.name,
        fp,
        state.step,
        decision=decision,
        reason=final["reason"],
        codes=final["codes"],
        decided_by=final["decided_by"],
        overridden_by=final["overridden_by"],
        submitted=final.get("submitted"),
    )
    dest = ctx.ws.safe_move(proc, DEST[decision])
    ctx.ws.write_note(
        dest,
        _note_text(
            proc.name,
            decision,
            final["reason"],
            final["codes"],
            final["decided_by"],
            state.step,
            state.cost_usd,
        ),
    )
    log.log("file_moved", proc.name, fp, state.step, to=ctx.ws.rel(dest))
    from agent.models import to_centavos

    invoice_number = inv.invoice_number if inv and not state.duplicate_found else None
    findings = json.dumps(state.findings, default=str)
    params = (
        ctx.ws.rel(dest),
        invoice_number,
        inv.vendor_name if inv else None,
        state.compare_po or (inv.po_number if inv else None),
        to_centavos(inv.total_php) if inv else None,
        decision,
        final["reason"],
        findings,
        "awaiting_human" if decision == "needs_approval" else "done",
        state.step,
        state.cost_usd,
        final["decided_by"],
        db.now_iso(),
        fp,
    )
    sql = (
        "UPDATE processed_invoices SET current_path=?, invoice_number=?, vendor_name=?, po_number=?,"
        " total_centavos=?, decision=?, reason=?, findings_json=?, status=?, steps=?, cost_usd=?,"
        " decided_by=?, decided_at=? WHERE fingerprint=?"
    )
    try:
        with ctx.conn:
            ctx.conn.execute(sql, params)
    except sqlite3.IntegrityError:  # same vendor+invoice number already stored
        with ctx.conn:
            ctx.conn.execute(sql, (params[0], None, *params[2:]))
    return {
        "file": proc.name,
        "decision": decision,
        "reason": final["reason"],
        "steps": state.step,
        "cost_usd": state.cost_usd,
        "decided_by": final["decided_by"],
        "codes": final["codes"],
        "submitted": final.get("submitted"),
        "fingerprint": fp,
    }


def run_inbox(
    ctx: Context,
    trigger: str = "cli",
    file: Path | None = None,
    max_cost: float | None = None,
) -> RunSummary:
    """Process every PDF in inbox/ (or one file). Holds the run lock for the whole run."""
    ctx.ws.acquire_lock()
    run_id = str(uuid.uuid4())
    summary = RunSummary(run_id)
    ctx.log.run_id = run_id
    limit = max_cost if max_cost is not None else ctx.settings.max_cost_usd_per_run
    try:
        recover_processing(ctx)
        with ctx.conn:
            ctx.conn.execute(
                "INSERT INTO runs (run_id,trigger,started_at) VALUES (?,?,?)",
                (run_id, trigger, db.now_iso()),
            )
        ctx.log.log("run_started", trigger=trigger)
        files = [file] if file else ctx.ws.inbox_files()
        for p in files:
            if summary.cost_usd >= limit:
                summary.cost_limit_hit = True
                ctx.log.log("guardrail", rule="G7", run_cost=summary.cost_usd)
                break
            res = process_file(ctx, p)
            summary.results.append(res)
            summary.cost_usd += res["cost_usd"]
    finally:
        with ctx.conn:
            ctx.conn.execute(
                "UPDATE runs SET finished_at=?, invoices=?, cost_usd=? WHERE run_id=?",
                (db.now_iso(), len(summary.results), summary.cost_usd, run_id),
            )
        ctx.log.log("run_finished", invoices=len(summary.results), cost_usd=summary.cost_usd)
        ctx.ws.release_lock()
    return summary


# ---- human decisions ------------------------------------------------------
def resolve_invoice(ctx: Context, ident: str) -> sqlite3.Row:
    ident = ident.strip()
    rows = ctx.conn.execute(
        "SELECT * FROM processed_invoices WHERE file_name=? OR (length(?)>=6 AND fingerprint LIKE ?)",
        (ident, ident, ident + "%"),
    ).fetchall()
    if not rows:
        raise NotFound(f"No invoice matches '{ident}'")
    if len(rows) > 1:
        raise Ambiguous("Ambiguous: " + ", ".join(f"{r['file_name']} ({r['fingerprint'][:8]})" for r in rows))
    return rows[0]


def apply_human_decision(ctx: Context, ident: str, approve: bool, note: str = "") -> dict[str, Any]:
    row = resolve_invoice(ctx, ident)
    if row["status"] != "awaiting_human":
        raise NotAwaiting(f"{row['file_name']} is not awaiting a human decision (status {row['status']})")
    decision = "approved" if approve else "rejected"
    src = ctx.ws.abs(row["current_path"])
    ctx.log.log(
        "human_decision",
        row["file_name"],
        row["fingerprint"],
        decision=decision,
        note=note,
    )
    dest = ctx.ws.move_with_note(src, decision)
    with dest.with_name(dest.name + ".note.txt").open("a", encoding="utf-8") as f:
        f.write(f"\nHuman decision: {decision.upper()}" + (f" - {note}" if note else "") + "\n")
    ctx.log.log("file_moved", row["file_name"], row["fingerprint"], to=ctx.ws.rel(dest))
    reason = f"{row['reason']} | Human {decision}: {note}" if note else row["reason"]
    with ctx.conn:
        ctx.conn.execute(
            "UPDATE processed_invoices SET decision=?, status='done', decided_by='human', reason=?,"
            " current_path=?, decided_at=? WHERE fingerprint=?",
            (decision, reason, ctx.ws.rel(dest), db.now_iso(), row["fingerprint"]),
        )
    return {
        "file": row["file_name"],
        "decision": decision,
        "fingerprint": row["fingerprint"],
    }


__all__ = [
    "Ambiguous",
    "Context",
    "NotAwaiting",
    "NotFound",
    "RunLocked",
    "apply_human_decision",
    "guardrails",
    "make_context",
    "process_file",
    "run_inbox",
]
