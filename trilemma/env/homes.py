"""opencode homes: two MCP connectors (slack + tanager), as a real setup would."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from trilemma.env.homes_base import BUILTIN_TOOLS_OFF

#: Azure OpenAI deployments a run can drive directly (``provider: azure``), declared the way
#: models.dev declares a model: opencode refuses a model id it has no metadata for, and the
#: metadata is also what it reads `max_tokens` and the per-run cost off. Costs are USD per
#: 1M tokens. ``reasoning: false`` is the honest value: these deployments return no chain of
#: thought. Add your own deployment here to use it.
AZURE_MODELS: Dict[str, Dict[str, Any]] = {
    "gpt-5.4": {
        "name": "GPT-5.4",
        "tool_call": True,
        "reasoning": False,
        "temperature": True,
        "cost": {"input": 2.50, "output": 15.0, "cache_read": 0.25},
        # 128k output is the deployment's own ceiling: max_completion_tokens 200000 is
        # rejected 400, 128000 is accepted.
        "limit": {"context": 400_000, "output": 128_000},
    },
}

MODEL_TABLES = {"azure": AZURE_MODELS}
TABLE_NAMES = {"azure": "AZURE_MODELS"}
KEY_ENV = {"azure": "AZURE_OPENAI_API_KEY"}


def make_home(root: Path, agent: str, *, model: str, proxy_port: int, world_port: int,
               system_prompt: str, temperature: float = 0.7,
               provider: str = "openrouter", api: str = "chat", alias: str = "") -> Path:
    """``alias``: the model id written into the home instead of ``model``. opencode tells
    the assistant "You are powered by the model named <id>", so this is what the model
    hears about itself; the proxy (``--model-alias``) puts ``model`` back before the call
    leaves. ``model`` still selects the MODEL_TABLES entry."""
    home = root / agent.lower()
    agents_dir = home / ".opencode" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    # The provider id stays "openrouter" even for Azure runs. opencode resolves a
    # provider id to an npm package it may have to fetch at runtime; the OpenRouter one
    # is already resolved, and the translation to Azure happens a
    # layer down in proxy.py, so an Azure config differs from its OpenRouter twins in the
    # model id and nothing else. (It is model-visible — opencode appends "You are
    # powered by the model named …" to the system prompt — which is why the deployment
    # name is passed through verbatim.)
    options: Dict[str, Any] = {
        "baseURL": f"http://127.0.0.1:{proxy_port}/a/{agent}/api/v1",
        "apiKey": "{env:OPENROUTER_API_KEY}",
    }
    openrouter: Dict[str, Any] = {"options": options}
    if provider in MODEL_TABLES:
        table = MODEL_TABLES[provider]
        options["apiKey"] = "{env:%s}" % KEY_ENV[provider]
        if model not in table:
            raise ValueError(f"unknown {provider} model {model!r}; add it to "
                             f"homes.{TABLE_NAMES[provider]}")
        openrouter["models"] = {alias or model: table[model]}
    elif provider != "openrouter":
        raise ValueError(f"unknown provider {provider!r}")
    if alias:
        model = alias

    # ``api="responses"``: the gpt-5.x deployments return a reasoning summary
    # only on the Responses API, never on chat completions (chat completions -> no reasoning
    # field, /v1/responses with reasoning.summary=auto -> a summary item). opencode's OpenRouter provider speaks chat completions, so the home
    # switches to the ``openai`` provider (ai-sdk's OpenAI provider, Responses API), still
    # pointed at the logging proxy; the proxy forwards ``/v1/responses`` to the upstream.
    # ``reasoningSummary: auto`` is the ai-sdk provider option that asks for the summary,
    # which then arrives as opencode ``reasoning`` parts exactly like an open model's CoT.
    if api not in ("chat", "responses"):
        raise ValueError(f"api must be 'chat' or 'responses', not {api!r}")
    if api == "responses":
        if provider not in MODEL_TABLES:
            raise ValueError("api='responses' needs provider azure")
        provider_id = "openai"
        options["baseURL"] = f"http://127.0.0.1:{proxy_port}/a/{agent}/v1"
        table_entry = dict(openrouter["models"][model])
        table_entry["options"] = {"reasoningSummary": "auto"}
        table_entry["reasoning"] = True  # summaries do come back on this path
        provider_block: Dict[str, Any] = {"options": options, "models": {model: table_entry}}
    else:
        provider_id, provider_block = "openrouter", openrouter

    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {provider_id: provider_block},
        "mcp": {
            "slack": {"type": "remote", "url": f"http://127.0.0.1:{world_port}/slack/mcp",
                      "headers": {"X-Agent-Name": agent}},
            "tanager": {"type": "remote", "url": f"http://127.0.0.1:{world_port}/tanager/mcp",
                        "headers": {"X-Agent-Name": agent}},
        },
        "share": "disabled",
        "autoupdate": False,
    }
    (home / "opencode.json").write_text(json.dumps(config, indent=2) + "\n")

    tools = dict(BUILTIN_TOOLS_OFF)
    tools["slack_*"] = True
    tools["tanager_*"] = True
    lines: List[str] = [
        "---",
        f"description: {agent}'s workplace assistant",
        "mode: primary",
        f"model: {provider_id}/{model}",
        f"temperature: {temperature}",
        "tools:",
        *[f"  {name}: {str(on).lower()}" for name, on in tools.items()],
        "---",
    ]
    (agents_dir / "assistant.md").write_text("\n".join(lines) + "\n" + system_prompt + "\n")
    return home
