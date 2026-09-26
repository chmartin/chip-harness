"""Strands agents (design + evolution), models via OpenRouter.

The harness config decides each agent's system prompt, context and TOOLS, so the
evolution loop can change what the design agent sees and can do.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from strands import Agent, tool
from strands.models.openai import OpenAIModel

from . import config, db, eda, reports

OPENROUTER_URL = "https://openrouter.ai/api/v1"

# Menu the evolution agent may choose from (validated in evolve()).
REPORT_OPTIONS = ["summary_slack", "critical_path", "tier1_stats"]
TOOL_OPTIONS = ["tier1_check"]
FIX_FAMILIES = ["pipeline_register", "input_register", "accumulator_split", "output_mux_pipeline",
                "logic_restructure", "retiming", "micro_optimization"]


def _model(model_id: str, max_tokens: int = 8000, temperature: float = 0.7) -> OpenAIModel:
    return OpenAIModel(
        client_args={"api_key": os.environ["OPENROUTER_API_KEY"], "base_url": OPENROUTER_URL},
        model_id=model_id, params={"max_tokens": max_tokens, "temperature": temperature})


def _usage(result, model_id: str) -> dict:
    u = {}
    try:
        u = result.metrics.accumulated_usage or {}
    except AttributeError:
        pass
    return {"model": f"openrouter/{model_id}", "tokens_in": u.get("inputTokens"),
            "tokens_out": u.get("outputTokens"), "cost_usd": None, "trace_url": None}


def _json_block(text: str) -> dict:
    for m in reversed(re.findall(r"```json\s*(.*?)```", text, re.S)):
        try:
            return json.loads(m)
        except ValueError:
            continue
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}


# ---------------------------------------------------------------- design agent
DESIGN_FORMAT = """
Reply with exactly two fenced blocks and nothing else:
```json
{"goal": "<one sentence: what you changed and why>", "fix_family": "<family>", "bottleneck": "<what limits fmax>"}
```
```verilog
<the COMPLETE new mac_array module>
```"""

HARD_RULES = """Hard rules (checked by a testbench; violations are rejected):
- Keep module name `mac_array`, its parameters (N, ACC) and all ports exactly as they are.
- `out` must equal the selected 24-bit accumulator; up to 5 extra cycles of latency are allowed on
  both the accumulate path and the sel->out path. Accumulators wrap at 24 bits. `clear` zeroes all
  accumulators; reset (rst_n low, async) zeroes everything.
- Plain synthesizable Verilog-2005 (Yosys). No `initial` blocks, no vendor primitives."""


def propose(version: dict, parent: dict, fix_family: str, slot: int) -> dict:
    """Return {"rtl", "goal", "fix_family", "bottleneck", "llm"} or {"error"}."""
    cfg = version["config"]
    model_id = cfg["models"]["design"]
    t2 = (parent.get("stages") or {}).get("tier2") or {}
    ctx = [f"Clock target: {config.DEFAULT_CLOCK_MHZ} MHz "
           f"(period {1000 / config.DEFAULT_CLOCK_MHZ:.2f} ns)."]
    if "summary_slack" in cfg["reports_read"]:
        ctx.append(f"Parent design: worst setup slack {t2.get('wns_ns')} ns, fmax {parent['score'].get('fmax_mhz')} MHz, "
                   f"area {t2.get('area_um2')} um^2.")
    if "critical_path" in cfg["reports_read"]:
        ctx.append("Critical path of the parent (OpenROAD, after routing):\n" + reports.critical_path(parent["_id"]))
    if "tier1_stats" in cfg["reports_read"]:
        ctx.append("Yosys stats of the parent:\n" + reports.tier1_log(parent["_id"]))
    k = cfg.get("context", {}).get("lessons", 0)
    if k:
        ls = db.recent_lessons(fix_family, k) or db.recent_lessons(None, k)
        if ls:
            ctx.append("Lessons from earlier trials:\n" + "\n".join(
                f"- [{l['outcome']}, {l.get('delta_fmax_mhz')} MHz] {l['text']}" for l in ls))

    tools = []
    if "tier1_check" in cfg.get("tools", []):
        @tool
        def tier1_check(verilog: str) -> str:
            """Run the testbench and a fast Yosys timing estimate on a draft of the full mac_array module.
            Returns PASS/FAIL, failure log tail, and estimated slack in ns. Use before your final answer."""
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "mac_array.v"
                p.write_text(verilog)
                tb = eda.testbench(p, Path(d) / "tb")
                if tb["status"] != "pass":
                    return f"TESTBENCH {tb['status'].upper()}:\n{tb['log_tail']}"
                t1 = eda.tier1(p, Path(d) / "t1", 1000 / config.DEFAULT_CLOCK_MHZ)
                ref = ((parent.get("stages") or {}).get("tier1") or {}).get("est_slack_ns")
                return (f"TESTBENCH PASS. Tier1: est_slack_ns={t1.get('est_slack_ns')} (parent: {ref}; the estimate "
                        f"is pessimistic, compare relative to the parent) cells={t1.get('cells')} area={t1.get('area_um2')}")
        tools.append(tier1_check)

    system = "\n\n".join([cfg["diagnosis_prompt"], HARD_RULES,
                          "Guardrails: " + "; ".join(cfg.get("guardrails", [])), DESIGN_FORMAT])
    prompt = "\n\n".join(ctx + [f"Fix family to try this time: {fix_family} (variant #{slot}).",
                                "Parent RTL:\n```verilog\n" + parent["design"]["rtl"] + "\n```"])
    agent = Agent(model=_model(model_id), system_prompt=system, tools=tools, callback_handler=None)
    try:
        result = agent(prompt)
    except Exception as e:  # network / provider errors -> rejected trial, loop continues
        return {"error": f"{type(e).__name__}: {e}"[:500]}
    text = str(result)
    vblocks = [b for b in re.findall(r"```(?:verilog|systemverilog|v)?\s*(.*?)```", text, re.S)
               if "module mac_array" in b]
    meta = _json_block(text)
    if not vblocks:
        return {"error": "no verilog block in reply", "llm": _usage(result, model_id)}
    return {"rtl": vblocks[-1].strip() + "\n", "goal": meta.get("goal", ""),
            "fix_family": meta.get("fix_family", fix_family), "bottleneck": meta.get("bottleneck", ""),
            "llm": _usage(result, model_id)}


# ---------------------------------------------------------------- evolution agent
EVOLVE_SYSTEM = f"""You improve an AI harness that drives a chip-design agent. The design agent edits
Verilog for a MAC array to maximize fmax; every candidate is verified (testbench) and scored by a full
place-and-route flow. Progress has plateaued. Diagnose WHY from the trial history, then rewrite the
harness configuration so the next generation does better.

