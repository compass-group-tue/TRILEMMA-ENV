"""Pass-through proxy to the model provider that dumps every request/response pair per agent.

Each assistant's home points its provider at ``http://127.0.0.1:<port>/a/<agent>/api/v1``;
the ``/a/<agent>`` prefix is stripped before forwarding and recorded on the dump line, so
one proxy serves all four assistants and the dump still says whose call each request was.

The dump is the ground-truth transcript layer: the exact system prompt, tools array, and
raw (possibly SSE) response text — including reasoning_content, whether or not opencode
surfaces it. SSE responses are buffered whole; with one assistant acting at a time that
costs nothing.

Two upstreams:

* ``--upstream openrouter`` (default) — verbatim forward to openrouter.ai. Unchanged.
* ``--upstream azure`` — forward to the Azure OpenAI resource named by
  ``AZURE_OPENAI_ENDPOINT``, on its OpenAI-compatible ``/openai/v1`` path. opencode keeps
  speaking to its OpenRouter provider; this leg translates. Three things differ and each
  is handled here, so no harness code above knows about it:

  - **auth and address** — ``api-key`` header instead of ``Authorization: Bearer``, and
    ``/api/v1/...`` becomes ``/openai/v1/...`` (no api-version on that path);
  - **body** — Azure 400s on any unknown parameter, and the OpenRouter provider sends
    several (``usage: {include: true}`` above all). The body is filtered to
    :data:`AZURE_PARAMS`, ``max_tokens`` is renamed to ``max_completion_tokens`` (the
    gpt-5 deployments reject the former outright), and streaming requests get
    ``stream_options.include_usage`` so opencode still gets its token counts back;
  - **429s** — one deployment serving a whole roster at once is exactly the concurrency
    ceiling. Retries with backoff live here because a 5xx
    or 429 that reaches opencode kills the turn.

  Measured against a live gpt-5.4 deployment: tools,
  streaming, ``temperature: 0.7`` and ``reasoning_effort`` all accepted;
  ``max_tokens``, ``usage``, ``parallel_tool_calls`` and ``stream_options`` on a
  non-streaming call all rejected 400. **No reasoning content comes back** — that is a
  property of the deployment, so ``steps_detail[].reasoning`` will be empty for these
  runs and the private debrief is the only introspective surface left.

OpenRouter-leg request options (all off by default, each recorded in the dump because it
rewrites the outbound body):

* ``--reasoning on|off`` sets OpenRouter's ``reasoning: {enabled: …}``. Some hosts serve a
  hybrid model with thinking off unless asked (deepseek-v4-pro, kimi-k2.6 on several
  providers), which silently turns a reasoning run into a no-reasoning one. The configs set
  it wherever the paper's runs depended on the provider's default; see configs/models.yaml.
* ``--pin-provider`` / ``--ignore-providers`` constrain OpenRouter's backend choice.

Run:  python -m trilemma.env.proxy --out <dir> [--port 8899] [--upstream azure]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import ssl
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import certifi
import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

UPSTREAM = "https://openrouter.ai"
HOP_BY_HOP = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding"}

DUMP: Path = None  # type: ignore[assignment]

# opencode stamps the real wall-clock date into its <env> block ("Today's date: Sat Aug
# 29 2026", JS toDateString format) — model-visible, and it contradicts a simulated
# calendar. --spoof-date rewrites it to the simulated date before forwarding; the dump
# records the rewritten body (what the model actually saw).
SPOOF_DATE: bytes | None = None
#: ``--model-alias ALIAS=REAL``: the home names the model ALIAS (which is what opencode
#: tells the model it is "powered by"), the request that leaves carries REAL — for a served
#: model whose id would say more about the experiment than the model. The dump records the
#: real id (what was sent) plus the alias, so the audit trail keeps both.
MODEL_ALIAS: Tuple[str, str] | None = None
_DATE_RE = re.compile(rb"Today's date: [A-Z][a-z]{2} [A-Z][a-z]{2} \d{2} \d{4}")

# ----------------------------------------------------------------------- azure
MODE = "openrouter"
#: OpenRouter provider pin, or "" for the router's own choice (the default). A
#: comma-separated list is an ordered allow-list (``order`` with fallbacks off).
PIN_PROVIDER = ""
#: OpenRouter backends never to route to (``provider.ignore``), comma-separated; applied
#: with or without a pin. "anthropic/fast" keeps Opus off Anthropic's 2x-priced fast lane
#: while leaving the rest of the pool to the router.
IGNORE_PROVIDERS = ""
#: ``--reasoning on|off``: OpenRouter's ``reasoning.enabled``; None leaves the body alone.
REASONING: bool | None = None
UPSTREAM_BASE = ""   # azure resource / gateway base, set from the environment
VERIFY: Any = True   # httpx verify: True, or a path to a CA bundle (--ca-bundle)

#: Where each translated upstream lives and how it authenticates.
UPSTREAMS = {
    #  mode:     (env var for the base, default base, auth header)
    "azure":   ("AZURE_OPENAI_ENDPOINT", "",            "api-key"),
}
#: Upstreams whose body is forwarded verbatim; the others go through the Azure whitelist.
PASSTHROUGH_BODY: set = set()

#: ``/api/v1`` (what opencode's OpenRouter provider calls) rewritten per upstream.
API_PREFIX = {"azure": "/openai/v1"}

#: Body parameters the Azure OpenAI ``/openai/v1/chat/completions`` path accepts. A
#: whitelist, not a blacklist: the failure mode to avoid is a 400 on a parameter some
#: future opencode version starts sending, and a dropped parameter is recoverable
#: (the dump records what was dropped) while a dead turn is not.
AZURE_PARAMS = {
    "model", "messages", "tools", "tool_choice", "stream", "stream_options",
    "temperature", "top_p", "max_completion_tokens", "stop", "seed", "n",
    "response_format", "reasoning_effort", "presence_penalty", "frequency_penalty",
    "logit_bias", "logprobs", "top_logprobs", "user", "metadata", "store", "modalities",
}

#: Status codes worth a retry, and the backoff ladder (seconds) between attempts. Azure
#: only: adding retries to the OpenRouter leg would change how every paper run
#: behaves under load.
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}
RETRY_BACKOFF = (2, 5, 10, 20, 40)

#: OpenRouter leg: a 400 whose body carries ``"pre_commit": true`` is the router rejecting
#: the request *before* inference (seen as streaks of ``unsupported_operation`` from one
#: backend): nothing billed, the model never ran, opencode gives up after its own six tries
#: and the runner would log an empty turn. Retrying it changes no sampling, so this one case gets a ladder of its own (~5 min,
#: under the 600 s turn timeout). When the ladder is exhausted, or when one agent has had
#: DEAD_AFTER consecutive non-200 final answers (whatever the status), the proxy writes
#: ``dead_seat.json`` next to the dump and the runner aborts the whole run: an assistant that
#: is silently not there is worse than no run. ``--dead-after 0`` disables the marker.
PRECOMMIT_BACKOFF = (5, 10, 15, 20, 30, 45, 60, 60, 60)
DEAD_AFTER = 6
DEAD_FILE: Path = None  # type: ignore[assignment]
STATUS_LOG: Path = None  # type: ignore[assignment]
_FAILS: Dict[str, int] = {}


#: Where each upstream's credential comes from.
KEY_VAR = {"azure": "AZURE_OPENAI_API_KEY"}


def translate(path: str, headers: Dict[str, str], body: bytes) -> Tuple[str, Dict[str, str], bytes, Dict[str, Any]]:
    """Rewrite an OpenAI-compatible OpenRouter call for the Azure upstream."""
    # opencode's OpenRouter provider calls /api/v1/..., its OpenAI provider /v1/... (the
    # Responses-API home, homes api="responses"); both land on the upstream's v1 prefix.
    url = UPSTREAM_BASE + re.sub(r"^(/api)?/v1", API_PREFIX[MODE], path)
    headers = {k: v for k, v in headers.items() if k.lower() not in ("authorization", "api-key")}
    key = os.environ[KEY_VAR[MODE]]
    if UPSTREAMS[MODE][2] == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    else:
        headers["api-key"] = key
    dropped: Dict[str, Any] = {}
    if not body:
        return url, headers, body, dropped
    try:
        payload: Dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        return url, headers, body, dropped
    if not isinstance(payload, dict):
        return url, headers, body, dropped
    if MODE in PASSTHROUGH_BODY:
        # Verbatim, except that streamed usage needs the OpenAI spelling: with only
        # OpenRouter's `usage: {include: true}` the final usage chunk never comes, and
        # opencode's token/cost accounting would read zero.
        if payload.get("stream") and "stream_options" not in payload:
            payload["stream_options"] = {"include_usage": True}
            body = json.dumps(payload).encode()
        return url, headers, body, dropped
    if path.endswith("/responses"):
        # Responses API: the ai-sdk OpenAI provider already speaks the deployment's
        # dialect (max_output_tokens, reasoning.{effort,summary}), so nothing is
        # filtered — a chat-completions whitelist would strip the request bare.
        return url, headers, body, dropped
    if "max_tokens" in payload:
        payload["max_completion_tokens"] = payload.pop("max_tokens")
    out = {}
    for k, v in payload.items():
        if k in AZURE_PARAMS:
            out[k] = v
        else:
            # Value and all: opencode sends `reasoningEffort` (an ai-sdk camelCase leak)
            # that OpenRouter ignores too, and a dropped parameter must stay auditable.
            dropped[k] = v
    if out.get("stream"):
        # OpenRouter's `usage: {include: true}` is what opencode sends and what was just
        # dropped; this is the OpenAI spelling of the same request.
        out["stream_options"] = {"include_usage": True}
    else:
        out.pop("stream_options", None)
    return url, headers, json.dumps(out).encode(), dropped


def pin_provider(body: bytes) -> bytes:
    """Constrain an OpenRouter call's backends: an ordered allow-list and/or an ignore list.

    Unpinned, the router scatters across backends of differing speed and quantization;
    under scatter a slow backend can produce answers that never come, and it is the
    heaviest turns that cross the timeout and die. Fallbacks outside the list stay off on purpose: a
    degraded pin should fail fast and visibly rather than silently reroute into the
    scatter it was meant to prevent. A multi-entry PIN_PROVIDER is tried in order
    (OpenRouter ``order``), which is the sanctioned fallback: named, not the router's.
    """
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body
    if not isinstance(payload, dict):
        return body
    if REASONING is not None:
        payload["reasoning"] = {**(payload.get("reasoning") or {}), "enabled": REASONING}
    prefs: Dict[str, Any] = {}
    if PIN_PROVIDER:
        prefs["order"] = [p.strip() for p in PIN_PROVIDER.split(",") if p.strip()]
        prefs["allow_fallbacks"] = False
    if IGNORE_PROVIDERS:
        prefs["ignore"] = [p.strip() for p in IGNORE_PROVIDERS.split(",") if p.strip()]
    if prefs:
        payload["provider"] = prefs
    return json.dumps(payload).encode()


def apply_alias(body: bytes) -> Tuple[bytes, bool]:
    """Swap the home's alias for the real model id in an outbound body."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body, False
    if not isinstance(payload, dict) or payload.get("model") != MODEL_ALIAS[0]:
        return body, False
    payload["model"] = MODEL_ALIAS[1]
    return json.dumps(payload).encode(), True


