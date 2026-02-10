# Audit : Security & Handlers — MetroClaude vs References

**Date** : 2026-02-10
**Scope** : `security/auth.py`, `handlers/message.py`, `handlers/commands.py`, `handlers/status.py`, `config.py`
**References** : RichardAtCT/claude-code-telegram, hanxiao/claudecode-telegram, six-ddc/ccbot

---

## Synthese

MetroClaude a une securite minimaliste (whitelist user ID, blocked commands). Les references montrent des couches supplementaires significatives : validation d'input, rate limiting, audit logging, sessions auth avec expiration, et surtout la gestion d'UI interactive (permissions, plan approval). Le rapport ci-dessous detaille chaque ecart.

---

## 1. Authentication

### Ce que font les references

**RichardAtCT** (~300 lignes) :
- `AuthenticationManager` avec providers multiples (whitelist + token)
- `WhitelistAuthProvider` : user ID set, mode dev `allow_all_dev`
- `TokenAuthProvider` : generation de tokens securises (`secrets.token_urlsafe(32)`), hash SHA-256, expiration configurable (30 jours par defaut)
- `UserSession` : dataclass avec `session_timeout` (24h), `is_expired()`, `refresh()`
- Middleware `auth_middleware` : chain authentication → session → handler
- `admin_required` middleware pour commandes admin
- Nettoyage automatique des sessions expirees

**hanxiao** :
- Aucune auth. Tout message entrant est traite. Seul le `TELEGRAM_BOT_TOKEN` protege.

### Ce que nous faisons

```python
# security/auth.py (19 lignes)
def is_authorized(user_id: int) -> bool:
    allowed = get_settings().get_allowed_user_ids()
    if not allowed:
        logger.warning("ALLOWED_USERS is empty — all users blocked")
        return False
    return user_id in allowed
```

Simple check `user_id in set`. Pas de session, pas de token, pas d'expiration.

### Ecarts et recommandations

| # | Constat | Priorite | Action recommandee |
|---|---------|----------|--------------------|
| A1 | Pas de session management — chaque message refait le check complet | P2 | Ajouter un cache de session basique (dict user_id → last_seen, expire apres inactivite) |
| A2 | Pas de feedback a l'utilisateur non-autorise — le handler return silencieusement | P1 | Repondre avec un message d'erreur + log du user_id non-autorise (aide au debug + securite) |
| A3 | Pas de mode dev (allow_all) pour le developpement local | P2 | Optionnel : ajouter `ALLOW_ALL_USERS=true` en dev |
| A4 | Pas de token auth pour acces multi-device | P2 | Pas necessaire pour usage mono-user. A considerer si plusieurs utilisateurs |
| A5 | `get_allowed_user_ids()` parse le CSV a chaque appel | P2 | Cacher le resultat (set) dans la config au lieu de parser chaque fois |

---

## 2. Input Validation & Command Injection

### Ce que font les references

**RichardAtCT** `validators.py` (~300 lignes) :
- `SecurityValidator` avec 14+ patterns dangereux detectes :
  - Path traversal : `..`, `~`
  - Command injection : `` ` ``, `$(...)`, `${...}`, `;`, `&&`, `||`
  - Redirection : `>`, `<`, `|`
  - Null bytes : `\x00`
- Validation de chemins : resolution + boundary check (approved directory)
- Validation de fichiers : extension whitelist, noms interdits (.env, .ssh, id_rsa...)
- `sanitize_command_input()` : supprime caracteres dangereux, limite a 1000 chars
- `validate_command_args()` : chaque argument verifie individuellement

**RichardAtCT** `middleware/security.py` :
- Detection d'URLs suspectes (.ru, .tk, bit.ly, javascript:)
- Detection de patterns de reconnaissance (ls /, cat /etc/, whoami)
- Tracking comportemental par utilisateur
- Limite taille fichier (10MB)
- Validation MIME types

**hanxiao** :
- Aucune validation. Le texte est envoye tel quel au tmux.
- Seul echappement : `prompt.replace('"', '\\"')` pour la commande loop.

### Ce que nous faisons

```python
# handlers/message.py (lignes 43-51)
first_word = text.split()[0] if text.split() else ""
if first_word in settings.blocked_commands:
    await update.message.reply_text(...)
    return
