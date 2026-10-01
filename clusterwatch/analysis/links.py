"""Recherche des adresses liées à une adresse de départ, avec niveau de certitude.

Chaque indice ajoute des points. Les indices « forts » sont des liens
structurels difficiles à expliquer entre deux personnes différentes :

- transfert de stake alpha (transfer_stake) entre les deux adresses ;
- flux TAO dans les deux sens ;
- même adresse de dépôt exchange (propre à un compte exchange) ;
- premier financement de l'une par l'autre.

La ressemblance d'empreinte de trading est un indice d'appui.

« très probable » exige au moins deux indices forts indépendants : une
attribution on-chain n'est jamais certaine à 100 %.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..config import FingerprintRef
from ..models import StakeTransfer, Transfer
from .fingerprint import Fingerprint, similarity

STAKE, BIDIR, DEPOSIT, FIRST_FUNDED, FUNDED_SEED = (
    "stake", "bidirectionnel", "depot_partage", "premier_financement", "a_finance_le_depart",
)
STRONG = {STAKE, BIDIR, DEPOSIT, FIRST_FUNDED, FUNDED_SEED}

TRES_PROBABLE, PROBABLE, POSSIBLE, FAIBLE = "très probable", "probable", "possible", "faible"
LEVEL_ORDER = {TRES_PROBABLE: 0, PROBABLE: 1, POSSIBLE: 2, FAIBLE: 3}


@dataclass
class Interaction:
    """Flux entre l'adresse de départ et une contrepartie."""

    address: str
    n_to: int = 0  # départ -> contrepartie
    tao_to: float = 0.0
    n_from: int = 0  # contrepartie -> départ
    tao_from: float = 0.0
    stake_n: int = 0
    stake_tao: float = 0.0

    @property
    def bidirectional(self) -> bool:
        return self.n_to > 0 and self.n_from > 0

    @property
    def rank_key(self) -> tuple:
        return (self.stake_n > 0, self.bidirectional, self.n_to + self.n_from + self.stake_n,
                self.tao_to + self.tao_from + self.stake_tao)


def aggregate_interactions(
    seed: str, transfers: list[Transfer], stakes: list[StakeTransfer]
) -> dict[str, Interaction]:
    out: dict[str, Interaction] = {}
    for t in transfers:
        if t.sender == seed and t.recipient != seed:
            i = out.setdefault(t.recipient, Interaction(t.recipient))
            i.n_to += 1
            i.tao_to += t.tao
        elif t.recipient == seed and t.sender != seed:
            i = out.setdefault(t.sender, Interaction(t.sender))
            i.n_from += 1
            i.tao_from += t.tao
    for s in stakes:
        other = s.recipient if s.sender == seed else s.sender if s.recipient == seed else None
        if other and other != seed:
            i = out.setdefault(other, Interaction(other))
            i.stake_n += 1
            i.stake_tao += s.tao
    return out


@dataclass
class Profile:
    """Ce qu'on sait d'une adresse examinée."""

    address: str
    n_trades: int = 0
    value_tao: float = 0.0
    last_trade: datetime | None = None
    first_funder: str | None = None
    counterparties: int = 0  # contreparties distinctes vues dans ses transferts
    top_out_address: str | None = None
    top_out_share: float = 0.0  # part de ses sorties TAO vers ce destinataire principal
    fingerprint: Fingerprint | None = None

    def is_active(self, now: datetime, days: int, min_tao: float) -> bool:
        recent = self.last_trade is not None and self.last_trade >= now - timedelta(days=days)
        return recent or self.value_tao >= min_tao


def profile_from_transfers(address: str, transfers: list[Transfer]) -> dict:
    """Champs de Profile déductibles des transferts (triés par bloc croissant)."""
    incoming = [t for t in transfers if t.recipient == address]
    outgoing = [t for t in transfers if t.sender == address]
    others = {t.sender for t in incoming} | {t.recipient for t in outgoing}
    out_by: dict[str, float] = {}
    for t in outgoing:
        out_by[t.recipient] = out_by.get(t.recipient, 0.0) + t.tao
    total_out = sum(out_by.values())
    top = max(out_by, key=out_by.get) if out_by else None
    return {
        "first_funder": min(incoming, key=lambda t: t.block).sender if incoming else None,
        "counterparties": len(others - {address}),
        "top_out_address": top,
        "top_out_share": out_by[top] / total_out if top and total_out > 0 else 0.0,
    }


