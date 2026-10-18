"""
统一数据存储（1.7.2）

背景：1.7.2 之前，所有「数据类」内容都放在后台配置（_conf_schema.json 的
template_list）里，导致后台配置既长又难改。现在统一搬到本模块管理的独立
JSON 文件，后台配置只保留开关与指令名，内容改由独立 HTML 配置页面维护。

数据文件：data/plugin_data/<插件>/<插件>_data.json

{
  "version": 2,
  "collections": {
    "api_list":             [ {...}, ... ],   # 接口规则（含 image/video/text）
    "fortune_list":         [ ... ],
    "word_reply_list":      [ ... ],
    "mount_list":           [ ... ],
    "job_list":             [ ... ],
    "greeting_morning_messages": [ ... ],
    "greeting_night_messages":   [ ... ],
    "pet_list":             [ ... ],
    "pet_work_events":      [ ... ],
    "pet_adventure_events": [ ... ],
    "pet_item_list":        [ ... ]
  }
}

对外接口：
    store = DataStore(path)
    store.get("pet_list")                 -> list
    store.set("pet_list", rows)           -> 覆盖写入
    store.append("pet_list", row)
    store.remove_at("pet_list", idx)
    store.update_at("pet_list", idx, row)
    store.migrate_from_config(cfg_dict)   -> 把旧后台配置里的数据搬进来（只做一次）
    store.summary(key, fields)            -> 生成只读文本摘要（给后台看）
"""
import json
import os
import threading

from astrbot.api import logger
_LOCK = threading.RLock()

COLLECTIONS = [
    "api_list",
    "fortune_list",
    "word_reply_list",
    "mount_list",
    "job_list",
    "greeting_morning_messages",
    "greeting_night_messages",
    "pet_list",
    "pet_work_events",
    "pet_adventure_events",
    "pet_item_list",
    "menu_texts",
    "shop_list",
    "poke_reply_list",
    "race_car_list",
    "race_lottery_pool",
    "race_npc_cars",
    # ★★ 1.6.0：农场物品总表（种子 / 肥料 / 家畜 / 饲料 / 农机）
    "farm_item_list",
]

SUMMARY_FIELDS = {
    "api_list": ("keywords", "media_type"),
    "fortune_list": ("fortune", "title"),
    "word_reply_list": ("keywords", "reply"),
    "mount_list": ("name", "price"),
    "job_list": ("name", "reward_min"),
    "greeting_morning_messages": ("message",),
    "greeting_night_messages": ("message",),
    "pet_list": ("name", "price", "elem"),
    "pet_work_events": ("event", "exp_min"),
    "pet_adventure_events": ("event", "exp_min"),
    "pet_item_list": ("name", "price"),
    "shop_list": ("name", "price", "kind"),
    "poke_reply_list": ("word",),
    "race_car_list": ("name", "price", "kind"),
    "race_lottery_pool": ("kind", "name", "weight"),
    "race_npc_cars": ("name", "speed", "accel", "handling"),
    # ★★ 1.6.0：农场物品总表
    "farm_item_list": ("kind", "name", "price", "level_req"),
}

TEMPLATE_KEYS = {
    "api_list": "meme_rule",
    "fortune_list": "fortune_item",
    "word_reply_list": "word_item",
    "mount_list": "mount_item",
    "job_list": "job_item",
    "greeting_morning_messages": "greeting_morning_item",
    "greeting_night_messages": "greeting_night_item",
    "pet_list": "pet_item",
    "pet_work_events": "pet_event",
    "pet_adventure_events": "pet_adventure_event",
    "pet_item_list": "pet_item_goods",
    "poke_reply_list": "poke_word_item",
}

DATA_VERSION = 2

