"""Guardrails: plain code applied to the model's decision. Only ever moves toward more review."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from agent.config import Settings
from agent.tools import InvoiceState, SubmitDecisionArgs


class CostLimitExceeded(Exception):
    pass


def check_cost(state: InvoiceState, settings: Settings) -> None:  # G6
    if state.cost_usd >= settings.max_cost_usd_per_invoice:
        raise CostLimitExceeded(f"invoice cost ${state.cost_usd:.4f} reached cap")


def run_cost_exceeded(run_cost: float, settings: Settings) -> bool:  # G7
    return run_cost >= settings.max_cost_usd_per_run


def force_flag(code: str, reason: str) -> dict[str, Any]:  # G5 / G6
    return {
        "decision": "flagged",
        "reason": reason,
        "codes": [code],
        "decided_by": "guardrail",
        "overridden_by": [code],
        "invoice": None,
        "submitted": None,
    }


def apply_decision(state: InvoiceState, settings: Settings, a: SubmitDecisionArgs) -> dict[str, Any]:
    decision, codes, overridden = a.decision, list(a.finding_codes), []
    reason = a.reason

    def override(to: str, gid: str, code: str, why: str) -> None:
        nonlocal decision, reason
        decision = to
        codes.append(code)
        overridden.append(gid)
        reason = f"{reason} [Guardrail {gid}: {why}]"

    if decision in ("approved", "needs_approval"):
        cr = state.compare_result
        if not cr or not cr["all_match"]:  # G1
            override("flagged", "G1", "GUARDRAIL_NO_MATCH", "no passing compare_to_po result")
        elif not state.duplicate_checked or state.duplicate_found:  # G2
            override(
                "flagged",
                "G2",
                "GUARDRAIL_DUPLICATE_UNCHECKED",
                "duplicate not cleared",
            )
        elif a.invoice is None or a.invoice.model_dump() != state.compare_invoice:  # G4
            override(
                "flagged",
                "G4",
                "GUARDRAIL_INVOICE_MISMATCH",
                "submitted invoice differs from the one compared",
            )
    if decision == "approved" and state.compare_invoice:  # G3
        total = Decimal(str(state.compare_invoice["total_php"]))
        if total > settings.approval_limit_php:
            override(
                "needs_approval",
                "G3",
                "OVER_APPROVAL_LIMIT",
                "total above approval limit",
            )
    return {
        "decision": decision,
        "reason": reason,
        "codes": codes,
        "decided_by": "guardrail" if overridden else "agent",
        "overridden_by": overridden or None,
        "invoice": a.invoice,
        "submitted": a.decision,
    }