You may change:
- diagnosis_prompt: the design agent's system prompt (strategy, domain knowledge).
- reports_read: subset of {REPORT_OPTIONS} (what the agent is shown about the parent).
- tools: subset of {TOOL_OPTIONS} (tier1_check lets the agent test drafts before answering).
- fix_families: list of {{"name", "weight"}} from {FIX_FAMILIES}; weights are sampling priors.
- context.lessons: 0-8 past lessons injected into the prompt.
- tier2_policy.max_tier1_regression_ns: null, or 0-2. Skip full place-and-route when a candidate's
  Tier 1 slack estimate is worse than its parent's by more than this (saves ~5 min per bad candidate).
- guardrails: may add, must keep "testbench must pass".
You may NOT change the scorer, the testbench, the plateau policy or the models.

Use the trial_history tool (it queries MongoDB Atlas). Reply with:
```json
{{"rationale": "<2-4 sentences: what was wrong and what you changed>", "config": {{<full new config>}}}}
```"""


def evolve(version: dict, reason: str) -> dict:
    cfg = version["config"]
    model_id = cfg["models"]["evolution"]

    @tool
    def trial_history(version_id: str = version["_id"]) -> str:
        """Compact history of trials for a harness version (goal, family, result, fmax)."""
        rows = []
        for t in db.version_trials(version_id):
            s = t.get("score", {})
            rows.append(f"{t['_id']} fam={t.get('diagnosis', {}).get('fix_family')} "
                        f"valid={s.get('valid')} fmax={s.get('fmax_mhz')} reject={s.get('reject_reason')} "
                        f"goal={t.get('goal', '')[:140]}")
        return "\n".join(rows) or "(none)"

    agent = Agent(model=_model(model_id, temperature=0.4), system_prompt=EVOLVE_SYSTEM,
                  tools=[trial_history], callback_handler=None)
    prompt = (f"Plateau reason: {reason}. Harness version {version['_id']} stats: {json.dumps(version.get('stats'))}\n"
              f"Current config:\n```json\n{json.dumps(cfg, indent=2)}\n```")
    result = agent(prompt)
    out = _json_block(str(result))
    return {"rationale": out.get("rationale", ""), "config": sanitize_config(out.get("config") or {}, cfg),
            "llm": _usage(result, model_id)}


def sanitize_config(new: dict, old: dict) -> dict:
    """Keep the evolution agent inside the allowed search space."""
    c = json.loads(json.dumps(old))
    if isinstance(new.get("diagnosis_prompt"), str) and len(new["diagnosis_prompt"]) > 40:
        c["diagnosis_prompt"] = new["diagnosis_prompt"][:6000]
    if isinstance(new.get("reports_read"), list):
        c["reports_read"] = [r for r in new["reports_read"] if r in REPORT_OPTIONS] or ["summary_slack"]
    if isinstance(new.get("tools"), list):
        c["tools"] = [t for t in new["tools"] if t in TOOL_OPTIONS]
    fams = [f for f in new.get("fix_families", []) if isinstance(f, dict) and f.get("name") in FIX_FAMILIES]
    if fams:
        c["fix_families"] = [{"name": f["name"], "weight": float(f.get("weight", 1))} for f in fams]
    lessons = (new.get("context") or {}).get("lessons")
    if isinstance(lessons, int):
        c.setdefault("context", {})["lessons"] = max(0, min(8, lessons))
    tp = new.get("tier2_policy") or {}
    if "max_tier1_regression_ns" in tp:
        r = tp["max_tier1_regression_ns"]
        c["tier2_policy"] = {"max_tier1_regression_ns":
                             None if r is None else float(max(0.0, min(2.0, r)))}
    if isinstance(new.get("guardrails"), list):
        g = [str(x) for x in new["guardrails"]][:10]
        c["guardrails"] = g if "testbench must pass" in g else ["testbench must pass"] + g
    c["models"] = old["models"]
    return c
