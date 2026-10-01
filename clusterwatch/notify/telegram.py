"""API Bot Telegram : envoi de messages et réception des commandes.

Variables d'environnement : TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID
(TELEGRAM_CHAT_ID peut contenir plusieurs identifiants séparés par des virgules).
"""

from __future__ import annotations

import os

import httpx

from ..report.telegram_text import split_message

TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
CHAT_ENV = "TELEGRAM_CHAT_ID"


class TelegramError(RuntimeError):
    pass


def chat_ids_from_env() -> list[str]:
    return [c.strip() for c in os.environ.get(CHAT_ENV, "").split(",") if c.strip()]


class TelegramApi:
    def __init__(self, token: str | None = None, http: httpx.Client | None = None):
        token = token or os.environ.get(TOKEN_ENV)
        if not token:
            raise TelegramError(f"variable d'environnement {TOKEN_ENV} non définie")
        self._base = f"https://api.telegram.org/bot{token}"
        self._http = http or httpx.Client(timeout=60.0)

    def _call(self, method: str, payload: dict) -> dict:
        try:
            resp = self._http.post(f"{self._base}/{method}", json=payload)
        except httpx.TransportError as exc:
            # Message d'erreur sans l'URL, qui contient le token.
            raise TelegramError(f"Telegram {method} : erreur réseau ({type(exc).__name__})") from None
        if resp.status_code != 200 or not resp.json().get("ok"):
            raise TelegramError(f"Telegram {method} : échec (HTTP {resp.status_code})")
        return resp.json()

    def send(self, chat_id: str, text: str) -> int:
        """Envoie `text` (découpé si besoin). Retourne le nombre de messages envoyés."""
        if not text.strip():
            return 0
        parts = split_message(text)
        for part in parts:
            self._call("sendMessage", {"chat_id": chat_id, "text": part, "disable_web_page_preview": True})
        return len(parts)

    def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict]:
        payload = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", payload).get("result") or []


def send_text(text: str, token: str | None = None, chat_id: str | None = None,
              http: httpx.Client | None = None) -> int:
    """Envoie `text` à `chat_id` (ou à tous les chats de TELEGRAM_CHAT_ID)."""
    chats = [chat_id] if chat_id else chat_ids_from_env()
    if not chats:
        raise TelegramError(f"{TOKEN_ENV} et {CHAT_ENV} doivent être définies")
    api = TelegramApi(token, http)
    return sum(api.send(c, text) for c in chats)
