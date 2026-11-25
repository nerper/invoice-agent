from agent.guardrails import (
    CostLimitExceeded,
    apply_decision,
    check_cost,
    run_cost_exceeded,
)
from agent.models import InvoiceData
from agent.tools import InvoiceState, SubmitDecisionArgs, ToolContext, dispatch
from tests.fakes import CASES, invoice_dict, path_of


def state_after_compare(settings, conn, case_id, dup_checked=True):
    case = CASES[case_id]
    st = InvoiceState(case["file"], path_of(case["file"]))
    ctx = ToolContext(settings, conn, st)
    if dup_checked:
        dispatch(
            "check_duplicate",
            {"vendor_name": case["vendor"], "invoice_number": case["invoice_number"]},
            ctx,
        )
    dispatch(
        "compare_to_po",
        {"po_number": case["po_number"], "invoice": invoice_dict(case)},
        ctx,
    )
    return st


def submit(st, settings, decision, case_id, invoice="same"):
    inv = InvoiceData.model_validate(invoice_dict(CASES[case_id])) if invoice == "same" else invoice
    return apply_decision(st, settings, SubmitDecisionArgs(decision=decision, reason="r", invoice=inv))


def test_clean_approve_passes(settings, conn):
    st = state_after_compare(settings, conn, "C01")
    f = submit(st, settings, "approved", "C01")
    assert f["decision"] == "approved" and f["decided_by"] == "agent" and not f["overridden_by"]


def test_g1_no_compare(settings):
    st = InvoiceState("x.pdf", path_of("INV-MOS-0101.pdf"), duplicate_checked=True)
    f = submit(st, settings, "approved", "C01")
    assert f["decision"] == "flagged" and f["overridden_by"] == ["G1"] and f["decided_by"] == "guardrail"


def test_g1_block_findings_cannot_be_approved_or_needs_approval(settings, conn):
    st = state_after_compare(settings, conn, "C03")
    assert submit(st, settings, "approved", "C03")["decision"] == "flagged"
    assert submit(st, settings, "needs_approval", "C03")["decision"] == "flagged"


def test_g2_duplicate_unchecked(settings, conn):
    st = state_after_compare(settings, conn, "C01", dup_checked=False)
    f = submit(st, settings, "approved", "C01")
    assert f["decision"] == "flagged" and f["overridden_by"] == ["G2"]


def test_g3_over_limit(settings, conn):
    st = state_after_compare(settings, conn, "C08")
    f = submit(st, settings, "approved", "C08")
    assert (
        f["decision"] == "needs_approval" and f["decided_by"] == "guardrail" and f["overridden_by"] == ["G3"]
    )


def test_g4_invoice_changed(settings, conn):
    st = state_after_compare(settings, conn, "C01")
    changed = invoice_dict(CASES["C01"]) | {"total_php": "1.00"}
    f = submit(st, settings, "approved", "C01", InvoiceData.model_validate(changed))
    assert f["decision"] == "flagged" and f["overridden_by"] == ["G4"]


def test_flagged_is_never_overridden(settings, conn):
    st = state_after_compare(settings, conn, "C01")
    assert submit(st, settings, "flagged", "C01")["decided_by"] == "agent"


def test_g5_g6_g7(settings):
    import pytest

    from agent.config import Settings

    st = InvoiceState("x", path_of("INV-MOS-0101.pdf"), cost_usd=0.03)
    with pytest.raises(CostLimitExceeded):
        check_cost(st, settings)
    assert run_cost_exceeded(0.6, settings) and not run_cost_exceeded(0.1, settings)
    assert Settings().max_steps_per_invoice == 8  # G5 value used by loop (see test_pipeline)
