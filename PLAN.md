J’ai passé en revue le code du dépôt contenu dans ton ZIP. Il y a quelques points réellement “bloquants” (risque de crash / cog inutilisable), et plusieurs points “Render/Discord” qui peuvent te faire perdre des données ou provoquer des comportements indésirables après redémarrage.

1. BUG BLOQUANT : `ActiviteCog.initialize_data()` lève `StopAsyncIteration` (crash de l’init + cog inutilisable)
   Fichier : `activite.py`
   Problème : dans `initialize_data()`, après avoir trouvé et parsé le JSON depuis `#console` (attachement ou bloc `json`) le code fait `raise StopAsyncIteration` pour “sortir” de la boucle. Sauf que cette exception n’est pas catchée, donc elle remonte et l’initialisation ne finit jamais correctement (et `self.initialized` n’est pas mis à `True`).

Extrait incriminé (tu as deux occurrences) :

* après lecture d’un fichier attaché `.json`
* après parsing d’un bloc ` ```json ... ``` `

Conséquence concrète :

* au premier redémarrage où le bot retrouve un snapshot dans `#console`, `initialize_data()` peut lever, le cog reste `initialized=False`, et `!activite` renvoie “Données en cours de chargement.” en permanence (ou le listener `on_ready` loggue une erreur).

Correctif recommandé (simple et robuste) :

* Remplacer les `raise StopAsyncIteration` par un `return` (ou un `break` + flag), et mettre `self.initialized=True` avant de sortir.
* Et surtout : ne pas appeler `initialize_data()` depuis `cog_load()` (voir point 2), car tu charges les extensions avant le READY Discord.

Patch minimal (logique, sans refactor complet) :

a) Dans `initialize_data()` : remplacer les deux `raise StopAsyncIteration` par quelque chose comme :

```py
self.activities_data = data_loaded
logger.info("Données surchargées ...")
self.initialized = True
logger.info("ActiviteCog: données initialisées.")
return
```

b) Dans `cog_load()` : ne plus faire `await self.initialize_data()` (tu as déjà un `@listener on_ready` dans ce cog, c’est l’endroit correct pour initialiser, car là les guilds/channels sont disponibles).

2. PROBLÈME STRUCTUREL “Render + setup_hook” : plusieurs cogs tentent de lire `bot.guilds` / l’historique Discord avant le READY
   Fichier clé : `main.py`
   Contexte : dans `main.py`, tu charges les extensions dans `setup_hook()`. Dans discord.py, à ce moment-là, le bot n’est pas encore “READY” (cache guilds/channels pas encore rempli). Donc tout code qui fait “lecture d’historique”, “resolve channel par nom”, ou même `for guild in bot.guilds:` au chargement du cog peut ne rien trouver.

Le pattern correct, que tu utilises déjà dans certains cogs (ex: `players.py`), c’est :

* en `cog_load()`: créer une tâche (`asyncio.create_task`) qui fait `await bot.wait_until_ready()` puis charge la data
* ou utiliser un `@commands.Cog.listener() async def on_ready()` + guard “initialisé ou non” (sans bloquer `setup_hook`)

Cogs concernés dans l’état actuel (impact persistance) :

* `activite.py` : init fait des accès guild/channel/console dans `cog_load` (et en plus le StopAsyncIteration)
* `stats.py` : `store.load()` est appelé dans `cog_load()` → à froid, `bot.guilds` peut être vide, donc pas de chargement console
* `up.py` : `load_promotions_data()` est appelé dans `cog_load()` → même problème, et donc promotions_data vide
* `job.py` : moins critique car tes commandes rechargent souvent depuis console, mais l’init “early” ne sert pas et peut conduire à états transitoires incohérents (et des boucles auto_prune qui tournent avant une vraie restauration)

Risque principal (celui qui fait mal sur Render) :

* au redémarrage, ces cogs ne restaurent pas depuis `#console`, repartent “vierges”, et le premier `dump` peut écraser indirectement l’historique (nouveau snapshot vide “plus récent”).

Correctif recommandé (pattern homogène sur tous les cogs persistants) :

* Ne jamais faire de lecture Discord “cache/historique” dans `cog_load()` de manière bloquante.
* Dans `cog_load()`: démarrer une tâche d’initialisation post-ready.

Exemple générique (pattern à copier/coller) :

```py
class MyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._init_lock = asyncio.Lock()
        self._init_task: asyncio.Task | None = None
        self.initialized = False

    async def cog_load(self):
        if self._init_task is None or self._init_task.done():
            self._init_task = asyncio.create_task(self._post_ready_init())

    async def _post_ready_init(self):
        await self.bot.wait_until_ready()
        async with self._init_lock:
            if self.initialized:
                return
            await self.initialize_data()  # ici tu peux toucher bot.guilds, history, etc.
            self.initialized = True
```

3. `stats.py` : sauvegarde périodique potentiellement trop tôt + logs non bornés (gonflement infini)
   Fichier : `stats.py`

Problèmes :
A) `self.save_loop.start()` est lancé dans `__init__()` immédiatement. Selon le comportement exact de `tasks.loop`, tu prends le risque que la boucle de sauvegarde tourne avant que la data historique soit chargée (et donc pousse un snapshot “template” dans `#console`, ou écrase le fichier local).
B) Tu append dans `self.stats_data["logs"]["messages_created"]` (et autres logs) sans limite. Sur un serveur actif, ça grossit très vite, et tu vas :

* exploser la taille du JSON
* spammer des fichiers attachés lourds
* consommer de la RAM

Correctifs recommandés :