def is_hub(p: Profile, limit: int) -> bool:
    return p.counterparties >= limit


def is_deposit_like(p: Profile, hub_limit: int, seed: str | None = None,
                    interaction: Interaction | None = None) -> bool:
    """Adresse de dépôt exchange probable : ne trade pas, peu d'expéditeurs,
    et reverse l'essentiel de ce qu'elle reçoit vers une seule adresse, qui
    n'est pas l'adresse de départ. Une adresse qui renvoie des TAO ou du stake
    à l'adresse de départ n'est pas un dépôt exchange."""
    if interaction is not None and (interaction.n_from or interaction.stake_n):
        return False
    return (
        p.n_trades == 0
        and p.top_out_address is not None
        and p.top_out_address != seed
        and p.top_out_share >= 0.8
        and p.counterparties <= min(15, hub_limit)
    )


@dataclass
class Link:
    address: str
    score: float = 0.0
    evidence: list[str] = field(default_factory=list)
    strong: set[str] = field(default_factory=set)
    profile: Profile | None = None
    active: bool = True
    level: str = FAIBLE


def _fmt(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ").replace(".", ",")


def score_link(
    address: str,
    seed_fp: Fingerprint | None,
    seed_first_funder: str | None,
    interaction: Interaction | None,
    profile: Profile | None,
    shared_deposit: str | None,
    ref: FingerprintRef,
    seed: str,
) -> Link:
    link = Link(address=address, profile=profile)

    def add(points: float, text: str, kind: str | None = None) -> None:
        link.score += points
        link.evidence.append(text)
        if kind:
            link.strong.add(kind)

    i = interaction
    if i and i.stake_n:
        add(3, f"{i.stake_n} transfert(s) de stake avec l'adresse de départ ({_fmt(i.stake_tao)} TAO)", STAKE)
    if i and i.bidirectional:
        add(3, f"flux TAO dans les deux sens : {i.n_to} envoi(s) ({_fmt(i.tao_to)} TAO) et "
               f"{i.n_from} retour(s) ({_fmt(i.tao_from)} TAO)", BIDIR)
    elif i and i.n_to:
        add(1 if i.n_to >= 2 else 0.5, f"a reçu {i.n_to} transfert(s) de l'adresse de départ ({_fmt(i.tao_to)} TAO)")
    elif i and i.n_from:
        add(1 if i.n_from >= 2 else 0.5, f"a envoyé {i.n_from} transfert(s) à l'adresse de départ ({_fmt(i.tao_from)} TAO)")
    if shared_deposit:
        add(3, f"envoie vers la même adresse de dépôt exchange ({shared_deposit[:6]}…{shared_deposit[-4:]})", DEPOSIT)
    if profile and profile.first_funder == seed:
        add(2, "son tout premier financement vient de l'adresse de départ", FIRST_FUNDED)
    if seed_first_funder == address:
        add(2, "a fait le tout premier financement de l'adresse de départ", FUNDED_SEED)

    fp = profile.fingerprint if profile else None
    if fp and seed_fp and fp.n_trades >= ref.min_trades and seed_fp.n_trades >= ref.min_trades:
        ok, n = similarity(seed_fp, fp)
        if n >= 4 and ok / n >= 0.8:
            add(2, f"empreinte de trading très proche ({ok}/{n} mesures concordantes)")
        elif n >= 4 and ok / n >= 0.6:
            add(1, f"empreinte de trading assez proche ({ok}/{n} mesures concordantes)")
        else:
            link.evidence.append(f"empreinte de trading différente ({ok}/{n} mesures concordantes)")

    if link.score >= 5 and len(link.strong) >= 2:
        link.level = TRES_PROBABLE
    elif link.score >= 4 and link.strong:
        link.level = PROBABLE
    elif link.score >= 2:
        link.level = POSSIBLE
    return link


def sort_links(links: list[Link]) -> list[Link]:
    return sorted(links, key=lambda l: (LEVEL_ORDER[l.level], -l.score, l.address))
