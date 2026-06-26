# skill-forge

A minimal, local-first chat app that demonstrates a simple **agentic-loop + swappable-skills** pattern:

> **One agent. One agentic loop. N swappable skills.**
> The single loop's LLM reasoning does all the routing — it decides which skill to use
> each turn. New capabilities are added as **skill folders**, never as new agents.

This is a deliberately small, readable implementation meant for learning the pattern —
no avatar, no heavy Azure infra. The design was inspired by ideas in the
[`aiappsgbb/kratos-agent`](https://github.com/aiappsgbb/kratos-agent) reference repo, but
it's our own implementation from scratch: just a FastAPI backend, a hand-rolled
Reason → Act → Observe loop over Azure OpenAI, and a one-file chat UI.

> **New here?** Read **[docs/THE-PATTERN.md](docs/THE-PATTERN.md)** for a guided
> explanation of how this differs from a plain function-calling agent, what
> *progressive disclosure* is, and when the approach pays off.

## The core idea

A **skill is just a folder** under `skills/` containing a `SKILL.md` file:

```markdown
---
name: web-grounding
description: Answer questions needing live/current web info via WorkIQ web grounding.
enabled: true
---

## Instructions
Procedural knowledge the agent loads on demand…
```

- The **`description`** is the routing signal — it's what the agent uses to decide when
  to invoke the skill.
- A skill is **code-backed** if its folder also contains a `tool.py` (a real callable
  function the agent can run, e.g. `rag_search`). Otherwise it's **instructions-only**
  (pure Markdown procedural knowledge served on demand).
- Add or remove a capability by adding/removing a folder — no code changes to the loop.

## Inspiration

The agentic-loop + skills-as-folders idea was inspired by the `aiappsgbb/kratos-agent`
reference repo. skill-forge is a fresh, from-scratch implementation — no shared code.
Conceptual parallels:

| Concept (seen in kratos-agent) | how skill-forge does it |
|--------------|-------------|
| One agent, one agentic loop owns routing | hand-rolled loop in `agent.py` (every step visible) |
| Skill = folder with `SKILL.md` frontmatter | `skill_registry.py` discovers + parses folders |
| Code-backed skill is a callable tool | each skill folder ships its own `tool.py` |
| "Always prefer a skill over guessing" | same system-prompt guidance |
| Blob / APM sources, MCP, tracing, evals | out of scope here |

## Architecture

```
User ─▶ web/index.html ──SSE──▶ /api/chat ─▶ agent.py (loop)
                                                │
                       ┌────────────────────────┼───────────────────────┐
                       ▼                        ▼                        ▼
               Reason (Azure OpenAI)      Act (run a skill tool)   Observe (feed result back)
                       ▲                                                 │
                       └─────────────────── iterate ─────────────────────┘

skills/web-grounding/SKILL.md + tool.py   (code-backed, WebIQ web grounding)
skills/rag-search/SKILL.md   + tool.py     (code-backed, Azure AI Search)
```

## Project layout

```
backend/app/
  config.py          # env settings (Azure OpenAI + skill dirs)
  models.py          # SSE event + request models
  skill_registry.py  # discover skills/*/SKILL.md, parse frontmatter
  skill_tools.py     # load code-backed tool.py, build OpenAI tool schemas
  agent.py           # the single Reason → Act → Observe loop
  main.py            # FastAPI: /api/chat (SSE), /api/skills, serves the UI
skills/              # one folder per skill (SKILL.md [+ tool.py])
web/index.html       # minimal chat UI with skill-invocation chips
```

## Run it locally

1. Create and fill an env file:
   ```powershell
   Copy-Item .env.example .env
   # edit .env with your Azure OpenAI endpoint / key / deployment
   ```
2. Start it — pick whichever toolchain you use:

   **With `uv`** (recommended; resolves Python 3.12/3.13 automatically):
   ```powershell
   uv run skill-forge
   ```

   **With pip + venv:**
   ```powershell
   python -m venv .venv; .\.venv\Scripts\Activate.ps1
   pip install -r backend/requirements.txt
   python backend/app/main.py          # or: uvicorn app.main:app --app-dir backend --reload
   ```
3. Open http://localhost:8000 and chat. Watch the skill-invocation chips to see which
   skill the single loop decided to use.

> Note: use Python **3.12 or 3.13**. On 3.14 the pinned `pydantic-core` has no wheel yet
> and would try (and fail) to build from Rust. `uv run` handles this for you.

Run the tests:
```powershell
uv run --extra dev pytest        # or: .\.venv\Scripts\python.exe -m pytest backend/tests
```

## Status

Both starter skills are **wired to real backends**:

- **`web-grounding`** → Microsoft WebIQ via the official `webiq` SDK (live web results
  with citations).
- **`rag-search`** → semantic retrieval over an existing Azure AI Search index.

Auth is keyless-first (`DefaultAzureCredential` / `az login`); set the matching API key
in `.env` only if you prefer key-based auth. See `.env.example` for all variables.