"""Deterministic invoice-vs-PO comparison. Pure functions, no I/O."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from agent.models import InvoiceData, to_centavos

_SUFFIXES = {"inc", "corp", "corporation", "co", "company", "ltd", "llc"}
ARITH_TOLERANCE = 100  # centavos (PHP 1.00)


def normalize_vendor(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    while words and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words)


def _peso(centavos: int) -> str:
    return f"₱{centavos / 100:,.2f}"


def _finding(code: str, severity: str, detail: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "detail": detail}


# ---------------------------------------------------------------------------
# THE MATH LIVES HERE: plain Python + Decimal/centavos integers, no model involved.
# The model only passes in the invoice values it read; this function does every
# comparison and sum, so the model can't miscount.
# ---------------------------------------------------------------------------
def compare(
    po: dict[str, Any],
    inv: InvoiceData,
    *,
    approval_limit_php: int,
    price_tolerance_pct: float,
    qty_tolerance: int,
) -> dict[str, Any]:
    f: list[dict[str, str]] = []
    # Check 1/2: PO must be open and vendor must match (company suffixes ignored).
    if po["status"] == "closed":
        f.append(_finding("PO_CLOSED", "block", f"{po['po_number']} is closed"))
    if normalize_vendor(inv.vendor_name) != normalize_vendor(po["vendor_name"]):
        f.append(
            _finding(
                "VENDOR_MISMATCH",
                "block",
                f"Invoice vendor '{inv.vendor_name}' vs PO vendor '{po['vendor_name']}'",
            )
        )

    # Check 3: line-by-line. Look up each invoice line on the PO by SKU (else by description).
    by_sku = {x["sku"].strip().upper(): x for x in po["lines"]}
    by_desc = {x["description"].strip().lower(): x for x in po["lines"]}
    tol = Decimal(str(price_tolerance_pct))
    line_sum = 0
    for i, ln in enumerate(inv.lines, 1):
        line_sum += to_centavos(ln.unit_price_php) * ln.qty
        key = ln.sku.strip().upper() if ln.sku else None
        pl = by_sku.get(key) if key else by_desc.get(ln.description.strip().lower())
        label = f"Line {i} {ln.sku or ln.description}"
        if pl is None:
            f.append(_finding("LINE_NOT_ON_PO", "block", f"{label}: not on {po['po_number']}"))
            continue
        # Quantity compared to the PO (tolerance from config.yaml).
        if abs(ln.qty - pl["qty"]) > qty_tolerance:
            f.append(
                _finding(
                    "QTY_MISMATCH",
                    "block",
                    f"{label}: invoiced qty {ln.qty} vs PO qty {pl['qty']}",
                )
            )
        # Unit price compared in integer centavos; small % differences are only "info".
        inv_c, po_c = to_centavos(ln.unit_price_php), pl["unit_price_centavos"]
        if inv_c != po_c:
            pct = (Decimal(inv_c - po_c) * 100 / Decimal(po_c)) if po_c else Decimal(100)
            msg = (
                f"{label}: unit price {_peso(inv_c)} vs PO {_peso(po_c)} "
                f"({pct:+.2f}%, tolerance {price_tolerance_pct}%)"
            )
            f.append(
                _finding(
                    "PRICE_MISMATCH" if abs(pct) > tol else "PRICE_WITHIN_TOLERANCE",
                    "block" if abs(pct) > tol else "info",
                    msg,
                )
            )

    # Check 4: arithmetic. Do the invoice lines add up to subtotal, and subtotal + VAT to total?
    sub, vat, total = (
        to_centavos(inv.subtotal_php),
        to_centavos(inv.vat_php),
        to_centavos(inv.total_php),
    )
    if abs(line_sum - sub) > ARITH_TOLERANCE or abs(sub + vat - total) > ARITH_TOLERANCE:
        f.append(
            _finding(
                "ARITHMETIC_ERROR",
                "block",
                f"Lines sum {_peso(line_sum)}, subtotal {_peso(sub)}, VAT {_peso(vat)}, total {_peso(total)}",
            )
        )
    # Check 5: total above the approval limit (info only; guardrail G3 routes it to a human).
    over = total > approval_limit_php * 100
    if over:
        f.append(
            _finding(
                "OVER_APPROVAL_LIMIT",
                "info",
                f"Total {_peso(total)} exceeds approval limit {_peso(approval_limit_php * 100)}",
            )
        )
    # all_match is True only when no "block" finding exists. Guardrails read this later.
    return {
        "all_match": not any(x["severity"] == "block" for x in f),
        "findings": f,
        "invoice_total_php": str(inv.total_php),
        "over_approval_limit": over,
    }
