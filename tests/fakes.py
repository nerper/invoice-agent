"""Scripted fake LLM: returns pre-planned tool calls, no network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from agent.config import ROOT
from agent.llm import LLMError, LLMResponse, ToolCall
from scripts.make_invoices import totals

CASES = {c["id"]: c for c in yaml.safe_load((ROOT / "tests/fixtures/cases.yaml").read_text())}


def invoice_dict(case: dict) -> dict[str, Any]:
    sub, vat, total = totals(case)
    return {
        "invoice_number": case["invoice_number"],
        "vendor_name": case["vendor"],
        "vendor_tin": case["vendor_tin"],
        "invoice_date": str(case["invoice_date"]),
        "po_number": case.get("po_number"),
        "lines": [
            {
                "sku": x["sku"],
                "description": x["description"],
                "qty": x["qty"],
                "unit_price_php": str(x["unit_price"]),
            }
            for x in case["lines"]
        ],
        "subtotal_php": str(sub),
        "vat_php": str(vat),
        "total_php": str(total),
    }


def call(name: str, **args: Any) -> LLMResponse:
    return LLMResponse([ToolCall(f"c-{name}", name, json.dumps(args))], cost_usd=0.001)


def full_script(
    case_id: str,
    decision: str,
    reason: str = "scripted",
    codes: list[str] | None = None,
    po: str | None = None,
) -> list[LLMResponse]:
    case = CASES[case_id]
    inv = invoice_dict(case)
    po = po or case.get("po_number") or "PO-1001"
    return [
        call("read_invoice"),
        call(
            "check_duplicate",
            vendor_name=inv["vendor_name"],
            invoice_number=inv["invoice_number"],
        ),
        call("find_purchase_order", po_number=po),
        call("compare_to_po", po_number=po, invoice=inv),
        call(
            "submit_decision",
            decision=decision,
            reason=reason,
            finding_codes=codes or [],
            invoice=inv,
        ),
    ]


class ScriptedLLM:
    """Plays one script per invoice, in the order invoices are processed."""

    def __init__(self, *scripts: list[LLMResponse | Exception]) -> None:
        self.scripts = list(scripts)
        self.current: list[LLMResponse | Exception] = []
        self.calls = 0
        self.started = 0

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        self.calls += 1
        if len(messages) == 2:  # first turn of a new invoice
            self.current = list(self.scripts[self.started])
            self.started += 1
        item = self.current.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def timeout_script() -> list[Exception]:
    return [LLMError("LLM unavailable after retries: APITimeoutError")]


def path_of(name: str) -> Path:
    return ROOT / "tests/fixtures/pdf" / name
