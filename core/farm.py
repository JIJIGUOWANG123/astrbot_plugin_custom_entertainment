"""
农场种菜（★★ 2.1.0 新增 · 娱乐系统子功能）。

设计要点
--------
· **积分来源**：签到系统的钱包 `total_score`（签到 / 打工赚的那个）。
  本模块**只管存档**，加减积分一律由 `main.py` 调签到模块的
  `get_score` / `add_score` 完成 —— 避免两套账。
· **等级**：★★ 2.1.0 第二版改成**农场经验**：
      · 种下时把种子的 `exp` 记在地里，**收获**时结算成农场经验
      · 不同作物 `exp` 不一样（越贵/越慢的经验越多）
      · 升到下一级需要 `exp_base + (级数 - 1) * exp_step` 点经验
  等级 -> 解锁土地数：
      lands = min(max_lands, init_lands + (level - 1))
  （签到系统**没有**等级概念，所以第一版借用「累计签到次数」，
    第二版按用户要求换成种菜收获给经验。）
· **每群每人一份存档**（键 `<群号>:<QQ>`）—— 和签到数据同口径（签到也是按群）。
· **只有一个物品总表**：`data_store` 的 `farm_item_list`，
  靠 `kind` 字段分成三份（和赛车的 `race_car_list` 用 kind 拆视图完全同一套做法）：
      kind = seed    种子商店
      kind = fert    农具商店（肥料：加速成长）
      kind = animal  工具商店（看门的家畜 —— 可买多只，默认上限 5）
      kind = feed    工具商店（家畜饲料 —— 喂了恢复体力）
  后台「数据管理」页直接增删改；表为空时用本文件内置默认值兜底。
· **看门会累**：装备中的家畜按小时掉体力，体力归零 -> 累了**不看守**
  （加速 / 增产 / 自动收获全部失效），喂饲料恢复。
  没装备的家畜**不掉体力**（体力只在看守时消耗）。
· 存档文件：`data/plugin_data/<插件>/<插件>_farm.json`
      {
        "users": {
          "群号:QQ": {
            "lands":    [ null | {"seed","planted","ready","amount"} ],
            "animals":  [ {"name","bought","energy","energy_at"} ],
            "equipped": "中华田园犬",
            "harvested": 12, "earned": 3456, "spent": 1200
          }
        }
      }
"""
import json
import os
import random
import time

from astrbot.api import logger

# ── 物品 kind ────────────────────────────────────────────────────
KIND_SEED = "seed"
KIND_FERT = "fert"
KIND_ANIMAL = "animal"
KIND_FEED = "feed"
# ★★ 2.1.0：农机（自动收割机 / 自动播种机）
KIND_TOOL = "tool"

ALL_KINDS = (KIND_SEED, KIND_FERT, KIND_ANIMAL, KIND_FEED, KIND_TOOL)

# 一个 `kind` 字段可能被填成这些写法，都认（大小写/别名一律归一）
_KIND_ALIAS = {
    "seed": KIND_SEED, "seeds": KIND_SEED, "种子": KIND_SEED,
    "": KIND_SEED,
    "fert": KIND_FERT, "fertilizer": KIND_FERT, "fertiliser": KIND_FERT,
    "肥料": KIND_FERT, "农具": KIND_FERT, "tool": KIND_TOOL,
    "item": KIND_FERT,
    "animal": KIND_ANIMAL, "animals": KIND_ANIMAL, "dog": KIND_ANIMAL,
    "dogs": KIND_ANIMAL, "pet": KIND_ANIMAL, "家畜": KIND_ANIMAL,
    "狗": KIND_ANIMAL, "看门狗": KIND_ANIMAL, "家畜/狗": KIND_ANIMAL,
    "feed": KIND_FEED, "feeds": KIND_FEED, "food": KIND_FEED,
    "饲料": KIND_FEED, "狗粮": KIND_FEED, "食物": KIND_FEED,
    "tools": KIND_TOOL, "machine": KIND_TOOL, "machines": KIND_TOOL,
    "农机": KIND_TOOL, "机器": KIND_TOOL, "工具": KIND_TOOL,
    "自动工具": KIND_TOOL,
}

