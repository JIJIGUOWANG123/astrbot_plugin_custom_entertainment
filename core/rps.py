"""
猜拳功能（1.7.5）

玩法：
    · 「猜拳 石头」          -> 随机挑一个群友对局
    · 「猜拳 石头 @某人」     -> 和指定的人对局
    · 赢家拿走随机积分，输家被扣同样的分（给对方）
    · 积分范围 1~上限（默认 100，后台可改任意数字）
    · 积分**可以为负**（签到系统支持负数，这里不截断到 0）

数据独立存放：`..._rps.json`
    {
      "version": 1,
      "groups": {
        "<群号>": {
          "<QQ>": {"win": 0, "lose": 0, "draw": 0, "score": 0,
                   "date": "2026-10-04", "today": 3}
        }
      }
    }

设计说明：
    · 记录只用于统计（战绩 / 当日次数），**不存积分**——
      积分真值始终在签到模块里，避免两边打架。
    · 所有文件操作都吞异常，绝不因为猜拳把主流程搞崩。
"""
import json
import os
import random
import time

try:
    from astrbot.api import logger
except Exception:  # pragma: no cover
    import logging
    logger = logging.getLogger("rps")

# 手势
RPS_CHOICES = ("石头", "剪刀", "布")
# 别名 -> 规范名
RPS_ALIAS = {
    "石头": "石头", "石頭": "石头", "rock": "石头", "r": "石头",
    "拳": "石头", "0": "石头",
    "剪刀": "剪刀", "剪子": "剪刀", "scissors": "剪刀", "s": "剪刀",
    "2": "剪刀",
    "布": "布", "paper": "布", "p": "布", "包袱": "布", "1": "布",
}
# 克制关系：key 赢 value
RPS_BEATS = {"石头": "剪刀", "剪刀": "布", "布": "石头"}
# 中间那句判定语
RPS_RESULT_TEXT = {
    "win": "挑战方获胜",
    "lose": "被挑战方获胜",
    "draw": "平局",
}
# 每个手势的 emoji
RPS_EMOJI = {"石头": "✊", "剪刀": "✌️", "布": "🖐️"}

_MAX_HISTORY = 20


def normalize_choice(raw: str) -> str:
    """把用户输入的手势规范成「石头/剪刀/布」，不认识返回空串"""
    s = str(raw or "").strip().lower()
    if not s:
        return ""
    if s in RPS_ALIAS:
        return RPS_ALIAS[s]
    # 去掉尾部标点再试一次
    s2 = s.strip("。.！!~～，,、 ")
    return RPS_ALIAS.get(s2, "")


def parse_rps_args(text: str, trigger_names=None):
    """
    从「猜拳 石头 @某人」里解析出 (手势, 目标QQ列表)。

    返回 (choice, targets)；choice 为空表示没给手势。
    """
    names = list(trigger_names or ["猜拳"])
    s = str(text or "").strip()
    # 去掉前导 /
    if s.startswith("/"):
        s = s[1:].strip()
    # 去掉指令名
    for n in sorted(names, key=len, reverse=True):
        n = str(n or "").strip()
        if n and s.startswith(n):
            s = s[len(n):].strip()
            break
    if not s:
        return "", []
    choice = ""
    rest = []
    for tok in s.split():
        if not choice:
            c = normalize_choice(tok)
            if c:
                choice = c
                continue
        rest.append(tok)
    return choice, rest


