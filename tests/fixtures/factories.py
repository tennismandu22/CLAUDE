"""Fabriques de données fictives pour les tests."""

from datetime import datetime, timedelta, timezone

from clusterwatch.config import parse_config
from clusterwatch.models import StakeTransfer, Trade, Transfer

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)

# Adresses fictives au format SS58 (48 caractères commençant par 5).
W1 = "5" + "A" * 47
W2 = "5" + "B" * 47
DEP = "5" + "D" * 47
FEE = "5" + "F" * 47
EXT = "5" + "X" * 47
CAND = "5" + "C" * 47


def cfg(**thresholds):
    return parse_config(
        {
            "wallets": {"rang1": [W1], "rang2": [W2]},
            "deposit_addresses": [DEP],
            "infrastructure": [{"address": FEE, "label": "collecteur", "role": "fee_collector"}],
            "thresholds": thresholds,
        }
    )


def trade(side="buy", tao=10.0, netuid=1, block=100, wallet=W1, price=0.02,
          slippage=0.002, hotkey="HK", ts=None, validator_name=""):
    return Trade(
        wallet=wallet, block=block, timestamp=ts or T0 + timedelta(seconds=12 * block),
        netuid=netuid, side=side, tao=tao, alpha=tao / price, price=price,
        slippage=slippage, hotkey=hotkey, validator_name=validator_name,
    )


def transfer(sender, recipient, tao, block=100):
    return Transfer(sender=sender, recipient=recipient, tao=tao, block=block, timestamp=T0)


def stake_transfer(sender, recipient, tao, netuid=1, block=100):
    return StakeTransfer(sender=sender, recipient=recipient, netuid=netuid, alpha=tao * 50,
                         tao=tao, block=block, timestamp=T0)
