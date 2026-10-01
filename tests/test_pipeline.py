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


def test_new_wallet_history_and_auto_add():
    """Une adresse ajoutée en cours de route : son historique est collecté sans
    double comptage ni événements, ses wallets liés sont détectés, et un lien
    de confiance forte est ajouté automatiquement au suivi."""
    from dataclasses import replace

    c = cfg()
    c1 = replace(c, rang2=())  # au départ, seul W1 est suivi
    fake = FakeTaostats()
    fake.add_transfer(EXT, W1, 10, block=10)
    fake.add_transfer(W1, W2, 4, block=30)  # W2 n'est pas encore suivi : sortie pour W1
    fake.add_transfer(W2, CAND, 1, block=40)
    # CAND : transfert de stake avec W2 et 16 trades → confiance forte
    fake.add_trade(W2, "UNDELEGATE", 2, 7, block=50, is_transfer=True, transfer_address={"ss58": CAND})
    for i in range(8):
        fake.add_trade(CAND, "DELEGATE", 10, 1 + i, block=60 + 2 * i)
        fake.add_trade(CAND, "UNDELEGATE", 10, 1 + i, block=61 + 2 * i)
    fake.add_trade(W2, "DELEGATE", 8, 9, block=70)

    state = State()
    run_pass(client_for(fake), c1, state)
    assert state.flows[W1].withdrawn_tao == 4
    assert W2 not in state.tracked_addresses

    # L'utilisateur ajoute W2 au groupe
    fake.head = 2000
    r = run_pass(client_for(fake), c, state)
    assert r.new_addresses == [W2]
    assert r.trades == [] and not r.events.big_trades  # historique non présenté comme nouveau
    assert state.flows[W1].withdrawn_tao == 4  # pas de double comptage côté W1
    assert state.flows[W2].funded_tao == 4 and state.flows[W2].withdrawn_tao == 1 + 2
    assert r.events.new_subnets == []
    assert CAND in r.auto_added and CAND in state.auto_wallets
    assert r.has_anything

    # Le wallet ajouté automatiquement est suivi au passage suivant
    fake.head = 3000
    c3 = c.with_auto_wallets(state.auto_wallets)
    r3 = run_pass(client_for(fake), c3, state)
    assert r3.new_addresses == [CAND]
    assert CAND in r3.balance.per_wallet


def test_rejected_wallet_is_not_auto_added():
    c = cfg()
    fake = FakeTaostats()
    fake.add_trade(W1, "UNDELEGATE", 2, 7, block=50, is_transfer=True, transfer_address={"ss58": CAND})
    for i in range(8):
        fake.add_trade(CAND, "DELEGATE", 10, 1 + i, block=60 + 2 * i)
        fake.add_trade(CAND, "UNDELEGATE", 10, 1 + i, block=61 + 2 * i)
    state = State(rejected=[CAND])
    run_pass(client_for(fake), c, state)
    assert state.candidates[CAND]["confidence"] == "fort"
    assert CAND not in state.auto_wallets