# Puis envoi direct au tmux :
await tmux_mgr.send_message(info.window_name, text)
```

Le texte est transmis **tel quel** au tmux apres le check blocked_commands.

### Ecarts et recommandations

| # | Constat | Priorite | Action recommandee |
|---|---------|----------|--------------------|
| V1 | **CRITIQUE : Pas de sanitization du texte avant envoi tmux** — un message contenant des caracteres de controle tmux ou des escape sequences pourrait manipuler le terminal | P0 | Ajouter une fonction `sanitize_tmux_input()` qui filtre les caracteres de controle (0x00-0x1F sauf \n, 0x7F, sequences ESC) |
| V2 | Pas de detection d'injection de commandes shell dans le texte | P1 | Ajouter un check basique pour les patterns `$(...)`, backticks, etc. dans les messages envoyes a Claude (bien que Claude filtre lui-meme, defense in depth) |
| V3 | Pas de limite de longueur sur le message entrant | P1 | Ajouter un `max_message_length` (ex: 4000 chars) pour eviter les payloads excessifs |
| V4 | Le path dans `/new [path]` n'est pas valide — `Path(os.path.expanduser(args[0])).resolve()` accepte n'importe quel path | P1 | Valider que le path est dans un repertoire autorise (boundary check) |
| V5 | Pas de validation de fichiers uploades (images, documents) | P2 | Pas critique pour le MVP (on ne supporte pas les fichiers), mais a prevoir |

---

## 3. Rate Limiting

### Ce que font les references

**RichardAtCT** (~250 lignes) :
- `RateLimiter` avec token bucket algorithm
- `RateLimitBucket` : capacity, tokens, refill_rate, consume(), get_wait_time()
- Dual limiting :
  - **Request rate** : tokens/seconde avec burst capacity
  - **Cost-based** : budget max par utilisateur ($), reset quotidien
- Per-user tracking avec asyncio locks
- Cleanup automatique des utilisateurs inactifs (24h)
- Status endpoint (utilisation, budget restant)

### Ce que nous faisons

Rien. Pas de rate limiting.

### Ecarts et recommandations

| # | Constat | Priorite | Action recommandee |
|---|---------|----------|--------------------|
| R1 | Pas de rate limiting — un utilisateur peut spammer le bot sans limite | P1 | Ajouter un rate limiter simple : max N messages par minute par utilisateur (cooldown dict) |
| R2 | Pas de tracking de cout API Claude | P2 | Optionnel pour usage mono-user, utile si partage. Peut etre ajoute plus tard |
| R3 | Pas de protection contre flood tmux | P1 | Limiter les messages envoyes au tmux (ex: 1 par seconde minimum) pour eviter de surcharger le process Claude |

---

## 4. Audit Logging

### Ce que font les references

**RichardAtCT** (~350 lignes) :
- `AuditLogger` avec `AuditEvent` dataclass (timestamp, user_id, event_type, success, risk_level)
- Events types : auth_attempt, session, command, file_access, security_violation, rate_limit_exceeded
- Risk levels : low, medium, high, critical
- `_assess_command_risk()` : categorise les commandes par risque
- `_assess_file_access_risk()` : categorise les acces fichiers
- `get_user_activity_summary()` et `get_security_dashboard()`
- Storage abstrait (InMemory pour dev)

### Ce que nous faisons

```python
logger.info("Sent to '%s': %s", info.window_name, text[:80])
```

Simple logging Python standard. Pas structure, pas de risk levels, pas d'audit trail.

### Ecarts et recommandations

| # | Constat | Priorite | Action recommandee |
|---|---------|----------|--------------------|
| L1 | Pas d'audit logging structure | P2 | Pour le MVP mono-user, le logging standard suffit. Ajouter un format structurel si multi-user |
| L2 | Les tentatives d'auth echouees ne sont pas loggees | P1 | Ajouter un log.warning quand `is_authorized()` retourne False, avec le user_id tentant l'acces |
| L3 | Pas de tracking des commandes envoyees a Claude | P2 | Logger les commandes avec timestamps pour audit post-mortem |

---

## 5. Blocked Commands

### Ce que font les references

**hanxiao** :
```python
BLOCKED_COMMANDS = [
    "/mcp", "/help", "/settings", "/config", "/model", "/compact", "/cost",
    "/doctor", "/init", "/login", "/logout", "/memory", "/permissions",
    "/pr", "/review", "/terminal", "/vim", "/approved-tools", "/listen"
]
```

**ccbot** : Gere via le systeme interactif — les commandes interactives sont supportees via le keyboard inline (pas bloquees).

### Ce que nous faisons

```python
blocked_commands: list[str] = [
    "/mcp", "/help", "/settings", "/config", "/model", "/compact",
    "/cost", "/doctor", "/init", "/login", "/logout", "/memory",
    "/permissions", "/pr", "/review", "/terminal", "/vim",
    "/approved-tools", "/listen",
]
```

Liste identique a hanxiao. Detection par premier mot uniquement.

### Ecarts et recommandations

| # | Constat | Priorite | Action recommandee |
|---|---------|----------|--------------------|
| B1 | La detection est naive — `text.split()[0]` ne detecte pas `/compact` au milieu d'un message multi-ligne | P2 | Acceptable pour le MVP : Claude recoit le texte complet, et les commandes slash ne marchent qu'en debut de prompt |
| B2 | Pas de `/clear` dans la liste (hanxiao le gere comme commande speciale) | P2 | Evaluer si `/clear` doit etre ajoute ou gere differemment |
| B3 | Pas de message explicatif indiquant l'alternative | P2 | Ameliorer le message d'erreur : "Utilisez X a la place" |

---

## 6. Typing Indicator

### Ce que font les references

**hanxiao** :
```python
def send_typing_loop(chat_id):
    while os.path.exists(PENDING_FILE):
        telegram_api("sendChatAction", {"chat_id": chat_id, "action": "typing"})
        time.sleep(4)
