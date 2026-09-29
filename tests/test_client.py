import httpx
import pytest

from clusterwatch.taostats.cache import DiskCache
from clusterwatch.taostats.client import TaostatsClient, TaostatsError
from clusterwatch.taostats.endpoints import parse_delegation, parse_transfer
from clusterwatch.models import StakeTransfer, Trade


def make_client(handler, tmp_path=None, **kw):
    sleeps = []
    http = httpx.Client(transport=httpx.MockTransport(handler), headers={"Authorization": "k"})
    client = TaostatsClient(
        api_key="k",
        min_interval_s=0,
        http=http,
        sleep=sleeps.append,
        cache=DiskCache(tmp_path) if tmp_path else None,
        **kw,
    )
    return client, sleeps


def test_retry_on_429_uses_retry_after():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"data": [1]})

    client, sleeps = make_client(handler)
    assert client.get("/x")["data"] == [1]
    assert 7.0 in sleeps


def test_gives_up_after_max_retries():
    client, _ = make_client(lambda req: httpx.Response(429), max_retries=2)
    with pytest.raises(TaostatsError):
        client.get("/x")


def test_missing_api_key(monkeypatch):
    monkeypatch.delenv("TAOSTATS_API_KEY", raising=False)
    with pytest.raises(TaostatsError):
        TaostatsClient()


def test_pagination_and_cache(tmp_path):
    seen = []

    def handler(req):
        page = int(req.url.params["page"])
        seen.append(page)
        return httpx.Response(
            200,
            json={"data": [page], "pagination": {"total_pages": 3, "next_page": page + 1 if page < 3 else None}},
        )

    client, _ = make_client(handler, tmp_path)
    assert list(client.paginate("/x", {"block_end": 10}, cacheable=True)) == [1, 2, 3]
    assert list(client.paginate("/x", {"block_end": 10}, cacheable=True)) == [1, 2, 3]
    assert seen == [1, 2, 3]  # le second passage vient du cache
    assert client.cache_hits == 3


def test_throttle_waits_between_calls():
    t = {"now": 0.0}
    sleeps = []
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    client = TaostatsClient(api_key="k", min_interval_s=12, http=http, sleep=sleeps.append, clock=lambda: t["now"])
    client.get("/a")
    t["now"] = 2.0
    client.get("/b")
    assert sleeps == [10.0]


def test_parse_delegation_trade_and_stake_transfer():
    base = {
        "block_number": 100,
        "timestamp": "2026-01-01T10:00:00Z",
        "nominator": {"ss58": "A"},
        "delegate": {"ss58": "H"},
        "netuid": 5,
        "amount": "10000000000",
        "alpha": "500000000000",
        "alpha_price_in_tao": "0.02",
        "slippage": "0.0015",
    }
    t = parse_delegation({**base, "action": "DELEGATE"})
    assert isinstance(t, Trade) and t.side == "buy" and t.tao == 10.0 and t.alpha == 500.0
    assert t.price == 0.02 and t.slippage == 0.0015

    st = parse_delegation({**base, "action": "UNDELEGATE", "is_transfer": True, "transfer_address": {"ss58": "B"}})
    assert isinstance(st, StakeTransfer) and st.sender == "A" and st.recipient == "B"


def test_parse_transfer_converts_rao():
    tr = parse_transfer(
        {"from": {"ss58": "A"}, "to": {"ss58": "B"}, "amount": "2500000000",
         "block_number": 5, "timestamp": "2026-01-01T00:00:00Z", "extrinsic_id": "5-1"}
    )
    assert tr.tao == 2.5 and tr.sender == "A" and tr.recipient == "B"
