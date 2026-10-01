# clusterwatch

Outil Python d'analyse et de surveillance de groupes de wallets Bittensor (dTAO).
Un **groupe** rassemble les wallets d'une même entité (le trader suivi, ton propre
portefeuille, un autre acteur…). Chaque groupe a ses propres wallets, son état, son PnL
et ses rapports.

Chaque passage relit la chaîne via l'API officielle Taostats depuis le dernier bloc
analysé, calcule un bilan, le PnL par wallet, les événements notables et les wallets
liés (candidats), puis produit un rapport Markdown en français et un texte compact pour
Telegram. Les wallets liés de confiance forte sont ajoutés automatiquement au suivi.

Les rapports ne contiennent que des données : aucun conseil d'investissement, aucune
interprétation des intentions du trader.

> **Débutant ?** Suis le guide pas à pas : [GUIDE_DEBUTANT.md](GUIDE_DEBUTANT.md).

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
| `TELEGRAM_BOT_TOKEN` | token du bot Telegram | pour Telegram |
| `TELEGRAM_CHAT_ID` | identifiant de ton chat (plusieurs possibles, séparés par des virgules) | pour Telegram |

Aucune clé n'est écrite dans le code ni dans la config. Le plus simple est de copier
`.env.example` en `.env` et d'y mettre les valeurs. Ce fichier est lu automatiquement
et ignoré par git. Les variables déjà définies dans le terminal restent prioritaires.

```bash
cp .env.example .env    # puis éditer .env
```

## Configuration

Tout le périmètre se modifie sans toucher au code.

### `config/settings.yaml` : réglages communs

- `infrastructure` : adresses jamais considérées comme candidates, pour tous les
  groupes (hot wallet, collecteur de frais…). `role: fee_collector` masque les
  micro-transferts vers cette adresse.
- `api` : rythme des appels (`min_interval_s`, 12 s par défaut pour l'offre gratuite à
  5 crédits/min), nombre de reprises, taille des pages, dossier de cache, limites pour
  l'historique des candidats.
