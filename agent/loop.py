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
        out.update(
            all_match=result["all_match"], codes=[f["code"] for f in result["findings"]]
        )
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


# ---------------------------------------------------------------------------
# THE TOOL-CALLING LOOP (one invoice). Each turn:
#   1. send the conversation + tool list to the model         (llm.chat)
#   2. the model replies with which tool to call next
#   3. code runs that tool and appends the result to the chat (dispatch)
#   4. repeat, so the model picks the next tool from the last result
# It ends when submit_decision is accepted, or a step/cost limit forces a flag.
# ---------------------------------------------------------------------------
def run_agent(llm: Any, ctx: ToolContext, log: ActionLog) -> dict[str, Any]:
    st: InvoiceState = ctx.state
    settings: Settings = ctx.settings
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"New invoice file: {st.file_name}. Process it."},
    ]
    schemas = tool_schemas()
    # Hard cap on turns so a confused model can't loop forever (guardrail G5, see bottom).
    for step in range(1, settings.max_steps_per_invoice + 1):
        try:
            guardrails.check_cost(st, settings)
        except guardrails.CostLimitExceeded:
            log.log("guardrail", st.file_name, st.fingerprint, st.step, rule="G6")
            return guardrails.force_flag(
                "COST_LIMIT_REACHED", "Per-invoice cost limit reached."
            )
        st.step = step
        # Ask the model what to do next.
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
        # Run each tool the model asked for.
        for call in resp.tool_calls:
            log.log(
                "tool_call",
                st.file_name,
                st.fingerprint,
                step,
                tool=call.name,
                args=call.args_raw[:300],
            )
            # Only the 5 whitelisted tools can run; anything else is rejected.
            if call.name not in ALLOWED_TOOLS:
                result = err("unknown_tool", f"Tool '{call.name}' is not allowed")
            else:
                try:
                    args = json.loads(call.args_raw)
                    if not isinstance(args, dict):
                        raise ValueError
                    result = dispatch(call.name, args, ctx)
                except (json.JSONDecodeError, ValueError):
                    result = err(
                        "invalid_arguments", "Arguments were not a valid JSON object"
                    )
            log.log(
                "tool_result",
                st.file_name,
                st.fingerprint,
                step,
                **_summarize(call.name, result),
            )
            # Feed the tool result back so the model can choose the next tool from it.
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(result, default=str),
                }
            )
            # submit_decision ends the loop; st.final already holds the guardrail-checked decision.
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
    # Ran out of steps without a decision: code flags it for a human (G5).
    log.log("guardrail", st.file_name, st.fingerprint, st.step, rule="G5")
    return guardrails.force_flag(
        "STEP_LIMIT_REACHED", "Step limit reached before a decision was submitted."
    )
