"""
QQ音乐榜单随机取歌（新歌榜 / 热歌榜）。

数据源：QQ音乐官网榜单接口（无需签名、无需登录）
  https://c.y.qq.com/v8/fcg-bin/fcg_v8_toplist_opt.fcg?topid=<id>&format=json
  topid 26 = 热歌榜，27 = 新歌榜

用法：拿到随机一首的「歌名 + 歌手」，再交给音乐模块正常搜歌播放
     （这样签名 / 封面 / 音源回退这些逻辑全都复用现成的）。
"""
import json
import random
import time

import aiohttp

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0.0.0 Safari/537.36")

TOPLIST_URL = ("https://c.y.qq.com/v8/fcg-bin/fcg_v8_toplist_opt.fcg"
               "?topid={topid}&format=json&inCharset=utf8"
               "&outCharset=utf-8&notice=0&platform=yqq&needNewCode=0")

TOPLISTS = {
    "new": {"topid": 27, "label": "新歌榜"},
    "hot": {"topid": 26, "label": "热歌榜"},
}

_CACHE = {}
_CACHE_TTL = 600


def _pick_song(raw):
    """从一条榜单记录里挖出 (歌名, 歌手)"""
    it = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    name = str(it.get("songname") or it.get("name") or "").strip()
    if not name:
        return None
    singers = it.get("singer") or []
    names = []
    if isinstance(singers, list):
        for s in singers:
            if isinstance(s, dict):
                nm = str(s.get("name") or "").strip()
                if nm:
                    names.append(nm)
            elif s:
                names.append(str(s).strip())
    artist = "、".join(names)
    mid = str(it.get("songmid") or it.get("mid") or "").strip()
    return {"name": name, "artist": artist, "mid": mid}


def parse_toplist(data):
    """把榜单接口返回的 JSON 解析成歌曲列表"""
    songs = []
    for raw in (data.get("songlist") or []):
        if not isinstance(raw, dict):
            continue
        s = _pick_song(raw)
        if s:
            songs.append(s)
    return songs


async def fetch_toplist(kind, timeout=12, logger=None):
    """取榜单全部歌曲；带 10 分钟内存缓存"""
    meta = TOPLISTS.get(kind)
    if not meta:
        return []
    now = time.time()
    hit = _CACHE.get(kind)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    url = TOPLIST_URL.format(topid=meta["topid"])
    try:
        _to = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=_to,
                                         headers={"User-Agent": UA,
                                                  "Referer": "https://y.qq.com/"}) as sess:
            async with sess.get(url) as resp:
                txt = await resp.text(errors="ignore")
        data = json.loads(txt)
        songs = parse_toplist(data)
        if songs:
            _CACHE[kind] = (now, songs)
        return songs
    except Exception:
        if logger:
            logger.warning("[music_toplist] 取 %s 失败", meta["label"],
                           exc_info=True)
        return []


async def random_song(kind, timeout=12, logger=None):
    """随机一首 -> {"name","artist","mid","label"}；失败返回 None"""
    meta = TOPLISTS.get(kind)
    if not meta:
        return None
    songs = await fetch_toplist(kind, timeout=timeout, logger=logger)
    if not songs:
        return None
    s = dict(random.choice(songs))
    s["label"] = meta["label"]
    return s
