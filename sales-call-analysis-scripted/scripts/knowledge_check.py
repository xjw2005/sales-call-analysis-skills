#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""知识库一致性自检：独立入口（退出码 0=通过 / 1=有错误 / 3=未配置）。

也由流水线的 --preflight 与 --knowledge-check 复用 run_check()。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import knowledge as kb
import pipeline as p


def run_check(root: str | Path, manifest_path: str | Path | None,
              valid_modules: set[str]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    kerrs, kwarns = kb.validate_knowledge(Path(root))
    errors.extend(kerrs)
    warnings.extend(kwarns)

    base = kb.load_knowledge_base(Path(root))
    mpath = Path(manifest_path) if manifest_path else kb.DEFAULT_MANIFEST
    if mpath.exists():
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"manifest 读取失败（{mpath}）: {exc}")
            manifest = {}
        merrs, mwarns = kb.validate_manifest(manifest, base, valid_modules)
        errors.extend(merrs)
        warnings.extend(mwarns)
        # 挂载覆盖检查：active 条目应至少被一个模块注入
        if base is not None:
            covered: set[str] = set()
            for name, spec in manifest.get("modules", {}).items():
                covered.update(
                    e.id for e in kb.select_knowledge(
                        base, spec.get("selection", []), spec.get("max_tokens")
                    )
                )
            unmounted = sorted(
                e.id for e in base.entries
                if e.status == "active" and e.id not in covered
            )
            if unmounted:
                warnings.append(
                    "挂载覆盖：以下 active 条目未被任何模块注入（新增内容记得挂载或确认不挂载）: "
                    + ", ".join(unmounted)
                )
    else:
        warnings.append(f"manifest 不存在（{mpath}），跳过清单校验")

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="知识库一致性自检")
    parser.add_argument(
        "--root", default=None,
        help="知识库目录；缺省读 config.local.json 的 knowledge_root",
    )
    parser.add_argument(
        "--manifest", default=None,
        help="注入清单路径；缺省用 skill 默认路径",
    )
    parser.add_argument("--quiet", action="store_true", help="只输出错误")
    args = parser.parse_args()

    root = args.root or p.CFG.get("knowledge_root")
    if not root:
        print("知识库未配置：config.local.json 缺 knowledge_root", file=sys.stderr)
        return 3

    errors, warnings = run_check(root, args.manifest, set(p.ALL_MODULES))
    for w in warnings:
        if not args.quiet:
            print(f"[kb-warn] {w}")
    for e in errors:
        print(f"[kb-ERROR] {e}")
    print(f"[kb-check] {'失败' if errors else '通过'}（错误 {len(errors)}，警告 {len(warnings)}）")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
