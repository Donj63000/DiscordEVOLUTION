# Correctif de restauration de l'annuaire métiers

## Cible et périmètre

Ce correctif cible `DiscordEVOLUTION-main (12)(1).zip`, la dernière archive fournie.
Il porte sur `job.py`, un lecteur de sauvegardes dédié, les tests et leur configuration.
Il n'ajoute aucune dépendance, ne modifie pas le schéma de `jobs_data.json` et ne
modifie ni le moteur EXO/FM, ni Defender, ni les autres registres de `#console`.

Le correctif FM Rétro v3 peut déjà être appliqué : les deux patchs portent sur des
fichiers distincts. La vérification de livraison applique réellement FM v3 puis
ce correctif à une extraction indépendante de l'archive.

Les captures montrent une indisponibilité de lecture, pas une preuve que les
métiers ont été supprimés. Aucune sauvegarde de métiers de production n'a été
fournie avec l'archive : il n'est donc pas possible de confirmer depuis celle-ci
le contenu encore présent dans le serveur Discord.

## Défaut reproduit dans le code d'origine

Le chemin d'écriture bascule les gros registres vers une pièce jointe
`jobs_data.json`. Le message contient seulement :

```text
===BOTJOBS=== etag:...
```

Le lecteur de consultation, distinct du lecteur de mutation, exigeait le mot
`fichier` dans le texte avant de lire une pièce jointe. Le propre format du
rédacteur était donc ignoré. La reproduction utilise 40 fiches synthétiques,
soit 2 820 octets : le lecteur original retourne `False` sans même lire le fichier.

Le nouveau lecteur reconnaît le nom exact de la pièce jointe, sans exiger cette
mention. Les prochaines écritures ajoutent néanmoins `(fichier)` pour rester
lisibles par l'ancien lecteur en cas de retour de code.

## Lecture et restauration non destructive

La consultation `/job`, les outils Evo, la préparation des mutations et leur
confirmation emploient désormais le même lecteur et le même décodage.

Sont acceptés les JSON complets inline, les anciens messages sans `etag`, les
fichiers `jobs_data.json` avec ou sans la mention `(fichier)`, et les anciens
messages du bot portant ce fichier sans marqueur. Le bot vérifie l'identifiant
numérique de l'auteur ; un utilisateur ou un webhook ne peut pas remplacer
l'annuaire en déposant simplement un fichier dans le salon.

Une pièce jointe fait autorité sur l'aperçu textuel. Un fichier inaccessible,
tronqué, ambigu, trop volumineux, ou dont l'empreinte ne correspond pas ne
déclenche jamais un repli sur un aperçu ou sur un ancien registre vide.
Les doublons de clés JSON sont refusés. La limite propre au lecteur est de
8 Mio ; l'écriture respecte aussi la limite du serveur.

La recherche consulte le message déjà connu, les épingles et une page récente.
Entre les sources trouvées, elle conserve l'ordre historique de version :
date d'édition, sinon date de création, puis identifiant. Une modification du
message canonique ancien est donc bien considérée comme une nouvelle version.

Lorsqu'aucune source n'est trouvée, la recherche poursuit l'historique par pages,
au-delà de l'ancienne fenêtre de 200/300 messages. Elle est bornée à 30 secondes
et refuse une pagination qui n'avance plus. Lorsqu'une source connue, épinglée
ou récente existe, elle ne reparcourt pas systématiquement toute l'histoire du
salon. Il ne s'agit donc pas d'une fusion exhaustive de toutes les anciennes
copies : les registres ne sont jamais fusionnés automatiquement.

La compatibilité de lecture des épingles couvre la coroutine de discord.py
2.4/2.5 et l'itérateur paginé de 2.6+, avec `limit=None` pour ce dernier.

