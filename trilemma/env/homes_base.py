"""Generate one opencode home directory per assistant.

A home is what makes an opencode instance *be* one employee's assistant: the provider config
(pointed through the logging proxy under the agent's own path prefix), the MCP connection
whose ``X-Agent-Name`` header is the assistant's identity, and the agent definition whose
body is the system prompt.

Homes live under ``/tmp`` **deliberately** — not the repo, not a scratch dir with the
experiment's name in it. opencode stamps the working directory into an ``<env>`` block in
the system prompt, so the path is model-visible: it must look like deployment plumbing,
never like an eval. It must also not be a git repo (the env block says so either way).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

#: Built-in tools an assistant must not have. `skill: false` also removes the
#: <available_skills> advert from the prompt (verified in the smoke run).
BUILTIN_TOOLS_OFF: Dict[str, bool] = {
    name: False
    for name in ("bash", "edit", "write", "read", "grep", "glob", "list", "patch",
                 "todowrite", "todoread", "webfetch", "websearch", "task", "skill")
}


def make_home(
    root: Path,
    agent: str,
    *,
    model: str,
    proxy_port: int,
    world_port: int,
    system_prompt: str,
    temperature: float = 0.7,
) -> Path:
    home = root / agent.lower()
    agents_dir = home / ".opencode" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "openrouter": {
                "options": {
                    "baseURL": f"http://127.0.0.1:{proxy_port}/a/{agent}/api/v1",
                    "apiKey": "{env:OPENROUTER_API_KEY}",
                }
            }
        },
        "mcp": {
            "tanager": {
                "type": "remote",
                "url": f"http://127.0.0.1:{world_port}/mcp",
                "headers": {"X-Agent-Name": agent},
            }
        },
        "share": "disabled",
        "autoupdate": False,
    }
    (home / "opencode.json").write_text(json.dumps(config, indent=2) + "\n")

    tools = dict(BUILTIN_TOOLS_OFF)
    tools["tanager_*"] = True
    frontmatter_lines: List[str] = [
        "---",
        f"description: {agent}'s workplace assistant",
        "mode: primary",
        f"model: openrouter/{model}",
        f"temperature: {temperature}",
        "tools:",
        *[f"  {name}: {str(on).lower()}" for name, on in tools.items()],
        "---",
    ]
    (agents_dir / "assistant.md").write_text("\n".join(frontmatter_lines) + "\n" + system_prompt + "\n")
    return home
