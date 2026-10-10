"""
群名片（1.7.3）

从 OneBot 接口取群成员资料，交给本地表情包服务渲染成名片图：

    左上角  群昵称
            专属称号
            群聊等级
    右上角  圆形头像
    右下角  入群时间

字段来源（QQ / OneBot `get_group_member_info`）：
  card        群昵称（优先），没有则用 nickname
  title       专属称号（群头衔），为空显示「无」
  role        owner / admin / member -> 群主 / 管理员 / 群成员
  level       QQ 等级（部分协议端不返回，没有就用 role 兜底）
  join_time   入群时间（Unix 秒），格式化成日期
  sex         性别（male / female / unknown），部分协议端不返回
"""
import datetime
import os
import tempfile

try:
    from astrbot.api import logger
except Exception:  # pragma: no cover
    import logging
    logger = logging.getLogger("group_card")

# role -> 中文
_ROLE_CN = {
    "owner": "群主",
    "admin": "管理员",
    "member": "群成员",
}
# 取不到入群时间时的占位
_NO_TIME = "入群时间：未知"


def _bot_of(event):
    """尽量从事件里取出底层 bot / api 对象（与 husband 模块同样思路）"""
    if event is None:
        return None
    bot = getattr(event, "bot", None)
    if bot is not None:
        return bot
    for attr in ("api", "client", "onebot"):
        alt = getattr(event, attr, None)
        if alt is not None:
            return alt
    return getattr(event, "message_obj", None) and getattr(
        event.message_obj, "bot", None) or None


async def _call_action(event, action: str, **params):
    """调一次 OneBot action，失败返回 None"""
    bot = _bot_of(event)
    if bot is None:
        return None
    # 兼容多种封装：bot.call_action / bot.api.call_action
    for target in (bot, getattr(bot, "api", None)):
        if target is None:
            continue
        fn = getattr(target, "call_action", None)
        if callable(fn):
            try:
                return await fn(action, **params)
            except Exception:
                logger.debug(f"[group_card] {action} 调用失败(target={type(target).__name__})")
                continue
    # 某些适配器直接暴露同名方法
    fn = getattr(bot, action, None)
    if callable(fn):
        try:
            return await fn(**params)
        except Exception:
            logger.debug(f"[group_card] {action} 直调失败")
    return None


async def fetch_member_info(event, group_id: str, user_id: str) -> dict:
    """
    取群成员资料。返回 dict（失败时字段为空/默认）。

    优先 `get_group_member_info`（只取一个人，省流量）；
    失败时退回 `get_group_member_list` 里找。
    """
    out = {
        "user_id": str(user_id or ""),
        "name": "",
        "title": "",
        "role": "",
        "level": "",
        "join_time": 0,
        "sex": "",
    }
    if not group_id or not user_id:
        return out

    api_gid = int(group_id) if str(group_id).isdigit() else group_id
    api_uid = int(user_id) if str(user_id).isdigit() else user_id

    info = await _call_action(event, "get_group_member_info",
                             group_id=api_gid, user_id=api_uid)
    if not isinstance(info, dict):
        # 退回成员列表
        members = await _call_action(event, "get_group_member_list",
                                     group_id=api_gid)
        if isinstance(members, list):
            for m in members:
                if not isinstance(m, dict):
                    continue
                if str(m.get("user_id", "")) == str(user_id):
                    info = m
                    break

    if isinstance(info, dict):
        out["name"] = str(
            info.get("card") or info.get("nickname") or ""
        ).strip()
        out["title"] = str(info.get("title") or "").strip()
        out["role"] = str(info.get("role") or "").strip()
        lv = info.get("level")
        out["level"] = str(lv).strip() if lv not in (None, "") else ""
        try:
            out["join_time"] = int(info.get("join_time") or 0)
        except Exception:
            out["join_time"] = 0
        out["sex"] = _norm_sex(info.get("sex") or info.get("gender"))
    return out


def _norm_sex(raw) -> str:
    """
    把各种写法的性别统一成 male / female / unknown。

    OneBot 标准 sex 字段是 male / female / unknown；
    也有协议端返回 男/女 或数字。
    ★ 数字只认明确约定：1=男、2=女。
      （0 在部分协议端表示「未知」，不敢猜，一律 unknown）
    """
    v = str(raw if raw is not None else "").strip().lower()
    if v in ("male", "m", "男", "1"):
        return "male"
    if v in ("female", "f", "女", "2"):
        return "female"
    return "unknown"


