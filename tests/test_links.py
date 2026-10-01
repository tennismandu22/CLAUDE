from datetime import datetime, timedelta, timezone

import yaml

from clusterwatch import service
from clusterwatch.analysis.fingerprint import compute_fingerprint, similarity
from clusterwatch.analysis.links import (
    BIDIR, DEPOSIT, POSSIBLE, PROBABLE, STAKE, TRES_PROBABLE,
    Interaction, Profile, aggregate_interactions, is_deposit_like, is_hub, profile_from_transfers, score_link,
)
from clusterwatch.config import FingerprintRef
from tests.fixtures.factories import CAND, DEP, EXT, W1, W2, stake_transfer, transfer
from tests.test_fingerprint import synthetic_trader
from tests.test_pipeline import FakeTaostats, client_for

REF = FingerprintRef()
SEED = W1
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_aggregate_interactions():
    inter = aggregate_interactions(
        SEED,
        [transfer(SEED, W2, 5), transfer(W2, SEED, 2), transfer(SEED, EXT, 1), transfer(SEED, EXT, 1)],
        [stake_transfer(SEED, CAND, 3)],
    )
    assert inter[W2].bidirectional and inter[W2].tao_to == 5 and inter[W2].tao_from == 2
    assert inter[EXT].n_to == 2 and not inter[EXT].bidirectional
    assert inter[CAND].stake_n == 1


def test_profile_and_deposit_detection():
    tr = [transfer(EXT, DEP, 5, block=1), transfer(SEED, DEP, 3, block=2), transfer(DEP, "X", 8, block=3)]
    fields = profile_from_transfers(DEP, tr)
    assert fields["first_funder"] == EXT and fields["top_out_share"] == 1.0 and fields["counterparties"] == 3
    assert is_deposit_like(Profile(DEP, n_trades=0, **fields), 50)
    assert not is_deposit_like(Profile(DEP, n_trades=4, **fields), 50)  # un wallet qui trade n'est pas un dépôt
    assert is_hub(Profile("H", counterparties=80), 50)


def test_levels_require_independent_strong_evidence():
    strong2 = score_link(W2, None, None, Interaction(W2, n_to=1, tao_to=5, n_from=1, tao_from=2, stake_n=1, stake_tao=3),
                         Profile(W2), None, REF, SEED)
    assert strong2.level == TRES_PROBABLE and {STAKE, BIDIR} <= strong2.strong

    deposit_and_first = score_link(CAND, None, None, None, Profile(CAND, first_funder=SEED), DEP, REF, SEED)
    assert deposit_and_first.level == TRES_PROBABLE

    deposit_only = score_link(CAND, None, None, None, Profile(CAND), DEP, REF, SEED)
    assert deposit_only.level == POSSIBLE and deposit_only.strong == {DEPOSIT}

    one_transfer = score_link(EXT, None, None, Interaction(EXT, n_to=1, tao_to=1), Profile(EXT), None, REF, SEED)
    assert one_transfer.level == "faible"


def test_similar_fingerprint_supports_but_never_alone():
    a = compute_fingerprint(SEED, synthetic_trader(), REF)
    b = compute_fingerprint(CAND, synthetic_trader(clip=12), REF)
    c = compute_fingerprint(EXT, synthetic_trader(clip=100, n_subnets=2, hold_hours=200, buy_hour=3), REF)
    ok, n = similarity(a, b)
    assert n >= 4 and ok == n
    assert similarity(a, c)[0] < 3

    only_fp = score_link(CAND, a, None, None, Profile(CAND, fingerprint=b), None, REF, SEED)
    assert only_fp.level == POSSIBLE  # une empreinte proche seule ne suffit jamais
    with_bidir = score_link(CAND, a, None, Interaction(CAND, n_to=1, tao_to=1, n_from=1, tao_from=1),
                            Profile(CAND, fingerprint=b), None, REF, SEED)
    assert with_bidir.level == PROBABLE


def test_find_links_end_to_end(tmp_path):
    cdir = tmp_path / "config"
    cdir.mkdir()
    (cdir / "settings.yaml").write_text(yaml.safe_dump({"api": {"min_interval_s": 0}}))
    ws = service.Workspace(cdir, tmp_path / "state", tmp_path / "reports")

    fake = FakeTaostats()
    hot = "5" + "H" * 47
    fake.add_transfer(EXT, SEED, 50, block=1)            # financement initial du départ
    fake.add_transfer(SEED, W2, 10, block=5)             # W2 : aller-retour + stake → très probable
    fake.add_transfer(W2, SEED, 4, block=6)
    fake.add_trade(SEED, "UNDELEGATE", 2, 3, block=7, is_transfer=True, transfer_address={"ss58": W2})
    fake.add_transfer(SEED, DEP, 5, block=8)             # DEP : dépôt exchange (reverse tout vers hot)
    fake.add_transfer(DEP, hot, 5, block=9)
    fake.add_transfer(SEED, CAND, 1, block=10)           # CAND : financé en premier par le départ…
    fake.add_transfer(CAND, DEP, 3, block=11)            # … et envoie vers le même dépôt → très probable
    fake.add_trade(CAND, "DELEGATE", 1, 4, block=12)
    fake.balances.update({W2: 3 * 10**9, CAND: 2 * 10**9})

    r = service.find_links(ws, client_for(fake), SEED, now=NOW)
    by = {l.address: l for l in r.links}
    assert r.deposits == [DEP]
    assert by[W2].level == TRES_PROBABLE and by[W2].active
    assert by[CAND].level == TRES_PROBABLE
    assert EXT not in by or by[EXT].level != TRES_PROBABLE

    text = service.render_links(ws, r)
    assert "TRÈS PROBABLE (2)" in text and W2 in text and CAND in text
    assert "Adresses de dépôt exchange détectées" in text


def test_wallet_sending_back_to_seed_is_not_a_deposit():
    fields = profile_from_transfers(W2, [transfer(SEED, W2, 10, block=1), transfer(W2, SEED, 4, block=2)])
    p = Profile(W2, n_trades=0, **fields)
    assert not is_deposit_like(p, 50, seed=SEED)
    assert not is_deposit_like(Profile(W2, n_trades=0, top_out_address="X", top_out_share=1.0, counterparties=2),
                               50, seed=SEED, interaction=Interaction(W2, n_from=1, tao_from=1))
