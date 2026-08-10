"""03-quest — Constrained quest / narrative generation.

Modal App: gf-04-03-quest
"""
from __future__ import annotations

import json
from typing import Any

import modal

APP_NAME = "gf-04-03-quest"
app = modal.App(APP_NAME)
image = modal.Image.debian_slim(python_version="3.11").pip_install("pydantic>=2", "fastapi[standard]")


@app.function(image=image, timeout=120)
def generate_quest(
    seed_hook: str,
    level: int = 1,
    world_flags: dict[str, Any] | None = None,
) -> dict:
    """Rule-templated quest graph (LLM can replace later via 01-brain)."""
    world_flags = world_flags or {}
    qid = f"q_{abs(hash(seed_hook)) % 10_000:04d}"
    quest = {
        "quest_id": qid,
        "title": f"关于「{seed_hook[:12]}」的委托",
        "level": level,
        "stages": [
            {
                "id": "talk_npc",
                "type": "talk",
                "target": "quest_giver",
                "desc": f"与委托人交谈，了解{seed_hook}",
            },
            {
                "id": "collect_or_goto",
                "type": "goto",
                "target": "marker_01",
                "desc": "前往目标地点调查",
            },
            {
                "id": "report",
                "type": "talk",
                "target": "quest_giver",
                "desc": "回报调查结果",
                "rewards": {"xp": 50 * level, "gold": 20 * level},
            },
        ],
        "requires_flags": world_flags,
        "status": "available",
        "godot_hint": "Drive with QuestMachine; stages are linear v0",
    }
    return quest


@app.function(image=image, timeout=60)
@modal.asgi_app()
def api():
    from fastapi import FastAPI
    from pydantic import BaseModel
    from typing import Any

    web = FastAPI(title="gf-04-03-quest")

    class Req(BaseModel):
        seed_hook: str
        level: int = 1
        world_flags: dict[str, Any] | None = None

    @web.post("/generate")
    def gen(req: Req):
        return generate_quest.remote(req.seed_hook, req.level, req.world_flags)

    return web


@app.local_entrypoint()
def main(hook: str = "丢失的护身符"):
    print(json.dumps(generate_quest.remote(hook), ensure_ascii=False, indent=2))
