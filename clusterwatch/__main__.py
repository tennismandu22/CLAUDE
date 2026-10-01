"""CLI : python -m clusterwatch {run,add,remove,groups,info,links,bot,probe,fingerprint}."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import service
from .config import DEFAULT_CONFIG_DIR, RANKS, ConfigError, list_groups, load_group, settings_config
from .notify.telegram import TelegramError
from .service import make_client
from .state import DEFAULT_STATE_DIR
from .taostats.client import TaostatsError

log = logging.getLogger("clusterwatch")


def workspace(args) -> service.Workspace:
    return service.Workspace(args.config_dir, args.state_dir, getattr(args, "reports", "reports"))


def cmd_run(args) -> int:
    ws = workspace(args)
    groups = args.group or list_groups(args.config_dir)
    if not groups:
        raise ConfigError("aucun groupe : créez-en un avec `python -m clusterwatch add <adresse> --group <nom>`")
    client = make_client(settings_config(args.config_dir))
    status = 0
    for name in groups:
        try:
            run = service.run_group(ws, name, client, dry_run=args.dry_run)
        except (ConfigError, TaostatsError) as exc:
            log.error("groupe %s : %s", name, exc)
            status = 1
            continue
        print(run.text + "\n")
        if not run.has_anything:
            log.info("groupe %s : rien à signaler", name)
        elif args.send or load_group(name, args.config_dir).telegram_enabled:
            if args.dry_run:
                log.info("--dry-run : pas d'envoi Telegram")
                continue
            from .notify.telegram import TelegramError, send_text

            try:
                n = send_text(run.text)
                log.info("rapport envoyé sur Telegram (%d message(s))", n)
            except TelegramError as exc:
                log.error("%s", exc)
                status = 1
    log.info("%d appels API, %d réponses depuis le cache", client.calls, client.cache_hits)
    return status


def cmd_add(args) -> int:
    print(service.add(workspace(args), args.group, args.address, args.rank))
    print("Son historique sera collecté au prochain passage (`python -m clusterwatch run`).")
    return 0


def cmd_remove(args) -> int:
    print(service.remove(workspace(args), args.group, args.address))
    return 0


def cmd_groups(args) -> int:
    print(service.groups_summary(workspace(args)))
    return 0


def cmd_info(args) -> int:
    ws = workspace(args)
    print(service.address_info(ws, make_client(settings_config(args.config_dir)), args.address))
    return 0


def cmd_links(args) -> int:
    ws = workspace(args)
    cfg = settings_config(args.config_dir)
    log.info("recherche des adresses liées (au plus ~%d min avec les réglages actuels)",
             service.links_estimate_minutes(cfg))
    result = service.find_links(ws, make_client(cfg), args.address)
    print(service.render_links(ws, result, show_all=args.all))
    return 0


def cmd_bot(args) -> int:
    from .bot import show_chat_ids, start_bot
    from .notify.telegram import TelegramApi

    if args.show_chat_id:
        show_chat_ids(TelegramApi())
    else:
        start_bot(workspace(args))
    return 0


def cmd_probe(args) -> int:
    """Affiche un échantillon brut de chaque endpoint pour valider les champs."""
    from .taostats.endpoints import PROBES

    cfg = settings_config(args.config_dir)
    client = make_client(cfg)
    address = args.address
    if not address:
        groups = list_groups(args.config_dir)
        if not groups:
            raise ConfigError("indiquez --address (aucun groupe configuré)")
        address = load_group(groups[0], args.config_dir).cluster[0]
    for name, (path, params) in PROBES.items():
        print(f"\n===== {name} : {path} =====")
        try:
            payload = client.get(path, params(address))
            data = payload.get("data")
            sample = data[:2] if isinstance(data, list) else payload
            print(json.dumps({"pagination": payload.get("pagination"), "data": sample}, indent=2)[:3000])
        except TaostatsError as exc:
            print(f"ERREUR : {exc}")
    return 0


def cmd_fingerprint(args) -> int:
    from .analysis.fingerprint import compute_fingerprint
    from .collect import fetch_history
    from .pipeline import split_moves

    cfg = settings_config(args.config_dir)
    client = make_client(cfg)
    trades, moves = split_moves(cfg, fetch_history(client, cfg, args.address))
    fp = compute_fingerprint(args.address, trades, cfg.fingerprint)
    print(f"Empreinte de {args.address}")
    print(f"Trades : {fp.n_trades} (+{len(moves)} changements de validateur exclus)")
    print(f"Critères vérifiés : {fp.n_ok}/{len(fp.core)} — cohérente : {'oui' if fp.is_coherent(cfg.fingerprint) else 'non'}")
    for c in fp.criteria:
        mark = {True: "OK ", False: "NON", None: "n/d"}[c.ok]
        print(f"  [{mark}] {c.label:<40} {c.measured}  (réf. {c.expected})")
    print("Heures UTC : " + " ".join(f"{h:02d}h:{n}" for h, n in enumerate(fp.hours) if n))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clusterwatch", description="Surveillance de groupes de wallets dTAO")
    parser.add_argument("--config-dir", default=str(DEFAULT_CONFIG_DIR))
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="passage complet : collecte, analyse, rapport (tous les groupes par défaut)")
    p.add_argument("--group", action="append", help="groupe à traiter (répétable)")
    p.add_argument("--reports", default="reports")
    p.add_argument("--dry-run", action="store_true", help="ne sauvegarde pas l'état et n'envoie rien")
    p.add_argument("--send", action="store_true", help="envoie le texte compact sur Telegram")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("add", help="ajoute une adresse à un groupe (le crée s'il n'existe pas)")
    p.add_argument("address")
    p.add_argument("--group", required=True)
    p.add_argument("--rank", default="rang1", choices=list(RANKS) + ["depot"],
                   help="catégorie : rang1 (défaut), rang2, observation ou depot (adresse de dépôt exchange)")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("remove", help="retire une adresse d'un groupe (elle ne sera plus ré-ajoutée auto)")
    p.add_argument("address")
    p.add_argument("--group", required=True)
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("groups", help="liste les groupes et leurs adresses suivies")
    p.set_defaults(func=cmd_groups)

    p = sub.add_parser("info", help="instantané d'une adresse quelconque (solde, positions, trades, empreinte)")
    p.add_argument("address")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("links", help="adresses actives liées à une adresse, avec niveau de certitude")
    p.add_argument("address")
    p.add_argument("--all", action="store_true", help="affiche aussi les liens « possible »")
    p.set_defaults(func=cmd_links)

    p = sub.add_parser("bot", help="démarre le bot Telegram (commandes à distance + passages planifiés)")
    p.add_argument("--reports", default="reports")
    p.add_argument("--show-chat-id", action="store_true",
                   help="affiche l'identifiant des chats qui écrivent au bot (configuration)")
    p.set_defaults(func=cmd_bot)

    p = sub.add_parser("probe", help="affiche un échantillon brut de chaque endpoint Taostats")
    p.add_argument("--address", help="adresse utilisée pour les requêtes (défaut : 1er wallet du 1er groupe)")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("fingerprint", help="calcule l'empreinte d'une adresse")
    p.add_argument("address")
    p.set_defaults(func=cmd_fingerprint)
    return parser


def main(argv: list[str] | None = None) -> int:
    from .env import load_dotenv

    load_dotenv()
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        return args.func(args)
    except (ConfigError, TaostatsError, TelegramError) as exc:
        log.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
