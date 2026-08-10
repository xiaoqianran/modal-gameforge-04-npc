"""GameForge 04-npc — ComfyUI-style ONE-SHOT pipeline (NO deploy).

  modal run apps/pipeline/app.py --text "前面那座塔是什么地方？"
  modal run apps/pipeline/app.py --text "有什么任务吗？" --npc-id guard_01

Nodes (仓内 01–03 串联，一次 modal run，容器即毁):
  01-brain   Persona + World + Memory → structured Godot actions
  02-memory  读写 NPC 记忆 / 世界状态 (Volume)
  03-quest   约束任务图生成
  pack       zip 全部产物

Default: CPU rule/schema engine (deterministic, free). No asgi, no keep_warm.
Upgrade path: swap brain to Qwen2.5-Instruct on A10G when you want GPU LLM.
"""
from __future__ import annotations

import io
import json
import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import modal

APP_NAME = "gf-04-npc-pipeline"
VOL_ASSETS = "gameforge-assets"
VOL_MEM = "gameforge-npc-memory"

app = modal.App(APP_NAME)
vol_assets = modal.Volume.from_name(VOL_ASSETS, create_if_missing=True)
vol_mem = modal.Volume.from_name(VOL_MEM, create_if_missing=True)

image = modal.Image.debian_slim(python_version="3.11").pip_install("pydantic>=2")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_root(job_id: str) -> Path:
    return Path("/assets") / "jobs" / job_id


def _write_meta(job_id: str, **fields: Any) -> dict:
    root = _job_root(job_id)
    meta_path = root / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"job_id": job_id, "artifacts": {}, "steps_done": []}
    meta.update(fields)
    meta["updated_at"] = _now()
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def _save(job_id: str, rel: str, data: bytes) -> str:
    path = _job_root(job_id) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    meta = _write_meta(job_id)
    arts = meta.setdefault("artifacts", {})
    arts[rel] = rel
    _write_meta(job_id, artifacts=arts)
    return rel


def _mem_dir(npc_id: str) -> Path:
    return Path("/mem") / "npcs" / npc_id


def _load_memory(npc_id: str, limit: int = 30) -> list[dict]:
    p = _mem_dir(npc_id) / "memory.jsonl"
    if not p.exists():
        return []
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def _append_memory(npc_id: str, entry: dict) -> dict:
    d = _mem_dir(npc_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "memory.jsonl"
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _load_world(npc_id: str) -> dict:
    p = _mem_dir(npc_id) / "world.json"
    if not p.exists():
        world = {
            "npc_id": npc_id,
            "location": "village_gate",
            "time_of_day": "day",
            "flags": {
                "knows_player": False,
                "quest_clear_wolves_offered": False,
                "quest_tower_hinted": False,
            },
            "relations": {"player": 0},
            "updated_at": _now(),
        }
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(world, ensure_ascii=False, indent=2), encoding="utf-8")
        return world
    return json.loads(p.read_text(encoding="utf-8"))


def _save_world(npc_id: str, world: dict) -> None:
    p = _mem_dir(npc_id) / "world.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    world["updated_at"] = _now()
    p.write_text(json.dumps(world, ensure_ascii=False, indent=2), encoding="utf-8")


