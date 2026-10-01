"""Chemins des endpoints Taostats et conversion des réponses en modèles internes.

C'est le SEUL module qui connaît les noms de champs de l'API. Si la doc
Taostats diffère (vérifier avec `python -m clusterwatch probe`), c'est ici
qu'il faut corriger.

Statut de vérification (voir README) :
  - /api/dtao/stake_balance/latest/v1  confirmé dans la doc
  - /api/dtao/pool/latest/v1           confirmé dans la doc
  - les autres chemins / champs         à confirmer avec `probe`
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Position, StakeTransfer, Trade, Transfer, rao_to_tao
from .client import TaostatsClient

BLOCK = "/api/block/v1"
DELEGATION = "/api/delegation/v1"
TRANSFER = "/api/transfer/v1"
ACCOUNT_LATEST = "/api/account/latest/v1"
STAKE_BALANCE_LATEST = "/api/dtao/stake_balance/latest/v1"
POOL_LATEST = "/api/dtao/pool/latest/v1"
SUBNET_IDENTITY = "/api/subnet/identity/v1"

ORDER_ASC = "block_number_asc"
ORDER_DESC = "block_number_desc"

# Unité du champ `slippage` renvoyé par l'API : "fraction" (0.001 = 0,1 %) ou "percent".
SLIPPAGE_UNIT = "fraction"


def _ss58(value) -> str:
    if isinstance(value, dict):
        return str(value.get("ss58") or "")
    return str(value or "")


def _ts(value) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    s = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _float(value, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None and value != "" else default
    except (TypeError, ValueError):
        return default


def _price(item: dict, tao: float, alpha: float) -> float:
    price = _float(item.get("alpha_price_in_tao"))
    if price > 1000:  # valeur renvoyée en RAO
        price /= 1e9
    if price <= 0 and alpha > 0:
        price = tao / alpha
    return price


def _slippage(item: dict) -> float:
    s = abs(_float(item.get("slippage")))
    return s / 100 if SLIPPAGE_UNIT == "percent" else s


def parse_delegation(item: dict) -> Trade | StakeTransfer:
    action = str(item.get("action", "")).upper()
    nominator = _ss58(item.get("nominator"))
    tao = rao_to_tao(item.get("amount"))
    alpha = rao_to_tao(item.get("alpha"))
    block = int(item.get("block_number"))
    ts = _ts(item.get("timestamp"))
    netuid = int(item.get("netuid") or 0)
    ext = str(item.get("extrinsic_id") or "")

    if item.get("is_transfer"):
        other = _ss58(item.get("transfer_address"))
        sender, recipient = (nominator, other) if action == "UNDELEGATE" else (other, nominator)
        return StakeTransfer(
            sender=sender, recipient=recipient, netuid=netuid, alpha=alpha, tao=tao,
            block=block, timestamp=ts, extrinsic_id=ext,
        )

    delegate = item.get("delegate")
    return Trade(
        wallet=nominator,
        block=block,
        timestamp=ts,
        netuid=netuid,
        side="buy" if action == "DELEGATE" else "sell",
        tao=tao,
        alpha=alpha,
        price=_price(item, tao, alpha),
        slippage=_slippage(item),
        hotkey=_ss58(delegate),
        validator_name=str(item.get("delegate_name") or ""),
        extrinsic_id=ext,
    )


def parse_transfer(item: dict) -> Transfer:
    return Transfer(
        sender=_ss58(item.get("from")),
        recipient=_ss58(item.get("to")),
        tao=rao_to_tao(item.get("amount")),
        block=int(item.get("block_number")),
        timestamp=_ts(item.get("timestamp")),
        extrinsic_id=str(item.get("extrinsic_id") or item.get("transaction_hash") or ""),
    )


def head_block(client: TaostatsClient) -> int:
    payload = client.get(BLOCK, {"limit": 1})
    return int(payload["data"][0]["block_number"])


def _range(block_start: int | None, block_end: int | None, order: str = ORDER_ASC) -> dict:
    return {"block_start": block_start, "block_end": block_end, "order": order}


def fetch_delegations(
    client: TaostatsClient,
    nominator: str,
    block_start: int | None,
    block_end: int | None,
    max_pages: int | None = None,
    order: str = ORDER_ASC,
) -> list[Trade | StakeTransfer]:
    params = {"nominator": nominator, **_range(block_start, block_end, order)}
    return [
        parse_delegation(i)
        for i in client.paginate(DELEGATION, params, cacheable=block_end is not None, max_pages=max_pages)
    ]


def fetch_transfers(
    client: TaostatsClient,
    block_start: int | None,
    block_end: int | None,
    address: str | None = None,
    to: str | None = None,
    max_pages: int | None = None,
    order: str = ORDER_ASC,
) -> list[Transfer]:
    params = {"address": address, "to": to, **_range(block_start, block_end, order)}
    return [
        parse_transfer(i)
        for i in client.paginate(TRANSFER, params, cacheable=block_end is not None, max_pages=max_pages)
    ]


def fetch_positions(client: TaostatsClient, coldkey: str) -> list[Position]:
    out = []
    for i in client.paginate(STAKE_BALANCE_LATEST, {"coldkey": coldkey}):
        alpha = rao_to_tao(i.get("balance"))
        if alpha <= 0:
            continue
        out.append(
            Position(
                coldkey=_ss58(i.get("coldkey")) or coldkey,
                hotkey=_ss58(i.get("hotkey")),
                netuid=int(i.get("netuid") or 0),
                alpha=alpha,
                tao_value=rao_to_tao(i.get("balance_as_tao")),
            )
        )
    return out


def fetch_free_balance(client: TaostatsClient, address: str) -> float:
    data = client.get(ACCOUNT_LATEST, {"address": address}).get("data") or []
    return rao_to_tao(data[0].get("balance_free")) if data else 0.0


def fetch_subnets(client: TaostatsClient) -> dict[int, dict]:
    """netuid -> {"name": str, "price": float (TAO par alpha)}."""
    subnets: dict[int, dict] = {}
    for i in client.paginate(POOL_LATEST, {}):
        netuid = int(i.get("netuid") or 0)
        price = _float(i.get("price"))
        if price > 1000:
            price /= 1e9
        subnets[netuid] = {"name": str(i.get("name") or ""), "price": price}
    if any(not s["name"] for s in subnets.values()):
        try:
            for i in client.paginate(SUBNET_IDENTITY, {}):
                netuid = int(i.get("netuid") or 0)
                if netuid in subnets and not subnets[netuid]["name"]:
                    subnets[netuid]["name"] = str(i.get("subnet_name") or "")
        except Exception:  # noms facultatifs : on n'échoue pas le passage pour eux
            pass
    return subnets


PROBES = {
    "block": (BLOCK, lambda w: {"limit": 1}),
    "delegation": (DELEGATION, lambda w: {"nominator": w, "limit": 2}),
    "transfer": (TRANSFER, lambda w: {"address": w, "limit": 2}),
    "account": (ACCOUNT_LATEST, lambda w: {"address": w}),
    "stake_balance": (STAKE_BALANCE_LATEST, lambda w: {"coldkey": w, "limit": 2}),
    "pool": (POOL_LATEST, lambda w: {"limit": 2}),
    "subnet_identity": (SUBNET_IDENTITY, lambda w: {"limit": 2}),
}
