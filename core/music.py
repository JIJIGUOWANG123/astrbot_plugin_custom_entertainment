"""
点歌功能（市场版 · **只发卡片**）

数据来源：后台可自定义的**单一**接口地址（默认 https://music.90svip.cn/）
  搜索：POST <接口地址>   data={input, filter=name, type, page}
        返回 {"data":[{name, artist, songid, link, url, cover, lrc}, ...]}
  音频：直接用搜索结果里的歌曲直链（`url` 字段）写进卡片，交给协议端播放

输出：**只发音乐卡片** —— 用 AstrBot 原生的
      `astrbot.api.message_components.Music` 组件，
      **由平台适配器负责签名**。插件自己不调任何外部签名服务、
      不自己拼 ark、也不发语音。

卡片封面二选一（`music_card_cover_source`）：
  · brand  —— 音源 LOGO（按这首歌的音源取对应平台官方图标，直链）
  · avatar —— 点歌那个群员的 QQ 头像（直链）

★ 封面全部是直链 URL：**不下载、不做图像处理、不写磁盘、不缓存**。
★ 音源只有三个：netease（网易）/ qq（QQ音乐）/ kugou（酷狗）。

指令流程：
  点歌 歌名     -> 搜索后**直接播第一首**
  搜歌 歌名     -> 列出前 N 首（后台可配数量），再用「选歌 序号」挑
  音源 [音源名] -> 查看 / 切换默认音源
  新歌榜随机 / 热歌榜随机 -> 从 QQ音乐榜单抽一首直接播
"""
import json
import re
import time

from .path_utils import title_bar, cmd_arg
from .path_utils import register_cmd_names as _register_cmd_names_impl

from astrbot.api import logger

try:
    import aiohttp
    _HAS_AIOHTTP = True
except Exception:
    _HAS_AIOHTTP = False

DEFAULT_BASE = "https://music.90svip.cn/"

# ★★ 卡片封面只保留两种（歌曲封面 / 下载 / 图像处理 / 缓存那一套已整块移除）
CARD_COVER_STYLES = ("brand", "avatar")
DEFAULT_COVER_STYLE = "brand"

# ★★ 品牌 LOGO：**各平台官方 icon 图床的直链**（实测均可直接访问，无防盗链）。
#   按「这首歌的音源」取对应的一张 —— 不是固定某一张。
MUSIC_BRAND_LOGO = {
    # QQ音乐：黄底绿音符
    "qq": ("https://p.qpic.cn/qqconnect/0/app_100497308_1626060999/100"
           "?max-age=2592000&t=0"),
    # 网易云音乐：红底白音符
    "netease": "https://i.gtimg.cn/open/app_icon/00/49/50/85/100495085_100_m.png",
    # 酷狗音乐：蓝底白色 K
    "kugou": "https://open.gtimg.cn/open/app_icon/00/20/51/41/205141_100_m.png",
}


def qq_avatar_url(qq: str) -> str:
    """点歌群员的 QQ 头像直链（640x640，稳定图床）"""
    uid = str(qq or "").strip()
    if not uid.isdigit():
        return ""
    return f"https://q1.qlogo.cn/g?b=qq&nk={uid}&s=640"


# 卡片 `type`：按音源给平台对应的品牌类型
MUSIC_CARD_TYPE = {
    "kugou": "kugou",
    "netease": "163",
    "qq": "qq",
}
DEFAULT_CARD_TYPE = "custom"

# 有**原生平台卡片**的音源（用 songid 当 id，交给平台自己渲染）
PLATFORM_CARD_SOURCES = {"netease"}

# 卡片 `image` 是必需字段，取不到封面时兜一张稳定可访问的官方图标
DEFAULT_CARD_IMAGE = (
    "https://p.qpic.cn/qqconnect/0/app_100497308_1626060999/100")


def resolve_music_base(raw: str) -> str:
    """
    把后台填的接口地址归一化成**带结尾斜杠的完整 URL**。

    接受：完整地址 / 域名；认不出就回落默认（默认 https://music.90svip.cn/）。
    ★ 只有一个接口地址，全插件所有音乐请求都走它。
    """
    v = str(raw or "").strip()
    if not v:
        return DEFAULT_BASE
    if not v.startswith(("http://", "https://")):
        _host = v.lstrip("/").split("/")[0]
        if not re.match(r"^[A-Za-z0-9._-]+\.[A-Za-z]{2,}$", _host):
            logger.warning(f"[music] 接口地址无法识别：{v!r} -> 回落默认")
            return DEFAULT_BASE
        v = "https://" + v.lstrip("/")
    if not re.match(r"^https?://[A-Za-z0-9._-]+\.[A-Za-z]{2,}", v):
        logger.warning(f"[music] 接口地址格式不正确：{v!r} -> 回落默认")
        return DEFAULT_BASE
    return v if v.endswith("/") else v + "/"


DEFAULT_SOURCE = "qq"
MUSIC_CARD_MAX_CHARS = 40
DEFAULT_LIMIT = 10

MENU_DEFAULT_NAMES = ["音乐功能"]
RETIRED_MENU_NAMES = {"点歌功能"}