# ── 内置默认物品总表 ─────────────────────────────────────────────
#   字段（和「数据管理」里看到的一致，按 kind 用其中一部分）：
#     kind           seed 种子 / fert 肥料 / animal 看门家畜 / feed 家畜饲料
#     name           名字（指令里就填它）
#     price          价格（积分，整数）
#     level_req      等级要求；**填 0 = 无等级限制**（默认很贵那种）
#     grow_minutes   生长时间（分钟）        —— 仅 seed
#     yield_min/max  收获产量区间（积分）    —— 仅 seed
#     speed_up       缩短生长时间（%）       —— 仅 fert：缩短**剩余**时间
#     yield_bonus    提高产量（%）           —— 仅 fert：直接加到这块地
#     steal_guard    防止被偷（%）           —— 仅 animal（看门唯一的用处）
#     energy         喂一次恢复多少体力      —— 仅 feed
#     exp            收获时给多少农场经验    —— 仅 seed（每级所需经验可配）
#     desc           说明
DEFAULT_ITEMS = [
    # ── 种子：等级解锁线（便宜 -> 贵）──────────────────────────
    {"kind": KIND_SEED, "name": "小白菜", "price": "20",
     "grow_minutes": "3", "yield_min": "26", "yield_max": "40",
     "exp": "5", "level_req": "1", "desc": "撒下去三分钟就能收，新手第一桶金"},
    {"kind": KIND_SEED, "name": "胡萝卜", "price": "50",
     "grow_minutes": "8", "yield_min": "70", "yield_max": "110",
     "exp": "12", "level_req": "2", "desc": "长得快、产量稳"},
    {"kind": KIND_SEED, "name": "番茄", "price": "120",
     "grow_minutes": "15", "yield_min": "170", "yield_max": "260",
     "exp": "25", "level_req": "4", "desc": "红彤彤的，价格是胡萝卜的两倍多"},
    {"kind": KIND_SEED, "name": "土豆", "price": "260",
     "grow_minutes": "30", "yield_min": "380", "yield_max": "560",
     "exp": "45", "level_req": "6", "desc": "埋在土里，挖出来一麻袋"},
    {"kind": KIND_SEED, "name": "草莓", "price": "500",
     "grow_minutes": "45", "yield_min": "760", "yield_max": "1100",
     "exp": "70", "level_req": "9", "desc": "娇气但值钱"},
    {"kind": KIND_SEED, "name": "西瓜", "price": "900",
     "grow_minutes": "60", "yield_min": "1400", "yield_max": "2000",
     "exp": "100", "level_req": "12", "desc": "一亩地一个大西瓜"},
    {"kind": KIND_SEED, "name": "葡萄", "price": "1600",
     "grow_minutes": "90", "yield_min": "2600", "yield_max": "3600",
     "exp": "150", "level_req": "15", "desc": "要搭架子，但一串顶一筐"},
    {"kind": KIND_SEED, "name": "火龙果", "price": "2800",
     "grow_minutes": "120", "yield_min": "4600", "yield_max": "6400",
     "exp": "220", "level_req": "18", "desc": "看着像仙人掌，结出来是钱"},
    {"kind": KIND_SEED, "name": "灵芝", "price": "5000",
     "grow_minutes": "180", "yield_min": "8500", "yield_max": "12000",
     "exp": "320", "level_req": "22", "desc": "深山老林里才长的东西"},
    {"kind": KIND_SEED, "name": "人参果", "price": "9000",
     "grow_minutes": "240", "yield_min": "16000", "yield_max": "22000",
     "exp": "450", "level_req": "26", "desc": "三千年一开花三千年一结果（本农场只要四小时）"},
    # ── 种子：**无等级限制**的进阶货（谁都能买，但默认价很贵）──
    {"kind": KIND_SEED, "name": "秘银麦", "price": "20000",
     "grow_minutes": "20", "yield_min": "24000", "yield_max": "28000",
     "exp": "120", "level_req": "0", "desc": "★ 无等级限制，但要 2 万 —— 新手也能靠它起飞（很贵）"},
    {"kind": KIND_SEED, "name": "黄金南瓜", "price": "60000",
     "grow_minutes": "45", "yield_min": "72000", "yield_max": "84000",
     "exp": "260", "level_req": "0", "desc": "★ 无等级限制，单价 6 万，攒够钱一口气升级"},
    {"kind": KIND_SEED, "name": "钻石玉米", "price": "150000",
     "grow_minutes": "90", "yield_min": "180000", "yield_max": "210000",
     "exp": "500", "level_req": "0", "desc": "★ 无等级限制的顶级种子，默认价 15 万"},

    # ── 肥料（农具商店）：加速成长、快点成熟，加成与价格各不相同 ──
    {"kind": KIND_FERT, "name": "草木灰", "price": "100",
     "level_req": "0", "speed_up": "10", "yield_bonus": "0",
     "desc": "最便宜的催熟，缩掉 10% 剩余时间"},
    {"kind": KIND_FERT, "name": "复合肥", "price": "400",
     "level_req": "3", "speed_up": "25", "yield_bonus": "0",
     "desc": "缩掉 25% 剩余时间"},
    {"kind": KIND_FERT, "name": "有机肥", "price": "1200",
     "level_req": "6", "speed_up": "40", "yield_bonus": "5",
     "desc": "缩掉 40% 剩余时间，顺便增产 5%"},
    {"kind": KIND_FERT, "name": "生长激素", "price": "4000",
     "level_req": "10", "speed_up": "60", "yield_bonus": "0",
     "desc": "缩掉 60% 剩余时间"},
    {"kind": KIND_FERT, "name": "催熟剂", "price": "15000",
     "level_req": "15", "speed_up": "100", "yield_bonus": "0",
     "desc": "★ 100% —— 撒下去立刻成熟"},
    {"kind": KIND_FERT, "name": "神农秘肥", "price": "50000",
     "level_req": "22", "speed_up": "100", "yield_bonus": "30",
     "desc": "★ 立刻成熟 + 这块地额外增产 30%"},

    # ── 看门的家畜（工具商店）：可买多只（上限后台可配，默认 5）──
    #   ★ 用户要求：**看门的只有「防止被偷」这一个作用**
    #     （不去动生长时间、产量、自动收获）
    {"kind": KIND_ANIMAL, "name": "中华田园犬", "price": "300",
     "level_req": "1", "steal_guard": "10",
     "desc": "土狗，看家护院，被偷概率 -10%"},
    {"kind": KIND_ANIMAL, "name": "柴犬", "price": "1200",
     "level_req": "4", "steal_guard": "20",
     "desc": "机灵，被偷概率 -20%"},
    {"kind": KIND_ANIMAL, "name": "边牧", "price": "4000",
     "level_req": "8", "steal_guard": "30",
     "desc": "会算数，被偷概率 -30%"},
    {"kind": KIND_ANIMAL, "name": "金毛", "price": "10000",
     "level_req": "13", "steal_guard": "40",
     "desc": "块头大，被偷概率 -40%"},
    {"kind": KIND_ANIMAL, "name": "德牧", "price": "25000",
     "level_req": "18", "steal_guard": "55",
     "desc": "训练有素，被偷概率 -55%"},
    {"kind": KIND_ANIMAL, "name": "藏獒", "price": "60000",
     "level_req": "24", "steal_guard": "75",
     "desc": "★ 最凶的一只，被偷概率 -75%"},

    # ── 家畜饲料（工具商店）：看门会累，喂了恢复体力 ──────────
    {"kind": KIND_FEED, "name": "干草", "price": "50",
     "level_req": "0", "energy": "20",
     "desc": "最便宜的，恢复 20 点体力"},
    {"kind": KIND_FEED, "name": "肉骨头", "price": "200",
     "level_req": "0", "energy": "50",
     "desc": "恢复 50 点体力"},
    {"kind": KIND_FEED, "name": "营养狗粮", "price": "800",
     "level_req": "5", "energy": "100",
     "desc": "恢复 100 点体力（正好喂饱一只）"},
    {"kind": KIND_FEED, "name": "特级罐头", "price": "3000",
     "level_req": "12", "energy": "300",
     "desc": "恢复 300 点体力，够看好几天"},
    {"kind": KIND_FEED, "name": "仙丹", "price": "20000",
     "level_req": "20", "energy": "1000",
     "desc": "恢复 1000 点体力（远超上限也会截断）"},

    # ── 农机（工具商店）：装备后**每天自动干活**，静默不打扰群里 ──
    {"kind": KIND_TOOL, "name": "自动收割机", "price": "80000",
     "level_req": "5", "auto_harvest": "1",
     "desc": "★ 装备后**每天自动收获**所有成熟的作物并直接卖掉，"
             "结果只记在「农场状态」里，不发群消息"},
    {"kind": KIND_TOOL, "name": "自动播种机", "price": "120000",
     "level_req": "8", "auto_plant": "1",
     "desc": "★ 装备后**每天自动播种**：从背包里挑最贵的种子，"
             "把空地都种上（没种子就不种），静默不发群消息"},

    # ── 偷菜装备（工具商店）：装备后**提高偷菜成功率** ────────
    #   ★ 不带装备时基础成功率只有 3%，失败还要赔对方积分
    {"kind": KIND_TOOL, "name": "破手套", "price": "500",
     "level_req": "1", "steal_bonus": "5",
     "desc": "偷菜成功率 +5%"},
    {"kind": KIND_TOOL, "name": "万能钥匙", "price": "3000",
     "level_req": "5", "steal_bonus": "12",
     "desc": "偷菜成功率 +12%"},
    {"kind": KIND_TOOL, "name": "潜行斗篷", "price": "15000",
     "level_req": "10", "steal_bonus": "25",
     "desc": "偷菜成功率 +25%"},
    {"kind": KIND_TOOL, "name": "猫步鞋", "price": "60000",
     "level_req": "16", "steal_bonus": "45",
     "desc": "偷菜成功率 +45%"},
    {"kind": KIND_TOOL, "name": "时之沙漏", "price": "200000",
     "level_req": "24", "steal_bonus": "80",
     "desc": "★ 偷菜成功率 +80%（最贵也最强）"},
]

# 一块地最短生长时间（秒）—— 加速再猛也不会秒熟，避免无限刷分
MIN_GROW_SEC = 30

# ── ★★ 土地品质（收获时可能「触发收获机遇」升级）─────────────────
#   normal 普通土地 -> red 红土地 -> black 黑土地（到顶就不再升）
#   加成 = 收获**产量**提高的百分比（产量高 -> 结算积分多）
QUALITY_NORMAL = "normal"
QUALITY_RED = "red"
QUALITY_BLACK = "black"
QUALITY_ORDER = (QUALITY_NORMAL, QUALITY_RED, QUALITY_BLACK)
QUALITY_LABEL = {
    QUALITY_NORMAL: "普通土地",
    QUALITY_RED: "红土地",
    QUALITY_BLACK: "黑土地",
}
QUALITY_EMOJI = {
    QUALITY_NORMAL: "🟫",
    QUALITY_RED: "🟥",
    QUALITY_BLACK: "⬛",
}
# kind 字段的别名不影响它；这里给「后台可能写中文品质」留个兼容
_QUALITY_ALIAS = {
    "": QUALITY_NORMAL, "normal": QUALITY_NORMAL, "普通": QUALITY_NORMAL,
    "普通土地": QUALITY_NORMAL, "default": QUALITY_NORMAL,
    "red": QUALITY_RED, "红": QUALITY_RED, "红土地": QUALITY_RED,
    "red_soil": QUALITY_RED,
    "black": QUALITY_BLACK, "黑": QUALITY_BLACK, "黑土地": QUALITY_BLACK,
    "black_soil": QUALITY_BLACK,
}


def norm_quality(raw) -> str:
    """把品质写法归一成 normal / red / black"""
    k = str(raw if raw is not None else "").strip().lower()
    return _QUALITY_ALIAS.get(k, QUALITY_NORMAL)


def _int(v, dflt=0) -> int:
    """把后台填的字符串安全转成整数"""
    try:
        s = str(v if v is not None else "").strip()
        if not s:
            return dflt
        return int(float(s))
    except Exception:
        return dflt


def norm_kind(raw) -> str:
    """把 kind 归一成 seed / fert / animal / feed（认不出的当种子）"""
    k = str(raw if raw is not None else "").strip().lower()
    return _KIND_ALIAS.get(k, KIND_SEED)


