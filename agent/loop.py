"""Per-invoice agent loop. The model picks the next tool; code enforces the limits."""

from __future__ import annotations

import json
from typing import Any

from agent import guardrails
from agent.actionlog import ActionLog
from agent.config import Settings
from agent.prompts import SYSTEM_PROMPT
from agent.tools import (
    ALLOWED_TOOLS,
    InvoiceState,
    ToolContext,
    dispatch,
    err,
    tool_schemas,
)


def _summarize(name: str, result: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"tool": name, "ok": result.get("ok")}
    if "error" in result:
        out["error"] = result["error"]
    if name == "read_invoice" and result.get("ok"):
        out["text"] = result["text"][:300]
    elif name == "compare_to_po" and result.get("ok"):
        out.update(all_match=result["all_match"], codes=[f["code"] for f in result["findings"]])
    elif name == "check_duplicate" and result.get("ok"):
        out["duplicate"] = result["duplicate"]
    elif name == "find_purchase_order":
        out["found"] = result.get("found")
    elif name == "submit_decision":
        out.update(
            final_decision=result.get("final_decision"),
            overridden_by=result.get("overridden_by"),
        )
    return out


def run_agent(llm: Any, ctx: ToolContext, log: ActionLog) -> dict[str, Any]:
    st: InvoiceState = ctx.state
    settings: Settings = ctx.settings
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"New invoice file: {st.file_name}. Process it."},
    ]
    schemas = tool_schemas()
    for step in range(1, settings.max_steps_per_invoice + 1):
        try:
            guardrails.check_cost(st, settings)
        except guardrails.CostLimitExceeded:
            log.log("guardrail", st.file_name, st.fingerprint, st.step, rule="G6")
            return guardrails.force_flag("COST_LIMIT_REACHED", "Per-invoice cost limit reached.")
        st.step = step
        resp = llm.chat(messages, schemas)
        st.cost_usd += resp.cost_usd
        log.log(
            "llm_response",
            st.file_name,
            st.fingerprint,
            step,
            cost_usd=resp.cost_usd,
            tool_calls=[c.name for c in resp.tool_calls],
            tokens_in=resp.tokens_in,
            tokens_out=resp.tokens_out,
        )
        if not resp.tool_calls:
            messages.append({"role": "assistant", "content": resp.content or ""})
            messages.append(
                {
                    "role": "user",
                    "content": "Call the next tool. Finish with submit_decision.",
                }
            )
            continue
        messages.append(
            {
                "role": "assistant",
                "content": resp.content,
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": c.args_raw},
                    }
                    for c in resp.tool_calls
                ],
            }
        )
        for call in resp.tool_calls:
            log.log(
                "tool_call",
                st.file_name,
                st.fingerprint,
                step,
                tool=call.name,
                args=call.args_raw[:300],
            )
            if call.name not in ALLOWED_TOOLS:
                result = err("unknown_tool", f"Tool '{call.name}' is not allowed")
            else:
                try:
                    args = json.loads(call.args_raw)
                    if not isinstance(args, dict):
                        raise ValueError
                    result = dispatch(call.name, args, ctx)
                except (json.JSONDecodeError, ValueError):
                    result = err("invalid_arguments", "Arguments were not a valid JSON object")
            log.log(
                "tool_result",
                st.file_name,
                st.fingerprint,
                step,
                **_summarize(call.name, result),
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, default=str),
                }
            )
            if call.name == "submit_decision" and result.get("accepted") and st.final:
                if st.final["overridden_by"]:
                    log.log(
                        "guardrail",
                        st.file_name,
                        st.fingerprint,
                        step,
                        rules=st.final["overridden_by"],
                        final=st.final["decision"],
                    )
                return st.final
    log.log("guardrail", st.file_name, st.fingerprint, st.step, rule="G5")
    return guardrails.force_flag("STEP_LIMIT_REACHED", "Step limit reached before a decision was submitted.")
