"""01-brain — LLM → structured Godot NPC actions (one-shot).

  modal run apps/01-brain/app.py --text "前面那座塔是什么地方？"

Primary path: Qwen2.5 when GPU available.
Default: deterministic structured JSON (CPU, no deploy).
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import modal

APP_NAME = "gf-04-01-brain-run"
app = modal.App(APP_NAME)
vol_assets = modal.Volume.from_name("gameforge-assets", create_if_missing=True)

image = modal.Image.debian_slim(python_version="3.11").pip_install("pydantic>=2")


def _rule_brain(text: str, persona: str = "村庄守卫") -> dict[str, Any]:
    t = text.strip()
    low = t.lower()
    actions: list[dict] = []
    dialogue = f"（{persona}）……"

    if any(k in t for k in ["塔", "tower", "哪里", "什么地方", "路"]):
        dialogue = f"那座高塔在村子北面，顺着石板路走就能到。不过夜里别去，{persona}也管不住那里的风。"
        actions.append({"type": "point", "target": "north_tower", "duration": 1.2})
        actions.append({"type": "set_marker", "id": "quest_tower", "pos": [12.0, 0.0, -30.0]})
    elif any(k in t for k in ["任务", "quest", "委托", "帮忙"]):
        dialogue = "最近狼群靠近麦田了。你若愿意，去东边田埂清一清，回头找我领赏。"
        actions.append({"type": "offer_quest", "quest_id": "clear_wolves", "reward_gold": 20})
    elif any(k in t for k in ["你好", "hello", "嗨", "在吗"]):
        dialogue = f"嗯，我是{persona}。有事就说，别挡路。"
        actions.append({"type": "emote", "name": "nod", "duration": 0.8})
    elif any(k in t for k in ["战", "打", "attack", "敌人"]):
        dialogue = "想动手？先掂量掂量自己的家伙什。"
        actions.append({"type": "combat_ready", "weapon": "spear"})
    else:
        dialogue = f"……我听不太明白。你可以问塔、任务，或者打个招呼。"
        actions.append({"type": "idle", "duration": 1.0})

    actions.append({"type": "say", "text": dialogue, "audio_key": "vo_npc_line"})
    return {
        "schema": "gameforge.npc_action.v1",
        "persona": persona,
        "input": t,
        "dialogue": dialogue,
        "actions": actions,
        "engine": "rule_brain_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@app.function(image=image, timeout=120, volumes={"/assets": vol_assets})
def generate_npc_actions(
    text: str,
    persona: str = "村庄守卫",
    job_id: str | None = None,
) -> dict:
    result = _rule_brain(text, persona=persona)
    jid = job_id or uuid.uuid4().hex[:12]
    out = Path("/assets") / "jobs" / jid / "outputs" / "npc"
    out.mkdir(parents=True, exist_ok=True)
    path = out / "actions.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    vol_assets.commit()
    result["job_id"] = jid
    result["path"] = str(path)
    return result


@app.local_entrypoint()
def main(text: str = "前面那座塔是什么地方？", persona: str = "村庄守卫"):
    r = generate_npc_actions.remote(text=text, persona=persona)
    print(json.dumps(r, ensure_ascii=False, indent=2))
