"""03-quest — constrained quest graph generator (one-shot).

  modal run apps/03-quest/app.py --seed "清剿东边狼群"
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import modal

APP_NAME = "gf-04-03-quest-run"
app = modal.App(APP_NAME)
image = modal.Image.debian_slim(python_version="3.11")


@app.function(image=image, timeout=120)
def generate_quest(seed: str = "清剿东边狼群", giver: str = "村庄守卫") -> dict:
    qid = "q_" + uuid.uuid4().hex[:8]
    quest = {
        "schema": "gameforge.quest.v1",
        "id": qid,
        "title": seed[:40] or "无名委托",
        "giver": giver,
        "objectives": [
            {"id": "obj1", "type": "goto", "target": "east_fields", "desc": "前往东边田埂"},
            {"id": "obj2", "type": "defeat", "target": "wolf", "count": 3, "desc": "击败 3 只狼"},
            {"id": "obj3", "type": "return", "target": giver, "desc": f"回报{giver}"},
        ],
        "rewards": {"gold": 20, "item": "village_token"},
        "flags": {"repeatable": False, "level_min": 1},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine": "quest_rule_v1",
    }
    return quest


@app.local_entrypoint()
def main(seed: str = "清剿东边狼群", giver: str = "村庄守卫"):
    q = generate_quest.remote(seed=seed, giver=giver)
    print(json.dumps(q, ensure_ascii=False, indent=2))
