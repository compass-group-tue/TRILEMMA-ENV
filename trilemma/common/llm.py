"""One LLM client for every judge: OpenRouter (default) or Azure OpenAI, with retries and JSON helpers.

Keys come from the environment only:

    OPENROUTER_API_KEY                      for ``openrouter:<model>`` specs
    AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY[, AZURE_OPENAI_API_VERSION]   for ``azure:<deployment>``

Two surfaces:

* ``make_caller("openrouter:openai/gpt-6.1-sol")`` -> a ``(system, user) -> str`` callable with
  ``.last_usage`` / ``.last_reasoning`` (thread-local) and ``.snapshot()`` (running totals incl.
  ``cost_usd`` as billed by OpenRouter). This is what the judge scripts use.
* ``chat(model, messages, **kw) -> str`` and ``chat_json(model, messages, **kw) -> dict`` for
  one-off calls. ``model`` is either a full spec (``openrouter:...`` / ``azure:...``) or a bare
  OpenRouter slug.

Judging defaults (kept from the paper's runs): temperature 0 where the model accepts one (the
gpt-5 line and Claude Sonnet 5 reject the parameter, so none is sent), ``reasoning.effort=medium``,
and OpenRouter provider routing without fallbacks when a backend is pinned, so a down backend fails
loudly instead of silently re-routing to another quantization mid-sweep.
"""
from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from typing import Any, Dict, Iterator, List, Optional

import requests

OPENROUTER_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1") + "/chat/completions"

#: OpenRouter backend a judge model is pinned to when nothing else is given. deepseek-v4-flash is
#: served by many backends at different quantizations; unpinned, every call draws a fresh one.
DEFAULT_PINS = {"deepseek/deepseek-v4-flash-0731": "GMICloud"}

#: models that reject ``temperature`` rather than ignoring it
_NO_TEMPERATURE_PREFIXES = ("openai/gpt-5", "openai/gpt-6", "anthropic/claude-sonnet-5", "anthropic/claude-opus-5")

#: generic judge system prompt: the instructions live in the user message
SYS = ("You are a meticulous evaluator. Follow the instructions in the user message exactly and "
       "respond with ONLY the requested JSON object — no prose before or after.")

_RETRY_STATUS = (408, 409, 425, 429, 500, 502, 503, 504)


