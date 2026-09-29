"""Formatage des nombres et libellés en français."""

from __future__ import annotations

from ..config import Config, short
from ..models import Trade


def num(x: float | None, digits: int = 2, signed: bool = False) -> str:
    if x is None:
        return "n/d"
    s = f"{x:+,.{digits}f}" if signed else f"{x:,.{digits}f}"
    return s.replace(",", " ").replace(".", ",")


def pct(fraction: float, digits: int = 2) -> str:
    return f"{num(fraction * 100, digits)} %"


def side(t: Trade) -> str:
    return "achat" if t.is_buy else "vente"


def subnet(netuid: int, subnets: dict[int, dict]) -> str:
    name = (subnets.get(netuid) or {}).get("name")
    return f"SN{netuid} {name}" if name else f"SN{netuid}"


def wallet(cfg: Config, address: str) -> str:
    return cfg.label(address)


__all__ = ["num", "pct", "side", "subnet", "wallet", "short"]
