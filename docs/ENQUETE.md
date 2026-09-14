# `/enquete` — dossiers factuels de modération

Correctif conçu pour l'archive **DiscordEVOLUTION-main (10).zip** fournie.
Il ajoute une extension native `discord.py`, sans supprimer de commande existante
et sans modifier les bases métier du bot. Python **3.11 ou supérieur** et les
dépendances actuelles du projet sont requis ; le code cible `discord.py >=2.4,<3`.

## 1. Ce que reçoit le staff

La commande produit d'abord un TXT de synthèse, puis un ou plusieurs TXT
d'historique. Le dossier reste dans les destinations staff configurées. Aucun
message privé ni ping n'est envoyé au compte recherché ; l'accusé de réception
est éphémère, visible uniquement du demandeur.

La synthèse indique le compte par son ID Discord, les noms connus et leur
provenance, la période, le motif, l'état actuel visible sur le serveur, les
statistiques mesurées, les espaces parcourus/exclus/en erreur et les limites.
L'historique classe les extraits par salon ou fil, puis par date croissante.
Chaque message comprend sa date en heure de Paris avec décalage UTC, son auteur,
son ID, un lien source, la raison de sa sélection et son texte cité distinctement
du commentaire du bot.

Le moteur recherche les messages écrits par le compte, ses mentions Discord,
les réponses à ses messages, les ID écrits dans le texte, les citations de noms
ou d'alias, les variantes approximatives et les titres des fils. Il recherche
aussi dans le texte visible des encarts, les questions/réponses de sondages,
les noms des pièces jointes et des autocollants. Les pièces jointes ne sont
ni téléchargées, ni exécutées, ni analysées par OCR.

Des voisins de conversation sont ajoutés autour des résultats. Le réglage
par défaut est trois messages avant et trois après, dans une fenêtre d'une
heure de chaque côté. Cela ne copie pas intégralement les conversations sans
rapport du serveur. Les originaux des réponses sont également joints lorsqu'ils
ont été collectés dans le même salon et la même période. Les doublons sont retirés.

**Un propos de tiers, une citation de nom ou une abréviation ne prouve rien sur
la personne.** Les attributions certaines par ID et les pistes textuelles sont
séparées. Aucun score de réputation, verdict, profil psychologique, réseau de
relations ou rapprochement avec une identité réelle n'est produit.

## 2. Installation

Sauvegarder le projet et les données avant toute modification. Depuis le dossier
qui contient `main.py`, après y avoir placé le patch :

```bash
git apply --check discord_enquete.patch
git apply discord_enquete.patch
python -m compileall -q enquete.py utils/enquete_core.py utils/enquete_service.py
python -m unittest discover -s tests -p "test_enquete_*.py" -v
```

Le contrôle Git doit réussir avant l'application. En cas de divergence, ne pas
forcer un patch partiellement appliqué : repartir de l'archive visée ou adapter
le diff aux modifications locales. `git apply` peut également être utilisé dans
un dossier extrait non initialisé en dépôt. Ne pas appliquer deux fois le patch.

Les fichiers ajoutés sont `enquete.py`, les deux modules `utils/enquete_*.py`,
quatre fichiers de tests et ce guide. `main.py` charge `enquete` avant
`slash_commands`. `.env.example`, `.gitignore` et la documentation slash sont mis
à jour. Aucune migration de `stats`, `players` ou `moderation` n'est exécutée.

Utiliser l'environnement virtuel habituel du bot avec ses dépendances installées.
Le fuseau IANA `Europe/Paris` doit être disponible. Sur un système sans base de
fuseaux, installer le paquet `tzdata` dans cet environnement avant le démarrage.
Aucune clé d'IA ni service externe supplémentaire n'est utilisé.

### Configuration minimale dans votre vrai `.env`

Les chaînes `REMPLACER_...` ci-dessous sont des repères, **pas des valeurs valides**.
Dans Discord, activer le mode développeur et copier les identifiants des salons
et du rôle concerné. Les noms décorés ou émojis de la capture ne permettent pas
de déduire ces identifiants.

```dotenv
ENQUETE_STAFF_CHANNEL_ID=REMPLACER_PAR_ID_GENERAL_STAFF
ENQUETE_CONSOLE_CHANNEL_ID=REMPLACER_PAR_ID_CONSOLE
ENQUETE_STAFF_ROLE_IDS=REMPLACER_PAR_ID_ROLE_STAFF

ENABLE_MEMBERS_INTENT=1
ENABLE_MESSAGE_CONTENT_INTENT=1
SYNC_SLASH_COMMANDS=1
```

