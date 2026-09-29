# clusterwatch

Outil Python d'analyse et de surveillance d'un cluster de wallets Bittensor (dTAO).
Chaque passage relit la chaîne via l'API officielle Taostats depuis le dernier bloc
analysé, calcule un bilan, le PnL par wallet, les événements notables et les wallets
candidats, puis produit un rapport Markdown en français et un texte compact pour Telegram.

Les rapports ne contiennent que des données : aucun conseil d'investissement, aucune
interprétation des intentions du trader.

## Installation

Python 3.10 ou plus récent.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Variables d'environnement

| Variable | Rôle | Obligatoire |
|---|---|---|
| `TAOSTATS_API_KEY` | clé de l'API Taostats (https://taostats.io/pro) | oui |
| `TELEGRAM_BOT_TOKEN` | token du bot Telegram | seulement pour l'envoi |
| `TELEGRAM_CHAT_ID` | identifiant du chat destinataire | seulement pour l'envoi |

Aucune clé n'est écrite dans le code ni dans la config.

```bash
export TAOSTATS_API_KEY="..."
```

## Configuration : `config/cluster.yaml`

Tout le périmètre se modifie dans ce fichier, sans toucher au code.

- `wallets.rang1`, `wallets.rang2`, `wallets.observation` : wallets attribués au trader.
  Ensemble, ils forment « le cluster ».
- `deposit_addresses` : adresses de dépôt exchange. Elles sont surveillées pour repérer
  de nouveaux expéditeurs, mais ne comptent pas comme wallets du trader.
- `infrastructure` : adresses jamais considérées comme candidates (hot wallet, collecteur
  de frais…). `role: fee_collector` masque les micro-transferts vers cette adresse.
- `api` : rythme des appels (`min_interval_s`, 12 s par défaut pour l'offre gratuite à
  5 crédits/min), nombre de reprises, taille des pages, dossier de cache, limites pour
  l'historique des candidats.
