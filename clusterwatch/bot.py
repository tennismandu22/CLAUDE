"""Bot Telegram : pilotage de clusterwatch à distance.

Le bot répond uniquement aux chats listés dans TELEGRAM_CHAT_ID et lance
aussi les passages automatiquement à intervalle régulier.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from . import service
from .config import RANKS, ConfigError, is_valid_address, list_groups, settings_config
from .notify.telegram import TelegramApi, TelegramError
from .taostats.client import TaostatsClient, TaostatsError

log = logging.getLogger(__name__)

HELP = """Commandes clusterwatch :

<adresse>  infos sur une adresse (solde, positions, trades récents, empreinte)
/info <adresse>  idem
/add <adresse> <groupe> [rang]  suivre une adresse ; le groupe est créé s'il n'existe pas
    rang : rang1 (défaut), rang2, observation, depot
/remove <adresse> <groupe>  ne plus suivre une adresse
/groupes  liste des groupes et des adresses suivies
/run [groupe]  lancer un passage maintenant (tous les groupes par défaut)
/aide  ce message

Avec l'offre Taostats gratuite, une réponse peut prendre plusieurs minutes."""


class Bot:
    def __init__(
        self,
        api: TelegramApi,
        ws: service.Workspace,
        allowed_chats: list[str],
        client_factory: Callable[[], TaostatsClient],
        run_every_minutes: int = 60,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not allowed_chats:
            raise TelegramError("aucun chat autorisé : définir TELEGRAM_CHAT_ID")
        self.api = api
        self.ws = ws
        self.allowed = {str(c) for c in allowed_chats}
        self._client_factory = client_factory
        self._client: TaostatsClient | None = None
        self.run_every_s = max(0, run_every_minutes) * 60
        self._clock = clock
        self.next_run: float | None = clock() if self.run_every_s else None
        self.offset: int | None = None

    @property
    def client(self) -> TaostatsClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def reply(self, chat_id: str, text: str) -> None:
        try:
            self.api.send(chat_id, text)
        except TelegramError as exc:
            log.error("%s", exc)

    # --- commandes -----------------------------------------------------------

    def handle(self, chat_id: str, text: str) -> None:
        chat_id = str(chat_id)
        if chat_id not in self.allowed:
            log.warning("message ignoré d'un chat non autorisé : %s", chat_id)
            return
        words = text.strip().split()
        if not words:
            return
        if is_valid_address(words[0]):
            words = ["/info"] + words
        cmd = words[0].split("@")[0].lower()
        args = words[1:]
        try:
            handler = {
                "/start": self.cmd_help, "/aide": self.cmd_help, "/help": self.cmd_help,
                "/info": self.cmd_info,
                "/add": self.cmd_add, "/ajouter": self.cmd_add,
                "/remove": self.cmd_remove, "/retirer": self.cmd_remove,
                "/groupes": self.cmd_groups, "/groups": self.cmd_groups,
                "/run": self.cmd_run,
            }.get(cmd)
            if handler is None:
                self.reply(chat_id, "Commande inconnue. Envoie /aide pour la liste.")
            else:
                handler(chat_id, args)
        except (ConfigError, TaostatsError, TelegramError) as exc:
            self.reply(chat_id, f"Erreur : {exc}")
        except Exception:
            log.exception("erreur pendant %r", cmd)
            self.reply(chat_id, "Erreur interne (voir les journaux du bot).")

    def cmd_help(self, chat_id: str, args: list[str]) -> None:
        self.reply(chat_id, HELP)

    def cmd_info(self, chat_id: str, args: list[str]) -> None:
        if len(args) != 1 or not is_valid_address(args[0]):
            self.reply(chat_id, "Usage : /info <adresse>")
            return
        self.reply(chat_id, "Recherche en cours…")
        info = service.address_info(self.ws, self.client, args[0])
        self.reply(chat_id, info + f"\n\nPour la suivre : /add {args[0]} <groupe>")

    def cmd_add(self, chat_id: str, args: list[str]) -> None:
        if len(args) not in (2, 3) or not is_valid_address(args[0]):
            groups = ", ".join(list_groups(self.ws.config_dir)) or "aucun"
            self.reply(chat_id, f"Usage : /add <adresse> <groupe> [rang]\nGroupes existants : {groups}")
            return
        address, group = args[0], args[1].lower()
        rank = args[2].lower() if len(args) == 3 else "rang1"
        if rank not in RANKS + ("depot",):
            self.reply(chat_id, f"Rang inconnu : {rank} (rang1, rang2, observation, depot)")
            return
        self.reply(chat_id, service.add(self.ws, group, address, rank)
                   + "\nPassage du groupe en cours, le rapport suit…")
        self._run_and_reply(chat_id, [group], always=True)

    def cmd_remove(self, chat_id: str, args: list[str]) -> None:
        if len(args) != 2:
            self.reply(chat_id, "Usage : /remove <adresse> <groupe>")
            return
        self.reply(chat_id, service.remove(self.ws, args[1].lower(), args[0]))

    def cmd_groups(self, chat_id: str, args: list[str]) -> None:
        self.reply(chat_id, service.groups_summary(self.ws, full_addresses=False))

    def cmd_run(self, chat_id: str, args: list[str]) -> None:
        groups = [g.lower() for g in args] or list_groups(self.ws.config_dir)
        self.reply(chat_id, "Passage en cours…")
        self._run_and_reply(chat_id, groups, always=True)

    def _run_and_reply(self, chat_id: str, groups: list[str], always: bool) -> None:
        for g in groups:
            run = service.run_group(self.ws, g, self.client)
            if always or run.has_anything:
                quiet = not run.has_anything and not run.initial
                self.reply(chat_id, run.text + ("\nRien de nouveau à signaler." if quiet else ""))

    # --- boucle --------------------------------------------------------------

    def scheduled_run(self) -> None:
        log.info("passage planifié")
        for g in list_groups(self.ws.config_dir):
            try:
                run = service.run_group(self.ws, g, self.client)
            except (ConfigError, TaostatsError) as exc:
                log.error("groupe %s : %s", g, exc)
                continue
            if run.has_anything:
                for chat in self.allowed:
                    self.reply(chat, run.text)

    def tick(self, poll_timeout: int = 30) -> None:
        """Un tour de boucle : passage planifié si dû, puis lecture des messages."""
        if self.next_run is not None and self._clock() >= self.next_run:
            self.next_run = self._clock() + self.run_every_s
            self.scheduled_run()
        for update in self.api.get_updates(self.offset, timeout=poll_timeout):
            self.offset = update["update_id"] + 1
            msg = update.get("message") or {}
            text = msg.get("text")
            if text:
                self.handle(str((msg.get("chat") or {}).get("id")), text)

    def run_forever(self) -> None:
        log.info("bot démarré (chats autorisés : %d, passage toutes les %d min)",
                 len(self.allowed), self.run_every_s // 60)
        while True:
            try:
                self.tick()
            except TelegramError as exc:
                log.warning("%s ; nouvel essai dans 10 s", exc)
                time.sleep(10)


def show_chat_ids(api: TelegramApi) -> None:
    """Affiche l'identifiant des chats qui écrivent au bot (aide à la configuration)."""
    print("Envoie un message à ton bot sur Telegram… (Ctrl+C pour arrêter)")
    offset = None
    while True:
        for update in api.get_updates(offset, timeout=30):
            offset = update["update_id"] + 1
            chat = (update.get("message") or {}).get("chat") or {}
            if chat.get("id") is not None:
                name = chat.get("username") or chat.get("first_name") or ""
                print(f"chat_id = {chat['id']}  ({name})")
                api.send(str(chat["id"]), f"Ton chat_id est {chat['id']}. Mets-le dans TELEGRAM_CHAT_ID.")


def start_bot(ws: service.Workspace) -> None:
    from .notify.telegram import chat_ids_from_env

    cfg = settings_config(ws.config_dir)
    bot = Bot(
        TelegramApi(),
        ws,
        chat_ids_from_env(),
        client_factory=lambda: service.make_client(cfg),
        run_every_minutes=cfg.telegram_run_every_minutes,
    )
    bot.run_forever()