class DataStore:
    """统一数据存储（线程安全，原子写盘，支持检测文件变更自动重载）"""

    def __init__(self, path: str):
        self.path = path or ""
        self.version = DATA_VERSION
        self.collections = {k: [] for k in COLLECTIONS}
        self.pet_work_events_ver = 0
        self._stamp = None
        self.load()

    def _file_stamp(self):
        """文件指纹；文件不存在返回 None"""
        if not self.path:
            return None
        try:
            st = os.stat(self.path)
            return (st.st_mtime_ns, st.st_size)
        except Exception:
            return None

    def reload_if_changed(self, force: bool = False) -> bool:
        """
        文件被外部改动过就重新读进来（**不覆盖**没变化的情况）。

        返回是否真的重载了。

        ★ 场景：插件常驻内存后，若有人直接编辑数据文件、或在另一个进程里
          写数据，内存副本会一直是旧的（表现为「后台加了/删了小鸟，图鉴数量不变」）。
          在读之前调一次本方法即可保持最新，代价只有一次 os.stat。

        force=True 时**无条件**重读（图鉴这类「必须实时」的入口用，
        彻底摆脱对时间戳判断的依赖；文件只有几百 KB，代价可忽略）。
        """
        cur = self._file_stamp()
        if cur is None:
            return False
        if force:
            self.load()
            self._stamp = self._file_stamp()
            return True
        if not force and self._stamp is not None and cur == self._stamp:
            return False
        if not force and self._stamp is None:
            self._stamp = cur
            return False
        self.load()
        self._stamp = self._file_stamp()
        logger.info(
            "[data_store] 检测到数据文件变化，已重新加载"
            f"{len(self.collections.get('api_list') or [])} 条接口）"
        )
        return True

    def load(self):
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8-sig") as f:
                data = json.load(f) or {}
            cols = data.get("collections", {})
            if not isinstance(cols, dict):
                cols = {}
            for k in COLLECTIONS:
                v = cols.get(k)
                self.collections[k] = list(v) if isinstance(v, list) else []
            for k in COLLECTIONS:
                if not self.collections[k] and isinstance(data.get(k), list):
                    self.collections[k] = list(data[k])
            try:
                self.version = int(data.get("version", DATA_VERSION) or DATA_VERSION)
            except Exception:
                self.version = DATA_VERSION
            try:
                self.pet_work_events_ver = int(
                    data.get("pet_work_events_ver", 0) or 0
                )
            except Exception:
                self.pet_work_events_ver = 0
            self._stamp = self._file_stamp()
        except Exception:
            logger.exception("[data_store] 读取失败")

    def save(self):
        if not self.path:
            return
        with _LOCK:
            try:
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "version": DATA_VERSION,
                            "pet_work_events_ver": int(
                                getattr(self, "pet_work_events_ver", 0) or 0
                            ),
                            "collections": self.collections,
                        },
                        f, ensure_ascii=False, indent=2,
                    )
                os.replace(tmp, self.path)
            except Exception:
                logger.exception("[data_store] 保存失败")

    def get(self, key: str, autoreload=False) -> list:
        """
        读一个集合。

        autoreload:
          · False / 0 -> 直接用内存副本
          · True / 1  -> 先看文件是否被外部改过（比对 mtime+大小），变了才重读
          · 2 / "force" -> **无条件重读**（图鉴这类必须实时的入口用）
        """
        if autoreload:
            try:
                _force = (autoreload == 2
                          or str(autoreload).strip().lower() == "force")
                self.reload_if_changed(force=_force)
            except Exception:
                logger.debug("[data_store] 自动重载失败", exc_info=True)
        with _LOCK:
            v = self.collections.get(key)
            return list(v) if isinstance(v, list) else []

    def get_ref(self, key: str) -> list:
        """取引用（调用方自己保证加锁与保存）"""
        with _LOCK:
            if not isinstance(self.collections.get(key), list):
                self.collections[key] = []
            return self.collections[key]

    def set(self, key: str, rows) -> bool:
        if key not in COLLECTIONS:
            logger.warning(f"[data_store] 未知集合：{key}")
            return False
        with _LOCK:
            self.collections[key] = list(rows or [])
        self.save()
        return True

    def append(self, key: str, row: dict) -> bool:
        if key not in COLLECTIONS:
            return False
        with _LOCK:
            self.collections.setdefault(key, [])
            if not isinstance(self.collections[key], list):
                self.collections[key] = []
            self.collections[key].append(dict(row or {}))
        self.save()
        return True

    def update_at(self, key: str, idx: int, row: dict) -> bool:
        if key not in COLLECTIONS:
            return False
        with _LOCK:
            arr = self.collections.get(key)
            if not isinstance(arr, list) or not (0 <= idx < len(arr)):
                return False
            merged = dict(arr[idx]) if isinstance(arr[idx], dict) else {}
            merged.update(dict(row or {}))
            arr[idx] = merged
        self.save()
        return True

    def remove_at(self, key: str, idx: int) -> bool:
        if key not in COLLECTIONS:
            return False
        with _LOCK:
            arr = self.collections.get(key)
            if not isinstance(arr, list) or not (0 <= idx < len(arr)):
                return False
            arr.pop(idx)
        self.save()
        return True

    def count(self, key: str) -> int:
        return len(self.get(key))

    def migrate_from_config(self, cfg: dict) -> dict:
        """
        把旧后台配置里的 template_list 数据搬进独立文件。
        返回 {"moved": {key: 条数}, "skipped": [key...]}，供日志与页面提示。

        规则：
          · 独立文件里已经有数据的集合 -> 跳过（不覆盖，以文件为准）
          · 后台配置里有数据、文件里没有 -> 迁移
          · 迁移后把后台配置里对应的键清空（写回由调用方负责）
        """
        cfg = cfg if isinstance(cfg, dict) else {}
        moved, skipped = {}, []
        with _LOCK:
            for key in COLLECTIONS:
                rows = cfg.get(key)
                if not isinstance(rows, list) or not rows:
                    continue
                if self.collections.get(key):
                    skipped.append(key)
                    continue
                clean = []
                for it in rows:
                    if not isinstance(it, dict):
                        continue
                    row = {k: v for k, v in it.items() if k != "__template_key"}
                    clean.append(row)
                if clean:
                    self.collections[key] = clean
                    moved[key] = len(clean)
        if moved:
            self.save()
            detail = "、".join(f"{k}({v}条)" for k, v in moved.items())
            logger.info(f"[data_store] 已从后台配置迁移 {len(moved)} 组数据：{detail}")
        if skipped:
            logger.info(f"[data_store] 以下集合已有独立数据，跳过迁移：{skipped}")
        return {"moved": moved, "skipped": skipped}

    def clear_keys_in_config(self, cfg: dict) -> dict:
        """把后台配置里已迁移的集合键清空（返回修改后的 cfg）"""
        cfg = dict(cfg or {}) if isinstance(cfg, dict) else {}
        for key in COLLECTIONS:
            if key in cfg and isinstance(cfg.get(key), list):
                cfg[key] = []
        return cfg

    def summary(self, key: str, limit: int = 8) -> str:
        rows = self.get(key)
        fields = SUMMARY_FIELDS.get(key, ())
        n = len(rows)
        if n == 0:
            return "（暂无数据，请在独立配置页面里添加）"
        head = f"共 {n} 条（在独立配置页面里增删改）"
        lines = [head, ""]
        for i, r in enumerate(rows[:limit], start=1):
            if not isinstance(r, dict):
                continue
            parts = []
            for f in fields:
                v = r.get(f)
                if v not in (None, ""):
                    s = str(v).replace("\n", " ")
                    if len(s) > 22:
                        s = s[:22] + "…"
                    parts.append(s)
            lines.append(f"{i}. " + " — ".join(parts) if parts else f"{i}. （空条目）")
        if n > limit:
            lines.append(f"… 其余 {n - limit} 条请到独立配置页查看")
        return "\n".join(lines)

    def all_summaries(self, limit: int = 5) -> dict:
        return {k: self.summary(k, limit) for k in COLLECTIONS}
