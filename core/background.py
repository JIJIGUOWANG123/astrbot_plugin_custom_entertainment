import asyncio
import datetime
from astrbot.api import logger

_mc_checker_lock = None
_chime_checker_lock = None
_status_push_lock = None
_daily_news_lock = None

_BG_TASKS = {}

async def _cancel_bg_task(name: str, why: str = "") -> int:
    """取消指定名字的旧后台任务，返回取消个数。"""
    tk = _BG_TASKS.pop(name, None)
    if tk is None:
        return 0
    if tk.done():
        return 0
    tk.cancel()
    logger.info(f"[apix-meme] 取消旧的「{name}」后台任务{why}")
    try:
        await asyncio.wait({tk}, timeout=3)
    except Exception:
        pass
    return 1

def _register_bg_task(name: str, task) -> None:
    """登记新任务（同名旧任务应已先被取消）。"""
    _BG_TASKS[name] = task

def bg_task_names() -> list:
    """当前登记的后台任务名（调试用）。"""
    return list(_BG_TASKS.keys())

def _get_mc_lock():
    global _mc_checker_lock
    if _mc_checker_lock is None:
        _mc_checker_lock = asyncio.Lock()
    return _mc_checker_lock

def _get_chime_lock():
    global _chime_checker_lock
    if _chime_checker_lock is None:
        _chime_checker_lock = asyncio.Lock()
    return _chime_checker_lock

def _get_status_push_lock():
    global _status_push_lock
    if _status_push_lock is None:
        _status_push_lock = asyncio.Lock()
    return _status_push_lock

def _get_daily_news_lock():
    global _daily_news_lock
    if _daily_news_lock is None:
        _daily_news_lock = asyncio.Lock()
    return _daily_news_lock

async def mc_auto_checker(plugin):
    await asyncio.sleep(20)
    logger.info("[minecraft] 自动检测循环启动")
    while True:
        try:
            if plugin.minecraft_module is None:
                await asyncio.sleep(60)
                continue
            if not plugin.minecraft_module.enable:
                await asyncio.sleep(60)
                continue
            interval = plugin.minecraft_module.interval
            if interval <= 0:
                await asyncio.sleep(60)
                continue
            logger.info("[minecraft] 本轮检测开始")
            result = await plugin.minecraft_module.check_all(force=True, include_bedrock=False)
            try:
                global_msgs = plugin.minecraft_module.check_new_versions(result)
                for m in global_msgs:
                    logger.info(f"[minecraft] 检测到新版本：{m}")
            except Exception:
                logger.exception("[minecraft] 全局通知异常")
            try:
                for gid in list(plugin.minecraft_module.auto_groups):
                    try:
                        msgs = plugin.minecraft_module.check_new_versions_for_group(gid, result)
                        for m in msgs:
                            umo = plugin.minecraft_module.get_umo(gid)
                            _t, _imgs = await plugin.render_group_text(
                                str(gid), f"📢 {m}")
                            pushed = await plugin._push_mc_notification(
                                gid, _t, umo=umo, images=_imgs
                            )
                            if not pushed:
                                logger.info(f"[minecraft] 群 {gid} 推送失败")
                    except Exception:
                        logger.exception(f"[minecraft] 群 {gid} 通知失败")
            except Exception:
                logger.exception("[minecraft] 群通知循环异常")
            await asyncio.sleep(max(60, interval))
        except asyncio.CancelledError:
            logger.info("[minecraft] 自动检测任务被取消")
            raise
        except Exception:
            logger.exception("[minecraft] 自动检测循环异常")
            await asyncio.sleep(300)

async def hourly_chime_checker(plugin):
    await asyncio.sleep(30)
    logger.info("[hourly_chime] 整点报时循环启动")
    while True:
        try:
            if plugin.hourly_chime_module is None or not plugin.hourly_chime_module.enable:
                await asyncio.sleep(60)
                continue
            now = datetime.datetime.now()
            if now.second > 55:
                await asyncio.sleep(5)
                continue
            hour = now.hour
            
            all_gids = plugin.hourly_chime_module.get_all_group_ids()
            for gid in all_gids:
                if not plugin.hourly_chime_module.check_and_mark(gid, hour):
                    continue
                
                text = await plugin.hourly_chime_module.build_text_async(
                    gid, hour)
                umo = plugin.hourly_chime_module.get_umo(gid)
                images = []
                try:
                    text, images = await plugin.render_group_text(
                        gid, text,
                        max_w=getattr(plugin.hourly_chime_module,
                                      "card_max_w", 0) or 0)
                except Exception:
                    logger.exception("[hourly_chime] 输出模式渲染失败，回落纯文本")
                    images = []
                ok = await plugin._push_mc_notification(gid, text, umo=umo,
                                                        images=images)
                if ok:
                    logger.info(f"[hourly_chime] 群 {gid} {hour:02d} 点报时已推送")
                else:
                    logger.warning(
                        f"[hourly_chime] 群 {gid} {hour:02d} 点报时推送失败，"
                        f"可能尚未记录 UMO，等本群下次发言后自动记录。"
                        f"（已标记为已推送，本小时内不再重复尝试）"
                    )
            await asyncio.sleep(max(10, plugin.hourly_chime_module.interval))
        except asyncio.CancelledError:
            logger.info("[hourly_chime] 整点报时任务被取消")
            raise
        except Exception:
            logger.exception("[hourly_chime] 整点报时循环异常")
            await asyncio.sleep(120)

