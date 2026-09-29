from clusterwatch.analysis.validator_moves import split_validator_moves
from tests.fixtures.factories import W1, W2, trade


def test_pair_same_subnet_same_price_zero_slippage_is_a_move():
    sell = trade("sell", 20, netuid=3, block=100, price=0.05, slippage=0.0, hotkey="OLD")
    buy = trade("buy", 20, netuid=3, block=100, price=0.05, slippage=0.0, hotkey="NEW")
    real = trade("buy", 7, netuid=3, block=110)
    trades, moves = split_validator_moves([sell, buy, real])
    assert trades == [real]
    assert len(moves) == 1 and moves[0].sell is sell and moves[0].buy is buy


def test_nonzero_slippage_is_a_real_trade():
    sell = trade("sell", 20, netuid=3, block=100, price=0.05, slippage=0.0)
    buy = trade("buy", 20, netuid=3, block=100, price=0.05, slippage=0.001)
    trades, moves = split_validator_moves([sell, buy])
    assert moves == [] and len(trades) == 2


def test_different_price_is_not_a_move():
    sell = trade("sell", 20, netuid=3, block=100, price=0.05, slippage=0.0)
    buy = trade("buy", 20, netuid=3, block=101, price=0.06, slippage=0.0)
    _, moves = split_validator_moves([sell, buy])
    assert moves == []


def test_different_subnet_or_wallet_is_not_a_move():
    sell = trade("sell", 20, netuid=3, block=100, price=0.05, slippage=0.0, wallet=W1)
    buy_other_subnet = trade("buy", 20, netuid=4, block=100, price=0.05, slippage=0.0, wallet=W1)
    buy_other_wallet = trade("buy", 20, netuid=3, block=100, price=0.05, slippage=0.0, wallet=W2)
    _, moves = split_validator_moves([sell, buy_other_subnet, buy_other_wallet])
    assert moves == []


def test_block_window_is_respected():
    sell = trade("sell", 20, netuid=3, block=100, price=0.05, slippage=0.0)
    buy = trade("buy", 20, netuid=3, block=120, price=0.05, slippage=0.0)
    assert split_validator_moves([sell, buy], block_window=5)[1] == []
    assert len(split_validator_moves([sell, buy], block_window=30)[1]) == 1


def test_each_trade_paired_at_most_once_closest_first():
    sell = trade("sell", 20, netuid=3, block=100, price=0.05, slippage=0.0)
    far = trade("buy", 20, netuid=3, block=104, price=0.05, slippage=0.0)
    near = trade("buy", 20, netuid=3, block=101, price=0.05, slippage=0.0)
    trades, moves = split_validator_moves([sell, far, near])
    assert len(moves) == 1 and moves[0].buy is near
    assert trades == [far]
