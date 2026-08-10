"""02-memory — NPC memory & world state store on Modal Volume.

Modal App: gf-04-02-memory
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import modal

APP_NAME = "gf-04-02-memory"
app = modal.App(APP_NAME)
vol = modal.Volume.from_name("gameforge-npc-memory", create_if_missing=True)
image = modal.Image.debian_slim(python_version="3.11").pip_install("fastapi[standard]")


def _npc_path(npc_id: str) -> Path:
    return Path("/mem") / "npc" / f"{npc_id}.json"


def _world_path(world_id: str = "default") -> Path:
    return Path("/mem") / "world" / f"{world_id}.json"


@app.function(image=image, volumes={"/mem": vol}, timeout=60)
def memory_get(npc_id: str) -> dict:
    p = _npc_path(npc_id)
    if not p.exists():
        return {"npc_id": npc_id, "facts": [], "dialog": []}
    return json.loads(p.read_text())


@app.function(image=image, volumes={"/mem": vol}, timeout=60)
def memory_append(npc_id: str, fact: dict[str, Any] | None = None, turn: dict | None = None) -> dict:
    data = memory_get.local(npc_id)
    if fact:
        data.setdefault("facts", []).append(fact)
    if turn:
        data.setdefault("dialog", []).append(turn)
        data["dialog"] = data["dialog"][-50:]
    p = _npc_path(npc_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    vol.commit()
    return data


@app.function(image=image, volumes={"/mem": vol}, timeout=60)
def world_get(world_id: str = "default") -> dict:
    p = _world_path(world_id)
    if not p.exists():
        return {"world_id": world_id, "flags": {}, "quests": {}}
    return json.loads(p.read_text())


@app.function(image=image, volumes={"/mem": vol}, timeout=60)
def world_patch(world_id: str, flags: dict | None = None, quests: dict | None = None) -> dict:
    data = world_get.local(world_id)
    if flags:
        data.setdefault("flags", {}).update(flags)
    if quests:
        data.setdefault("quests", {}).update(quests)
    p = _world_path(world_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    vol.commit()
    return data


@app.function(image=image, volumes={"/mem": vol}, timeout=60)
@modal.asgi_app()
def api():
    from fastapi import FastAPI
    from pydantic import BaseModel
    from typing import Any

    web = FastAPI(title="gf-04-02-memory")

    class MemIn(BaseModel):
        npc_id: str
        fact: dict[str, Any] | None = None
        turn: dict[str, Any] | None = None

    class WorldIn(BaseModel):
        world_id: str = "default"
        flags: dict[str, Any] | None = None
        quests: dict[str, Any] | None = None

    @web.get("/npc/{npc_id}")
    def get_npc(npc_id: str):
        return memory_get.local(npc_id)

    @web.post("/npc")
    def post_npc(body: MemIn):
        return memory_append.local(body.npc_id, body.fact, body.turn)

    @web.get("/world/{world_id}")
    def get_world(world_id: str):
        return world_get.local(world_id)

    @web.post("/world")
    def post_world(body: WorldIn):
        return world_patch.local(body.world_id, body.flags, body.quests)

    return web


@app.local_entrypoint()
def main():
    print(memory_append.remote("guard_01", fact={"met_player": True}))
    print(world_patch.remote("default", flags={"gate_open": False}))
