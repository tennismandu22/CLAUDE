"""Rapport Markdown complet d'un passage (données uniquement, sans interprétation)."""

from __future__ import annotations

from pathlib import Path

from ..analysis.discovery import REASON_LABELS
from ..config import Config
from ..pipeline import RunResult
from .fmt import num, pct, side, subnet, wallet


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def render_markdown(cfg: Config, r: RunResult) -> str:
    col, b, ev, sn = r.collected, r.balance, r.events, r.collected.subnets
    L: list[str] = [f"# Rapport clusterwatch — groupe « {r.group} » — {r.run_at:%Y-%m-%d %H:%M} UTC", ""]
    start = "début de l'historique" if col.block_start is None else f"bloc {col.block_start}"
    L.append(f"Période analysée : {start} → bloc {col.block_end}.")
    if r.initial:
        L.append("")
        L.append("**Premier passage** : état de référence établi. Les événements historiques ne sont "
                 "pas détaillés ; seuls leurs totaux sont indiqués.")
    L.append("")

    # --- Bilan
    L += ["## Bilan du cluster", ""]
    L += _table(["Indicateur", "Valeur"], [
        ["Valeur totale", f"{num(b.value_tao)} TAO"],
        ["dont TAO libres", f"{num(b.free_tao)} TAO"],
        ["dont positions alpha", f"{num(b.staked_tao)} TAO"],
        ["Variation depuis le passage précédent", _variation(r)],
        ["Achats", f"{b.n_buys} trades, {num(b.buys_tao)} TAO"],
        ["Ventes", f"{b.n_sells} trades, {num(b.sells_tao)} TAO"],
        ["Flux de trading net (ventes − achats)", f"{num(b.net_flow_tao, signed=True)} TAO"],
        ["Volume brassé", f"{num(b.volume_tao)} TAO"],
        ["Changements de validateur (exclus)", str(len(r.validator_moves))],
    ])
    L.append("")

    # --- PnL
    L += ["## PnL par wallet", "", "PnL = position actuelle + total sorti − total financé.", ""]
    rows = []
    for p in r.pnls:
        rows.append([wallet(cfg, p.wallet), num(p.value_tao), num(p.funded_tao), num(p.withdrawn_tao),
                     num(p.pnl_tao, signed=True)])
    rows.append(["**Total cluster**", num(sum(p.value_tao for p in r.pnls)),
                 num(sum(p.funded_tao for p in r.pnls)), num(sum(p.withdrawn_tao for p in r.pnls)),
                 num(sum(p.pnl_tao for p in r.pnls), signed=True)])
    L += _table(["Wallet", "Valeur (TAO)", "Financé", "Sorti", "PnL"], rows)
    L.append("")

    # --- Événements
    L += ["## Événements", ""]
    if r.initial:
        L += [
            f"- Trades historiques : {len(r.trades)} ({len(ev.big_trades)} ≥ "
            f"{num(cfg.thresholds.big_trade_tao, 0)} TAO)",
            f"- Transferts TAO historiques : {len(ev.tao_transfers)}",
            f"- Transferts de stake historiques : {len(ev.stake_transfers)}",
            f"- Changements de validateur historiques : {len(r.validator_moves)}",
            "",
        ]
    else:
        L += _events_sections(cfg, r)

    # --- Suivi
    if r.new_addresses or r.auto_added:
        L += ["## Changements du périmètre suivi", ""]
        if r.new_addresses:
            L.append("Adresses suivies pour la première fois (historique complet collecté, "
                     "non détaillé dans les événements) :")
            L += [f"- `{a}` — {cfg.label(a)}, valeur {num(b.per_wallet.get(a))} TAO"
                  if a in b.per_wallet else f"- `{a}` — {cfg.label(a)}" for a in r.new_addresses]
            L.append("")
        if r.auto_added:
            L.append("Wallets ajoutés automatiquement (confiance forte), suivis à partir du prochain passage :")
            L += [f"- `{a}`" for a in r.auto_added]
            L.append("")

    # --- Candidats
    L += ["## Nouveaux wallets candidats", ""]
    if not r.candidates:
        L.append("Aucun nouveau candidat ni changement de niveau de confiance.")
    for c in r.candidates:
        L += [f"### `{c.address}` — confiance **{c.confidence}**", ""]
        L.append("Raisons : " + "; ".join(REASON_LABELS[x] for x in sorted(c.reasons)) + ".")
        fp = c.fingerprint
        if fp is None:
            L += ["", "Empreinte : historique indisponible.", ""]
            continue
        L += ["", f"Trades analysés : {fp.n_trades} — critères vérifiés : {fp.n_ok}/{len(fp.core)}", ""]
        L += _table(["Critère", "Mesuré", "Référence", "OK"], [
            [c_.label, c_.measured, c_.expected, {True: "oui", False: "non", None: "n/d"}[c_.ok]]
            for c_ in fp.criteria
        ])
        L.append("")
    if r.pending_candidates:
        L += ["", f"{r.pending_candidates} candidat(s) en attente d'évaluation au prochain passage."]
    L.append("")
    return "\n".join(L)


