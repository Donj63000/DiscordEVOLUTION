# Mémoire Render : correction ciblée pour DiscordEVOLUTION

Cible : contenu original de `DiscordEVOLUTION-main (17).zip`.
Aucune mesure de l'instance Render n'a été réalisée. Le cache identifié est un
consommateur évitable, mais pas nécessairement l'unique cause du dépassement.

## Diagnostic et correction

### Archives Build

Dans `utils/build/console_repository.py`, `_refresh()` conservait le texte
intégral de chaque instantané dans `_snapshot_payloads`. La restauration
reconstituait ce cache ; redémarrer ne supprimait donc pas cette occupation
permanente. Son volume suivait celui des archives référencées dans l'index.

Le cache devient `_snapshot_hashes` : une empreinte SHA-256 par version,
calculée après lecture et validation de l'archive. Il conserve encore un peu
de mémoire par version, mais plus les textes complets des catalogues.

L'intégrité des fragments et du contenu global, la validité UTF-8 et le
contrôle exact des données lors de la réutilisation d'un identifiant restent
vérifiés. `snapshot()` et `latest_snapshot()` continuent à relire les archives
dans Discord. Les anciennes versions restent accessibles, sans migration,
suppression d'archives ni modification de leur format.

Ce changement réduit la mémoire permanente du cache, pas tous les pics :
la vérification lit et décode encore une archive à la fois.

### Serveur de suivi HTTP

`alive.py` ajoute le mode explicite `ALIVE_SERVER=aiohttp`. Il utilise la
bibliothèque déjà présente dans `requirements.txt` et un thread du processus
du bot, sans lancer Gunicorn. Flask n'est chargé que pour les anciens modes.

Le corps de la réponse à `GET /` est conservé ; le nouveau mode le sert en
texte brut UTF-8. Les signaux restent gérés par le thread principal.
La page confirme seulement que le serveur HTTP répond, pas que Discord est connecté.

Les modes `gunicorn`, `wsgiref` et l'accès `alive:app` restent disponibles.
Sans variable explicite, le choix historique du mode est conservé.

### Mesures mémoire

`utils/runtime_memory.py` fournit les relevés ; `main.py` journalise après
chaque tentative de chargement d'extension et démarre le relevé périodique.

| Champ | Signification |
| --- | --- |
| `rss_mib` | RAM résidente du processus Python courant. |
| `peak_rss_mib` | Maximum historique de cette RAM depuis le démarrage. |
| `cgroup_mib` | Consommation du groupe de processus Linux, si disponible. |
| `limit_mib` | Limite connue de ce groupe, si disponible. |

Les valeurs sont en Mio (1 048 576 octets). Les fichiers Linux sont lus aux
emplacements usuels des conteneurs ; une valeur indisponible apparaît `n/a`.
Le cgroup peut inclure d'autres processus et des caches système. La mesure
n'est pas une somme calculée à partir du seul processus Python.

Un avertissement est émis à partir de 85 % d'une limite cgroup connue.
Il ne déclenche aucun arrêt, nettoyage ou changement des fonctions du bot.
`MEMORY_LOG_INTERVAL=60` donne un relevé par minute ; `0` désactive les relevés
périodiques, pas ceux du chargement des extensions. Un intervalle positif est
borné entre 10 et 86 400 secondes ; une valeur non entière utilise 60 secondes.
Aucun historique de mesures n'est conservé en RAM par ce module.

## Périmètre préservé

Aucune commande, aucun module fonctionnel, aucun intent ou cache Discord des
membres/messages n'est désactivé. Les quotas, les contrôles d'accès, le verrou
d'instance, les règles de calcul et les sauvegardes restent inchangés.
`requirements.txt`, les données JSON et les secrets ne sont pas modifiés.

Les changements exécutés par le bot concernent uniquement `alive.py`,
`main.py`, `utils/build/console_repository.py` et le nouveau
`utils/runtime_memory.py`. Le reste du patch contient des tests, de la
documentation et de la configuration d'exemple.

## Appliquer le patch

Conserver d'abord un commit du dépôt et des modifications locales utiles.
Depuis sa racine, là où se trouve `main.py` :

```sh
git apply --check DiscordEVOLUTION_Render_512Mo.patch
git apply DiscordEVOLUTION_Render_512Mo.patch
git diff --check
```

Ne lancer la deuxième commande que si la première réussit. Le patch vise
l'archive originale, pas l'archive corrigée fournie précédemment.
Ne pas forcer avec `--reject` si la vérification échoue : examiner les écarts.
Ne pas supprimer les sauvegardes de `#console` ou les données locales.

## Réglages Render indispensables

Conserver la commande de build :

```sh
python -m pip install -r requirements.txt
```

Utiliser une seule commande de démarrage :

```sh
python main.py
```

Ajouter ou modifier ces variables dans le service Render :

```dotenv
ALIVE_IN_PROCESS=1
ALIVE_SERVER=aiohttp
MEMORY_LOG_INTERVAL=60
```

Conserver tous les autres réglages, notamment `DISCORD_TOKEN`.
Le serveur écoute sur `0.0.0.0` et réutilise `PORT`.
`ALIVE_WORKERS` et `ALIVE_THREADS` ne concernent que Gunicorn.