```
Thread daemon, controle par fichier `PENDING_FILE`. Cycle 4 secondes.

**ccbot** :
- Pas de typing loop explicite — le status polling (1s) gere l'indication de travail via status messages (spinner, progress bar).

### Ce que nous faisons

```python
class TypingManager:
    async def _typing_loop(self, chat_id, topic_id):
        while True:
            await self._bot.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(4)
```

Asyncio task, cycle 4 secondes. Gestion par chat_id + topic_id.

### Ecarts et recommandations

| # | Constat | Priorite | Action recommandee |
|---|---------|----------|--------------------|
| T1 | Notre implementation est correcte et bien structuree (asyncio vs threading) | -- | OK, pas d'action |
| T2 | Le `TypingManager` n'est pas connecte au monitor — il faudrait le declencher automatiquement quand Claude travaille | P1 | Integrer avec le monitoring JSONL : start_typing quand `assistant` event debute, stop_typing quand `result` event arrive |
| T3 | Pas de gestion du topic_id dans `send_chat_action` pour les groupes | P0 | **Deja gere** : `kwargs["message_thread_id"] = topic_id` — OK |

---

## 7. Interactive UI (Permission Prompts, AskUserQuestion)

### Ce que font les references

**ccbot** `interactive_ui.py` (~180 lignes) :
- Detection des UIs interactives dans le terminal (AskUserQuestion, ExitPlanMode, RestoreCheckpoint)
- Keyboard inline avec navigation : Up/Down/Left/Right/Enter/Esc/Space/Tab/Refresh
- Tracking des messages interactifs par (user_id, thread_id)
- Edition du message existant (evite le spam) ou envoi d'un nouveau
- `clear_interactive_msg()` : nettoyage quand l'UI disparait
- Support multi-topic (thread_id)

### Ce que nous faisons

Rien. Les prompts de permission Claude ne sont pas geres. L'utilisateur ne peut pas repondre aux `AskUserQuestion` ou approuver les permissions d'outils.

### Ecarts et recommandations

| # | Constat | Priorite | Action recommandee |
|---|---------|----------|--------------------|
| I1 | **CRITIQUE : Pas de gestion des permission prompts** — quand Claude demande la permission d'executer un outil, l'utilisateur ne peut pas repondre via Telegram | P0 | Implementer la detection de permission prompts dans le terminal (parser le contenu capture) + envoyer un inline keyboard avec Approve/Deny |
| I2 | Pas de gestion AskUserQuestion — quand Claude pose une question avec choix multiples | P0 | Implementer un keyboard inline avec les options detectees |
| I3 | Pas de gestion ExitPlanMode | P1 | Detecter quand Claude est en plan mode et proposer un bouton "Exit Plan Mode" |
| I4 | Le module `handlers/` ne gere pas les callback_query (boutons inline) | P0 | Ajouter un `CallbackQueryHandler` dans le bot pour traiter les clics sur les boutons |

---

## 8. Status Polling & Exit Detection

### Ce que font les references

**ccbot** `status_polling.py` (~250 lignes) :
- Polling toutes les 1 seconde (vs notre 2s dans config)
- Detection de la commande courante du pane (`pane_current_command`)
- Notification de sortie de Claude avec bouton "Restart"
- `_restart_command()` : reconstruit la commande avec `-c` (continue)
- Nettoyage automatique des bindings stale (fenetre tmux disparue)
- Verification periodique de l'existence des topics (60s) — supprime les bindings si topic supprime
- Integration avec le systeme d'UI interactive

### Ce que nous faisons

```python
# status.py
def detect_spinner(terminal_content: str) -> bool:
    # Check last 3 lines for spinner chars