La console est facultative pour `destination:staff`. Une valeur vide utilise
`CHANNEL_CONSOLE_ID` si celui-ci est déjà configuré. Aucun salon n'est deviné
par son nom, créé, rejoint ou rendu public par cette extension.

Plusieurs rôles staff peuvent être indiqués par IDs séparés de virgules.
Lorsque des IDs sont fournis, un autre rôle simplement nommé « Staff » ne donne
pas accès. Sans IDs, le nom exact configuré dans `ENQUETE_STAFF_ROLE_NAME` est
utilisé ; à défaut, `IASTAFF_ROLE`, puis `STAFF_ROLE_NAME`, puis `Staff`.
Les administrateurs du serveur sont autorisés, mais les contrôles de diffusion
restent applicables.

Activer aussi **Server Members Intent** et **Message Content Intent** dans la
page Bot du Developer Portal, avec l'approbation Discord lorsqu'elle est requise.
Les indicateurs du `.env` seuls ne donnent pas l'autorisation côté Discord [1].
La présence n'est pas requise et n'est pas collectée.

Redémarrer ensuite le bot et laisser sa synchronisation slash habituelle agir.
Le réglage existant `SYNC_SLASH_GUILD_ID` peut limiter la synchronisation au serveur
de test. Le patch n'exécute aucune synchronisation distante pendant l'installation
ou les tests.

### Autorisation d'utiliser les commandes

Les quatre commandes sont déclarées pour une installation serveur uniquement,
avec **Gérer le serveur** comme permission Discord par défaut. Un administrateur
peut les autoriser à un rôle staff plus restreint dans les paramètres du serveur,
rubrique Intégrations / application / commandes. Cette dérogation Discord
n'annule jamais le contrôle du rôle staff exécuté par le bot [7].

Le bot a besoin de Voir le salon, Voir les anciens messages, Envoyer des messages
et Joindre des fichiers dans la destination. Sur les sources : Voir le salon et
Voir les anciens messages ; Connecter est également nécessaire pour lire le chat
d'un salon vocal [2]. Ne pas attribuer Administrateur uniquement pour ce module.
Le droit Voir les logs est facultatif pour l'audit ; Gérer les fils élargit la
découverte des archives privées, sans dispenser les lecteurs des contrôles.

## 3. Commandes et exemples

Dans Discord, choisir `/enquete`, puis remplir son champ `pseudo`. Il s'agit
d'une vraie commande slash, pas d'une commande préfixée `!enquete`.

```text
/enquete pseudo:coca-cola
/enquete pseudo:Illunerah aliases:illun,illuner destination:staff
/enquete pseudo:Illunerah aliases:illun contexte:5 approximatif:True
/enquete pseudo:Illunerah depuis:01/08/2026 jusqua:31/08/2026
/enquete pseudo:Illunerah reactions:True destination:les-deux
```

Les noms ci-dessus sont des exemples, pas des comptes analysés. Sélectionner
les booléens avec l'interface Discord. Le champ `pseudo` accepte aussi une mention
ou un ID Discord. L'autocomplétion aide à sélectionner le bon compte. La résolution
finale relit la liste complète des membres, plutôt que de se fier à ce cache.

Un nom ambigu est refusé : choisir l'ID exact. Pour un ancien membre, fournir
son ID et ses anciens noms dans `aliases`. Le bot ne va pas chercher son profil
global avec `fetch_user` et ne cherche pas de compte sur d'autres serveurs.

| Option | Défaut | Effet |
|---|---|---|
| `pseudo` | obligatoire | Pseudo actuel exact normalisé, mention ou ID. |
| `aliases` | aucun | Au plus 32 variantes manuelles, séparées par virgules, points-virgules ou retours à la ligne. |
| `depuis` | historique disponible | Premier jour inclus, format JJ/MM/AAAA ou AAAA-MM-JJ. |
| `jusqua` | démarrage de la collecte | Dernier jour inclus, en heure de Paris, plafonné à l'instant de départ. |
| `destination` | `staff` | `staff`, `console`, ou `les-deux`. Les deux reçoivent le dossier entier. |
| `contexte` | `3` | De 0 à 10 messages voisins de chaque côté ; les originaux des réponses restent joints s'ils sont disponibles. |
| `approximatif` | `True` | Ajouter les abréviations et petites fautes possibles, marquées comme pistes. |
| `reactions` | `False` | Vérifier également les réactions de la cible encore présentes ; plus lent. |
| `motif` | Consultation de modération | Motif inscrit dans la synthèse confidentielle. |

