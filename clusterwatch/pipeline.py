"""Orchestration d'un passage : collecte → analyses → mise à jour de l'état."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .analysis.balance import ClusterBalance, compute_balance
from .analysis.discovery import Candidate, confidence, find_candidates, update_counterparties
from .analysis.events import Events, detect_events
from .analysis.fingerprint import compute_fingerprint
from .analysis.pnl import WalletPnl, compute_pnl, update_flows
from .analysis.validator_moves import split_validator_moves
from .collect import Collected, collect, fetch_history
from .config import Config
from .models import Trade, ValidatorMove
from .state import State
from .taostats.client import TaostatsClient, TaostatsError

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    run_at: datetime
    initial: bool
    collected: Collected
    trades: list[Trade]
    validator_moves: list[ValidatorMove]
    balance: ClusterBalance
    pnls: list[WalletPnl]
    events: Events
    candidates: list[Candidate] = field(default_factory=list)
    pending_candidates: int = 0

    @property
    def has_anything(self) -> bool:
        return not self.initial and (self.events.has_anything or bool(self.candidates))


def split_moves(cfg: Config, trades: list[Trade]):
    th = cfg.thresholds
    return split_validator_moves(
        trades, th.validator_move_block_window, th.validator_move_price_tolerance, th.validator_move_slippage_max
    )


def evaluate_candidate(
    client: TaostatsClient, cfg: Config, address: str, reasons: set[str], block_end: int | None
) -> Candidate:
    cand = Candidate(address=address, reasons=set(reasons))
    try:
        history, _ = split_moves(cfg, fetch_history(client, cfg, address, block_end))
        cand.fingerprint = compute_fingerprint(address, history, cfg.fingerprint)
    except TaostatsError as exc:
        log.warning("empreinte impossible pour %s : %s", address, exc)
    cand.confidence = confidence(cand.reasons, cand.fingerprint, cfg.fingerprint)
    return cand


def run_pass(client: TaostatsClient, cfg: Config, state: State, now: datetime | None = None) -> RunResult:
    """Exécute un passage et met à jour `state` en mémoire (sauvegarde par l'appelant)."""
    now = now or datetime.now(timezone.utc)
    initial = state.is_initial
    col = collect(client, cfg, state)

    trades, moves = split_moves(cfg, col.trades)
    balance = compute_balance(col.snapshots, trades, state.cluster_value_tao)

    update_flows(state.flows, cfg.cluster, col.transfers, col.stake_transfers, not_withdrawals=cfg.fee_collectors)
    pnls = compute_pnl(balance.per_wallet, state.flows)

    events = detect_events(
        cfg, trades, moves, col.transfers, col.stake_transfers, col.snapshots,
        seen_subnets=set(state.seen_subnets),
        previous_wallet_values=state.wallet_values,
        initial_run=initial,
    )

    # Candidats : fusion avec ceux déjà connus, réévaluation si nouvelles raisons.
    update_counterparties(state.counterparties, cfg, col.transfers, col.stake_transfers)
    found = find_candidates(
        cfg, col.transfers, col.stake_transfers, col.deposit_transfers,
        set(state.known_depositors), state.counterparties,
    )
    for addr, reasons in found.items():
        entry = state.candidates.setdefault(addr, {"reasons": [], "first_seen_block": col.block_end})
        merged = set(entry["reasons"]) | reasons
        if merged != set(entry["reasons"]) or "confidence" not in entry:
            entry["reasons"] = sorted(merged)
            entry["pending"] = True

    evaluated: list[Candidate] = []
    pending = [a for a, e in state.candidates.items() if e.get("pending")]
    for addr in pending[: cfg.api.max_candidates_per_run]:
        entry = state.candidates[addr]
        log.info("empreinte du candidat %s", addr)
        cand = evaluate_candidate(client, cfg, addr, set(entry["reasons"]), col.block_end)
        previous = entry.get("confidence")
        entry.update(
            pending=False,
            confidence=cand.confidence,
            n_trades=cand.fingerprint.n_trades if cand.fingerprint else None,
            criteria_ok=cand.fingerprint.n_ok if cand.fingerprint else None,
            evaluated_block=col.block_end,
        )
        if previous != cand.confidence:
            evaluated.append(cand)
    order = {"fort": 0, "moyen": 1, "faible": 2}
    evaluated.sort(key=lambda c: (order[c.confidence], c.address))

    # Mise à jour de l'état
    state.last_block = col.block_end
    state.last_run = now.isoformat(timespec="seconds")
    state.cluster_value_tao = balance.value_tao
    state.wallet_values = dict(balance.per_wallet)
    state.seen_subnets = sorted(
        set(state.seen_subnets) | {n for s in col.snapshots for n in s.subnets} | {t.netuid for t in trades if t.is_buy}
    )
    state.known_depositors = sorted(
        set(state.known_depositors)
        | {t.sender for t in col.deposit_transfers if t.recipient in cfg.deposit_addresses}
    )

    return RunResult(
        run_at=now,
        initial=initial,
        collected=col,
        trades=trades,
        validator_moves=moves,
        balance=balance,
        pnls=pnls,
        events=events,
        candidates=evaluated,
        pending_candidates=sum(1 for e in state.candidates.values() if e.get("pending")),
    )
