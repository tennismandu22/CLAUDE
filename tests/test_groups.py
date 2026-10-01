import pytest
import yaml

from clusterwatch.config import ConfigError, list_groups, load_group
from clusterwatch.groups import add_address, remove_address
from clusterwatch.state import State
from tests.fixtures.factories import CAND, DEP, EXT, FEE, W1, W2

SETTINGS = {
    "infrastructure": [{"address": FEE, "label": "collecteur", "role": "fee_collector"}],
    "thresholds": {"big_trade_tao": 5},
}


@pytest.fixture
def cdir(tmp_path):
    (tmp_path / "settings.yaml").write_text(yaml.safe_dump(SETTINGS), encoding="utf-8")
    return tmp_path


def test_add_creates_group_and_merges_settings(cdir):
    add_address("perso", W1, "rang1", cdir)
    add_address("perso", DEP, "depot", cdir)
    assert list_groups(cdir) == ["perso"]
    cfg = load_group("perso", cdir)
    assert cfg.name == "perso" and cfg.rang1 == (W1,) and cfg.deposit_addresses == (DEP,)
    assert cfg.fee_collectors == {FEE}  # infrastructure commune héritée


def test_group_overrides_settings(cdir):
    add_address("g", W1, "rang1", cdir)
    path = cdir / "groups" / "g.yaml"
    data = yaml.safe_load(path.read_text())
    data["thresholds"] = {"big_trade_tao": 20}
    data["infrastructure"] = [{"address": EXT, "label": "propre au groupe"}]
    path.write_text(yaml.safe_dump(data))
    cfg = load_group("g", cdir)
    assert cfg.thresholds.big_trade_tao == 20
    assert {i.address for i in cfg.infrastructure} == {FEE, EXT}


def test_add_keeps_header_comments_and_rejects_duplicates(cdir):
    add_address("g", W1, "rang1", cdir)
    add_address("g", W2, "rang2", cdir)
    text = (cdir / "groups" / "g.yaml").read_text()
    assert text.startswith("# Groupe « g »")
    with pytest.raises(ConfigError):
        add_address("g", W2, "observation", cdir)


def test_invalid_inputs(cdir):
    with pytest.raises(ConfigError):
        add_address("g", "pas-une-adresse", "rang1", cdir)
    with pytest.raises(ConfigError):
        add_address("Mauvais Nom", W1, "rang1", cdir)
    with pytest.raises(ConfigError):
        add_address("g", W1, "rang9", cdir)


def test_remove_marks_rejected_and_manual_add_wins(cdir):
    state = State(auto_wallets={CAND: {"added_block": 1, "reasons": []}})
    add_address("g", W1, "rang1", cdir)
    add_address("g", W2, "rang2", cdir)
    remove_address("g", W2, cdir, state)
    remove_address("g", CAND, cdir, state)  # wallet ajouté automatiquement
    assert load_group("g", cdir).cluster == (W1,)
    assert sorted(state.rejected) == sorted([W2, CAND]) and state.auto_wallets == {}
    add_address("g", CAND, "observation", cdir, state)
    assert CAND not in state.rejected


def test_cannot_remove_last_wallet(cdir):
    add_address("g", W1, "rang1", cdir)
    with pytest.raises(ConfigError):
        remove_address("g", W1, cdir, State())


def test_auto_wallets_are_part_of_cluster(cdir):
    add_address("g", W1, "rang1", cdir)
    cfg = load_group("g", cdir, auto_wallets=[CAND])
    assert cfg.cluster == (W1, CAND) and cfg.rank_of(CAND) == "ajout auto"