Pour une enquête lancée, utiliser l'identifiant de 12 caractères renvoyé :

```text
/enquete-statut identifiant:012345abcdef
/enquete-annuler identifiant:012345abcdef
/enquete-purger identifiant:012345abcdef
```

Le statut reste consultable après expiration de l'interaction initiale. Le
traitement et les publications ordinaires du bot ne dépendent pas du jeton de
cette interaction, dont la validité est limitée à quinze minutes [3].
Le demandeur et les administrateurs peuvent annuler ou purger ; un autre membre
staff peut consulter l'état. L'annulation tente aussi de retirer les messages
déjà publiés par ce dossier. Une purge incomplète conserve les identifiants
nécessaires à une nouvelle tentative.

## 4. `Illunerah`, `illun`, homonymes et anciens noms

La recherche normalise la casse, les accents et les séparateurs. `CÔCA-Cola`
et `coca cola` correspondent, mais une partie d'un mot plus long n'est pas
automatiquement une citation exacte. Les expressions fournies ne sont jamais
exécutées comme expressions régulières.

Avec seulement `Illunerah`, le mot `illun` est une **PISTE_ABREVIATION**.
Avec `aliases:illun`, il devient une **CITATION** d'un alias fourni par le staff :
cela ne transforme pas la phrase en preuve d'identité. Une faute à une seule
insertion, suppression ou substitution peut constituer une **PISTE_TYPO**.
Les variantes très courtes sont volontairement évitées. Les surnoms sans
ressemblance, les surnoms de deux lettres, les contractions particulières ou
les translittérations doivent être fournis explicitement.

La liste des noms peut être enrichie par les messages du compte et les
changements de surnom effectivement présents dans le journal d'audit.
**Le nom renvoyé sur un ancien message peut être le nom actuel** : il ne prouve
pas que ce nom était utilisé à la date du message. Les homonymes parmi les membres
actuels sont signalés. Leur absence ne garantit pas l'absence d'homonyme historique.

Un titre de fil correspondant retient son premier message disponible et son
contexte. Tous les messages du fil ne deviennent pas automatiquement des propos
sur le membre. Un nom situé seulement dans le nom du salon parent n'est pas
confondu avec le titre du fil.

La détection est lexicale et explicable, pas sémantique : « lui », un surnom
inconnu, une image contenant son nom ou une allusion implicite peuvent échapper
à la recherche. Aucun texte n'est transmis à un fournisseur d'IA.

## 5. Sources et limites historiques

Les sources examinées sont les salons lisibles, leurs chats vocaux textuels,
les fils actifs et les fils archivés découvrables, y compris les fils de forums.
La pagination des archives n'est pas filtrée sur leur date d'archivage : celle-ci
n'est pas la date des conversations. Les fils privés exigent une vérification
d'appartenance pour les personnes qui ne disposent pas du droit Gérer les fils.
Le code n'appelle jamais `join`, `unarchive` ou une modification de permissions [5].

Les messages sont parcourus par pages de 100, du plus récent au plus ancien.
Les salons alternent entre les pages pour ne pas consacrer tout un budget au
premier salon. Le filtre temporel s'applique à la date de création du message,
pas à sa date de modification ni à l'ajout de ses réactions.

**Les suppressions et les anciennes versions ne sont pas récupérables par une
simple relecture des messages actuels.** Le dossier ne comporte que les traces
déjà disponibles et autorisées. L'historique des réactions retirées n'est pas
reconstitué. Les réactions vérifiées sont un état courant ; leur date d'ajout
n'est pas connue. Le nombre de réactions reçues n'est pas un nombre de personnes
distinctes.

L'audit Discord inclut seulement les entrées dont l'acteur ou la cible correspond
à l'ID recherché, dans la période disponible. Il est exporté uniquement si le bot,
le demandeur et tous les lecteurs disposent du droit Voir les logs. Discord
conserve ces entrées pendant 45 jours [4]. Le motif enregistré est rapporté,
pas validé comme vrai. Les modifications détaillées sont limitées aux surnoms,
rôles, timeouts, mute et deaf.

Les informations actuelles comprennent les noms renvoyés, rôles, création du
compte, dernière arrivée connue, timeout et début de boost actuellement visible.
L'âge du compte n'est pas l'âge réel de la personne. Il ne s'agit pas d'un
inventaire de toutes les propriétés ou de tous les événements possibles de Discord.

