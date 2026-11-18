from __future__ import annotations

import asyncio
import logging
import random

import aiohttp
from bilibili_api import Credential, user
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
