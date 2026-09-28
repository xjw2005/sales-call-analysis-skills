"""把一条拜访的分析结果导入数据库（AI 接入前的过渡工具）。

用法（在服务器 DingGO-backend 目录）：
    docker compose exec api python scripts/import_analysis.py --visit-id 12 --file result.json

result.json 结构与小程序 model/analysis.js 一致，可以是：
    {"analysis": {...}, "transcript": [...]}   或直接是 analysis 对象本身
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.models import Visit  # noqa: E402
from app.services.ingest import apply_analysis  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="导入拜访分析结果")
    parser.add_argument("--visit-id", type=int, required=True)
    parser.add_argument("--file", required=True)
    args = parser.parse_args()

    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    analysis = data.get("analysis", data)
    transcript = data.get("transcript")
    with SessionLocal() as db:
        visit = db.get(Visit, args.visit_id)
        if not visit:
            sys.exit(f"拜访记录 {args.visit_id} 不存在")
        apply_analysis(db, visit, analysis, transcript)
        todos = len((analysis.get("nextAction") or {}).get("actions", []))
        print(f"已导入拜访 {visit.id}：状态 done，生成待办 {todos} 条")


if __name__ == "__main__":
    main()