SEARCH_CACHE_TTL = 90.0
_SEARCH_CACHE = {}


def _search_cache_get(key):
    """取缓存；过期自动清掉。命中返回 items，未命中返回 None。"""
    hit = _SEARCH_CACHE.get(key)
    if not hit:
        return None
    ts, items = hit
    if time.time() - ts > SEARCH_CACHE_TTL:
        _SEARCH_CACHE.pop(key, None)
        return None
    return items


def _search_cache_put(key, items):
    """写缓存；超过 64 条就丢掉一半最旧的，避免无限增长。"""
    if len(_SEARCH_CACHE) > 64:
        old = sorted(_SEARCH_CACHE.items(), key=lambda kv: kv[1][0])
        for k, _ in old[:32]:
            _SEARCH_CACHE.pop(k, None)
    _SEARCH_CACHE[key] = (time.time(), list(items or []))


def clear_search_cache():
    """清空搜索缓存（出问题时可用）"""
    _SEARCH_CACHE.clear()


SOURCES = {
    "netease": ("netease", "网易"),
    "qq": ("qq", "QQ"),
    "kugou": ("kugou", "酷狗"),
}

RETIRED_SOURCES = {
    "kuwo": "酷我", "migu": "咪咕", "qianqian": "千千",
}

SOURCE_ALIAS = {
    "网易": "netease", "网易云": "netease", "netease": "netease",
    "wy": "netease", "wangyi": "netease",
    "qq": "qq", "qq音乐": "qq", "qqmusic": "qq", "qqyy": "qq",
    "酷狗": "kugou", "kugou": "kugou", "kg": "kugou",
    "酷我": "kuwo", "kuwo": "kuwo", "kw": "kuwo",
    "咪咕": "migu", "migu": "migu", "mg": "migu",
    "千千": "qianqian", "千千音乐": "qianqian", "qianqian": "qianqian",
    "qqq": "qianqian", "91q": "qianqian",
}

SOURCE_ORDER = ["netease", "qq", "kugou"]


def is_retired_source(code: str) -> bool:
    """该音源代号是不是**已下线**"""
    return str(code or "").strip().lower() in RETIRED_SOURCES


def source_label(code: str) -> str:
    """内部代号 -> 中文名"""
    return (SOURCES.get(str(code or "").strip().lower()) or ("", str(code)))[1]


def resolve_source(text: str):
    """把用户输入的音源名解析成内部代号；解析不出返回 "" """
    t = str(text or "").strip().lower()
    if not t:
        return ""
    if t in SOURCE_ALIAS:
        return SOURCE_ALIAS[t]
    t2 = "".join(ch for ch in t if not ch.isspace())
    return SOURCE_ALIAS.get(t2, "")


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _parse_names(raw: str) -> list:
    if not raw:
        return []
    return [x.strip() for x in str(raw).split(",") if x.strip()]


def _clean_query(text: str) -> str:
    """把「歌名 - 歌手」「歌名 歌手」等写法原样交给接口，只做去噪"""
    t = str(text or "").strip()
    t = re.sub(r"^[《【\[（(]+", "", t)
    t = re.sub(r"[》】\]）)]+$", "", t)
    return t.strip()


_SHARED_SESS = [None]


def _shared_session(headers: dict = None, timeout=None):
    """
    取共享的 aiohttp ClientSession（连接池复用）。

    ★ 只复用**默认头**的会话；带自定义 headers 的调用直接传 `headers=`
      给请求，避免不同调用方互相污染。
    ★ 会话失效（事件循环换了 / 被关掉）会自动重建。
    """
    if not _HAS_AIOHTTP:
        return None
    sess = _SHARED_SESS[0]
    if sess is not None and not sess.closed:
        return sess
    try:
        sess = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=90, sock_read=60),
            headers={"User-Agent": _UA},
        )
        _SHARED_SESS[0] = sess
        return sess
    except Exception:
        logger.debug("[music] 创建共享会话失败", exc_info=True)
        return None


