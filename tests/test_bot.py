"""Bot Telegram contre un faux Telegram et une fausse API Taostats."""

import pytest
import yaml

from clusterwatch import service
from clusterwatch.bot import Bot
from clusterwatch.notify.telegram import TelegramError
from tests.fixtures.factories import CAND, EXT, W1, W2
from tests.test_pipeline import FakeTaostats, client_for

ME = "111"


class FakeTelegram:
    def __init__(self, updates=()):
        self.sent: list[tuple[str, str]] = []
        self.updates = list(updates)

    def send(self, chat_id, text):
        self.sent.append((str(chat_id), text))
        return 1

    def get_updates(self, offset, timeout=30):
        out = [u for u in self.updates if offset is None or u["update_id"] >= offset]
        self.updates = []
        return out

    def texts(self):
        return "\n---\n".join(t for _, t in self.sent)


@pytest.fixture
def env(tmp_path):
    cdir = tmp_path / "config"
    cdir.mkdir()
    (cdir / "settings.yaml").write_text(yaml.safe_dump({"api": {"min_interval_s": 0}}))
    ws = service.Workspace(cdir, tmp_path / "state", tmp_path / "reports")
    fake = FakeTaostats()
    fake.add_transfer(EXT, W1, 10, block=10)
    fake.add_trade(W1, "DELEGATE", 3, 1, block=20)
    tg = FakeTelegram()
    t = {"now": 0.0}
    bot = Bot(tg, ws, [ME], client_factory=lambda: client_for(fake), run_every_minutes=60, clock=lambda: t["now"])
    return bot, tg, ws, fake, t


def test_unauthorized_chat_is_ignored(env):
    bot, tg, *_ = env
    bot.handle("999", "/groupes")
    bot.handle("999", f"/add {W1} perso")
    assert tg.sent == []


def test_help_and_unknown(env):
    bot, tg, *_ = env
    bot.handle(ME, "/aide")
    bot.handle(ME, "/nimportequoi")
    assert "/add <adresse> <groupe>" in tg.sent[0][1]
    assert "Commande inconnue" in tg.sent[1][1]


def test_add_creates_group_runs_it_and_replies(env):
    bot, tg, ws, *_ = env
    bot.handle(ME, f"/add {W1} perso")
    out = tg.texts()
    assert "groupe 'perso' créé" in out
    assert "clusterwatch [perso]" in out and "Premier passage" in out
    assert (ws.reports_dir / "perso").exists()

    bot.handle(ME, "/groupes")
    assert "[perso]" in tg.sent[-1][1]


def test_add_usage_and_bad_rank(env):
    bot, tg, *_ = env
    bot.handle(ME, f"/add {W1}")
    bot.handle(ME, f"/add {W1} perso rang9")
    bot.handle(ME, "/add pasuneadresse perso")
    assert "Usage" in tg.sent[0][1] and "Rang inconnu" in tg.sent[1][1] and "Usage" in tg.sent[2][1]


def test_bare_address_gives_info(env):
    bot, tg, *_ = env
    bot.handle(ME, W1)
    out = tg.texts()
    assert f"Adresse {W1}" in out and "Valeur : 7,00 TAO" in out
    assert f"/add {W1} <groupe>" in out


def test_remove_and_errors_are_reported(env):
    bot, tg, *_ = env
    bot.handle(ME, f"/add {W1} perso")
    bot.handle(ME, f"/add {W2} perso rang2")
    bot.handle(ME, f"/remove {W2} perso")
    assert "retirée" in tg.sent[-1][1]
    bot.handle(ME, f"/remove {CAND} perso")
    assert tg.sent[-1][1].startswith("Erreur :")


def test_scheduled_run_sends_only_when_something_new(env):
    bot, tg, ws, fake, t = env
    service.add(ws, "perso", W1)
    bot.tick(poll_timeout=0)  # premier passage planifié : référence, rien envoyé
    assert tg.sent == []
    t["now"] = 30 * 60
    bot.tick(poll_timeout=0)  # pas encore l'heure
    fake.head = 2000
    fake.add_trade(W1, "DELEGATE", 12, 7, block=1500)
    t["now"] = 61 * 60
    bot.tick(poll_timeout=0)
    assert len(tg.sent) == 1 and "Trades ≥ 5 TAO : 1" in tg.sent[0][1]


def test_updates_are_dispatched_and_offset_advances(env):
    bot, tg, *_ = env
    bot.next_run = None
    tg.updates = [{"update_id": 5, "message": {"chat": {"id": int(ME)}, "text": "/aide"}}]
    bot.tick(poll_timeout=0)
    assert bot.offset == 6 and len(tg.sent) == 1


def test_requires_allowed_chat():
    with pytest.raises(TelegramError):
        Bot(FakeTelegram(), service.Workspace(), [], client_factory=lambda: None)
