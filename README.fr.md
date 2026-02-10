# MetroClaude

> Pilotez Claude Code depuis votre telephone via Telegram.

MetroClaude fait le pont entre Telegram et Claude Code via tmux. Envoyez des prompts, approuvez les permissions, reprenez des sessions -- le tout depuis votre telephone. Votre Mac fait le travail.

**1 topic Telegram = 1 fenetre tmux = 1 session Claude Code**

```
Telephone (Telegram)     Mac (MetroClaude)           tmux
 +-----------+     long    +-------------+   send-keys   +--------+
 |  Topic A  | ---------> | bot.py      | ------------> | claude |
 |           | <--------- | monitor.py  | <------------ | (JSONL)|
 +-----------+   reponse   +-------------+   byte-offset +--------+
```

Vous pouvez basculer entre `tmux attach` et Telegram sans perdre le contexte.

## Fonctionnalites

- **UI interactive** -- permissions, AskUserQuestion, plan mode en boutons inline
- **Pairing d'outils** -- les messages tool_use s'actualisent avec un check/croix quand le resultat arrive
- **Queue intelligente** -- fusion auto des messages consecutifs, decoupage a 4096 chars, retry sur rate limit
- **Markdown** -- `telegramify-markdown` avec blockquotes extensibles et fallback texte brut
- **Securite** -- whitelist utilisateurs, assainissement des entrees, prevention injection tmux, rate limiting
- **Gestion de sessions** -- persistance entre redemarrages, reprise de sessions recentes, nettoyage auto
- **Indicateur de frappe** -- affiche "typing..." pendant que Claude travaille
- **Detection de crash** -- detecte la sortie de Claude, propose un bouton Restart avec `--resume`
- **Forward commandes** -- `/clear`, `/compact`, `/cost` relayees directement a Claude

## Demarrage rapide

### Prerequis

