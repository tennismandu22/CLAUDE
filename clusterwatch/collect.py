"""Collecte incrémentale depuis le dernier bloc analysé."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .config import Config
from .models import StakeTransfer, Trade, Transfer, WalletSnapshot
from .state import State
from .taostats import endpoints as ep
from .taostats.client import TaostatsClient

log = logging.getLogger(__name__)


@dataclass
class Collected:
    block_start: int | None
    block_end: int
    trades: list[Trade] = field(default_factory=list)
    transfers: list[Transfer] = field(default_factory=list)  # impliquant au moins un wallet du cluster
    stake_transfers: list[StakeTransfer] = field(default_factory=list)
    deposit_transfers: list[Transfer] = field(default_factory=list)  # vers les adresses de dépôt
    snapshots: list[WalletSnapshot] = field(default_factory=list)
    subnets: dict[int, dict] = field(default_factory=dict)


def _transfer_key(t: Transfer) -> tuple:
    return (t.extrinsic_id or t.block, t.sender, t.recipient, round(t.tao, 9))


def _stake_key(s: StakeTransfer) -> tuple:
    return (s.extrinsic_id or s.block, s.sender, s.recipient, s.netuid)


def dedupe(items: list, key) -> list:
    seen, out = set(), []
    for i in items:
        k = key(i)
        if k not in seen:
            seen.add(k)
            out.append(i)
    return sorted(out, key=lambda x: x.block)


def block_start_for(cfg: Config, state: State) -> int | None:
    return state.last_block + 1 if state.last_block is not None else cfg.start_block


def collect(client: TaostatsClient, cfg: Config, state: State) -> Collected:
    head = ep.head_block(client)
    start = block_start_for(cfg, state)
    out = Collected(block_start=start, block_end=head)
    if start is not None and start > head:
        log.info("aucun nouveau bloc depuis le dernier passage")
        start = head + 1

    log.info("subnets : prix et noms")
    out.subnets = ep.fetch_subnets(client)

    transfers: list[Transfer] = []
    stakes: list[StakeTransfer] = []
    for i, wallet in enumerate(cfg.cluster, 1):
        log.info("[%d/%d] %s", i, len(cfg.cluster), wallet)
        if start is None or start <= head:
            for ev in ep.fetch_delegations(client, wallet, start, head):
                if isinstance(ev, StakeTransfer):
                    if ev.sender != ev.recipient:
                        stakes.append(ev)
                else:
                    out.trades.append(ev)
            transfers += ep.fetch_transfers(client, start, head, address=wallet)

        positions = ep.fetch_positions(client, wallet)
        for idx, p in enumerate(positions):
            if p.tao_value <= 0 and p.netuid in out.subnets:
                positions[idx] = p.__class__(**{**p.__dict__, "tao_value": p.alpha * out.subnets[p.netuid]["price"]})
        out.snapshots.append(
            WalletSnapshot(address=wallet, free_tao=ep.fetch_free_balance(client, wallet), positions=positions)
        )

    if start is None or start <= head:
        for dep in cfg.deposit_addresses:
            log.info("dépôt %s", dep)
            out.deposit_transfers += ep.fetch_transfers(client, start, head, to=dep)

    out.transfers = dedupe(transfers, _transfer_key)
    out.stake_transfers = dedupe(stakes, _stake_key)
    out.deposit_transfers = dedupe(out.deposit_transfers, _transfer_key)
    out.trades.sort(key=lambda t: (t.block, t.wallet))
    return out


def fetch_history(client: TaostatsClient, cfg: Config, address: str, block_end: int | None = None) -> list[Trade]:
    """Historique de trades d'une adresse (pour l'empreinte), borné en pages."""
    events = ep.fetch_delegations(client, address, None, block_end, max_pages=cfg.api.candidate_max_pages)
    return [e for e in events if isinstance(e, Trade)]
