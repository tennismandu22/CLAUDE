import pytest

from clusterwatch.analysis.pnl import compute_pnl, update_flows
from clusterwatch.state import WalletFlows
from tests.fixtures.factories import EXT, FEE, W1, W2, stake_transfer, transfer


def test_pnl_position_plus_withdrawn_minus_funded():
    flows = {}
    update_flows(
        flows,
        [W1],
        [transfer(EXT, W1, 100), transfer(W1, EXT, 30), transfer(EXT, W1, 20)],
    )
    (p,) = compute_pnl({W1: 110.0}, flows)
    assert p.funded_tao == 120 and p.withdrawn_tao == 30
    assert p.pnl_tao == pytest.approx(110 + 30 - 120)


def test_stake_transfers_count_as_flows():
    flows = {}
    update_flows(flows, [W1], [], [stake_transfer(EXT, W1, 50), stake_transfer(W1, EXT, 10)])
    (p,) = compute_pnl({W1: 45.0}, flows)
    assert p.pnl_tao == pytest.approx(45 + 10 - 50)


def test_internal_transfer_cancels_at_cluster_level():
    flows = {}
    update_flows(flows, [W1, W2], [transfer(EXT, W1, 100), transfer(W1, W2, 40)])
    pnls = {p.wallet: p for p in compute_pnl({W1: 60.0, W2: 40.0}, flows)}
    assert pnls[W1].pnl_tao == pytest.approx(0)
    assert pnls[W2].pnl_tao == pytest.approx(0)
    assert sum(p.pnl_tao for p in pnls.values()) == pytest.approx(0)


def test_fees_are_a_cost_not_a_withdrawal():
    flows = {}
    update_flows(flows, [W1], [transfer(EXT, W1, 10), transfer(W1, FEE, 0.005)], not_withdrawals=[FEE])
    (p,) = compute_pnl({W1: 9.995}, flows)
    assert p.withdrawn_tao == 0
    assert p.pnl_tao == pytest.approx(-0.005)


def test_flows_accumulate_across_runs():
    flows = {W1: WalletFlows(funded_tao=100, withdrawn_tao=5)}
    update_flows(flows, [W1], [transfer(W1, EXT, 15)])
    (p,) = compute_pnl({W1: 90.0}, flows)
    assert p.withdrawn_tao == 20 and p.pnl_tao == pytest.approx(10)


def test_wallet_without_flows():
    (p,) = compute_pnl({W1: 3.0}, {})
    assert p.pnl_tao == 3.0
