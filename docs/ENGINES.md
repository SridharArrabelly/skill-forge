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
 ReAct loop         (CLI runtime loop)        (planned)        Service (planned)
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

**Caveat noticed:** the runtime ships its **own** built-in tools (file, shell,
web). We point its working directory at a throwaway scratch dir and add a system
message telling it to prefer our skills and not touch files — but tool selection
is inherently **less predictable** than the hand-rolled loop, because we no
longer own the tool list end-to-end. That trade (control vs. managed
convenience) is the whole point of the comparison.

---

## Side-by-side

| Dimension              | Stage 1 — Hand-rolled            | Stage 2 — Copilot SDK                    |
| ---------------------- | -------------------------------- | ---------------------------------------- |
| Who owns the loop      | **You** (`app/agent.py`)         | Copilot CLI **runtime**                  |
| Loop code to maintain  | ~170 lines, fully visible        | ~0 (you write event translation only)    |
| Model / provider       | Your Azure OpenAI deployment     | Copilot models (gpt-5.x, claude, gemini) |
| Auth                   | `DefaultAzureCredential` (keyless)| Logged-in Copilot user (no key)         |
| Tool registration      | OpenAI `tools=[]` from skills    | SDK `Tool(...)` from the **same** skills |
| Progressive disclosure | Yes (built in)                   | Yes (carries over unchanged)             |
| Tool selection control | Full — only your tools exist     | Partial — runtime adds its own built-ins |
| Streaming              | Per-chunk content events         | `assistant.message_delta` → content      |
| Extras you get free    | None                             | Context compaction, session persistence  |
| Hosting / dependency   | OpenAI SDK + Azure endpoint      | Bundled Copilot runtime binary           |
| Lock-in                | Low (any OpenAI-compatible API)  | Medium (Copilot platform + subscription) |

**Rule of thumb:** reach for Stage 1 when you need to *see and control* every
step (debugging, custom routing, strict tool boundaries). Reach for Stage 2 when
you want a capable managed loop and your users already have Copilot — at the cost
of some control over how the loop behaves.

---

## Coming next

- **Stage 3 — Microsoft Agent Framework** (local, bring-your-own Azure OpenAI):
  a framework-owned loop you still host yourself.
- **Stage 4 — Azure AI Foundry Agent Service** (hosted; tools run server-side):
  the fully-managed end of the spectrum.

Each will add a row to the table above and a section here.
