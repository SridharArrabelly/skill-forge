# skill-forge

A minimal, local-first chat app that demonstrates a simple **agentic-loop + swappable-skills** pattern:

> **One agent. One agentic loop. N swappable skills.**
> The single loop's LLM reasoning does all the routing — it decides which skill to use
> each turn. New capabilities are added as **skill folders**, never as new agents.

This is a deliberately small, readable implementation meant for learning the pattern —
no avatar, no heavy Azure infra. The design was inspired by ideas in the
[`aiappsgbb/kratos-agent`](https://github.com/aiappsgbb/kratos-agent) reference repo, but
it's our own implementation from scratch: a FastAPI backend, a hand-rolled
Reason → Act → Observe loop over Azure OpenAI (plus a pluggable engine layer so the
same skills can run under other orchestrators — see [docs/ENGINES.md](docs/ENGINES.md)),
and a one-file chat UI.

> **New here?** Read **[docs/THE-PATTERN.md](docs/THE-PATTERN.md)** for a guided
> explanation of how this differs from a plain function-calling agent, what
> *progressive disclosure* is, and when the approach pays off. Then see
> **[docs/ENGINES.md](docs/ENGINES.md)** for how the *same* skills run under
> different orchestration engines (hand-rolled loop vs. GitHub Copilot SDK …).

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
User ─▶ web/index.html ──SSE──▶ /api/chat ─▶ engine (selected in the UI)
                                               │
   ┌──────────────────┬──────────────────┬─────┴────────────────┐
   ▼                  ▼                  ▼                        ▼
 handrolled        copilot_sdk      copilot_sdk_byom        agent_framework
 you own the       Copilot runtime  Copilot runtime         Agent Framework loop
 loop (agent.py),  owns the loop,   owns the loop,          ▸ Copilot SDK (BYOM)
 your Azure OpenAI  Copilot models   your Azure OpenAI       ▸ your Azure OpenAI
   └──────────────────┴──────────────────┴─────┬────────────────┘
                                               ▼
                              the SAME skill tools (skill_tools.py)
                                               │
                        ┌────────────────────────┴────────────────────────┐
                        ▼                                                  ▼
   skills/web-grounding/SKILL.md + tool.py            skills/rag-search/SKILL.md + tool.py
   (code-backed, WebIQ web grounding)                 (code-backed, Azure AI Search)
```

Every engine emits the **same** SSE events (`content`, `tool_call`, `error`,
`done`), so the UI is engine-agnostic. See **[docs/ENGINES.md](docs/ENGINES.md)**.

## Project layout

```
backend/app/
  config.py          # env settings (Azure OpenAI + skill dirs)
  models.py          # SSE event + request models (incl. engine selector field)
  skill_registry.py  # discover skills/*/SKILL.md, parse frontmatter
  skill_tools.py     # load code-backed tool.py, build OpenAI tool schemas
  agent.py           # the hand-rolled Reason → Act → Observe loop
  engines/           # the engine abstraction (one interface, many backends)
    base.py          #   AgentEngine ABC + shared SSE event contract
    handrolled.py    #   Stage 1: adapter over agent.py
    copilot_sdk.py   #   Stage 2: GitHub Copilot SDK (runtime owns the loop)
    copilot_sdk_byom.py #  Stage 2b: Copilot SDK runtime loop, BYOM your Azure OpenAI
    agent_framework.py #   Stage 3: Agent Framework loop ▸ Copilot SDK (BYOM) ▸ your model
    byom.py          #   shared Azure "Bring Your Own Model" provider config
    __init__.py      #   EngineRegistry + ENGINE_CLASSES (register new engines here)
  main.py            # FastAPI: /api/chat (SSE), /api/engines, /api/skills, serves UI
skills/              # one folder per skill (SKILL.md [+ tool.py])
web/index.html       # minimal chat UI with engine selector + skill chips
docs/THE-PATTERN.md  # why skills-as-folders / progressive disclosure
docs/ENGINES.md      # how the same skills run under different engines
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
3. Open http://localhost:8000 and chat. Use the **engine** selector (top-right) to
   switch between the hand-rolled loop, the Copilot SDK (Copilot models or BYOM), and
   the Agent Framework, and watch the skill-invocation chips to see which skill the
   loop decided to use.

   **Optional — enable the GitHub Copilot SDK engines** (Stage 2 + Stage 2b/3 share
   the runtime):
   ```powershell
   pip install github-copilot-sdk      # already in requirements.txt
   python -m copilot download-runtime  # one-time: cache the runtime binary
   gh auth login; gh auth refresh --scopes copilot   # the runtime authenticates as you
   # optional (Stage 2 only): choose a Copilot model (default gpt-5.4-mini)
   # setx COPILOT_SDK_MODEL "claude-sonnet-4.5"
   ```
   - **Copilot SDK** (Stage 2) runs on Copilot's hosted models — no Azure OpenAI needed.
   - **Copilot SDK (BYOM)** (Stage 2b) and **Agent Framework + Copilot SDK (BYOM)**
     (Stage 3) point the runtime at *your* Azure OpenAI, so they also need the
     `AZURE_OPENAI_*` settings + `az login`. The BYOM model must be an **o-series or
     gpt-5 family** deployment (the SDK encrypts prompts; `gpt-5.4-mini` works,
     `gpt-4o` does not).

   **Optional — enable the Agent Framework engine:**
   ```powershell
   pip install agent-framework          # already in requirements.txt
   ```
   Each engine appears in the dropdown automatically once its dependencies + settings
   are present; otherwise the option shows as unavailable with the reason.

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

**Engines.** The chat UI has an **engine** selector. Each engine drives the same two
skills behind the same event stream; only the loop changes:

- **Hand-rolled ReAct loop** (default) → our own Reason → Act → Observe over Azure OpenAI.
- **GitHub Copilot SDK** → the Copilot CLI runtime owns the loop; authenticates as your
  logged-in Copilot user and runs on **Copilot's models** (no Azure OpenAI). Pick a model
  with `COPILOT_SDK_MODEL` (default `gpt-5.4-mini`).
- **GitHub Copilot SDK (BYOM)** → the *same* Copilot runtime loop, but inference is routed
  to **your own Azure OpenAI deployment** via a Bring-Your-Own-Model provider config. Clean
  A/B against the previous engine: same loop, only the model swaps (and billing stays on
  your Azure subscription).
- **Agent Framework + Copilot SDK (BYOM)** → Microsoft Agent Framework owns the loop and
  drives the Copilot runtime in BYOM mode, on your Azure OpenAI. Stacks a framework loop on
  top of the Copilot runtime — the "everything managed, but on your model" end of the
  spectrum.

> The two BYOM engines need both a logged-in Copilot user *and* `AZURE_OPENAI_*` + `az login`,
> and the deployment must be an o-series or gpt-5 family model (the SDK encrypts prompts).

See **[docs/ENGINES.md](docs/ENGINES.md)** for the full comparison. The remaining engine
(Foundry Agent Service) is planned.