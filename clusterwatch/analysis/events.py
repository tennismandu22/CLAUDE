"""Sélection des événements à signaler pour un passage."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Config
from ..models import StakeTransfer, Trade, Transfer, ValidatorMove, WalletSnapshot


@dataclass
class SmallTradesSummary:
    n_buys: int = 0
    n_sells: int = 0
    buys_tao: float = 0.0
    sells_tao: float = 0.0

    @property
    def count(self) -> int:
        return self.n_buys + self.n_sells


@dataclass
class Events:
    big_trades: list[Trade] = field(default_factory=list)
    small_trades: SmallTradesSummary = field(default_factory=SmallTradesSummary)
    validator_moves: list[ValidatorMove] = field(default_factory=list)
    tao_transfers: list[Transfer] = field(default_factory=list)
    stake_transfers: list[StakeTransfer] = field(default_factory=list)
    new_subnets: list[int] = field(default_factory=list)
    watched_subnet_trades: list[Trade] = field(default_factory=list)
    watched_subnet_stake_transfers: list[StakeTransfer] = field(default_factory=list)
    awakened_wallets: list[str] = field(default_factory=list)

    @property
    def has_anything(self) -> bool:
        """Vrai s'il y a au moins un événement à signaler.

        Les changements de validateur seuls ne déclenchent pas d'envoi.
        """
        return bool(
            self.big_trades
            or self.small_trades.count
            or self.tao_transfers
            or self.stake_transfers
            or self.new_subnets
            or self.watched_subnet_trades
            or self.watched_subnet_stake_transfers
            or self.awakened_wallets
        )


def is_hidden_micro_fee(t: Transfer, cfg: Config) -> bool:
    return t.recipient in cfg.fee_collectors and t.tao < cfg.thresholds.micro_fee_tao


def detect_events(
    cfg: Config,
    trades: list[Trade],
    validator_moves: list[ValidatorMove],
    transfers: list[Transfer],
    stake_transfers: list[StakeTransfer],
    snapshots: list[WalletSnapshot],
    seen_subnets: set[int],
    previous_wallet_values: dict[str, float],
    initial_run: bool,
) -> Events:
    """`trades` exclut déjà les changements de validateur ; `transfers` et
    `stake_transfers` sont ceux qui impliquent au moins un wallet du cluster."""
    th = cfg.thresholds
    cluster = set(cfg.cluster)
    ev = Events(validator_moves=validator_moves)

    for t in trades:
        if t.tao >= th.big_trade_tao:
            ev.big_trades.append(t)
        elif t.is_buy:
            ev.small_trades.n_buys += 1
            ev.small_trades.buys_tao += t.tao
        else:
            ev.small_trades.n_sells += 1
            ev.small_trades.sells_tao += t.tao

    ev.tao_transfers = [t for t in transfers if not is_hidden_micro_fee(t, cfg)]
    ev.stake_transfers = list(stake_transfers)

    watched = set(th.watch_subnets)
    ev.watched_subnet_trades = [t for t in trades if t.netuid in watched]
    ev.watched_subnet_trades += [m.sell for m in validator_moves if m.sell.netuid in watched]
    ev.watched_subnet_trades += [m.buy for m in validator_moves if m.buy.netuid in watched]
    ev.watched_subnet_stake_transfers = [s for s in stake_transfers if s.netuid in watched]

    current_subnets = {n for s in snapshots for n in s.subnets} | {t.netuid for t in trades if t.is_buy}
    if not initial_run:
        ev.new_subnets = sorted(current_subnets - seen_subnets)

    active = {t.wallet for t in trades}
    active |= {t.recipient for t in transfers if t.recipient in cluster}
    active |= {s.recipient for s in stake_transfers if s.recipient in cluster}
    values = {s.address: s.total_tao for s in snapshots}
    for wallet, prev in previous_wallet_values.items():
        if prev < th.emptied_wallet_tao and (
            wallet in active or values.get(wallet, 0.0) >= th.emptied_wallet_tao
        ):
            ev.awakened_wallets.append(wallet)

    ev.big_trades.sort(key=lambda t: t.block)
    ev.tao_transfers.sort(key=lambda t: t.block)
    ev.stake_transfers.sort(key=lambda t: t.block)
    ev.watched_subnet_trades.sort(key=lambda t: t.block)
    return ev
