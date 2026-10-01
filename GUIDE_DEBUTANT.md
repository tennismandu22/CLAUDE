# Mettre en place clusterwatch, pas à pas (guide débutant)

Ce guide part de zéro. Suis les étapes **dans l'ordre**. Quand une étape diffère entre
Windows et Mac, les deux versions sont données : ne fais que celle de ton ordinateur.

Vocabulaire utile :

- **Terminal** : une fenêtre où l'on tape des commandes au clavier. On tape la commande,
  puis on appuie sur **Entrée**.
- **Dossier du projet** : le dossier `clusterwatch` que tu vas créer à l'étape 2. Toutes
  les commandes se tapent depuis ce dossier.

---

## Étape 1 — Installer Python (une seule fois, ~10 min)

Python est le langage dans lequel l'outil est écrit. Il faut l'installer pour pouvoir le
lancer.

**Windows**

1. Va sur https://www.python.org/downloads/ et clique sur le gros bouton jaune
   « Download Python 3.x ».
2. Ouvre le fichier téléchargé.
3. **Important** : en bas de la première fenêtre, coche la case
   **« Add python.exe to PATH »**. Clique ensuite sur « Install Now ».
4. Vérifie l'installation :
   - appuie sur la touche Windows, tape `cmd` et ouvre **Invite de commandes** ;
   - tape `python --version` puis Entrée ;
   - tu dois voir `Python 3.12.x` (ou une version 3.10 ou plus récente).

**Mac**

1. Va sur https://www.python.org/downloads/ et télécharge « Python 3.x » pour macOS.
2. Ouvre le fichier `.pkg` et suis l'installation (Continuer → Installer).
3. Vérifie l'installation :
   - appuie sur Cmd + Espace, tape `Terminal` puis Entrée ;
   - tape `python3 --version` puis Entrée ;
   - tu dois voir `Python 3.12.x` (ou une version 3.10 ou plus récente).

---

## Étape 2 — Télécharger le code (~3 min)

1. Va sur https://github.com/tennismandu22/claude et connecte-toi à ton compte GitHub.
2. Clique sur le bouton vert **« Code »**, puis sur **« Download ZIP »**.
3. Décompresse le ZIP :
   - **Windows** : clic droit sur le fichier → « Extraire tout » ;
   - **Mac** : double-clic sur le fichier.
4. Tu obtiens un dossier au nom compliqué (par exemple `claude-claude-great-franklin-…`).
   **Renomme-le `clusterwatch`** et déplace-le dans ton dossier **Documents**.

---

## Étape 3 — Ouvrir un terminal dans le dossier du projet

À refaire à chaque nouvelle session de travail.

**Windows** : ouvre **Invite de commandes** (touche Windows → `cmd`) et tape :

```
cd %USERPROFILE%\Documents\clusterwatch
```

**Mac** : ouvre **Terminal** et tape :

```
cd ~/Documents/clusterwatch
```

Si aucun message d'erreur n'apparaît, tu es dans le bon dossier.

---

## Étape 4 — Installer l'outil (une seule fois, ~2 min)

Toujours dans le terminal de l'étape 3, tape ces commandes une par une, chacune suivie
d'Entrée.

**Windows**

```
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

**Mac**

```
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Après la 2e commande, le début de la ligne affiche **`(.venv)`**. Cela veut dire que
l'environnement de l'outil est activé.

**À chaque nouvelle ouverture du terminal**, il faudra refaire l'étape 3 puis cette
2e commande (`.venv\Scripts\activate` ou `source .venv/bin/activate`), mais pas les deux
autres.

Vérification facultative : tape `pytest`. Tu dois voir à la fin une ligne du type
`72 passed`.

---

## Étape 5 — Obtenir ta clé Taostats (~5 min)

1. Va sur https://taostats.io/pro et crée un compte, ou connecte-toi. L'offre gratuite
   suffit.
