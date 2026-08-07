#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""知识库加载与注入：解析 frontmatter/原子条目，按 manifest 选择，渲染注入块，校验引用。

格式约定见 Obsidian 仓库 `knowledge/README.md`。RAG 就绪：每个 `##` 标题块即一个原子条目
= 未来检索 chunk；`select_knowledge` 是 manifest 静态点名与向量检索的统一抽象点。

只依赖标准库；由 pipeline.py 调用 `configure()` 后使用。
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = SKILL_ROOT / "references" / "knowledge" / "manifest.json"

ENTRY_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*-\d{3}$")
FILE_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)+$")
DOMAIN_RE = re.compile(r"^[a-z0-9]{1,20}$")
ENTRY_HEAD_RE = re.compile(r"^##\s+(.+?)[（(]([a-z0-9]+(?:-[a-z0-9]+)*-\d{3})[）)]\s*$")
KB_REF_RE = re.compile(r"知识#([a-z0-9]+(?:-[a-z0-9]+)*-\d{3})")

STATUS_ENUM = ("active", "deprecated", "archived", "superseded")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MIN_CHARS, MAX_CHARS = 40, 2500  # 正文长度警戒线（字符）

_NON_KB_FILES = {"README.md", "index.md", "CHANGELOG.md", "TODO-设计.md"}


@dataclass(frozen=True)
class KnowledgeEntry:
    id: str
    title: str
    tags: tuple[str, ...]
    content: str
    version: int
    status: str
    superseded_by: str | None
    updated: str
    file: str          # 相对 knowledge_root 的路径（posix）
    domain: str


@dataclass
class KnowledgeBase:
    root: Path
    entries: list[KnowledgeEntry]
    by_id: dict[str, KnowledgeEntry]
    by_file: dict[str, list[KnowledgeEntry]]
    domains: set[str]

    def get(self, eid: str) -> KnowledgeEntry | None:
        return self.by_id.get(eid)

    def entries_in_file(self, relpath: str) -> list[KnowledgeEntry]:
        return self.by_file.get(relpath.replace("\\", "/"), [])

    def query(self, facets: dict[str, Any], limit: int | None = None) -> list[KnowledgeEntry]:
        status = facets.get("status", "active")
        tags = {t for t in facets.get("tags", []) if t}
        results = [
            e for e in self.entries
            if e.status == status and (not tags or tags.issubset(set(e.tags)))
        ]
        results.sort(key=lambda e: e.id)
        return results[:limit] if limit else results


# ---------------------------------------------------------------------------
# frontmatter / 条目解析（最小 YAML 子集，不引入 PyYAML）
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"^---\n(.*?)\n---\s*\n?", re.DOTALL)


def _parse_scalar(v: str) -> Any:
    v = v.strip()
    if v in ("true", "True"):
        return True
    if v in ("false", "False"):
        return False
    if v in ("null", "None"):
        return None
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("\"", "'"):
        return v[1:-1]
    try:
        return int(v)
    except ValueError:
        return v


def _parse_fm_value(raw: str) -> Any:
    v = raw.strip()
    if v.startswith("[") and v.endswith("]"):
        return [_parse_scalar(x) for x in v[1:-1].split(",") if x.strip()]
    return _parse_scalar(v)


def _parse_fm_items(items: list[str]) -> list[Any]:
    out: list[Any] = []
    for item in items:
        it = item[2:] if item.startswith("- ") else item
        m = re.match(r"^([A-Za-z0-9_]+):(.*)$", it)
        if m:
            out.append({m.group(1): _parse_fm_value(m.group(2))})
        else:
            out.append(_parse_scalar(it))
    return out


def parse_frontmatter(text: str) -> dict[str, Any] | None:
    m = _FM_RE.match(text)
    if not m:
        return None
    data: dict[str, Any] = {}
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        m = re.match(r"^([A-Za-z0-9_]+):(.*)$", line)
        if not m:
            i += 1
            continue
        key, rest = m.group(1), m.group(2).strip()
        if rest:
            data[key] = _parse_fm_value(rest)
            i += 1
            continue
        items: list[str] = []
        i += 1
        while i < len(lines) and (lines[i].startswith("  ") or lines[i].startswith("\t")):
            items.append(lines[i].strip())
            i += 1
        data[key] = _parse_fm_items(items)
    return data


