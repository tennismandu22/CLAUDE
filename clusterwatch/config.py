"""Chargement et validation de la configuration.

- config/settings.yaml      : réglages communs (API, seuils, empreinte, infrastructure, Telegram)
- config/groups/<nom>.yaml  : un fichier par groupe suivi (wallets, adresses de dépôt,
                              infrastructure et seuils propres au groupe)
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

DEFAULT_CONFIG_DIR = Path("config")
SETTINGS_FILE = "settings.yaml"
GROUPS_DIR = "groups"
RANKS = ("rang1", "rang2", "observation")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class InfraAddress:
    address: str
    label: str
    role: str = "other"


@dataclass(frozen=True)
class ApiConfig:
    base_url: str = "https://api.taostats.io"
    min_interval_s: float = 12.0
    max_retries: int = 6
    page_limit: int = 200
    candidate_max_pages: int = 10
    max_candidates_per_run: int = 15
    cache_dir: str = "cache"


@dataclass(frozen=True)
class Thresholds:
    big_trade_tao: float = 5.0
    micro_fee_tao: float = 0.01
    watch_subnets: tuple[int, ...] = (118,)
    emptied_wallet_tao: float = 0.1
    validator_move_block_window: int = 5
    validator_move_price_tolerance: float = 0.0001
    validator_move_slippage_max: float = 0.0


@dataclass(frozen=True)
class FingerprintRef:
    min_trades: int = 15
    buy_sell_balance_tolerance_pct: float = 1.0
    clip_median_tao: tuple[float, float] = (4, 26)
    slippage_median_pct: tuple[float, float] = (0.09, 0.31)
    slippage_p90_max_pct: float = 0.85
    subnets_touched: tuple[int, int] = (12, 130)
    holding_median_days: tuple[float, float] = (0.4, 4)
    quiet_hours_utc: tuple[int, ...] = (1, 2, 3, 4, 5)
    peak_hours_utc: tuple[int, ...] = (7, 8, 9, 10, 11, 12, 20, 21)
    quiet_share_max: float = 0.08
    peak_share_min: float = 0.55
    min_criteria_ok: int = 5
    reference_validator_names: tuple[str, ...] = ("taostats",)
    reference_validator_hotkeys: tuple[str, ...] = ()


@dataclass(frozen=True)
class AutoAdd:
    """Ajout automatique au suivi des wallets liés de confiance « forte »."""

    enabled: bool = True
    max_per_run: int = 3


@dataclass(frozen=True)
class Config:
    """Configuration d'UN groupe, fusionnée avec les réglages communs."""

    rang1: tuple[str, ...]
    rang2: tuple[str, ...]
    observation: tuple[str, ...]
    deposit_addresses: tuple[str, ...]
    infrastructure: tuple[InfraAddress, ...]
    api: ApiConfig = field(default_factory=ApiConfig)
    start_block: int | None = None
    thresholds: Thresholds = field(default_factory=Thresholds)
    fingerprint: FingerprintRef = field(default_factory=FingerprintRef)
    telegram_enabled: bool = False
    auto_add: AutoAdd = field(default_factory=AutoAdd)
    name: str = "principal"
    # Wallets ajoutés automatiquement (stockés dans l'état du groupe, pas dans le YAML).
    auto_wallets: tuple[str, ...] = ()

    @property
    def cluster(self) -> tuple[str, ...]:
        """Tous les wallets suivis du groupe (rangs 1, 2, observation et ajouts auto)."""
        manual = self.rang1 + self.rang2 + self.observation
        return manual + tuple(a for a in self.auto_wallets if a not in manual)

    def with_auto_wallets(self, addresses) -> "Config":
        return replace(self, auto_wallets=tuple(addresses))

    def rank_of(self, address: str) -> str | None:
        if address in self.rang1:
            return "rang 1"
        if address in self.rang2:
            return "rang 2"
        if address in self.observation:
            return "observation"
        if address in self.auto_wallets:
            return "ajout auto"
        return None

    @property
    def infra_by_address(self) -> dict[str, InfraAddress]:
        return {i.address: i for i in self.infrastructure}

    @property
    def fee_collectors(self) -> set[str]:
        return {i.address for i in self.infrastructure if i.role == "fee_collector"}

    def label(self, address: str) -> str:
        """Étiquette lisible d'une adresse connue, sinon adresse abrégée."""
        rank = self.rank_of(address)
        if rank:
            return f"{short(address)} ({rank})"
        if address in self.deposit_addresses:
            return f"{short(address)} (dépôt exchange)"
        infra = self.infra_by_address.get(address)
        if infra:
            return f"{short(address)} ({infra.label})"
        return short(address)

    def is_ignored_counterparty(self, address: str) -> bool:
        return address in self.infra_by_address or address in self.deposit_addresses


