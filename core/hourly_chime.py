import os
import json
import time
import datetime
import threading
from astrbot.api import logger

from .path_utils import parse_gid_list as _parse_gid_list

_GLOBAL_CHIME_LOCK = threading.Lock()

def _now_hour() -> int:
    return datetime.datetime.now().hour

def _today_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d")

def _fmt_time(ts=None) -> str:
    if ts is None:
        ts = time.time()
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "（未知）"

def _bool_cfg(cfg: dict, key: str, default: bool = True) -> bool:
    """
    读布尔配置。

    ★ 必须自己判字符串：yaml/后台可能给 "false"/"0"/"否"，
      直接 bool("false") 会得到 True（历史踩过的坑）。
    """
    v = (cfg or {}).get(key, default)
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in ("false", "0", "no", "off", "否", "关闭", "关", ""):
        return False
    if s in ("true", "1", "yes", "on", "是", "开启", "开"):
        return True
    return default

class HourlyChimeModule:
    """
    整点报时模块：
      - 按群开启/关闭
      - 默认整点报时（0-23 全部整点），也可只报指定整点
      - 支持自定义报时文案（{hour} 占位符）
      - 后台每 N 秒轮询一次，命中整点则推送
      - 每个群保存一份 unified_msg_origin，用于主动推送
      - 支持手动触发测试推送（不写 last_chime，不影响正常整点判重）
    """

    def __init__(self, plugin_dir: str, data_path: str):
        self.plugin_dir = plugin_dir
        self.data_path = data_path
        self.enable = True
        self.interval = 30
        self.default_template = "🕐 现在是 {hour} 点整"
        self.yiyan_enable = False
        self.yiyan_api = "https://api.yviii.com/yiyan/yi.php"
        self.yiyan_prefix = "💬 "
        self.yiyan_timeout = 8
        self.card_max_w = 720
        self.groups = {}
        self.last_chime = {}
        self.umos = {}
        self.enabled_groups = []
        self.use_backend_groups = False
        self._cfg_write = None
        self._load_state()

    def reload(self, cfg: dict):
        self.enable = bool(cfg.get("hourly_chime_enable", True))
        try:
            itv = int(str(cfg.get("hourly_chime_interval", "30")).strip() or "30")
        except Exception:
            itv = 30
        if itv < 5:
            itv = 5
        self.interval = itv
        self.default_template = str(
            cfg.get("hourly_chime_template", "🕐 现在是 {hour} 点整")
            or "🕐 现在是 {hour} 点整"
        ).strip()

        self.yiyan_enable = _bool_cfg(cfg, "hourly_chime_yiyan_enable", False)
        self.yiyan_api = str(
            cfg.get("hourly_chime_yiyan_api",
                    "https://api.yviii.com/yiyan/yi.php")
            or "https://api.yviii.com/yiyan/yi.php").strip()
        self.yiyan_prefix = str(
            cfg.get("hourly_chime_yiyan_prefix", "") or "")
        try:
            self.yiyan_timeout = max(
                3, min(30, int(str(cfg.get("hourly_chime_yiyan_timeout",
                                           "8")).strip() or "8")))
        except Exception:
            self.yiyan_timeout = 8

        try:
            self.card_max_w = max(
                360, min(900, int(str(cfg.get("hourly_chime_card_max_w",
                                              "720")).strip() or "720")))
        except Exception:
            self.card_max_w = 720

        self.enabled_groups = _parse_gid_list(cfg.get("hourly_chime_groups", ""))
        self.use_backend_groups = bool(self.enabled_groups)
        self._sync_backend_groups()

    def _sync_backend_groups(self):
        """白名单语义：只有后台填写的群才报时，其余群一律关闭。"""
        with _GLOBAL_CHIME_LOCK:
            self._load_state()
            changed = False
            for gid in self.enabled_groups:
                cfg = self.groups.get(gid)
                if not isinstance(cfg, dict):
                    cfg = {"on": False, "template": "", "hours": []}
                if not cfg.get("on"):
                    cfg["on"] = True
                    changed = True
                self.groups[gid] = cfg
            for gid, cfg in list(self.groups.items()):
                if gid in self.enabled_groups:
                    continue
                if isinstance(cfg, dict) and cfg.get("on"):
                    cfg["on"] = False
                    changed = True
            if changed:
                self._save_state()
            logger.info(
                f"[hourly_chime] 后台报时名单：{len(self.enabled_groups)} 个群"
            )

    def _load_state(self):
        if not os.path.exists(self.data_path):
            return
        try:
            with open(self.data_path, "r", encoding="utf-8-sig") as f:
                data = json.load(f) or {}
            self.groups = data.get("groups", {}) or {}
            self.last_chime = data.get("last_chime", {}) or {}
            self.umos = data.get("umos", {}) or {}
        except Exception:
            logger.exception("[hourly_chime] 读取状态失败")

    def _save_state(self):
        try:
            tmp = self.data_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "groups": self.groups,
                        "last_chime": self.last_chime,
                        "umos": self.umos,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(tmp, self.data_path)
        except Exception:
            logger.exception("[hourly_chime] 保存状态失败")

    def record_umo(self, group_id, umo: str):
        if not group_id or not umo:
            return
        gid = str(group_id)
        if self.umos.get(gid) == umo:
            return
            
        with _GLOBAL_CHIME_LOCK:
            self._load_state()
            if self.umos.get(gid) != umo:
                self.umos[gid] = umo
                self._save_state()

    def get_umo(self, group_id) -> str:
        return self.umos.get(str(group_id), "")

    def is_group_on(self, group_id) -> bool:
        """
        该群是否报时（白名单语义：后台填了才报时）。
        """
        gid = str(group_id)
        if gid not in self.enabled_groups:
            return False
        cfg = self.groups.get(gid)
        if not isinstance(cfg, dict):
            return True
        return bool(cfg.get("on", True))

    def set_group_on(self, group_id, on: bool):
        with _GLOBAL_CHIME_LOCK:
            self._load_state()
            gid = str(group_id)
            cfg = self.groups.get(gid)
            if not isinstance(cfg, dict):
                cfg = {"on": False, "template": "", "hours": []}
            cfg["on"] = bool(on)
            self.groups[gid] = cfg
            if on and gid not in self.enabled_groups:
                self.enabled_groups.append(gid)
            elif not on and gid in self.enabled_groups:
                self.enabled_groups.remove(gid)
            self._save_state()
        try:
            if self._cfg_write:
                self._cfg_write(gid, bool(on))
        except Exception:
            logger.exception("[hourly_chime] 同步后台群号失败")

    def get_group_hours(self, group_id) -> list:
        gid = str(group_id)
        cfg = self.groups.get(gid)
        if not isinstance(cfg, dict):
            return []
        hours = cfg.get("hours", [])
        if not isinstance(hours, list):
            return []
        out = []
        for h in hours:
            try:
                n = int(h)
                if 0 <= n <= 23 and n not in out:
                    out.append(n)
            except Exception:
                continue
        return sorted(out)

    def set_group_hours(self, group_id, hours: list):
        with _GLOBAL_CHIME_LOCK:
            self._load_state()
            gid = str(group_id)
            cfg = self.groups.get(gid)
            if not isinstance(cfg, dict):
                cfg = {"on": False, "template": "", "hours": []}
            cleaned = []
            for h in hours:
                try:
                    n = int(h)
                    if 0 <= n <= 23 and n not in cleaned:
                        cleaned.append(n)
                except Exception:
                    continue
            cfg["hours"] = sorted(cleaned)
            self.groups[gid] = cfg
            self._save_state()

    def get_group_template(self, group_id) -> str:
        gid = str(group_id)
        cfg = self.groups.get(gid)
        if not isinstance(cfg, dict):
            return ""
        return str(cfg.get("template", "")).strip()

    def set_group_template(self, group_id, template: str):
        with _GLOBAL_CHIME_LOCK:
            self._load_state()
            gid = str(group_id)
            cfg = self.groups.get(gid)
            if not isinstance(cfg, dict):
                cfg = {"on": False, "template": "", "hours": []}
            cfg["template"] = str(template or "").strip()
            self.groups[gid] = cfg
            self._save_state()

    def list_groups(self) -> list:
        out = []
        for gid, cfg in self.groups.items():
            if not isinstance(cfg, dict):
                continue
            if not cfg.get("on"):
                continue
            out.append({
                "group_id": gid,
                "hours": self.get_group_hours(gid),
                "template": self.get_group_template(gid) or self.default_template,
            })
        return out

    def get_all_group_ids(self) -> list:
        """
        需要遍历的群 ID：后台名单里的群（白名单语义只认这些群）。
        """
        with _GLOBAL_CHIME_LOCK:
            self._load_state()
            return sorted(str(x) for x in self.enabled_groups)

    def render_text(self, template: str, hour: int) -> str:
        if not template:
            template = self.default_template
        try:
            return template.format(hour=hour)
        except Exception:
            return template.replace("{hour}", str(hour))

    def check_and_mark(self, group_id, hour: int) -> bool:
        """
        在全局锁内完成：重新加载磁盘最新状态 -> 检查开关/时间 -> 标记已发送 -> 保存。
        彻底解决多实例并发导致的重复发送问题。
        """
        with _GLOBAL_CHIME_LOCK:
            self._load_state()
            
            gid = str(group_id)
            if not self.is_group_on(gid):
                return False
                
            hours = self.get_group_hours(gid)
            if hours and hour not in hours:
                return False
                
            today = _today_str()
            key = f"{today} {hour:02d}"
            if self.last_chime.get(gid) == key:
                return False
                
            self.last_chime[gid] = key
            if len(self.last_chime) > 500:
                keys_to_remove = [k for k, v in self.last_chime.items() if not v.startswith(today)]
                for k in keys_to_remove:
                    self.last_chime.pop(k, None)
                    
            self._save_state()
            return True

    def build_manual_text(self, group_id, hour: int = None) -> str:
        """构造手动测试推送的文案（不加"测试"前缀，保持和正常报时一致）"""
        if hour is None:
            hour = _now_hour()
        tpl = self.get_group_template(group_id) or self.default_template
        return self.render_text(tpl, hour)

    async def fetch_yiyan(self) -> str:
        """
        取「每日一言」纯文本。

        接口（默认 https://api.yviii.com/yiyan/yi.php）直接返回一整句，
        这里清洗成**单行**；失败返回空串（不阻断报时）。
        """
        if not self.yiyan_enable or not self.yiyan_api:
            return ""
        try:
            import aiohttp
        except Exception:
            logger.debug("[hourly_chime] 没有 aiohttp，跳过每日一言")
            return ""
        try:
            timeout = aiohttp.ClientTimeout(
                total=max(3, int(self.yiyan_timeout or 8)))
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(self.yiyan_api) as resp:
                    if resp.status != 200:
                        logger.warning(
                            f"[hourly_chime] 每日一言接口 HTTP {resp.status}")
                        return ""
                    raw = await resp.read()
            text = raw.decode("utf-8", "ignore")
            text = " ".join(str(text or "").split()).strip()
            text = text.strip(chr(34)).strip(chr(39)).strip().strip()
            return text[:120]
        except Exception as e:
            logger.warning(f"[hourly_chime] 取每日一言失败："
                           f"{type(e).__name__}: {str(e)[:80]}")
            return ""

    async def build_text_async(self, group_id, hour: int = None) -> str:
        """
        最终报时文案：**第一行**报时，**第二行**每日一言。

        ★ 第一行要**不换行**：模板别写太长
          （默认已改成「🕐 现在是 {hour} 点整」）。
        ★ 接口失败时只发第一行，不影响报时。
        ★ 后台把 hourly_chime_yiyan_enable 关掉就只有第一行。
        """
        if hour is None:
            hour = _now_hour()
        first = self.build_manual_text(group_id, hour)
        if not self.yiyan_enable:
            return first
        second = await self.fetch_yiyan()
        if not second:
            return first
        return first + "\n" + str(self.yiyan_prefix or "") + second
