"""01-brain — LLM → structured Godot NPC actions.

Modal App: gf-04-01-brain
Primary: Qwen2.5-14B-Instruct on GPU with JSON schema prompting.
Fallback: deterministic template (no GPU) for CI / cold demos.
"""
from __future__ import annotations

import json
import re
from typing import Any

import modal

APP_NAME = "gf-04-01-brain"
MODEL_ID = "Qwen/Qwen2.5-14B-Instruct"
CACHE = "/cache"
app = modal.App(APP_NAME)
vol_cache = modal.Volume.from_name("gameforge-model-cache", create_if_missing=True)
vol_assets = modal.Volume.from_name("gameforge-assets", create_if_missing=True)

image_cpu = modal.Image.debian_slim(python_version="3.11").pip_install(
    "pydantic>=2", "fastapi[standard]"
)

image_gpu = (
    modal.Image.from_registry("nvidia/cuda:12.4.0-devel-ubuntu22.04", add_python="3.11")
    .entrypoint([])
    .pip_install(
        "torch==2.5.1",
        "transformers>=4.46.0",
        "accelerate",
        "sentencepiece",
        "protobuf",
        "pydantic>=2",
        "fastapi[standard]",
        extra_index_url="https://download.pytorch.org/whl/cu124",
    )
)

SYSTEM = """You are an NPC brain for a Godot game.
Return ONLY valid JSON (no markdown) matching:
{
  "npc_id": string,
  "say": string,
  "emotion": string,
  "actions": [
    {"type":"look_at","target":"player"} |
    {"type":"move_to","pos":[x,y,z],"speed":1.0} |
    {"type":"play_anim","name":string,"oneshot":true} |
    {"type":"set_quest_flag","key":string,"value":any} |
    {"type":"wait","seconds":number} |
    {"type":"emote","emotion":string} |
    {"type":"say","text":string}
  ],
  "memory_writes": [{"key":string,"value":any}],
  "quest_updates": [{"quest_id":string,"status":string}]
}
Allowed action types ONLY from the list above. Keep say under 40 Chinese characters when language is zh.
"""


def _fallback(npc_id: str, user_text: str, persona: str) -> dict:
    line = f"我是{npc_id}。{user_text[:20]}" if user_text else f"你好，我是{npc_id}。"
    return {
        "npc_id": npc_id,
        "say": line[:40],
        "emotion": "neutral",
        "actions": [
            {"type": "look_at", "target": "player"},
            {"type": "say", "text": line[:40]},
            {"type": "play_anim", "name": "talk", "oneshot": True},
        ],
        "memory_writes": [{"key": "last_player_line", "value": user_text}],
        "quest_updates": [],
        "engine": "fallback",
        "persona_used": persona[:80],
    }


@app.function(image=image_cpu, timeout=60, volumes={"/assets": vol_assets})
def think_fallback(
    npc_id: str,
    persona: str,
    player_text: str,
    world_state: dict[str, Any] | None = None,
) -> dict:
    _ = world_state
    return _fallback(npc_id, player_text, persona)


@app.cls(
    image=image_gpu,
    gpu="A100-40GB",
    timeout=20 * 60,
    scaledown_window=120,
    volumes={CACHE: vol_cache, "/assets": vol_assets},
    
)
class NpcBrain:
    @modal.enter()
    def load(self):
        import os
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        os.environ.setdefault("HF_HOME", CACHE)
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, cache_dir=CACHE)
        self.model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID,
            torch_dtype=torch.bfloat16,
            device_map="auto",
            cache_dir=CACHE,
        )
        vol_cache.commit()

    @modal.method()
    def think(
        self,
        npc_id: str,
        persona: str,
        player_text: str,
        world_state: dict[str, Any] | None = None,
        memory: list[dict] | None = None,
    ) -> dict:
        world_state = world_state or {}
        memory = memory or []
        user = {
            "npc_id": npc_id,
            "persona": persona,
            "player_text": player_text,
            "world_state": world_state,
            "memory": memory[-12:],
        }
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ]
        text = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self.tokenizer([text], return_tensors="pt").to(self.model.device)
        out = self.model.generate(**inputs, max_new_tokens=512, do_sample=True, temperature=0.7)
        gen = out[0][inputs.input_ids.shape[-1] :]
        raw = self.tokenizer.decode(gen, skip_special_tokens=True)
        data = _parse_json(raw)
        if not data:
            data = _fallback(npc_id, player_text, persona)
            data["raw"] = raw[:500]
        data["engine"] = MODEL_ID
        return data

    @modal.method()
    def think_to_job(
        self,
        job_id: str,
        npc_id: str,
        persona: str,
        player_text: str,
    ) -> dict:
        from pathlib import Path

        result = self.think.local(npc_id, persona, player_text)
        out = Path("/assets") / "jobs" / job_id / "outputs" / "npc" / "actions.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        vol_assets.commit()
        return result


def _parse_json(raw: str) -> dict | None:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    try:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start : end + 1])
    except Exception:
        return None
    return None


@app.function(image=image_cpu, timeout=60)
@modal.asgi_app()
def api():
    from fastapi import FastAPI
    from pydantic import BaseModel, Field

    web = FastAPI(title="gf-04-01-brain")

    class Req(BaseModel):
        npc_id: str = "guard_01"
        persona: str = "村庄守卫，说话简短，略微警惕"
        player_text: str = "你好"
        use_gpu: bool = Field(default=False, description="True → Qwen2.5-14B")
        world_state: dict[str, Any] = Field(default_factory=dict)

    @web.get("/health")
    def health():
        return {"ok": True, "app": APP_NAME, "model": MODEL_ID}

    @web.post("/think")
    def think(req: Req):
        if req.use_gpu:
            return NpcBrain().think.remote(
                req.npc_id, req.persona, req.player_text, req.world_state
            )
        return think_fallback.remote(
            req.npc_id, req.persona, req.player_text, req.world_state
        )

    return web


@app.local_entrypoint()
def main(text: str = "前面那座塔是什么地方？", gpu: bool = False):
    if gpu:
        print(json.dumps(NpcBrain().think.remote("guard_01", "谨慎的守卫", text), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(think_fallback.remote("guard_01", "谨慎的守卫", text), ensure_ascii=False, indent=2))