def _brain(
    text: str,
    persona: str,
    npc_id: str,
    world: dict,
    memories: list[dict],
) -> dict[str, Any]:
    """Deterministic structured brain → Godot action list (schema v1)."""
    t = text.strip()
    actions: list[dict] = []
    world_delta: dict[str, Any] = {}
    quest_seed: str | None = None
    intent = "unknown"

    mem_hint = ""
    if memories:
        last = memories[-3:]
        bits = [m.get("text", "")[:40] for m in last]
        mem_hint = "；".join(bits)

    # intent detection
    if any(k in t for k in ["塔", "tower", "哪里", "什么地方", "路怎么走", "北面"]):
        intent = "ask_location_tower"
        dialogue = (
            f"那座高塔在村子北面，顺着石板路走就能到。"
            f"不过夜里别去，{persona}也管不住那里的风。"
        )
        if world.get("flags", {}).get("quest_tower_hinted"):
            dialogue = "北塔我说过了——石板路一直往北，别在夜里硬闯。"
        actions += [
            {"type": "look_at", "target": "player", "duration": 0.4},
            {"type": "point", "target": "north_tower", "duration": 1.2},
            {"type": "set_marker", "id": "marker_north_tower", "pos": [12.0, 0.0, -30.0], "label": "北塔"},
            {"type": "set_flag", "flag": "quest_tower_hinted", "value": True},
        ]
        world_delta.setdefault("flags", {})["quest_tower_hinted"] = True
        world_delta.setdefault("flags", {})["knows_player"] = True
        world_delta.setdefault("relations", {})["player"] = int(world.get("relations", {}).get("player", 0)) + 1

    elif any(k in t for k in ["任务", "quest", "委托", "帮忙", "有活", "工作"]):
        intent = "ask_quest"
        if world.get("flags", {}).get("quest_clear_wolves_offered"):
            dialogue = "狼群的事我已经跟你说过了。东边田埂，清完回来找我。"
            actions += [
                {"type": "emote", "name": "point_east", "duration": 0.8},
                {"type": "set_marker", "id": "marker_east_fields", "pos": [40.0, 0.0, 8.0], "label": "东边田埂"},
            ]
            quest_seed = "清剿东边狼群"
        else:
            dialogue = "最近狼群靠近麦田了。你若愿意，去东边田埂清一清，回头找我领赏。"
            actions += [
                {"type": "offer_quest", "quest_id": "clear_wolves", "title": "清剿东边狼群"},
                {"type": "set_marker", "id": "marker_east_fields", "pos": [40.0, 0.0, 8.0], "label": "东边田埂"},
                {"type": "set_flag", "flag": "quest_clear_wolves_offered", "value": True},
            ]
            quest_seed = "清剿东边狼群"
            world_delta.setdefault("flags", {})["quest_clear_wolves_offered"] = True
            world_delta.setdefault("flags", {})["knows_player"] = True

    elif any(k in t for k in ["你好", "hello", "嗨", "在吗", "早上好", "晚上好"]):
        intent = "greet"
        if world.get("flags", {}).get("knows_player"):
            dialogue = f"又见面了。我是{persona}，有事快说。"
        else:
            dialogue = f"嗯，我是{persona}。有事就说，别挡路。"
        actions += [
            {"type": "emote", "name": "nod", "duration": 0.8},
            {"type": "look_at", "target": "player", "duration": 0.5},
        ]
        world_delta.setdefault("flags", {})["knows_player"] = True
        world_delta.setdefault("relations", {})["player"] = int(world.get("relations", {}).get("player", 0)) + 1

    elif any(k in t for k in ["战", "打", "attack", "敌人", "动手", "比试"]):
        intent = "threat"
        dialogue = "想动手？先掂量掂量自己的家伙什。村里动武，我可不是吓大的。"
        actions += [
            {"type": "combat_ready", "weapon": "spear"},
            {"type": "emote", "name": "glare", "duration": 1.0},
            {"type": "set_flag", "flag": "hostile_warning", "value": True},
        ]
        world_delta.setdefault("relations", {})["player"] = int(world.get("relations", {}).get("player", 0)) - 2

    elif any(k in t for k in ["记住", "别忘", "memory", "名字叫"]):
        intent = "ask_remember"
        dialogue = "好，我记下了。" + (f"（先前：{mem_hint}）" if mem_hint else "")
        actions += [{"type": "emote", "name": "think", "duration": 0.6}]

    else:
        intent = "fallback"
        dialogue = "……我听不太明白。你可以问北塔、任务，或者打个招呼。"
        actions += [{"type": "idle", "duration": 1.0}, {"type": "emote", "name": "shrug", "duration": 0.5}]

    # always speak last as explicit action for Godot dialogue system
    actions.append(
        {
            "type": "say",
            "text": dialogue,
            "audio_key": f"vo_{npc_id}_{intent}",
            "bubble": True,
        }
    )

    return {
        "schema": "gameforge.npc_action.v1",
        "npc_id": npc_id,
        "persona": persona,
        "input": t,
        "intent": intent,
        "dialogue": dialogue,
        "actions": actions,
        "world_delta": world_delta,
        "quest_seed": quest_seed,
        "memory_context": [m.get("text") for m in memories[-5:]],
        "engine": "rule_brain_v2",
        "created_at": _now(),
    }


