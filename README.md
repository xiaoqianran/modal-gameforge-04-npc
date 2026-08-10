# modal-gameforge-04-npc

智能 NPC：**结构化动作脑 → 记忆/世界状态 → 任务叙事**

## 仓内编号

| 序号 | 模块 | 默认模型 |
|------|------|----------|
| 01 | Brain | **Qwen2.5-14B-Instruct**（GPU）/ 规则 fallback |
| 02 | Memory | Volume + JSON/SQLite 记忆库 |
| 03 | Quest | 约束式任务图生成 |

输出严格符合 `00-hub` contracts 中的 `NpcTurn` JSON，供 Godot 解释执行。
