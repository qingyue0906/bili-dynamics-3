from __future__ import annotations

import asyncio
import logging
import random

import aiohttp
from bilibili_api import Credential, dynamic as bili_dynamic, user
from bilibili_api.exceptions import NetworkException, ResponseCodeException

from .config import Config

logger = logging.getLogger(__name__)

RISK_CODES = {-352, -412, -429, -509, -799}


class BiliClient:
    def __init__(self, config: Config) -> None:
        config.require_cookie()
        self.config = config
        self.credential = Credential(
            sessdata=config.sessdata,
            bili_jct=config.bili_jct,
            buvid3=config.buvid3,
        )

    async def _sleep_backoff(self, attempt: int, risk: bool) -> None:
        base = 60.0 if risk else 5.0
        wait = min(300.0, base * (2 ** attempt))
        wait += random.uniform(0, min(10.0, wait * 0.2))
        logger.warning("API 限流/异常，等待 %.0f 秒后重试（第 %d 次）", wait, attempt + 1)
        await asyncio.sleep(wait)

    async def fetch_page(self, uid: int, offset: str = "") -> dict:
        retries = self.config.general.retries
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                u = user.User(int(uid), credential=self.credential)
                return await u.get_dynamics_new(offset)
            except ResponseCodeException as exc:
                last_error = exc
                if exc.code == -101:
                    raise RuntimeError("Cookie 未登录或已失效，请更新 .env 中的 BILI_SESSDATA") from exc
                if attempt >= retries:
                    break
                await self._sleep_backoff(attempt, risk=exc.code in RISK_CODES)
            except NetworkException as exc:
                last_error = exc
                if attempt >= retries:
                    break
                await self._sleep_backoff(attempt, risk=False)
            except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as exc:
                last_error = exc
                if attempt >= retries:
                    break
                await self._sleep_backoff(attempt, risk=False)
            except Exception as exc:  # bilibili-api wraps some errors
                last_error = exc
                if attempt >= retries:
                    break
                await self._sleep_backoff(attempt, risk=False)
        assert last_error is not None
        raise last_error

    async def fetch_dynamic(self, dynamic_id: int) -> dict:
        retries = self.config.general.retries
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                d = bili_dynamic.Dynamic(int(dynamic_id), credential=self.credential)
                data = await d.get_info()
                return data if isinstance(data, dict) else {}
            except ResponseCodeException as exc:
                last_error = exc
                if attempt >= retries:
                    break
                await self._sleep_backoff(attempt, risk=exc.code in RISK_CODES)
            except NetworkException as exc:
                last_error = exc
                if attempt >= retries:
                    break
                await self._sleep_backoff(attempt, risk=False)
            except Exception as exc:
                last_error = exc
                if attempt >= retries:
                    break
                await self._sleep_backoff(attempt, risk=False)
        assert last_error is not None
        raise last_error

    async def fetch_user_info(self, uid: int) -> dict:
        retries = min(2, self.config.general.retries)
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                u = user.User(int(uid), credential=self.credential)
                data = await u.get_user_info()
                return data if isinstance(data, dict) else {}
            except Exception as exc:
                last_error = exc
                if attempt >= retries:
                    break
                await self._sleep_backoff(attempt, risk=False)
        if last_error:
            logger.warning("获取用户 %s 信息失败：%s", uid, last_error)
        return {}


async def check_credential(sessdata: str, bili_jct: str, buvid3: str) -> tuple[bool, str]:
    cookies = {
        "SESSDATA": sessdata or "",
        "bili_jct": bili_jct or "",
        "buvid3": buvid3 or "",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
        ),
        "Referer": "https://www.bilibili.com/",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(
                "https://api.bilibili.com/x/web-interface/nav",
                cookies=cookies,
            ) as response:
                data = await response.json(content_type=None)
    except Exception as exc:
        return False, f"网络请求失败：{exc}"
    code = data.get("code") if isinstance(data, dict) else None
    payload = data.get("data") if isinstance(data, dict) else None
    if code == 0 and isinstance(payload, dict) and payload.get("isLogin"):
        name = payload.get("uname") or ""
        return True, f"登录有效：{name}" if name else "登录有效"
    if code == -101:
        return False, "Cookie 未登录或已失效"
    message = data.get("message") if isinstance(data, dict) else ""
    return False, f"接口返回 {code}：{message}"
