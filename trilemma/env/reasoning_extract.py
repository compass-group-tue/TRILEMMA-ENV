"""Merge the models' full reasoning + step structure from proxy_dump.jsonl into run.json.

opencode surfaces reasoning in its response parts only sometimes, so ``turns[].reasoning``
is patchy. The proxy dump has the raw traffic of every request, which yields the complete
picture from two complementary sources:

- **Step structure** (tool calls + text per model call) comes from the *final request's
  message history*: opencode appends one assistant message per model call, carrying its
  tool_calls (name + arguments) and content verbatim. This survives any dump truncation
  of the streamed response.
- **Reasoning** comes from the streamed SSE (``delta.reasoning`` chunks), which is the
  only place it exists. A step whose dump line was truncated (older runs capped
  response_text at 400k, and SSE overhead is ~30x payload) keeps its reasoning prefix and
  is tagged ``reasoning_truncated``.

Correlation needs no timestamps: requests within one turn share the conversation, and a
new turn adds exactly one user message — so a request's user-message count is the agent's
turn ordinal, and same-count requests are that turn's steps in wall order.

Adds, per turn:
  ``steps_detail``      [{step, reasoning, text}]
  ``steps``             model-call count
  ``tool_calls[].step`` which model call asked for it
  ``reasoning_full``    flat list of per-step reasoning (for scripts)

Idempotent.  Run:  python -m trilemma.env.reasoning_extract <run.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

MCP_PREFIXES = ("tanager_", "slack_")
OLD_TRUNCATION = 400_000


def _sse_reasoning(response_text: str) -> str:
    parts: List[str] = []
    for line in response_text.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        try:
            delta = json.loads(line[6:])["choices"][0]["delta"]
        except (json.JSONDecodeError, LookupError):
            continue
        if delta.get("reasoning"):
            parts.append(delta["reasoning"])
    return "".join(parts)


# ---- Responses API: the gpt-5.x homes built with homes api="responses" ----
# The dump line's path ends in /responses; the request carries ``input`` items instead of
# ``messages``, and the SSE is typed events instead of chat chunks. The reasoning here is
# the deployment's *summary* of its chain of thought (reasoning.summary=auto), which is all
# these models ever return; it is stored in the same field as an open model's full CoT.

def _is_responses(d: Dict[str, Any]) -> bool:
    return str(d.get("path", "")).endswith("/responses")


def _resp_events(response_text: str):
    for line in response_text.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        try:
            yield json.loads(line[6:])
        except json.JSONDecodeError:
            continue


def _resp_reasoning(response_text: str) -> str:
    parts: List[str] = []
    streamed = False
    for ev in _resp_events(response_text):
        t = ev.get("type", "")
        if t == "response.reasoning_summary_text.delta":
            parts.append(ev.get("delta") or ""); streamed = True
        elif t == "response.reasoning_summary_part.added" and parts:
            parts.append("\n\n")
    if streamed:
        return "".join(parts)
    try:  # non-streaming body
        body = json.loads(response_text)
        return "\n\n".join(s.get("text", "") for o in body.get("output", [])
                           if o.get("type") == "reasoning" for s in o.get("summary", []))
    except (json.JSONDecodeError, AttributeError):
        return ""


def _resp_text(response_text: str) -> str:
    parts = [ev.get("delta") or "" for ev in _resp_events(response_text)
             if ev.get("type") == "response.output_text.delta"]
    if parts:
        return "".join(parts)
    try:
        body = json.loads(response_text)
        return "".join(c.get("text", "") for o in body.get("output", []) if o.get("type") == "message"
                       for c in o.get("content", []) if c.get("type") == "output_text")
    except (json.JSONDecodeError, AttributeError):
        return ""


def _resp_n_users(request: Dict[str, Any]) -> int:
    items = request.get("input") or []
    if isinstance(items, str):
        return 1
    return sum(1 for it in items if isinstance(it, dict) and it.get("role") == "user")


def _resp_history_steps(final_request: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Assistant output after the last user item = steps 1..K-1. A step is the run of
    message / function_call items up to the function_call_output items that answer it."""
    items = final_request.get("input") or []
    if isinstance(items, str):
        return []
    last_user = max((i for i, it in enumerate(items) if isinstance(it, dict) and it.get("role") == "user"), default=-1)
    steps: List[Dict[str, Any]] = []
    cur: Dict[str, Any] | None = None
    for it in items[last_user + 1:]:
        if not isinstance(it, dict):
            continue
        typ = it.get("type") or ("message" if "role" in it else "")
        if typ == "function_call_output":
            if cur is not None:
                steps.append(cur); cur = None
            continue
        if typ == "message" and it.get("role") == "assistant":
            content = it.get("content")
            if isinstance(content, list):
                content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
            cur = cur or {"text": "", "tool_calls": []}
            cur["text"] += content or ""
        elif typ == "function_call":
            raw = str(it.get("name") or "")
            name = next((raw[len(px):] for px in MCP_PREFIXES if raw.startswith(px)), raw)
            cur = cur or {"text": "", "tool_calls": []}
            cur["tool_calls"].append({"name": name, "arguments": it.get("arguments")})
        # reasoning items carry no text on the wire (only ids); the summary is in the SSE
    if cur is not None:
        steps.append(cur)
    return steps


def _sse_text(response_text: str) -> str:
    parts: List[str] = []
    for line in response_text.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        try:
            delta = json.loads(line[6:])["choices"][0]["delta"]
        except (json.JSONDecodeError, LookupError):
            continue
        if delta.get("content"):
            parts.append(delta["content"])
    return "".join(parts)


