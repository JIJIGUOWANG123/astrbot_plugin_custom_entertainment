import os
import json
from astrbot.api import logger

try:
    from .path_utils import title_bar as _title_bar
except Exception:
    def _title_bar(t):
        return str(t)

try:
    from .path_utils import title_foot as _title_foot
except Exception:
    def _title_foot():
        return "─" * 20

FEATURE_ALIAS = {
    "报时功能": "chime",
    "报时推送": "chime",
    "MC系统": "minecraft",
    "接口系统": "meme",
    "签到系统": "checkin",
    "打卡系统": "greeting",
    "图鉴系统": "bird",
    "管理系统": "admin",
    "银行功能": "bank",
    "打工功能": "job",
    "赛车功能": "racing",
    "坐骑功能": "mount",
    "神偷功能": "steal",
    "签到商店": "shop",
    "运势功能": "fortune",
    "老公功能": "husband",
    "人品功能": "luck",
    "天气查询": "weather",
    "油价查询": "oil",
    "互动功能": "poke",
    "群员管理": "member",
    "积分调整": "score_admin",
    "猜数功能": "guess",
    "整点报时": "chime",
    "状态推送": "push",
    "读报推送": "news_auto",
    "电脑状态": "status",
    "状态功能": "status",
    "MC推送": "minecraft",
    "词库系统": "word_reply",
    "娱乐功能": "entertain",
    "全局尾巴": "reply_tail",
    "输出模式": "output_mode",
    "运势": "fortune",
    "今日运势": "fortune",
    "fortune": "fortune",
    "老公": "husband",
    "今日老公": "husband",
    "husband": "husband",
    "人品": "luck",
    "今日人品": "luck",
    "luck": "luck",
    "词库": "word_reply",
    "word_reply": "word_reply",
    "表情": "meme",
    "表情包": "meme",
    "meme": "meme",
    "电脑状态": "status",
    "状态": "status",
    "status": "status",
    "早安": "greeting",
    "晚安": "greeting",
    "早安晚安": "greeting",
    "greeting": "greeting",
    "mc": "minecraft",
    "MC": "minecraft",
    "minecraft": "minecraft",
    "MC版本": "minecraft",
    "mc版本": "minecraft",
    "签到": "checkin",
    "checkin": "checkin",
    "神偷": "steal",
    "偷积分": "steal",
    "steal": "steal",
    "银行": "bank",
    "bank": "bank",
    "坐骑": "mount",
    "mount": "mount",
    "打工": "job",
    "job": "job",
    "整点报时": "chime",
    "报时": "chime",
    "chime": "chime",
    "读报功能": "news",
    "手动读报": "news",
    "news": "news",
    "状态推送": "push",
    "定时状态推送": "push",
    "push": "push",
    "每日读报": "news_auto",
    "读报": "news_auto",
    "news_auto": "news_auto",
    "发言功能": "speak_rank",
    "发言排行": "speak_rank",
    "发言统计": "speak_rank",
    "活跃排行": "speak_rank",
    "上周排行": "speak_rank",
    "上月排行": "speak_rank",
    "speak_rank": "speak_rank",
    "宠物功能": "pet",
    "宠物": "pet",
    "pet": "pet",
    "积分调整": "score_admin",
    "加积分": "score_admin",
    "减积分": "score_admin",
    "作弊": "score_admin",
    "score_admin": "score_admin",
    "音乐功能": "music",
    "音乐": "music",
    "点歌": "music",
    "music": "music",
    "小鸟图鉴": "bird",
    "小鸟": "bird",
    "图鉴": "bird",
    "bird": "bird",
    "猜拳功能": "rps",
    "猜拳": "rps",
    "rps": "rps",
    "猜数功能": "guess",
    "猜数游戏": "guess",
    "猜数": "guess",
    "猜数字": "guess",
    "guess": "guess",
    "名片功能": "card",
    "名片": "card",
    "card": "card",
    "查询功能": "qqinfo",
    "查询": "qqinfo",
    "查QQ": "qqinfo",
    "qqinfo": "qqinfo",
    "B站功能": "bili",
    "B站": "bili",
    "b站功能": "bili",
    "b站": "bili",
    "哔哩哔哩": "bili",
    "bili": "bili",
    "抽奖功能": "lottery",
    "抽奖": "lottery",
    "lottery": "lottery",
    "抢劫功能": "rob",
    "抢劫": "rob",
    "抢银行": "rob",
    "抢劫银行": "rob",
    "打劫银行": "rob",
    "rob": "rob",
    "奖池功能": "lottery_pool",
    "奖池": "lottery_pool",
    "参与抽奖": "lottery_pool",
    "lottery_pool": "lottery_pool",
    "群管系统": "group_admin",
    "群管": "group_admin",
    "group_admin": "group_admin",
}