def is_precommit_reject(response: httpx.Response) -> bool:
    """True for OpenRouter's pre-inference rejection (nothing was billed or sampled)."""
    if response.status_code != 400:
        return False
    try:
        err = json.loads(response.text)["error"]
        raw = err.get("metadata", {}).get("raw")
        inner = json.loads(raw) if isinstance(raw, str) else (raw or {})
        return bool(inner.get("error", inner).get("pre_commit"))
    except (json.JSONDecodeError, LookupError, AttributeError, TypeError):
        return False


def retry_delay(response: httpx.Response, attempt: int) -> float:
    try:
        return min(float(response.headers.get("retry-after", "")), 60.0)
    except ValueError:
        return float(RETRY_BACKOFF[attempt])


async def proxy(request: Request) -> Response:
    path = request.url.path
    agent = ""
    if (m := re.match(r"^/a/([^/]+)(/.*)$", path)):
        agent, path = m.group(1), m.group(2)
    body = await request.body()
    if SPOOF_DATE and body:
        body = _DATE_RE.sub(b"Today's date: " + SPOOF_DATE, body)
    aliased = False
    if MODEL_ALIAS and body:
        body, aliased = apply_alias(body)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    dropped: Dict[str, Any] = {}
    if MODE != "openrouter":
        if request.method == "GET" and path == "/":
            return Response("ok")  # the runner's readiness probe; no such route upstream
        url, headers, body, dropped = translate(path, headers, body)
        backoff = RETRY_BACKOFF
    else:
        url, backoff = UPSTREAM + path, ()
        if (PIN_PROVIDER or IGNORE_PROVIDERS or REASONING is not None) and body:
            body = pin_provider(body)

    statuses: List[int] = []
    precommit = 0  # pre-commit 400s retried so far (OpenRouter leg only)
    async with httpx.AsyncClient(timeout=600, verify=VERIFY) as client:
        attempt = 0
        while True:
            upstream = await client.request(
                request.method, url,
                params=dict(request.query_params), content=body, headers=headers,
            )
            statuses.append(upstream.status_code)
            if upstream.status_code in RETRY_STATUS and attempt < len(backoff):
                await asyncio.sleep(retry_delay(upstream, attempt))
                attempt += 1
                continue
            if (MODE == "openrouter" and precommit < len(PRECOMMIT_BACKOFF)
                    and is_precommit_reject(upstream)):
                print(f"[proxy] {agent}: pre-commit 400 from upstream, retry "
                      f"{precommit + 1}/{len(PRECOMMIT_BACKOFF)} in {PRECOMMIT_BACKOFF[precommit]}s",
                      flush=True)
                await asyncio.sleep(PRECOMMIT_BACKOFF[precommit])
                precommit += 1
                continue
            break
    final = upstream.status_code
    dead_reason = ""
    if DEAD_AFTER and agent:
        if final == 200:
            _FAILS[agent] = 0
        else:
            _FAILS[agent] = _FAILS.get(agent, 0) + 1
            if precommit == len(PRECOMMIT_BACKOFF) and is_precommit_reject(upstream):
                dead_reason = "precommit_ladder_exhausted"
            elif _FAILS[agent] >= DEAD_AFTER:
                dead_reason = f"{DEAD_AFTER}_consecutive_failures"
    if STATUS_LOG is not None:
        with STATUS_LOG.open("a") as f:
            f.write(json.dumps({"wall": time.time(), "agent": agent, "status": final,
                                "attempts": statuses, "precommit_retries": precommit,
                                **({"dead": dead_reason} if dead_reason else {})}) + "\n")
    if dead_reason and DEAD_FILE is not None and not DEAD_FILE.exists():
        DEAD_FILE.write_text(json.dumps({
            "wall": time.time(), "agent": agent, "reason": dead_reason, "status": final,
            "attempts": statuses, "response_text": upstream.text[:2000]}, indent=1))
        print(f"[proxy] DEAD SEAT {agent}: {dead_reason} (statuses {statuses}) — "
              f"wrote {DEAD_FILE}", flush=True)
    try:
        req_json = json.loads(body) if body else None
    except json.JSONDecodeError:
        req_json = {"_raw": body.decode(errors="replace")[:2000]}
    line = {
        "wall": time.time(), "agent": agent, "path": path,
        "status": upstream.status_code, "request": req_json,
        # SSE overhead is ~30x the payload (one ~350-byte data line per reasoning
        # fragment); a 400k cap once silently cut the tool-call chunks off a step.
        "response_text": upstream.text[:8_000_000],
    }
    if MODE != "openrouter":
        line["upstream"] = MODE
    if len(statuses) > 1:
        line["attempts"] = statuses
    if precommit:
        line["precommit_retries"] = precommit
    if dropped:
        line["dropped_params"] = dropped
    if aliased:
        line["model_alias"] = MODEL_ALIAS[0]
    with DUMP.open("a") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return Response(
        upstream.content,
        status_code=upstream.status_code,
        headers={"content-type": upstream.headers.get("content-type", "application/json")},
    )