def detect_claude_prompt(terminal_content: str) -> bool:
    # Check last 3 lines for ">" prompt
```

Detection basique de spinner et prompt, mais :
- Pas de polling loop integre (le monitoring JSONL dans monitor.py gere une partie)
- Pas de detection de sortie de Claude
- Pas de bouton restart
- Pas de nettoyage des bindings stale

### Ecarts et recommandations

| # | Constat | Priorite | Action recommandee |
|---|---------|----------|--------------------|
| S1 | Pas de detection de sortie de Claude (process exit) | P0 | Ajouter un check `pane_current_command` dans la boucle de monitoring — si la commande change (claude → bash/zsh), notifier l'utilisateur |
| S2 | Pas de bouton Restart apres sortie | P1 | Envoyer un InlineKeyboard avec "Restart" quand Claude quitte — relancer avec `claude -c` |
| S3 | Pas de nettoyage des bindings stale | P1 | Quand une fenetre tmux disparait, supprimer automatiquement la session du manager |
| S4 | Pas de verification d'existence des topics | P2 | Pour les groupes avec topics, verifier periodiquement que le topic existe encore |
| S5 | Le `detect_claude_prompt` a un bug logique — `stripped.endswith(">") and len(stripped) < 20` manque des parentheses | P1 | Corriger : `(stripped.endswith(">") and len(stripped) < 20)` — actuellement le `or` est evalue incorrectement a cause de la precedence |

---

## 9. Error Handling

### Ce que font les references

**RichardAtCT** :
- Exception custom `SecurityError` pour les erreurs de securite
- Chaque middleware catch les exceptions separement
- Messages utilisateur formates avec emojis et instructions claires

**ccbot** :
- `safe_send()` wrapper pour les envois Telegram (gere les erreurs reseau)
- `rate_limit_send_message()` pour eviter le flood
- Gestion granulaire des `BadRequest` Telegram (topic invalide, message trop vieux, etc.)

### Ce que nous faisons

```python
except Exception as e:
    logger.exception("Failed to send to tmux")
    await update.message.reply_text(f"Erreur envoi : {e}")
```

Catch generique avec message d'erreur brut.

### Ecarts et recommandations

| # | Constat | Priorite | Action recommandee |
|---|---------|----------|--------------------|
| E1 | Le message d'erreur expose le traceback Python a l'utilisateur (`str(e)`) | P1 | Remplacer par un message generique + logger l'erreur complete en interne |
| E2 | Pas de retry sur les erreurs Telegram transitoires | P2 | Ajouter un retry basique (1-2 tentatives) pour les erreurs reseau |
| E3 | Pas de gestion specifique des erreurs Telegram (BadRequest, Forbidden, etc.) | P2 | Catcher `telegram.error.BadRequest` separement pour les topics supprimes, messages trop longs, etc. |

---

## 10. Path Traversal dans /new

### Ce que fait la reference

**RichardAtCT** `validators.py` :
```python
def validate_path(self, user_path):
    # Check dangerous patterns (.. ~ ${ etc.)
    # Resolve path
    # Check boundary (is_within_directory)
