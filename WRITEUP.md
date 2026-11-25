# Invoice Agent: write-up

_Draft. Fill in results after running `npm run eval`._

**Why this task.** Invoice matching is a real back-office job with a ground truth and an easy-to-explain risk: paying something you should not.

**Architecture decisions.** Deterministic matching in code, not the LLM. No file-write tools for the model. Guardrails after the decision, only ever toward more human review. Money in integer centavos.

**What did not work.** See `DEVLOG.md`.

**How I worked with the coding agent.** _3 to 5 key prompts and corrections go here._

**Results.** _Eval accuracy, false approvals, cost per invoice from `eval_report.md`._
