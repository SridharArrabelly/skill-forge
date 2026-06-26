# Engines: the same skills, different loops

skill-forge is a learning project. The *capabilities* (two skills: `rag-search`
and `web-grounding`) and the *UI* stay fixed. What we swap is the **engine** —
the thing that owns the agentic loop and decides, turn by turn, which skill to
call. Every engine implements one interface (`app/engines/base.py:AgentEngine`)
and emits the **same** SSE events (`content`, `tool_call`, `error`, `done`), so
you can switch engines from the dropdown and compare them apples-to-apples.

This doc grows one section per engine as we build them.

```
            you own the loop  ───────────────────────────────►  fully managed
  Stage 1        Stage 2          Stage 2b            Stage 3            Stage 4
 Hand-rolled   Copilot SDK     Copilot SDK (BYOM)  Agent Framework   Foundry Agent
 ReAct loop    runtime loop    runtime loop        + Copilot SDK     Service
 your AOAI     Copilot models  your AOAI (BYOM)    (BYOM) your AOAI  (planned)
```

The two axes this project teaches: **who owns the loop** (left → right) and **whose
model runs underneath** (Copilot-hosted vs. your own Azure OpenAI, "BYOM"). Stage 2
vs. 2b isolates *just the model backend* (same Copilot loop); Stage 1 vs. 3 isolates
*just the loop owner* (same Azure OpenAI model).

---

## Stage 1 — Hand-rolled ReAct loop (`handrolled`)

**File:** `app/agent.py` (the loop) + `app/engines/handrolled.py` (adapter).

We write the Reason → Act → Observe loop ourselves and call **Azure OpenAI**
directly:

1. Send the system prompt (the skill catalogue) + history + tools to the model.
2. If the model returns tool calls, we execute them via `SkillToolset.call(...)`
   and append the results.
3. Loop until the model returns a final text answer (bounded by a step guard).

Every byte of orchestration is visible and editable. Tools are registered as an
OpenAI `tools=[...]` array built from the discovered skills, plus the synthetic
`load_skill_instructions` tool that powers **progressive disclosure** (the model
sees one-line descriptions and pulls a skill's full `SKILL.md` only when it needs
it).

**You provide:** an Azure OpenAI endpoint + deployment (keyless via
`DefaultAzureCredential`).

---

## Stage 2 — GitHub Copilot SDK (`copilot_sdk`)

**File:** `app/engines/copilot_sdk.py`. Package: `github-copilot-sdk`
(`from copilot import CopilotClient`).

Here we **do not write a loop at all**. The Copilot CLI *runtime* owns the
agentic loop; the SDK is a JSON-RPC client that drives it in-process. We:

1. Build SDK `Tool`s from the **same** `SkillToolset` — identical names,
   descriptions, and JSON schemas, including `load_skill_instructions`. The tool
   handlers call straight back into `SkillToolset.call(...)`, so **no skill logic
   is duplicated**.
2. `create_session(...)`, `send(prompt)`, and translate the runtime's event
   stream (`assistant.message_delta`, our tool start/complete, `session.idle`)
   into the shared SSE events.

The headline differences from Stage 1:

- **Auth & models:** authenticates as your **logged-in Copilot user** — no API
  key, no Azure OpenAI. It runs on Copilot's models (`gpt-5.x`, `claude-*`,
  `gemini-*`). Pick one with `COPILOT_SDK_MODEL` (default `gpt-5.4-mini`).
- **Who owns the loop:** the runtime. We lose direct control of the iteration
  but gain a managed loop (and free extras like context compaction / "infinite
  sessions").

**Progressive disclosure survives.** In testing, the Copilot model spontaneously
called `load_skill_instructions("rag-search")` and *then* `rag_search` — exactly
the Stage-1 pattern, with no special prompting.

**Built-in tools — and how we pin them out.** The Copilot runtime is a full
coding agent, so *by default* the model sees a **merged catalog**: our custom
skills **plus** the runtime's own built-ins (`view`/`read_file`, `edit`/`create`,
`shell`, web search, glob/grep, and an isolated set like `ask_user`, `task`,
`skill`). To keep the comparison honest — and match Stage 1's "only our tools
exist" — this engine passes `available_tools` as an **allowlist** of just our
three custom tools (`rag_search`, `web_grounding`, `load_skill_instructions`).
The runtime hides everything else from the model. Verified: with the allowlist
on, a knowledge-base question used *only* `load_skill_instructions` → `rag_search`
and no built-in tools at all.