def make_music_card(it: dict, cover_url: str = "", audio_url: str = "",
                    title: str = "", desc: str = ""):
    """
    构造音乐卡片组件（AstrBot 原生 `Music`），不可用时返回 None。

    ★ 走**平台适配器自己的签名链路** —— 插件只把组件塞进消息链，
      不调任何外部签名服务、不自己拼 ark。

    · 网易（`PLATFORM_CARD_SOURCES`）有原生平台卡，用 `songid` 当 id
    · QQ / 酷狗 用 custom 数据流（不给 id，直接用插件给的
      url / audio / title / image），平台侧按 `type` 显示对应品牌

    ⚠️ AstrBot 的 `Music._type` 以 `_` 开头，pydantic 会当成私有字段，
       **构造函数里传了也会被丢掉**，所以构造完用 `object.__setattr__`
       补回去 —— 否则 `toDict()` 出来的 data 里没有 `type`。
    """
    try:
        from astrbot.api.message_components import Music as _Music
    except Exception:
        return None
    if _Music is None:
        return None

    src = str(it.get("source", "") or "").strip().lower()
    songid = str(it.get("songid", "") or "").strip()
    name = str(title or it.get("name", "") or "").strip()
    if not name:
        return None
    _au = str(audio_url or it.get("url", "") or "").strip()
    if not _au.startswith(("http://", "https://")):
        return None
    _jump = str(it.get("link", "") or "").strip()
    if not _jump.startswith(("http://", "https://")):
        _jump = _au
    _img = str(cover_url or "").strip()
    if not _img.startswith(("http://", "https://")):
        _img = DEFAULT_CARD_IMAGE

    ctype = MUSIC_CARD_TYPE.get(src, DEFAULT_CARD_TYPE)
    kwargs = {
        "title": name[:MUSIC_CARD_MAX_CHARS],
        "content": str(desc or it.get("artist", "") or "")[:MUSIC_CARD_MAX_CHARS],
        "url": _jump,
        "audio": _au,
        "image": _img,
    }
    if src in PLATFORM_CARD_SOURCES and songid.isdigit():
        # 原生平台卡：平台用自己的 id 取数据，不给 id 会被当成 custom
        kwargs["id"] = int(songid)
    else:
        # custom 数据流：**不给 id**（给了平台就会按平台类型去查，反而取不到）
        kwargs["id"] = None
    try:
        card = _Music(**kwargs)
    except Exception:
        logger.debug("[music] 构造 Music 组件失败", exc_info=True)
        return None
    try:
        object.__setattr__(card, "_type", ctype)
    except Exception:
        logger.debug("[music] 写入卡片 type 失败", exc_info=True)
    return card