```

### Ce que nous faisons

```python
# commands.py ligne 67
work_dir = str(Path(os.path.expanduser(args[0])).resolve())
```

**Aucune validation**. Un utilisateur autorise peut specifier n'importe quel chemin.

### Ecarts et recommandations

| # | Constat | Priorite | Action recommandee |
|---|---------|----------|--------------------|
| P1 | `/new /etc/passwd` ou `/new ~/../../` serait accepte | P1 | Ajouter une validation : le path doit etre dans un repertoire autorise (settings.working_dir ou liste de paths approuves) |

---

## Resume par Priorite

### P0 — Bloquant pour un usage reel

| # | Description |
|---|-------------|
| V1 | Sanitization du texte avant envoi tmux (caracteres de controle) |
| I1 | Gestion des permission prompts (inline keyboard) |
| I2 | Gestion AskUserQuestion |
| I4 | CallbackQueryHandler pour les boutons inline |
| S1 | Detection de sortie de Claude |

### P1 — Important

| # | Description |
|---|-------------|
| A2 | Feedback a l'utilisateur non-autorise |
| V2 | Detection basique d'injection de commandes |
| V3 | Limite de longueur des messages |
| V4 | Validation du path dans /new |
| R1 | Rate limiting basique (messages/minute) |
| R3 | Protection flood tmux |
| L2 | Logger les tentatives d'auth echouees |
| T2 | Connecter TypingManager au monitoring JSONL |
| S2 | Bouton Restart apres sortie Claude |
| S3 | Nettoyage bindings stale |
| S5 | Fix bug precedence operateurs dans detect_claude_prompt |
| E1 | Ne pas exposer les tracebacks a l'utilisateur |
| P1 | Validation path dans /new |

### P2 — Nice to have

| # | Description |
|---|-------------|
| A1 | Cache de session basique |
| A3 | Mode dev allow_all |
| A5 | Cacher le parsing CSV |
| B2 | Evaluer /clear dans blocked commands |
| B3 | Message d'erreur plus explicatif pour commandes bloquees |
| L1 | Audit logging structure |
| L3 | Tracking commandes envoyees |
| R2 | Tracking cout API |
| V5 | Validation fichiers uploades |
| S4 | Verification existence topics |
| E2 | Retry erreurs Telegram |
| E3 | Gestion specifique erreurs Telegram |
| I3 | Gestion ExitPlanMode |

---

## Bug Identifie

**`handlers/status.py` ligne 86** :
```python
if stripped == ">" or stripped.endswith(">") and len(stripped) < 20:
```

Le `or` a une precedence inferieure au `and`, donc cette expression est evaluee comme :
```python
if stripped == ">" or (stripped.endswith(">") and len(stripped) < 20):
```

C'est probablement le comportement voulu dans ce cas precis (le `==` est un cas separe du `endswith and len`), mais l'ambiguite devrait etre resolue avec des parentheses explicites pour la lisibilite.

---

## Fichiers audites

| Fichier | Lignes | Couverture securite |
|---------|--------|---------------------|
| `metroclaude/security/auth.py` | 19 | Whitelist basique uniquement |
| `metroclaude/handlers/message.py` | 61 | Blocked commands check |
| `metroclaude/handlers/commands.py` | 240 | Auth check par handler, pas de validation path |
| `metroclaude/handlers/status.py` | 89 | Typing + spinner detection |
| `metroclaude/config.py` | 69 | Blocked commands list |

## References consultees

| Source | Fichier | Lignes approx. |
|--------|---------|----------------|
| RichardAtCT | `src/security/auth.py` | ~300 |
| RichardAtCT | `src/security/validators.py` | ~300 |
| RichardAtCT | `src/security/rate_limiter.py` | ~250 |
| RichardAtCT | `src/security/audit.py` | ~350 |
| RichardAtCT | `src/bot/middleware/auth.py` | ~120 |
| RichardAtCT | `src/bot/middleware/security.py` | ~200 (resume) |
| hanxiao | `bridge.py` | ~300 (resume) |
| ccbot | `handlers/interactive_ui.py` | ~180 |
| ccbot | `handlers/status_polling.py` | ~250 |
