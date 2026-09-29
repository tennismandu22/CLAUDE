"""Bilan du cluster pour un passage : valeur, variation, flux de trading."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Trade, WalletSnapshot


@dataclass
class ClusterBalance:
    value_tao: float
    free_tao: float
    staked_tao: float
    previous_value_tao: float | None
    buys_tao: float
    sells_tao: float
    n_buys: int
    n_sells: int
    per_wallet: dict[str, float] = field(default_factory=dict)

    @property
    def variation_tao(self) -> float | None:
        if self.previous_value_tao is None:
            return None
        return self.value_tao - self.previous_value_tao

    @property
    def net_flow_tao(self) -> float:
        """Flux de trading net : ventes − achats (positif = TAO récupérés)."""
        return self.sells_tao - self.buys_tao

    @property
    def volume_tao(self) -> float:
        return self.buys_tao + self.sells_tao


def compute_balance(
    snapshots: list[WalletSnapshot],
    trades: list[Trade],
    previous_value_tao: float | None,
) -> ClusterBalance:
    """`trades` doit déjà exclure les changements de validateur."""
    buys = [t for t in trades if t.is_buy]
    sells = [t for t in trades if not t.is_buy]
    return ClusterBalance(
        value_tao=sum(s.total_tao for s in snapshots),
        free_tao=sum(s.free_tao for s in snapshots),
        staked_tao=sum(s.staked_tao for s in snapshots),
        previous_value_tao=previous_value_tao,
        buys_tao=sum(t.tao for t in buys),
        sells_tao=sum(t.tao for t in sells),
        n_buys=len(buys),
        n_sells=len(sells),
        per_wallet={s.address: s.total_tao for s in snapshots},
    )