Changer `.env.example` ne suffit pas à activer le mode HTTP léger dans Render.
Ne pas remplacer le véritable `.env`. Ne pas ajouter `python alive.py`
ou `gunicorn alive:app` à la commande : `main.py` lance déjà le suivi HTTP.

Enregistrer les fichiers du patch dans un commit, pousser vers la branche
déployée par le service puis vérifier le déploiement.

## Vérification après déploiement

### Blocage Discord au démarrage

Si Discord renvoie HTTP 429 avant le chargement des extensions, le processus
reste actif et ferme le client ayant échoué. Il recrée un client après 15 minutes,
puis 30 minutes, puis une heure entre les tentatives suivantes, avec une marge
aléatoire de 1 à 10 secondes. Un en-tête `Retry-After` supérieur est respecté.
Les erreurs d'identifiants, de permissions et de chargement des extensions ne
sont pas reprises par ce mécanisme. SIGTERM interrompt aussi l'attente.

`/` conserve sa réponse historique de suivi HTTP. `/ready` renvoie 503 pendant
l'attente ou une déconnexion, puis 200 après connexion et acquisition du verrou
d'instance. Ce dernier indicateur fonctionne dans les serveurs intégrés aiohttp
et wsgiref ; un processus Gunicorn séparé ne partage pas l'état du bot.
Conserver le contrôle de vie Render existant : utiliser `/ready` pour le
diagnostic, pas pour provoquer des redémarrages pendant un blocage Discord.

Le statut Render « Live » ne prouve donc pas que Discord est connecté.
Vérifier aussi `/ready`, le journal de connexion et l'acquisition du verrou.
Si le blocage persiste après une heure observée, réunir pour le support Render
l'identifiant du service et du déploiement, les horaires UTC, l'IP sortante et
les identifiants Cloudflare Ray présents dans les anciens logs. Ne pas joindre
les tokens. La reprise espacée ne lève pas elle-même un blocage Cloudflare.

Vérifier le chargement de toutes les extensions critiques, la connexion
Discord et la présence de lignes `MEMORY phase=...` dans les logs.
Tester `!ping`, la page HTTP, un build existant et un ancien build utilisant
un catalogue archivé. Vérifier aussi les commandes habituelles du serveur.

Observer la mémoire au démarrage, au repos et pendant les opérations lourdes,
notamment une actualisation de catalogue. Un relevé par minute peut manquer
un pic bref. Examiner également le maximum RSS et les métriques du service.

Le correctif ne garantit pas une RAM toujours inférieure à 512 Mo.
Si la limite reste atteinte, les mesures aideront à localiser le prochain
poste à traiter, sans supprimer des fonctions au hasard.

## Tests et limites de validation

Avec les dépendances du projet installées, exécuter :

```sh
python -m pytest -q tests_memory tests_build/test_console_repository.py tests/test_alive.py
```

La vérification locale a donné **84 tests réussis** :

| Périmètre | Résultat |
| --- | --- |
| HTTP et mesures mémoire ajoutés | 25 tests. |
| Persistance Build, avec Discord simulé | 55 tests, dont 3 nouveaux. |
| Contrats HTTP déjà présents | 4 tests. |

Les 52 tests originaux de persistance ont également réussi avant correction.
Ils couvrent notamment la restauration, les anciennes versions, les collisions,
les fragments altérés, les erreurs d'écriture et les accès concurrents.
Un essai séparé a démarré réellement `keep_alive()` en mode aiohttp, servi
30 requêtes supplémentaires et vérifié l'absence d'import Flask/Gunicorn.

**Limites :** `discord.py`, Flask et Gunicorn ne sont pas installés dans
l'environnement local. Les tests de persistance utilisent des doublures des
fichiers, exceptions et types nécessaires de Discord, hors du dépôt livré.
L'accès WSGI est testé avec une application simulée, pas un serveur
Flask/Gunicorn réel. Aucun accès Discord ou Render n'a été effectué.
La suite complète du projet n'a pas été exécutée.

Environnement local : Python 3.13.5, aiohttp 3.13.3, Pydantic 2.13.4,
pytest 9.0.2 et pytest-asyncio 1.3.0. Ces versions diffèrent de celles du
projet ; aucune mise à jour des dépendances n'est requise par le patch.
Les tests ne prouvent ni l'absence de toute régression ni la RAM en production.

## Retour arrière

Après déploiement, revenir au commit précédent et remettre `ALIVE_SERVER`
à sa valeur antérieure, par exemple `gunicorn` si c'était le mode utilisé.
L'ancien `alive.py` ne connaît pas la valeur `aiohttp`.
Aucune conversion des sauvegardes n'est nécessaire.

Pour annuler un patch local non encore suivi d'autres modifications :

```sh
git apply --reverse --check DiscordEVOLUTION_Render_512Mo.patch
git apply --reverse DiscordEVOLUTION_Render_512Mo.patch
```

Ne pas forcer si le contrôle échoue. Après un commit dédié déjà partagé,
préférer l'annulation de ce commit avec `git revert` et redéployer.

## Références techniques

- [Serveur aiohttp, version déclarée dans le projet](https://docs.aiohttp.org/en/v3.11.11/web_reference.html).
- [Application et annulation d'un patch Git](https://git-scm.com/docs/git-apply).

Le diagnostic propre au bot provient du code du ZIP fourni ; les résultats
ci-dessus proviennent des vérifications locales décrites, pas de Render.
