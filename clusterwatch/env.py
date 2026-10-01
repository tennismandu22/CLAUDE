"""Lecture d'un fichier .env (clés et tokens), pour ne pas les retaper à chaque fois.

Format : une ligne CLE=valeur par variable ; les lignes vides et celles qui
commencent par # sont ignorées. Une variable déjà définie dans l'environnement
n'est pas écrasée. Le fichier .env est ignoré par git.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path | str = ".env") -> list[str]:
    """Charge `path` s'il existe. Retourne les noms des variables chargées."""
    path = Path(path)
    if not path.exists():
        return []
    loaded = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