async def daily_status_push_checker(plugin):
    """
    定时状态推送循环（按分钟间隔）。
    每分钟检查一次：某个群距离上次推送已满一个间隔，就推一次机器人运行状态。
    推送群与间隔由后台配置决定；留空时沿用群里指令设置的群（原有行为）。
    """
    await asyncio.sleep(45)
    logger.info("[status_push] 定时状态推送循环启动")
    while True:
        try:
            module = plugin.status_push_module
            if module is None or not module.enable:
                await asyncio.sleep(60)
                continue

            for gid in module.get_all_group_ids():
                try:
                    if not plugin._is_group_feature_enable(gid, "status"):
                        continue
                except Exception:
                    pass
                if not module.check_and_mark(gid):
                    continue
                iv = module.get_group_interval(gid)
                ok = await _push_status_to_group(plugin, module, gid)
                if ok:
                    logger.info(f"[status_push] 群 {gid} 运行状态已推送（间隔 {iv} 分钟）")
                else:
                    logger.warning(
                        f"[status_push] 群 {gid} 推送失败，"
                        f"可能尚未记录 UMO，等本群下次发言后自动记录"
                    )

            await asyncio.sleep(60)
        except asyncio.CancelledError:
            logger.info("[status_push] 状态推送任务被取消")
            raise
        except Exception:
            logger.exception("[status_push] 状态推送循环异常")
            await asyncio.sleep(120)

async def _push_status_to_group(plugin, module, gid) -> bool:
    """生成并推送一次电脑状态文本。"""
    try:
        from .system_status import collect_runtime_status
        text = await collect_runtime_status(
            event=None,
            group_id=gid,
            cfg=plugin._read_disk_config() or {},
            plugin=plugin,
        )
    except Exception:
        logger.exception(f"[status_push] 群 {gid} 生成状态文本失败")
        return False
    umo = module.get_umo(gid)
    images = []
    try:
        text, images = await plugin.render_group_text(str(gid), text)
    except Exception:
        logger.exception(f"[status_push] 群 {gid} 输出模式渲染失败，回落纯文本")
        images = []
    return await plugin._push_mc_notification(gid, text, umo=umo,
                                              images=images)

async def daily_news_checker(plugin):
    """
    每日读报循环。
    每分钟检查一次：命中群设置的 HH:MM 且今日未推送过，则推送一次 60s 读报。
    图片接口不可用时回退为文字播报。
    """
    await asyncio.sleep(50)
    logger.info("[daily_news] 每日读报循环启动")
    while True:
        try:
            module = getattr(plugin, "daily_news_module", None)
            if module is None or not module.enable:
                await asyncio.sleep(60)
                continue

            now = datetime.datetime.now()
            if now.second > 50:
                await asyncio.sleep(5)
                continue

            hhmm = now.strftime("%H:%M")
            for gid in module.get_all_group_ids():
                if not module.check_and_mark(gid, hhmm):
                    continue

                umo = module.get_umo(gid)
                pushed = False
                try:
                    from .meme_api import download_media
                    from .daily_news import NEWS_API_IMAGE
                    media_path, kind = await download_media(NEWS_API_IMAGE, expect="image")
                    if kind == "image":
                        from astrbot.api.message_components import Image
                        chain = [Image.fromFileSystem(media_path)]
                        result_obj = _build_chain_result(chain)
                        await plugin.context.send_message(umo, result_obj)
                        pushed = True
                        logger.info(f"[daily_news] 群 {gid} 读报图片已推送")
                except Exception:
                    logger.exception(f"[daily_news] 群 {gid} 读报图片推送失败，尝试文字回退")

                if not pushed:
                    try:
                        text = await _fetch_news_text()
                        if text:
                            try:
                                _t2, _imgs2 = await plugin.render_group_text(
                                    str(gid), text)
                            except Exception:
                                logger.exception(
                                    "[daily_news] 文字兜底渲染失败，回落纯文本")
                                _t2, _imgs2 = text, []
                            if not _t2 and not _imgs2:
                                _t2 = text
                            pushed = await plugin._push_mc_notification(
                                gid, _t2, umo=umo, images=_imgs2)
                    except Exception:
                        logger.exception(f"[daily_news] 群 {gid} 读报文字回退失败")

                if not pushed:
                    logger.warning(
                        f"[daily_news] 群 {gid} {hhmm} 读报推送失败，"
                        f"可能尚未记录 UMO，等本群下次发言后自动记录。"
                        f"（已标记为今日已推送，不再重复尝试）"
                    )

            await asyncio.sleep(max(10, module.interval))
        except asyncio.CancelledError:
            logger.info("[daily_news] 每日读报任务被取消")
            raise
        except Exception:
            logger.exception("[daily_news] 每日读报循环异常")
            await asyncio.sleep(120)