def _generate_quest(seed: str, giver: str, npc_id: str) -> dict:
    qid = "q_" + uuid.uuid4().hex[:8]
    # specialize a bit from seed keywords
    if any(k in seed for k in ["狼", "wolf"]):
        objectives = [
            {"id": "obj1", "type": "goto", "target": "east_fields", "desc": "前往东边田埂"},
            {"id": "obj2", "type": "defeat", "target": "wolf", "count": 3, "desc": "击败 3 只狼"},
            {"id": "obj3", "type": "return", "target": npc_id, "desc": f"回报{giver}"},
        ]
        rewards = {"gold": 20, "item": "village_token", "xp": 50}
    elif any(k in seed for k in ["塔", "tower"]):
        objectives = [
            {"id": "obj1", "type": "goto", "target": "north_tower", "desc": "前往北塔"},
            {"id": "obj2", "type": "investigate", "target": "tower_seal", "desc": "调查塔底封印"},
            {"id": "obj3", "type": "return", "target": npc_id, "desc": f"向{giver}汇报"},
        ]
        rewards = {"gold": 35, "item": "old_map_fragment", "xp": 80}
    else:
        objectives = [
            {"id": "obj1", "type": "goto", "target": "village_square", "desc": "前往村口广场"},
            {"id": "obj2", "type": "talk", "target": giver, "desc": f"与{giver}交谈"},
        ]
        rewards = {"gold": 10, "xp": 20}

    return {
        "schema": "gameforge.quest.v1",
        "id": qid,
        "title": seed[:48] or "无名委托",
        "giver": giver,
        "giver_npc_id": npc_id,
        "objectives": objectives,
        "rewards": rewards,
        "flags": {"repeatable": False, "level_min": 1, "auto_track": True},
        "created_at": _now(),
        "engine": "quest_rule_v2",
    }


def _godot_bundle(brain: dict, quest: dict | None, world: dict, memories: list[dict]) -> dict:
    """Single file Godot can parse at runtime."""
    return {
        "schema": "gameforge.godot_npc_tick.v1",
        "npc_id": brain["npc_id"],
        "dialogue": brain["dialogue"],
        "actions": brain["actions"],
        "quest": quest,
        "world": world,
        "memory_tail": memories[-10:],
        "engine": "npc_pipeline_v1",
        "created_at": _now(),
    }


