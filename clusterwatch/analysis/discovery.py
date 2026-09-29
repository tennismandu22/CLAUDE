"""Détection de wallets candidats et niveau de confiance."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Config, FingerprintRef
from ..models import StakeTransfer, Transfer
from ..state import Counterparty
from .fingerprint import Fingerprint

DEPOSIT = "depot_partage"
TAO_OUT = "transfert_tao"
STAKE = "transfert_stake"
BIDIRECTIONAL = "flux_bidirectionnel"

REASON_LABELS = {
    DEPOSIT: "nouvel expéditeur vers une adresse de dépôt",
    TAO_OUT: "destinataire d'un transfert TAO depuis le cluster",
    STAKE: "transfert de stake avec le cluster",
    BIDIRECTIONAL: "flux TAO/stake dans les deux sens avec le cluster",
}

FORT, MOYEN, FAIBLE = "fort", "moyen", "faible"


@dataclass
class Candidate:
    address: str
    reasons: set[str] = field(default_factory=set)
    fingerprint: Fingerprint | None = None
    confidence: str = FAIBLE


def update_counterparties(
    counterparties: dict[str, Counterparty],
    cfg: Config,
    transfers: list[Transfer],
    stake_transfers: list[StakeTransfer],
) -> None:
    """Cumule les flux entre le cluster et chaque adresse externe."""
    cluster = set(cfg.cluster)
    for t in transfers:
        if t.sender in cluster and t.recipient not in cluster:
            counterparties.setdefault(t.recipient, Counterparty()).from_cluster_tao += t.tao
        elif t.recipient in cluster and t.sender not in cluster:
            counterparties.setdefault(t.sender, Counterparty()).to_cluster_tao += t.tao
    for s in stake_transfers:
        if s.sender in cluster and s.recipient not in cluster:
            counterparties.setdefault(s.recipient, Counterparty()).stake_from_cluster_tao += s.tao
        elif s.recipient in cluster and s.sender not in cluster:
            counterparties.setdefault(s.sender, Counterparty()).stake_to_cluster_tao += s.tao


def is_bidirectional(c: Counterparty) -> bool:
    out = c.from_cluster_tao + c.stake_from_cluster_tao
    back = c.to_cluster_tao + c.stake_to_cluster_tao
    return out > 0 and back > 0


def find_candidates(
    cfg: Config,
    transfers: list[Transfer],
    stake_transfers: list[StakeTransfer],
    deposit_transfers: list[Transfer],
    known_depositors: set[str],
    counterparties: dict[str, Counterparty],
) -> dict[str, set[str]]:
    """Adresse candidate -> raisons, pour les transferts de ce passage."""
    cluster = set(cfg.cluster)
    out: dict[str, set[str]] = {}

    def add(addr: str, reason: str) -> None:
        if addr and addr not in cluster and not cfg.is_ignored_counterparty(addr):
            out.setdefault(addr, set()).add(reason)

    for t in deposit_transfers:
        if t.recipient in cfg.deposit_addresses and t.sender not in known_depositors:
            add(t.sender, DEPOSIT)
    for t in transfers:
        if t.sender in cluster:
            add(t.recipient, TAO_OUT)
    for s in stake_transfers:
        if s.sender in cluster:
            add(s.recipient, STAKE)
        elif s.recipient in cluster:
            add(s.sender, STAKE)
    for addr, reasons in out.items():
        cp = counterparties.get(addr)
        if cp and is_bidirectional(cp):
            reasons.add(BIDIRECTIONAL)
    return out


def confidence(reasons: set[str], fp: Fingerprint | None, ref: FingerprintRef) -> str:
    if fp is None or fp.n_trades < ref.min_trades:
        return FAIBLE
    if STAKE in reasons or BIDIRECTIONAL in reasons:
        return FORT
    if DEPOSIT in reasons and fp.is_coherent(ref):
        return MOYEN
    return FAIBLE