def _variation(r: RunResult) -> str:
    v = r.balance.variation_tao
    if v is None:
        return "n/d (premier passage)"
    out = f"{num(v, signed=True)} TAO"
    if r.new_wallets_value_tao:
        out += f" (dont {num(r.new_wallets_value_tao)} TAO de wallets nouvellement suivis)"
    return out


def _events_sections(cfg: Config, r: RunResult) -> list[str]:
    ev, sn = r.events, r.collected.subnets
    L: list[str] = []
    big = num(cfg.thresholds.big_trade_tao, 0)

    L += [f"### Trades ≥ {big} TAO", ""]
    if ev.big_trades:
        L += _table(["Heure UTC", "Wallet", "Sens", "Subnet", "TAO", "Prix", "Slippage"], [
            [f"{t.timestamp:%d/%m %H:%M}", wallet(cfg, t.wallet), side(t), subnet(t.netuid, sn),
             num(t.tao), num(t.price, 6), pct(t.slippage)] for t in ev.big_trades
        ])
    else:
        L.append("Aucun.")
    L.append("")

    s = ev.small_trades
    L += [f"### Trades < {big} TAO", ""]
    L.append(f"{s.count} trades : {s.n_buys} achats ({num(s.buys_tao)} TAO), "
             f"{s.n_sells} ventes ({num(s.sells_tao)} TAO)." if s.count else "Aucun.")
    L.append("")

    L += ["### Changements de validateur (exclus des trades)", ""]
    if ev.validator_moves:
        L += _table(["Bloc", "Wallet", "Subnet", "Alpha", "De", "Vers"], [
            [str(m.sell.block), wallet(cfg, m.sell.wallet), subnet(m.sell.netuid, sn), num(m.sell.alpha),
             m.sell.hotkey[:8], m.buy.hotkey[:8]] for m in ev.validator_moves
        ])
    else:
        L.append("Aucun.")
    L.append("")

    L += ["### Transferts TAO", ""]
    if ev.tao_transfers:
        L += _table(["Heure UTC", "De", "Vers", "TAO"], [
            [f"{t.timestamp:%d/%m %H:%M}", wallet(cfg, t.sender), wallet(cfg, t.recipient), num(t.tao, 4)]
            for t in ev.tao_transfers
        ])
    else:
        L.append("Aucun.")
    L.append("")

    L += ["### Transferts de stake alpha", ""]
    if ev.stake_transfers:
        L += _table(["Heure UTC", "De", "Vers", "Subnet", "Alpha", "Valeur TAO"], [
            [f"{s.timestamp:%d/%m %H:%M}", wallet(cfg, s.sender), wallet(cfg, s.recipient),
             subnet(s.netuid, sn), num(s.alpha), num(s.tao)] for s in ev.stake_transfers
        ])
    else:
        L.append("Aucun.")
    L.append("")

    L += ["### Première position du cluster sur un subnet", ""]
    L.append(", ".join(subnet(n, sn) for n in ev.new_subnets) if ev.new_subnets else "Aucune.")
    L.append("")

    watched = ", ".join(f"SN{n}" for n in cfg.thresholds.watch_subnets)
    L += [f"### Mouvements sur {watched}", ""]
    if ev.watched_subnet_trades or ev.watched_subnet_stake_transfers:
        for t in ev.watched_subnet_trades:
            L.append(f"- {t.timestamp:%d/%m %H:%M} {wallet(cfg, t.wallet)} {side(t)} "
                     f"{subnet(t.netuid, sn)} {num(t.tao)} TAO")
        for s in ev.watched_subnet_stake_transfers:
            L.append(f"- {s.timestamp:%d/%m %H:%M} transfert de stake {wallet(cfg, s.sender)} → "
                     f"{wallet(cfg, s.recipient)} {subnet(s.netuid, sn)} {num(s.tao)} TAO")
    else:
        L.append("Aucun.")
    L.append("")

    L += ["### Réveil de wallets vidés", ""]
    L.append(", ".join(wallet(cfg, w) for w in ev.awakened_wallets) if ev.awakened_wallets else "Aucun.")
    L.append("")
    return L


def write_report(cfg: Config, r: RunResult, reports_dir: Path | str = "reports") -> Path:
    d = Path(reports_dir) / r.group
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{r.run_at:%Y-%m-%d_%H%M}.md"
    path.write_text(render_markdown(cfg, r), encoding="utf-8")
    return path
