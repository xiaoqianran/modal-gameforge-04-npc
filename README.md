# modal-gameforge-04-npc

智能 NPC —— **one-shot `modal run` only**（无 asgi / 无 deploy 常驻）。

```bash
modal run apps/01-brain/app.py --text "前面那座塔是什么地方？"
modal run apps/02-memory/app.py --npc-id guard_01 --text "玩家问了北塔"
modal run apps/03-quest/app.py --seed "清剿东边狼群"
```

| 序号 | 模块 | 说明 |
|------|------|------|
| 01 | brain | 结构化 NPC actions JSON |
| 02 | memory | Volume 记忆 |
| 03 | quest | 任务图 |

## Docs

https://xiaoqianran.github.io/modal-gameforge-04-npc/