### Anciennes bases déjà présentes dans ce projet

`StatsCog` possède des journaux de créations/modifications/suppressions, réactions
et mouvements vocaux, souvent sans `guild_id`. Une trace n'est admise que si son
ID de salon permet de la rattacher à ce serveur et si tous les destinataires
peuvent lire cette source. Un mouvement vocal nécessite l'accès aux deux salons.

Les dates sans fuseau sont ignorées tant que `ENQUETE_LEGACY_TIMEZONE` n'est pas
renseigné. Indiquer **le fuseau réel de la machine qui écrivait ces logs**, sans
supposer UTC ou Paris. Les heures ambiguës ou inexistantes lors du changement
d'heure ne sont pas devinées. Les textes peuvent être absents ou déjà tronqués
dans la base d'origine, notamment selon `STATS_STORE_CONTENT`. Le patch ne change
pas cette option et ne répare pas rétroactivement les données manquantes.

Les compteurs globaux de `StatsCog`, la présence et les durées supposées sont
exclus. Les anciens avertissements de `ModerationCog` et noms de personnages
de `PlayersCog` n'ont pas de provenance serveur vérifiable. Leur import est
désactivé tant que l'administrateur n'a pas confirmé cette provenance avec :

```dotenv
ENQUETE_LEGACY_DATA_GUILD_ID=REMPLACER_PAR_ID_DU_SERVEUR_VERIFIE
```

Ce réglage est une confirmation administrative, pas une preuve reconstituée.
Les avertissements sont alors décrits comme un compteur **non daté**, jamais
comme une chronologie. Les noms de personnages deviennent des alias déclarés,
pas des identités réelles déduites. Les bases existantes restent inchangées.

## 6. Confidentialité des destinations

**Choix recommandé : Général-Staff uniquement.** `les-deux` duplique le dossier
dans la console et élargit le groupe de lecteurs pris en compte. Il n'existe pas
d'envoi automatique supplémentaire à la console quand `destination:staff`.

Les destinations doivent être des salons textuels ordinaires de ce serveur,
pas des annonces ni des fils. `@everyone` ne doit pas pouvoir les voir.
Chaque compte qui voit une destination, y compris un autre bot, doit être
autorisé staff ou administrateur. Le bot enquête lui-même est exempté de cette
dernière condition puisqu'il produit le dossier.

Le bot relit les membres, les salons et les permissions des rôles. Un cache de
rôles incohérent avec la lecture distante provoque un refus temporaire. **Chaque
lecteur du rapport doit aussi pouvoir voir et lire chaque source exportée**.
Exemple : un ticket réservé à deux responsables n'est pas recopié dans un
Général-Staff lisible par dix modérateurs. Le dossier indique l'exclusion sans
divulguer le nom de l'espace. Les sources à limite d'âge ne sont pas recopiées
dans une destination non classée de même.

Les droits sont revalidés après la collecte et avant chaque fichier publié.
Si une source disparaît ou qu'une permission change, l'envoi est arrêté et les
publications déjà faites sont retirées dans la mesure où Discord l'autorise.

Le compte ciblé ne doit pas voir la destination. Un compte administrateur ne
peut pas être dissimulé par un refus de permission de salon : l'enquête échoue
plutôt que de lui promettre une confidentialité impossible.

Ces vérifications valent au moment de la publication. Elles ne protègent pas
contre un staff qui retransmet le fichier, un autre bot autorisé qui l'archive,
un administrateur du serveur ou de l'hébergement, ni un changement de lecteurs
après publication. Réviser les permissions et la rétention avec cette limite
en tête.

## 7. Budgets, gros serveurs et incidents

