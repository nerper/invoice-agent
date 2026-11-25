"""CLI entry: python -m agent.main run | watch | approve | reject | status | ui"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from agent import pipeline
from agent.config import load_settings
from agent.llm import LLMError, OpenRouterLLM
from agent.workspace import RunLocked


def _live(rec: dict) -> None:
    ev, d = rec["event"], rec["data"]
    inv = rec.get("invoice") or ""
    if ev == "invoice_started":
        print(f"\n▶ {inv}")
    elif ev == "tool_call":
        print(f"  step {rec['step']}: {d['tool']}")
    elif ev == "tool_result" and d.get("error"):
        print(f"     ! {d['error']}")
    elif ev == "decision":
        tag = f" [GUARDRAIL {','.join(d['overridden_by'])}]" if d.get("overridden_by") else ""
        print(f"  ⇒ {d['decision'].upper()}{tag}: {d['reason']}")
    elif ev == "invoice_skipped":
        print(f"  ⇒ skipped ({d['code']})")
    elif ev == "error":
        print(f"  ✗ {d.get('message')}")
    elif ev == "guardrail" and d.get("rule") == "G7":
        print("  ✗ run cost limit reached; remaining files stay in inbox/")


def _table(results: list[dict]) -> None:
    print(f"\n{'FILE':34} {'DECISION':15} {'BY':10} {'STEPS':>5} {'COST':>9}")
    for r in results:
        print(
            f"{r['file'][:34]:34} {r['decision']:15} {(r.get('decided_by') or '-'):10} "
            f"{r['steps']:>5} ${r['cost_usd']:.4f}"
        )
    print(f"{'TOTAL':34} {'':15} {'':10} {'':>5} ${sum(r['cost_usd'] for r in results):.4f}")


def cmd_run(args: argparse.Namespace) -> int:
    s = load_settings()
    ctx = pipeline.make_context(s)
    ctx.log.listeners.append(_live)
    if not args.dry_check:
        ctx.llm = OpenRouterLLM(s)
    try:
        summary = pipeline.run_inbox(ctx, "cli", Path(args.file) if args.file else None, args.max_cost)
    except RunLocked as e:
        print(f"Cannot run: {e}", file=sys.stderr)
        return 1
    if not summary.results:
        print("Inbox is empty. Try: npm run demo:reset")
    else:
        _table(summary.results)
    return 2 if summary.cost_limit_hit else 0


def cmd_watch(_: argparse.Namespace) -> int:
    s = load_settings()
    ctx = pipeline.make_context(s, OpenRouterLLM(s))
    ctx.log.listeners.append(_live)
    print(f"Watching {ctx.ws.dir('inbox')} every {s.watch_interval_s}s. Ctrl+C to stop.")
    try:
        while True:
            if ctx.ws.inbox_files():
                try:
                    pipeline.run_inbox(ctx, "watch")
                except RunLocked:
                    pass
            time.sleep(s.watch_interval_s)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def cmd_human(args: argparse.Namespace, approve: bool) -> int:
    ctx = pipeline.make_context(load_settings())
    try:
        r = pipeline.apply_human_decision(ctx, args.id, approve, args.note if approve else args.reason)
    except pipeline.PipelineError as e:
        print(str(e), file=sys.stderr)
        return e.code
    print(f"{r['file']}: {r['decision']}")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    ctx = pipeline.make_context(load_settings())
    for k, v in ctx.ws.counts().items():
        print(f"{k:15} {v}")
    row = ctx.conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
    if row:
        print(f"\nlast run: {row['invoices']} invoices, ${row['cost_usd']:.4f} ({row['trigger']})")
    waiting = ctx.conn.execute(
        "SELECT file_name, substr(fingerprint,1,8) fp FROM processed_invoices WHERE status='awaiting_human'"
    ).fetchall()
    for w in waiting:
        print(f"awaiting human: {w['file_name']} ({w['fp']})")
    return 0


def cmd_ui(_: argparse.Namespace) -> int:
    import uvicorn

    s = load_settings()
    print(f"Invoice Agent UI: http://{s.ui.host}:{s.ui.port}")
    uvicorn.run(
        "web.app:create_app",
        factory=True,
        host=s.ui.host,
        port=s.ui.port,
        log_level="warning",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--file")
    r.add_argument("--max-cost", type=float)
    r.add_argument("--dry-check", action="store_true", help=argparse.SUPPRESS)
    sub.add_parser("watch")
    a = sub.add_parser("approve")
    a.add_argument("id")
    a.add_argument("--note", default="")
    j = sub.add_parser("reject")
    j.add_argument("id")
    j.add_argument("--reason", required=True)
    sub.add_parser("status")
    sub.add_parser("ui")
    args = p.parse_args(argv)
    try:
        return {
            "run": cmd_run,
            "watch": cmd_watch,
            "status": cmd_status,
            "ui": cmd_ui,
            "approve": lambda x: cmd_human(x, True),
            "reject": lambda x: cmd_human(x, False),
        }[args.cmd](args)
    except LLMError as e:
        print(f"LLM error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