def _parse_callout(block_lines: list[str]) -> tuple[dict[str, Any], list[str]]:
    """解析条目开头的 `[!kb]` callout。返回 (元数据, 剩余正文行)。"""
    meta: dict[str, Any] = {}
    rest: list[str] = []
    in_kb = False
    for line in block_lines:
        if line.startswith(">"):
            body = line[1:].strip()
            if body.startswith("[!kb]"):
                in_kb = True
                continue
            if in_kb:
                if ":" in body:
                    k, v = body.split(":", 1)
                    meta[k.strip()] = _parse_scalar(v.strip())
                continue
        if in_kb:
            in_kb = False
        rest.append(line)
    return meta, rest


def _build_entry(relpath: str, domain: str, title: str, eid: str, buf: list[str]) -> KnowledgeEntry:
    meta, rest = _parse_callout(buf)
    tags = meta.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    version = meta.get("version", 1)
    if isinstance(version, str):
        version = int(version) if version.isdigit() else 1
    status = meta.get("status", "active")
    content = "\n".join(rest).strip()
    return KnowledgeEntry(
        id=eid,
        title=title.strip(),
        tags=tuple(tags),
        content=content,
        version=version,
        status=status,
        superseded_by=meta.get("superseded_by"),
        updated=meta.get("updated", ""),
        file=relpath,
        domain=domain,
    )


def parse_entries(md_text: str, relpath: str, domain: str) -> list[KnowledgeEntry]:
    entries: list[KnowledgeEntry] = []
    title = ""
    eid: str | None = None
    buf: list[str] = []
    for line in md_text.splitlines():
        if line.startswith("## 变更记录"):
            # 文件末尾的变更记录不属于任何条目，到此处截断（README：解析器跳过）
            if eid is not None:
                entries.append(_build_entry(relpath, domain, title, eid, buf))
            eid = None
            break
        m = ENTRY_HEAD_RE.match(line)
        if m:
            if eid is not None:
                entries.append(_build_entry(relpath, domain, title, eid, buf))
            title, eid = m.group(1), m.group(2)
            buf = []
            continue
        buf.append(line)
    if eid is not None:
        entries.append(_build_entry(relpath, domain, title, eid, buf))
    return entries


# ---------------------------------------------------------------------------
# 加载与缓存
# ---------------------------------------------------------------------------

def load_knowledge_base(root: Path) -> KnowledgeBase | None:
    if not root.is_dir():
        return None
    entries: list[KnowledgeEntry] = []
    by_file: dict[str, list[KnowledgeEntry]] = {}
    domains: set[str] = set()
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("_templates/") or path.name in _NON_KB_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        if not fm:
            continue
        if fm.get("type") == "domain":
            code = fm.get("code")
            if code and isinstance(code, str) and DOMAIN_RE.match(code):
                domains.add(code)
            continue
        if fm.get("type") != "knowledge":
            continue
        domain = fm.get("domain", "")
        domains.add(domain)
        file_entries = parse_entries(text, rel, domain)
        entries.extend(file_entries)
        by_file[rel] = file_entries
    by_id = {e.id: e for e in entries}
    return KnowledgeBase(root=root, entries=entries, by_id=by_id, by_file=by_file, domains=domains)


# 运行期配置与缓存
_ROOT: Path | None = None
_ENABLED = False
_MANIFEST_PATH: Path = DEFAULT_MANIFEST
_MODE = "manifest"
_base_cache: list[KnowledgeBase | None] = [None]
_block_cache: dict[str, str] = {}
_id_cache: dict[str, frozenset[str]] = {}
_signature: list[tuple[Any, ...] | None] = [None]
_last_check: list[float] = [0.0]
_lock = threading.Lock()