class Caller:
    """``(system, user) -> str`` against OpenRouter or Azure, retried with backoff."""

    def __init__(self, provider: str, model: str, *, max_tokens: Optional[int] = 6000,
                 temperature: Optional[float] = 0.0, reasoning_effort: Optional[str] = "medium",
                 provider_routing: Optional[Dict[str, Any]] = None, timeout: int = 180,
                 max_retries: int = 6):
        if provider not in ("openrouter", "azure"):
            raise ValueError(f"unknown provider {provider!r} (openrouter | azure)")
        self.provider, self.model = provider, model
        self.max_tokens, self.temperature = max_tokens, temperature
        self.reasoning_effort, self.provider_routing = reasoning_effort, provider_routing
        self.timeout, self.max_retries = timeout, max_retries
        self._local = threading.local()
        self._lock = threading.Lock()
        self.totals = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                       "total_tokens": 0, "cost_usd": 0.0}

    # -- tracking ---------------------------------------------------------------------------
    @property
    def last_usage(self) -> Dict[str, Any]:
        return getattr(self._local, "usage", {})

    @property
    def last_reasoning(self) -> str:
        return getattr(self._local, "reasoning", "")

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.totals)

    # -- request ----------------------------------------------------------------------------
    def _request(self, messages: List[Dict[str, Any]]):
        if self.provider == "openrouter":
            key = os.getenv("OPENROUTER_API_KEY")
            if not key:
                raise RuntimeError("OPENROUTER_API_KEY is not set")
            body: Dict[str, Any] = {"model": self.model, "messages": messages, "usage": {"include": True}}
            if self.max_tokens:
                body["max_tokens"] = self.max_tokens
            if self.temperature is not None:
                body["temperature"] = self.temperature
            if self.reasoning_effort:
                body["reasoning"] = {"effort": self.reasoning_effort}
            if self.provider_routing:
                body["provider"] = self.provider_routing
            return OPENROUTER_URL, {"Authorization": f"Bearer {key}"}, body
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
        key = os.getenv("AZURE_OPENAI_API_KEY")
        if not endpoint or not key:
            raise RuntimeError("AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY are not set")
        version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
        url = f"{endpoint}/openai/deployments/{self.model}/chat/completions?api-version={version}"
        body = {"messages": messages}
        if self.max_tokens:
            body["max_completion_tokens"] = self.max_tokens
        if self.temperature is not None and not self.model.startswith(("gpt-5", "gpt-6", "o")):
            body["temperature"] = self.temperature
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        return url, {"api-key": key}, body

    def complete(self, messages: List[Dict[str, Any]]) -> str:
        url, headers, body = self._request(messages)
        headers = {**headers, "Content-Type": "application/json"}
        last = ""
        for attempt in range(self.max_retries):
            try:
                r = requests.post(url, headers=headers, json=body, timeout=self.timeout)
                if r.status_code in _RETRY_STATUS:
                    last = f"HTTP {r.status_code}: {r.text[:200]}"
                elif r.status_code >= 400:
                    raise RuntimeError(f"{self.provider} HTTP {r.status_code}: {r.text[:400]}")
                else:
                    data = r.json()
                    if data.get("error"):
                        last = f"error body: {str(data['error'])[:200]}"
                    else:
                        return self._record(data)
            except requests.RequestException as exc:
                last = f"{type(exc).__name__}: {exc}"
            time.sleep(min(2.0 * (2 ** attempt), 60.0) + random.uniform(0, 2.0))
        raise RuntimeError(f"{self.provider}:{self.model} failed after {self.max_retries} attempts: {last}")

    def _record(self, data: Dict[str, Any]) -> str:
        usage = dict(data.get("usage") or {})
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        if choice.get("finish_reason"):
            usage["finish_reason"] = choice["finish_reason"]
        if data.get("provider"):
            usage["provider_name"] = data["provider"]
        cost = usage.get("cost")
        if isinstance(cost, (int, float)):
            usage["cost_usd"] = float(cost)
        self._local.usage = usage
        self._local.reasoning = str(msg.get("reasoning") or "")
        with self._lock:
            self.totals["calls"] += 1
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if isinstance(usage.get(k), (int, float)):
                    self.totals[k] += int(usage[k])
            self.totals["cost_usd"] += float(usage.get("cost_usd") or 0.0)
        return str(msg.get("content") or "")

    def __call__(self, system_prompt: str, user_prompt: str) -> str:
        return self.complete([{"role": "system", "content": system_prompt},
                              {"role": "user", "content": user_prompt}])


def make_caller(spec: str, *, max_tokens: Optional[int] = 6000, pin: Optional[str] = None,
                reasoning_effort: Optional[str] = "medium") -> Caller:
    """``provider:model`` -> a tracked ``(system, user) -> str`` caller.

    ``pin`` names the OpenRouter backend(s) allowed — one slug or an ordered comma list such as
    ``"openai,azure"``; ``"none"`` routes freely. Fallbacks outside the list are disabled.
    """
    provider, _, model = spec.partition(":")
    if not model:
        provider, model = "openrouter", spec
    if provider == "azure":
        return Caller("azure", model, max_tokens=max_tokens, reasoning_effort=reasoning_effort)
    if provider != "openrouter":
        raise ValueError(f"judge spec must be openrouter:<model> or azure:<deployment>, got {spec!r}")
    chosen = DEFAULT_PINS.get(model) if pin is None else (None if pin == "none" else pin)
    order = [x.strip() for x in str(chosen).split(",") if x.strip()] if chosen else []
    routing = {"order": order, "allow_fallbacks": False} if order else None
    temp = None if model.startswith(_NO_TEMPERATURE_PREFIXES) else 0.0
    return Caller("openrouter", model, max_tokens=max_tokens, temperature=temp,
                  reasoning_effort=reasoning_effort, provider_routing=routing)


# ---- one-off calls --------------------------------------------------------------------------