def short(address: str) -> str:
    return f"{address[:6]}…{address[-4:]}" if len(address) > 12 else address


def _tuple(value, typ=str) -> tuple:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ConfigError(f"liste attendue, reçu {value!r}")
    return tuple(typ(v) for v in value)


def _pair(value, typ=float) -> tuple:
    t = _tuple(value, typ)
    if len(t) != 2 or t[0] > t[1]:
        raise ConfigError(f"intervalle [min, max] attendu, reçu {value!r}")
    return t


def is_valid_address(addr: str) -> bool:
    return isinstance(addr, str) and addr.startswith("5") and 46 <= len(addr) <= 48 and addr.isalnum()


def _validate_address(addr: str) -> None:
    if not is_valid_address(addr):
        raise ConfigError(f"adresse SS58 invalide : {addr!r}")


def parse_config(raw: dict, name: str = "principal", auto_wallets=(), require_wallets: bool = True) -> Config:
    wallets = raw.get("wallets") or {}
    rang1 = _tuple(wallets.get("rang1"))
    rang2 = _tuple(wallets.get("rang2"))
    observation = _tuple(wallets.get("observation"))
    deposits = _tuple(raw.get("deposit_addresses"))
    infra = tuple(
        InfraAddress(address=str(i["address"]), label=str(i.get("label", "")), role=str(i.get("role", "other")))
        for i in (raw.get("infrastructure") or [])
    )

    all_addrs = list(rang1 + rang2 + observation + deposits) + [i.address for i in infra]
    for a in all_addrs:
        _validate_address(a)
    dupes = {a for a in all_addrs if all_addrs.count(a) > 1}
    if dupes:
        raise ConfigError(f"adresse(s) présente(s) dans plusieurs catégories : {sorted(dupes)}")
    if require_wallets and not rang1 + rang2 + observation:
        raise ConfigError(f"groupe {name!r} : aucun wallet suivi")

    a = raw.get("api") or {}
    api = ApiConfig(
        base_url=str(a.get("base_url", ApiConfig.base_url)).rstrip("/"),
        min_interval_s=float(a.get("min_interval_s", ApiConfig.min_interval_s)),
        max_retries=int(a.get("max_retries", ApiConfig.max_retries)),
        page_limit=int(a.get("page_limit", ApiConfig.page_limit)),
        candidate_max_pages=int(a.get("candidate_max_pages", ApiConfig.candidate_max_pages)),
        max_candidates_per_run=int(a.get("max_candidates_per_run", ApiConfig.max_candidates_per_run)),
        cache_dir=str(a.get("cache_dir", ApiConfig.cache_dir)),
    )

    t = raw.get("thresholds") or {}
    d = Thresholds()
    thresholds = Thresholds(
        big_trade_tao=float(t.get("big_trade_tao", d.big_trade_tao)),
        micro_fee_tao=float(t.get("micro_fee_tao", d.micro_fee_tao)),
        watch_subnets=_tuple(t.get("watch_subnets", list(d.watch_subnets)), int),
        emptied_wallet_tao=float(t.get("emptied_wallet_tao", d.emptied_wallet_tao)),
        validator_move_block_window=int(t.get("validator_move_block_window", d.validator_move_block_window)),
        validator_move_price_tolerance=float(
            t.get("validator_move_price_tolerance", d.validator_move_price_tolerance)
        ),
        validator_move_slippage_max=float(t.get("validator_move_slippage_max", d.validator_move_slippage_max)),
    )

    f = raw.get("fingerprint") or {}
    fd = FingerprintRef()
    fingerprint = FingerprintRef(
        min_trades=int(f.get("min_trades", fd.min_trades)),
        buy_sell_balance_tolerance_pct=float(
            f.get("buy_sell_balance_tolerance_pct", fd.buy_sell_balance_tolerance_pct)
        ),
        clip_median_tao=_pair(f.get("clip_median_tao", list(fd.clip_median_tao))),
        slippage_median_pct=_pair(f.get("slippage_median_pct", list(fd.slippage_median_pct))),
        slippage_p90_max_pct=float(f.get("slippage_p90_max_pct", fd.slippage_p90_max_pct)),
        subnets_touched=_pair(f.get("subnets_touched", list(fd.subnets_touched)), int),
        holding_median_days=_pair(f.get("holding_median_days", list(fd.holding_median_days))),
        quiet_hours_utc=_tuple(f.get("quiet_hours_utc", list(fd.quiet_hours_utc)), int),
        peak_hours_utc=_tuple(f.get("peak_hours_utc", list(fd.peak_hours_utc)), int),
        quiet_share_max=float(f.get("quiet_share_max", fd.quiet_share_max)),
        peak_share_min=float(f.get("peak_share_min", fd.peak_share_min)),
        min_criteria_ok=int(f.get("min_criteria_ok", fd.min_criteria_ok)),
        reference_validator_names=tuple(
            s.lower() for s in _tuple(f.get("reference_validator_names", list(fd.reference_validator_names)))
        ),
        reference_validator_hotkeys=_tuple(f.get("reference_validator_hotkeys")),
    )

    start_block = (raw.get("collection") or {}).get("start_block")
    aa = raw.get("auto_add") or {}
    return Config(
        rang1=rang1,
        rang2=rang2,
        observation=observation,
        deposit_addresses=deposits,
        infrastructure=infra,
        api=api,
        start_block=int(start_block) if start_block is not None else None,
        thresholds=thresholds,
        fingerprint=fingerprint,
        telegram_enabled=bool((raw.get("telegram") or {}).get("enabled", False)),
        auto_add=AutoAdd(
            enabled=bool(aa.get("enabled", AutoAdd.enabled)),
            max_per_run=int(aa.get("max_per_run", AutoAdd.max_per_run)),
        ),
        name=name,
        auto_wallets=tuple(auto_wallets),
    )