- `collection.start_block` : bloc de départ de l'historique d'une adresse nouvellement
  suivie (`null` = tout l'historique, nécessaire pour un PnL complet).
- `thresholds` : seuil des gros trades (5 TAO), des micro-frais (0,01 TAO), subnets
  surveillés (`[118]`), seuil « wallet vidé », paramètres de détection des changements
  de validateur.
- `fingerprint` : bornes de l'empreinte de référence (voir plus bas).
- `auto_add` : ajout automatique des wallets liés de confiance forte (`enabled`,
  `max_per_run`).
- `telegram.enabled` : `true` pour que `run` envoie le rapport sans `--send`.
- `telegram.run_every_minutes` : intervalle des passages automatiques du bot.

### `config/groups/<nom>.yaml` : un fichier par groupe

- `wallets.rang1`, `wallets.rang2`, `wallets.observation` : wallets du groupe.
- `deposit_addresses` : adresses de dépôt exchange. Elles sont surveillées pour repérer
  de nouveaux expéditeurs, mais ne comptent pas comme wallets du groupe.
- Facultatif : `infrastructure` (ajoutée à la liste commune), `thresholds`,
  `fingerprint` ou `auto_add` pour redéfinir un réglage pour ce groupe seulement.

Le groupe `principal` contient le cluster du trader suivi. Ces fichiers peuvent être
édités à la main, ou gérés avec les commandes `add` et `remove`.

Une adresse ne peut figurer que dans une seule catégorie d'un groupe ; la config est
validée au chargement.

## Usage

```bash
# Ajouter une adresse à un groupe ; le groupe est créé s'il n'existe pas
python -m clusterwatch add 5Xxx... --group mon-pf
python -m clusterwatch add 5Yyy... --group principal --rank rang2
python -m clusterwatch add 5Zzz... --group mon-pf --rank depot   # adresse de dépôt exchange

# Retirer une adresse (elle ne sera plus jamais ré-ajoutée automatiquement)
python -m clusterwatch remove 5Yyy... --group principal

# Lister les groupes, leurs adresses, les ajouts auto et les candidats forts
python -m clusterwatch groups

# Passage complet sur tous les groupes : collecte, analyse, rapports, texte compact
python -m clusterwatch run
python -m clusterwatch run --group mon-pf        # un seul groupe

# Idem sans sauvegarder l'état ni rien envoyer (pour tester)
python -m clusterwatch run --dry-run

# Passage + envoi Telegram (seulement pour les groupes qui ont quelque chose à signaler)
python -m clusterwatch run --send

# Infos sur une adresse quelconque, sans la suivre
python -m clusterwatch info 5Xxx...

# Adresses actives liées à une adresse (voir « Trouver les adresses liées »)
python -m clusterwatch links 5Xxx...
python -m clusterwatch links 5Xxx... --all     # inclut les liens « possible »

# Bot Telegram (voir la section dédiée)
python -m clusterwatch bot

# Affiche un échantillon brut de chaque endpoint Taostats (vérification des champs)
python -m clusterwatch probe

# Empreinte d'une adresse quelconque (utile pour calibrer la référence sur les wallets connus)
python -m clusterwatch fingerprint 5Et1cWpHVPdpaiTFjvmEdw4BVt4tTmwzPJ9TUFmEJvrWH3qR
```

Options globales : `--config-dir`, `--state-dir`, `-v` (journal détaillé).
Options de `run` : `--group` (répétable), `--reports`, `--dry-run`, `--send`.

### Ajouter une nouvelle adresse

Au passage qui suit un `add`, l'outil :

1. récupère tout l'historique de l'adresse (trades, transferts, positions) ;
2. l'intègre au PnL sans double comptage : un transfert passé entre cette adresse et
   un wallet déjà suivi avait déjà été compté du côté de ce dernier ;
3. ne présente pas cet historique comme de nouveaux événements, mais liste l'adresse
   dans « Changements du périmètre suivi » ;
4. cherche ses wallets liés dans cet historique : destinataires de ses transferts TAO
   ou de stake, flux dans les deux sens.

Ensuite, l'adresse est suivie comme les autres.

### Wallets liés ajoutés automatiquement

Quand un candidat atteint la confiance **forte** (au moins 15 trades et un transfert
de stake ou des flux dans les deux sens avec le groupe), il est ajouté au suivi en
catégorie « ajout auto ». Les règles :

- au plus `auto_add.max_per_run` ajouts par passage ;
- chaque ajout est signalé dans le rapport et sur Telegram ;
- l'adresse est collectée à partir du passage suivant, et ses propres wallets liés sont
  détectés à leur tour ;
- les ajouts auto sont enregistrés dans l'état du groupe (`state/<groupe>.json`), pas
  dans le YAML ;
- `remove` retire un ajout auto et l'empêche de revenir ;
- `add` le rend permanent.

Le **premier passage** d'un groupe établit la référence : valeur, cumuls de PnL,
subnets déjà détenus, expéditeurs déjà connus vers les dépôts. Il ne détaille pas les
événements historiques et n'envoie rien sur Telegram. Avec l'offre gratuite, il peut
durer plusieurs dizaines de minutes.

## Trouver les adresses liées à une adresse

`python -m clusterwatch links <adresse>` (ou `/liens <adresse>` sur Telegram) explore
les liens on-chain de l'adresse et liste les adresses **actives** qui lui sont
probablement liées. Pour chacune, il donne le niveau de certitude et les preuves
trouvées.

**Méthode**

1. Lecture de l'historique de l'adresse de départ : transferts TAO, transferts de
   stake, trades (pour son empreinte).
2. Examen détaillé des contreparties principales : solde, positions, trades récents,
   premier financeur, nombre de contreparties.
3. Classement de chaque contrepartie :
   - **hub** (exchange, service, trop de contreparties) : ignoré ;
   - **adresse de dépôt exchange** : ne trade pas et reverse presque tout vers une
     seule adresse. Les adresses de dépôt déjà connues dans tes groupes comptent aussi.
     Les **autres expéditeurs** vers ce dépôt sont alors examinés : une adresse de
     dépôt appartient à un seul compte exchange ;
   - **wallet** : évalué.
4. Chaque wallet reçoit des points selon les indices trouvés :

| Indice | Type | Points |
|---|---|---|
| Transfert de stake avec l'adresse de départ | fort | 3 |
| Flux TAO dans les deux sens | fort | 3 |
| Même adresse de dépôt exchange | fort | 3 |
| Tout premier financement reçu de l'adresse de départ (ou l'inverse) | fort | 2 |
| Transferts à sens unique | appui | 0,5–1 |
| Empreinte de trading très proche de celle de l'adresse de départ (≥ 15 trades des deux côtés) | appui | 1–2 |

**Niveaux**

- **très probable** : au moins **deux indices forts indépendants** et 5 points ou plus ;
- **probable** : un indice fort et 4 points ou plus ;
- **possible** : 2 points ou plus. Masqué par défaut, affiché avec `--all` ou
  `/liens <adresse> tout`.

Une ressemblance d'empreinte seule ne dépasse jamais « possible ». Les adresses liées
mais inactives (pas de trade depuis `active_days` jours et valeur sous `active_min_tao`)
sont écartées et seulement comptées.

Une attribution on-chain reste une **probabilité**, jamais une certitude absolue : un
transfert peut venir d'un proche, d'un OTC ou d'un service. Les preuves sont listées
pour que tu puisses juger.

