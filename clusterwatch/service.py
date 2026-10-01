"""Opérations communes à la CLI et au bot Telegram."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import DEFAULT_CONFIG_DIR, RANKS, Config, list_groups, load_group, settings_config, short
from .state import DEFAULT_STATE_DIR, load_state, save_state, state_path
from .taostats.cache import DiskCache
from .taostats.client import TaostatsClient

log = logging.getLogger(__name__)


def make_client(cfg: Config) -> TaostatsClient:
    return TaostatsClient(
        base_url=cfg.api.base_url,
        min_interval_s=cfg.api.min_interval_s,
        max_retries=cfg.api.max_retries,
        page_limit=cfg.api.page_limit,
        cache=DiskCache(cfg.api.cache_dir),
    )


@dataclass
class Workspace:
    """Emplacements des fichiers de config, d'état et de rapports."""

    config_dir: Path | str = DEFAULT_CONFIG_DIR
    state_dir: Path | str = DEFAULT_STATE_DIR
    reports_dir: Path | str = "reports"

    def load(self, group: str):
        spath = state_path(group, self.state_dir)
        state = load_state(spath)
        cfg = load_group(group, self.config_dir, auto_wallets=state.auto_wallets.keys())
        return cfg, state, spath


@dataclass
class GroupRun:
    group: str
    text: str
    has_anything: bool
    report_path: Path
    initial: bool = False


def run_group(ws: Workspace, group: str, client: TaostatsClient, dry_run: bool = False) -> GroupRun:
    from .pipeline import run_pass
    from .report.markdown import write_report
    from .report.telegram_text import render_telegram

    cfg, state, spath = ws.load(group)
    log.info("=== groupe %s : %d wallets suivis ===", group, len(cfg.cluster))
    result = run_pass(client, cfg, state)
    path = write_report(cfg, result, ws.reports_dir)
    log.info("rapport écrit : %s", path)
    if dry_run:
        log.info("--dry-run : état non sauvegardé")
    else:
        save_state(state, spath)
    return GroupRun(group, render_telegram(cfg, result), result.has_anything, path, result.initial)


def add(ws: Workspace, group: str, address: str, rank: str = "rang1") -> str:
    from .groups import add_address

    spath = state_path(group, ws.state_dir)
    state = load_state(spath)
    msg = add_address(group, address, rank, ws.config_dir, state)
    if spath.exists():
        save_state(state, spath)
    return msg


def remove(ws: Workspace, group: str, address: str) -> str:
    from .groups import remove_address

    spath = state_path(group, ws.state_dir)
    state = load_state(spath)
    msg = remove_address(group, address, ws.config_dir, state)
    save_state(state, spath)
    return msg


def groups_summary(ws: Workspace, full_addresses: bool = True) -> str:
    names = list_groups(ws.config_dir)
    if not names:
        return "Aucun groupe. Ajoutez une adresse pour en créer un."
    fmt = (lambda a: a) if full_addresses else short
    out = []
    for name in names:
        cfg, state, _ = ws.load(name)
        last = state.last_run or "jamais"
        value = "n/d" if state.cluster_value_tao is None else f"{state.cluster_value_tao:.2f} TAO"
        out.append(f"[{name}] dernier passage : {last} — valeur : {value}")
        for rank in RANKS:
            out += [f"  {rank:<12} {fmt(a)}" for a in getattr(cfg, rank)]
        out += [f"  {'ajout auto':<12} {fmt(a)}" for a in cfg.auto_wallets]
        out += [f"  {'dépôt':<12} {fmt(a)}" for a in cfg.deposit_addresses]
        strong = [a for a, e in state.candidates.items() if e.get("confidence") == "fort" and a not in cfg.cluster]
        if strong:
            out.append("  candidats forts non suivis : " + ", ".join(short(a) for a in strong))
        if state.rejected:
            out.append("  retirés (jamais ré-ajoutés) : " + ", ".join(short(a) for a in state.rejected))
        out.append("")
    return "\n".join(out).rstrip()


def address_info(ws: Workspace, client: TaostatsClient, address: str, now: datetime | None = None) -> str:
    """Instantané d'une adresse quelconque : solde, positions, activité récente, empreinte."""
    from .analysis.fingerprint import compute_fingerprint
    from .collect import fetch_history
    from .pipeline import split_moves
    from .report.fmt import num, subnet
    from .taostats import endpoints as ep

    now = now or datetime.now(timezone.utc)
    cfg = settings_config(ws.config_dir)
    subnets = ep.fetch_subnets(client)
    free = ep.fetch_free_balance(client, address)
    positions = ep.fetch_positions(client, address)
    for i, p in enumerate(positions):
        if p.tao_value <= 0 and p.netuid in subnets:
            positions[i] = p.__class__(**{**p.__dict__, "tao_value": p.alpha * subnets[p.netuid]["price"]})
    staked = sum(p.tao_value for p in positions)
    trades, moves = split_moves(cfg, fetch_history(client, cfg, address))

    L = [f"Adresse {address}"]
    tracked = [g for g in list_groups(ws.config_dir) if address in ws.load(g)[0].cluster]
    if tracked:
        L.append("Suivie dans : " + ", ".join(tracked))
    L += [
        f"Valeur : {num(free + staked)} TAO (libres {num(free)}, positions {num(staked)})",
        f"Subnets détenus : {len({p.netuid for p in positions})}",
    ]
    by_value = sorted(positions, key=lambda p: p.tao_value, reverse=True)
    for p in by_value[:10]:
        L.append(f"• {subnet(p.netuid, subnets)} : {num(p.tao_value)} TAO")
    if len(by_value) > 10:
        L.append(f"  … +{len(by_value) - 10} autres positions")

    recent = [t for t in trades if t.timestamp >= now - timedelta(days=7)]
    buys = [t for t in recent if t.is_buy]
    sells = [t for t in recent if not t.is_buy]
    L += ["", f"7 derniers jours : {len(buys)} achats ({num(sum(t.tao for t in buys))} TAO), "
              f"{len(sells)} ventes ({num(sum(t.tao for t in sells))} TAO)"]
    for t in sorted(recent, key=lambda t: t.block, reverse=True)[:5]:
        L.append(f"• {t.timestamp:%d/%m %H:%M} {'ACHAT' if t.is_buy else 'VENTE'} "
                 f"{subnet(t.netuid, subnets)} {num(t.tao)} TAO")

    fp = compute_fingerprint(address, trades, cfg.fingerprint)
    L += ["", f"Empreinte ({fp.n_trades} trades analysés, {len(moves)} changements de validateur exclus) : "
              f"{fp.n_ok}/{len(fp.core)} critères de la référence"]
    for c in fp.criteria:
        mark = {True: "oui", False: "non", None: "n/d"}[c.ok]
        L.append(f"• {c.label} : {c.measured} [{mark}]")
    return "\n".join(L)
