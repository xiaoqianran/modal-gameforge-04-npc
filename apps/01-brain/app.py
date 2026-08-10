"""Brain — prefer apps/pipeline (node 01)

One-shot only. Prefer the unified pipeline:

  modal run apps/pipeline/app.py --text "前面那座塔是什么地方？"

Module kept for directory numbering compatibility.
"""
import modal

app = modal.App("gf-04-01-brain-redirect")

@app.local_entrypoint()
def main():
    print('Use: modal run apps/pipeline/app.py --text "..."')
    print("Module: 01-brain")
