# The Pattern: One Agentic Loop, Skills as Folders

This document explains *why* skill-forge is built the way it is — for someone who has
already built a function-calling agent and wants to understand what (if anything) is
different here, and when the approach pays off.

If you just want to run it, see the [README](../README.md). If you want to understand
the design, read on.

---

## 1. Start with what you already know

A function-calling agent is a loop:

1. **Reason** — send the user's message + a list of tool definitions to the model.
2. **Act** — the model replies "call `get_weather(city="Paris")`"; you run that function.
3. **Observe** — you feed the function's result back into the model.
4. Repeat until the model stops calling tools and returns a final answer.

skill-forge's loop (`backend/app/agent.py`) is **exactly this**. There is no secret
loop, no multi-agent orchestration, no coordinator. One agent, one loop.

So if the engine is identical, what's the point? The difference is **not in the loop** —
it's in **how capabilities are packaged and shown to the model.** Three ideas do all the
work:

1. Skills are **folders**, not hardcoded registrations.
2. **Progressive disclosure** — the model sees a tiny menu first, and pulls full detail
   only for the skill it decides to use.
3. A skill can be **instructions only** — a capability with no code at all.

Let's take them one at a time.

---

## 2. Skills are folders, not code

In a typical agent, adding a tool is a code change:

```python
tools = [weather_tool, search_tool, ...]   # a literal list you edit and redeploy
```

In skill-forge, a **skill is just a folder** under `skills/` containing a `SKILL.md`:

```
skills/
  web-grounding/
    SKILL.md        # what the skill is + when to use it + how to use it
    tool.py         # (optional) the actual callable
  rag-search/
    SKILL.md
    tool.py
```

`SKILL.md` is Markdown with YAML frontmatter:

```markdown
---
name: web-grounding
description: Use when the question needs live/current info from the public web.
enabled: true
---

## Instructions
1. Turn the user's request into a focused search query.
2. Call the `web_grounding` tool.
3. Cite the source titles/URLs in your answer.
```

- **`description`** is the *routing signal* — the one line the model uses to decide
  whether this skill is relevant.
- **`## Instructions`** is the *procedure* — loaded on demand (see §4).
- A folder with a **`tool.py`** is **code-backed** (it ships a real callable). A folder
  without one is **instructions-only**.

### Nothing else changes when you add one

The registry (`skill_registry.py`) doesn't contain a list of skills — it **scans the
folder at runtime**:

```python
for child in sorted(self.skills_dir.iterdir()):   # walk skills/
    skill = _skill_from_dir(child)                  # any folder with a SKILL.md
    if skill is not None:
        self._skills[skill.name] = skill            # registered automatically
```

The toolset (`skill_tools.py`) is just as generic — it loops over discovered skills and
dynamically imports each folder's `tool.py`:

```python
for skill in self.registry.enabled():
    if not skill.code_backed:
        continue
    module = _import_tool_module(skill)             # importlib loads THIS folder's tool.py
    self._functions[skill.tool_name] = FunctionTool(
        name=skill.tool_name,                       # "web_grounding"
        description=skill.description,              # from SKILL.md
        parameters=getattr(module, "TOOL"),        # from tool.py
        run=getattr(module, "run"),                # from tool.py
    )
```

There is **no `if name == "web_grounding"`** anywhere. The framework is an engine; skills
are data it discovers. The "registration" is *inverted* — it lives inside each skill
folder, which only has to satisfy a tiny contract:

> A code-backed skill's `tool.py` must expose **`TOOL`** (a JSON-schema dict for the
> arguments) and **`run(**kwargs)`** (the callable). That's the whole interface.

**Result:** add a capability by dropping a folder; remove it by deleting the folder;
disable it with `enabled: false`. No edits to `skill_registry.py`, `skill_tools.py`, or
`agent.py`. A live reload endpoint (`POST /api/skills/reload`) re-scans without a restart.

---

## 3. The cost of the "flat tools" approach

Why not just register every tool the normal way? It works fine for a handful of tools.
The problems appear as you scale:

- **Context bloat.** Every tool's full name + description + argument schema sits in the
  prompt on **every single turn**, whether relevant or not. At 5 tools that's noise; at
  50 tools it's a wall of JSON the model re-reads constantly.
- **Worse routing.** More irrelevant tool definitions in context = more chances for the
  model to pick the wrong one or get distracted. Selection accuracy degrades as the menu
  grows.
- **Higher cost & latency.** Those tokens are paid for on every request.
- **Procedure has nowhere to live.** A function tool is a name + a one-line description +
  a schema. There's no good place to put "*here's the 4-step way to use this well*."

This is the problem progressive disclosure solves.

---

## 4. Progressive disclosure (the key idea)

**Progressive disclosure** = show the model a small menu up front, and reveal the full
detail of a capability *only when it decides to use that capability*. Two tiers instead
of one.

### How it works here

**Tier 1 — the menu (always in context, but tiny).** The system prompt lists only each
skill's one-line `description`:

```
Available skills:
- web-grounding (callable tool): Use when the question needs live/current web info.
- rag-search (callable tool): Use for questions answered from the internal knowledge base.
```

