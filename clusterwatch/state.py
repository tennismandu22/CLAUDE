"""État persistant entre deux passages (state/state.json, versionné dans le dépôt)."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_STATE_PATH = Path("state/state.json")


@dataclass
class WalletFlows:
    """Cumuls servant au PnL : TAO financés (entrants) et sortis (sortants)."""

    funded_tao: float = 0.0
    withdrawn_tao: float = 0.0


@dataclass
class Counterparty:
    """Flux cumulés entre le cluster et une adresse externe."""

    to_cluster_tao: float = 0.0
    from_cluster_tao: float = 0.0
    stake_from_cluster_tao: float = 0.0
    stake_to_cluster_tao: float = 0.0


@dataclass
class State:
    last_block: int | None = None
    last_run: str | None = None
    cluster_value_tao: float | None = None
    wallet_values: dict[str, float] = field(default_factory=dict)
    flows: dict[str, WalletFlows] = field(default_factory=dict)
    seen_subnets: list[int] = field(default_factory=list)
    known_depositors: list[str] = field(default_factory=list)
    counterparties: dict[str, Counterparty] = field(default_factory=dict)
    candidates: dict[str, dict] = field(default_factory=dict)

    @property
    def is_initial(self) -> bool:
        return self.last_block is None

    def flows_for(self, wallet: str) -> WalletFlows:
        return self.flows.setdefault(wallet, WalletFlows())

    def counterparty(self, address: str) -> Counterparty:
        return self.counterparties.setdefault(address, Counterparty())

    def to_dict(self) -> dict:
        d = asdict(self)
        d["seen_subnets"] = sorted(set(self.seen_subnets))
        d["known_depositors"] = sorted(set(self.known_depositors))
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "State":
        return cls(
            last_block=d.get("last_block"),
            last_run=d.get("last_run"),
            cluster_value_tao=d.get("cluster_value_tao"),
            wallet_values=dict(d.get("wallet_values") or {}),
            flows={k: WalletFlows(**v) for k, v in (d.get("flows") or {}).items()},
            seen_subnets=list(d.get("seen_subnets") or []),
            known_depositors=list(d.get("known_depositors") or []),
            counterparties={k: Counterparty(**v) for k, v in (d.get("counterparties") or {}).items()},
            candidates=dict(d.get("candidates") or {}),
        )


def load_state(path: Path | str = DEFAULT_STATE_PATH) -> State:
    path = Path(path)
    if not path.exists():
        return State()
    with path.open(encoding="utf-8") as fh:
        return State.from_dict(json.load(fh))


def save_state(state: State, path: Path | str = DEFAULT_STATE_PATH) -> None:
    """Écriture atomique : fichier temporaire puis renommage."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state.to_dict(), fh, indent=2, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