FEATURE_LABEL = {
    "bird": "小鸟图鉴",
    "fortune": "今日运势",
    "husband": "今日老公",
    "luck": "今日人品",
    "word_reply": "词库系统",
    "meme": "接口系统",
    "status": "电脑状态",
    "greeting": "打卡系统",
    "minecraft": "MC系统",
    "checkin": "签到系统",
    "steal": "神偷功能",
    "bank": "银行功能",
    "mount": "坐骑功能",
    "job": "打工功能",
    "chime": "整点报时",
    "news": "读报功能",
    "push": "状态推送",
    "news_auto": "每日读报",
    "speak_rank": "发言功能",
    "pet": "宠物功能",
    "score_admin": "积分调整",
    "music": "音乐功能",
    "rps": "猜拳功能",
    "guess": "猜数功能",
    "card": "名片功能",
    "qqinfo": "查询功能",
    "lottery": "抽奖功能",
    "rob": "抢劫功能",
    "lottery_pool": "奖池功能",
    "group_admin": "群管系统",
    "bili": "B站功能",
    "entertain": "娱乐功能",
    "checkin": "签到系统",
    "admin": "管理系统",
    "shop": "签到商店",
    "weather": "天气查询",
    "oil": "油价查询",
    "poke": "互动功能",
    "member": "群员管理",
    "output_mode": "输出模式",
    "reply_tail": "全局尾巴",
    "racing": "赛车功能",
}

CHECKIN_SUB_FEATURES = ("steal", "bank", "mount", "job", "lottery", "rob",
                        "lottery_pool", "pet", "guess", "shop",
                        "score_admin")

PARENT_FEATURE = {
    "steal": "checkin",
    "bank": "checkin",
    "mount": "checkin",
    "job": "checkin",
    "lottery": "checkin",
    "rob": "checkin",
    "lottery_pool": "checkin",
    "pet": "checkin",
    "racing": "checkin",
    "rps": "checkin",
    "guess": "checkin",
    "shop": "checkin",
    "score_admin": "checkin",
    "music": "entertain",
    "card": "entertain",
    "bili": "entertain",
    "luck": "entertain",
    "husband": "entertain",
    "fortune": "entertain",
    "qqinfo": "entertain",
    "speak_rank": "entertain",
}

FEATURE_EXTRA_PARENT = {
    "lottery_pool": "lottery",
}