@app.function(
    image=image,
    cpu=2,
    memory=2048,
    timeout=10 * 60,
    volumes={"/assets": vol_assets, "/mem": vol_mem},
)
def run_npc_pipeline(
    text: str = "前面那座塔是什么地方？",
    persona: str = "村庄守卫",
    npc_id: str = "guard_01",
) -> dict:
    job_id = uuid.uuid4().hex[:12]
    root = _job_root(job_id)
    (root / "inputs").mkdir(parents=True, exist_ok=True)
    (root / "outputs").mkdir(parents=True, exist_ok=True)
    req = {
        "text": text,
        "persona": persona,
        "npc_id": npc_id,
        "pipeline": "npc_vertical_slice_v1",
        "mode": "one_shot_modal_run",
    }
    (root / "inputs" / "request.json").write_text(json.dumps(req, ensure_ascii=False, indent=2), encoding="utf-8")
    meta = {
        "job_id": job_id,
        "pipeline": "npc_vertical_slice_v1",
        "status": "running",
        "text": text,
        "npc_id": npc_id,
        "steps": ["02-memory-load", "01-brain", "03-quest", "02-memory-write", "pack"],
        "steps_done": [],
        "artifacts": {"inputs/request.json": "inputs/request.json"},
        "errors": {},
        "created_at": _now(),
        "updated_at": _now(),
    }
    (root / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    vol_assets.commit()
    results: dict[str, Any] = {"job_id": job_id, "nodes": {}}

    # ───────────── 02 MEMORY LOAD ─────────────
    print("[02-memory] load")
    try:
        world = _load_world(npc_id)
        memories = _load_memory(npc_id, limit=30)
        snap = {"world": world, "memories": memories, "count": len(memories)}
        rel = _save(job_id, "outputs/02_memory_before.json", json.dumps(snap, ensure_ascii=False, indent=2).encode())
        steps = list(_write_meta(job_id).get("steps_done") or [])
        steps.append("02-memory-load")
        _write_meta(job_id, steps_done=steps)
        results["nodes"]["02-memory-load"] = {"ok": True, "memory_count": len(memories), "artifact": rel}
        print("[02-memory] load OK", len(memories))
    except Exception as e:
        print("[02-memory] load FAIL", e)
        world = {"npc_id": npc_id, "flags": {}, "relations": {"player": 0}}
        memories = []
        errs = _write_meta(job_id).get("errors") or {}
        errs["02-memory-load"] = str(e)[:500]
        steps = list(_write_meta(job_id).get("steps_done") or [])
        steps.append("02-memory-load-fallback")
        _write_meta(job_id, steps_done=steps, errors=errs)
        results["nodes"]["02-memory-load"] = {"ok": False, "fallback": True, "error": str(e)[:300]}

    # ───────────── 01 BRAIN ─────────────
    print("[01-brain]")
    try:
        brain = _brain(text, persona=persona, npc_id=npc_id, world=world, memories=memories)
        rel = _save(job_id, "outputs/01_actions.json", json.dumps(brain, ensure_ascii=False, indent=2).encode())
        steps = list(_write_meta(job_id).get("steps_done") or [])
        steps.append("01-brain")
        _write_meta(job_id, steps_done=steps, intent=brain.get("intent"))
        results["nodes"]["01-brain"] = {
            "ok": True,
            "engine": brain.get("engine"),
            "intent": brain.get("intent"),
            "n_actions": len(brain.get("actions", [])),
            "artifact": rel,
        }
        print("[01-brain] OK", brain.get("intent"), len(brain.get("actions", [])))
    except Exception as e:
        print("[01-brain] FAIL", e)
        brain = {
            "schema": "gameforge.npc_action.v1",
            "npc_id": npc_id,
            "dialogue": "……",
            "actions": [{"type": "say", "text": "……"}],
            "intent": "error",
            "quest_seed": None,
            "world_delta": {},
            "error": str(e)[:300],
        }
        _save(job_id, "outputs/01_actions.json", json.dumps(brain, ensure_ascii=False, indent=2).encode())
        errs = _write_meta(job_id).get("errors") or {}
        errs["01-brain"] = str(e)[:500]
        steps = list(_write_meta(job_id).get("steps_done") or [])
        steps.append("01-brain-fail")
        _write_meta(job_id, steps_done=steps, errors=errs)
        results["nodes"]["01-brain"] = {"ok": False, "error": str(e)[:300]}

    # ───────────── 03 QUEST ─────────────
    print("[03-quest]")
    quest = None
    try:
        seed = brain.get("quest_seed")
        if seed:
            quest = _generate_quest(seed, giver=persona, npc_id=npc_id)
            # attach quest id onto offer action if present
            for a in brain.get("actions", []):
                if a.get("type") == "offer_quest":
                    a["quest_id"] = quest["id"]
                    a["title"] = quest["title"]
            # rewrite actions file with quest id filled
            _save(job_id, "outputs/01_actions.json", json.dumps(brain, ensure_ascii=False, indent=2).encode())
            rel = _save(job_id, "outputs/03_quest.json", json.dumps(quest, ensure_ascii=False, indent=2).encode())
            results["nodes"]["03-quest"] = {"ok": True, "quest_id": quest["id"], "title": quest["title"], "artifact": rel}
            print("[03-quest] OK", quest["id"])
        else:
            empty = {"schema": "gameforge.quest.v1", "offered": False, "reason": "no_quest_intent"}
            rel = _save(job_id, "outputs/03_quest.json", json.dumps(empty, ensure_ascii=False, indent=2).encode())
            results["nodes"]["03-quest"] = {"ok": True, "offered": False, "artifact": rel}
            print("[03-quest] skip (no seed)")
        steps = list(_write_meta(job_id).get("steps_done") or [])
        steps.append("03-quest")
        _write_meta(job_id, steps_done=steps)
    except Exception as e:
        print("[03-quest] FAIL", e)
        errs = _write_meta(job_id).get("errors") or {}
        errs["03-quest"] = str(e)[:500]
        steps = list(_write_meta(job_id).get("steps_done") or [])
        steps.append("03-quest-fail")
        _write_meta(job_id, steps_done=steps, errors=errs)
        results["nodes"]["03-quest"] = {"ok": False, "error": str(e)[:300]}

    # ───────────── 02 MEMORY WRITE + world apply ─────────────
    print("[02-memory] write")
    try:
        # apply world_delta
        flags = world.setdefault("flags", {})
        for k, v in (brain.get("world_delta") or {}).get("flags", {}).items():
            flags[k] = v
        rels = world.setdefault("relations", {})
        for k, v in (brain.get("world_delta") or {}).get("relations", {}).items():
            rels[k] = v
        _save_world(npc_id, world)

        entry = {
            "id": uuid.uuid4().hex[:10],
            "ts": _now(),
            "kind": "dialogue_turn",
            "text": f"player: {text} | npc: {brain.get('dialogue', '')[:80]}",
            "meta": {
                "intent": brain.get("intent"),
                "job_id": job_id,
                "quest_id": (quest or {}).get("id"),
            },
        }
        _append_memory(npc_id, entry)
        if quest:
            _append_memory(
                npc_id,
                {
                    "id": uuid.uuid4().hex[:10],
                    "ts": _now(),
                    "kind": "quest_offer",
                    "text": f"offered quest {quest['id']}: {quest['title']}",
                    "meta": {"quest_id": quest["id"]},
                },
            )

        memories_after = _load_memory(npc_id, limit=30)
        after = {"world": world, "memories": memories_after, "count": len(memories_after), "last_entry": entry}
        rel = _save(job_id, "outputs/02_memory_after.json", json.dumps(after, ensure_ascii=False, indent=2).encode())
        vol_mem.commit()
        steps = list(_write_meta(job_id).get("steps_done") or [])
        steps.append("02-memory-write")
        _write_meta(job_id, steps_done=steps)
        results["nodes"]["02-memory-write"] = {
            "ok": True,
            "memory_count": len(memories_after),
            "artifact": rel,
        }
        print("[02-memory] write OK", len(memories_after))
    except Exception as e:
        print("[02-memory] write FAIL", e)
        errs = _write_meta(job_id).get("errors") or {}
        errs["02-memory-write"] = str(e)[:500]
        steps = list(_write_meta(job_id).get("steps_done") or [])
        steps.append("02-memory-write-fail")
        _write_meta(job_id, steps_done=steps, errors=errs)
        results["nodes"]["02-memory-write"] = {"ok": False, "error": str(e)[:300]}
        memories_after = memories

    # godot bundle
    bundle = _godot_bundle(brain, quest, world, memories_after)
    _save(job_id, "outputs/godot_npc_tick.json", json.dumps(bundle, ensure_ascii=False, indent=2).encode())

    # ───────────── PACK ─────────────
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in root.rglob("*"):
            if p.is_file():
                zf.write(p, arcname=str(p.relative_to(root)))
    zdata = buf.getvalue()
    _save(job_id, "outputs/npc_pack.zip", zdata)
    steps = list(_write_meta(job_id).get("steps_done") or [])
    steps.append("pack")
    final = _write_meta(job_id, steps_done=steps, status="done", intent=brain.get("intent"))
    vol_assets.commit()
    results["status"] = "done"
    results["meta"] = final
    results["pack_bytes"] = len(zdata)
    results["dialogue"] = brain.get("dialogue")
    results["intent"] = brain.get("intent")
    print("[pack] OK", len(zdata))
    return results


@app.function(image=image, volumes={"/assets": vol_assets}, timeout=120)
def read_artifact(job_id: str, rel: str) -> bytes:
    path = _job_root(job_id) / rel
    if not path.exists():
        raise FileNotFoundError(f"{job_id}:{rel}")
    return path.read_bytes()


@app.local_entrypoint()
def main(
    text: str = "前面那座塔是什么地方？",
    persona: str = "村庄守卫",
    npc_id: str = "guard_01",
    out_dir: str = "/workspace/modal-gameforge/modal-gameforge-04-npc/out",
    second_turn: str = "有什么任务吗？",
):
    """Run one or two turns to prove memory persistence across jobs."""
    print("=== GameForge 04-npc pipeline (one-shot, no deploy) ===")
    print(f"turn1 text={text!r} npc={npc_id}")
    r1 = run_npc_pipeline.remote(text=text, persona=persona, npc_id=npc_id)
    print(json.dumps({"job_id": r1["job_id"], "status": r1.get("status"), "intent": r1.get("intent")}, ensure_ascii=False, indent=2))

    jobs = [r1]
    if second_turn:
        print(f"turn2 text={second_turn!r} (memory continuity)")
        r2 = run_npc_pipeline.remote(text=second_turn, persona=persona, npc_id=npc_id)
        print(json.dumps({"job_id": r2["job_id"], "status": r2.get("status"), "intent": r2.get("intent")}, ensure_ascii=False, indent=2))
        jobs.append(r2)

    out_root = Path(out_dir)
    for result in jobs:
        job_id = result["job_id"]
        out = out_root / job_id
        out.mkdir(parents=True, exist_ok=True)
        for rel in [
            "meta.json",
            "outputs/01_actions.json",
            "outputs/02_memory_before.json",
            "outputs/02_memory_after.json",
            "outputs/03_quest.json",
            "outputs/godot_npc_tick.json",
            "outputs/npc_pack.zip",
        ]:
            try:
                data = read_artifact.remote(job_id, rel)
                dest = out / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                print(f"  saved {dest} ({len(data)} bytes)")
            except Exception as e:
                print(f"  skip {rel}: {e}")
        summary = {
            "job_id": job_id,
            "status": result.get("status"),
            "intent": result.get("intent"),
            "dialogue": result.get("dialogue"),
            "nodes": result.get("nodes"),
            "steps_done": result.get("meta", {}).get("steps_done"),
            "local_out": str(out),
        }
        (out / "RUN_SUMMARY.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # validate both done
    bad = [j for j in jobs if j.get("status") != "done"]
    print("=== DONE ===")
    print(json.dumps(
        [{"job_id": j["job_id"], "intent": j.get("intent"), "status": j.get("status")} for j in jobs],
        ensure_ascii=False,
        indent=2,
    ))
    if bad:
        raise SystemExit(1)