2. Ouvre la rubrique **API Keys** (https://taostats.io/pro/api-keys).
3. Crée une clé et **copie-la**.

Ne la partage avec personne, et ne la colle jamais dans une conversation, même avec moi.

---

## Étape 6 — Créer ton fichier secret `.env` (~3 min)

Ce fichier contient tes clés. Il reste sur ton ordinateur et n'est jamais envoyé sur
GitHub.

**Windows** (dans le terminal, avec `(.venv)` affiché) :

```
copy .env.example .env
notepad .env
```

**Mac** :

```
cp .env.example .env
open -e .env
```

Le fichier s'ouvre dans un éditeur de texte. Remplace `colle_ta_cle_ici` par ta clé
Taostats. Garde le reste tel quel pour l'instant. La ligne doit ressembler à :

```
TAOSTATS_API_KEY=tao-abc123...
```

Enregistre (Ctrl+S sur Windows, Cmd+S sur Mac) et ferme l'éditeur.

---

## Étape 7 — Premier test : vérifier la connexion à Taostats (~2 min)

Dans le terminal :

```
python -m clusterwatch probe
```

L'outil interroge Taostats et affiche des exemples de données. Il attend 12 s entre deux
appels, c'est normal avec l'offre gratuite.

👉 **Copie tout ce qui s'affiche et envoie-le-moi** dans la conversation. Ce texte ne
contient aucune clé secrète. Je vérifierai que l'outil lit correctement les données de
Taostats et je corrigerai le code si besoin.

Si je fais une correction, je te dirai exactement comment récupérer la nouvelle version.

---

## Étape 8 — Premier passage complet

Une fois le `probe` validé :

```
python -m clusterwatch run
```

- Le premier passage récupère tout l'historique des 11 wallets. Avec l'offre gratuite, il
  peut prendre **30 minutes à plus d'une heure**.
- Laisse la fenêtre ouverte et l'ordinateur allumé pendant ce temps.
- À la fin, le résumé s'affiche dans le terminal.
- Le rapport complet est enregistré dans `reports\principal\` (Windows) ou
  `reports/principal/` (Mac). Ouvre-le avec un éditeur de texte ou un lecteur Markdown.

Les passages suivants sont beaucoup plus rapides : ils ne lisent que les nouveautés.

---

## Étape 9 — Créer ton bot Telegram (~5 min)

1. Dans Telegram, cherche **@BotFather** (avec le badge bleu officiel) et ouvre la
   conversation.
2. Envoie `/newbot`.
3. Choisis un **nom** (par exemple `Mon clusterwatch`), puis un **identifiant** qui finit
   par `bot` (par exemple `moncw_perso_bot`).
4. BotFather te répond avec un **token** qui ressemble à
   `7123456789:AAH...`. Copie-le.
5. Ouvre ton `.env` (comme à l'étape 6 : `notepad .env` ou `open -e .env`) et remplace
   `colle_ton_token_ici` par ce token. Enregistre.

---

## Étape 10 — Trouver ton identifiant Telegram (chat_id) (~2 min)

1. Dans le terminal :
   ```
   python -m clusterwatch bot --show-chat-id
   ```
2. Dans Telegram, cherche ton bot par son identifiant (`@moncw_perso_bot`), appuie sur
   **Démarrer**, puis envoie-lui `bonjour`.
3. Le bot te répond « Ton chat_id est 123456789 ». Le même numéro s'affiche dans le
   terminal.
4. Ouvre ton `.env` et remplace `colle_ton_chat_id_ici` par ce numéro. Enregistre.
5. Dans le terminal, arrête la commande avec **Ctrl+C**.

Le bot ne répondra qu'à ce chat_id. Personne d'autre ne peut l'utiliser.

---

## Étape 11 — Lancer le bot

```
python -m clusterwatch bot
```

Puis, depuis ton téléphone, dans la conversation avec ton bot :

- `/aide` : liste des commandes ;
- envoie une adresse seule (`5…`) : ses infos (valeur, positions, trades récents) ;
- `/liens 5…` : les adresses actives liées à cette adresse ;
- `/add 5… mongroupe` : suivre cette adresse ;
- `/groupes` : ce qui est suivi ;
- `/run` : lancer un passage maintenant.

Le bot fait aussi un passage toutes les heures et t'écrit seulement s'il y a du nouveau.

**Le bot ne marche que tant que cette fenêtre reste ouverte et que l'ordinateur est
allumé.** Pour éviter la mise en veille :

- **Windows** : Paramètres → Système → Alimentation → mise en veille sur **Jamais** (sur
  secteur) ;
- **Mac** : Réglages Système → Batterie (ou Économiseur d'énergie) → empêcher la suspension
  automatique sur secteur.

**Après un redémarrage de l'ordinateur**, il faut relancer le bot :

1. l'étape 3 (ouvrir le terminal dans le dossier) ;
2. activer l'environnement : `.venv\Scripts\activate` (Windows) ou
   `source .venv/bin/activate` (Mac) ;
3. `python -m clusterwatch bot`.

Plus tard, si tu veux que le bot tourne 24 h/24 sans ton ordinateur (Raspberry Pi ou
petit serveur loué), demande-moi et je te guiderai.

---

## Mémo : commandes utiles

| Je veux… | Commande (dans le terminal, `(.venv)` affiché) |
|---|---|
| Lancer le bot Telegram | `python -m clusterwatch bot` |
| Faire un passage sans le bot | `python -m clusterwatch run` |
| Infos sur une adresse | `python -m clusterwatch info 5…` |
| Adresses liées à une adresse | `python -m clusterwatch links 5…` |
| Suivre une adresse | `python -m clusterwatch add 5… --group mongroupe` |
| Ne plus suivre une adresse | `python -m clusterwatch remove 5… --group mongroupe` |
| Voir ce qui est suivi | `python -m clusterwatch groups` |
| Arrêter une commande en cours | Ctrl+C |

---

## En cas de problème

| Message | Solution |
|---|---|
| `'python' n'est pas reconnu…` (Windows) | Python n'a pas été ajouté au PATH. Réinstalle-le en cochant « Add python.exe to PATH » (étape 1), ou remplace `python` par `py` dans les commandes. |
| `command not found: python` (Mac) | Utilise `python3`, ou active d'abord l'environnement (`source .venv/bin/activate`). |
| `No module named clusterwatch` | L'environnement n'est pas activé (pas de `(.venv)` au début de la ligne) ou tu n'es pas dans le bon dossier. Refais l'étape 3 puis l'activation. |
| `TAOSTATS_API_KEY non définie` | Le fichier `.env` est absent, mal nommé ou pas dans le dossier `clusterwatch`. Sous Windows, vérifie qu'il ne s'appelle pas `.env.txt`. |
| `accès refusé (HTTP 401)` | La clé Taostats est incorrecte. Recopie-la dans `.env`, sans espace ni guillemet en trop. |
| Le bot ne répond pas | Vérifie que `python -m clusterwatch bot` tourne toujours, et que `TELEGRAM_CHAT_ID` dans `.env` est bien le numéro donné à l'étape 10. |
| C'est très lent | C'est normal avec l'offre gratuite (12 s entre deux appels). Laisse tourner. |

Pour tout autre message d'erreur, copie-le en entier et envoie-le-moi, en masquant
toute clé ou tout token s'il y en a.

## Sécurité

- Ne partage **jamais** ton fichier `.env`, ta clé Taostats ou ton token Telegram.
- Si tu penses qu'ils ont fuité :
  - régénère la clé sur taostats.io ;
  - régénère le token avec `/revoke` auprès de @BotFather ;
  - mets les nouvelles valeurs dans `.env`.
