"""Version texte compacte du rapport, prête pour Telegram (texte brut)."""

from __future__ import annotations

from ..config import Config, short
from ..pipeline import RunResult
from .fmt import num, side, subnet

TELEGRAM_MAX = 4096
MAX_LINES_PER_SECTION = 15


def _capped(lines: list[str]) -> list[str]:
    if len(lines) <= MAX_LINES_PER_SECTION:
        return lines
    return lines[:MAX_LINES_PER_SECTION] + [f"  … +{len(lines) - MAX_LINES_PER_SECTION} (voir rapport)"]


def render_telegram(cfg: Config, r: RunResult) -> str:
    b, ev, sn = r.balance, r.events, r.collected.subnets
    var = "" if b.variation_tao is None else f" ({num(b.variation_tao, signed=True)})"
    L = [
        f"clusterwatch [{r.group}] {r.run_at:%d/%m %H:%M} UTC",
        f"Valeur du groupe : {num(b.value_tao)} TAO{var}",
        f"Net trading : {num(b.net_flow_tao, signed=True)} TAO | volume {num(b.volume_tao)} TAO",
    ]
    if r.initial:
        L.append("Premier passage : référence établie.")
        L += ["", "Wallets (valeur / PnL) :"]
        L += _capped([f"• {short(p.wallet)} {num(p.value_tao)} TAO / {num(p.pnl_tao, signed=True)}"
                      for p in sorted(r.pnls, key=lambda p: p.value_tao, reverse=True)])
        if r.auto_added:
            L += ["", "Ajout auto au suivi (confiance forte) :"] + [f"• {a}" for a in r.auto_added]
        if r.candidates:
            L += ["", "Wallets liés détectés :"]
            L += _capped([f"• {c.address} {c.confidence}" for c in r.candidates])
        return "\n".join(L)

    big = num(cfg.thresholds.big_trade_tao, 0)
    if ev.big_trades:
        L += ["", f"Trades ≥ {big} TAO : {len(ev.big_trades)}"]
        L += _capped([
            f"• {t.timestamp:%H:%M} {short(t.wallet)} {side(t).upper()} {subnet(t.netuid, sn)} {num(t.tao)} TAO"
            for t in ev.big_trades
        ])
    s = ev.small_trades
    if s.count:
        L.append(f"Trades < {big} TAO : {s.count} ({s.n_buys} achats {num(s.buys_tao)} / "
                 f"{s.n_sells} ventes {num(s.sells_tao)})")
    if ev.validator_moves:
        L.append(f"Changements de validateur : {len(ev.validator_moves)}")
    if ev.tao_transfers:
        L += ["", "Transferts TAO :"]
        L += _capped([f"• {short(t.sender)} → {short(t.recipient)} {num(t.tao, 4)}" for t in ev.tao_transfers])
    if ev.stake_transfers:
        L += ["", "Transferts de stake :"]
        L += _capped([f"• {short(x.sender)} → {short(x.recipient)} {subnet(x.netuid, sn)} {num(x.tao)} TAO"
                      for x in ev.stake_transfers])
    if ev.new_subnets:
        L.append("Nouveau(x) subnet(s) : " + ", ".join(subnet(n, sn) for n in ev.new_subnets))
    if ev.watched_subnet_trades or ev.watched_subnet_stake_transfers:
        n = len(ev.watched_subnet_trades) + len(ev.watched_subnet_stake_transfers)
        watched = ", ".join(f"SN{x}" for x in cfg.thresholds.watch_subnets)
        L.append(f"Mouvements {watched} : {n}")
    if ev.awakened_wallets:
        L.append("Réveil : " + ", ".join(short(w) for w in ev.awakened_wallets))
    if r.new_addresses:
        L.append("Nouvelles adresses suivies : " + ", ".join(short(a) for a in r.new_addresses))
    if r.auto_added:
        L += ["", "Ajout auto au suivi (confiance forte) :"]
        L += [f"• {a}" for a in r.auto_added]
    if r.candidates:
        L += ["", "Candidats :"]
        L += _capped([
            f"• {c.address} {c.confidence} ({c.fingerprint.n_trades if c.fingerprint else 0} trades)"
            for c in r.candidates
        ])
    return "\n".join(L)


def split_message(text: str, limit: int = TELEGRAM_MAX) -> list[str]:
    """Découpe sur les fins de ligne pour respecter la limite Telegram."""
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{cur}\n{line}" if cur else line
        if len(candidate) > limit:
            parts.append(cur)
            cur = line
        else:
            cur = candidate
    if cur:
        parts.append(cur)
    return parts
