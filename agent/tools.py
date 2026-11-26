"""The five tools the model may call. Errors are returned, never raised."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pdfplumber
from pydantic import BaseModel, ValidationError, model_validator

from agent import db
from agent.config import Settings
from agent.matching import compare
from agent.models import InvoiceData

MAX_TEXT = 12000


@dataclass
# State is written ONLY by tool code (never by the model). Guardrails trust this.
class InvoiceState:
    """Everything the guardrails trust: results of tools, not claims of the model."""

    file_name: str
    file_path: Path
    fingerprint: str = ""
    step: int = 0
    cost_usd: float = 0.0
    read_ok: bool = False
    duplicate_checked: bool = False
    duplicate_found: bool = False
    compare_result: dict[str, Any] | None = None
    compare_invoice: dict[str, Any] | None = None
    compare_po: str | None = None
    final: dict[str, Any] | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ToolContext:
    settings: Settings
    conn: Any
    state: InvoiceState


def err(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message, **extra}


# ---- input models (JSON schemas are generated from these) ----------------
class ReadInvoiceArgs(BaseModel):
    """Read the text of the invoice currently being processed. Takes no arguments."""


class FindPOArgs(BaseModel):
    po_number: str | None = None
    vendor_hint: str | None = None

    @model_validator(mode="after")
    def _one(self) -> FindPOArgs:
        if not self.po_number and not self.vendor_hint:
            raise ValueError("provide po_number or vendor_hint")
        return self


class CheckDuplicateArgs(BaseModel):
    vendor_name: str
    invoice_number: str


class ComparePOArgs(BaseModel):
    po_number: str
    invoice: InvoiceData


class SubmitDecisionArgs(BaseModel):
    decision: Literal["approved", "flagged", "needs_approval"]
    reason: str
    finding_codes: list[str] = []
    invoice: InvoiceData | None = None


# ---- tool implementations -------------------------------------------------
# TOOL 1 of 5 - read the PDF. Extracts text with pdfplumber; the MODEL then interprets
# that text (any layout) into structured invoice fields.
def read_invoice(_: ReadInvoiceArgs, ctx: ToolContext) -> dict[str, Any]:
    try:
        with pdfplumber.open(ctx.state.file_path) as pdf:
            pages = pdf.pages
            text = "\n".join((p.extract_text() or "") for p in pages)
            count = len(pages)
    except Exception as e:  # noqa: BLE001 - any parser failure means unreadable
        return err("unreadable_pdf", f"Could not parse PDF: {type(e).__name__}")
    if not text.strip():
        return err("empty_text", "PDF has no extractable text")
    ctx.state.read_ok = True
    return {
        "ok": True,
        "file_name": ctx.state.file_name,
        "page_count": count,
        "text": text[:MAX_TEXT],
    }


# TOOL 2 of 5 - find the PO in SQLite (by PO number, or open POs for a vendor hint).
def find_purchase_order(a: FindPOArgs, ctx: ToolContext) -> dict[str, Any]:
    if a.po_number:
        po = db.get_po(ctx.conn, a.po_number)
        if po:
            return {"ok": True, "found": True, "po": po}
    cands = db.open_pos_for_vendor(ctx.conn, a.vendor_hint, 3) if a.vendor_hint else []
    msg = f"PO '{a.po_number}' not found" if a.po_number else "No PO number given"
    return err("not_found", msg, found=False, candidates=cands)


# TOOL 3 of 5 - duplicate check against previously processed invoices in SQLite.
def check_duplicate(a: CheckDuplicateArgs, ctx: ToolContext) -> dict[str, Any]:
    existing = db.find_existing(ctx.conn, a.vendor_name, a.invoice_number)
    ctx.state.duplicate_checked = True
    ctx.state.duplicate_found = existing is not None
    slim = None
    if existing:
        slim = {k: existing[k] for k in ("file_name", "invoice_number", "decision", "decided_at")}
    return {"ok": True, "duplicate": existing is not None, "existing": slim}


# TOOL 4 of 5 - hands the invoice to agent/matching.py compare() (plain-code math).
def compare_to_po(a: ComparePOArgs, ctx: ToolContext) -> dict[str, Any]:
    po = db.get_po(ctx.conn, a.po_number)
    if not po:
        return err("not_found", f"PO '{a.po_number}' not found")
    s = ctx.settings
    res = compare(
        po,
        a.invoice,
        approval_limit_php=s.approval_limit_php,
        price_tolerance_pct=s.price_tolerance_pct,
        qty_tolerance=s.qty_tolerance,
    )
    st = ctx.state
    st.compare_result, st.compare_po = res, po["po_number"]
    st.compare_invoice = a.invoice.model_dump()
    st.findings = res["findings"]
    return {"ok": True, **res}


# TOOL 5 of 5 - the model's ONLY way to finish. It does not move or write files;
# it just proposes a decision, and guardrails.apply_decision() checks it first.
def submit_decision(a: SubmitDecisionArgs, ctx: ToolContext) -> dict[str, Any]:
    from agent.guardrails import apply_decision

    final = apply_decision(ctx.state, ctx.settings, a)
    ctx.state.final = final
    return {
        "ok": True,
        "accepted": True,
        "final_decision": final["decision"],
        "overridden_by": final["overridden_by"],
    }


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    fn: Any


# The complete menu of tools the model is allowed to use. Nothing else exists for it.
TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool(
            "read_invoice",
            "Read the text of the invoice file currently being processed.",
            ReadInvoiceArgs,
            read_invoice,
        ),
        Tool(
            "find_purchase_order",
            "Look up a PO by number. If no PO number is on the invoice, pass vendor_hint to get open POs for that vendor.",
            FindPOArgs,
            find_purchase_order,
        ),
        Tool(
            "check_duplicate",
            "Check whether this vendor + invoice number was already processed.",
            CheckDuplicateArgs,
            check_duplicate,
        ),
        Tool(
            "compare_to_po",
            "Deterministically compare the extracted invoice to a PO. Never do the arithmetic yourself; use this.",
            ComparePOArgs,
            compare_to_po,
        ),
        Tool(
            "submit_decision",
            "Submit the final decision (approved, flagged, needs_approval). This ends processing.",
            SubmitDecisionArgs,
            submit_decision,
        ),
    ]
}
ALLOWED_TOOLS = frozenset(TOOLS)


# Turns the tool list into the JSON schemas that are sent to the model each turn.
def tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.args_model.model_json_schema(),
            },
        }
        for t in TOOLS.values()
    ]


# Runs a tool the model asked for, after validating its arguments.
def dispatch(name: str, raw_args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    tool = TOOLS.get(name)
    if tool is None:
        return err("unknown_tool", f"Tool '{name}' is not allowed")
    try:
        args = tool.args_model.model_validate(raw_args)
    except ValidationError as e:
        msgs = "; ".join(f"{'.'.join(str(x) for x in er['loc'])}: {er['msg']}" for er in e.errors()[:5])
        return err("invalid_arguments", msgs)
    return tool.fn(args, ctx)
