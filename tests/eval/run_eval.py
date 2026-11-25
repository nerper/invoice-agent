"""Real-model eval over the 13 labelled invoices. Usage: python -m tests.eval.run_eval [--repeats N] [--model M]"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

from agent import pipeline
from agent.config import ROOT, load_settings
from agent.llm import OpenRouterLLM

CASES = yaml.safe_load((ROOT / "tests/fixtures/cases.yaml").read_text())


def run_once(settings) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        s = settings.model_copy(update={"workspace_dir": tmp, "max_cost_usd_per_run": 5.0})
        ctx = pipeline.make_context(s, OpenRouterLLM(s))
        rows = []
        for case in CASES:  # ID order, shared DB: C07 must follow C01
            shutil.copy(
                ROOT / "tests/fixtures/pdf" / case["file"],
                ctx.ws.dir("inbox") / case["file"],
            )
            res = pipeline.run_inbox(ctx, "cli").results[0]
            exp = case["expected"]
            rows.append(
                {
                    "id": case["id"],
                    "expected": exp["decision"],
                    "actual": res["decision"],
                    "submitted": res.get("submitted") or res["decision"],
                    "codes_ok": set(exp["codes"]) <= set(res.get("codes") or []),
                    "steps": res["steps"],
                    "cost": res["cost_usd"],
                    "override": res.get("decided_by") == "guardrail",
                }
            )
        return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--model")
    args = ap.parse_args()
    s = load_settings()
    if args.model:
        s = s.model_copy(update={"model": args.model})
    all_rows: list[list[dict]] = []
    for i in range(args.repeats):
        print(f"repeat {i + 1}/{args.repeats} ...", file=sys.stderr)
        all_rows.append(run_once(s))
    flat = [r for rows in all_rows for r in rows]
    n = len(flat)
    acc = sum(r["actual"] == r["expected"] for r in flat) / n
    agent_acc = sum(r["submitted"] == r["expected"] for r in flat) / n
    false_app = sum(r["actual"] == "approved" and r["expected"] != "approved" for r in flat)
    cost = sum(r["cost"] for r in flat)
    lines = [
        f"# Eval report\n\nModel: `{s.model}`  Repeats: {args.repeats}\n",
        "| Case | Expected | Actual | Agent-only | Codes | Steps | Cost | Guardrail |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in all_rows[-1]:
        lines.append(
            f"| {r['id']} | {r['expected']} | {r['actual']} | {r['submitted']} | "
            f"{'ok' if r['codes_ok'] else 'MISSING'} | {r['steps']} | ${r['cost']:.4f} | "
            f"{'override' if r['override'] else '-'} |"
        )
    lines += [
        "",
        f"- Decision accuracy (final): **{acc:.1%}**",
        f"- Agent-only accuracy (before guardrails): **{agent_acc:.1%}**",
        f"- False approvals: **{false_app}**",
        f"- Total cost: ${cost:.4f}  Mean steps: {sum(r['steps'] for r in flat) / n:.1f}",
    ]
    report = "\n".join(lines) + "\n"
    Path(ROOT / "eval_report.md").write_text(report)
    print(report)
    return 1 if false_app > 0 or acc < 0.9 else 0


if __name__ == "__main__":
    sys.exit(main())
