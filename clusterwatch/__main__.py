"""CLI : python -m clusterwatch {run,add,remove,groups,probe,fingerprint}."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import DEFAULT_CONFIG_DIR, RANKS, ConfigError, list_groups, load_group, settings_config, short
from .state import DEFAULT_STATE_DIR, load_state, save_state, state_path
from .taostats.cache import DiskCache
from .taostats.client import TaostatsClient, TaostatsError

log = logging.getLogger("clusterwatch")


def make_client(cfg) -> TaostatsClient:
    return TaostatsClient(
        base_url=cfg.api.base_url,
        min_interval_s=cfg.api.min_interval_s,
        max_retries=cfg.api.max_retries,
        page_limit=cfg.api.page_limit,
        cache=DiskCache(cfg.api.cache_dir),
    )


def run_group(args, name: str, client: TaostatsClient | None) -> tuple[int, TaostatsClient]:
    from .pipeline import run_pass
    from .report.markdown import write_report
    from .report.telegram_text import render_telegram

    spath = state_path(name, args.state_dir)
    state = load_state(spath)
    cfg = load_group(name, args.config_dir, auto_wallets=state.auto_wallets.keys())
    client = client or make_client(cfg)
    log.info("=== groupe %s : %d wallets suivis ===", name, len(cfg.cluster))
    result = run_pass(client, cfg, state)
    path = write_report(cfg, result, args.reports)
    text = render_telegram(cfg, result)
    log.info("rapport écrit : %s", path)

    if args.dry_run:
        log.info("--dry-run : état non sauvegardé")
    else:
        save_state(state, spath)

    print(text + "\n")
    if not result.has_anything:
        log.info("groupe %s : rien à signaler", name)
    elif args.send or cfg.telegram_enabled:
        if args.dry_run:
            log.info("--dry-run : pas d'envoi Telegram")
        else:
            from .notify.telegram import TelegramError, send_text

            try:
                n = send_text(text)
                log.info("rapport envoyé sur Telegram (%d message(s))", n)
            except TelegramError as exc:
                log.error("%s", exc)
                return 1, client
    return 0, client


def cmd_run(args) -> int:
    groups = args.group or list_groups(args.config_dir)
    if not groups:
        raise ConfigError("aucun groupe : créez-en un avec `python -m clusterwatch add <adresse> --group <nom>`")
    status, client = 0, None
    for name in groups:
        try:
            code, client = run_group(args, name, client)
            status = max(status, code)
        except (ConfigError, TaostatsError) as exc:
            log.error("groupe %s : %s", name, exc)
            status = 1
    if client:
        log.info("%d appels API, %d réponses depuis le cache", client.calls, client.cache_hits)
    return status


def cmd_add(args) -> int:
    from .groups import add_address

    spath = state_path(args.group, args.state_dir)
    state = load_state(spath)
    print(add_address(args.group, args.address, args.rank, args.config_dir, state))
    load_group(args.group, args.config_dir, auto_wallets=state.auto_wallets.keys())  # validation
    if spath.exists():
        save_state(state, spath)
    return 0


def cmd_remove(args) -> int:
    from .groups import remove_address

    spath = state_path(args.group, args.state_dir)
    state = load_state(spath)
    print(remove_address(args.group, args.address, args.config_dir, state))
    save_state(state, spath)
    return 0


def cmd_groups(args) -> int:
    names = list_groups(args.config_dir)
    if not names:
        print("Aucun groupe. Créez-en un : python -m clusterwatch add <adresse> --group <nom>")
        return 0
    for name in names:
        state = load_state(state_path(name, args.state_dir))
        cfg = load_group(name, args.config_dir, auto_wallets=state.auto_wallets.keys())
        last = state.last_run or "jamais"
        value = "n/d" if state.cluster_value_tao is None else f"{state.cluster_value_tao:.2f} TAO"
        print(f"\n[{name}] dernier passage : {last} — valeur : {value}")
        for rank in RANKS:
            for a in getattr(cfg, rank):
                print(f"  {rank:<12} {a}")
        for a in cfg.auto_wallets:
            print(f"  {'ajout auto':<12} {a}")
        for a in cfg.deposit_addresses:
            print(f"  {'dépôt':<12} {a}")
        strong = [a for a, e in state.candidates.items() if e.get("confidence") == "fort" and a not in cfg.cluster]
        if strong:
            print("  candidats forts non suivis : " + ", ".join(short(a) for a in strong))
        if state.rejected:
            print("  retirés (jamais ré-ajoutés) : " + ", ".join(short(a) for a in state.rejected))
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

    p = sub.add_parser("probe", help="affiche un échantillon brut de chaque endpoint Taostats")
    p.add_argument("--address", help="adresse utilisée pour les requêtes (défaut : 1er wallet du 1er groupe)")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("fingerprint", help="calcule l'empreinte d'une adresse")
    p.add_argument("address")
    p.set_defaults(func=cmd_fingerprint)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        return args.func(args)
    except (ConfigError, TaostatsError) as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