| Variable | Défaut | Signification |
|---|---:|---|
| `ENQUETE_DEFAULT_DAYS` | 0 | Pas de restriction de date par défaut ; ce n'est pas une absence de budgets. |
| `ENQUETE_MAX_MESSAGES` | 0 | Pas de plafond de messages parcourus ; toute valeur positive plafonne le scan global. |
| `ENQUETE_MAX_SECONDS` | 7200 | Deux heures pour découverte, lecture, audit et anciennes traces. |
| `ENQUETE_MAX_DISK_MIB` | 512 | Budget temporaire indicatif, contrôlé par lots ; environ la moitié est réservée à l'export. |
| `ENQUETE_MAX_THREADS` | 5000 | Plafond global de découverte de fils uniques. |
| `ENQUETE_AUDIT_LIMIT` | 10000 | Entrées d'audit du serveur examinées ; 0 désactive l'audit. |
| `ENQUETE_PART_MIB` | 4 | Taille cible de chaque TXT, réduite selon la limite de pièces jointes du serveur. |
| `ENQUETE_MAX_PARTS` | 64 | Jusqu'à 63 TXT d'historique, plus la synthèse fractionnable séparément. |
| `ENQUETE_CONTEXT_MINUTES` | 60 | Fenêtre temporelle de voisinage de chaque côté. |
| `ENQUETE_COOLDOWN_SECONDS` | 60 | Délai minimal entre départs de dossiers sur le serveur. |
| `ENQUETE_RETENTION_DAYS` | 7 | Échéance de retrait des publications. |
| `ENQUETE_EXCLUDED_CHANNEL_IDS` | vide | Exclure explicitement salons, parents de fils ou catégories par IDs. |

Une seule enquête s'exécute à la fois par processus du bot. Le module ne coordonne
pas plusieurs réplicas ni plusieurs processus : utiliser une seule instance avec
ce stockage. Une seconde commande est refusée plutôt que mise dans une file infinie.

Le temps maximal global de la tâche ajoute trente minutes au budget de collecte
pour l'analyse, l'export et la publication. L'attente de la configuration initiale
est elle aussi bornée. Les limitations de débit sont gérées par le SDK ; des
reprises bornées existent pour les erreurs réseau et serveur transitoires.
Un salon en erreur n'empêche pas la lecture des autres ; une incohérence de
serveur ou de pagination arrête le dossier.

La mémoire contient surtout une page, le cache de noms et la liste des membres.
L'index SQLite temporaire stocke les messages nécessaires à la recherche et au
contexte. Il ne s'agit pas d'une archive permanente. Certains calculs, index et
tris utilisent toutefois de la mémoire ; ce n'est pas une garantie de mémoire
constante indépendamment du nombre de membres/messages.

Le disque est contrôlé par lots, pas par quota système strict. Une marge est
réservée à la sélection et aux TXT ; une synthèse qui dépasse finalement le budget
provoque un refus de publication et un nettoyage. Prévoir de l'espace libre et
surveiller l'hébergement. Aucun fichier n'est coupé au milieu d'un caractère UTF-8.
Une limite d'export produit un marquage PARTIEL explicite et un manifeste de
fichiers ; les empreintes SHA-256 contrôlent la copie, pas la véracité des propos.

Sur un gros serveur, commencer par une période courte et sans `reactions:True`.
Les réactions données nécessitent des vérifications supplémentaires par emoji
et type de réaction, même lorsque le message n'est pas autrement pertinent.
Une recherche vérifie un seul utilisateur après l'ID de la cible moins un,
plutôt que de télécharger la liste entière des personnes ayant réagi [5].

Une collecte arrêtée par son budget ne se présente jamais comme exhaustive.
Les compteurs de la synthèse concernent les messages lus, et peuvent dépasser
ceux effectivement inclus dans un export tronqué. Lire les deux indicateurs
« COUVERTURE DE LA COLLECTE » et « EXPORT TXT ». Pour approfondir, relancer des
périodes plus petites ; il n'existe pas de reprise de curseur après redémarrage.

## 8. Stockage, nettoyage et utilisation responsable

Le module crée `data/enquete/`, ignoré par Git :
`tmp/` contient l'index et les TXT temporaires ; `publications.sqlite3` ne conserve
que les identifiants de dossier, serveur, demandeur, messages publiés, échéances
et états. Ni compte cible ni extrait n'est conservé dans ce journal local.

Les répertoires sont restreints au compte de service sous POSIX et les fichiers
locaux à ce compte. Ce n'est pas un chiffrement du disque. Sous Windows, appliquer
également des ACL système appropriées. Les TXT et l'index sont supprimés après
la tâche, même sur annulation et erreur traitée.

Après un arrêt brutal, des fichiers temporaires peuvent subsister. Le nettoyeur
retire les répertoires inactifs vieux de plus que le budget de collecte augmenté
d'une heure. Avec le réglage par défaut, il s'agit de trois heures, plus le délai
du passage suivant. Un redémarrage marque les anciens dossiers en cours comme
interrompus, sans reprendre silencieusement une enquête.

Les publications expirent sept jours après le lancement, avec un passage toutes
les quinze minutes quand le bot est connecté. Hors ligne, le bot ne peut rien
effacer. Les échecs de suppression sont réessayés ; les suppressions déjà faites
sont tolérées. Persister `publications.sqlite3` entre déploiements : sa perte
empêcherait le bot de retrouver les publications à purger. Ne pas exposer
`data/enquete/` via le serveur web et exclure `tmp/` des sauvegardes générales.