Une lecture échouée laisse le registre mémoire et le fichier local intacts.
Une lecture seule ne remplace pas non plus une ancienne copie locale.
Un registre distant explicitement vide reste distinct d'un registre absent.
Une source absente avec mémoire, identifiant connu, état incertain ou fichier
local interdit l'initialisation silencieuse d'un annuaire vide.
La toute première inscription reste possible sur une installation réellement
neuve, sans indice de données antérieures, après parcours concluant de l'historique.

## Protection contre les suppressions involontaires

Le nettoyage automatique attend maintenant `wait_until_ready()` ; il n'est
plus exécuté immédiatement depuis `cog_load`, appelé avant la connexion.

Pour nettoyer, l'énumération REST des membres doit aboutir sur tous les serveurs
concernés. Toute erreur, expiration, absence de décompte fiable ou liste plus
courte qu'attendu annule le nettoyage sans écrire. Le cache apporte seulement
des preuves positives de présence ; son incomplétude n'est pas une preuve de départ.

Les anciennes fiches indexées par un pseudo sont conservées. La simple
homonymie avec un compte actuel ne les migre plus automatiquement vers un
identifiant, ni ne les supprime lorsqu'un autre membre part. Leur rattachement
reste une opération de maintenance nécessitant une preuve de propriété.
Sur plusieurs serveurs, un événement de départ isolé laisse le recensement
complet décider du nettoyage du registre historique commun.

L'énumération de membres n'est pas un instantané transactionnel Discord.
Le correctif protège les arrivées connues du cache et les recensements incomplets ;
il ne prétend pas offrir une transaction distribuée avec le serveur Discord.

La commande de nettoyage de `#console` protège aussi les anciennes sauvegardes
du bot qui ne possèdent que la pièce jointe `jobs_data.json`.

## Écritures et concurrence

Le verrou de mutation, les confirmations, l'annulation des anciennes saisies
et l'idempotence des requêtes Evo restent en place. La lecture du registre
distant est refaite avant mutation, et le fichier écrit est relu avant confirmation.

L'épinglage devient une amélioration de repérage, pas une condition de réussite
de la sauvegarde : son échec est journalisé, mais ne transforme pas une écriture
relue et vérifiée en échec. La copie locale n'est remplacée qu'après confirmation,
par un fichier temporaire dans le même dossier, `fsync` puis `os.replace`.
Un échec de cette copie locale est journalisé et ne tronque pas le fichier précédent.

Ces garanties portent sur un processus du bot. Le verrou de singleton existant
reste nécessaire pour éviter deux instances qui écrivent simultanément.
Il n'y a pas de compare-and-swap atomique entre plusieurs processus Discord.

## Déploiement

Avant le redéploiement, conserver hors du bot toute copie existante de
`jobs_data.json` et les messages `===BOTJOBS===`. Ne pas nettoyer `#console`,
ne pas remplacer la sauvegarde par `{}` et ne pas réinscrire tous les joueurs
pour masquer l'indisponibilité.

Depuis la racine du projet :

```bash
git apply --check --whitespace=error-all DiscordEVOLUTION-metiers-restauration.patch
git apply --whitespace=error-all DiscordEVOLUTION-metiers-restauration.patch
```

La première commande doit réussir. En cas de conflit avec une autre version,
ne pas forcer l'application. Redéployer ensuite le bot avec ses dépendances
habituelles, puis tester `/job liste` et `/job rechercher`.

Vérifier dans le salon configuré par `CHANNEL_CONSOLE_ID` / `CHANNEL_CONSOLE`
que le bot peut voir le salon et lire l'historique. L'API Discord peut retourner
une liste vide en l'absence de `READ_MESSAGE_HISTORY` [1] ; le correctif contrôle
donc ces permissions avant d'interpréter une absence de messages.
Les permissions d'écriture et d'envoi de pièces jointes restent nécessaires
pour sauvegarder des modifications. La permission d'épinglage est souhaitable,
mais n'est plus bloquante.

Le nettoyage complet nécessite l'intent membres du code et du portail développeur
selon la configuration Discord [3]. Son absence doit désormais suspendre le
nettoyage plutôt que supprimer des professions.

