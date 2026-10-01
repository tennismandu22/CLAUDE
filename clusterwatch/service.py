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


# --- Adresses liées ------------------------------------------------------------


@dataclass
class LinksResult:
    seed: str
    seed_trades: int
    links: list
    deposits: list[str]
    hubs: list[str]
    examined: int


def links_estimate_minutes(cfg: Config) -> int:
    """Durée maximale approximative d'une recherche de liens avec les réglages actuels."""
    L = cfg.links
    calls = 4 + 2 * L.seed_pages + (L.history_pages + 3) * (L.max_counterparties + 2 * L.max_shared_senders)
    return max(1, round(calls * cfg.api.min_interval_s / 60))


def find_links(ws: Workspace, client: TaostatsClient, seed: str, now: datetime | None = None) -> LinksResult:
    from .analysis import links as lk
    from .analysis.fingerprint import compute_fingerprint
    from .models import StakeTransfer, Trade
    from .pipeline import split_moves
    from .taostats import endpoints as ep

    now = now or datetime.now(timezone.utc)
    cfg = settings_config(ws.config_dir)
    L = cfg.links
    known_deposits = set()
    for g in list_groups(ws.config_dir):
        known_deposits |= set(ws.load(g)[0].deposit_addresses)
    ignored = set(cfg.infra_by_address) | {seed}

    head = ep.head_block(client)
    subnets = ep.fetch_subnets(client)

    def history(address: str, pages: int):
        events = ep.fetch_delegations(client, address, None, head, max_pages=pages, order=ep.ORDER_DESC)
        trades = [e for e in events if isinstance(e, Trade)]
        stakes = [e for e in events if isinstance(e, StakeTransfer)]
        real, _ = split_moves(cfg, trades)
        return real, stakes

    seed_transfers = ep.fetch_transfers(client, None, head, address=seed, max_pages=L.seed_pages)
    seed_trades, seed_stakes = history(seed, L.seed_pages)
    seed_fp = compute_fingerprint(seed, seed_trades, cfg.fingerprint)
    seed_first_funder = lk.profile_from_transfers(seed, seed_transfers)["first_funder"]

    inter = {a: i for a, i in lk.aggregate_interactions(seed, seed_transfers, seed_stakes).items()
             if a not in ignored}
    ranked = [i.address for i in sorted(inter.values(), key=lambda i: i.rank_key, reverse=True)]
    to_examine = ranked[: L.max_counterparties]
    if seed_first_funder and seed_first_funder not in ignored and seed_first_funder not in to_examine:
        to_examine.append(seed_first_funder)
    to_examine += [a for a in ranked if a in known_deposits and a not in to_examine]

    profiles: dict[str, lk.Profile] = {}

    def profile(address: str) -> lk.Profile:
        if address in profiles:
            return profiles[address]
        log.info("examen de %s", address)
        transfers = ep.fetch_transfers(client, None, head, address=address, max_pages=1)
        trades, _ = history(address, L.history_pages)
        positions = ep.fetch_positions(client, address)
        staked = sum(p.tao_value or p.alpha * subnets.get(p.netuid, {}).get("price", 0.0) for p in positions)
        p = lk.Profile(
            address=address,
            n_trades=len(trades),
            value_tao=ep.fetch_free_balance(client, address) + staked,
            last_trade=max((t.timestamp for t in trades), default=None),
            fingerprint=compute_fingerprint(address, trades, cfg.fingerprint) if trades else None,
            **lk.profile_from_transfers(address, transfers),
        )
        profiles[address] = p
        return p

    hubs, deposits, wallets = [], [], []
    for a in to_examine:
        p = profile(a)
        if a in known_deposits or lk.is_deposit_like(p, L.hub_counterparties, seed, inter.get(a)):
            deposits.append(a)
        elif lk.is_hub(p, L.hub_counterparties):
            hubs.append(a)
        else:
            wallets.append(a)

    shared: dict[str, str] = {}
    for d in deposits:
        senders: dict[str, int] = {}
        for t in ep.fetch_transfers(client, None, head, to=d, max_pages=1, order=ep.ORDER_DESC):
            if t.sender not in ignored and t.sender not in deposits and t.sender not in hubs:
                senders[t.sender] = senders.get(t.sender, 0) + 1
        for s in sorted(senders, key=senders.get, reverse=True)[: L.max_shared_senders]:
            shared.setdefault(s, d)
            if s not in wallets:
                p = profile(s)
                if lk.is_hub(p, L.hub_counterparties):
                    hubs.append(s)
                else:
                    wallets.append(s)

    links = []
    for a in wallets:
        link = lk.score_link(a, seed_fp, seed_first_funder, inter.get(a), profiles.get(a),
                             shared.get(a), cfg.fingerprint, seed)
        link.active = profiles[a].is_active(now, L.active_days, L.active_min_tao)
        links.append(link)
    return LinksResult(seed, seed_fp.n_trades, lk.sort_links(links), deposits, hubs, len(profiles))


def render_links(ws: Workspace, r: LinksResult, show_all: bool = False) -> str:
    from .analysis.links import POSSIBLE, PROBABLE, TRES_PROBABLE
    from .report.fmt import num

    shown_levels = (TRES_PROBABLE, PROBABLE, POSSIBLE) if show_all else (TRES_PROBABLE, PROBABLE)
    active = [l for l in r.links if l.active]
    L = [f"Adresses liées à {r.seed}",
         f"{r.examined} adresse(s) examinée(s), {r.seed_trades} trades de l'adresse de départ analysés."]
    for level in shown_levels:
        group = [l for l in active if l.level == level]
        if not group:
            continue
        L += ["", f"{level.upper()} ({len(group)})"]
        for l in group:
            p = l.profile
            last = f", dernier trade {p.last_trade:%d/%m/%Y}" if p and p.last_trade else ""
            L.append(f"• {l.address}")
            L.append(f"  valeur {num(p.value_tao if p else 0)} TAO, {p.n_trades if p else 0} trades analysés{last}")
            L += [f"  - {e}" for e in l.evidence]
    if not any(l.level in shown_levels for l in active):
        L += ["", "Aucune adresse active liée avec un niveau suffisant."]

    hidden = [l for l in active if l.level not in shown_levels and l.level != "faible"]
    inactive = [l for l in r.links if not l.active and l.level != "faible"]
    if hidden:
        L += ["", f"{len(hidden)} lien(s) « possible » non affiché(s) (indices insuffisants)."]
    if inactive:
        L.append(f"{len(inactive)} adresse(s) liée(s) mais inactive(s), écartée(s).")
    if r.deposits:
        L += ["", "Adresses de dépôt exchange détectées : " + ", ".join(r.deposits)]
    if r.hubs:
        L.append(f"{len(r.hubs)} hub(s) ignoré(s) (exchange ou service : trop de contreparties).")
    L += ["", "« Très probable » = au moins deux indices forts indépendants. Une attribution "
              "on-chain reste une probabilité, pas une certitude absolue."]
    return "\n".join(L)