class FarmModule:
    """农场存档 + 数值计算（不含积分加减，那部分在 main.py）"""

    def __init__(self, data_path: str, store=None):
        self.data_path = data_path
        self.store = store
        self.enable = True
        self.init_lands = 3
        self.max_lands = 30
        self.max_level = 30
        # ★ 等级经验：升到下一级需要 exp_base + (级数-1) * exp_step
        self.exp_base = 100
        self.exp_step = 60
        # ★ 家畜：可养多只 + 看门会累
        self.max_animals = 5
        self.energy_max = 100
        self.energy_per_hour = 10
        # ★ 偷菜
        self.steal_enable = True
        # ★ 用户要求：**不带装备时基础成功率只有 3%**
        self.steal_base_rate = 3
        self.steal_ratio = 30
        self.steal_daily_limit = 3
        self.steal_min_amount = 10
        # ★ 偷菜失败要赔对方积分（随机区间，默认 1~500）
        self.steal_comp_min = 1
        self.steal_comp_max = 500
        # ★ 土地品质（收获机遇）
        self.upgrade_chance = 5
        self.bonus_red = 10
        self.bonus_black = 20
        self.users = {}
        self._load()

    # ── 配置 ──────────────────────────────────────────────────
    def reload(self, cfg: dict):
        cfg = cfg if isinstance(cfg, dict) else {}

        def _b(k, d):
            v = cfg.get(k)
            if v is None:
                return d
            if isinstance(v, bool):
                return v
            return str(v).strip().lower() in ("1", "true", "yes", "on", "开")

        self.enable = _b("farm_enable", True)
        self.init_lands = max(1, min(30, _int(cfg.get("farm_init_lands"), 3)))
        self.max_lands = max(self.init_lands,
                             min(30, _int(cfg.get("farm_max_lands"), 30)))
        self.max_level = max(1, min(999, _int(cfg.get("farm_max_level"), 30)))
        self.exp_base = max(1, _int(cfg.get("farm_exp_base"), 100))
        self.exp_step = max(0, _int(cfg.get("farm_exp_step"), 60))
        self.max_animals = max(1, min(50, _int(cfg.get("farm_max_animals"), 5)))
        self.energy_max = max(1, _int(cfg.get("farm_animal_energy_max"), 100))
        self.energy_per_hour = max(
            0, _int(cfg.get("farm_animal_energy_per_hour"), 10))
        # ★ 偷菜
        self.steal_enable = _b("farm_steal_enable", True)
        self.steal_base_rate = max(
            0, min(100, _int(cfg.get("farm_steal_base_rate"), 3)))
        self.steal_ratio = max(
            1, min(100, _int(cfg.get("farm_steal_ratio"), 30)))
        self.steal_daily_limit = max(
            0, _int(cfg.get("farm_steal_daily_limit"), 3))
        self.steal_min_amount = max(
            0, _int(cfg.get("farm_steal_min_amount"), 10))
        self.steal_comp_min = max(
            0, _int(cfg.get("farm_steal_fail_comp_min"), 1))
        self.steal_comp_max = max(
            self.steal_comp_min, _int(cfg.get("farm_steal_fail_comp_max"), 500))
        # ★ 土地品质
        self.upgrade_chance = max(
            0, min(100, _int(cfg.get("farm_harvest_upgrade_chance"), 5)))
        self.bonus_red = max(0, _int(cfg.get("farm_quality_bonus_red"), 10))
        self.bonus_black = max(0, _int(cfg.get("farm_quality_bonus_black"), 20))
        logger.info(
            "[farm] 农场：开关=%s 初始地=%d 上限=%d 级上限=%d "
            "升级经验=%d+%d/级 "
            "家畜上限=%d 体力=%d/%d每小时 偷菜=%s 基础成功率=%d%% "
            "偷取比例=%d%% 每天%d次 "
            "收获机遇=%d%% 红土地+%d%% 黑土地+%d%%",
            self.enable, self.init_lands, self.max_lands,
            self.max_level, self.exp_base, self.exp_step,
            self.max_animals, self.energy_max, self.energy_per_hour,
            self.steal_enable, self.steal_base_rate, self.steal_ratio,
            self.steal_daily_limit,
            self.upgrade_chance, self.bonus_red, self.bonus_black)

    # ── 物品总表（数据集合优先，空则用内置默认）──────────────
    @property
    def store_key(self) -> str:
        return "farm_item_list"

    def _rows(self) -> list:
        rows = []
        if self.store is not None:
            try:
                rows = self.store.get(self.store_key, autoreload=2) or []
            except Exception:
                rows = []
        out = [r for r in rows
               if isinstance(r, dict) and str(r.get("name") or "").strip()]
        return out or [dict(x) for x in DEFAULT_ITEMS]

    @staticmethod
    def item_of(row: dict) -> dict:
        """规范化一条物品记录（整数、kind 归一、缺字段给安全默认）"""
        kind = norm_kind(row.get("kind"))
        it = {
            "kind": kind,
            "name": str(row.get("name") or "").strip(),
            "price": max(0, _int(row.get("price"), 0)),
            "level_req": max(0, _int(row.get("level_req"), 0)),
            "grow_minutes": max(1, _int(row.get("grow_minutes"), 10)),
            "yield_min": max(0, _int(row.get("yield_min"), 10)),
            "yield_max": max(0, _int(row.get("yield_max"), 10)),
            "speed_up": max(0, min(100, _int(row.get("speed_up"), 0))),
            "yield_bonus": max(0, _int(row.get("yield_bonus"), 0)),
            "auto_harvest": 1 if _int(row.get("auto_harvest"), 0) else 0,
            "auto_plant": 1 if _int(row.get("auto_plant"), 0) else 0,
            "steal_guard": max(0, min(100, _int(row.get("steal_guard"), 0))),
            "steal_bonus": max(0, min(500, _int(row.get("steal_bonus"), 0))),
            "energy": max(0, _int(row.get("energy"), 0)),
            "exp": max(0, _int(row.get("exp"), 0)),
            "desc": str(row.get("desc") or ""),
        }
        if it["yield_max"] < it["yield_min"]:
            it["yield_max"] = it["yield_min"]
        return it

    def items(self, kind: str = "") -> list:
        """
        物品列表（已规范化，**按价格从便宜到贵**排 —— 商店编号就是这个顺序）。

        kind 为空返回全部；否则只返回该 kind。
        """
        out = [self.item_of(r) for r in self._rows()]
        out = [x for x in out if x["name"]]
        if kind:
            out = [x for x in out if x["kind"] == kind]
        out.sort(key=lambda x: (x["price"], x["name"]))
        return out

    def seeds(self) -> list:
        return self.items(KIND_SEED)

    def fertilizers(self) -> list:
        return self.items(KIND_FERT)

    def animals(self) -> list:
        """看门的家畜（工具商店的货）"""
        return self.items(KIND_ANIMAL)

    def feeds(self) -> list:
        """家畜饲料（工具商店的货）"""
        return self.items(KIND_FEED)

    def tools(self) -> list:
        """农机（自动收割机 / 自动播种机）"""
        return self.items(KIND_TOOL)

    def tool_shop(self) -> list:
        """
        工具商店的**整份货架**：家畜 + 饲料 + 农机。
        （主程序按 `farm_shop_per_page` 分页显示，避免一屏塞太多）
        """
        return self.animals() + self.feeds() + self.tools()

    # 兼容旧叫法
    def dogs(self) -> list:
        return self.animals()

    @staticmethod
    def _find(rows: list, key):
        """在 rows 里按「名字（含模糊）」或「1 起的序号」找一条，找不到返回 {}"""
        k = str(key or "").strip()
        if not k:
            return {}
        if k.isdigit():
            n = int(k)
            if 1 <= n <= len(rows):
                return rows[n - 1]
        low = k.lower()
        for x in rows:
            if x["name"].lower() == low:
                return x
        for x in rows:
            if low and low in x["name"].lower():
                return x
        return {}

    def find_seed(self, key) -> dict:
        return self._find(self.seeds(), key)

    def find_fert(self, key) -> dict:
        return self._find(self.fertilizers(), key)

    def find_animal(self, key) -> dict:
        return self._find(self.animals(), key)

    def find_feed(self, key) -> dict:
        return self._find(self.feeds(), key)

    def find_machine(self, key) -> dict:
        """农机的精确查找（只在家具表里找，避免和家畜/饲料重名）"""
        return self._find(self.tools(), key)

    def find_tool(self, key) -> dict:
        """工具商店的货架里找（家畜 + 饲料）"""
        return self._find(self.tool_shop(), key)

    # 兼容旧叫法
    def find_dog(self, key) -> dict:
        return self.find_animal(key)

    def ensure_defaults(self) -> dict:
        """
        把内置物品表**补齐**进 data_store，让后台「数据管理」看得见、改得动。

        ★★ 2.1.0：由「只在集合完全为空时写」改成**按 (kind, name) 合并补齐**。

        原因：早期版本写入默认表时还没有「工具」这一类，
        集合一旦非空就再也不会补 —— 于是后台「工具」页看不到那 7 件，
        用户以为没有、也没法改（用户反馈：「暂无工具」）。

        规则：
        · 集合为空 -> 写入全部默认（保留「删空 = 恢复默认」的老行为）
        · 集合非空 -> 只补**缺的那几条**，**已有条目一律不动**
          （用户改过的价格 / 数值 / 生长时间绝不会被覆盖）
        返回 {"items": 新增条数}
        """
        if self.store is None:
            return {"items": 0}
        try:
            cur = [dict(r) for r in (self.store.get(self.store_key) or [])
                   if isinstance(r, dict)
                   and str(r.get("name") or "").strip()]

            def _key(x):
                return (str(x.get("kind") or "").strip(),
                        str(x.get("name") or "").strip())

            have = {_key(r) for r in cur}
            added = [dict(x) for x in DEFAULT_ITEMS if _key(x) not in have]
            if not added:
                return {"items": 0}
            self.store.set(self.store_key, cur + added)
            logger.info(
                "[farm] 物品表已补齐 %d 条（原有 %d -> 共 %d）",
                len(added), len(cur), len(cur) + len(added))
            return {"items": len(added)}
        except Exception:
            logger.exception("[farm] 补齐默认物品表失败")
            return {"items": 0}

    # ── 等级 / 经验 / 土地数 ──────────────────────────────────
    def exp_floor(self, level: int) -> int:
        """升到第 `level` 级所需的**累计经验**（level<=1 时为 0）"""
        lv = max(1, int(level or 1))
        total = 0
        for i in range(1, min(lv, self.max_level)):
            total += self.exp_base + (i - 1) * self.exp_step
        return total

    def level_of(self, exp) -> int:
        """累计农场经验 -> 等级（1 起，上限 max_level）"""
        e = max(0, _int(exp, 0))
        lv = 1
        while lv < self.max_level and e >= self.exp_floor(lv + 1):
            lv += 1
        return lv

    def exp_progress(self, exp) -> dict:
        """
        经验条信息：
          {"exp","level","in_level","need","floor","next_floor","is_max"}
        """
        e = max(0, _int(exp, 0))
        lv = self.level_of(e)
        floor = self.exp_floor(lv)
        if lv >= self.max_level:
            return {"exp": e, "level": lv, "in_level": e - floor,
                    "need": 0, "floor": floor, "next_floor": floor,
                    "is_max": True}
        nxt = self.exp_floor(lv + 1)
        return {"exp": e, "level": lv, "in_level": e - floor,
                "need": max(0, nxt - floor), "floor": floor,
                "next_floor": nxt, "is_max": False}

    def land_count_of(self, exp) -> int:
        """累计农场经验 -> 应解锁土地数（每升 1 级多 1 块）"""
        lv = self.level_of(exp)
        return min(self.max_lands, self.init_lands + (lv - 1))

    def next_level_need(self, exp) -> int:
        """还差多少**经验**升下一级（已满级返回 0）"""
        p = self.exp_progress(exp)
        if p["is_max"]:
            return 0
        return max(0, p["next_floor"] - p["exp"])

    def add_exp(self, group_id, user_id, amount, now=None) -> dict:
        """
        加农场经验（收获时结算）。

        返回 {"gain","exp","level","level_up","from_level","new_lands"}
        """
        u = self.user(group_id, user_id)
        gain = max(0, _int(amount, 0))
        before = _int(u.get("exp"), 0)
        lv_before = self.level_of(before)
        lands_before = self.land_count_of(before)
        u["exp"] = before + gain
        lv_after = self.level_of(u["exp"])
        lands_after = self.land_count_of(u["exp"])
        # ★ 同步 last_level：升级提示由**收货那一条消息**负责，
        #   不能让下一条指令再报一次
        u["last_level"] = lv_after
        self.save()
        return {"gain": gain, "exp": u["exp"], "level": lv_after,
                "from_level": lv_before, "level_up": lv_after > lv_before,
                "new_lands": max(0, lands_after - lands_before)}

    def sync_level(self, group_id, user_id, now=None) -> dict:
        """
        把等级变化同步出来（给「我的农场」提示升级用）。

        返回 {"level","new","gained","exp","in_level","need","is_max"}
        """
        u = self.user(group_id, user_id)
        lv = self.level_of(u.get("exp"))
        last = max(1, _int(u.get("last_level"), 1))
        gained = max(0, lv - last)
        if lv != last:
            u["last_level"] = lv
            self.save()
        p = self.exp_progress(u.get("exp"))
        return {"level": lv, "new": gained,
                "gained": gained, "exp": p["exp"],
                "in_level": p["in_level"], "need": p["need"],
                "is_max": p["is_max"]}

    @staticmethod
    def level_ok(item: dict, level: int) -> bool:
        """等级是否够买（level_req = 0 = 无等级限制）"""
        need = _int((item or {}).get("level_req"), 0)
        return (need <= 0) or (int(level or 1) >= need)

    # ── 存档 ──────────────────────────────────────────────────
    @staticmethod
    def _key(group_id, user_id) -> str:
        return "%s:%s" % (str(group_id or "").strip(),
                          str(user_id or "").strip())

    def _load(self):
        self.users = {}
        if not self.data_path or not os.path.exists(self.data_path):
            return
        try:
            with open(self.data_path, "r", encoding="utf-8-sig") as f:
                data = json.load(f) or {}
            users = data.get("users")
            if isinstance(users, dict):
                self.users = users
        except Exception:
            logger.exception("[farm] 农场存档读取失败")

    def save(self):
        if not self.data_path:
            return
        try:
            d = os.path.dirname(self.data_path)
            if d and not os.path.isdir(d):
                os.makedirs(d, exist_ok=True)
            tmp = self.data_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"users": self.users}, f,
                          ensure_ascii=False, indent=2)
            os.replace(tmp, self.data_path)
        except Exception:
            logger.exception("[farm] 农场存档保存失败")

    def user(self, group_id, user_id, create: bool = True) -> dict:
        """取某人农场存档（create=False 时不新建；自动兼容旧的单只 dog 字段）"""
        key = self._key(group_id, user_id)
        u = self.users.get(key)
        if not isinstance(u, dict):
            if not create:
                return {}
            u = {"lands": [], "animals": [], "equipped": "",
                 "exp": 0, "last_level": 1,
                 "bag": {}, "tools": [], "tools_on": [], "today": {},
                 "harvested": 0, "earned": 0, "spent": 0}
            self.users[key] = u
        # ★ 老存档补齐新字段（背包 / 农机 / 今日统计）
        if not isinstance(u.get("bag"), dict):
            u["bag"] = {}
        if not isinstance(u.get("tools"), list):
            u["tools"] = []
        if not isinstance(u.get("tools_on"), list):
            u["tools_on"] = []
        if not isinstance(u.get("today"), dict):
            u["today"] = {}
        if not isinstance(u.get("lands"), list):
            u["lands"] = []
        # ★ 老存档里空地是 `null` -> 统一成 {"quality": "normal"}，
        #   这样收获机遇升级出来的红土 / 黑土才能留在空地上
        for _i, _land in enumerate(u["lands"]):
            if not isinstance(_land, dict):
                u["lands"][_i] = {"quality": QUALITY_NORMAL}
            elif not str(_land.get("quality") or "").strip():
                _land["quality"] = QUALITY_NORMAL
        if not isinstance(u.get("animals"), list):
            u["animals"] = []
        # ★ 兼容：老存档只有单只 `dog` 字段 -> 搬成 animals + equipped
        _old = str(u.get("dog") or "").strip()
        if _old and not u["animals"]:
            u["animals"].append({
                "name": _old,
                "bought": _int(u.get("dog_at"), 0),
                "energy": self.energy_max,
                "energy_at": _int(u.get("dog_at"), 0),
            })
        if _old and not str(u.get("equipped") or "").strip():
            u["equipped"] = _old
        u.pop("dog", None)
        u.pop("dog_at", None)
        return u

    def sync_lands(self, group_id, user_id, want: int) -> dict:
        """
        让土地格子数跟上等级（只增不减）。

        ★ 每块地都是一个 dict（至少带 `quality`），这样「收获机遇」升级的
          土地品质能**一直留着**（空地也记着自己是红土 / 黑土）。

        返回 {"added": n, "total": n}
        """
        u = self.user(group_id, user_id)
        lands = u["lands"]
        want = max(self.init_lands, min(self.max_lands, int(want or 0)))
        added = 0
        while len(lands) < want:
            lands.append({"quality": QUALITY_NORMAL})
            added += 1
        return {"added": added, "total": len(lands)}

    # ── 土地品质（收获机遇）────────────────────────────────────
    def quality_of(self, land) -> str:
        """某块地的品质（不是 dict / 没写 -> 普通）"""
        if not isinstance(land, dict):
            return QUALITY_NORMAL
        return norm_quality(land.get("quality"))

    def quality_bonus(self, land) -> int:
        """该地品质带来的**产量加成%**（普通 0 / 红 / 黑）"""
        q = self.quality_of(land)
        if q == QUALITY_RED:
            return max(0, int(self.bonus_red))
        if q == QUALITY_BLACK:
            return max(0, int(self.bonus_black))
        return 0

    @staticmethod
    def quality_label(land) -> str:
        q = land if isinstance(land, str) else None
        if q is None:
            q = norm_quality((land or {}).get("quality")
                             if isinstance(land, dict) else "")
        return QUALITY_LABEL.get(q, QUALITY_LABEL[QUALITY_NORMAL])

    @staticmethod
    def quality_emoji(land) -> str:
        q = land if isinstance(land, str) else None
        if q is None:
            q = norm_quality((land or {}).get("quality")
                             if isinstance(land, dict) else "")
        return QUALITY_EMOJI.get(q, QUALITY_EMOJI[QUALITY_NORMAL])

    def quality_counts(self, group_id, user_id) -> dict:
        """某人各地质的块数（给「我的农场」显示）"""
        u = self.user(group_id, user_id, create=False)
        out = {q: 0 for q in QUALITY_ORDER}
        for land in (u or {}).get("lands") or []:
            out[self.quality_of(land)] = out.get(self.quality_of(land), 0) + 1
        return out

    def upgrade_quality(self, land) -> dict:
        """
        把一块地的品质升一档（普通->红->黑）。

        返回 {"changed","before","after","label_before","label_after"}
        """
        before = self.quality_of(land)
        if before not in QUALITY_ORDER:
            before = QUALITY_NORMAL
        i = QUALITY_ORDER.index(before)
        if i >= len(QUALITY_ORDER) - 1:
            return {"changed": False, "before": before, "after": before,
                    "label_before": QUALITY_LABEL[before],
                    "label_after": QUALITY_LABEL[before]}
        after = QUALITY_ORDER[i + 1]
        if isinstance(land, dict):
            land["quality"] = after
        return {"changed": True, "before": before, "after": after,
                "label_before": QUALITY_LABEL[before],
                "label_after": QUALITY_LABEL[after]}

    def roll_upgrade(self, land) -> dict:
        """掷一次「收获机遇」；命中就把这块地升一档"""
        chance = max(0, min(100, int(self.upgrade_chance)))
        if chance <= 0:
            return {"rolled": False, "changed": False}
        try:
            hit = random.randint(1, 100) <= chance
        except Exception:
            hit = False
        if not hit:
            return {"rolled": True, "changed": False}
        r = self.upgrade_quality(land)
        r["rolled"] = True
        r["chance"] = chance
        return r

    # ── 土地状态 ──────────────────────────────────────────────
    @staticmethod
    def state_of(land, now=None) -> str:
        """'empty' / 'growing' / 'ready'"""
        if not isinstance(land, dict):
            return "empty"
        if not str(land.get("seed") or "").strip():
            return "empty"
        try:
            ready = int(land.get("ready", 0) or 0)
        except Exception:
            ready = 0
        return "ready" if int(now or time.time()) >= ready else "growing"

    @staticmethod
    def progress(land, now=None) -> int:
        """生长进度 0~100（已成熟=100，空地=0）"""
        if not isinstance(land, dict):
            return 0
        try:
            planted = int(land.get("planted", 0) or 0)
            ready = int(land.get("ready", 0) or 0)
        except Exception:
            return 0
        if ready <= planted:
            return 100
        cur = int(now or time.time())
        if cur >= ready:
            return 100
        if cur <= planted:
            return 0
        return int((cur - planted) * 100 / (ready - planted))

    @staticmethod
    def left_seconds(land, now=None) -> int:
        """还差多少秒成熟（没种 / 已熟 = 0）"""
        if not isinstance(land, dict):
            return 0
        try:
            ready = int(land.get("ready", 0) or 0)
        except Exception:
            return 0
        return max(0, ready - int(now or time.time()))

    # ── 家畜：体力（看门会累）────────────────────────────────
    def _decay(self, rec: dict, guarding: bool, now: int) -> int:
        """
        结算一只家畜的体力，返回结算后的体力。

        ★ **只有正在看守的那只**才掉体力（guarding=True）；
          待命的家畜只把 energy_at 推到当前，不掉血。
        """
        now = int(now or time.time())
        try:
            last = int(rec.get("energy_at", now) or now)
        except Exception:
            last = now
        if last > now:
            last = now
        en = _int(rec.get("energy"), self.energy_max)
        if en > self.energy_max:
            en = self.energy_max
        if guarding and self.energy_per_hour > 0:
            cost = (now - last) * float(self.energy_per_hour) / 3600.0
            if cost > 0:
                en = max(0, int(en - cost))
        rec["energy"] = en
        rec["energy_at"] = now
        return en

    def animals_owned(self, group_id, user_id, now=None) -> list:
        """某人养的全部家畜（每条含体力；**结算后**的体力）"""
        now = int(now or time.time())
        u = self.user(group_id, user_id)
        equipped = str(u.get("equipped") or "").strip()
        out = []
        for rec in u["animals"]:
            if not isinstance(rec, dict):
                continue
            nm = str(rec.get("name") or "").strip()
            if not nm:
                continue
            en = self._decay(rec, nm == equipped, now)
            base = self.find_animal(nm) or {}
            out.append({
                "name": nm,
                "energy": en,
                "energy_max": self.energy_max,
                "tired": en <= 0,
                "equipped": (nm == equipped),
                # ★ 看门家畜的唯一作用：防偷（累了就不生效）
                "steal_guard": (_int(base.get("steal_guard"), 0)
                                if (base and en > 0) else 0),
                "steal_guard_max": _int(base.get("steal_guard"), 0),
                "speed_up": _int(base.get("speed_up"), 0),
                "yield_bonus": _int(base.get("yield_bonus"), 0),
                "auto_harvest": 1 if _int(base.get("auto_harvest"), 0) else 0,
                "desc": str(base.get("desc") or ""),
                "in_shop": bool(base),
            })
        return out

    def animal_count(self, group_id, user_id, now=None) -> int:
        return len(self.animals_owned(group_id, user_id, now))

    def equipped_name(self, group_id, user_id) -> str:
        u = self.user(group_id, user_id, create=False)
        return str((u or {}).get("equipped") or "").strip()

    def owner_of(self, group_id, user_id, name) -> dict:
        """在某人养的家畜里按名字找一条（返回引用，找不到 {}）"""
        u = self.user(group_id, user_id)
        low = str(name or "").strip().lower()
        if not low:
            return {}
        for rec in u["animals"]:
            if not isinstance(rec, dict):
                continue
            nm = str(rec.get("name") or "").strip()
            if nm.lower() == low or (low and low in nm.lower()):
                return rec
        return {}

    def add_animal(self, group_id, user_id, item: dict, now=None) -> dict:
        """
        买一只家畜（可买多只，上限 `max_animals`）。

        返回 {"ok","reason","count","max","equipped"}
        """
        now = int(now or time.time())
        u = self.user(group_id, user_id)
        owned = [str(r.get("name") or "") for r in u["animals"]
                 if isinstance(r, dict)]
        if item["name"] in owned:
            return {"ok": False, "reason": "dup", "count": len(owned),
                    "max": self.max_animals}
        if len(owned) >= self.max_animals:
            return {"ok": False, "reason": "full", "count": len(owned),
                    "max": self.max_animals}
        u["animals"].append({"name": item["name"], "bought": now,
                             "energy": self.energy_max, "energy_at": now})
        u["spent"] = _int(u.get("spent"), 0) + int(item.get("price", 0) or 0)
        # 一只都没有时自动装备上（不然买了不知道去哪了）
        if not str(u.get("equipped") or "").strip():
            u["equipped"] = item["name"]
        self.save()
        return {"ok": True, "reason": "", "count": len(u["animals"]),
                "max": self.max_animals, "equipped": u.get("equipped")}

    def set_equipped(self, group_id, user_id, name, now=None) -> dict:
        """装备某只家畜去看门（旧的卸下、体力冻结）"""
        now = int(now or time.time())
        u = self.user(group_id, user_id)
        rec = self.owner_of(group_id, user_id, name)
        if not rec:
            return {"ok": False, "reason": "no_such"}
        cur = str(u.get("equipped") or "").strip()
        # 先把所有家畜的体力结算到当前时刻（卸下后不再掉）
        for r in u["animals"]:
            if isinstance(r, dict):
                self._decay(r, str(r.get("name") or "") == cur, now)
        u["equipped"] = str(rec.get("name") or "")
        self.save()
        return {"ok": True, "reason": "", "name": u["equipped"],
                "old": cur, "energy": _int(rec.get("energy"), 0),
                "tired": _int(rec.get("energy"), 0) <= 0}

    def guard_of_user(self, group_id, user_id, now=None) -> dict:
        """
        当前**在看门**的家畜（含结算后的体力）。

        返回 {} = 没装备；返回的 dict 里：
          · tired=True  -> 累了，`speed_up/yield_bonus/auto_harvest` 已**清零**
          · 否则按商店里的数值给加成
        """
        now = int(now or time.time())
        u = self.user(group_id, user_id, create=False)
        if not u:
            return {}
        name = str(u.get("equipped") or "").strip()
        if not name:
            return {}
        rec = None
        for r in u.get("animals") or []:
            if isinstance(r, dict) and str(r.get("name") or "") == name:
                rec = r
                break
        if rec is None:
            return {}
        en = self._decay(rec, True, now)
        base = self.find_animal(name)
        if not base:
            # 家畜被后台下架了 -> 只保留名字，加成按 0 算（不报错）
            return {"kind": KIND_ANIMAL, "name": name, "price": 0,
                    "level_req": 0, "speed_up": 0, "yield_bonus": 0,
                    "auto_harvest": 0, "grow_minutes": 10, "yield_min": 0,
                    "yield_max": 0, "energy": en,
                    "energy_max": self.energy_max, "tired": en <= 0,
                    "in_shop": False,
                    "desc": "（这只家畜已从后台移除，加成失效）"}
        d = dict(base)
        d["energy"] = en
        d["energy_max"] = self.energy_max
        d["tired"] = en <= 0
        d["in_shop"] = True
        if d["tired"]:
            d["speed_up"] = 0
            d["yield_bonus"] = 0
            d["auto_harvest"] = 0
        return d

    # 兼容旧叫法
    def dog_of_user(self, group_id, user_id, now=None) -> dict:
        return self.guard_of_user(group_id, user_id, now)

    def feed_animal(self, group_id, user_id, item: dict, name=None,
                    now=None) -> dict:
        """
        喂一份饲料。name 为空 -> 自动挑：优先「装备中且饿了」的，
        其次体力最低的那只。

        返回 {"ok","reason","target","before","after","added","wasted",
              "tired_before","energy_max"}
        """
        now = int(now or time.time())
        u = self.user(group_id, user_id)
        owned = self.animals_owned(group_id, user_id, now)
        if not owned:
            return {"ok": False, "reason": "no_animal"}
        equipped = str(u.get("equipped") or "").strip()
        target = None
        if str(name or "").strip():
            target = self.owner_of(group_id, user_id, name)
            if not target:
                return {"ok": False, "reason": "no_such"}
        else:
            _rank = sorted(
                owned,
                key=lambda a: (0 if (a["equipped"] and a["tired"]) else 1,
                               a["energy"], 0 if a["equipped"] else 1))
            target = self.owner_of(group_id, user_id, _rank[0]["name"])
        if not target:
            return {"ok": False, "reason": "no_such"}
        nm = str(target.get("name") or "")
        before = self._decay(target, nm == equipped, now)
        add = _int(item.get("energy"), 0)
        after = min(self.energy_max, before + add)
        target["energy"] = after
        target["energy_at"] = now
        self.save()
        return {"ok": True, "reason": "", "target": nm,
                "before": before, "after": after,
                "added": max(0, after - before),
                "wasted": max(0, (before + add) - after),
                "tired_before": before <= 0,
                "energy_max": self.energy_max}

    # ── 种植 / 收获 ───────────────────────────────────────────
    def plant(self, group_id, user_id, seed: dict, now=None,
              index=None) -> dict:
        """
        种下 seed。

        · index=None -> 按顺序种在**第一块空地**（自动播种机也走这条）
        · index=N    -> 种在第 N 块地（0 起）；越界 / 已被占 -> 返回原因

        返回：
          {"ok","reason","index","ready","grow_sec","amount","auto",
           "base_sec","guard","quality","quality_bonus"}
          · reason: "" / "full" 满了 / "range" 没这块地 / "occupied" 已种
          · auto：种之前顺手自动收获的结果（有自动收获家畜在看门时）
        """
        now = int(now or time.time())
        guard = self.guard_of_user(group_id, user_id, now)
        auto = self.auto_harvest(group_id, user_id, now)
        u = self.user(group_id, user_id)
        lands = u["lands"]
        idx = -1
        if index is None:
            for i, land in enumerate(lands):
                if self.state_of(land, now) == "empty":
                    idx = i
                    break
        else:
            try:
                i = int(index)
            except Exception:
                return {"ok": False, "reason": "bad", "index": -1,
                        "auto": auto}
            if not (0 <= i < len(lands)):
                return {"ok": False, "reason": "range", "index": i,
                        "auto": auto}
            if self.state_of(lands[i], now) != "empty":
                return {"ok": False, "reason": "occupied", "index": i,
                        "auto": auto}
            idx = i
        if idx < 0:
            return {"ok": False, "reason": "full", "index": -1, "auto": auto}
        base = max(1, int(seed.get("grow_minutes", 10) or 10)) * 60
        cut = int(guard.get("speed_up", 0) or 0)
        grow = int(base * (100 - cut) / 100)
        grow = max(MIN_GROW_SEC, grow)
        ymin = int(seed.get("yield_min", 0) or 0)
        ymax = int(seed.get("yield_max", 0) or 0)
        if ymax < ymin:
            ymax = ymin
        amount = random.randint(ymin, ymax) if ymax > 0 else 0
        # ★ 原地改，**保住这块地的品质**（红土 / 黑土不能被种菜冲掉）
        land = lands[idx]
        if not isinstance(land, dict):
            land = {"quality": QUALITY_NORMAL}
            lands[idx] = land
        land["seed"] = seed["name"]
        land["planted"] = now
        land["ready"] = now + grow
        land["amount"] = amount
        land["exp"] = _int(seed.get("exp"), 0)
        u["spent"] = _int(u.get("spent"), 0) + int(seed.get("price", 0) or 0)
        self.save()
        return {"ok": True, "reason": "", "index": idx,
                "ready": now + grow, "grow_sec": grow,
                "amount": amount, "auto": auto,
                "base_sec": base, "guard": guard, "dog": guard,
                "quality": self.quality_of(land),
                "quality_bonus": self.quality_bonus(land)}

    def grow_target(self, group_id, user_id, index=None, now=None):
        """
        选一块**正在生长**的地来施肥。

        index 给了就用它（越界 / 不是生长中 -> 返回 (None, 下标, 原因)）；
        没给就自动选**剩余时间最长**的那块（催它最划算）。
        返回 (土地, 下标, 原因)；成功时原因为 ""。
        """
        now = int(now or time.time())
        u = self.user(group_id, user_id)
        lands = u["lands"]
        if not lands:
            return None, -1, "empty"
        if index is not None:
            try:
                i = int(index)
            except Exception:
                return None, -1, "bad"
            if not (0 <= i < len(lands)):
                return None, -1, "range"
            st = self.state_of(lands[i], now)
            if st == "empty":
                return None, i, "empty_land"
            if st == "ready":
                return None, i, "already"
            return lands[i], i, ""
        best_i, best_left = -1, -1
        for i, land in enumerate(lands):
            if self.state_of(land, now) != "growing":
                continue
            left = self.left_seconds(land, now)
            if left > best_left:
                best_i, best_left = i, left
        if best_i < 0:
            return None, -1, "none"
        return lands[best_i], best_i, ""

    def use_item(self, group_id, user_id, item: dict, index=None,
                 now=None) -> dict:
        """
        对一块**正在生长**的地用肥料。

        效果：
          · speed_up%    -> 缩短**剩余**时间（100 = 立刻成熟）
          · yield_bonus% -> 直接加到这块地的产量上

        返回 {"ok","reason","index","before","after","matured",
              "saved_sec","yield_bonus"}
        """
        now = int(now or time.time())
        land, i, why = self.grow_target(group_id, user_id, index, now)
        if land is None:
            return {"ok": False, "reason": why, "index": i}
        left = self.left_seconds(land, now)
        cut = int(item.get("speed_up", 0) or 0)
        save_sec = int(left * cut / 100)
        # 至少缩 1 秒（不然小数值肥料看着没效果），但不会超过剩余时间
        if cut > 0:
            save_sec = max(1, min(left, save_sec))
        new_ready = int(land.get("ready", now)) - save_sec
        new_ready = max(now, new_ready)
        land["ready"] = new_ready
        yb = int(item.get("yield_bonus", 0) or 0)
        if yb:
            base = _int(land.get("amount"), 0)
            land["amount"] = int(base * (100 + yb) / 100)
        matured = new_ready <= now
        self.save()
        return {"ok": True, "reason": "", "index": i,
                "before": left, "after": max(0, new_ready - now),
                "matured": matured,
                "saved_sec": left - max(0, new_ready - now),
                "yield_bonus": yb,
                "ready": new_ready, "seed": str(land.get("seed") or "")}

    def harvest(self, group_id, user_id, index=None, now=None) -> dict:
        """
        收获。index=None 表示收**所有**成熟的地。

        返回 {"ok","picked":[{index,seed,amount,base}], "total","ready_left",
              "guard"}
        """
        now = int(now or time.time())
        u = self.user(group_id, user_id)
        lands = u["lands"]
        guard = self.guard_of_user(group_id, user_id, now)
        bonus = int(guard.get("yield_bonus", 0) or 0)
        picked = []
        if index is None:
            targets = [i for i, land in enumerate(lands)
                       if self.state_of(land, now) == "ready"]
        else:
            targets = []
            try:
                i = int(index)
            except Exception:
                i = -1
            if 0 <= i < len(lands) and self.state_of(lands[i], now) == "ready":
                targets = [i]
        total = 0
        exp_gain = 0
        upgrades = []
        for i in targets:
            land = lands[i]
            base = max(0, _int(land.get("amount"), 0))
            qbonus = self.quality_bonus(land)
            got = int(base * (100 + bonus) / 100)
            got = int(got * (100 + qbonus) / 100)
            picked.append({"index": i, "seed": str(land.get("seed") or ""),
                           "amount": got, "base": base,
                           "exp": _int(land.get("exp"), 0),
                           "quality": self.quality_of(land),
                           "quality_bonus": qbonus,
                           "quality_label": self.quality_label(land)})
            total += got
            exp_gain += _int(land.get("exp"), 0)
            # ★ 收获机遇：有概率把这块地升一档（升完继续留在地里）
            keep = {"quality": self.quality_of(land)}
            lands[i] = keep
            up = self.roll_upgrade(keep)
            if up.get("changed"):
                upgrades.append({"index": i,
                                 "before": up["before"],
                                 "after": up["after"],
                                 "label_before": up["label_before"],
                                 "label_after": up["label_after"],
                                 "bonus": self.quality_bonus(keep)})
        up_exp = None
        if picked:
            u["harvested"] = _int(u.get("harvested"), 0) + len(picked)
            u["earned"] = _int(u.get("earned"), 0) + total
            if exp_gain > 0:
                up_exp = self.add_exp(group_id, user_id, exp_gain, now)
            else:
                self.save()
                up_exp = {"gain": 0, "exp": _int(u.get("exp"), 0),
                          "level": self.level_of(u.get("exp")),
                          "from_level": self.level_of(u.get("exp")),
                          "level_up": False, "new_lands": 0}
        elif upgrades:
            self.save()
        still = sum(1 for land in lands if self.state_of(land, now) == "ready")
        return {"ok": bool(picked), "picked": picked, "total": total,
                "ready_left": still, "guard": guard, "dog": guard,
                "exp_gain": exp_gain, "exp": up_exp, "upgrades": upgrades}

    def auto_harvest(self, group_id, user_id, now=None) -> dict:
        """
        自动收获（只有**在看门且没累**的自动收获家畜才动）。

        返回 {"ok","picked","total","guard"}；没有时 ok=False picked=[]。
        """
        now = int(now or time.time())
        guard = self.guard_of_user(group_id, user_id, now)
        if not int(guard.get("auto_harvest", 0) or 0):
            return {"ok": False, "picked": [], "total": 0, "guard": guard,
                    "dog": guard}
        return self.harvest(group_id, user_id, index=None, now=now)

    def stats(self) -> dict:
        """总览（后台用）"""
        n = len(self.users)
        crops = 0
        guarding = 0
        for u in self.users.values():
            if not isinstance(u, dict):
                continue
            crops += sum(1 for land in (u.get("lands") or [])
                         if isinstance(land, dict)
                         and str(land.get("seed") or "").strip())
            if str(u.get("equipped") or "").strip():
                guarding += 1
        try:
            kinds = {"seed": len(self.seeds()),
                     "fert": len(self.fertilizers()),
                     "animal": len(self.animals()),
                     "feed": len(self.feeds())}
        except Exception:
            kinds = {}
        return {"users": n, "crops": crops, "guarding": guarding,
                "items": kinds}

    # ── ★ 偷菜 ────────────────────────────────────────────────
    @staticmethod
    def day_key(now=None) -> str:
        """本地日期串（每天限次用）"""
        try:
            return time.strftime("%Y-%m-%d",
                                 time.localtime(int(now or time.time())))
        except Exception:
            return ""

    def steal_left(self, group_id, user_id, now=None, limit=None) -> int:
        """今天还剩几次偷菜机会"""
        lim = self.steal_daily_limit if limit is None else int(limit)
        if lim <= 0:
            return 0
        u = self.user(group_id, user_id)
        if str(u.get("steal_day") or "") != self.day_key(now):
            return lim
        return max(0, lim - _int(u.get("steal_used"), 0))

    def _steal_mark(self, group_id, user_id, now=None):
        """记一次偷菜（跨天自动归零）"""
        u = self.user(group_id, user_id)
        dk = self.day_key(now)
        if str(u.get("steal_day") or "") != dk:
            u["steal_day"] = dk
            u["steal_used"] = 0
        u["steal_used"] = _int(u.get("steal_used"), 0) + 1
        return u["steal_used"]

    def stealable(self, group_id, user_id, now=None, min_amount=None) -> list:
        """
        某人**可以被人偷**的地：已成熟、产量 >= 门槛。

        返回 [{"index", "seed", "amount"}]（按产量从高到低）
        """
        now = int(now or time.time())
        _min = self.steal_min_amount if min_amount is None else int(min_amount)
        u = self.user(group_id, user_id, create=False)
        if not u:
            return []
        out = []
        for i, land in enumerate(u.get("lands") or []):
            if self.state_of(land, now) != "ready":
                continue
            amt = _int((land or {}).get("amount"), 0)
            if amt < _min:
                continue
            out.append({"index": i,
                        "seed": str((land or {}).get("seed") or ""),
                        "amount": amt})
        out.sort(key=lambda x: (-x["amount"], x["index"]))
        return out

    def guard_rate_of(self, group_id, user_id, now=None) -> int:
        """某人当前**有效的**防偷比例（家畜累了 / 没装备 => 0）"""
        g = self.guard_of_user(group_id, user_id, now)
        if not g or g.get("tired"):
            return 0
        return max(0, min(100, _int(g.get("steal_guard"), 0)))

    def steal_bonus_of(self, group_id, user_id) -> int:
        """
        某人**装备中**的偷菜装备带来的成功率加成（%）。

        ★ 用户要求：不带装备时基础成功率只有 `steal_base_rate`（默认 3%），
          戴上工具商店的偷菜装备才会往上加。
        """
        bonus = 0
        for it in self.tools_on(group_id, user_id):
            bonus += max(0, _int(it.get("steal_bonus"), 0))
        return max(0, bonus)

    def steal_tools_on(self, group_id, user_id) -> list:
        """当前装备中、且**真的加偷菜成功率**的装备"""
        out = []
        for it in self.tools_on(group_id, user_id):
            if _int(it.get("steal_bonus"), 0) > 0:
                out.append(it)
        return out

    def steal(self, group_id, from_uid, to_uid, now=None):
        """
        偷 `to_uid` 的菜（同一群内）。

        规则：
          · 只有**已成熟**且产量 >= `steal_min_amount` 的地能被偷
          · 成功率 = `steal_base_rate` - 对方**在看门且没累**的家畜防偷比例
          · 成功偷走那块地产量的 `steal_ratio`%：
              偷的人 +钱；被偷的那块地 `amount` 等量减少（不是全没了）
              减到 0 就变成空地
        返回 dict：
          {"ok","reason","chance","guard","roll","target_seed",
           "target_index","take","left_on_land","victim_emptied"}
        """
        now = int(now or time.time())
        tu = self.user(group_id, to_uid, create=False)
        if not tu:
            return {"ok": False, "reason": "no_farm"}
        lands = list(tu.get("lands") or [])
        if not lands:
            return {"ok": False, "reason": "no_land"}
        cands = self.stealable(group_id, to_uid, now)
        if not cands:
            if any(self.state_of(l, now) == "growing" for l in lands):
                return {"ok": False, "reason": "growing"}
            if any(self.state_of(l, now) == "ready" for l in lands):
                return {"ok": False, "reason": "too_little"}
            return {"ok": False, "reason": "empty"}
        guard = self.guard_rate_of(group_id, to_uid, now)
        bonus = self.steal_bonus_of(group_id, from_uid)
        chance = max(0, min(100,
                            int(self.steal_base_rate) + int(bonus)
                            - int(guard)))
        _tools = self.steal_tools_on(group_id, from_uid)
        _tnames = "、".join(str(x.get("name")) for x in _tools)
        if chance <= 0:
            # ★ 区分「被看门犬压到 0」和「成功率本身就是 0」
            _reason = "guarded" if guard > 0 else "norate"
            return {"ok": False, "reason": _reason, "chance": 0,
                    "guard": guard, "bonus": bonus, "tool_names": _tnames,
                    "guard_name": str(
                        (self.guard_of_user(group_id, to_uid, now) or {})
                        .get("name") or "")}
        roll = random.randint(1, 100)
        if roll > chance:
            # ★ 用户要求：**失败要赔对方积分**（随机区间，默认 1~500）
            try:
                cmin = max(0, int(self.steal_comp_min))
                cmax = max(cmin, int(self.steal_comp_max))
            except Exception:
                cmin, cmax = 1, 500
            comp = random.randint(cmin, cmax) if cmax > 0 else 0
            return {"ok": False, "reason": "failed", "chance": chance,
                    "guard": guard, "roll": roll, "bonus": bonus,
                    "tool_names": _tnames, "comp": int(comp),
                    "comp_min": cmin, "comp_max": cmax,
                    "to_uid": str(to_uid)}
        best = cands[0]
        i = int(best["index"])
        land = lands[i]
        amt = _int(land.get("amount"), 0)
        take = max(1, int(amt * int(self.steal_ratio) / 100))
        take = min(take, amt)
        left = amt - take
        emptied = False
        if left <= 0:
            # ★ 偷空也**保留这块地的品质**（红土 / 黑土不会被偷走）
            lands[i] = {"quality": self.quality_of(land)}
            emptied = True
        else:
            land["amount"] = left
        # 被偷记录（给主人看）
        try:
            log = tu.get("stolen")
            if not isinstance(log, list):
                log = []
            log.append({"by": str(from_uid), "seed": str(best["seed"]),
                        "amount": int(take), "at": now})
            tu["stolen"] = log[-10:]
            tu["stolen_total"] = _int(tu.get("stolen_total"), 0) + int(take)
        except Exception:
            logger.debug("[farm] 写被偷记录失败", exc_info=True)
        su = self.user(group_id, from_uid)
        su["stole_total"] = _int(su.get("stole_total"), 0) + int(take)
        self._steal_mark(group_id, from_uid, now)
        self.save()
        return {"ok": True, "reason": "", "chance": chance, "guard": guard,
                "roll": roll, "target_seed": str(best["seed"]),
                "target_index": i, "take": int(take),
                "left_on_land": int(left), "victim_emptied": emptied,
                "bonus": bonus, "tool_names": _tnames}

    def steal_log(self, group_id, user_id, limit: int = 5) -> list:
        """某人的被偷记录（最近 limit 条，新的在前）"""
        u = self.user(group_id, user_id, create=False)
        log = (u or {}).get("stolen")
        if not isinstance(log, list):
            return []
        out = [x for x in log if isinstance(x, dict)]
        out.reverse()
        return out[:max(1, int(limit or 1))]

    # ── ★ 背包（买来的种子 / 饲料放这里）──────────────────────
    def bag(self, group_id, user_id) -> dict:
        """某人背包：{名字: 数量}（数量 <= 0 的会被清掉）"""
        u = self.user(group_id, user_id)
        b = u.get("bag")
        if not isinstance(b, dict):
            b = {}
            u["bag"] = b
        for k in list(b.keys()):
            try:
                n = int(b.get(k, 0) or 0)
            except Exception:
                n = 0
            if n <= 0:
                b.pop(k, None)
            else:
                b[k] = n
        return b

    def bag_count(self, group_id, user_id, name) -> int:
        return int(self.bag(group_id, user_id).get(str(name or "").strip(), 0)
                   or 0)

    def bag_add(self, group_id, user_id, name, qty=1) -> int:
        """往背包里放东西，返回放完后的数量"""
        nm = str(name or "").strip()
        if not nm:
            return 0
        b = self.bag(group_id, user_id)
        b[nm] = int(b.get(nm, 0) or 0) + max(0, int(qty or 0))
        self.save()
        return b[nm]

    def bag_take(self, group_id, user_id, name, qty=1) -> bool:
        """从背包里拿走东西；不够返回 False（不动数据）"""
        nm = str(name or "").strip()
        n = max(0, int(qty or 0))
        if not nm or n <= 0:
            return False
        b = self.bag(group_id, user_id)
        cur = int(b.get(nm, 0) or 0)
        if cur < n:
            return False
        left = cur - n
        if left > 0:
            b[nm] = left
        else:
            b.pop(nm, None)
        self.save()
        return True

    def bag_seeds(self, group_id, user_id) -> list:
        """
        背包里的**种子**：[{"name","count","seed"(规范化条目)}]，按价格从贵到便宜。
        （只保留后台仍在架的种子）
        """
        b = self.bag(group_id, user_id)
        out = []
        for nm, n in b.items():
            it = self.find_seed(nm)
            if not it:
                continue
            out.append({"name": nm, "count": int(n or 0), "seed": it})
        out.sort(key=lambda x: (-x["seed"]["price"], x["name"]))
        return out

    def bag_feeds(self, group_id, user_id) -> list:
        """背包里的饲料（只保留后台仍在架的）"""
        b = self.bag(group_id, user_id)
        out = []
        for nm, n in b.items():
            it = self.find_feed(nm)
            if not it:
                continue
            out.append({"name": nm, "count": int(n or 0), "item": it})
        out.sort(key=lambda x: (-x["item"]["price"], x["name"]))
        return out

    def bag_feed_total(self, group_id, user_id) -> int:
        """背包里饲料的总份数"""
        return sum(int(x["count"] or 0)
                   for x in self.bag_feeds(group_id, user_id))

    def bag_unknown(self, group_id, user_id) -> list:
        """背包里已经不认识的条目（后台下架了）"""
        b = self.bag(group_id, user_id)
        out = []
        for nm, n in b.items():
            if self.find_seed(nm) or self.find_feed(nm):
                continue
            out.append({"name": nm, "count": int(n or 0)})
        out.sort(key=lambda x: x["name"])
        return out

    def best_seed_for(self, group_id, user_id, level) -> dict:
        """
        自动播种机挑种子：背包里**等级够**的、最贵的那一种。

        返回 {"name","count","seed"}；没有可用种子返回 {}。
        """
        for x in self.bag_seeds(group_id, user_id):
            if self.level_ok(x["seed"], level):
                return x
        return {}

    # ── ★ 农机（自动收割机 / 自动播种机）──────────────────────
    def tools_owned(self, group_id, user_id) -> list:
        """某人拥有的农机：[{"name","item","on"}]"""
        u = self.user(group_id, user_id)
        owned = [str(x) for x in (u.get("tools") or []) if str(x).strip()]
        on = {str(x) for x in (u.get("tools_on") or []) if str(x).strip()}
        out = []
        for nm in owned:
            it = self.find_machine(nm)
            out.append({"name": nm, "item": it or {}, "on": nm in on})
        return out

    def add_tool(self, group_id, user_id, item) -> dict:
        """买一台农机（同一台不能重复买；买了默认就装备上）"""
        u = self.user(group_id, user_id)
        nm = str(item.get("name") or "").strip()
        owned = [str(x) for x in (u.get("tools") or [])]
        if nm in owned:
            return {"ok": False, "reason": "dup"}
        u.setdefault("tools", []).append(nm)
        u["spent"] = _int(u.get("spent"), 0) + int(item.get("price", 0) or 0)
        u.setdefault("tools_on", [])
        if nm not in u["tools_on"]:
            u["tools_on"].append(nm)
        self.save()
        return {"ok": True, "reason": "", "name": nm,
                "count": len(u["tools"]), "on": True}

    def toggle_tool(self, group_id, user_id, name) -> dict:
        """装备 / 卸下一台农机（开关式，一次调用切换状态）"""
        u = self.user(group_id, user_id)
        rec = None
        for r in self.tools_owned(group_id, user_id):
            if r["name"] == name:
                rec = r
                break
        if rec is None:
            return {"ok": False, "reason": "no_such"}
        on = {str(x) for x in (u.get("tools_on") or [])}
        if rec["name"] in on:
            on.discard(rec["name"])
            state = False
        else:
            on.add(rec["name"])
            state = True
        u["tools_on"] = sorted(on)
        self.save()
        return {"ok": True, "reason": "", "name": rec["name"],
                "on": state, "item": rec["item"] or {}}

    def tools_on(self, group_id, user_id) -> list:
        """当前**已装备**的农机条目（后台下架的会忽略）"""
        u = self.user(group_id, user_id)
        on = {str(x) for x in (u.get("tools_on") or []) if str(x).strip()}
        out = []
        for nm in sorted(on):
            it = self.find_machine(nm)
            if it:
                out.append(it)
        return out

    def has_machine(self, group_id, user_id, field: str) -> bool:
        """是否装备了带某个能力的农机（auto_harvest / auto_plant）"""
        for it in self.tools_on(group_id, user_id):
            if int(it.get(field, 0) or 0):
                return True
        return False

    # ── ★ 今日统计（「农场状态」用）────────────────────────────
    def today(self, group_id, user_id, now=None) -> dict:
        """
        某人**今天**的农场统计（跨天自动归零）。

        · harvest_times / harvest_crops / earned  今天收获次数 / 作物数 / 赚了多少
        · planted                                  今天播种了多少作物
        · auto_harvest / auto_plant                其中多少是农机自动干的
        · stolen_times / stolen_amount             今天被偷几次 / 损失多少
        · stole_times / stole_amount               今天偷到几次 / 多少
        """
        u = self.user(group_id, user_id)
        dk = self.day_key(now)
        t = u.get("today")
        if not isinstance(t, dict) or str(t.get("date") or "") != dk:
            t = {"date": dk, "harvest_times": 0, "harvest_crops": 0,
                 "earned": 0, "planted": 0, "auto_harvest": 0,
                 "auto_plant": 0, "stolen_times": 0, "stolen_amount": 0,
                 "stole_times": 0, "stole_amount": 0,
                 "stole_fail": 0, "stole_paid": 0, "comp_got": 0,
                 "stolen_log": []}
            u["today"] = t
        if not isinstance(t.get("stolen_log"), list):
            t["stolen_log"] = []
        return t

    def note(self, group_id, user_id, field: str, delta=1, now=None) -> dict:
        """给今天的某项统计 +delta（自动跨天归零）"""
        t = self.today(group_id, user_id, now)
        try:
            d = int(delta or 0)
        except Exception:
            d = 0
        t[field] = _int(t.get(field), 0) + d
        return t

    def note_stolen(self, group_id, user_id, by, seed, amount, now=None):
        """记一次「被偷」（受害者视角）"""
        t = self.today(group_id, user_id, now)
        t["stolen_times"] = _int(t.get("stolen_times"), 0) + 1
        t["stolen_amount"] = _int(t.get("stolen_amount"), 0) + int(amount or 0)
        t["stolen_log"].append({"by": str(by), "seed": str(seed),
                                "amount": int(amount or 0),
                                "at": int(now or time.time())})
        t["stolen_log"] = t["stolen_log"][-10:]

    # ── ★ 农机每日自动干活（静默，不产生任何群消息）──────────
    def auto_due(self, group_id, user_id, now=None) -> bool:
        """今天还没自动干过活？（没装备任何农机 -> False）"""
        if not self.tools_on(group_id, user_id):
            return False
        u = self.user(group_id, user_id)
        return str(u.get("auto_day") or "") != self.day_key(now)

    def auto_mark(self, group_id, user_id, now=None) -> None:
        u = self.user(group_id, user_id)
        u["auto_day"] = self.day_key(now)
        self.save()

    def all_user_keys(self) -> list:
        """所有农场存档的 (群号, QQ) —— 给后台定时任务遍历用"""
        out = []
        for key in list(self.users.keys()):
            if not isinstance(key, str) or ":" not in key:
                continue
            gid, uid = key.split(":", 1)
            if gid and uid:
                out.append((gid, uid))
        return out
