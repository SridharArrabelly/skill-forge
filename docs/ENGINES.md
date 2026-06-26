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
   Stage 1               Stage 2               Stage 3            Stage 4
 Hand-rolled        GitHub Copilot SDK     Agent Framework     Foundry Agent
 ReAct loop         (CLI runtime loop)    (framework loop)    Service (planned)
```

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

## Stage 3 — Microsoft Agent Framework (`agent_framework`)

**File:** `app/engines/agent_framework.py`. Packages: `agent-framework`
(`from agent_framework import FunctionTool`, `from agent_framework.openai import
OpenAIChatClient`).

Like Stage 2, we **don't write the loop** — Microsoft Agent Framework owns it. But
unlike Stage 2, the model behind the loop is **your own Azure OpenAI deployment**
(the same backend Stage 1 calls directly). That's the whole point of this engine:
it isolates the single variable **"who owns the loop"** while holding the model
constant against Stage 1.

How it reuses the shared layer with zero duplication:

1. Each entry from `SkillToolset.openai_tools()` becomes a framework `FunctionTool`
   built from an **explicit JSON schema** — `FunctionTool(name=..., description=...,
   input_model=<json-schema dict>, func=handler)`. No typed Python signature is
   required, so the model sees the *same* tools (`rag_search`, `web_grounding`,
   `load_skill_instructions`) as every other engine. Each handler calls straight
   back into `SkillToolset.call(...)`.
2. We build one `Agent` over an `OpenAIChatClient` pointed at our Azure OpenAI
   resource, stream a turn with `agent.run(prompt, stream=True)`, and translate the
   framework's streamed text plus our handler callbacks into the shared SSE events.

The headline differences from Stage 1:

- **Who owns the loop:** the framework. We lose the line-by-line visibility of
  Stage 1's ~170-line loop but gain managed tool orchestration, middleware, and
  multi-turn sessions for free.
- **Same model, same auth:** your Azure OpenAI deployment, keyless via
  `DefaultAzureCredential` (`az login`) — identical to Stage 1.

**Progressive disclosure survives — even under a framework-owned loop.** In
testing, the model called `load_skill_instructions("rag-search")` and *then*
`rag_search`, exactly the Stage-1 pattern, with no special prompting.

**Two wiring gotchas worth knowing** (both handled in `_get_client`, and good
illustrations of how a managed client can fight your environment):

- `OpenAIChatClient` is a **Responses API** client, which needs a recent API
  version. We deliberately **don't pass `api_version`** so the SDK uses its
  Responses-compatible default; pinning the older `2024-10-21` that Stage 1 uses
  for chat-completions makes the Responses endpoint reject the call
  ("API version not supported").
- Our `.env` uses an **empty** `AZURE_OPENAI_API_KEY` to mean "keyless". The SDK
  reads that env var, and an *empty* value poisons credential resolution (it
  commits to key-auth, then fails with "Missing credentials"). So when keyless we
  drop the blank var before building the client.

---

## Side-by-side

| Dimension              | Stage 1 — Hand-rolled            | Stage 2 — Copilot SDK                    | Stage 3 — Agent Framework                |
| ---------------------- | -------------------------------- | ---------------------------------------- | ---------------------------------------- |
| Who owns the loop      | **You** (`app/agent.py`)         | Copilot CLI **runtime**                  | Agent **Framework**                      |
| Loop code to maintain  | ~170 lines, fully visible        | ~0 (you write event translation only)    | ~0 (you write event translation only)    |
| Model / provider       | Your Azure OpenAI deployment     | Copilot models (gpt-5.x, claude, gemini) | **Your Azure OpenAI deployment**         |
| Auth                   | `DefaultAzureCredential` (keyless)| Logged-in Copilot user (no key)         | `DefaultAzureCredential` (keyless)       |
| Tool registration      | OpenAI `tools=[]` from skills    | SDK `Tool(...)` from the **same** skills | `FunctionTool(...)` from the **same** skills |
| Progressive disclosure | Yes (built in)                   | Yes (carries over unchanged)             | Yes (carries over unchanged)             |
| Tool selection control | Full — only your tools exist   | Full — pinned via `available_tools` allowlist | Full — only your tools registered    |
| Streaming              | Per-chunk content events         | `assistant.message_delta` → content      | `agent.run(stream=True)` updates → content |
| Extras you get free    | None                             | Context compaction, session persistence  | Tool orchestration, middleware, sessions |
| Hosting / dependency   | OpenAI SDK + Azure endpoint      | Bundled Copilot runtime binary           | `agent-framework` + Azure endpoint       |
| Lock-in                | Low (any OpenAI-compatible API)  | Medium (Copilot platform + subscription) | Low–medium (framework API, your model)   |

**Rule of thumb:** reach for Stage 1 when you need to *see and control* every step
(debugging, custom routing, strict tool boundaries). Reach for Stage 2 when you
want a capable managed loop and your users already have Copilot. Reach for Stage 3
when you want a managed loop **on your own model/infra** — framework conveniences
(middleware, multi-agent workflows, sessions) without giving up your Azure OpenAI
deployment.

---

## Coming next

- **Stage 4 — Azure AI Foundry Agent Service** (hosted; tools run server-side):
  the fully-managed end of the spectrum.

Each will add a row to the table above and a section here.
