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
    group: str = "principal"
    # Adresses suivies pour la première fois à ce passage (ajout manuel ou auto précédent).
    new_addresses: list[str] = field(default_factory=list)
    # Valeur des wallets nouvellement suivis, incluse dans la variation du cluster.
    new_wallets_value_tao: float = 0.0
    # Wallets ajoutés automatiquement à ce passage (suivis à partir du prochain).
    auto_added: list[str] = field(default_factory=list)

    @property
    def has_anything(self) -> bool:
        return not self.initial and (
            self.events.has_anything or bool(self.candidates) or bool(self.new_addresses) or bool(self.auto_added)
        )


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


def _split_by_block(items: list, last_block: int | None) -> tuple[list, list]:
    """(nouveaux, historiques) : les éléments historiques (bloc ≤ dernier bloc
    analysé) ne peuvent venir que d'adresses nouvellement suivies."""
    if last_block is None:
        return list(items), []
    return [x for x in items if x.block > last_block], [x for x in items if x.block <= last_block]


def run_pass(client: TaostatsClient, cfg: Config, state: State, now: datetime | None = None) -> RunResult:
    """Exécute un passage et met à jour `state` en mémoire (sauvegarde par l'appelant)."""
    now = now or datetime.now(timezone.utc)
    initial = state.is_initial
    last = state.last_block
    col = collect(client, cfg, state)
    new_wallets = col.new_addresses & set(cfg.cluster)

    all_trades, all_moves = split_moves(cfg, col.trades)
    trades, hist_trades = _split_by_block(all_trades, last)
    moves = [m for m in all_moves if last is None or m.sell.block > last]
    transfers, hist_transfers = _split_by_block(col.transfers, last)
    stakes, hist_stakes = _split_by_block(col.stake_transfers, last)

    balance = compute_balance(col.snapshots, trades, state.cluster_value_tao)

    # PnL : les flux historiques d'une adresse nouvelle ne sont comptés que pour
    # elle (l'autre côté, s'il est déjà suivi, a été compté aux passages précédents).
    update_flows(state.flows, cfg.cluster, transfers, stakes, not_withdrawals=cfg.fee_collectors)
    update_flows(state.flows, new_wallets, hist_transfers, hist_stakes, not_withdrawals=cfg.fee_collectors)
    pnls = compute_pnl(balance.per_wallet, state.flows)

    # Les subnets déjà détenus par un wallet nouvellement suivi ne sont pas une « première position ».
    new_wallet_subnets = {n for s in col.snapshots if s.address in new_wallets for n in s.subnets}
    new_wallet_subnets |= {t.netuid for t in hist_trades if t.is_buy}
    events = detect_events(
        cfg, trades, moves, transfers, stakes, col.snapshots,
        seen_subnets=set(state.seen_subnets) | new_wallet_subnets,
        previous_wallet_values=state.wallet_values,
        initial_run=initial,
    )

    # Candidats : fusion avec ceux déjà connus, réévaluation si nouvelles raisons.
    # L'historique des adresses nouvelles sert aussi à trouver leurs wallets liés.
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

    cluster = set(cfg.cluster)
    evaluated: list[Candidate] = []
    pending = [a for a, e in state.candidates.items() if e.get("pending") and a not in cluster]
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

    # Ajout automatique des candidats de confiance forte.
    auto_added: list[str] = []
    if cfg.auto_add.enabled:
        rejected = set(state.rejected)
        for addr, entry in sorted(state.candidates.items()):
            if len(auto_added) >= cfg.auto_add.max_per_run:
                break
            if (
                entry.get("confidence") == "fort"
                and addr not in cluster
                and addr not in rejected
                and addr not in state.auto_wallets
                and not cfg.is_ignored_counterparty(addr)
            ):
                state.auto_wallets[addr] = {"added_block": col.block_end, "reasons": entry["reasons"]}
                auto_added.append(addr)
                log.info("ajout automatique au suivi : %s", addr)

    # Mise à jour de l'état
    state.last_block = col.block_end
    state.last_run = now.isoformat(timespec="seconds")
    state.cluster_value_tao = balance.value_tao
    state.wallet_values = dict(balance.per_wallet)
    state.seen_subnets = sorted(
        set(state.seen_subnets) | {n for s in col.snapshots for n in s.subnets}
        | {t.netuid for t in all_trades if t.is_buy}
    )
    state.known_depositors = sorted(
        set(state.known_depositors)
        | {t.sender for t in col.deposit_transfers if t.recipient in cfg.deposit_addresses}
    )
    state.tracked_addresses = sorted(set(cfg.cluster) | set(cfg.deposit_addresses))

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
        pending_candidates=sum(
            1 for a, e in state.candidates.items() if e.get("pending") and a not in cluster
        ),
        group=cfg.name,
        new_addresses=sorted(col.new_addresses),
        new_wallets_value_tao=sum(v for a, v in balance.per_wallet.items() if a in new_wallets),
        auto_added=auto_added,
    )
