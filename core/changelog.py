"""
更新日志解析（1.7.3）

数据源：插件根目录 README.md 的「## 📝 更新日志」章节。

采用**运行时解析**而不是硬编码，好处是：
  · 更新 README 就等于更新日志，不用改两处
  · 不会出现「指令显示的版本和 README 不一致」
解析失败时返回空列表，调用方回落提示，不影响其它功能。

输出原则（与 README 一致）：
  · 正文**完整保留**，不截断、不改写，只去掉 Markdown 标记
  · 指令默认**只显示最新一天**的更新（同一天的多条一起显示）
"""
import os
import re

try:
    from astrbot.api import logger
except Exception:  # pragma: no cover
    import logging
    logger = logging.getLogger("changelog")

# 章节标题（标题里含「更新日志」即可，允许前后有空格）
_SECTION_RE = re.compile(r"^##\s*[^#\n]*更新日志\s*$", re.M)
# 单条：**v1.7.3（2026-10-03）** 正文（正文可以是多行，每行一项）
#   正文用「行首」锚定结束：遇到空行后的下一个 **vX.Y.Z（日期）** 或 ## 标题为止
_ENTRY_HEAD_RE = re.compile(
    r"^\*\*v([0-9][0-9.]*)[（(]([^）)]*)[）)]\*\*[ \t]*(.*)$", re.M
)
_README_NAME = "README.md"
# 显示条数硬上限（「全部」时避免刷屏）
_MAX_ALL = 30


def _readme_path() -> str:
    """插件根目录下的 README.md（本模块在 core/ 下）"""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, _README_NAME)


def read_readme() -> str:
    """读 README 文本，失败返回空串"""
    try:
        p = _readme_path()
        if not os.path.isfile(p):
            logger.warning(f"[changelog] 未找到 {p}")
            return ""
        with open(p, "r", encoding="utf-8-sig") as f:
            return f.read()
    except Exception:
        logger.exception("[changelog] 读取 README 失败")
        return ""


def _clean_body(raw: str) -> str:
    """
    清理正文：去掉 Markdown 标记，**保留换行**（每行一项）。

    · 不截断内容
    · 去掉 `**加粗**`、`` `代码` ``（聊天窗口不渲染 Markdown）
    · 去掉行首的 `-`/`*` 列表符号 —— 群里看到「- 」很啰嗦
    """
    s = str(raw or "")
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)   # 加粗
    s = re.sub(r"`([^`]*)`", r"\1", s)        # 行内代码
    out_lines = []
    for ln in s.splitlines():
        ln = re.sub(r"[ \t\u3000]+", " ", ln).strip()
        ln = re.sub(r"^[-*\u2022]\s*", "", ln)   # 行首列表符号
        if ln:
            out_lines.append(ln)
    return "\n".join(out_lines)


def parse_entries() -> list:
    """
    解析更新日志，返回 [{version, date, body}, ...]
    （按 README 顺序，最新在前）
    """
    text = read_readme()
    if not text:
        return []
    # 只取「更新日志」章节
    m = _SECTION_RE.search(text)
    seg = text[m.end():] if m else text
    # 遇到下一个二级标题就停
    nxt = re.search(r"^##\s", seg, re.M)
    if nxt:
        seg = seg[:nxt.start()]

    out = []
    heads = list(_ENTRY_HEAD_RE.finditer(seg))
    for k, em in enumerate(heads):
        # 正文 = 本行剩余 + 直到下一条目之前的行
        end = heads[k + 1].start() if k + 1 < len(heads) else len(seg)
        first = em.group(3).strip()
        rest = seg[em.end():end]
        # 去掉与下一条之间的分隔空行
        rest = rest.rstrip()
        body = (first + "\n" + rest) if rest.strip() else first
        out.append({
            "version": em.group(1),
            "date": em.group(2).strip(),
            "body": _clean_body(body),
        })
    return out


def latest_day() -> list:
    """
    最新**一天**的所有条目。

    同一天可能发了多个版本（如 1.7.2 / 1.7.1 都是 10-02），
    这些一起返回，方便「今天更新了什么」一次看全。
    """
    items = parse_entries()
    if not items:
        return []
    d0 = items[0].get("date", "")
    if not d0:
        return items[:1]
    return [it for it in items if it.get("date", "") == d0]


def build_text(scope: str = "day", title: str = "本次更新") -> str:
    """
    拼成可直接发送的文本。

    scope:
      "day"  -> 只显示最新一天（默认）
      "all"  -> 显示全部（最多 _MAX_ALL 条）

    正文与 README 完全一致（仅去掉 Markdown 标记）。
    """
    if str(scope).lower() in ("all", "全部"):
        items = parse_entries()[:_MAX_ALL]
    else:
        items = latest_day()

    if not items:
        return ("⚠️ 读不到更新日志\n"
                "💡 请确认插件根目录存在 README.md，且含「更新日志」章节")

    lines = []
    if title:
        lines.append(f"📝 {title}")     # 简洁一行标题
    for it in items:
        head = f"v{it['version']}"
        if it.get("date"):
            head += f"（{it['date']}）"
        lines.append("")
        lines.append(head)
        if it.get("body"):
            lines.append(it["body"])
    return "\n".join(lines).rstrip()
