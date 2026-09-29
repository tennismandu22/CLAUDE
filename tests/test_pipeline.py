"""Passage complet contre une fausse API Taostats (données fictives)."""

from datetime import datetime, timedelta, timezone

import httpx

from clusterwatch.pipeline import run_pass
from clusterwatch.report.markdown import render_markdown, write_report
from clusterwatch.report.telegram_text import render_telegram, split_message
from clusterwatch.state import State, load_state, save_state
from clusterwatch.taostats.client import TaostatsClient
from tests.fixtures.factories import CAND, DEP, EXT, FEE, W1, W2, cfg

RAO = 10**9


class FakeTaostats:
    def __init__(self):
        self.head = 1000
        self.delegations = []  # dicts au format API
        self.transfers = []
        self.balances = {W1: 5 * RAO, W2: 0}
        self.stakes = {W1: [(1, 100 * RAO, 2 * RAO)], W2: []}

    def add_trade(self, wallet, action, tao, netuid, block, slippage="0.002", price="0.02", **extra):
        self.delegations.append({
            "block_number": block, "timestamp": self.ts(block), "action": action,
            "nominator": {"ss58": wallet}, "delegate": {"ss58": "HK"}, "netuid": netuid,
            "amount": str(int(tao * RAO)), "alpha": str(int(tao / float(price) * RAO)),
            "alpha_price_in_tao": price, "slippage": slippage, "extrinsic_id": f"{block}-{len(self.delegations)}",
            **extra,
        })

    def add_transfer(self, sender, recipient, tao, block):
        self.transfers.append({
            "from": {"ss58": sender}, "to": {"ss58": recipient}, "amount": str(int(tao * RAO)),
            "block_number": block, "timestamp": self.ts(block), "extrinsic_id": f"{block}-t{len(self.transfers)}",
        })

    @staticmethod
    def ts(block):
        t = datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(seconds=12 * block)
        return t.isoformat().replace("+00:00", "Z")

    def _range(self, items, q):
        lo = int(q.get("block_start", 0))
        hi = int(q.get("block_end", 10**12))
        return [i for i in items if lo <= i["block_number"] <= hi]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        q, path = dict(req.url.params), req.url.path
        if path == "/api/block/v1":
            data = [{"block_number": self.head}]
        elif path == "/api/dtao/pool/latest/v1":
            data = [{"netuid": n, "name": f"Net{n}", "price": "0.02"} for n in (1, 7, 118)]
        elif path == "/api/delegation/v1":
            data = [d for d in self._range(self.delegations, q) if d["nominator"]["ss58"] == q["nominator"]]
        elif path == "/api/transfer/v1":
            items = self._range(self.transfers, q)
            if "address" in q:
                data = [t for t in items if q["address"] in (t["from"]["ss58"], t["to"]["ss58"])]
            else:
                data = [t for t in items if t["to"]["ss58"] == q["to"]]
        elif path == "/api/account/latest/v1":
            data = [{"balance_free": str(self.balances.get(q["address"], 0))}]
        elif path == "/api/dtao/stake_balance/latest/v1":
            data = [{"hotkey": {"ss58": "HK"}, "netuid": n, "balance": str(a), "balance_as_tao": str(t)}
                    for n, a, t in self.stakes.get(q["coldkey"], [])]
        else:
            return httpx.Response(404)
        return httpx.Response(200, json={"data": data, "pagination": {"total_pages": 1, "next_page": None}})


def client_for(fake):
    http = httpx.Client(transport=httpx.MockTransport(fake))
    return TaostatsClient(api_key="k", min_interval_s=0, http=http, sleep=lambda s: None)


def test_two_passes(tmp_path):
    c = cfg()
    fake = FakeTaostats()
    fake.add_transfer(EXT, W1, 10, block=10)
    fake.add_trade(W1, "DELEGATE", 3, 1, block=20)
    state = State()

    r1 = run_pass(client_for(fake), c, state)
    assert r1.initial and not r1.has_anything
    assert r1.balance.value_tao == 7.0  # 5 libres + 2 en position
    assert state.last_block == 1000 and state.seen_subnets == [1]
    assert "Premier passage" in render_markdown(c, r1)
    save_state(state, tmp_path / "state.json")

    # Nouveaux événements après le premier passage
    fake.head = 2000
    fake.add_trade(W1, "DELEGATE", 12, 118, block=1100)
    fake.add_trade(W1, "UNDELEGATE", 2, 7, block=1110)
    fake.add_trade(W1, "UNDELEGATE", 20, 1, block=1200, slippage="0", price="0.05")  # changement de validateur
    fake.add_trade(W1, "DELEGATE", 20, 1, block=1200, slippage="0", price="0.05")
    fake.add_transfer(W1, FEE, 0.001, block=1300)  # micro-frais masqué
    fake.add_transfer(W1, CAND, 4, block=1310)
    fake.add_transfer(CAND, DEP, 4, block=1320)
    fake.add_trade(W1, "UNDELEGATE", 1, 1, block=1400, is_transfer=True, transfer_address={"ss58": W2})
    fake.balances[W1] = 1 * RAO
    fake.stakes[W1].append((118, 600 * RAO, 12 * RAO))

    state = load_state(tmp_path / "state.json")
    r2 = run_pass(client_for(fake), c, state, now=datetime(2026, 9, 29, 18, 0, tzinfo=timezone.utc))
    ev = r2.events
    assert not r2.initial and r2.has_anything
    assert [t.tao for t in ev.big_trades] == [12.0]
    assert ev.small_trades.n_sells == 1
    assert len(ev.validator_moves) == 1
    assert [t.recipient for t in ev.tao_transfers] == [CAND]
    assert len(ev.stake_transfers) == 1 and ev.stake_transfers[0].recipient == W2
    assert ev.new_subnets == [118]
    assert [t.netuid for t in ev.watched_subnet_trades] == [118]
    assert r2.balance.variation_tao == r2.balance.value_tao - 7.0
    assert r2.balance.net_flow_tao == 2 - 12

    # Candidat : destinataire d'un transfert + expéditeur vers un dépôt, < 15 trades → faible
    assert [x.address for x in r2.candidates] == [CAND]
    assert r2.candidates[0].confidence == "faible"
    assert CAND in state.known_depositors

    # PnL : W1 a reçu 10, envoyé 4 + stake 1 vers W2 ; W2 a reçu le stake
    pnls = {p.wallet: p for p in r2.pnls}
    assert pnls[W1].funded_tao == 10 and pnls[W1].withdrawn_tao == 5
    assert pnls[W2].funded_tao == 1

    md = render_markdown(c, r2)
    assert "Trades ≥ 5 TAO" in md and "SN118 Net118" in md and "Changements de validateur" in md
    tg = render_telegram(c, r2)
    assert "Candidats" in tg and len(tg) < 4096
    assert write_report(c, r2, tmp_path / "reports").name == "2026-09-29_1800.md"

    # Troisième passage sans activité : rien à signaler
    fake.head = 2100
    r3 = run_pass(client_for(fake), c, state)
    assert not r3.has_anything


def test_split_message():
    text = "\n".join(["x" * 100] * 100)
    parts = split_message(text, limit=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert "\n".join(parts) == text