- `collection.start_block` : bloc de départ du tout premier passage (`null` = tout
  l'historique, nécessaire pour un PnL complet).
- `thresholds` : seuil des gros trades (5 TAO), des micro-frais (0,01 TAO), subnets
  surveillés (`[118]`), seuil « wallet vidé », paramètres de détection des changements
  de validateur.
- `fingerprint` : bornes de l'empreinte de référence (voir plus bas).
- `telegram.enabled` : `true` pour envoyer automatiquement à chaque passage.

Une adresse ne peut figurer que dans une seule catégorie ; la config est validée au
chargement.

## Usage

```bash
# Passage complet : collecte, analyse, rapport dans reports/, texte compact sur la sortie standard
python -m clusterwatch run

# Idem sans sauvegarder l'état ni rien envoyer (pour tester)
python -m clusterwatch run --dry-run

# Passage + envoi Telegram (seulement s'il y a quelque chose à signaler)
python -m clusterwatch run --send

# Affiche un échantillon brut de chaque endpoint Taostats (vérification des champs)
python -m clusterwatch probe

# Empreinte d'une adresse quelconque (utile pour calibrer la référence sur les wallets connus)
python -m clusterwatch fingerprint 5Et1cWpHVPdpaiTFjvmEdw4BVt4tTmwzPJ9TUFmEJvrWH3qR
```

Options globales : `--config chemin.yaml`, `-v` (journal détaillé).
Options de `run` : `--state`, `--reports`, `--dry-run`, `--send`.

Le **premier passage** établit la référence : valeur du cluster, cumuls de PnL, subnets
déjà détenus, expéditeurs déjà connus vers les dépôts. Il ne détaille pas les événements
historiques et n'envoie rien sur Telegram. Avec l'offre gratuite, il peut durer plusieurs
dizaines de minutes.

## Fichiers produits

- `state/state.json` : état entre deux passages (dernier bloc, valeur précédente, cumuls
  financé/sorti, subnets vus, expéditeurs connus, candidats). **Versionné** : le commiter
  après chaque passage rend l'analyse reproductible. Il est écrit de façon atomique et
  seulement si le passage réussit.
- `reports/AAAA-MM-JJ_HHMM.md` : rapport complet du passage.
- `cache/` (ignoré par git) : réponses API sur des plages de blocs fermées, réutilisées si
  un passage est relancé.

## Ce que calcule un passage

**Bilan**
- Valeur du cluster : TAO libres + positions alpha valorisées en TAO.
- Variation depuis le passage précédent.
- Flux de trading net (ventes − achats) et volume brassé (achats + ventes).
- Tous les montants RAO sont divisés par 1e9.

**Changements de validateur**
- Une vente et un achat du même wallet, sur le même subnet, à quelques blocs d'écart
  (`validator_move_block_window`), au même prix et avec un slippage nul.
- Ils sont exclus du décompte des trades et listés à part.

**PnL par wallet** = position actuelle + total sorti − total financé.
- Financé : TAO et stake alpha reçus par transfert.
- Sorti : TAO et stake alpha envoyés, sauf vers un collecteur de frais (un frais est un
  coût, pas un retrait).
- Un transfert entre deux wallets du cluster s'annule au niveau du total.
- Si `start_block` n'est pas `null`, les flux antérieurs ne sont pas comptés.

**Événements signalés**
- Trades ≥ 5 TAO, détaillés.
- Trades < 5 TAO, résumés en une ligne.
- Transferts TAO, sauf micro-frais < 0,01 vers le collecteur.
- Transferts de stake alpha.
- Première position du cluster sur un subnet.
- Tout mouvement sur SN118.
- Réveil d'un wallet vidé.

**Wallets candidats**
- Sources : nouveaux expéditeurs vers les adresses de dépôt, destinataires de transferts
  TAO ou de stake depuis le cluster.
- L'infrastructure et les dépôts sont exclus.
- Pour chaque candidat, l'empreinte est calculée sur son historique de trades et
  comparée à la référence :

| Critère | Référence par défaut |
|---|---|
| Équilibre achats/ventes | écart ≤ 1 % |
| Clip médian | 4–26 TAO |
| Slippage | médian 0,09–0,31 %, P90 < 0,85 % |
| Subnets touchés | 12–130 |
| Détention médiane (FIFO achat→vente) | 0,4–4 jours |
| Heures UTC | ≤ 8 % des trades en 01h–06h, ≥ 55 % en 07h–13h et 20h–22h |
| Validateur Taostats | informatif, jamais éliminatoire |

Une empreinte est « cohérente » quand au moins `min_criteria_ok` critères sur 6 sont
vérifiés (5 par défaut).

Niveau de confiance :
- **fort** : transfert de stake avec le cluster, ou flux dans les deux sens ;
- **moyen** : adresse de dépôt partagée et empreinte cohérente ;
- **faible** : tous les autres cas ;
- moins de 15 trades : jamais mieux que **faible**.

Au plus `max_candidates_per_run` candidats sont évalués par passage ; les suivants le
sont aux passages suivants. Seuls les candidats nouveaux, ou dont la confiance a changé,
apparaissent dans le rapport.

Les seuils horaires (`quiet_share_max`, `peak_share_min`) sont des valeurs de départ :
calibre-les avec `python -m clusterwatch fingerprint <wallet rang 1>`.

## Endpoints Taostats utilisés

Tous les chemins et noms de champs sont regroupés dans
`clusterwatch/taostats/endpoints.py`. Une correction ne touche que ce fichier.

| Besoin | Endpoint | Statut |
|---|---|---|
| Positions alpha | `GET /api/dtao/stake_balance/latest/v1?coldkey=` | confirmé (doc) |
| Prix et noms des subnets | `GET /api/dtao/pool/latest/v1` | confirmé (doc) |
| Trades delegate/undelegate, transferts de stake | `GET /api/delegation/v1?nominator=` | à confirmer avec `probe` |
| Transferts TAO | `GET /api/transfer/v1?address=` / `?to=` | à confirmer avec `probe` |
| Solde libre | `GET /api/account/latest/v1?address=` | à confirmer avec `probe` |
| Dernier bloc | `GET /api/block/v1?limit=1` | à confirmer avec `probe` |
| Noms des subnets (secours) | `GET /api/subnet/identity/v1` | à confirmer avec `probe` |

Points à vérifier au premier `probe` :
- l'unité du champ `slippage` : fraction ou pourcentage (constante `SLIPPAGE_UNIT`) ;
- la présence de `is_transfer` et `transfer_address` pour les transferts de stake ;
- le hotkey du validateur Taostats dans `fingerprint.reference_validator_hotkeys`.

Côté client :
- la clé est envoyée dans l'en-tête `Authorization` ;
- une pause minimale est respectée entre deux appels ;
- en cas de 429, l'en-tête `Retry-After` est respecté, sinon backoff exponentiel ;
- les erreurs 5xx sont réessayées.

## Tests

```bash
pytest
```

Les tests utilisent uniquement des données fictives. Ils couvrent :
- le calcul d'empreinte ;
- le PnL ;
- le filtrage des changements de validateur ;
- les événements et la confiance des candidats ;
- le client HTTP (reprise 429, pagination, cache) ;
- un passage complet contre une fausse API.

## Planification

Exemple de cron toutes les heures (adapter les chemins) :

```cron
5 * * * * cd /chemin/clusterwatch && . .venv/bin/activate && python -m clusterwatch run --send && git add state reports && git commit -qm "passage clusterwatch" 
```