class RpsModule:
    """猜拳数据层（战绩统计 + 当日次数）"""

    def __init__(self, data_path: str):
        self.data_path = data_path
        self.groups = {}
        # 配置（由 main.py 注入）
        self.enable = True
        # ★ 积分显示名称（由 main.py 注入）
        self.score_name = "积分"
        self.trigger_names = ["猜拳"]
        self.bet_max = 100          # 单局积分上限
        self.daily_limit = 0        # 每人每天次数上限（0 = 不限）
        self.allow_negative = True  # 允许负积分
        self._load()

    # ------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------
    def _load(self):
        try:
            if not os.path.isfile(self.data_path):
                return
            with open(self.data_path, "r", encoding="utf-8-sig") as f:
                d = json.load(f)
            g = d.get("groups")
            if isinstance(g, dict):
                self.groups = g
        except Exception:
            logger.exception("[rps] 读取猜拳数据失败")

    def _save(self):
        try:
            d = os.path.dirname(self.data_path)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            tmp = self.data_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "groups": self.groups}, f,
                          ensure_ascii=False, indent=1)
            os.replace(tmp, self.data_path)
        except Exception:
            logger.exception("[rps] 保存猜拳数据失败")

    # ------------------------------------------------------------
    # 记录访问
    # ------------------------------------------------------------
    @staticmethod
    def _gid(group_id) -> str:
        return str(group_id or "").strip()

    def _bucket(self, group_id) -> dict:
        gid = self._gid(group_id)
        b = self.groups.get(gid)
        if not isinstance(b, dict):
            b = {}
            self.groups[gid] = b
        return b

    def get_user(self, group_id, uid) -> dict:
        """取某人的猜拳记录（不存在返回空 dict，不落盘）"""
        b = self._bucket(group_id)
        u = b.get(str(uid))
        return u if isinstance(u, dict) else {}

    def ensure_user(self, group_id, uid) -> dict:
        b = self._bucket(group_id)
        uid = str(uid)
        u = b.get(uid)
        if not isinstance(u, dict):
            u = {"win": 0, "lose": 0, "draw": 0, "score": 0,
                 "date": "", "today": 0, "history": []}
            b[uid] = u
        return u

    def today_count(self, group_id, uid) -> int:
        """今天已经猜了几次（跨天自动归零）"""
        u = self.get_user(group_id, uid)
        if not u:
            return 0
        today = time.strftime("%Y-%m-%d")
        if str(u.get("date") or "") != today:
            return 0
        try:
            return int(u.get("today", 0))
        except Exception:
            return 0

    def record(self, group_id, uid, result: str, delta: int = 0,
               detail: str = ""):
        """
        记一局。

        result: "win" / "lose" / "draw"
        delta : 本局积分变化（正负都行）
        """
        u = self.ensure_user(group_id, uid)
        today = time.strftime("%Y-%m-%d")
        if str(u.get("date") or "") != today:
            u["date"] = today
            u["today"] = 0
        u["today"] = int(u.get("today", 0)) + 1
        if result in ("win", "lose", "draw"):
            u[result] = int(u.get(result, 0)) + 1
        try:
            u["score"] = int(u.get("score", 0)) + int(delta)
        except Exception:
            pass
        h = u.get("history")
        if not isinstance(h, list):
            h = []
        if detail:
            h.append({"t": time.strftime("%m-%d %H:%M"), "d": detail,
                      "r": result, "v": int(delta)})
            if len(h) > _MAX_HISTORY:
                h = h[-_MAX_HISTORY:]
        u["history"] = h
        self._save()
        return u

    # ------------------------------------------------------------
    # 对局
    # ------------------------------------------------------------
    def pick_opponent(self, group_id, exclude=(), candidates=None):
        """
        随机挑一个对手。

        candidates: 外部给的候选 QQ 列表（一般来自签到群成员表）；
                    为空则从猜拳记录里挑。
        """
        ex = {str(x) for x in (exclude or [])}
        pool = []
        for x in (candidates or []):
            x = str(x or "").strip()
            if x and x not in ex:
                pool.append(x)
        if not pool:
            for x in self._bucket(group_id).keys():
                if x and x not in ex:
                    pool.append(x)
        if not pool:
            return ""
        return random.choice(pool)

    def judge(self, a_choice: str, b_choice: str) -> str:
        """判定：返回 "win"(挑战方赢) / "lose"(被挑战方赢) / "draw" """
        a = normalize_choice(a_choice)
        b = normalize_choice(b_choice)
        if not a or not b:
            return "draw"
        if a == b:
            return "draw"
        return "win" if RPS_BEATS.get(a) == b else "lose"

    def random_bet(self, lo: int = 1, hi: int = None) -> int:
        """随机积分（含两端）"""
        try:
            lo = max(1, int(lo))
        except Exception:
            lo = 1
        try:
            hi = int(hi) if hi else int(self.bet_max or 100)
        except Exception:
            hi = 100
        if hi < lo:
            hi = lo
        return random.randint(lo, hi)

    def stats_text(self, group_id, uid, nick: str = "") -> str:
        """个人猜拳战绩"""
        u = self.get_user(group_id, uid)
        w = int(u.get("win", 0))
        l = int(u.get("lose", 0))
        d = int(u.get("draw", 0))
        total = w + l + d
        rate = f"{w / total * 100:.0f}%" if total else "—"
        return (
            f"✊ 猜拳战绩：{nick or uid}\n"
            f"🎮 总局数：{total}\n"
            f"🏆 胜 {w}　💀 负 {l}　🤝 平 {d}\n"
            f"📊 胜率：{rate}\n"
            f"📅 今日：{self.today_count(group_id, uid)} 局"
        )