def _args_equal(raw: Any, args: Any) -> bool:
    """Do a history call's arguments and a recorded call's args describe the same call?

    History stores the model's raw JSON argument string; the run record stores the parsed
    dict. Compare parsed, and treat "no arguments recorded on one side" as no evidence
    either way rather than as a mismatch.
    """
    if raw in (None, "") or args is None:
        return True
    try:
        return json.loads(raw) == args
    except (json.JSONDecodeError, TypeError):
        return False


def _assign_steps(calls: List[Dict[str, Any]], step_calls: List[List[Dict[str, Any]]],
                  n_steps: int) -> None:
    """Stamp every recorded call with the model call that asked for it.

    The previous version walked a flat queue of tool *names* and, on any mismatch, discarded
    queue entries until one matched. A single step that issues parallel tool calls is enough
    to break it: the calls execute in whatever order they finish, which need not be the order
    the model emitted them, so the matcher throws away the entries it skipped past and every
    later call is then matched against a queue short by that many. The damage cascades — one
    reordering early in a long turn left 41 of 48 calls unattributed in a measured run — and
    it degrades worst in exactly the long, tool-heavy turns whose reasoning matters most.

    Instead: a step's calls are a *set*, matched on name and arguments, consumed as they are
    found. Steps are searched forward from the last one matched, so ordering across steps is
    still respected, but reordering within a step costs nothing and nothing is ever discarded.
    Calls left over once the history is exhausted belong to the final model call, whose
    tool_calls never re-enter the history — they get ``n_steps`` rather than 0.
    """
    pools = [list(sc) for sc in step_calls]
    at = 0
    for call in calls:
        placed = False
        for s in range(at, len(pools)):
            pool = pools[s]
            hit = next((j for j, c in enumerate(pool) if c["name"] == call["tool"]
                        and _args_equal(c.get("arguments"), call.get("args"))), None)
            if hit is None:
                hit = next((j for j, c in enumerate(pool) if c["name"] == call["tool"]), None)
            if hit is not None:
                pool.pop(hit)
                call["step"] = s + 1
                at, placed = s, True
                break
        if not placed:
            # Nothing left to match: the final model call's calls, or a call the history
            # never carried. n_steps is right for the former and honest for the latter.
            call["step"] = n_steps if all(not p for p in pools[at:]) else 0


def _history_steps(final_request: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Assistant messages after the last user message = steps 1..K-1, in order."""
    messages = final_request.get("messages") or []
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    steps = []
    for m in messages[last_user + 1:]:
        if m.get("role") != "assistant":
            continue
        calls = []
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw = str(fn.get("name") or "")
            name = next((raw[len(px):] for px in MCP_PREFIXES if raw.startswith(px)), raw)
            calls.append({"name": name, "arguments": fn.get("arguments")})
        content = m.get("content")
        if isinstance(content, list):  # content-parts form
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        steps.append({"text": content or "", "tool_calls": calls})
    return steps


def enrich(run_path: str | Path) -> Path:
    run_path = Path(run_path)
    r = json.loads(run_path.read_text())
    dump_path = run_path.parent / "proxy_dump.jsonl"

    groups: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    for line in dump_path.read_text().splitlines():
        d = json.loads(line)
        if d["path"].endswith("/chat/completions") and d.get("request"):
            n_users = sum(1 for m in d["request"].get("messages", []) if m.get("role") == "user")
            groups.setdefault((d.get("agent") or "", n_users), []).append(d)
        elif _is_responses(d) and d.get("request"):
            groups.setdefault((d.get("agent") or "", _resp_n_users(d["request"])), []).append(d)

    ordinal: Dict[str, int] = {}
    filled = total_steps = truncated = 0
    for t in r["turns"]:
        ordinal[t["agent"]] = ordinal.get(t["agent"], 0) + 1
        requests = groups.get((t["agent"], ordinal[t["agent"]]), [])
        responses_api = bool(requests) and _is_responses(requests[-1])
        history = ((_resp_history_steps if responses_api else _history_steps)(requests[-1]["request"])
                   if requests else [])

        details: List[Dict[str, Any]] = []
        step_calls: List[List[Dict[str, Any]]] = []
        for i, req in enumerate(requests, 1):
            resp = req.get("response_text") or ""
            was_cut = len(resp) >= OLD_TRUNCATION
            if i <= len(history):  # structure from the next requests' history
                step_src = history[i - 1]
            else:  # the final step never re-enters history; its text is in the SSE
                step_src = {"text": (_resp_text if responses_api else _sse_text)(resp), "tool_calls": []}
            detail = {"step": i, "reasoning": (_resp_reasoning if responses_api else _sse_reasoning)(resp),
                      "text": step_src["text"]}
            if was_cut:
                detail["reasoning_truncated"] = True
                truncated += 1
            details.append(detail)
            step_calls.append(list(step_src["tool_calls"]))

        t["steps"] = len(details)
        t["steps_detail"] = details
        t["reasoning_full"] = [d["reasoning"] for d in details]
        _assign_steps(t["tool_calls"], step_calls, len(details))
        if any(d["reasoning"].strip() for d in details):
            filled += 1
        total_steps += len(details)

    run_path.write_text(json.dumps(r, indent=2, ensure_ascii=False))
    unassigned = sum(1 for t in r["turns"] for c in t["tool_calls"] if not c.get("step"))
    print(f"{filled}/{len(r['turns'])} turns with reasoning, {total_steps} steps, "
          f"{unassigned} unassigned calls, {truncated} truncated-reasoning steps")
    return run_path


if __name__ == "__main__":
    enrich(sys.argv[1])