**Durée.** Les bornes de la section `links` de `config/settings.yaml` limitent le
nombre d'appels. Avec l'offre gratuite, une recherche prend au plus ~25 min, souvent
bien moins, et le bot annonce la durée maximale au lancement.

Pour suivre ensuite une adresse trouvée : `add` / `/add`. Les passages réguliers
continuent la détection et ajoutent automatiquement les liens de confiance forte (voir
plus haut).

## Bot Telegram : rapports et commandes à distance

Le bot fait deux choses :

- il lance un passage sur tous les groupes à intervalle régulier
  (`telegram.run_every_minutes`, 60 min par défaut) et t'envoie le rapport compact de
  chaque groupe qui a quelque chose de nouveau ;
- il répond à tes messages, depuis ton téléphone :

| Message | Effet |
|---|---|
| une adresse seule (`5Xxx…`) | infos sur l'adresse : valeur, positions, trades des 7 derniers jours, empreinte |
| `/info <adresse>` | idem |
| `/liens <adresse> [tout]` | adresses actives liées à cette adresse, avec niveau de certitude et preuves |
| `/add <adresse> <groupe> [rang]` | suit l'adresse (groupe créé s'il n'existe pas), lance le passage et envoie le rapport |
| `/remove <adresse> <groupe>` | arrête de suivre l'adresse |
| `/groupes` | liste des groupes et des adresses suivies |
| `/run [groupe]` | lance un passage maintenant |
| `/aide` | liste des commandes |

Le bot ne répond qu'aux chats listés dans `TELEGRAM_CHAT_ID` et ignore tous les autres.
Avec l'offre Taostats gratuite (12 s entre deux appels), une réponse peut prendre de
quelques secondes à plusieurs minutes. Un `/add` sur une adresse très active est le cas
le plus long, car tout son historique est récupéré.

### Mise en place (une seule fois)

1. Sur Telegram, ouvre une conversation avec **@BotFather**, envoie `/newbot` et
   choisis un nom. BotFather te donne un **token** (`123456:ABC…`).
2. Sur ta machine :
   ```bash
   export TELEGRAM_BOT_TOKEN="123456:ABC..."
   python -m clusterwatch bot --show-chat-id
   ```
   Envoie un message à ton bot. Il te répond avec ton **chat_id**, qui s'affiche aussi
   dans le terminal. Arrête ensuite avec Ctrl+C.
3. Démarre le bot :
   ```bash
   export TAOSTATS_API_KEY="..."
   export TELEGRAM_BOT_TOKEN="123456:ABC..."
   export TELEGRAM_CHAT_ID="ton_chat_id"
   python -m clusterwatch bot
   ```

Le bot doit **tourner en permanence** pour répondre et faire les passages planifiés.
Il faut donc une machine allumée : ton ordinateur, un Raspberry Pi ou un petit serveur
(VPS). Si la machine s'éteint, les messages envoyés entre-temps sont traités au
redémarrage.

Le token et le chat_id ne sont jamais écrits dans le code ni dans la config.

## Fichiers produits

- `state/<groupe>.json` : état du groupe entre deux passages (dernier bloc, valeur
  précédente, cumuls financé/sorti, subnets vus, expéditeurs connus, candidats, ajouts
  auto, adresses retirées). **Versionné** : le commiter
  après chaque passage rend l'analyse reproductible. Il est écrit de façon atomique et
  seulement si le passage réussit.
- `reports/<groupe>/AAAA-MM-JJ_HHMM.md` : rapport complet du passage pour ce groupe.
- `cache/` (ignoré par git) : réponses API sur des plages de blocs fermées, réutilisées si
  un passage est relancé.

## Ce que calcule un passage

**Bilan**
- Valeur du groupe : TAO libres + positions alpha valorisées en TAO.
- Variation depuis le passage précédent (la part due aux wallets nouvellement suivis
  est indiquée).
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
  TAO ou de stake depuis le groupe (y compris dans l'historique d'une adresse
  nouvellement suivie).
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
- la gestion des groupes (ajout, retrait, fusion des réglages) ;
- l'ajout d'une adresse en cours de route et l'ajout automatique des wallets liés ;
- le bot Telegram (chats autorisés, commandes, passages planifiés) ;
- la recherche d'adresses liées (indices, niveaux, détection des dépôts exchange) ;
- le client HTTP (reprise 429, pagination, cache) ;
- un passage complet contre une fausse API.

## Planification

Exemple de cron toutes les heures (adapter les chemins) :

```cron
5 * * * * cd /chemin/clusterwatch && . .venv/bin/activate && python -m clusterwatch run --send && git add state reports config && git commit -qm "passage clusterwatch" 
```
