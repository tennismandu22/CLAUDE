import httpx
import pytest

from clusterwatch.notify.telegram import TelegramError, send_text


def test_send_splits_and_posts():
    sent = []

    def handler(req):
        sent.append(req)
        return httpx.Response(200, json={"ok": True})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    n = send_text("ligne\n" * 1000, token="T", chat_id="42", http=http)
    assert n == len(sent) == 2
    assert sent[0].url.path == "/botT/sendMessage"


def test_error_does_not_leak_token():
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"ok": False})))
    with pytest.raises(TelegramError) as exc:
        send_text("x", token="SECRET", chat_id="1", http=http)
    assert "SECRET" not in str(exc.value)


def test_missing_env(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(TelegramError):
        send_text("x")
