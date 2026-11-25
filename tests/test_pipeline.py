import dataclasses
import shutil

from agent import pipeline
from agent.llm import LLMResponse
from tests.fakes import ScriptedLLM, call, full_script, path_of, timeout_script


def make(settings, llm, *pdfs):
    ctx = pipeline.make_context(settings, llm)
    for n in pdfs:
        shutil.copy(path_of(n), ctx.ws.dir("inbox") / n)
    return ctx


def events(ctx):
    return [r["event"] for r in ctx.log.read()]


def test_clean_invoice_approved(settings):
    llm = ScriptedLLM(full_script("C01", "approved", "All match."))
    ctx = make(settings, llm, "INV-MOS-0101.pdf")
    s = pipeline.run_inbox(ctx)
    assert s.results[0]["decision"] == "approved"
    f = ctx.ws.dir("approved") / "INV-MOS-0101.pdf"
    assert f.exists() and (ctx.ws.dir("approved") / "INV-MOS-0101.pdf.note.txt").exists()
    row = ctx.conn.execute("SELECT * FROM processed_invoices").fetchone()
    assert row["decision"] == "approved" and row["status"] == "done" and row["total_centavos"] == 1097600
    assert row["steps"] == 5
    ev = events(ctx)
    assert ev[0] == "run_started" and ev[-1] == "run_finished"
    assert ev.index("invoice_started") < ev.index("decision") < ev.index("file_moved")
    assert not ctx.ws.lock_path.exists()


def test_guardrail_override_then_human_approve(settings):
    llm = ScriptedLLM(full_script("C08", "approved", "Looks fine."))
    ctx = make(settings, llm, "INV-LTD-0301.pdf")
    r = pipeline.run_inbox(ctx).results[0]
    assert r["decision"] == "needs_approval" and r["decided_by"] == "guardrail"
    row = ctx.conn.execute("SELECT * FROM processed_invoices").fetchone()
    assert row["status"] == "awaiting_human"
    assert (ctx.ws.dir("needs_approval") / "INV-LTD-0301.pdf").exists()
    out = pipeline.apply_human_decision(ctx, row["fingerprint"][:8], True, "ok by CFO")
    assert out["decision"] == "approved"
    assert (ctx.ws.dir("approved") / "INV-LTD-0301.pdf").exists()
    assert not (ctx.ws.dir("needs_approval") / "INV-LTD-0301.pdf").exists()
    assert (ctx.ws.dir("approved") / "INV-LTD-0301.pdf.note.txt").exists()
    row = ctx.conn.execute("SELECT * FROM processed_invoices").fetchone()
    assert row["decided_by"] == "human" and row["decision"] == "approved"
    import pytest

    with pytest.raises(pipeline.NotAwaiting):
        pipeline.apply_human_decision(ctx, "INV-LTD-0301.pdf", True)


def test_human_reject(settings):
    ctx = make(settings, ScriptedLLM(full_script("C08", "needs_approval")), "INV-LTD-0301.pdf")
    pipeline.run_inbox(ctx)
    pipeline.apply_human_decision(ctx, "INV-LTD-0301.pdf", False, "wrong amount")
    assert (ctx.ws.dir("rejected") / "INV-LTD-0301.pdf").exists()


def test_second_run_makes_no_llm_calls(settings):
    llm = ScriptedLLM(full_script("C01", "approved"))
    ctx = make(settings, llm, "INV-MOS-0101.pdf")
    pipeline.run_inbox(ctx)
    calls = llm.calls
    assert pipeline.run_inbox(ctx).results == []
    assert llm.calls == calls


def test_exact_same_file_dropped_again(settings):
    llm = ScriptedLLM(full_script("C01", "approved"))
    ctx = make(settings, llm, "INV-MOS-0101.pdf")
    pipeline.run_inbox(ctx)
    calls = llm.calls
    shutil.copy(path_of("INV-MOS-0101.pdf"), ctx.ws.dir("inbox") / "INV-MOS-0101.pdf")
    r = pipeline.run_inbox(ctx).results[0]
    assert r["decision"] == "flagged" and r["skipped"] and llm.calls == calls
    assert (ctx.ws.dir("flagged") / "INV-MOS-0101.pdf").exists()
    assert "EXACT_DUPLICATE_FILE" in (ctx.ws.dir("flagged") / "INV-MOS-0101.pdf.note.txt").read_text()


def test_resent_invoice_number_detected_as_duplicate(settings):
    ctx = make(settings, None, "INV-MOS-0101.pdf", "INV-MOS-0108-resend.pdf")
    ctx.llm = ScriptedLLM(
        full_script("C01", "approved"),
        full_script("C07", "approved", "wrongly approving"),
    )
    rs = pipeline.run_inbox(ctx).results
    by = {r["file"]: r for r in rs}
    assert by["INV-MOS-0101.pdf"]["decision"] == "approved"
    dup = by["INV-MOS-0108-resend.pdf"]
    assert dup["decision"] == "flagged" and dup["decided_by"] == "guardrail"


