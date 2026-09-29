from datetime import timedelta

import pytest

from clusterwatch.analysis.discovery import (
    BIDIRECTIONAL, DEPOSIT, FAIBLE, FORT, MOYEN, STAKE, TAO_OUT,
    confidence, find_candidates, update_counterparties,
)
from clusterwatch.analysis.fingerprint import (
    compute_fingerprint, holding_durations_days, median, percentile,
)
from clusterwatch.config import FingerprintRef
from tests.fixtures.factories import CAND, DEP, EXT, FEE, T0, W1, cfg, stake_transfer, trade, transfer

REF = FingerprintRef(reference_validator_hotkeys=("TAOSTATS_HK",))


def synthetic_trader(n_pairs=20, clip=10.0, slippage=0.002, n_subnets=15, hold_hours=25,
                     buy_hour=8, sell_ratio=1.0, hotkey="TAOSTATS_HK"):
    """Trader fictif : n_pairs achats puis ventes du même alpha, un subnet par paire."""
    trades = []
    for i in range(n_pairs):
        day = T0.replace(hour=buy_hour) + timedelta(days=2 * i)
        netuid = 1 + i % n_subnets
        buy = trade("buy", clip, netuid=netuid, block=1000 * i, slippage=slippage, ts=day, hotkey=hotkey)
        sell = trade("sell", clip * sell_ratio, netuid=netuid, block=1000 * i + 500, slippage=slippage,
                     ts=day + timedelta(hours=hold_hours), hotkey=hotkey)
        sell = sell.__class__(**{**sell.__dict__, "alpha": buy.alpha})
        trades += [buy, sell]
    return trades


def by_key(fp):
    return {c.key: c for c in fp.criteria}


def test_stats_helpers():
    assert median([3, 1, 2]) == 2
    assert median([1, 2, 3, 4]) == 2.5
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 90) == pytest.approx(9.1)
    assert median([]) is None


def test_fifo_holding_durations():
    b1 = trade("buy", 10, netuid=1, block=1, price=0.1, ts=T0)
    b2 = trade("buy", 10, netuid=1, block=2, price=0.1, ts=T0 + timedelta(days=1))
    s = trade("sell", 15, netuid=1, block=3, price=0.1, ts=T0 + timedelta(days=2))
    # la vente de 150 alpha consomme le lot 1 (100, 2 j) puis 50 du lot 2 (1 j)
    assert holding_durations_days([b1, b2, s]) == pytest.approx([2.0, 1.0])


def test_reference_like_trader_matches_all_criteria():
    fp = compute_fingerprint(CAND, synthetic_trader(), REF)
    c = by_key(fp)
    assert all(c[k].ok for k in ["balance", "clip", "slippage", "subnets", "holding", "hours"])
    assert c["validator"].ok and c["validator"].informative
    assert fp.n_ok == 6 and fp.is_coherent(REF)


@pytest.mark.parametrize(
    "kwargs, failing",
    [
        ({"sell_ratio": 0.9}, "balance"),
        ({"clip": 50.0}, "clip"),
        ({"slippage": 0.01}, "slippage"),
        ({"n_subnets": 3}, "subnets"),
        ({"hold_hours": 2}, "holding"),
        ({"buy_hour": 3, "hold_hours": 24}, "hours"),
    ],
)
def test_each_criterion_can_fail(kwargs, failing):
    fp = compute_fingerprint(CAND, synthetic_trader(**kwargs), REF)
    c = by_key(fp)
    assert c[failing].ok is False
    assert fp.n_ok == 5  # un seul critère en échec : l'empreinte reste cohérente


def test_slippage_p90_limit():
    trades = synthetic_trader(slippage=0.002)
    # 10 % des trades avec un slippage de 2 % : médiane OK mais P90 > 0,85 %
    trades = [t.__class__(**{**t.__dict__, "slippage": 0.02}) if i % 5 == 0 else t
              for i, t in enumerate(trades)]
    assert by_key(compute_fingerprint(CAND, trades, REF))["slippage"].ok is False


def test_validator_is_never_eliminatory():
    fp = compute_fingerprint(CAND, synthetic_trader(hotkey="OTHER"), REF)
    assert by_key(fp)["validator"].ok is False
    assert fp.is_coherent(REF)


def test_validator_matched_by_name():
    trades = [t.__class__(**{**t.__dict__, "hotkey": "X", "validator_name": "Taostats & Corcel"})
              for t in synthetic_trader()]
    assert by_key(compute_fingerprint(CAND, trades, REF))["validator"].ok


def test_empty_history():
    fp = compute_fingerprint(CAND, [], REF)
    assert fp.n_trades == 0 and fp.n_ok == 0 and not fp.is_coherent(REF)


# --- niveau de confiance ---------------------------------------------------

GOOD = compute_fingerprint(CAND, synthetic_trader(), REF)
BAD = compute_fingerprint(CAND, synthetic_trader(clip=100, slippage=0.02, n_subnets=2), REF)
FEW = compute_fingerprint(CAND, synthetic_trader(n_pairs=7), REF)  # 14 trades


def test_confidence_levels():
    assert confidence({STAKE}, BAD, REF) == FORT
    assert confidence({TAO_OUT, BIDIRECTIONAL}, BAD, REF) == FORT
    assert confidence({DEPOSIT}, GOOD, REF) == MOYEN
    assert confidence({DEPOSIT}, BAD, REF) == FAIBLE
    assert confidence({TAO_OUT}, GOOD, REF) == FAIBLE


def test_less_than_15_trades_is_never_better_than_low():
    assert FEW.n_trades == 14
    assert confidence({STAKE, BIDIRECTIONAL}, FEW, REF) == FAIBLE
    assert confidence({DEPOSIT}, FEW, REF) == FAIBLE
    assert confidence({STAKE}, None, REF) == FAIBLE


def test_find_candidates_sources_and_exclusions():
    c = cfg()
    transfers = [transfer(W1, EXT, 5), transfer(W1, FEE, 1), transfer(W1, DEP, 3)]
    stakes = [stake_transfer(W1, CAND, 2)]
    deposits = [transfer(CAND, DEP, 1), transfer("5" + "K" * 47, DEP, 1), transfer(W1, DEP, 3)]
    cps = {}
    update_counterparties(cps, c, [transfer(EXT, W1, 1)] + transfers, stakes)
    found = find_candidates(c, transfers, stakes, deposits, {"5" + "K" * 47}, cps)
    assert found == {EXT: {TAO_OUT, BIDIRECTIONAL}, CAND: {STAKE, DEPOSIT}}