That's a few tokens per skill — cheap even with dozens of skills.

**Tier 2 — the detail (loaded on demand).** Alongside the real tools, the toolset exposes
one synthetic tool, **`load_skill_instructions(name)`**. When a description matches the
user's need, the model first calls:

```
load_skill_instructions(name="rag-search")
```

…which returns the **full `## Instructions` body** of that skill's `SKILL.md`. *Now* the
model has the detailed procedure, and it proceeds to call the real tool
(`rag_search(...)`).

### The flow

```
User asks something
        │
        ▼
Model sees the one-line menu  ──▶  picks the relevant skill
        │
        ▼
load_skill_instructions("rag-search")   ◀── reveal full procedure (Tier 2)
        │
        ▼
rag_search(query=...)                    ◀── run the actual tool
        │
        ▼
Observe result ──▶ grounded final answer
```

You can watch this happen live in the UI: the busy indicator shows
*"Reading the Rag Search playbook…"* (the `load_skill_instructions` call), then
*"Searching the knowledge base…"* (the real tool), then the answer streams in.

### Why it's worth it

- **Context stays lean.** Always-on context is just the short catalogue; rich procedure
  is pulled only for the one skill in play — regardless of how many skills exist.
- **Routing scales.** The model chooses from short descriptions, not a sea of schemas.
- **Procedures can be rich.** Because instructions load on demand, a `SKILL.md` can hold
  a full multi-step playbook without taxing every turn.

### The honest trade-off

Progressive disclosure costs **one extra round-trip** (`load_skill_instructions` before
the real call). For a 3-tool app that's pure overhead and you'd be faster with flat
tools. The pattern is a **bet on scale and operability**, and the win grows with the
number of skills and the richness of their procedures — not with raw speed on a tiny app.

---

## 5. Instructions-only skills (capabilities with no code)

A skill doesn't need a `tool.py`. An **instructions-only** skill is pure procedural
knowledge — a Markdown playbook that tells the model *how to accomplish something using
other tools it already has*. The model loads it through the exact same
`load_skill_instructions` mechanism.

Example: a `refund-policy` skill with no code, whose `## Instructions` say "*when a user
asks for a refund, check eligibility with `rag_search` against the policy index, then
explain the steps in this order…*". No new function — just encoded know-how.

This matters because:

- **Non-engineers can author capabilities.** Anyone who can write Markdown can add or
  refine a skill. No deploy, no Python.
- **Workflows become editable artifacts.** The "how" lives in a file you can diff and
  review, not buried in a prompt string or code.
- **A plain function tool can't do this** — it's only a name + description + schema, with
  nowhere to carry a procedure.

---

## 6. Summary: how this differs from a flat function-calling agent

| | Flat function-calling agent | skill-forge |
|---|---|---|
| The loop | Reason → Act → Observe | **identical** |
| Add a capability | Edit code, redeploy | Drop in a folder |
| Remove / disable | Edit code | Delete folder / `enabled: false` |
| Always-on context | Every tool's full schema, every turn | One-line catalogue; detail on demand |
| Scales to many tools | Routing degrades, prompt bloats | Stays lean |
| Procedural knowledge | Description string only | Full `SKILL.md` playbook |
| Code-free capabilities | Not possible | Instructions-only skills |
| Who can author | Engineers | Anyone who writes Markdown |
| Reload without redeploy | Usually no | Yes (`/api/skills/reload`) |

---

## 7. Trade-offs (be honest)

This pattern is not free:

- **Extra round-trip per skill use** (the `load_skill_instructions` step). Pure overhead
  on small apps.
- **Runtime, not compile-time, safety.** A malformed `SKILL.md` or a `tool.py` missing
  `TOOL`/`run` is caught when loading, not by your type checker. You trade compile-time
  guarantees for drop-in flexibility. (skill-forge fails loudly at load time if the
  contract isn't met.)
- **The model must follow the two-step convention.** It's prompted to call
  `load_skill_instructions` before a skill; a weaker model could skip it. Worth verifying
  with the models you use.

**Use it when** you expect many capabilities, want non-engineers to contribute
capabilities, or have rich multi-step procedures. **Skip it when** you have a fixed
handful of simple tools and latency is paramount — plain function calling is simpler and
faster there.

---

## 8. Where to look in the code

| Concern | File |
|---|---|
| Discover folders, parse `SKILL.md` | `backend/app/skill_registry.py` |
| Build OpenAI tools + `load_skill_instructions`, dispatch calls | `backend/app/skill_tools.py` |
| The single Reason → Act → Observe loop + system prompt | `backend/app/agent.py` |
| The engine abstraction (run the same skills under different orchestrators) | `backend/app/engines/` — see [ENGINES.md](ENGINES.md) |
| FastAPI endpoints + SSE streaming + UI | `backend/app/main.py` |
| The two starter skills | `skills/web-grounding/`, `skills/rag-search/` |

The three core modules are deliberately small and readable — you can understand the whole
engine in one sitting, which is the point.