def configure(root: str | Path | None, enabled: bool = True,
              manifest_path: str | Path | None = None, mode: str = "manifest") -> None:
    global _ROOT, _ENABLED, _MANIFEST_PATH, _MODE
    with _lock:
        _ROOT = Path(root) if root else None
        _ENABLED = bool(enabled)
        _MANIFEST_PATH = Path(manifest_path) if manifest_path else DEFAULT_MANIFEST
        _MODE = mode
        _base_cache[0] = None
        _block_cache.clear()
        _id_cache.clear()
        _signature[0] = None


def _kb_signature() -> tuple[Any, ...]:
    if _ROOT is None or not _ROOT.is_dir():
        return ()
    sig = []
    for path in sorted(_ROOT.rglob("*.md")):
        rel = path.relative_to(_ROOT).as_posix()
        if rel.startswith("_templates/"):
            continue
        try:
            st = path.stat()
            sig.append((rel, st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    return tuple(sig)


def _refresh_cache() -> None:
    now = time.time()
    if now - _last_check[0] < 5.0:
        return
    _last_check[0] = now
    sig = _kb_signature()
    if sig != _signature[0]:
        _signature[0] = sig
        _base_cache[0] = None
        _block_cache.clear()
        _id_cache.clear()


def _base() -> KnowledgeBase | None:
    if _base_cache[0] is None and _ROOT is not None:
        _base_cache[0] = load_knowledge_base(_ROOT)
    return _base_cache[0]


def _manifest() -> dict[str, Any]:
    if not _MANIFEST_PATH.exists():
        return {}
    try:
        return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# 选择 / 渲染 / 校验
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return max(1, int(cjk / 1.7) + int(other / 3.5))


def select_knowledge(base: KnowledgeBase, selection: list[dict[str, Any]],
                     max_tokens: int | None = None) -> list[KnowledgeEntry]:
    selected: list[KnowledgeEntry] = []
    seen: set[str] = set()

    def add(e: KnowledgeEntry | None) -> None:
        if e is None or e.status != "active" or e.id in seen:
            return
        seen.add(e.id)
        selected.append(e)

    for item in selection:
        t = item.get("type")
        if t == "file":
            for e in base.entries_in_file(item.get("path", "")):
                add(e)
        elif t == "entry":
            add(base.get(item.get("id", "")))
        elif t == "query":
            for e in base.query(item.get("facets", {}), item.get("limit")):
                add(e)
        # 未知类型忽略（由 manifest.schema 约束）

    if max_tokens is None or not selected:
        return selected
    budgeted: list[KnowledgeEntry] = []
    total = 0
    for e in selected:
        cost = estimate_tokens(_render_entry(e))
        if budgeted and total + cost > max_tokens:
            break
        budgeted.append(e)
        total += cost
    return budgeted or selected[:1]


def _render_entry(e: KnowledgeEntry) -> str:
    tags = "、".join(e.tags) or "—"
    return f"## 知识#{e.id} {e.title}\n- 标签：{tags}\n- 版本：{e.version}\n\n{e.content}"


_GUARD = (
    "以下条目是项目知识库的通用参考，用于理解与归类，**不是本条录音的事实**；"
    "与录音证据冲突时以录音为准。只允许用于帮助分类、命名与推断方向，"
    "禁止据此补写录音未提及的事实。若输出参考了某条知识，请在对应文本字段内标注「知识#<id>」。"
)


def render_block(entries: list[KnowledgeEntry], instruction: str = "") -> str:
    if not entries:
        return ""
    parts = ["# 参考知识库（通用分类参考）", "", _GUARD]
    if instruction:
        parts.extend(["", instruction])
    for e in entries:
        parts.extend(["", _render_entry(e)])
    return "\n".join(parts)


def knowledge_block_for(module: str) -> str:
    if not _ENABLED or _ROOT is None:
        return ""
    _refresh_cache()
    if module in _block_cache:
        return _block_cache[module]
    with _lock:
        if module in _block_cache:
            return _block_cache[module]
        block, ids = _compute_block(module)
        _block_cache[module] = block
        _id_cache[module] = ids
    return block


def _compute_block(module: str) -> tuple[str, frozenset[str]]:
    base = _base()
    if base is None:
        return "", frozenset()
    spec = _manifest().get("modules", {}).get(module)
    if not spec:
        return "", frozenset()
    entries = select_knowledge(base, spec.get("selection", []), spec.get("max_tokens"))
    return render_block(entries, spec.get("instruction", "")), frozenset(e.id for e in entries)


def injected_ids(module: str) -> frozenset[str]:
    knowledge_block_for(module)
    return _id_cache.get(module, frozenset())


def is_enabled() -> bool:
    return _ENABLED and _ROOT is not None and _ROOT.is_dir()


def extract_knowledge_refs(text: str) -> list[str]:
    return KB_REF_RE.findall(text)


def _iter_texts(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_texts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_texts(v)


def validate_knowledge_refs(candidate: Any, allowed_ids: frozenset[str]) -> list[str]:
    bad: set[str] = set()
    for text in _iter_texts(candidate):
        for ref in extract_knowledge_refs(text):
            if ref not in allowed_ids:
                bad.add(ref)
    return sorted(bad)


def collect_knowledge_refs(obj: Any) -> list[str]:
    """收集候选输出中出现的全部知识引用（审计日志用）。"""
    refs: set[str] = set()
    for text in _iter_texts(obj):
        refs.update(extract_knowledge_refs(text))
    return sorted(refs)


def strip_knowledge_refs(text: str) -> str:
    if not text:
        return text
    text = KB_REF_RE.sub("", text)
    text = re.sub(r"[（(]\s*[）)]", "", text)  # 清除引用后残留的空括号
    return re.sub(r"[ \t]{2,}", " ", text).strip()


# ---------------------------------------------------------------------------
# 一致性校验（供 knowledge_check.py 与 --preflight 调用）
# ---------------------------------------------------------------------------

def validate_knowledge(root: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    root = Path(root)
    if not root.is_dir():
        return [f"知识库目录不存在: {root}"], []
    if not (root / "README.md").is_file():
        errors.append("知识库缺少 README.md")

    # 1) 收集领域代码（type: domain 的 README）
    domains: set[str] = set()
    for path in sorted(root.rglob("README.md")):
        if path.parent == root:
            continue
        fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        if fm and fm.get("type") == "domain" and isinstance(fm.get("code"), str):
            if DOMAIN_RE.match(fm["code"]):
                domains.add(fm["code"])
            else:
                errors.append(f"{path}: domain code 非法: {fm['code']!r}")

    # 2) 单遍解析所有知识文件，收集条目
    files: list[tuple[Path, dict[str, Any], list[KnowledgeEntry]]] = []
    entry_ids: dict[str, str] = {}
    file_ids: dict[str, Path] = {}
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("_templates/") or path.name in _NON_KB_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        if not fm:
            warnings.append(f"{path}: 无 frontmatter，跳过")
            continue
        if fm.get("type") == "domain":
            continue
        if fm.get("type") != "knowledge":
            warnings.append(f"{path}: 未知 type={fm.get('type')!r}，跳过")
            continue

        fid = fm.get("id")
        if not isinstance(fid, str) or not FILE_ID_RE.match(fid):
            errors.append(f"{path}: 文件级 id 非法: {fid!r}")
        elif fid in file_ids:
            errors.append(f"重复文件级 id: {fid} ({file_ids[fid]} 与 {path})")
        else:
            file_ids[fid] = path

        domain = fm.get("domain")
        if not isinstance(domain, str) or not DOMAIN_RE.match(domain):
            errors.append(f"{path}: domain 非法: {domain!r}")
        elif domain not in domains:
            errors.append(f"{path}: domain {domain!r} 未注册（缺少 {domain}/README.md）")

        version = fm.get("version")
        if not isinstance(version, int) or version < 1:
            errors.append(f"{path}: version 非法: {version!r}")
        status = fm.get("status", "active")
        if status not in STATUS_ENUM:
            errors.append(f"{path}: status 非法: {status!r}")
        updated = fm.get("updated", "")
        if not isinstance(updated, str) or not DATE_RE.match(updated):
            errors.append(f"{path}: updated 非法: {updated!r}")

        entries = parse_entries(text, rel, domain)
        files.append((path, fm, entries))

        file_version = version if isinstance(version, int) else 0
        for e in entries:
            if e.id in entry_ids:
                errors.append(f"重复条目 id: {e.id} ({entry_ids[e.id]} 与 {path})")
            else:
                entry_ids[e.id] = str(path)
            if not ENTRY_ID_RE.match(e.id):
                errors.append(f"{path}: 条目 id 非法: {e.id!r}")
            if e.version < 1:
                errors.append(f"{path}: 条目 {e.id} version 非法: {e.version}")
            if e.status not in STATUS_ENUM:
                errors.append(f"{path}: 条目 {e.id} status 非法: {e.status!r}")
            if e.status == "superseded" and not e.superseded_by:
                errors.append(f"{path}: 条目 {e.id} 为 superseded 但缺 superseded_by")
            n = len(e.content)
            if n < MIN_CHARS:
                warnings.append(f"{path}: 条目 {e.id} 过短({n}字符)，疑似空壳")
            elif n > MAX_CHARS:
                warnings.append(f"{path}: 条目 {e.id} 过长({n}字符)，建议拆分")
            file_version = max(file_version, e.version)
        if file_version != (version if isinstance(version, int) else 0):
            warnings.append(f"{path}: 文件 version({version}) 低于条目最大 version({file_version})")

    by_id = {e.id: e for _, _, ents in files for e in ents}
    active_ids = {e.id for e in by_id.values() if e.status == "active"}

    # 3) superseded → superseded_by 指向 active 条目
    for _, _, entries in files:
        for e in entries:
            if e.status != "superseded":
                continue
            target = e.superseded_by
            if not target:  # 缺 superseded_by 已在第 2 步报过
                continue
            if target not in by_id:
                errors.append(f"{e.file}: 条目 {e.id} 的 superseded_by {target!r} 不存在")
            elif by_id[target].status != "active":
                errors.append(f"{e.file}: 条目 {e.id} 的 superseded_by {target} 不是 active")

    # 4) 孤立引用：KB 文件正文里的 知识#<id> 必须解析到 active 条目
    for _, _, entries in files:
        for e in entries:
            for ref in extract_knowledge_refs(e.content):
                if ref not in active_ids:
                    errors.append(f"{e.file}: 条目 {e.id} 引用未知/非 active 知识#{ref}")

    return errors, warnings


def validate_manifest(manifest: Any, base: KnowledgeBase | None,
                      valid_modules: set[str]) -> tuple[list[str], list[str]]:
    """校验注入清单：模块名合法、selection 引用的文件/条目存在且 active、预算为正。"""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(manifest, dict) or not isinstance(manifest.get("modules"), dict):
        return ["manifest 必须含 modules 对象"], []
    mods: dict[str, Any] = manifest["modules"]
    for name, spec in mods.items():
        if name not in valid_modules:
            errors.append(f"manifest 引用未知模块: {name}")
            continue
        if not isinstance(spec, dict):
            errors.append(f"manifest[{name}] 不是对象")
            continue
        mt = spec.get("max_tokens")
        if not isinstance(mt, int) or mt < 1:
            errors.append(f"manifest[{name}].max_tokens 非法: {mt!r}")
        selection = spec.get("selection")
        if not isinstance(selection, list):
            errors.append(f"manifest[{name}].selection 必须是数组")
            continue
        for item in selection:
            if not isinstance(item, dict):
                errors.append(f"manifest[{name}].selection 含非对象项")
                continue
            t = item.get("type")
            if t == "file":
                rel = item.get("path")
                if not rel or base is None or rel not in base.by_file:
                    errors.append(f"manifest[{name}] 引用的文件不存在: {rel}")
            elif t == "entry":
                eid = item.get("id")
                e = base.get(eid) if base else None
                if e is None:
                    errors.append(f"manifest[{name}] 引用的条目不存在: {eid}")
                elif e.status != "active":
                    errors.append(f"manifest[{name}] 引用的条目非 active: {eid} ({e.status})")
            elif t == "query":
                if not isinstance(item.get("facets"), dict):
                    errors.append(f"manifest[{name}] query 缺 facets")
            else:
                errors.append(f"manifest[{name}] 未知 selection type: {t!r}")
    return errors, warnings
