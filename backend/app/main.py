"""FastAPI app: the chat API plus the static UI.

Endpoints:
  GET  /                -> the chat UI (web/index.html)
  GET  /api/skills      -> the discovered skill catalogue (what the agent can do)
  POST /api/chat        -> run one turn; streams SSE events from the agent loop

History is owned by the client (sent in each request) — no server-side session
store in this minimal cut.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make the package importable whether launched as `uvicorn app.main:app`
# (with --app-dir backend) OR directly as `python backend/app/main.py`.
# In the direct case Python only puts backend/app/ on sys.path, so `app` is
# not importable until we add backend/ ourselves.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.config import REPO_ROOT, get_settings
from app.models import ChatRequest
from app.agent import Agent
from app.skill_registry import SkillRegistry
from app.skill_tools import SkillToolset

# Load .env before settings are read.
load_dotenv(REPO_ROOT / ".env")

WEB_DIR = REPO_ROOT / "web"

app = FastAPI(title="skill-forge", version="0.1.0")

# Built once at startup; re-scan via POST /api/skills/reload during development.
_settings = get_settings()
_registry = SkillRegistry(_settings.skills_path).load()
_toolset = SkillToolset(_registry).build()
_agent = Agent(_settings, _toolset)


@app.get("/api/skills")
def list_skills() -> JSONResponse:
    """Return the discovered skills so the UI can show what the agent can do."""
    return JSONResponse(
        {
            "azure_configured": _settings.azure_configured,
            "skills": [
                {
                    "name": s.name,
                    "description": s.description,
                    "kind": s.kind,
                    "enabled": s.enabled,
                }
                for s in _registry.all()
            ],
        }
    )


@app.post("/api/skills/reload")
def reload_skills() -> JSONResponse:
    """Re-scan the skills directory (handy while authoring skills)."""
    _registry.load()
    _toolset.build()
    return list_skills()


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """Stream the agent's Reason->Act->Observe events as SSE."""

    async def event_stream():
        async for event in _agent.run(req.message, req.history):
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


def main() -> None:
    """Run the dev server: `python backend/app/main.py` or `uv run skill-forge`."""
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
