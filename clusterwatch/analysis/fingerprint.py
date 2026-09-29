"""Empreinte de trading d'une adresse et comparaison à la référence du cluster."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field

from ..config import FingerprintRef
from ..models import Trade


def median(xs: list[float]) -> float | None:
    return percentile(xs, 50)


def percentile(xs: list[float], p: float) -> float | None:
    """Percentile avec interpolation linéaire (méthode « linear » de numpy)."""
    if not xs:
        return None
    s = sorted(xs)
    k = (len(s) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def holding_durations_days(trades: list[Trade]) -> list[float]:
    """Durées de détention (jours) par appariement FIFO achat→vente, par subnet."""
    lots: dict[int, deque] = defaultdict(deque)
    durations: list[float] = []
    for t in sorted(trades, key=lambda t: (t.block, 0 if t.is_buy else 1)):
        if t.is_buy:
            lots[t.netuid].append([t.alpha, t.timestamp])
            continue
        remaining = t.alpha
        queue = lots[t.netuid]
        while remaining > 1e-12 and queue:
            lot = queue[0]
            used = min(lot[0], remaining)
            durations.append((t.timestamp - lot[1]).total_seconds() / 86400)
            lot[0] -= used
            remaining -= used
            if lot[0] <= 1e-12:
                queue.popleft()
    return durations


@dataclass
class Criterion:
    key: str
    label: str
    measured: str
    expected: str
    ok: bool | None  # None = non mesurable
    informative: bool = False


@dataclass
class Fingerprint:
    address: str
    n_trades: int
    criteria: list[Criterion] = field(default_factory=list)
    hours: list[int] = field(default_factory=lambda: [0] * 24)

    @property
    def core(self) -> list[Criterion]:
        return [c for c in self.criteria if not c.informative]

    @property
    def n_ok(self) -> int:
        return sum(1 for c in self.core if c.ok)

    def is_coherent(self, ref: FingerprintRef) -> bool:
        return self.n_trades >= ref.min_trades and self.n_ok >= ref.min_criteria_ok


def _fmt(x: float | None, digits: int = 2, unit: str = "") -> str:
    return "n/d" if x is None else f"{x:.{digits}f}{unit}".replace(".", ",")


def _in(x: float | None, bounds) -> bool | None:
    return None if x is None else bounds[0] <= x <= bounds[1]


def _uses_reference_validator(t: Trade, ref: FingerprintRef) -> bool:
    if t.hotkey and t.hotkey in ref.reference_validator_hotkeys:
        return True
    name = t.validator_name.lower()
    return any(n in name for n in ref.reference_validator_names) if name else False


def compute_fingerprint(address: str, trades: list[Trade], ref: FingerprintRef) -> Fingerprint:
    """`trades` doit déjà exclure les changements de validateur."""
    fp = Fingerprint(address=address, n_trades=len(trades))
    buys = [t for t in trades if t.is_buy]
    buy_tao = sum(t.tao for t in buys)
    sell_tao = sum(t.tao for t in trades if not t.is_buy)

    # 1. Équilibre achats / ventes
    top = max(buy_tao, sell_tao)
    imbalance = abs(buy_tao - sell_tao) / top * 100 if top > 0 else None
    fp.criteria.append(Criterion(
        "balance", "Équilibre achats/ventes",
        f"achats {_fmt(buy_tao)} / ventes {_fmt(sell_tao)} TAO (écart {_fmt(imbalance)} %)",
        f"écart ≤ {_fmt(ref.buy_sell_balance_tolerance_pct)} %",
        None if imbalance is None else imbalance <= ref.buy_sell_balance_tolerance_pct,
    ))

    # 2. Clip médian
    clip = median([t.tao for t in trades])
    fp.criteria.append(Criterion(
        "clip", "Clip médian", _fmt(clip, unit=" TAO"),
        f"{_fmt(ref.clip_median_tao[0], 0)}–{_fmt(ref.clip_median_tao[1], 0)} TAO",
        _in(clip, ref.clip_median_tao),
    ))

    # 3. Slippage médian et P90 (en %)
    slips = [t.slippage * 100 for t in trades]
    s_med, s_p90 = median(slips), percentile(slips, 90)
    ok = None
    if s_med is not None:
        ok = bool(_in(s_med, ref.slippage_median_pct)) and s_p90 < ref.slippage_p90_max_pct
    fp.criteria.append(Criterion(
        "slippage", "Slippage",
        f"médian {_fmt(s_med)} %, P90 {_fmt(s_p90)} %",
        f"médian {_fmt(ref.slippage_median_pct[0])}–{_fmt(ref.slippage_median_pct[1])} %, "
        f"P90 < {_fmt(ref.slippage_p90_max_pct)} %",
        ok,
    ))

    # 4. Subnets touchés
    n_subnets = len({t.netuid for t in trades})
    fp.criteria.append(Criterion(
        "subnets", "Subnets touchés", str(n_subnets),
        f"{ref.subnets_touched[0]}–{ref.subnets_touched[1]}",
        _in(n_subnets, ref.subnets_touched) if trades else None,
    ))

    # 5. Détention médiane
    hold = median(holding_durations_days(trades))
    fp.criteria.append(Criterion(
        "holding", "Détention médiane", _fmt(hold, unit=" j"),
        f"{_fmt(ref.holding_median_days[0], 1)}–{_fmt(ref.holding_median_days[1], 1)} j",
        _in(hold, ref.holding_median_days),
    ))

    # 6. Profil horaire UTC
    for t in trades:
        fp.hours[t.timestamp.hour] += 1
    if trades:
        quiet = sum(fp.hours[h] for h in ref.quiet_hours_utc) / len(trades)
        peak = sum(fp.hours[h] for h in ref.peak_hours_utc) / len(trades)
        fp.criteria.append(Criterion(
            "hours", "Heures UTC",
            f"creux {_fmt(quiet * 100, 0)} %, pics {_fmt(peak * 100, 0)} %",
            f"creux ≤ {_fmt(ref.quiet_share_max * 100, 0)} %, pics ≥ {_fmt(ref.peak_share_min * 100, 0)} %",
            quiet <= ref.quiet_share_max and peak >= ref.peak_share_min,
        ))
    else:
        fp.criteria.append(Criterion("hours", "Heures UTC", "n/d", "", None))

    # 7. Validateur (informatif uniquement, jamais éliminatoire)
    ref_tao = sum(t.tao for t in buys if _uses_reference_validator(t, ref))
    share = ref_tao / buy_tao * 100 if buy_tao > 0 else None
    fp.criteria.append(Criterion(
        "validator", "Validateur de référence (informatif)",
        f"{_fmt(share, 0)} % des achats",
        ", ".join(ref.reference_validator_names) or "—",
        None if share is None else share > 0,
        informative=True,
    ))
    return fp