def chat(model: str, messages: List[Dict[str, Any]], *, max_tokens: int = 6000,
         pin: Optional[str] = None, reasoning_effort: Optional[str] = "medium") -> str:
    """One completion. ``model`` = ``openrouter:<slug>``, ``azure:<deployment>`` or a bare slug."""
    return make_caller(model, max_tokens=max_tokens, pin=pin,
                       reasoning_effort=reasoning_effort).complete(messages)


def chat_json(model: str, messages: List[Dict[str, Any]], *, retries: int = 2,
              require_keys: tuple = (), **kw) -> Dict[str, Any]:
    """``chat`` + parse the reply as one JSON object (fenced, chatty or slightly malformed replies
    are recovered); unparseable replies are retried with a nudge. Raises ValueError at the end."""
    msgs = list(messages)
    raw = ""
    for _ in range(retries + 1):
        raw = chat(model, msgs, **kw)
        obj = parse_json(raw, require_keys=require_keys)
        if obj is not None:
            return obj
        msgs = msgs + [{"role": "assistant", "content": raw},
                       {"role": "user", "content": "That did not parse as the required JSON object. "
                                                   "Reply with ONLY the JSON object."}]
    raise ValueError(f"no JSON object in reply ({len(raw)} chars)")


def replicates(fn, n: int = 3):
    """Run ``fn(replicate_index)`` for 1..n and return the list (the paper judges in 3 replicates)."""
    return [fn(i) for i in range(1, n + 1)]


# ---- JSON extraction ------------------------------------------------------------------------

def strip_json(text: str) -> str:
    """The JSON object out of a possibly fenced / chatty response (first ``{`` to last ``}``)."""
    s = (text or "").strip()
    if "```" in s:
        block = s.split("```", 2)
        if len(block) >= 2:
            inner = block[1]
            if inner.lstrip().lower().startswith("json"):
                inner = inner.split("\n", 1)[1] if "\n" in inner else ""
            s = inner.strip()
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start: end + 1]
    return s


_ESCAPED_OPEN = re.compile(r'(?<=[\[{,:])\s*\\"')
_ESCAPED_CLOSE = re.compile(r'\\"(?=\s*[,\]}])')


def salvage_json(text: str) -> Optional[Dict[str, Any]]:
    """Repair a reply whose string delimiters are escaped (``\\"``) as if the JSON were embedded in
    another string. Only delimiter-position escapes are unescaped. None when it still fails."""
    repaired = _ESCAPED_CLOSE.sub('"', _ESCAPED_OPEN.sub(' "', text))
    if repaired == text:
        return None
    try:
        obj = json.loads(repaired)
    except Exception:  # noqa: BLE001
        return None
    return obj if isinstance(obj, dict) else None


def balanced_objects(text: str) -> Iterator[str]:
    """Every top-level ``{...}`` in ``text``, string- and escape-aware."""
    depth = start = 0
    in_str = escaped = False
    for i, ch in enumerate(text or ""):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                yield text[start: i + 1]
            elif depth < 0:
                depth = 0


def parse_json(raw: str, require_keys: tuple = ()) -> Optional[Dict[str, Any]]:
    """Best-effort: strict parse, delimiter salvage, then the first balanced object that has
    ``require_keys`` (any of them; empty = any dict)."""
    def ok(o):
        return isinstance(o, dict) and (not require_keys or any(k in o for k in require_keys))
    s = strip_json(raw)
    try:
        obj = json.loads(s)
        if ok(obj):
            return obj
    except Exception:  # noqa: BLE001
        obj = salvage_json(s)
        if ok(obj):
            return obj
    for chunk in balanced_objects(raw):
        try:
            obj = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if ok(obj):
            return obj
    return None


def load_prompt_template(path) -> str:
    """The fillable prompt body of a prompt file: everything after the first ``---`` line (the
    part above it is a header for humans and is never sent)."""
    from pathlib import Path
    text = Path(path).read_text(encoding="utf-8")
    parts = text.split("\n---\n", 1)
    return (parts[1] if len(parts) == 2 else text).strip("\n")
