"""CLI : python -m clusterwatch {run,probe,fingerprint}."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from .state import DEFAULT_STATE_PATH, load_state, save_state
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


def cmd_run(args, cfg) -> int:
    from .pipeline import run_pass
    from .report.markdown import write_report
    from .report.telegram_text import render_telegram

    state = load_state(args.state)
    client = make_client(cfg)
    result = run_pass(client, cfg, state)
    path = write_report(cfg, result, args.reports)
    text = render_telegram(cfg, result)
    log.info("rapport écrit : %s (%d appels API, %d depuis le cache)", path, client.calls, client.cache_hits)

    if args.dry_run:
        log.info("--dry-run : état non sauvegardé")
    else:
        save_state(state, args.state)

    print(text)
    if not result.has_anything:
        log.info("rien à signaler")
    elif args.send or cfg.telegram_enabled:
        if args.dry_run:
            log.info("--dry-run : pas d'envoi Telegram")
        else:
            from .notify.telegram import send_text

            send_text(text)
            log.info("rapport envoyé sur Telegram")
    return 0


def cmd_probe(args, cfg) -> int:
    """Affiche un échantillon brut de chaque endpoint pour valider les champs."""
    from .taostats.endpoints import PROBES

    client = make_client(cfg)
    address = args.address or cfg.cluster[0]
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


def cmd_fingerprint(args, cfg) -> int:
    from .analysis.fingerprint import compute_fingerprint
    from .collect import fetch_history
    from .pipeline import split_moves

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clusterwatch", description="Surveillance d'un cluster de wallets dTAO")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="passage complet : collecte, analyse, rapport")
    p.add_argument("--state", default=str(DEFAULT_STATE_PATH))
    p.add_argument("--reports", default="reports")
    p.add_argument("--dry-run", action="store_true", help="ne sauvegarde pas l'état et n'envoie rien")
    p.add_argument("--send", action="store_true", help="envoie le texte compact sur Telegram")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("probe", help="affiche un échantillon brut de chaque endpoint Taostats")
    p.add_argument("--address", help="adresse utilisée pour les requêtes (défaut : 1er wallet)")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("fingerprint", help="calcule l'empreinte d'une adresse")
    p.add_argument("address")
    p.set_defaults(func=cmd_fingerprint)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        cfg = load_config(args.config)
        return args.func(args, cfg)
    except (ConfigError, TaostatsError) as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
