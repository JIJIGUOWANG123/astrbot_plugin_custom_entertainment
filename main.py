import os
import random
import time
import json
import re
import asyncio
import datetime
import inspect
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import At, Plain, Image, Video
try:
    from astrbot.api.message_components import Json
except Exception:
    Json = None
try:
    from astrbot.api.message_components import Music
except Exception:
    Music = None
try:
    from astrbot.api.message_components import Record
except Exception:
    Record = None
try:
    from astrbot.api.message_components import File as AudioFile
except Exception:
    AudioFile = None
try:
    from .core import qq_info as _qq_info
    _HAS_QQ_INFO = True
except Exception as e:
    _HAS_QQ_INFO = False
    logger.warning(f"[apix-meme] qq_info 模块加载失败: {e}")

_HAS_OUTPUT_MODE = False
OutputModeModule = None

def strip_inline_img(text):
    """
    去掉文本里的行内图片标记 `[[img:名字]]`。

    ★ 原版在 `core/output_mode.py` 里；市场版只发文本，
      行内图片本来就不显示，所以这里直接把整行标记抹掉即可。
    """
    import re as _re
    try:
        out = []
        for ln in str(text or "").split("\n"):
            if _re.match(r"^\s*\[\[\s*img\s*:.*?\]\]\s*$", ln, _re.I):
                continue
            out.append(_re.sub(r"\[\[\s*img\s*:.*?\]\]", "", ln, flags=_re.I))
        return "\n".join(out)
    except Exception:
        return str(text or "")

from aiohttp import web

MENU_TEXT_VERSION = "40"

_EXTRA_PAGES = {"poke"}

MENU_TEXT_LIVE = {"checkin_lottery", "checkin_bank",
                  # ★★ 1.9.1：借款还款含「利息 / 还款期限 / 单次上限」，
                  #   必须实时生成 —— 以前不在白名单里，后台改了期限群里还是旧的
                  "checkin_bank_loan", "checkin_shop_help",
                  "job_refill", "job_refill_status", "job_list",
                  "checkin_job"}

MENU_TEXT_LEGACY_FRAGS = {
    "checkin": ("🏆签到排行", "每天只能抢一次", "🚨抢银行"),
    "checkin_signin": ("🏆签到排行",),
    "checkin_query": ("🏆签到排行",
                      "三种排行榜"),
    "checkin_bank": ("查看持有", "总资产"),
    "checkin_lottery": ("投入",
                        "每次固定花"),
    "checkin_steal": ("银行里的",),
    "checkin_mount": ("没有任何坐骑", "只显示坐骑列表",
                      "消耗金币购买坐骑", "装备后每天群里第一句话会提示登场",
                      "图片模式会把坐骑图插在"),
    "checkin_job": ("打工赚取",),
    "checkin_admin": ("减到 0 为止",),
    "music": ("搜索歌曲，列出前几首供选择", "供选择",
              "也可以：点歌 QQ", "一次搜索只能选一首，想再选请重新「点歌",
              "卡片仅网易云/酷我/咪咕有效", "不带参数查看全部音源",
              "打开本菜单", "音乐地址带时间签名会过期"),
    "music_find": ("知道要听哪首、不想挑",),
    "notice": ("提示功能总开关",),
    "pet": ("指定**自己的**宠物（对方随机）",),
    "checkin_shop_help": ("共 12 件商品（后台可增删改）",
                          "花金币购买（每样只需买一次）",
                          "要用下面的「启用武器」选出在用的那把",
                          "「签到统计」里会多显示保险箱余额"),
    "guess": ("放弃当前这局，重新开始",
              "每次花费：",
              "📊 次数限制："),
    "auth": ("📝 授权指令（群里发，需管理员）", "🔍 授权状态指令",
             "📋 授权列表指令", "三条指令分工：",
             "💡 后台可改：总开关",
             "🔑 插件总开关：", "📝 远程指令：「授权 群号 时长」",
             "📅 未设置的群：", "⚙️ 后台可改："),
    "auth_cmd": ("🔒 需 **AstrBot 全局管理员**（群主 / 群管 无权）",
                 "📝 授权（不带群号 = 本群）",
                 "🎯 指定群（群号放前面）",
                 "🛠️ 管理"),
    "auth_query": ("🔓 任何人都能查（不需要权限）",
                   "🔍 授权状态（查本群）",
                   "📋 授权列表（查全部群）",
                   "「授权状态」　　　　查**本群**是否已授权",
                   "「授权查询」　　　　列出**每个群**的授权状态"),
}

def _host_is_trusted(host: str) -> bool:
    """
    判断某个来源主机名是否可信（用于插件 Web 接口的同源校验）。

    ★★ 1.1：原来只认 `127.0.0.1 / localhost / ::1 / 0.0.0.0` ——
      于是**从局域网另一台机器访问 AstrBot 面板**时，
      浏览器带的 Origin 是那个内网 IP，被判定为「跨域」直接 403，
      表现为「后台页面点保存全部 → 失败」。

    现在额外放行**内网地址**（RFC1918 + 链路本地 + `.local`）：
      · 10.x.x.x / 172.16~31.x.x / 192.168.x.x
      · 169.254.x.x（链路本地）
      · *.local（mDNS）· IPv6 fc00::/7
    ★ 仍然**不放行**公网地址 —— 安全性不受影响（这些接口本来也
      必须经 AstrBot 面板登录才能到达）。
    """
    h = str(host or "").strip().lower().strip("[]")
    if not h:
        return False
    if h in ("127.0.0.1", "localhost", "::1", "0.0.0.0", "::"):
        return True
    if h.endswith(".local"):
        return True
    try:
        import ipaddress as _ip
        ip = _ip.ip_address(h)
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local)
    except Exception:
        pass
    return False

def _cors_allow_origin_of(request) -> str:
    """
    计算要回显的 Access-Control-Allow-Origin。

    ★ AstrBot 的插件后台页跑在 iframe（srcdoc / blob）里，浏览器给出的
      Origin 是**字符串 "null"**。它不是「未知来源」，而是本机受信任页面，
      必须放行；否则插件页里所有 fetch 都会被浏览器 CORS 拦掉。

    只回显 null / 本机地址，其它来源不回显（浏览器自然拒绝）。
    """
    origin = ""
    try:
        if request is not None:
            headers = getattr(request, "headers", None)
            if headers:
                origin = str(headers.get("Origin", "") or "").strip()
    except Exception:
        origin = ""
    if not origin:
        return ""
    if origin == "null":
        return "null"
    try:
        from urllib.parse import urlparse
        host = (urlparse(origin).hostname or "").lower()
        if _host_is_trusted(host):
            return origin
    except Exception:
        pass
    return ""

def _with_cors(resp: web.Response, request=None) -> web.Response:
    """给响应补上 CORS 头（仅对 null / 本机来源回显）"""
    try:
        allow = _cors_allow_origin_of(request)
        if allow and hasattr(resp, "headers"):
            resp.headers["Access-Control-Allow-Origin"] = allow
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = \
                "Content-Type, X-Plugin-Token"
            resp.headers["Vary"] = "Origin"
    except Exception:
        pass
    return resp

async def _options_handler(request):
    """处理浏览器 OPTIONS 预检：回 204 并带上 CORS 头"""
    return _with_cors(web.Response(status=204), request)

async def _fetch_daily_news_text() -> str:
    """拉取 60s 读报的文字版，作为图片不可用时的回退。"""
    try:
        import aiohttp as _aiohttp
        from .core.daily_news import NEWS_API_TEXT
        _t = _aiohttp.ClientTimeout(total=15)
        async with _aiohttp.ClientSession(timeout=_t) as session:
            async with session.get(NEWS_API_TEXT) as resp:
                if resp.status != 200:
                    return ""
                body = await resp.read()
        text = body.decode("utf-8", "ignore").strip()
        if not text:
            return ""
        return "📰 【今日读报】\n\n" + text
    except Exception:
        logger.exception("[apix-meme] 获取读报文字失败")
        return ""

try:
    from .core.system_status import (
        collect_runtime_status,
        GLOBAL_STATS,
        set_plugin_version,
        set_msg_stats_path as _set_msg_stats_path,
        load_msg_stats as _load_msg_stats,
        save_msg_stats as _save_msg_stats,
        # ★★ 1.5.0：接口**运行时**报错计数（用户在群里用接口失败时 +1）
        bump_api_err as _bump_api_err,
    )
    _HAS_STATUS = True
except Exception as e:
    _HAS_STATUS = False
    GLOBAL_STATS = {"recv": 0, "send": 0}

    def set_plugin_version(v):
        pass

    def _set_msg_stats_path(p):
        pass

    def _load_msg_stats():
        return GLOBAL_STATS

    def _save_msg_stats(force=False):
        return False

    def _bump_api_err():
        pass
    logger.warning(f"[apix-meme] system_status 模块加载失败: {e}")

def _fmt_uptime(sec) -> str:
    """
    ★★ 市场版 1.1：把秒数写成「1 天 2 小时」这种人话
    （后台「机器人数据」显示运行时长用）。
    """
    try:
        _s = max(0, int(float(sec or 0)))
    except Exception:
        return "—"
    _d, _rem = divmod(_s, 86400)
    _h, _rem = divmod(_rem, 3600)
    _m, _sec = divmod(_rem, 60)
    if _d:
        return f"{_d} 天 {_h} 小时"
    if _h:
        return f"{_h} 小时 {_m} 分"
    if _m:
        return f"{_m} 分 {_sec} 秒"
    return f"{_sec} 秒"

try:
    from .core.group_filter import (
        parse_line_list,
        parse_group_block_keywords,
        is_group_allowed,
        is_keyword_blocked,
        parse_gid_whitelist,
        group_allowed,
    )
    _HAS_FILTER = True
except Exception as e:
    _HAS_FILTER = False
    logger.warning(f"[apix-meme] group_filter 模块加载失败: {e}")
    def parse_line_list(raw): return []
    def parse_group_block_keywords(raw): return {}
    def is_group_allowed(g, m, w, b): return True
    def is_keyword_blocked(g, k, mp): return False
    def parse_gid_whitelist(raw): return []
    def group_allowed(g, allowed): return True

try:
    from .core.fortune import FortuneModule
    _HAS_FORTUNE = True
except Exception as e:
    _HAS_FORTUNE = False
    FortuneModule = None
    logger.warning(f"[apix-meme] fortune 模块加载失败: {e}")

try:
    from .core.husband import HusbandModule
    _HAS_HUSBAND = True
except Exception as e:
    _HAS_HUSBAND = False
    HusbandModule = None
    logger.warning(f"[apix-meme] husband 模块加载失败: {e}")

try:
    from .core.luck import LuckModule
    _HAS_LUCK = True
except Exception as e:
    _HAS_LUCK = False
    LuckModule = None
    logger.warning(f"[apix-meme] luck 模块加载失败: {e}")

try:
    from .core.auth import AuthModule
    _HAS_AUTH = True
except Exception as e:
    _HAS_AUTH = False
    AuthModule = None
    logger.warning(f"[apix-meme] auth 模块加载失败: {e}")

try:
    from .core.meme_api import (
        parse_params,
        build_url,
        download_image,
        download_media,
        detect_param_types,
        resolve_param_refs,
        validate_meme_params,
        fetch_text,
    )
    _HAS_MEME_API = True
except Exception as e:
    _HAS_MEME_API = False
    logger.warning(f"[apix-meme] meme_api 模块加载失败: {e}")
    def parse_params(raw): return []
    def build_url(t, p): return t
    def detect_param_types(t, n): return ["any"] * n
    async def download_image(url, timeout=15):
        raise RuntimeError("meme_api 模块未加载")
    async def fetch_text(url, timeout=15, json_field=""):
        raise RuntimeError("meme_api 模块未加载")
    async def download_media(url, timeout=15):
        raise RuntimeError("meme_api 模块未加载")

try:
    from .core.word_reply import WordReplyModule
    _HAS_WORD_REPLY = True
except Exception as e:
    _HAS_WORD_REPLY = False
    WordReplyModule = None
    logger.warning(f"[apix-meme] word_reply 模块加载失败: {e}")

try:
    from .core.greeting import GreetingModule
    _HAS_GREETING = True
except Exception as e:
    _HAS_GREETING = False
    GreetingModule = None
    logger.warning(f"[apix-meme] greeting 模块加载失败: {e}")

try:
    from .core.minecraft import MinecraftVersionModule
    _HAS_MINECRAFT = True
except Exception as e:
    _HAS_MINECRAFT = False
    MinecraftVersionModule = None
    logger.warning(f"[apix-meme] minecraft 模块加载失败: {e}")

try:
    from .core.checkin import CheckinModule
    _HAS_CHECKIN = True
except Exception as e:
    _HAS_CHECKIN = False
    CheckinModule = None
    logger.warning(f"[apix-meme] checkin 模块加载失败: {e}")

try:
    from .core.group_admin import GroupAdminModule
    _HAS_GROUP_ADMIN = True
except Exception as e:
    _HAS_GROUP_ADMIN = False
    GroupAdminModule = None
    logger.warning(f"[apix-meme] group_admin 模块加载失败: {e}")

try:
    from .core.notice import NoticeModule, NOTICE_KINDS, NOTICE_LABEL
    _HAS_NOTICE = True
except Exception as e:
    _HAS_NOTICE = False
    NoticeModule = None
    NOTICE_KINDS = ("card", "mute", "leave", "admin", "title")
    NOTICE_LABEL = {}
    logger.warning(f"[apix-meme] notice 模块加载失败: {e}")

try:
    from .core.poke_reply import PokeReplyModule, DEFAULT_WORDS
    _HAS_POKE_REPLY = True
except Exception as e:
    _HAS_POKE_REPLY = False
    PokeReplyModule = None
    DEFAULT_WORDS = ()
    logger.warning(f"[apix-meme] poke_reply 模块加载失败: {e}")

try:
    from .core.admin_commands import AdminCommands, FEATURE_LABEL
    _HAS_ADMIN_CMDS = True
except Exception as e:
    _HAS_ADMIN_CMDS = False
    AdminCommands = None
    FEATURE_LABEL = {}
    logger.warning(f"[apix-meme] admin_commands 模块加载失败: {e}")

try:
    from .core.hourly_chime import HourlyChimeModule
    _HAS_HOURLY_CHIME = True
except Exception as e:
    _HAS_HOURLY_CHIME = False
    HourlyChimeModule = None
    logger.warning(f"[apix-meme] hourly_chime 模块加载失败: {e}")

try:
    from .core.reply_tail import ReplyTail
    _HAS_REPLY_TAIL = True
except Exception as e:
    _HAS_REPLY_TAIL = False
    ReplyTail = None
    logger.warning(f"[apix-meme] reply_tail 模块加载失败: {e}")

try:
    from .core.web_config import (
        register_web_apis,
        read_schema,
        read_config,
        write_config,
    )
    _HAS_WEB_CONFIG = True
except Exception as e:
    _HAS_WEB_CONFIG = False
    logger.warning(f"[apix-meme] web_config 模块加载失败: {e}")
    def register_web_apis(context): return False
    def read_schema(): return {}
    def read_config(): return {}
    def write_config(data): return False

try:
    from .core.status_push import StatusPushModule, fmt_seconds
    _HAS_STATUS_PUSH = True
except Exception as e:
    _HAS_STATUS_PUSH = False
    StatusPushModule = None
    def fmt_seconds(sec): return "—"
    logger.warning(f"[apix-meme] status_push 模块加载失败: {e}")

try:
    from .core.moderation import (
        ModerationModule,
        parse_blacklist,
        get_action as get_blacklist_action,
        upsert_blacklist,
        remove_blacklist,
        ACTION_KICK,
        ACTION_MUTE,
        ACTION_IGNORE,
        ACTION_LABEL,
    )
    _HAS_MODERATION = True
except Exception as e:
    _HAS_MODERATION = False
    ModerationModule = None
    logger.warning(f"[apix-meme] moderation 模块加载失败: {e}")
    def parse_blacklist(raw): return {}
    def get_blacklist_action(bl, uid, default="ignore"): return ""
    def upsert_blacklist(raw, uid, act): return str(raw or "")
    def remove_blacklist(raw, uid): return str(raw or ""), False
    ACTION_KICK, ACTION_MUTE, ACTION_IGNORE = "kick", "mute", "ignore"
    ACTION_LABEL = {}

try:
    from .core.daily_news import DailyNewsModule
    _HAS_DAILY_NEWS = True
except Exception as e:
    _HAS_DAILY_NEWS = False
    DailyNewsModule = None
    logger.warning(f"[apix-meme] daily_news 模块加载失败: {e}")

try:
    from .core.speak_stats import SpeakStatsModule
    _HAS_SPEAK_STATS = True
except Exception as e:
    _HAS_SPEAK_STATS = False
    SpeakStatsModule = None
    logger.warning(f"[apix-meme] speak_stats 模块加载失败: {e}")

try:
    from .core.pet import PetModule
    _HAS_PET = True
except Exception as e:
    _HAS_PET = False
    PetModule = None
    logger.warning(f"[apix-meme] pet 模块加载失败: {e}")

try:
    from .core.guess import GuessModule
    _HAS_GUESS = True
except Exception as e:
    _HAS_GUESS = False
    GuessModule = None
    logger.warning(f"[apix-meme] guess 模块加载失败: {e}")

# ★★ 1.6.0：农场种菜（自主实现，不依赖任何渲染服务）
try:
    from .core.farm import FarmModule
    _HAS_FARM = True
except Exception as e:
    _HAS_FARM = False
    FarmModule = None
    logger.warning(f"[apix-meme] farm 模块加载失败: {e}")

try:
    from .core.racing import (
        RacingModule as _RacingModule,
        parse_car_list as _parse_car_list,
        ATTR_KEYS as _RACE_ATTR_KEYS,
        ATTR_LABEL as _RACE_ATTR_LABEL,
        ATTR_WEIGHT as _RACE_WEIGHT,
    )
    _HAS_RACING = True
except Exception as e:
    _HAS_RACING = False
    _RacingModule = None
    _RACE_ATTR_KEYS = ("speed", "accel", "handling", "stability", "luck")
    _RACE_ATTR_LABEL = {}
    _RACE_WEIGHT = {}

    def _parse_car_list(raw):
        return []

    logger.warning(f"[apix-meme] racing 模块加载失败: {e}")

try:
    from .core.data_store import DataStore, COLLECTIONS as DATA_COLLECTIONS
    _HAS_DATA_STORE = True
except Exception as e:
    _HAS_DATA_STORE = False
    DataStore = None
    DATA_COLLECTIONS = []
    logger.warning(f"[apix-meme] data_store 模块加载失败: {e}")

try:
    from .core.umo_push import (
        get_event_umo,
        push_message,
    )
    _HAS_UMO_PUSH = True
except Exception as e:
    _HAS_UMO_PUSH = False
    logger.warning(f"[apix-meme] umo_push 模块加载失败: {e}")
    def get_event_umo(event): return ""
    async def push_message(context, group_id, text, umo="",
                           hourly_chime_module=None, minecraft_module=None):
        return False

try:
    from .core.menu_builder import (
        build_main_menu,
        build_meme_menu,
        build_mc_menu,
        build_mc_version_menu,
        build_mc_server_menu,
        build_mc_player_menu,
        build_mc_push_menu,
        build_greeting_menu,
        build_checkin_menu,
        build_daily_menu,
        build_admin_menu,
        build_meme_feature_menu,
            build_greeting_morning_menu,
        build_greeting_night_menu,
        build_greeting_rank_menu,
        build_racing_menu,
        build_checkin_signin_menu,
        build_checkin_query_menu,
        build_checkin_bank_menu,
        build_checkin_lottery_menu,
        build_checkin_steal_menu,
        build_checkin_mount_menu,
        build_checkin_mount_manage_menu,
        build_checkin_mount_admin_menu,
        build_checkin_job_menu,
        build_checkin_job_list_menu,
        build_checkin_job_refill_menu,
        build_stamina_menu,
        build_checkin_bank_save_menu,
        build_checkin_bank_loan_menu,
        build_checkin_bank_fund_menu,
        build_checkin_bank_rob_menu,
        build_checkin_shop_help_menu,
        build_pet_care_menu,
        build_pet_fight_menu,
        build_pet_item_menu,
        build_weather_menu,
        build_speak_menu,
        build_pet_menu,
        build_guess_menu,
        build_music_menu,
        build_meme_admin_menu,
        build_word_admin_menu,
        build_checkin_admin_menu,
        build_output_mode_menu,
        build_fortune_menu,
        build_husband_menu,
        build_luck_menu,
        build_news_menu,
        build_status_admin_menu,
        build_switch_admin_menu,
        build_chime_admin_menu,
        build_news_admin_menu,
        build_member_admin_menu,
        build_notice_menu,
        build_auth_menu,
    build_auth_query_menu,
    build_auth_cmd_menu,
    # ★★ 1.6.0：农场功能菜单
    build_farm_menu,
    build_farm_plant_menu,
    build_farm_seed_menu,
    build_farm_fert_menu,
    build_farm_livestock_menu,
    build_farm_machine_menu,
    build_farm_steal_menu,
    _ga_help_lines,
        build_must_at_admin_menu,
        build_super_admin_menu,
        build_entertain_menu,
        build_qqinfo_menu,
        MENU_REGISTRY,
        resolve_menu,
        menu_default,
    )
    _HAS_MENU = True
except Exception as e:
    _HAS_MENU = False
    logger.warning(f"[apix-meme] menu_builder 模块加载失败: {e}")
    MENU_REGISTRY = {}
    def menu_default(plugin, menu_id): return ""
    def resolve_menu(plugin, menu_id, custom=None): return ""
    def build_main_menu(p): return ""
    def build_bird_menu(p): return ""
    def build_meme_menu(p): return ""
    def build_mc_menu(p): return ""
    def build_greeting_menu(p): return ""
    def build_checkin_menu(p): return ""
    def build_daily_menu(p): return ""
    def build_admin_menu(p): return ""
    def build_meme_feature_menu(p): return ""
    def build_greeting_morning_menu(p): return ""
    def build_greeting_night_menu(p): return ""
    def build_greeting_rank_menu(p): return ""
    def build_checkin_signin_menu(p): return ""
    def build_checkin_query_menu(p): return ""
    def build_checkin_bank_menu(p): return ""
    def build_checkin_lottery_menu(p): return ""
    def build_checkin_steal_menu(p): return ""
    def build_checkin_mount_menu(p): return ""
    def build_checkin_job_menu(p): return ""
    def build_checkin_job_list_menu(p): return ""
    def build_checkin_job_refill_menu(p): return ""
    def build_stamina_menu(p): return ""
    def build_checkin_bank_save_menu(p): return ""
    def build_checkin_bank_loan_menu(p): return ""
    def build_checkin_bank_fund_menu(p): return ""
    def build_checkin_bank_rob_menu(p): return ""
    def build_checkin_shop_help_menu(p, page=1): return ""
    def build_pet_care_menu(p): return ""
    def build_pet_fight_menu(p): return ""
    def build_pet_item_menu(p): return ""
    def build_weather_menu(p): return ""
    def build_meme_admin_menu(p): return ""
    def build_word_admin_menu(p): return ""
    def build_checkin_admin_menu(p): return ""
    def build_status_admin_menu(p): return ""
    def build_switch_admin_menu(p): return ""
    def build_chime_admin_menu(p): return ""
    def build_news_admin_menu(p): return ""
    def build_member_admin_menu(p): return ""
    def build_notice_menu(p): return ""
    def build_auth_menu(p): return ""
    def build_auth_cmd_menu(p): return ""
    def build_auth_query_menu(p): return ""
    def build_must_at_admin_menu(p): return ""
    def build_super_admin_menu(p): return ""
    def build_music_menu(p): return ""
    # ★★ 1.6.0：农场菜单（menu_builder 没加载时的兜底）
    def build_farm_menu(p): return ""
    def build_farm_plant_menu(p): return ""
    def build_farm_seed_menu(p): return ""
    def build_farm_fert_menu(p): return ""
    def build_farm_livestock_menu(p): return ""
    def build_farm_machine_menu(p): return ""
    def build_farm_steal_menu(p): return ""

try:
    from .core.background import (
        ensure_mc_checker,
        ensure_hourly_chime,
        ensure_status_push,
        daily_status_push_checker,
        ensure_daily_news,
        daily_news_checker,
        ensure_farm_auto,
        farm_auto_checker,
    )
    _HAS_BACKGROUND = True
except Exception as e:
    _HAS_BACKGROUND = False
    logger.warning(f"[apix-meme] background 模块加载失败: {e}")
    async def ensure_mc_checker(plugin): pass
    async def ensure_hourly_chime(plugin): pass
    async def ensure_status_push(plugin): pass
    async def daily_status_push_checker(plugin): pass
    async def ensure_daily_news(plugin): pass
    async def daily_news_checker(plugin): pass
    async def ensure_farm_auto(plugin): pass
    async def farm_auto_checker(plugin): pass

from .core.path_utils import (
    PLUGIN_ID,
    plugin_dir as _plugin_dir,
    _plugin_data_dir,
    config_path as _config_path,
    fortune_data_path as _fortune_data_path,
    husband_data_path as _husband_data_path,
    luck_data_path as _luck_data_path,
    auth_data_path as _auth_data_path,
    greeting_data_path as _greeting_data_path,
    minecraft_data_path as _minecraft_data_path,
    checkin_data_path as _checkin_data_path,
    hourly_chime_data_path as _hourly_chime_data_path,
    status_push_data_path as _status_push_data_path,
    daily_news_data_path as _daily_news_data_path,
    speak_stats_data_path as _speak_stats_data_path,
    pet_data_path as _pet_data_path,
    guess_data_path as _guess_data_path,
    # ★★ 1.6.0：农场存档路径
    farm_data_path as _farm_data_path,
    msg_stats_path as _msg_stats_path,
    # ★★ 1.5.0：接口测试结果文件（落盘）
    api_test_path as _api_test_path,
    parse_gid_list,
    data_store_path as _data_store_path,
    fmt_num,
    racing_data_path as _racing_data_path,
    # ★★ 1.5.1：音乐功能数据路径（点歌会话目前只在内存，不写盘）
    music_data_path as _music_data_path,
    read_plugin_version,
    read_disk_config,
    write_disk_config,
    read_astrbot_panel_config,
)
from .core.event_helpers import (
    to_str,
    get_group_id_from_event as _get_group_id_from_event,
    get_ats_from_msg,
    split_text_and_ats,
    restore_ats,
    has_at_bot,
    get_sender_name,
)
from .core.nickname import NicknameCache
from .core.path_utils import title_bar, title_foot
from .core.path_utils import cmd_arg

# ★★ 1.5.1：音乐功能（点歌 / 搜歌 / 选歌 / 音源 / 榜单随机）
#   ★ 只发**音乐卡片**：用 AstrBot 原生 Music 组件，由平台适配器负责签名；
#     插件自己不调任何外部签名服务、不发语音、不缓存封面图。
try:
    from .core.music import MusicModule
    from .core import music_toplist as music_toplist
    _HAS_MUSIC = True
except Exception as e:
    _HAS_MUSIC = False
    MusicModule = None
    music_toplist = None
    logger.warning(f"[apix-meme] music 模块加载失败: {e}")

BIRD_DIR_DEFAULT = ""
_HAS_BIRD = False
BirdCollection = None

@register(
    PLUGIN_ID,
    "自定义娱乐",
    "接口系统 + 每日运势 + 今日老公 + 今日人品 + 早安晚安 + Minecraft Java版检测 + 电脑状态 + 定时状态推送 + 每日读报 + 自定义词库 + 早晚安打卡排行 + 每日签到 + 偷积分 + 银行系统 + 坐骑商店 + 打工系统 + 整点报时 + 群员管理 + 数据备份 + 简约菜单系统 + 戳一戳随机回复",
    "1.0",
    "chicken butt"
)
class ApixIqfkSimpleMeme(Star):

    def _bird_img_of(self, nm):
        """存根：小鸟图鉴已移除，永远返回空"""
        return ""

    def _top_bird_for(self, target, group_id):
        """存根：小鸟图鉴已移除，永远返回空"""
        return ("", "")
    BASE_URL = "https://apix.iqfk.top/api/sv1"
    PAGE_SIZE = 20

    def __init__(self, context: Context, config: dict):
        super().__init__(context)

        self.config = config or {}
        self.rules = []
        self.must_at_bot = False
        self.cmd_main_name = "自定义娱乐"
        self.cmd_admin_name = "管理员指令"
        self.cmd_list_name = "表情包列表"
        self.cmd_greeting_morning_raw = "早安,早上好,起床"
        self.cmd_greeting_night_raw = "晚安,睡觉,睡了"
        self.cmd_minecraft_name = "MC版本"
        self.cmd_mc_auto_name = "MC自动检测"
        self.cmd_mc_test_name = "MC测试推送"
        self.cmd_add_name = "添加表情"
        self.cmd_del_name = "删除规则"
        self.cmd_save_name = "保存规则"
        self.cmd_status_names = ["电脑状态"]
        self.cmd_status_name = "电脑状态"
        self.cmd_word_add_name = "加词"
        self.cmd_word_del_name = "删词"
        self.cmd_word_list_name = "词库列表"
        self.cmd_enable_name = "自定义娱乐 开启"
        self.cmd_disable_name = "自定义娱乐 关闭"
        self.cmd_view_admin_name = "查看管理员"
        self.cmd_refresh_admin_name = "刷新群管"
        self.cmd_add_admin_name = "加管理员"
        self.cmd_del_admin_name = "删管理员"
        self.cmd_admin_list_name = "管理员列表"
        self.cmd_enable_feature_name = "开启功能"
        self.cmd_disable_feature_name = "关闭功能"
        self.cmd_feature_status_name = "功能状态"
        self.cmd_greeting_rank_name = "打卡排行"
        self.cmd_greeting_stats_name = "打卡统计"
        self.cmd_job_add_name = "添加工种"
        self.cmd_job_del_name = "删除工种"
        self.cmd_mount_add_name = "添加坐骑"
        self.cmd_mount_del_name = "删除坐骑"

        self.cmd_must_at_bot_on_name = "必须艾特开"
        self.cmd_must_at_bot_off_name = "必须艾特关"
        self.cmd_must_at_bot_status_name = "必须艾特状态"

        self.cmd_meme_menu_name = "接口系统"
        self.cmd_meme_image_name = "图片接口"
        self.cmd_meme_video_name = "视频接口"
        self.cmd_meme_text_name = "文本接口"
        self.cmd_mc_menu_name = "世界系统"
        # ★★ 1.5.0：世界系统 4 个子功能入口
        self.cmd_mc_version_menu_name = "版本查询"
        self.cmd_mc_server_menu_name = "服务查询"
        self.cmd_mc_player_menu_name = "玩家信息"
        self.cmd_mc_push_menu_name = "版本推送"
        self.cmd_mute_list_name = "禁言列表"
        self.mute_unit_name = "秒"
        self.cmd_greeting_menu_name = "打卡系统"
        self.cmd_checkin_menu_name = "签到系统"
        self.cmd_daily_menu_name = "每日系统"
        self.cmd_entertain_menu_name = "娱乐系统"
        self.qqinfo_enable = True
        self.cmd_qqinfo = "查询,查QQ,查qq,查用户"
        self.cmd_qqinfo_menu_name = "查询功能"

        self.cmd_chime_on_name = "整点报时开"
        self.cmd_chime_off_name = "整点报时关"
        self.cmd_chime_status_name = "整点报时状态"
        self.cmd_chime_hours_name = "整点报时时段"
        self.cmd_chime_text_name = "整点报时文案"
        self.cmd_chime_list_name = "整点报时列表"
        self.cmd_chime_test_name = "整点报时测试"

        self.cmd_daily_push_on_name = "状态推送开"
        self.cmd_daily_push_off_name = "状态推送关"
        self.cmd_daily_push_time_name = "状态推送间隔"
        self.cmd_daily_push_list_name = "状态推送列表"

        self.cmd_kick_name = "踢出"
        self.cmd_mute_name = "禁言"
        self.cmd_unmute_name = "解除禁言"
        self.cmd_blacklist_name = "拉黑"
        self.cmd_unblacklist_name = "解除拉黑"
        self.cmd_blacklist_list_name = "黑名单列表"

        self.cmd_daily_news_name = "今日读报,每日读报"
        self.cmd_daily_news_manual_on_name = "开启功能 读报功能,开启读报功能"
        self.cmd_daily_news_manual_off_name = "关闭功能 读报功能,关闭读报功能"
        self.cmd_daily_news_on_name = "开启功能 读报推送,开启读报推送"
        self.cmd_daily_news_off_name = "读报推送关"
        self.cmd_daily_news_time_name = "读报推送时间"
        self.cmd_daily_news_list_name = "读报推送列表"

        self.group_mode = "off"
        self.group_whitelist = []
        self.group_blacklist = []
        self.allowed_groups = []
        self.status_groups = []
        self.status_enable = True
        self.score_admin_enable = True
        self.score_admin_groups = []
        self.meme_enable = True
        self.meme_groups = []
        self.group_block_map = {}
        self.disabled_groups = []
        self.plugin_admins = []
        self.score_admin_whitelist = []
        self.web_admin_token = ""
        self.moderation_enable = True
        self._blacklist_raw = ""
        self.member_blacklist = {}
        self.blacklist_default_action = "ignore"
        # ★★ 1.4.2：接口测试结果缓存（内存态，重载即清空）
        #   { 规则键: {"ok": bool, "msg": str, "ms": int, "kind": str} }
        self._api_test_cache = {}
        self._api_test_at = ""
        self.mute_duration = 600
        self.group_feature_switches = {}
        self.group_must_at_bot = {}
        self.minecraft_auto_groups = []
        self.minecraft_groups = []
        self.minecraft_test_groups = []
        self.feature_switch = {
            "fortune": True,
            "husband": True,
            "luck": True,
            "word_reply": True,
            "meme": True,
            "greeting": True,
            "minecraft": True,
            "checkin": True,
        }

        self._dirty = False
        self._mc_checker_started = False
        self._mc_checker_task = None
        self._chime_checker_started = False
        self._chime_checker_task = None
        self._status_push_started = False
        self._status_push_task = None
        self._daily_news_started = False
        self._daily_news_task = None
        self._platform_id_cache = None

        self._nickname_cache = NicknameCache()
        self.nickname_cache = self._nickname_cache.as_dict()

        try:
            set_plugin_version(read_plugin_version(self))
        except Exception:
            logger.exception("[apix-meme] 注入插件版本失败")

        try:
            _set_msg_stats_path(_msg_stats_path())
            _load_msg_stats()
        except Exception:
            logger.exception("[apix-meme] 恢复收发计数失败")

        # ★★ 1.5.0：恢复上次的**接口测试结果**（重载插件后后台还能看到）
        try:
            self._load_api_test_cache()
        except Exception:
            logger.exception("[apix-meme] 恢复接口测试结果失败")

        # ★★ 1.4.0：给 logger 挂上错误计数器（后台「数据统计」的「错误次数」）。
        #   纯本地统计，不上报、不外发。
        try:
            from .core.system_status import (
                install_error_counter as _install_err_counter)
            if _install_err_counter(logger):
                logger.info("[apix-meme] 错误计数器已接入（本地统计）")
        except Exception:
            logger.debug("[apix-meme] 错误计数器接入失败", exc_info=True)

        self.fortune_module = None
        if _HAS_FORTUNE:
            try:
                self.fortune_module = FortuneModule(
                    plugin_dir=_plugin_dir(),
                    data_path=_fortune_data_path(),
                )
            except Exception:
                logger.exception("[apix-meme] 初始化运势模块失败")

        self.husband_module = None
        if _HAS_HUSBAND:
            try:
                self.husband_module = HusbandModule(
                    plugin_dir=_plugin_dir(),
                    data_path=_husband_data_path(),
                )
                logger.info("[apix-meme] 老公模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化老公模块失败")

        self.luck_module = None
        if _HAS_LUCK:
            try:
                self.luck_module = LuckModule(
                    plugin_dir=_plugin_dir(),
                    data_path=_luck_data_path(),
                )
                logger.info("[apix-meme] 人品模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化人品模块失败")

        self.auth_module = None
        if _HAS_AUTH:
            try:
                self.auth_module = AuthModule(
                    plugin_dir=_plugin_dir(),
                    data_path=_auth_data_path(),
                )
                _st = self.auth_module.stats()
                logger.info(
                    f"[apix-meme] 授权模块初始化成功"
                    f"（单独授权 {_st['total']} 群："
                    f"永久 {_st['never']} / 有效 {_st['active']} / "
                    f"过期 {_st['expired']} / 停用 {_st['disabled']}）")
            except Exception:
                logger.exception("[apix-meme] 初始化授权模块失败")

        self.word_reply_module = None
        if _HAS_WORD_REPLY:
            try:
                self.word_reply_module = WordReplyModule(
                    plugin_dir=_plugin_dir(),
                    data_path="",
                )
                logger.info("[apix-meme] 词库模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化词库模块失败")

        self.greeting_module = None
        if _HAS_GREETING:
            try:
                self.greeting_module = GreetingModule(
                    plugin_dir=_plugin_dir(),
                    data_path=_greeting_data_path(),
                )
                self.greeting_module.nickname_cache = self.nickname_cache
                logger.info("[apix-meme] 早安晚安模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化早安晚安模块失败")

        self.minecraft_module = None
        if _HAS_MINECRAFT:
            try:
                self.minecraft_module = MinecraftVersionModule(
                    plugin_dir=_plugin_dir(),
                    data_path=_minecraft_data_path(),
                )
                logger.info("[apix-meme] MC 版本检测模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化 MC 版本模块失败")

        self.checkin_module = None
        if _HAS_CHECKIN:
            try:
                self.checkin_module = CheckinModule(
                    plugin_dir=_plugin_dir(),
                    data_path=_checkin_data_path(),
                )
                self.checkin_module.nickname_cache = self.nickname_cache
                logger.info("[apix-meme] 签到模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化签到模块失败")

        self.group_admin_module = None
        if _HAS_GROUP_ADMIN:
            try:
                self.group_admin_module = GroupAdminModule()
                logger.info("[apix-meme] 群管识别模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化群管模块失败")

        self.notice_module = None

        self.moderation_module = None
        if _HAS_MODERATION:
            try:
                self.moderation_module = ModerationModule(self.group_admin_module)
                logger.info("[apix-meme] 群员管理模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化群员管理模块失败")

        self.admin_commands = None
        if _HAS_ADMIN_CMDS:
            try:
                self.admin_commands = AdminCommands(self)
                logger.info("[apix-meme] 管理指令模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化管理指令模块失败")

        self.reply_tail = None
        if _HAS_REPLY_TAIL:
            try:
                self.reply_tail = ReplyTail()
                logger.info("[apix-meme] 回复尾巴模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化回复尾巴模块失败")

        self.hourly_chime_module = None
        if _HAS_HOURLY_CHIME:
            try:
                self.hourly_chime_module = HourlyChimeModule(
                    plugin_dir=_plugin_dir(),
                    data_path=_hourly_chime_data_path(),
                )
                logger.info("[apix-meme] 整点报时模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化整点报时模块失败")

        self.status_push_module = None
        if _HAS_STATUS_PUSH:
            try:
                self.status_push_module = StatusPushModule(
                    plugin_dir=_plugin_dir(),
                    data_path=_status_push_data_path(),
                )
                logger.info("[apix-meme] 定时状态推送模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化定时状态推送模块失败")

        self.daily_news_module = None
        if _HAS_DAILY_NEWS:
            try:
                self.daily_news_module = DailyNewsModule(
                    plugin_dir=_plugin_dir(),
                    data_path=_daily_news_data_path(),
                )
                logger.info("[apix-meme] 每日读报模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化每日读报模块失败")

        self.speak_stats_module = None
        if _HAS_SPEAK_STATS:
            try:
                self.speak_stats_module = SpeakStatsModule(
                    plugin_dir=_plugin_dir(),
                    data_path=_speak_stats_data_path(),
                )
                logger.info("[apix-meme] 发言统计模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化发言统计模块失败")

        self.bird_collection = None
        if _HAS_BIRD:
            try:
                self.bird_collection = BirdCollection(_bird_data_path())
                _bst = self.bird_collection.stats()
                logger.info(
                    "[apix-meme] 小鸟收集模块初始化成功"
                    "（%d 个用户 / %d 条收集）",
                    _bst.get("users", 0), _bst.get("owned", 0))
            except Exception:
                logger.exception("[apix-meme] 初始化小鸟收集模块失败")
        self.bird_enable = True
        self.cmd_bird = ["今日小鸟"]
        self.cmd_gallery = ["我的图鉴", "小鸟图鉴"]
        self.cmd_bird_menu = ["小鸟图鉴"]
        self.cmd_bird_list = ["图鉴列表"]
        self.cmd_bird_view = ["图鉴查看"]
        self.bird_list_per_page = 10
        self.bird_groups = []
        self.bird_image_dir = BIRD_DIR_DEFAULT
        self.bird_rank_enable = True

        self.score_name = "积分"
        self.cmd_weather_menu_name = "天气查询"

        self.guess_module = None
        if _HAS_GUESS:
            try:
                self.guess_module = GuessModule(_guess_data_path())
                logger.info("[apix-meme] 猜数模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化猜数模块失败")
        self.guess_enable = True
        self.guess_groups = []

        # ★★ 1.6.0：农场种菜（娱乐系统子功能）
        self.farm_enable = True
        self.farm_groups = []
        self.cmd_farm_menu = "农场功能"
        self.cmd_farm_view = "我的农场,查看农场"
        self.cmd_farm_plant = "种菜,种植"
        self.cmd_farm_harvest = "收获,收菜"
        # ★ 一键收获：把所有成熟的一次性收完并**直接卖掉**（积分立刻到手）
        self.cmd_farm_harvest_all = "一键收获,全部收获,一键收菜"
        # ★ 三个商店：种子商店 / 农具商店（肥料）/ 工具商店（看门家畜）
        self.cmd_farm_shop = "种子商店,买种子"
        self.cmd_farm_buy_seed = "购买种子"
        self.cmd_farm_impl_shop = "农具商店"
        self.cmd_farm_buy_item = "购买肥料"
        self.cmd_farm_use_item = "施肥,使用肥料"
        self.cmd_farm_dog = "工具商店,家畜商店,买狗,狗商店"
        self.cmd_farm_buy_dog = "购买家畜,购买狗"
        self.cmd_farm_feed = "喂食,喂养"
        self.cmd_farm_buy_feed = "购买饲料"
        self.cmd_farm_equip_dog = "装备家畜,换看门犬"
        self.cmd_farm_my_animals = "我的家畜,家畜列表"
        self.cmd_farm_steal = "偷菜,偷庄稼"
        self.cmd_farm_steal_log = "被偷记录,偷菜记录"
        self.cmd_farm_bag = "我的背包,背包"
        self.cmd_farm_status = "农场状态,今日农场"
        self.cmd_farm_buy_tool = "购买工具,购买农机"
        self.cmd_farm_equip_tool = "装备工具,装备农机"
        self.farm_init_lands = 3
        self.farm_max_lands = 30
        self.farm_max_level = 30
        # ★ 等级靠**农场经验**（收获给），不同作物经验不同
        self.farm_exp_base = 100
        self.farm_exp_step = 60
        # ★ 家畜：可养多只 + 看门会累（唯一作用是防偷）+ 列表分页
        self.farm_max_animals = 5
        self.farm_animal_energy_max = 100
        self.farm_animal_energy_per_hour = 10
        self.farm_shop_per_page = 8
        self.farm_land_per_page = 12
        # ★ 偷菜
        self.farm_steal_enable = True
        self.farm_steal_base_rate = 3
        self.farm_steal_ratio = 30
        self.farm_steal_daily_limit = 3
        self.farm_steal_min_amount = 10
        # ★ 偷菜失败赔付对方积分（随机区间）
        self.farm_steal_fail_comp_min = 1
        self.farm_steal_fail_comp_max = 500
        # ★ 土地品质：收获时可能「触发收获机遇」升级（普通 -> 红 -> 黑）
        self.farm_harvest_upgrade_chance = 5
        self.farm_quality_bonus_red = 10
        self.farm_quality_bonus_black = 20
        # ★ 农机每日自动作业（自动收割机 / 自动播种机；静默不发群消息）
        self.farm_auto_enable = True
        self.farm_auto_interval_min = 10

        self.cmd_chime_on_name = "开启功能 报时功能,开启报时功能"
        self.cmd_chime_off_name = "关闭功能 报时功能,关闭报时功能"
        self.cmd_chime_status_name = "整点报时状态"



        self.racing_module = None
        if _HAS_RACING:
            try:
                self.racing_module = _RacingModule(_racing_data_path())
                self.racing_module._plugin = self
                logger.info("[apix-meme] 赛车模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化赛车模块失败")
        self.racing_enable = True
        self.racing_groups = []
        self.cmd_race_join = "参与比赛"
        self.cmd_race_fast = "快速比赛"
        self.cmd_race_my = "我的赛车"
        self.cmd_race_equip = "装备赛车"
        self.cmd_race_view = "赛车查看"
        self.cmd_race_shop = "赛车商店"
        self.cmd_race_buy = "购买赛车"
        self.cmd_race_lucky = "赛车抽奖"
        self.cmd_race_lucky_all = "赛车一键抽奖,赛车全部抽奖"
        self.cmd_race_menu_name = "赛车功能"
        self.race_players = 5
        self.race_wait_sec = 3600
        self.race_daily_limit = 10
        self.race_entry_fee = 500
        self.race_pool_bonus_min = 1000
        self.race_pool_bonus_max = 5000
        self.race_fast_npc = 5
        self.race_npc_fill = True
        self.race_npc_power = 100
        self.race_show_time = True
        self.race_lucky_fee = 2000
        self.race_lucky_daily_limit = 1
        self.race_shop_list = ""
        self.race_lottery_cars = ""

        import time as _bt
        self._boot_ts = _bt.time()
        self._seen_groups = set()
        self._schema_cache = {}
        self._registered_routes = []
        self.cmd_guess_start = ["开始猜数"]
        self.cmd_guess = ["猜数"]
        self.guess_min = 1
        self.guess_max = 100
        self.guess_max_tries = 3
        self.guess_timeout_sec = 60
        self.guess_reward_min = 100
        self.guess_reward_max = 500
        self.guess_penalty = 1000
        self.guess_allow_negative = True
        self.guess_cost = 0
        self.guess_reward = 100
        self.guess_daily_limit = 0

        self.pet_module = None
        if _HAS_PET:
            try:
                self.pet_module = PetModule(
                    plugin_dir=_plugin_dir(),
                    data_path=_pet_data_path(),
                )
                logger.info("[apix-meme] 宠物模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化宠物模块失败")

        self.data_store = None
        if _HAS_DATA_STORE:
            try:
                self.data_store = DataStore(_data_store_path())
                logger.info(
                    f"[apix-meme] 数据文件已加载："
                    f"{sum(len(self.data_store.get(k)) for k in DATA_COLLECTIONS)} 条数据"
                )
            except Exception:
                logger.exception("[apix-meme] 初始化数据存储失败")

        if self.checkin_module is not None and self.data_store is not None:
            try:
                self.checkin_module.data_store = self.data_store
            except Exception:
                logger.exception("[apix-meme] 注入数据存储到签到模块失败")

        # ★★ 1.6.0：农场模块（依赖 data_store 存种子/农具/家畜目录）
        self.farm_module = None
        if _HAS_FARM:
            try:
                self.farm_module = FarmModule(
                    data_path=_farm_data_path(),
                    store=getattr(self, "data_store", None))
                if getattr(self, "data_store", None) is not None:
                    self.farm_module.ensure_defaults()
                logger.info("[apix-meme] 农场模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化农场模块失败")

        self.notice_module = None
        if _HAS_NOTICE:
            try:
                self.notice_module = NoticeModule(self.data_store)
                logger.info("[apix-meme] 群事件提示模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化提示模块失败")

        self.poke_reply_module = None
        if _HAS_POKE_REPLY:
            try:
                self.poke_reply_module = PokeReplyModule()
                logger.info("[apix-meme] 戳一戳回复模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化戳一戳回复模块失败")

        # ★★ 1.5.1：音乐功能（只发卡片）
        self.music_module = None
        if _HAS_MUSIC:
            try:
                self.music_module = MusicModule(
                    _plugin_dir(), _music_data_path())
                logger.info("[apix-meme] 音乐模块初始化成功（仅卡片输出）")
            except Exception:
                logger.exception("[apix-meme] 初始化音乐模块失败")

        self.output_mode_module = None
        logger.info("[apix-meme] 市场版：仅文字输出模式（未启用图片渲染）")
        if False:
            try:
                self.output_mode_module = OutputModeModule(
                    plugin_dir=_plugin_dir()
                )
                try:
                    self.output_mode_module._clean_old_caches()
                except Exception:
                    logger.exception("[apix-meme] 清理旧渲染缓存失败")
                try:
                    from .core import path_utils as _pu
                    _om = self.output_mode_module
                    _pu.set_glyph_probe(
                        lambda chars=None, _m=_om: _m.missing_glyphs_sync(
                            chars or ""))
                    logger.info("[apix-meme] 缺字形探测已接入")
                except Exception:
                    logger.exception("[apix-meme] 接入缺字形探测失败")
                logger.info("[apix-meme] 输出模式模块初始化成功")
            except Exception:
                logger.exception("[apix-meme] 初始化输出模式模块失败")

        try:
            logger.info(f"[apix-meme] 磁盘配置路径: {_config_path()}")
        except Exception:
            pass

        self.reload_cfg()
        self._migrate_old_format()

        try:
            self._migrate_data_to_store()
        except Exception:
            logger.exception("[apix-meme] 迁移数据到独立文件失败")
        try:
            self._sync_defaults_to_store()
        except Exception:
            logger.exception("[apix-meme] 写入默认数据失败")
        try:
            self._cleanup_stray_astrbot_data()
        except Exception:
            logger.exception("[apix-meme] 清理误写目录异常")
        try:
            self._sync_summaries_to_config()
        except Exception:
            logger.exception("[apix-meme] 同步数据摘要失败")
        try:
            self.reload_cfg()
        except Exception:
            logger.exception("[apix-meme] 迁移后重载配置失败")

        try:
            self._register_bridge_routes()
        except Exception:
            logger.exception("[apix-meme] 注册桥接路由异常")

        if _HAS_WEB_CONFIG:
            try:
                ok = register_web_apis(self.context)
                if ok:
                    logger.info("[apix-meme] 自定义配置页面路由已注册")
                else:
                    logger.warning("[apix-meme] 自定义配置页面路由注册失败")
            except Exception:
                logger.exception("[apix-meme] 注册自定义配置页面路由异常")

    def _register_bridge_routes(self):
        try:
            from astrbot.api.web import json_response, error_response
        except Exception:
            try:
                from aiohttp import web as _web
                def json_response(data, status_code=200):
                    return _web.json_response(data, status=status_code)
                def error_response(msg, status_code=400):
                    return _web.json_response({"ok": False, "msg": msg}, status=status_code)
            except Exception:
                json_response = lambda d, status_code=200: d
                error_response = lambda m, status_code=400: {"ok": False, "msg": m}

        def _origin_allowed(request) -> bool:
            """
            校验请求来源：
              · 没有 Origin 头 —— 同源请求或非浏览器客户端，放行
              · 有 Origin —— 只允许与本机同源的地址（面板自身）
            """
            try:
                req = request if request is not None else _bound_request()
                if req is None:
                    return True
                headers = getattr(req, "headers", None)
                if not headers:
                    return True
                origin = str(headers.get("Origin", "") or "").strip()
                if not origin:
                    return True
                if origin == "null":
                    return True
                from urllib.parse import urlparse
                host = (urlparse(origin).hostname or "").lower()
                return _host_is_trusted(host)
            except Exception:
                return True

        def _cors_allow_origin(request=None) -> str:
            """
            计算要回显的 Access-Control-Allow-Origin。

            ★ 关键：AstrBot 的插件后台页跑在 iframe 里（srcdoc / blob），
              浏览器给出的 Origin 是**字符串 "null"**。这不是「未知来源」，
              而是本机受信任页面，必须放行，否则所有 fetch 都被 CORS 拦掉。

            只回显：null / 本机地址；其它来源一律不回显（浏览器自然拒绝）。
            """
            try:
                req = request if request is not None else _bound_request()
                headers = getattr(req, "headers", None) if req is not None else None
                origin = str(headers.get("Origin", "") or "").strip() if headers else ""
            except Exception:
                origin = ""
            if not origin:
                return ""
            if origin == "null":
                return "null"
            try:
                from urllib.parse import urlparse
                host = (urlparse(origin).hostname or "").lower()
                if _host_is_trusted(host):
                    return origin
            except Exception:
                pass
            return ""

        def _with_cors(resp, request=None):
            """给响应补上 CORS 头（仅对本机/null 来源回显）"""
            try:
                allow = self._cors_allow_origin(request)
                if allow and hasattr(resp, "headers"):
                    resp.headers["Access-Control-Allow-Origin"] = allow
                    resp.headers["Access-Control-Allow-Methods"] =                         "GET, POST, OPTIONS"
                    resp.headers["Access-Control-Allow-Headers"] =                         "Content-Type, X-Plugin-Token"
                    resp.headers["Vary"] = "Origin"
            except Exception:
                pass
            return resp

        def _options_response(request=None):
            """OPTIONS 预检：回 204 并带上 CORS 头"""
            try:
                from aiohttp import web as _web
                resp = _web.Response(status=204)
            except Exception:
                resp = {"ok": True}
            return self._with_cors(resp, request)

        def _reject_cross_origin(request=None):
            """跨域来源直接拒绝，返回 Response 或 None"""
            if _origin_allowed(request):
                return None
            logger.warning("[apix-meme] 已拒绝来自非本机来源的 Web 请求")
            return _with_cors(json_response(
                {"ok": False, "msg": "拒绝跨域访问"}, status_code=403
            ), request)

        def _require_admin(request=None):
            """
            写操作的额外鉴权。

            说明：这些接口本身已由 AstrBot 面板（需登录）分发，且上面已强制
            拒绝跨域来源，因此这里提供一层**可选的**令牌校验：
              · 后台配置了 `web_admin_token` -> 必须携带匹配的令牌才能写
              · 未配置令牌 -> 依赖「同源 + 面板登录」这层防护
            返回 None 表示通过，否则返回错误响应。
            """
            token_cfg = str(getattr(self, "web_admin_token", "") or "").strip()
            if not token_cfg:
                return None
            try:
                req = request if request is not None else _bound_request()
            except Exception:
                req = request
            given = ""
            try:
                if req is not None:
                    h = getattr(req, "headers", None)
                    if h:
                        given = str(h.get("X-Plugin-Token", "") or "")
            except Exception:
                given = ""
            if given and given == token_cfg:
                return None
            logger.warning("[apix-meme] 写操作令牌校验失败，已拒绝")
            return _with_cors(json_response(
                {"ok": False, "msg": "无权限：令牌缺失或不正确"},
                status_code=403,
            ), request)

        def _bound_request():
            """AstrBot 4.27+ 插件 Web API 的请求对象由框架绑定在模块级代理上，
            而不是通过处理函数的第一个位置参数传入（路由无 <...> 占位符时
            框架只会传 path_params，request 永远是 None）。"""
            try:
                from astrbot.api.web import request as _req
                return _req
            except Exception:
                return None

        def _req_method(src) -> str:
            if src is None:
                return ""
            try:
                return str(getattr(src, "method", "") or "").upper()
            except Exception:
                return ""

        async def _extract_payload(request=None, kwargs=None):
            """按 位置参数 → 框架绑定请求(body/json) → kwargs 顺序提取配置字典。"""
            if isinstance(request, dict) and request:
                return request

            for src in (_bound_request(), request):
                if src is None:
                    continue
                for reader in ("json", "text", "body", "get_json", "get_data"):
                    fn = getattr(src, reader, None)
                    if not callable(fn):
                        continue
                    try:
                        raw = fn()
                        if inspect.isawaitable(raw):
                            raw = await raw
                    except Exception:
                        continue
                    if isinstance(raw, dict) and raw:
                        return raw
                    if isinstance(raw, (bytes, bytearray)):
                        raw = bytes(raw).decode("utf-8", "ignore")
                    if isinstance(raw, str) and raw.strip():
                        try:
                            parsed = json.loads(raw)
                        except Exception:
                            continue
                        if isinstance(parsed, dict) and parsed:
                            return parsed

            if kwargs:
                meta_keys = {"request", "self", "context", "data", "config", "body", "payload"}
                candidate = {k: v for k, v in kwargs.items() if k not in meta_keys}
                if candidate:
                    return candidate
            return None

        async def _get_config_handler(request=None, **kwargs):
            if _req_method(request) == "OPTIONS" or _req_method(_bound_request()) == "OPTIONS":
                return _options_response(request)
            denied = _reject_cross_origin(request)
            if denied is not None:
                return denied
            try:
                schema = {}
                if _HAS_WEB_CONFIG:
                    try:
                        schema = read_schema()
                    except Exception:
                        logger.exception("[apix-meme] read_schema 失败")
                config = self._read_disk_config() or {}
                if not isinstance(config, dict):
                    config = {}
                logger.info(
                    f"[apix-meme] GET /config: schema={len(schema)} config={len(config)}"
                )
                return _with_cors(json_response({"ok": True, "schema": schema, "config": config}), request)
            except Exception as e:
                logger.exception("[apix-meme] GET /config 失败")
                return _with_cors(error_response(f"读取失败: {e}", status_code=500), request)

        async def _save_config_handler(request=None, **kwargs):
            if _req_method(request) == "OPTIONS" or _req_method(_bound_request()) == "OPTIONS":
                return _options_response(request)
            denied = _reject_cross_origin(request)
            if denied is not None:
                return denied
            denied = _require_admin(request)
            if denied is not None:
                return denied

            try:
                logger.info("[apix‑meme] POST /config 被调用")
                data = await _extract_payload(request, kwargs)
                logger.info(f"[apix-meme] POST /config 提取到 payload 类型={type(data).__name__}")

                if isinstance(data, dict):
                    for _wrap in ("data", "config"):
                        if len(data) == 1 and isinstance(data.get(_wrap), dict) and data[_wrap]:
                            data = data[_wrap]
                            break

                if not isinstance(data, dict) or not data:
                    logger.warning("[apix-meme] POST /config 数据为空")
                    return _with_cors(json_response({"ok": False, "msg": "数据为空"}), request)

                old_cfg = self._read_disk_config() or {}
                if not isinstance(old_cfg, dict):
                    old_cfg = {}
                _prev_cfg = dict(old_cfg)
                old_cfg.update(data)

                try:
                    def _bt(v):
                        if isinstance(v, bool):
                            return v
                        return str(v or "").strip().lower() not in (
                            "", "0", "false", "no", "off", "否", "关闭", "关")
                    _was_on = _bt(_prev_cfg.get("reply_tail_enable", False))
                    _now_off = not _bt(old_cfg.get("reply_tail_enable", False))
                    _empty = not any(
                        str(old_cfg.get(_k) or "").strip()
                        for _k in ("reply_tail_api", "reply_tail_text"))
                    if _was_on and _now_off and _empty:
                        old_cfg["reply_tail_enable"] = True
                        logger.warning(
                            "[apix-meme] 面板保存把 reply_tail_enable 下发成 "
                            "false，但尾巴内容为空 -> 保持开启（防止误关）")
                except Exception:
                    pass

                _freed_n = 0
                try:
                    _qq_raw = to_str(
                        old_cfg.get("bank_unfreeze_list") or "").strip()
                    if _qq_raw:
                        _ck = getattr(self, "checkin_module", None)
                        if _ck is not None:
                            for _one in re.split(r"[,，、\s]+", _qq_raw):
                                _one = _one.strip()
                                if _one:
                                    _freed_n += _ck.unfreeze_by_qq(_one)
                            logger.info(f"[apix-meme] 后台批量解冻 {_freed_n} 条"
                                        f"（输入：{_qq_raw}）")
                    old_cfg["bank_unfreeze_list"] = ""
                except Exception:
                    logger.exception("[apix-meme] 后台批量解冻失败")

                save_ok = self._write_disk_config(old_cfg)

                logger.info(f"[apix-meme] 配置保存结果 ok={save_ok}，共 {len(data)} 项")
                return _with_cors(json_response({
                    "ok": bool(save_ok),
                    "freed": _freed_n,
                    "msg": "已保存" if save_ok else "保存失败！",
                    "config": old_cfg,
                }), request)

            except Exception as e:
                logger.exception("[apix-meme] 保存配置接口异常")
                return _with_cors(json_response({"ok": False, "msg": f"保存失败:{e}"}, status_code=500), request)

        async def _preview_status_handler(request=None, **kwargs):
            """返回当前配置下的电脑状态文本，供独立页面预览。"""
            denied = _reject_cross_origin(request)
            if denied is not None:
                return denied
            try:
                group_id = ""
                try:
                    req = _bound_request()
                    q = getattr(req, "query", None)
                    if q is not None:
                        group_id = str(q.get("group_id", "") or "")
                    if not group_id:
                        group_id = str(kwargs.get("group_id", "") or "")
                except Exception:
                    pass

                text = await collect_runtime_status(
                    event=None,
                    group_id=group_id or None,
                    cfg=self._read_disk_config() or {},
                    plugin=self,
                )
                return _with_cors(json_response({
                    "ok": True,
                    "text": text,
                    "group_id": group_id,
                }), request)
            except Exception as e:
                logger.exception("[apix-meme] 预览电脑状态失败")
                return _with_cors(json_response({"ok": False, "msg": f"预览失败: {e}"}, status_code=500), request)

        async def _test_status_push_handler(request=None, **kwargs):
            """按当前（含未保存）配置生成状态文本并立即推送到指定群。"""
            denied = _reject_cross_origin(request)
            if denied is not None:
                return denied
            denied = _require_admin(request)
            if denied is not None:
                return denied
            try:
                if self.status_push_module is None:
                    return _with_cors(json_response({"ok": False, "msg": "定时状态推送模块未加载"}), request)

                data = await _extract_payload(request, kwargs)
                if isinstance(data, dict):
                    for _wrap in ("data", "config"):
                        if len(data) == 1 and isinstance(data.get(_wrap), dict) and data[_wrap]:
                            data = data[_wrap]
                            break
                if not isinstance(data, dict):
                    data = {}

                group_id = str(data.pop("group_id", "") or "").strip()
                if not group_id:
                    enabled = self.status_push_module.list_groups()
                    if enabled:
                        group_id = enabled[0]["group_id"]
                if not group_id:
                    return _with_cors(json_response({
                        "ok": False,
                        "msg": "没有可推送的群：请先在群里用「状态推送开」开启，或把群号作为 group_id 传入",
                    }), request)

                cfg = dict(self._read_disk_config() or {})
                cfg.update(data)

                text = await collect_runtime_status(
                    event=None, group_id=group_id, cfg=cfg, plugin=self
                )
                umo = self.status_push_module.get_umo(group_id)
                images = []
                try:
                    text, images = await self.render_group_text(
                        str(group_id), text)
                except Exception:
                    logger.exception("[apix-meme] 状态推送：输出模式渲染失败，回落纯文本")
                    images = []
                ok = await self._push_mc_notification(group_id, text,
                                                      umo=umo, images=images)
                logger.info(f"[apix-meme] 测试推送群 {group_id} 结果 ok={ok}")
                return _with_cors(json_response({
                    "ok": bool(ok),
                    "group_id": group_id,
                    "msg": "已推送" if ok else "推送失败：该群可能尚未记录 UMO（请先在该群发一条消息）",
                }), request)
            except Exception as e:
                logger.exception("[apix-meme] 测试推送失败")
                return _with_cors(json_response({"ok": False, "msg": f"推送失败: {e}"}, status_code=500), request)

        async def _backup_info_handler(request=None, **kwargs):
            """返回默认备份目录与数据文件清单，供页面展示。"""
            denied = _reject_cross_origin(request)
            if denied is not None:
                return denied
            try:
                from .core.backup import all_data_files, default_backup_dir
                files = []
                for label, path in all_data_files():
                    try:
                        exists = os.path.isfile(path)
                        size = os.path.getsize(path) if exists else 0
                    except Exception:
                        exists, size = False, 0
                    files.append({
                        "label": label,
                        "file": os.path.basename(path),
                        "exists": exists,
                        "size": size,
                    })
                return _with_cors(json_response({
                    "ok": True,
                    "default_dir": default_backup_dir(),
                    "files": files,
                }), request)
            except Exception as e:
                logger.exception("[apix-meme] 读取备份信息失败")
                return _with_cors(json_response({"ok": False, "msg": f"读取失败: {e}"}, status_code=500), request)

        async def _backup_handler(request=None, **kwargs):
            """把数据备份到指定目录（留空则用默认目录）。"""
            denied = _reject_cross_origin(request)
            if denied is not None:
                return denied
            denied = _require_admin(request)
            if denied is not None:
                return denied
            try:
                data = await _extract_payload(request, kwargs)
                if isinstance(data, dict):
                    for _wrap in ("data", "config"):
                        if len(data) == 1 and isinstance(data.get(_wrap), dict) and data[_wrap]:
                            data = data[_wrap]
                            break
                if not isinstance(data, dict):
                    data = {}
                target_dir = str(data.get("target_dir", "") or "").strip()

                from .core.backup import create_backup
                result = create_backup(target_dir)
                if result.get("ok"):
                    logger.info(f"[apix-meme] 数据已备份到 {result.get('path')}")
                return _with_cors(json_response(result), request)
            except Exception as e:
                logger.exception("[apix-meme] 备份失败")
                return _with_cors(json_response({"ok": False, "msg": f"备份失败: {e}"}, status_code=500), request)

        async def _backup_download_handler(request=None, **kwargs):
            """返回全部数据文件的聚合 JSON，供浏览器下载。"""
            denied = _reject_cross_origin(request)
            if denied is not None:
                return denied
            try:
                from .core.backup import build_bundle
                result = build_bundle()
                return _with_cors(json_response(result), request)
            except Exception as e:
                logger.exception("[apix-meme] 生成下载包失败")
                return _with_cors(json_response({"ok": False, "msg": f"生成失败: {e}"}, status_code=500), request)

        async def _import_preview_handler(request=None, **kwargs):
            """预检导入来源，不写入任何文件。"""
            denied = _reject_cross_origin(request)
            if denied is not None:
                return denied
            try:
                data = await _extract_payload(request, kwargs)
                if isinstance(data, dict):
                    for _wrap in ("data", "config"):
                        if len(data) == 1 and isinstance(data.get(_wrap), dict) and data[_wrap]:
                            data = data[_wrap]
                            break
                if not isinstance(data, dict):
                    data = {}
                source = str(data.get("source", "") or "").strip()
                if not source:
                    return _with_cors(json_response({
                        "ok": False, "files": [], "total": 0,
                        "msg": "请填写备份目录或备份包路径",
                    }), request)
                from .core.backup import preview_import
                return _with_cors(json_response(preview_import(source)), request)
            except Exception as e:
                logger.exception("[apix-meme] 导入预检失败")
                return _with_cors(json_response({"ok": False, "files": [], "total": 0,
                                                 "msg": f"预检失败: {e}"}, status_code=500), request)

        async def _import_data_handler(request=None, **kwargs):
            """从备份目录或备份包还原数据（导入前自动备份当前数据）。"""
            denied = _reject_cross_origin(request)
            if denied is not None:
                return denied
            denied = _require_admin(request)
            if denied is not None:
                return denied
            try:
                data = await _extract_payload(request, kwargs)
                if isinstance(data, dict):
                    for _wrap in ("data", "config"):
                        if len(data) == 1 and isinstance(data.get(_wrap), dict) and data[_wrap]:
                            data = data[_wrap]
                            break
                if not isinstance(data, dict):
                    data = {}
                source = str(data.get("source", "") or "").strip()
                mode = str(data.get("mode", "replace") or "replace").strip()
                if mode not in ("replace", "merge_skip"):
                    mode = "replace"
                if not source:
                    return _with_cors(json_response({
                        "ok": False, "imported": [], "skipped": [],
                        "msg": "请填写备份目录或备份包路径",
                    }), request)
                from .core.backup import import_data
                result = import_data(source, mode=mode)
                if result.get("ok"):
                    logger.info(f"[apix-meme] 数据导入完成：{result.get('msg')}")
                return _with_cors(json_response(result), request)
            except Exception as e:
                logger.exception("[apix-meme] 导入失败")
                return _with_cors(json_response({"ok": False, "imported": [], "skipped": [],
                                                 "msg": f"导入失败: {e}"}, status_code=500), request)

        async def _page_handler(request=None, **kwargs):
            if _req_method(request) == "OPTIONS" or _req_method(_bound_request()) == "OPTIONS":
                return _options_response(request)
            denied = _reject_cross_origin(request)
            if denied is not None:
                return denied
            try:
                if _HAS_WEB_CONFIG:
                    try:
                        from .core.web_config import render_page_html
                        html = render_page_html()
                        if html:
                            from aiohttp import web as _web
                            return _web.Response(text=html, content_type="text/html")
                    except Exception:
                        logger.exception("[apix-meme] 渲染页面失败")
                from aiohttp import web as _web
                return _web.Response(text="index.html not found", status=404)
            except Exception as e:
                logger.exception("[apix-meme] GET /page 失败")
                from aiohttp import web as _web
                return _web.Response(text=f"error: {e}", status=500)

        async def _page_extra_handler(request=None, **kwargs):
            if _req_method(request) == "OPTIONS" or _req_method(_bound_request()) == "OPTIONS":
                return _options_response(request)
            denied = _reject_cross_origin(request)
            if denied is not None:
                return denied
            try:
                req = request if request is not None else _bound_request()
                _path = str(getattr(req, "path", "") or "").rstrip("/")
                _name = _path.rsplit("/", 1)[-1].strip().lower()
                from aiohttp import web as _web
                if _name not in _EXTRA_PAGES:
                    return _web.Response(text="page not found", status=404)
                _f = os.path.join(_plugin_dir(), "pages", _name,
                                  "index.html")
                if not os.path.isfile(_f):
                    return _web.Response(text=f"{_name}/index.html not found",
                                         status=404)
                with open(_f, "r", encoding="utf-8") as _fp:
                    _html = _fp.read()
                return _web.Response(text=_html, content_type="text/html")
            except Exception as e:
                logger.exception("[apix-meme] GET /page/<name> 失败")
                from aiohttp import web as _web
                return _web.Response(text=f"error: {e}", status=500)

        def _query_param(request, key: str, default: str = "") -> str:
            """
            从请求里取 GET 查询参数。

            AstrBot 的桥接 request 形态不一（可能 aiohttp request、
            也可能包装过的 dict），所以挨个可能的位置找一遍。
            """
            req = request
            if req is None:
                try:
                    req = _bound_request()
                except Exception:
                    req = None
            if req is None:
                return default
            for attr in ("query", "args", "params", "rel_url"):
                try:
                    q = getattr(req, attr, None)
                    if q is None:
                        continue
                    if hasattr(q, "get"):
                        v = q.get(key)
                        if v is not None:
                            return str(v)
                    g = getattr(q, "getall", None)
                    if callable(g):
                        v = g(key)
                        if v:
                            return str(v[0])
                except Exception:
                    continue
            try:
                if isinstance(req, dict):
                    for holder in (req, req.get("query"), req.get("args"),
                                   req.get("params")):
                        if isinstance(holder, dict) and key in holder:
                            return str(holder[key])
            except Exception:
                pass
            try:
                url = ""
                for attr in ("url", "path_qs", "query_string", "raw_path"):
                    v = getattr(req, attr, None)
                    if v:
                        url = str(v)
                        break
                if url and "?" in url:
                    from urllib.parse import unquote_plus
                    for pair in url.split("?", 1)[1].split("&"):
                        if "=" in pair:
                            k, _, val = pair.partition("=")
                            if k == key:
                                return unquote_plus(val)
            except Exception:
                pass
            return default

        async def _read_json_body(request=None):
            """
            读 POST 请求体。

            ★ 必须 await：AstrBot 桥接过来的请求，body/json 是协程，
              之前用同步 getattr(req, "_dsh_body") 永远拿不到内容，
              导致 POST /data 的 key 恒为空 -> 一直报「未知集合：」。

            ★★ 1.4.2 加固：先走已验证的 `_extract_payload`；
              它万一整体抛异常（例如 `_bound_request()` 返回的框架代理
              在访问 reader 属性时出错），**再用传入的 request 自己读一遍**，
              别直接返回空 dict —— 那会让调用方以为「请求体是空的」。
            """
            try:
                body = await _extract_payload(request, kwargs=None)
                if isinstance(body, dict) and body:
                    return body
            except Exception:
                logger.exception("[apix-meme] 读取请求体失败（走兜底）")

            # ── 兜底：自己从 request 读 ──
            try:
                if isinstance(request, dict) and request:
                    return request
                for reader in ("json", "text", "body"):
                    fn = getattr(request, reader, None)
                    if not callable(fn):
                        continue
                    try:
                        raw = fn()
                        if inspect.isawaitable(raw):
                            raw = await raw
                    except Exception:
                        continue
                    if isinstance(raw, dict) and raw:
                        return raw
                    if isinstance(raw, (bytes, bytearray)):
                        raw = bytes(raw).decode("utf-8", "ignore")
                    if isinstance(raw, str) and raw.strip():
                        try:
                            _p = json.loads(raw)
                        except Exception:
                            continue
                        if isinstance(_p, dict) and _p:
                            return _p
            except Exception:
                logger.debug("[apix-meme] 兜底读取请求体失败", exc_info=True)
            return {}

        async def _get_data_handler(request=None, **kwargs):
            if _req_method(request) == "OPTIONS" or _req_method(_bound_request()) == "OPTIONS":
                return _options_response(request)
            denied = _reject_cross_origin(request)
            if denied is not None:
                return denied
            try:
                logger.info("[apix‑meme] GET /data 被调用")
                cols = {}
                if self.data_store is not None:
                    try:
                        self._init_menu_texts()
                    except Exception:
                        logger.exception("[apix-meme] 自动补齐菜单内容失败")
                    for k in DATA_COLLECTIONS:
                        cols[k] = self.data_store.get(k, autoreload=True)
                return _with_cors(json_response({
                    "ok": True,
                    "collections": cols,
                    "keys": list(DATA_COLLECTIONS),
                    "botStats": self._bot_stats(),
                }), request)
            except Exception as e:
                logger.exception("[apix-meme] GET /data 失败")
                return _with_cors(error_response(f"读取失败: {e}", status_code=500), request)

        async def _bird_resolve_handler(request=None, **kwargs):
            """
            GET /birdResolve?names=a,b&dir=xxx

            按昵称解析小鸟图片（后台「图片自动识别」用）。
            从浏览器同源调用，不依赖渲染服务的跨域策略。
            """
            if _req_method(request) == "OPTIONS" or _req_method(_bound_request()) == "OPTIONS":
                return _options_response(request)
            denied = _reject_cross_origin(request)
            if denied is not None:
                return denied
            try:
                raw = ""
                try:
                    raw = str(_query_param(request, "names") or "")
                except Exception:
                    raw = ""
                dir_ = ""
                try:
                    dir_ = str(_query_param(request, "dir") or "")
                except Exception:
                    dir_ = ""
                if not raw:
                    return _with_cors(error_response("缺少 names", status_code=400), request)
                if not dir_:
                    dir_ = str(getattr(self, "bird_image_dir", "")
                               or BIRD_DIR_DEFAULT).strip()
                names = [x.strip() for x in raw.split(",") if x.strip()]
                resolved, missing = {}, []
                for nm in names[:300]:
                    p = _bird_image_path(nm, dir_)
                    if p:
                        resolved[nm] = os.path.basename(p)
                    else:
                        missing.append(nm)
                logger.info(
                    f"[apix-meme] /birdResolve: 目录={dir_!r} "
                    f"识别 {len(resolved)}/{len(names)}")
                return _with_cors(json_response({
                    "ok": True, "dir": dir_, "resolved": resolved,
                    "missing": missing, "count": len(resolved),
                }), request)
            except Exception as e:
                logger.exception("[apix-meme] /birdResolve 失败")
                return _with_cors(error_response(f"解析失败: {e}", status_code=500), request)

        async def _bank_frozen_handler(request=None, **kwargs):
            """GET/POST /bankFrozen —— 查看冻结账号 / 提前解冻"""
            if _req_method(request) == "OPTIONS" or _req_method(_bound_request()) == "OPTIONS":
                return _options_response(request)
            denied = _reject_cross_origin(request)
            if denied is not None:
                return denied
            try:
                ck = getattr(self, "checkin_module", None)
                if ck is None:
                    return _with_cors(json_response({
                        "ok": False, "msg": "签到模块未加载"}), request)

                # ★★ 1.9.1 修复：本路由**没有路径占位符**，框架传进来的
                #   `request` 是 **None** —— 真正要用的请求对象得走
                #   `_bound_request()`（同一函数判断 OPTIONS 时就是这么做的）。
                #   以前这里只看形参 `request`，导致 POST 的 JSON 体被**完全忽略**：
                #   `qq` 读不到 -> freed 恒为 0 -> 后台点「解除冻结」永远提示
                #   「该账号已不在冻结中」，其实**根本没执行解冻**。
                _req = request
                if _req is None:
                    try:
                        _req = _bound_request()
                    except Exception:
                        _req = None
                params = {}
                try:
                    if _req is not None and getattr(_req, "query", None):
                        params.update({k: _req.query.get(k)
                                       for k in _req.query})
                except Exception:
                    pass
                try:
                    if not params and _req is not None:
                        _j = await _req.json()
                        if isinstance(_j, dict):
                            params.update(_j)
                except Exception:
                    pass

                qq_raw = to_str(params.get("qq") or "").strip()
                gid = to_str(params.get("gid") or "").strip()
                clear = to_str(params.get("clear") or "").strip().lower() in (
                    "1", "true", "yes", "on")

                freed = 0
                if qq_raw:
                    for _one in re.split(r"[,，、\s]+", qq_raw):
                        _one = _one.strip()
                        if not _one:
                            continue
                        if gid:
                            if ck.unfreeze(gid, _one):
                                freed += 1
                        else:
                            freed += ck.unfreeze_by_qq(_one)

                if clear:
                    try:
                        _cfg = self._read_disk_config() or {}
                        if to_str(_cfg.get("bank_unfreeze_list") or "").strip():
                            _cfg["bank_unfreeze_list"] = ""
                            self._write_disk_config(_cfg)
                    except Exception:
                        logger.exception("[apix-meme] 清空 bank_unfreeze_list 失败")

                frozen = ck.list_frozen() or []
                logger.info(f"[apix-meme] /bankFrozen: 解冻 {freed} 条，"
                            f"当前冻结 {len(frozen)} 条")
                return _with_cors(json_response({
                    "ok": True,
                    "freed": freed,
                    "frozen": frozen,
                    "count": len(frozen),
                }), request)
            except Exception as e:
                logger.exception("[apix-meme] /bankFrozen 失败")
                return _with_cors(error_response(f"操作失败: {e}",
                                                 status_code=500), request)

        async def _data_op_handler(request=None, **kwargs):
            """
            GET 方式的「单条数据操作」——**不依赖桥接 POST**。

            背景：插件页跑在 AstrBot 面板的沙箱 iframe 里，POST 必须走
            桥接（bridge.apiPost），一旦桥接不可用就发不出去，而 fetch 兜底
            会被 CORS 拦掉 —— 于是「点删除、保存全部」**静默失败**。
            GET（bridge.apiGet / 同源 fetch）在同样环境下是可用的，
            所以这里提供一个小参数的 GET 通道，专门用于「必须成功」的增删改。

            参数（查询串）：
              key     集合名（如 bird_list）
              op      del / append / update / set
              index   下标（del / update 用）
              row     JSON 字符串（append / update 用）
              rows    JSON 字符串（set 用）
              token   配置了 web_admin_token 时必填
            """
            try:
                req = request if request is not None else _bound_request()

                _body = {}
                if _req_method(req) == "POST":
                    try:
                        _body = await _read_json_body(req) or {}
                    except Exception:
                        _body = {}
                    if not isinstance(_body, dict):
                        _body = {}

                def _q(name, default=""):
                    if name in _body and _body.get(name) is not None:
                        v = _body.get(name)
                        return default if v is None else str(v)
                    try:
                        if req is None:
                            return default
                        args = getattr(req, "args", None)
                        if args is None:
                            return default
                        v = args.get(name)
                        return default if v is None else str(v)
                    except Exception:
                        return default

                token_cfg = str(getattr(self, "web_admin_token", "") or "").strip()
                if token_cfg and _q("token").strip() != token_cfg:
                    logger.warning("[apix-meme] /dataOp 令牌校验失败，已拒绝")
                    return _with_cors(json_response(
                        {"ok": False, "msg": "无权限：令牌缺失或不正确"},
                        status_code=403), request)

                key = _q("key").strip()
                op = (_q("op").strip() or "append").lower()
                if key not in DATA_COLLECTIONS:
                    return _with_cors(json_response(
                        {"ok": False, "msg": f"未知集合：{key}"},
                        status_code=400), request)
                if self.data_store is None:
                    return _with_cors(json_response(
                        {"ok": False, "msg": "数据存储未初始化"},
                        status_code=500), request)

                try:
                    self.data_store.get(key, autoreload=2)
                except Exception:
                    pass

                idx = -1
                try:
                    idx = int(_q("index", "-1"))
                except Exception:
                    idx = -1

                if op == "del":
                    if idx < 0:
                        return _with_cors(json_response(
                            {"ok": False, "msg": "index 无效"},
                            status_code=400), request)
                    ok = self.data_store.remove_at(key, idx)
                elif op == "append":
                    raw = _q("row")
                    row = {}
                    if raw:
                        try:
                            row = json.loads(raw)
                        except Exception:
                            return _with_cors(json_response(
                                {"ok": False, "msg": "row 不是合法 JSON"},
                                status_code=400), request)
                    if not isinstance(row, dict):
                        return _with_cors(json_response(
                            {"ok": False, "msg": "row 必须是对象"},
                            status_code=400), request)
                    ok = self.data_store.append(key, row)
                elif op == "update":
                    raw = _q("row")
                    try:
                        row = json.loads(raw) if raw else {}
                    except Exception:
                        return _with_cors(json_response(
                            {"ok": False, "msg": "row 不是合法 JSON"},
                            status_code=400), request)
                    if not isinstance(row, dict) or idx < 0:
                        return _with_cors(json_response(
                            {"ok": False, "msg": "index/row 无效"},
                            status_code=400), request)
                    ok = self.data_store.update_at(key, idx, row)
                elif op == "set":
                    raw = _q("rows")
                    try:
                        rows = json.loads(raw) if raw else []
                    except Exception:
                        return _with_cors(json_response(
                            {"ok": False, "msg": "rows 不是合法 JSON"},
                            status_code=400), request)
                    if not isinstance(rows, list):
                        return _with_cors(json_response(
                            {"ok": False, "msg": "rows 必须是数组"},
                            status_code=400), request)
                    ok = self.data_store.set(key, rows)
                else:
                    return _with_cors(json_response(
                        {"ok": False, "msg": f"不支持的操作：{op}"},
                        status_code=400), request)

                rows_now = self.data_store.get(key, autoreload=2)
                logger.info("[apix-meme] /dataOp op=%s key=%s index=%s ok=%s -> %d 条",
                            op, key, idx, ok, len(rows_now))
                return _with_cors(json_response({
                    "ok": bool(ok),
                    "msg": "已保存" if ok else "保存失败",
                    "key": key,
                    "count": len(rows_now),
                    "rows": rows_now,
                }), request)
            except Exception as e:
                logger.exception("[apix-meme] /dataOp 失败")
                return _with_cors(json_response(
                    {"ok": False, "msg": f"操作异常：{e}"},
                    status_code=500), request)

        async def _save_data_handler(request=None, **kwargs):
            if _req_method(request) == "OPTIONS" or _req_method(_bound_request()) == "OPTIONS":
                return _options_response(request)
            denied = _reject_cross_origin(request)
            if denied is not None:
                return denied
            denied_admin = _require_admin(request)
            if denied_admin is not None:
                return denied_admin
            try:
                body = await _read_json_body(request)
                logger.info(
                    f"[apix-meme] POST /data 被调用："
                    f"action={body.get('action')!r} key={body.get('key')!r} "
                    f"rows={len(body.get('rows') or []) if isinstance(body.get('rows'), list) else '-'}"
                )
                if not isinstance(body, dict):
                    return _with_cors(error_response("请求体格式错误", status_code=400), request)
                action = str(body.get("action", "") or "set")
                key = str(body.get("key", "") or "").strip()
                if not key:
                    logger.warning(
                        "[apix-meme] POST /data 缺少集合名（前端 bug 或页面过期）"
                    )
                    return _with_cors(error_response(
                        "缺少集合名（请强制刷新页面 Ctrl+Shift+R 后重试）",
                        status_code=400,
                    ), request)
                if key not in DATA_COLLECTIONS:
                    logger.warning(f"[apix-meme] POST /data 未知集合：{key!r}")
                    return _with_cors(error_response(
                        f"未知集合：{key}（可用：{', '.join(DATA_COLLECTIONS)}）",
                        status_code=400,
                    ), request)
                if self.data_store is None:
                    return _with_cors(error_response("数据存储未初始化", status_code=500), request)

                ok = False
                if action == "set":
                    rows = body.get("rows")
                    if not isinstance(rows, list):
                        return _with_cors(error_response("rows 必须是数组", status_code=400), request)
                    if key == "menu_texts":
                        _seen2, _clean2 = set(), []
                        for _r2 in rows:
                            if not isinstance(_r2, dict):
                                continue
                            _m2 = str(_r2.get("id") or "").strip()
                            if not _m2 or _m2 not in MENU_REGISTRY:
                                continue
                            if _m2 in _seen2:
                                continue
                            _seen2.add(_m2)
                            _clean2.append(_r2)
                        if len(_clean2) != len(rows):
                            logger.info("[apix-meme] menu_texts 写入时清洗："
                                        "%d -> %d 条", len(rows), len(_clean2))
                        rows = _clean2
                    ok = self.data_store.set(key, rows)
                elif action == "append":
                    row = body.get("row")
                    if not isinstance(row, dict):
                        return _with_cors(error_response("row 必须是对象", status_code=400), request)
                    ok = self.data_store.append(key, row)
                elif action == "update":
                    try:
                        idx = int(body.get("index"))
                    except Exception:
                        return _with_cors(error_response("index 无效", status_code=400), request)
                    row = body.get("row")
                    if not isinstance(row, dict):
                        return _with_cors(error_response("row 必须是对象", status_code=400), request)
                    ok = self.data_store.update_at(key, idx, row)
                elif action == "remove":
                    try:
                        idx = int(body.get("index"))
                    except Exception:
                        return _with_cors(error_response("index 无效", status_code=400), request)
                    ok = self.data_store.remove_at(key, idx)
                else:
                    return _with_cors(error_response(f"未知操作：{action}", status_code=400), request)

                try:
                    self.reload_cfg()
                except Exception:
                    logger.exception("[apix-meme] 数据变更后重载失败")
                try:
                    self._sync_summaries_to_config()
                except Exception:
                    logger.exception("[apix-meme] 数据变更后同步摘要失败")

                return _with_cors(json_response({
                    "ok": bool(ok),
                    "key": key,
                    "count": self.data_store.count(key),
                    "collections": {k: self.data_store.get(k) for k in DATA_COLLECTIONS},
                }), request)
            except Exception as e:
                logger.exception("[apix-meme] POST /data 失败")
                return _with_cors(error_response(f"保存失败: {e}", status_code=500), request)

        if not hasattr(self.context, "register_web_api"):
            logger.error("[apix-meme] context 没有 register_web_api，无法注册桥接路由")
            return False

        async def _auth_status_handler(request=None, **kwargs):
            """
            GET /auth —— 授权状态列表（后台页面用）。

            返回结构：
                {ok, enable, default_days, default_text, warn_days, cmd,
                 status_cmd, list_cmd, stats,
                 rows: [{gid, state, expire_ts, expire_text, left_days, note}]}

            ★★ 两个必须遵守的约定（都实测踩过）：

              ① 必须用 **astrbot.api.web.json_response**（返回 Starlette
                 JSONResponse，面板能自动解包）。
                 用 aiohttp 的 `web.json_response` 会被面板序列化成**空对象
                 `{}`** —— 页面拿到的就是 `{}`，于是显示成
                 「0 条 / ❌ 已停用 / 提前 undefined 天」。

              ② request 参数在**路由没有 `<...>` 占位符**时永远是 None
                 （框架只传 path_params），所以要用 `_bound_request()`
                 拿框架绑定的那个请求对象。
            """
            if _req_method(request) == "OPTIONS" or _req_method(_bound_request()) == "OPTIONS":
                return _options_response(request)
            denied = _reject_cross_origin(request)
            if denied is not None:
                return denied
            try:
                am = getattr(self, "auth_module", None)
                if am is None:
                    return _with_cors(json_response({
                        "ok": False, "msg": "授权模块未加载"}), request)

                now = int(time.time())
                rows = []
                for gid, r in am.groups.items():
                    ts = int(r.get("expire_ts", 0))
                    if ts == 0:
                        state, left = "never", 0
                    elif ts < 0:
                        state, left = "disabled", -1
                    elif ts <= now:
                        state, left = "expired", (ts - now) / 86400.0
                    else:
                        left = (ts - now) / 86400.0
                        state = ("soon"
                                 if left <= float(getattr(am, "warn_days", 3))
                                 else "active")
                    try:
                        exp = ("" if ts <= 0
                               else time.strftime("%Y-%m-%d %H:%M",
                                                  time.localtime(ts)))
                    except Exception:
                        exp = str(ts)
                    rows.append({
                        "gid": str(gid),
                        "state": state,
                        "expire_ts": ts,
                        "expire_text": exp,
                        "left_days": round(left, 2),
                        "note": str(r.get("note", "") or ""),
                    })
                _ord = {"never": 0, "soon": 1, "active": 2, "expired": 3,
                        "disabled": 4}
                rows.sort(key=lambda x: (_ord.get(x["state"], 9),
                                         x["expire_ts"] or 0))
                try:
                    _dtxt = am.default_rule_text(markdown=False)
                except Exception:
                    _dtxt = ""
                return _with_cors(json_response({
                    "ok": True,
                    "enable": bool(am.enable),
                    "default_days": int(am.default_days),
                    "default_text": _dtxt,
                    "warn_days": float(am.warn_days),
                    "cmd": (getattr(am, "cmd_names", None) or ["授权"])[0],
                    "status_cmd": (getattr(am, "status_names", None)
                                   or ["授权状态"])[0],
                    "list_cmd": (getattr(am, "list_names", None)
                                 or ["授权列表"])[0],
                    "stats": am.stats(),
                    "rows": rows,
                    "now": now,
                }), request)
            except Exception as e:
                logger.exception("[apix-meme] 读取授权状态失败")
                return _with_cors(error_response(f"读取失败: {e}"),
                                  request)
        async def _cmd_check_handler(request=None, **kwargs):
            """
            ★★ 1.5.0：GET /cmdCheck —— **指令冲突检查**（只读）。

            返回：
                { ok, total_names, total_keys, conflict_count,
                  conflicts:  [{name, sources:[{key,desc}]}],  不同配置撞名
                  dup_in_key: [{key, name, desc}],             同键内重复
                  empty_keys: [ key ]                            空配置
                }
            """
            try:
                return _with_cors(json_response(self._check_cmd_conflicts()), request)
            except Exception as e:
                logger.exception("[apix-meme] 指令检查失败")
                return _with_cors(error_response(f"检查失败: {e}",
                                                 status_code=500), request)

        async def _stats_handler(request=None, **kwargs):
            """
            ★★ 1.4.0：GET /stats —— 后台「数据统计」页（**只读**）。

            只返回统计数据，**不提供任何写入入口** —— 页面上也没有保存按钮。

            ★ 隐私说明：本接口的数据**全部在插件本地计算**
              （消息计数 / 群计数 / 日志错误计数 / 授权状态），
              **不采集用户隐私、不上报、不发送到任何第三方**。

            返回：
                recv, send,              累计收到 / 发送消息数
                calls, cmds, errs,       调用次数 / 指令执行数 / 错误次数
                users,                   去重用户数（本地昵称缓存条数）
                join, leave, live_count, 入群 / 退群次数 / 目前加入群数量
                uptime_sec, uptime_text, 在线时长
                auth_valid, auth_expired, auth_total,  未到期 / 到期 / 总授权群数
                now

            ★ 与 /auth 同样的两个约定：
              ① 用 astrbot.api.web.json_response（Starlette JSONResponse）
              ② 无路径占位符时 request 为 None，要用 _bound_request()
            """
            if _req_method(request) == "OPTIONS" or _req_method(_bound_request()) == "OPTIONS":
                return _options_response(request)
            denied = _reject_cross_origin(request)
            if denied is not None:
                return denied
            try:
                st = self._bot_stats()
                return _with_cors(json_response({
                    "ok": True,
                    "ro": True,
                    "recv": int(st.get("recv", 0) or 0),
                    "send": int(st.get("send", 0) or 0),
                    "join": int(st.get("join", 0) or 0),
                    "leave": int(st.get("leave", 0) or 0),
                    "calls": int(st.get("calls", 0) or 0),
                    "cmds": int(st.get("cmds", 0) or 0),
                    "errs": int(st.get("errs", 0) or 0),
                    # ★★ 1.5.0：接口运行时报错次数
                    "api_errs": int(st.get("api_errs", 0) or 0),
                    "users": int(st.get("users", 0) or 0),
                    # ★★ 1.4.2：被拉黑的成员人数
                    "blacklist_count": int(st.get("blacklist_count", 0) or 0),
                    # ★★ 1.4.2：接口系统统计（来自「接口测试」页的实测结果）
                    "api_total": int(st.get("api_total", 0) or 0),
                    "api_ok": int(st.get("api_ok", 0) or 0),
                    "api_bad": int(st.get("api_bad", 0) or 0),
                    "api_tested": bool(st.get("api_tested", False)),
                    "api_test_at": str(st.get("api_test_at") or ""),
                    "live_count": int(st.get("live_count",
                                              st.get("seen_count", 0)) or 0),
                    "uptime_sec": int(st.get("uptime_sec", 0) or 0),
                    "uptime_text": str(st.get("uptime_text") or "—"),
                    "auth_valid": int(st.get("auth_valid", 0) or 0),
                    "auth_expired": int(st.get("auth_expired", 0) or 0),
                    "auth_total": int(st.get("auth_total", 0) or 0),
                    "auth_stats": st.get("auth_stats") or {},
                    "now": str(st.get("now") or ""),
                    "version": str(st.get("version") or ""),
                    # 兼容旧「机器人数据」字段
                    "seen_count": int(st.get("seen_count", 0) or 0),
                    "enabled_count": int(st.get("enabled_count", 0) or 0),
                    "menus": int(st.get("menus", 0) or 0),
                    "schema_keys": int(st.get("schema_keys", 0) or 0),
                    "routes": int(st.get("routes", 0) or 0),
                    "collections": int(st.get("collections", 0) or 0),
                    "feature_on": int(st.get("feature_on", 0) or 0),
                    "feature_total": int(st.get("feature_total", 0) or 0),
                }), request)
            except Exception as e:
                logger.exception("[apix-meme] 读取数据统计失败")
                return _with_cors(error_response(f"读取失败: {e}",
                                                 status_code=500), request)

        async def _api_test_handler(request=None, **kwargs):
            """
            ★★ 1.4.2：接口测试（后台「接口测试」页面用）。

            GET  /apiTest                 -> 列出全部接口规则 + 上次测试结果
            POST /apiTest  {keys:[...]}   -> 测这些（不传或空 = **全部**）
                                             {timeout:15, concurrency:6}

            返回：
                { ok: True, total, ok_count, bad_count, untested, at,
                  items: [ {key, keywords, media_type, url_template, json_field,
                            tested, ok, msg, ms, kind, url} ] }

            ★ 只读操作：只发请求做探活，**不修改任何配置或数据**。
            ★ 隐私说明：请求直接发往**用户自己配置的接口地址**，
              不经过任何第三方中转，也不上报测试结果。
            """
            if _req_method(request) == "OPTIONS" or _req_method(_bound_request()) == "OPTIONS":
                return _options_response(request)
            denied = _reject_cross_origin(request)
            if denied is not None:
                return denied
            try:
                # ★★ 语义约定：
                #   GET  -> 只读：返回当前列表 + 上次结果
                #   POST -> **一定真测**（keys 不传/空 = 全部）
                #
                # ★ 注意：不能用 `if _keys is not None` 判断要不要测 ——
                #   前端「全部测试」传的是空 keys，那样会**整个跳过测试**、
                #   只回一份旧缓存，用户看到「测试完成」但数字没变。
                _keys = None
                _do_test = False
                _timeout = 15
                _conc = 6
                _is_post = (_req_method(request) == "POST" or
                            _req_method(_bound_request()) == "POST")
                if _is_post:
                    _do_test = True
                    body = {}
                    try:
                        body = await _read_json_body(request)
                    except Exception:
                        body = {}
                    if isinstance(body, dict):
                        _k = body.get("keys")
                        # 兼容外层包一层 {data: {...}} 的形态
                        if _k is None and isinstance(body.get("data"), dict):
                            _k = body["data"].get("keys")
                        if isinstance(_k, list) and _k:
                            _keys = [to_str(x) for x in _k if to_str(x)]
                        try:
                            _timeout = max(3, min(120, int(
                                body.get("timeout", 15) or 15)))
                        except Exception:
                            _timeout = 15
                        try:
                            _conc = max(1, min(16, int(
                                body.get("concurrency", 6) or 6)))
                        except Exception:
                            _conc = 6

                _rules = self._api_list_for_test()
                _cache = getattr(self, "_api_test_cache", None) or {}

                if _do_test:
                    logger.info(
                        f"[apix-meme] 接口测试：开始测 "
                        f"{len(_keys) if _keys else len(_rules)} 条"
                        f"（超时 {_timeout}s，并发 {_conc}）")
                    _r = await self._test_apis(keys=_keys, timeout=_timeout,
                                               concurrency=_conc)
                    _cache = getattr(self, "_api_test_cache", None) or {}
                    logger.info(f"[apix-meme] 接口测试完成："
                                f"正常 {_r.get('ok')} / 错误 {_r.get('bad')}")

                items = []
                for r in _rules:
                    k = self._api_rule_key(r)
                    _c = _cache.get(k) or {}
                    items.append({
                        "key": k,
                        "keywords": to_str(r.get("keywords", "")),
                        "media_type": to_str(r.get("media_type", "image")),
                        "url_template": to_str(r.get("url_template", "")),
                        "json_field": to_str(r.get("json_field", "")),
                        "tested": bool(_c),
                        "ok": bool(_c.get("ok", False)),
                        "msg": to_str(_c.get("msg", "")),
                        "ms": int(_c.get("ms", 0) or 0),
                        "kind": to_str(_c.get("kind", "")),
                        "url": to_str(_c.get("url", "")),
                    })
                _ok_n = sum(1 for x in items if x["tested"] and x["ok"])
                _bad_n = sum(1 for x in items if x["tested"] and not x["ok"])
                return _with_cors(json_response({
                    "ok": True,
                    "total": len(items),
                    "ok_count": _ok_n,
                    "bad_count": _bad_n,
                    "untested": len(items) - _ok_n - _bad_n,
                    "at": to_str(getattr(self, "_api_test_at", "") or ""),
                    "items": items,
                }), request)
            except Exception as e:
                logger.exception("[apix-meme] 接口测试失败")
                return _with_cors(error_response(f"测试失败: {e}",
                                                 status_code=500), request)

        routes = [
            (f"/{PLUGIN_ID}/config", _get_config_handler,  ["GET", "OPTIONS"],  "读取配置"),
            (f"/{PLUGIN_ID}/config", _save_config_handler, ["POST", "OPTIONS"], "保存配置"),
            (f"/{PLUGIN_ID}/previewStatus",    _preview_status_handler,    ["GET", "OPTIONS"],  "预览电脑状态"),
            (f"/{PLUGIN_ID}/testStatusPush",   _test_status_push_handler,  ["POST", "OPTIONS"], "测试推送电脑状态"),
            (f"/{PLUGIN_ID}/backupInfo",       _backup_info_handler,       ["GET", "OPTIONS"],  "备份信息"),
            (f"/{PLUGIN_ID}/backup",           _backup_handler,            ["POST", "OPTIONS"], "备份数据到目录"),
            (f"/{PLUGIN_ID}/backupDownload",   _backup_download_handler,   ["GET", "OPTIONS"],  "导出数据供下载"),
            (f"/{PLUGIN_ID}/importPreview",    _import_preview_handler,    ["POST", "OPTIONS"], "预检导入来源"),
            (f"/{PLUGIN_ID}/importData",       _import_data_handler,       ["POST", "OPTIONS"], "导入数据"),
            (f"/{PLUGIN_ID}/birdResolve",      _bird_resolve_handler,      ["GET", "OPTIONS"],  "小鸟图片识别"),
            (f"/{PLUGIN_ID}/bankFrozen",       _bank_frozen_handler,       ["GET", "POST", "OPTIONS"], "银行冻结账号（查看 / 解冻）"),
            (f"/{PLUGIN_ID}/data",             _get_data_handler,          ["GET", "OPTIONS"],  "读取数据集合"),
            (f"/{PLUGIN_ID}/data",             _save_data_handler,         ["POST", "OPTIONS"], "保存数据集合"),
            (f"/{PLUGIN_ID}/dataOp",           _data_op_handler,           ["GET", "OPTIONS"],  "单条数据增删改（GET）"),
            (f"/{PLUGIN_ID}/dataOp",           _data_op_handler,           ["POST", "OPTIONS"], "单条数据增删改（POST）"),
            (f"/{PLUGIN_ID}/page",   _page_handler,        ["GET", "OPTIONS"],  "配置页面"),
            (f"/{PLUGIN_ID}/auth",   _auth_status_handler,  ["GET", "OPTIONS"],  "授权状态列表"),
            (f"/{PLUGIN_ID}/apiTest", _api_test_handler,     ["GET", "POST", "OPTIONS"], "接口测试（只读探活）"),
            (f"/{PLUGIN_ID}/stats",  _stats_handler,        ["GET", "OPTIONS"],  "数据统计（只读，本地）"),
            # ★★ 1.5.0：指令冲突检查（只读）
            (f"/{PLUGIN_ID}/cmdCheck", _cmd_check_handler, ["GET", "OPTIONS"], "指令冲突检查（只读）"),
            (f"/{PLUGIN_ID}/page/{{name}}", _page_extra_handler,
             ["GET", "OPTIONS"], "专属设置页（独立HTML）"),
        ]

        ok_count = 0
        _reg = []
        for route, func, methods, desc in routes:
            try:
                self.context.register_web_api(route, func, methods, desc)
                ok_count += 1
                _reg.append(route)
                logger.info(f"[apix-meme] 桥接路由已注册: {route} {methods}")
            except Exception as e:
                logger.error(f"[apix-meme] 注册失败 {route} {methods}: {e}")
        try:
            self._registered_routes = list(_reg)
        except Exception:
            pass

        return ok_count > 0

    # ======================================================
    # ★★ 1.5.0：**指令冲突检查**
    #   扫描所有 `cmd_*` / `menu_*` 配置里的指令名，找出：
    #     ① 不同配置用了**同一个名字**（真冲突 —— 谁先匹配谁生效）
    #     ② 同一个配置里**名字重复**
    #     ③ 空配置
    #   数据取自**磁盘上的实际配置**（不是默认值），看到的就是运行时真实情况。
    #   ★ 只读：不修改任何配置。
    # ======================================================
    def _check_cmd_conflicts(self) -> dict:
        try:
            cfg = self._read_disk_config() or {}
        except Exception:
            cfg = {}
        sch = {}
        try:
            import json as _json
            import os as _os
            _p = _os.path.join(_os.path.dirname(__file__),
                               "_conf_schema.json")
            with open(_p, encoding="utf-8-sig") as _f:  # ★ 不用 io（main.py 没 import io）
                sch = _json.load(_f)
        except Exception:
            sch = {}

        def _desc(k):
            try:
                return str((sch.get(k) or {}).get("description", "") or k)
            except Exception:
                return k

        names = {}
        dup_in_key = []
        empty_keys = []
        dead_keys = []
        n_keys = 0
        for k, v in (cfg or {}).items():
            ks = str(k)
            if not (ks.startswith("cmd_") or ks.startswith("menu_")):
                continue
            # ★★ 1.9.0：跳过**已废弃的键**（不在 schema 里 = 功能已移除，
            #   只剩残留值，不该报成「指令冲突」）。读不到 schema 就不跳。
            if sch and ks not in sch:
                dead_keys.append(ks)
                continue
            if v is None or isinstance(v, (dict, list, bool, int, float)):
                continue
            raw = str(v)
            if not raw.strip():
                empty_keys.append(ks)
                continue
            n_keys += 1
            seen = []
            for part in raw.replace("\u3001", ",").replace("，", ",").split(","):
                nm = part.strip()
                if not nm:
                    continue
                if nm in seen:
                    dup_in_key.append({"key": ks, "name": nm,
                                       "desc": _desc(ks)})
                    continue
                seen.append(nm)
                names.setdefault(nm, []).append({"key": ks,
                                                 "desc": _desc(ks)})

        conflicts = []
        for nm, srcs in names.items():
            if len({s["key"] for s in srcs}) > 1:
                conflicts.append({"name": nm, "sources": srcs})
        conflicts.sort(key=lambda x: (-len(x["sources"]), x["name"]))
        return {
            "ok": True,
            "total_names": len(names),
            "total_keys": n_keys,
            "conflict_count": len(conflicts),
            "conflicts": conflicts,
            "dup_in_key": dup_in_key,
            "empty_keys": empty_keys,
            "dead_keys": dead_keys,
        }

    def _bot_stats(self) -> dict:
        """
        ★★ 市场版 1.1：后台「机器人数据」视图用的统计。

        包含：收发消息总数 / 启用群数 / 各功能开启群数 / 已记录群数 /
              数据集合数 / 菜单数 / 配置项数 / Web 路由数 / 版本 / 运行时长。
        ★ 任何一项出错都不影响其它项（全部 try 包住）。
        """
        out = {}
        try:
            out["recv"] = max(0, int(GLOBAL_STATS.get("recv", 0) or 0))
            out["send"] = max(0, int(GLOBAL_STATS.get("send", 0) or 0))
        except Exception:
            out["recv"] = out["send"] = 0
        # ★★ 1.4.0：「数据统计」页要的几项（全部**只读**展示，纯本地统计）
        try:
            out["join"] = max(0, int(GLOBAL_STATS.get("join", 0) or 0))
            out["leave"] = max(0, int(GLOBAL_STATS.get("leave", 0) or 0))
            out["calls"] = max(0, int(GLOBAL_STATS.get("calls", 0) or 0))
            out["cmds"] = max(0, int(GLOBAL_STATS.get("cmds", 0) or 0))
            out["errs"] = max(0, int(GLOBAL_STATS.get("errs", 0) or 0))
            # ★★ 1.5.0：接口**运行时**报错次数（用户在群里用接口失败）
            out["api_errs"] = max(0, int(GLOBAL_STATS.get("api_errs", 0) or 0))
        except Exception:
            out["join"] = out["leave"] = 0
            out["calls"] = out["cmds"] = out["errs"] = 0
            out["api_errs"] = 0
        # 用户数量 = 昵称缓存里去重后的用户条数
        try:
            _nc = getattr(self, "_nickname_cache", None)
            _nd = _nc.as_dict() if _nc is not None else {}
            out["users"] = len([k for k in (_nd or {}) if str(k).strip()])
        except Exception:
            out["users"] = 0
        # ★★ 1.4.2：**被拉黑的成员人数**（成员黑名单，纯本地统计）
        try:
            _mb = getattr(self, "member_blacklist", None)
            out["blacklist_count"] = len(
                [k for k in (_mb or {}) if str(k).strip()])
        except Exception:
            out["blacklist_count"] = 0
        # ★★ 1.4.2：**接口系统统计**
        #   api_total = 接口规则总条数
        #   api_ok / api_bad = 最近一次「接口测试」的结果（内存缓存）
        #   api_tested = 是否测过（页面区分「未测试」和「0 个正常」）
        try:
            out["api_total"] = len(self._api_list_for_test())
        except Exception:
            out["api_total"] = 0
        try:
            _cache = getattr(self, "_api_test_cache", None) or {}
            out["api_tested"] = bool(_cache)
            out["api_ok"] = sum(
                1 for v in _cache.values() if (v or {}).get("ok"))
            out["api_bad"] = sum(
                1 for v in _cache.values() if not (v or {}).get("ok"))
            out["api_test_at"] = str(getattr(self, "_api_test_at", "") or "")
        except Exception:
            out["api_tested"] = False
            out["api_ok"] = out["api_bad"] = 0
            out["api_test_at"] = ""
        # 授权分布：未到期群数 / 到期群数
        try:
            _am = getattr(self, "auth_module", None)
            if _am is not None:
                _as = _am.stats()
                _exp = int(_as.get("expired", 0) or 0)
                _tot = int(_as.get("total", 0) or 0)
                out["auth_stats"] = _as
                out["auth_total"] = _tot
                out["auth_expired"] = _exp
                out["auth_valid"] = max(0, _tot - _exp)
            else:
                out["auth_total"] = out["auth_expired"] = out["auth_valid"] = 0
        except Exception:
            out["auth_total"] = out["auth_expired"] = out["auth_valid"] = 0
        try:
            _seen = sorted({str(g) for g in (self._seen_groups or set())
                            if str(g).strip()})
        except Exception:
            _seen = []
        try:
            _enabled = sorted({str(g) for g in (self.allowed_groups or [])
                               if str(g).strip()})
        except Exception:
            _enabled = []
        out["seen_groups"] = _seen
        out["seen_count"] = len(_seen)
        # ★★ 1.4.0：「目前加入群数量」= 见过消息的群 + 当前已授权群 的并集
        #   （机器人实际在服务的群；纯拉群列表拿不到，只能这样估）
        try:
            _live = set(_seen)
            for _g in (getattr(self.auth_module, "groups", None) or {}):
                if str(_g).strip():
                    _live.add(str(_g))
            out["live_groups"] = sorted(_live)
            out["live_count"] = len(_live)
        except Exception:
            out["live_count"] = len(_seen)
        out["enabled_groups"] = _enabled
        out["enabled_count"] = len(_enabled)
        _feat = {}
        try:
            for _k, _v in (getattr(self, "feature_switch", None) or {}).items():
                _feat[str(_k)] = bool(_v)
        except Exception:
            _feat = {}
        out["features"] = _feat
        out["feature_on"] = sum(1 for v in _feat.values() if v)
        out["feature_total"] = len(_feat)
        _gf = {}
        try:
            for _k, _v in (getattr(self, "group_feature_switches",
                                   None) or {}).items():
                if not isinstance(_v, dict):
                    continue
                _on = sum(1 for x in _v.values() if x)
                if _on:
                    _gf[str(_k)] = _on
        except Exception:
            _gf = {}
        out["group_feature_on"] = _gf
        try:
            out["collections"] = len(DATA_COLLECTIONS)
        except Exception:
            out["collections"] = 0
        try:
            from .core.menu_builder import MENU_REGISTRY as _MR
            out["menus"] = len(_MR)
        except Exception:
            out["menus"] = 0
        try:
            out["schema_keys"] = len(self._schema_cache or {})
        except Exception:
            out["schema_keys"] = 0
        try:
            out["routes"] = len(self._registered_routes or [])
        except Exception:
            out["routes"] = 0
        try:
            from .core import system_status as _ss
            out["version"] = str(_ss.get_plugin_version() or "")
        except Exception:
            try:
                from .core.path_utils import read_plugin_version as _rpv
                out["version"] = str(_rpv() or "")
            except Exception:
                out["version"] = ""
        try:
            import time as _t2
            _up = max(0, int(_t2.time() - float(self._boot_ts or 0)))
            out["uptime_sec"] = _up
            out["uptime_text"] = _fmt_uptime(_up)
        except Exception:
            out["uptime_sec"] = 0
            out["uptime_text"] = "—"
        try:
            import time as _t3
            out["now"] = _t3.strftime("%Y-%m-%d %H:%M:%S", _t3.localtime())
        except Exception:
            out["now"] = ""
        return out

    def _config_path(self) -> str:
        return _config_path()

    # ======================================================
    # ★★ 1.4.2：接口系统 —— 测试接口可用性
    # ------------------------------------------------------
    # 接口规则（api_list）里只有 keywords / url_template / media_type，
    # **没有**「成功失败」标记，所以「正常/错误接口数」只能靠**实测**。
    # 这里把结果缓存在内存（self._api_test_cache），统计页即可展示。
    #
    # ★★ 1.5.0：结果**落盘**（api_test_path）——
    #   以前只在内存里，重载插件就没了，再进后台页面看不到上次数据。
    #   现在写完就存，启动时读回，重载 / 重进页面都能看到上次结果。
    #
    # ★ 全部为**只读**探活：不修改任何配置或数据；
    #   落盘的只有「测试结论」（成功/失败/原因/耗时），**不含任何图片**，
    #   请求也只发往**用户自己配置的接口地址**，不经过第三方中转、不上报。
    # ======================================================
    def _load_api_test_cache(self) -> None:
        """
        启动时把上次的接口测试结果读回来。

        文件结构：
            {
              "at": "2026-10-15 20:30:00",
              "results": { "<关键词>|<url>": {...}, ... }
            }
        ★ 读失败一律当「没测过」，绝不抛异常影响插件启动。
        """
        self._api_test_cache = {}
        self._api_test_at = ""
        try:
            p = _api_test_path()
            if not p or not os.path.isfile(p):
                return
            with open(p, "r", encoding="utf-8-sig") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                return
            res = d.get("results")
            if isinstance(res, dict):
                ok = {}
                for k, v in res.items():
                    if isinstance(v, dict) and str(k).strip():
                        ok[str(k)] = v
                self._api_test_cache = ok
            self._api_test_at = to_str(d.get("at", "") or "")
            if self._api_test_cache:
                _n_ok = sum(1 for v in self._api_test_cache.values()
                            if (v or {}).get("ok"))
                logger.info(
                    f"[apix-meme] 已恢复接口测试结果："
                    f"正常 {_n_ok} / 错误 "
                    f"{len(self._api_test_cache) - _n_ok}"
                    f"（测试于 {self._api_test_at or '—'}）")
        except Exception:
            logger.debug("[apix-meme] 读取接口测试结果失败", exc_info=True)
            self._api_test_cache = {}
            self._api_test_at = ""

    def _save_api_test_cache(self) -> bool:
        """
        把接口测试结果写入磁盘。

        ★ 只保留**当前还存在**的接口结果（接口被删掉后，它的旧结果没意义）。
          剪枝只在这里做 —— 启动时 `self.rules` 可能还没加载完，
          那时候剪枝会把结果全清空。

        ★ 写失败只记日志，不影响测试结果展示（内存里依然是新的）。
        """
        try:
            p = _api_test_path()
            if not p:
                return False
            _cache = dict(getattr(self, "_api_test_cache", None) or {})

            # ── 按当前规则剪枝（拿不到规则就不剪，避免误删）──
            try:
                _live = set()
                for _r in self._api_list_for_test():
                    _k = self._api_rule_key(_r)
                    if _k:
                        _live.add(_k)
                if _live:
                    _dropped = [k for k in _cache if k not in _live]
                    for k in _dropped:
                        _cache.pop(k, None)
                    if _dropped:
                        logger.info(
                            f"[apix-meme] 接口测试结果已清理 "
                            f"{len(_dropped)} 条失效记录（接口已删除或改过）")
                        self._api_test_cache = dict(_cache)
            except Exception:
                logger.debug("[apix-meme] 接口测试结果剪枝跳过",
                             exc_info=True)

            d = {
                "at": to_str(getattr(self, "_api_test_at", "") or ""),
                "saved": time.strftime("%Y-%m-%d %H:%M:%S",
                                       time.localtime()),
                "results": _cache,
            }
            _dir = os.path.dirname(p)
            if _dir and not os.path.isdir(_dir):
                os.makedirs(_dir, exist_ok=True)
            # 原子写：先写临时文件再替换，避免写一半断电留个坏文件
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
            return True
        except Exception:
            logger.exception("[apix-meme] 保存接口测试结果失败")
            return False

    @staticmethod
    def _api_rule_key(rule: dict) -> str:
        """
        接口规则的唯一键。

        ★ keywords 可能是**列表**（内存规则）也可能是**字符串**
          （数据文件），统一按逗号拼成稳定字符串 —— 否则 `str(list)`
          会得到 `"['a', 'b']"` 这种带引号括号的形式，
          同一规则在两条路径下算出的键不一致，缓存就对不上了。
        """
        try:
            kw = rule.get("keywords", "")
            if isinstance(kw, (list, tuple, set)):
                kw = ",".join(to_str(x).strip() for x in kw
                              if to_str(x).strip())
            kw = to_str(kw).strip()
            url = to_str(rule.get("url_template", "")).strip()
            return f"{kw}|{url}"
        except Exception:
            return ""

    @staticmethod
    def _kw_to_str(raw) -> str:
        """keywords 统一成逗号分隔的字符串（列表/字符串都吃）"""
        try:
            if isinstance(raw, (list, tuple, set)):
                return ",".join(to_str(x).strip() for x in raw
                                if to_str(x).strip())
            return to_str(raw).strip()
        except Exception:
            return ""

    def _api_list_for_test(self) -> list:
        """
        取当前**生效**的接口规则列表（测试用）。

        优先用内存里的 self.rules（和实际回复走的是同一份），
        拿不到再退回数据文件里的 api_list。
        """
        out = []
        try:
            for r in (getattr(self, "rules", None) or []):
                if not isinstance(r, dict):
                    continue
                url = to_str(r.get("url_template", "")).strip()
                if not url:
                    continue
                out.append({
                    "keywords": self._kw_to_str(r.get("keywords", "")),
                    "url_template": url,
                    "param_count": to_str(r.get("param_count", "1")).strip() or "1",
                    "media_type": (to_str(r.get("media_type", "image")).strip().lower()
                                   or "image"),
                    "json_field": to_str(r.get("json_field", "")).strip(),
                })
        except Exception:
            out = []
        if out:
            return out
        try:
            for r in (self.data_store.get("api_list") or []):
                if not isinstance(r, dict):
                    continue
                url = to_str(r.get("url_template", "")).strip()
                if not url:
                    continue
                out.append({
                    "keywords": self._kw_to_str(r.get("keywords", "")),
                    "url_template": url,
                    "param_count": to_str(r.get("param_count", "1")).strip() or "1",
                    "media_type": (to_str(r.get("media_type", "image")).strip().lower()
                                   or "image"),
                    "json_field": to_str(r.get("json_field", "")).strip(),
                })
        except Exception:
            pass
        return out

    async def _test_one_api(self, rule: dict, timeout: int = 15,
                            qq: str = "") -> dict:
        """
        测试**单条**接口是否可用。

        流程复刻真实调用链（这样结果才有意义）：
            ① 把 url_template 里的 {qq} / {text} 等占位符填上
            ② text  类型 -> fetch_text()
               image/video -> download_media()（真下下来，确认不是空壳）
            ③ 返回 {"ok", "msg", "ms", "kind", "url"}

        ★ 只读操作：只发请求、不写任何配置、不改数据。
        """
        t0 = time.time()
        res = {"ok": False, "msg": "", "ms": 0, "kind": "", "url": ""}
        url_tpl = to_str(rule.get("url_template", "")).strip()
        if not url_tpl:
            res["msg"] = "规则里没填接口地址"
            return res
        media = (to_str(rule.get("media_type", "image")).strip().lower()
                 or "image")
        jf = to_str(rule.get("json_field", "")).strip()

        # ① 填占位符
        #   ★★ 关键：**按占位符类型填对应的测试值**，不能一律填 QQ 号。
        #   `{text}` 是「用户输入的文本」（如天气接口要的是**城市名**），
        #   一律填 `10001` 会让天气接口返回 404「没找到城市 10001」，
        #   看着像接口坏了，其实是测试参数不对。
        _qq = to_str(qq).strip() or "10001"
        _text = "北京"
        try:
            need = max(1, int(rule.get("param_count", "1") or 1))
        except Exception:
            need = 1
        try:
            _types = detect_param_types(url_tpl, need)
        except Exception:
            _types = ["any"] * need
        params = []
        for _i in range(need):
            _t = (_types[_i] if _i < len(_types) else "any")
            params.append(_text if _t == "text" else _qq)
        try:
            url = build_url(url_tpl, params)
        except Exception:
            url = url_tpl
            for ph in ("{qq}", "{1}", "{0}", "{pic}"):
                url = url.replace(ph, _qq)
            for ph in ("{text}", "{text1}"):
                url = url.replace(ph, _text)
        for _i in range(1, 6):
            _t = (_types[_i - 1] if (_i - 1) < len(_types) else "any")
            _v = _text if _t == "text" else _qq
            url = url.replace(f"{{text{_i}}}", _v).replace(f"{{qq{_i}}}", _v)
        res["url"] = url

        if re.search(r"\{[^}]*\}", url):
            res["msg"] = f"地址里还有没替换的占位符：{url[:60]}"
            res["ms"] = int((time.time() - t0) * 1000)
            return res

        # ② 真发请求
        try:
            if media == "text":
                txt = await fetch_text(url, timeout=timeout, json_field=jf)
                txt = to_str(txt).strip()
                if not txt:
                    res["msg"] = "接口返回了空内容"
                else:
                    res["ok"] = True
                    res["kind"] = "text"
                    res["msg"] = f"正常（返回 {len(txt)} 字）：{txt[:60]}"
            else:
                path, kind = await download_media(
                    url, timeout=timeout,
                    expect=media if media in ("image", "video") else "",
                    json_field=jf)
                if not path:
                    res["msg"] = "下载失败（没拿到文件）"
                else:
                    try:
                        size = os.path.getsize(path)
                    except Exception:
                        size = 0
                    if size <= 0:
                        res["msg"] = "下载到了空文件"
                    else:
                        res["ok"] = True
                        res["kind"] = kind or media
                        res["msg"] = (f"正常（{kind or media}，"
                                      f"{size / 1024.0:.0f} KB）")
                    try:
                        os.remove(path)
                    except Exception:
                        pass
        except Exception as e:
            res["msg"] = f"{type(e).__name__}: {str(e)[:110]}"
        res["ms"] = int((time.time() - t0) * 1000)
        return res

    async def _test_apis(self, keys=None, timeout: int = 15,
                         concurrency: int = 6) -> dict:
        """
        批量测试接口（并发跑，避免几十条串行太慢）。

        keys: 只测这些键；None = 全部
        返回 {"total", "ok", "bad", "at", "results"}
        """
        rules = self._api_list_for_test()
        todo = []
        for r in rules:
            k = self._api_rule_key(r)
            if not k:
                continue
            if keys and k not in keys:
                continue
            todo.append((k, r))
        logger.info(f"[apix-meme] 接口测试：待测 {len(todo)} 条"
                    f"（规则共 {len(rules)} 条"
                    f"{'，仅指定 ' + str(len(keys)) + ' 条' if keys else '，全部'}"
                    f"，并发 {concurrency}）")

        results = {}
        sem = asyncio.Semaphore(max(1, min(16, int(concurrency or 6))))

        async def _one(k, r):
            async with sem:
                try:
                    return k, await self._test_one_api(r, timeout=timeout)
                except Exception as e:
                    return k, {"ok": False, "ms": 0, "kind": "",
                               "msg": f"{type(e).__name__}: {str(e)[:110]}"}

        if todo:
            pairs = await asyncio.gather(*[_one(k, r) for k, r in todo],
                                         return_exceptions=False)
            for k, v in pairs:
                results[k] = v

        try:
            if self._api_test_cache is None:
                self._api_test_cache = {}
            self._api_test_cache.update(results)
            self._api_test_at = time.strftime("%Y-%m-%d %H:%M:%S",
                                              time.localtime())
        except Exception:
            pass

        # ★★ 1.5.0：**落盘** —— 重载插件 / 重进后台页面还能看到这次结果
        self._save_api_test_cache()

        ok_n = sum(1 for v in results.values() if v.get("ok"))
        return {
            "total": len(results),
            "ok": ok_n,
            "bad": len(results) - ok_n,
            "at": str(self._api_test_at or ""),
            "results": results,
        }

    def _build_text_avatar_result(self, event, text: str, avatar_path: str):
        """把文本与头像组成 chain，交给 yield event.chain_result 发送"""
        chain = []
        if text:
            chain.append(Plain(text))
        if avatar_path:
            try:
                chain.append(Image.fromFileSystem(avatar_path))
            except Exception:
                logger.exception("[apix-meme] 加载头像失败")
        return chain

    async def _build_text_avatar_result_async(self, event, text: str,
                                              avatar_path: str):
        """
        按本群「输出模式」组装「文字 + 头像」消息。

        text 模式  -> 文字 + 头像（原行为）
        image 模式 -> 文字渲染成图片 + 头像；渲染失败回落文字
        """
        om = getattr(self, "output_mode_module", None)
        if om is None:
            return self._build_text_avatar_result(event, text, avatar_path)
        try:
            _gid = _get_group_id_from_event(event) or ""
            mode = om.mode_of(_gid)
        except Exception:
            mode = "text"
        if not str(mode).startswith("image"):
            return self._build_text_avatar_result(event, text, avatar_path)
        img_path = ""
        try:
            img_path = await om.render_husband_card(text, avatar_path or "")
        except Exception:
            logger.exception("[apix-meme] 老公卡片渲染失败，回落文字+头像")
        if img_path:
            try:
                return [Image.fromFileSystem(img_path)]
            except Exception:
                logger.exception("[apix-meme] 加载老公卡片失败")
        return self._build_text_avatar_result(event, text, avatar_path)

    def _is_group_feature_enable(self, group_id: str, feature_key: str) -> bool:
        if not group_id:
            return True
        gid = str(group_id)
        group_cfg = self.group_feature_switches.get(gid, {})
        return bool(group_cfg.get(feature_key, True))

    def _should_require_at(self, group_id: str) -> bool:
        if group_id and str(group_id) in self.group_must_at_bot:
            return bool(self.group_must_at_bot[str(group_id)])
        return bool(self.must_at_bot)

    def _key_pic_suffix(self) -> str:
        """
        ★★ 1.4.3：拼「pic 占位」后缀。

        ★ 以前这里会读后台的「API 默认 KEY」拼成 `&key=xxx&pic={pic}`。
          现在**接口地址由用户自己填完整**（含 key），插件不再自动拼 key，
          所以这里只返回 `&pic={pic}`。
        """
        return "&pic={pic}"

    async def _check_admin(self, event) -> bool:
        if self.admin_commands is None:
            try:
                return event.is_admin()
            except Exception:
                return False
        return await self.admin_commands.check_admin(event)

    async def _push_mc_notification(self, group_id: str, text: str, umo: str = "",
                                    images=None) -> bool:
        """
        主动推送一条消息到群。

        images: 可选，本地图片路径列表（图片模式下渲染好的图），
                会附在文字之后一起发送。
        """
        return await push_message(
            self.context,
            group_id,
            text,
            umo=umo,
            hourly_chime_module=self.hourly_chime_module,
            minecraft_module=self.minecraft_module,
            images=images,
        )

    async def render_group_text(self, group_id, text: str,
                                with_tail: bool = True,
                                max_w: int = 0) -> tuple:
        """
        按指定群的「输出模式」渲染（供定时/主动推送用）。

        返回 (要发的文字, 图片路径列表)：
          · text 模式          -> (原文, [])
          · image 模式渲染成功 -> ("", [图路径])   ← 只发图，不重复发文字
          · image 模式渲染失败 -> (原文, [])       ← 回落到纯文本

        ★ with_tail=True 时**把尾巴单独传给渲染端**
          （渲染端画在收尾横线下面，且不会加宽卡片）。
          ★ 这些主动推送不走事件管线，所以必须在这里自己取。
        ★ max_w：卡片宽度上限（整点报时传大一点，一行放得下更好看）
        """
        om = getattr(self, "output_mode_module", None)
        if om is None or not text:
            return text, []
        try:
            from .core.path_utils import strip_md as _smd2
        except Exception:
            try:
                from core.path_utils import strip_md as _smd2
            except Exception:
                _smd2 = None
        if _smd2 is not None:
            text = _smd2(text)
        try:
            from .core.path_utils import with_bottom_bar as _wbb2
        except Exception:
            try:
                from core.path_utils import with_bottom_bar as _wbb2
            except Exception:
                _wbb2 = None
        if _wbb2 is not None:
            text = _wbb2(text)
        _tail = ""
        if with_tail:
            try:
                _rt = getattr(self, "reply_tail", None)
                if _rt is not None and _rt.enable:
                    _tail = await _rt.tail_text()
            except Exception:
                logger.exception("[apix-meme] 推送取尾巴失败")
        try:
            mode = om.mode_of(group_id)
        except Exception:
            return text, []
        if not str(mode).startswith("image"):
            if _tail:
                return text + "\n" + _tail, []
            return text, []
        try:
            p = await om.render_to_path(
                text, "plain", tail=_tail, max_w=max_w,
                theme="white" if mode == "image_light" else "")
        except Exception:
            logger.exception("[apix-meme] 文字转图失败，回落纯文本")
            if _tail:
                return text + "\n" + _tail, []
            return text, []
        if p:
            return "", [p]
        if _tail:
            return text + "\n" + _tail, []
        return text, []

    def _read_disk_config(self) -> dict:
        """
        读插件配置。

        ★ 以 plugin_data（插件自己写的）为准，
          但用 AstrBot 面板配置**补齐它没有的键** ——
          面板注册的新配置项（如 bird_image_dir）不会自己写进 plugin_data，
          不补的话这些键读出来是 None。
        """
        disk = read_disk_config()
        if not isinstance(disk, dict):
            disk = {}
        panel = getattr(self, "config", None)
        if not isinstance(panel, dict) or not panel:
            try:
                panel = read_astrbot_panel_config()
            except Exception:
                panel = {}
        if isinstance(panel, dict) and panel:
            merged = {}
            merged.update(panel)
            merged.update(disk)
            return merged
        return disk

    def _sync_to_astrbot_config(self, updates: dict = None) -> bool:
        """
        把插件侧的配置同步进 AstrBot 的配置（面板 data/config）。

        ★ 背景（重要）：
          插件自己存一份配置在 plugin_data/，后台面板存一份在 data/config/。
          如果只写自己那份，面板一保存就会用旧值把插件侧的改动覆盖掉
          （例如群内「切换输出模式 图片」后，面板保存一下图片模式就没了）。

        做法：面板配置里缺的键用插件侧补齐，updates 里的键显式覆盖，
              然后调 AstrBotConfig.save_config() 落盘。
        """
        cfg = getattr(self, "config", None)
        if not isinstance(cfg, dict):
            return False
        try:
            merged = {}
            disk = self._read_disk_config()
            if isinstance(disk, dict):
                merged.update(disk)
            merged.update(cfg)
            if isinstance(updates, dict):
                merged.update(updates)
            cfg.clear()
            cfg.update(merged)
            save = getattr(cfg, "save_config", None)
            if callable(save):
                save()
                logger.info("[apix-meme] 已同步到 AstrBot 配置（面板）")
                return True
            logger.warning("[apix-meme] self.config 不支持 save_config，仅在内存生效")
            return False
        except Exception:
            logger.exception("[apix-meme] 同步到 AstrBot 配置失败")
            return False

    async def get_config(self):
        """
        前端 AstrBotPluginPage.apiGet("getConfig") 会调用这里。
        返回 {"ok": True, "schema": {...}, "config": {...}}
        """
        try:
            schema = {}
            if _HAS_WEB_CONFIG:
                try:
                    schema = read_schema()
                except Exception:
                    logger.exception("[apix-meme] read_schema 失败")
            try:
                if isinstance(schema, dict) and schema:
                    self._schema_cache = schema
            except Exception:
                pass

            config = self._read_disk_config() or {}
            if not isinstance(config, dict):
                config = {}

            logger.info(
                f"[apix-meme] get_config: schema={len(schema)} 项, "
                f"config={len(config)} 项, path={_config_path()}"
            )
            return {"ok": True, "schema": schema, "config": config}
        except Exception:
            logger.exception("[apix-meme] get_config 失败")
            return {"ok": False, "msg": "读取失败", "schema": {}, "config": {}}

    async def save_config(self, data=None, **kwargs):
        """
        前端 AstrBotPluginPage.apiPost("saveConfig", {...}) 会调用这里。
        兼容 AstrBot 各种传参方式：
          - save_config({...})
          - save_config(data={...})
          - save_config(config={...})
          - save_config(body={...})
          - 甚至 kwargs 本身就是配置键值对
        """
        try:
            logger.info(
                f"[apix-meme] save_config 收到 type={type(data).__name__}, "
                f"kwargs_keys={list(kwargs.keys()) if kwargs else []}"
            )

            payload = None

            if isinstance(data, dict) and data:
                payload = data
                logger.info(f"[apix-meme] save_config 使用位置参数，{len(payload)} 项")

            if not payload and kwargs:
                for k in ("data", "config", "body", "payload"):
                    v = kwargs.get(k)
                    if isinstance(v, dict) and v:
                        inner = v.get("data") if "data" in v and len(v) == 1 else v
                        if isinstance(inner, dict) and inner:
                            payload = inner
                            logger.info(f"[apix-meme] save_config 命中 kwargs[{k}]，{len(payload)} 项")
                            break
                    if isinstance(v, str) and v.strip():
                        try:
                            import json as _json
                            parsed = _json.loads(v)
                            if isinstance(parsed, dict) and parsed:
                                payload = parsed
                                logger.info(f"[apix-meme] save_config 命中 kwargs[{k}] 字符串，{len(payload)} 项")
                                break
                        except Exception:
                            pass

            if not payload and kwargs:
                meta_keys = {"request", "self", "context", "data", "config", "body", "payload"}
                candidate = {k: v for k, v in kwargs.items() if k not in meta_keys}
                if candidate:
                    payload = candidate
                    logger.info(f"[apix-meme] save_config 命中 kwargs 自身，{len(payload)} 项")

            if not isinstance(payload, dict) or not payload:
                logger.warning("[apix-meme] save_config 数据为空，拒绝保存")
                return {"ok": False, "msg": "保存数据为空"}

            if "data" in payload and isinstance(payload["data"], dict) and len(payload) == 1:
                payload = payload["data"]
            if "config" in payload and isinstance(payload["config"], dict) and len(payload) == 1:
                payload = payload["config"]

            logger.info(f"[apix-meme] save_config 待写入 keys={list(payload.keys())}")

            existing = self._read_disk_config() or {}
            if not isinstance(existing, dict):
                existing = {}
            existing.update(payload)
            ok = self._write_disk_config(existing)
            if ok:
                self._sync_to_astrbot_config(payload)
            return {"ok": bool(ok), "msg": "已保存" if ok else "保存失败"}
        except Exception:
            logger.exception("[apix-meme] save_config 失败")
            return {"ok": False, "msg": "保存异常"}

    def _write_disk_config(self, data: dict) -> bool:
        ok = write_disk_config(data)
        if ok:
            logger.info(f"[apix-meme] 已写入磁盘: {_config_path()}")
        return ok

    def _migrate_old_format(self):
        try:
            disk_cfg = self._read_disk_config()
            if not disk_cfg:
                return
            api_list = disk_cfg.get("api_list", [])
            if not isinstance(api_list, list):
                return
            changed = False
            new_list = []
            for item in api_list:
                if not isinstance(item, dict):
                    continue
                kw = to_str(item.get("keywords", "")).strip()
                url_template = to_str(item.get("url_template", "")).strip()
                param_count = to_str(item.get("param_count", "")).strip()
                old_meme = to_str(item.get("meme", "")).strip()
                if not kw:
                    continue
                if not url_template and old_meme:
                    url_template = (
                        f"{self.BASE_URL}?meme={old_meme}"
                        + self._key_pic_suffix()
                    )
                    param_count = param_count or "1"
                    changed = True
                elif url_template and "meme=&" in url_template and old_meme:
                    url_template = url_template.replace(
                        "meme=&", f"meme={old_meme}&"
                    )
                    changed = True
                if not url_template:
                    continue
                if not param_count:
                    param_count = "1"
                    changed = True
                if "meme" in item:
                    changed = True
                new_list.append({
                    "__template_key": "meme_rule",
                    "keywords": kw,
                    "url_template": url_template,
                    "param_count": param_count,
                    "media_type": to_str(item.get("media_type", "image")).strip().lower() or "image",
                    "json_field": to_str(item.get("json_field", "")).strip(),
                })
            if changed:
                disk_cfg["api_list"] = new_list
                self._write_disk_config(disk_cfg)
                logger.info(f"[apix-meme] 配置已自动迁移到新格式，共 {len(new_list)} 条规则")
        except Exception:
            logger.exception("[apix-meme] 自动迁移失败")

    def _write_group_list(self, cfg_key: str, group_id: str, enabled: bool) -> bool:
        """
        把某个群号加入/移出后台「生效的群号」名单（key=cfg_key），并立即生效。
        白名单语义：enabled=True 加入，False 移除。
        供群里的开关指令（整点报时开/关、状态推送开/关、读报开/关）复用。
        """
        try:
            gid = str(group_id).strip()
            if not gid or not gid.isdigit():
                return False
            path = _config_path()
            cfg = {}
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8-sig") as f:
                    cfg = json.load(f) or {}
            if not isinstance(cfg, dict):
                cfg = {}
            gids = list(parse_gid_whitelist(cfg.get(cfg_key, "")))
            if enabled:
                if gid not in gids:
                    gids.append(gid)
            else:
                if gid in gids:
                    gids.remove(gid)
            cfg[cfg_key] = ",".join(gids)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            return True
        except Exception:
            logger.exception(f"[apix-meme] 写回后台群号名单失败：{cfg_key}")
            return False

    def _write_music_source(self, code: str) -> bool:
        """把点歌默认音源写进后台配置（远程切换后重启仍生效）"""
        code = str(code or "").strip().lower()
        if not code:
            return False
        try:
            if (self.music_module is not None
                    and not self.music_module.is_valid_source(code)):
                logger.warning(f"[apix-meme] 拒绝写入无效音源：{code!r}")
                return False
        except Exception:
            pass
        try:
            existing = self._read_disk_config() or {}
            if not isinstance(existing, dict):
                existing = {}
            existing["music_default_source"] = code
            self._write_disk_config(existing)
            try:
                if isinstance(self.config, dict):
                    self.config["music_default_source"] = code
            except Exception:
                pass
            return True
        except Exception:
            logger.exception("[apix-meme] 写回默认音源失败")
            return False

    def _write_list_to_config(self, cfg_key: str, rows: list, template_key: str) -> bool:
        """把一组 template_list 行写回后台配置，并同步内存 config。"""
        try:
            existing = self._read_disk_config() or {}
            existing[cfg_key] = [
                dict(r, __template_key=template_key) for r in (rows or [])
            ]
            self._write_disk_config(existing)
            try:
                if isinstance(self.config, dict):
                    self.config[cfg_key] = existing[cfg_key]
            except Exception:
                pass
            return True
        except Exception:
            logger.exception(f"[apix-meme] 写回配置失败：{cfg_key}")
            return False

    def _migrate_data_to_store(self):
        """
        把 1.7.2 及以前存在后台配置里的数据搬到独立数据文件（幂等）。
        搬完后把后台配置里对应的键清空，避免后台越来越臃肿。
        """
        if self.data_store is None:
            return
        try:
            existing = self._read_disk_config() or {}
            if not isinstance(existing, dict):
                return
            result = self.data_store.migrate_from_config(existing)
            moved = result.get("moved") or {}
            if not moved:
                return
            for key in moved:
                existing[key] = ""
            self._write_disk_config(existing)
            try:
                if isinstance(self.config, dict):
                    self.config.update(existing)
            except Exception:
                pass
            logger.info(
                f"[apix-meme] 数据迁移完成，共 {len(moved)} 组："
                + "、".join(f"{k}({v})" for k, v in moved.items())
            )
        except Exception:
            logger.exception("[apix-meme] 读取配置迁移数据失败")

    _PET_WORK_V1_NAMES = (
        "在街角发传单", "帮邻居看店", "送外卖跑单", "当图书馆管理员",
        "参加宠物选美", "给小朋友当玩伴", "去码头搬货", "做直播当网红",
        "帮侦探找线索", "上山采药",
    )
    _PET_WORK_SIGS = None

    @classmethod
    def _pet_work_builtin_signatures(cls) -> list:
        """返回历史内置上班事件集合的清单（用于识别「这是默认值」）"""
        if cls._PET_WORK_SIGS is not None:
            return cls._PET_WORK_SIGS
        try:
            from .core.pet import DEFAULT_WORK_EVENTS as _DEF
        except Exception:
            try:
                from core.pet import DEFAULT_WORK_EVENTS as _DEF
            except Exception:
                _DEF = []
        cur = sorted(str(e[0]) for e in (_DEF or []) if e)
        out = []
        if cur:
            out.append(cur)
        v1 = set(cls._PET_WORK_V1_NAMES)
        v2 = [x for x in cur if x not in v1]
        if v2:
            out.append(sorted(v2))
        old = sorted(x for x in cls._PET_WORK_V1_NAMES if x in set(cur))
        if old:
            out.append(old)
        cls._PET_WORK_SIGS = out
        return out

    def _upgrade_pet_work_events(self):
        """
        上班事件升级：把内置默认刷新成最新的一套（1.7.6 = 120 条、收益更高）。

        ★ 安全策略：只有当数据文件里存的**就是某版内置默认**（对比事件名集合）
          时才替换；用户自己增删改过的内容一律保留。
        · 只在登记的版本低于 target 时执行一次
        · 执行前把整个数据文件备份成 *_data.backup_<时间戳>.json
        """
        if self.data_store is None:
            return
        target = 3
        try:
            if int(getattr(self.data_store, "pet_work_events_ver", 0) or 0) >= target:
                return
        except Exception:
            pass

        try:
            _existing = self.data_store.get("pet_work_events", autoreload=2) or []
        except Exception:
            _existing = []
        _names = sorted(str(r.get("event") or "") for r in _existing
                        if isinstance(r, dict) and str(r.get("event") or ""))
        _builtin = self._pet_work_builtin_signatures()
        _is_builtin = bool(_names) and any(_names == sig for sig in _builtin)

        if _existing and not _is_builtin:
            logger.info(
                "[apix-meme] 上班事件是你自己改过的（%d 条），"
                "本次升级**不覆盖**；想用新的内置事件可在后台清空该列表",
                len(_existing))
            try:
                self.data_store.pet_work_events_ver = target
                self.data_store.save()
            except Exception:
                pass
            return

        try:
            import shutil
            import time as _t
            src = getattr(self.data_store, "path", "") or ""
            if src and os.path.exists(src):
                bak = f"{src}.backup_{_t.strftime('%Y%m%d_%H%M%S')}.json"
                shutil.copy2(src, bak)
                logger.info(f"[apix-meme] 上班事件升级前已备份：{os.path.basename(bak)}")
        except Exception:
            logger.exception("[apix-meme] 备份数据文件失败（继续升级）")
        try:
            pet = getattr(self, "pet_module", None)
            try:
                from .core.pet import DEFAULT_WORK_EVENTS as _DEF
            except Exception:
                try:
                    from core.pet import DEFAULT_WORK_EVENTS as _DEF
                except Exception:
                    _DEF = []
            rows = [{"event": e, "exp_min": str(a), "exp_max": str(b),
                     "score_min": str(c), "score_max": str(d)}
                    for (e, a, b, c, d) in (_DEF or [])]
            if rows:
                self._save_collection("pet_work_events", rows)
                if pet is not None:
                    try:
                        pet.work_events = [
                            {"event": e, "exp_min": a, "exp_max": b,
                             "score_min": c, "score_max": d}
                            for (e, a, b, c, d) in (_DEF or [])]
                    except Exception:
                        pass
                logger.info(
                    f"[apix-meme] 上班事件已升级为内置 {len(rows)} 条"
                    f"（原数据已备份）"
                )
            else:
                logger.warning("[apix-meme] 内置上班事件表为空，跳过升级")
        except Exception:
            logger.exception("[apix-meme] 写入新的上班事件失败")
        try:
            self.data_store.pet_work_events_ver = target
            self.data_store.save()
        except Exception:
            logger.exception("[apix-meme] 登记上班事件版本失败")

    _STRAY_MARKERS = ("cmd_config.json", "t2i_templates")

    def _cleanup_stray_astrbot_data(self) -> bool:
        """
        清理 AstrBot 误写到「插件根目录/data」的内容。

        原因：AstrBot 的 TemplateManager 一实例化就会往
        get_astrbot_data_path() 里写 data/cmd_config.json 与
        data/t2i_templates/*.html。而 AstrBot 的根目录解析依赖
        环境变量 ASTRBOT_DESKTOP_CLIENT=1，缺失时会退回「当前工作目录」，
        于是这些文件偶尔会落到插件目录里（本插件从不读它们）。

        安全约束（任一不满足就放弃清理，避免误删）：
          · 真正的数据目录必须已存在（说明路径解析正常）
          · 待清理路径必须与数据目录不同、且不互相包含
          · 待清理路径必须确实在插件目录下
          · 只认「AstrBot 特征文件」；一旦发现 plugin_data/ 就判定
            可能是本插件兜底数据，直接放弃
        """
        try:
            import shutil

            stray = os.path.join(_plugin_dir(), "data")
            if not os.path.isdir(stray):
                return False

            def _real(p):
                return os.path.realpath(os.path.abspath(p))

            stray_r = _real(stray)
            live_r = _real(_plugin_data_dir())
            plugin_r = _real(_plugin_dir())

            if not os.path.isdir(live_r):
                return False
            if (stray_r == live_r
                    or stray_r.startswith(live_r + os.sep)
                    or live_r.startswith(stray_r + os.sep)):
                return False
            if not stray_r.startswith(plugin_r + os.sep):
                return False
            if os.path.isdir(os.path.join(stray_r, "plugin_data")):
                logger.info(
                    "[apix-meme] 插件目录下的 data/plugin_data 存在，"
                    "按兜底数据处理，不清理"
                )
                return False
            names = set(os.listdir(stray_r))
            if not any(m in names for m in self._STRAY_MARKERS):
                return False

            shutil.rmtree(stray_r)
            logger.info(
                f"[apix-meme] 已清理 AstrBot 误写到插件目录的内容："
                f"{stray_r}（含 {sorted(names)}）"
            )
            return True
        except Exception:
            logger.exception("[apix-meme] 清理误写目录失败")
            return False

    def _sync_defaults_to_store(self):
        """
        首次运行时把代码里内置的默认数据写进独立数据文件（已存在则不覆盖，
        避免冲掉用户自己改过的内容）。这样打开独立配置页面就能直接看到并编辑。
        覆盖：宠物图鉴 / 上班事件 / 物品 / 接口规则 / 运势 等内置默认。
        """
        if self.data_store is None:
            return
        try:
            pet = getattr(self, "pet_module", None)
            if pet is not None:
                if not self.data_store.get("pet_list") and pet.catalog:
                    self._save_collection("pet_list", [
                        {"name": c.get("name", ""), "desc": c.get("desc", ""),
                         "price": str(c.get("price", 100)),
                         "elem": c.get("elem", "金")}
                        for c in pet.catalog
                    ])
                    logger.info(f"[apix-meme] 已写入 {len(pet.catalog)} 种默认宠物图鉴")
                if not self.data_store.get("pet_work_events") and pet.work_events:
                    self._save_collection("pet_work_events", [
                        {"event": e.get("event", ""),
                         "exp_min": str(e.get("exp_min", 5)),
                         "exp_max": str(e.get("exp_max", 30)),
                         "score_min": str(e.get("score_min", 1)),
                         "score_max": str(e.get("score_max", 5))}
                        for e in pet.work_events
                    ])
                    logger.info(f"[apix-meme] 已写入 {len(pet.work_events)} 条默认上班事件")
                if (not self.data_store.get("pet_adventure_events")
                        and getattr(pet, "adventure_events", None)):
                    self._save_collection("pet_adventure_events", [
                        {"event": e.get("event", ""),
                         "exp_min": str(e.get("exp_min", 5)),
                         "exp_max": str(e.get("exp_max", 40)),
                         "score_min": str(e.get("score_min", 1)),
                         "score_max": str(e.get("score_max", 6)),
                         "hp_cost": str(e.get("hp_cost", 0))}
                        for e in pet.adventure_events
                    ])
                    logger.info(
                        f"[apix-meme] 已写入 {len(pet.adventure_events)} 条默认冒险事件"
                    )
                if not self.data_store.get("pet_item_list") and pet.items:
                    self._save_collection("pet_item_list", [
                        {"name": i.get("name", ""),
                         "price": str(i.get("price", 50)),
                         "kind": i.get("kind", "hp"),
                         "value": str(i.get("value", 50)),
                         "desc": i.get("desc", "")}
                        for i in pet.items
                    ])
                    logger.info(f"[apix-meme] 已写入 {len(pet.items)} 个默认物品")
                self._upgrade_pet_work_events()
            gm = getattr(self, "greeting_module", None)
            if gm is not None:
                self._init_menu_texts()

                if not self.data_store.get("greeting_morning_messages"):
                    rows = [
                        {"start_hour": str(m.get("start", 0)),
                         "end_hour": str(m.get("end", 24)),
                         "message": str(m.get("message", ""))}
                        for m in (getattr(gm, "morning_messages", None) or [])
                        if str(m.get("message", "")).strip()
                    ]
                    if rows:
                        self._save_collection("greeting_morning_messages", rows)
                        logger.info(f"[apix-meme] 已写入 {len(rows)} 条默认早安文案")
                if not self.data_store.get("greeting_night_messages"):
                    rows = [
                        {"start_hour": str(m.get("start", 0)),
                         "end_hour": str(m.get("end", 24)),
                         "message": str(m.get("message", ""))}
                        for m in (getattr(gm, "night_messages", None) or [])
                        if str(m.get("message", "")).strip()
                    ]
                    if rows:
                        self._save_collection("greeting_night_messages", rows)
                        logger.info(f"[apix-meme] 已写入 {len(rows)} 条默认晚安文案")
            if not self.data_store.get("api_list") and getattr(self, "rules", None):
                self._save_collection("api_list", [
                    {"keywords": ",".join(r.get("keywords") or []),
                     "url_template": r.get("url_template", ""),
                     "param_count": str(r.get("param_count", "1")),
                     "media_type": r.get("media_type", "image") or "image",
                     "json_field": r.get("json_field", "") or ""}
                    for r in self.rules
                ])
                logger.info(f"[apix-meme] 已写入 {len(self.rules)} 条默认接口规则")
        except Exception:
            logger.exception("[apix-meme] 写入默认数据失败")

    def _mount_img_dir(self, cfg=None) -> str:
        """
        坐骑素材目录（1.7.6）。

        取配置里的 `mount_img_dir`；没有就用签到模块上的值；
        再没有用内置的默认目录。
        """
        try:
            _cfg = cfg if isinstance(cfg, dict) else (self._read_disk_config() or {})
        except Exception:
            _cfg = {}
        d = to_str(_cfg.get("mount_img_dir", "")).strip()
        if not d:
            try:
                d = str(getattr(self.checkin_module, "mount_img_dir", "")
                        or "").strip()
            except Exception:
                d = ""
        return d

    def _sync_summaries_to_config(self):
        """
        把数据文件的当前内容以「只读摘要」写进后台配置的对应字段，
        这样在 AstrBot 后台也能一眼看到有哪些数据（改还是去独立页面改）。
        """
        if self.data_store is None:
            return
        try:
            existing = self._read_disk_config() or {}
            if not isinstance(existing, dict):
                return
            changed = False
            for key in DATA_COLLECTIONS:
                text = self.data_store.summary(key, limit=5)
                _skey = f"data_summary_{key}"
                _cur = existing.get(key)
                if _cur is not None and not isinstance(_cur, list):
                    existing.pop(key, None)
                    changed = True
                    logger.warning(
                        f"[apix-meme] 已清除脏配置 {key}（类型 "
                        f"{type(_cur).__name__}，应为数组）")
                if str(existing.get(_skey, "") or "") != text:
                    existing[_skey] = text
                    changed = True
            if changed:
                self._write_disk_config(existing)
                logger.info("[apix-meme] 已同步数据摘要到后台配置（只读展示）")
        except Exception:
            logger.exception("[apix-meme] 同步数据摘要失败")

    def _save_collection(self, key: str, rows: list) -> bool:
        """
        把一组数据写入独立数据文件（1.7.2 起数据不再存后台配置）。
        数据文件不可用时回退到写后台配置，保证不丢数据。
        """
        rows = list(rows or [])
        if self.data_store is not None:
            try:
                ok = self.data_store.set(key, rows)
                if ok:
                    try:
                        if isinstance(self.config, dict):
                            self.config[key] = rows
                    except Exception:
                        pass
                    return True
            except Exception:
                logger.exception(f"[apix-meme] 写入数据文件失败：{key}")
        try:
            existing = self._read_disk_config() or {}
            if not isinstance(existing, dict):
                existing = {}
            existing[key] = rows
            self._write_disk_config(existing)
            return True
        except Exception:
            logger.exception(f"[apix-meme] 回退写入后台配置也失败：{key}")
            return False

    def _write_pet_catalog(self, catalog: list) -> bool:
        """把宠物图鉴写入独立数据文件"""
        return self._save_collection("pet_list", [
            {
                "name": str(c.get("name", "")),
                "desc": str(c.get("desc", "")),
                "price": str(c.get("price", 100)),
                "elem": str(c.get("elem", "金")),
            }
            for c in (catalog or [])
        ])

    async def _group_member_ids(self, event, group_id: str):
        """
        取本群**当前真实成员**的 QQ 集合（用于排行榜剔除退群者）。

        ★ 为什么需要：AstrBot 这一版**没有「成员减少」事件**
          （群里搜不到任何 group_decrease 处理），无法在退群那一刻清数据；
          所以改成**查询时过滤** —— 拉一次群名单，不在名单里的不上榜。

        ★ 三层兜底拿 bot 对象（和 core/group_admin.get_group_members 同口径）
        ★ 5 分钟缓存：排行榜点几次不会反复请求群名单
        ★ **任何失败都返回 None** —— 调用方据此不过滤，
          绝不能让「名单拉不到」把整个排行榜变成空的。
        """
        try:
            gid = str(group_id or "").strip()
            if not gid or not gid.isdigit():
                return None
            import time as _t
            _now = _t.monotonic()
            _cache = getattr(self, "_gmember_cache", None)
            if _cache is None:
                _cache = {}
                self._gmember_cache = _cache
            _hit = _cache.get(gid)
            if _hit and _now - _hit[0] < 300:
                return _hit[1]
            bot = getattr(event, "bot", None)
            if bot is None:
                for _a in ("api", "client", "onebot"):
                    _alt = getattr(event, _a, None)
                    if _alt is not None:
                        bot = _alt
                        break
            if bot is None:
                return None
            _members = None
            if hasattr(bot, "get_group_member_list"):
                try:
                    _members = await bot.get_group_member_list(
                        group_id=int(gid))
                except Exception:
                    _members = None
            if not _members and hasattr(bot, "call_action"):
                try:
                    _members = await bot.call_action(
                        "get_group_member_list", group_id=int(gid))
                except Exception:
                    _members = None
            if not _members:
                return None
            _ids = set()
            for _m in _members:
                try:
                    if isinstance(_m, dict):
                        _u = (_m.get("user_id") or _m.get("uin")
                              or _m.get("qq") or "")
                    else:
                        _u = (getattr(_m, "user_id", "")
                              or getattr(_m, "uin", ""))
                    _u = str(_u).strip()
                    if _u:
                        _ids.add(_u)
                except Exception:
                    continue
            if not _ids:
                return None
            _cache[gid] = (_now, _ids)
            logger.info(f"[apix-meme] 群 {gid} 成员名单 {len(_ids)} 人（已缓存）")
            try:
                if self.checkin_module is not None:
                    self.checkin_module.purge_departed(gid, _ids)
            except Exception:
                logger.debug("[apix-meme] 清理退群数据失败", exc_info=True)
            return _ids
        except Exception:
            logger.debug("[apix-meme] 取群成员名单失败", exc_info=True)
            return None

    def _refresh_cfg_if_changed(self) -> bool:
        """
        配置文件变了就自动重载配置。返回**是否发生了重载**。

        ★ 用 `config_path()` 找配置文件（和写盘同一口径）。
        ★ 1 秒节流：避免同一批输出里反复 stat 文件。
        ★ 重载异常只记日志，绝不影响本次输出。
        """
        try:
            import time as _t
            now = _t.monotonic()
            _last = getattr(self, "_cfg_refresh_ts", None)
            if _last is not None and now - float(_last) < 1.0:
                return False
            self._cfg_refresh_ts = now
            try:
                from .core.path_utils import config_path as _cp
            except Exception:
                try:
                    from core.path_utils import config_path as _cp
                except Exception:
                    return False
            path = _cp()
            if not path or not os.path.isfile(path):
                return False
            mt = os.path.getmtime(path)
            _seen = getattr(self, "_cfg_mtime_seen", None)
            if _seen is None:
                self._cfg_mtime_seen = mt
                return False
            if mt <= _seen:
                return False
            self._cfg_mtime_seen = mt
            logger.info("[apix-meme] 检测到配置变更，自动热刷新")
            self.reload_cfg()
            return True
        except Exception:
            logger.debug("[apix-meme] 配置热刷新失败", exc_info=True)
            return False

    def reload_cfg(self):
        pending_rules = list(self.rules) if self._dirty else []

        disk_cfg = self._read_disk_config()

        merged = {}
        if isinstance(self.config, dict):
            merged.update(self.config)
        if isinstance(disk_cfg, dict):
            merged.update(disk_cfg)
            _missing = [k for k in self.config if k not in disk_cfg] \
                if isinstance(self.config, dict) else []
            if _missing:
                logger.info(
                    f"[apix-meme] 面板有 {len(_missing)} 个插件配置里没有的键，"
                    f"沿用面板值（如 {_missing[:3]}）")

        if self.data_store is not None:
            try:
                for _k in DATA_COLLECTIONS:
                    _rows = self.data_store.get(_k)
                    if _rows:
                        merged[_k] = _rows
            except Exception:
                logger.exception("[apix-meme] 注入数据文件内容失败")

        self.must_at_bot = bool(merged.get("must_at_bot", False))

        # ★★ 1.5.0：**指令名统一迁移**。

        #   这一版改了几个默认名，但**老配置里存的是旧名**（新默认值不会覆盖

        #   已保存的值）—— 这里按「旧名 -> 新名」一次性纠正并落盘。

        #   ★ 只改「第一个名字恰好等于旧默认名」的项：用户自己改过的名字**不动**。

        try:

            _RENAME_150 = {

                "cmd_mc_menu":         ("MC系统",   "世界系统"),

                "cmd_entertain_menu":  ("娱乐功能", "娱乐系统"),

                "cmd_daily_menu":      ("每日系列", "每日系统"),

                "cmd_weather_menu":    ("天气查询", "天气功能"),

                "cmd_oil_menu":        ("油价查询", "油价功能"),

                "cmd_bili_menu":       ("B站功能",  "哔哩功能"),

                "cmd_mute":            ("禁言群员", "禁言"),

                "cmd_kick":            ("踢出群员", "踢出"),

                "cmd_blacklist":       ("拉黑成员", "拉黑"),

                "menu_auth_names":    ("授权功能", "授权系统"),

                "cmd_enable":          (("娱乐系统开", "开启自定义娱乐"), "自定义娱乐 开启"),
                "cmd_disable":         (("娱乐系统关", "关闭自定义娱乐"), "自定义娱乐 关闭"),
            }

            _renamed = []

            for _k, (_old, _new) in _RENAME_150.items():

                _v = merged.get(_k, None)

                if _v is None:

                    continue

                _parts = [x.strip() for x in str(_v).split(",")]

                _olds = _old if isinstance(_old, (tuple, list)) else (_old,)

                if _parts and _parts[0] in _olds:
                    _parts[0] = _new

                    merged[_k] = ",".join([x for x in _parts if x])

                    _renamed.append(f"{_old}->{_new}")

            if _renamed:

                logger.info("[apix-meme] 1.5.0 指令名迁移：" + "、".join(_renamed))

                try:

                    _disk = self._read_disk_config() or {}

                    if isinstance(_disk, dict):

                        for _k in _RENAME_150:

                            if _k in merged:

                                _disk[_k] = merged[_k]

                        self._write_disk_config(_disk)

                except Exception:

                    logger.debug("[apix-meme] 迁移结果落盘失败", exc_info=True)

        except Exception:

            logger.exception("[apix-meme] 1.5.0 指令名迁移失败")


        self.cmd_main_name = to_str(merged.get("cmd_main", "自定义娱乐")).strip() or "自定义娱乐"
        self.cmd_admin_name = to_str(merged.get("cmd_admin", "管理员指令")).strip() or "管理员指令"
        self.cmd_list_name = to_str(merged.get("cmd_list", "表情包列表")).strip() or "表情包列表"
        self.cmd_greeting_morning_raw = to_str(merged.get("cmd_greeting_morning", "早安,早上好,起床")).strip() or "早安,早上好,起床"
        self.cmd_greeting_night_raw = to_str(merged.get("cmd_greeting_night", "晚安,睡觉,睡了")).strip() or "晚安,睡觉,睡了"
        self.cmd_minecraft_name = to_str(merged.get("cmd_minecraft", "MC版本")).strip() or "MC版本"
        self.cmd_mc_auto_name = to_str(merged.get("cmd_mc_auto", "MC自动检测")).strip() or "MC自动检测"
        self.cmd_mc_test_name = to_str(merged.get("cmd_mc_test", "MC测试推送")).strip() or "MC测试推送"
        self.cmd_add_name = to_str(merged.get("cmd_add", "添加表情")).strip() or "添加表情"
        self.cmd_del_name = to_str(merged.get("cmd_del", "删除规则")).strip() or "删除规则"
        self.cmd_save_name = to_str(merged.get("cmd_save", "保存规则")).strip() or "保存规则"
        self.cmd_word_add_name = to_str(merged.get("cmd_word_add", "加词")).strip() or "加词"
        self.cmd_word_del_name = to_str(merged.get("cmd_word_del", "删词")).strip() or "删词"
        self.cmd_word_list_name = to_str(merged.get("cmd_word_list", "词库列表")).strip() or "词库列表"
        self.cmd_enable_name = to_str(merged.get("cmd_enable", "自定义娱乐 开启")).strip() or "自定义娱乐 开启"
        self.cmd_disable_name = to_str(merged.get("cmd_disable", "自定义娱乐 关闭")).strip() or "自定义娱乐 关闭"
        self.cmd_view_admin_name = to_str(merged.get("cmd_view_admin", "查看管理员")).strip() or "查看管理员"
        self.cmd_refresh_admin_name = to_str(merged.get("cmd_refresh_admin", "刷新群管")).strip() or "刷新群管"
        self.cmd_add_admin_name = to_str(merged.get("cmd_add_admin", "加管理员")).strip() or "加管理员"
        self.cmd_del_admin_name = to_str(merged.get("cmd_del_admin", "删管理员")).strip() or "删管理员"
        self.cmd_admin_list_name = to_str(merged.get("cmd_admin_list", "管理员列表")).strip() or "管理员列表"
        self.cmd_enable_feature_name = to_str(merged.get("cmd_enable_feature", "开启功能")).strip() or "开启功能"
        self.cmd_disable_feature_name = to_str(merged.get("cmd_disable_feature", "关闭功能")).strip() or "关闭功能"
        self.cmd_feature_status_name = to_str(merged.get("cmd_feature_status", "功能状态")).strip() or "功能状态"
        self.cmd_greeting_rank_name = to_str(merged.get("cmd_greeting_rank", "打卡排行")).strip() or "打卡排行"
        self.cmd_greeting_stats_name = to_str(merged.get("cmd_greeting_stats", "打卡统计")).strip() or "打卡统计"
        self.cmd_job_add_name = to_str(merged.get("cmd_job_add_names", "添加工种")).strip() or "添加工种"
        self.cmd_job_del_name = to_str(merged.get("cmd_job_del_names", "删除工种")).strip() or "删除工种"
        self.cmd_mount_add_name = to_str(merged.get("cmd_mount_add_names", "添加坐骑")).strip() or "添加坐骑"
        self.cmd_mount_del_name = to_str(merged.get("cmd_mount_del_names", "删除坐骑,删坐骑")).strip() or "删除坐骑"

        self.cmd_must_at_bot_on_name = to_str(merged.get("cmd_must_at_bot_on", "必须艾特开")).strip() or "必须艾特开"
        self.cmd_must_at_bot_off_name = to_str(merged.get("cmd_must_at_bot_off", "必须艾特关")).strip() or "必须艾特关"
        self.cmd_must_at_bot_status_name = to_str(merged.get("cmd_must_at_bot_status", "必须艾特状态")).strip() or "必须艾特状态"

        gmb_raw = merged.get("group_must_at_bot", {})
        if isinstance(gmb_raw, dict):
            self.group_must_at_bot = {str(k): bool(v) for k, v in gmb_raw.items()}
        else:
            self.group_must_at_bot = {}

        self.cmd_meme_menu_name = to_str(merged.get("cmd_meme_menu", "接口系统")).strip() or "接口系统"
        self.cmd_meme_image_name = to_str(merged.get("cmd_meme_image", "图片接口")).strip() or "图片接口"
        self.cmd_meme_video_name = to_str(merged.get("cmd_meme_video", "视频接口")).strip() or "视频接口"
        self.cmd_meme_text_name = to_str(merged.get("cmd_meme_text", "文本接口")).strip() or "文本接口"
        self.cmd_mc_menu_name = to_str(merged.get("cmd_mc_menu", "世界系统")).strip() or "世界系统"
        self.cmd_mc_version_menu_name = to_str(merged.get(
            "menu_mc_version_names", "版本查询")).strip() or "版本查询"
        self.cmd_mc_server_menu_name = to_str(merged.get(
            "menu_mc_server_names", "服务查询")).strip() or "服务查询"
        self.cmd_mc_player_menu_name = to_str(merged.get(
            "menu_mc_player_names", "玩家信息")).strip() or "玩家信息"
        self.cmd_mc_push_menu_name = to_str(merged.get(
            "menu_mc_push_names", "版本推送")).strip() or "版本推送"
        self.cmd_mute_list_name = to_str(merged.get(
            "cmd_mute_list", "禁言列表")).strip() or "禁言列表"
        _mu = to_str(merged.get("mute_unit", "秒")).strip()
        self.mute_unit_name = _mu if _mu in ("秒", "分", "时", "天") else "秒"
        self.cmd_greeting_menu_name = to_str(merged.get("cmd_greeting_menu", "打卡系统")).strip() or "打卡系统"
        self.cmd_checkin_menu_name = to_str(merged.get("cmd_checkin_menu", "签到系统")).strip() or "签到系统"
        self.cmd_daily_menu_name = to_str(merged.get("cmd_daily_menu", "每日系统")).strip() or "每日系列"
        self.cmd_entertain_menu_name = to_str(
            merged.get("cmd_entertain_menu", "娱乐系统")).strip() or "娱乐功能"
        self.qqinfo_enable = bool(merged.get("qqinfo_enable", True))
        self.cmd_qqinfo = to_str(
            merged.get("cmd_qqinfo", "查询,查QQ,查qq,查用户")).strip() or "查询"
        self.cmd_qqinfo_menu_name = to_str(
            merged.get("cmd_qqinfo_menu", "查询功能")).strip() or "查询功能"
        self.cmd_weather_menu_name = to_str(
            merged.get("cmd_weather_menu", "天气查询")).strip() or "天气查询"

        self.cmd_chime_on_name = to_str(merged.get("cmd_chime_on", "整点报时开")).strip() or "整点报时开"
        self.cmd_chime_off_name = to_str(merged.get("cmd_chime_off", "整点报时关")).strip() or "整点报时关"
        self.cmd_chime_status_name = to_str(merged.get("cmd_chime_status", "整点报时状态")).strip() or "整点报时状态"
        self.cmd_chime_hours_name = to_str(merged.get("cmd_chime_hours", "整点报时时段")).strip() or "整点报时时段"
        self.cmd_chime_text_name = to_str(merged.get("cmd_chime_text", "整点报时文案")).strip() or "整点报时文案"
        self.cmd_chime_list_name = to_str(merged.get("cmd_chime_list", "整点报时列表")).strip() or "整点报时列表"
        self.cmd_chime_test_name = to_str(merged.get("cmd_chime_test", "整点报时测试")).strip() or "整点报时测试"

        self.cmd_daily_push_on_name = to_str(merged.get("cmd_daily_push_on", "状态推送开")).strip() or "状态推送开"
        self.cmd_daily_push_off_name = to_str(merged.get("cmd_daily_push_off", "状态推送关")).strip() or "状态推送关"
        self.cmd_daily_push_time_name = to_str(merged.get("cmd_daily_push_time", "状态推送间隔")).strip() or "状态推送间隔"
        self.cmd_daily_push_list_name = to_str(merged.get("cmd_daily_push_list", "状态推送列表")).strip() or "状态推送列表"

        self.cmd_blacklist_list_name = to_str(merged.get("cmd_blacklist_list", "黑名单列表")).strip() or "黑名单列表"

        self.moderation_enable = bool(merged.get("moderation_enable", True))
        self._blacklist_raw = to_str(merged.get("member_blacklist", ""))
        self.member_blacklist = parse_blacklist(self._blacklist_raw)
        self.blacklist_default_action = (
            to_str(merged.get("blacklist_default_action", "ignore")).strip().lower()
            or "ignore"
        )
        if self.blacklist_default_action not in (ACTION_KICK, ACTION_MUTE, ACTION_IGNORE):
            self.blacklist_default_action = ACTION_IGNORE
        try:
            self.mute_duration = int(to_str(merged.get("mute_duration", "600")).strip() or "600")
        except Exception:
            self.mute_duration = 600
        if self.mute_duration < 60:
            self.mute_duration = 60

        try:
            _mig = {
                "cmd_daily_news": ("每日读报", "今日读报,每日读报"),
                "cmd_daily_news": ("读报功能,每日读报", "今日读报,每日读报"),
                "cmd_daily_news_on": ("读报推送开",
                                      "开启功能 读报推送,开启读报推送"),
                "cmd_daily_news_off": ("读报推送关",
                                       "关闭功能 读报推送,关闭读报推送"),
            }
            _cur = {}
            for _k, (_old, _new) in _mig.items():
                _v = to_str(merged.get(_k, "")).strip()
                if _v == _old:
                    merged[_k] = _new
                    _cur[_k] = _new
            if _cur:
                try:
                    _disk = self._read_disk_config() or {}
                    if isinstance(_disk, dict):
                        _disk.update(_cur)
                        self._write_disk_config(_disk)
                except Exception:
                    logger.debug("[apix-meme] 读报指令名迁移写盘失败",
                                 exc_info=True)
                logger.info(f"[apix-meme] 读报指令名已迁移 {len(_cur)} 项："
                            f"{list(_cur)}")
        except Exception:
            logger.debug("[apix-meme] 读报指令名迁移失败", exc_info=True)

        self.cmd_daily_news_manual_on_name = to_str(merged.get(
            "cmd_daily_news_manual_on",
            "开启功能 读报功能,开启读报功能"
        )).strip() or "开启功能 读报功能,开启读报功能"
        self.cmd_daily_news_manual_off_name = to_str(merged.get(
            "cmd_daily_news_manual_off",
            "关闭功能 读报功能,关闭读报功能"
        )).strip() or "关闭功能 读报功能,关闭读报功能"
        self.cmd_daily_news_name = to_str(merged.get("cmd_daily_news", "今日读报,每日读报")).strip() or "每日读报"
        self.cmd_daily_news_on_name = to_str(merged.get("cmd_daily_news_on", "开启功能 读报推送,开启读报推送")).strip() or "读报推送开"
        self.cmd_daily_news_off_name = to_str(merged.get("cmd_daily_news_off", "关闭功能 读报推送,关闭读报推送")).strip() or "读报推送关"
        self.cmd_daily_news_time_name = to_str(merged.get("cmd_daily_news_time", "读报推送时间")).strip() or "读报推送时间"
        self.cmd_daily_news_list_name = to_str(merged.get("cmd_daily_news_list", "读报推送列表")).strip() or "读报推送列表"

        if self.daily_news_module is not None:
            try:
                self.daily_news_module.reload(merged)
            except Exception:
                logger.exception("[apix-meme] 刷新每日读报模块失败")

        if self.speak_stats_module is not None:
            try:
                self.speak_stats_module.reload(merged)
            except Exception:
                logger.exception("[apix-meme] 刷新发言统计模块失败")

        try:
            def _bn(v, dflt):
                if isinstance(v, (list, tuple)):
                    out = [str(x).strip() for x in v if str(x).strip()]
                else:
                    out = [x.strip() for x in
                           str(v or "").replace("，", ",").split(",")
                           if x.strip()]
                return out or list(dflt)
            self.bird_enable = bool(merged.get("bird_enable", True))
            self.cmd_bird = _bn(merged.get("cmd_bird"), ["今日小鸟"])
            self.cmd_gallery = _bn(merged.get("cmd_gallery"),
                                   ["我的图鉴", "小鸟图鉴"])
            self.cmd_bird_menu = _bn(merged.get("cmd_bird_menu"),
                                     ["小鸟图鉴"])
            self.cmd_bird_list = _bn(merged.get("cmd_bird_list"),
                                     ["图鉴列表"])
            self.cmd_bird_view = _bn(merged.get("cmd_bird_view"),
                                     ["图鉴查看"])
            try:
                self.bird_list_per_page = max(1, min(50, int(
                    merged.get("bird_list_per_page", 10) or 10)))
            except Exception:
                self.bird_list_per_page = 10
            try:
                self.bird_groups = parse_gid_list(
                    merged.get("bird_groups", ""))
            except Exception:
                self.bird_groups = []
            self.bird_image_dir = str(
                merged.get("bird_image_dir") or BIRD_DIR_DEFAULT).strip()
            self.bird_rank_enable = bool(
                merged.get("bird_rank_enable", True))
            _sn = str(merged.get("score_name", "") or "积分").strip() or "积分"
            self.score_name = _sn
            for _mod in ("checkin_module", "pet_module"):
                _m = getattr(self, _mod, None)
                if _m is not None:
                    try:
                        _m.score_name = _sn
                    except Exception:
                        pass

            try:
                from .core.path_utils import (
                    set_title_prefix as _set_pfx,
                    TITLE_PREFIX_DEFAULT as _PFX_DEF)
            except Exception:
                try:
                    from core.path_utils import (
                        set_title_prefix as _set_pfx,
                        TITLE_PREFIX_DEFAULT as _PFX_DEF)
                except Exception:
                    _set_pfx = None
                    _PFX_DEF = "铁剑娘系统："
            if _set_pfx is not None:
                if "title_prefix" in merged:
                    _pfx = merged.get("title_prefix")
                else:
                    _pfx = _PFX_DEF
                try:
                    self.title_prefix = _set_pfx(_pfx)
                except Exception:
                    logger.exception("[apix-meme] 设置标题前缀失败")
                    self.title_prefix = _PFX_DEF

            try:
                _mmn = merged.get("mount_menu_names")
                self.mount_menu_names_raw = (
                    "" if _mmn is None else str(_mmn).strip())
            except Exception:
                self.mount_menu_names_raw = ""

            try:
                _prev = dict(getattr(self, "_menu_entry_prev", {}) or {})
                _er = {}
                for _mid, (_k, _d) in self.MENU_ENTRY_DEFS.items():
                    _v = merged.get(_k)
                    _er[_k] = "" if _v is None else str(_v).strip()
                    _seen = list(_prev.get(_k) or [])
                    for _x in (_d or "").split(","):
                        _x = _x.strip()
                        if _x and _x not in _seen:
                            _seen.append(_x)
                    for _x in _er[_k].replace("，", ",").split(","):
                        _x = _x.strip()
                        if _x and _x not in _seen:
                            _seen.append(_x)
                    _prev[_k] = _seen
                self._menu_entry_raw = _er
                self._menu_entry_prev = _prev
            except Exception:
                self._menu_entry_raw = {}

            self.qqinfo_groups = parse_gid_whitelist(
                merged.get("qqinfo_groups", ""))

            self.guess_groups = parse_gid_whitelist(
                merged.get("guess_groups", ""))
            self.guess_enable = bool(merged.get("guess_enable", True))

            self.farm_enable = bool(merged.get("farm_enable", True))
            self.farm_groups = parse_gid_whitelist(merged.get("farm_groups", ""))
            self.cmd_farm_menu = to_str(
                merged.get("cmd_farm_menu", "农场功能")).strip() or "农场功能"
            self.cmd_farm_view = to_str(
                merged.get("cmd_farm_view", "我的农场,查看农场")).strip() \
                or "我的农场"
            self.cmd_farm_plant = to_str(
                merged.get("cmd_farm_plant", "种菜,种植")).strip() or "种菜"
            self.cmd_farm_harvest = to_str(
                merged.get("cmd_farm_harvest", "收获,收菜")).strip() or "收获"
            self.cmd_farm_harvest_all = to_str(
                merged.get("cmd_farm_harvest_all",
                           "一键收获,全部收获,一键收菜")).strip() or "一键收获"
            self.cmd_farm_shop = to_str(
                merged.get("cmd_farm_shop", "种子商店,买种子")).strip() \
                or "种子商店"
            self.cmd_farm_buy_seed = to_str(
                merged.get("cmd_farm_buy_seed", "购买种子")).strip() or "购买种子"
            self.cmd_farm_impl_shop = to_str(
                merged.get("cmd_farm_impl_shop", "农具商店")).strip() or "农具商店"
            self.cmd_farm_buy_item = to_str(
                merged.get("cmd_farm_buy_item", "购买肥料")).strip() or "购买肥料"
            self.cmd_farm_use_item = to_str(
                merged.get("cmd_farm_use_item", "施肥,使用肥料")).strip() or "施肥"
            self.cmd_farm_dog = to_str(
                merged.get("cmd_farm_dog",
                           "工具商店,家畜商店,买狗,狗商店")).strip() or "工具商店"
            self.cmd_farm_buy_dog = to_str(
                merged.get("cmd_farm_buy_dog", "购买家畜,购买狗")).strip() \
                or "购买家畜"
            self.cmd_farm_feed = to_str(
                merged.get("cmd_farm_feed", "喂食,喂养")).strip() or "喂食"
            self.cmd_farm_buy_feed = to_str(
                merged.get("cmd_farm_buy_feed", "购买饲料")).strip() or "购买饲料"
            self.cmd_farm_equip_dog = to_str(
                merged.get("cmd_farm_equip_dog", "装备家畜,换看门犬")).strip() \
                or "装备家畜"
            self.cmd_farm_my_animals = to_str(
                merged.get("cmd_farm_my_animals", "我的家畜,家畜列表")).strip() \
                or "我的家畜"
            self.cmd_farm_steal = to_str(
                merged.get("cmd_farm_steal", "偷菜,偷庄稼")).strip() or "偷菜"
            self.cmd_farm_steal_log = to_str(
                merged.get("cmd_farm_steal_log", "被偷记录,偷菜记录")).strip() \
                or "被偷记录"
            self.cmd_farm_bag = to_str(
                merged.get("cmd_farm_bag", "我的背包,背包")).strip() or "我的背包"
            self.cmd_farm_status = to_str(
                merged.get("cmd_farm_status", "农场状态,今日农场")).strip() \
                or "农场状态"
            # ★★ 2.1.x：**土地状态** —— 从「我的农场」拆出来的逐块明细
            #   （有没有种 / 种的是什么 / 熟了没）。多个名用英文逗号分隔。
            self.cmd_farm_land = to_str(
                merged.get("cmd_farm_land", "土地状态,地块状态")).strip() \
                or "土地状态"
            self.cmd_farm_buy_tool = to_str(
                merged.get("cmd_farm_buy_tool", "购买工具,购买农机")).strip() \
                or "购买工具"
            self.cmd_farm_equip_tool = to_str(
                merged.get("cmd_farm_equip_tool", "装备工具,装备农机")).strip() \
                or "装备工具"

            def _farm_int(_k, _d):
                """后台填的是字符串，安全转 int（坏值用默认）"""
                try:
                    _s = str(merged.get(_k, "") or "").strip()
                    return int(float(_s)) if _s else _d
                except Exception:
                    return _d

            def _farm_bool(_k, _d):
                _v = merged.get(_k)
                if _v is None:
                    return _d
                if isinstance(_v, bool):
                    return _v
                return str(_v).strip().lower() in ("1", "true", "yes", "on", "开")

            self.farm_init_lands = _farm_int("farm_init_lands", 3)
            self.farm_max_lands = _farm_int("farm_max_lands", 30)
            self.farm_max_level = _farm_int("farm_max_level", 30)
            self.farm_exp_base = _farm_int("farm_exp_base", 100)
            self.farm_exp_step = _farm_int("farm_exp_step", 60)
            self.farm_max_animals = _farm_int("farm_max_animals", 5)
            self.farm_animal_energy_max = _farm_int("farm_animal_energy_max", 100)
            self.farm_animal_energy_per_hour = _farm_int(
                "farm_animal_energy_per_hour", 10)
            self.farm_shop_per_page = _farm_int("farm_shop_per_page", 8)
            self.farm_land_per_page = _farm_int("farm_land_per_page", 12)
            self.farm_steal_enable = _farm_bool("farm_steal_enable", True)
            self.farm_steal_base_rate = _farm_int("farm_steal_base_rate", 3)
            self.farm_steal_ratio = _farm_int("farm_steal_ratio", 30)
            self.farm_steal_daily_limit = _farm_int("farm_steal_daily_limit", 3)
            self.farm_steal_min_amount = _farm_int("farm_steal_min_amount", 10)
            self.farm_steal_fail_comp_min = _farm_int(
                "farm_steal_fail_comp_min", 1)
            self.farm_steal_fail_comp_max = _farm_int(
                "farm_steal_fail_comp_max", 500)
            self.farm_harvest_upgrade_chance = _farm_int(
                "farm_harvest_upgrade_chance", 5)
            self.farm_quality_bonus_red = _farm_int("farm_quality_bonus_red", 10)
            self.farm_quality_bonus_black = _farm_int(
                "farm_quality_bonus_black", 20)
            self.farm_auto_enable = _farm_bool("farm_auto_enable", True)
            self.farm_auto_interval_min = max(
                1, _farm_int("farm_auto_interval_min", 10))
            if self.farm_module is not None:
                try:
                    self.farm_module.reload({
                        "farm_enable": self.farm_enable,
                        "farm_init_lands": self.farm_init_lands,
                        "farm_max_lands": self.farm_max_lands,
                        "farm_max_level": self.farm_max_level,
                        "farm_exp_base": self.farm_exp_base,
                        "farm_exp_step": self.farm_exp_step,
                        "farm_max_animals": self.farm_max_animals,
                        "farm_animal_energy_max": self.farm_animal_energy_max,
                        "farm_animal_energy_per_hour":
                            self.farm_animal_energy_per_hour,
                        "farm_steal_enable": self.farm_steal_enable,
                        "farm_steal_base_rate": self.farm_steal_base_rate,
                        "farm_steal_ratio": self.farm_steal_ratio,
                        "farm_steal_daily_limit": self.farm_steal_daily_limit,
                        "farm_steal_min_amount": self.farm_steal_min_amount,
                        "farm_steal_fail_comp_min":
                            self.farm_steal_fail_comp_min,
                        "farm_steal_fail_comp_max":
                            self.farm_steal_fail_comp_max,
                        "farm_harvest_upgrade_chance":
                            self.farm_harvest_upgrade_chance,
                        "farm_quality_bonus_red": self.farm_quality_bonus_red,
                        "farm_quality_bonus_black": self.farm_quality_bonus_black,
                    })
                except Exception:
                    logger.exception("[apix-meme] 农场配置加载失败")



            self.racing_enable = bool(merged.get("racing_enable", True))

            def _ri(key, dflt, lo=None, hi=None):
                try:
                    v = int(str(merged.get(key, dflt) or dflt).strip())
                except Exception:
                    v = int(dflt)
                if lo is not None:
                    v = max(lo, v)
                if hi is not None:
                    v = min(hi, v)
                return v

            self.racing_groups = parse_gid_whitelist(
                merged.get("racing_groups", ""))
            self.cmd_race_join = to_str(merged.get(
                "cmd_race_join", "参与比赛")).strip() or "参与比赛"
            self.cmd_race_fast = to_str(merged.get(
                "cmd_race_fast", "快速比赛")).strip() or "快速比赛"
            self.cmd_race_my = to_str(merged.get(
                "cmd_race_my", "我的赛车")).strip() or "我的赛车"
            self.cmd_race_equip = to_str(merged.get(
                "cmd_race_equip", "装备赛车")).strip() or "装备赛车"
            self.cmd_race_view = to_str(merged.get(
                "cmd_race_view", "赛车查看")).strip() or "赛车查看"
            self.cmd_race_shop = to_str(merged.get(
                "cmd_race_shop", "赛车商店")).strip() or "赛车商店"
            self.cmd_race_buy = to_str(merged.get(
                "cmd_race_buy", "购买赛车")).strip() or "购买赛车"
            self.cmd_race_lucky = to_str(merged.get(
                "cmd_race_lucky", "赛车抽奖")).strip() or "赛车抽奖"
            self.cmd_race_lucky_all = to_str(merged.get(
                "cmd_race_lucky_all", "赛车一键抽奖,赛车全部抽奖")
            ).strip() or "赛车一键抽奖,赛车全部抽奖"
            self.cmd_race_menu_name = to_str(merged.get(
                "cmd_race_menu", "赛车功能")).strip() or "赛车功能"
            self.race_players = _ri("race_players", 5, lo=2, hi=20)
            self.race_wait_sec = _ri("race_wait_sec", 3600, lo=0)
            self.race_daily_limit = _ri("race_daily_limit", 10, lo=0)
            self.race_entry_fee = _ri("race_entry_fee", 500, lo=0)
            self.race_pool_bonus_min = _ri("race_pool_bonus_min", 1000, lo=0)
            self.race_pool_bonus_max = _ri("race_pool_bonus_max", 5000, lo=0)
            if self.race_pool_bonus_max < self.race_pool_bonus_min:
                self.race_pool_bonus_min, self.race_pool_bonus_max = (
                    self.race_pool_bonus_max, self.race_pool_bonus_min)
            self.race_fast_npc = _ri("race_fast_npc", 5, lo=1, hi=20)
            self.race_npc_fill = bool(merged.get("race_npc_fill", True))
            self.race_npc_power = _ri("race_npc_power", 100, lo=10, hi=300)
            self.race_show_time = bool(merged.get("race_show_time", True))
            self.race_lucky_fee = _ri("race_lucky_fee", 2000, lo=0)
            self.race_lucky_daily_limit = _ri(
                "race_lucky_daily_limit", 1, lo=0)
            self.race_shop_list = str(merged.get("race_shop_list", "") or "")
            self.race_lottery_cars = str(
                merged.get("race_lottery_cars", "") or "")

            if self.racing_module is not None:
                try:
                    rm = self.racing_module
                    rm.enable = self.racing_enable
                    rm.score_name = self.score_name
                    rm.trigger_names = [self.cmd_race_join]
                    rm.fast_names = [self.cmd_race_fast]
                    rm.my_names = [self.cmd_race_my]
                    rm.equip_names = [self.cmd_race_equip]
                    rm.view_names = [self.cmd_race_view]
                    rm.shop_names = [self.cmd_race_shop]
                    rm.buy_names = [self.cmd_race_buy]
                    rm.lucky_names = [self.cmd_race_lucky]
                    rm.lucky_all_names = self._split_names(
                        self.cmd_race_lucky_all)
                    rm.lucky_all_raw = self.cmd_race_lucky_all
                    rm.players = self.race_players
                    rm.wait_sec = self.race_wait_sec
                    rm.daily_limit = self.race_daily_limit
                    rm.entry_fee = self.race_entry_fee
                    rm.pool_bonus_min = self.race_pool_bonus_min
                    rm.pool_bonus_max = self.race_pool_bonus_max
                    rm.npc_fill = self.race_npc_fill
                    rm.fast_npc = self.race_fast_npc
                    rm.npc_power = self.race_npc_power
                    rm.show_time = self.race_show_time
                    rm.lucky_fee = self.race_lucky_fee
                    rm.lucky_daily_limit = self.race_lucky_daily_limit
                    _from_store = False
                    try:
                        _from_store = bool(rm.load_cars_from_store())
                    except Exception:
                        logger.exception("[apix-meme] 从数据文件读赛车失败")
                    if not _from_store:
                        _shop = _parse_car_list(self.race_shop_list)
                        if _shop:
                            for _c in _shop:
                                _c["kind"] = "shop"
                            rm.shop = _shop
                        _lot = _parse_car_list(self.race_lottery_cars)
                        if _lot:
                            for _c in _lot:
                                _c["kind"] = "lucky"
                            rm.lottery_cars = _lot
                    try:
                        rm.load_npcs_from_store()
                    except Exception:
                        logger.debug("[apix-meme] 读 NPC 用车失败",
                                     exc_info=True)
                    try:
                        _lp = (self.data_store.get("race_lottery_pool")
                               if getattr(self, "data_store", None)
                               else None)
                        if isinstance(_lp, list) and _lp:
                            rm.lottery_pool = [dict(x) for x in _lp
                                               if isinstance(x, dict)]
                    except Exception:
                        logger.debug("[apix-meme] 读抽奖奖池失败",
                                     exc_info=True)
                except Exception:
                    logger.exception("[apix-meme] 同步赛车配置失败")
            self.cmd_guess_start = _bn(merged.get("cmd_guess_start"),
                                       ["开始猜数"])
            self.cmd_guess = _bn(merged.get("cmd_guess"), ["猜数"])

            def _gi(key, dflt):
                try:
                    return int(str(merged.get(key, dflt)).strip() or dflt)
                except Exception:
                    return dflt

            self.guess_min = _gi("guess_min", 1)
            self.guess_max = _gi("guess_max", 100)
            if self.guess_max < self.guess_min:
                self.guess_min, self.guess_max = (self.guess_max,
                                                  self.guess_min)
            self.guess_max_tries = max(1, _gi("guess_max_tries", 3))
            self.guess_timeout_sec = max(0, _gi("guess_timeout_sec", 60))
            self.guess_reward_min = max(0, _gi("guess_reward_min", 100))
            self.guess_reward_max = max(0, _gi("guess_reward_max", 500))
            if self.guess_reward_max < self.guess_reward_min:
                self.guess_reward_min, self.guess_reward_max = (
                    self.guess_reward_max, self.guess_reward_min)
            self.guess_penalty = max(0, _gi("guess_penalty", 1000))
            self.guess_allow_negative = bool(
                merged.get("guess_allow_negative", True))
            if self.guess_module is not None:
                try:
                    self.guess_module.enable = self.guess_enable
                    self.guess_module.score_name = self.score_name
                    self.guess_module.trigger_names = list(self.cmd_guess)
                    self.guess_module.guess_min = self.guess_min
                    self.guess_module.guess_max = self.guess_max
                    self.guess_module.max_tries = self.guess_max_tries
                    self.guess_module.timeout_sec = self.guess_timeout_sec
                    self.guess_module.reward_min = self.guess_reward_min
                    self.guess_module.reward_max = self.guess_reward_max
                    self.guess_module.penalty = self.guess_penalty
                    self.guess_module.allow_negative = self.guess_allow_negative
                except Exception:
                    logger.exception("[apix-meme] 同步猜数配置失败")
            logger.info(
                "[apix-meme] 猜数：开关=%s 开局=%s 参与=%s 范围=%s~%s "
                "每人%s次 超时=%s秒 奖励=%s~%s 猜错扣=%s",
                self.guess_enable, self.cmd_guess_start, self.cmd_guess,
                self.guess_min, self.guess_max, self.guess_max_tries,
                self.guess_timeout_sec, self.guess_reward_min,
                self.guess_reward_max, self.guess_penalty)
            logger.info(
                f"[apix-meme] 小鸟图鉴：开关={self.bird_enable} "
                f"菜单={self.cmd_bird_menu} 抽鸟={self.cmd_bird} "
                f"图鉴={self.cmd_gallery} 黑名单={self.bird_groups} "
                f"素材目录={self.bird_image_dir!r}")
        except Exception:
            logger.exception("[apix-meme] 刷新小鸟图鉴配置失败")

        if self.pet_module is not None:
            try:
                self.pet_module.reload(merged)
                self.pet_module._cfg_catalog_write = self._write_pet_catalog
            except Exception:
                logger.exception("[apix-meme] 刷新宠物模块失败")
        if self.output_mode_module is not None:
            try:
                self.output_mode_module.reload(merged)
            except Exception:
                logger.exception("[apix-meme] 刷新输出模式模块失败")

        if self.status_push_module is not None:
            try:
                self.status_push_module.reload(merged)
            except Exception:
                logger.exception("[apix-meme] 刷新定时状态推送模块失败")

        status_raw = to_str(merged.get("cmd_status", "电脑状态")).strip() or "电脑状态"
        self.cmd_status_names = [s.strip() for s in status_raw.split(",") if s.strip()]
        if not self.cmd_status_names:
            self.cmd_status_names = ["电脑状态"]
        self.cmd_status_name = self.cmd_status_names[0]

        self.score_admin_whitelist = [
            x.strip() for x in to_str(
                merged.get("score_admin_whitelist", "")).splitlines()
            if x.strip()
        ]
        self.plugin_admins = [
            x.strip() for x in to_str(merged.get("plugin_admins", "")).splitlines()
            if x.strip()
        ]

        self.web_admin_token = to_str(merged.get("web_admin_token", "")).strip()

        self.minecraft_auto_groups = [
            x.strip() for x in to_str(merged.get("minecraft_auto_groups", "")).splitlines()
            if x.strip()
        ]
        self.minecraft_test_groups = [
            x.strip() for x in to_str(merged.get("minecraft_test_groups", "")).splitlines()
            if x.strip()
        ]
        self.minecraft_groups = parse_gid_whitelist(
            merged.get("minecraft_groups", ""))
        if self.minecraft_module is not None:
            merged_groups = list(dict.fromkeys(
                list(self.minecraft_module.auto_groups) + list(self.minecraft_auto_groups)
            ))
            self.minecraft_module.auto_groups = merged_groups
            self.minecraft_auto_groups = merged_groups

        self.feature_switch = {k: True for k in self.feature_switch.keys()}

        gfs_raw = merged.get("group_feature_switches", {})
        if isinstance(gfs_raw, dict):
            self.group_feature_switches = gfs_raw
        else:
            self.group_feature_switches = {}

        try:
            ttl = int(to_str(merged.get("group_admin_cache_ttl", "600")).strip() or "600")
        except Exception:
            ttl = 600
        if self.group_admin_module is not None:
            self.group_admin_module.set_ttl(ttl)

        if self.notice_module is not None:
            try:
                self.notice_module.reload(merged)
            except Exception:
                logger.exception("[apix-meme] 载入提示功能配置失败")

        if self.poke_reply_module is not None:
            try:
                _pw = None
                try:
                    if self.data_store is not None:
                        _pw = self.data_store.get("poke_reply_list")
                except Exception:
                    _pw = None
                self.poke_reply_module.reload(merged, words=_pw)
                _ps = self.poke_reply_module.stats()
                logger.info(
                    "[apix-meme] 戳一戳回复：开关=%s 词库=%d 条 "
                    "限频=%s 秒内 %s 次（全局） 只认戳自己=%s",
                    self.poke_reply_module.enable,
                    len(self.poke_reply_module.words or []),
                    _ps.get("window"), _ps.get("max"),
                    self.poke_reply_module.only_self)
            except Exception:
                logger.exception("[apix-meme] 载入戳一戳回复配置失败")

        self.group_mode = to_str(merged.get("group_mode", "off")).strip().lower() or "off"
        if self.group_mode not in ("off", "whitelist", "blacklist"):
            self.group_mode = "off"
        self.group_whitelist = parse_line_list(merged.get("group_whitelist", ""))
        self.group_blacklist = parse_line_list(merged.get("group_blacklist", ""))
        self.allowed_groups = parse_gid_whitelist(merged.get("global_groups", ""))
        self.status_groups = parse_gid_whitelist(merged.get("status_groups", ""))
        if "status_feature_enable" in merged:
            self.status_enable = bool(merged.get("status_feature_enable"))
        elif "status_enable" in merged:
            self.status_enable = bool(merged.get("status_enable"))
        else:
            self.status_enable = bool(merged.get("daily_status_push_enable", True))
        if self.status_push_module is not None:
            try:
                self.status_push_module.set_enable(self.status_enable)
            except Exception:
                logger.exception("[apix-meme] 同步电脑状态总开关失败")

        try:
            if self.hourly_chime_module is not None:
                self.hourly_chime_module._cfg_write = (
                    lambda gid, on: self._write_group_list(
                        "hourly_chime_groups", gid, on))
            if self.status_push_module is not None:
                self.status_push_module._cfg_write = (
                    lambda gid, on: self._write_group_list(
                        "daily_status_push_groups", gid, on))
            if self.daily_news_module is not None:
                self.daily_news_module._cfg_write = (
                    lambda gid, on: self._write_group_list(
                        "daily_news_auto_groups", gid, on))
                self.daily_news_module._manual_cfg_write = (
                    lambda gid, on: self._write_group_list(
                        "daily_news_groups", gid, on))
        except Exception:
            logger.exception("[apix-meme] 注入群号写回回调失败")

        self.score_admin_enable = bool(merged.get("score_admin_feature_enable", True))
        self.score_admin_groups = parse_gid_whitelist(merged.get("score_admin_groups", ""))

        if "meme_feature_enable" in merged:
            self.meme_enable = bool(merged.get("meme_feature_enable"))
        elif "meme_enable" in merged:
            self.meme_enable = bool(merged.get("meme_enable"))
        else:
            self.meme_enable = True
        self.meme_groups = parse_gid_whitelist(merged.get("meme_groups", ""))
        _block_map = parse_group_block_keywords(merged.get("group_block_keywords", ""))
        self.group_block_map = _block_map if isinstance(_block_map, dict) else {}
        self.disabled_groups = self._parse_disabled_groups(merged.get("disabled_groups", ""))

        if self.fortune_module is not None:
            try:
                self.fortune_module.reload(merged)
            except Exception:
                logger.exception("[apix-meme] 刷新运势模块失败")
        if self.husband_module is not None:
            try:
                self.husband_module.reload(merged)
            except Exception:
                logger.exception("[apix-meme] 刷新老公模块失败")
        if self.luck_module is not None:
            try:
                self.luck_module.reload(merged)
            except Exception:
                logger.exception("[apix-meme] 刷新人品模块失败")
        if getattr(self, "auth_module", None) is not None:
            try:
                self.auth_module.reload(merged)
            except Exception:
                logger.exception("[apix-meme] 刷新授权模块失败")
        if self.word_reply_module is not None:
            try:
                self.word_reply_module.reload(merged)
            except Exception:
                logger.exception("[apix-meme] 刷新词库模块失败")
        if self.greeting_module is not None:
            try:
                self.greeting_module.reload(merged)
            except Exception:
                logger.exception("[apix-meme] 刷新早安晚安模块失败")
        if self.minecraft_module is not None:
            try:
                self.minecraft_module.reload(merged)
            except Exception:
                logger.exception("[apix-meme] 刷新 MC 版本模块失败")
        if self.checkin_module is not None:
            try:
                self.checkin_module.reload(merged)
            except Exception:
                logger.exception("[apix-meme] 刷新签到模块失败")
        if self.hourly_chime_module is not None:
            try:
                self.hourly_chime_module.reload(merged)
            except Exception:
                logger.exception("[apix-meme] 刷新整点报时模块失败")
        if self.reply_tail is not None:
            try:
                self.reply_tail.reload(merged)
            except Exception:
                logger.exception("[apix-meme] 刷新回复尾巴配置失败")
        # ★★ 1.5.1：音乐功能（加载 music_* 配置）
        if getattr(self, "music_module", None) is not None:
            try:
                self.music_module.reload(merged)
            except Exception:
                logger.exception("[apix-meme] 刷新音乐模块失败")

        api_list_raw = merged.get("api_list", [])
        disk_rules = []
        if isinstance(api_list_raw, list):
            for item in api_list_raw:
                if not isinstance(item, dict):
                    continue
                kw = to_str(item.get("keywords", "")).strip()
                url_template = to_str(item.get("url_template", "")).strip()
                param_count = to_str(item.get("param_count", "1")).strip() or "1"
                old_meme = to_str(item.get("meme", "")).strip()
                if not url_template and old_meme:
                    url_template = (
                        f"{self.BASE_URL}?meme={old_meme}"
                        + self._key_pic_suffix()
                    )
                if url_template and "meme=&" in url_template and old_meme:
                    url_template = url_template.replace(
                        "meme=&", f"meme={old_meme}&"
                    )
                if not kw or not url_template:
                    continue
                keywords = [k.strip() for k in kw.split(",") if k.strip()]
                if not keywords:
                    continue
                disk_rules.append({
                    "keywords": keywords,
                    "url_template": url_template,
                    "param_count": param_count,
                    "media_type": to_str(item.get("media_type", "image")).strip().lower() or "image",
                    "json_field": to_str(item.get("json_field", "")).strip(),
                    "meme": old_meme,
                })
        elif isinstance(api_list_raw, str):
            for line in api_list_raw.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = [p.strip() for p in line.rsplit(",", 1)]
                if len(parts) != 2 or not parts[0] or not parts[1]:
                    continue
                kw_str, meme_id = parts
                keywords = [k.strip() for k in kw_str.split(",") if k.strip()]
                if not keywords:
                    continue
                url_template = (
                    f"{self.BASE_URL}?meme={meme_id}"
                    + self._key_pic_suffix()
                )
                disk_rules.append({
                    "keywords": keywords,
                    "url_template": url_template,
                    "param_count": "1",
                    "meme": meme_id,
                })

        if pending_rules:
            existing_keys = set()
            for r in disk_rules:
                existing_keys.add(",".join(r["keywords"]) + "|" + r.get("url_template", ""))
            for r in pending_rules:
                key = ",".join(r["keywords"]) + "|" + r.get("url_template", "")
                if key not in existing_keys:
                    disk_rules.append(r)
                    existing_keys.add(key)

        self.rules = disk_rules

        logger.info(
            f"[apix-meme] 已加载 {len(self.rules)} 条规则，"
            f"群模式={self.group_mode}，屏蔽群数={len(self.group_block_map)}，"
            f"关闭群数={len(self.disabled_groups)}，插件专属管理员={len(self.plugin_admins)}"
        )

    @staticmethod
    def _parse_disabled_groups(raw) -> list:
        if not raw:
            return []
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        return [x.strip() for x in str(raw).splitlines() if x.strip()]

    def save_rules_to_config(self):
        try:
            new_list = []
            for rule in self.rules:
                new_list.append({
                    "__template_key": "meme_rule",
                    "keywords": ",".join(rule["keywords"]),
                    "url_template": rule.get("url_template", ""),
                    "param_count": rule.get("param_count", "1"),
                    "media_type": rule.get("media_type", "image") or "image",
                    "json_field": rule.get("json_field", "") or "",
                })

            block_lines = []
            if isinstance(self.group_block_map, dict):
                for gid, kws in self.group_block_map.items():
                    if kws:
                        block_lines.append(f"{gid}: {','.join(kws)}")
            block_text = "\n".join(block_lines)

            word_reply_list = []
            if self.word_reply_module is not None:
                for rule in self.word_reply_module.rules:
                    word_reply_list.append({
                        "__template_key": "word_reply_item",
                        "keywords": ",".join(rule["keywords"]),
                        "reply": rule["reply"],
                    })

            if self.minecraft_module is not None:
                self.minecraft_auto_groups = list(self.minecraft_module.auto_groups)

            existing = self._read_disk_config() or {}
            if not isinstance(existing, dict):
                existing = {}

            existing["must_at_bot"] = self.must_at_bot
            # ★★ 1.4.3：不再写 `default_key` —— 接口地址改为**用户自己填完整**，
            #   插件不再自动拼 `&key=...`
            self._save_collection("api_list", [
                {k: v for k, v in r.items() if k != "__template_key"}
                for r in new_list
            ])

            existing["cmd_main"] = self.cmd_main_name
            existing["cmd_admin"] = self.cmd_admin_name
            existing["cmd_list"] = self.cmd_list_name
            existing["cmd_greeting_morning"] = self.cmd_greeting_morning_raw
            existing["cmd_greeting_night"] = self.cmd_greeting_night_raw
            existing["cmd_minecraft"] = self.cmd_minecraft_name
            existing["cmd_mc_auto"] = self.cmd_mc_auto_name
            existing["cmd_mc_test"] = self.cmd_mc_test_name
            existing["cmd_add"] = self.cmd_add_name
            existing["cmd_del"] = self.cmd_del_name
            existing["cmd_save"] = self.cmd_save_name
            existing["cmd_status"] = ",".join(self.cmd_status_names)
            existing["cmd_word_add"] = self.cmd_word_add_name
            existing["cmd_word_del"] = self.cmd_word_del_name
            existing["cmd_word_list"] = self.cmd_word_list_name
            existing["cmd_enable"] = self.cmd_enable_name
            existing["cmd_disable"] = self.cmd_disable_name
            existing["cmd_view_admin"] = self.cmd_view_admin_name
            existing["cmd_refresh_admin"] = self.cmd_refresh_admin_name
            existing["bird_enable"] = bool(getattr(self, "bird_enable", True))
            existing["cmd_bird"] = ",".join(
                getattr(self, "cmd_bird", None) or ["今日小鸟"])
            existing["cmd_gallery"] = ",".join(
                getattr(self, "cmd_gallery", None) or ["我的图鉴"])
            existing["cmd_bird_menu"] = ",".join(
                getattr(self, "cmd_bird_menu", None) or ["小鸟图鉴"])
            existing["cmd_bird_list"] = ",".join(
                getattr(self, "cmd_bird_list", None) or ["图鉴列表"])
            existing["cmd_bird_view"] = ",".join(
                getattr(self, "cmd_bird_view", None) or ["图鉴查看"])
            existing["bird_list_per_page"] = int(
                getattr(self, "bird_list_per_page", 10) or 10)
            existing["bird_groups"] = ",".join(
                str(x) for x in (getattr(self, "bird_groups", None) or []))
            existing["bird_rank_enable"] = bool(
                getattr(self, "bird_rank_enable", True))
            existing["cmd_add_admin"] = self.cmd_add_admin_name
            existing["cmd_del_admin"] = self.cmd_del_admin_name
            existing["cmd_admin_list"] = self.cmd_admin_list_name
            existing["cmd_enable_feature"] = self.cmd_enable_feature_name
            existing["cmd_disable_feature"] = self.cmd_disable_feature_name
            existing["cmd_feature_status"] = self.cmd_feature_status_name
            existing["cmd_greeting_rank"] = self.cmd_greeting_rank_name
            existing["cmd_greeting_stats"] = self.cmd_greeting_stats_name
            existing["cmd_job_add_names"] = self.cmd_job_add_name
            existing["cmd_job_del_names"] = self.cmd_job_del_name
            existing["cmd_mount_add_names"] = self.cmd_mount_add_name
            existing["cmd_mount_del_names"] = self.cmd_mount_del_name

            existing["cmd_must_at_bot_on"] = self.cmd_must_at_bot_on_name
            existing["cmd_must_at_bot_off"] = self.cmd_must_at_bot_off_name
            existing["cmd_must_at_bot_status"] = self.cmd_must_at_bot_status_name
            existing["group_must_at_bot"] = self.group_must_at_bot

            existing["cmd_meme_menu"] = self.cmd_meme_menu_name
            existing["cmd_meme_image"] = self.cmd_meme_image_name
            existing["cmd_meme_video"] = self.cmd_meme_video_name
            existing["cmd_meme_text"] = self.cmd_meme_text_name
            existing["cmd_mc_menu"] = self.cmd_mc_menu_name
            existing["cmd_greeting_menu"] = self.cmd_greeting_menu_name
            existing["cmd_checkin_menu"] = self.cmd_checkin_menu_name
            existing["cmd_daily_menu"] = self.cmd_daily_menu_name

            existing["cmd_chime_on"] = self.cmd_chime_on_name
            existing["cmd_chime_off"] = self.cmd_chime_off_name
            existing["cmd_chime_status"] = self.cmd_chime_status_name
            existing["cmd_chime_hours"] = self.cmd_chime_hours_name
            existing["cmd_chime_text"] = self.cmd_chime_text_name
            existing["cmd_chime_list"] = self.cmd_chime_list_name
            existing["cmd_chime_test"] = self.cmd_chime_test_name
            existing["cmd_daily_push_on"] = self.cmd_daily_push_on_name
            existing["cmd_daily_push_off"] = self.cmd_daily_push_off_name
            existing["cmd_daily_push_time"] = self.cmd_daily_push_time_name
            existing["cmd_daily_push_list"] = self.cmd_daily_push_list_name
            existing["cmd_blacklist_list"] = self.cmd_blacklist_list_name
            existing["member_blacklist"] = self._blacklist_raw
            existing["cmd_daily_news_manual_on"] = \
                self.cmd_daily_news_manual_on_name
            existing["cmd_daily_news_manual_off"] = \
                self.cmd_daily_news_manual_off_name
            existing["cmd_daily_news"] = self.cmd_daily_news_name
            existing["cmd_daily_news_on"] = self.cmd_daily_news_on_name
            existing["cmd_daily_news_off"] = self.cmd_daily_news_off_name
            existing["cmd_daily_news_time"] = self.cmd_daily_news_time_name
            existing["cmd_daily_news_list"] = self.cmd_daily_news_list_name

            existing["plugin_admins"] = "\n".join(self.plugin_admins)
            existing["score_admin_whitelist"] = "\n".join(
                self.score_admin_whitelist or [])
            existing["web_admin_token"] = self.web_admin_token
            existing["feature_switch"] = {k: True for k in self.feature_switch.keys()}
            existing["group_feature_switches"] = self.group_feature_switches
            existing["group_admin_cache_ttl"] = str(
                self.group_admin_module.cache_ttl if self.group_admin_module else 600
            )
            existing["group_mode"] = self.group_mode
            existing["group_whitelist"] = "\n".join(self.group_whitelist)
            existing["group_blacklist"] = "\n".join(self.group_blacklist)
            existing["global_groups"] = ",".join(self.allowed_groups)
            existing["status_groups"] = ",".join(self.status_groups)
            existing["status_feature_enable"] = self.status_enable
            existing["meme_feature_enable"] = self.meme_enable
            existing["meme_groups"] = ",".join(self.meme_groups)
            if self.status_push_module is not None:
                existing.setdefault("daily_status_push_groups", "")
            existing["group_block_keywords"] = block_text
            existing["disabled_groups"] = "\n".join(self.disabled_groups)
            self._save_collection("word_reply_list", [
                {k: v for k, v in r.items() if k != "__template_key"}
                for r in word_reply_list
            ])
            existing["minecraft_auto_groups"] = "\n".join(self.minecraft_auto_groups)
            existing["minecraft_test_groups"] = "\n".join(self.minecraft_test_groups)
            existing["minecraft_groups"] = ",".join(self.minecraft_groups or [])
            existing["qqinfo_groups"] = ",".join(
                getattr(self, "qqinfo_groups", None) or [])
            existing["qqinfo_enable"] = bool(
                getattr(self, "qqinfo_enable", True))
            existing["cmd_qqinfo"] = getattr(self, "cmd_qqinfo", "查询")
            existing["cmd_qqinfo_menu"] = getattr(
                self, "cmd_qqinfo_menu_name", "查询功能")

            existing["racing_enable"] = self.racing_enable
            existing["racing_groups"] = ",".join(
                getattr(self, "racing_groups", None) or [])
            for _k, _attr, _d in (
                    ("cmd_race_join", "cmd_race_join", "参与比赛"),
                    ("cmd_race_fast", "cmd_race_fast", "快速比赛"),
                    ("cmd_race_my", "cmd_race_my", "我的赛车"),
                    ("cmd_race_equip", "cmd_race_equip", "装备赛车"),
                    ("cmd_race_view", "cmd_race_view", "赛车查看"),
                    ("cmd_race_shop", "cmd_race_shop", "赛车商店"),
                    ("cmd_race_buy", "cmd_race_buy", "购买赛车"),
                    ("cmd_race_lucky", "cmd_race_lucky", "赛车抽奖"),
                    ("cmd_race_lucky_all", "cmd_race_lucky_all",
                     "赛车一键抽奖,赛车全部抽奖"),
                    ("cmd_race_menu", "cmd_race_menu_name", "赛车功能")):
                existing[_k] = getattr(self, _attr, _d)
            for _k, _attr, _d in (
                    ("race_players", "race_players", 5),
                    ("race_wait_sec", "race_wait_sec", 3600),
                    ("race_daily_limit", "race_daily_limit", 10),
                    ("race_entry_fee", "race_entry_fee", 500),
                    ("race_pool_bonus_min", "race_pool_bonus_min", 1000),
                    ("race_pool_bonus_max", "race_pool_bonus_max", 5000),
                    ("race_fast_npc", "race_fast_npc", 5),
                    ("race_npc_power", "race_npc_power", 100),
                    ("race_lucky_fee", "race_lucky_fee", 2000),
                    ("race_lucky_daily_limit", "race_lucky_daily_limit", 1)):
                existing[_k] = str(getattr(self, _attr, _d))
            existing["race_npc_fill"] = bool(
                getattr(self, "race_npc_fill", True))
            existing["race_show_time"] = bool(
                getattr(self, "race_show_time", True))
            existing["race_shop_list"] = str(
                getattr(self, "race_shop_list", "") or "")
            existing["race_lottery_cars"] = str(
                getattr(self, "race_lottery_cars", "") or "")

            json.dumps(existing, ensure_ascii=False)

            ok = self._write_disk_config(existing)
            if not ok:
                return False

            self.config.update(existing)
            self._dirty = False
            logger.info(f"[apix-meme] 保存成功，共 {len(new_list)} 条表情包规则")
            return True
        except Exception:
            logger.exception("[apix-meme] 保存规则失败（未知错误）")
            return False

    def _extract_multi_arg(self, text: str, cmd_name: str):
        variants = [cmd_name, f"/{cmd_name}"]
        for v in variants:
            if text.startswith(v + " "):
                rest = text[len(v) + 1:].strip()
                parts = rest.split(maxsplit=1)
                a1 = parts[0] if len(parts) >= 1 else ""
                a2 = parts[1] if len(parts) >= 2 else None
                return a1, a2
        return None, None

    def _extract_arg(self, text: str, cmd_name: str):
        n2 = f"/{cmd_name}"
        if text.startswith(n2 + " "):
            return text[len(n2) + 1:].strip()
        if text.startswith(cmd_name + " "):
            return text[len(cmd_name) + 1:].strip()
        return None

    def _is_disable_feature(self, text: str):
        return self._extract_multi_arg(text, self.cmd_disable_feature_name)

    def _is_enable_feature(self, text: str):
        return self._extract_multi_arg(text, self.cmd_enable_feature_name)

    def _is_feature_status_cmd(self, text: str):
        variants = [self.cmd_feature_status_name, f"/{self.cmd_feature_status_name}"]
        for v in variants:
            if text == v:
                return True, None
            if text.startswith(v + " "):
                tg = text[len(v) + 1:].strip()
                return True, tg
        return False, None

    def _is_greeting_rank_cmd(self, text: str) -> bool:
        n1 = self.cmd_greeting_rank_name
        return text in (n1, f"/{n1}")

    def _is_greeting_stats_cmd(self, text: str):
        """打卡统计：不跟参数查自己，可跟 @某人 / QQ号 查他人。"""
        n1 = self.cmd_greeting_stats_name
        n2 = f"/{n1}"
        if text == n1 or text == n2:
            return ("self",)
        _carg = cmd_arg(text, (n1, n2))
        if _carg is not None:
                arg = _carg
                if arg:
                    return ("target", arg)
                return ("self",)
        return None

    def _interface_media_of(self, rule: dict) -> str:
        """取规则的媒体类型：image / video / text（缺省视为 image）。"""
        media = str(rule.get("media_type", "") or "").strip().lower()
        if media == "video":
            return "video"
        if media == "text":
            return "text"
        return "image"

    def _render_interface_list(self, page: int, media: str = "") -> str:
        """
        渲染接口规则列表。返回空串表示没有可展示的规则。
        media: ""=全部 / "image"=只看图片 / "video"=只看视频
        """
        media = str(media or "").strip().lower()
        if media in ("image", "video", "text"):
            pool = [r for r in self.rules if self._interface_media_of(r) == media]
            title = {"image": "图片接口列表", "video": "视频接口列表",
                     "text": "文本接口列表"}[media]
        else:
            pool = list(self.rules)
            media = ""
            title = "接口列表"

        total = len(pool)
        if total == 0:
            return ""

        page_size = self.PAGE_SIZE
        max_page = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(int(page or 1), max_page))
        start = (page - 1) * page_size
        end = start + page_size

        lines = [f"{title_bar(f'{title}（第 {page}/{max_page} 页，共 {total} 条）')}"]
        for i, rule in enumerate(pool[start:end], start=start + 1):
            kw = " / ".join(rule["keywords"])
            pc = rule.get("param_count", "1")
            try:
                ptypes = detect_param_types(rule.get("url_template", ""), int(pc))
            except Exception:
                ptypes = ["any"] * int(pc) if str(pc).isdigit() else []

            if str(pc) == "0":
                type_desc = "无参数"
            else:
                labels = []
                for t in ptypes:
                    if t == "qq":
                        labels.append("QQ号或@")
                    elif t == "text":
                        labels.append("文本")
                    else:
                        labels.append("参数")
                type_desc = " + ".join(labels) if labels else "参数"

            media_desc = {"video": "视频", "text": "文本"}.get(
                self._interface_media_of(rule), "图片")
            lines.append(f"{i}. {kw}（{type_desc}｜{media_desc}）")

        return "\n".join(lines)

    def _interface_menu_hint(self, media: str) -> str:
        """翻页提示里用的指令名：按媒体类型取对应配置的指令。"""
        media = str(media or "").strip().lower()
        if media == "video":
            return getattr(self, "cmd_meme_video_name", "") or "视频接口"
        if media == "image":
            return getattr(self, "cmd_meme_image_name", "") or "图片接口"
        if media == "text":
            return getattr(self, "cmd_meme_text_name", "") or "文本接口"
        return self.cmd_list_name

    def _interface_usage_lines(self, media: str = "") -> list:
        """
        根据该分类下的实际规则，生成「怎么调用」的示例。
        全部是无参数接口时只提示发关键词；含带参数接口时才给参数示例。
        """
        media = str(media or "").strip().lower()
        if media in ("image", "video", "text"):
            pool = [r for r in self.rules if self._interface_media_of(r) == media]
        else:
            pool = list(self.rules)

        def _first_kw(rules):
            for r in rules:
                kws = r.get("keywords") or []
                for k in kws:
                    if str(k).strip():
                        return str(k).strip()
            return ""

        no_param, with_param = [], []
        for r in pool:
            pc = str(r.get("param_count", "1") or "1").strip()
            if pc.isdigit() and int(pc) > 0:
                with_param.append(r)
            else:
                no_param.append(r)

        if not with_param:
            kw = _first_kw(no_param)
            if not kw:
                return []
            return [
                "发送「关键词」调用无参数接口",
                f"例：{kw}",
            ]

        if not no_param:
            kw = _first_kw(with_param)
            lines = ["发送「关键词 + 参数」调用带参数接口"]
            if kw:
                lines.append(f"例：{kw} @某人")
                lines.append(f"例：{kw} QQ号")
            else:
                lines.append("例：关键词 @某人")
            return lines

        lines = ["发送「关键词」调用无参数接口，发送「关键词 + 参数」调用带参数接口"]
        kw_np = _first_kw(no_param)
        if kw_np:
            lines.append(f"例：{kw_np}")
        kw_wp = _first_kw(with_param)
        if kw_wp:
            lines.append(f"例：{kw_wp} @某人")
        return lines

    def _is_checkin_cmd(self, text: str) -> bool:
        if self.checkin_module is None:
            return False
        try:
            return self.checkin_module.match_trigger(text)
        except Exception:
            return False

    def _is_checkin_rank_cmd(self, text: str) -> bool:
        """★ 1.7.6：旧的「签到排行」已删除，永远返回 False（保留方法以免外部调用报错）"""
        return False

    def _is_checkin_info_cmd(self, text: str):
        if self.checkin_module is None:
            return None
        try:
            return self.checkin_module.match_info_trigger(text)
        except Exception:
            return None

    def _checkin_first_trigger(self) -> str:
        """取签到统计的第一个触发词，用于提示文案。"""
        if self.checkin_module is None:
            return "签到统计"
        try:
            names = self.checkin_module.info_trigger_names
            if names:
                return str(names[0])
        except Exception:
            pass
        return "签到统计"

    def _match_status_cmd(self, text: str) -> bool:
        for name in self.cmd_status_names:
            if not name:
                continue
            if text == name or text == f"/{name}":
                return True
        return False

    def _is_main_menu(self, text: str) -> bool:
        return self._name_list_hit(text, "cmd_main_name")

    def _is_admin_menu(self, text: str) -> bool:
        return self._name_list_hit(text, "cmd_admin_name")

    def _is_meme_menu(self, text: str) -> bool:
        """接口系统二级菜单入口，指令名支持英文逗号分隔多个。"""
        for n in self._split_names(self.cmd_meme_menu_name):
            if text == n or text == f"/{n}":
                return True
        return False

    def _is_mc_menu(self, text: str) -> bool:
        return self._name_list_hit(text, "cmd_mc_menu_name")

    # ★★ 1.5.0：世界系统 4 个子菜单识别（支持自定义多个，逗号分隔）
    def _is_mc_sub_menu(self, text: str, attr: str) -> bool:
        raw = to_str(getattr(self, attr, "") or "")
        for nm in [x.strip() for x in raw.split(",") if x.strip()]:
            if text in (nm, f"/{nm}"):
                return True
        return False

    def _is_mc_version_menu(self, text: str) -> bool:
        return self._is_mc_sub_menu(text, "cmd_mc_version_menu_name")

    def _is_mc_server_menu(self, text: str) -> bool:
        return self._is_mc_sub_menu(text, "cmd_mc_server_menu_name")

    def _is_mc_player_menu(self, text: str) -> bool:
        return self._is_mc_sub_menu(text, "cmd_mc_player_menu_name")

    def _is_mc_push_menu(self, text: str) -> bool:
        return self._is_mc_sub_menu(text, "cmd_mc_push_menu_name")

    def _is_greeting_menu(self, text: str) -> bool:
        return self._name_list_hit(text, "cmd_greeting_menu_name")

    def _is_checkin_menu(self, text: str) -> bool:
        return self._name_list_hit(text, "cmd_checkin_menu_name")

    def _is_entertain_menu(self, text: str) -> bool:
        """娱乐功能入口（支持多个指令名）"""
        for n in self._split_names(getattr(self, "cmd_entertain_menu_name", "")):
            if text in (n, f"/{n}"):
                return True
        return False

    def _is_qqinfo_menu(self, text: str) -> bool:
        """「查询功能」子菜单入口"""
        t = str(text or "").strip()
        nm = getattr(self, "cmd_qqinfo_menu_name", "") or "查询功能"
        return t in (nm, f"/{nm}")

    def _pet_parse_target(self, event, arg: str) -> str:
        """
        从「决斗」/「他的宠物」的参数里取目标 QQ。

        优先用消息组件里的 @（最可靠），其次从文本里抠：
          「@某人」「@某人(123456)」「123456」
        """
        tid = ""
        try:
            for _x in (get_ats_from_msg(event) or []):
                _s = to_str(_x).strip()
                if _s.isdigit():
                    tid = _s
                    break
        except Exception:
            logger.debug("[apix-meme] 取 @ 列表失败", exc_info=True)
        if tid:
            return tid
        raw = str(arg or "")
        m = re.search(r"[\(（](\d{5,12})[\)）]", raw)
        if m:
            return m.group(1)
        m = re.search(r"\d{5,12}", raw)
        return m.group(0) if m else ""

    def _pet_parse_names(self, event, arg: str):
        """
        解析决斗参数，返回 (目标QQ, 我方宠物名, 对方宠物名)。

        支持：
          「@某人」                    -> ("QQ", "", "")
          「@某人 对方宠物名」          -> ("QQ", "", "对方宠物名")
          「我方宠物名 @某人 对方宠物名」-> ("QQ", "我方宠物名", "对方宠物名")

        ★ 我方宠物名来自**指令前面的部分**（见 on_msg 里的 _pet_pre），
          这里只负责 @ 后面的部分。
        """
        tid = self._pet_parse_target(event, arg)
        rest = str(arg or "")
        rest = re.sub(r"@[^\s]*", " ", rest)
        rest = re.sub(r"[\(（]\d{5,12}[\)）]", " ", rest)
        rest = re.sub(r"\d{5,12}", " ", rest)
        rest = " ".join(rest.split()).strip()
        d_want = rest
        return (tid, d_want)

    async def _dispatch_qqinfo(self, event, msg_str: str, group_id: str):
        """
        「查询功能」的调度（从 on_msg 抽出来，便于测试）。

        处理顺序：
          ① 子菜单入口        -> 发菜单
          ② 指令匹配          -> 校验开关/黑名单/群功能状态 -> 处理
          ③ ★ @机器人 + 光发指令 -> 目标换成**机器人自己的 QQ**
             （用户 @ 机器人 = 想看机器人账号的资料）

        命中并已发送时把 self._qqinfo_handled 置 True，
        调用方据此决定是否 return。
        """
        self._qqinfo_handled = False

        if self._is_qqinfo_menu(msg_str):
            async for _r in self._send_content(event,
                                               self._build_qqinfo_menu()):
                yield _r
            self._qqinfo_handled = True
            return

        _bot_at = False
        try:
            _self_id = to_str(event.get_self_id())
            if _self_id:
                _bot_at = _self_id in [to_str(x) for x in
                                       (get_ats_from_msg(event) or [])]
        except Exception:
            _bot_at = False

        _qq_arg = self._match_qqinfo_cmd(msg_str, bot_mentioned=_bot_at)
        if _qq_arg is None:
            return

        if _qq_arg == "" and _bot_at:
            try:
                _self_qq = to_str(event.get_self_id()).strip()
            except Exception:
                _self_qq = ""
            if _self_qq.isdigit():
                _qq_arg = _self_qq
                logger.info("[apix-meme][追踪] @机器人 -> 查询机器人自己 %s",
                            _self_qq)

        if not getattr(self, "qqinfo_enable", True):
            self._qqinfo_handled = True
            return
        if not group_allowed(group_id, getattr(self, "qqinfo_groups", [])):
            self._qqinfo_handled = True
            return
        if not self._is_group_feature_enable(group_id, "qqinfo"):
            self._qqinfo_handled = True
            return

        async for _r in self._handle_qqinfo_cmd(
                event, group_id, _qq_arg, bot_mentioned=_bot_at):
            yield _r
        self._qqinfo_handled = True

    def _match_qqinfo_cmd(self, text: str, bot_mentioned: bool = False):
        """
        匹配「查询功能」指令。

        返回：None=没命中；""=查自己；否则=参数原文（@某人 / QQ号）

        ★★ **必须带参数**：光发「查询」两个字**不触发**。
           因为「查询」是很常见的词，不带参数太容易误触发
           （群里随便说一句带「查询」的话就出图，很烦）。

        ★ 例外：**@ 了机器人**时，光发「查询」也触发（返回 ""）。
          因为 AstrBot 会把 @ 机器人从消息文本里剔除，
          用户看到的是「@机器人 查询」，但插件拿到的是「查询」。
          → 返回 ""，由调用方把目标换成**机器人自己的 QQ**
            （用户 @ 机器人 = 想看机器人账号的资料）。

        支持：`查询 @某人` / `查询 QQ号` / `查询@某人(QQ)` / `查询123456789`
        只认「指令 + @某人 或 纯数字 QQ」，后面跟别的字符一律不触发。
        """
        t = str(text or "").strip()
        t = t.replace("\r", " ").replace("\n", " ").replace("\t", " ")
        t = t.replace("\u3000", " ")
        t = " ".join(t.split())
        if not t:
            return None
        names = self._split_names(getattr(self, "cmd_qqinfo", "") or "查询")
        for nm in sorted(names, key=len, reverse=True):
            if not nm:
                continue
            for prefix in (nm, f"/{nm}"):
                if t == prefix:
                    return "" if bot_mentioned else None
                if not t.startswith(prefix):
                    continue
                rest = t[len(prefix):].strip(" ：:，,")
                if not rest:
                    return "" if bot_mentioned else None
                if rest.startswith("@") or rest.isdigit():
                    return rest
                return None
        return None

    async def _handle_qqinfo_cmd(self, event, group_id, arg,
                                 bot_mentioned: bool = False):
        """
        处理查询指令：拉资料 -> 格式化 -> 出卡片（带头像）-> 发送。

        arg            参数原文（@某人 / QQ号）；为空表示无目标
        bot_mentioned  是否 @ 了机器人。
                       ★ @ 机器人 + 光发「查询」时，调用方已经把 arg
                         换成**机器人自己的 QQ**，所以这里是正常查资料。
                       带不了目标（如「查询 你好」）才出用法菜单。
        """
        if not _HAS_QQ_INFO:
            async for _r in self._send_content(
                    event, "❌ 查询模块未加载（core/qq_info.py）"):
                yield _r
            return
        target = ""
        _at_raw = None
        _self = ""
        try:
            _self = to_str(event.get_self_id())
        except Exception:
            _self = ""

        try:
            ats = get_ats_from_msg(event)
            _at_raw = [to_str(x).strip() for x in (ats or []) if to_str(x).strip()]
            _others = [x for x in _at_raw if not (_self and x == _self)]
            if _others:
                target = _others[0]
        except Exception:
            logger.exception("[apix-meme] 取 @ 列表失败")
            _at_raw = []
            target = ""
        _parsed = ""
        if not target.isdigit() and arg:
            try:
                _parsed = _qq_info.parse_target(arg) or ""
            except Exception:
                logger.exception("[apix-meme] 解析查询目标失败")
                _parsed = ""
            if _parsed.isdigit():
                target = _parsed
        try:
            logger.info(
                "[apix-meme][追踪] 查询目标：msg=%r arg=%r ats=%r "
                "self=%r parsed=%r -> target=%r",
                str(getattr(event, "message_str", ""))[:80], arg,
                _at_raw, _self, _parsed, target)
        except Exception:
            pass
        if not target.isdigit():
            _menu = self._build_qqinfo_menu()
            _tip = ("⚠️ 没认出要查谁的 QQ\n"
                    "💡 用法：查询 @某人 或 查询 QQ号")
            async for _r in self._send_content(event, _tip + "\n\n" + _menu):
                yield _r
            return
        try:
            info = await _qq_info.fetch_user_info(event, target, group_id)
        except Exception:
            logger.exception("[apix-meme] 查询 QQ 资料失败")
            info = {}
        text = _qq_info.format_user_info(info, target)
        try:
            logger.info("[apix-meme][追踪] 查询结果 %s：\n%s", target, text)
        except Exception:
            pass

        om = getattr(self, "output_mode_module", None)
        _mode = "text"
        if om is not None:
            try:
                _mode = om.mode_of(group_id) or "text"
            except Exception:
                _mode = "text"
        img_path = ""
        if (str(_mode).startswith("image") and om is not None
                and hasattr(om, "render_query_card")):
            avatar_path = ""
            try:
                img_path = await om.render_query_card(
                    text, avatar_path=avatar_path,
                    tail=await self._peek_tail_text(event))
            except Exception:
                logger.exception("[apix-meme] 查询卡片渲染失败")
                img_path = ""
        if img_path:
            try:
                self._mark_tail_done(event)
                from astrbot.api.message_components import Image as _Img
                yield event.chain_result([_Img.fromFileSystem(img_path)])
                return
            except Exception:
                logger.exception("[apix-meme] 查询图片发送失败，回落文字")
        async for _r in self._send_content(event, text):
            yield _r

    def _is_daily_menu(self, text: str) -> bool:
        return self._name_list_hit(text, "cmd_daily_menu_name")

    def _is_fortune_menu(self, text: str) -> bool:
        return text in ("运势功能", "/运势功能")

    def _is_husband_menu(self, text: str) -> bool:
        return text in ("老公功能", "/老公功能")

    def _is_luck_menu(self, text: str) -> bool:
        return text in ("人品功能", "/人品功能")

    def _is_news_menu(self, text: str) -> bool:
        return text in ("读报功能", "/读报功能")

    def _is_meme_feature_menu(self, text: str):
        """
        接口系统三级子菜单识别。
        返回 (media, page)：media 为 "image" / "video" / ""（全部）。
        图片、视频各自使用可后台自定义的指令名（支持英文逗号分隔多个）。
        """
        groups = [
            ("image", self._split_names(getattr(self, "cmd_meme_image_name", "") or "图片接口")),
            ("video", self._split_names(getattr(self, "cmd_meme_video_name", "") or "视频接口")),
            ("text", self._split_names(getattr(self, "cmd_meme_text_name", "") or "文本接口")),
        ]
        for media, names in groups:
            for n in names:
                if not n:
                    continue
                if text == n or text == f"/{n}":
                    return (media, 1)
                _carg = cmd_arg(text, (n,))
                if _carg is not None:
                        arg = _carg
                        if arg.isdigit():
                            return (media, int(arg))
                        return (media, 1)
        return None

    @staticmethod
    def _split_names(raw) -> list:
        """
        把指令名配置统一拆成列表。

        ★ 必须支持 list/tuple：`cmd_bird_menu` 等在配置加载时
          已经被处理成列表了，若这里再做 str(list)，
          会得到字面量 "['图鉴系统']"，导致匹配永远失败
          （「图鉴系统」发出去毫无反应就是这个原因）。
        """
        if isinstance(raw, (list, tuple, set)):
            return [str(x).strip() for x in raw if str(x).strip()]
        return [x.strip() for x in
                str(raw or "").replace("，", ",").split(",") if x.strip()]

    def _is_greeting_morning_menu(self, text: str) -> bool:
        return text in ("早安打卡", "/早安打卡")

    def _is_greeting_night_menu(self, text: str) -> bool:
        return text in ("晚安打卡", "/晚安打卡")

    def _is_greeting_rank_menu(self, text: str) -> bool:
        return text in ("打卡排行功能", "/打卡排行功能")

    def _is_checkin_signin_menu(self, text: str) -> bool:
        return self._menu_entry_hit("checkin_signin", text)

    def _is_checkin_query_menu(self, text: str) -> bool:
        return self._menu_entry_hit("checkin_query", text)

    MENU_ENTRY_DEFS = {
        # ★★ 1.5.0：补齐（入口名存在 *_trigger_names / cmd_* 上）
        "admin":             ("cmd_admin", "管理系统"),
        "checkin":           ("checkin_trigger_names", "签到系统"),
        "daily":             ("cmd_daily_menu", "每日系列"),
        "entertain":         ("cmd_entertain_menu", "娱乐功能"),
        "fortune":           ("fortune_trigger_names", "运势功能"),
        "greeting":          ("cmd_greeting_menu", "打卡系统"),
        "greeting_morning":  ("cmd_greeting_morning", "早安打卡"),
        "greeting_night":    ("cmd_greeting_night", "晚安打卡"),
        "greeting_rank":     ("cmd_greeting_rank", "打卡排行"),
        "husband":           ("husband_trigger_names", "老公功能"),
        "luck":              ("luck_trigger_names", "人品功能"),
        "main":              ("cmd_main", "主菜单"),
        "mc":                ("cmd_mc_menu", "世界系统"),
        "meme":              ("cmd_meme_menu", "接口系统"),
        "output_mode":       ("cmd_output_mode", "输出模式"),
        "qqinfo":            ("cmd_qqinfo_menu", "查询功能"),
        "weather":           ("cmd_weather_menu", "天气查询"),
        # ★★ 1.5.0：授权系统入口（可自定义多个名，与「授权 xxx」指令互不干扰）
        "auth":            ("menu_auth_names", "授权系统"),
        "stats":           ("menu_stats_names",    "运行数据"),
        "racing":           ("cmd_race_menu",       "赛车功能"),
        # ★★ 1.6.0：农场功能 + 6 个下级菜单
        "farm":             ("cmd_farm_menu",       "农场功能"),
        "farm_plant":       ("menu_farm_plant_names",  "种植管理"),
        "farm_seed":        ("menu_farm_seed_names",   "种子说明"),
        "farm_fert":        ("menu_farm_fert_names",   "农具说明"),
        "farm_livestock":   ("menu_farm_livestock_names", "家畜说明"),
        "farm_machine":     ("menu_farm_machine_names",   "农机说明"),
        "farm_steal":       ("menu_farm_steal_names",     "偷菜玩法"),
        "checkin_signin":  ("menu_checkin_signin_names",  "签到指令"),
        "checkin_query":   ("menu_checkin_query_names",   "查询指令"),
        "checkin_bank":    ("menu_checkin_bank_names",    "银行功能"),
        "checkin_lottery": ("menu_checkin_lottery_names", "抽奖功能"),
        "checkin_steal":   ("menu_checkin_steal_names",   "神偷功能"),
        "checkin_job":     ("menu_checkin_job_names",     "打工功能"),
        "checkin_shop_help": ("menu_checkin_shop_help_names", "商店帮助"),
        "job_list":        ("job_info_trigger_names",       "打工列表"),
        "job_refill":      ("stamina_refill_menu_names",    "加班功能"),
        "job_refill_status": ("stamina_refill_status_names", "加班状态"),
        "stamina":         ("stamina_view_trigger_names",   "我的体力"),
        "checkin_bank_save": ("menu_bank_save_names",     "存钱取钱"),
        "checkin_bank_loan": ("menu_bank_loan_names",     "借款还款"),
        "checkin_bank_fund": ("menu_bank_fund_names",     "理财功能"),
        "checkin_bank_rob":  ("menu_bank_rob_names",      "抢银行帮助"),
        "pet":             ("menu_pet_names",             "宠物功能"),
        "pet_care":        ("menu_pet_care_names",        "宠物养成"),
        "pet_fight":       ("menu_pet_fight_names",       "宠物玩法"),
        "pet_item":        ("menu_pet_item_names",        "宠物道具"),
        # ★★ 1.5.1：音乐功能主菜单入口（配置键 menu_music_names）
        #   ★ 原来这里的 music_source / music_find 两个孤儿键已清理 ——
        #     市场版音乐功能只注册一个「音乐功能」主菜单，
        #     音源切换 / 搜歌 都是**指令**（cmd_music_*），不是菜单入口。
        "music":           ("menu_music_names",           "音乐功能"),
        "guess":           ("cmd_guess_menu",            "猜数功能"),
        "mount_manage":    ("menu_mount_manage_names",    "坐骑管理"),
        "mount_admin":     ("menu_mount_admin_names",     "坐骑设置"),
        "auth_cmd":        ("menu_auth_cmd_names",        "授权指令"),
        "auth_query":      ("menu_auth_query_names",      "授权查询"),
    }
    MENU_ENTRY_LEGACY = {
        "checkin_bank_fund": ("银行理财",),
    }

    def _menu_entry_names(self, menu_id: str) -> list:
        """
        该菜单入口的**全部**可用名字。

        顺序：① 用户当前配置的名字 ② 历史配置过的名字（含默认名）
             ③ 内置旧名兼容
        ★★ 1.7.7：②是为了「改过名后旧名字还能认」——
          否则用户改完名，别人还发老名字就再也打不开了。
        """
        mid = str(menu_id or "").strip()
        item = self.MENU_ENTRY_DEFS.get(mid)
        out = []
        if item:
            key, dflt = item
            for x in str(getattr(self, "_menu_entry_raw", {}).get(key)
                         or "").replace("，", ",").split(","):
                x = x.strip()
                if x and x not in out:
                    out.append(x)
            if not out and dflt:
                out.append(dflt)
            try:
                for x in (getattr(self, "_menu_entry_prev", {}) or {}).get(
                        key, ()):
                    x = str(x or "").strip()
                    if x and x not in out:
                        out.append(x)
            except Exception:
                pass
        for x in self.MENU_ENTRY_LEGACY.get(mid, ()):
            if x and x not in out:
                out.append(x)
        return out

    def _menu_entry_name(self, menu_id: str) -> str:
        """该菜单入口的**主名**（第一个）—— 菜单文案里显示的就是它"""
        n = self._menu_entry_names(menu_id)
        return n[0] if n else ""

    def _menu_entry_hit(self, menu_id: str, text: str) -> bool:
        """用户发的是不是这个菜单入口（支持前导 /）"""
        s = str(text or "").strip()
        return s in [x for n in self._menu_entry_names(menu_id)
                     for x in (n, f"/{n}")]

    def _is_stats_menu(self, text: str) -> bool:
        """
        ★★ 1.4.0：远程指令「运行数据」—— 查看插件运行统计。

        ★ 任何人可用（只读展示，不涉及任何修改）。
        ★ 上架版**只有文本**（本版不启用图片模式）。
        """
        return self._menu_entry_hit("stats", text)

    def _stats_lines(self) -> list:
        """
        ★★ 1.4.0：运行数据文本（后台「数据统计」的同款口径）。

        ★ 所有数字都在**本地**算出来，不采集隐私、不上报。
        """
        st = {}
        try:
            st = self._bot_stats()
        except Exception:
            logger.exception("[apix-meme] 收集运行数据失败")

        def _n(k):
            try:
                return f"{int(st.get(k, 0) or 0):,}"
            except Exception:
                return "0"

        _tested = bool(st.get("api_tested"))
        _sub = (f"（测于 {st.get('api_test_at') or '—'}）" if _tested
                else "（还没测过，去后台「接口测试」跑一次）")
        lines = [
            f"📊 运行数据　{st.get('version') or ''}".rstrip(),
            f"　 快照：{st.get('now') or '—'}",
            "",
            f"📥 收到信息累计　{_n('recv')}",
            f"📤 发送信息累计　{_n('send')}",
            f"🔔 调用次数　　　{_n('calls')}",
            f"🎯 指令执行数　　{_n('cmds')}",
            f"👤 用户数量　　　{_n('users')}",
            f"⚠️ 错误次数　　　{_n('errs')}",
            # ★★ 1.9.0：和后台「数据统计」同款 —— 补上接口错误次数
            f"🛠️ 接口错误次数　{_n('api_errs')}",
            "",
            f"👥 目前加入群数　{_n('live_count')}",
            # ★★ 1.9.0：改「累计人数」口径（后台 1.8.2 已改）
            f"➕ 加群累计人数　{_n('join')}",
            f"🚪 退群累计人数　{_n('leave')}",
            f"🚫 黑名单人数　　{_n('blacklist_count')}",
            f"⏱️ 电脑运行时长　{st.get('uptime_text') or '—'}",
            "",
            # ★★ 1.9.0：接口统计（和后台三个卡片一致）
            f"🔌 接口总数　　　{_n('api_total')}　{_sub}",
            f"✅ 正常接口数　　{_n('api_ok') if _tested else '—'}",
            f"❌ 错误接口数　　{_n('api_bad') if _tested else '—'}",
            "",
            f"✅ 未到期群数　　{_n('auth_valid')}",
            f"⛔ 到期群数　　　{_n('auth_expired')}",
        ]
        return lines

    def _build_stats_menu(self, event=None, group_id: str = "") -> str:
        """运行数据文本（上架版固定文本输出）"""
        try:
            lines = self._stats_lines()
        except Exception:
            logger.exception("[apix-meme] 构建运行数据失败")
            lines = ["📊 运行数据", "", "⚠️ 统计读取失败，请稍后再试"]
        lines.append("")
        lines.append("💡 本群指令，只读展示，不涉及任何修改")
        return "\n".join(lines)

    def _is_checkin_bank_menu(self, text: str) -> bool:
        return self._menu_entry_hit("checkin_bank", text)

    def _is_checkin_bank_save_menu(self, text: str) -> bool:
        return self._menu_entry_hit("checkin_bank_save", text)

    def _is_checkin_bank_loan_menu(self, text: str) -> bool:
        return self._menu_entry_hit("checkin_bank_loan", text)

    def _is_checkin_bank_fund_menu(self, text: str) -> bool:
        """★ 入口名「理财功能」—— 输出**理财相关指令**。
        ★ 兼容旧名「银行理财」。"""
        return self._menu_entry_hit("checkin_bank_fund", text)

    def _is_checkin_bank_rob_menu(self, text: str) -> bool:
        """★ 入口名默认「抢银行帮助」—— 避开 `抢银行` 这条**指令**。"""
        return self._menu_entry_hit("checkin_bank_rob", text)

    def _is_pet_care_menu(self, text: str) -> bool:
        return self._menu_entry_hit("pet_care", text)

    def _is_pet_fight_menu(self, text: str) -> bool:
        return self._menu_entry_hit("pet_fight", text)

    def _is_pet_item_menu(self, text: str) -> bool:
        return self._menu_entry_hit("pet_item", text)

    def _is_checkin_lottery_menu(self, text: str) -> bool:
        return self._menu_entry_hit("checkin_lottery", text)

    def _is_checkin_steal_menu(self, text: str) -> bool:
        return self._menu_entry_hit("checkin_steal", text)

    def _is_checkin_shop_help_menu(self, text: str) -> bool:
        """
        ★★ 1.7.6：「商店帮助」菜单入口（签到系统一级菜单右侧那个）。

        ★ 和「签到商店」**指令**区分开：
          · `签到商店`（指令）-> 输出**商品列表**（do_shop_list）
          · `商店帮助`（菜单入口）-> 输出**指令说明**（build_checkin_shop_help_menu）
        ★★ 1.7.7：支持页码 —— 「商店帮助 2」看第 2 页。
        """
        return self._menu_entry_page("checkin_shop_help", text) is not None

    def _shop_help_page(self, text: str) -> int:
        """取「商店帮助」的页码（没写就是 1）"""
        v = self._menu_entry_page("checkin_shop_help", text)
        try:
            return max(1, int(str(v).strip() or 1))
        except Exception:
            return 1

    def _menu_entry_page(self, menu_id: str, text: str):
        """
        ★★ 1.7.7：菜单入口 + **可选页码**。

        命中返回**页码字符串**（没写页码返回 ""）；未命中返回 None。
        用于「商店帮助 2」这种要翻页的菜单。
        """
        s = str(text or "").strip()
        for n in self._menu_entry_names(menu_id):
            n = str(n or "").strip()
            if not n:
                continue
            if s == n or s == f"/{n}":
                return ""
            if s.startswith(n):
                _rest = s[len(n):].strip()
                if _rest.isdigit():
                    return _rest
        return None

    def _mount_menu_names(self) -> list:
        """
        坐骑**指令菜单**的触发词（后台 `mount_menu_names` 可自定义）。

        ★★ 用户要求：发 `坐骑功能` -> 输出「坐骑相关指令菜单」。
        ★ 注意它不能同时被 `mount_trigger_names`（查看已拥有坐骑）占用，
          否则列表匹配会把菜单吃掉（分发顺序上列表更靠前）。
        """
        try:
            raw = str(getattr(self, "mount_menu_names_raw", "") or "").strip()
            if raw:
                out = [x.strip() for x in raw.split(",") if x.strip()]
                if out:
                    return out
        except Exception:
            pass
        return ["坐骑功能", "坐骑帮助"]

    def _is_checkin_mount_menu(self, text: str) -> bool:
        """坐骑**指令菜单**入口：`坐骑功能` / `坐骑帮助`"""
        s = str(text or "").strip().lstrip("/").strip()
        return s in [str(n).strip() for n in self._mount_menu_names()
                     if str(n).strip()]

    def _is_mount_manage_menu(self, text: str) -> bool:
        """★ 1.7.7：坐骑下级菜单「坐骑管理」"""
        return self._menu_entry_hit("mount_manage", text)

    def _is_mount_admin_menu(self, text: str) -> bool:
        """★ 1.7.7：坐骑下级菜单「坐骑设置」"""
        return self._menu_entry_hit("mount_admin", text)

    def _is_checkin_job_menu(self, text: str) -> bool:
        return self._menu_entry_hit("checkin_job", text)

    def _is_speak_menu(self, text: str) -> bool:
        return text in ("发言功能", "/发言功能")

    def _is_pet_menu(self, text: str) -> bool:
        return self._menu_entry_hit("pet", text)

    def _is_guess_menu(self, text: str) -> bool:
        """★ 1.7.7：娱乐功能下级菜单「猜数功能」"""
        return self._menu_entry_hit("guess", text)

    def _is_music_menu(self, text: str) -> bool:
        """
        ★★ 1.5.1：音乐功能菜单入口（默认名「音乐功能」，后台 `menu_music_names` 可改）。

        ★ 两处都认：
          · 音乐模块自己的入口名表（后台 `menu_music_names` /
            `cmd_music_menu_extra_names` 都进这里）
          · main 的菜单入口表（MENU_ENTRY_DEFS["music"]）
        这样无论哪条配置先被改，发出去都能打开菜单。
        """
        if self.music_module is not None:
            try:
                if self.music_module.match_menu(text):
                    return True
            except Exception:
                logger.debug("[apix-meme] 音乐菜单入口匹配失败", exc_info=True)
        try:
            if self._menu_entry_hit("music", text):
                return True
        except Exception:
            pass
        return str(text or "").strip() in ("音乐功能", "/音乐功能")

    def _is_meme_admin_menu(self, text: str) -> bool:
        return text in ("接口管理", "/接口管理", "表情管理", "/表情管理")

    def _is_word_admin_menu(self, text: str) -> bool:
        return text in ("词库管理", "/词库管理")

    def _is_checkin_admin_menu(self, text: str) -> bool:
        return text in ("签到管理", "/签到管理")

    def _is_status_admin_menu(self, text: str) -> bool:
        return text in ("状态管理", "/状态管理")

    def _is_switch_admin_menu(self, text: str) -> bool:
        return text in ("开关管理", "/开关管理")

    def _is_chime_admin_menu(self, text: str) -> bool:
        return text in ("整点管理", "/整点管理")

    def _is_must_at_admin_menu(self, text: str) -> bool:
        return text in ("艾特设置", "/艾特设置")

    def _is_super_admin_menu(self, text: str) -> bool:
        return text in ("超管设置", "/超管设置")

    def _is_list_menu(self, text: str):
        candidates = [self.cmd_list_name, f"/{self.cmd_list_name}"]
        for cmd in candidates:
            if text == cmd:
                return 1
            if text.startswith(cmd + " "):
                arg = text[len(cmd) + 1:].strip()
                if arg.isdigit():
                    return int(arg)
        return None

    def _is_minecraft_cmd(self, text: str):
        n1 = self.cmd_minecraft_name
        n2 = f"/{n1}"
        if text == n1 or text == n2:
            return (False,)
        _carg = cmd_arg(text, (n1, n2))
        if _carg is not None:
                arg = _carg.lower()
                if arg in ("刷新", "更新", "refresh", "reload"):
                    return (True,)
                return (False,)
        return None

    def _is_mc_auto_cmd(self, text: str) -> bool:
        n1 = self.cmd_mc_auto_name
        return text in (n1, f"/{n1}")

    def _is_mc_test_cmd(self, text: str) -> bool:
        n1 = self.cmd_mc_test_name
        return text in (n1, f"/{n1}")

    def _switch_variants(self, raw: str) -> set:
        """★★ 1.5.0：总开关的等价写法（后缀式，兼容去空格）"""
        name = str(raw or "").strip()
        if not name:
            return set()
        return {name, f"/{name}", name.replace(" ", ""),
                f"/{name.replace(' ', '')}"}

    def _is_enable_cmd(self, text: str) -> bool:
        t = str(text or "").strip()
        if t in self._switch_variants(self.cmd_enable_name):
            return True
        return t in ("开启自定义娱乐", "自定义娱乐开启", "娱乐系统开")

    def _is_disable_cmd(self, text: str) -> bool:
        t = str(text or "").strip()
        if t in self._switch_variants(self.cmd_disable_name):
            return True
        return t in ("关闭自定义娱乐", "自定义娱乐关闭", "娱乐系统关")

    def _is_view_admin_cmd(self, text: str) -> bool:
        return self._name_list_hit(text, "cmd_view_admin_name")

    def _is_refresh_admin_cmd(self, text: str) -> bool:
        return self._name_list_hit(text, "cmd_refresh_admin_name")

    def _is_admin_list_cmd(self, text: str) -> bool:
        return self._name_list_hit(text, "cmd_admin_list_name")

    def _is_word_add(self, text: str):
        return self._extract_arg(text, self.cmd_word_add_name)

    def _is_word_del(self, text: str):
        return self._extract_arg(text, self.cmd_word_del_name)

    def _is_add_admin(self, text: str):
        return self._extract_arg(text, self.cmd_add_admin_name)

    def _is_del_admin(self, text: str):
        return self._extract_arg(text, self.cmd_del_admin_name)

    def _is_word_list(self, text: str) -> bool:
        return self._name_list_hit(text, "cmd_word_list_name")

    def _is_chime_on_cmd(self, text: str):
        n = self.cmd_chime_on_name
        if text == n or text == f"/{n}":
            return ("self",)
        _carg = cmd_arg(text, (n,))
        if _carg is not None:
                arg = _carg
                return ("target", arg) if arg else ("self",)
        return None

    def _is_chime_off_cmd(self, text: str):
        n = self.cmd_chime_off_name
        if text == n or text == f"/{n}":
            return ("self",)
        _carg = cmd_arg(text, (n,))
        if _carg is not None:
                arg = _carg
                return ("target", arg) if arg else ("self",)
        return None

    def _is_chime_status_cmd(self, text: str):
        n = self.cmd_chime_status_name
        if text == n or text == f"/{n}":
            return ("self",)
        _carg = cmd_arg(text, (n,))
        if _carg is not None:
                arg = _carg
                return ("target", arg) if arg else ("self",)
        return None

    def _is_chime_hours_cmd(self, text: str):
        n = self.cmd_chime_hours_name
        if text == n or text == f"/{n}":
            return ("query",)
        _carg = cmd_arg(text, (n,))
        if _carg is not None:
                arg = _carg
                return ("set", arg) if arg else ("query",)
        return None

    def _is_chime_text_cmd(self, text: str):
        n = self.cmd_chime_text_name
        if text == n or text == f"/{n}":
            return ("query",)
        _carg = cmd_arg(text, (n,))
        if _carg is not None:
                arg = _carg
                return ("set", arg) if arg else ("query",)
        return None

    def _is_chime_list_cmd(self, text: str) -> bool:
        n = self.cmd_chime_list_name
        return text in (n, f"/{n}")

    @staticmethod
    def _reshape_time_args(arg: str):
        """
        解析「时间」类参数，兼容两种顺序：
          状态推送间隔 60 123456   -> (123456, "60")
          状态推送间隔 123456 60   -> (123456, "60")
          状态推送间隔 60             -> ("", "60")
        返回的时间统一规范化为 HH:MM；无法识别为时间时返回空串，
        由调用方给出用法提示。若两个 token 都不是时间，则把第一个当群号、
        第二个当非法时间输入，以便提示用户格式错误。
        """
        from .core.status_push import parse_hhmm
        parts = str(arg or "").split()
        if len(parts) >= 2:
            first, second = parse_hhmm(parts[0]), parse_hhmm(parts[1])
            if first:
                return parts[1], first
            if second:
                return parts[0], second
            return parts[0], ""
        if parts:
            only = parse_hhmm(parts[0])
            if only:
                return "", only
            return "", ""
        return "", ""

    def _is_daily_push_on_cmd(self, text: str):
        return self._extract_arg(text, self.cmd_daily_push_on_name)

    def _is_daily_push_off_cmd(self, text: str):
        return self._extract_arg(text, self.cmd_daily_push_off_name)

    def _is_daily_push_time_cmd(self, text: str):
        n = self.cmd_daily_push_time_name
        if text == n or text == f"/{n}":
            return ("self",)
        _carg = cmd_arg(text, (n,))
        if _carg is not None:
                arg = _carg
                if not arg:
                    return ("self",)
                gid, hhmm = self._reshape_time_args(arg)
                return ("set", gid, hhmm)
        return None

    def _is_daily_push_list_cmd(self, text: str) -> bool:
        n = self.cmd_daily_push_list_name
        return text in (n, f"/{n}")

    def _is_kick_cmd(self, text: str):
        n = self.cmd_kick_name
        if text == n or text == f"/{n}":
            return ""
        _carg = cmd_arg(text, (n,))
        if _carg is not None:
                return _carg
        return None

    def _is_mute_cmd(self, text: str):
        n = self.cmd_mute_name
        if text == n or text == f"/{n}":
            return ""
        _carg = cmd_arg(text, (n,))
        if _carg is not None:
                return _carg
        return None

    def _is_unmute_cmd(self, text: str):
        n = self.cmd_unmute_name
        if text == n or text == f"/{n}":
            return ""
        _carg = cmd_arg(text, (n,))
        if _carg is not None:
                return _carg
        return None

    def _is_blacklist_cmd(self, text: str):
        """拉黑成员 QQ号 [kick|mute|ignore]"""
        n = self.cmd_blacklist_name
        if text == n or text == f"/{n}":
            return ""
        _carg = cmd_arg(text, (n,))
        if _carg is not None:
                return _carg
        return None

    def _is_unblacklist_cmd(self, text: str):
        n = self.cmd_unblacklist_name
        if text == n or text == f"/{n}":
            return ""
        _carg = cmd_arg(text, (n,))
        if _carg is not None:
                return _carg
        return None

    def _is_blacklist_list_cmd(self, text: str) -> bool:
        n = self.cmd_blacklist_list_name
        return text in (n, f"/{n}")

    def _name_list(self, attr: str) -> list:
        """
        ★★ 1.7：把一个「指令名属性」展开成**全部候选名**。

        配置里可以写多个别名（英文逗号分隔），例如
            cmd_chime_on = "开启功能 报时功能,开启报时功能"
        这里统一拆好，匹配函数只要把返回的列表丢给 `cmd_arg()` 即可。

        返回按**长度倒序**：长名优先匹配，避免「开启功能 报时功能」
        被更短的别名截断。
        """
        raw = getattr(self, attr, "") or ""
        names = self._split_names(raw)
        out = []
        for x in names:
            x = str(x).strip()
            if not x:
                continue
            out.append(x)
            if not x.startswith("/"):
                out.append("/" + x)
        return sorted(set(out), key=len, reverse=True)

    def _name_list_hit(self, text: str, attr: str) -> bool:
        """`text` 是否**精确**等于该属性的任一候选名（含 / 前缀）"""
        return str(text or "").strip() in self._name_list(attr)

    def _is_daily_news_manual_on_cmd(self, text: str):
        """手动读报开关：`开启功能 读报功能`（★ 1.2）"""
        return self._name_list_hit(text, "cmd_daily_news_manual_on_name")

    def _is_daily_news_manual_off_cmd(self, text: str):
        """手动读报开关：`关闭功能 读报功能`（★ 1.2）"""
        return self._name_list_hit(text, "cmd_daily_news_manual_off_name")

    def _is_daily_news_cmd(self, text: str) -> bool:
        """手动读报指令，支持逗号分隔多个。"""
        for n in self._split_names(self.cmd_daily_news_name):
            if text == n or text == f"/{n}":
                return True
        return False

    def _is_daily_news_on_cmd(self, text: str):
        for n in self._split_names(self.cmd_daily_news_on_name):
            r = self._extract_arg(text, n)
            if r is not None:
                return r
            if text in (n, f"/{n}"):
                return ""
        return None

    def _is_daily_news_off_cmd(self, text: str):
        for n in self._split_names(self.cmd_daily_news_off_name):
            r = self._extract_arg(text, n)
            if r is not None:
                return r
            if text in (n, f"/{n}"):
                return ""
        return None

    def _is_daily_news_time_cmd(self, text: str):
        n = self.cmd_daily_news_time_name
        if text == n or text == f"/{n}":
            return ("self",)
        _carg = cmd_arg(text, (n,))
        if _carg is not None:
                arg = _carg
                if not arg:
                    return ("self",)
                gid, hhmm = self._reshape_time_args(arg)
                return ("set", gid, hhmm)
        return None

    def _is_daily_news_list_cmd(self, text: str) -> bool:
        n = self.cmd_daily_news_list_name
        return text in (n, f"/{n}")

    def _is_chime_test_cmd(self, text: str):
        n = self.cmd_chime_test_name
        if text == n or text == f"/{n}":
            return ("self",)
        _carg = cmd_arg(text, (n,))
        if _carg is not None:
                arg = _carg
                return ("target", arg) if arg else ("self",)
        return None

    def _is_must_at_on_cmd(self, text: str):
        n = self.cmd_must_at_bot_on_name
        if text == n or text == f"/{n}":
            return ("self",)
        _carg = cmd_arg(text, (n,))
        if _carg is not None:
                arg = _carg
                return ("target", arg) if arg else ("self",)
        return None

    def _is_must_at_off_cmd(self, text: str):
        n = self.cmd_must_at_bot_off_name
        if text == n or text == f"/{n}":
            return ("self",)
        _carg = cmd_arg(text, (n,))
        if _carg is not None:
                arg = _carg
                return ("target", arg) if arg else ("self",)
        return None

    def _is_must_at_status_cmd(self, text: str):
        n = self.cmd_must_at_bot_status_name
        if text == n or text == f"/{n}":
            return ("self",)
        _carg = cmd_arg(text, (n,))
        if _carg is not None:
                arg = _carg
                return ("target", arg) if arg else ("self",)
        return None

    def _build_entertain_menu(self):
        return self._menu("entertain", lambda: build_entertain_menu(self))

    def _build_qqinfo_menu(self):
        return self._menu("qqinfo", lambda: build_qqinfo_menu(self))

    def _is_global_admin(self, event) -> bool:
        """
        是否 **AstrBot 全局管理员**。

        ★ 与 `_check_admin` 的区别：这里**不认群主 / 群管理员**。
          AstrBot 的 `event.is_admin()` 只对
          `astrbot_config["admins_id"]` 里的人返回 True
          （见 core/pipeline/waking_check/stage.py）。
        """
        try:
            return bool(event.is_admin())
        except Exception:
            return False

    def _can_adjust_score(self, event) -> bool:
        """
        能不能用「加积分 / 减积分」。

        只认两种（按用户要求，群主/群管**不能用**）：
          ① AstrBot 全局管理员
          ② 后台「加减积分 QQ 白名单」里列出的 QQ
        """
        try:
            who = to_str(event.get_sender_id())
        except Exception:
            who = ""
        if who and who in (getattr(self, "score_admin_whitelist", None) or []):
            return True
        return self._is_global_admin(event)

    async def terminate(self) -> None:
        """
        插件被禁用 / 重载时由 AstrBot 调用。

        ★ 必须取消所有后台循环，否则它们会变成「孤儿任务」：
          旧循环继续跑，用的还是**重载前的代码**，
          于是会发出用旧逻辑生成的内容（例如未 await 的协程对象）。

        取消顺序：
          ① 模块级登记表 `_BG_TASKS`（最可靠 —— 重载后新实例也能取消旧任务）
          ② 兼容兜底：实例属性上的任务对象
        """
        try:
            _save_msg_stats(force=True)
            logger.info(f"[apix-meme] 收发计数已保存："
                        f"收 {GLOBAL_STATS.get('recv')} / "
                        f"发 {GLOBAL_STATS.get('send')}")
        except Exception:
            logger.debug("[apix-meme] 保存收发计数失败", exc_info=True)

        try:
            from .core.background import _BG_TASKS as _reg
        except Exception:
            try:
                from core.background import _BG_TASKS as _reg
            except Exception:
                _reg = {}
        for name in list(_reg.keys()):
            tk = _reg.get(name)
            if tk is None:
                continue
            try:
                if not tk.done():
                    tk.cancel()
                    logger.info(f"[apix-meme] 已取消后台任务：{name}")
            except Exception:
                logger.debug(f"[apix-meme] 取消 {name} 失败", exc_info=True)
            _reg.pop(name, None)

        tasks = [
            ("状态推送", getattr(self, "_status_push_task", None)),
            ("整点报时", getattr(self, "_chime_checker_task", None)),
            ("MC 检测", getattr(self, "_mc_checker_task", None)),
            ("每日读报", getattr(self, "_daily_news_task", None)),
        ]
        for name, tk in tasks:
            if tk is None:
                continue
            try:
                if not tk.done():
                    tk.cancel()
                    logger.info(f"[apix-meme] 已取消后台任务：{name}")
            except Exception:
                logger.debug(f"[apix-meme] 取消 {name} 任务失败", exc_info=True)
        for flag in ("_status_push_started", "_chime_checker_started",
                     "_mc_checker_started", "_daily_news_started"):
            try:
                setattr(self, flag, False)
            except Exception:
                pass
        pend = [tk for _n, tk in tasks if tk is not None and not tk.done()]
        if pend:
            try:
                await asyncio.gather(*pend, return_exceptions=True)
            except Exception:
                pass
        logger.info("[apix-meme] 插件卸载")

    def _score_sn(self) -> str:
        """积分显示名（后台可自定义）"""
        mod = getattr(self, "checkin_module", None)
        nm = str(getattr(mod, "score_name", "") or "").strip() if mod else ""
        return nm or str(getattr(self, "score_name", "") or "积分").strip() or "积分"

    def _is_weather_menu(self, text: str) -> bool:
        """是否命中「天气查询」菜单入口指令"""
        s = str(text or "").strip()
        if s.startswith("/"):
            s = s[1:].strip()
        names = self._split_names(
            getattr(self, "cmd_weather_menu_name", "") or "天气查询")
        return s in names or ("/" + s) in names

    def _build_weather_menu(self):
        return self._menu("weather", lambda: build_weather_menu(self))

    def _race_name(self, attr: str, dflt: str) -> str:
        """取赛车某个指令名（读插件属性，改后台立刻生效）"""
        return (self._split_names(getattr(self, attr, "") or [dflt])
                or [dflt])[0]

    def _race_sn(self) -> str:
        """积分显示名（真值在签到模块里）"""
        ck = getattr(self, "checkin_module", None)
        try:
            if ck is not None:
                return ck._sn()
        except Exception:
            pass
        return "积分"

    def _race_cmd_arg(self, text: str, attr: str, dflt: str):
        """
        匹配「指令名 参数」。

        返回 参数原文 / ""（只写了指令）/ None（没命中）。

        ★★ 1.6：空格**可有可无**（和项目其它指令一致）：
          · `装备赛车 2` / `装备赛车2` / `装备赛车:2` 都认
          · `装备赛车疾风 GT` -> 参数 `疾风 GT`
          · 但要避免误吃：`赛车商店` 不会被当成 `赛车` + `商店`
        """
        s = str(text or "").strip()
        if s.startswith("/"):
            s = s[1:].strip()
        if not s:
            return None
        names = self._split_names(getattr(self, attr, "") or [dflt]) or [dflt]
        for nm in sorted([str(x).strip() for x in names if str(x).strip()],
                         key=len, reverse=True):
            if s == nm:
                return ""
            if not s.startswith(nm):
                continue
            rest = s[len(nm):]
            if not rest:
                return ""
            if rest[0] in " \t\u3000:：,，、|-—@":
                rest = rest.lstrip(" \t\u3000:：,，、|-—@").strip()
                if rest:
                    return rest
                return ""
            return rest.strip()
        return None

    def _is_race_menu(self, text: str):
        """
        ★★ 1.6：赛车菜单入口（**支持页码**）。

        `赛车功能` / `赛车功能 2` —— 命中返回页码字符串（""=第 1 页），
        未命中返回 None。
        """
        return self._menu_entry_page("racing", text)

    def _build_racing_menu(self, page=1):
        return self._menu("racing",
                          lambda: build_racing_menu(self, page))

    def _racing_gate(self, group_id):
        """赛车功能统一前置检查。返回错误文案；"" = 放行"""
        if not bool(getattr(self, "racing_enable", True)):
            return "⚠️ 赛车功能已关闭（后台可开启）"
        if not _HAS_RACING or self.racing_module is None:
            return "⚠️ 赛车模块未加载（core/racing.py）"
        if not group_allowed(group_id, getattr(self, "racing_groups", None)):
            return ""
        return ""

    def _racing_ok(self, group_id) -> bool:
        """该群是否允许用赛车功能"""
        if not bool(getattr(self, "racing_enable", True)):
            return False
        if not _HAS_RACING or self.racing_module is None:
            return False
        return bool(group_allowed(group_id,
                                  getattr(self, "racing_groups", None)))

    def _race_lucky_text(self, group_id, uid, user_name: str) -> str:
        """
        ★★ 1.6：赛车抽奖**说明页**（不带参数发指令时看这个）。
        """
        rm = self.racing_module
        sn = self._race_sn()
        cmd = self._race_name("cmd_race_lucky", "赛车抽奖")
        cmd_all = self._race_name("cmd_race_lucky_all", "赛车一键抽奖")
        sn_shop = self._race_name("cmd_race_shop", "赛车商店")
        fee = max(0, int(getattr(rm, "lucky_fee", 0) or 0))
        _lim = max(0, int(getattr(rm, "lucky_daily_limit", 0) or 0))
        _left = rm.lucky_left_today(group_id, uid)
        _lc = rm.lucky_left_cars(group_id, uid)
        rows = [title_bar("赛车抽奖"),
                f"🎰 花 **{fmt_num(fee)}** {sn} 抽一次",
                ""]
        rows.append(f"📅 每天可抽：**{_lim}** 次" if _lim > 0
                    else "📅 每天可抽：**不限**")
        if _lim > 0:
            rows.append(f"　 今天还剩：**{_left}** 次")
        rows.append("")
        rows += ["── 奖品 ──",
                 "· **限定赛车**（商店买不到的超稀有）",
                 f"　 还剩 **{len(_lc)}** 台没抽到"]
        if _lc:
            rows.append("　" + "、".join(str(x) for x in _lc))
        else:
            rows.append("　 🎉 你已经**全部收集**了！")
        rows += ["· 积分（奖池里的其它奖品）",
                 "",
                 "★ **抽到过的车不会再抽中同一台**",
                 "　 抽完的限定车会从奖池里移除",
                 "　 剩下的车抽光后只出积分",
                 "",
                 f"💡 单抽：「{cmd} 抽」",
                 f"💡 一键抽完剩余次数：「{cmd_all}」",
                 f"💡 看说明：「{cmd}」",
                 f"💡 买车：「{sn_shop}」",
                 title_foot()]
        return "\n".join(rows)

    RACE_SHOP_PER_PAGE = 5

    def _race_shop_text(self, page=1) -> str:
        """赛车商店（**分页**，每页 5 辆）。页码用 `赛车商店 2`。"""
        rm = self.racing_module
        sn = self._race_sn()
        cmd_shop = self._race_name("cmd_race_shop", "赛车商店")
        shop = list(getattr(rm, "shop", []) or [])
        per = max(1, int(self.RACE_SHOP_PER_PAGE or 5))
        total = max(1, (len(shop) + per - 1) // per)
        p = max(1, min(int(page or 1), total))
        rows = [title_bar("赛车商店"),
                f"💳 用积分购买，可买**多辆**车"]
        if not shop:
            rows += ["", "⚠️ 商店里还没有车（后台可添加）"]
            rows.append(title_foot())
            return "\n".join(rows)
        _lo = (p - 1) * per
        _hi = min(len(shop), _lo + per)
        rows += [f"📄 第 {p}/{total} 页　"
                 f"（第 {_lo + 1}~{_hi} 辆 / 共 {len(shop)} 辆）", ""]
        for i in range(_lo, _hi):
            c = shop[i]
            rows.append(f"{i + 1}. **{c.get('name')}**　"
                        f"{c.get('price', 0)} {sn}")
            _desc = str(c.get("desc") or "").strip()
            if _desc and _desc != "—":
                rows.append(f"　 {_desc}")
            _at = "　".join(f"{_RACE_ATTR_LABEL.get(k, k)}"
                            f"{int(c.get(k, 50) or 50)}"
                            for k in _RACE_ATTR_KEYS)
            rows.append(f"　 {_at}")
            if i != _hi - 1:
                rows.append("")
        if p >= total:
            lot = list(getattr(rm, "lottery_cars", []) or [])
            if lot:
                rows += ["", "★ 以下为**抽奖限定**，商店买不到：",
                         "　" + "、".join(str(c.get("name")) for c in lot),
                         "💡 去抽奖碰碰运气"]
        if total > 1:
            _nav = []
            if p > 1:
                _nav.append(f"⬅️ 上一页：「{cmd_shop} {p - 1}」")
            if p < total:
                _nav.append(f"➡️ 下一页：「{cmd_shop} {p + 1}」")
            if _nav:
                rows += ["", *_nav]
        _cmd_buy = self._race_name("cmd_race_buy", "购买赛车")
        rows += ["", f"💡 买车：「{_cmd_buy} 车名」或「{_cmd_buy} 序号」",
                 f"　 序号是上表里的**全局编号**",
                 f"💡 看属性用「"
                 f"{self._race_name('cmd_race_view', '赛车查看')} 车名/序号」",
                 title_foot()]
        return "\n".join(rows)

    def _race_garage_text(self, group_id, uid, user_name: str) -> str:
        rm = self.racing_module
        cars = rm.owned_cars(group_id, uid)
        eq = rm.equipped_car(group_id, uid)
        u = rm.get_user(group_id, uid)
        _eqname = str(eq.get("name")) if eq else ""
        rows = [title_bar("我的赛车"),
                f"👤 {user_name or u.get('name') or f'QQ{uid}'}"]
        if not cars:
            rows += ["🏎️ 还没有赛车",
                     "",
                     f"💡 去「{self._race_name('cmd_race_shop', '赛车商店')}」"
                     f"买一辆"]
            rows.append(title_foot())
            return "\n".join(rows)
        rows.append(f"🏎️ 拥有 **{len(cars)}** 辆")
        if _eqname:
            rows.append(f"✅ 当前装备：**{_eqname}**")
        rows += ["",
                 "── 车库 ──"]
        for i, c in enumerate(cars, 1):
            _nm = str(c.get("name"))
            _mark = "✅" if _nm == _eqname else "　"
            rows.append(f"{_mark} {i}. {_nm}")
        rows += ["",
                 f"📅 今日场次：{int(u.get('today', 0) or 0)}/"
                 f"{rm.daily_limit if rm.daily_limit > 0 else '∞'}",
                 f"🏁 总场次：{int(u.get('races', 0) or 0)}　"
                 f"🥇 冠军：{int(u.get('wins', 0) or 0)}　"
                 f"🏅 前三：{int(u.get('top3', 0) or 0)}",
                 "",
                 f"💡 看属性：「"
                 f"{self._race_name('cmd_race_view', '赛车查看')} 车名/序号」",
                 f"💡 换车：「"
                 f"{self._race_name('cmd_race_equip', '装备赛车')}"
                 f" 车名/序号」",
                 f"💡 参赛：「{self._race_name('cmd_race_join', '参与比赛')}」",
                 title_foot()]
        return "\n".join(rows)

    def _race_resolve_car(self, group_id, uid, raw):
        """
        ★★ 1.6：把用户输入（**车名 或 序号**）解析成一辆车。

        查找顺序（和用户直觉一致）：
          ① 自己车库里的序号 / 车名（车库列表里的编号）
          ② 商店里的序号 / 车名
          ③ 任意已知车（抽奖限定车）按名字

        返回 (car, err)
        """
        rm = self.racing_module
        t = str(raw or "").strip()
        if not t:
            return None, "请写车名或序号"
        owned = rm.owned_cars(group_id, uid)
        if owned:
            car, err = rm.pick_car(owned, t)
            if car is not None:
                return car, ""
            _owned_err = err
        else:
            _owned_err = ""
        shop = list(getattr(rm, "shop", []) or [])
        if shop:
            car, err = rm.pick_car(shop, t)
            if car is not None:
                return car, ""
            _shop_err = err
        else:
            _shop_err = ""
        car = rm.find_car(t)
        if car is not None:
            return car, ""
        if rm._is_index(t):
            return None, (f"序号超出范围（车库 {len(owned)} 辆、"
                          f"商店 {len(shop)} 辆）")
        return None, f"没有找到「{t}」"

    def _race_view_text(self, group_id, uid, car_name: str) -> str:
        rm = self.racing_module
        car, _err = self._race_resolve_car(group_id, uid, car_name)
        if car is None:
            return "\n".join([
                title_bar("赛车查看"),
                f"❌ {_err}",
                "",
                "💡 写**车名**或**序号**都行，例："
                f"「{self._race_name('cmd_race_view', '赛车查看')} 2」",
                "💡 看列表："
                f"「{self._race_name('cmd_race_shop', '赛车商店')}」/"
                f"「{self._race_name('cmd_race_my', '我的赛车')}」",
                title_foot()])
        others = sorted(list(getattr(rm, "shop", []) or []),
                        key=lambda x: -float(x.get("speed", 0) or 0))[:4]
        others = [c for c in others if c.get("name") != car.get("name")]
        try:
            chance = rm.win_chance_of(car, others)
        except Exception:
            chance = 0.0
        owned = any(str(c.get("name")) == str(car.get("name"))
                    for c in rm.owned_cars(group_id, uid))
        rows = [title_bar("赛车查看"),
                f"🏎️ **{car.get('name')}**"
                + ("　（已拥有）" if owned else "")]
        _desc = str(car.get("desc") or "").strip()
        if _desc and _desc != "—":
            rows.append(f"　 {_desc}")
        rows.append("")
        rows += rm.car_stat_lines(car)
        _power = round(sum(float(car.get(k, 50) or 50) * w
                           for k, w in _RACE_WEIGHT.items()), 1)
        rows += ["",
                 f"📊 综合实力：**{_power}**",
                 f"🎯 对商店最强 4 辆的估算胜率：**{chance}%**",
                 "　 （估算值，实际比赛有随机波动）",
                 "",
                 "💡 稳定越低波动越大 —— 冷门也可能夺冠",
                 title_foot()]
        return "\n".join(rows)

    async def _race_do_draws(self, event, group_id, uid, user_name,
                             times: int = 1):
        """
        ★★ 1.6：赛车抽奖的**统一执行器**。

        times = 1  -> 单抽（`赛车抽奖 抽`）
        times = 0  -> 把**今天剩余次数一次抽完**（`赛车一键抽奖`）

        会一直抽到：次数用完 / 积分不够 / 奖品抽光。
        每次抽奖独立扣费。
        """
        rm = self.racing_module
        sn = self._race_sn()
        cmd = self._race_name("cmd_race_lucky", "赛车抽奖")
        cmd_all = self._race_name("cmd_race_lucky_all", "赛车一键抽奖")

        ck = getattr(self, "checkin_module", None)
        if ck is None:
            async for _r in self._send_content(event, "⚠️ 签到模块未加载"):
                yield _r
            return

        _fee = max(0, int(getattr(rm, "lucky_fee", 0) or 0))
        _lim = max(0, int(getattr(rm, "lucky_daily_limit", 0) or 0))
        _left = rm.lucky_left_today(group_id, uid)
        if _left <= 0:
            async for _r in self._send_content(
                    event,
                    f"⚠️ 今天的抽奖次数用完了\n"
                    f"📅 每天 **{_lim}** 次，明天再来\n"
                    f"💡 看说明：「{cmd}」"):
                yield _r
            return

        _plan = _left if times <= 0 else min(int(times), _left)
        _unlimited = (_lim <= 0)
        if _plan <= 0:
            _plan = 1

        _results = []
        _spent = 0
        _stop = ""
        for _i in range(_plan):
            if not rm.lucky_prizes(group_id, uid):
                _stop = rm.lucky_draw(group_id, uid)[1] or "奖池空了"
                break
            if _fee > 0:
                _pay = ck.pay(
                    group_id, uid, _fee,
                    allow_bank=bool(getattr(ck, "use_bank_pay", True)))
                _ok = _pay[0] if isinstance(_pay, tuple) else bool(_pay)
                if not _ok:
                    _stop = (f"积分不够（还差一点，单抽要 "
                             f"**{fmt_num(_fee)}** {sn}）")
                    break
                _spent += _fee
            kind, prize = rm.lucky_draw(group_id, uid)
            if kind is None:
                if _fee > 0:
                    try:
                        ck.add_score(group_id, uid, _fee)
                        _spent -= _fee
                    except Exception:
                        logger.debug("[apix-meme] 抽奖退款失败",
                                     exc_info=True)
                _stop = str(prize)
                break
            _u = rm.touch_today(group_id, uid)
            _u["lucky_today"] = int(_u.get("lucky_today", 0) or 0) + 1
            _results.append((kind, prize))
            if kind == "car":
                rm.add_car(group_id, uid, str(prize.get("name")))
            else:
                try:
                    ck.add_score(group_id, uid,
                                 max(0, int(prize.get("amount", 0) or 0)))
                except Exception:
                    logger.debug("[apix-meme] 发积分奖失败", exc_info=True)
            if _unlimited and len(_results) >= 200:
                _stop = "单次最多抽 200 次"
                break

        _left2 = rm.lucky_left_today(group_id, uid)
        _rest_cars = rm.lucky_left_cars(group_id, uid)
        _n_car = sum(1 for k, _ in _results if k == "car")
        _n_score = len(_results) - _n_car
        _sum_score = sum(max(0, int(p.get("amount", 0) or 0))
                         for k, p in _results if k == "score")

        if len(_results) > 1:
            rows = [title_bar("赛车一键抽奖"),
                    f"🎰 共抽 **{len(_results)}** 次",
                    ""]
            _cars = [str(p.get("name")) for k, p in _results if k == "car"]
            if _cars:
                rows += ["🏎️🎉 **抽到赛车**："]
                for _nm in _cars:
                    rows.append(f"　 🎁 **{_nm}**")
                rows.append("")
            if _n_score:
                rows += [f"💰 积分奖 **{_n_score}** 次，"
                         f"合计 **+{fmt_num(_sum_score)}** {sn}", ""]
            rows += [f"💸 总花费：-{fmt_num(_spent)} {sn}",
                     (f"📅 今天还能抽 {_left2} 次" if _lim > 0
                      else "📅 今天不限次数"),
                     f"🌟 限定车还剩 **{len(_rest_cars)}** 台"
                     if _rest_cars else "🎉 限定车**全部收集**完成！"]
            if _stop:
                rows.append(f"⏹️ 已停止：{_stop}")
            rows += ["",
                     f"💡 看车库：「"
                     f"{self._race_name('cmd_race_my', '我的赛车')}」",
                     title_foot()]
            async for _r in self._send_content(event, "\n".join(rows)):
                yield _r
            return

        if not _results:
            _msg = _stop or "没抽到东西"
            if "不够" in _msg:
                _msg = (f"❌ {_msg}\n💡 先去签到/打工赚钱")
            else:
                _msg = f"⚠️ {_msg}\n💡 看说明：「{cmd}」"
            async for _r in self._send_content(event, _msg):
                yield _r
            return

        kind, prize = _results[0]
        if kind == "car":
            _nm = str(prize.get("name"))
            _new = str(_nm) in [str(x) for x in
                                rm.get_user(group_id, uid).get("cars") or []]
            rows = [title_bar("赛车抽奖"),
                    "🏎️🎉 **中大奖！抽到赛车**",
                    "",
                    f"🎁 **{_nm}**　（新入库）",
                    ""]
            rows += rm.car_stat_lines(prize)
            rows += ["",
                     f"💸 花费：-{fmt_num(_spent or _fee)} {sn}",
                     (f"📅 今天还能抽 {_left2} 次" if _lim > 0
                      else "📅 今天不限次数"),
                     "",
                     (f"🌟 限定车还剩 **{len(_rest_cars)}** 台没抽到"
                      if _rest_cars else "🎉 限定车**全部收集**完成！"),
                     f"💡 看车库：「"
                     f"{self._race_name('cmd_race_my', '我的赛车')}」"]
            if _stop:
                rows.append(f"⏹️ {_stop}")
            if _lim != 1:
                rows.append(f"💡 一键抽完：「{cmd_all}」")
            rows.append(title_foot())
        else:
            _amt = max(0, int(prize.get("amount", 0) or 0))
            rows = [title_bar("赛车抽奖"),
                    "🎰 抽奖结果",
                    "",
                    f"🎁 获得 **+{fmt_num(_amt)}** {sn}",
                    "",
                    f"💸 花费：-{fmt_num(_spent or _fee)} {sn}",
                    (f"📅 今天还能抽 {_left2} 次" if _lim > 0
                     else "📅 今天不限次数"),
                    ""]
            if _stop:
                rows.append(f"⏹️ {_stop}")
            rows += [f"💡 再抽一次：「{cmd} 抽」",
                     f"💡 一键抽完：「{cmd_all}」",
                     title_foot()]
        async for _r in self._send_content(event, "\n".join(rows)):
            yield _r

    async def _handle_racing_cmd(self, event, group_id, uid, user_name,
                                 action: str, arg: str):
        """
        处理赛车各指令。

        action: my / equip / view / shop / buy / join / fast
        """
        if not _HAS_RACING or self.racing_module is None:
            async for _r in self._send_content(
                    event, "⚠️ 赛车模块未加载（core/racing.py）"):
                yield _r
            return
        rm = self.racing_module
        sn = self._race_sn()

        if action == "shop":
            want = str(arg or "").strip()
            _cmd_shop = self._race_name("cmd_race_shop", "赛车商店")
            if not want:
                async for _r in self._send_content(
                        event, self._race_shop_text(1)):
                    yield _r
                return
            _shop = list(getattr(rm, "shop", []) or [])
            _per = max(1, int(self.RACE_SHOP_PER_PAGE or 5))
            _pages = max(1, (len(_shop) + _per - 1) // _per)
            if want.isdigit():
                _n = int(want)
                if 1 <= _n <= _pages:
                    async for _r in self._send_content(
                            event, self._race_shop_text(_n)):
                        yield _r
                    return
                async for _r in self._send_content(
                        event, self._race_shop_text(1)):
                    yield _r
                return
            car, _perr = rm.pick_car(_shop, want)
            if car is None:
                async for _r in self._send_content(
                        event,
                        f"❌ {_perr}\n"
                        f"💡 看列表：「{_cmd_shop}」\n"
                        f"💡 翻页：「{_cmd_shop} 页号」（共 {_pages} 页）"):
                    yield _r
                return
            try:
                _idx = [str(c.get("name")) for c in _shop].index(
                    str(car.get("name")))
                _pg = _idx // _per + 1
            except Exception:
                _pg = 1
            async for _r in self._send_content(
                    event, self._race_shop_text(_pg)):
                yield _r
            return

        if action == "buy":
            want = str(arg or "").strip()
            _cmd_buy = self._race_name("cmd_race_buy", "购买赛车")
            _cmd_shop = self._race_name("cmd_race_shop", "赛车商店")
            _shop = list(getattr(rm, "shop", []) or [])
            if not want:
                async for _r in self._send_content(
                        event,
                        f"⚠️ 请写**车名**或**序号**：\n"
                        f"💡 「{_cmd_buy} 保时捷911」\n"
                        f"💡 「{_cmd_buy} 3」\n"
                        f"💡 看列表：「{_cmd_shop}」"):
                    yield _r
                return
            car, _perr = rm.pick_car(_shop, want)
            if car is None:
                async for _r in self._send_content(
                        event,
                        f"❌ {_perr}\n"
                        f"💡 序号按「{_cmd_shop}」列表里的编号\n"
                        f"💡 例：「{_cmd_buy} 3」或「{_cmd_buy} 保时捷911」"):
                    yield _r
                return
            if any(str(c.get("name")) == str(car.get("name"))
                   for c in rm.owned_cars(group_id, uid)):
                async for _r in self._send_content(
                        event, f"⚠️ 你已经有一辆「{car.get('name')}」了"):
                    yield _r
                return
            price = max(0, int(car.get("price", 0) or 0))
            ck = getattr(self, "checkin_module", None)
            if ck is None:
                async for _r in self._send_content(
                        event, "⚠️ 签到模块未加载"):
                    yield _r
                return
            _pay = ck.pay(group_id, uid, price,
                          allow_bank=bool(getattr(ck, "use_bank_pay", True)))
            _ok_pay = _pay[0] if isinstance(_pay, tuple) else bool(_pay)
            if not _ok_pay:
                async for _r in self._send_content(
                        event,
                        f"❌ 积分不够：需要 **{fmt_num(price)}** {sn}\n"
                        f"💡 先去签到/打工赚钱"):
                    yield _r
                return
            rm.add_car(group_id, uid, car.get("name"))
            rows = [title_bar("购买赛车"),
                    f"🎉 成功买下 **{car.get('name')}**",
                    f"💸 花费：-{fmt_num(price)} {sn}",
                    ""]
            rows += rm.car_stat_lines(car)
            rows += ["",
                     f"✅ 已自动装备（用「"
                     f"{self._race_name('cmd_race_equip', '装备赛车')}」可换）",
                     title_foot()]
            async for _r in self._send_content(event, "\n".join(rows)):
                yield _r
            return

        if action == "my":
            async for _r in self._send_content(
                    event, self._race_garage_text(group_id, uid, user_name)):
                yield _r
            return

        if action == "equip":
            want = str(arg or "").strip()
            if not want:
                cars = rm.owned_cars(group_id, uid)
                if not cars:
                    async for _r in self._send_content(
                            event,
                            f"⚠️ 你还没有赛车，先去「"
                            f"{self._race_name('cmd_race_buy', '购买赛车')}」买一辆"):
                        yield _r
                    return
                async for _r in self._send_content(
                        event,
                        "\n".join([title_bar("装备赛车"),
                                   "请写要装备的**车名**或**序号**：",
                                   ""]
                                  + [f"　{i}. {c.get('name')}"
                                     for i, c in enumerate(cars, 1)]
                                  + ["",
                                     f"💡 例：「"
                                     f"{self._race_name('cmd_race_equip', '装备赛车')}"
                                     f" 2」或「… {cars[0].get('name')}」",
                                     title_foot()])):
                    yield _r
                return
            ok, res = rm.equip(group_id, uid, want)
            if not ok:
                async for _r in self._send_content(event, f"⚠️ {res}"):
                    yield _r
                return
            rows = [title_bar("装备赛车"),
                    f"✅ 已装备 **{res.get('name')}**", ""]
            rows += rm.car_stat_lines(res)
            rows += ["", f"💡 参赛：「"
                     f"{self._race_name('cmd_race_join', '参与比赛')}」",
                     title_foot()]
            async for _r in self._send_content(event, "\n".join(rows)):
                yield _r
            return

        if action == "lucky":
            _cmd = self._race_name("cmd_race_lucky", "赛车抽奖")
            _want = str(arg or "").strip()
            if _want not in ("抽", "1", "抽奖", "一次", "go", "GO",
                             "抽一次", "来一次", "抽一发"):
                async for _r in self._send_content(
                        event,
                        self._race_lucky_text(group_id, uid, user_name)):
                    yield _r
                return
            async for _r in self._race_do_draws(event, group_id, uid,
                                                user_name, 1):
                yield _r
            return

        if action == "luckyall":
            async for _r in self._race_do_draws(
                    event, group_id, uid, user_name, 0):
                yield _r
            return

        if action == "view":
            want = str(arg or "").strip()
            if not want:
                eq = rm.equipped_car(group_id, uid)
                if eq is None:
                    async for _r in self._send_content(
                            event,
                            f"⚠️ 请写**车名**或**序号**："
                            f"「{self._race_name('cmd_race_view', '赛车查看')}"
                            f" 2」\n"
                            f"💡 商店里的车："
                            f"「{self._race_name('cmd_race_shop', '赛车商店')}」"):
                        yield _r
                    return
                want = str(eq.get("name"))
            txt = self._race_view_text(group_id, uid, want)
            async for _r in self._send_content(event, txt):
                yield _r
            return

        if action == "join":
            qs = rm.queue_size(group_id)
            if qs >= rm.players:
                async for _r in self._race_run(event, group_id, "normal"):
                    yield _r
                return
            car = rm.equipped_car(group_id, uid)
            if car is None:
                async for _r in self._send_content(
                        event,
                        f"⚠️ 你还没有赛车，先去「"
                        f"{self._race_name('cmd_race_buy', '购买赛车')}」"
                        f"买一辆"):
                    yield _r
                return
            fee = max(0, int(getattr(rm, "entry_fee", 0) or 0))
            ck = getattr(self, "checkin_module", None)
            if fee > 0 and ck is not None:
                _pay = ck.pay(group_id, uid, fee,
                              allow_bank=bool(
                                  getattr(ck, "use_bank_pay", True)))
                _ok_pay = _pay[0] if isinstance(_pay, tuple) else bool(_pay)
                if not _ok_pay:
                    async for _r in self._send_content(
                            event,
                            f"❌ 报名费不够：需要 **{fmt_num(fee)}** {sn}\n"
                            f"💡 报名费**不退**，攒够再来"):
                        yield _r
                    return
            err = rm.join(group_id, uid, user_name, str(car.get("name")))
            if err:
                if fee > 0 and ck is not None:
                    try:
                        ck.add_score(group_id, uid, fee)
                    except Exception:
                        logger.debug("[apix-meme] 退还报名费失败",
                                     exc_info=True)
                async for _r in self._send_content(event, err):
                    yield _r
                return
            n = rm.queue_size(group_id)
            left = rm.left_today(group_id, uid)
            try:
                _bonus, _ = rm.daily_bonus(group_id)
            except Exception:
                _bonus = 0
            rows = [title_bar("参与比赛"),
                    f"✅ {user_name} 已报名（{n}/{rm.players}）",
                    f"🏎️ 座驾：**{car.get('name')}**",
                    f"💸 报名费：-{fmt_num(fee)} {sn}（**不退**，进奖池）",
                    f"💰 今日奖池加成：**{fmt_num(_bonus)}** {sn}"
                    f"（当天固定）",
                    f"📅 今日剩余：{left} 场"]
            if n < rm.players:
                _w = int(getattr(rm, "wait_sec", 3600) or 0)
                _wt = (f"{_w // 60} 分钟" if _w and _w % 60 == 0
                       else f"{_w} 秒") if _w else "不自动补位"
                rows.append("")
                rows.append(f"⏳ 还差 **{rm.players - n}** 人开赛"
                            f"（{_wt}没满就补 NPC 开赛）")
                rows.append(f"💡 拉人来战，或发「"
                            f"{self._race_name('cmd_race_fast', '快速比赛')}"
                            f"」立刻和 NPC 赛")
            rows.append(title_foot())
            async for _r in self._send_content(event, "\n".join(rows)):
                yield _r
            if rm.queue_size(group_id) >= rm.players or rm._queue_expired(
                    group_id):
                async for _r in self._race_run(event, group_id, "normal"):
                    yield _r
            return

        if action == "fast":
            car = rm.equipped_car(group_id, uid)
            if car is None:
                async for _r in self._send_content(
                        event,
                        f"⚠️ 你还没有赛车，先去「"
                        f"{self._race_name('cmd_race_buy', '购买赛车')}」"
                        f"买一辆"):
                    yield _r
                return
            if rm.left_today(group_id, uid) <= 0:
                async for _r in self._send_content(
                        event,
                        f"⚠️ 你今天已经跑满 {rm.daily_limit} 场了，明天再来"):
                    yield _r
                return
            fee = max(0, int(getattr(rm, "entry_fee", 0) or 0))
            ck = getattr(self, "checkin_module", None)
            if fee > 0 and ck is not None:
                _pay = ck.pay(group_id, uid, fee,
                              allow_bank=bool(
                                  getattr(ck, "use_bank_pay", True)))
                _ok_pay = _pay[0] if isinstance(_pay, tuple) else bool(_pay)
                if not _ok_pay:
                    async for _r in self._send_content(
                            event,
                            f"❌ 报名费不够：需要 **{fmt_num(fee)}** {sn}"):
                        yield _r
                    return
            lineup = [{"uid": str(uid), "name": user_name,
                       "car": str(car.get("name")), "car_obj": car,
                       "ts": int(time.time()), "npc": False,
                       "counted": False}]
            lineup += rm.make_npcs(
                max(0, int(getattr(rm, "fast_npc", 5) or 5) - 0),
                exclude_names=[user_name])
            async for _r in self._race_settle_and_send(
                    event, group_id, lineup, pve=True):
                yield _r
            return

    async def _race_run(self, event, group_id, kind: str = "normal"):
        """从队列取人开赛（会补 NPC）"""
        rm = self.racing_module
        if rm is None:
            return
        lineup, npc_n = rm.build_lineup(group_id)
        if not lineup:
            return
        async for _r in self._race_settle_and_send(
                event, group_id, lineup):
            yield _r

    async def _race_settle_and_send(self, event, group_id, lineup: list,
                                    pve: bool = False):
        """
        结算一局 + 发文字 + **真正发奖**。

        ★ 发奖走签到模块（积分的真值在那里）。
        """
        rm = self.racing_module
        ck = getattr(self, "checkin_module", None)
        sn = self._race_sn()
        try:
            res = rm.settle(group_id, lineup)
        except Exception:
            logger.exception("[apix-meme] 赛车结算失败")
            async for _r in self._send_content(event, "❌ 比赛结算失败"):
                yield _r
            return

        ranking = res.get("ranking") or []
        pool = int(res.get("pool", 0) or 0)
        bonus = int(res.get("bonus", 0) or 0)
        prizes = res.get("prizes") or []
        winners = res.get("winners") or []

        for w in winners:
            if w.get("npc"):
                continue
            amt = max(0, int(w.get("amount", 0) or 0))
            if amt <= 0 or ck is None:
                continue
            try:
                ck.add_score(group_id, w.get("uid"), amt)
            except Exception:
                logger.exception("[apix-meme] 赛车发奖失败 %s", w.get("uid"))

        rows = [title_bar("比赛结果" + ("（PVE）" if pve else "")),
                f"🏁 共 **{len(ranking)}** 辆车参赛"
                + (f"（NPC {res.get('npc_n', 0)}）"
                   if res.get("npc_n") else ""),
                ""]
        _medal = {1: "🥇", 2: "🥈", 3: "🥉"}
        for it in ranking:
            rk = int(it.get("rank", 0))
            _m = _medal.get(rk, f"{rk}.")
            _npc = "🤖 " if it.get("npc") else ""
            _t = (f"　{it.get('time')}s"
                  if bool(getattr(rm, "show_time", True)) else "")
            rows.append(f"{_m} {_npc}**{it.get('name')}**"
                        f"（{it.get('car')}）{_t}")
        rows += ["", "── 奖金 ──",
                 f"💰 奖池：**{fmt_num(pool)}** {sn}"
                 f"（报名费 + 今日加成 {fmt_num(bonus)}）"]
        for i, amt in enumerate(prizes[:3]):
            if i < len(ranking):
                _nm = ranking[i].get("name")
                _npc = "（NPC，不发奖）" if ranking[i].get("npc") else ""
                rows.append(f"　{_medal.get(i + 1, '')} {_nm}："
                            f"+{fmt_num(amt)} {sn}{_npc}")
        rows.append("　4、5 名没有奖金 😢")
        rows.append(title_foot())
        async for _r in self._send_content(event, "\n".join(rows)):
            yield _r

    def _guess_names(self) -> list:
        """
        参与指令名（默认「猜数」）。

        ★★ 1.7.7：**按长度倒序** —— 长名先匹配，避免被短名截断。
        """
        names = self._split_names(getattr(self, "cmd_guess", None)
                                  or ["猜数"])
        return sorted([n for n in names if n], key=len, reverse=True)

    def _guess_start_names(self) -> list:
        """开新局指令名（默认「开始猜数」），同样按长度倒序"""
        names = self._split_names(getattr(self, "cmd_guess_start", None)
                                  or ["开始猜数"])
        return sorted([n for n in names if n], key=len, reverse=True)

    def _match_names_exact(self, text: str, names) -> bool:
        """完全等于某个指令名（可选前导 /）"""
        s = str(text or "").strip()
        if not s:
            return False
        for n in (names or []):
            n = str(n or "").strip()
            if n and (s == n or s == f"/{n}"):
                return True
        return False

    def _is_guess_start_cmd(self, text: str) -> bool:
        """是不是「开始猜数」"""
        return self._match_names_exact(text, self._guess_start_names())

    def _is_guess_cmd(self, text: str) -> bool:
        """
        是不是猜数**参与**指令。

        ★ 只认：完全等于指令名（纯开新局已移到「开始猜数」），
          或后面紧跟数字/空格的带参写法。
          「猜数功能」（菜单入口）不会被吃掉。
        """
        s = str(text or "").strip()
        if not s:
            return False
        if self._match_names_exact(s, self._guess_names()):
            return True
        for n in self._guess_names():
            if not n or not s.startswith(n):
                continue
            rest = s[len(n):]
            if rest[:1].isspace() or rest[:1].isdigit():
                return True
        return False

    def _guess_arg(self, text: str) -> str:
        """剥掉参与指令名，取出后面的数字"""
        s = str(text or "").strip()
        for n in self._guess_names():
            if s == n or s == f"/{n}":
                return ""
            if s.startswith(n):
                return s[len(n):].strip()
        return s

    def _guess_spend(self, group_id, uid, delta: int) -> tuple:
        """
        直接改签到系统里的钱包积分。返回 (成功?, 钱包余额)。

        ★ 猜数用的是**签到系统那一份积分**（和偷积分/打工同一个钱包）。
        """
        ck = getattr(self, "checkin_module", None)
        if ck is None:
            return (False, 0)
        try:
            u = ck._guser_setdefault(group_id, uid, {
                "total_score": 0, "total_checkin": 0, "last_date": "",
                "streak": 0, "month_map": {}, "bank": 0,
            })
            cur = int(u.get("total_score", 0) or 0)
            new = cur + int(delta)
            if new < 0 and not bool(getattr(self, "guess_allow_negative", True)):
                return (False, cur)
            u["total_score"] = new
            ck._save()
            return (True, new)
        except Exception:
            logger.exception("[apix-meme] 猜数改积分失败")
            return (False, 0)

    def _guess_wallet(self, group_id, uid) -> int:
        try:
            ck = getattr(self, "checkin_module", None)
            u = ck._guser(group_id, uid) if ck is not None else {}
            return int((u or {}).get("total_score", 0) or 0)
        except Exception:
            return 0

    def _guess_settle(self, group_id) -> list:
        """
        ★ 结算「没人猜超时」的局。返回要发送的文本列表（未触发=空）。

        ★ 超时只在 `guess_timeout_sec > 0` 时启用；默认 60 秒。
        ★ 什么都不扣 —— 没人猜中就没人赢，直接作废这局。
        """
        gm = getattr(self, "guess_module", None)
        if gm is None or not gm.timeout_enabled():
            return []
        if not gm.is_idle_timeout(group_id):
            return []
        r = gm.current_round(group_id)
        if not r or r.get("finished"):
            return []
        ans = int(r.get("answer", 0) or 0)
        gm.finish_round(group_id, "", 0, closed_by="timeout")
        gm.clear_round(group_id)
        gm._save()
        logger.info("[apix-meme] 猜数超时关闭（群 %s，答案 %s）", group_id, ans)
        return "\n".join([
            f"{title_bar('猜数游戏')}",
            f"⏰ {gm.timeout_sec_text()}没人猜，这一局关闭了",
            f"🎯 答案是：**{ans}**",
            f"💡 想再来一局发「{self._guess_start_names()[0]}」",
        ])

    async def _handle_guess_start(self, event, group_id):
        """「开始猜数」—— 开新一局"""
        gm = getattr(self, "guess_module", None)
        if gm is None:
            async for _r in self._send_content(event, "⚠️ 猜数模块未加载"):
                yield _r
            return
        uid = to_str(event.get_sender_id())
        nick = get_sender_name(event) or f"QQ{uid}"
        _cmd = self._guess_names()[0]
        _start = self._guess_start_names()[0]
        try:
            _max = max(1, int(getattr(self, "guess_max_tries", 3) or 3))
        except Exception:
            _max = 3
        try:
            _pen = max(0, int(getattr(self, "guess_penalty", 1000) or 0))
        except Exception:
            _pen = 0
        _sn = str(getattr(self, "score_name", "") or "积分")

        old = gm.current_round(group_id)
        if old and not old.get("finished"):
            async for _r in self._send_content(event, "\n".join([
                f"{title_bar('猜数游戏')}",
                f"👤 {nick}",
                "",
                "⚠️ 已经有一局正在进行",
                f"🎯 范围：{old.get('lo')} ~ {old.get('hi')}",
                f"💡 直接发「{_cmd} 数字」参与",
                f"💡 想重开一局，再发一次「{_start}」",
                ])):
                yield _r
            return

        r = gm.start_round(group_id)
        _lines = [
            f"{title_bar('猜数游戏')}",
            f"👤 发起人：{nick}",
            "",
            f"🎯 我想好了一个 **{r['lo']} ~ {r['hi']}** 之间的整数",
            f"🙋 所有人都能猜，**每人最多猜 {_max} 次**",
            f"💡 参与：「{_cmd} 数字」（例：{_cmd} {r['lo']}）",
        ]
        if _pen > 0:
            _lines.append(f"💸 猜错扣 {_pen} {_sn}")
        if gm.timeout_enabled():
            _lines.append(f"⏰ {gm.timeout_sec_text()}内没人猜就关闭")
        async for _r in self._send_content(event, "\n".join(_lines)):
            yield _r

    async def _handle_guess_cmd(self, event, group_id, msg_str: str):
        """「猜数 数字」—— 参与猜一次"""
        gm = getattr(self, "guess_module", None)
        if gm is None:
            async for _r in self._send_content(event, "⚠️ 猜数模块未加载"):
                yield _r
            return
        _settled = self._guess_settle(group_id)
        if _settled:
            async for _r in self._send_content(event, _settled):
                yield _r
        uid = to_str(event.get_sender_id())
        nick = get_sender_name(event) or f"QQ{uid}"
        _cmd = self._guess_names()[0]
        _start = self._guess_start_names()[0]
        _sn = str(getattr(self, "score_name", "") or "积分")
        try:
            _pen = max(0, int(getattr(self, "guess_penalty", 1000) or 0))
        except Exception:
            _pen = 0

        r = gm.current_round(group_id)
        if not r or r.get("finished"):
            async for _r in self._send_content(event, "\n".join([
                f"{title_bar('猜数游戏')}",
                f"👤 {nick}",
                "",
                "⚠️ 现在没有正在进行的猜数",
                f"💡 发「{_start}」开一局",
                ])):
                yield _r
            return

        arg = self._guess_arg(msg_str).strip()
        _lo = int(r.get("lo", 1) or 1)
        _hi = int(r.get("hi", 100) or 100)
        if not arg.isdigit():
            async for _r in self._send_content(event, "\n".join([
                f"{title_bar('猜数游戏')}",
                f"👤 {nick}",
                "",
                f"⚠️ 请填一个数字，例如「{_cmd} {_lo}」",
                f"🎯 范围：{_lo} ~ {_hi}",
                ])):
                yield _r
            return
        num = int(arg)
        if num < _lo or num > _hi:
            async for _r in self._send_content(event, "\n".join([
                f"{title_bar('猜数游戏')}",
                f"👤 {nick}",
                "",
                f"⚠️ 只能猜 {_lo} ~ {_hi} 之间的数",
                ])):
                yield _r
            return

        _left = gm.left_of(r, uid)
        if _left <= 0:
            _max = max(1, int(getattr(self, "guess_max_tries", 3) or 3))
            async for _r in self._send_content(event, "\n".join([
                f"{title_bar('猜数游戏')}",
                f"👤 {nick}",
                "",
                f"⚠️ 你这一局已经猜满 {_max} 次了",
                f"💡 等别人猜中，或发「{_start}」开新的一局",
                ])):
                yield _r
            return

        ans = int(r.get("answer", 0) or 0)
        gm.record_try(group_id, uid)
        gm.note_guess(group_id, uid, num)
        _left2 = gm.left_of(gm.current_round(group_id), uid)

        if num == ans:
            reward = gm.roll_reward()
            if reward > 0:
                self._guess_spend(group_id, uid, reward)
            gm.finish_round(group_id, uid, reward, closed_by="win")
            gm.clear_round(group_id)
            gm._save()
            _lines = [
                f"{title_bar('猜数游戏')}",
                f"👤 {nick}",
                "",
                f"🎉 猜对了！答案就是 **{ans}**",
            ]
            if reward > 0:
                _lines.append(f"🎁 奖励 **+{reward}** {_sn}（随机）")
            _lines += [
                f"💳 当前{_sn}：{self._guess_wallet(group_id, uid)}",
                "",
                f"💡 想再来一局发「{_start}」",
            ]
            async for _r in self._send_content(event, "\n".join(_lines)):
                yield _r
            return

        _hint = "太大了 ⬇️" if num > ans else "太小了 ⬆️"
        _paid = 0
        _ok = True
        if _pen > 0:
            _ok, _bal = self._guess_spend(group_id, uid, -_pen)
            if _ok:
                _paid = _pen
        gm.note_lose(group_id, uid)
        gm._save()
        _lines = [
            f"{title_bar('猜数游戏')}",
            f"👤 {nick}",
            "",
            f"❌ {num} —— {_hint}",
        ]
        if _paid > 0:
            _lines.append(f"💸 猜错扣 **{_paid}** {_sn}"
                          f"（剩 {self._guess_wallet(group_id, uid)}）")
        elif _pen > 0 and not _ok:
            _lines.append(f"💸 猜错要扣 {_pen} {_sn}，但你的{_sn}不够")
        _lines.append(f"🎯 这一局你还能猜 **{_left2}** 次")
        if gm.timeout_enabled():
            _lines.append(f"⏰ {gm.timeout_sec_text()}内没人猜就关闭")
        async for _r in self._send_content(event, "\n".join(_lines)):
            yield _r

    def _guess_menu_live(self) -> str:
        """（保留占位，暂无内容）"""
        return ""

    def _nick_of_group(self, group_id, uid) -> str:
        """取群内昵称（签到模块优先，其次脱敏 QQ 号）"""
        try:
            ck = getattr(self, "checkin_module", None)
            if ck is not None and hasattr(ck, "_nick_of"):
                nm = str(ck._nick_of(group_id, uid) or "").strip()
                if nm:
                    return nm
        except Exception:
            pass
        s = str(uid or "")
        return f"{s[:3]}****{s[-2:]}" if len(s) > 5 else s

    def _parse_page_arg(self, text: str, names):
        """
        匹配「指令 [页码]」。

        返回：
          int  -> 页码（不带页码 = 1）
          None -> 不匹配

        允许：`我的图鉴`、`我的图鉴 2`、`我的图鉴2`、`/我的图鉴 3`、`我的图鉴 第2页`
        ★ 只吃纯数字，`我的图鉴abc` 一律不触发（避免误吃聊天）。
        """
        t = str(text or "").strip()
        if t.startswith("/"):
            t = t[1:].strip()
        if not t:
            return None
        for nm in names or []:
            nm = str(nm or "").strip()
            if not nm or not t.startswith(nm):
                continue
            rest = t[len(nm):].strip()
            if not rest:
                return 1
            m = re.fullmatch(r"(?:第\s*)?(\d+)\s*(?:页)?", rest)
            if not m:
                continue
            try:
                return max(1, int(m.group(1)))
            except Exception:
                return 1
        return None

    def _match_gallery_page(self, text: str, names, per_page: int = 60):
        """「图鉴指令 [页码]」（保留原签名，内部走 _parse_page_arg）"""
        return self._parse_page_arg(text, names)

    async def _handle_gallery_cmd(self, event, group_id, page: int = 1):
        """「我的图鉴」：展示小鸟收集进度（固定图片输出，支持页码）"""
        from astrbot.api.message_components import Image

        if not group_id:
            async for _r in self._send_content(event, "⚠️ 请在群聊中使用小鸟图鉴"):
                yield _r
            return
        bc = getattr(self, "bird_collection", None)
        birds = []
        if self.data_store is not None:
            for row in self.data_store.get("bird_list", autoreload=2):
                if isinstance(row, dict) and str(row.get("name") or "").strip():
                    birds.append(row)
        if not birds:
            async for _r in self._send_content(event, "❌ 后台还没有配置小鸟\n"
                "💡 请在「后台 → 小鸟图鉴」里添加小鸟"):
                yield _r
            return

        uid = to_str(event.get_sender_id())
        owned = {}
        if bc is not None:
            try:
                owned = bc.get_user(group_id, uid)
            except Exception:
                owned = {}

        items = []
        for row in birds:
            nm = str(row.get("name") or "").strip()
            row_data = owned.get(nm)
            items.append({
                "name": nm,
                "img": self._bird_img_of(nm) or "",
                "level": int((row_data or {}).get("lv", 0) or 0),
                "owned": isinstance(row_data, dict),
            })
            if len(items) >= 200:
                break

        got = sum(1 for x in items if x["owned"])

        rank, rank_users = 0, 0
        if bc is not None and getattr(self, "bird_rank_enable", True):
            try:
                _valid = {str(r.get("name") or "").strip() for r in birds}
                rank, rank_users, _mine = bc.rank_of(uid, _valid)
            except Exception:
                rank, rank_users = 0, 0
                logger.debug("[apix-meme] 计算小鸟排名失败", exc_info=True)

        om = getattr(self, "output_mode_module", None)
        bg = ""
        as_image = False
        try:
            if om is not None:
                bg = str(getattr(om, "bg_image", "") or "")
                as_image = str(om.mode_of(group_id)).startswith("image")
        except Exception:
            as_image = False

        img_path = ""
        pages = 1
        if om is not None and as_image:
            try:
                img_path, pages = await om.render_bird_gallery(
                    items, bg=bg, page=page,
                    rank=rank, total_users=rank_users)
            except Exception:
                logger.exception("[apix-meme] 渲染小鸟图鉴失败")
                img_path, pages = "", 1
        logger.info(
            "[apix-meme] 图鉴：小鸟 %d 只 / 已收集 %d / 第 %s 页 / 图片=%s",
            len(items), got, page, "有" if img_path else "无（回落文本）")
        if img_path:
            yield event.chain_result([Image.fromFileSystem(img_path)])
            return
        _per = 60
        pages = max(1, -(-len(items) // _per))
        try:
            page = int(page)
        except Exception:
            page = 1
        if page < 1 or page > pages:
            page = 1
        head = f"🐦 小鸟图鉴　已收集 {got} / {len(items)}"
        if rank > 0:
            head += f"　（第 {rank} 名 / 共 {rank_users} 人）"
        if pages > 1:
            head += f"　（第 {page} / {pages} 页）"
        lines = [head]
        start = (page - 1) * _per
        for x in items[start:start + _per]:
            if x["owned"]:
                lines.append(f"· {x['name']} Lv.{min(x['level'], 100)}")
        if got == 0:
            lines.append("（还没抽到过小鸟，发「今日小鸟」试试）")
        if pages > 1:
            _gd = (self._split_names(getattr(self, "cmd_gallery", None))
                   or ["我的图鉴"])[0]
            lines.append(f"💡 发「{_gd} {min(page + 1, pages)}」看下一页")
        async for _r in self._send_content(event, "\n".join(lines)):
            yield _r

    def _gallery_group_mode(self, group_id):
        """返回 (输出模式, 背景图)"""
        om = getattr(self, "output_mode_module", None)
        if om is None:
            return "text", ""
        try:
            return om.mode_of(group_id), str(getattr(om, "bg_image", "") or "")
        except Exception:
            return "text", ""

    @staticmethod
    def _lines_to_image(path, mode):
        """把渲染出的图片路径包成消息组件；mode 不是 image 或路径为空则返回 []"""
        if not path or not str(mode).startswith("image"):
            return []
        from astrbot.api.message_components import Image
        try:
            return [Image.fromFileSystem(path)]
        except Exception:
            logger.exception(
                "[apix-meme] 构造图片组件失败，已回落文本；path=%r", path)
            return []

    def _menu_rows(self) -> list:
        """后台菜单文本原始行（[{id,label,text}, ...]）"""
        try:
            rows = self.data_store.get("menu_texts")
        except Exception:
            return []
        if isinstance(rows, dict):
            return [{"id": k, "label": "", "text": v}
                    for k, v in rows.items()]
        return rows if isinstance(rows, list) else []

    def _menu_custom(self) -> dict:
        """菜单 id -> 自定义文本（只含非空的）"""
        out = {}
        for r in self._menu_rows():
            if not isinstance(r, dict):
                continue
            mid = str(r.get("id") or "").strip()
            txt = str(r.get("text") or "").strip()
            if mid and txt:
                out[mid] = txt
        return out

    _OLD_TITLE_PREFIXES = ("自定义娱乐：", "自定义娱乐:",
                              "鸡鸡娱乐系统：", "鸡鸡娱乐系统:")

    def _apply_menu_prefix(self, text: str) -> str:
        """
        让**自定义菜单文案**的标题也跟随「全局标题前缀」。

        ★ 为什么需要：菜单文案是**存在后台里**的整段文本，
          用户改过之后就是一份静态内容，不会自动跟着前缀设置变。
          这里在出口统一处理：
            · 首行是「已知旧前缀 + 标题」-> 换成当前前缀（或去掉）
            · 首行没有前缀                -> 补上当前前缀
            · 当前前缀为空                -> 去掉前缀
          标题本身（前缀后面的字）**原样保留**，不覆盖用户的改法。

        ★ 只动第一行，正文一行都不碰。
        """
        s = str(text or "")
        if not s.strip():
            return s
        try:
            from .core.path_utils import TITLE_PREFIX as _cur
        except Exception:
            try:
                from core.path_utils import TITLE_PREFIX as _cur
            except Exception:
                _cur = ""
        _cur = _cur or ""
        _olds = [p for p in (list(self._OLD_TITLE_PREFIXES)
                             + ["铁剑娘系统：", _cur]) if p]
        lines = s.split("\n")
        _i = 0
        while _i < len(lines) and not lines[_i].strip():
            _i += 1
        if _i >= len(lines):
            return s
        _first = lines[_i]
        _st = _first.strip()
        _core = _st.strip("━─=＝-—·・ \t")
        _hit = ""
        for _p in _olds:
            if _core.startswith(_p):
                _hit = _p
                break
        if _hit:
            _title = _core[len(_hit):].strip()
        else:
            if any(_st.startswith(_p) for _p in _olds):
                _title = _st
            else:
                _title = _core or _st
        if not _title:
            return s
        _out = (_cur + _title) if _cur else _title
        _lead = _first[:len(_first) - len(_first.lstrip())]
        lines[_i] = _lead + _out
        return "\n".join(lines)

    def _menu(self, menu_id: str, default_fn) -> str:
        """
        取菜单内容：**后台自定义优先**，没有就用生成默认。

        ★ 例外：MENU_TEXT_LIVE 里的菜单**永远实时生成** ——
          它们含「每次抽奖花多少 / 利息多少 / 每天几次」这类数值，
          必须跟当前配置一致，不能被存盘文案挡住。

        ★ 1.7.6：出口统一做一次「图标与文字之间补空格」，
          这样 44 个菜单（含后台可自定义文案）全都一致，
          不用去改几十处 f-string。
        ★★ 1.7.6：渲染前**热刷新配置** —— 后台（含独立 HTML 配置页）
          保存的自定义指令名会立刻体现在菜单里，不用手动重载插件。
        """
        try:
            self._refresh_cfg_if_changed()
        except Exception:
            logger.debug("[apix-meme] 菜单热刷新失败", exc_info=True)

        def _fix(t: str) -> str:
            try:
                from .core.menu_builder import _icon_space_text
                _r = _icon_space_text(t)
            except Exception:
                try:
                    from core.menu_builder import _icon_space_text
                    _r = _icon_space_text(t)
                except Exception:
                    _r = t
            try:
                _ap = getattr(self, "_apply_menu_prefix", None)
                if callable(_ap):
                    _r2 = _ap(_r)
                    if str(_r2 or "").strip():
                        _r = _r2
            except Exception:
                logger.debug("[apix-meme] 应用标题前缀失败，用原文", exc_info=True)
            return _r

        if str(menu_id) not in MENU_TEXT_LIVE:
            try:
                val = str(self._menu_custom().get(menu_id) or "").strip()
                if val:
                    return _fix(val)
            except Exception:
                pass
        try:
            return _fix(str(default_fn() or ""))
        except Exception:
            logger.exception("[apix-meme] 生成菜单失败: %s", menu_id)
            return ""

    def _strip_title_prefix(self, text: str) -> str:
        """
        去掉菜单文案的**标题前缀**（`铁剑娘系统：` / 旧前缀），只留正文。

        ★★ 1.7.6：判断「内容是否还是系统默认」时必须忽略前缀 ——
          前缀是**渲染时动态加**的，用户换过前缀后旧文案会和新默认值
          只差前缀那一行，从而被误判成「用户手改过」，导致菜单永远
          刷新不了（用户反馈：音乐菜单输出内容没有更新）。
        """
        s = str(text or "")
        try:
            from .core.path_utils import TITLE_PREFIX as _tp
        except Exception:
            try:
                from core.path_utils import TITLE_PREFIX as _tp
            except Exception:
                _tp = ""
        lines = s.split("\n")
        if not lines:
            return s
        _pfxs = [_tp] + [p for p in
                         ("自定义娱乐：", "自定义娱乐:",
                          "鸡鸡娱乐系统：", "鸡鸡娱乐系统:",
                          "铁剑娘系统：", "铁剑娘系统:") if p]
        for _p in _pfxs:
            _p = str(_p or "")
            if _p and lines[0].strip().startswith(_p.strip()):
                lines = lines[1:]
                break
        return "\n".join(lines).strip()

    def _menu_text_untouched(self, menu_id: str, text: str) -> bool:
        """
        判断某条菜单文案是否**还是系统生成的默认值**（用户没改过）。

        用于版本升级时安全地刷新菜单：
          · 是历史默认值 -> 可以重生成（否则菜单里写死的数值会停在旧配置）
          · 改过了       -> 绝不覆盖

        ★ 关键：必须记住**所有历史版本的默认值**，不能只记上一版。
          否则跨版本升级时（v2 -> v4），v2 的旧文案会因为「不等于 v3 的默认值」
          被误判成用户自定义，从而只盖版本戳、内容永远不刷新。
        ★★ 1.7.6：比对时**忽略标题前缀**（见 `_strip_title_prefix`）。
        """
        cur = str(text or "").strip()
        if not cur:
            return True
        cur_body = self._strip_title_prefix(cur)
        try:
            for _r in self._menu_rows():
                if not isinstance(_r, dict):
                    continue
                if str(_r.get("id") or "").strip() != str(menu_id):
                    continue
                _prev = _r.get("prev")
                if isinstance(_prev, (list, tuple)):
                    if cur_body in [self._strip_title_prefix(str(x))
                                    for x in _prev if x]:
                        return True
                elif isinstance(_prev, str) and _prev.strip():
                    if cur_body == self._strip_title_prefix(_prev):
                        return True
                break
        except Exception:
            pass
        for frag in MENU_TEXT_LEGACY_FRAGS.get(str(menu_id), ()):
            if frag in cur_body:
                return True
        return False

    def _menu_text_is_stale_subset(self, menu_id: str, text: str) -> bool:
        """
        存盘文案是不是「**当前默认值的旧版**」—— 即它所有内容块都能在
        当前生成的默认值里找到（只是**缺了新加的块**）。

        ★★ 1.5 为什么需要（用户反馈：MC系统输出内容没有新功能相关指令）：
          我给 MC 菜单加了「玩家查询 / MCJ / MCB」三块，旧文案因此
          **少了一整块**，长度 164 vs 295 ——
            · `_menu_text_similar_to_default` 有「长度比 < 0.6 直接 False」
              的门槛（164/295 = 0.556），于是兜底失效；
            · 结果被判成「用户手改过」，菜单**永远刷新不了**。
          但「旧文案的每一块都在新默认里」恰恰证明它只是**过期默认值**，
          不是用户自定义 —— 用户自己写的文案不会正好是默认值的子集。

        ★ 防误伤：要求
           · 两侧都切成 ≥3 个块（太短的文案不参与，免得误判）
           · 旧文案的块**全部**能在新默认里找到
           · 新默认确实更丰富（块数更多）
        """
        try:
            cur = self._strip_title_prefix(text)
            new = self._strip_title_prefix(
                str(menu_default(self, menu_id) or ""))
            if not cur or not new:
                return False

            def _blocks(s):
                out = []
                for ln in s.split("\n"):
                    t = ln.strip()
                    if not t or all(c in "━─=＝-—·・ \t" for c in t):
                        continue
                    out.append(t)
                return out

            cb, nb = _blocks(cur), _blocks(new)
            if len(cb) < 3 or len(nb) <= len(cb):
                return False
            nset = set(nb)
            return all(b in nset for b in cb)
        except Exception:
            logger.debug("[apix-meme] 过期子集判定失败", exc_info=True)
            return False
    def _menu_text_similar_to_default(self, menu_id: str,
                                      text: str) -> bool:
        """
        存盘文案与**当前默认值**结构是否基本相同（=> 是旧版系统默认）。

        ★★ 1.7.6 为什么需要：
          菜单里含**动态数值**（「单局金币 1 ~ 5000」/「每天最多 20 局」/
          「也可以用：查QQ / 查qq」），配置一改，旧文案就既不在 `prev`
          清单里、也不匹配历史指纹 —— 于是被误判成「用户手改过」，
          **永远刷新不了**（用户反馈：音乐菜单输出内容没有更新）。
          这里用相似度兜底：结构一样、只有数值/个别词不同，就是系统默认。

        ★ 故意保守：阈值 0.75，且长度差太大直接判 False，
          免得把用户真改过的文案冲掉。
        """
        try:
            cur_body = self._strip_title_prefix(text)
            new_body = self._strip_title_prefix(
                str(menu_default(self, menu_id) or ""))
            if not cur_body or not new_body:
                return False
            a, b = len(cur_body), len(new_body)
            if min(a, b) / max(a, b) < 0.6:
                return False
            from difflib import SequenceMatcher as _SM
            return _SM(None, cur_body, new_body).ratio() >= 0.75
        except Exception:
            logger.debug("[apix-meme] 菜单相似度判断失败", exc_info=True)
            return False

    def _init_menu_texts(self):
        """
        把菜单默认内容生成/补进后台数据。

        触发条件（都只影响**这一条**）：
          · 该菜单在后台**还没有条目**        -> 新增
          · 该菜单条目存在、但**内容为空**     -> 恢复默认
            （后台的提示就是「清空 = 恢复默认」，以前这条没实现）

        · 已有**非空**内容的条目一律不动（保留用户改过的）
        · 默认生成失败（返回空）时不覆盖，避免把默认顶成空
        · 返回 (写入条数, 生成失败的id列表)
        """
        try:
            rows = self._menu_rows()
            _seen, _clean, _dropped = set(), [], []
            for _r in rows:
                if not isinstance(_r, dict):
                    continue
                _mid = str(_r.get("id") or "").strip()
                if not _mid or _mid not in MENU_REGISTRY:
                    _dropped.append(_mid or "(空)")
                    continue
                if _mid in _seen:
                    _dropped.append(_mid + "(重复)")
                    continue
                _seen.add(_mid)
                _clean.append(_r)
            _changed = len(_clean) != len(rows)
            rows = _clean

            _by_id = {}
            for _r in rows:
                _by_id[str(_r.get("id") or "").strip()] = _r
            have = {k for k, v in _by_id.items() if str(v.get("text") or "").strip()}

            added, restored, skipped = 0, 0, []
            refreshed = 0
            _protect = []
            _skipped_gen = []
            _menu_ver = str(MENU_TEXT_VERSION)
            for mid, (label, _fn) in MENU_REGISTRY.items():
                is_new = mid not in _by_id
                if not is_new and mid in have:
                    _row = _by_id[mid]
                    _cur = str(_row.get("text") or "")
                    _prev = _row.get("prev")
                    if not isinstance(_prev, list):
                        _lst = []
                        if isinstance(_prev, str) and _prev.strip():
                            _lst.append(_prev.strip())
                        _row["prev"] = _lst
                    _new = str(menu_default(self, mid) or "")
                    if not _new.strip():
                        _skipped_gen.append(mid)
                        continue
                    if self._strip_title_prefix(_cur) == \
                            self._strip_title_prefix(_new):
                        _row["version"] = _menu_ver
                        continue
                    if not self._menu_text_untouched(mid, _cur):
                        if (not self._menu_text_similar_to_default(mid, _cur)
                                and not self._menu_text_is_stale_subset(
                                    mid, _cur)):
                            _row["version"] = _menu_ver
                            if self._strip_title_prefix(
                                    _cur) != self._strip_title_prefix(_new):
                                _protect.append(mid)
                            continue
                    _lst = [str(x) for x in _row.get("prev") or []]
                    if _cur.strip() and _cur.strip() not in _lst:
                        _lst.append(_cur.strip())
                    _row["prev"] = _lst[-8:]
                    _row["text"] = _new
                    _row["label"] = label
                    _row["version"] = _menu_ver
                    refreshed += 1
                    continue
                txt = menu_default(self, mid)
                if not str(txt or "").strip():
                    skipped.append(mid)
                    continue
                if is_new:
                    rows.append({"id": mid, "label": label, "text": txt,
                                 "version": _menu_ver})
                    added += 1
                else:
                    _by_id[mid]["text"] = txt
                    _by_id[mid]["label"] = label
                    _by_id[mid]["version"] = _menu_ver
                    restored += 1
            if added or restored or _changed or refreshed:
                self.data_store.set("menu_texts", rows)
                if refreshed:
                    logger.info("[apix-meme] 菜单文案已随版本更新 %d 条"
                                "（%s）", refreshed, _menu_ver)
                if _protect:
                    logger.info("[apix-meme] %d 条菜单判定为「用户自定义」，"
                                "保持不动：%s", len(_protect),
                                ",".join(_protect))
                if _skipped_gen:
                    logger.warning(
                        "[apix-meme] %d 条菜单暂时生成不出默认值，"
                        "保留原内容、下次启动再试：%s",
                        len(_skipped_gen), ",".join(_skipped_gen))
                if _dropped:
                    logger.info("[apix-meme] 已清掉无效菜单条目：%s",
                                ",".join(_dropped))
                if added or restored:
                    logger.info(
                        "[apix-meme] 菜单默认内容已写入后台"
                        "（新增 %d 项，恢复默认 %d 项）", added, restored)
            if skipped:
                logger.warning("[apix-meme] 这些菜单默认内容生成失败，未写入：%s",
                               ",".join(skipped))
            return added + restored, skipped
        except Exception:
            logger.exception("[apix-meme] 写入菜单默认内容失败")
            return 0, []

    def _build_main_menu(self):
        return self._menu("main", lambda: build_main_menu(self))
    def _build_meme_menu(self):
        return self._menu("meme", lambda: build_meme_menu(self))
    def _build_mc_menu(self):
        return self._menu("mc", lambda: build_mc_menu(self))
    # ★★ 1.5.0：世界系统 4 个子菜单
    def _build_mc_version_menu(self):
        return self._menu("mc_version", lambda: build_mc_version_menu(self))
    def _build_mc_server_menu(self):
        return self._menu("mc_server", lambda: build_mc_server_menu(self))
    def _build_mc_player_menu(self):
        return self._menu("mc_player", lambda: build_mc_player_menu(self))
    def _build_mc_push_menu(self):
        return self._menu("mc_push", lambda: build_mc_push_menu(self))
    def _build_greeting_menu(self):
        return self._menu("greeting", lambda: build_greeting_menu(self))
    def _persist_output_modes(self) -> bool:
        """把每群输出模式写回后台配置（重启后仍生效）"""
        om = getattr(self, "output_mode_module", None)
        if om is None:
            return False
        try:
            cfg = self._read_disk_config() or {}
            if not isinstance(cfg, dict):
                cfg = {}
            cfg["output_group_modes"] = om.dump_groups()
            self._write_disk_config(cfg)
            try:
                if isinstance(self.config, dict):
                    self.config["output_group_modes"] = om.dump_groups()
            except Exception:
                pass
            logger.info(f"[apix-meme] 输出模式已保存：{om.dump_groups()}")
            self._sync_to_astrbot_config(
                {"output_group_modes": om.dump_groups(),
                 "output_default_mode": om.default_mode,
                 "output_mode_enable": om.enable})
            return True
        except Exception:
            logger.exception("[apix-meme] 写回输出模式失败")
            return False

    async def _peek_tail_text(self, event) -> str:
        """
        取本条回复的尾巴文本（给**专用卡片**用，如猜拳 / 查询）。

        ★ 1.7.6：专用卡片（猜拳 / 查询）以前不渲染尾巴，
          于是钩子只能补一条**单独的文本消息**（用户看到「图片 + 文本」分离）。
          现在把这些卡片的尾巴也交给渲染端画进图里，所以先在这里取一次。

        ★ 同一条回复内取到的是**同一条**（用 event 做 key）。
        ★ **不**在这里打 `_apix_tail_done` —— 万一卡片渲染失败回落文本，
          还得靠钩子/`_send_content` 把尾巴补上（否则尾巴直接丢了）。
          真正发出「带尾巴的图」之后，调用方要调 `_mark_tail_done()`。
        """
        try:
            if getattr(event, "_apix_tail_done", False):
                return ""
            rt = getattr(self, "reply_tail", None)
            if rt is None or not rt.enable:
                return ""
            if rt.only_group:
                try:
                    if not _get_group_id_from_event(event):
                        return ""
                except Exception:
                    pass
            return (await rt.tail_text(token=id(event))) or ""
        except Exception:
            logger.exception("[apix-meme] 取卡片尾巴失败")
            return ""

    @staticmethod
    def _mark_tail_done(event):
        """标记「这条回复的尾巴已经随图片发出去了」，钩子不要再加"""
        try:
            event._apix_tail_done = True
        except Exception:
            pass

    async def _send_content(self, event, text: str, **kwargs):
        """
        按本群「输出模式」发送内容。

        text 模式 -> 纯文本（保持原样）
        image 模式 -> 渲染成图片；渲染失败自动回落纯文本

        kwargs:
          with_tail=False -> **不要**拼回复尾巴
            （定时推送/整点报时自己拼，避免加两次）
          no_tail=True    -> 同上（可读性更好的别名）
        """
        if isinstance(text, (list, tuple)):
            text = text[0] if text else ""
        if not isinstance(text, str):
            text = str(text or "")
        if not text:
            return
        try:
            self._refresh_cfg_if_changed()
        except Exception:
            logger.debug("[apix-meme] 输出前热刷新失败", exc_info=True)
        try:
            from .core.path_utils import with_bottom_bar as _wbb
        except Exception:
            try:
                from core.path_utils import with_bottom_bar as _wbb
            except Exception:
                _wbb = None
        if _wbb is not None:
            text = _wbb(text)
        with_tail = bool(kwargs.pop("with_tail", True))
        _tail_text = ""
        if with_tail and not getattr(event, "_apix_tail_done", False) \
                and not getattr(event, "_apix_own_reply", False):
            _rt = getattr(self, "reply_tail", None)
            if _rt is not None and _rt.enable:
                try:
                    _okg = True
                    if _rt.only_group:
                        try:
                            _okg = bool(_get_group_id_from_event(event))
                        except Exception:
                            _okg = True
                    if _okg:
                        _tail_text = await _rt.tail_text(token=id(event))
                        if _tail_text:
                            logger.info(f"[reply_tail] 尾巴：{_tail_text[:40]!r}")
                except Exception:
                    logger.exception("[apix-meme] 取回复尾巴失败")
        om = getattr(self, "output_mode_module", None)
        if om is None:
            _plain0 = text
            if _tail_text:
                _plain0 = text + "\n" + _tail_text
            yield event.plain_result(_plain0)
            if _tail_text:
                try:
                    event._apix_tail_done = True
                except Exception:
                    pass
            return
        try:
            _gid = _get_group_id_from_event(event) or ""
            _idir = ""
            try:
                _idir = str(getattr(self.checkin_module, "mount_img_dir", "")
                            or "")
            except Exception:
                _idir = ""
            chain, mode = await om.render_chain(text, _gid, img_dir=_idir,
                                                 tail=_tail_text)
        except Exception:
            logger.exception("[apix-meme] 输出模式渲染异常，回落纯文本")
            chain, mode = [], "text"
        if chain:
            if _tail_text:
                try:
                    event._apix_tail_done = True
                except Exception:
                    pass
            yield event.chain_result(chain)
        else:
            try:
                from .core.output_mode import strip_inline_img
            except Exception:
                try:
                    from core.output_mode import strip_inline_img
                except Exception:
                    strip_inline_img = None
            _plain = strip_inline_img(text) if strip_inline_img else text
            try:
                from .core.path_utils import strip_md as _smd3
            except Exception:
                try:
                    from core.path_utils import strip_md as _smd3
                except Exception:
                    _smd3 = None
            if _smd3 is not None:
                _plain = _smd3(_plain)
            if _tail_text:
                _plain = _plain + "\n" + _tail_text
                try:
                    event._apix_tail_done = True
                except Exception:
                    pass
            yield event.plain_result(_plain)

    def _is_output_mode_menu(self, text: str) -> bool:
        """「输出模式」菜单入口（管理员可看当前模式）"""
        om = getattr(self, "output_mode_module", None)
        if om is None:
            return False
        names = set(om.cmd_names) | {"输出模式"}
        return text in names or text in {f"/{n}" for n in names}

    def _build_output_mode_menu(self):
        """构建输出模式菜单（顺带把当前群号挂上，供菜单显示本群模式）"""
        try:
            self._menu_group_id = getattr(self, "_cur_group_id", "") or ""
        except Exception:
            self._menu_group_id = ""
        return self._menu("output_mode", lambda: build_output_mode_menu(self))

    def _build_checkin_menu(self):
        return self._menu("checkin", lambda: build_checkin_menu(self))
    def _build_daily_menu(self):
        return self._menu("daily", lambda: build_daily_menu(self))
    def _build_fortune_menu(self):
        return self._menu("fortune", lambda: build_fortune_menu(self))
    def _build_husband_menu(self):
        return self._menu("husband", lambda: build_husband_menu(self))
    def _build_luck_menu(self):
        return self._menu("luck", lambda: build_luck_menu(self))
    def _build_news_menu(self):
        return self._menu("news", lambda: build_news_menu(self))
    def _build_admin_menu(self):
        return self._menu("admin", lambda: build_admin_menu(self))
    def _build_meme_feature_menu(self):
        return self._menu("meme_feature", lambda: build_meme_feature_menu(self))
    def _build_greeting_morning_menu(self):
        return self._menu("greeting_morning", lambda: build_greeting_morning_menu(self))
    def _build_greeting_night_menu(self):
        return self._menu("greeting_night", lambda: build_greeting_night_menu(self))
    def _build_greeting_rank_menu(self):
        return self._menu("greeting_rank", lambda: build_greeting_rank_menu(self))
    def _build_checkin_signin_menu(self):
        return self._menu("checkin_signin", lambda: build_checkin_signin_menu(self))
    def _build_checkin_query_menu(self):
        return self._menu("checkin_query", lambda: build_checkin_query_menu(self))
    def _build_checkin_bank_menu(self):
        return self._menu("checkin_bank", lambda: build_checkin_bank_menu(self))
    def _build_checkin_bank_save_menu(self):
        return self._menu("checkin_bank_save",
                          lambda: build_checkin_bank_save_menu(self))
    def _build_checkin_bank_loan_menu(self):
        return self._menu("checkin_bank_loan",
                          lambda: build_checkin_bank_loan_menu(self))
    def _build_checkin_bank_fund_menu(self):
        return self._menu("checkin_bank_fund",
                          lambda: build_checkin_bank_fund_menu(self))
    def _build_checkin_bank_rob_menu(self):
        return self._menu("checkin_bank_rob",
                          lambda: build_checkin_bank_rob_menu(self))
    def _build_checkin_lottery_menu(self):
        return self._menu("checkin_lottery",
                          lambda: build_checkin_lottery_menu(self))
    def _build_checkin_steal_menu(self):
        return self._menu("checkin_steal", lambda: build_checkin_steal_menu(self))
    def _build_checkin_shop_help_menu(self, page=1):
        """
        ★★ 1.7.7：商店帮助菜单（**分页**）。

        ★ 和「签到商店」指令区分开：
          · `签到商店 [页码]`（指令）-> 商品列表（每页 10 个）
          · `商店帮助 [页码]` （菜单）-> 玩法与指令说明（共 3 页）
        """
        return self._menu("checkin_shop_help",
                          lambda: build_checkin_shop_help_menu(self, page))
    def _build_checkin_mount_menu(self):
        return self._menu("checkin_mount", lambda: build_checkin_mount_menu(self))
    def _build_mount_manage_menu(self):
        return self._menu("mount_manage",
                          lambda: build_checkin_mount_manage_menu(self))
    def _build_mount_admin_menu(self):
        return self._menu("mount_admin",
                          lambda: build_checkin_mount_admin_menu(self))
    def _build_checkin_job_menu(self):
        return self._menu("checkin_job", lambda: build_checkin_job_menu(self))

    def _is_job_list_menu(self, text: str) -> bool:
        return self._menu_entry_hit("job_list", text)

    def _is_job_refill_menu(self, text: str) -> bool:
        return self._menu_entry_hit("job_refill", text)

    def _is_job_refill_status_menu(self, text: str) -> bool:
        return self._menu_entry_hit("job_refill_status", text)

    def _is_stamina_menu(self, text: str) -> bool:
        return self._menu_entry_hit("stamina", text)

    def _is_stamina_eat_menu(self, text: str) -> bool:
        return self._menu_entry_hit("stamina_eat", text)

    def _build_job_list_menu(self):
        return self._menu("job_list",
                          lambda: build_checkin_job_list_menu(self))

    def _build_job_refill_menu(self):
        return self._menu("job_refill",
                          lambda: build_checkin_job_refill_menu(self))

    def _build_job_refill_status_menu(self):
        """
        ★★ 1.7.7：加班状态。

        ★ 注意：「加班状态」在**分发时走指令分支**（实时数据），
          不走菜单分支 —— 因为「可加班 / 不可加班工种」是按**人 + 时间**
          算出来的，静态菜单文案给不出。所以这个 builder 只作为菜单
          列表里的备用内容（后台「菜单输出内容」里能看到并自定义）。
        """
        return self._menu("job_refill_status",
                          lambda: build_checkin_job_refill_status_menu(self))

    def _build_stamina_menu(self):
        return self._menu("stamina", lambda: build_stamina_menu(self))
    def _build_speak_menu(self):
        return self._menu("speak", lambda: build_speak_menu(self))
    def _build_pet_menu(self):
        return self._menu("pet", lambda: build_pet_menu(self))
    def _build_pet_care_menu(self):
        return self._menu("pet_care", lambda: build_pet_care_menu(self))
    def _build_pet_fight_menu(self):
        return self._menu("pet_fight", lambda: build_pet_fight_menu(self))
    def _build_pet_item_menu(self):
        return self._menu("pet_item", lambda: build_pet_item_menu(self))
    def _build_guess_menu(self):
        return self._menu("guess", lambda: build_guess_menu(self))
    def _build_music_menu(self):
        """★ 1.5.1：音乐功能菜单（标题走 _entry_name，后台可改名）"""
        return self._menu("music", lambda: build_music_menu(self))

    async def _music_play_pick(self, event, group_id, _uid, _music,
                              it, _sess_src="", skip_search: bool = False):
        """
        ★★ 1.5.1：播放**指定的一首歌**（点歌 / 搜歌 / 选歌 共用）。

        市场版**只发音乐卡片**：
          · 卡片用 AstrBot 原生 `Music` 组件构造，
            **签名交给平台适配器**（插件不调外部签名服务）
          · 封面二选一（音源 LOGO / 点歌人头像），都是直链 ——
            不下载、不做图像处理、不写磁盘、不缓存
          · 不发语音、不下载音频文件

        `skip_search=True`：点歌链路刚刚搜过，手上的 `it` 就是最新结果，
        省掉一次网络请求；选歌链路结果可能放了很久（链接带时间签名），
        所以默认重搜一次换新链接。
        """
        try:
            _vuid = to_str(event.get_sender_id())
        except Exception:
            _vuid = str(_uid or "")

        if not skip_search:
            try:
                _fresh, _err = await _music.search(
                    it.get("name", ""), _sess_src or it.get("source", ""))
                if _fresh:
                    for _f in _fresh:
                        if str(_f.get("songid", "")) == str(it.get("songid", "")):
                            it = _f
                            break
                    else:
                        it = _fresh[0]
            except Exception:
                logger.debug("[apix-meme] 重搜失败，用原结果", exc_info=True)

        _audio_url = str(it.get("url", "") or "")
        try:
            _cover = _music.resolve_card_cover(it, user_id=_vuid)
        except Exception:
            logger.debug("[apix-meme] 解析卡片封面失败", exc_info=True)
            _cover = ""

        _card = None
        try:
            _card = _music.make_music_card(
                it, cover_url=_cover, audio_url=_audio_url,
                title=str(it.get("name", "") or ""),
                desc=str(it.get("artist", "") or ""))
        except Exception:
            logger.exception("[apix-meme] 构造音乐卡片失败")

        chain = []
        if _card is not None:
            chain.append(_card)
            chain.append(Plain(_music.build_card_text(
                it, direct_url=_audio_url)))
            logger.info(
                f"[apix-meme] 点歌使用音乐卡片（平台适配器签名）："
                f"{it.get('name')} / {it.get('artist')} / "
                f"音源={it.get('source')} / 封面={_cover[:60]}")
        else:
            chain.append(Plain(_music.build_pick_text(
                it, direct_url=_audio_url)))
            logger.warning(
                "[apix-meme] 卡片构造失败（多半是没拿到音频直链），"
                "本首改用文本，文本里带音乐地址")
        if chain:
            yield event.chain_result(chain)
            GLOBAL_STATS["send"] += 1
    def _build_meme_admin_menu(self):
        return self._menu("meme_admin", lambda: build_meme_admin_menu(self))
    def _build_word_admin_menu(self):
        return self._menu("word_admin", lambda: build_word_admin_menu(self))
    def _build_checkin_admin_menu(self):
        return self._menu("checkin_admin", lambda: build_checkin_admin_menu(self))
    def _build_status_admin_menu(self):
        return self._menu("status_admin", lambda: build_status_admin_menu(self))
    def _build_switch_admin_menu(self):
        return self._menu("switch_admin", lambda: build_switch_admin_menu(self))
    def _build_chime_admin_menu(self):
        return self._menu("chime_admin", lambda: build_chime_admin_menu(self))

    def _is_news_admin_menu(self, text: str) -> bool:
        return text in ("读报管理", "/读报管理")

    def _build_news_admin_menu(self):
        return self._menu("news_admin", lambda: build_news_admin_menu(self))

    def _is_member_admin_menu(self, text: str) -> bool:
        return text in ("成员管理", "/成员管理")

    def _is_notice_menu(self, text: str) -> bool:
        return text in ("提示功能", "/提示功能")

    def _build_notice_menu(self):
        return self._menu("notice", lambda: build_notice_menu(self))

    def _is_auth_menu(self, text: str) -> bool:
        """
        授权功能菜单入口。

        ★ 入口名可在后台 `menu_auth_names` 自定义（默认「授权功能」）。
          为了不和**授权指令** `授权 xxx` 打架，这里只认
          「菜单入口名」本身，不做前缀匹配。
        """
        t = str(text or "").strip()
        if not t:
            return False
        names = ["授权系统"]
        try:
            got = self._menu_entry_names("auth")
            if got:
                names = [str(x).strip() for x in got if str(x).strip()] or names
        except Exception:
            pass
        for n2 in names:
            if n2 and (t == n2 or t == f"/{n2}"):
                return True
        return False

    def _build_auth_menu(self, group_id: str = ""):
        return self._menu("auth", lambda: build_auth_menu(self, group_id))

    def _is_auth_sub_menu(self, text: str):
        """返回 menu_id（'auth_cmd' / 'auth_query'）或 None"""
        t = str(text or "").strip()
        if not t:
            return None
        for mid in ("auth_cmd", "auth_query"):
            for n in self._menu_entry_names(mid):
                if n and (t == n or t == f"/{n}"):
                    return mid
        return None

    def _build_auth_sub_menu(self, menu_id: str,
                             group_id: str = ""):
        _M = {
            "auth_cmd": lambda: build_auth_cmd_menu(self, group_id),
            "auth_query": lambda: build_auth_query_menu(self, group_id),
        }
        fn = _M.get(str(menu_id or ""))
        if fn is None:
            return ""
        return self._menu(str(menu_id), fn)

    def _build_member_admin_menu(self):
        return self._menu("member_admin", lambda: build_member_admin_menu(self))
    def _build_must_at_admin_menu(self):
        return self._menu("must_at_admin", lambda: build_must_at_admin_menu(self))
    def _build_super_admin_menu(self):
        return self._menu("super_admin", lambda: build_super_admin_menu(self))

    async def _build_admin_list_text(self, event) -> str:
        lines = [f"{title_bar('管理员列表')}"]
        lines.append("【AstrBot 全局管理员】")
        try:
            if event.is_admin():
                lines.append(f"  · {to_str(event.get_sender_id())}（你）")
            lines.append("  · 具体名单请在 AstrBot 管理面板查看")
        except Exception:
            lines.append("  · （读取失败）")
        lines.append("")
        lines.append("【插件专属管理员】")
        if self.plugin_admins:
            for a in self.plugin_admins:
                lines.append(f"  · {a}")
        else:
            lines.append("  · （无）")
        if self.group_admin_module is not None:
            try:
                info = await self.group_admin_module.get_group_admins(event)
                owner = info.get("owner", "")
                admins = info.get("admins", [])
                lines.append("")
                lines.append("【本群群主】")
                lines.append(f"  · {owner}" if owner else "  · （无）")
                lines.append("")
                lines.append("【本群管理员】")
                if admins:
                    for a in admins:
                        lines.append(f"  · {a}")
                else:
                    lines.append("  · （无）")
                ts = info.get("ts", 0)
                if ts:
                    age = int(time.time() - ts)
                    lines.append("")
                    lines.append(f"（缓存时间：{age} 秒前，输入「{self.cmd_refresh_admin_name}」可刷新）")
            except Exception:
                logger.exception("[apix-meme] 读取群管失败")
                lines.append("")
                lines.append("【本群群主/管理员】读取失败")
        else:
            lines.append("")
            lines.append("（群管识别模块未加载）")
        return "\n".join(lines)

    def _decorating_result_hook():
        """拿到 on_decorating_result 装饰器；没有就返回空装饰器"""
        try:
            deco = getattr(filter, "on_decorating_result", None)
            if deco is not None:
                return deco()
        except Exception:
            logger.warning("[apix-meme] 本版本没有 on_decorating_result 钩子，"
                           "回复尾巴将不可用（不影响其他功能）")

        def _noop(_fn):
            return _fn
        return _noop

    @_decorating_result_hook()
    async def _append_reply_tail(self, event):
        """
        兜底：万一某条回复没有走 `_send_content`（所以没提前拼尾巴），
        在这里补上。

        ★ 正常路径是 `_send_content` **渲染前**就把尾巴拼进文字了
          （这样图片模式里尾巴才会出现在图片内），
          它会打上 `_apix_tail_done`，这里直接跳过、不会重复加。
        """
        try:
            rt = getattr(self, "reply_tail", None)
            if rt is None or not rt.enable:
                return
            if getattr(event, "_apix_tail_done", False):
                return
            result = event.get_result()
            if result is None or not getattr(result, "chain", None):
                return
            if rt.only_group:
                try:
                    if not event.get_group_id():
                        return
                except Exception:
                    pass
            if not rt.is_own_result(result):
                return
            try:
                from .core.reply_tail import chain_has_own_image as _choi
                if _choi(result):
                    logger.info("[reply_tail] 图里已含尾巴，跳过补文本")
                    return
            except Exception:
                pass
            tail = await rt.tail_text(token=id(event))
            if not tail:
                return
            try:
                from astrbot.api.message_components import Plain
                result.chain.append(Plain("\n" + tail))
            except Exception:
                result.message("\n" + tail)
            try:
                event._apix_tail_done = True
            except Exception:
                pass
            logger.info("[reply_tail] 已追加尾巴（兜底路径）")
        except Exception:
            logger.exception("[apix-meme] 追加回复尾巴失败")

    # ═══════════ ★★ 2.1.0 农场种菜（娱乐系统子功能）═══════════
    #   ★ 输出**跟随本群输出模式**（文本模式发文字 / 图片模式发图）——
    #     统一走 `_farm_send()` -> `_send_content()`。
    #   ★ 积分只动签到系统的**钱包** `total_score`（用户要求）。
    #   ★ **等级 = 农场经验**：种下时记下种子的 exp，收获时结算成经验；
    #     不同作物 exp 不同（见 farm.py 的默认物品表）。
    #   ★ 三家店：种子商店 / 农具商店（肥料）/ 工具商店（家畜 + 饲料）；
    #     列表一律**分页**，避免一屏塞太多。
    #   ★ 看门家畜**只有一个作用：降低被偷概率**；看门会累，累了不看守。
    #   ★ 可以偷别人的菜（`偷菜 @某人`）。

    def _is_farm_menu(self, text: str) -> bool:
        """农场子菜单入口（入口名后台可配，支持逗号分隔多个）"""
        return self._menu_entry_hit("farm", text)

    def _build_farm_menu(self):
        return self._menu("farm", lambda: build_farm_menu(self))

    # ★★ 2.1.0：农场的 6 个下级菜单（入口名后台可配：menu_farm_*_names）
    FARM_SUBMENUS = ("farm_plant", "farm_seed", "farm_fert",
                     "farm_livestock", "farm_machine", "farm_steal")

    def _farm_sub_menu(self, text: str):
        """命中了哪个农场下级菜单？没有返回 ""（★ 在动作指令之前判）"""
        for _mid in self.FARM_SUBMENUS:
            if self._menu_entry_hit(_mid, text):
                return _mid
        return ""

    def _build_farm_sub_menu(self, mid: str) -> str:
        return self._menu(mid, lambda: menu_default(self, mid))

    # ★★ 2.1.x：农场**全部指令名配置键** ——
    #   `_farm_rest_ok()` 用它判断「紧挨着的那段是不是另一条农场指令」。
    #   ★ 以后新增农场指令，记得把配置键补到这里。
    FARM_CMD_ATTRS = (
        "cmd_farm_view", "cmd_farm_plant", "cmd_farm_harvest",
        "cmd_farm_harvest_all", "cmd_farm_shop", "cmd_farm_buy_seed",
        "cmd_farm_impl_shop", "cmd_farm_buy_item", "cmd_farm_use_item",
        "cmd_farm_dog", "cmd_farm_buy_dog", "cmd_farm_feed",
        "cmd_farm_buy_feed", "cmd_farm_equip_dog", "cmd_farm_my_animals",
        "cmd_farm_steal", "cmd_farm_steal_log", "cmd_farm_bag",
        "cmd_farm_status", "cmd_farm_land", "cmd_farm_buy_tool",
        "cmd_farm_equip_tool",
    )

    def _farm_names(self, attr: str, dflt) -> list:
        """农场某条指令的全部别名（后台可配，逗号分隔）"""
        return self._split_names(getattr(self, attr, None)) or [dflt]

    def _farm_all_names(self) -> set:
        """农场**全部**指令名 + 7 个菜单入口名 —— 无空格匹配的排除表"""
        out = set()
        for _attr in self.FARM_CMD_ATTRS:
            for _n in self._split_names(getattr(self, _attr, None)):
                out.add(str(_n).lstrip("/").strip())
        for _mid in ("farm",) + tuple(self.FARM_SUBMENUS):
            try:
                for _n in self._menu_entry_names(_mid):
                    out.add(str(_n).lstrip("/").strip())
            except Exception:
                pass
        out.discard("")
        return out

    def _farm_rest_ok(self, rest: str) -> bool:
        """
        ★★ 2.1.x：**紧挨着（没空格）**写的那段，像不像参数？

        用户要求「指令和参数之间无需空格也能触发」，所以
        `种菜番茄` / `土地状态2` / `收获3` / `偷菜@某人` 都要认。

        口径照 `core/path_utils.py` 的 `_looks_like_arg`：
          ① 空的 / 超过 64 字 -> 不算参数
          ② 本身是 / 以 / 以结尾 / 含有**另一条农场指令名**
             -> 不算参数（那是别的指令被截了）
          ③ 其余都算（页码、@某人、作物名、装备名、家畜名…）
        """
        s = str(rest or "").strip()
        if not s or len(s) > 64:
            return False
        _names = self._farm_all_names()
        if s in _names:
            return False
        for _nm in _names:
            if _nm and _nm != s and (s.startswith(_nm)
                                     or _nm.endswith(s)):
                return False
        for _nm in _names:
            if len(_nm) >= 3 and _nm != s and _nm in s:
                return False
        return True

    def _farm_match(self, text: str, attr: str, dflt):
        """
        匹配「指令 [参数]」—— ★★ 2.1.x：**空格可有可无**。

            `土地状态 2` / `土地状态2` / `土地状态:2` / `土地状态：2`
            `种菜 番茄`  / `种菜番茄`
            `收获 3`     / `收获3`
            `偷菜 @某人` / `偷菜@某人`

        返回：None=没命中；""=命中但没带参数；否则=参数原文。
        ★ 长名优先（避免「买种子」吃掉「购买种子」这种前缀冲突）。
        ★ 没有空格时，紧挨着的那段要过一遍 `_farm_rest_ok()`
          （免得 `背包` 把「背包里…」这种闲聊吃掉、别的指令被截成参数）。
        """
        t = str(text or "").strip()
        t = " ".join(t.replace("\u3000", " ").split())
        if not t:
            return None
        for nm in sorted(self._farm_names(attr, dflt), key=len, reverse=True):
            if not nm:
                continue
            for prefix in (nm, f"/{nm}"):
                if t == prefix:
                    return ""
                if not t.startswith(prefix):
                    continue
                rest = t[len(prefix):]
                if not rest:
                    return ""
                # ① 打了分隔符（空格 / 冒号 / 逗号）—— 照旧永远认
                if rest[0] in " \t:：,，":
                    return rest.strip(" ：:，,")
                # ② 紧挨着写（没空格）—— 只有这段「像参数」才认
                if self._farm_rest_ok(rest):
                    return rest.strip()
        return None

    async def _farm_send(self, event, text: str):
        """
        农场的输出出口：**跟随本群输出模式**（文本模式发文字 / 图片模式发图）。

        ★★ 2.1.0（后续调整）：用户要求农场和输出模式同步 ——
          原来是固定图片输出，现在改成跟 `_send_content` 一致，
          本群是文本就发文本、是图片就渲染成图片。

        ★★ 2.1.0：出口统一过一遍 `icon_gap_text()` ——
          行内图标（🔒01. / 🥩营养狗粮 / 🧤破手套…）后面补半角空格，
          否则渲染成图片时 emoji 会压到后面的文字上（用户反馈「图标和文字重合」）。
        """
        try:
            from .core.menu_builder import icon_gap_text as _gap
        except Exception:
            try:
                from core.menu_builder import icon_gap_text as _gap
            except Exception:
                _gap = None
        if _gap is not None and text:
            try:
                text = _gap(text)
            except Exception:
                logger.debug("[farm] 图标间距修正失败，用原文", exc_info=True)
        # ★★ 2.1.0：农场输出**加宽卡片**。
        #   商店一行一件（名称+价格+时间+经验+等级），
        #   默认卡片宽度装不下 -> 会被**折断**（如「Lv.4」
        #   的「4」掉到下一行），看起来一团乱（用户反馈）。
        #   渲染服务上限 900，这里取 780 留余量。
        # ★★ 2.1.0：卡片宽度**按内容自动决定**。
        #   之前对**所有**农场输出都传 max_w=780（本意是给商店
        #   那种长行），结果**菜单也被撑得很宽**、
        #   内容居中两侧留白很大，和其它菜单不一致
        #   （用户反馈）。
        #   现在：先量文本最宽行，**默认卡片装得下就不加宽**（和其它菜单
        #   一致），只有真放不下（如商店一行一件）才加到 780。
        _use_w = 0
        try:
            from .core.menu_builder import _tw as _twf
        except Exception:
            try:
                from core.menu_builder import _tw as _twf
            except Exception:
                _twf = None
        if _twf is not None and text:
            try:
                _need = max((_twf(l, 26) for l in str(text).split("\n")),
                            default=0)
                # 480 = 默认卡片（540）的内宽
                if _need > 480:
                    _use_w = 780
            except Exception:
                logger.debug("[farm] 量文本宽度失败，用默认宽", exc_info=True)
        async for _r in self._send_content(event, text, max_w=_use_w):
            yield _r

    # ── 积分 ──────────────────────────────────────────────────
    def _farm_wallet(self, group_id, uid) -> int:
        """签到系统的钱包余额（`total_score`）"""
        cm = getattr(self, "checkin_module", None)
        if cm is None:
            return 0
        try:
            return int(cm.get_score(group_id, uid) or 0)
        except Exception:
            logger.debug("[farm] 读钱包失败", exc_info=True)
            return 0

    def _farm_spend(self, group_id, uid, amount) -> bool:
        """只扣**钱包**；不够返回 False（不动任何数据）"""
        try:
            need = int(amount or 0)
        except Exception:
            need = 0
        if need <= 0:
            return True
        cm = getattr(self, "checkin_module", None)
        if cm is None:
            return False
        if self._farm_wallet(group_id, uid) < need:
            return False
        cm.add_score(group_id, uid, -need, allow_negative=False)
        return True

    def _farm_earn(self, group_id, uid, amount) -> int:
        """进账（收获 / 偷菜结算）：加分进钱包"""
        try:
            amt = int(amount or 0)
        except Exception:
            amt = 0
        if amt <= 0:
            return self._farm_wallet(group_id, uid)
        cm = getattr(self, "checkin_module", None)
        if cm is None:
            return self._farm_wallet(group_id, uid)
        return int(cm.add_score(group_id, uid, amt, allow_negative=True) or 0)

    def _farm_score_name(self) -> str:
        cm = getattr(self, "checkin_module", None)
        if cm is not None:
            try:
                return str(cm.score_label() or "积分")
            except Exception:
                pass
        return "积分"

    def _farm_nick(self, group_id, uid) -> str:
        cm = getattr(self, "checkin_module", None)
        if cm is not None:
            try:
                nm = cm._nick_of(group_id, str(uid))
                if nm:
                    return str(nm)
            except Exception:
                pass
        return str(uid)

    # ── 小工具 ────────────────────────────────────────────────
    @staticmethod
    def _farm_bar(pct, width: int = 10) -> str:
        """进度条：▓▓▓░░░░░░░ 45%"""
        try:
            p = max(0, min(100, int(pct)))
        except Exception:
            p = 0
        full = int(round(p * width / 100.0))
        return "▓" * full + "░" * (width - full) + f" {p}%"

    @staticmethod
    def _farm_dur(sec) -> str:
        """秒 -> 「1 小时 3 分」/「7 分 50 秒」/「40 秒」"""
        try:
            s = max(0, int(sec))
        except Exception:
            s = 0
        h, r = divmod(s, 3600)
        m, s2 = divmod(r, 60)
        if h:
            return f"{h} 小时 {m} 分"
        if m:
            return f"{m} 分 {s2} 秒"
        return f"{s2} 秒"

    @staticmethod
    @staticmethod
    def _farm_dur_min(minutes) -> str:
        """
        ★★ 2.1.x：分钟的**紧凑写法**（商店列表一行一件用）。

        以前列表里写成「1 小时 30 分」，一件一行时太长会被折行；
        `_farm_dur` 又是「1 小时 3 分」那种长写法，所以这里单开一个：
            · 整小时 -> 「3时」      · 其余 -> 「90分」（90 比 1时30分短得多）
        """
        try:
            m = max(0, int(minutes))
        except Exception:
            m = 0
        if m >= 60 and m % 60 == 0:
            return f"{m // 60}时"
        return f"{m}分"

    @staticmethod
    def _farm_page_of(arg, dflt: int = 1) -> int:
        """参数里的页码（不合法就回第 1 页）"""
        s = str(arg or "").strip()
        if not s:
            return dflt
        try:
            return max(1, int(s))
        except Exception:
            return dflt

    @staticmethod
    def _farm_page_nav(cmd: str, page: int, pages: int) -> list:
        """分页提示（照插件里其它分页菜单的写法）"""
        out = [f"📄 第 {page} / {pages} 页"]
        if page > 1:
            out.append(f"⬅️ 上一页：「{cmd} {page - 1}」")
        if page < pages:
            out.append(f"➡️ 下一页：「{cmd} {page + 1}」")
        return out

    def _farm_slice(self, rows: list, page: int, per: int):
        """分页切片，返回 (本页数据, 页码, 总页数)"""
        rows = list(rows or [])
        per = max(1, int(per or 1))
        pages = max(1, -(-len(rows) // per))
        page = max(1, min(pages, int(page or 1)))
        return rows[(page - 1) * per:page * per], page, pages

    # ★★ 2.1.x：**通用兜底分页**每页正文行数（不分页时无影响）。
    #   要调大/调小只改这一个数；测试里也可以临时改实例属性。
    FARM_TEXT_PAGE_LINES = 18

    def _farm_page_text(self, cmd: str, lines: list, page: int = 1,
                        per: int = 0) -> list:
        """
        ★★ 2.1.x：**通用兜底分页** —— 把一坨农场输出按固定行数切页。

        · 没超一屏（正文行数 <= per）时**原样返回**，输出和以前**完全一样**；
        · 超了就按 per 行切页：第 1 行当标题在每页重复，末尾补翻页提示。
        · 每页行数取 `FARM_TEXT_PAGE_LINES`（类属性，默认 18）。
        """
        rows = list(lines or [])
        if len(rows) <= 1:
            return rows
        per = max(4, int(per or 0)
                  or int(getattr(self, "FARM_TEXT_PAGE_LINES", 18) or 18))
        head, body = rows[0], rows[1:]
        if len(body) <= per:
            return rows
        pages = max(1, -(-len(body) // per))
        p = max(1, min(pages, int(page or 1)))
        out = [head] + body[(p - 1) * per:p * per]
        out.append("")
        out.extend(self._farm_page_nav(cmd, p, pages))
        return out

    @staticmethod
    def _farm_no(i: int) -> str:
        """1 起的圈码（超出范围就用阿拉伯数字）"""
        _idx = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚"
        return _idx[i] if 0 <= i < len(_idx) else str(i + 1)

    # ── 看门家畜（唯一的用处：防偷；会累）────────────────────
    def _farm_guard(self, group_id, uid) -> dict:
        fm = getattr(self, "farm_module", None)
        if fm is None:
            return {}
        try:
            return fm.guard_of_user(group_id, uid) or {}
        except Exception:
            logger.debug("[farm] 读看门家畜失败", exc_info=True)
            return {}

    def _farm_guard_line(self, guard: dict) -> str:
        """看门家畜一行说明（含体力；累了明确写「不看守」）"""
        if not guard or not str(guard.get("name") or "").strip():
            return "🐕 看门：无（被偷概率就是基础的那么多）"
        try:
            en = int(guard.get("energy", 0) or 0)
            emax = int(guard.get("energy_max", 0) or 0)
        except Exception:
            en, emax = 0, 0
        _pct = self._farm_bar(int(en * 100 / emax) if emax else 0, width=8)
        _g = int(guard.get("steal_guard", 0) or 0)
        _head = f"🐕 看门：{guard.get('name')}　体力 {en}/{emax}　{_pct}"
        if guard.get("tired"):
            return _head + "\n😴 它累了，**现在不看守**（防偷失效，喂点饲料）"
        return _head + f"　🛡 防偷 -{_g}%"

    def _farm_prepare(self, group_id, uid):
        """
        农场指令的**统一前置**：
          ① 结算看门家畜的体力（看门会累），并落盘
          ② 按**农场经验**算等级，土地格子数跟上等级（只增不减）
          ③ 有「自动收获」家畜在看门且没累时，把成熟的地自动收掉
       返回 dict（失败时 ok=False）。
        """
        fm = getattr(self, "farm_module", None)
        if fm is None:
            return {"ok": False, "reason": "nomod"}
        now = int(time.time())
        try:
            fm.animals_owned(group_id, uid, now)   # 结算体力（掉血）
            fm.save()
        except Exception:
            logger.debug("[farm] 家畜体力结算失败", exc_info=True)
        u = fm.user(group_id, uid)
        exp = int(u.get("exp", 0) or 0)
        lv = fm.level_of(exp)
        want = fm.land_count_of(exp)
        sync = fm.sync_lands(group_id, uid, want)
        try:
            lv_ev = fm.sync_level(group_id, uid, now)
        except Exception:
            lv_ev = {"new": 0, "level": lv}
        prog = fm.exp_progress(exp)
        guard = self._farm_guard(group_id, uid)
        auto = {"ok": False, "picked": [], "total": 0}
        try:
            auto = fm.auto_harvest(group_id, uid, now)
        except Exception:
            logger.exception("[farm] 自动收获失败")
        if int(auto.get("total") or 0) > 0:
            self._farm_earn(group_id, uid, int(auto["total"]))
        return {"ok": True, "now": now, "level": lv, "want": want,
                "new_lands": int(sync.get("added") or 0),
                "total_lands": int(sync.get("total") or 0),
                "exp": exp, "exp_prog": prog,
                "level_ev": lv_ev,
                "auto": auto, "guard": guard,
                "next_need": fm.next_level_need(exp),
                "max_lands": fm.max_lands, "max_level": fm.max_level,
                "max_animals": fm.max_animals,
                "steal_left": fm.steal_left(group_id, uid, now),
                "steal_limit": fm.steal_daily_limit,
                "steal_ratio": fm.steal_ratio,
                "steal_base": fm.steal_base_rate,
                "steal_bonus": fm.steal_bonus_of(group_id, uid),
                "upgrade_chance": fm.upgrade_chance,
                "bonus_red": fm.bonus_red,
                "bonus_black": fm.bonus_black}

    def _farm_auto_lines(self, pre: dict) -> list:
        """自动收获的提示行（没什么可提示就返回空表）"""
        auto = (pre or {}).get("auto") or {}
        picked = auto.get("picked") or []
        if not picked:
            return []
        out = [f"🐕 看门家畜自动收获了 {len(picked)} 块地："]
        for it in picked:
            out.append(f"　 · {it.get('seed')} ×{fmt_num(it.get('amount'))}")
        out.append(f"　 💰 进账 +{fmt_num(auto.get('total'))}")
        return out

    def _farm_upgrade_lines(self, pre: dict) -> list:
        """升级提示（土地解锁）"""
        out = []
        lv_ev = (pre or {}).get("level_ev") or {}
        if int(lv_ev.get("gained") or 0) > 0:
            out.append(f"🎉 升级啦！现在是 Lv.{lv_ev.get('level')}")
        if pre.get("new_lands"):
            out.append(f"🌱 新解锁 {pre['new_lands']} 块土地")
        return out

    def _farm_exp_line(self, pre: dict) -> str:
        """
        经验 / 进度 —— ★★ 2.1.0：改成**多行**（用户要求换行输出）。

            等级 Lv.5
            　 ✨ 经验：120
            　 📊 进度 ▓▓▓▓░░░░ 46%（120/260）

        ★ 以前是**一行**，而且里面又写了一遍「Lv.N」，
          和外层拼的「👤 等级 Lv.N」**重复显示**。现在去掉了。
        """
        p = (pre or {}).get("exp_prog") or {}
        if p.get("is_max"):
            return f"　 ✨ 经验：{fmt_num(p.get('exp'))}（已满级）"
        _need = p.get("need") or 0
        _pct = int(int(p.get("in_level", 0) or 0) * 100 / _need) if _need else 0
        return ("　 ✨ 经验：" + fmt_num(p.get("exp")) + "\n"
                + "　 📊 进度 " + self._farm_bar(_pct, 8)
                + "（" + fmt_num(p.get("in_level")) + "/"
                + fmt_num(p.get("need")) + "）")

    # ── 「我的农场」（总览：等级 / 经验 / 土地数 / 品质 / 看门 / 被偷）
    #    ★★ 2.1.x：**不再分页** —— 逐块明细已拆到「土地状态」，
    #    这里只剩固定十来行；`page` 参数**留着兼容但不用**。
    def _farm_view_text(self, group_id, uid, page: int = 1) -> str:
        fm = getattr(self, "farm_module", None)
        pre = self._farm_prepare(group_id, uid)
        if not pre.get("ok"):
            return "❌ 农场模块未加载，请查看后台日志"
        now = pre["now"]
        u = fm.user(group_id, uid)
        lands = list(u.get("lands") or [])
        guard = pre.get("guard") or {}
        sn = self._farm_score_name()
        wallet = self._farm_wallet(group_id, uid)
        _animals = fm.animals_owned(group_id, uid, now)

        lines = [f"🌾 农场　{self._farm_nick(group_id, uid)}"]
        lines.append(f"👤 等级 Lv.{pre['level']}")
        lines.append(self._farm_exp_line(pre))
        lines.append(f"💰 {sn}：{fmt_num(wallet)}")
        lines.append(self._farm_guard_line(guard))
        if _animals:
            _tired = sum(1 for a in _animals if a["tired"])
            _extra = f"（😴 {_tired} 只累了）" if _tired else ""
            lines.append(f"🏠 家畜：{len(_animals)} / {pre['max_animals']}"
                         f"{_extra}")
        _nxt = ""
        if int(pre.get("next_need") or 0) > 0:
            _nxt = f"　再 {fmt_num(pre['next_need'])} 经验升 Lv.{pre['level'] + 1}"
        elif pre["level"] >= pre["max_level"]:
            _nxt = "　（已满级）"
        lines.append(f"🌱 土地：{len(lands)} / {pre['max_lands']}{_nxt}")
        _qc = fm.quality_counts(group_id, uid)
        lines.append(f"🏞 品质：🟫普通 {_qc.get('normal', 0)}　"
                     f"🟥红土地 {_qc.get('red', 0)}"
                     f"（+{fm.bonus_red}%产量）　"
                     f"⬛黑土地 {_qc.get('black', 0)}"
                     f"（+{fm.bonus_black}%产量）")
        lines.append(f"🍀 收获机遇：每次收获 {pre.get('upgrade_chance')}% "
                     f"概率给那块地升品质")
        # ★★ 2.1.x：用户要求 —— 「今天偷菜剩余次数」
        #   **挪到「农场状态」**，这里不再重复。
        lines.extend(self._farm_upgrade_lines(pre))
        lines.extend(self._farm_auto_lines(pre))
        _stolen = fm.steal_log(group_id, uid, 3)
        if _stolen:
            lines.append("🚨 最近被偷：")
            for x in _stolen:
                lines.append(f"　 · {x.get('seed')} ×{fmt_num(x.get('amount'))}"
                             f"（{x.get('by')}）")

        # ★★ 2.1.x：**逐块明细整段拆走了** ——
        #   原来的「土地格子（每行 5 格）+ 每块地详情（圈码 + 进度 + 剩余
        #   时间）」现在由独立指令「土地状态」输出（用户要求：
        #   把地块作物状态分离成另一个指令）。
        #   ★ 只拆**逐块明细**；上面那些**汇总**（等级 / 经验 /
        #     土地数量 / 品质统计 / 看门家畜 / 金圆券 / 偷菜）全部保留。
        lines.append("")
        _landcmd = self._farm_names("cmd_farm_land", "土地状态")[0]
        lines.append(f"🧭 每块地有没有种 / 熟了没：发「{_landcmd}」"
                     f"（「{_landcmd} 2」翻页）")

        lines.append("")
        plant = self._farm_names("cmd_farm_plant", "种菜")[0]
        harvest = self._farm_names("cmd_farm_harvest", "收获")[0]
        seedshop = self._farm_names("cmd_farm_shop", "种子商店")[0]
        itemshop = self._farm_names("cmd_farm_impl_shop", "农具商店")[0]
        dogshop = self._farm_names("cmd_farm_dog", "工具商店")[0]
        steal = self._farm_names("cmd_farm_steal", "偷菜")[0]
        myan = self._farm_names("cmd_farm_my_animals", "我的家畜")[0]
        lines.append(f"💡 {plant} 种子名 ／ {harvest} ／ {seedshop} ／ "
                     f"{itemshop} ／ {dogshop}")
        lines.append(f"💡 {steal} @某人 ／ {myan}")
        return "\n".join(lines)

    # ── 「土地状态」（逐块：有没有种 / 种什么 / 熟没熟，分页）────
    def _farm_land_line(self, fm, land, idx: int, now) -> str:
        """
        **一块地一行**（用户要求：一眼能扫完）。

            ① 🟫 空地
            ② 🟥 空地　红土地 +10%
            ③ 🟫 🌱 番茄　▓▓▓▓░░░░ 45%　还需 1 小时 30 分
            ④ ⬛ 🍅 玉米　✅ 已成熟　可收 24 金圆券

        · 第 1 个图标 = **土地品质**（🟫普通 / 🟥红土地 / ⬛黑土地）
        · 第 2 个图标 = **作物状态**（🌱生长中 / 🍅可收获）
        """
        _no = self._farm_no(idx)
        _q = fm.quality_emoji(land)
        _st = fm.state_of(land, now)
        if _st == "empty":
            _qb = fm.quality_bonus(land)
            if _qb:
                return f"{_no} {_q} 空地　{fm.quality_label(land)} +{_qb}%"
            return f"{_no} {_q} 空地"
        _seed = str(land.get("seed") or "") or "（认不出的作物）"
        if _st == "ready":
            _qb = fm.quality_bonus(land)
            _amt = int(land.get("amount") or 0)
            _got = int(_amt * (100 + _qb) / 100)
            return (f"{_no} {_q} 🍅 {_seed}　✅ 已成熟　可收 "
                    f"{fmt_num(_got)} {self._farm_score_name()}")
        return (f"{_no} {_q} 🌱 {_seed}　"
                f"{self._farm_bar(fm.progress(land, now), 8)}"
                f"　还需 {self._farm_dur(fm.left_seconds(land, now))}")

    def _farm_land_text(self, group_id, uid, page: int = 1) -> str:
        """
        「土地状态 [页码]」—— **逐块**列每块地：

            · 第几块地（圈码）
            · 有没有种作物（空地 / 已种）
            · 种的是什么作物
            · 熟了没有（✅已成熟 / 还在长 + 还剩多久）

        ★★ 2.1.x：从「我的农场」拆出来的独立指令（用户要求把
          「地块作物状态」分离出来）。「我的农场」只留汇总，
          逐块明细全部在这里。
        ★ 一行一块地；分页复用 `farm_land_per_page`（默认 12 行一页），
          翻页提示里的指令名取 `cmd_farm_land` 的**第一个名字**。
        """
        fm = getattr(self, "farm_module", None)
        pre = self._farm_prepare(group_id, uid)
        if not pre.get("ok"):
            return "❌ 农场模块未加载，请查看后台日志"
        now = pre["now"]
        lands = list(fm.user(group_id, uid).get("lands") or [])
        _empty = _grow = _ready = 0
        for _land in lands:
            _st = fm.state_of(_land, now)
            if _st == "ready":
                _ready += 1
            elif _st == "empty":
                _empty += 1
            else:
                _grow += 1
        lines = [f"🌾 土地状态　{self._farm_nick(group_id, uid)}"]
        lines.append(f"📊 共 {len(lands)} 块　🟫空地 {_empty}　"
                     f"🌱生长中 {_grow}　🍅可收获 {_ready}")
        lines.append("")
        if not lands:
            lines.append("（还没有土地）")
            return "\n".join(lines)
        # ★ 后台这个键填了非数字也不让指令崩（坏值回默认 12）
        try:
            per = int(getattr(self, "farm_land_per_page", 12) or 12)
        except Exception:
            per = 12
        per = max(1, per)
        _rows, page, pages = self._farm_slice(lands, page, per)
        _base = (page - 1) * per
        for _k, _land in enumerate(_rows):
            lines.append(self._farm_land_line(fm, _land, _base + _k, now))
        if pages > 1:
            _cmd = self._farm_names("cmd_farm_land", "土地状态")[0]
            lines.append("")
            lines.extend(self._farm_page_nav(_cmd, page, pages))
        lines.append("")
        plant = self._farm_names("cmd_farm_plant", "种菜")[0]
        harvest = self._farm_names("cmd_farm_harvest", "收获")[0]
        hall = self._farm_names("cmd_farm_harvest_all", "一键收获")[0]
        view = self._farm_names("cmd_farm_view", "我的农场")[0]
        lines.append(f"💡 {plant} 种子名 [地块] ／ {harvest} [编号] ／ "
                     f"{hall} ／ {view}")
        return "\n".join(lines)

    # ── 种子商店（分页）────────────────────────────────────────
    # ── 种子商店（分页 · ★ 一行一件）────────────────────────────
    # ── 种子商店（分页 · ★ 一行一件）────────────────────────────
    def _farm_seed_shop_text(self, group_id, uid, page: int = 1) -> str:
        """
        「种子商店 [页码]」—— 输出在售种子列表（**一行一件** + 分页）。

        ★★ 2.1.x：以前一件占**两行**（名称价格 + 属性细节），一屏放不下几件；
          用户要求「商店菜单输出物品列表、要短、要分页」——
          现在压成一行：名称 / 价格 / 生长时间 / 经验（等级不够时补行尾等级）。
          ★ 行宽按渲染卡片内宽（540 宽 / 字号 26 -> 约 480px）**实测**过，
            不会折行；属性之间用半角空格，省下的宽度留给内容。
        """
        fm = getattr(self, "farm_module", None)
        if fm is None:
            return "❌ 农场模块未加载，请查看后台日志"
        pre = self._farm_prepare(group_id, uid)
        lv = int(pre.get("level") or 1)
        sn = self._farm_score_name()
        wallet = self._farm_wallet(group_id, uid)
        _all = fm.seeds()
        per = max(1, int(getattr(self, "farm_shop_per_page", 8) or 8))
        rows, page, pages = self._farm_slice(_all, page, per)
        # ★★ 2.1.0：去掉「本群成员的等级 + 钱包余额」
        #   （用户要求）—— 同时把两行合成一行，不易被折断。
        lines = [f"🛒 种子商店　在售 {len(_all)} 种　价格单位：{sn}"]
        lines.append("")
        _base = (page - 1) * per
        for k, s in enumerate(rows):
            i = _base + k + 1
            _lock = "🔓" if int(s["level_req"]) <= 0 else (
                "🔒" if lv < s["level_req"] else "　")
            _lv = ("" if int(s["level_req"]) <= lv
                   else f" Lv.{int(s['level_req'])}")
            lines.append(
                f"{_lock}{i:02d}.{s['name']}　{fmt_num(s['price'])}"
                f" ⏳{self._farm_dur_min(s['grow_minutes'])}"
                f" ✨+{fmt_num(s['exp'])}{_lv}")
        if not rows:
            lines.append("　（后台还没配种子）")
        if pages > 1:
            _cmd = self._farm_names("cmd_farm_shop", "种子商店")[0]
            lines.append("")
            lines.extend(self._farm_page_nav(_cmd, page, pages))
        lines.append("")
        lines.append("🔒 = 等级不够（行尾是需要的等级）")
        lines.append("🔓 = 无等级限制（默认很贵）")
        buy = self._farm_names("cmd_farm_buy_seed", "购买种子")[0]
        plant = self._farm_names("cmd_farm_plant", "种菜")[0]
        lines.append(f"💡 {buy} 种子名（或编号）")
        lines.append(f"💡 {plant} 种子名 [地块]：从背包种下")
        return "\n".join(lines)

    # ── 农具商店（肥料，分页 · ★ 一行一件）──────────────────────
    def _farm_item_shop_text(self, group_id, uid, page: int = 1) -> str:
        """「农具商店 [页码]」—— 输出在售肥料列表（**一行一件** + 分页）"""
        fm = getattr(self, "farm_module", None)
        if fm is None:
            return "❌ 农场模块未加载，请查看后台日志"
        pre = self._farm_prepare(group_id, uid)
        lv = int(pre.get("level") or 1)
        sn = self._farm_score_name()
        wallet = self._farm_wallet(group_id, uid)
        _all = fm.fertilizers()
        per = max(1, int(getattr(self, "farm_shop_per_page", 8) or 8))
        rows, page, pages = self._farm_slice(_all, page, per)
        # ★★ 2.1.0：去掉等级 + 钱包余额，合成一行
        lines = [f"🧪 农具商店　在售 {len(_all)} 种　价格单位：{sn}"]
        lines.append("")
        _base = (page - 1) * per
        for k, it in enumerate(rows):
            i = _base + k + 1
            _lock = "🔓" if int(it["level_req"]) <= 0 else (
                "🔒" if lv < it["level_req"] else "　")
            _lv = ("" if int(it["level_req"]) <= lv
                   else f" Lv.{int(it['level_req'])}")
            _e = []
            if int(it["speed_up"]) >= 100:
                _e.append("⚡熟")
            elif int(it["speed_up"]):
                _e.append(f"-{int(it['speed_up'])}%时")
            if int(it["yield_bonus"]):
                _e.append(f"+{int(it['yield_bonus'])}%产")
            lines.append(
                f"{_lock}{i:02d}.{it['name']}　{fmt_num(it['price'])}"
                f" {'/'.join(_e) if _e else '无效果'}{_lv}")
        if not rows:
            lines.append("　（后台还没配肥料）")
        if pages > 1:
            _cmd = self._farm_names("cmd_farm_impl_shop", "农具商店")[0]
            lines.append("")
            lines.extend(self._farm_page_nav(_cmd, page, pages))
        lines.append("")
        lines.append("🔒 = 等级不够（行尾是需要的等级）")
        lines.append("🔓 = 无等级限制　⚡ = 立刻成熟")
        lines.append("-N%时 = 缩短剩余生长　+N%产 = 增产")
        buy = self._farm_names("cmd_farm_buy_item", "购买肥料")[0]
        use = self._farm_names("cmd_farm_use_item", "施肥")[0]
        lines.append(f"💡 {buy} 肥料名 [地块]：买了立刻用")
        lines.append(f"💡 {use} 肥料名 [地块]：同上，不扣积分")
        lines.append("💡 不写地块 = 挑剩余最长的地")
        return "\n".join(lines)

    # ── 工具商店（家畜 + 饲料 + 农机，同一份货架分页 · 一行一件）──
    def _farm_tool_shop_text(self, group_id, uid, page: int = 1) -> str:
        """「工具商店 [页码]」—— 输出在售家畜 / 饲料 / 农机（**一行一件** + 分页）"""
        fm = getattr(self, "farm_module", None)
        if fm is None:
            return "❌ 农场模块未加载，请查看后台日志"
        pre = self._farm_prepare(group_id, uid)
        lv = int(pre.get("level") or 1)
        sn = self._farm_score_name()
        wallet = self._farm_wallet(group_id, uid)
        _animals = fm.animals_owned(group_id, uid, pre.get("now"))
        _owned = {a["name"]: a for a in _animals}
        equipped = fm.equipped_name(group_id, uid)
        _all = fm.tool_shop()
        _owned_tools = [str(x.get("name"))
                        for x in fm.tools_owned(group_id, uid)]
        _on_tools = [str(x.get("name")) for x in fm.tools_on(group_id, uid)]
        per = max(1, int(getattr(self, "farm_shop_per_page", 8) or 8))
        rows, page, pages = self._farm_slice(_all, page, per)
        # ★★ 2.1.0：去掉「本群成员的等级 + 钱包余额」（用户要求）
        lines = [f"🐕 工具商店　在售 {len(_all)} 种"
                 + (f"　看门：{equipped}" if equipped else "")
                 + (f"　已养家畜 {len(_animals)}/{pre['max_animals']}"
                    if _animals else "")]
        lines.append("")
        _base = (page - 1) * per
        for k, it in enumerate(rows):
            i = _base + k + 1
            _lock = "🔓" if int(it["level_req"]) <= 0 else (
                "🔒" if lv < it["level_req"] else "　")
            _lv = ("" if int(it["level_req"]) <= lv
                   else f" Lv.{int(it['level_req'])}")
            if it["kind"] == "animal":
                _have = _owned.get(it["name"])
                _tag = ""
                if _have:
                    if _have["tired"]:
                        _tag = "　😴累了"
                    elif _have["equipped"]:
                        _tag = "　✅在看门"
                    else:
                        _tag = f"　已养·体力{_have['energy']}"
                lines.append(
                    f"{_lock}{i:02d}.🐕{it['name']}　{fmt_num(it['price'])}"
                    f" 🛡-{int(it.get('steal_guard', 0) or 0)}%{_lv}{_tag}")
            elif it["kind"] == "tool":
                _is_steal = int(it.get("steal_bonus", 0) or 0) > 0
                _ic = "🧤" if _is_steal else "🤖"
                _tag = ""
                if it["name"] in _owned_tools:
                    _tag = ("　✅装备中" if it["name"] in _on_tools
                            else "　已买未装")
                _m = []
                if int(it.get("auto_harvest", 0) or 0):
                    _m.append("收割")
                if int(it.get("auto_plant", 0) or 0):
                    _m.append("播种")
                if _is_steal:
                    _m.append(f"偷菜+{int(it['steal_bonus'])}%")
                lines.append(
                    f"{_lock}{i:02d}.{_ic}{it['name']}　{fmt_num(it['price'])}"
                    f" {'/'.join(_m) if _m else '无功能'}{_lv}{_tag}")
            else:
                lines.append(
                    f"{_lock}{i:02d}.🥩{it['name']}　{fmt_num(it['price'])}"
                    f" 🍖+{int(it.get('energy', 0) or 0)}{_lv}")
        if not rows:
            lines.append("　（后台还没上架）")
        if pages > 1:
            _cmd = self._farm_names("cmd_farm_dog", "工具商店")[0]
            lines.append("")
            lines.extend(self._farm_page_nav(_cmd, page, pages))
        lines.append("")
        lines.append("🔒 = 等级不够（行尾是需要的等级）")
        buy = self._farm_names("cmd_farm_buy_dog", "购买家畜")[0]
        equip = self._farm_names("cmd_farm_equip_dog", "装备家畜")[0]
        buyf = self._farm_names("cmd_farm_buy_feed", "购买饲料")[0]
        feed = self._farm_names("cmd_farm_feed", "喂食")[0]
        buytool = self._farm_names("cmd_farm_buy_tool", "购买工具")[0]
        equiptool = self._farm_names("cmd_farm_equip_tool", "装备工具")[0]
        lines.append(f"💡 {buy} 名　{equip} 名")
        lines.append(f"💡 {buyf} 名　{feed} 名 [家畜名]")
        lines.append(f"💡 {buytool} 名　{equiptool} 名（开关式）")
        lines.append("⚠️ 看门会累：体力归零就不防偷")
        lines.append("🤖 农机每天自动干活一次（静默）")
        return "\n".join(lines)

    # ── 我的家畜（体力一览）────────────────────────────────────
    def _farm_my_animals_text(self, group_id, uid, page: int = 1) -> str:
        fm = getattr(self, "farm_module", None)
        if fm is None:
            return "❌ 农场模块未加载，请查看后台日志"
        pre = self._farm_prepare(group_id, uid)
        _animals = fm.animals_owned(group_id, uid, pre.get("now"))
        lines = [f"🏠 我的家畜　{len(_animals)} / {pre['max_animals']}"]
        lines.append("")
        if not _animals:
            lines.append("（还没有家畜，去工具商店请一只）")
        for i, a in enumerate(_animals):
            _pct = self._farm_bar(
                int(a["energy"] * 100 / a["energy_max"])
                if a["energy_max"] else 0, width=8)
            _tag = ("😴 累了·不看守" if a["tired"]
                    else ("✅ 在看门" if a["equipped"] else "待命"))
            lines.append(f"{self._farm_no(i)} {a['name']}　{_tag}")
            lines.append(f"　 体力 {a['energy']}/{a['energy_max']}　{_pct}")
            _g = int(a.get("steal_guard", 0) or 0)
            _gmax = int(a.get("steal_guard_max", _g) or 0)
            lines.append(f"　 🛡 防偷 -{_gmax}%"
                         + ("　（累了，暂时不生效）" if a["tired"]
                            else ("　✅ 生效中" if a["equipped"]
                                  else "　（待命，不生效）")))
            if not a.get("in_shop"):
                lines.append("　 ⚠️ 这只已从后台下架，防偷失效")
        lines.append("")
        equip = self._farm_names("cmd_farm_equip_dog", "装备家畜")[0]
        buyf = self._farm_names("cmd_farm_buy_feed", "购买饲料")[0]
        feed = self._farm_names("cmd_farm_feed", "喂食")[0]
        lines.append(f"💡 {equip} 家畜名　换看门犬")
        lines.append(f"💡 {buyf} 饲料名 [家畜名]　／ {feed} 饲料名 [家畜名]")
        lines.append("💡 只有**在看门且没累**的那只才防偷")
        return "\n".join(self._farm_page_text(
            self._farm_names("cmd_farm_my_animals", "我的家畜")[0],
            lines, page))

    # ── handler ───────────────────────────────────────────────
    async def _handle_farm_view(self, event, group_id, uid, arg):
        """
        「我的农场」—— ★★ 2.1.x：**不再分页**。

        逐块明细拆到「土地状态」之后，这里的正文是固定十来行
        （等级 / 经验 / 土地数量 / 品质统计 / 看门家畜 / 金圆券 /
        被偷记录），一屏看得完。页码参数**收下但不用**：
        老习惯发「我的农场 2」不报错，只是内容跟第 1 页一样。
        """
        async for _r in self._farm_send(
                event, self._farm_view_text(group_id, uid)):
            yield _r

    async def _handle_farm_land(self, event, group_id, uid, arg):
        """「土地状态 [页码]」—— 逐块看有没有种 / 种什么 / 熟没熟"""
        async for _r in self._farm_send(
                event, self._farm_land_text(group_id, uid,
                                            self._farm_page_of(arg))):
            yield _r

    async def _handle_farm_shop(self, event, group_id, uid, arg):
        """「种子商店 [页码]」"""
        async for _r in self._farm_send(
                event, self._farm_seed_shop_text(group_id, uid,
                                                 self._farm_page_of(arg))):
            yield _r

    async def _handle_farm_item_shop(self, event, group_id, uid, arg):
        """「农具商店 [页码]」= 看肥料"""
        async for _r in self._farm_send(
                event, self._farm_item_shop_text(group_id, uid,
                                                 self._farm_page_of(arg))):
            yield _r

    async def _handle_farm_dog_shop(self, event, group_id, uid, arg):
        """「工具商店 [页码]」= 家畜 + 饲料"""
        async for _r in self._farm_send(
                event, self._farm_tool_shop_text(group_id, uid,
                                                 self._farm_page_of(arg))):
            yield _r

    async def _handle_farm_my_animals(self, event, group_id, uid, arg):
        """「我的家畜 [页码]」"""
        async for _r in self._farm_send(
                event, self._farm_my_animals_text(group_id, uid,
                                                  self._farm_page_of(arg))):
            yield _r

    async def _handle_farm_plant(self, event, group_id, uid, arg):
        """
        「种菜 种子名 [地块编号]」—— 从**背包**拿种子种下。

        · 不带地块编号 -> 按顺序种在**第一块空地**
        · 带地块编号   -> 就种那一块（`种菜 番茄 3`）
        """
        fm = getattr(self, "farm_module", None)
        if fm is None:
            async for _r in self._farm_send(
                    event, "❌ 农场模块未加载，请查看后台日志"):
                yield _r
            return
        pre = self._farm_prepare(group_id, uid)
        lv = int(pre.get("level") or 1)
        sn = self._farm_score_name()
        _bagseeds = fm.bag_seeds(group_id, uid)
        # ★ 参数拆成「种子 / 地块编号」：`种菜 番茄 3`、`种菜 番茄3`、`种菜 3`
        _skey, _land = self._farm_split_arg(arg)
        seed = fm.find_seed(_skey) if str(_skey or "").strip() else {}
        _plant = self._farm_names("cmd_farm_plant", "种菜")[0]
        _buy = self._farm_names("cmd_farm_buy_seed", "购买种子")[0]
        _bagcmd = self._farm_names("cmd_farm_bag", "我的背包")[0]
        # ① 参数为空 / 认不出：列背包里能种的
        if not seed:
            if not _bagseeds:
                async for _r in self._farm_send(event, "\n".join([
                        "🎒 背包里没有种子",
                        f"💡 先去「"
                        f"{self._farm_names('cmd_farm_shop', '种子商店')[0]}」"
                        f"用「{_buy} 种子名」买",
                ])):
                    yield _r
                return
            lines = [f"⚠️ {'请写清种什么' if not str(arg or '').strip() else '没认出这个种子'}",
                     "", "🎒 背包里的种子："]
            for i, x in enumerate(_bagseeds, 1):
                _lock = "🔒" if not fm.level_ok(x["seed"], lv) else "　"
                lines.append(f"{_lock}{i}. {x['name']} ×{x['count']}"
                             f"　需 Lv.{x['seed']['level_req']}"
                             if int(x["seed"]["level_req"]) > 0
                             else f"{_lock}{i}. {x['name']} ×{x['count']}"
                                  f"　无等级限制")
            lines.append("")
            lines.append(f"💡 用法：{_plant} 种子名 [地块编号]　📋 {_bagcmd}")
            lines.append("💡 不写地块编号 = 按顺序种第一块空地")
            async for _r in self._farm_send(event, "\n".join(lines)):
                yield _r
            return
        # ② 背包里没有这种种子
        if fm.bag_count(group_id, uid, seed["name"]) <= 0:
            async for _r in self._farm_send(event, "\n".join([
                    f"🎒 背包里没有「{seed['name']}」",
                    f"💡 先买：{_buy} {seed['name']}",
                    f"💡 看看有什么：{_bagcmd}",
            ])):
                yield _r
            return
        # ③ 等级
        if not fm.level_ok(seed, lv):
            async for _r in self._farm_send(event, "\n".join([
                    f"🔒 等级不够：{seed['name']} 需要 Lv.{seed['level_req']}",
                    f"👤 你当前 Lv.{lv}　"
                    f"（{fmt_num(pre.get('exp'))} 经验）",
                    f"💡 还差 {fmt_num(pre.get('next_need'))} 经验升级"
                    f"（种菜收获就有经验）",
            ])):
                yield _r
            return
        # ④ 种下（先占好背包里的那一份，失败再还回去）
        if not fm.bag_take(group_id, uid, seed["name"], 1):
            async for _r in self._farm_send(
                    event, f"🎒 背包里没有「{seed['name']}」了"):
                yield _r
            return
        r = fm.plant(group_id, uid, seed, index=_land)
        _al = self._farm_auto_lines({"auto": r.get("auto") or {}})
        if not r.get("ok"):
            fm.bag_add(group_id, uid, seed["name"], 1)
            _why = str(r.get("reason") or "")
            if _why == "range":
                _msg = f"⚠️ 没有第 {(_land or 0) + 1} 块地"
            elif _why == "occupied":
                _msg = (f"🚧 第 {(_land or 0) + 1} 块地已经种着东西了\n"
                        f"💡 想种空地就别写编号（按顺序自动找空地）")
            else:
                _msg = (f"🚧 土地满了（{pre.get('total_lands', 0)} / "
                        f"{pre.get('max_lands', 30)} 块都在种）\n"
                        f"💡 先「"
                        f"{self._farm_names('cmd_farm_harvest_all', '一键收获')[0]}」"
                        f"把成熟的收掉\n"
                        f"💡 升级可解锁更多土地（每级 +1，最多 "
                        f"{pre.get('max_lands', 30)} 块）")
            async for _r in self._farm_send(event, "\n".join(
                    [_msg, "（种子已退回背包）"] + _al)):
                yield _r
            return
        fm.note(group_id, uid, "planted", 1)
        _left = fm.bag_count(group_id, uid, seed["name"])
        _where = (f"第 {r['index'] + 1} 块（{self._farm_no(r['index'])}）"
                  + ("　★ 指定地块" if _land is not None else "　（按顺序）"))
        async for _r in self._farm_send(event, "\n".join(
                ["🌱 种下啦！"]
                + _al
                + [f"🌾 种子：{seed['name']}（背包还剩 {_left} 个）",
                   f"📍 土地：{_where}",
                   f"⏳ 生长：{self._farm_dur(r['grow_sec'])}",
                   f"📈 预计收获：{fmt_num(r.get('amount'))} {sn}"
                   f"　✨ +{fmt_num(seed['exp'])} 经验",
                   "",
                   f"💡 到点了发「"
                   f"{self._farm_names('cmd_farm_harvest_all', '一键收获')[0]}」"])):
            yield _r

    async def _handle_farm_harvest(self, event, group_id, uid, arg,
                                   bulk: bool = False):
        """
        「收获 [编号]」= 收全部 / 只收一块；
        「一键收获」（bulk=True）= 把**所有**成熟的收完并**直接卖掉**（积分立刻到手）。
        """
        fm = getattr(self, "farm_module", None)
        if fm is None:
            async for _r in self._farm_send(
                    event, "❌ 农场模块未加载，请查看后台日志"):
                yield _r
            return
        pre = self._farm_prepare(group_id, uid)
        sn = self._farm_score_name()
        _arg = "" if bulk else str(arg or "").strip()
        index = None
        if _arg:
            if not _arg.isdigit():
                async for _r in self._farm_send(event, "\n".join([
                        "⚠️ 编号要写数字（1 起）",
                        f"💡 用法："
                        f"{self._farm_names('cmd_farm_harvest', '收获')[0]} 3",
                ])):
                    yield _r
                return
            index = int(_arg) - 1
        _al = self._farm_auto_lines(pre)
        r = fm.harvest(group_id, uid, index=index)
        picked = r.get("picked") or []
        if not picked:
            if index is not None:
                _u = fm.user(group_id, uid)
                if not (0 <= index < len(_u.get("lands") or [])):
                    _msg = f"⚠️ 没有第 {index + 1} 块地"
                else:
                    _st = fm.state_of((_u["lands"])[index], pre.get("now"))
                    _msg = ("🟫 第 %d 块是空地，没东西可收" % (index + 1)
                            if _st == "empty"
                            else "🌱 第 %d 块还没熟，再等等" % (index + 1))
            else:
                _msg = ("🚧 现在没有成熟的作物可卖"
                        if bulk else "🚧 现在没有可收获的土地")
            async for _r in self._farm_send(event, "\n".join(
                    [_msg] + _al
                    + [f"💡 发「"
                       f"{self._farm_names('cmd_farm_view', '我的农场')[0]}」"
                       "看进度"])):
                yield _r
            return
        self._farm_earn(group_id, uid, int(r.get("total") or 0))
        # ★ 记今日统计（「农场状态」用）
        try:
            fm.note(group_id, uid, "harvest_times", 1, pre.get("now"))
            fm.note(group_id, uid, "harvest_crops", len(picked),
                    pre.get("now"))
            fm.note(group_id, uid, "earned", int(r.get("total") or 0),
                    pre.get("now"))
            fm.save()
        except Exception:
            logger.debug("[farm] 记今日收获统计失败", exc_info=True)
        lines = ["🧺 一键收获（全部收完并卖掉）！" if bulk else "🧺 收获完成！"]
        lines.extend(_al)
        lines.append(f"✅ 收了 {len(picked)} 块地：")
        for it in picked:
            _qb = int(it.get("quality_bonus") or 0)
            lines.append(f"　 · {it.get('seed')} ×{fmt_num(it.get('amount'))}"
                         f"　+{fmt_num(it.get('exp'))} 经验"
                         + (f"　🏞 {it.get('quality_label')} +{_qb}%"
                            if _qb else ""))
        lines.append(f"💰 卖掉了，合计 +{fmt_num(r.get('total'))} {sn}")
        lines.append(f"💳 钱包余额：{fmt_num(self._farm_wallet(group_id, uid))}")
        # ★ 收获机遇：土地品质升级
        for u2 in (r.get("upgrades") or []):
            lines.append(f"🍀 触发收获机遇！第 {u2.get('index', 0) + 1} 块地 "
                         f"{u2.get('label_before')} → "
                         f"**{u2.get('label_after')}**"
                         f"（以后这块地 +{u2.get('bonus')}% 产量）")
        _up = r.get("exp") or {}
        if int(_up.get("gain") or 0) > 0:
            lines.append(f"✨ 经验 +{fmt_num(_up.get('gain'))}"
                         f"（共 {fmt_num(_up.get('exp'))}）")
        if _up.get("level_up"):
            lines.append(f"🎉 升级啦！Lv.{_up.get('from_level')} → "
                         f"Lv.{_up.get('level')}")
            if int(_up.get("new_lands") or 0) > 0:
                lines.append(f"🌱 新解锁 {_up['new_lands']} 块土地")
        _prog = fm.exp_progress(fm.user(group_id, uid).get("exp"))
        if not _prog.get("is_max"):
            lines.append(f"📊 距离 Lv.{_prog['level'] + 1} 还差 "
                         f"{fmt_num(_prog['need'] - _prog['in_level'])} 经验")
        if int(r.get("ready_left") or 0) > 0:
            lines.append(f"💡 还有 {r['ready_left']} 块地熟着，"
                         f"发「{self._farm_names('cmd_farm_harvest', '收获')[0]}」"
                         "一起收")
        async for _r in self._farm_send(event, "\n".join(lines)):
            yield _r

    async def _handle_farm_harvest_all(self, event, group_id, uid, arg):
        """「一键收获」= 所有成熟的一次收完并**直接卖掉**（积分立刻到手）"""
        async for _r in self._handle_farm_harvest(
                event, group_id, uid, "", bulk=True):
            yield _r

    async def _handle_farm_buy_seed(self, event, group_id, uid, arg):
        """「购买种子 名字/编号」—— 扣钱后放进**背包**（要种再发「种菜」）"""
        fm = getattr(self, "farm_module", None)
        if fm is None:
            async for _r in self._farm_send(
                    event, "❌ 农场模块未加载，请查看后台日志"):
                yield _r
            return
        seed = fm.find_seed(arg)
        if not seed:
            async for _r in self._farm_send(event, "\n".join([
                    "⚠️ 没认出这个种子",
                    f"💡 用法："
                    f"{self._farm_names('cmd_farm_buy_seed', '购买种子')[0]}"
                    f" 种子名（或编号）",
                    f"💡 看商店："
                    f"{self._farm_names('cmd_farm_shop', '种子商店')[0]}",
            ])):
                yield _r
            return
        pre = self._farm_prepare(group_id, uid)
        lv = int(pre.get("level") or 1)
        sn = self._farm_score_name()
        if not fm.level_ok(seed, lv):
            async for _r in self._farm_send(event, "\n".join([
                    f"🔒 等级不够：{seed['name']} 需要 Lv.{seed['level_req']}",
                    f"👤 你当前 Lv.{lv}　"
                    f"（{fmt_num(pre.get('exp'))} 经验，还差 "
                    f"{fmt_num(pre.get('next_need'))} 升级）",
            ])):
                yield _r
            return
        price = int(seed["price"])
        if self._farm_wallet(group_id, uid) < price:
            async for _r in self._farm_send(event, "\n".join([
                    f"💸 {sn}不够：{seed['name']} 要 {fmt_num(price)}，"
                    f"你只有 {fmt_num(self._farm_wallet(group_id, uid))}",
                    "💡 去签到/打工/收获攒一攒",
            ])):
                yield _r
            return
        self._farm_spend(group_id, uid, price)
        _n = fm.bag_add(group_id, uid, seed["name"], 1)
        _plant = self._farm_names("cmd_farm_plant", "种菜")[0]
        _bagcmd = self._farm_names("cmd_farm_bag", "我的背包")[0]
        async for _r in self._farm_send(event, "\n".join([
                "🛒 购买成功，已放进背包",
                f"🌾 种子：{seed['name']} ×1（背包共 {_n} 个）",
                f"⏳ 生长：{self._farm_dur(seed['grow_minutes'] * 60)}"
                f"　📈 产量 {fmt_num(seed['yield_min'])}~"
                f"{fmt_num(seed['yield_max'])}"
                f"　✨ +{fmt_num(seed['exp'])} 经验",
                f"💸 花费：-{fmt_num(price)}　"
                f"💰 余额：{fmt_num(self._farm_wallet(group_id, uid))}",
                "",
                f"💡 种下去：{_plant} {seed['name']}",
                f"💡 看背包：{_bagcmd}"])):
            yield _r

    # ── 肥料 ──────────────────────────────────────────────────
    async def _farm_apply_item(self, event, group_id, uid, item, index,
                               pay: bool):
        """
        用一份肥料的公共实现（`施肥` 与 `购买肥料` 共用）。

        pay=True  -> 先扣钱再用（购买肥料）
        pay=False -> 直接用（施肥，不扣积分）
        """
        fm = getattr(self, "farm_module", None)
        pre = self._farm_prepare(group_id, uid)
        lv = int(pre.get("level") or 1)
        sn = self._farm_score_name()
        if not fm.level_ok(item, lv):
            async for _r in self._farm_send(event, "\n".join([
                    f"🔒 等级不够：{item['name']} 需要 Lv.{item['level_req']}",
                    f"👤 你当前 Lv.{lv}　（{fmt_num(pre.get('exp'))} 经验）",
            ])):
                yield _r
            return
        price = int(item["price"])
        if pay and self._farm_wallet(group_id, uid) < price:
            async for _r in self._farm_send(event, "\n".join([
                    f"💸 {sn}不够：{item['name']} 要 {fmt_num(price)}，"
                    f"你只有 {fmt_num(self._farm_wallet(group_id, uid))}",
                    "💡 去签到/打工/收获攒一攒",
            ])):
                yield _r
            return
        r = fm.use_item(group_id, uid, item, index=index)
        if not r.get("ok"):
            _why = {
                "empty": "🚧 一块地都没种，施肥没用",
                "none": "🌱 现在没有正在生长的作物（成熟了要先收）",
                "bad": "⚠️ 地块编号要写数字（1 起）",
                "range": "⚠️ 没有这块地",
                "empty_land": "🟫 这块地是空的，先种点东西",
                "already": "🍅 这块地已经成熟了，直接收吧",
            }.get(r.get("reason"), "🚧 施肥失败")
            async for _r in self._farm_send(event, "\n".join([
                    _why,
                    f"💡 发「"
                    f"{self._farm_names('cmd_farm_view', '我的农场')[0]}」"
                    "看土地",
            ])):
                yield _r
            return
        if pay:
            self._farm_spend(group_id, uid, price)
        lines = [f"🧪 施肥成功：{item['name']}"]
        lines.append(f"📍 第 {r['index'] + 1} 块"
                     f"（{self._farm_no(r['index'])}）· {r.get('seed')}")
        if r.get("matured"):
            lines.append("⚡ 直接熟透了！可以收")
        else:
            lines.append(f"⏳ 剩余生长：{self._farm_dur(r.get('before'))}"
                         f" → {self._farm_dur(r.get('after'))}"
                         f"（省了 {self._farm_dur(r.get('saved_sec'))}）")
        if int(r.get("yield_bonus") or 0):
            lines.append(f"📈 这块地产量 +{r['yield_bonus']}%")
        if pay:
            lines.append(f"💸 花费：-{fmt_num(price)}　"
                         f"💰 余额：{fmt_num(self._farm_wallet(group_id, uid))}")
        harvest = self._farm_names("cmd_farm_harvest", "收获")[0]
        if r.get("matured"):
            lines.append(f"💡 发「{harvest}」收掉")
        async for _r in self._farm_send(event, "\n".join(lines)):
            yield _r

    async def _handle_farm_buy_item(self, event, group_id, uid, arg):
        """「购买肥料 肥料名 [地块编号]」—— 扣钱并立刻施肥"""
        fm = getattr(self, "farm_module", None)
        if fm is None:
            async for _r in self._farm_send(
                    event, "❌ 农场模块未加载，请查看后台日志"):
                yield _r
            return
        _name, _idx = self._farm_split_arg(arg)
        item = fm.find_fert(_name)
        if not item:
            async for _r in self._farm_send(event, "\n".join([
                    "⚠️ 没认出这种肥料",
                    f"💡 用法："
                    f"{self._farm_names('cmd_farm_buy_item', '购买肥料')[0]}"
                    f" 肥料名 [地块编号]",
                    f"💡 看商店："
                    f"{self._farm_names('cmd_farm_impl_shop', '农具商店')[0]}",
            ])):
                yield _r
            return
        async for _r in self._farm_apply_item(
                event, group_id, uid, item, _idx, pay=True):
            yield _r

    async def _handle_farm_use_item(self, event, group_id, uid, arg):
        """「施肥 肥料名 [地块编号]」—— 直接用（不扣积分）"""
        fm = getattr(self, "farm_module", None)
        if fm is None:
            async for _r in self._farm_send(
                    event, "❌ 农场模块未加载，请查看后台日志"):
                yield _r
            return
        _name, _idx = self._farm_split_arg(arg)
        if not _name:
            async for _r in self._farm_send(event, "\n".join([
                    "⚠️ 请写清用哪种肥料",
                    f"💡 用法："
                    f"{self._farm_names('cmd_farm_use_item', '施肥')[0]}"
                    f" 肥料名 [地块编号]",
                    f"💡 看商店："
                    f"{self._farm_names('cmd_farm_impl_shop', '农具商店')[0]}",
            ])):
                yield _r
            return
        item = fm.find_fert(_name)
        if not item:
            async for _r in self._farm_send(event, "\n".join([
                    "⚠️ 没认出这种肥料",
                    f"💡 看商店："
                    f"{self._farm_names('cmd_farm_impl_shop', '农具商店')[0]}",
            ])):
                yield _r
            return
        async for _r in self._farm_apply_item(
                event, group_id, uid, item, _idx, pay=False):
            yield _r

    # ── 家畜 ──────────────────────────────────────────────────
    async def _handle_farm_buy_dog(self, event, group_id, uid, arg):
        """「购买家畜 名字/编号」—— 从工具商店买家畜（可买多只）"""
        fm = getattr(self, "farm_module", None)
        if fm is None:
            async for _r in self._farm_send(
                    event, "❌ 农场模块未加载，请查看后台日志"):
                yield _r
            return
        item = fm.find_tool(arg)
        if item and item.get("kind") != "animal":
            _k = str(item.get("kind"))
            _tip = (f"⚠️「{item['name']}」是**饲料**，不是家畜\n"
                    f"💡 买饲料请用："
                    f"{self._farm_names('cmd_farm_buy_feed', '购买饲料')[0]}"
                    f" {item['name']}") if _k == "feed" else (
                    f"⚠️「{item['name']}」是**农机**，不是家畜\n"
                    f"💡 买农机请用："
                    f"{self._farm_names('cmd_farm_buy_tool', '购买工具')[0]}"
                    f" {item['name']}")
            async for _r in self._farm_send(event, "\n".join([
                    _tip,
            ])):
                yield _r
            return
        if not item:
            async for _r in self._farm_send(event, "\n".join([
                    "⚠️ 没认出这只家畜",
                    f"💡 用法："
                    f"{self._farm_names('cmd_farm_buy_dog', '购买家畜')[0]}"
                    f" 家畜名（或编号）",
                    f"💡 看商店："
                    f"{self._farm_names('cmd_farm_dog', '工具商店')[0]}",
            ])):
                yield _r
            return
        pre = self._farm_prepare(group_id, uid)
        lv = int(pre.get("level") or 1)
        sn = self._farm_score_name()
        if not fm.level_ok(item, lv):
            async for _r in self._farm_send(event, "\n".join([
                    f"🔒 等级不够：{item['name']} 需要 Lv.{item['level_req']}",
                    f"👤 你当前 Lv.{lv}　"
                    f"（{fmt_num(pre.get('exp'))} 经验，还差 "
                    f"{fmt_num(pre.get('next_need'))} 升级）",
            ])):
                yield _r
            return
        _owned = fm.animals_owned(group_id, uid, pre.get("now"))
        if any(a["name"] == item["name"] for a in _owned):
            async for _r in self._farm_send(event, "\n".join([
                    f"🐕 {item['name']} 你家已经养了一只了",
                    "💡 同一种只要一只就够（可以换别的家畜）",
                    f"💡 家畜上限：{len(_owned)} / {pre['max_animals']}",
            ])):
                yield _r
            return
        if len(_owned) >= int(pre["max_animals"]):
            async for _r in self._farm_send(event, "\n".join([
                    f"🚧 家畜满了：{len(_owned)} / {pre['max_animals']}",
                    "💡 上限可在后台「农场功能 → 家畜上限」调",
                    f"💡 发「"
                    f"{self._farm_names('cmd_farm_my_animals', '我的家畜')[0]}」"
                    "看已养的家畜",
            ])):
                yield _r
            return
        price = int(item["price"])
        if self._farm_wallet(group_id, uid) < price:
            async for _r in self._farm_send(event, "\n".join([
                    f"💸 {sn}不够：{item['name']} 要 {fmt_num(price)}，"
                    f"你只有 {fmt_num(self._farm_wallet(group_id, uid))}",
                    "💡 去签到/打工/收获攒一攒",
            ])):
                yield _r
            return
        r = fm.add_animal(group_id, uid, item)
        if not r.get("ok"):
            async for _r in self._farm_send(
                    event, "🚧 买不了（家畜满了或已养过这只）"):
                yield _r
            return
        self._farm_spend(group_id, uid, price)
        lines = ["🐕 买回家畜！", f"🏷 {item['name']}"]
        lines.append(f"🛡 效果：防偷 -{item['steal_guard']}%"
                     "（看门家畜的**唯一**作用）")
        lines.append(f"🏠 家畜：{r['count']} / {r['max']}")
        if str(r.get("equipped") or "") == item["name"]:
            lines.append(f"✅ 已自动装备为看门犬（体力 {fm.energy_max}）")
        else:
            equip = self._farm_names("cmd_farm_equip_dog", "装备家畜")[0]
            lines.append(f"💡 想让它看门：发「{equip} {item['name']}」")
        lines.append(f"💸 花费：-{fmt_num(price)}　"
                     f"💰 余额：{fmt_num(self._farm_wallet(group_id, uid))}")
        lines.append("⚠️ 看门会累，体力归零就不看守了（喂饲料恢复）")
        async for _r in self._farm_send(event, "\n".join(lines)):
            yield _r

    async def _handle_farm_equip_dog(self, event, group_id, uid, arg):
        """「装备家畜 名字」—— 换上看门犬"""
        fm = getattr(self, "farm_module", None)
        if fm is None:
            async for _r in self._farm_send(
                    event, "❌ 农场模块未加载，请查看后台日志"):
                yield _r
            return
        pre = self._farm_prepare(group_id, uid)
        _owned = fm.animals_owned(group_id, uid, pre.get("now"))
        if not _owned:
            async for _r in self._farm_send(event, "\n".join([
                    "🏠 你还没有家畜",
                    f"💡 去工具商店请一只："
                    f"{self._farm_names('cmd_farm_dog', '工具商店')[0]}",
            ])):
                yield _r
            return
        name = str(arg or "").strip()
        if not name:
            lines = ["⚠️ 要装备哪一只？", ""]
            for i, a in enumerate(_owned):
                _tag = ("😴 累了" if a["tired"]
                        else ("✅ 在看门" if a["equipped"] else "待命"))
                lines.append(f"{self._farm_no(i)} {a['name']}　{_tag}"
                             f"　体力 {a['energy']}/{a['energy_max']}")
            equip = self._farm_names("cmd_farm_equip_dog", "装备家畜")[0]
            lines.append("")
            lines.append(f"💡 用法：{equip} 家畜名（或编号）")
            async for _r in self._farm_send(event, "\n".join(lines)):
                yield _r
            return
        _key = name
        if name.isdigit() and 1 <= int(name) <= len(_owned):
            _key = _owned[int(name) - 1]["name"]
        r = fm.set_equipped(group_id, uid, _key)
        if not r.get("ok"):
            async for _r in self._farm_send(event, "\n".join([
                    f"⚠️ 你家里没有「{name}」这只家畜",
                    f"💡 发「"
                    f"{self._farm_names('cmd_farm_my_animals', '我的家畜')[0]}」"
                    "看已养的家畜",
            ])):
                yield _r
            return
        lines = [f"✅ 已换上看门犬：{r['name']}"]
        if r.get("old") and r["old"] != r["name"]:
            lines.append(f"（原来的 {r['old']} 卸下待命，体力冻结不掉）")
        lines.append(self._farm_guard_line(fm.guard_of_user(group_id, uid)))
        if r.get("tired"):
            lines.append("⚠️ 它现在**体力为 0，不会看守**（防偷失效），"
                         "先喂点饲料")
        async for _r in self._farm_send(event, "\n".join(lines)):
            yield _r

    async def _handle_farm_buy_feed(self, event, group_id, uid, arg):
        """「购买饲料 饲料名」—— 扣钱后放进**背包**（要喂再发「喂食」）"""
        fm = getattr(self, "farm_module", None)
        if fm is None:
            async for _r in self._farm_send(
                    event, "❌ 农场模块未加载，请查看后台日志"):
                yield _r
            return
        _name, _who = self._farm_split2(arg)
        item = fm.find_feed(_name)
        if not item:
            _hit = fm.find_tool(_name)
            _tip = (f"⚠️「{_name}」是**家畜**，不是饲料\n"
                    f"💡 买家畜请用："
                    f"{self._farm_names('cmd_farm_buy_dog', '购买家畜')[0]}"
                    f" {_name}") if (_hit and _hit.get("kind") == "animal") \
                else "⚠️ 没认出这种饲料"
            async for _r in self._farm_send(event, "\n".join([
                    _tip,
                    f"💡 用法："
                    f"{self._farm_names('cmd_farm_buy_feed', '购买饲料')[0]}"
                    f" 饲料名",
                    f"💡 看商店："
                    f"{self._farm_names('cmd_farm_dog', '工具商店')[0]}",
            ])):
                yield _r
            return
        pre = self._farm_prepare(group_id, uid)
        lv = int(pre.get("level") or 1)
        sn = self._farm_score_name()
        if not fm.level_ok(item, lv):
            async for _r in self._farm_send(event, "\n".join([
                    f"🔒 等级不够：{item['name']} 需要 Lv.{item['level_req']}",
                    f"👤 你当前 Lv.{lv}　（{fmt_num(pre.get('exp'))} 经验）",
            ])):
                yield _r
            return
        price = int(item["price"])
        if self._farm_wallet(group_id, uid) < price:
            async for _r in self._farm_send(event, "\n".join([
                    f"💸 {sn}不够：{item['name']} 要 {fmt_num(price)}，"
                    f"你只有 {fmt_num(self._farm_wallet(group_id, uid))}",
                    "💡 去签到/打工/收获攒一攒",
            ])):
                yield _r
            return
        self._farm_spend(group_id, uid, price)
        _n = fm.bag_add(group_id, uid, item["name"], 1)
        _feed = self._farm_names("cmd_farm_feed", "喂食")[0]
        _bagcmd = self._farm_names("cmd_farm_bag", "我的背包")[0]
        async for _r in self._farm_send(event, "\n".join([
                "🛒 购买成功，已放进背包",
                f"🥩 饲料：{item['name']} ×1（背包共 {_n} 个）",
                f"🍖 恢复体力 +{item['energy']} / 个",
                f"💸 花费：-{fmt_num(price)}　"
                f"💰 余额：{fmt_num(self._farm_wallet(group_id, uid))}",
                "",
                f"💡 喂家畜：{_feed} {item['name']} [家畜名]",
                f"💡 背包饲料共 {fm.bag_feed_total(group_id, uid)} 份"
                f"　📋 {_bagcmd}"])):
            yield _r

    async def _handle_farm_feed(self, event, group_id, uid, arg):
        """「喂食 饲料名 [家畜名]」—— 直接用饲料（不扣积分）"""
        fm = getattr(self, "farm_module", None)
        if fm is None:
            async for _r in self._farm_send(
                    event, "❌ 农场模块未加载，请查看后台日志"):
                yield _r
            return
        _name, _who = self._farm_split2(arg)
        if not _name:
            async for _r in self._farm_send(event, "\n".join([
                    "⚠️ 请写清喂哪种饲料",
                    f"💡 用法："
                    f"{self._farm_names('cmd_farm_feed', '喂食')[0]}"
                    f" 饲料名 [家畜名]",
                    f"💡 看商店："
                    f"{self._farm_names('cmd_farm_dog', '工具商店')[0]}",
            ])):
                yield _r
            return
        item = fm.find_feed(_name)
        if not item:
            async for _r in self._farm_send(event, "\n".join([
                    "⚠️ 没认出这种饲料",
                    f"💡 看商店："
                    f"{self._farm_names('cmd_farm_dog', '工具商店')[0]}",
            ])):
                yield _r
            return
        async for _r in self._farm_feed_flow(
                event, group_id, uid, item, _who, pay=False):
            yield _r

    async def _farm_feed_flow(self, event, group_id, uid, item, who,
                              pay: bool):
        """喂饲料的公共实现（购买饲料 / 喂食 共用）"""
        fm = getattr(self, "farm_module", None)
        pre = self._farm_prepare(group_id, uid)
        lv = int(pre.get("level") or 1)
        sn = self._farm_score_name()
        _owned = fm.animals_owned(group_id, uid, pre.get("now"))
        if not _owned:
            async for _r in self._farm_send(event, "\n".join([
                    "🏠 你还没有家畜，喂了也没人吃",
                    f"💡 去工具商店请一只："
                    f"{self._farm_names('cmd_farm_dog', '工具商店')[0]}",
            ])):
                yield _r
            return
        if not fm.level_ok(item, lv):
            async for _r in self._farm_send(event, "\n".join([
                    f"🔒 等级不够：{item['name']} 需要 Lv.{item['level_req']}",
                    f"👤 你当前 Lv.{lv}　（{fmt_num(pre.get('exp'))} 经验）",
            ])):
                yield _r
            return
        price = int(item["price"])
        if pay and self._farm_wallet(group_id, uid) < price:
            async for _r in self._farm_send(event, "\n".join([
                    f"💸 {sn}不够：{item['name']} 要 {fmt_num(price)}，"
                    f"你只有 {fmt_num(self._farm_wallet(group_id, uid))}",
                    "💡 去签到/打工/收获攒一攒",
            ])):
                yield _r
            return
        r = fm.feed_animal(group_id, uid, item, name=who)
        if not r.get("ok"):
            async for _r in self._farm_send(event, "\n".join([
                    f"⚠️ 你家里没有「{who}」这只家畜",
                    f"💡 发「"
                    f"{self._farm_names('cmd_farm_my_animals', '我的家畜')[0]}」"
                    "看已养的家畜",
            ])):
                yield _r
            return
        # pay=True（购买饲料）时**先买进背包**再喂；pay=False 直接喂背包里的
        if pay:
            self._farm_spend(group_id, uid, price)
            fm.bag_add(group_id, uid, item["name"], 1)
        if not fm.bag_take(group_id, uid, item["name"], 1):
            if pay:
                fm.bag_add(group_id, uid, item["name"], 1)
            async for _r in self._farm_send(event, "\n".join([
                    f"🎒 背包里没有「{item['name']}」",
                    f"💡 先买："
                    f"{self._farm_names('cmd_farm_buy_feed', '购买饲料')[0]}"
                    f" {item['name']}",
                    f"💡 看背包："
                    f"{self._farm_names('cmd_farm_bag', '我的背包')[0]}",
            ])):
                yield _r
            return
        r = fm.feed_animal(group_id, uid, item, name=who)
        if not r.get("ok"):
            fm.bag_add(group_id, uid, item["name"], 1)   # 喂失败退回去
            async for _r in self._farm_send(event, "\n".join([
                    f"⚠️ 你家里没有「{who}」这只家畜",
                    f"💡 发「"
                    f"{self._farm_names('cmd_farm_my_animals', '我的家畜')[0]}」"
                    "看已养的家畜",
            ])):
                yield _r
            return
        lines = [f"🍖 喂食成功：{item['name']} → {r['target']}"]
        lines.append(f"💚 体力：{r['before']} → "
                     f"{r['after']} / {r['energy_max']}"
                     f"（+{r['added']}）")
        if r.get("tired_before"):
            lines.append("😴 它刚才已经累趴了（不看守），现在恢复看门")
        if int(r.get("wasted") or 0):
            lines.append(f"⚠️ 体力满了，浪费了 {r['wasted']} 点（下次少买点）")
        _fleft = fm.bag_feed_total(group_id, uid)
        lines.append(f"🎒 背包饲料还剩 {_fleft} 份")
        if pay:
            lines.append(f"💸 花费：-{fmt_num(price)}　"
                         f"💰 余额：{fmt_num(self._farm_wallet(group_id, uid))}")
        lines.append(self._farm_guard_line(
            fm.guard_of_user(group_id, uid, pre.get("now"))))
        async for _r in self._farm_send(event, "\n".join(lines)):
            yield _r

    # ── ★ 偷菜 ────────────────────────────────────────────────
    async def _handle_farm_steal(self, event, group_id, uid, arg):
        """「偷菜 @某人 / QQ号」—— 偷别人的成熟作物"""
        fm = getattr(self, "farm_module", None)
        if fm is None:
            async for _r in self._farm_send(
                    event, "❌ 农场模块未加载，请查看后台日志"):
                yield _r
            return
        if not getattr(fm, "steal_enable", True):
            async for _r in self._farm_send(
                    event, "🚫 偷菜功能已被后台关闭"):
                yield _r
            return
        pre = self._farm_prepare(group_id, uid)
        target = ""
        try:
            target = self._pet_parse_target(event, arg) or ""
        except Exception:
            target = ""
        steal = self._farm_names("cmd_farm_steal", "偷菜")[0]
        if not target:
            async for _r in self._farm_send(event, "\n".join([
                f"⚠️ 要偷谁的？",
                f"💡 用法：{steal} @某人",
                f"💡 或：{steal} QQ号",
            ])):
                yield _r
            return
        if str(target) == str(uid):
            async for _r in self._farm_send(
                    event, "🤨 偷自己的菜…… 那不就是收菜吗"):
                yield _r
            return
        left = fm.steal_left(group_id, uid, pre.get("now"))
        if left <= 0:
            async for _r in self._farm_send(event, "\n".join([
                    f"🚧 今天的偷菜次数用完了"
                    f"（{pre.get('steal_limit')} 次 / 天）",
                    "💡 明天再来，或者后台调「每天最多偷几次」",
            ])):
                yield _r
            return
        r = fm.steal(group_id, uid, target, pre.get("now"))
        if not r.get("ok"):
            _why = {
                "no_farm": "🏠 对方还没开农场，没菜可偷",
                "no_land": "🏠 对方一块地都没有",
                "empty": "🟫 对方地里什么都没有",
                "growing": "🌱 对方的菜还没熟，再等等",
                "too_little": "🥬 对方地里那点收成太少，懒得偷",
            }.get(r.get("reason"))
            if _why is None and r.get("reason") == "guarded":
                _gn = r.get("guard_name") or "看门犬"
                _why = (f"🐕 被 {_gn} 拦住了！\n"
                        f"🛡 防偷 {r.get('guard')}%，"
                        f"成功率被压到 0%")
            if _why is None and r.get("reason") == "norate":
                _why = (f"🚫 目前偷菜成功率是 0%\n"
                        f"💡 去「"
                        f"{self._farm_names('cmd_farm_dog', '工具商店')[0]}」"
                        f"买**偷菜装备**提高成功率（基础 "
                        f"{fm.steal_base_rate}%）")
            if _why is None:
                _why = (f"🚧 没偷到（成功率 {r.get('chance')}%，"
                        f"掷出 {r.get('roll')}）")
                if int(r.get("bonus") or 0) > 0:
                    _why += (f"\n🧤 你的装备加成 +{r.get('bonus')}%"
                             + (f"（{r.get('tool_names')}）"
                                if r.get("tool_names") else ""))
                if int(r.get("guard") or 0) > 0:
                    _why += f"\n🐕 对方看门犬防偷 -{r.get('guard')}%"
            _out = [_why]
            # ★ 用户要求：失败要赔对方积分（随机区间）
            if r.get("reason") == "failed":
                _comp = int(r.get("comp") or 0)
                _paid = 0
                if _comp > 0:
                    _paid = min(_comp, max(0, self._farm_wallet(
                        group_id, uid)))
                    if _paid > 0:
                        self._farm_spend(group_id, uid, _paid)
                        self._farm_earn(group_id, target, _paid)
                    try:
                        fm.note(group_id, uid, "stole_fail", 1,
                                pre.get("now"))
                        fm.note(group_id, uid, "stole_paid", _paid,
                                pre.get("now"))
                        fm.note(group_id, target, "comp_got", _paid,
                                pre.get("now"))
                        fm.save()
                    except Exception:
                        logger.debug("[farm] 记赔付统计失败", exc_info=True)
                _sn2 = self._farm_score_name()
                if _comp <= 0:
                    _out.append("💸 不过后台把赔付关了，这次不用赔")
                elif _paid < _comp:
                    _out.append(f"💸 被抓了！该赔 {fmt_num(_comp)} {_sn2}，"
                                f"但你只有 {fmt_num(_paid)}，全赔光了")
                else:
                    _out.append(f"💸 被主人抓个正着，赔付对方 "
                                f"{fmt_num(_paid)} {_sn2}")
                _out.append(f"💰 你的余额："
                            f"{fmt_num(self._farm_wallet(group_id, uid))}")
            _out.append(f"📅 今天还剩 {left - 1} 次"
                        if r.get("reason") == "failed"
                        else f"📅 今天还剩 {left} 次")
            async for _r in self._farm_send(event, "\n".join(_out)):
                yield _r
            return
        self._farm_earn(group_id, uid, int(r.get("take") or 0))
        # ★ 记今日统计：偷到的算自己的，被偷的记在对方头上
        try:
            fm.note(group_id, uid, "stole_times", 1, pre.get("now"))
            fm.note(group_id, uid, "stole_amount", int(r.get("take") or 0),
                    pre.get("now"))
            fm.note_stolen(group_id, target, uid,
                           r.get("target_seed"), int(r.get("take") or 0),
                           pre.get("now"))
            fm.save()
        except Exception:
            logger.debug("[farm] 记今日偷菜统计失败", exc_info=True)
        sn = self._farm_score_name()
        lines = ["🥕 偷菜成功！",
                 f"🏠 目标：{self._farm_nick(group_id, target)}",
                 f"🌾 偷到：{r.get('target_seed')} "
                 f"×{fmt_num(r.get('take'))}"]
        if r.get("victim_emptied"):
            lines.append("（那块地被你偷空了）")
        else:
            lines.append(f"（那块地还剩 {fmt_num(r.get('left_on_land'))}）")
        lines.append(f"🎲 成功率 {r.get('chance')}%"
                     + (f"（基础 {fm.steal_base_rate}%"
                        f" + 装备 {r.get('bonus')}%"
                        if int(r.get("bonus") or 0) > 0
                        else f"（基础 {fm.steal_base_rate}%")
                     + (f" − 对方看门犬防偷 {r.get('guard')}%）"
                        if int(r.get("guard") or 0) > 0 else "）"))
        if r.get("tool_names"):
            lines.append(f"🧤 生效装备：{r.get('tool_names')}")
        lines.append(f"💰 进账 +{fmt_num(r.get('take'))} {sn}")
        lines.append(f"📅 今天还剩 {left - 1} 次")
        async for _r in self._farm_send(event, "\n".join(lines)):
            yield _r

    async def _handle_farm_steal_log(self, event, group_id, uid, arg):
        """「被偷记录」"""
        fm = getattr(self, "farm_module", None)
        if fm is None:
            async for _r in self._farm_send(
                    event, "❌ 农场模块未加载，请查看后台日志"):
                yield _r
            return
        pre = self._farm_prepare(group_id, uid)
        log = fm.steal_log(group_id, uid, 10)
        u = fm.user(group_id, uid)
        guard = pre.get("guard") or {}
        lines = ["🚨 被偷记录"]
        lines.append(self._farm_guard_line(guard))
        lines.append(f"📉 累计被偷：{fmt_num(u.get('stolen_total'))} 积分")
        lines.append(f"📈 累计偷到：{fmt_num(u.get('stole_total'))} 积分")
        lines.append("")
        if not log:
            lines.append("（还没被偷过，看门犬挺给力）")
        for x in log:
            _t = ""
            try:
                _t = time.strftime("%m-%d %H:%M",
                                   time.localtime(int(x.get("at") or 0)))
            except Exception:
                _t = ""
            lines.append(f"· {x.get('seed')} ×{fmt_num(x.get('amount'))}"
                         f"　被 {x.get('by')} 偷走　{_t}")
        lines.append("")
        steal = self._farm_names("cmd_farm_steal", "偷菜")[0]
        dogshop = self._farm_names("cmd_farm_dog", "工具商店")[0]
        lines.append(f"💡 提高防偷：去「{dogshop}」换更凶的看门犬")
        lines.append(f"💡 看门犬累了要喂饲料，不然防偷失效")
        lines.append(f"💡 你也可以偷别人：{steal} @某人")
        async for _r in self._farm_send(event, "\n".join(lines)):
            yield _r

    # ── ★ 我的背包（农场种子 / 饲料 + 签到商店食物）────────────
    def _farm_checkin_foods(self, group_id, uid) -> dict:
        """签到商店买的**食物**（{名字: 数量}）—— 联动签到系统"""
        cm = getattr(self, "checkin_module", None)
        if cm is None:
            return {}
        try:
            return dict(cm.owned_foods_of(group_id, uid) or {})
        except Exception:
            logger.debug("[farm] 读签到背包食物失败", exc_info=True)
            return {}

    def _farm_bag_text(self, group_id, uid, page: int = 1) -> str:
        fm = getattr(self, "farm_module", None)
        if fm is None:
            return "❌ 农场模块未加载，请查看后台日志"
        pre = self._farm_prepare(group_id, uid)
        sn = self._farm_score_name()
        seeds = fm.bag_seeds(group_id, uid)
        feeds = fm.bag_feeds(group_id, uid)
        unknown = fm.bag_unknown(group_id, uid)
        foods = self._farm_checkin_foods(group_id, uid)
        _bag = fm.bag(group_id, uid)

        lines = [f"🎒 我的背包　{self._farm_nick(group_id, uid)}"]
        lines.append(f"💰 {sn}：{fmt_num(self._farm_wallet(group_id, uid))}"
                     f"　👤 Lv.{pre.get('level')}")
        lines.append("")

        lines.append(f"🌱 种子（{len(seeds)} 种 / 共 "
                     f"{sum(x['count'] for x in seeds)} 个）")
        if not seeds:
            lines.append("　（空，去「种子商店」买）")
        for i, x in enumerate(seeds):
            _lock = "" if fm.level_ok(x["seed"], int(pre.get("level") or 1)) \
                else "🔒"
            lines.append(
                f"　{self._farm_no(i)} {x['name']} ×{x['count']}{_lock}"
                f"　单价 {fmt_num(x['seed']['price'])}"
                f"　产量 {fmt_num(x['seed']['yield_min'])}~"
                f"{fmt_num(x['seed']['yield_max'])}"
                f"　+{fmt_num(x['seed']['exp'])} 经验"
                f"　需 Lv.{x['seed']['level_req']}")

        lines.append("")
        lines.append(f"🥩 家畜饲料（{len(feeds)} 种 / 共 "
                     f"{sum(x['count'] for x in feeds)} 份）")
        if not feeds:
            lines.append("　（空，去「工具商店」买）")
        for i, x in enumerate(feeds):
            lines.append(f"　{self._farm_no(i)} {x['name']} ×{x['count']}"
                         f"　恢复体力 +{x['item']['energy']}")

        lines.append("")
        lines.append(f"🍖 签到商店食物（{len(foods)} 种 / 共 "
                     f"{sum(int(v or 0) for v in foods.values())} 个）")
        if not foods:
            lines.append("　（空，去签到商店买）")
        cm = getattr(self, "checkin_module", None)
        for i, (nm, n) in enumerate(sorted(foods.items())):
            _restore = ""
            if cm is not None:
                try:
                    _restore = (f"　恢复体力 +"
                                f"{cm.food_restore_of(cm.food_item_of(nm))}")
                except Exception:
                    _restore = ""
            lines.append(f"　{self._farm_no(i)} {nm} ×{int(n or 0)}{_restore}")

        if unknown:
            lines.append("")
            lines.append("❔ 已下架（后台删了，无法使用）")
            for nm, n in ((x["name"], x["count"]) for x in unknown):
                lines.append(f"　· {nm} ×{n}")

        lines.append("")
        plant = self._farm_names("cmd_farm_plant", "种菜")[0]
        feed = self._farm_names("cmd_farm_feed", "喂食")[0]
        eat = "吃食物"
        if cm is not None:
            try:
                eat = (cm.food_eat_trigger_names or ["吃食物"])[0]
            except Exception:
                eat = "吃食物"
        lines.append(f"💡 {plant} 种子名　／　{feed} 饲料名 [家畜名]"
                     f"　／　{eat} 食物名")
        lines.append(f"💡 背包里共 {len(_bag) + len(foods)} 种东西"
                     f"（农场 {len(_bag)} + 签到食物 {len(foods)}）")
        _bagcmd2 = self._farm_names("cmd_farm_bag", "我的背包")[0]
        return "\n".join(self._farm_page_text(_bagcmd2, lines, page))

    async def _handle_farm_bag(self, event, group_id, uid, arg):
        """「我的背包 [页码]」"""
        async for _r in self._farm_send(
                event, self._farm_bag_text(group_id, uid,
                                           self._farm_page_of(arg))):
            yield _r

    # ── ★ 农场状态（只看**自己今天**的数据）────────────────────
    def _farm_status_text(self, group_id, uid, page: int = 1) -> str:
        """
        「农场状态 [页码]」—— **只看自己今天**的农场数据。

        ★★ 2.1.x：**去重**。和「我的农场」重复的内容一律删掉：
            · 等级 / 经验进度（「我的农场」有）
            · 土地数量 / 品质统计（「我的农场」有）
            · 看门家畜 + 已养家畜数（「我的农场」有）
            · 钱包余额（「我的农场」有）
            · 「今天偷菜：还剩几次」（「我的农场」逐字有）
          这里只留**今天才有意义**的：
            收获 / 播种 / 被偷与偷菜明细 / 饲料 / 农机 / 背包数量

        ★ 分页：用通用兜底分页 `_farm_page_text` —— 正文超过
          `FARM_TEXT_PAGE_LINES`（18）行才分页，没超就原样输出。
        """
        fm = getattr(self, "farm_module", None)
        if fm is None:
            return "❌ 农场模块未加载，请查看后台日志"
        pre = self._farm_prepare(group_id, uid)
        now = pre.get("now")
        sn = self._farm_score_name()
        t = fm.today(group_id, uid, now)
        _feed_n = fm.bag_feed_total(group_id, uid)
        _seed_n = sum(x["count"] for x in fm.bag_seeds(group_id, uid))
        _machines = fm.tools_on(group_id, uid)

        # ★★ 2.1.0：标题和昵称**换行**；去掉括号里的日期
        # ★★ 2.1.x：等级 / 经验 / 土地数量 / 品质统计**删掉** ——
        #   这些是「我的农场」的总览内容，这里不再重复。
        lines = ["📋 农场状态",
                 f"👨‍🌾 {self._farm_nick(group_id, uid)}",
                 ""]

        # ① 今天收获 / 赚了多少
        lines.append("🧺 今天收获")
        # ★★ 2.1.0：作物数量、赚了多少**各占一行**
        lines.append(f"　 收了 {int(t.get('harvest_times') or 0)} 次")
        lines.append(f"　 {int(t.get('harvest_crops') or 0)} 个作物")
        lines.append(f"　 💰 赚了 {fmt_num(t.get('earned'))} {sn}")
        if int(t.get("auto_harvest") or 0):
            lines.append(f"　 🤖 其中自动收割机代收 "
                         f"{int(t.get('auto_harvest'))} 个作物")
        # ② 今天播种
        lines.append(f"🌾 今天播种：{int(t.get('planted') or 0)} 个作物")
        if int(t.get("auto_plant") or 0):
            lines.append(f"　 🤖 其中自动播种机代播 "
                         f"{int(t.get('auto_plant'))} 个")
        # ③ 饲料剩余
        #   ★★ 2.1.x：原来这里的「🐕 今天上班的看门家畜 / 🏠 已养家畜」
        #     和「我的农场」的「🐕 看门：…／🏠 家畜：…」是同一份内容
        #     （连 `_farm_guard_line()` 都是同一个函数），**整段删掉**；
        #     家畜相关留给「我的农场」，这里只留今天要喂的饲料。
        lines.append("")
        # ★★ 2.1.x：用户要求 —— 「看门家畜今日体力」留在本指令
        #   （只留这一行；「已养家畜 N / M」仍在「我的农场」）。
        try:
            _g9 = fm.guard_of_user(group_id, uid)
            if _g9 and str(_g9.get("name") or "").strip():
                lines.append("🐕 今天上班："
                             + self._farm_guard_line(_g9))
        except Exception:
            logger.debug("[farm] 读看门家畜失败", exc_info=True)
        lines.append(f"🥩 饲料剩余：{_feed_n} 份")
        _feeds = fm.bag_feeds(group_id, uid)
        if _feeds:
            lines.append("　 " + "　".join(
                f"{x['name']}×{x['count']}" for x in _feeds))
        else:
            lines.append("　（背包里没饲料了，家畜累了就没人看守）")
        # ④ 今天有没有被偷 / 今天偷菜
        lines.append("")
        _st = int(t.get("stolen_times") or 0)
        if _st:
            lines.append(f"🚨 今天被偷了 {_st} 次，共损失 "
                         f"{fmt_num(t.get('stolen_amount'))} {sn}")
            for x in (t.get("stolen_log") or [])[-3:]:
                lines.append(f"　 · {x.get('seed')} "
                             f"×{fmt_num(x.get('amount'))}"
                             f"　被 {x.get('by')} 偷走")
        else:
            lines.append("✅ 今天还没被偷")
        if int(t.get("stole_times") or 0):
            lines.append(f"🥕 今天偷了 {int(t.get('stole_times'))} 次，"
                         f"进账 {fmt_num(t.get('stole_amount'))} {sn}")
        # ★★ 2.1.x：用户要求 —— 「今天偷菜剩余次数」**放回本指令**
        #   （从「我的农场」挪过来）。
        try:
            _lim2 = int(getattr(fm, "steal_daily_limit", 0) or 0)
            if _lim2 > 0:
                _used2 = int(t.get("stole_times") or 0)
                lines.append(f"🎯 今天偷菜次数还剩 "
                             f"{max(0, _lim2 - _used2)} / {_lim2} 次")
        except Exception:
            logger.debug("[farm] 读偷菜剩余次数失败", exc_info=True)
        _sb = fm.steal_bonus_of(group_id, uid)
        _stools = fm.steal_tools_on(group_id, uid)
        lines.append(f"　 🥕 偷菜成功率：{fm.steal_base_rate}%"
                     + (f" + 装备 {_sb}%" if _sb else "（没戴偷菜装备）")
                     + " − 对方看门犬防偷")
        if _stools:
            lines.append("　 🧤 生效装备：" + "、".join(
                str(x.get("name")) for x in _stools))
        if int(t.get("stole_fail") or 0):
            lines.append(f"　 💸 今天偷失败 {int(t.get('stole_fail'))} 次，"
                         f"赔出去 {fmt_num(t.get('stole_paid'))} {sn}")
        if int(t.get("comp_got") or 0):
            lines.append(f"　 💰 今天收到赔付 {fmt_num(t.get('comp_got'))} "
                         f"{sn}")
        # ⑤ 农机
        lines.append("")
        if _machines:
            lines.append("🤖 已装备农机：" + "、".join(
                str(x.get("name")) for x in _machines))
            lines.append(f"　 每天自动干活一次（当前统计已算进上面）")
        else:
            lines.append("🤖 农机：无（去「工具商店」买自动收割机 / "
                         "自动播种机）")
        lines.append("")
        # ★★ 2.1.x：钱包余额**删掉**（「我的农场」里「💰 金圆券：N」有），
        #   这里只留「我的农场」没有的背包数量。
        lines.append(f"🎒 背包：种子 {_seed_n} 个　饲料 {_feed_n} 份")
        _bagcmd = self._farm_names("cmd_farm_bag", "我的背包")[0]
        _view = self._farm_names("cmd_farm_view", "我的农场")[0]
        _land = self._farm_names("cmd_farm_land", "土地状态")[0]
        _all = self._farm_names("cmd_farm_harvest_all", "一键收获")[0]
        lines.append(f"💡 {_bagcmd} ／ {_view} ／ {_land} ／ {_all}")
        _scmd = self._farm_names("cmd_farm_status", "农场状态")[0]
        return "\n".join(self._farm_page_text(_scmd, lines, page))

    async def _handle_farm_status(self, event, group_id, uid, arg):
        """「农场状态 [页码]」—— 自己今天的农场数据"""
        async for _r in self._farm_send(
                event, self._farm_status_text(group_id, uid,
                                              self._farm_page_of(arg))):
            yield _r

    # ── ★ 农机（自动收割机 / 自动播种机）──────────────────────
    async def _handle_farm_buy_tool(self, event, group_id, uid, arg):
        """「购买工具 名字/编号」—— 买农机（买了默认就装备上）"""
        fm = getattr(self, "farm_module", None)
        if fm is None:
            async for _r in self._farm_send(
                    event, "❌ 农场模块未加载，请查看后台日志"):
                yield _r
            return
        item = fm.find_machine(arg)
        if not item:
            _names = "、".join(x["name"] for x in fm.tools()) or "（后台没上架）"
            async for _r in self._farm_send(event, "\n".join([
                    "⚠️ 没认出这台农机",
                    f"💡 用法："
                    f"{self._farm_names('cmd_farm_buy_tool', '购买工具')[0]}"
                    f" 名字（或编号）",
                    f"💡 现有农机：{_names}",
                    f"💡 看商店："
                    f"{self._farm_names('cmd_farm_dog', '工具商店')[0]}",
            ])):
                yield _r
            return
        pre = self._farm_prepare(group_id, uid)
        lv = int(pre.get("level") or 1)
        sn = self._farm_score_name()
        if not fm.level_ok(item, lv):
            async for _r in self._farm_send(event, "\n".join([
                    f"🔒 等级不够：{item['name']} 需要 Lv.{item['level_req']}",
                    f"👤 你当前 Lv.{lv}　"
                    f"（{fmt_num(pre.get('exp'))} 经验，还差 "
                    f"{fmt_num(pre.get('next_need'))} 升级）",
            ])):
                yield _r
            return
        if any(x["name"] == item["name"]
               for x in fm.tools_owned(group_id, uid)):
            async for _r in self._farm_send(
                    event, f"🤖 {item['name']} 你已经有了（不用再买）"):
                yield _r
            return
        price = int(item["price"])
        if self._farm_wallet(group_id, uid) < price:
            async for _r in self._farm_send(event, "\n".join([
                    f"💸 {sn}不够：{item['name']} 要 {fmt_num(price)}，"
                    f"你只有 {fmt_num(self._farm_wallet(group_id, uid))}",
                    "💡 去签到/打工/收获攒一攒",
            ])):
                yield _r
            return
        r = fm.add_tool(group_id, uid, item)
        if not r.get("ok"):
            async for _r in self._farm_send(
                    event, "🚧 买不了（已经有这一台了）"):
                yield _r
            return
        self._farm_spend(group_id, uid, price)
        _eff = []
        if int(item.get("auto_harvest", 0) or 0):
            _eff.append("每天自动收获（直接卖掉）")
        if int(item.get("auto_plant", 0) or 0):
            _eff.append("每天自动播种（用背包里的种子）")
        if int(item.get("steal_bonus", 0) or 0):
            _eff.append(f"偷菜成功率 +{item['steal_bonus']}%")
        lines = ["🤖 买下装备！" if int(item.get("steal_bonus", 0) or 0) == 0
                 else "🧤 买下偷菜装备！", f"🏷 {item['name']}"]
        lines.append("⚙️ 功能：" + ("、".join(_eff) if _eff else "无"))
        lines.append("✅ 已自动装备（每天自动干活一次，静默不发群消息）"
                     if int(item.get("steal_bonus", 0) or 0) == 0
                     else "✅ 已自动装备（偷菜成功率立刻提高）")
        lines.append(f"💸 花费：-{fmt_num(price)}　"
                     f"💰 余额：{fmt_num(self._farm_wallet(group_id, uid))}")
        lines.append(f"💡 看今天干了什么："
                     f"{self._farm_names('cmd_farm_status', '农场状态')[0]}")
        lines.append("⚠️ 自动播种会用掉背包里的种子，记得先囤货"
                     if int(item.get("auto_plant", 0) or 0) else
                     f"💡 现在偷菜成功率：{fm.steal_base_rate}% + "
                     f"{fm.steal_bonus_of(group_id, uid)}%")
        async for _r in self._farm_send(event, "\n".join(lines)):
            yield _r

    async def _handle_farm_equip_tool(self, event, group_id, uid, arg):
        """「装备工具 名字」—— 装备 / 卸下农机（开关式）"""
        fm = getattr(self, "farm_module", None)
        if fm is None:
            async for _r in self._farm_send(
                    event, "❌ 农场模块未加载，请查看后台日志"):
                yield _r
            return
        pre = self._farm_prepare(group_id, uid)
        _owned = fm.tools_owned(group_id, uid)
        _equip = self._farm_names("cmd_farm_equip_tool", "装备工具")[0]
        if not _owned:
            async for _r in self._farm_send(event, "\n".join([
                    "🤖 你还没有农机",
                    f"💡 去「"
                    f"{self._farm_names('cmd_farm_dog', '工具商店')[0]}」"
                    f"用「"
                    f"{self._farm_names('cmd_farm_buy_tool', '购买工具')[0]}"
                    f" 名字」买",
            ])):
                yield _r
            return
        name = str(arg or "").strip()
        if not name:
            lines = ["⚠️ 要装备哪一台？", ""]
            for i, x in enumerate(_owned):
                lines.append(f"{self._farm_no(i)} {x['name']}"
                             f"　{'✅ 已装备' if x['on'] else '未装备'}")
            lines.append("")
            lines.append(f"💡 用法：{_equip} 名字（或编号）——再发一次就卸下")
            async for _r in self._farm_send(event, "\n".join(lines)):
                yield _r
            return
        _key = name
        if name.isdigit() and 1 <= int(name) <= len(_owned):
            _key = _owned[int(name) - 1]["name"]
        r = fm.toggle_tool(group_id, uid, _key)
        if not r.get("ok"):
            async for _r in self._farm_send(event, "\n".join([
                    f"⚠️ 你家里没有「{name}」这台农机",
                    f"💡 发「{_equip}」看不带参数的列表",
            ])):
                yield _r
            return
        _it = r.get("item") or {}
        _eff = []
        if int(_it.get("auto_harvest", 0) or 0):
            _eff.append("每天自动收获")
        if int(_it.get("auto_plant", 0) or 0):
            _eff.append("每天自动播种")
        if int(_it.get("steal_bonus", 0) or 0):
            _eff.append(f"偷菜成功率 +{_it['steal_bonus']}%")
        lines = [f"{'✅ 已装备' if r.get('on') else '⏸ 已卸下'}：{r['name']}"]
        if _eff:
            lines.append("⚙️ 功能：" + "、".join(_eff))
        _cur = fm.steal_bonus_of(group_id, uid)
        lines.append(f"🥕 当前偷菜加成：+{_cur}%"
                     f"（成功率 = {fm.steal_base_rate}% + {_cur}%"
                     f" − 对方看门犬防偷）")
        if int(_it.get("steal_bonus", 0) or 0) == 0:
            lines.append("💡 每天自动干活一次，结果只记在「农场状态」里，"
                         "不会发群消息")
        async for _r in self._farm_send(event, "\n".join(lines)):
            yield _r

    # ── ★ 农机每日自动干活（静默；后台定时任务 + 惰性兜底）────
    def _farm_auto_run(self, group_id, uid, now=None) -> dict:
        """
        给一个人跑一次「今天的农机自动作业」（静默，不发送任何消息）。

        返回 {"ran","reason","harvested","earned","planted","exp"}
        · 只跑**今天还没跑过**的；没装备农机 -> 不跑
        · 自动收割机：收所有成熟的并直接卖掉（积分进钱包）
        · 自动播种机：从背包挑最贵的可用种子，把空地都种上
        """
        fm = getattr(self, "farm_module", None)
        if fm is None:
            return {"ran": False, "reason": "nomod"}
        if not bool(getattr(self, "farm_auto_enable", True)):
            return {"ran": False, "reason": "disabled"}
        now = int(now or time.time())
        if not fm.auto_due(group_id, uid, now):
            return {"ran": False, "reason": "not_due"}
        _do_h = fm.has_machine(group_id, uid, "auto_harvest")
        _do_p = fm.has_machine(group_id, uid, "auto_plant")
        out = {"ran": True, "reason": "", "harvested": 0, "earned": 0,
               "planted": 0, "exp": 0, "auto_harvest": _do_h,
               "auto_plant": _do_p}
        # ① 自动收割
        if _do_h:
            try:
                r = fm.harvest(group_id, uid, index=None, now=now)
            except Exception:
                logger.exception("[farm] 自动收割失败")
                r = {}
            picked = r.get("picked") or []
            if picked:
                self._farm_earn(group_id, uid, int(r.get("total") or 0))
                t = fm.today(group_id, uid, now)

                def _ti(_v):
                    try:
                        return int(_v or 0)
                    except Exception:
                        return 0

                t["harvest_times"] = _ti(t.get("harvest_times")) + 1
                t["harvest_crops"] = _ti(t.get("harvest_crops")) + len(picked)
                t["earned"] = _ti(t.get("earned")) + int(r.get("total") or 0)
                t["auto_harvest"] = _ti(t.get("auto_harvest")) + len(picked)
                _up = r.get("exp") or {}
                out["exp"] = int(_up.get("gain") or 0)
                fm.save()
                out["harvested"] = len(picked)
                out["earned"] = int(r.get("total") or 0)
                logger.info("[farm] 自动收割机：群=%s 用户=%s 收 %d 块，"
                            "进账 %d", group_id, uid, len(picked),
                            out["earned"])
        # ② 自动播种
        if _do_p:
            try:
                _pre = self._farm_prepare(group_id, uid)
                lv = int(_pre.get("level") or 1)
                _n = 0
                for _ in range(60):          # 上限保护（最多 30 块地）
                    pick = fm.best_seed_for(group_id, uid, lv)
                    if not pick:
                        break
                    if not fm.bag_take(group_id, uid, pick["name"], 1):
                        break
                    rr = fm.plant(group_id, uid, pick["seed"], now)
                    if not rr.get("ok"):
                        fm.bag_add(group_id, uid, pick["name"], 1)
                        break
                    _n += 1
                if _n:
                    fm.note(group_id, uid, "planted", _n, now)
                    fm.note(group_id, uid, "auto_plant", _n, now)
                    fm.save()
                    out["planted"] = _n
                    logger.info("[farm] 自动播种机：群=%s 用户=%s 播 %d 块",
                                group_id, uid, _n)
            except Exception:
                logger.exception("[farm] 自动播种失败")
        fm.auto_mark(group_id, uid, now)
        return out

    def _farm_auto_all(self) -> dict:
        """给**所有**农场存档跑一次今天的自动作业（后台定时任务用）"""
        fm = getattr(self, "farm_module", None)
        if fm is None or not getattr(fm, "enable", True):
            return {"users": 0, "ran": 0}
        keys = fm.all_user_keys()
        ran = 0
        for gid, uid in keys:
            try:
                if self._farm_auto_run(gid, uid).get("ran"):
                    ran += 1
            except Exception:
                logger.exception("[farm] 自动作业失败：群=%s 用户=%s",
                                 gid, uid)
        return {"users": len(keys), "ran": ran}

    # ── 参数拆分 ──────────────────────────────────────────────
    @staticmethod
    def _farm_split_arg(arg):
        """
        把「肥料名 [地块编号]」拆成 (名字, 编号或 None)。

        支持 `复合肥`、`复合肥 3`、`复合肥3` 三种写法。
        """
        t = " ".join(str(arg or "").replace("\u3000", " ").split()).strip()
        if not t:
            return "", None
        parts = t.split()
        if len(parts) >= 2 and parts[-1].isdigit():
            return " ".join(parts[:-1]).strip(), int(parts[-1]) - 1
        if len(parts) == 1:
            import re as _re
            m = _re.match(r"^(.*?)(\d+)$", parts[0])
            if m and m.group(1).strip():
                return m.group(1).strip(), int(m.group(2)) - 1
        return t, None

    @staticmethod
    def _farm_split2(arg):
        """
        把「名字 [附加参数]」拆成 (名字, 附加参数或 "")。

        用于「喂食 狗粮 柴犬」这种——第一段是物品名，后面全是附加参数。
        """
        t = " ".join(str(arg or "").replace("\u3000", " ").split()).strip()
        if not t:
            return "", ""
        parts = t.split(" ", 1)
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], parts[1].strip()

    async def _dispatch_farm(self, event, msg_str, group_id):
        """
        农场指令总调度。

        顺序：**菜单入口** -> 查看 -> 收获 -> 三家店 -> 我的家畜 / 被偷记录
              -> 购买 -> 施肥/喂食 -> 偷菜 -> 种菜。
        命中后一定会发东西（不会静默）。
        """
        if self._is_farm_menu(msg_str):
            async for _r in self._farm_send(event, self._build_farm_menu()):
                yield _r
            return
        # ★ 下级菜单（种植管理 / 种子图鉴 / 农具图鉴 / 家畜图鉴 /
        #   农机图鉴 / 偷菜玩法）—— 先判菜单，再判动作指令
        _sub = self._farm_sub_menu(msg_str)
        if _sub:
            async for _r in self._farm_send(
                    event, self._build_farm_sub_menu(_sub)):
                yield _r
            return
        try:
            uid = to_str(event.get_sender_id())
        except Exception:
            uid = ""
        if not group_id or not uid:
            async for _r in self._farm_send(event, "⚠️ 请在群聊里玩农场"):
                yield _r
            return
        for attr, dflt, fn in (
                ("cmd_farm_view", "我的农场", self._handle_farm_view),
                ("cmd_farm_harvest_all", "一键收获",
                 self._handle_farm_harvest_all),
                ("cmd_farm_harvest", "收获", self._handle_farm_harvest),
                ("cmd_farm_shop", "种子商店", self._handle_farm_shop),
                ("cmd_farm_impl_shop", "农具商店",
                 self._handle_farm_item_shop),
                ("cmd_farm_dog", "工具商店", self._handle_farm_dog_shop),
                ("cmd_farm_my_animals", "我的家畜",
                 self._handle_farm_my_animals),
                ("cmd_farm_steal_log", "被偷记录",
                 self._handle_farm_steal_log),
                ("cmd_farm_buy_seed", "购买种子", self._handle_farm_buy_seed),
                ("cmd_farm_buy_item", "购买肥料", self._handle_farm_buy_item),
                ("cmd_farm_buy_dog", "购买家畜", self._handle_farm_buy_dog),
                ("cmd_farm_buy_feed", "购买饲料",
                 self._handle_farm_buy_feed),
                ("cmd_farm_equip_dog", "装备家畜",
                 self._handle_farm_equip_dog),
                ("cmd_farm_use_item", "施肥", self._handle_farm_use_item),
                ("cmd_farm_feed", "喂食", self._handle_farm_feed),
                ("cmd_farm_steal", "偷菜", self._handle_farm_steal),
                ("cmd_farm_bag", "我的背包", self._handle_farm_bag),
                ("cmd_farm_status", "农场状态", self._handle_farm_status),
                ("cmd_farm_land", "土地状态", self._handle_farm_land),
                ("cmd_farm_buy_tool", "购买工具",
                 self._handle_farm_buy_tool),
                ("cmd_farm_equip_tool", "装备工具",
                 self._handle_farm_equip_tool),
                ("cmd_farm_plant", "种菜", self._handle_farm_plant),
        ):
            arg = self._farm_match(msg_str, attr, dflt)
            if arg is None:
                continue
            async for _r in fn(event, group_id, uid, arg):
                yield _r
            return
        async for _r in self._farm_send(event, self._build_farm_menu()):
            yield _r

    @filter.event_message_type(EventMessageType.ALL)
    async def on_msg(self, event: AstrMessageEvent):
        GLOBAL_STATS["recv"] += 1
        # ★★ 1.4.0：调用次数 —— 每条进来处理的消息 +1（后台「数据统计」用）
        GLOBAL_STATS.bump("calls")
        try:
            _sg = str(_get_group_id_from_event(event) or "")
            if _sg and isinstance(self._seen_groups, set):
                self._seen_groups.add(_sg)
            if _sg and getattr(self, "auth_module", None) is not None:
                try:
                    self.auth_module.known_groups = self._seen_groups
                except Exception:
                    pass
        except Exception:
            pass
        try:
            from .core.system_status import set_bot_id as _set_bot_id
            _set_bot_id(event.get_self_id())
        except Exception:
            pass
        try:
            await ensure_mc_checker(self)
        except Exception:
            logger.exception("[apix-meme] ensure_mc_checker 调用失败")
        try:
            await ensure_hourly_chime(self)
        except Exception:
            logger.exception("[apix-meme] ensure_hourly_chime 调用失败")
        try:
            await ensure_status_push(self)
        except Exception:
            logger.exception("[apix-meme] ensure_status_push 调用失败")
        try:
            await ensure_daily_news(self)
        except Exception:
            logger.exception("[apix-meme] ensure_daily_news 调用失败")
        # ★★ 1.6.0：农场农机自动作业（种下去就不用管）
        try:
            await ensure_farm_auto(self)
        except Exception:
            logger.exception("[apix-meme] ensure_farm_auto 调用失败")

        if not self._dirty:
            self.reload_cfg()

        msg_str = to_str(event.message_str).strip()

        _auth_gid = str(_get_group_id_from_event(event) or "")
        if _auth_gid and getattr(self, "auth_module", None) is not None:
            try:
                _is_menu_now = (self._is_auth_menu(msg_str)
                                or bool(self._is_auth_sub_menu(msg_str)))
            except Exception:
                _is_menu_now = False
            if _is_menu_now:
                pass
            else:
                try:
                    _auth_hit = self.auth_module.match_cmd(msg_str)
                except Exception:
                    _auth_hit = None
            try:
                _auth_st = self.auth_module.match_status_cmd(msg_str)
            except Exception:
                _auth_st = ""
            if _is_menu_now:
                _auth_hit = None
            if _auth_st and not _auth_hit:
                try:
                    _sallowed = self.auth_module.cmd_allowed(_auth_gid)
                except Exception:
                    _sallowed = False
                if not _sallowed:
                    logger.info(
                        f"[auth] 群 {_auth_gid} 不在授权指令白名单，"
                        f"忽略 {msg_str[:40]!r}")
                    return
                _stxt = self.auth_module.handle_cmd(msg_str, _auth_gid)
                if _stxt:
                    async for _r in self._send_content(event, _stxt):
                        yield _r
                return
            try:
                _auth_ls = self.auth_module.match_list_cmd(msg_str)
            except Exception:
                _auth_ls = ""
            if _auth_ls and not _auth_hit:
                try:
                    _lallowed = self.auth_module.cmd_allowed(_auth_gid)
                except Exception:
                    _lallowed = False
                if not _lallowed:
                    logger.info(
                        f"[auth] 群 {_auth_gid} 不在授权指令白名单，"
                        f"忽略 {msg_str[:40]!r}")
                    return
                _ltxt = self.auth_module.handle_cmd(msg_str, _auth_gid)
                if _ltxt:
                    async for _r in self._send_content(event, _ltxt):
                        yield _r
                return
            if _auth_hit:
                try:
                    _allowed = self.auth_module.cmd_allowed(_auth_gid)
                except Exception:
                    _allowed = False
                if not _allowed:
                    logger.info(
                        f"[auth] 群 {_auth_gid} 不在授权指令白名单，"
                        f"忽略 {msg_str[:40]!r}")
                    return
                try:
                    _is_ga = self._is_global_admin(event)
                except Exception:
                    _is_ga = False
                if not _is_ga:
                    async for _r in self._send_content(
                            event,
                            "⛔ 只有 **AstrBot 全局管理员** 能授权\n"
                            "────────────────────\n"
                            "　 群主 / 群管理员 / 插件专属管理员 都无权\n"
                            "💡 怎么设置：后台「授权功能」页有详细说明\n"
                            f"💡 你可以自己查状态："
                            f"{(getattr(self.auth_module, 'status_names', None) or ['授权状态'])[0]}"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                _txt = self.auth_module.handle_cmd(msg_str, _auth_gid)
                if _txt:
                    async for _r in self._send_content(event, _txt):
                        yield _r
                return
            if not _is_menu_now:
                try:
                    if not self.auth_module.is_authorized(_auth_gid):
                        logger.info(
                            f"[auth] 群 {_auth_gid} 未授权，已提示用户")
                        try:
                            _dtxt = self.auth_module.deny_text(_auth_gid)
                        except Exception:
                            _dtxt = ""
                        if _dtxt:
                            async for _r in self._send_content(event, _dtxt):
                                yield _r
                            GLOBAL_STATS["send"] += 1
                        return
                    try:
                        _wtxt = self.auth_module.warn_text(_auth_gid)
                    except Exception:
                        _wtxt = ""
                    if _wtxt:
                        async for _r in self._send_content(event, _wtxt):
                            yield _r
                except Exception:
                    logger.exception("[apix-meme] 授权判定失败（放行）")

        _ngid = str(_get_group_id_from_event(event) or "")
        if self.notice_module is not None and _ngid:
            _want_admin = False
            _nm = None
            try:
                _nm = self.notice_module.match_cmd(msg_str)
            except Exception:
                _nm = None
            _is_status = msg_str in ("提示状态", "/提示状态")
            if _nm or _is_status:
                _want_admin = bool(_nm)
            if _want_admin and not await self._check_admin(event):
                async for _r in self._send_content(
                        event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if _is_status:
                async for _r in self._send_content(
                        event, self.notice_module.status_text(_ngid)):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if _nm:
                _action, _kinds = _nm
                _add = (_action == "on")
                _ok_names, _bad = [], []
                for _k in _kinds:
                    try:
                        _ok, _note = self.notice_module.edit_whitelist(
                            _k, _ngid, _add)
                    except Exception:
                        logger.exception("[apix-meme] 改写提示白名单失败")
                        _ok, _note = False, "异常"
                    _lab = NOTICE_LABEL.get(_k, _k)
                    if _ok:
                        _ok_names.append(_lab + (
                            "（已在白名单里）" if _note == "already"
                            else "（本不在白名单）" if _note == "absent"
                            else ""))
                    else:
                        _bad.append(f"{_lab}（{_note}）")
                try:
                    self.reload_cfg()
                except Exception:
                    pass
                _lines = []
                if _ok_names:
                    _lines.append(
                        ("✅ 已开启提示：" if _add else "🚫 已关闭提示：")
                        + "、".join(_ok_names))
                    _lines.append(f"🏠 本群：{_ngid}")
                    for _k in _kinds:
                        _lines.append(
                            f"　 {NOTICE_LABEL.get(_k, _k)} 白名单："
                            f"{self.notice_module.whitelist_state(_k)}")
                    _lines.append("💡 也可以在后台按提示分别填写群号")
                if _bad:
                    _lines.append("❌ 处理失败：" + "、".join(_bad))
                async for _r in self._send_content(
                        event, "\n".join(_lines) or "❌ 没有可处理的项目"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return

        group_id = _get_group_id_from_event(event)
        if self.poke_reply_module is not None \
                and getattr(self.poke_reply_module, "enable", False):
            try:
                _pk_raw = None
                try:
                    _pk_raw = getattr(event.message_obj, "raw_message", None)
                except Exception:
                    _pk_raw = None
                if self.poke_reply_module.is_poke(_pk_raw):
                    _pk_bot = ""
                    try:
                        from .core.system_status import get_cached_bot_id
                        _pk_bot = str(get_cached_bot_id() or "")
                    except Exception:
                        _pk_bot = ""
                    if not _pk_bot:
                        try:
                            _pk_bot = to_str(event.get_self_id())
                        except Exception:
                            _pk_bot = ""
                    _pk = self.poke_reply_module.match(_pk_raw, _pk_bot)
                    if _pk and _pk.get("word"):
                        _st = self.poke_reply_module.stats()
                        logger.info(
                            "[apix-meme] 戳一戳回复：群=%s 发起=%s 目标=%s "
                            "本窗口已用 %s/%s",
                            _pk.get("group_id"), _pk.get("sender"),
                            _pk.get("target"), _st.get("used"),
                            _st.get("max"))
                        async for _r in self._send_content(
                                event, _pk["word"]):
                            yield _r
                        GLOBAL_STATS["send"] += 1
                    return
            except Exception:
                logger.exception("[apix-meme] 处理戳一戳回复失败")
                return

        if self.notice_module is not None:
            try:
                _raw = None
                try:
                    _raw = getattr(event.message_obj, "raw_message", None)
                except Exception:
                    _raw = None
                _is_notice = (isinstance(_raw, dict)
                              and str(_raw.get("post_type") or "") == "notice")
                if _is_notice and getattr(self.notice_module, "debug", False):
                    _dg = str(group_id or (_raw.get("group_id")
                                           if isinstance(_raw, dict) else "")
                              or "")
                    try:
                        self.notice_module.remember_from_event(_dg, _raw)
                    except Exception:
                        pass
                    async for _r in self._send_content(
                            event, self.notice_module.debug_text(_dg, _raw)):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                _info = self.notice_module.parse_notice(_raw)
                if _info:
                    _gid = str(_get_group_id_from_event(event)
                               or _raw.get("group_id") or "")
                    try:
                        if str(_info.get("kind") or "") == "card" \
                                and not _info.get("old"):
                            _info["old"] = self.notice_module.name_of(
                                _gid, _info.get("uid") or "", "")
                    except Exception:
                        pass
                    try:
                        self.notice_module.remember_from_event(_gid, _raw)
                    except Exception:
                        pass
                    _kind = str(_info.get("kind") or "")
                    try:
                        if _kind == "_count":
                            self.notice_module.bump_count(_gid, +1)
                            GLOBAL_STATS["join"] = int(
                                GLOBAL_STATS.get("join", 0) or 0) + 1
                        elif _kind == "leave":
                            self.notice_module.bump_count(_gid, -1)
                            GLOBAL_STATS["leave"] = int(
                                GLOBAL_STATS.get("leave", 0) or 0) + 1
                    except Exception:
                        pass
                    if _kind and _kind != "_count" and \
                            self.notice_module.is_on(_gid, _kind):
                        _txt = self.notice_module.build_message(_gid, _info)
                        if _txt:
                            async for _r in self._send_content(event, _txt):
                                yield _r
                            GLOBAL_STATS["send"] += 1
                    return
            except Exception:
                logger.exception("[apix-meme] 处理群通知事件失败")
                return

        bot_id = to_str(event.get_self_id())
        sender_id = to_str(event.get_sender_id())
        if sender_id == bot_id:
            return

        try:
            sender_name = get_sender_name(event)
            if sender_id and sender_name:
                self._nickname_cache.update(sender_id, sender_name)
                if self.notice_module is not None:
                    _cg = str(_get_group_id_from_event(event) or "")
                    if _cg:
                        self.notice_module.remember_name(_cg, sender_id,
                                                         sender_name)
            try:
                _gid2 = str(_get_group_id_from_event(event) or "")
                if (_gid2 and sender_id and sender_name
                        and self.checkin_module is not None
                        and sender_id != bot_id):
                    _u2 = self.checkin_module._guser(_gid2, sender_id)
                    if isinstance(_u2, dict) and \
                            str(_u2.get("name") or "") != sender_name:
                        _u2["name"] = sender_name
                        self.checkin_module._need_save = True
            except Exception:
                logger.debug("[apix-meme] 同步本群昵称失败", exc_info=True)
            if self.notice_module is not None and bot_id:
                self.notice_module.set_bot_identity(bot_id, sender_name or "")
        except Exception:
            logger.exception("[apix-meme] 更新昵称缓存失败")

        if group_id and self.checkin_module is not None \
                and getattr(self.checkin_module, "pool_enable", False):
            try:
                for _pt in self.checkin_module.pool_tick(group_id):
                    if _pt:
                        async for _r in self._send_content(event, _pt):
                            yield _r
                        GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 奖池超时检查失败")

        if group_id and self.guess_module is not None:
            try:
                _gt = self._guess_settle(group_id)
                if _gt:
                    async for _r in self._send_content(event, _gt):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 猜数超时检查失败")

        if group_id and self.checkin_module is not None \
                and getattr(self.checkin_module, "mount_greet_enable", False) \
                and self._is_group_feature_enable(group_id, "mount"):
            try:
                _gt = self.checkin_module.mount_daily_greet(
                    group_id, sender_id, sender_name)
                if _gt:
                    try:
                        self.checkin_module._save()
                    except Exception:
                        pass
                    async for _r in self._send_content(event, _gt):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 坐骑登场提示失败")

        if sender_id and self.moderation_enable and self.member_blacklist:
            bl_action = get_blacklist_action(
                self.member_blacklist, sender_id, self.blacklist_default_action
            )
            if bl_action:
                if bl_action in (ACTION_KICK, ACTION_MUTE) and self.moderation_module is not None:
                    can, role = await self.moderation_module.bot_can_moderate(event)
                    if can:
                        ok = await self.moderation_module.apply_action(
                            event, sender_id, bl_action, self.mute_duration
                        )
                        label = ACTION_LABEL.get(bl_action, bl_action)
                        logger.info(
                            f"[moderation] 黑名单成员 {sender_id} 发言触发{label}，结果 ok={ok}"
                        )
                    else:
                        logger.warning(
                            f"[moderation] 机器人无管理权限（{role}），"
                            f"黑名单成员 {sender_id} 的 {bl_action} 动作被跳过，仅无视"
                        )
                return

        if group_id:
            try:
                umo = get_event_umo(event)
                if umo:
                    logger.info(f"[apix-meme] 记录 UMO: 群 {group_id} -> {umo}")
                    if self.hourly_chime_module is not None:
                        self.hourly_chime_module.record_umo(group_id, umo)
                    if self.minecraft_module is not None:
                        self.minecraft_module.record_umo(group_id, umo)
                    if self.status_push_module is not None:
                        self.status_push_module.record_context(group_id, umo)
                    if self.daily_news_module is not None:
                        self.daily_news_module.record_context(group_id, umo)
                else:
                    logger.warning(f"[apix-meme] 群 {group_id} 未取到 UMO")
            except Exception:
                logger.exception("[apix-meme] 记录 UMO 失败")

        ac = self.admin_commands

        try:
            if self.checkin_module is not None and sender_id:
                self.checkin_module.record_speak(sender_id, group_id)
                self.checkin_module.settle_interest_if_needed()
            try:
                if self.speak_stats_module is not None:
                    self.speak_stats_module.record(
                        group_id, sender_id, get_sender_name(event)
                    )
            except Exception:
                logger.exception("[apix-meme] 记录发言统计失败")
        except Exception:
            pass

        if self.output_mode_module is not None and group_id:
            _om_mode, _om_err = self.output_mode_module.match_switch_cmd(msg_str)
            if _om_mode is not None or _om_err:
                if not await self._check_admin(event):
                    async for _r in self._send_content(event, "⚠️仅管理员/群主/群管/插件专属管理员可执行"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                if not _om_mode:
                    if _om_err:
                        async for _r in self._send_content(event, _om_err):
                            yield _r
                    else:
                        async for _r in self._send_content(event, self.output_mode_module.status_text(group_id)):
                            yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                if self.output_mode_module.set_mode(group_id, _om_mode):
                    try:
                        self._persist_output_modes()
                    except Exception:
                        logger.exception("[apix-meme] 保存输出模式失败")
                    _label = self.output_mode_module.MODE_LABELS.get(
                        _om_mode, _om_mode
                    )
                    async for _r in self._send_content(event, f"✅ 本群输出模式已切换为：{_label}\n"
                        f"💡 再发「{self.output_mode_module.cmd_names[0]}」可查看当前模式"):
                        yield _r
                else:
                    async for _r in self._send_content(event, "❌ 切换失败（模式不合法）"):
                        yield _r
                GLOBAL_STATS["send"] += 1
                return

        if group_id and group_id in self.disabled_groups:
            if self._is_enable_cmd(msg_str):
                if not await self._check_admin(event):
                    async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                if ac is not None:
                    async for _r in self._send_content(event, ac.group_enable(group_id)):
                        yield _r
                else:
                    try:
                        self.disabled_groups.remove(group_id)
                        self.save_rules_to_config()
                        async for _r in self._send_content(event, "✅本群娱乐系统已开启"):
                            yield _r
                    except Exception:
                        async for _r in self._send_content(event, "❌操作失败"):
                            yield _r
                GLOBAL_STATS["send"] += 1
                return
            return

        if self._is_disable_cmd(msg_str):
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if ac is not None:
                async for _r in self._send_content(event, ac.group_disable(group_id)):
                    yield _r
            else:
                if group_id:
                    if group_id not in self.disabled_groups:
                        self.disabled_groups.append(group_id)
                        self.save_rules_to_config()
                        async for _r in self._send_content(event, "✅本群娱乐系统已关闭"):
                            yield _r
                    else:
                        async for _r in self._send_content(event, "本群已经处于关闭状态"):
                            yield _r
                else:
                    async for _r in self._send_content(event, "私聊无法关闭"):
                        yield _r
            GLOBAL_STATS["send"] += 1
            return

        arg_add_admin = self._is_add_admin(msg_str)
        if arg_add_admin is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if ac is None:
                logger.warning("[apix-meme] ❌管理指令模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, f"✅已添加插件专属管理员：{arg_add_admin}" if ac.add_plugin_admin(arg_add_admin)
                else "⚠️参数无效"):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        arg_del_admin = self._is_del_admin(msg_str)
        if arg_del_admin is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if ac is None:
                logger.warning("[apix-meme] ❌管理指令模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, f"✅已删除插件专属管理员：{arg_del_admin}" if ac.del_plugin_admin(arg_del_admin)
                else f"⚠️未找到该管理员：{arg_del_admin}"):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._is_admin_list_cmd(msg_str):
            if ac is None:
                logger.warning("[apix-meme] ❌管理指令模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            admins = ac.list_plugin_admins()
            if not admins:
                async for _r in self._send_content(event, f"{title_bar('插件专属管理员')}\n（无）"):
                    yield _r
            else:
                lines = [f"{title_bar('插件专属管理员（{len(admins)} 人）')}"]
                for i, a in enumerate(admins, start=1):
                    lines.append(f"{i}：{a}")
                async for _r in self._send_content(event, "\n".join(lines)):
                    yield _r
            GLOBAL_STATS["send"] += 1
            return

        # ★★ 1.5.0：**艾特状态** —— 是否要求 @ 机器人才能玩（每群独立）
        #   用法：`开启功能 艾特状态` / `关闭功能 艾特状态`
        _ma_hit = None
        _ma_t = str(msg_str or "").strip()
        for _on_off in ("开启功能", "关闭功能"):
            for _base in (_on_off, f"/{_on_off}"):
                if not _ma_t.startswith(_base):
                    continue
                _rest = _ma_t[len(_base):].strip(" 　:：,，、|—")
                if _rest.startswith("艾特状态"):
                    _tail = _rest[len("艾特状态"):].strip(" 　:：,，、|—")
                    _ma_hit = (_tail, _on_off == "开启功能")
        if _ma_hit is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管/插件管理员可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if ac is None:
                logger.warning("[apix-meme] ❌管理指令模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            _ma_gid = str(_ma_hit[0] or _get_group_id_from_event(event) or "").strip()
            if not _ma_gid or not _ma_gid.isdigit():
                async for _r in self._send_content(
                        event, "⚠️ 请在群聊中使用，或带上群号：\n"
                               "· 开启功能 艾特状态 群号\n"
                               "· 关闭功能 艾特状态 群号"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            try:
                # ★ 先把内存里的值写好，**再**保存 ——
                #   save_rules_to_config() 会用磁盘旧值回填，
                #   先保存的话刚设的值会被盖回去。
                if not hasattr(self, "group_must_at_bot") \
                        or self.group_must_at_bot is None:
                    self.group_must_at_bot = {}
                self.group_must_at_bot[str(_ma_gid)] = bool(_ma_hit[1])
                self.save_rules_to_config()
                _ma_txt = (f"✅ 已{'开启' if _ma_hit[1] else '关闭'}"
                           f"群【{_ma_gid}】的「必须艾特机器人」限制")
            except Exception:
                logger.exception("[apix-meme] 写「艾特状态」失败")
                _ma_txt = "❌ 设置失败，请查看后台日志"
            _ma_tip = "需要 @ 机器人才能玩" if _ma_hit[1] else "不需要 @ 机器人"
            async for _r in self._send_content(
                    event, f"{_ma_txt}\n💡 本群现在：**{_ma_tip}**"):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        arg_disable_feature, target_gid_dis = self._is_disable_feature(msg_str)
        if arg_disable_feature is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管/插件管理员可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if ac is None:
                logger.warning("[apix-meme] ❌管理指令模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            cur_group_id = _get_group_id_from_event(event)
            async for _r in self._send_content(event, ac.disable_feature(arg_disable_feature, group_id=cur_group_id, target_group=target_gid_dis)):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        arg_enable_feature, target_gid_en = self._is_enable_feature(msg_str)
        if arg_enable_feature is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管/插件管理员可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if ac is None:
                logger.warning("[apix-meme] ❌管理指令模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            cur_group_id = _get_group_id_from_event(event)
            async for _r in self._send_content(event, ac.enable_feature(arg_enable_feature, group_id=cur_group_id, target_group=target_gid_en)):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        is_status_cmd, target_gid_status = self._is_feature_status_cmd(msg_str)
        if is_status_cmd:
            if ac is None:
                logger.warning("[apix-meme] ❌管理指令模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            cur_group_id = _get_group_id_from_event(event)
            async for _r in self._send_content(event, ac.feature_status_text(group_id=cur_group_id, target_group=target_gid_status)):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        must_at_on_args = self._is_must_at_on_cmd(msg_str)
        if must_at_on_args is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if ac is None:
                logger.warning("[apix-meme] ❌管理指令模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            target_gid = group_id
            if must_at_on_args[0] == "target":
                target_gid = must_at_on_args[1]
            if not target_gid or not str(target_gid).isdigit():
                async for _r in self._send_content(event, f"⚠️ 请在群聊中使用，或指定群号：{self.cmd_must_at_bot_on_name} 群号"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, ac.set_group_must_at_bot(target_gid, True)):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        must_at_off_args = self._is_must_at_off_cmd(msg_str)
        if must_at_off_args is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if ac is None:
                logger.warning("[apix-meme] ❌管理指令模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            target_gid = group_id
            if must_at_off_args[0] == "target":
                target_gid = must_at_off_args[1]
            if not target_gid or not str(target_gid).isdigit():
                async for _r in self._send_content(event, f"⚠️ 请在群聊中使用，或指定群号：{self.cmd_must_at_bot_off_name} 群号"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, ac.set_group_must_at_bot(target_gid, False)):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        must_at_status_args = self._is_must_at_status_cmd(msg_str)
        if must_at_status_args is not None:
            if ac is None:
                logger.warning("[apix-meme] ❌管理指令模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            cur_group_id = _get_group_id_from_event(event)
            target_gid = None
            if must_at_status_args[0] == "target":
                target_gid = must_at_status_args[1]
            async for _r in self._send_content(event, ac.must_at_bot_status_text(group_id=cur_group_id, target_group=target_gid)):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._should_require_at(group_id):
            if not has_at_bot(event, bot_id):
                return

        if self.greeting_module is not None:
            for _rk_kind, _rk_fn in (
                    ("days", "build_rank_by_days"),
                    ("awake", "build_rank_by_awake"),
                    ("sleep", "build_rank_by_sleep")):
                try:
                    _hit = bool(self.greeting_module.match_rank_kind(
                        msg_str, _rk_kind))
                except Exception:
                    _hit = False
                if not _hit:
                    continue
                if not self._is_group_feature_enable(group_id, "greeting"):
                    GLOBAL_STATS["send"] += 1
                    return
                try:
                    async for _r in self._send_content(
                            event,
                            getattr(self.greeting_module, _rk_fn)()):
                        yield _r
                except Exception:
                    logger.exception(
                        f"[apix-meme] 打卡排行({_rk_kind})失败")
                    async for _r in self._send_content(event, "❌ 查询失败"):
                        yield _r
                GLOBAL_STATS["send"] += 1
                return

        gstats_args = self._is_greeting_stats_cmd(msg_str)
        if gstats_args is not None:
            if not self._is_group_feature_enable(group_id, "greeting"):
                GLOBAL_STATS["send"] += 1
                return
            if self.greeting_module is None:
                logger.warning("[apix-meme] ❌ 早安晚安模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            try:
                if gstats_args[0] == "self":
                    target_uid = to_str(event.get_sender_id())
                    target_name = get_sender_name(event)
                    text = self.greeting_module.build_stats_text(target_uid, target_name)
                else:
                    raw = gstats_args[1]
                    target_uid = ""
                    try:
                        at_list = get_ats_from_msg(event)
                        if at_list:
                            target_uid = at_list[0]
                    except Exception:
                        pass
                    if not target_uid and raw.isdigit():
                        target_uid = raw
                    if not target_uid:
                        cleaned = raw.lstrip("@").strip()
                        if cleaned.isdigit():
                            target_uid = cleaned
                    if not target_uid:
                        async for _r in self._send_content(event, f"⚠️ 请用「{self.cmd_greeting_stats_name} @某人」"
                            f"或「{self.cmd_greeting_stats_name} QQ号」查询"):
                            yield _r
                        GLOBAL_STATS["send"] += 1
                        return
                    text = self.greeting_module.build_stats_text(target_uid)
                async for _r in self._send_content(event, text):
                    yield _r
            except Exception:
                logger.exception("[apix-meme] 打卡统计失败")
                async for _r in self._send_content(event, "❌ 查询失败"):
                    yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._is_checkin_cmd(msg_str):
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if self.checkin_module is None:
                logger.warning("[apix-meme] ❌ 签到模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            try:
                self.checkin_module.settle_interest_if_needed()
                text = self.checkin_module.do_checkin(event)
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 签到失败")
                async for _r in self._send_content(event, "❌ 签到失败"):
                    yield _r
            return

        ss = self.speak_stats_module
        if ss is not None and ss.enable:
            def _ss_ok() -> bool:
                if not ss.is_group_allowed(group_id):
                    logger.warning(
                        f"[apix-meme] 群 {group_id} 未开启发言统计，忽略指令"
                    )
                    return False
                return True

            if ss.match_rank_yesterday(msg_str):
                if _ss_ok():
                    async for _r in self._send_content(event, ss.rank_yesterday_text(group_id)):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                return

            if ss.match_rank_active(msg_str):
                if _ss_ok():
                    async for _r in self._send_content(event, ss.rank_active_text(group_id)):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                return

            if ss.match_rank_week(msg_str):
                if _ss_ok():
                    async for _r in self._send_content(event, ss.rank_week_text(group_id)):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                return

            if ss.match_rank_month(msg_str):
                if _ss_ok():
                    async for _r in self._send_content(event, ss.rank_month_text(group_id)):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                return

            if ss.match_my_stat(msg_str):
                if _ss_ok():
                    uid = to_str(event.get_sender_id())
                    sname = get_sender_name(event)
                    async for _r in self._send_content(event, ss.my_stat_text(group_id, uid, sname)):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                return

        info_args = self._is_checkin_info_cmd(msg_str)
        if info_args is not None:
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if self.checkin_module is None:
                logger.warning("[apix-meme] ❌ 签到模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            try:
                if info_args[0] == "self":
                    target_uid = to_str(event.get_sender_id())
                    target_name = get_sender_name(event)
                    text = self.checkin_module.build_user_info_text(group_id, target_uid, target_name)
                else:
                    raw = info_args[1]
                    target_uid = ""
                    try:
                        at_list = get_ats_from_msg(event)
                        if at_list:
                            target_uid = at_list[0]
                    except Exception:
                        pass
                    if not target_uid and raw.isdigit():
                        target_uid = raw
                    if not target_uid:
                        cleaned = raw.lstrip("@").strip()
                        if cleaned.isdigit():
                            target_uid = cleaned
                    if not target_uid:
                        _hint = self._checkin_first_trigger()
                        async for _r in self._send_content(event, f"⚠️ 请用「{_hint} @某人」或「{_hint} QQ号」查询"):
                            yield _r
                        GLOBAL_STATS["send"] += 1
                        return
                    text = self.checkin_module.build_user_info_text(group_id, target_uid)
                async for _r in self._send_content(event, text):
                    yield _r
            except Exception:
                logger.exception("[apix-meme] 签到统计失败")
                async for _r in self._send_content(event, "❌ 查询失败"):
                    yield _r
            GLOBAL_STATS["send"] += 1
            return

        steal_args = self.checkin_module.match_steal_trigger(msg_str) if self.checkin_module else None
        if steal_args is not None:
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "steal"):
                GLOBAL_STATS["send"] += 1
                return
            try:
                self.checkin_module.settle_interest_if_needed()
                target_raw = ""
                if steal_args[0] == "target":
                    target_raw = steal_args[1]
                    try:
                        at_list = get_ats_from_msg(event)
                        if at_list:
                            target_raw = at_list[0]
                    except Exception:
                        pass
                if not target_raw:
                    async for _r in self._send_content(event, f"⚠️ 请指定要偷的人\n"
                        f"用法：「{self.checkin_module.steal_trigger_names[0]} @某人」"
                        f"或「{self.checkin_module.steal_trigger_names[0]} QQ号」"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                text = self.checkin_module.do_steal(event, target_raw)
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 偷积分失败")
                async for _r in self._send_content(event, "❌ 偷取失败"):
                    yield _r
            return

        if self.checkin_module is not None:
            _wipe = self.checkin_module.match_wipe_wealth(msg_str)
            if _wipe is not None:
                _wcmd = self.checkin_module.wipe_wealth_trigger_names[0]
                if not self.score_admin_enable:
                    logger.info("[apix-meme] 积分调整总开关关闭，忽略 %s", _wcmd)
                    GLOBAL_STATS["send"] += 1
                    return
                if not group_allowed(group_id, self.score_admin_groups):
                    logger.warning(
                        "[apix-meme] 群 %s 未在「积分调整指定群」里，忽略 %s",
                        group_id, _wcmd)
                    GLOBAL_STATS["send"] += 1
                    return
                if not self._is_group_feature_enable(group_id, "score_admin"):
                    GLOBAL_STATS["send"] += 1
                    return
                if not self._can_adjust_score(event):
                    logger.warning(
                        "[apix-meme] %s 无权限使用「%s」（发件人 %s，群 %s）",
                        get_sender_name(event) or event.get_sender_id(),
                        _wcmd, event.get_sender_id(), group_id)
                    async for _r in self._send_content(
                            event,
                            f"⚠️ 你没有权限使用「{_wcmd}」\n"
                            f"💡 只允许 AstrBot 全局管理员，"
                            f"或后台「加减积分 QQ 白名单」里的人"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                try:
                    text = self.checkin_module.do_wipe_wealth(event, _wipe)
                    if text:
                        async for _r in self._send_content(event, text):
                            yield _r
                        GLOBAL_STATS["send"] += 1
                except Exception:
                    logger.exception("[apix-meme] 清空财富失败")
                    async for _r in self._send_content(event, "❌ 清空失败"):
                        yield _r
                return

        score_cmd = None
        _dbg_hit = False
        if self.checkin_module is not None:
            for _key, _delta, _match in (
                ("add", 1, self.checkin_module.match_score_add),
                ("sub", -1, self.checkin_module.match_score_sub),
            ):
                _hit = _match(msg_str)
                if _hit is not None:
                    score_cmd = (_key, _delta, _hit)
                    _dbg_hit = True
                    break
        if (not _dbg_hit
                and any(x in msg_str for x in ("积分", "加积分", "减积分"))):
            logger.info(
                "[apix-meme][追踪] 积分指令未命中：msg_str=%r "
                "names_add=%r names_sub=%r checkin=%s",
                msg_str,
                getattr(self.checkin_module, "score_add_trigger_names", None),
                getattr(self.checkin_module, "score_sub_trigger_names", None),
                self.checkin_module is not None)
        elif _dbg_hit:
            logger.info(
                "[apix-meme][追踪] 积分指令命中：msg_str=%r -> %r",
                msg_str, score_cmd)
        if score_cmd is not None:
            _key, _delta, _hit = score_cmd
            _cmd_name = "加积分" if _delta >= 0 else "减积分"
            _verb = "增加" if _delta >= 0 else "减少"
            if not self.score_admin_enable:
                logger.info("[apix-meme] 积分调整总开关关闭，忽略 %s", _cmd_name)
                GLOBAL_STATS["send"] += 1
                return
            if not group_allowed(group_id, self.score_admin_groups):
                logger.warning(
                    "[apix-meme] 群 %s 未在「积分调整指定群」里，忽略 %s",
                    group_id, _cmd_name)
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "score_admin"):
                logger.info("[apix-meme] 本群已关闭「积分调整」功能，忽略 %s",
                            _cmd_name)
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "checkin"):
                logger.info("[apix-meme] 本群已关闭「签到系统」功能，忽略 %s",
                            _cmd_name)
                GLOBAL_STATS["send"] += 1
                return
            _who = to_str(event.get_sender_id())
            if not self._can_adjust_score(event):
                logger.warning(
                    "[apix-meme] %s 既不是 AstrBot 全局管理员，"
                    "也不在加减积分白名单里，拒绝「%s」"
                    "（发件人 %s，群 %s）",
                    get_sender_name(event) or _who, _cmd_name, _who, group_id)
                async for _r in self._send_content(
                        event,
                        f"⚠️ 你没有权限使用「{_cmd_name}」\n"
                        f"💡 只允许 AstrBot 全局管理员，"
                        f"或后台「加减积分 QQ 白名单」里的人"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            try:
                _names = (self.checkin_module.score_add_trigger_names if _delta >= 0
                          else self.checkin_module.score_sub_trigger_names)
                if _hit[0] == "help":
                    async for _r in self._send_content(event, f"{title_bar('{self._score_sn()}调整')}\n"
                        f"用法：「{_names[0]} @某人 数量」\n"
                        f"　 也可写「{_names[0]} QQ号 数量」\n"
                        f"　 例：{_names[0]} @小明 100"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                raw = _hit[1] if len(_hit) > 1 else ""
                qq, amount, _err = self.checkin_module.parse_score_args(raw)
                if not qq:
                    try:
                        _ats = get_ats_from_msg(event) or []
                        if _ats:
                            qq = str(_ats[0])
                    except Exception:
                        pass
                if not qq:
                    async for _r in self._send_content(
                            event,
                            f"⚠️ 没认出要{_verb}谁的{self._score_sn()}\n"
                            f"💡 用法：{_cmd_name} @某人 数量"
                            f"　或　{_cmd_name} QQ号 数量\n"
                            f"　 例：{_cmd_name} @小明 100"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                if amount is None or amount == 0:
                    async for _r in self._send_content(
                            event,
                            f"⚠️ 没认出要{_verb}多少{self._score_sn()}\n"
                            f"💡 用法：{_cmd_name} @某人 数量\n"
                            f"　 例：{_cmd_name} @小明 100"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                operator = get_sender_name(event) or to_str(event.get_sender_id())
                text = self.checkin_module.do_score_adjust(
                    group_id, qq, amount, delta=_delta, operator=operator
                )
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 积分调整失败")
                async for _r in self._send_content(event, "❌ 操作失败"):
                    yield _r
                GLOBAL_STATS["send"] += 1
            return

        if self.checkin_module is not None and \
                self.checkin_module.match_pool_trigger(msg_str):
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "lottery"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "lottery_pool"):
                GLOBAL_STATS["send"] += 1
                return
            try:
                text = self.checkin_module.do_pool_join(event)
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 奖池参与失败")
                async for _r in self._send_content(event, "❌ 操作失败"):
                    yield _r
            return

        if self.checkin_module is not None and \
                self.checkin_module.match_pool_info_trigger(msg_str):
            try:
                text = self.checkin_module.do_pool_info(event)
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 奖池信息失败")
            return

        if self.checkin_module is not None and \
                self.checkin_module.match_rob_trigger(msg_str):
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "bank"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "rob"):
                GLOBAL_STATS["send"] += 1
                return
            try:
                text = self.checkin_module.do_rob_bank(event)
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 抢银行失败")
                async for _r in self._send_content(event, "❌ 操作失败"):
                    yield _r
            return

        if self.checkin_module is not None:
            _fund_hit = (
                self.checkin_module.match_fund_trigger(msg_str)
                or self.checkin_module.match_fund_buy_trigger(msg_str)
                is not None
                or self.checkin_module.match_fund_sell_trigger(msg_str)
                is not None
            )
            if _fund_hit:
                if not self._is_group_feature_enable(group_id, "checkin"):
                    GLOBAL_STATS["send"] += 1
                    return
                if not self._is_group_feature_enable(group_id, "bank"):
                    GLOBAL_STATS["send"] += 1
                    return
                try:
                    _fb = self.checkin_module.match_fund_buy_trigger(msg_str)
                    _fs = self.checkin_module.match_fund_sell_trigger(msg_str)
                    if _fb is not None:
                        text = self.checkin_module.do_fund_buy(event, _fb)
                    elif _fs is not None:
                        text = self.checkin_module.do_fund_sell(event, _fs)
                    else:
                        text = self.checkin_module.do_fund_view(event)
                    if text:
                        async for _r in self._send_content(event, text):
                            yield _r
                        GLOBAL_STATS["send"] += 1
                except Exception:
                    logger.exception("[apix-meme] 理财操作失败")
                    async for _r in self._send_content(event, "❌ 操作失败"):
                        yield _r
                return

        if self.checkin_module is not None and self.checkin_module.match_bank_trigger(msg_str):
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "bank"):
                GLOBAL_STATS["send"] += 1
                return
            try:
                self.checkin_module.settle_interest_if_needed()
                text = self.checkin_module.do_bank_info(event)
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 银行查询失败")
                async for _r in self._send_content(event, "❌ 查询失败"):
                    yield _r
            return

        deposit_args = self.checkin_module.match_deposit_trigger(msg_str) if self.checkin_module else None
        if deposit_args is not None:
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "bank"):
                GLOBAL_STATS["send"] += 1
                return
            try:
                self.checkin_module.settle_interest_if_needed()
                text = self.checkin_module.do_deposit(event, deposit_args[1])
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 存钱失败")
                async for _r in self._send_content(event, "❌ 操作失败"):
                    yield _r
            return

        withdraw_args = self.checkin_module.match_withdraw_trigger(msg_str) if self.checkin_module else None
        if withdraw_args is not None:
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "bank"):
                GLOBAL_STATS["send"] += 1
                return
            try:
                self.checkin_module.settle_interest_if_needed()
                text = self.checkin_module.do_withdraw(event, withdraw_args[1])
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 取钱失败")
                async for _r in self._send_content(event, "❌ 操作失败"):
                    yield _r
            return

        loan_args = self.checkin_module.match_loan_trigger(msg_str) if self.checkin_module else None
        if loan_args is not None:
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "bank"):
                GLOBAL_STATS["send"] += 1
                return
            try:
                self.checkin_module.settle_interest_if_needed()
                text = self.checkin_module.do_loan(event, loan_args[1])
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 借款失败")
                async for _r in self._send_content(event, "❌ 操作失败"):
                    yield _r
            return

        repay_args = self.checkin_module.match_repay_trigger(msg_str) if self.checkin_module else None
        if repay_args is not None:
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "bank"):
                GLOBAL_STATS["send"] += 1
                return
            try:
                self.checkin_module.settle_interest_if_needed()
                text = self.checkin_module.do_repay(event, repay_args[1])
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 还款失败")
                async for _r in self._send_content(event, "❌ 操作失败"):
                    yield _r
            return

        if self.checkin_module is not None and self.checkin_module.match_loan_info_trigger(msg_str):
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "bank"):
                GLOBAL_STATS["send"] += 1
                return
            try:
                text = self.checkin_module.do_loan_info(event)
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 借款查询失败")
                async for _r in self._send_content(event, "❌ 查询失败"):
                    yield _r
            return

        if self.checkin_module is not None and \
                self.checkin_module.match_lottery_all_trigger(msg_str):
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "lottery"):
                GLOBAL_STATS["send"] += 1
                return
            try:
                text = self.checkin_module.do_lottery_all(event)
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 全部抽奖失败")
                async for _r in self._send_content(event, "❌ 抽奖失败"):
                    yield _r
            return

        if self.checkin_module is not None and \
                self.checkin_module.match_lottery_trigger(msg_str):
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "lottery"):
                GLOBAL_STATS["send"] += 1
                return
            try:
                text = self.checkin_module.do_lottery(event)
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 抽奖失败")
                async for _r in self._send_content(event, "❌ 抽奖失败"):
                    yield _r
            return

        for _rk_kind, _rk_fn in (
                ("count", "build_rank_by_count"),
                ("wealth", "build_rank_by_wealth"),
                ("debt", "build_rank_by_debt")):
            _rk_args = (self.checkin_module.match_rank_kind(msg_str, _rk_kind)
                        if self.checkin_module is not None else False)
            if not _rk_args:
                continue
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            try:
                try:
                    _mids = await self._group_member_ids(event, group_id)
                except Exception:
                    _mids = None
                text = getattr(self.checkin_module, _rk_fn)(
                    group_id, member_ids=_mids)
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception(f"[apix-meme] 签到排行({_rk_kind})失败")
                async for _r in self._send_content(event, "❌ 查询失败"):
                    yield _r
            return

        if self._is_stats_menu(msg_str):
            # ★★ 1.4.0：远程指令「运行数据」（只读，固定文本输出）
            _gid_st = str(_get_group_id_from_event(event) or "")
            async for _r in self._send_content(
                    event, self._build_stats_menu(event, _gid_st)):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._is_checkin_mount_menu(msg_str):
            if self._is_group_feature_enable(group_id, "checkin") \
                    and self._is_group_feature_enable(group_id, "mount"):
                async for _r in self._send_content(
                        event, self._build_checkin_mount_menu()):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return

        mount_args = self.checkin_module.match_mount_trigger(msg_str) if self.checkin_module else None

        mk_equip = self.checkin_module.match_mount_equip_trigger(msg_str) \
            if self.checkin_module else None
        mk_view = self.checkin_module.match_mount_view_trigger(msg_str) \
            if self.checkin_module else None
        if mk_equip is not None or mk_view is not None:
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "mount"):
                GLOBAL_STATS["send"] += 1
                return
            _is_view = mk_view is not None
            _mname = (mk_view if _is_view else mk_equip) or ""
            try:
                _txt = (self.checkin_module.do_mount_view(event, _mname)
                        if _is_view
                        else self.checkin_module.do_mount_equip(event, _mname))
                if _txt:
                    async for _r in self._send_content(event, _txt):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 坐骑装备/查看失败")
                async for _r in self._send_content(event, "❌ 操作失败"):
                    yield _r
            return

        _ml_page = self.checkin_module.match_mount_list_trigger(msg_str) \
            if self.checkin_module else None
        if _ml_page is not None:
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "mount"):
                GLOBAL_STATS["send"] += 1
                return
            try:
                _txt = self.checkin_module.do_mount_list(_ml_page)
                if _txt:
                    async for _r in self._send_content(event, _txt):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 坐骑列表失败")
                async for _r in self._send_content(event, "❌ 查询失败"):
                    yield _r
            return

        if mount_args is not None:
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "mount"):
                GLOBAL_STATS["send"] += 1
                return
            try:
                _mpage = mount_args[1] if len(mount_args) > 1 else 1
                text = self.checkin_module.do_mount_info(event, _mpage)
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 坐骑查询失败")
                async for _r in self._send_content(event, "❌ 查询失败"):
                    yield _r
            return

        mount_buy_args = self.checkin_module.match_mount_buy_trigger(msg_str) if self.checkin_module else None
        if mount_buy_args is not None:
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "mount"):
                GLOBAL_STATS["send"] += 1
                return
            try:
                text = self.checkin_module.do_mount_buy(event, mount_buy_args[1])
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 购买坐骑失败")
                async for _r in self._send_content(event, "❌ 操作失败"):
                    yield _r
            return

        if self.checkin_module is not None:
            _sall = self.checkin_module.match_stamina_refill_all(msg_str)
            _sst = self.checkin_module.match_stamina_refill_status(msg_str)
            _sr = self.checkin_module.match_stamina_refill_trigger(msg_str)
            _se2 = self.checkin_module.match_food_eat_trigger(msg_str)
            _sview = self.checkin_module.match_stamina_view_trigger(msg_str)
            _sr_bare = (_sr is not None and str(_sr).strip() == "")
            _st_hit = bool(
                _sview or _se2 is not None or _sall or _sst
                or _sr is not None
            )
            if _st_hit:
                if not self._is_group_feature_enable(group_id, "checkin"):
                    GLOBAL_STATS["send"] += 1
                    return
                try:
                    if _se2 is not None:
                        text = self.checkin_module.do_food_eat(event, _se2)
                    elif _sall:
                        text = self.checkin_module.do_stamina_refill(
                            event, "__all__")
                    elif _sst:
                        text = self.checkin_module.do_stamina_refill(event, "")
                    elif _sr_bare:
                        text = ""
                    elif _sr is not None:
                        text = self.checkin_module.do_stamina_refill(event, _sr)
                    else:
                        text = self.checkin_module.do_stamina_view(event)
                    if text:
                        async for _r in self._send_content(event, text):
                            yield _r
                        GLOBAL_STATS["send"] += 1
                except Exception:
                    logger.exception("[apix-meme] 体力功能失败")
                    async for _r in self._send_content(event, "❌ 操作失败"):
                        yield _r
                return

        if self.checkin_module is not None:
            _shop_arg = self.checkin_module.match_shop_trigger(msg_str)
            _shop_hit = (
                _shop_arg is not None
                or self.checkin_module.match_shop_buy_trigger(msg_str) is not None
                or self.checkin_module.match_shop_save_trigger(msg_str) is not None
                or self.checkin_module.match_shop_take_trigger(msg_str) is not None
                or self.checkin_module.match_shop_equip_trigger(msg_str) is not None
                or self.checkin_module.match_my_equip_trigger(msg_str) is not None
            )
            if _shop_hit:
                if not self._is_group_feature_enable(group_id, "checkin"):
                    GLOBAL_STATS["send"] += 1
                    return
                if not self._is_group_feature_enable(group_id, "shop"):
                    GLOBAL_STATS["send"] += 1
                    return
                try:
                    _sv = self.checkin_module.match_shop_buy_trigger(msg_str)
                    _ss = self.checkin_module.match_shop_save_trigger(msg_str)
                    _st = self.checkin_module.match_shop_take_trigger(msg_str)
                    _se = self.checkin_module.match_shop_equip_trigger(msg_str)
                    _me = self.checkin_module.match_my_equip_trigger(msg_str)
                    if _se is not None:
                        text = self.checkin_module.do_shop_equip(event, _se)
                    elif _me:
                        text = self.checkin_module.do_my_equip(event)
                    elif _sv is not None:
                        text = self.checkin_module.do_shop_buy(event, _sv)
                    elif _ss is not None:
                        text = self.checkin_module.do_shop_save(event, _ss)
                    elif _st is not None:
                        text = self.checkin_module.do_shop_take(event, _st)
                    else:
                        text = self.checkin_module.do_shop_list(
                            event=event, page=_shop_arg or 1)
                    if text:
                        async for _r in self._send_content(event, text):
                            yield _r
                        GLOBAL_STATS["send"] += 1
                except Exception:
                    logger.exception("[apix-meme] 签到商店失败")
                    async for _r in self._send_content(event, "❌ 操作失败"):
                        yield _r
                return

        if self.checkin_module is not None and self.checkin_module.match_job_info_trigger(msg_str):
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "job"):
                GLOBAL_STATS["send"] += 1
                return
            try:
                async for _r in self._send_content(event, self.checkin_module.do_job_list()):
                    yield _r
                GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 打工列表失败")
                async for _r in self._send_content(event, "❌ 查询失败"):
                    yield _r
            return

        if self.checkin_module is not None and \
                self.checkin_module.match_job_all_trigger(msg_str):
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "job"):
                GLOBAL_STATS["send"] += 1
                return
            try:
                _txt = self.checkin_module.do_job_work_all(event)
                if _txt:
                    async for _r in self._send_content(event, _txt):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 全部打工失败")
                async for _r in self._send_content(event, "❌ 操作失败"):
                    yield _r
            return

        job_args = self.checkin_module.match_job_trigger(msg_str) if self.checkin_module else None
        if job_args is not None:
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "job"):
                GLOBAL_STATS["send"] += 1
                return
            try:
                if job_args[0] == "list":
                    text = self.checkin_module.do_job_list()
                else:
                    text = self.checkin_module.do_job_work(event, job_args[1])
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 打工失败")
                async for _r in self._send_content(event, "❌ 操作失败"):
                    yield _r
            return

        job_add_arg = self.checkin_module.match_job_add_cmd(msg_str) if self.checkin_module else None
        if job_add_arg is not None:
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "job"):
                GLOBAL_STATS["send"] += 1
                return
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            try:
                async for _r in self._send_content(event, self.checkin_module.do_job_add(job_add_arg)):
                    yield _r
                try:
                    _rows = []
                    for j in self.checkin_module.job_list:
                        _rows.append({
                            "name": j["name"],
                            "duration_hours": str(j["duration_hours"]),
                            "reward_min": str(j["reward_min"]),
                            "reward_max": str(j["reward_max"]),
                        })
                    self._save_collection("job_list", _rows)
                except Exception:
                    logger.exception("[apix-meme] 保存工种失败")
                GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 添加工种失败")
                async for _r in self._send_content(event, "❌ 操作失败"):
                    yield _r
            return

        job_del_arg = self.checkin_module.match_job_del_cmd(msg_str) if self.checkin_module else None
        if job_del_arg is not None:
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "job"):
                GLOBAL_STATS["send"] += 1
                return
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            try:
                async for _r in self._send_content(event, self.checkin_module.do_job_del(job_del_arg)):
                    yield _r
                try:
                    _rows = []
                    for j in self.checkin_module.job_list:
                        _rows.append({
                            "name": j["name"],
                            "duration_hours": str(j["duration_hours"]),
                            "reward_min": str(j["reward_min"]),
                            "reward_max": str(j["reward_max"]),
                        })
                    self._save_collection("job_list", _rows)
                except Exception:
                    logger.exception("[apix-meme] 保存工种失败")
                GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 删除工种失败")
                async for _r in self._send_content(event, "❌ 操作失败"):
                    yield _r
            return

        mount_add_arg = self.checkin_module.match_mount_add_cmd(msg_str) if self.checkin_module else None
        if mount_add_arg is not None:
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "mount"):
                GLOBAL_STATS["send"] += 1
                return
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            try:
                async for _r in self._send_content(event, self.checkin_module.do_mount_add(mount_add_arg)):
                    yield _r
                try:
                    _rows = []
                    for m in self.checkin_module.mount_list:
                        _rows.append({
                            "name": m["name"],
                            "price": str(m["price"]),
                            "desc": m["desc"],
                        })
                    self._save_collection("mount_list", _rows)
                except Exception:
                    logger.exception("[apix-meme] 保存坐骑失败")
                GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 添加坐骑失败")
                async for _r in self._send_content(event, "❌ 操作失败"):
                    yield _r
            return

        mount_del_arg = self.checkin_module.match_mount_del_cmd(msg_str) if self.checkin_module else None
        if mount_del_arg is not None:
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "mount"):
                GLOBAL_STATS["send"] += 1
                return
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            try:
                async for _r in self._send_content(event, self.checkin_module.do_mount_del(mount_del_arg)):
                    yield _r
                try:
                    _rows = []
                    for m in self.checkin_module.mount_list:
                        _rows.append({
                            "name": m["name"],
                            "price": str(m["price"]),
                            "desc": m["desc"],
                        })
                    self._save_collection("mount_list", _rows)
                except Exception:
                    logger.exception("[apix-meme] 保存坐骑失败")
                GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 删除坐骑失败")
                async for _r in self._send_content(event, "❌ 操作失败"):
                    yield _r
            return

        pet = self.pet_module
        if pet is not None and pet.enable:
            _pet_arg = None
            _pet_cmd = ""
            self._pet_pre = ""
            try:
                _names0 = getattr(pet, "cmd_duel", []) or []
                if isinstance(_names0, str):
                    _names0 = [_names0]
                for _nm0 in sorted([x for x in _names0 if x],
                                   key=len, reverse=True):
                    _idx = msg_str.find(_nm0)
                    if _idx > 0:
                        _pre = msg_str[:_idx].strip().lstrip("/").strip()
                        _pre = re.sub(r"@[^\s]*", " ", _pre)
                        _pre = re.sub(r"[\(（]\d{5,12}[\)）]", " ", _pre)
                        _pre = re.sub(r"\d{5,12}", " ", _pre)
                        self._pet_pre = " ".join(_pre.split()).strip()
                        break
            except Exception:
                logger.debug("[apix-meme] 解析我方宠物名失败", exc_info=True)
                self._pet_pre = ""

            for _cand, _name in (
                (pet.parse_buy, "buy"), (pet.parse_mine, "mine"),
                (pet.parse_sell, "sell"), (pet.parse_release, "release"),
                (pet.parse_shop, "shop"), (pet.parse_adventure, "adventure"),
                (pet.parse_view, "view"), (pet.parse_rest, "rest"),
                (pet.parse_duel, "duel"), (pet.parse_work, "work"),
                (pet.parse_his_pet, "his_pet"),
                (pet.parse_item_shop, "item_shop"),
                (pet.parse_item_buy, "item_buy"),
                (pet.parse_item_use, "item_use"),
            ):
                _got = _cand(msg_str)
                if _got is not None:
                    _pet_arg, _pet_cmd = _got, _name
                    break

            _pet_admin = ""
            if _pet_cmd == "":
                _a = pet.parse_add(msg_str)
                if _a is not None:
                    _pet_arg, _pet_cmd, _pet_admin = _a, "add", "add"
                else:
                    _a = pet.parse_del(msg_str)
                    if _a is not None:
                        _pet_arg, _pet_cmd, _pet_admin = _a, "del", "del"

            if _pet_cmd:
                if not pet.is_group_allowed(group_id):
                    logger.warning(
                        f"[apix-meme] 群 {group_id} 未开启宠物功能，忽略指令 {_pet_cmd}"
                    )
                    GLOBAL_STATS["send"] += 1
                    return
                if not self._is_group_feature_enable(group_id, "pet"):
                    GLOBAL_STATS["send"] += 1
                    return

                if _pet_admin and not await self._check_admin(event):
                    async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return

                uid = to_str(event.get_sender_id())
                uname = get_sender_name(event)

                def _pet_score_get() -> int:
                    """★ 1.7.6：可用资金 = 钱包 + 银行（自动用银行扣款时）"""
                    try:
                        if self.checkin_module is None:
                            return 0
                        if getattr(self.checkin_module, "use_bank_pay", True):
                            return int(self.checkin_module.avail_funds(
                                group_id, uid))
                        u = self.checkin_module._guser(group_id, uid)
                        return int(u.get("total_score", 0))
                    except Exception:
                        return 0

                def _pet_score_set(v: int):
                    """★ 兼容旧的「设置余额」语义：算出差额后走统一扣款/加钱"""
                    try:
                        if self.checkin_module is None:
                            return
                        ck2 = self.checkin_module
                        cur = _pet_score_get()
                        delta = int(v) - int(cur)
                        if delta < 0:
                            ck2.pay(group_id, uid, -delta)
                        elif delta > 0:
                            u = ck2._guser_setdefault(
                                group_id, uid,
                                {"total_score": 0, "total_checkin": 0,
                                 "last_date": "", "streak": 0,
                                 "month_map": {}, "bank": 0})
                            u["total_score"] = int(u.get("total_score", 0)) + delta
                        ck2._save()
                    except Exception:
                        logger.exception("[apix-meme] 宠物积分写入失败")

                try:
                    if self.checkin_module is not None:
                        _ck3 = self.checkin_module
                        pet.loan_hint = _ck3.loan_hint
                        pet.short_hint = (
                            lambda need: _ck3.short_hint(group_id, uid, need))
                        pet.pay_note = (
                            lambda r: _ck3.pay_note(r))
                except Exception:
                    pass

                try:
                    text = None
                    if _pet_cmd == "buy":
                        text = pet.buy(group_id, uid, _pet_arg,
                                       _pet_score_get, _pet_score_set)
                    elif _pet_cmd == "mine":
                        _mp = int(_pet_arg) if str(_pet_arg).isdigit() else 1
                        text = pet.mine_text(group_id, uid, page=_mp)
                    elif _pet_cmd == "sell":
                        text = pet.sell(group_id, uid, _pet_arg,
                                        _pet_score_get, _pet_score_set)
                    elif _pet_cmd == "release":
                        text = pet.release(group_id, uid, _pet_arg)
                    elif _pet_cmd == "shop":
                        page = int(_pet_arg) if str(_pet_arg).isdigit() else 1
                        text = pet.shop_text(group_id, page, uid)
                    elif _pet_cmd == "adventure":
                        text = pet.adventure(group_id, uid, _pet_arg,
                                             _pet_score_get, _pet_score_set)
                    elif _pet_cmd == "view":
                        text = pet.view_text(group_id, uid, _pet_arg, uname)
                    elif _pet_cmd == "rest":
                        text = pet.rest(group_id, uid, _pet_arg)
                    elif _pet_cmd == "work":
                        text = pet.work(group_id, uid, _pet_arg,
                                        _pet_score_get, _pet_score_set)
                    elif _pet_cmd == "duel":
                        tid, d_want = self._pet_parse_names(event, _pet_arg)
                        a_want = str(getattr(self, "_pet_pre", "") or "").strip()
                        if not tid:
                            text = (
                                f"⚠️ 请 @ 一位有宠物的群友\n"
                                f"用法：{pet.cmd_duel[0]} @某人\n"
                                f"　　　我的宠物名 {pet.cmd_duel[0]} @某人"
                                f" 对方宠物名")
                        elif tid == uid:
                            text = "⚠️ 不能和自己决斗"
                        else:
                            dname = ""
                            try:
                                dname = self._nickname_cache.get(tid, "") or ""
                            except Exception:
                                pass
                            text = pet.duel(group_id, uid, tid, uname, dname,
                                            a_want=a_want, d_want=d_want)
                    elif _pet_cmd == "his_pet":
                        tid = self._pet_parse_target(event, _pet_arg)
                        if not tid:
                            text = (f"⚠️ 请 @ 一位群友\n"
                                    f"用法：{pet.cmd_his_pet[0]} @某人")
                        else:
                            oname = ""
                            try:
                                oname = self._nickname_cache.get(tid, "") or ""
                            except Exception:
                                pass
                            text = pet.his_pets_text(group_id, tid, oname)
                    elif _pet_cmd == "item_shop":
                        text = pet.item_shop_text(group_id, uid)
                    elif _pet_cmd == "item_buy":
                        text = pet.buy_item(group_id, uid, _pet_arg,
                                            _pet_score_get, _pet_score_set)
                    elif _pet_cmd == "item_use":
                        text = pet.use_item(group_id, uid, _pet_arg)
                    elif _pet_cmd == "add":
                        text = pet.add_pet(_pet_arg)
                    elif _pet_cmd == "del":
                        text = pet.del_pet(_pet_arg)

                    if text:
                        async for _r in self._send_content(event, text):
                            yield _r
                        GLOBAL_STATS["send"] += 1
                except Exception:
                    logger.exception(f"[apix-meme] 宠物指令 {_pet_cmd} 失败")
                return

        if self._is_view_admin_cmd(msg_str):
            try:
                async for _r in self._send_content(event, await self._build_admin_list_text(event)):
                    yield _r
            except Exception:
                logger.exception("[apix-meme] 查看管理员失败")
                async for _r in self._send_content(event, "❌查询失败"):
                    yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._is_refresh_admin_cmd(msg_str):
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if self.group_admin_module is None:
                logger.warning("[apix-meme] ❌群管模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            try:
                info = await self.group_admin_module.get_group_admins(event, force=True)
                owner = info.get("owner", "")
                admins = info.get("admins", [])
                async for _r in self._send_content(event, f"✅本群群管缓存已刷新\n群主：{owner or '（无）'}\n管理员：{len(admins)} 人"):
                    yield _r
            except Exception:
                logger.exception("[apix-meme] 刷新群管失败")
                async for _r in self._send_content(event, "❌刷新失败"):
                    yield _r
            GLOBAL_STATS["send"] += 1
            return

        chime_on_args = self._is_chime_on_cmd(msg_str)
        if chime_on_args is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if self.hourly_chime_module is None:
                logger.warning("[apix-meme] ❌ 整点报时模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            target_gid = group_id
            if chime_on_args[0] == "target":
                target_gid = chime_on_args[1]
            if not target_gid or not str(target_gid).isdigit():
                async for _r in self._send_content(event, f"⚠️ 请在群聊中使用，或指定群号：{self.cmd_chime_on_name} 群号"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            self.hourly_chime_module.set_group_on(target_gid, True)
            scope = f"群【{target_gid}】" if chime_on_args[0] == "target" else "本群"
            async for _r in self._send_content(event, f"✅已开启{scope}：整点报时\n"
                f"💡 已从后台「整点报时」的群号黑名单中移除本群，报时恢复"):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        chime_off_args = self._is_chime_off_cmd(msg_str)
        if chime_off_args is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if self.hourly_chime_module is None:
                logger.warning("[apix-meme] ❌ 整点报时模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            target_gid = group_id
            if chime_off_args[0] == "target":
                target_gid = chime_off_args[1]
            if not target_gid or not str(target_gid).isdigit():
                async for _r in self._send_content(event, f"⚠️ 请在群聊中使用，或指定群号：{self.cmd_chime_off_name} 群号"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            self.hourly_chime_module.set_group_on(target_gid, False)
            scope = f"群【{target_gid}】" if chime_off_args[0] == "target" else "本群"
            async for _r in self._send_content(event, f"✅已关闭{scope}：整点报时\n"
                f"💡 已把群号写入后台「整点报时」的群号黑名单，本群不再报时"):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        chime_status_args = self._is_chime_status_cmd(msg_str)
        if chime_status_args is not None:
            if self.hourly_chime_module is None:
                logger.warning("[apix-meme] ❌ 整点报时模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            target_gid = group_id
            if chime_status_args[0] == "target":
                target_gid = chime_status_args[1]
            if not target_gid:
                async for _r in self._send_content(event, f"⚠️ 请在群聊中使用，或指定群号：{self.cmd_chime_status_name} 群号"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            on = self.hourly_chime_module.is_group_on(target_gid)
            hours = self.hourly_chime_module.get_group_hours(target_gid)
            tpl = self.hourly_chime_module.get_group_template(target_gid) or self.hourly_chime_module.default_template
            umo = self.hourly_chime_module.get_umo(target_gid)
            async for _r in self._send_content(event, "\n".join([
                f"{title_bar(f'群 {target_gid} 整点报时状态')}",
                f"开关：{'✅ 开启' if on else '❌ 关闭'}",
                f"报时整点：{'全部整点' if not hours else ', '.join(f'{h:02d}:00' for h in hours)}",
                f"文案：{tpl}",
                f"推送通道：{'✅ 已就绪' if umo else '⚠️ 尚未记录（本群发言一次后自动记录）'}",
                ])):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        chime_hours_args = self._is_chime_hours_cmd(msg_str)
        if chime_hours_args is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if self.hourly_chime_module is None:
                logger.warning("[apix-meme] ❌ 整点报时模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            if not group_id:
                async for _r in self._send_content(event, "⚠️ 请在群聊中使用该指令"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if chime_hours_args[0] == "query":
                hours = self.hourly_chime_module.get_group_hours(group_id)
                if not hours:
                    async for _r in self._send_content(event, f"本群当前报时时段：全部整点\n"
                        f"💡 用「{self.cmd_chime_hours_name} 8,12,20」改为指定整点\n"
                        f"💡 用「{self.cmd_chime_hours_name} all」恢复全部整点"):
                        yield _r
                else:
                    async for _r in self._send_content(event, "本群当前报时时段：" + ", ".join(f"{h:02d}:00" for h in hours)):
                        yield _r
                GLOBAL_STATS["send"] += 1
                return
            raw = chime_hours_args[1]
            if raw.strip().lower() in ("all", "全部"):
                self.hourly_chime_module.set_group_hours(group_id, [])
                async for _r in self._send_content(event, "✅ 已恢复为全部整点报时"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            parts = []
            for x in raw.replace("，", ",").split(","):
                x = x.strip()
                if x and x.isdigit():
                    parts.append(int(x))
            if not parts:
                async for _r in self._send_content(event, f"⚠️ 参数无效\n用法：「{self.cmd_chime_hours_name} 8,12,20」\n"
                    f"或：「{self.cmd_chime_hours_name} all」恢复全部整点"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            self.hourly_chime_module.set_group_hours(group_id, parts)
            async for _r in self._send_content(event, "✅ 本群报时时段已更新：" + ", ".join(f"{h:02d}:00" for h in sorted(parts))):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        chime_text_args = self._is_chime_text_cmd(msg_str)
        if chime_text_args is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if self.hourly_chime_module is None:
                logger.warning("[apix-meme] ❌ 整点报时模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            if not group_id:
                async for _r in self._send_content(event, "⚠️ 请在群聊中使用该指令"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if chime_text_args[0] == "query":
                tpl = self.hourly_chime_module.get_group_template(group_id) or self.hourly_chime_module.default_template
                async for _r in self._send_content(event, f"本群当前报时文案：\n{tpl}\n\n"
                    f"💡 用「{self.cmd_chime_text_name} 自定义文案」修改\n"
                    f"💡 文案里可用 {{hour}} 占位符"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            new_tpl = chime_text_args[1]
            if len(new_tpl) > 200:
                async for _r in self._send_content(event, "⚠️ 文案过长（最多 200 字）"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            self.hourly_chime_module.set_group_template(group_id, new_tpl)
            async for _r in self._send_content(event, f"✅ 本群报时文案已更新：\n{new_tpl}"):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._is_chime_list_cmd(msg_str):
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if self.hourly_chime_module is None:
                logger.warning("[apix-meme] ❌ 整点报时模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            items = self.hourly_chime_module.list_groups()
            if not items:
                async for _r in self._send_content(event, f"{title_bar('整点报时列表')}\n（暂无开启的群）"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            lines = [f"{title_bar('整点报时列表（{len(items)} 个群）')}"]
            for it in items:
                gid = it["group_id"]
                hours = it["hours"]
                hstr = "全部整点" if not hours else ", ".join(f"{h:02d}" for h in hours)
                umo = self.hourly_chime_module.get_umo(gid)
                umo_tag = "✅" if umo else "⚠️"
                lines.append(f"· 群 {gid} — {hstr} {umo_tag}")
            lines.append("")
            lines.append("💡 ✅=推送通道已就绪，⚠️=该群还没有发过消息")
            async for _r in self._send_content(event, "\n".join(lines)):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        chime_test_args = self._is_chime_test_cmd(msg_str)
        if chime_test_args is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if self.hourly_chime_module is None:
                logger.warning("[apix-meme] ❌ 整点报时模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            target_gid = group_id
            if chime_test_args[0] == "target":
                target_gid = chime_test_args[1]
            if not target_gid or not str(target_gid).isdigit():
                async for _r in self._send_content(event, f"⚠️ 请在群聊中使用，或指定群号：{self.cmd_chime_test_name} 群号"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            is_on = self.hourly_chime_module.is_group_on(target_gid)
            current_umo = get_event_umo(event)
            if current_umo:
                self.hourly_chime_module.record_umo(target_gid, current_umo)
            umo = self.hourly_chime_module.get_umo(target_gid)
            text = await self.hourly_chime_module.build_text_async(target_gid)
            images = []
            try:
                text, images = await self.render_group_text(
                str(target_gid), text,
                max_w=getattr(self.hourly_chime_module, "card_max_w", 0) or 0)
            except Exception:
                logger.exception("[apix-meme] 报时测试：输出模式渲染失败，回落纯文本")
                images = []
            ok = await self._push_mc_notification(str(target_gid), text,
                                                  umo=umo, images=images)
            if ok:
                if chime_test_args[0] == "target":
                    async for _r in self._send_content(event, f"✅ 已向群【{target_gid}】手动推送一条整点报时\n"
                        f"💡 该群开关状态：{'已开启' if is_on else '未开启'}"):
                        yield _r
                else:
                    async for _r in self._send_content(event, "✅ 手动触发成功，已向本群推送一条整点报时\n"
                        "💡 本次为测试推送，不影响正常整点判重"):
                        yield _r
            else:
                async for _r in self._send_content(event, "❌ 推送失败\n"
                    "可能原因：\n"
                    "  · 本群尚未记录 UMO（请先在本群发一条普通消息）\n"
                    "  · 平台实例 ID 未识别\n"
                    "请查看后台日志 [push] 相关信息"):
                    yield _r
            GLOBAL_STATS["send"] += 1
            return

        push_on_arg = self._is_daily_push_on_cmd(msg_str)
        if push_on_arg is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if self.status_push_module is None:
                logger.warning("[apix-meme] ❌ 定时状态推送模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            target_gid = push_on_arg or group_id
            if not target_gid or not str(target_gid).isdigit():
                async for _r in self._send_content(event, f"⚠️ 请在群聊中使用，或指定群号：{self.cmd_daily_push_on_name} 群号"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if not self.status_push_module.is_group_configurable(target_gid):
                async for _r in self._send_content(event, f"⚠️ 群【{target_gid}】在后台「电脑状态」的群号黑名单里\n"
                    f"💡 请先在后台「电脑状态 · 不生效的群号」中移除该群号"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            self.status_push_module.set_group_on(target_gid, True)
            iv = self.status_push_module.get_group_interval(target_gid)
            custom = self.status_push_module.get_group_raw_interval(target_gid)
            scope = f"群【{target_gid}】" if push_on_arg else "本群"
            async for _r in self._send_content(event, f"✅已开启{scope}：定时状态推送\n"
                f"⏱ 推送间隔：每 {iv} 分钟一次"
                f"{'（本群自定义）' if custom else '（跟随全局默认）'}\n"
                f"💡 已从后台「电脑状态」的群号黑名单中移除本群\n"
                f"💡 可用「{self.cmd_daily_push_time_name} {iv}」单独改间隔（单位分钟）"):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        push_off_arg = self._is_daily_push_off_cmd(msg_str)
        if push_off_arg is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if self.status_push_module is None:
                logger.warning("[apix-meme] ❌ 定时状态推送模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            target_gid = push_off_arg or group_id
            if not target_gid or not str(target_gid).isdigit():
                async for _r in self._send_content(event, f"⚠️ 请在群聊中使用，或指定群号：{self.cmd_daily_push_off_name} 群号"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            self.status_push_module.set_group_on(target_gid, False)
            scope = f"群【{target_gid}】" if push_off_arg else "本群"
            async for _r in self._send_content(event, f"✅已关闭{scope}：定时状态推送\n"
                f"💡 已把群号写入后台「电脑状态」的群号黑名单，本群不再接收定时推送"):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        push_time_args = self._is_daily_push_time_cmd(msg_str)
        if push_time_args is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if self.status_push_module is None:
                logger.warning("[apix-meme] ❌ 定时状态推送模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            m = self.status_push_module

            if push_time_args[0] == "self":
                target_gid = group_id
                if not target_gid:
                    async for _r in self._send_content(event, f"⏱ 全局默认推送间隔：每 {m.default_interval} 分钟一次\n"
                        f"💡 在群里用「{self.cmd_daily_push_time_name} 60」设置本群间隔"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                eff = m.get_group_interval(target_gid)
                raw_iv = m.get_group_raw_interval(target_gid)
                on = m.is_group_on(target_gid)
                last = m.get_last_push(target_gid)
                last_at = str(last.get("at", "") or "（尚未推送过）")
                async for _r in self._send_content(event, f"⏱ 群【{target_gid}】推送间隔：每 {eff} 分钟一次\n"
                    f"· 来源：{'本群自定义' if raw_iv else f'跟随全局默认（{m.default_interval} 分钟）'}\n"
                    f"· 开关：{'已开启' if on else '未开启'}\n"
                    f"· 上次推送：{last_at}\n"
                    f"💡 设置：{self.cmd_daily_push_time_name} 60\n"
                    f"💡 指定群：{self.cmd_daily_push_time_name} 群号 60"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return

            _, arg_gid, arg_time = push_time_args
            target_gid = arg_gid or group_id
            if not target_gid or not str(target_gid).isdigit():
                async for _r in self._send_content(event, f"⚠️ 无法确定群号\n"
                    f"用法：{self.cmd_daily_push_time_name} 60\n"
                    f"或：{self.cmd_daily_push_time_name} 群号 60"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if not arg_time:
                async for _r in self._send_content(event, f"⚠️ 间隔无效\n"
                    f"用法：{self.cmd_daily_push_time_name} 60（单位分钟，例如 60 = 每 60 分钟一次）"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if not m.set_group_interval(target_gid, arg_time):
                async for _r in self._send_content(event, f"⚠️ 间隔无效：{arg_time}\n"
                    f"请填分钟数（正整数），例如 30、60、120；也支持「1小时」"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            eff = m.get_group_interval(target_gid)
            on = m.is_group_on(target_gid)
            tip = "" if on else f"\n💡 该群推送开关还没开，用「{self.cmd_daily_push_on_name}」开启"
            async for _r in self._send_content(event, f"✅ 群【{target_gid}】推送间隔已设为每 {eff} 分钟一次{tip}"):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._is_daily_push_list_cmd(msg_str):
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if self.status_push_module is None:
                logger.warning("[apix-meme] ❌ 定时状态推送模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            m = self.status_push_module
            head = (
                f"{title_bar('定时状态推送列表')}\n"
                f"总开关：{'✅ 已开启' if m.enable else '❌ 未开启（后台配置中打开）'}\n"
                f"全局默认间隔：每 {m.default_interval} 分钟一次"
            )
            if not m.group_specs:
                head += "\n⚠️ 后台未填写推送群号，定时推送不会推送给任何群"
            items = m.list_groups()
            if not items:
                async for _r in self._send_content(event, head + "\n（暂无开启推送的群）"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            lines = [head, f"已开启的群：{len(items)} 个"]
            for it in items:
                gid = it["group_id"]
                umo_tag = "✅" if m.get_umo(gid) else "⚠️"
                src = "自定义" if it["custom"] else "默认"
                last = m.get_last_push(gid)
                push_info = ""
                if last:
                    last_ts = float(last.get("ts", 0) or 0)
                    if last.get("init") or last_ts <= 0:
                        push_info = "（尚未推送）"
                    else:
                        push_info = f"（上次 {fmt_seconds(time.time() - last_ts)}前）"
                lines.append(
                    f"· 群 {gid} — 每 {it['interval']} 分钟（{src}）{umo_tag} {push_info}"
                )
            lines.append("")
            lines.append("💡 ✅=推送通道已就绪，⚠️=该群还没有发过消息")
            async for _r in self._send_content(event, "\n".join(lines)):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        for _mn_on, _mn_attr in ((True, "cmd_daily_news_manual_on_name"),
                                 (False, "cmd_daily_news_manual_off_name")):
            if not self._name_list_hit(msg_str, _mn_attr):
                continue
            if not await self._check_admin(event):
                async for _r in self._send_content(
                        event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            mod = self.daily_news_module
            if mod is None:
                logger.warning("[apix-meme] ❌ 每日读报模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            _mn_tgt = ""
            try:
                _mn_tgt = self._extract_arg(
                    msg_str, getattr(self, _mn_attr)) or ""
            except Exception:
                _mn_tgt = ""
            _mn_gid = _mn_tgt or group_id
            if not _mn_gid or not str(_mn_gid).isdigit():
                async for _r in self._send_content(
                        event, "⚠️请在群聊中使用该指令"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            try:
                mod.set_manual_group(str(_mn_gid), bool(_mn_on))
            except Exception:
                logger.exception("[apix-meme] 写手动读报名单失败")
            async for _r in self._send_content(
                    event, f"{'✅' if _mn_on else '❌'} 读报功能"):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._is_daily_news_cmd(msg_str):
            try:
                mod = self.daily_news_module
                if mod is not None and not mod.is_manual_allowed(group_id):
                    logger.warning(
                        f"[apix-meme] 群 {group_id} 已关闭手动读报，忽略该指令；"
                        f"可用「开启功能 读报功能」恢复，"
                        f"或在后台「每日读报 · 手动读报不生效的群号」里移除本群"
                    )
                    GLOBAL_STATS["send"] += 1
                    return
            except Exception:
                logger.exception("[apix-meme] 手动读报名单校验失败")
            async for _r in self._send_content(event, "⏳ 正在获取今日读报，请稍候..."):
                yield _r
            GLOBAL_STATS["send"] += 1
            image_sent = False
            try:
                from .core.daily_news import NEWS_API_IMAGE
                media_path, kind = await download_media(NEWS_API_IMAGE, expect="image")
                if kind == "image":
                    yield event.chain_result([Image.fromFileSystem(media_path)])
                    GLOBAL_STATS["send"] += 1
                    image_sent = True
            except Exception:
                logger.exception("[apix-meme] 手动读报：获取图片失败")

            if not image_sent:
                try:
                    text = await _fetch_daily_news_text()
                    if text:
                        async for _r in self._send_content(event, text):
                            yield _r
                        GLOBAL_STATS["send"] += 1
                    else:
                        async for _r in self._send_content(event, "❌ 获取读报失败，请稍后重试或查看后台日志"):
                            yield _r
                except Exception:
                    logger.exception("[apix-meme] 手动读报：文字回退失败")
                    async for _r in self._send_content(event, "❌ 获取读报失败，请查看后台日志"):
                        yield _r
            return

        news_on_arg = self._is_daily_news_on_cmd(msg_str)
        if news_on_arg is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if self.daily_news_module is None:
                logger.warning("[apix-meme] ❌ 每日读报模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            target_gid = news_on_arg or group_id
            if not target_gid or not str(target_gid).isdigit():
                async for _r in self._send_content(event, f"⚠️ 请在群聊中使用，或指定群号：{self.cmd_daily_news_on_name} 群号"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            self.daily_news_module.set_group_on(target_gid, True)
            t = self.daily_news_module.get_group_time(target_gid)
            custom = self.daily_news_module.get_group_raw_time(target_gid)
            scope = f"群【{target_gid}】" if news_on_arg else "本群"
            async for _r in self._send_content(event, f"✅ 已开启{scope}的每日读报推送\n"
                f"🕐 推送时间：{t}{'（本群自定义）' if custom else '（跟随全局默认）'}\n"
                f"💡 可用「{self.cmd_daily_news_time_name} {t}」单独改时间"):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        news_off_arg = self._is_daily_news_off_cmd(msg_str)
        if news_off_arg is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if self.daily_news_module is None:
                logger.warning("[apix-meme] ❌ 每日读报模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            target_gid = news_off_arg or group_id
            if not target_gid or not str(target_gid).isdigit():
                async for _r in self._send_content(event, f"⚠️ 请在群聊中使用，或指定群号：{self.cmd_daily_news_off_name} 群号"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            self.daily_news_module.set_group_on(target_gid, False)
            scope = f"群【{target_gid}】" if news_off_arg else "本群"
            async for _r in self._send_content(event, f"✅ 已关闭{scope}的每日读报推送"):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        news_time_args = self._is_daily_news_time_cmd(msg_str)
        if news_time_args is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if self.daily_news_module is None:
                logger.warning("[apix-meme] ❌ 每日读报模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            m = self.daily_news_module

            if news_time_args[0] == "self":
                target_gid = group_id
                if not target_gid:
                    async for _r in self._send_content(event, f"🕐 全局默认读报时间：{m.default_time}\n"
                        f"💡 在群里用「{self.cmd_daily_news_time_name} 08:00」设置本群时间"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                eff = m.get_group_time(target_gid)
                raw_t = m.get_group_raw_time(target_gid)
                on = m.is_group_on(target_gid)
                async for _r in self._send_content(event, f"🕐 群【{target_gid}】读报时间：{eff}\n"
                    f"· 来源：{'本群自定义' if raw_t else '跟随全局默认（' + m.default_time + '）'}\n"
                    f"· 开关：{'已开启' if on else '未开启'}\n"
                    f"💡 设置：{self.cmd_daily_news_time_name} 08:00\n"
                    f"💡 指定群：{self.cmd_daily_news_time_name} 群号 08:00"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return

            _, arg_gid, arg_time = news_time_args
            target_gid = arg_gid or group_id
            if not target_gid or not str(target_gid).isdigit():
                async for _r in self._send_content(event, f"⚠️ 无法确定群号\n"
                    f"用法：{self.cmd_daily_news_time_name} 08:00\n"
                    f"或：{self.cmd_daily_news_time_name} 群号 08:00"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if not arg_time:
                async for _r in self._send_content(event, f"⚠️ 时间格式无效\n"
                    f"用法：{self.cmd_daily_news_time_name} 08:00（24 小时制，支持整点或精确到分钟）"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if not m.set_group_time(target_gid, arg_time):
                async for _r in self._send_content(event, f"⚠️ 时间格式无效：{arg_time}\n"
                    f"请用 24 小时制 HH:MM，例如 08:00、07:30、21:15"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            eff = m.get_group_time(target_gid)
            on = m.is_group_on(target_gid)
            tip = "" if on else f"\n💡 该群推送开关还没开，用「{self.cmd_daily_news_on_name}」开启"
            async for _r in self._send_content(event, f"✅ 群【{target_gid}】读报时间已设为 {eff}{tip}"):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._is_daily_news_list_cmd(msg_str):
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if self.daily_news_module is None:
                logger.warning("[apix-meme] ❌ 每日读报模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            m = self.daily_news_module
            head = (
                f"{title_bar('每日读报推送列表')}\n"
                f"总开关：{'✅ 已开启' if m.enable else '❌ 未开启（后台配置中打开）'}\n"
                f"全局默认时间：{m.default_time}"
            )
            items = m.list_groups()
            if not items:
                async for _r in self._send_content(event, head + "\n（暂无开启读报推送的群）"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            lines = [head, f"已开启的群：{len(items)} 个"]
            for it in items:
                gid = it["group_id"]
                umo_tag = "✅" if m.get_umo(gid) else "⚠️"
                src = "自定义" if it["custom"] else "默认"
                lines.append(f"· 群 {gid} — {it['time']}（{src}）{umo_tag}")
            lines.append("")
            lines.append("💡 ✅=推送通道已就绪，⚠️=该群还没有发过消息")
            async for _r in self._send_content(event, "\n".join(lines)):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        def _resolve_target_uid(raw: str) -> str:
            """从参数里解析目标 QQ：支持 @某人 或 纯数字。"""
            uid = ""
            try:
                at_list = get_ats_from_msg(event)
                if at_list:
                    uid = str(at_list[0])
            except Exception:
                pass
            if not uid and raw:
                cleaned = str(raw).lstrip("@").strip().split()[0] if str(raw).strip() else ""
                if cleaned.isdigit():
                    uid = cleaned
            return uid

        def _moderation_guard():
            """返回 (ok, 错误提示)。功能关闭时错误提示为空 -> 静默不回消息。"""
            if not self.moderation_enable:
                return False, ""
            if self.moderation_module is None:
                return False, "❌ 群员管理模块未加载"
            if not group_id:
                return False, "⚠️ 请在群聊中使用该指令"
            return True, ""

        kick_raw = self._is_kick_cmd(msg_str)
        if kick_raw is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管/插件专属管理员可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            ok, err = _moderation_guard()
            if not ok:
                if err:
                    async for _r in self._send_content(event, err):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                return
            target = _resolve_target_uid(kick_raw)
            if not target:
                async for _r in self._send_content(event, f"⚠️ 请指定要踢出的成员\n用法：{self.cmd_kick_name} @某人 或 {self.cmd_kick_name} QQ号"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if target == to_str(bot_id):
                async for _r in self._send_content(event, "⚠️ 不能踢出机器人自己"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            can, role = await self.moderation_module.bot_can_moderate(event)
            if not can:
                async for _r in self._send_content(event, self.moderation_module.no_permission_tip(role)):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            done = await self.moderation_module.do_kick(event, target)
            if not done:
                async for _r in self._send_content(
                        event, "❌ 踢出失败，请检查机器人权限或查看后台日志"):
                    yield _r
                GLOBAL_STATS["send"] += 1
            logger.info(f"[moderation] 踢出指令执行 group={group_id} "
                        f"target={target} ok={done}")
            return

        mute_raw = self._is_mute_cmd(msg_str)
        if mute_raw is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管/插件专属管理员可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            ok, err = _moderation_guard()
            if not ok:
                if err:
                    async for _r in self._send_content(event, err):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                return
            parts = str(mute_raw).split()
            target = _resolve_target_uid(parts[0] if parts else "")
            dur = self.mute_duration
            if len(parts) >= 2 and parts[1].isdigit():
                dur = int(parts[1])
            if not target:
                async for _r in self._send_content(event, f"⚠️ 请指定要禁言的成员\n"
                    f"用法：{self.cmd_mute_name} @某人 [秒数]\n"
                    f"💡 不填秒数用后台默认值（当前 {self.mute_duration} 秒）"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if target == to_str(bot_id):
                async for _r in self._send_content(event, "⚠️ 不能禁言机器人自己"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            can, role = await self.moderation_module.bot_can_moderate(event)
            if not can:
                async for _r in self._send_content(event, self.moderation_module.no_permission_tip(role)):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            done = await self.moderation_module.do_mute(event, target, dur)
            if not done:
                async for _r in self._send_content(
                        event, "❌ 禁言失败，请检查机器人权限或查看后台日志"):
                    yield _r
                GLOBAL_STATS["send"] += 1
            logger.info(f"[moderation] 禁言指令执行 group={group_id} "
                        f"target={target} dur={dur} ok={done}")
            return

        unmute_raw = self._is_unmute_cmd(msg_str)
        if unmute_raw is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管/插件专属管理员可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            ok, err = _moderation_guard()
            if not ok:
                if err:
                    async for _r in self._send_content(event, err):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                return
            target = _resolve_target_uid(unmute_raw)
            if not target:
                async for _r in self._send_content(event, f"⚠️ 用法：{self.cmd_unmute_name} @某人 或 {self.cmd_unmute_name} QQ号"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            can, role = await self.moderation_module.bot_can_moderate(event)
            if not can:
                async for _r in self._send_content(event, self.moderation_module.no_permission_tip(role)):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            done = await self.moderation_module.do_unmute(event, target)
            if not done:
                async for _r in self._send_content(
                        event, "❌ 解除禁言失败，请查看后台日志"):
                    yield _r
                GLOBAL_STATS["send"] += 1
            logger.info(f"[moderation] 解禁指令执行 group={group_id} "
                        f"target={target} ok={done}")
            return

        bl_raw = self._is_blacklist_cmd(msg_str)
        if bl_raw is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管/插件专属管理员可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if not self.moderation_enable:
                GLOBAL_STATS["send"] += 1
                return
            parts = str(bl_raw).split()
            target = _resolve_target_uid(parts[0] if parts else "")
            act = parts[1].lower() if len(parts) >= 2 else ""
            if act not in (ACTION_KICK, ACTION_MUTE, ACTION_IGNORE):
                act = ""
            if not target:
                async for _r in self._send_content(event, f"⚠️ 请指定要拉黑的成员\n"
                    f"用法：{self.cmd_blacklist_name} @某人 [kick|mute|ignore]\n"
                    f"　 kick=再发言就踢出　mute=再发言就禁言　ignore=无视其指令\n"
                    f"💡 不填动作则用后台默认（当前 {ACTION_LABEL.get(self.blacklist_default_action, self.blacklist_default_action)}）"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if target == to_str(bot_id):
                async for _r in self._send_content(event, "⚠️ 不能拉黑机器人自己"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return

            self._blacklist_raw = upsert_blacklist(self._blacklist_raw, target, act or self.blacklist_default_action)
            self.member_blacklist = parse_blacklist(self._blacklist_raw)
            self.save_rules_to_config()

            eff = self.member_blacklist.get(target, self.blacklist_default_action)
            eff_label = ACTION_LABEL.get(eff, eff)
            async for _r in self._send_content(event, f"✅ 已拉黑成员 {target}\n"
                f"· 后续发言处理：{eff_label}\n"
                f"💡 可用「{self.cmd_unblacklist_name} {target}」解除"):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        ubl_raw = self._is_unblacklist_cmd(msg_str)
        if ubl_raw is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管/插件专属管理员可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            target = _resolve_target_uid(ubl_raw)
            if not target:
                async for _r in self._send_content(event, f"⚠️ 用法：{self.cmd_unblacklist_name} @某人 或 {self.cmd_unblacklist_name} QQ号"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            new_raw, removed = remove_blacklist(self._blacklist_raw, target)
            if removed:
                self._blacklist_raw = new_raw
                self.member_blacklist = parse_blacklist(self._blacklist_raw)
                self.save_rules_to_config()
            async for _r in self._send_content(event, f"✅ 已解除成员 {target} 的拉黑" if removed
                else f"⚠️ 成员 {target} 不在黑名单里"):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._is_blacklist_list_cmd(msg_str):
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管/插件专属管理员可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            head = (
                f"{title_bar('成员黑名单')}\n"
                f"功能开关：{'✅ 已开启' if self.moderation_enable else '❌ 已关闭'}\n"
                f"默认动作：{ACTION_LABEL.get(self.blacklist_default_action, self.blacklist_default_action)}\n"
                f"禁言时长：{self.mute_duration} 秒"
            )
            if not self.member_blacklist:
                async for _r in self._send_content(event, head + "\n（黑名单为空）"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            lines = [head, f"已拉黑：{len(self.member_blacklist)} 人"]
            for uid, act in sorted(self.member_blacklist.items()):
                a = act or self.blacklist_default_action
                lines.append(f"· {uid} — {ACTION_LABEL.get(a, a)}")
            lines.append("")
            lines.append(f"💡 解除：{self.cmd_unblacklist_name} QQ号")
            async for _r in self._send_content(event, "\n".join(lines)):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._is_mc_auto_cmd(msg_str):
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if not group_id:
                async for _r in self._send_content(event, "⚠️请在群聊中使用该指令"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if self.minecraft_module is None:
                logger.warning("[apix-meme] ❌ Minecraft 版本模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "minecraft"):
                GLOBAL_STATS["send"] += 1
                return
            current = self.minecraft_module.is_group_auto(group_id)
            new_state = not current
            self.minecraft_module.set_group_auto(group_id, new_state)
            try:
                self.minecraft_auto_groups = list(self.minecraft_module.auto_groups)
                self.save_rules_to_config()
            except Exception:
                logger.exception("[apix-meme] 保存 MC 自动检测群列表失败")
            if new_state:
                async for _r in self._send_content(event, "✅ 本群已开启 Minecraft 版本自动检测\n"
                    f"📢 检测到新版本时会推送到本群\n"
                    f"⚙️ 检测间隔：{self.minecraft_module.interval} 秒\n"
                    f"💡 再次发送「{self.cmd_mc_auto_name}」可关闭"):
                    yield _r
            else:
                pass
            GLOBAL_STATS["send"] += 1
            return

        if self._is_mc_test_cmd(msg_str):
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if self.minecraft_module is None:
                logger.warning("[apix-meme] ❌ Minecraft 版本模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "minecraft"):
                GLOBAL_STATS["send"] += 1
                return

            test_groups = list(getattr(self, "minecraft_test_groups", []) or [])
            if test_groups:
                groups = test_groups
                scope_note = "（来自后台「MC测试推送指定群」配置）"
            else:
                groups = list(self.minecraft_module.auto_groups)
                scope_note = "（未配置指定群，默认推送给所有开启自动检测的群）"

            if not groups:
                async for _r in self._send_content(event, "⚠️ 没有可推送的群\n"
                    f"💡 在后台「MC测试推送指定群」里填群号（一行一个），"
                    f"或先在本群发「{self.cmd_mc_auto_name}」开启自动检测"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return

            async for _r in self._send_content(event, f"⏳ 正在拉取最新版本并推送到 {len(groups)} 个群，请稍候...\n{scope_note}"):
                yield _r
            GLOBAL_STATS["send"] += 1

            try:
                result = await self.minecraft_module.check_all(force=True, include_bedrock=True)
                text = self.minecraft_module.format_text(result, include_bedrock=True)
            except Exception:
                logger.exception("[apix-meme] MC 测试推送：拉取版本失败")
                async for _r in self._send_content(event, "❌ 拉取版本失败，请查看后台日志"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return

            push_text = "📢 【MC版本测试推送】\n\n" + text
            ok_count = 0
            fail_count = 0
            fail_groups = []
            for gid in groups:
                try:
                    umo = self.minecraft_module.get_umo(gid)
                    _t, _imgs = await self.render_group_text(str(gid), push_text)
                    ok = await self._push_mc_notification(str(gid), _t,
                                                          umo=umo, images=_imgs)
                    if ok:
                        ok_count += 1
                    else:
                        fail_count += 1
                        fail_groups.append(str(gid))
                except Exception:
                    logger.exception(f"[apix-meme] MC 测试推送：群 {gid} 推送异常")
                    fail_count += 1
                    fail_groups.append(str(gid))

            lines = [
                f"{title_bar('MC 测试推送结果')}",
                f"✅ 成功：{ok_count} 个群",
                f"❌ 失败：{fail_count} 个群",
            ]
            if fail_groups:
                lines.append("")
                lines.append("失败群号：")
                for g in fail_groups:
                    lines.append(f"  · {g}")
            lines.append("")
            lines.append(f"💡 推送范围：{scope_note}")
            if test_groups:
                lines.append(f"💡 共 {len(groups)} 个群（后台指定）")
            else:
                lines.append(f"💡 共 {len(groups)} 个群开启了自动检测")
            async for _r in self._send_content(event, "\n".join(lines)):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        mc_args = self._is_minecraft_cmd(msg_str)
        if mc_args is not None:
            if not group_allowed(group_id, self.minecraft_groups):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "minecraft"):
                GLOBAL_STATS["send"] += 1
                return
            if self.minecraft_module is None:
                logger.warning("[apix-meme] ❌ Minecraft 版本模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            force = mc_args[0]
            try:
                result = await self.minecraft_module.check_all(force=force, include_bedrock=True)
                async for _r in self._send_content(event, self.minecraft_module.format_text(result, include_bedrock=True)):
                    yield _r
            except Exception:
                logger.exception("[apix-meme] MC 版本查询失败")
                async for _r in self._send_content(event, "❌ 查询失败，请稍后重试"):
                    yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self.allowed_groups:
            if not group_allowed(group_id, self.allowed_groups):
                return
        elif not is_group_allowed(group_id, self.group_mode, self.group_whitelist, self.group_blacklist):
            return

        if (
            self._is_group_feature_enable(group_id, "fortune")
            and self.fortune_module is not None
            and self.fortune_module.match_trigger(msg_str)
            and self.fortune_module._is_group_allowed(group_id)
        ):
            try:
                text = self.fortune_module.get_today_text(event)
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 运势处理失败")
            return

        if (
            self._is_group_feature_enable(group_id, "husband")
            and self.husband_module is not None
            and self.husband_module.match_trigger(msg_str)
            and self.husband_module._is_group_allowed(group_id)
        ):
            try:
                text, avatar_path = await self.husband_module.get_today_result(event)
                chain = await self._build_text_avatar_result_async(
                    event, text, avatar_path)
                if chain:
                    yield event.chain_result(chain)
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 老公处理失败")
            return

        if (
            self._is_group_feature_enable(group_id, "husband")
            and self.husband_module is not None
            and getattr(self.husband_module, "match_force", None) is not None
            and self.husband_module.match_force(msg_str)
            and self.husband_module._is_group_allowed(group_id)
        ):
            try:
                try:
                    self_id = to_str(event.get_self_id())
                except Exception:
                    self_id = ""
                text, avatar_path = await self.husband_module.force_husband(
                    event, msg_str, self_id
                )
                chain = await self._build_text_avatar_result_async(
                    event, text, avatar_path)
                if chain:
                    yield event.chain_result(chain)
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 逼迫处理失败")
            return

        if (
            self._is_group_feature_enable(group_id, "luck")
            and self.luck_module is not None
            and self.luck_module.match_trigger(msg_str)
            and self.luck_module._is_group_allowed(group_id)
        ):
            try:
                text = self.luck_module.get_today_text(event)
                if text:
                    async for _r in self._send_content(event, text):
                        yield _r
                    GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 人品处理失败")
            return

        if self._is_group_feature_enable(group_id, "word_reply") and self.word_reply_module is not None:
            rule, _kw = self.word_reply_module.match(msg_str, group_id)
            if rule:
                reply = self.word_reply_module.get_reply(rule)
                if reply:
                    async for _r in self._send_content(event, reply):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                return

        args_add = self._is_word_add(msg_str)
        if args_add is not None:
            if not self._is_group_feature_enable(group_id, "word_reply"):
                GLOBAL_STATS["send"] += 1
                return
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if ac is None:
                logger.warning("[apix-meme] ❌管理指令模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            _t = ac.word_add(args_add)
            if _t:
                async for _r in self._send_content(event, _t):
                    yield _r
            GLOBAL_STATS["send"] += 1
            return

        args_del = self._is_word_del(msg_str)
        if args_del is not None:
            if not self._is_group_feature_enable(group_id, "word_reply"):
                GLOBAL_STATS["send"] += 1
                return
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if ac is None:
                logger.warning("[apix-meme] ❌管理指令模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            _t = ac.word_del(args_del)
            if _t:
                async for _r in self._send_content(event, _t):
                    yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._is_word_list(msg_str):
            if not self._is_group_feature_enable(group_id, "word_reply"):
                GLOBAL_STATS["send"] += 1
                return
            if ac is None:
                logger.warning("[apix-meme] ❌管理指令模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            _t = ac.word_list_text()
            if _t:
                async for _r in self._send_content(event, _t):
                    yield _r
            GLOBAL_STATS["send"] += 1
            return

        arg_add = self._extract_arg(msg_str, self.cmd_add_name)
        if arg_add is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if ac is None:
                logger.warning("[apix-meme] ❌管理指令模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, ac.meme_add(arg_add)):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        arg_del = self._extract_arg(msg_str, self.cmd_del_name)
        if arg_del is not None:
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if ac is None:
                logger.warning("[apix-meme] ❌管理指令模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, ac.meme_del(arg_del)):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._name_list_hit(msg_str, "cmd_save_name"):
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if ac is None:
                logger.warning("[apix-meme] ❌管理指令模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, ac.meme_save()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._match_status_cmd(msg_str):
            if not await self._check_admin(event):
                async for _r in self._send_content(event, "⚠️仅管理员/群主/群管可执行"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            if not self.status_enable:
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "status"):
                GLOBAL_STATS["send"] += 1
                return
            if not group_allowed(group_id, self.status_groups):
                GLOBAL_STATS["send"] += 1
                return
            try:
                _status_text = await collect_runtime_status(
                    event=event, group_id=group_id or None,
                    cfg=self._read_disk_config() or {}, plugin=self)
                async for _r in self._send_content(event, _status_text):
                    yield _r
            except Exception:
                logger.exception("[apix-meme] 电脑状态查询失败")
                async for _r in self._send_content(event, "❌ 查询失败"):
                    yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._is_greeting_morning_menu(msg_str):
            async for _r in self._send_content(event, self._build_greeting_morning_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_greeting_night_menu(msg_str):
            async for _r in self._send_content(event, self._build_greeting_night_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_greeting_rank_menu(msg_str):
            async for _r in self._send_content(event, self._build_greeting_rank_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._is_checkin_signin_menu(msg_str):
            async for _r in self._send_content(event, self._build_checkin_signin_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_checkin_query_menu(msg_str):
            async for _r in self._send_content(event, self._build_checkin_query_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_checkin_bank_save_menu(msg_str):
            async for _r in self._send_content(event, self._build_checkin_bank_save_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_checkin_bank_loan_menu(msg_str):
            async for _r in self._send_content(event, self._build_checkin_bank_loan_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_checkin_bank_fund_menu(msg_str):
            async for _r in self._send_content(event, self._build_checkin_bank_fund_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_checkin_bank_rob_menu(msg_str):
            async for _r in self._send_content(event, self._build_checkin_bank_rob_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_checkin_bank_menu(msg_str):
            async for _r in self._send_content(event, self._build_checkin_bank_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_checkin_lottery_menu(msg_str):
            async for _r in self._send_content(event, self._build_checkin_lottery_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_checkin_steal_menu(msg_str):
            async for _r in self._send_content(event, self._build_checkin_steal_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_checkin_shop_help_menu(msg_str):
            async for _r in self._send_content(
                    event, self._build_checkin_shop_help_menu(
                        self._shop_help_page(msg_str))):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_mount_manage_menu(msg_str):
            async for _r in self._send_content(event, self._build_mount_manage_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_mount_admin_menu(msg_str):
            async for _r in self._send_content(event, self._build_mount_admin_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_checkin_mount_menu(msg_str):
            async for _r in self._send_content(event, self._build_checkin_mount_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_job_list_menu(msg_str):
            async for _r in self._send_content(event, self._build_job_list_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_job_refill_menu(msg_str):
            async for _r in self._send_content(event, self._build_job_refill_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_stamina_menu(msg_str):
            if not getattr(self.checkin_module, "stamina_enable", True):
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, self._build_stamina_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_checkin_job_menu(msg_str):
            async for _r in self._send_content(event, self._build_checkin_job_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        _pet_sub = None
        if self._is_pet_care_menu(msg_str):
            _pet_sub = self._build_pet_care_menu
        elif self._is_pet_fight_menu(msg_str):
            _pet_sub = self._build_pet_fight_menu
        elif self._is_pet_item_menu(msg_str):
            _pet_sub = self._build_pet_item_menu
        if _pet_sub is not None:
            if not self._is_group_feature_enable(group_id, "pet"):
                GLOBAL_STATS["send"] += 1
                return
            _pm = self.pet_module
            if _pm is not None and not _pm.is_group_allowed(group_id):
                logger.warning(f"[apix-meme] 群 {group_id} 未开启宠物功能，忽略菜单")
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, _pet_sub()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_pet_menu(msg_str):
            if not self._is_group_feature_enable(group_id, "pet"):
                GLOBAL_STATS["send"] += 1
                return
            pet_mod = self.pet_module
            if pet_mod is not None and not pet_mod.is_group_allowed(group_id):
                logger.warning(f"[apix-meme] 群 {group_id} 未开启宠物功能，忽略菜单")
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, self._build_pet_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_speak_menu(msg_str):
            if not self._is_group_feature_enable(group_id, "speak_rank"):
                GLOBAL_STATS["send"] += 1
                return
            ss_mod = self.speak_stats_module
            if ss_mod is not None and not ss_mod.is_group_allowed(group_id):
                logger.warning(f"[apix-meme] 群 {group_id} 未开启发言统计，忽略菜单")
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, self._build_speak_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        meme_feature_args = self._is_meme_feature_menu(msg_str)
        if meme_feature_args is not None:
            if not self.meme_enable:
                GLOBAL_STATS["send"] += 1
                return
            if not group_allowed(group_id, self.meme_groups):
                GLOBAL_STATS["send"] += 1
                return
            if ac is None:
                logger.warning("[apix-meme] ❌管理指令模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            media, page = meme_feature_args
            body = self._render_interface_list(page, media)
            if not body:
                kind = {"video": "视频", "image": "图片", "text": "文本"}.get(media, "")
                async for _r in self._send_content(event, f"暂无{kind}接口规则"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            lines = [body]
            lines.append("")
            lines.extend(self._interface_usage_lines(media))
            lines.append("")
            lines.append(f"💡 发送「{self._interface_menu_hint(media)} 页码」翻页")
            async for _r in self._send_content(event, "\n".join(lines)):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._is_meme_admin_menu(msg_str):
            async for _r in self._send_content(event, self._build_meme_admin_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_word_admin_menu(msg_str):
            async for _r in self._send_content(event, self._build_word_admin_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_checkin_admin_menu(msg_str):
            async for _r in self._send_content(event, self._build_checkin_admin_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_output_mode_menu(msg_str):
            try:
                self._menu_group_id = group_id or ""
            except Exception:
                self._menu_group_id = ""
            async for _r in self._send_content(event, self._build_output_mode_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_status_admin_menu(msg_str):
            async for _r in self._send_content(event, self._build_status_admin_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_switch_admin_menu(msg_str):
            async for _r in self._send_content(event, self._build_switch_admin_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_chime_admin_menu(msg_str):
            async for _r in self._send_content(event, self._build_chime_admin_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_news_admin_menu(msg_str):
            async for _r in self._send_content(event, self._build_news_admin_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_member_admin_menu(msg_str):
            async for _r in self._send_content(event, self._build_member_admin_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_must_at_admin_menu(msg_str):
            async for _r in self._send_content(event, self._build_must_at_admin_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_super_admin_menu(msg_str):
            async for _r in self._send_content(event, self._build_super_admin_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_notice_menu(msg_str):
            async for _r in self._send_content(event, self._build_notice_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_auth_menu(msg_str):
            async for _r in self._send_content(
                    event,
                    self._build_auth_menu(
                        str(_get_group_id_from_event(event) or ""))):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        _auth_sub = self._is_auth_sub_menu(msg_str)
        if _auth_sub:
            async for _r in self._send_content(
                    event,
                    self._build_auth_sub_menu(
                        _auth_sub,
                        str(_get_group_id_from_event(event) or ""))):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        _farm_hit = self._is_farm_menu(msg_str)
        if not _farm_hit and self._farm_sub_menu(msg_str):
            _farm_hit = True
        if not _farm_hit:
            for _fa, _fd in (("cmd_farm_view", "我的农场"),
                             ("cmd_farm_plant", "种菜"),
                             ("cmd_farm_harvest_all", "一键收获"),
                             ("cmd_farm_harvest", "收获"),
                             ("cmd_farm_shop", "种子商店"),
                             ("cmd_farm_buy_seed", "购买种子"),
                             ("cmd_farm_impl_shop", "农具商店"),
                             ("cmd_farm_buy_item", "购买肥料"),
                             ("cmd_farm_use_item", "施肥"),
                             ("cmd_farm_dog", "工具商店"),
                             ("cmd_farm_buy_dog", "购买家畜"),
                             ("cmd_farm_feed", "喂食"),
                             ("cmd_farm_buy_feed", "购买饲料"),
                             ("cmd_farm_equip_dog", "装备家畜"),
                             ("cmd_farm_my_animals", "我的家畜"),
                             ("cmd_farm_steal", "偷菜"),
                             ("cmd_farm_steal_log", "被偷记录"),
                             ("cmd_farm_bag", "我的背包"),
                             ("cmd_farm_status", "农场状态"),
                             ("cmd_farm_land", "土地状态"),
                             ("cmd_farm_buy_tool", "购买工具"),
                             ("cmd_farm_equip_tool", "装备工具")):
                if self._farm_match(msg_str, _fa, _fd) is not None:
                    _farm_hit = True
                    break
        if _farm_hit:
            if not getattr(self, "farm_enable", True):
                GLOBAL_STATS["send"] += 1
                return
            if not group_allowed(group_id,
                                 getattr(self, "farm_groups", [])):
                GLOBAL_STATS["send"] += 1
                return
            if not self._is_group_feature_enable(group_id, "farm"):
                GLOBAL_STATS["send"] += 1
                return
            # ★★ 2.1.0（后续调整）：农场**跟随本群输出模式**（不再强制图片）
            async for _r in self._dispatch_farm(event, msg_str, group_id):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._is_entertain_menu(msg_str):
            async for _r in self._send_content(event, self._build_entertain_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        # ★★ 1.5.1：音乐功能菜单入口（默认「音乐功能」，后台 menu_music_names 可改）
        if self._is_music_menu(msg_str):
            if self.music_module is None or not self.music_module.enable:
                GLOBAL_STATS["send"] += 1
                return
            if not self.music_module.is_group_allowed(group_id):
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, self._build_music_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._is_guess_menu(msg_str):
            if not group_allowed(group_id, self.guess_groups):
                GLOBAL_STATS["send"] += 1
                return
            if self._is_group_feature_enable(group_id, "guess"):
                async for _r in self._send_content(event, self._build_guess_menu()):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
        if (self.racing_module is not None
                and self._is_group_feature_enable(group_id, "racing")):
            _race_page = self._is_race_menu(msg_str)
            if _race_page is not None:
                async for _r in self._send_content(
                        event,
                        self._build_racing_menu(
                            max(1, int(_race_page or 1)))):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            _uid = to_str(event.get_sender_id())
            _uname = get_sender_name(event) or f"QQ{_uid}"
            _race_hit = None
            for _act, _attr, _dflt in (
                    ("shop", "cmd_race_shop", "赛车商店"),
                    ("buy", "cmd_race_buy", "购买赛车"),
                    ("luckyall", "cmd_race_lucky_all", "赛车一键抽奖"),
                    ("lucky", "cmd_race_lucky", "赛车抽奖"),
                    ("view", "cmd_race_view", "赛车查看"),
                    ("equip", "cmd_race_equip", "装备赛车"),
                    ("my", "cmd_race_my", "我的赛车"),
                    ("fast", "cmd_race_fast", "快速比赛"),
                    ("join", "cmd_race_join", "参与比赛")):
                _a = self._race_cmd_arg(msg_str, _attr, _dflt)
                if _a is not None:
                    _race_hit = (_act, _a)
                    break
            if _race_hit is not None:
                async for _r in self._handle_racing_cmd(
                        event, group_id, _uid, _uname,
                        _race_hit[0], _race_hit[1]):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return

        if self._is_guess_start_cmd(msg_str):
            if not group_allowed(group_id, self.guess_groups):
                GLOBAL_STATS["send"] += 1
                return
            if self._is_group_feature_enable(group_id, "guess"):
                async for _r in self._handle_guess_start(event, group_id):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
        if self._is_guess_cmd(msg_str):
            if not group_allowed(group_id, self.guess_groups):
                GLOBAL_STATS["send"] += 1
                return
            if self._is_group_feature_enable(group_id, "guess"):
                async for _r in self._handle_guess_cmd(event, group_id,
                                                       msg_str):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return

        # ==================================================================
        # ★★ 1.5.1：音乐功能（点歌 / 搜歌 / 选歌 / 音源 / 榜单随机）
        #
        #   ★ 只发**音乐卡片**：AstrBot 原生 `Music` 组件 + 平台适配器签名。
        #   ★ 音源只有三个：netease / qq / kugou。
        #   ★ 卡片封面只有两种：音源 LOGO / 点歌人头像（都是直链，不缓存）。
        # ==================================================================
        _music = getattr(self, "music_module", None)

        # —— 音源切换（不带参数 = 查看当前与全部音源）——
        _music_src = _music.match_source(msg_str) if _music is not None else None
        if _music_src is not None:
            if not _music.enable:
                GLOBAL_STATS["send"] += 1
                return
            if not _music.is_group_allowed(group_id):
                logger.warning(f"[apix-meme] 群 {group_id} 未开启音乐功能，忽略指令")
                GLOBAL_STATS["send"] += 1
                return
            try:
                if not _music_src:
                    async for _r in self._send_content(
                            event, _music.build_source_text()):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                if not await self._check_admin(event):
                    async for _r in self._send_content(
                            event, "⚠️仅管理员/群主/群管/插件专属管理员可执行"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                _code, _serr = _music.parse_source_arg(_music_src)
                if _serr:
                    _avail = " / ".join(_music.source_label(c)
                                        for c in _music.source_order())
                    async for _r in self._send_content(
                            event,
                            f"⚠️ 无法识别的音源：{_music_src}\n"
                            f"💡 可选：{_avail}"
                            "\n　 （酷我 / 咪咕 / 千千 已下线）"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                _music.source = _code
                _saved = self._write_music_source(_code)
                _lines = [
                    f"{title_bar('音源切换')}",
                    "",
                    f"✅ 已切换到：{_music.source_label(_code)}",
                ]
                if not _saved:
                    _lines.append("⚠️ 写入配置失败，重启后可能失效")
                _lines.append("")
                _lines.append(f"💡 用「{_music.search_name} 歌名」开始点歌")
                async for _r in self._send_content(event, "\n".join(_lines)):
                    yield _r
                GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 切换音源失败")
                async for _r in self._send_content(event, "❌ 切换失败"):
                    yield _r
                GLOBAL_STATS["send"] += 1
            return

        # —— 搜歌（先出列表，再用「选歌 序号」挑）——
        if _music is not None:
            _find_src = _music.match_find_by_source(msg_str)
            _find_q = _music.match_find(msg_str)
            if _find_src is not None or _find_q is not None:
                if not _music.enable:
                    GLOBAL_STATS["send"] += 1
                    return
                if not _music.is_group_allowed(group_id):
                    logger.warning(f"[apix-meme] 群 {group_id} 未开启音乐功能，忽略指令")
                    GLOBAL_STATS["send"] += 1
                    return
                _fsrc = _find_src[0] if _find_src is not None else ""
                _fq = (_find_src[1] if _find_src is not None else _find_q)
                _flabel = _music.source_label(_fsrc) if _fsrc else ""
                _fcmd = _music.find_name
                try:
                    if not _fq:
                        async for _r in self._send_content(
                                event,
                                f"{title_bar(f'{_flabel}{_fcmd}' if _flabel else _fcmd)}\n"
                                f"用法：「{_flabel}{_fcmd} 歌名」\n"
                                f"　 例：{_flabel}{_fcmd} 不要说话\n"
                                f"\n"
                                f"💡 输出歌曲列表（最多 {_music.limit} 首），"
                                f"再用「{_music.pick_name} 序号」挑一首"
                                + (f"\n💡 只这一次用{_flabel}，默认音源不变"
                                   f"（当前：{_music.source_label(_music.source)}）"
                                   if _fsrc else "")):
                            yield _r
                        GLOBAL_STATS["send"] += 1
                        return
                    items, err = await _music.search(_fq, _fsrc or None)
                    if err == "empty":
                        async for _r in self._send_content(
                                event,
                                f"⚠️ 请给出歌名，例：{_flabel}{_fcmd} 不要说话"):
                            yield _r
                    elif err:
                        async for _r in self._send_content(
                                event, f"❌ 搜索失败：{err}"):
                            yield _r
                    elif not items:
                        async for _r in self._send_content(
                                event, _music.empty_hint(_fq, _flabel)):
                            yield _r
                    else:
                        _music.save_session(
                            group_id, to_str(event.get_sender_id()), items,
                            _fsrc or _music.source)
                        async for _r in self._send_content(
                                event, _music.build_search_text(_fq, items)):
                            yield _r
                    GLOBAL_STATS["send"] += 1
                except Exception:
                    logger.exception("[apix-meme] 搜歌失败")
                    async for _r in self._send_content(event, "❌ 搜索失败，请稍后再试"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                return

        # —— 点歌（直接播第一首）：音源专属写法 `QQ点歌 歌名` ——
        if _music is not None:
            _src_query = _music.match_search_by_source(msg_str)
            if _src_query is not None:
                _src_code, _mq = _src_query
                if not _music.enable:
                    GLOBAL_STATS["send"] += 1
                    return
                if not _music.is_group_allowed(group_id):
                    logger.warning(f"[apix-meme] 群 {group_id} 未开启音乐功能，忽略指令")
                    GLOBAL_STATS["send"] += 1
                    return
                _src_label = _music.source_label(_src_code)
                try:
                    if not _mq:
                        async for _r in self._send_content(event, "\n".join([
                                f"{title_bar(f'{_src_label}点歌')}",
                                f"用法：「{_src_label}{_music.search_name} 歌名」",
                                f"　 例：{_src_label}{_music.search_name} 不要说话",
                                "",
                                f"💡 只这一首用{_src_label}，默认音源不变"
                                f"（当前：{_music.source_label(_music.source)}）",
                        ])):
                            yield _r
                        GLOBAL_STATS["send"] += 1
                        return
                    items, err = await _music.search(_mq, _src_code)
                    if err == "empty":
                        async for _r in self._send_content(
                                event,
                                f"⚠️ 请给出歌名，例：{_src_label}{_music.search_name} 不要说话"):
                            yield _r
                    elif err:
                        async for _r in self._send_content(
                                event, f"❌ 搜索失败：{err}"):
                            yield _r
                    elif not items:
                        async for _r in self._send_content(
                                event, _music.empty_hint(_mq, _src_label)):
                            yield _r
                    else:
                        async for _r in self._music_play_pick(
                                event, group_id, "", _music, items[0],
                                _src_code, skip_search=True):
                            yield _r
                    GLOBAL_STATS["send"] += 1
                except Exception:
                    logger.exception("[apix-meme] 点歌失败（指定音源）")
                    async for _r in self._send_content(event, "❌ 搜索失败，请稍后再试"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                return

        # —— 榜单随机（新歌榜随机 / 热歌榜随机）——
        _span_kind = None
        if _music is not None:
            try:
                _span_kind = _music.match_span(msg_str)
            except Exception:
                _span_kind = None
        if _span_kind is not None:
            if not _music.enable:
                GLOBAL_STATS["send"] += 1
                return
            if not _music.is_group_allowed(group_id):
                logger.warning(f"[apix-meme] 群 {group_id} 未开启音乐功能，忽略榜单随机")
                GLOBAL_STATS["send"] += 1
                return
            try:
                _rs = None
                if music_toplist is not None:
                    _rs = await music_toplist.random_song(_span_kind, logger=logger)
                if not _rs:
                    async for _r in self._send_content(
                            event, "❌ 榜单取歌失败，请稍后再试（网络或接口波动）"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                _q = _rs["name"]
                if _rs.get("artist"):
                    _q = f"{_rs['name']} {_rs['artist']}"
                logger.info("[apix-meme] 榜单随机 %s -> %r",
                            _rs.get("label"), _q)
                items, _err = await _music.search(_q)
                if (not items) and _rs.get("artist"):
                    items, _err = await _music.search(_rs["name"])
                if not items:
                    async for _r in self._send_content(
                            event,
                            f"⚠️ 抽到「{_rs['name']}」但没搜到资源，换一首再试\n"
                            f"　 来源：QQ音乐{_rs.get('label', '')}"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                async for _r in self._music_play_pick(
                        event, group_id, "", _music, items[0],
                        _music.source, skip_search=True):
                    yield _r
                GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 榜单随机失败")
                async for _r in self._send_content(event, "❌ 播放失败，请稍后再试"):
                    yield _r
                GLOBAL_STATS["send"] += 1
            return

        # —— 点歌（默认音源，直接播第一首）——
        _music_q = _music.match_search(msg_str) if _music is not None else None
        if _music_q is not None:
            if not _music.enable:
                GLOBAL_STATS["send"] += 1
                return
            if not _music.is_group_allowed(group_id):
                logger.warning(f"[apix-meme] 群 {group_id} 未开启音乐功能，忽略指令")
                GLOBAL_STATS["send"] += 1
                return
            try:
                if not _music_q:
                    async for _r in self._send_content(event, "\n".join([
                            f"{title_bar('点歌')}",
                            f"用法：「{_music.search_name} 歌名」",
                            f"　 例：{_music.search_name} 不要说话",
                    ])):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                items, err = await _music.search(_music_q)
                if err == "empty":
                    async for _r in self._send_content(
                            event,
                            f"⚠️ 请给出歌名，例：{_music.search_name} 不要说话"):
                        yield _r
                elif err:
                    async for _r in self._send_content(
                            event, f"❌ 搜索失败：{err}"):
                        yield _r
                elif not items:
                    async for _r in self._send_content(
                            event, _music.empty_hint(_music_q)):
                        yield _r
                else:
                    async for _r in self._music_play_pick(
                            event, group_id, "", _music, items[0],
                            _music.source, skip_search=True):
                        yield _r
                GLOBAL_STATS["send"] += 1
            except Exception:
                logger.exception("[apix-meme] 点歌失败")
                async for _r in self._send_content(event, "❌ 搜索失败，请稍后再试"):
                    yield _r
                GLOBAL_STATS["send"] += 1
            return

        # —— 选歌 ——
        _music_pick = _music.match_pick(msg_str) if _music is not None else None
        if _music_pick is not None:
            if not _music.enable:
                GLOBAL_STATS["send"] += 1
                return
            if not _music.is_group_allowed(group_id):
                logger.warning(f"[apix-meme] 群 {group_id} 未开启音乐功能，忽略指令")
                GLOBAL_STATS["send"] += 1
                return
            try:
                if not _music_pick:
                    async for _r in self._send_content(event, "\n".join([
                            f"{title_bar('点歌')}",
                            f"用法：「{_music.pick_name} 序号」",
                            f"　 先搜索：{_music.find_name} 歌名",
                    ])):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                _uid = to_str(event.get_sender_id())
                _sess_src = _music.get_session_source(group_id, _uid)
                items, _serr = _music.consume_session(group_id, _uid)
                if _serr == "used":
                    async for _r in self._send_content(
                            event,
                            "⚠️ 这次搜索已经选过歌了（一次搜索只能选一首）\n"
                            f"💡 想再选请重新「{_music.find_name} 歌名」"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                if not items:
                    async for _r in self._send_content(
                            event,
                            "⚠️ 还没有搜索结果\n"
                            f"💡 先「{_music.find_name} 歌名」，"
                            f"再「{_music.pick_name} 序号」"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                idx = _music.parse_index(_music_pick)
                if idx < 1 or idx > len(items):
                    _music.restore_session(group_id, _uid)
                    async for _r in self._send_content(
                            event, f"⚠️ 序号超出范围（1-{len(items)}）"):
                        yield _r
                    GLOBAL_STATS["send"] += 1
                    return
                async for _r in self._music_play_pick(
                        event, group_id, _uid, _music, items[idx - 1], _sess_src):
                    yield _r
            except Exception:
                logger.exception("[apix-meme] 选歌失败")
                async for _r in self._send_content(event, "❌ 点歌失败，请稍后再试"):
                    yield _r
                GLOBAL_STATS["send"] += 1
            return

        async for _r in self._dispatch_qqinfo(event, msg_str, group_id):
            yield _r
        if getattr(self, "_qqinfo_handled", False):
            self._qqinfo_handled = False
            GLOBAL_STATS["send"] += 1
            return

        if self._is_main_menu(msg_str):
            async for _r in self._send_content(event, self._build_main_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_meme_menu(msg_str):
            if not self.meme_enable:
                GLOBAL_STATS["send"] += 1
                return
            if not group_allowed(group_id, self.meme_groups):
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, self._build_meme_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_mc_menu(msg_str):
            if not self._is_group_feature_enable(group_id, "minecraft"):
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, self._build_mc_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._is_mc_version_menu(msg_str):
            # ★★ 1.5.0：世界系统子菜单（版本查询）
            if not self._is_group_feature_enable(group_id, "minecraft"):
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, self._build_mc_version_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_mc_server_menu(msg_str):
            # ★★ 1.5.0：世界系统子菜单（服务查询）
            if not self._is_group_feature_enable(group_id, "minecraft"):
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, self._build_mc_server_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_mc_player_menu(msg_str):
            # ★★ 1.5.0：世界系统子菜单（玩家信息）
            if not self._is_group_feature_enable(group_id, "minecraft"):
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, self._build_mc_player_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_mc_push_menu(msg_str):
            # ★★ 1.5.0：世界系统子菜单（版本推送）
            if not self._is_group_feature_enable(group_id, "minecraft"):
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, self._build_mc_push_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_greeting_menu(msg_str):
            if not self._is_group_feature_enable(group_id, "greeting"):
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, self._build_greeting_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_checkin_menu(msg_str):
            if not self._is_group_feature_enable(group_id, "checkin"):
                GLOBAL_STATS["send"] += 1
                return
            async for _r in self._send_content(event, self._build_checkin_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_daily_menu(msg_str):
            async for _r in self._send_content(event, self._build_daily_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_fortune_menu(msg_str):
            async for _r in self._send_content(event, self._build_fortune_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_husband_menu(msg_str):
            async for _r in self._send_content(event, self._build_husband_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_luck_menu(msg_str):
            async for _r in self._send_content(event, self._build_luck_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_news_menu(msg_str):
            async for _r in self._send_content(event, self._build_news_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return
        if self._is_admin_menu(msg_str):
            async for _r in self._send_content(event, self._build_admin_menu()):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        page = self._is_list_menu(msg_str)
        if page is not None:
            if not self.meme_enable:
                GLOBAL_STATS["send"] += 1
                return
            if not group_allowed(group_id, self.meme_groups):
                GLOBAL_STATS["send"] += 1
                return
            if ac is None:
                logger.warning("[apix-meme] ❌管理指令模块未加载")
                GLOBAL_STATS["send"] += 1
                return
            body = self._render_interface_list(page)
            if not body:
                async for _r in self._send_content(event, "暂无接口规则"):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return
            lines = [body, ""]
            lines.append(f"💡 发送「{self.cmd_list_name} 页码」翻页")
            async for _r in self._send_content(event, "\n".join(lines)):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        if self._is_group_feature_enable(group_id, "greeting") and self.greeting_module is not None:
            greeting_group_ok = self.greeting_module.is_group_allowed(group_id)
            if (self.greeting_module.match_morning(msg_str) and greeting_group_ok):
                try:
                    text = self.greeting_module.handle_morning(event)
                    if text:
                        async for _r in self._send_content(event, text):
                            yield _r
                        GLOBAL_STATS["send"] += 1
                except Exception:
                    logger.exception("[apix-meme] 早安处理失败")
                return
            if (self.greeting_module.match_night(msg_str) and greeting_group_ok):
                try:
                    text = self.greeting_module.handle_night(event)
                    if text:
                        async for _r in self._send_content(event, text):
                            yield _r
                        GLOBAL_STATS["send"] += 1
                except Exception:
                    logger.exception("[apix-meme] 晚安处理失败")
                return

        if self._is_weather_menu(msg_str):
            if group_allowed(group_id, self.meme_groups):
                async for _r in self._send_content(
                        event, self._build_weather_menu()):
                    yield _r
                GLOBAL_STATS["send"] += 1
                return

        # ★★ 1.5.1：音乐指令兜底 —— 「点歌/搜歌 + 与指令重名的歌名」。
        #
        # 为什么需要：
        #   `path_utils._looks_like_arg()` 和 `music._arg_is_other_cmd()`
        #   都会把「和已知指令重名」的词判成**别的指令**，避免
        #   `点歌功能` 这类被当成歌名搜掉。
        #   副作用：**跟指令重名的歌名就永远搜不了**（例如「打工」「天气」）。
        #
        #   这里加的兜底：用户明确写了 `点歌`/`搜歌` 前缀时，说明他要搜歌；
        #   既然上面精确匹配都没命中，就把剩下的部分当歌名。
        #   ★ `点歌打工` -> 直接播第一首（同 `点歌 歌名`）
        #     `搜歌打工` -> 输出歌曲列表（同 `搜歌 歌名`）
        _fbm = getattr(self, "music_module", None)
        if _fbm is not None and _fbm.enable:
            try:
                _fb = ""
                _fbrest = ""
                _fb_is_list = False
                _pairs = [(str(x), True) for x in
                          (getattr(_fbm, "find_names", None) or []) if x] + \
                    [(str(x), False) for x in
                     (getattr(_fbm, "search_names", None) or []) if x]
                for _nm, _is_list in sorted(_pairs, key=lambda x: len(x[0]),
                                            reverse=True):
                    for _canon in (_nm, "/" + _nm):
                        if not msg_str.startswith(_canon):
                            continue
                        _tail = msg_str[len(_canon):]
                        if not _tail:
                            break
                        if not _tail[0].isspace() and not _tail[0].isalnum():
                            break
                        if _tail.strip():
                            _fb, _fbrest, _fb_is_list = _nm, _tail.strip(), _is_list
                        break
                    if _fb:
                        break
                if _fb and _fbrest and _fbm.is_group_allowed(group_id):
                    logger.info(
                        f"[apix-meme] 音乐兜底：「{_fb}{_fbrest}」"
                        f"（歌名与其它指令重名）-> "
                        f"{'出列表' if _fb_is_list else '直接播第一首'}")
                    items, err = await _fbm.search(_fbrest, None)
                    if err:
                        async for _r in self._send_content(
                                event, f"❌ 搜索失败：{err}"):
                            yield _r
                    elif not items:
                        async for _r in self._send_content(
                                event, _fbm.empty_hint(_fbrest, "")):
                            yield _r
                    elif _fb_is_list:
                        _fbm.save_session(
                            group_id, to_str(event.get_sender_id()), items,
                            _fbm.source)
                        async for _r in self._send_content(
                                event, _fbm.build_search_text(_fbrest, items)):
                            yield _r
                    else:
                        async for _r in self._music_play_pick(
                                event, group_id, "", _fbm, items[0],
                                _fbm.source, skip_search=True):
                            yield _r
                    GLOBAL_STATS["send"] += 1
                    return
            except Exception:
                logger.exception("[apix-meme] 音乐兜底搜索失败")

        if (self.meme_enable
                and group_allowed(group_id, self.meme_groups)
                and self._is_group_feature_enable(group_id, "meme")):
            async for result in self._handle_meme(event, msg_str):
                yield result
            return
    _KW_JOIN_CHARS = " \t\u3000@,"

    def _is_join_char(self, ch: str, s: str, pos: int) -> bool:
        """
        s[pos] 是否算「关键词与参数之间的合法粘合字符」。

        特意支持 @占位符（\x00AT0\x00）：
        群里 @ 某人时，文本里是 \x00AT0\x00，
        紧邻关键词的字符是 \x00 —— 它必须被认作合法边界。
        """
        if not ch:
            return False
        if ch == "\x00":
            return s.startswith("\x00AT", pos) or s.rfind("\x00AT", 0, pos + 1) >= 0
        return ch in self._KW_JOIN_CHARS

    def _strip_at_placeholders(self, s: str) -> str:
        """去掉 @占位符本身，以及紧挨着它的字面 @（如果有）"""
        out = s
        for m in re.finditer(r"\x00AT\d+\x00", s):
            tok = m.group(0)
            prev = out[out.find(tok) - 1: out.find(tok)] if tok in out else ""
            out = out.replace(tok, "", 1)
            if prev == "@":
                out = out.replace("@", "", 1)
        return out

    @staticmethod
    def _make_text_validator():
        """
        文本类参数（url_template 里写 {text}）的校验器。

        与 QQ 号参数不同，这里**允许任意文字**：
          · 去掉两端的空白与常见分隔符（：:，,、）
          · 允许内部有空格、标点、中英文混排
          · 空串不算参数（返回 ""，由调用方判定为「只命中了关键词」）
        """

        def _validate(raw):
            t = str(raw or "").strip()
            if not t:
                return ""
            t = t.lstrip("：:，,、;；").strip()
            return t
        return _validate

    def _match_meme_rule(self, text: str, kw: str, validate,
                         param_type: str = "any",
                         need_param: bool = True) -> "str | None":
        """
        判断 text 里是否以「完整的关键词 + 可选参数」形式出现。

        返回参数字符串（归一化后），不匹配返回 None。

        规则：
          · 关键词必须是完整的词 —— 紧邻字符若存在，必须是
            「参数」的组成部分（数字 / @ / 分隔符），不能是文字或标点。
          · 关键词与参数之间**有没有空格都行**：
            OK 114514 / OK114514 之外的形如 OK@114514、OK@某人 都支持；
            注意 OK114514（纯数字直接粘连）不算，避免误伤。
          · 参数部分必须整体通过 validate。

        例（kw = "OK"）：
          OK                -> ""       命中
          OK 114514         -> 114514   命中
          OK@114514         -> 114514   命中
          OK,114514         -> 114514   命中
          OK@某人            -> 对方QQ    命中
          @某人 OK           -> 对方QQ    命中
          OK啊 114514        -> None     不命中（贴了「啊」）
          OK@114514啊        -> None     不命中
          OK114514          -> None     不命中（关键词与号码粘在一起）
          OK，今天天气不错     -> None     不命中
        """
        if not text or not kw:
            return None

        if param_type == "text":
            validate = self._make_text_validator()

        def _try_rest(rest: str):
            """关键词在最前：rest 必须是合法参数"""
            ok = validate(rest)
            return ok if ok else None

        def _try_head(head: str):
            """关键词在最后：head 必须是合法参数"""
            if not head:
                return None
            ok = validate(head)
            return ok if ok else None

        if text.strip() == kw:
            return "" if not need_param else None

        if text.endswith(kw):
            head = text[: -len(kw)]
            if head and (param_type == "text"
                         or self._is_join_char(head[-1], head, len(head) - 1)
                         or head[-1].isdigit()):
                got = _try_head(head)
                if got is not None:
                    return got

        if text.startswith(kw):
            rest = text[len(kw):]
            if rest and (param_type == "text"
                         or self._is_join_char(rest[0], rest, 0)
                         or rest[0].isdigit()):
                got = _try_rest(rest)
                if got is not None:
                    return got

        idx = text.find(kw)
        if idx > 0:
            head = text[:idx]
            tail = text[idx + len(kw):]
            if (head and tail
                    and self._is_join_char(head[-1], head, len(head) - 1)
                    and self._is_join_char(tail[0], tail, 0)):
                ok_h = validate(head)
                ok_t = validate(tail)
                if ok_h and ok_t:
                    return (ok_h + " " + ok_t).strip()
                if ok_h:
                    return ok_h
                if ok_t:
                    return ok_t
        return None

    async def _handle_meme(self, event: AstrMessageEvent, msg_str: str):
        group_id = _get_group_id_from_event(event)

        raw_clean, at_map = split_text_and_ats(event)
        raw_clean = " ".join(raw_clean.split())
        token_map = {}
        text_for_match = raw_clean
        for i, ph in enumerate(list(at_map.keys())):
            token = f" \x00AT{i}\x00 "
            text_for_match = text_for_match.replace(ph, token)
            token_map[token.strip()] = at_map[ph]
        text_for_match = " ".join(text_for_match.split())
        at_map = token_map

        if not text_for_match:
            return

        matched_rule = None
        matched_kw = ""
        param_str = ""
        _flat = []
        for _r in self.rules:
            for _k in (_r.get("keywords") or []):
                if _k:
                    _flat.append((len(_k), _r, _k))
        _flat.sort(key=lambda x: x[0], reverse=True)

        for _len, rule, kw in _flat:
            if not kw:
                continue
            try:
                _pc = int(rule.get("param_count", "1") or 0)
            except Exception:
                _pc = 1
            _ptypes = detect_param_types(
                rule.get("url_template", ""), max(0, _pc)
            )
            _ptype = _ptypes[0] if _ptypes else "any"
            _ps = self._match_meme_rule(text_for_match, kw,
                                        validate_meme_params, _ptype,
                                        need_param=(_pc > 0))
            if _ps is not None:
                matched_rule = rule
                matched_kw = kw
                param_str = _ps
                break
        if not matched_rule:
            _maybe = [k for r in self.rules for k in r["keywords"]
                      if k and k in text_for_match]
            if _maybe:
                logger.info(
                    f"[apix-meme] 命中关键词 {_maybe[0]!r} 但其余内容不是参数"
                    f"（该接口需要 QQ号 / @人 / 文本参数），已忽略本条"
                )

        if not matched_rule:
            return

        if is_keyword_blocked(group_id, matched_kw, self.group_block_map):
            return

        try:
            need = int(matched_rule.get("param_count", "1"))
        except Exception:
            need = 1
        if need < 0:
            need = 0

        param_types = detect_param_types(
            matched_rule.get("url_template", ""), need
        )
        _sender_id = to_str(event.get_sender_id())
        _bot_id = to_str(event.get_self_id())

        if need == 0:
            params = []
        else:
            if not str(param_str or "").strip():
                logger.info(
                    "[apix-meme] 接口「%s」需要参数，但这条消息没带参数，已忽略",
                    matched_kw)
                return
            param_str = restore_ats(param_str, at_map)
            if "\x00" in param_str:
                param_str = re.sub(r"\x00AT\d+\x00", " ", param_str)
            params = parse_params(param_str) if param_str else []
            for i in range(need):
                if i < len(params):
                    continue
                if param_types[i] == "qq" and _sender_id:
                    params.append(_sender_id)
            if len(params) < need:
                return
            params = [
                resolve_param_refs(p, _sender_id, _bot_id) for p in params
            ]
            for i in range(need):
                if param_types[i] == "qq":
                    if not params[i].isdigit():
                        logger.warning(
                            f"[apix-meme] 接口「{matched_kw}」第 {i+1} 个参数"
                            f"需要 QQ 号，收到 {params[i]!r}；"
                            f"可用 {{{{qq}}}} 表示自己，或直接 @ 某人"
                        )
                        return

        url = build_url(matched_rule["url_template"], params)

        raw_media = str(matched_rule.get("media_type", "") or "").strip().lower()
        json_field = str(matched_rule.get("json_field", "") or "").strip()

        if raw_media == "text":
            try:
                text = await fetch_text(url, json_field=json_field)
            except Exception as e:
                # ★★ 1.5.0：接口运行时失败 —— 计入「接口错误次数」，
                #   并用 ERROR 级日志（后台「错误次数」也会 +1）
                _bump_api_err()
                logger.error(
                    f"[apix-meme] 接口「{matched_kw}」文本获取失败：{e}")
                return
            if not text:
                # 请求成功但**没拿到内容** —— 同样是「接口坏了」
                _bump_api_err()
                logger.error(
                    f"[apix-meme] 接口「{matched_kw}」文本为空"
                    f"（接口没返回内容）")
                return
            async for _r in self._send_content(event, text):
                yield _r
            GLOBAL_STATS["send"] += 1
            return

        expect_kind = raw_media if raw_media in ("image", "video") else ""
        try:
            media_path, media_kind = await download_media(
                url, expect=expect_kind, json_field=json_field
            )
        except Exception as e:
            # ★★ 1.5.0：接口运行时失败 —— 同上
            _bump_api_err()
            logger.error(
                f"[apix-meme] 接口「{matched_kw}」媒体获取失败：{e}")
            return

        if expect_kind and media_kind != expect_kind:
            logger.warning(
                f"[apix-meme] 规则标注为 {expect_kind}，但接口返回 {media_kind}，按实际类型发送"
            )

        try:
            if media_kind == "video":
                chain = [Video.fromFileSystem(media_path)]
            else:
                chain = [Image.fromFileSystem(media_path)]
            yield event.chain_result(chain)
            GLOBAL_STATS["send"] += 1
        except Exception:
            _bump_api_err()
            logger.exception(f"[apix-meme] 接口「{matched_kw}」发送媒体失败")
