# modal-gameforge-04-npc

智能 NPC —— **ComfyUI 式一次性流水线**（**禁止 deploy / asgi 常驻**）。

## 一键跑通

```bash
modal run apps/pipeline/app.py --text "前面那座塔是什么地方？"
```

默认会再跑第二句 `有什么任务吗？` 验证 **记忆跨 job 持久化**。

```bash
modal run apps/pipeline/app.py \
  --text "你好" \
  --second-turn "有什么任务吗？" \
  --npc-id guard_01
```

跑完容器销毁。默认 **CPU** 规则引擎（结构化 JSON，零 GPU）。

## 仓内节点（01 起重排）

| 序号 | 节点 | 默认实现 |
|------|------|----------|
| 01 | brain | Persona + World + Memory → Godot actions JSON |
| 02 | memory | Volume 记忆读写 + world flags |
| 03 | quest | 约束任务图（狼群/北塔…） |

## 产物

```text
out/<job_id>/
  outputs/01_actions.json
  outputs/02_memory_before.json
  outputs/02_memory_after.json
  outputs/03_quest.json
  outputs/godot_npc_tick.json
  outputs/npc_pack.zip
```

## Docs

https://xiaoqianran.github.io/modal-gameforge-04-npc/

## 不要做的事

- ❌ `modal deploy`
- ❌ `@modal.asgi_app` 常驻
- ❌ `keep_warm` / 常驻 GPU

## 已验证跑通（one-shot · 双回合）

```text
turn1 job: 98aa67d1671a  intent=ask_location_tower  status=done
turn2 job: 6b46b52937fa  intent=ask_quest           status=done  quest=q_eadc9581
memory: 0 → 1 → 3 (Volume persist across jobs)
nodes: memory-load ✓  brain ✓  quest ✓  memory-write ✓  pack ✓
mode: modal run only → Stopping app (CPU, 无 asgi/deploy)
```