class MusicModule:
    """点歌功能（只发卡片）"""

    def __init__(self, plugin_dir: str, data_path: str = ""):
        self.plugin_dir = plugin_dir
        self.data_path = data_path
        self.enable = True
        self.search_names = ["点歌"]
        self.find_names = ["搜歌"]
        self.pick_names = ["选歌"]
        self.span_new_names = ["新歌榜随机"]
        self.span_hot_names = ["热歌榜随机"]
        self.source_names = ["音源"]
        self.menu_names = list(MENU_DEFAULT_NAMES)
        self.limit = DEFAULT_LIMIT
        self.base_url = DEFAULT_BASE
        self.source = DEFAULT_SOURCE
        self.allowed_groups = []
        self.sessions = {}
        self.session_ttl = 600
        self._cover_style = DEFAULT_COVER_STYLE

    def reload(self, cfg: dict):
        cfg = cfg if isinstance(cfg, dict) else {}
        self.enable = bool(cfg.get("music_enable", True))

        names = _parse_names(str(cfg.get("cmd_music_search_names", "点歌") or ""))
        self.search_names = [names[0]] if names else ["点歌"]

        names = _parse_names(str(cfg.get("cmd_music_find_names", "搜歌") or ""))
        self.find_names = names if names else ["搜歌"]

        names = _parse_names(str(cfg.get("cmd_music_pick_names", "选歌") or ""))
        self.pick_names = names if names else ["选歌"]

        names = _parse_names(str(
            cfg.get("cmd_music_span_new_names", "新歌榜随机") or ""))
        self.span_new_names = names if names else ["新歌榜随机"]

        names = _parse_names(str(
            cfg.get("cmd_music_span_hot_names", "热歌榜随机") or ""))
        self.span_hot_names = names if names else ["热歌榜随机"]

        names = _parse_names(str(
            cfg.get("cmd_music_source_names", "音源,切换音源") or ""))
        self.source_names = names if names else ["音源"]

        # ★ 卡片封面：brand（音源 LOGO）/ avatar（点歌人头像），没有第三种
        _st = str(cfg.get("music_card_cover_source", DEFAULT_COVER_STYLE)
                  or DEFAULT_COVER_STYLE).strip().lower()
        self._cover_style = _st if _st in CARD_COVER_STYLES else DEFAULT_COVER_STYLE

        # ★ 菜单入口名（与 main.py 的 MENU_ENTRY_DEFS["music"] 同一个配置键）
        extra = _parse_names(str(cfg.get("cmd_music_menu_extra_names", "") or ""))
        legacy = _parse_names(str(cfg.get("menu_music_names", "") or ""))
        names = list(MENU_DEFAULT_NAMES)
        for n in list(legacy) + list(extra):
            if not n or n in names:
                continue
            if n in RETIRED_MENU_NAMES:
                logger.info(f"[music] 忽略已停用的菜单入口名：{n}")
                continue
            names.append(n)
        self.menu_names = names or ["音乐功能"]

        try:
            n = int(float(str(cfg.get("music_search_limit", DEFAULT_LIMIT)
                              or DEFAULT_LIMIT)))
        except Exception:
            n = DEFAULT_LIMIT
        self.limit = max(5, min(30, n))

        # ★ 只有一个接口地址：全插件所有音乐请求都走它
        self.base_url = resolve_music_base(
            cfg.get("music_api_base", DEFAULT_BASE))

        raw_src = str(cfg.get("music_default_source", DEFAULT_SOURCE)
                      or DEFAULT_SOURCE)
        src = resolve_source(raw_src) or str(raw_src).strip().lower()
        self.source = src if src in SOURCES else DEFAULT_SOURCE

        raw = str(cfg.get("music_groups", "") or "")
        self.allowed_groups = [x.strip() for x in re.split(r"[,，、;；\s]+", raw)
                               if x.strip().isdigit()]

        self._register_cmd_names()

    # ---------- 卡片封面（只有两种直链，不下载不缓存） ----------

    def cover_style(self) -> str:
        """卡片封面用哪张图：brand（音源 LOGO）/ avatar（点歌人头像）"""
        v = str(getattr(self, "_cover_style", DEFAULT_COVER_STYLE)
                or "").strip().lower()
        return v if v in CARD_COVER_STYLES else DEFAULT_COVER_STYLE

    def cover_source(self) -> str:
        """`cover_style()` 的旧名（菜单/兼容用）"""
        return self.cover_style()

    def brand_logo(self, source: str = "") -> str:
        """按音源取品牌 LOGO（各平台官方图标直链）"""
        src = str(source or "").strip().lower()
        return str(MUSIC_BRAND_LOGO.get(src, "") or "")

    def resolve_card_cover(self, it: dict, user_id: str = "") -> str:
        """
        取卡片封面直链（**纯拼接，不发任何请求**）。

        brand  -> 按这首歌的音源取对应平台的官方 LOGO
        avatar -> 点歌那个群员的 QQ 头像
        取不到就返回空串（调用方会用兜底图，保证 image 非空）。
        """
        style = self.cover_style()
        if style == "avatar":
            av = qq_avatar_url(user_id)
            if av:
                logger.info(f"[music] 卡片封面用点歌人头像：{av[:70]}")
                return av
            logger.info("[music] 拿不到点歌人 QQ 号，封面回退音源 LOGO")
        logo = self.brand_logo(str(it.get("source", "") or ""))
        if logo:
            logger.info(f"[music] 卡片封面用音源 LOGO：{logo[:70]}")
        return logo

    # ---------- 输出形态（只有卡片） ----------

    def use_card(self) -> bool:
        """是否使用卡片模式输出 —— 市场版**恒为卡片**"""
        return True

    def output_mode_label(self) -> str:
        return "卡片模式"

    def card_reliable_for(self, source: str = "") -> bool:
        """该音源卡片是否可用：三个音源都走原生 Music 组件，都可用"""
        return str(source or self.source or "").strip().lower() in SOURCES

    def use_card_for(self, source: str = "") -> bool:
        return self.card_reliable_for(source)

    def make_music_card(self, it: dict, cover_url: str = "",
                        audio_url: str = "", title: str = "",
                        desc: str = ""):
        """构造音乐卡片组件（转调模块级 `make_music_card`，方便调用方只持有模块）"""
        return make_music_card(it, cover_url=cover_url, audio_url=audio_url,
                               title=title, desc=desc)

    def build_card_text(self, it: dict, page_url: str = "",
                        direct_url: str = "") -> str:
        """卡片模式的文本：卡片已带歌名/封面/播放按钮，文字只留最小提示"""
        name = str(it.get("name", "") or "")
        artist = str(it.get("artist", "") or "")
        src_label = source_label(str(it.get("source", "") or ""))
        title = f"{name} - {artist}" if artist else name
        lines = [f"🎵 {title}"]
        if src_label:
            lines.append(f"📻 来源：{src_label}")
        url_audio = direct_url or str(it.get("url", "") or "")
        if url_audio:
            lines.append(f"⬇️ 音乐地址：{url_audio}")
        lines.append("🎧 点卡片上的播放键即可收听")
        return "\n".join(lines)

    # ---------- 指令名 ----------

    @property
    def search_name(self) -> str:
        return (self.search_names or ["点歌"])[0]

    @property
    def pick_name(self) -> str:
        return (self.pick_names or ["选歌"])[0]

    @property
    def find_name(self) -> str:
        """「搜歌」指令名（输出列表供选歌）"""
        return (getattr(self, "find_names", None) or ["搜歌"])[0]

    def _register_cmd_names(self):
        """
        把音乐指令名登记到全局表。

        ★ 为什么：`搜歌QQ不要说话` 这种，短指令 `搜歌` 不该把
          `QQ不要说话` 当成歌名 —— 登记后 `cmd_arg` 能认出
          「这段以另一条指令/音源前缀开头」，于是不当参数处理。
        """
        try:
            _all = []
            for _k, _v in vars(self).items():
                if not _k.endswith("_names"):
                    continue
                if isinstance(_v, str):
                    if _v.strip():
                        _all.append(_v.strip())
                elif isinstance(_v, (list, tuple, set)):
                    _all.extend(str(x).strip() for x in _v if str(x).strip())
            return _register_cmd_names_impl(_all)
        except Exception:
            return 0

    def is_group_allowed(self, group_id) -> bool:
        """群号黑名单：留空 = 所有群都生效"""
        if not self.allowed_groups:
            return True
        return str(group_id or "") not in self.allowed_groups

    def _arg_is_other_cmd(self, arg: str) -> bool:
        """
        剩下的这段「歌名」其实是不是**另一条指令**？

        ★ 指令名之间会互相包含 —— `点歌模式` / `点歌功能` 都以 `点歌` 开头。
          不判掉的话 `点歌功能` 会变成搜「功能」。
        """
        a = str(arg or "").strip()
        if not a:
            return False
        try:
            from .path_utils import known_cmd_names as _kcn
        except Exception:
            try:
                from core.path_utils import known_cmd_names as _kcn
            except Exception:
                return False
        try:
            names = _kcn()
        except Exception:
            return False
        for nm in names:
            nm = str(nm or "").strip()
            if not nm:
                continue
            if a == nm:
                return True
            if nm.endswith(a) or a.startswith(nm):
                return True
        try:
            from .path_utils import _src_store as _ss
            for _p in _ss():
                _p = str(_p or "")
                if _p and a.lower() == _p.lower():
                    return True
        except Exception:
            pass
        return False

    # ---------- 匹配 ----------

    def match_search(self, text: str):
        """
        返回歌名；只发指令名时返回 ""（提示用法）；不匹配返回 None。
        ★ 空格**可有可无** —— `点歌不要说话` 也认。
        """
        if not self.enable:
            return None
        t = str(text or "").strip()
        if not t:
            return None
        try:
            if self.match_search_by_source(t) is not None:
                return None
        except Exception:
            pass
        _r = cmd_arg(t, self.search_names or ["点歌"])
        if _r and self._arg_is_other_cmd(_r):
            return None
        return _r

    def match_find(self, text: str):
        """
        「搜歌」：返回歌名；只发指令名时返回 ""（提示用法）；不匹配 None。

        · `搜歌 歌名` -> **输出歌曲列表**（默认后台十首）
        · `选歌 序号` -> 挑其中一首播放
        """
        if not self.enable:
            return None
        t = str(text or "").strip()
        if not t:
            return None
        try:
            if self.match_find_by_source(t) is not None:
                return None
        except Exception:
            pass
        _r = cmd_arg(t, getattr(self, "find_names", None) or ["搜歌"])
        if _r and self._arg_is_other_cmd(_r):
            return None
        return _r

    def match_pick(self, text: str):
        """返回序号字符串；只发指令名时返回 ""；不匹配返回 None。"""
        if not self.enable:
            return None
        t = str(text or "").strip()
        if not t:
            return None
        return cmd_arg(t, self.pick_names or ["选歌"])

    @staticmethod
    def parse_index(raw):
        """从「选歌 3」的参数里取出序号；解析不出返回 0"""
        t = str(raw or "").strip()
        m = re.search(r"\d+", t)
        if not m:
            return 0
        try:
            return int(m.group(0))
        except Exception:
            return 0

    def match_menu(self, text: str) -> bool:
        """菜单入口匹配（支持后台自定义多个名字）"""
        t = str(text or "").strip()
        if not t:
            return False
        for name in self.menu_names or []:
            if name and (t == name or t == f"/{name}"):
                return True
        return False

    @property
    def menu_name(self) -> str:
        return (self.menu_names or ["音乐功能"])[0]

    def match_span(self, text: str):
        """
        榜单随机指令：
          `新歌榜随机` -> "new"     `热歌榜随机` -> "hot"
        没命中返回 None。
        """
        t = str(text or "").strip()
        if not t:
            return None
        for nm in (self.span_new_names or ["新歌榜随机"]):
            if nm and (t == nm or t == f"/{nm}"):
                return "new"
        for nm in (self.span_hot_names or ["热歌榜随机"]):
            if nm and (t == nm or t == f"/{nm}"):
                return "hot"
        return None

    @property
    def span_new_name(self) -> str:
        return (self.span_new_names or ["新歌榜随机"])[0]

    @property
    def span_hot_name(self) -> str:
        return (self.span_hot_names or ["热歌榜随机"])[0]

    def source_command_pairs(self):
        """
        生成「音源专属点歌指令」表：[(指令词, 音源代号), ...]

        由「音源中文名 + 短别名」+「点歌指令名」拼成，
        例如：QQ点歌 / 网易点歌 / 酷狗点歌 / 网易云点歌 / qq点歌。

        只保留**中文名**与**短别名（≤3 字符）**，
        避免生成 netease点歌 / wangyi点歌 / qqmusic点歌 这类冗长写法。
        返回按**长度倒序**，保证长指令先被匹配。
        """
        names = []
        seen = set()
        codes = list(self.source_order())

        def _accept(name: str) -> bool:
            if not name:
                return False
            if any("\u4e00" <= ch <= "\u9fff" for ch in name):
                return True
            return 2 <= len(name) <= 3

        for code in codes:
            label = source_label(code)
            if label:
                names.append((label, code))
        _SKIP_ALIAS = {"qqq", "91q"}
        for alias, code in SOURCE_ALIAS.items():
            a = str(alias)
            if a.lower() in _SKIP_ALIAS:
                continue
            if code not in codes:
                continue
            if _accept(a):
                names.append((a, code))

        pairs = []
        cmd = str(getattr(self, "search_name", "") or "点歌")
        for name, code in names:
            key = (name, code)
            if not name or key in seen:
                continue
            seen.add(key)
            pairs.append((f"{name}{cmd}", code))
        pairs.sort(key=lambda x: len(x[0]), reverse=True)
        return pairs

    def find_command_pairs(self):
        """
        「音源专属**搜歌**指令」表：[(指令词, 音源代号), ...]
        例如：QQ搜歌 / 网易搜歌 / 网易云搜歌 / qq搜歌。
        """
        names = []
        seen = set()
        codes = list(self.source_order())

        def _accept(name: str) -> bool:
            if not name:
                return False
            if any("\u4e00" <= ch <= "\u9fff" for ch in name):
                return True
            return 2 <= len(name) <= 3

        for code in codes:
            label = source_label(code)
            if label:
                names.append((label, code))
        _SKIP_ALIAS = {"qqq", "91q"}
        for alias, code in SOURCE_ALIAS.items():
            a = str(alias)
            if a.lower() in _SKIP_ALIAS:
                continue
            if code not in codes:
                continue
            if _accept(a):
                names.append((a, code))

        pairs = []
        for cmd in (getattr(self, "find_names", None) or ["搜歌"]):
            cmd = str(cmd).strip()
            if not cmd:
                continue
            for name, code in names:
                key = (name, code, cmd)
                if not name or key in seen:
                    continue
                seen.add(key)
                pairs.append((f"{name}{cmd}", code))
        pairs.sort(key=lambda x: len(x[0]), reverse=True)
        return pairs

    def match_find_by_source(self, text: str):
        """
        音源专属**搜歌**指令。支持两种写法：
            QQ搜歌 歌名      -> ("qq", "歌名")
            搜歌 QQ 歌名     -> ("qq", "歌名")
        只发「QQ搜歌」不带歌名时返回 (code, "")，用于提示用法。
        """
        if not self.enable:
            return None
        t = str(text or "").strip()
        if not t:
            return None

        for pref, code in self.find_command_pairs():
            r = cmd_arg(t, [pref])
            if r is not None:
                return (code, r)

        for cmd in (getattr(self, "find_names", None) or ["搜歌"]):
            cmd = str(cmd).strip()
            if not cmd:
                continue
            for c in (cmd + " ", f"/{cmd} "):
                if not t.startswith(c):
                    continue
                rest = t[len(c):].strip()
                if not rest:
                    return None
                head, _, tail = rest.partition(" ")
                code = resolve_source(head)
                if code:
                    return (code, tail.strip())
        return None

    def match_search_by_source(self, text: str):
        """
        音源专属点歌指令。支持两种写法：
            QQ点歌 歌名      -> ("qq", "歌名")
            点歌 QQ 歌名     -> ("qq", "歌名")
        只发「QQ点歌」不带歌名时返回 (code, "")，用于提示用法。
        """
        if not self.enable:
            return None
        t = str(text or "").strip()
        if not t:
            return None

        for pref, code in self.source_command_pairs():
            r = cmd_arg(t, [pref])
            if r is not None:
                return (code, r)

        cmd = str(getattr(self, "search_name", "") or "点歌")
        for c in (cmd + " ", f"/{cmd} "):
            if not t.startswith(c):
                continue
            rest = t[len(c):].strip()
            if not rest:
                return None
            head, _, tail = rest.partition(" ")
            code = resolve_source(head)
            if code:
                return (code, tail.strip())
        return None

    def match_source(self, text: str):
        """
        音源切换指令：
            音源            -> 返回 ""（查询当前音源）
            音源 网易       -> 返回 "网易"（切换）
        不匹配返回 None
        """
        if not self.enable:
            return None
        t = str(text or "").strip()
        if not t:
            return None
        for name in self.source_names:
            if not name:
                continue
            for pref in (name + " ", f"/{name} "):
                if t.startswith(pref):
                    return t[len(pref):].strip()
            if t == name or t == f"/{name}":
                return ""
        return None

    def build_source_text(self, current: str = "") -> str:
        """音源列表 / 当前音源"""
        cur = current or self.source
        lines = [f"{title_bar('音源切换')}", ""]
        for i, code in enumerate(SOURCE_ORDER, start=1):
            mark = "✅ " if code == cur else "　 "
            lines.append(f"{mark}{i}. {source_label(code)}")
        lines.append("")
        lines.append(f"📍 当前音源：{source_label(cur)}")
        lines.append("")
        cmd = (self.source_names or ["音源"])[0]
        labels = [source_label(c) for c in SOURCE_ORDER]
        codes = [str(c) for c in SOURCE_ORDER]
        lines.append("💡 切换：" + " / ".join(f"「{cmd} {n}」" for n in labels))
        lines.append(f"💡 也可写代号：{cmd} " + " / ".join(codes))
        lines.append("")
        lines.append(
            "💡 只想换一首的音源："
            + " / ".join(f"{n}{self.search_name} 歌名" for n in labels)
        )
        return "\n".join(lines)

    @staticmethod
    def source_order() -> list:
        """全部音源代号（按固定顺序）"""
        return list(SOURCE_ORDER)

    def source_label(self, code: str = "") -> str:
        """音源代号 -> 中文名；不传则用当前音源"""
        return source_label(code or self.source)

    def parse_source_arg(self, raw: str):
        """
        解析用户给的音源；返回 (内部代号, 错误信息)

        只接受**当前在用的音源**（SOURCES 里的），
        已下线的别名（酷我 / 咪咕 / 千千）会被判为 unknown。
        """
        t = str(raw or "").strip()
        if not t:
            return "", "empty"
        code = resolve_source(t)
        if not code or code not in SOURCES:
            return "", "unknown"
        return code, ""

    def is_valid_source(self, code: str) -> bool:
        """该代号是否是当前在用的音源"""
        return str(code or "").strip().lower() in SOURCES

    # ---------- 会话（搜歌 -> 选歌） ----------

    def _sess_key(self, group_id, user_id) -> str:
        return f"{group_id}|{user_id}"

    def save_session(self, group_id, user_id, items: list, source: str = ""):
        """
        每次搜索都会重置会话（used=False），即「重新点歌后可再选一次」。

        source 记录本次搜索用的音源：选歌时按它取文件，
        这样搜索后即使默认音源被改，也能选到原来那首。
        """
        key = self._sess_key(group_id, user_id)
        self.sessions[key] = {
            "items": list(items or []),
            "ts": time.time(),
            "used": False,
            "owner": str(user_id or ""),
            "source": str(source or getattr(self, "source", "") or ""),
        }
        now = time.time()
        for k in list(self.sessions.keys()):
            if now - float(self.sessions[k].get("ts", 0) or 0) > self.session_ttl:
                self.sessions.pop(k, None)

    def get_session_source(self, group_id, user_id) -> str:
        """取会话里记录的音源（选歌阶段用）"""
        s = self.sessions.get(self._sess_key(group_id, user_id))
        if not s:
            return ""
        return str(s.get("source", "") or "")

    def get_session(self, group_id, user_id) -> list:
        s = self.sessions.get(self._sess_key(group_id, user_id))
        if not s:
            return []
        if time.time() - float(s.get("ts", 0) or 0) > self.session_ttl:
            self.sessions.pop(self._sess_key(group_id, user_id), None)
            return []
        return list(s.get("items", []))

    def consume_session(self, group_id, user_id):
        """
        取用会话（选歌用）。返回 (items, err)：
            err="none"  -> 没有搜索结果，需要先搜歌
            err="used"  -> 这次搜索结果已经选过了，需要重新搜歌
            err=""      -> 可用（并标记为已用，一次搜索只能选一次）
        """
        key = self._sess_key(group_id, user_id)
        s = self.sessions.get(key)
        if not s:
            return [], "none"
        if time.time() - float(s.get("ts", 0) or 0) > self.session_ttl:
            self.sessions.pop(key, None)
            return [], "none"
        if s.get("used"):
            return [], "used"
        s["used"] = True
        s["ts"] = time.time()
        return list(s.get("items", [])), ""

    def restore_session(self, group_id, user_id):
        """把「已用」标记退回（序号无效时，让用户还能重选）"""
        s = self.sessions.get(self._sess_key(group_id, user_id))
        if isinstance(s, dict):
            s["used"] = False

    # ---------- 搜索 ----------

    async def _post_search_one(self, base: str, query: str, source: str,
                               page: int = 1):
        """按接口地址搜一次；返回 (items, err)"""
        payload = {
            "input": query,
            "filter": "name",
            "type": (SOURCES.get(source) or SOURCES[DEFAULT_SOURCE])[0],
            "page": page,
        }
        headers = {
            "User-Agent": _UA,
            "Referer": base,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        sess = _shared_session()
        if sess is None:
            return None, "缺少 aiohttp 依赖"
        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with sess.post(base, data=payload, headers=headers,
                                 timeout=timeout) as resp:
                if resp.status != 200:
                    return None, f"接口返回 {resp.status}"
                raw = await resp.text()
        except Exception as e:
            logger.warning(f"[music] 搜索失败（{base}）: {e}")
            return None, "搜索请求失败"
        try:
            data = json.loads(raw)
        except Exception:
            return None, "接口返回格式异常"
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return [], ""
        return items, ""

    async def _post_search(self, query: str, source: str, page: int = 1):
        """★ 只有一个接口地址，所以只请求一次（不留多音源 / 备用站）"""
        if not _HAS_AIOHTTP:
            return None, "缺少 aiohttp 依赖"
        return await self._post_search_one(self.base_url, query, source, page)

    async def search(self, query: str, source: str = ""):
        """
        搜索歌曲，返回 (items, err)。
        items 每项：{"index", "name", "artist", "songid", "link", "url", "source"}
        """
        q = _clean_query(query)
        if not q:
            return [], "empty"
        src = (source or self.source or DEFAULT_SOURCE).lower()
        if src not in SOURCES:
            src = DEFAULT_SOURCE
        _ck = (q, src, self.base_url)
        _hit = _search_cache_get(_ck)
        if _hit is not None:
            logger.info(f"[music] 搜索缓存命中「{q}」({src})，跳过网络")
            items, err = _hit, ""
        else:
            items, err = await self._post_search(q, src)
        if err:
            return [], err

        if not items:
            for _alt in self._fallback_queries(q):
                logger.info(f"[music] 「{q}」搜不到，改用「{_alt}」重试")
                _it2, _e2 = await self._post_search(_alt, src)
                if _e2:
                    continue
                if _it2:
                    items = _it2
                    break

        if items and len(items) < self.limit:
            try:
                _seen = {str(x.get("songid", "")) for x in items
                         if isinstance(x, dict)}
                _page = 1
                _maxpage = 5
                while len(items) < self.limit and _page < _maxpage:
                    _page += 1
                    _more, _me = await self._post_search(q, src, page=_page)
                    if _me or not _more:
                        break
                    _added = 0
                    for _x in _more:
                        if not isinstance(_x, dict):
                            continue
                        _sid = str(_x.get("songid", ""))
                        if _sid and _sid in _seen:
                            continue
                        if _sid:
                            _seen.add(_sid)
                        items.append(_x)
                        _added += 1
                    if not _added:
                        break
                logger.info(
                    f"[music] 「{q}」多页合并后共 {len(items)} 条"
                    f"（目标 {self.limit}，取了 {_page} 页）")
            except Exception:
                logger.debug("[music] 多页合并失败", exc_info=True)

        if items:
            _search_cache_put(_ck, items)
        out = []
        for it in (items or [])[:self.limit]:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name", "") or "").strip()
            if not name:
                continue
            row = {
                "name": name,
                "artist": str(it.get("artist", "") or "").strip(),
                "songid": str(it.get("songid", "") or ""),
                "link": self._abs(str(it.get("link", "") or "")),
                "url": self._abs(str(it.get("url", "") or "")),
                "source": src,
            }
            out.append(row)
        return out, ""

    def empty_hint(self, query: str = "", src_label: str = "") -> str:
        """
        「搜到 0 条」时给用户的提示（含英文歌名的改写建议）。

        ★ 接口对带空格的英文查询支持很差，所以要主动告诉用户该怎么写，
          而不是只说「没搜到」。
        """
        q = str(query or "").strip()
        _pre = f"{src_label}" if src_label else ""
        _cmd = f"{_pre}{self.search_name}"
        lines = [f"{title_bar('没搜到')}"]
        lines.append(f"🔍 关键词：{q}" if q else "🔍 没搜到相关歌曲")
        alts = self._fallback_queries(q)
        if alts:
            lines.append("")
            lines.append("💡 这个接口对**带空格的英文**支持不好，试试：")
            for a in alts[:2]:
                lines.append(f"　 「{_cmd} {a}」")
        else:
            lines.append("")
            lines.append("💡 换个关键词，或只发其中一两个词试试")
        return "\n".join(lines)

    @staticmethod
    def _fallback_queries(q: str) -> list:
        """
        0 条时的「换个写法」候选（按优先级）。

        ★ 只对**英文**查询做，中文歌名带空格本来就正常。
        """
        s = str(q or "").strip()
        if not s or " " not in s:
            return []
        if any("\u4e00" <= ch <= "\u9fff" for ch in s):
            return []
        out = []
        a = re.sub(r"\s+", "", s)
        if a and a.lower() != s.lower():
            out.append(a)
        b = s.split()[0]
        if b and b.lower() not in (s.lower(), a.lower()):
            out.append(b)
        return out

    def _abs(self, u: str) -> str:
        """相对路径按接口地址补全"""
        if not u:
            return ""
        if u.startswith("http://") or u.startswith("https://"):
            return u
        return str(self.base_url or "").rstrip("/") + "/" + u.lstrip("/")

    # ---------- 文本（卡片构造失败时的兜底） ----------

    def build_search_text(self, query: str, items: list) -> str:
        lines = [f"{title_bar(f'点歌搜索：{_clean_query(query)}')}", ""]
        if not items:
            lines.append("😥 没有搜到相关歌曲，换个关键词试试")
            lines.append("")
            lines.append(f"💡 用「{self.search_name} 歌名」重新搜索")
            return "\n".join(lines)
        for i, it in enumerate(items, start=1):
            artist = f" — {it['artist']}" if it.get("artist") else ""
            lines.append(f"{i}. {it['name']}{artist}")
        lines.append("")
        lines.append(f"💡 用「{self.pick_name} 序号」选择要点的歌")
        lines.append(f"💡 共 {len(items)} 首（最多显示 {self.limit} 首）")
        lines.append(
            f"💡 一次搜索只能选一首，想再选请重新「{self.search_name} 歌名」"
        )
        return "\n".join(lines)

    def build_pick_text(self, it: dict, page_url: str = "",
                        direct_url: str = "") -> str:
        """
        选歌后的推送文本（卡片构造不出来时的兜底）：
            歌名 - 歌手 / 来源 / 音乐地址
        """
        name = str(it.get("name", "") or "")
        artist = str(it.get("artist", "") or "")
        src_label = source_label(str(it.get("source", "") or ""))

        parts = [f"{title_bar('点歌')}", ""]
        parts.append(f"🎵 {name} - {artist}" if artist else f"🎵 {name}")
        if src_label:
            parts.append(f"📻 来源：{src_label}")
        parts.append("")

        url_audio = direct_url or str(it.get("url", "") or "")
        if url_audio:
            parts.append(f"⬇️ 音乐地址：{url_audio}")
            parts.append("　（该站签名只对当秒有效，链接基本转眼就失效）")
        return "\n".join(parts)
