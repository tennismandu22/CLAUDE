"""Modèles internes. Tous les montants sont en TAO (ou en alpha), jamais en RAO."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

RAO_PER_TAO = 1e9


def rao_to_tao(value) -> float:
    """Convertit un montant RAO (int, str ou None) en TAO."""
    if value is None or value == "":
        return 0.0
    return float(value) / RAO_PER_TAO


@dataclass(frozen=True)
class Trade:
    """Achat (delegate : TAO → alpha) ou vente (undelegate : alpha → TAO)."""

    wallet: str
    block: int
    timestamp: datetime
    netuid: int
    side: str  # "buy" | "sell"
    tao: float
    alpha: float
    price: float  # prix de l'alpha en TAO
    slippage: float  # fraction (0.001 = 0,1 %)
    hotkey: str = ""
    validator_name: str = ""
    extrinsic_id: str = ""

    @property
    def is_buy(self) -> bool:
        return self.side == "buy"


@dataclass(frozen=True)
class Transfer:
    """Transfert de TAO libre entre deux coldkeys."""

    sender: str
    recipient: str
    tao: float
    block: int
    timestamp: datetime
    extrinsic_id: str = ""


@dataclass(frozen=True)
class StakeTransfer:
    """Transfert de stake alpha d'une coldkey à une autre (transfer_stake)."""

    sender: str
    recipient: str
    netuid: int
    alpha: float
    tao: float  # valeur en TAO au moment du transfert
    block: int
    timestamp: datetime
    extrinsic_id: str = ""


@dataclass(frozen=True)
class Position:
    coldkey: str
    hotkey: str
    netuid: int
    alpha: float
    tao_value: float


@dataclass
class WalletSnapshot:
    address: str
    free_tao: float = 0.0
    positions: list[Position] = field(default_factory=list)

    @property
    def staked_tao(self) -> float:
        return sum(p.tao_value for p in self.positions)

    @property
    def total_tao(self) -> float:
        return self.free_tao + self.staked_tao

    @property
    def subnets(self) -> set[int]:
        return {p.netuid for p in self.positions if p.alpha > 0}


@dataclass
class ValidatorMove:
    """Paire vente/achat identifiée comme un changement de validateur."""

    sell: Trade
    buy: Trade