async def _fetch_news_text() -> str:
    """拉取 60s 读报的文字版，作为图片失败时的回退。"""
    try:
        import aiohttp
        from .daily_news import NEWS_API_TEXT
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(NEWS_API_TEXT) as resp:
                if resp.status != 200:
                    return ""
                body = await resp.read()
        text = body.decode("utf-8", "ignore").strip()
        if not text:
            return ""
        return "📰 【今日读报】\n\n" + text
    except Exception:
        logger.exception("[daily_news] 获取读报文字失败")
        return ""

def _build_chain_result(chain):
    """构造带 .chain 的结果对象。"""
    try:
        from astrbot.api.event import MessageEventResult
        r = MessageEventResult()
        r.chain = chain
        return r
    except Exception:
        class _R:
            pass
        r = _R()
        r.chain = chain
        return r

async def ensure_daily_news(plugin):
    if getattr(plugin, "_daily_news_started", False):
        return
    lock = _get_daily_news_lock()
    async with lock:
        if getattr(plugin, "_daily_news_started", False):
            return
        if getattr(plugin, "daily_news_module", None) is None:
            return
        plugin._daily_news_started = True
        try:
            await _cancel_bg_task("daily_news", "（启动新的之前）")
        except Exception:
            pass
        try:
            loop = asyncio.get_event_loop()
            plugin._daily_news_task = loop.create_task(daily_news_checker(plugin))
            _register_bg_task("daily_news", plugin._daily_news_task)
            logger.info("[apix-meme] 每日读报任务已启动")
        except Exception:
            logger.exception("[apix-meme] 启动每日读报任务失败")

async def ensure_mc_checker(plugin):
    if getattr(plugin, "_mc_checker_started", False):
        return
    lock = _get_mc_lock()
    async with lock:
        if getattr(plugin, "_mc_checker_started", False):
            return
        if getattr(plugin, "minecraft_module", None) is None:
            return
        plugin._mc_checker_started = True
        try:
            await _cancel_bg_task("mc_checker", "（启动新的之前）")
        except Exception:
            pass
        try:
            loop = asyncio.get_event_loop()
            plugin._mc_checker_task = loop.create_task(mc_auto_checker(plugin))
            _register_bg_task("mc_checker", plugin._mc_checker_task)
            logger.info("[apix-meme] MC 版本自动检测任务已启动")
        except Exception:
            logger.exception("[apix-meme] 启动MC 版本自动检测任务失败")

async def ensure_status_push(plugin):
    if getattr(plugin, "_status_push_started", False):
        return
    lock = _get_status_push_lock()
    async with lock:
        if getattr(plugin, "_status_push_started", False):
            return
        if getattr(plugin, "status_push_module", None) is None:
            return
        plugin._status_push_started = True
        try:
            await _cancel_bg_task("status_push", "（启动新的之前）")
        except Exception:
            pass
        try:
            loop = asyncio.get_event_loop()
            plugin._status_push_task = loop.create_task(daily_status_push_checker(plugin))
            _register_bg_task("status_push", plugin._status_push_task)
            logger.info("[apix-meme] 每日状态推送任务已启动")
        except Exception:
            logger.exception("[apix-meme] 启动每日状态推送任务失败")

async def ensure_hourly_chime(plugin):
    if getattr(plugin, "_chime_checker_started", False):
        return
    lock = _get_chime_lock()
    async with lock:
        if getattr(plugin, "_chime_checker_started", False):
            return
        if getattr(plugin, "hourly_chime_module", None) is None:
            return
        plugin._chime_checker_started = True
        try:
            await _cancel_bg_task("hourly_chime", "（启动新的之前）")
        except Exception:
            pass
        try:
            loop = asyncio.get_event_loop()
            plugin._hourly_chime_task = loop.create_task(hourly_chime_checker(plugin))
            _register_bg_task("hourly_chime", plugin._hourly_chime_task)
            logger.info("[apix-meme] 整点报时任务已启动")
        except Exception:
            logger.exception("[apix-meme] 启动整点报时任务失败")