Le message de journal attendu lors d'une récupération réussie ressemble à :

```text
Jobs: snapshot loaded guild_id=... message_id=... records=...
```

Un échec indique désormais une cause exploitable : permissions, sauvegarde
absente, fichier illisible ou dépassement de délai. Sur un très grand historique,
épingler le bon message **déjà publié par le bot** permet de le retrouver rapidement.
Une pièce jointe republiée par un compte utilisateur n'est volontairement pas
importée automatiquement.

`JOB_CONSOLE_HISTORY_LIMIT` devient la taille de page du parcours de récupération
(plafonnée à 1 000), pas une coupure définitive après les premiers messages.
Sa valeur habituelle 200/300 suffit. La valeur `0` autorise encore une source
connue ou épinglée, mais ne permet pas de conclure qu'une nouvelle installation
est vide. Le TTL de consultation de `JOB_CONSOLE_SYNC_TTL` est conservé.
Il n'est pas nécessaire d'activer `JOB_ALLOW_LOCAL_FALLBACK` pour ce correctif.
Cette option n'autorise pas à republier aveuglément une copie locale périmée.

## Limites de restauration

Le correctif relit une sauvegarde existante et valide. Il ne reconstitue pas des
professions depuis les snapshots joueurs, activités, budget ou anciens membres.
Il ne répare pas silencieusement une sauvegarde corrompue et ne remplace pas un
registre explicitement vide par un ancien plus rempli, ce qui pourrait annuler
des suppressions légitimes.

Si le message et toutes ses copies ont réellement été supprimés, une sauvegarde
conservée par le Staff est nécessaire. Il n'existe pas de récupération de données
absentes de tous les supports. Aucune opération sur les métiers réels du serveur
n'a été effectuée pendant la préparation de ce patch.

## Tests

La suite `tests_jobs` contient des scénarios de format pur et des scénarios métier
paramétrés en deux modes : isolé et SDK natif. Le mode isolé exécute les corps
originaux de `job.py` et `utils/discord_history.py` par AST, avec des doubles du
transport. Il ne réimplémente pas leurs méthodes ; seuls les imports, la base
`Cog` et les décorateurs du SDK sont retirés. Il ne remplace aucun module Discord
globalement. Le mode natif utilise les classes réelles lorsque `discord.py` est
installé. Aucun mode n'appelle un serveur Discord réel.

Les régressions couvrent notamment la pièce jointe sans mention `fichier`,
la recherche au-delà de 1 100 messages, la pagination des épingles, les permissions,
les sources corrompues, les erreurs réseau, les annuaires explicitement vides,
les confirmations d'écriture, l'annulation, les écritures concurrentes,
les anciennes fiches, le démarrage et le nettoyage incomplet.

Commandes dans l'environnement habituel du bot :

```bash
python -m pytest -q tests_jobs
python -m pytest -q tests/test_job_command.py tests_evo/test_jobs_consistency_hotfix.py tests_evo/test_job_declarations_visibility.py tests/test_slash_commands.py
```

La configuration pytest inclut `tests_jobs` dans les tests découverts par défaut.
Les deux anciens tests d'initialisation attendent désormais l'absence de migration
automatique des pseudos. Le journal de livraison distingue les tests exécutés
des cas SDK sautés si cette dépendance manque.

## Références techniques

Sources primaires consultées le 17 septembre 2026 :

[1] Discord, Message Resource, lecture des messages, permissions et épingles :
https://docs.discord.com/developers/resources/message

[2] discord.py, source v2.6.4, `Messageable.pins`, pagination et compatibilité :
https://raw.githubusercontent.com/Rapptz/discord.py/v2.6.4/discord/abc.py

[3] discord.py, Gateway Intents, cache membres et récupération des membres :
https://discordpy.readthedocs.io/en/stable/intents.html

[4] discord.py, tâches, `before_loop` et `wait_until_ready` :
https://discordpy.readthedocs.io/en/stable/ext/tasks/index.html