class AdminCommands:
    def __init__(self, plugin):
        self.plugin = plugin

    @staticmethod
    def _sender_id(event) -> str:
        try:
            return str(event.get_sender_id() or "")
        except Exception:
            return ""

    async def check_admin(self, event) -> bool:
        p = self.plugin
        user_id = self._sender_id(event)
        if not user_id:
            return False
        try:
            if event.is_admin():
                return True
        except Exception:
            pass
        if user_id in p.plugin_admins:
            return True
        if p.group_admin_module is not None:
            try:
                return await p.group_admin_module.is_group_admin(event, user_id)
            except Exception:
                logger.exception("[apix-meme] 群管判定失败")
                return False
        return False

    def add_plugin_admin(self, qq: str) -> bool:
        p = self.plugin
        qq = str(qq).strip()
        if not qq:
            return False
        if qq not in p.plugin_admins:
            p.plugin_admins.append(qq)
            p.save_rules_to_config()
        return True

    def del_plugin_admin(self, qq: str) -> bool:
        p = self.plugin
        qq = str(qq).strip()
        if qq in p.plugin_admins:
            p.plugin_admins.remove(qq)
            p.save_rules_to_config()
            return True
        return False

    def list_plugin_admins(self) -> list:
        return list(self.plugin.plugin_admins)

    def is_feature_on(self, key: str) -> bool:
        return True

    FEATURE_GROUP_KEY = {
        "fortune":    "fortune_groups",
        "husband":    "husband_groups",
        "luck":       "luck_groups",
        "word_reply": "word_reply_groups",
        "greeting":   "greeting_groups",
        "checkin":    "checkin_groups",
        "steal":      "steal_groups",
        "bank":       "bank_groups",
        "mount":      "mount_groups",
        "job":        "job_groups",
        "meme":       "meme_groups",
        "status":     "status_groups",
        "chime":      "hourly_chime_groups",
        "news":       "daily_news_groups",
        "push":       "daily_status_push_groups",
        "news_auto":  "daily_news_auto_groups",
        "bird":       "bird_groups",
        "speak_rank": "speak_stats_groups",
        "pet":        "pet_groups",
        "score_admin": "score_admin_groups",
        "music":      "music_groups",
        "rps":        "rps_groups",
        "guess":      "guess_groups",
        "minecraft":  "minecraft_groups",
        "card":       "card_groups",
        "qqinfo":     "qqinfo_groups",
        "lottery":    "lottery_groups",
        "rob":        "rob_groups",
        "lottery_pool": "lottery_pool_groups",
        "group_admin": "group_admin_groups",
        "bili":       "bili_groups",
        "chime":      "hourly_chime_groups",
        "news_auto":  "daily_news_auto_groups",
        "entertain":  "entertain_groups",
        "admin":      "admin_groups",
        "racing":     "racing_groups",
        "shop":       "shop_groups",
        "weather":    "weather_groups",
        "oil":        "oil_groups",
        "poke":       "poke_groups",
        "member":     "member_groups",
    }

    WHITELIST_FEATURES = {"chime", "push", "news_auto", "group_admin"}

    @staticmethod
    def _parse_gids(raw) -> list:
        """把配置里的群号串解析成列表（支持逗号/换行/中文逗号等）"""
        import re as _re
        if not raw:
            return []
        out, seen = [], set()
        for piece in _re.split(r"[,，、;；\s]+", str(raw)):
            gid = piece.strip()
            if gid and gid.isdigit() and gid not in seen:
                seen.add(gid)
                out.append(gid)
        return out

    def _set_feature_group_block(self, feat: str, gid: str, enabled: bool) -> bool:
        """
        把某群加入/移出「该功能的群号名单」，并立即重载使配置生效。

        名单语义分两种（见 WHITELIST_FEATURES）：
          · 白名单（报时/读报/推送/电脑状态）：enabled=True 加入名单，False 移除
          · 黑名单（其余功能）              ：enabled=True 移除，False 加入
        返回是否真的改动了。
        """
        import os
        import json as _json
        p = self.plugin
        cfg_key = self.FEATURE_GROUP_KEY.get(feat)
        if not cfg_key:
            return False
        try:
            from .path_utils import config_path as _cp
        except Exception:
            try:
                from path_utils import config_path as _cp
            except Exception:
                logger.exception("[apix-meme] 定位配置文件失败")
                return False
        try:
            path = _cp()
        except Exception:
            logger.exception("[apix-meme] 获取配置路径失败")
            return False

        cfg = {}
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8-sig") as f:
                    cfg = _json.load(f) or {}
        except Exception:
            cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}

        gids = self._parse_gids(cfg.get(cfg_key, ""))
        want_in = bool(enabled) if feat in self.WHITELIST_FEATURES else (not enabled)
        changed = False
        if want_in:
            if gid not in gids:
                gids.append(gid)
                changed = True
        else:
            if gid in gids:
                gids.remove(gid)
                changed = True

        new_text = ",".join(gids)
        if str(cfg.get(cfg_key, "") or "") != new_text:
            changed = True
        cfg[cfg_key] = new_text
        try:
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                _json.dump(cfg, f, ensure_ascii=False, indent=2)
            os.replace(path + ".tmp", path)
        except Exception:
            logger.exception("[apix-meme] 写入功能群号失败")
            return False

        try:
            p.reload_cfg()
        except Exception:
            logger.exception("[apix-meme] 关闭功能后重载配置失败")
        return changed

    def disable_feature(self, name: str, group_id: str = None, target_group: str = None) -> str:
        key = self._resolve_feature(name)
        if not key:
            return (f"⚠️未知功能：{name}\n"
                    f"可用：{' / '.join(FEATURE_LABEL.values())}\n"
                    f"💡 各功能自己配置的触发词也能用")
        p = self.plugin

        gid = None
        if target_group:
            gid = str(target_group).strip()
        elif group_id:
            gid = str(group_id).strip()

        if not gid:
            return "⚠️ 请在群聊中使用该指令，或指定群号：关闭功能 运势 群号"

        label = FEATURE_LABEL.get(key, key)
        if key not in self.FEATURE_GROUP_KEY:
            return (f"⚠️「{label}」暂不支持按群开关\n"
                    f"💡 请在后台直接配置该功能的群号")

        self._set_feature_group_block(key, gid, False)
        if gid not in p.group_feature_switches:
            p.group_feature_switches[gid] = {}
        p.group_feature_switches[gid][key] = False
        p.save_rules_to_config()

        scope = f"群【{gid}】" if target_group else "本群"
        note = ("已把本群号从后台生效名单移除，该功能所有指令都不再响应"
                if key in self.WHITELIST_FEATURES
                else "已把群号写入后台群号黑名单，该功能所有指令都不再响应")
        return (f"❌已关闭{scope}：{label}\n"
                f"💡 {note}")

    @staticmethod
    def _group_in_list(gids, target) -> bool:
        """群号黑名单是否命中 target（留空视为不排除任何群）。"""
        try:
            items = [str(x) for x in (gids or [])]
        except Exception:
            return False
        if not items:
            return False
        return str(target) in items

    def primary_labels(self) -> dict:
        """
        返回 {feature_key: 展示名}，展示名取「后台配置的第一个指令关键词」。
        没配到就回退到内置的 FEATURE_LABEL，保证任何情况下都有名字可显示。
        每次调用都重新读取，改了配置立刻生效。
        """
        p = self.plugin

        def first(*candidates):
            for words in candidates:
                for w in (words or []):
                    w = str(w).strip()
                    if w:
                        return w
            return ""

        def names(attr, mod_attr):
            mod = getattr(p, mod_attr, None)
            if mod is None:
                return []
            val = getattr(mod, attr, None)
            return [str(x) for x in val] if isinstance(val, (list, tuple)) else []

        def cmd_names(attr):
            raw = getattr(p, attr, "") or ""
            if isinstance(raw, (list, tuple)):
                return [str(x) for x in raw if str(x).strip()]
            return [x for x in str(raw).split(",") if x.strip()]

        got = {
            "fortune":    first(names("trigger_names", "fortune_module")),
            "husband":    first(names("trigger_names", "husband_module")),
            "luck":       first(names("trigger_names", "luck_module")),
            "word_reply": first(cmd_names("cmd_word_add_name"),
                                cmd_names("cmd_word_list_name")),
            "greeting":   first(names("morning_names", "greeting_module")),
            "checkin":    first(names("trigger_names", "checkin_module")),
            "steal":      first(names("steal_trigger_names", "checkin_module")),
            "bank":       first(names("bank_trigger_names", "checkin_module")),
            "mount":      first(names("mount_trigger_names", "checkin_module")),
            "job":        first(names("job_trigger_names", "checkin_module")),
            "minecraft":  first(cmd_names("cmd_mc_menu_name")),
            "meme":       first(cmd_names("cmd_meme_image_name"),
                                cmd_names("cmd_meme_menu_name")),
            "status":     first(cmd_names("cmd_status_names")),
        }
        night_first = first(names("night_names", "greeting_module"))
        if got.get("greeting") and night_first:
            got["greeting"] = f"{got['greeting']}/{night_first}"

        out = {}
        for key, label in FEATURE_LABEL.items():
            out[key] = got.get(key) or label
        return out

    def _runtime_alias(self) -> dict:
        """
        从各功能模块读取「实际配置的触发词」，动态并入别名表。
        这样把运势触发词配成「抽签」后，就能直接用「关闭功能 抽签」。
        每次调用都重新读取，改了配置立刻生效，不需要重载插件。
        """
        p = self.plugin
        alias = {}

        def add(key, words):
            for w in (words or []):
                w = str(w).strip()
                if w:
                    alias[w] = key

        def names(attr, mod_attr):
            """取模块的 trigger_names / *__trigger_names__"""
            mod = getattr(p, mod_attr, None)
            if mod is None:
                return []
            val = getattr(mod, attr, None)
            return list(val) if isinstance(val, (list, tuple)) else []

        def cmd_names(attr):
            raw = getattr(p, attr, "") or ""
            if isinstance(raw, (list, tuple)):
                return [str(x).strip() for x in raw if str(x).strip()]
            return [x.strip() for x in str(raw).split(",") if x.strip()]

        add("fortune",    names("trigger_names", "fortune_module"))
        add("husband",    names("trigger_names", "husband_module"))
        add("luck",       names("trigger_names", "luck_module"))
        add("word_reply", cmd_names("cmd_word_add_name"))
        add("word_reply", cmd_names("cmd_word_del_name"))
        add("word_reply", cmd_names("cmd_word_list_name"))

        add("greeting",   names("morning_names", "greeting_module"))
        add("greeting",   names("night_names", "greeting_module"))
        add("greeting",   cmd_names("cmd_greeting_stats_name"))
        add("greeting",   cmd_names("cmd_greeting_rank_name"))

        add("checkin",    names("trigger_names", "checkin_module"))
        add("checkin",    names("info_trigger_names", "checkin_module"))
        add("steal",      names("steal_trigger_names", "checkin_module"))
        add("bank",       names("bank_trigger_names", "checkin_module"))
        add("bank",       names("bank_deposit_names", "checkin_module"))
        add("bank",       names("bank_withdraw_names", "checkin_module"))
        add("mount",      names("mount_trigger_names", "checkin_module"))
        add("mount",      names("mount_list_trigger_names", "checkin_module"))
        add("mount",      names("mount_buy_trigger_names", "checkin_module"))
        add("mount",      names("mount_equip_names", "checkin_module"))
        add("mount",      names("mount_view_names", "checkin_module"))
        add("job",        names("job_trigger_names", "checkin_module"))
        add("job",        names("job_info_trigger_names", "checkin_module"))

        add("minecraft",  cmd_names("cmd_mc_menu_name"))
        add("meme",       cmd_names("cmd_meme_menu_name"))
        add("meme",       cmd_names("cmd_meme_image_name"))
        add("meme",       cmd_names("cmd_meme_video_name"))
        add("status",     cmd_names("cmd_status_names"))
        add("bird",       cmd_names("cmd_bird_menu"))
        add("bird",       cmd_names("cmd_bird"))
        add("bird",       cmd_names("cmd_gallery"))
        add("bird",       cmd_names("cmd_bird_list"))
        add("bird",       cmd_names("cmd_bird_view"))
        for _k, _label in FEATURE_LABEL.items():
            add(_k, [_label])
        return alias

    def _resolve_feature(self, name: str):
        """把用户输入的功能名解析成 feature key。"""
        text = str(name or "").strip()
        if not text:
            return None
        for _k, _label in FEATURE_LABEL.items():
            if text == _label:
                return _k
        key = FEATURE_ALIAS.get(text)
        if key:
            return key
        return self._runtime_alias().get(text)

    def enable_feature(self, name: str, group_id: str = None, target_group: str = None) -> str:
        key = self._resolve_feature(name)
        if not key:
            return (f"⚠️未知功能：{name}\n"
                    f"可用：{' / '.join(FEATURE_LABEL.values())}\n"
                    f"💡 各功能自己配置的触发词也能用")
        p = self.plugin

        gid = None
        if target_group:
            gid = str(target_group).strip()
        elif group_id:
            gid = str(group_id).strip()

        if not gid:
            return "⚠️ 请在群聊中使用该指令，或指定群号：开启功能 运势 群号"

        label = FEATURE_LABEL.get(key, key)
        if key not in self.FEATURE_GROUP_KEY:
            return (f"⚠️「{label}」暂不支持按群开关\n"
                    f"💡 请在后台直接配置该功能的群号")

        self._set_feature_group_block(key, gid, True)
        if gid not in p.group_feature_switches:
            p.group_feature_switches[gid] = {}
        p.group_feature_switches[gid][key] = True
        p.save_rules_to_config()

        scope = f"群【{gid}】" if target_group else "本群"
        note = ("已把本群号加入后台生效名单，该功能指令恢复响应"
                if key in self.WHITELIST_FEATURES
                else "已从后台群号黑名单中移除，该功能指令恢复响应")
        return (f"✅已开启{scope}：{label}\n"
                f"💡 {note}")

    def _sub_on(self, mod_attr: str, gid: str,
                list_attr: str = "enabled_groups") -> bool:
        """群号订阅类功能的「本群是否已开」"""
        m = getattr(self.plugin, mod_attr, None)
        if m is None:
            return False
        try:
            return bool(m.is_group_on(gid))
        except Exception:
            pass
        try:
            return gid in [str(x) for x in (getattr(m, list_attr, None) or [])]
        except Exception:
            return False

    def feature_status_text(self, group_id: str = None,
                            target_group: str = None,
                            page: int = 1) -> str:
        """
        ★★ 1.7：本群功能状态（**简约版**）。

        版式（用户指定）：
            📋 功能状态
            ━━━━━━━━━━━━━━━━━━━━
            状态推送[开启] 读报推送[开启] 报时推送[开启]
            ...
            （每行三个，一屏看完，**不分页**）

            💡 开启功能 XX  开启本群指定功能
            💡 关闭功能 XX  关闭本群指定功能

        · `page` 参数仅为兼容旧调用保留，现在不再分页。
        · 状态判定综合：**父系统门禁** + 后台功能开关 + 本群开关 + 群号名单。
          ★ 父系统（如签到）关掉时，它的子功能（银行 / 打工 / 坐骑 /
            赛车 / 宠物 / 抽奖 / 抢劫 / 奖池…）即使自己开着也**不生效**，
            所以这里一律显示「关闭」—— 与分发代码同口径，避免
            「显示开启却用不了」的错觉。归属见 `PARENT_FEATURE`。
        """
        p = self.plugin
        gid = ""
        if target_group:
            gid = str(target_group).strip()
        elif group_id:
            gid = str(group_id).strip()
        if not gid:
            return "⚠️ 请在群聊中使用该指令，或指定群号：功能状态 群号"

        group_cfg = p.group_feature_switches.get(gid, {}) or {}
        ck_mod = getattr(p, "checkin_module", None)

        def _ck(attr):
            if ck_mod is None:
                return True
            return bool(getattr(ck_mod, attr, True))

        def _mod(mod_attr):
            m = getattr(p, mod_attr, None)
            if m is None:
                return True
            return bool(getattr(m, "enable", True))

        glob = {
            "fortune": _mod("fortune_module"),
            "husband": _mod("husband_module"),
            "luck": _mod("luck_module"),
            "word_reply": _mod("word_reply_module"),
            "meme": bool(getattr(p, "meme_enable", True)),
            "status": bool(getattr(p, "status_enable", True)),
            "greeting": _mod("greeting_module"),
            "minecraft": _mod("minecraft_module"),
            "checkin": _mod("checkin_module"),
            "steal": _ck("steal_enable"),
            "bank": _ck("bank_enable"),
            "mount": _ck("mount_enable"),
            "job": _ck("job_enable"),
            "lottery": _ck("lottery_enable"),
            "rob": _ck("rob_enable"),
            "lottery_pool": _ck("lottery_pool_enable"),
            "speak_rank": bool(getattr(
                getattr(p, "speak_stats_module", None), "enable", True)),
            "pet": bool(getattr(
                getattr(p, "pet_module", None), "enable", True)),
            "score_admin": bool(getattr(p, "score_admin_enable", True)),
            "music": bool(getattr(
                getattr(p, "music_module", None), "enable", True)),
            "bird": bool(getattr(p, "bird_enable", True)),
            "bili": bool(getattr(p, "bili_enable", True)),
            "guess": bool(getattr(p, "guess_enable", True)),
            "group_admin": True,
            "card": True,
            "qqinfo": bool(getattr(p, "qqinfo_enable", True)),
            "entertain": bool(group_cfg.get("entertain", True)),
            "weather": bool(group_cfg.get("weather", True)),
            "oil": bool(group_cfg.get("oil", True)),
            "poke": bool(group_cfg.get("poke", True)),
            "member": bool(group_cfg.get("member", True)),
        }

        def _in(lst):
            return self._group_in_list(lst, gid)

        blk = {
            "status": _in(getattr(p, "status_groups", None)),
            "news": _in(getattr(getattr(p, "daily_news_module", None),
                                "manual_groups", None)),
            "speak_rank": _in(getattr(getattr(p, "speak_stats_module", None),
                                      "allowed_groups", None)),
            "pet": _in(getattr(getattr(p, "pet_module", None),
                               "allowed_groups", None)),
            "score_admin": _in(getattr(p, "score_admin_groups", None)),
            "music": _in(getattr(getattr(p, "music_module", None),
                                 "allowed_groups", None)),
            "bird": _in(getattr(p, "bird_groups", None)),
            "meme": _in(getattr(p, "meme_groups", None)),
            "news": not bool(getattr(
                getattr(p, "daily_news_module", None),
                "is_manual_allowed", lambda _g: True)(gid)),
            "entertain": _in(getattr(p, "entertain_groups", None)),
            "racing": _in(getattr(p, "racing_groups", None)),
            "shop": _in(getattr(p, "shop_groups", None)),
            "weather": _in(getattr(p, "weather_groups", None)),
            "oil": _in(getattr(p, "oil_groups", None)),
            "poke": _in(getattr(p, "poke_groups", None)),
            "member": _in(getattr(p, "member_groups", None)),
        }
        white = {
            "group_admin": _in(getattr(p, "group_admin_groups", None)),
            "chime": self._sub_on("hourly_chime_module", gid),
            "push": self._sub_on("status_push_module", gid),
            "news_auto": self._sub_on("daily_news_module", gid, "auto_groups"),
        }
        _mc = getattr(p, "minecraft_module", None)
        mc_auto = False
        if _mc is not None:
            try:
                mc_auto = bool(_mc.is_group_auto(gid))
            except Exception:
                mc_auto = False

        def _on(key, kind="normal"):
            """
            综合判定：**父系统** + 全局停用 + 名单排除 + 本群关掉。

            ★★ 1.7：先判父系统 —— 父系统（如签到）关掉时，子功能
              （银行 / 打工 / 坐骑 / 宠物…）即使自己开着也不生效，
              分发代码就是先判父再判子。这里必须同口径，否则
              状态显示会误导用户。
            """
            if kind == "always":
                return True
            _p = PARENT_FEATURE.get(key)
            if _p and not _on(_p, "normal"):
                return False
            _ep = FEATURE_EXTRA_PARENT.get(key)
            if _ep and not _on(_ep, "normal"):
                return False
            if not glob.get(key, True):
                return False
            if kind == "white":
                if not white.get(key, False):
                    return False
                return bool(group_cfg.get(key, True))
            if kind == "subscribe":
                return bool(white.get(key, False))
            if blk.get(key, False):
                return False
            return bool(group_cfg.get(key, True))

        ITEMS = [
            ("状态推送", "push", "subscribe"),
            ("读报推送", "news_auto", "subscribe"),
            ("报时推送", "chime", "subscribe"),
            ("MC推送", "minecraft", "mc"),
            ("状态功能", "status", "normal"),
            ("读报功能", "news", "normal"),
            ("娱乐功能", "entertain", "normal"),
            ("音乐功能", "music", "normal"),
            ("MC系统", "minecraft", "normal"),
            ("接口系统", "meme", "normal"),
            ("签到系统", "checkin", "normal"),
            ("打卡系统", "greeting", "normal"),
            ("图鉴系统", "bird", "normal"),
            ("群管系统", "group_admin", "white"),
            ("管理系统", "admin", "normal"),
            ("词库系统", "word_reply", "normal"),
            ("宠物功能", "pet", "normal"),
            ("银行功能", "bank", "normal"),
            ("打工功能", "job", "normal"),
            ("赛车功能", "racing", "normal"),
            ("坐骑功能", "mount", "normal"),
            ("神偷功能", "steal", "normal"),
            ("抽奖功能", "lottery", "normal"),
            ("抢劫功能", "rob", "normal"),
            ("签到商店", "shop", "normal"),
            ("运势功能", "fortune", "normal"),
            ("老公功能", "husband", "normal"),
            ("人品功能", "luck", "normal"),
            ("查询功能", "qqinfo", "normal"),
            ("名片功能", "card", "normal"),
            ("B站功能", "bili", "normal"),
            ("小鸟图鉴", "bird", "normal"),
            ("猜数功能", "guess", "normal"),
            ("奖池功能", "lottery_pool", "normal"),
            ("天气查询", "weather", "normal"),
            ("油价查询", "oil", "normal"),
            ("互动功能", "poke", "normal"),
            ("发言功能", "speak_rank", "normal"),
            ("积分调整", "score_admin", "normal"),
            ("群员管理", "member", "normal"),
        ]

        cells = []
        for label, key, kind in ITEMS:
            if kind == "always":
                on, blocked = True, False
            elif kind == "mc":
                on = bool(glob.get("minecraft", True)) and mc_auto
                blocked = False
            else:
                on = _on(key, kind)
                blocked = bool(
                    not on
                    and (PARENT_FEATURE.get(key)
                         or FEATURE_EXTRA_PARENT.get(key))
                    and glob.get(key, True)
                    and bool(group_cfg.get(key, True))
                    and not blk.get(key, False))
            cells.append(f"{label}［{'✅' if on else '❌'}］")

        PER = 3
        SEP = "　"

        CELL = 5
        lines = [f"{_title_bar('功能状态')}", ""]
        for i in range(0, len(cells), PER):
            row = cells[i:i + PER]
            parts = []
            for j, c in enumerate(row):
                if j == len(row) - 1:
                    parts.append(c)
                    continue
                used = 0
                for ch in c:
                    used += 0 if ord(ch) == 0xFE0F else (
                        1 if ord(ch) > 0x2000 else 0.5)
                pad = max(1, int(round(CELL - used)))
                parts.append(c + SEP * pad)
            lines.append("".join(parts).rstrip())
        lines.append("")
        lines.append("💡 开启功能 XX  开启本群指定功能")
        lines.append("💡 关闭功能 XX  关闭本群指定功能")
        lines.append(_title_foot())

        try:
            _w = 0
            for l in lines:
                _c = 0.0
                for ch in l:
                    if ord(ch) == 0xFE0F:
                        continue
                    _c += 1.0 if ord(ch) > 0x2000 else 0.5
                _w = max(_w, _c)
            self.LAST_WIDTH = max(760, int(_w * 16.5) + 40)
        except Exception:
            self.LAST_WIDTH = 760
        return "\n".join(lines)

    def set_group_must_at_bot(self, group_id: str, enabled: bool) -> str:
        p = self.plugin
        gid = str(group_id).strip()
        if not gid:
            return "⚠️ 请指定群号"
        if not hasattr(p, "group_must_at_bot") or p.group_must_at_bot is None:
            p.group_must_at_bot = {}
        p.group_must_at_bot[gid] = bool(enabled)
        p.save_rules_to_config()
        state = "开启" if enabled else "关闭"
        return f"✅ 已{state}群【{gid}】的「必须艾特机器人」限制"

    def get_group_must_at_bot(self, group_id: str) -> bool:
        p = self.plugin
        gid = str(group_id).strip()
        if not gid:
            return bool(p.must_at_bot)
        if hasattr(p, "group_must_at_bot") and gid in p.group_must_at_bot:
            return bool(p.group_must_at_bot[gid])
        return bool(p.must_at_bot)

    def must_at_bot_status_text(self, group_id: str = None, target_group: str = None) -> str:
        p = self.plugin
        gid = None
        if target_group:
            gid = str(target_group).strip()
        elif group_id:
            gid = str(group_id).strip()
        if not gid:
            return "⚠️ 请在群聊中使用该指令，或指定群号：必须艾特状态 群号"
        enabled = self.get_group_must_at_bot(gid)
        global_default = bool(p.must_at_bot)
        is_override = hasattr(p, "group_must_at_bot") and gid in p.group_must_at_bot
        lines = [
            _title_bar(f"群 {gid} 必须艾特机器人状态"),
            f"当前状态：{'✅ 开启（必须 @机器人 才响应）' if enabled else '❌ 关闭（无需 @机器人）'}",
            f"来源：{'本群独立设置' if is_override else '跟随全局默认'}",
            f"全局默认：{'开启' if global_default else '关闭'}",
            "",
            "💡 用「必须艾特开 [群号]」「必须艾特关 [群号]」调整",
        ]
        return "\n".join(lines)

    def word_add(self, args: str) -> str:
        p = self.plugin
        if p.word_reply_module is None:
            logger.warning("[apix-meme] 词库模块未加载，忽略该指令")
            return ""
        if "|" not in args:
            return f"用法：{p.cmd_word_add_name} 关键词1,关键词2 | 回复内容"
        kw_part, reply_part = args.split("|", 1)
        kw_part = kw_part.strip()
        reply_part = reply_part.strip()
        if not kw_part or not reply_part:
            return "关键词和回复内容都不能为空"
        keywords = [k.strip() for k in kw_part.split(",") if k.strip()]
        if not keywords:
            return "关键词不能为空"
        p.word_reply_module.rules.append({
            "keywords": keywords,
            "reply": reply_part,
        })
        p.save_rules_to_config()
        return f"✅词库已添加\n关键词：{','.join(keywords)}\n回复：{reply_part}"

    def word_del(self, args: str) -> str:
        p = self.plugin
        if p.word_reply_module is None:
            logger.warning("[apix-meme] 词库模块未加载，忽略该指令")
            return ""
        if not args.isdigit():
            return f"用法：{p.cmd_word_del_name} 序号"
        idx = int(args) - 1
        if idx < 0 or idx >= len(p.word_reply_module.rules):
            return "⚠️序号不存在"
        removed = p.word_reply_module.rules.pop(idx)
        p.save_rules_to_config()
        return f"✅已删除词库：{'/'.join(removed['keywords'])}"

    def word_list_text(self) -> str:
        p = self.plugin
        if p.word_reply_module is None:
            logger.warning("[apix-meme] 词库模块未加载，忽略该指令")
            return ""
        rules = p.word_reply_module.rules
        if not rules:
            return "词库为空"
        lines = [_title_bar(f"词库列表（共 {len(rules)} 条）")]
        for i, rule in enumerate(rules, start=1):
            kw = " ☆ ".join(rule["keywords"])
            reply = rule["reply"].replace("\n", " ")
            if len(reply) > 20:
                reply = reply[:20] + "..."
            lines.append(f"{i}：{kw} → {reply}")
        return "\n".join(lines)

    def group_disable(self, group_id: str) -> str:
        p = self.plugin
        if not group_id:
            return "私聊无法关闭"
        if group_id in p.disabled_groups:
            return "本群已经处于关闭状态"
        p.disabled_groups.append(group_id)
        p.save_rules_to_config()
        return "✅本群娱乐系统已关闭"

    def group_enable(self, group_id: str) -> str:
        p = self.plugin
        if not group_id:
            return "私聊无法开启"
        if group_id in p.disabled_groups:
            p.disabled_groups.remove(group_id)
            p.save_rules_to_config()
        return "✅本群娱乐系统已开启"

    @staticmethod
    def _sync_rules(p):
        """
        改规则前先把 self.rules 与磁盘对齐。
        因为 reload_cfg() 每次都会按磁盘顺序重建 self.rules（未保存的修改会被
        挂到列表末尾），若直接用内存里的序号增删，用户看到列表和实际序号可能不一致。
        """
        try:
            if not getattr(p, "_dirty", False):
                p.reload_cfg()
        except Exception:
            logger.exception("[apix-meme] 同步规则列表失败")

    def meme_add(self, args: str) -> str:
        p = self.plugin
        if not args:
            return (
                f"用法1（单参数）：{p.cmd_add_name} 关键词1,关键词2,URL模板\n"
                f"用法2（多参数）：{p.cmd_add_name} 关键词1,关键词2 | URL模板 | 参数个数\n"
                f"用法3（指定返回类型）：{p.cmd_add_name} 关键词 | URL模板 | 参数个数 | video\n"
                f"用法4（接口返回 JSON）：{p.cmd_add_name} 关键词 | URL模板 | 参数个数 | video | data.video\n"
                f"　 （参数个数填 0 表示无参数接口）\n"
                f"　 （返回类型：image=图片 / video=视频 / text=文本，默认 image）\n"
                f"　 （text 文本接口支持纯文本、document.write(\"...\") 与 JSON 取字段）\n"
                f"　 （最后一段填 JSON 里媒体地址 / 文本内容的字段路径，留空则自动搜索）"
            )

        self._sync_rules(p)

        if "|" in args:
            parts = [x.strip() for x in args.split("|")]
            if len(parts) < 2 or len(parts) > 5:
                return "格式错误！多参数格式：关键词 | URL | 参数个数 | 返回类型 | JSON字段"
            kw_str = parts[0]
            url_template = parts[1]
            param_count = parts[2] if len(parts) >= 3 else "1"
            media_type = parts[3].lower() if len(parts) >= 4 else ""
            json_field = parts[4] if len(parts) >= 5 else ""
            keywords = [k.strip() for k in kw_str.split(",") if k.strip()]
            if not keywords or not url_template:
                return "关键词和 URL 不能为空"
            if not param_count.isdigit():
                param_count = "1"
            if media_type not in ("image", "video", "text"):
                media_type = "image"
            p.rules.append({
                "keywords": keywords,
                "url_template": url_template,
                "param_count": param_count,
                "media_type": media_type,
                "json_field": json_field,
                "meme": "",
            })
            p._dirty = True
            kind_label = {"video": "视频", "text": "文本"}.get(media_type, "图片")
            extra = f"，JSON字段 {json_field}" if json_field else ""
            return (
                f"✅添加成功（{param_count} 参数，{kind_label}{extra}）\n"
                f"执行 {p.cmd_save_name} 保存！"
            )

        parts = [x.strip() for x in args.rsplit(",", 1)]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return "格式错误！"
        kw_str, url_template = parts
        keywords = [k.strip() for k in kw_str.split(",") if k.strip()]
        if not keywords:
            return "关键词不能为空"
        p.rules.append({
            "keywords": keywords,
            "url_template": url_template,
            "param_count": "1",
            "media_type": "image",
            "json_field": "",
            "meme": "",
        })
        p._dirty = True
        return f"✅添加成功（1 参数，图片）\n执行 {p.cmd_save_name} 保存！"

    def meme_del(self, args: str) -> str:
        p = self.plugin
        if not args or not args.isdigit():
            return f"用法：{p.cmd_del_name} 序号"
        self._sync_rules(p)
        idx = int(args) - 1
        if idx < 0 or idx >= len(p.rules):
            return "⚠️序号不存在"
        p.rules.pop(idx)
        p._dirty = True
        return f"✅已删除，执行 {p.cmd_save_name} 保存！"

    def meme_save(self) -> str:
        p = self.plugin
        ok = p.save_rules_to_config()
        if ok:
            return "✅所有规则已保存到插件配置！"
        return "❌保存失败，请查看后台日志"
