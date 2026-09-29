"""Client HTTP Taostats : authentification, débit, reprise sur 429, pagination."""

from __future__ import annotations

import logging
import os
import time
from typing import Callable, Iterator

import httpx

from .cache import DiskCache

log = logging.getLogger(__name__)

API_KEY_ENV = "TAOSTATS_API_KEY"


class TaostatsError(RuntimeError):
    pass


class TaostatsClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.taostats.io",
        min_interval_s: float = 12.0,
        max_retries: int = 6,
        page_limit: int = 200,
        cache: DiskCache | None = None,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        api_key = api_key or os.environ.get(API_KEY_ENV)
        if not api_key:
            raise TaostatsError(f"variable d'environnement {API_KEY_ENV} non définie")
        self.base_url = base_url.rstrip("/")
        self.min_interval_s = min_interval_s
        self.max_retries = max_retries
        self.page_limit = page_limit
        self.cache = cache
        self._sleep = sleep
        self._clock = clock
        self._last_call: float | None = None
        self.calls = 0
        self.cache_hits = 0
        self._http = http or httpx.Client(
            timeout=30.0,
            headers={"Authorization": api_key, "Accept": "application/json"},
        )

    def _throttle(self) -> None:
        if self._last_call is not None:
            wait = self.min_interval_s - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()

    def get(self, path: str, params: dict | None = None, cacheable: bool = False) -> dict:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        if cacheable and self.cache:
            cached = self.cache.get(path, params)
            if cached is not None:
                self.cache_hits += 1
                return cached

        url = f"{self.base_url}{path}"
        for attempt in range(self.max_retries + 1):
            self._throttle()
            self.calls += 1
            try:
                resp = self._http.get(url, params=params)
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise TaostatsError(f"erreur réseau sur {path} : {exc}") from exc
                self._sleep(self._backoff(attempt))
                continue

            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= self.max_retries:
                    raise TaostatsError(f"{path} : HTTP {resp.status_code} après {attempt + 1} tentatives")
                delay = self._retry_after(resp) or self._backoff(attempt)
                log.warning("HTTP %s sur %s, nouvel essai dans %.0f s", resp.status_code, path, delay)
                self._sleep(delay)
                continue
            if resp.status_code in (401, 403):
                raise TaostatsError(f"{path} : accès refusé (HTTP {resp.status_code}), vérifier {API_KEY_ENV}")
            if resp.status_code >= 400:
                raise TaostatsError(f"{path} : HTTP {resp.status_code} {resp.text[:200]}")

            payload = resp.json()
            if cacheable and self.cache:
                self.cache.set(path, params, payload)
            return payload
        raise TaostatsError(f"{path} : échec")  # pragma: no cover

    def _backoff(self, attempt: int) -> float:
        return min(max(self.min_interval_s, 2.0) * (2**attempt), 300.0)

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float | None:
        value = resp.headers.get("Retry-After")
        try:
            return float(value) if value is not None else None
        except ValueError:
            return None

    def paginate(
        self,
        path: str,
        params: dict | None = None,
        cacheable: bool = False,
        max_pages: int | None = None,
    ) -> Iterator[dict]:
        """Itère sur les éléments de `data` de toutes les pages."""
        page = 1
        while True:
            p = dict(params or {}, page=page, limit=self.page_limit)
            payload = self.get(path, p, cacheable=cacheable)
            items = payload.get("data") or []
            yield from items
            pag = payload.get("pagination") or {}
            next_page = pag.get("next_page")
            total_pages = pag.get("total_pages")
            if not items:
                return
            if next_page is None and (total_pages is None or page >= int(total_pages)):
                return
            page = int(next_page) if next_page else page + 1
            if max_pages is not None and page > max_pages:
                log.warning("%s : limite de %d pages atteinte", path, max_pages)
                return