---

## Stage 2b — GitHub Copilot SDK with BYOM (`copilot_sdk_byom`)

**File:** `app/engines/copilot_sdk_byom.py` (a ~30-line subclass of
`CopilotSdkEngine`) + `app/engines/byom.py` (the shared Azure provider config).

This is the **same engine as Stage 2** — the Copilot runtime still owns the loop,
skills are still SDK `Tool`s, the allowlist still pins out the built-ins — with
exactly **one thing changed: the model backend.** Instead of GitHub's hosted
models, we hand the runtime a `provider` config (BYOM, "Bring Your Own Model")
pointed at **your own Azure OpenAI deployment**:

```python
provider = {
    "type": "azure",
    "wire_api": "responses",
    "base_url": "https://<resource>.openai.azure.com",
    "azure": {"api_version": "2025-04-01-preview"},
    "get_bearer_token": <DefaultAzureCredential token callback>,  # keyless
}
session = await client.create_session(model="gpt-5.4-mini", provider=provider, ...)
```

Because *only* the model swaps, Stage 2 vs. 2b is a clean A/B: any behaviour
difference you observe is the **model**, not the orchestration. (And the inference
billing lands on your Azure subscription instead of Copilot's.)

Things worth knowing:

- **Auth is doubled.** The runtime still authenticates to GitHub to *start*
  (logged-in Copilot user), but **inference** goes to your Azure OpenAI — keyless
  via `DefaultAzureCredential` (`az login`), the same identity Stage 1 uses. Set
  `AZURE_OPENAI_API_KEY` to use key auth instead.
- **Encrypted-content constraint (important).** The Copilot SDK **encrypts prompts**
  before sending, so only model families that can decrypt that format work via BYOM:
  the **o-series and gpt-5 family**. `gpt-5.4-mini` ✅; a `gpt-4o` deployment fails
  with *"Encrypted content is not supported."* (`byom.py` checks for this and
  rewrites the error into a helpful message.)

---

## Stage 3 — Agent Framework over the Copilot SDK, BYOM (`agent_framework`)

**File:** `app/engines/agent_framework.py`. Packages: `agent-framework`
(`from agent_framework import FunctionTool`, `from agent_framework.github import
GitHubCopilotAgent`) + the GitHub Copilot SDK.

This stage stacks **two** managed pieces — it's the deliberate fusion of Stage 2b
and a framework-owned loop:

- **Microsoft Agent Framework owns the agentic loop** (we don't hand-write Reason →
  Act → Observe at all), and
- **the GitHub Copilot SDK is the model backend, in BYOM mode** — the framework's
  `GitHubCopilotAgent` drives the Copilot runtime, which we point at **your own
  Azure OpenAI deployment** via the *same* `provider` config Stage 2b uses.

So the stack is: **Agent Framework loop ▸ Copilot SDK runtime ▸ your Azure OpenAI.**
It's the "everything managed, but on your model" end of the spectrum, short of a
fully hosted service. (This mirrors the
[Agent Framework + Copilot SDK](https://devblogs.microsoft.com/agent-framework/build-ai-agents-with-github-copilot-sdk-and-microsoft-agent-framework/)
pattern from the user's `agent-framework-sdk-lab`, with a BYOM `provider` added.)

How it reuses the shared layer with zero duplication:

1. Each entry from `SkillToolset.openai_tools()` becomes a framework `FunctionTool`
   built from an **explicit JSON schema** — `FunctionTool(name=..., description=...,
   input_model=<json-schema dict>, func=handler)`. No typed Python signature is
   required, so the model sees the *same* tools (`rag_search`, `web_grounding`,
   `load_skill_instructions`) as every other engine. Each handler calls straight
   back into `SkillToolset.call(...)`.
2. We construct one `GitHubCopilotAgent` with those tools and the BYOM `provider`,
   stream a turn with `agent.run(prompt, stream=True)`, and translate the streamed
   text plus our handler callbacks into the shared SSE events.

The headline differences from Stage 1:

- **Who owns the loop:** the framework (driving the Copilot runtime). We lose the
  line-by-line visibility of Stage 1's loop but gain managed tool orchestration,
  middleware, and multi-turn sessions for free.
- **Same model, doubled auth:** your Azure OpenAI deployment (keyless,
  `DefaultAzureCredential`) — *plus* a logged-in Copilot user, since the runtime
  still authenticates to GitHub to start.

**Progressive disclosure survives — even with a framework loop on a BYOM backend.**
Verified end-to-end: the model called `load_skill_instructions("rag-search")` and
*then* answered from the skill — the same Stage-1 pattern, no special prompting.

**Two gotchas worth knowing** (both handled in the engine):

- **Custom tools are permission-gated.** The Copilot runtime gates custom-tool
  execution behind a permission request and **denies by default** if no handler is
  set — so without intervention our skills silently come back *"permission denied"*
  and the model gives up. The engine passes `on_permission_request:
  PermissionHandler.approve_all` in `default_options` to approve them. (This took a
  live debug session to pin down — the failure looks like the model "refusing,"
  not a config error.)
- **Encrypted-content constraint** — same as Stage 2b: o-series / gpt-5 family only.

---

## Side-by-side

| Dimension              | Stage 1 — Hand-rolled            | Stage 2 — Copilot SDK                    | Stage 2b — Copilot SDK (BYOM)            | Stage 3 — Agent Framework + Copilot SDK (BYOM) |
| ---------------------- | -------------------------------- | ---------------------------------------- | ---------------------------------------- | ---------------------------------------- |
| Who owns the loop      | **You** (`app/agent.py`)         | Copilot CLI **runtime**                  | Copilot CLI **runtime**                  | Agent **Framework** (drives Copilot runtime) |
| Loop code to maintain  | ~170 lines, fully visible        | ~0 (event translation only)              | ~0 (subclass adds ~1 option)             | ~0 (event translation only)              |
| Model / provider       | Your Azure OpenAI deployment     | Copilot models (gpt-5.x, claude, gemini) | **Your Azure OpenAI** (BYOM)             | **Your Azure OpenAI** (BYOM)             |
| Auth                   | `DefaultAzureCredential` (keyless)| Logged-in Copilot user (no key)         | Copilot user **+** keyless Azure         | Copilot user **+** keyless Azure         |
| Tool registration      | OpenAI `tools=[]` from skills    | SDK `Tool(...)` from the **same** skills | SDK `Tool(...)` from the **same** skills | `FunctionTool(...)` from the **same** skills |
| Progressive disclosure | Yes (built in)                   | Yes (carries over)                       | Yes (carries over)                       | Yes (verified)                           |
| Tool selection control | Full — only your tools exist   | Full — `available_tools` allowlist     | Full — `available_tools` allowlist     | Tools approved via `on_permission_request` |
| Model constraint       | Any Azure deployment             | Any Copilot model                        | **o-series / gpt-5 only** (encryption)   | **o-series / gpt-5 only** (encryption)   |
| Streaming              | Per-chunk content events         | `assistant.message_delta` → content      | `assistant.message_delta` → content      | `agent.run(stream=True)` updates → content |
| Extras you get free    | None                             | Compaction, session persistence          | Compaction, session persistence          | Tool orchestration, middleware, sessions |
| Hosting / dependency   | OpenAI SDK + Azure endpoint      | Bundled Copilot runtime binary           | Copilot runtime + Azure endpoint         | `agent-framework` + Copilot runtime + Azure |
| Lock-in                | Low (any OpenAI-compatible API)  | Medium (Copilot platform + subscription) | Medium (Copilot runtime, your model)     | Medium (framework + Copilot runtime, your model) |

**Rule of thumb:**
- **Stage 1** when you need to *see and control* every step (debugging, custom
  routing, strict tool boundaries).
- **Stage 2** when you want a capable managed loop and your users already have
  Copilot — happy to run on Copilot's models.
- **Stage 2b** when you want that same managed Copilot loop but need inference on
  **your own Azure OpenAI** (data residency, billing, a specific deployment).
- **Stage 3** when you additionally want **framework conveniences** (middleware,
  multi-agent workflows, typed sessions) on top of the Copilot runtime — still on
  your own model.

---

## Coming next

- **Stage 4 — Azure AI Foundry Agent Service** (hosted; tools run server-side):
  the fully-managed end of the spectrum.

Each will add a row to the table above and a section here.
