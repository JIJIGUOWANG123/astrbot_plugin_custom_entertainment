"""
授权功能（1.3.0 新增）

按群控制插件是否可用，支持**到期自动关闭整个插件**。

数据文件：`data/plugin_data/<插件>/..._auth.json`
```json
{
  "groups": {
    "619915692": {"expire_ts": 0,       "note": ""},
    "378474934": {"expire_ts": 1794000000, "note": "月卡"},
    "123456":    {"expire_ts": -1,      "note": "已停用"}
  },
  "seen": {"999888": 1791900000}
}
```

`expire_ts` 约定：
```
 0       永久授权（从不过期）
 >0      Unix 秒级到期时间，到点即失效
 -1     手动停用
```

**默认行为**（后台 `auth_default_days`）—— ★★ 1.3.0 起**默认全部关闭**：
```
 auth_default_days = -1  -> 没记录的群一律**不授权**（默认，插件静默）
 auth_default_days = 0   -> 没记录的群一律**永久**
 auth_default_days = N   -> 没记录的群**首次见到时**自动登记 N 天试用
```
所以要**先授权**才能用：群里发「授权 30d」或「授权 永久」。
"""
import json
import os
import re
import time

from astrbot.api import logger

from .path_utils import auth_data_path

NEVER = 0
DISABLED = -1

def _now() -> int:
    return int(time.time())

def fmt_ts(ts: int) -> str:
    """把时间戳格式化成人看的样子"""
    try:
        t = int(ts)
    except Exception:
        return "?"
    if t == NEVER:
        return "永久"
    if t < 0:
        return "已停用"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))
    except Exception:
        return str(t)

def parse_duration(text: str):
    """
    解析时长 -> 秒数。认这些写法（大小写不敏感）：
        `30d` / `30天` / `30`   -> 30 天
        `12h` / `12小时`        -> 12 小时
        `90m` / `90分钟`        -> 90 分钟
        `永久` / `never` / `0`  -> 0（永久）
        `停用` / `off` / `-1`   -> -1
    解析不出返回 None。
    """
    s = str(text or "").strip().lower()
    if not s:
        return None
    if s in ("永久", "never", "forever", "0"):
        return NEVER
    if s in ("停用", "禁用", "off", "disable", "-1"):
        return DISABLED
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(d|day|days|天|日|h|hour|hours|小时|时|m|min|mins|分钟|分|s|sec|秒)?$", s)
    if not m:
        return None
    try:
        n = float(m.group(1))
    except Exception:
        return None
    unit = m.group(2) or "d"
    if unit in ("d", "day", "days", "天", "日"):
        sec = n * 86400
    elif unit in ("h", "hour", "hours", "小时", "时"):
        sec = n * 3600
    elif unit in ("m", "min", "mins", "分钟", "分"):
        sec = n * 60
    else:
        sec = n
    sec = int(sec)
    return sec if sec > 0 else NEVER