`/enquete-purger` retire les publications enregistrées du dossier, pas les
messages originaux du serveur ni les anciennes bases StatsCog. Les copies déjà
téléchargées ou archivées par un autre système restent hors de portée du bot.

Les secrets les plus évidents dans les TXT sont masqués au mieux : emails,
adresses IPv4, téléphones internationaux et certains jetons. **Ce masquage ne
garantit pas l'absence de données sensibles.** Les caractères de contrôle sont
rendus visibles et les propos cités restent indentés pour éviter qu'un membre
falsifie visuellement une section du rapport. Aucun contenu de conversation
n'est écrit par ce module dans les logs d'erreur.

La confidentialité d'une consultation ne justifie pas de cacher l'existence de
l'outil. Prévoir une information générale sur la modération, les données
consultées, les destinataires, la rétention et un contact de signalement.
Limiter chaque dossier à un besoin concret de modération et vérifier les
citations dans leur contexte avant toute décision.

La politique développeurs Discord impose notamment de limiter les données à
la fonctionnalité déclarée, interdit le profilage des utilisateurs/relations
et l'extraction abusive de données, et interdit de contourner les protections
de confidentialité [6]. Ce correctif n'est pas une certification de conformité
de votre déploiement : pas de surveillance systématique, de recherche d'identité
réelle, de vie privée, de MP, d'autres serveurs, de présence ou d'écoute vocale.
Les obligations applicables dépendent aussi du contexte d'exploitation.

## 9. Vérification avant production

Les tests se lancent sans jeton ni connexion au serveur :

```bash
python -m unittest discover -s tests -p "test_enquete_*.py" -v
```

`test_enquete_core.py` couvre le moteur et le TXT ; `test_enquete_service.py`
utilise des objets simulant les méthodes publiques de lecture ; le test
d'adaptateur simule le SDK pour exercer publication, annulation, purge et
nettoyage. Cela ne valide pas une connexion réelle.

`test_enquete_sdk.py` vérifie le vrai chargement du cog, les schémas slash et la
préservation des commandes natives par le catalogue lorsqu'un vrai `discord.py`
est installé. Il est explicitement ignoré si ce SDK est absent. La CI existante
collecte aussi ces tests via pytest. Aucune exécution de la CI distante ni aucun
essai sur votre serveur n'est impliqué par la présence des tests.

Faire ensuite un essai dans un serveur de test avec un compte non-staff, deux
faux homonymes, un fil privé accessible à certains modérateurs seulement et des
messages datés connus. Vérifier l'autocomplétion, le statut, l'annulation, la purge,
la confidentialité des deux destinations et l'échec après retrait d'une
permission. Ne pas tester sur une personne en imaginant que les simulations
garantissent une collecte complète.

Pour annuler l'installation, purger d'abord les dossiers encore publiés, arrêter
le bot, vérifier puis inverser le patch :

```bash
git apply --reverse --check discord_enquete.patch
git apply --reverse discord_enquete.patch
```

Retirer les commandes distantes avec l'outil de déploiement habituel si nécessaire :
retirer seulement les fichiers du module ne constitue pas une purge des
publications déjà présentes sur Discord.

## Références officielles consultées pour la conception

[1] Intents Discord et restrictions HTTP :
https://docs.discord.com/developers/events/gateway

[2] Messages, droits d'historique et contenu exposé :
https://docs.discord.com/developers/resources/message

[3] Réponses aux interactions et durée des jetons :
https://docs.discord.com/developers/interactions/receiving-and-responding

[4] Journaux d'audit Discord :
https://docs.discord.com/developers/resources/audit-log

[5] API publique `discord.py` et code de référence v2.4.0 :
https://discordpy.readthedocs.io/en/stable/api.html
https://raw.githubusercontent.com/Rapptz/discord.py/v2.4.0/discord/reaction.py
https://raw.githubusercontent.com/Rapptz/discord.py/v2.4.0/discord/channel.py
https://raw.githubusercontent.com/Rapptz/discord.py/v2.4.0/discord/threads.py

[6] Politique développeurs :
https://support-dev.discord.com/hc/en-us/articles/8563934450327-Discord-Developer-Policy

[7] Commandes d'application `discord.py` :
https://discordpy.readthedocs.io/en/stable/interactions/api.html
