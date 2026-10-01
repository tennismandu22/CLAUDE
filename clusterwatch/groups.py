"""Gestion des groupes suivis : création, ajout et retrait d'adresses.

Les fichiers config/groups/<nom>.yaml sont réécrits par ces commandes ; les
lignes de commentaire en tête de fichier sont conservées.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .config import DEFAULT_CONFIG_DIR, RANKS, ConfigError, groups_dir, is_valid_address
from .state import State

DEPOSIT = "depot"
GROUP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


def _group_file(name: str, config_dir) -> Path:
    if not GROUP_NAME_RE.match(name):
        raise ConfigError(f"nom de groupe invalide : {name!r} (minuscules, chiffres, - et _)")
    return groups_dir(config_dir) / f"{name}.yaml"


def _read(path: Path) -> tuple[list[str], dict]:
    if not path.exists():
        return [], {}
    text = path.read_text(encoding="utf-8")
    header = []
    for line in text.splitlines():
        if line.startswith("#") or (header and not line.strip()):
            header.append(line)
        else:
            break
    while header and not header[-1].strip():
        header.pop()
    return header, yaml.safe_load(text) or {}


def _write(path: Path, header: list[str], data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(("\n".join(header) + "\n\n" if header else "") + body, encoding="utf-8")
    tmp.replace(path)


def _categories(data: dict) -> dict[str, list]:
    wallets = data.get("wallets") or {}
    cats = {r: list(wallets.get(r) or []) for r in RANKS}
    cats[DEPOSIT] = list(data.get("deposit_addresses") or [])
    return cats


def _store(data: dict, cats: dict[str, list]) -> dict:
    out = dict(data)
    out["wallets"] = {r: cats[r] for r in RANKS if cats[r]}
    if cats[DEPOSIT]:
        out["deposit_addresses"] = cats[DEPOSIT]
    else:
        out.pop("deposit_addresses", None)
    return out


def add_address(
    name: str, address: str, rank: str = "rang1", config_dir=DEFAULT_CONFIG_DIR, state: State | None = None
) -> str:
    """Ajoute `address` au groupe `name` (créé s'il n'existe pas). Retourne un message."""
    if rank not in RANKS + (DEPOSIT,):
        raise ConfigError(f"catégorie inconnue : {rank!r} (choix : {', '.join(RANKS + (DEPOSIT,))})")
    if not is_valid_address(address):
        raise ConfigError(f"adresse SS58 invalide : {address!r}")
    path = _group_file(name, config_dir)
    created = not path.exists()
    header, data = _read(path)
    if created:
        header = [f"# Groupe « {name} » créé par `clusterwatch add`.",
                  "# Ajouter une adresse : python -m clusterwatch add <adresse> --group " + name]

    cats = _categories(data)
    for cat, addrs in cats.items():
        if address in addrs:
            raise ConfigError(f"{address} est déjà dans le groupe {name!r} ({cat})")
    cats[rank].append(address)
    _write(path, header, _store(data, cats))

    if state is not None:
        # Un ajout manuel l'emporte sur un ajout auto ou un retrait précédent.
        state.auto_wallets.pop(address, None)
        state.rejected = [a for a in state.rejected if a != address]
    verb = "créé avec" if created else "mis à jour :"
    return f"groupe {name!r} {verb} {address} ({rank})."


def remove_address(name: str, address: str, config_dir=DEFAULT_CONFIG_DIR, state: State | None = None) -> str:
    """Retire `address` du groupe ; elle ne sera plus ré-ajoutée automatiquement."""
    path = _group_file(name, config_dir)
    if not path.exists():
        raise ConfigError(f"groupe inconnu : {name!r}")
    header, data = _read(path)
    cats = _categories(data)
    found = [c for c, addrs in cats.items() if address in addrs]
    auto = state is not None and address in state.auto_wallets
    if not found and not auto:
        raise ConfigError(f"{address} n'est pas suivie dans le groupe {name!r}")
    for c in found:
        cats[c].remove(address)
    if found:
        if any(c in RANKS for c in found) and not any(cats[r] for r in RANKS) and not (state and state.auto_wallets.keys() - {address}):
            raise ConfigError(f"impossible de retirer le dernier wallet du groupe {name!r}")
        _write(path, header, _store(data, cats))
    if state is not None:
        state.auto_wallets.pop(address, None)
        if address not in state.rejected:
            state.rejected.append(address)
    return f"{address} retirée du groupe {name!r}."