class AuthModule:
    """按群授权 + 到期关闭整个插件"""

    def __init__(self, plugin_dir: str = "", data_path: str = ""):
        self.plugin_dir = str(plugin_dir or "")
        self.data_path = str(data_path or "") or auth_data_path()
        self.enable = True
        self.default_days = -1
        self.warn_days = 3.0
        self.groups = {}
        self.seen = {}
        self.joined_groups = None
        self.known_groups = None
        self._log_cache = {}
        self.reload({})

    def reload(self, cfg: dict):
        cfg = cfg or {}
        self.enable = bool(cfg.get("auth_enable", True))
        try:
            from .path_utils import parse_cmd_names as _pcn
            self.cmd_names = _pcn(cfg.get("auth_remote_commands", "授权"))
        except Exception:
            raw = str(cfg.get("auth_remote_commands", "授权") or "授权")
            self.cmd_names = [x.strip() for x in
                              re.split(r"[\n,，]", raw) if x.strip()] or ["授权"]
        raw2 = str(cfg.get("auth_status_commands", "授权状态") or "授权状态")
        self.status_names = [x.strip() for x in
                             re.split(r"[\n,，]", raw2) if x.strip()] or ["授权状态"]
        raw3 = str(cfg.get("auth_list_commands", "授权列表") or "授权列表")
        self.list_names = [x.strip() for x in
                           re.split(r"[\n,，]", raw3) if x.strip()] or ["授权列表"]
        self.admin_groups = set()

        def _int(key, dflt):
            try:
                v = cfg.get(key, None)
                if v is None or str(v).strip() == "":
                    return dflt
                return int(float(str(v).strip()))
            except Exception:
                return dflt

        self.default_days = _int("auth_default_days", -1)
        try:
            self.warn_days = max(0.0, float(str(
                cfg.get("auth_warn_days", 3) or 3)))
        except Exception:
            self.warn_days = 3.0
        self._load()

        if self.default_days == 0 and not self.groups:
            logger.info(
                "[auth] 检测到旧默认值 auth_default_days=0（=全部永久），"
                "已按「授权默认全部关闭」纠正为 -1")
            self.default_days = -1

        try:
            self.cleanup_expired(30)
        except Exception:
            logger.debug("[auth] 清理过期记录失败", exc_info=True)

    def _load(self):
        if not self.data_path or not os.path.exists(self.data_path):
            self.groups, self.seen = {}, {}
            return
        try:
            with open(self.data_path, "r", encoding="utf-8-sig") as f:
                d = json.load(f) or {}
            g = d.get("groups")
            s = d.get("seen")
            self.groups = g if isinstance(g, dict) else {}
            self.seen = s if isinstance(s, dict) else {}
            clean = {}
            for k, v in self.groups.items():
                if not isinstance(v, dict):
                    continue
                try:
                    ts = int(v.get("expire_ts", NEVER))
                except Exception:
                    ts = NEVER
                clean[str(k)] = {"expire_ts": ts,
                                 "note": str(v.get("note", "") or "")}
            self.groups = clean
        except Exception:
            logger.exception("[auth] 读取授权数据失败")
            self.groups, self.seen = {}, {}

    def _save(self):
        if not self.data_path:
            return
        try:
            os.makedirs(os.path.dirname(self.data_path), exist_ok=True)
            tmp = self.data_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"groups": self.groups, "seen": self.seen},
                          f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.data_path)
        except Exception:
            logger.exception("[auth] 保存授权数据失败")

    def default_rule_text(self, markdown: bool = True) -> str:
        """
        「未设置的群」规则文案（两处以上要用，集中一处避免各写各的）。

        `auth_default_days`：-1 不授权（默认） / 0 永久 / N>0 试用 N 天
        """
        d = int(getattr(self, "default_days", -1))
        if d < 0:
            return "🚫 **不授权**（默认）" if markdown else "不授权（默认）"
        if d == 0:
            return "♾️ **永久授权**" if markdown else "永久授权"
        return (f"🎁 试用 **{d} 天**" if markdown else f"试用 {d} 天")

    def record(self, group_id):
        """
        取某个群的授权记录。

        ★★ 1.3.0：**默认全部关闭** —— 没设置过的群一律「未授权」。

        `auth_default_days` 的语义（新）：
        ```
          -1   未设置 = **不授权**（默认，插件对该群静默）
           0   未设置 = 永久授权
          N>0  首次见到时自动登记 N 天试用
        ```
        """
        gid = str(group_id or "").strip()
        if not gid.isdigit():
            return None
        r = self.groups.get(gid)
        if isinstance(r, dict):
            return r
        if self.default_days <= 0:
            return {"expire_ts": (NEVER if self.default_days == 0 else DISABLED),
                    "note": ""}
        first = self.seen.get(gid)
        if not isinstance(first, (int, float)) or first <= 0:
            first = _now()
            self.seen[gid] = first
            self._save()
        return {"expire_ts": int(first) + self.default_days * 86400,
                "note": f"默认 {self.default_days} 天"}

    def is_authorized(self, group_id) -> bool:
        if not self.enable:
            return True
        r = self.record(group_id)
        if r is None:
            return True
        ts = int(r.get("expire_ts", NEVER))
        if ts == NEVER:
            return True
        if ts < 0:
            return False
        return _now() < ts

    def remaining_sec(self, group_id) -> int:
        """剩余秒数：0=永久，-1=已停用，>0=剩余秒（可能为负=已过期）"""
        r = self.record(group_id)
        if r is None:
            return 0
        ts = int(r.get("expire_ts", NEVER))
        if ts == NEVER:
            return 0
        if ts < 0:
            return -1
        return ts - _now()

    def set_group(self, group_id, spec, note: str = ""):
        """
        设置一个群的授权。`spec` 可以是：
            `永久` / `never` / `0`      -> 永久
            `停用` / `off` / `-1`       -> 停用
            `30d` / `30天` / `12h` …    -> 从现在起算的时长
            纯数字时间戳（> 10 位）      -> 直接当到期时间
        返回 (ok, msg)
        """
        gid = str(group_id or "").strip()
        if not gid.isdigit():
            return False, "群号不合法"
        s = str(spec or "").strip()
        if not s:
            return False, "缺少时长（如 30d / 12h / 永久）"
        if re.match(r"^\d{10,}$", s):
            ts = int(s)
        else:
            sec = parse_duration(s)
            if sec is None:
                return False, f"看不懂的时长：{s}"
            if sec == NEVER:
                ts = NEVER
            elif sec == DISABLED:
                ts = DISABLED
            else:
                ts = _now() + sec
        self.groups[gid] = {"expire_ts": int(ts), "note": str(note or "")}
        self.seen.setdefault(gid, _now())
        self._save()
        logger.info(f"[auth] 群 {gid} 授权已设为 {fmt_ts(ts)}")
        return True, fmt_ts(ts)

    def remove_group(self, group_id):
        """删掉记录 -> 回到「按 default_days 判定」"""
        gid = str(group_id or "").strip()
        if gid in self.groups:
            self.groups.pop(gid, None)
            self._save()
            logger.info(f"[auth] 群 {gid} 授权记录已删除")
            return True
        return False

    def cleanup_expired(self, keep_days: int = 30):
        """
        清理**早已过期很久**的记录（默认保留 30 天，方便回溯与提醒）。

        ★★ 关键：**不能一到期就删** —— 删了之后该群会回落到
          「未设置的群」规则（`default_days=0` 时 = 永久授权），
          等于**越权放行**，这是安全漏洞。
          所以只删「过期超过 keep_days 天」的，过期判定本身照常生效。

        ★ 不会动：永久（0）、停用（-1）、未过期的。
        返回被移除的数量。
        """
        if keep_days <= 0:
            return 0
        cutoff = _now() - keep_days * 86400
        dead = [k for k, v in self.groups.items()
                if 0 < int(v.get("expire_ts", NEVER)) < cutoff]
        for k in dead:
            self.groups.pop(k, None)
        if dead:
            self._save()
            logger.info(f"[auth] 已清理 {len(dead)} 条过期很久的授权记录")
        return len(dead)

    def deny_text(self, group_id) -> str:
        """
        ★★ 1.3.0：**未授权时**给用户的提示。

        以前是静默（什么都不回），用户完全不知道为什么没反应。
        现在所有指令触发时都回这段，明确告诉他们要授权才能用。
        """
        gid = str(group_id or "").strip()
        cmd = (getattr(self, "cmd_names", None) or ["授权"])[0]
        st_cmd = (getattr(self, "status_names", None) or ["授权状态"])[0]
        left = self.remaining_sec(gid) if gid else -1
        why = "已过期" if (gid and left != -1) else "未授权"
        lines = [
            "🔒 本群未授权",
            "──────────",
        ]
        if gid:
            lines.append(f"　 群号：{gid}")
        lines.append(f"　 状态：⛔ **{why}** —— 插件对本群不可用")
        lines.append("──────────")
        lines.append(f"💡 授权本群：「{cmd} 永久」或「{cmd} 30d」")
        lines.append(f"💡 指定群：「{cmd} 群号 30d」")
        lines.append(f"💡 查状态：「{st_cmd}」")
        return "\n".join(lines)

    def warn_text(self, group_id) -> str:
        """
        到期前提醒（同一天只提醒一次）。返回提示文本，无需提醒返回空串。

        ★ 注意：**未授权/已过期**的提示不在这里 —— 那走 `deny_text()`。
          这里只管「还有几天到期」的续期提醒。
        """
        if not self.enable:
            return ""
        left = self.remaining_sec(group_id)
        if left == 0 or left == -1:
            return ""
        if left <= 0:
            return ""
        if left > self.warn_days * 86400:
            return ""
        gid = str(group_id or "")
        today = time.strftime("%Y-%m-%d")
        if self._log_cache.get(gid) == today:
            return ""
        self._log_cache[gid] = today
        days = max(1, int(left // 86400) + (1 if left % 86400 else 0))
        cmd = (getattr(self, "cmd_names", None) or ["授权"])[0]
        return (f"⏰ 本群授权还有 {days} 天到期\n"
                f"💡 续期：「{cmd} 30d」或「{cmd} 永久」")

    def match_cmd(self, text: str) -> str:
        """
        是不是授权指令？是就返回指令名，否则空串。

        ★★ **必须严格匹配**（指令名 或 指令名+空白+参数）。
          不能用 `cmd_arg()` —— 它「空格可有可无」，
          会把菜单名「授权功能」和「授权列表」一起吃进来。
        """
        s = str(text or "").strip()
        if not s:
            return ""
        for nm in (getattr(self, "cmd_names", None) or ["授权"]):
            nm = str(nm or "").strip()
            if not nm:
                continue
            if s == nm:
                return nm
            for _p in (nm + " ", nm + "\u3000", "/" + nm + " ", "/" + nm):
                if s.startswith(_p):
                    return nm
            if s == "/" + nm:
                return nm
        return ""

    def cmd_allowed(self, group_id) -> bool:
        """
        ★★ 1.3.0：**已废弃** —— 现在只按「AstrBot 全局管理员」判定，
          不再看群白名单。永远返回 True（老调用点不会因此失效）。
        """
        return True

    def match_status_cmd(self, text: str) -> str:
        """
        是不是「授权状态」指令？是就返回指令名，否则空串。

        ★★ 严格匹配（同 `match_cmd`）：入口名 或 入口名+空白+群号。
        """
        s = str(text or "").strip()
        if not s:
            return ""
        for nm in (getattr(self, "status_names", None) or ["授权状态"]):
            nm = str(nm or "").strip()
            if not nm:
                continue
            if s == nm or s == "/" + nm:
                return nm
            for _p in (nm + " ", nm + "\u3000", "/" + nm + " ", "/" + nm):
                if s.startswith(_p):
                    return nm
        return ""

    def status_text(self, group_id) -> str:
        """
        「授权状态」—— **只显示本群是否已授权**。

        未授权时明确显示「本群未授权」，并附授权指令（续期提示保留）。
        """
        gid = str(group_id or "").strip()
        cmd = (getattr(self, "cmd_names", None) or ["授权"])[0]
        st_cmd = (getattr(self, "status_names", None) or ["授权状态"])[0]
        if not gid:
            return ("⚠️ 拿不到群号（私聊里请带上群号）\n"
                    f"💡 用法：「{st_cmd} 群号」")
        lines = ["🔑 授权状态", "──────────", f"　 群号：{gid}"]
        if not self.enable:
            lines.append("　 状态：♻️ **授权机制已停用**（所有群都能用）")
            return "\n".join(lines)

        left = self.remaining_sec(gid)
        if left == 0:
            lines.append("　 状态：✅ **本群已授权**（永久）")
        elif left > 0:
            lines.append(f"　 状态：✅ **本群已授权**（剩 {left / 86400:.1f} 天）")
        elif left == -1:
            _rec = self.groups.get(gid)
            if isinstance(_rec, dict) and int(_rec.get("expire_ts", 0)) < 0:
                lines.append("　 状态：🚫 **本群未授权**（已被停用）")
            else:
                lines.append("　 状态：⛔ **本群未授权**（尚未授权）")
        else:
            lines.append("　 状态：⛔ **本群未授权**（已过期）")

        lines.append("──────────")
        if left >= 0:
            lines.append(f"💡 续期：「{cmd} 30d」或「{cmd} 永久」")
        else:
            lines.append("⚠️ 本群尚未授权，插件无法使用")
            lines.append(f"💡 授权本群：「{cmd} 永久」或「{cmd} 30d」")
            lines.append(f"💡 指定群：「{cmd} 群号 30d」")
        return "\n".join(lines)

    @staticmethod
    def _arg_after(text: str, cmd: str) -> str:
        s = str(text or "").strip()
        c = str(cmd or "").strip()
        if not c:
            return s
        if s == c or s == "/" + c:
            return ""
        for _p in (c + " ", c + "\u3000", "/" + c + " ", "/" + c):
            if s.startswith(_p):
                return s[len(_p):].strip(" \u3000")
        return s

    def handle_cmd(self, text: str, group_id: str = "") -> str:
        """
        处理授权指令，返回要发出去的文本。

        ★★ 1.3.0：`group_id` = 当前群（「授权状态」不带群号时用它）。

        支持：
            授权 <群号> <时长>    设置（时长：永久/30d/12h/停用/删除）
            授权 列表             列出所有授权
            授权 状态 [群号]      查状态（不带群号 = 当前群）
            授权 查 [群号]        同上（兼容写法）
            授权 帮助             用法
        """
        if self.match_list_cmd(text):
            return self.list_text()

        _sc = self.match_status_cmd(text)
        if _sc:
            _a = self._arg_after(text, _sc).split()
            _g = _a[0] if _a and _a[0].isdigit() else group_id
            return self.status_text(_g)

        cmd = self.match_cmd(text) or (self.cmd_names or ["授权"])[0]
        arg = self._arg_after(text, cmd)
        if not arg or arg in ("帮助", "help", "?", "？"):
            return self.help_text()

        parts = arg.split()
        head = parts[0]

        if head in ("列表", "list", "全部"):
            return self.list_text()

        if head in ("状态", "查", "查询", "query", "check", "status"):
            gid = parts[1] if len(parts) > 1 and parts[1].isdigit() else group_id
            return self.status_text(gid)

        if head.isdigit():
            gid, spec_parts = head, parts[1:]
        else:
            gid, spec_parts = str(group_id or "").strip(), parts
        if not gid:
            return ("⚠️ 拿不到群号（私聊里请带上群号）\n"
                    f"💡 用法：「{cmd} 群号 30d」")
        spec = spec_parts[0] if spec_parts else ""
        note = " ".join(spec_parts[1:]) if len(spec_parts) > 1 else ""
        if not spec:
            left = self.remaining_sec(gid)
            state = ("永久" if left == 0 else
                     "已停用" if left == -1 else
                     f"剩 {left / 86400:.1f} 天" if left > 0 else "已过期/未授权")
            return (f"ℹ️ 群 {gid} 当前：{state}\n"
                    f"💡 改：「{cmd} {gid} 永久」/「{cmd} {gid} 30d」/"
                    f"「{cmd} {gid} 停用」\n"
                    f"💡 也可以省掉群号：「{cmd} 永久」（= 本群）")
        if spec in ("删除", "删", "del", "delete", "移除"):
            ok = self.remove_group(gid)
            return (f"✅ 已删除群 {gid} 的授权记录"
                    if ok else f"ℹ️ 群 {gid} 本来就没有单独设置")
        ok, res = self.set_group(gid, spec, note)
        if not ok:
            return f"❌ {res}\n💡 {self.help_text()}"
        if res == "永久":
            return f"✅ 群 {gid} 已设为**永久授权**"
        if res == "已停用":
            return f"🚫 群 {gid} 已**停用**（插件对该群静默）"
        return f"✅ 群 {gid} 授权到 **{res}**"

        return f"⚠️ 看不懂：「{arg}」\n{self.help_text()}"

    def help_text(self) -> str:
        cmd = (getattr(self, "cmd_names", None) or ["授权"])[0]
        st_cmd = (getattr(self, "status_names", None) or ["授权状态"])[0]
        ls_cmd = (getattr(self, "list_names", None) or ["授权列表"])[0]
        return "\n".join([
            "🔑 授权功能 · 用法",
            "──────────",
            f"　 {st_cmd}            查**本群**状态",
            f"　 {st_cmd} 群号       查某个群",
            f"　 {ls_cmd}            看**每个群**的状态",
            f"　 {cmd} 永久          给**本群**永久授权",
            f"　 {cmd} 30d           给本群 30 天",
            f"　 {cmd} 停用          停用本群",
            f"　 {cmd} 删除          删本群记录",
            f"　 {cmd} 群号 30d      指定群（群号在前）",
            "──────────",
            "💡 到期后插件对该群**完全静默**（连开关都轮不到）",
        ])

    def match_list_cmd(self, text: str) -> str:
        """
        是不是「授权列表」指令？是就返回指令名，否则空串。

        ★★ 这是**独立指令**（不带空格），默认就是 `授权列表`
          —— 和 `授权` 指令完全分开，避免被前缀匹配吃掉。
        """
        s = str(text or "").strip()
        if not s:
            return ""
        for nm in (getattr(self, "list_names", None) or ["授权列表"]):
            nm = str(nm or "").strip()
            if not nm:
                continue
            if s == nm or s == "/" + nm:
                return nm
        return ""

    def list_text(self, limit: int = 60) -> str:
        """
        「授权列表」—— 只输出**机器人加入的每个群**的授权状态
        + 如何授权的指令提示。

        群来源（按优先级合并去重）：
          ① `joined_groups` —— 外部注入的「机器人已加入的群」
          ② `known_groups`  —— 插件见过的群（发过消息的群）
          ③ `self.groups`   —— 已有授权记录的群
        """
        cmd = (getattr(self, "cmd_names", None) or ["授权"])[0]
        st_cmd = (getattr(self, "status_names", None) or ["授权状态"])[0]
        lines = ["🔑 授权列表", "──────────"]

        if not self.enable:
            lines.append("　 ♻️ 授权机制已停用（所有群都能用）")
            return "\n".join(lines)

        gids = []
        for src in (getattr(self, "joined_groups", None),
                    getattr(self, "known_groups", None),
                    self.groups):
            try:
                for g in (src or []):
                    g = str(g).strip()
                    if g.isdigit() and g not in gids:
                        gids.append(g)
            except Exception:
                pass
        if not gids:
            lines.append("（还不知道机器人加入了哪些群）")
            lines.append("")
            lines.append("💡 群里发一条消息后，这个群就会出现在这里")
            return "\n".join(lines)

        for gid in gids[:limit]:
            left = self.remaining_sec(gid)
            if left == 0:
                lines.append(f"✅ {gid}　已授权（永久）")
            elif left > 0:
                lines.append(f"✅ {gid}　已授权（剩 {left / 86400:.1f} 天）")
            else:
                lines.append(f"⛔ {gid}　未授权")
        if len(gids) > limit:
            lines.append(f"…… 还有 {len(gids) - limit} 个群")

        lines.append("──────────")
        lines.append(f"💡 授权「{cmd} 永久」或「{cmd} 30d」（= 本群）")
        lines.append(f"💡 指定群：「{cmd} 群号 30d」")
        lines.append(f"💡 查本群：「{st_cmd}」")
        return "\n".join(lines)

    def status_lines(self, limit: int = 40) -> list:
        """后台/菜单用的授权概览"""
        if not self.groups:
            base = ("当前没有任何「单独设置」的群 —— "
                    f"未设置的群：{self.default_rule_text()}")
            return [base]
        out = []
        now = _now()
        items = sorted(self.groups.items(),
                       key=lambda kv: int(kv[1].get("expire_ts", NEVER)))
        for gid, r in items[:limit]:
            ts = int(r.get("expire_ts", NEVER))
            if ts == NEVER:
                mark = "♾️"
            elif ts < 0:
                mark = "🚫"
            elif ts <= now:
                mark = "⛔"
            elif ts - now <= self.warn_days * 86400:
                mark = "⏰"
            else:
                mark = "✅"
            note = f"  {r.get('note')}" if r.get("note") else ""
            out.append(f"{mark} {gid}  {fmt_ts(ts)}{note}")
        if len(self.groups) > limit:
            out.append(f"… 还有 {len(self.groups) - limit} 个群")
        return out

    def stats(self) -> dict:
        now = _now()
        s = {"total": len(self.groups), "never": 0,
             "active": 0, "expired": 0, "disabled": 0}
        for r in self.groups.values():
            ts = int(r.get("expire_ts", NEVER))
            if ts == NEVER:
                s["never"] += 1
            elif ts < 0:
                s["disabled"] += 1
            elif ts <= now:
                s["expired"] += 1
            else:
                s["active"] += 1
        return s