- macOS ou Linux
- Python 3.11+
- tmux (`brew install tmux`)
- Claude Code CLI (`claude` dans votre PATH)
- Un token bot Telegram (via [@BotFather](https://t.me/BotFather))
- Un groupe Telegram avec les Topics actives

### Installation

```bash
git clone https://github.com/EncrEor/metroclaude.git
cd metroclaude
pip install -e ".[markdown,dev]"
```

### Configuration

```bash
cp .env.example .env
```

Editez `.env` :

```bash
TELEGRAM_BOT_TOKEN=votre-token-botfather
ALLOWED_USERS=votre-telegram-user-id
```

> Obtenez votre user ID via [@userinfobot](https://t.me/userinfobot) sur Telegram.

### Lancement

```bash
python -m metroclaude
```

Ou si installe :

```bash
metroclaude
```

## Commandes Telegram

| Commande | Description |
|----------|-------------|
| `/start` | Message de bienvenue |
| `/new [chemin]` | Demarrer une nouvelle session Claude dans ce topic |
| `/stop` | Arreter la session et tuer la fenetre tmux |
| `/status` | Lister toutes les sessions actives |
| `/resume` | Reprendre une session recente (clavier inline) |
| `/screenshot` | Capturer le contenu du terminal |

Toute `/commande` non reconnue (comme `/clear`, `/compact`, `/cost`) est relayee directement a Claude.

## Comment ca marche

### Flux des messages

1. Vous tapez dans un topic Telegram
2. MetroClaude assainit l'entree et l'envoie a la fenetre tmux correspondante via `send-keys`
3. Claude Code traite le prompt et ecrit dans `~/.claude/projects/.../session.jsonl`
4. Le moniteur JSONL detecte les nouveaux octets (suivi byte-offset, polling 2s)
5. Le parser extrait les evenements text, tool_use et tool_result
6. La queue formate, fusionne et envoie sur Telegram avec markdown

### Prompts interactifs

Quand Claude demande une permission ou pose une question :
- Le poller de statut capture le terminal toutes les 2s
- Des regex detectent les permissions, AskUserQuestion, plan mode
- Un clavier inline est envoye sur Telegram
- Votre appui sur un bouton envoie les touches correspondantes a tmux

### Cycle de vie des sessions

- `/new` cree une fenetre tmux, lance `claude`, attend que le hook SessionStart enregistre l'ID de session
- Le script hook ecrit dans `~/.metroclaude/session_map.json` (avec verrouillage fichier)
- Le moniteur commence a scruter le fichier JSONL
- `/stop` tue la fenetre et nettoie tout
- Les sessions perimees (fenetres tmux mortes) sont nettoyees automatiquement toutes les 30s

## Configuration

| Variable | Requis | Defaut | Description |
|----------|--------|--------|-------------|
| `TELEGRAM_BOT_TOKEN` | Oui | -- | Token bot depuis @BotFather |
| `ALLOWED_USERS` | Oui | -- | User IDs Telegram separes par des virgules |
| `TMUX_SESSION_NAME` | Non | `metroclaude` | Nom de la session tmux |
| `CLAUDE_COMMAND` | Non | `claude` | Commande CLI Claude |
| `MONITOR_POLL_INTERVAL` | Non | `2.0` | Intervalle de polling JSONL (secondes) |
| `LOG_LEVEL` | Non | `INFO` | DEBUG, INFO, WARNING, ERROR |
| `WORKING_DIR` | Non | `~/Documents/Joy_Claude` | Repertoire projet par defaut |

## Structure du projet

```
metroclaude/
  __main__.py          # Point d'entree CLI
  config.py            # Pydantic Settings (.env)
  bot.py               # Polling Telegram, routage, dispatch evenements
  tmux.py              # Wrapper async libtmux
  monitor.py           # Polling JSONL byte-offset
  parser.py            # Parser d'evenements JSONL
  session.py           # Etat des sessions + persistance JSON
  hooks.py             # Enregistrement hook SessionStart
  hooks_session_start.py  # Script hook (tourne dans le pane tmux)
  exceptions.py        # Hierarchie d'exceptions typees
  handlers/
    commands.py        # /new, /stop, /status, /resume, /screenshot
    message.py         # Messages texte + forward commandes
    interactive.py     # Claviers inline permissions/questions
    callback_data.py   # Encodage/decodage callbacks
    status.py          # Typing manager + detection terminal
  security/
    auth.py            # Whitelist utilisateurs
    input_sanitizer.py # Prevention injection tmux
    rate_limiter.py    # Rate limiting par utilisateur + par fenetre
  utils/
    queue.py           # Queue de messages avec pairing d'outils
    markdown.py        # Markdown -> format Telegram
tests/                 # 93 tests pytest
```

## Securite

MetroClaude tourne localement sur votre Mac. Pas de serveur cloud, pas de proxy API.

- **Whitelist utilisateurs** -- seuls les `ALLOWED_USERS` peuvent interagir
- **Assainissement des entrees** -- caracteres de controle, injection backtick, patterns `$(...)` nettoyes avant tmux
- **Validation des chemins** -- `/new` n'accepte que les repertoires sous `$HOME`
- **Rate limiting** -- 20 messages/minute par utilisateur, minimum 1s entre les envois tmux
- **Erreurs generiques** -- les tracebacks sont logues, pas exposes sur Telegram
- **Verrouillage fichier** -- `fcntl.flock()` sur session_map.json pour les acces concurrents

## Inspire par

MetroClaude est un "best of" de 7 projets open source :

| Projet | Ce qu'on a pris |
|--------|----------------|
| [ccbot](https://github.com/six-ddc/ccbot) | Bridge tmux, moniteur JSONL, queue de messages, UI interactive |
| [claude-code-telegram](https://github.com/RichardAtCT/claude-code-telegram) | Securite 5 couches, gestion de sessions |
| [claudecode-telegram](https://github.com/hanxiao/claudecode-telegram) | Indicateur de frappe, commandes bloquees, markdown |
| [claude-telegram-bot](https://github.com/linuz90/claude-telegram-bot) | Reprise de session, recuperation crash |
| [bot-on-anything](https://github.com/zhayujie/bot-on-anything) | Concept d'abstraction canal |

## Developpement

```bash
# Installer avec les dependances dev
pip install -e ".[markdown,dev]"

# Lancer les tests (93 tests)
pytest tests/ -v

# Lancer avec les logs debug
LOG_LEVEL=DEBUG python -m metroclaude
```

## Licence

MIT
