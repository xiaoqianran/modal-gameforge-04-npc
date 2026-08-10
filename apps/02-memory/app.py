"""02-memory — NPC memory / world state on Modal Volume (one-shot).

  modal run apps/02-memory/app.py --npc-id guard_01 --text "玩家问了北塔"
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import modal

APP_NAME = "gf-04-02-memory-run"
app = modal.App(APP_NAME)
vol = modal.Volume.from_name("gameforge-npc-memory", create_if_missing=True)
image = modal.Image.debian_slim(python_version="3.11")


def _npc_path(npc_id: str) -> Path:
    return Path("/mem") / "npcs" / npc_id / "memory.jsonl"


@app.function(image=image, volumes={"/mem": vol}, timeout=120)
def remember(npc_id: str, text: str, kind: str = "observation", meta: dict | None = None) -> dict:
    p = _npc_path(npc_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "id": uuid.uuid4().hex[:10],
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "text": text,
        "meta": meta or {},
    }
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    vol.commit()
    return {"ok": True, "npc_id": npc_id, "entry": entry}


@app.function(image=image, volumes={"/mem": vol}, timeout=120)
def recall(npc_id: str, limit: int = 20) -> dict:
    p = _npc_path(npc_id)
    if not p.exists():
        return {"ok": True, "npc_id": npc_id, "memories": []}
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    items = [json.loads(x) for x in lines[-limit:]]
    return {"ok": True, "npc_id": npc_id, "memories": items, "count": len(items)}


@app.local_entrypoint()
def main(npc_id: str = "guard_01", text: str = "玩家询问了北塔方向"):
    w = remember.remote(npc_id=npc_id, text=text)
    r = recall.remote(npc_id=npc_id, limit=5)
    print(json.dumps({"write": w, "recall": r}, ensure_ascii=False, indent=2))
