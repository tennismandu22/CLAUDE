"""PnL par wallet : position actuelle + total sorti − total financé.

- financé : TAO reçus par transfert + stake alpha reçu par transfert (valeur TAO) ;
- sorti   : TAO envoyés + stake alpha envoyé (valeur TAO), sauf vers les
            collecteurs de frais (un frais est un coût, pas un retrait).

Un transfert entre deux wallets du cluster compte comme sortie pour l'un et
financement pour l'autre : il s'annule au niveau du cluster.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..models import StakeTransfer, Transfer
from ..state import WalletFlows


@dataclass(frozen=True)
class WalletPnl:
    wallet: str
    value_tao: float
    funded_tao: float
    withdrawn_tao: float

    @property
    def pnl_tao(self) -> float:
        return self.value_tao + self.withdrawn_tao - self.funded_tao


def update_flows(
    flows: dict[str, WalletFlows],
    wallets: Iterable[str],
    transfers: Iterable[Transfer],
    stake_transfers: Iterable[StakeTransfer] = (),
    not_withdrawals: Iterable[str] = (),
) -> None:
    """Ajoute aux cumuls les transferts d'un passage (déjà dédoublonnés)."""
    wallets = set(wallets)
    excluded = set(not_withdrawals)
    for t in list(transfers) + list(stake_transfers):
        if t.sender in wallets and t.recipient not in excluded:
            flows.setdefault(t.sender, WalletFlows()).withdrawn_tao += t.tao
        if t.recipient in wallets:
            flows.setdefault(t.recipient, WalletFlows()).funded_tao += t.tao


def compute_pnl(values: dict[str, float], flows: dict[str, WalletFlows]) -> list[WalletPnl]:
    out = []
    for wallet, value in values.items():
        f = flows.get(wallet, WalletFlows())
        out.append(WalletPnl(wallet, value, f.funded_tao, f.withdrawn_tao))
    return out