* Démarrer `save_loop` uniquement après la restauration (post-ready).
* Ajouter un cap (env var) sur les listes de logs, ex: 1000 ou 5000 entrées max.

Exemple de cap minimal :

```py
MAX_LOGS = int(os.getenv("STATS_MAX_LOGS", "2000"))

lst = self.stats_data["logs"]["messages_created"]
lst.append(msg_log)
if len(lst) > MAX_LOGS:
    del lst[:-MAX_LOGS]
```

4. `up.py` : scan historique complet `limit=None` = énorme risque de rate-limit / freeze, et init trop tôt
   Fichier : `up.py`

Deux problèmes distincts :

A) Restauration promotions_data trop tôt
`load_promotions_data()` est appelé dans `cog_load()`, donc avant READY : `self.bot.guilds` vide ⇒ pas de `console_channel` ⇒ pas de restauration ⇒ `promotions_data` vide au redémarrage.

Fix : faire la restauration post-ready (task ou `before_loop`).

B) `scan_entire_history()` parcourt absolument tout l’historique (`limit=None`) de tous les channels
Ça, sur un Discord vivant, c’est le genre de truc qui :

* prend des heures
* déclenche des rate limits
* rend le bot quasi inutilisable pendant le scan
* et parfois se fait tuer par Render (watchdog / timeout implicite) ou crash sur exceptions réseau

Fix conseillé (par ordre de robustesse) :

1. Meilleure approche : compter en “temps réel” via `on_message` + persister les compteurs dans `#console` (tu as déjà une base de persistance dans ce projet). Plus besoin de scanner l’historique.
2. Approche intermédiaire : ne scanner que les X derniers jours (`after=...`) et/ou limiter à N messages par channel (ex: 2000).
3. Approche rapide : augmenter l’intervalle et réduire drastiquement le scope.

Exemple de scan “limité” (si tu veux rester proche du code actuel) :

```py
from datetime import timedelta

SCAN_DAYS = int(os.getenv("UP_SCAN_DAYS", "180"))
SCAN_LIMIT = int(os.getenv("UP_SCAN_LIMIT_PER_CHANNEL", "5000"))
after = discord.utils.utcnow() - timedelta(days=SCAN_DAYS)

async for msg in channel.history(limit=SCAN_LIMIT, after=after, oldest_first=False):
    ...
```

5. `sondage.py` : ID affiché faux dans le footer + clôture manuelle non restreinte
   Fichier : `sondage.py`

Bug fonctionnel :

* Tu affiches dans le footer l’ID de `ctx.message.id` (le message de commande) au lieu de l’ID du message du sondage (`sondage_message.id`).
  Résultat : l’ID communiqué aux utilisateurs n’est pas celui à utiliser pour `!close_sondage`.

Problème de contrôle d’accès :

* `!close_sondage <id>` est utilisable par n’importe qui (pas de check Staff, pas de check “auteur du sondage”).

Correctif minimal :

* Footer: mettre `sondage_message.id`.
* Close: restreindre à l’auteur du sondage OU au rôle Staff (ou une permission type `manage_messages`).

6. Persistance Render : plusieurs états reposent encore sur des fichiers locaux ou de la RAM
   Tu l’as bien expliqué : Render efface le filesystem/état entre redémarrages. Or certains modules stockent encore uniquement en local/mémoire :

* `welcome.py` : `welcome_data.json` local uniquement → perdu après restart (risque de re-déclencher le parcours welcome / doublons)
* `sondage.py` : `POLL_STORAGE` en mémoire uniquement → tous les sondages “en cours” sont perdus au restart
* `moderation.py` : warnings en JSON local (et ce cog n’est même pas chargé dans `main.py` actuellement), donc non persistant sur Render

Recommandation : pour tout état utile après restart, utiliser le même mécanisme que le reste (snapshot dans `#console`, via `ConsoleStore` ou un store dédié).

7. Amélioration de robustesse : lock “singleton” acquis après le chargement des extensions
   Ce n’est pas un “bug” immédiat, mais c’est un vrai risque de comportements en double.

Dans `main.py` :

* Tu charges les cogs dans `setup_hook()` (avant lock)
* Le lock singleton est acquis dans `on_ready()`

Conséquence : si Render lance deux instances (deploy/restart transitoire), les deux peuvent charger des cogs, démarrer des tasks, et potentiellement agir (envoyer des messages) avant que la non-leader ne s’éteigne.

Atténuations possibles :

* Mettre des guards dans les loops/actions sensibles : `if not getattr(self.bot, "_singleton_ready", False): return`
* Ou acquérir le lock le plus tôt possible via `CHANNEL_CONSOLE_ID` + `fetch_channel()` (HTTP) avant de charger les extensions (ça demande un peu de refactor, mais c’est faisable et plus propre).

Ce que j’ai vérifié côté qualité globale

* La suite de tests incluse dans `tests/` passe (90 tests OK, 1 skipped) une fois les dépendances installées.
* Les tests ne couvrent pas les zones “à risque” que je liste ci-dessus (notamment `activite.py` et `up.py`), donc ces bugs peuvent exister sans être détectés par la CI locale.

Si tu veux, je peux te proposer un patch “prêt à commit” (diff git) pour :

* corriger `activite.py` (StopAsyncIteration + init post-ready),
* sécuriser `stats.py` (init post-ready + save loop + cap logs),
* sécuriser `up.py` (restauration post-ready + stratégie de comptage sans scan infini),
* corriger `sondage.py` (ID + permissions).

Mais même sans refactor lourd, corriger les points 1, 2, 4 et 5 te donnera un gain immédiat en stabilité sur Render.
