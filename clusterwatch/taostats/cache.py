"""Cache disque des réponses API.

Seules les requêtes « fermées » (plage de blocs bornée par block_end, donc
immuables) sont mises en cache sans expiration. Les endpoints « latest »
ne passent pas par le cache.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


class DiskCache:
    def __init__(self, root: Path | str):
        self.root = Path(root) / "http"

    @staticmethod
    def key(path: str, params: dict) -> str:
        canon = json.dumps({"path": path, "params": params}, sort_keys=True, default=str)
        return hashlib.sha256(canon.encode()).hexdigest()

    def _file(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, path: str, params: dict):
        f = self._file(self.key(path, params))
        if not f.exists():
            return None
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def set(self, path: str, params: dict, payload) -> None:
        f = self._file(self.key(path, params))
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(f)