def test_processing_recovered_on_start(settings):
    ctx = make(settings, ScriptedLLM(full_script("C01", "approved")))
    shutil.copy(path_of("INV-MOS-0101.pdf"), ctx.ws.dir("processing") / "INV-MOS-0101.pdf")
    s = pipeline.run_inbox(ctx)
    assert s.results[0]["decision"] == "approved"
    assert not list(ctx.ws.dir("processing").glob("*.pdf"))


def test_llm_timeout_returns_file_to_inbox_then_retries(settings):
    llm = ScriptedLLM(timeout_script(), full_script("C01", "approved"))
    ctx = make(settings, llm, "INV-MOS-0101.pdf")
    r = pipeline.run_inbox(ctx).results[0]
    assert r["decision"] == "error"
    assert (ctx.ws.dir("inbox") / "INV-MOS-0101.pdf").exists()
    row = ctx.conn.execute("SELECT * FROM processed_invoices").fetchone()
    assert row["decision"] == "error"
    assert pipeline.run_inbox(ctx).results[0]["decision"] == "approved"


def test_step_limit_forces_flag(settings):
    s = (
        dataclasses.replace(settings)
        if dataclasses.is_dataclass(settings)
        else settings.model_copy(update={"max_steps_per_invoice": 3})
    )
    loop = [call("read_invoice")] * 3
    ctx = make(s, ScriptedLLM(loop), "INV-MOS-0101.pdf")
    r = pipeline.run_inbox(ctx).results[0]
    assert r["decision"] == "flagged" and "STEP_LIMIT_REACHED" in r["codes"]


def test_per_invoice_cost_limit(settings):
    s = settings.model_copy(update={"max_cost_usd_per_invoice": 0.002})
    ctx = make(s, ScriptedLLM(full_script("C01", "approved")), "INV-MOS-0101.pdf")
    r = pipeline.run_inbox(ctx).results[0]
    assert r["decision"] == "flagged" and "COST_LIMIT_REACHED" in r["codes"]


def test_per_run_cost_limit_stops_run(settings):
    s = settings.model_copy(update={"max_cost_usd_per_run": 0.004})
    ctx = make(
        s,
        ScriptedLLM(full_script("C01", "approved"), full_script("C03", "flagged")),
        "INV-MOS-0101.pdf",
        "INV-MOS-0103.pdf",
    )
    summary = pipeline.run_inbox(ctx)
    assert summary.cost_limit_hit and len(summary.results) == 1
    assert len(list(ctx.ws.dir("inbox").glob("*.pdf"))) == 1


def test_corrupt_pdf_flagged_and_tool_errors_returned(settings):
    inv_none = [
        call("read_invoice"),
        call(
            "submit_decision",
            decision="flagged",
            reason="unreadable",
            finding_codes=["UNREADABLE"],
        ),
    ]
    ctx = make(settings, ScriptedLLM(inv_none), "INV-CORRUPT-0001.pdf")
    r = pipeline.run_inbox(ctx).results[0]
    assert r["decision"] == "flagged"
    assert any(e["data"].get("error") == "unreadable_pdf" for e in ctx.log.read(event="tool_result"))


def test_invalid_and_unknown_tool_calls_count_as_steps(settings):
    from agent.llm import ToolCall

    bad = LLMResponse(
        [ToolCall("1", "rm_rf", "{}"), ToolCall("2", "read_invoice", "not json")],
        cost_usd=0.001,
    )
    ctx = make(settings, ScriptedLLM([bad] + full_script("C01", "flagged")), "INV-MOS-0101.pdf")
    r = pipeline.run_inbox(ctx).results[0]
    assert r["steps"] == 6
    errs = [e["data"].get("error") for e in ctx.log.read(event="tool_result")]
    assert "unknown_tool" in errs and "invalid_arguments" in errs


def test_name_collision_suffix(settings):
    ctx = make(settings, ScriptedLLM(full_script("C01", "approved")), "INV-MOS-0101.pdf")
    (ctx.ws.dir("approved") / "INV-MOS-0101.pdf").write_bytes(b"x")
    pipeline.run_inbox(ctx)
    assert (ctx.ws.dir("approved") / "INV-MOS-0101-1.pdf").exists()


def test_run_lock_blocks_second_run(settings):
    import pytest

    from agent.workspace import RunLocked

    ctx = make(settings, ScriptedLLM())
    ctx.ws.acquire_lock()
    with pytest.raises(RunLocked):
        pipeline.run_inbox(ctx)
    ctx.ws.lock_path.write_text("999999")  # stale pid
    pipeline.run_inbox(ctx)