def _read_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} : contenu YAML inattendu")
    return data


def merge_settings(settings: dict, group: dict) -> dict:
    """Fusionne réglages communs et fichier de groupe (le groupe l'emporte)."""
    merged = dict(settings)
    for key, value in group.items():
        if key == "infrastructure":
            merged[key] = list(settings.get(key) or []) + list(value or [])
        elif key in ("thresholds", "fingerprint", "api", "auto_add") and isinstance(value, dict):
            merged[key] = {**(settings.get(key) or {}), **value}
        else:
            merged[key] = value
    return merged


def groups_dir(config_dir: Path | str = DEFAULT_CONFIG_DIR) -> Path:
    return Path(config_dir) / GROUPS_DIR


def list_groups(config_dir: Path | str = DEFAULT_CONFIG_DIR) -> list[str]:
    d = groups_dir(config_dir)
    return sorted(p.stem for p in d.glob("*.yaml")) if d.exists() else []


def load_settings(config_dir: Path | str = DEFAULT_CONFIG_DIR) -> dict:
    path = Path(config_dir) / SETTINGS_FILE
    if not path.exists():
        raise ConfigError(f"fichier de réglages introuvable : {path}")
    return _read_yaml(path)


def load_group(name: str, config_dir: Path | str = DEFAULT_CONFIG_DIR, auto_wallets=()) -> Config:
    path = groups_dir(config_dir) / f"{name}.yaml"
    if not path.exists():
        raise ConfigError(f"groupe inconnu : {name!r} (fichier {path} absent)")
    return parse_config(merge_settings(load_settings(config_dir), _read_yaml(path)), name, auto_wallets)


def settings_config(config_dir: Path | str = DEFAULT_CONFIG_DIR) -> Config:
    """Réglages communs seuls (pour probe / fingerprint), sans groupe."""
    return parse_config(load_settings(config_dir), name="(réglages)", require_wallets=False)
