"""Envoi du texte compact via l'API Bot Telegram.

Variables d'environnement : TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID.
"""

from __future__ import annotations

import os

import httpx

from ..report.telegram_text import split_message

TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
CHAT_ENV = "TELEGRAM_CHAT_ID"


class TelegramError(RuntimeError):
    pass


def send_text(text: str, token: str | None = None, chat_id: str | None = None,
              http: httpx.Client | None = None) -> int:
    """Envoie `text` (découpé si besoin). Retourne le nombre de messages envoyés."""
    token = token or os.environ.get(TOKEN_ENV)
    chat_id = chat_id or os.environ.get(CHAT_ENV)
    if not token or not chat_id:
        raise TelegramError(f"{TOKEN_ENV} et {CHAT_ENV} doivent être définies")
    if not text.strip():
        return 0
    client = http or httpx.Client(timeout=30.0)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    parts = split_message(text)
    for part in parts:
        resp = client.post(url, json={"chat_id": chat_id, "text": part, "disable_web_page_preview": True})
        if resp.status_code != 200 or not resp.json().get("ok"):
            # Ne jamais inclure le token dans le message d'erreur.
            raise TelegramError(f"échec de l'envoi Telegram (HTTP {resp.status_code})")
    return len(parts)
