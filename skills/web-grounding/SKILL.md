---
name: web-grounding
description: Use when the question needs live, current, or real-time information from the public web (news, prices, recent events, "today", anything that changes over time). Backed by Microsoft WebIQ web grounding.
enabled: true
---

## Instructions

Use this skill to ground answers in **live web** information instead of relying on
the model's training cutoff.

1. Extract a focused search query from the user's request (drop conversational filler).
2. Call the `web_grounding` tool with that query.
3. Read the returned snippets and **cite the sources** (titles/URLs) in your answer.
4. If the results are empty or low-quality, say so plainly rather than guessing.

Prefer this skill over answering from memory whenever the user asks about something
current or time-sensitive.
