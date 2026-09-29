import pytest

from clusterwatch.analysis.balance import compute_balance
from clusterwatch.analysis.events import detect_events
from clusterwatch.models import Position, WalletSnapshot
from tests.fixtures.factories import EXT, FEE, W1, W2, cfg, stake_transfer, trade, transfer


def snap(addr, free=0.0, positions=()):
    return WalletSnapshot(address=addr, free_tao=free, positions=list(positions))


def pos(addr, netuid, tao):
    return Position(coldkey=addr, hotkey="HK", netuid=netuid, alpha=tao * 50, tao_value=tao)


def test_balance_net_flow_and_volume():
    snaps = [snap(W1, 10, [pos(W1, 1, 40)]), snap(W2, 5)]
    trades = [trade("buy", 10), trade("sell", 12), trade("sell", 3)]
    b = compute_balance(snaps, trades, previous_value_tao=50)
    assert b.value_tao == 55 and b.variation_tao == 5
    assert b.net_flow_tao == pytest.approx(5) and b.volume_tao == pytest.approx(25)
    assert compute_balance(snaps, [], None).variation_tao is None


def run(c, **kw):
    args = dict(trades=[], validator_moves=[], transfers=[], stake_transfers=[],
                snapshots=[], seen_subnets=set(), previous_wallet_values={}, initial_run=False)
    args.update(kw)
    return detect_events(c, **args)


def test_big_and_small_trades():
    ev = run(cfg(), trades=[trade("buy", 5.0), trade("buy", 4.99), trade("sell", 1.0)])
    assert [t.tao for t in ev.big_trades] == [5.0]
    assert ev.small_trades.n_buys == 1 and ev.small_trades.n_sells == 1


def test_micro_fee_to_collector_hidden_but_not_other_transfers():
    ev = run(cfg(), transfers=[transfer(W1, FEE, 0.005), transfer(W1, FEE, 0.5), transfer(W1, EXT, 0.005)])
    assert [(t.recipient, t.tao) for t in ev.tao_transfers] == [(FEE, 0.5), (EXT, 0.005)]


def test_watched_subnet_and_new_subnet():
    ev = run(
        cfg(),
        trades=[trade("buy", 1, netuid=118), trade("buy", 1, netuid=7)],
        stake_transfers=[stake_transfer(W1, EXT, 2, netuid=118)],
        snapshots=[snap(W1, 0, [pos(W1, 7, 1), pos(W1, 118, 1)])],
        seen_subnets={7},
    )
    assert [t.netuid for t in ev.watched_subnet_trades] == [118]
    assert len(ev.watched_subnet_stake_transfers) == 1
    assert ev.new_subnets == [118]


def test_no_new_subnet_on_initial_run():
    ev = run(cfg(), snapshots=[snap(W1, 0, [pos(W1, 9, 1)])], initial_run=True)
    assert ev.new_subnets == []


def test_awakened_wallet():
    ev = run(cfg(), trades=[trade("buy", 1, wallet=W2)], previous_wallet_values={W1: 0.0, W2: 0.01})
    assert ev.awakened_wallets == [W2]
    ev = run(cfg(), snapshots=[snap(W1, 3)], previous_wallet_values={W1: 0.0})
    assert ev.awakened_wallets == [W1]


def test_nothing_to_report():
    assert not run(cfg()).has_anything