def main() -> None:
    global DUMP, SPOOF_DATE, MODE, UPSTREAM_BASE, VERIFY, PIN_PROVIDER, IGNORE_PROVIDERS, MODEL_ALIAS
    global REASONING
    global DEAD_AFTER, DEAD_FILE, STATUS_LOG
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--upstream", default="openrouter",
                    choices=["openrouter", "azure"],
                    help="where to forward: openrouter.ai, or the Azure OpenAI resource in "
                         "AZURE_OPENAI_ENDPOINT (translated, see module docstring)")
    ap.add_argument("--ca-bundle", default=None,
                    help="extra CA certificates to trust upstream (e.g. a private gateway)")
    ap.add_argument("--reasoning", default=None, choices=["on", "off"],
                    help="OpenRouter leg: send reasoning.enabled true/false on every call")
    ap.add_argument("--pin-provider", default="",
                    help="OpenRouter backend(s) to pin every call to, e.g. Chutes, with "
                         "fallbacks off; a comma-separated list is tried in order. "
                         "Ignored for the Azure upstream.")
    ap.add_argument("--ignore-providers", default="",
                    help="OpenRouter backend(s) never to use, comma-separated, e.g. "
                         "anthropic/fast; the router picks among the rest")
    ap.add_argument("--model-alias", default=None, metavar="ALIAS=REAL",
                    help="rewrite request model ALIAS (the id in the opencode home) to "
                         "REAL (the id the upstream serves) — see MODEL_ALIAS")
    ap.add_argument("--dead-after", type=int, default=DEAD_AFTER,
                    help="write dead_seat.json after this many consecutive non-200 final "
                         "answers to one agent, or when the pre-commit 400 ladder is "
                         "exhausted; 0 disables the marker (the ladder stays)")
    ap.add_argument("--spoof-date", default=None,
                    help="ISO date the simulation is set on; rewrites opencode's "
                         "env-block 'Today's date' line in outbound request bodies")
    args = ap.parse_args()
    MODE = args.upstream
    PIN_PROVIDER = args.pin_provider
    IGNORE_PROVIDERS = args.ignore_providers
    REASONING = None if args.reasoning is None else (args.reasoning == "on")
    if MODE != "openrouter":
        base_var, base_default, _ = UPSTREAMS[MODE]
        for var in (KEY_VAR[MODE], *([] if base_default else [base_var])):
            if not os.environ.get(var):
                raise SystemExit(f"--upstream {MODE} needs {var} in the environment")
        UPSTREAM_BASE = (os.environ.get(base_var) or base_default).rstrip("/")
    if args.ca_bundle:
        # certifi's roots PLUS the private one, so public TLS keeps working.
        ctx = ssl.create_default_context(cafile=certifi.where())
        ctx.load_verify_locations(cafile=args.ca_bundle)
        VERIFY = ctx
    if args.model_alias:
        alias, _, real = args.model_alias.partition("=")
        if not alias or not real:
            raise SystemExit("--model-alias wants ALIAS=REAL")
        MODEL_ALIAS = (alias, real)
    if args.spoof_date:
        SPOOF_DATE = datetime.fromisoformat(args.spoof_date).strftime("%a %b %d %Y").encode()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    DUMP = out / "proxy_dump.jsonl"
    STATUS_LOG = out / "proxy_status.jsonl"   # one small line per answer; the runner reads it
    DEAD_FILE = out / "dead_seat.json"
    DEAD_AFTER = max(0, args.dead_after)
    app = Starlette(routes=[Route("/{path:path}", proxy, methods=["GET", "POST"])])
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