async def fetch_qq_gender(event, user_id: str, group_id: str = "") -> str:
    """
    查 QQ 资料里的性别。

    ★ NapCat 的 `get_stranger_info` **不返回 sex 字段**（一直是 unknown），
      而 `get_group_member_info` 按 OneBot v11 标准是**返回 sex** 的。
      所以先查群成员资料，再退回陌生人资料。

    （早先写反了顺序，导致所有人都是 ?）

    都拿不到 -> "unknown"（名片上显示 ?）。
    """
    uid = str(user_id or "").strip()
    if not uid.isdigit():
        return "unknown"
    api_uid = int(uid)

    def _pick(info):
        if not isinstance(info, dict):
            return "unknown"
        # 有些实现用 gender，有些用 sex
        for key in ("sex", "gender"):
            v = info.get(key)
            s = _norm_sex(v)
            if s != "unknown":
                return s
        return "unknown"

    # ① 群成员资料（最可靠，OneBot v11 标准字段）
    gid = str(group_id or "").strip()
    if gid:
        api_gid = int(gid) if gid.isdigit() else gid
        try:
            info = await _call_action(event, "get_group_member_info",
                                      group_id=api_gid, user_id=api_uid,
                                      no_cache=False)
            logger.info(f"[group_card] 群成员性别({uid}) -> {info}")
            s = _pick(info)
            if s != "unknown":
                return s
        except Exception:
            logger.exception("[group_card] 群成员性别获取异常")

    # ② 不传群号时，或群成员资料没给 -> 陌生人资料
    try:
        info = await _call_action(event, "get_stranger_info",
                                  user_id=api_uid, no_cache=False)
        logger.info(f"[group_card] 陌生人性别({uid}) -> {info}")
        s = _pick(info)
        if s != "unknown":
            return s
    except Exception:
        logger.exception("[group_card] 陌生人性别获取异常")
    return "unknown"


def format_gender(raw) -> str:
    """性别 -> 名片上显示的符号"""
    v = _norm_sex(raw)
    return {"male": "♂", "female": "♀"}.get(v, "?")


def format_join_time(ts) -> str:
    """Unix 秒 -> 「入群时间：2024-03-15」；无效则给占位"""
    try:
        ts = int(ts or 0)
    except Exception:
        ts = 0
    if ts <= 0:
        return _NO_TIME
    try:
        dt = datetime.datetime.fromtimestamp(ts)
        return f"入群时间：{dt.strftime('%Y-%m-%d')}"
    except Exception:
        return _NO_TIME


def format_level(info: dict) -> str:
    """
    群聊等级文案。

    优先 QQ 等级（level），没有就用身份（群主/管理员/群成员）兜底，
    两个都没有就显示「群成员」。
    """
    lv = str((info or {}).get("level") or "").strip()
    if lv:
        # 纯数字补个 Lv. 前缀，好看一点
        if lv.isdigit():
            return f"Lv.{lv}"
        return lv
    role = str((info or {}).get("role") or "").strip().lower()
    return _ROLE_CN.get(role, "群成员")


def format_role(info: dict) -> str:
    """
    群成员身份（传给资料卡上色）。

    返回 onebot 约定的取值：owner / admin / member；
    另有超管用 super（部分协议端会返回）。
    """
    role = str((info or {}).get("role") or "").strip().lower()
    if role in ("owner", "admin", "superadmin", "super"):
        return "super" if role in ("superadmin", "super") else role
    return "member"


def format_title(info: dict) -> str:
    """专属称号：没有就显示「无」"""
    t = str((info or {}).get("title") or "").strip()
    return t or "无"


async def download_avatar_file(event, user_id: str) -> str:
    """
    取头像并落盘，返回本地路径（失败返回空串）。

    插件把本地文件转 base64 传给渲染服务，这样渲染端不必访问 QQ。
    """
    import aiohttp
    uid = str(user_id or "").strip()
    if not uid.isdigit():
        return ""
    url = f"https://q1.qlogo.cn/g?b=qq&nk={uid}&s=640"
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url) as resp:
                if resp.status != 200:
                    logger.warning(f"[group_card] 头像 HTTP {resp.status}")
                    return ""
                blob = await resp.read()
        if not blob:
            return ""
        d = os.path.join(tempfile.gettempdir(), "astrbot_meme_card")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{uid}.png")
        with open(path, "wb") as f:
            f.write(blob)
        return path
    except Exception:
        logger.exception("[group_card] 头像下载失败")
        return ""
