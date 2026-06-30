---
name: rag-search
description: Use when the user asks about internal/private knowledge, documents, policies, or any indexed corpus that should be answered from a knowledge base rather than the open web. Retrieval-augmented generation over an Azure AI Search index.
enabled: true
---

## Instructions

Use this skill to answer from an **internal knowledge base** (Azure AI Search),
not the public web and not the model's memory.

1. Turn the user's question into a search query.
2. Call the `rag_search` tool (optionally set `top` to control how many passages
   come back).
3. Ground your answer **only** in the returned passages. Quote/cite the `title`,
   `source`, and `page` of each passage you use.
4. If nothing relevant comes back, say the knowledge base has no answer rather
   than inventing one.

This skill is for grounded, citable answers over private content.
