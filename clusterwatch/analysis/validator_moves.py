"""Détection des changements de validateur (move_stake).

Un changement de validateur apparaît comme une vente et un achat du même
wallet, sur le même subnet, à quelques blocs d'écart, au même prix et sans
slippage. Ces paires ne sont pas des trades : on les retire du décompte et
on les liste à part.
"""

from __future__ import annotations

from collections import defaultdict

from ..models import Trade, ValidatorMove

_EPS = 1e-12


def _same_price(a: float, b: float, tol: float) -> bool:
    ref = max(abs(a), abs(b))
    return ref == 0 or abs(a - b) / ref <= tol + _EPS


def split_validator_moves(
    trades: list[Trade],
    block_window: int = 5,
    price_tolerance: float = 0.0001,
    slippage_max: float = 0.0,
) -> tuple[list[Trade], list[ValidatorMove]]:
    """Retourne (trades réels, changements de validateur)."""
    groups: dict[tuple[str, int], list[Trade]] = defaultdict(list)
    for t in trades:
        groups[(t.wallet, t.netuid)].append(t)

    paired: set[int] = set()
    moves: list[ValidatorMove] = []
    for group in groups.values():
        sells = [t for t in group if not t.is_buy and t.slippage <= slippage_max + _EPS]
        buys = [t for t in group if t.is_buy and t.slippage <= slippage_max + _EPS]
        for sell in sorted(sells, key=lambda t: t.block):
            best = None
            for buy in buys:
                if id(buy) in paired:
                    continue
                if abs(buy.block - sell.block) > block_window:
                    continue
                if not _same_price(sell.price, buy.price, price_tolerance):
                    continue
                score = (abs(buy.block - sell.block), abs(buy.alpha - sell.alpha))
                if best is None or score < best[0]:
                    best = (score, buy)
            if best is not None:
                buy = best[1]
                paired.update((id(sell), id(buy)))
                moves.append(ValidatorMove(sell=sell, buy=buy))

    real = [t for t in trades if id(t) not in paired]
    moves.sort(key=lambda m: m.sell.block)
    return real, moves
