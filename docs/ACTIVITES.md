# Activités Evolution : annonces interactives, équipes et calendrier

## Base du correctif

Ce correctif s'applique directement à `DiscordEVOLUTION-main (6).zip`.
Il complète le système d'activités existant : aucun second registre, aucune nouvelle
base de données et aucune nouvelle dépendance Python. Les commandes IA désactivées
restent désactivées. `organisation.py` n'est pas utilisé pour créer une seconde
liste d'inscriptions.

Le fichier `activite.png` est déjà présent à la racine de cette archive. Le code
l'utilise directement, sans hébergeur d'images, sans téléchargement au démarrage
et sans dépendre du dossier de lancement du processus.

## Parcours des membres

### Créer une sortie

`/activite creer` ouvre le formulaire existant, sans argument obligatoire :
titre, date, lieu, nombre de places et précisions. La date peut être
`demain 21h`, `vendredi 20h30` ou `18/09/2026 21:00`, toujours interprétée à Paris.
Les heures inexistantes ou ambiguës lors d'un changement d'heure sont refusées.

La durée prévue est de **180 minutes par défaut**. Pour deux heures, utiliser
`/activite creer duree:120`, puis remplir le formulaire. Le maximum est sept jours.
Le Staff peut changer la valeur par défaut avec `ACTIVITE_DEFAULT_DURATION_MINUTES`.
Les activités anciennes migrées reçoivent 180 minutes, indépendamment du réglage
choisi ensuite pour les nouvelles créations.

Le membre reçoit un aperçu privé avec l'image et l'heure de fin prévue.
Rien n'est enregistré avant **Confirmer**. Après confirmation, le bot sauvegarde
la sortie, confirme l'inscription de l'organisateur, puis publie automatiquement
l'annonce dans le salon d'organisation configuré et prépare le rôle d'équipe.

L'annonce contient le titre, les précisions, l'image, la date de Paris, les horaires
Discord, le lieu, l'organisateur, la capacité, les participants, l'attente éventuelle,
le rôle temporaire et le statut. Les boutons sont :

**S'inscrire** / **Liste d'attente**, **Se désinscrire**, **Détails**,
**Participants**, **Gérer** et **Calendrier**.

Le créateur compte dans la capacité. Celle-ci reste réglable de 1 à 100 personnes ;
huit places par défaut. Une même confirmation réessayée ne crée pas plusieurs
activités. Les brouillons privés expirent après dix minutes et ne survivent pas à
un redémarrage ; les activités confirmées et les boutons publics, eux, persistent.

### Rejoindre et quitter sans connaître un identifiant

`/activite rejoindre`, envoyé **sans argument**, ouvre une liste privée paginée.
Chaque ligne affiche le nom, la date avec l'année, les inscrits, la capacité,
les places libres et les premiers participants. Sélectionner une activité inscrit
directement le membre, sans deuxième commande ni identifiant à recopier.

L'option slash facultative `identifiant` conserve une autocomplétion lisible :

`12/09/2026 20:00 · Donjon Blop · 5/8 inscrits · 3 libres · #12`

La recherche porte sur le titre complet, la date ou le numéro, même lorsque le libellé
est abrégé. Discord limite l'autocomplétion à 25 propositions ; le panneau sans argument
est paginé et reste utilisable au-delà de cette limite.

`/activite quitter` sans argument ouvre la liste de ses inscriptions et attentes.
Sur une annonce, **Se désinscrire** agit directement sur cette activité.
Les doublons sont refusés. Quand une activité est complète, la sélection rejoint
la file d'attente, limitée à 100 personnes. Une place libérée est attribuée au premier
membre éligible en attente. Les participants promus reçoivent une notification ciblée.

Le bouton **Participants** affiche aussi une liste lisible dans Discord, avec un fichier
texte complet pour les groupes trop longs. Les inscriptions sont autorisées aux membres
validés et au Staff. Se désinscrire reste possible après perte du rôle validé, avant le départ.

### Gérer une sortie

**Gérer** est réservé à l'organisateur et au Staff : modification dans un formulaire
prérempli, annulation confirmée, réparation/publication de l'annonce.
`/activite modifier identifiant:12 duree:240` permet de modifier la durée avec le formulaire.
Prévoir cette durée avant le départ. Une nouvelle date de début doit toujours être future.

Les modifications conservent les inscriptions. Réduire la capacité sous le nombre
d'inscrits est interdit. L'augmentation peut promouvoir des membres en attente.
Les révisions empêchent deux formulaires périmés de s'écraser.

Une annulation conserve l'historique et les listes mais ferme les inscriptions
et demande immédiatement la suppression du rôle. Une ancienne activité peut être
reprogrammée explicitement ; créer une nouvelle occurrence conserve mieux son historique.

Le menu contextuel **Applications → Créer une activité** sur une annonce textuelle
du salon d'organisation reste disponible. Il préremplit le formulaire et conserve
le lien d'origine ; il ne supprime pas le message du membre.

## Rôle temporaire

Le rôle suit le format `equipe Donjon Blop 12/09/2026 20:00`, avec une troncature sûre
du titre si nécessaire, sans tronquer la date. Il ne reçoit aucune permission,
n'est pas mis en évidence et n'est pas librement mentionnable.

Seuls les participants confirmés le reçoivent, organisateur compris. Les personnes
en attente ne le reçoivent qu'à leur promotion. Quitter la liste retire le rôle.
Changer le titre ou l'horaire renomme le même rôle ; cela ne recrée pas toute l'équipe.

**Le rôle n'est plus supprimé à l'heure de début.** Les inscriptions ferment au départ,
mais le rôle reste jusqu'à `début + durée prévue`. La maintenance le supprime lors
d'un passage suivant cette fin prévue. Elle fonctionne par cycles d'une minute ;
des erreurs réseau, des limites Discord ou des permissions manquantes peuvent retarder
le traitement. Après un arrêt, la reprise traite également les équipes arrivées à échéance.

Le bot ne détecte pas la fin réelle du donjon en jeu : il utilise la fin prévue affichée.
Il conserve l'activité et ses participants après suppression du rôle.

La création du rôle utilise un nom de préparation unique enregistré avant l'appel
Discord, puis son identifiant est sauvegardé avant le renommage. Cela permet de retrouver
un rôle créé juste avant une coupure. Le bot n'adopte pas un rôle permanent simplement
parce qu'il porte le même nom qu'une équipe.

Seuls les rôles référencés par une activité ou par ce nom de préparation peuvent être
nettoyés. Un rôle géré, doté de permissions ou situé au-dessus du bot est refusé
et signalé au Staff plutôt que supprimé arbitrairement. Une erreur de rôle ne fait
pas perdre l'inscription : elle est signalée et retentée par la maintenance.

## Calendrier, annonces et rappels synchronisés

Le snapshot d'activités reste l'unique source des annonces, menus et calendriers.
Création, modification, inscription, départ, promotion et annulation mettent à jour
la même entrée. La fiche publique est éditée, pas repostée à chaque clic.
L'image déjà attachée est conservée lors des éditions.

Les sessions de calendrier encore actives sont actualisées après les écritures,
avec regroupement des éditions rapprochées. Elles relisent également les données
lors des interactions. Les vues mois/semaine, les filtres et le rendu existants
sont conservés. Une erreur de rendu ou de publication n'annule pas les données sauvegardées.

**Un calendrier expiré ou créé avant un redémarrage reste un instantané.**
Relancer `/calendrier` pour ouvrir une session actualisée. Les boutons publics des
annonces sont persistants ; les panneaux privés et calendriers gardent leur durée
de session de dix minutes.

Une annonce supprimée est marquée pour réparation. Si l'activité est toujours à venir
ou en cours, la maintenance recherche d'abord une annonce existante, puis la republie
si nécessaire. Les recherches de récupération couvrent les 200 derniers messages.
Les anciennes annonces non reconnues peuvent demander **Gérer → Réparer / publier**.

Les rappels à 24 heures et à une heure sont conservés, avec les horaires de Paris
et des mentions limitées aux inscrits. Les changements d'horaire réinitialisent
les rappels. Les promotions, modifications et annulations notifient les intéressés.
Les publications et éditions d'annonces ne déclenchent aucun ping général.

## Correction du « réfléchit… »

La première réponse après un différé avec chargement modifie explicitement
`edit_original_response`, y compris pour les fichiers et les erreurs privées.
Les réponses suivantes restent des suivis privés. Un clic sur un composant différé
sans chargement ne doit jamais remplacer l'annonce publique par sa confirmation privée.

La prévisualisation valide du formulaire est acquittée avant l'envoi de l'image.
Une mutation reçoit une confirmation dès la sauvegarde, avant les appels de rôles
et d'annonces. Les accès Discord de ces effets ne conservent plus le verrou global
des inscriptions. Les écritures du snapshot restent sérialisées.

Les commandes d'activités/calendrier ont un budget d'exécution de 45 secondes,
les écritures du snapshot de 15 secondes, la synchronisation d'une fiche ou équipe
de 25 secondes. Les appels individuels de rôles sont également bornés.
La limite de commande est détectée même si l'enveloppe de commandes de discord.py
intercepte l'annulation. Les erreurs ont une réponse explicite, dans la mesure où
l'API Discord permet encore de la transmettre.

Une écriture distante interrompue peut avoir réussi sans confirmation.
Dans ce cas, le bot suspend les nouvelles écritures et relit la console plutôt que
de repartir d'une copie mémoire possiblement périmée. Vérifier `/activite liste`
avant de refaire une création.

## Sauvegarde et migration

Le message `===BOTACTIVITES===` dans `#console` reste la source de vérité.
Le schéma passe en **version 3** : durée, fin UTC, références des équipes et métadonnées
de reprise s'ajoutent aux informations existantes. Les dates de Paris, identifiants,
participants, attente, champs inconnus et activités annulées sont conservés.
Les entrées invalides restent en quarantaine.

Le cache local `activities_data.json` demeure jetable. Il ne remplace pas un snapshot
distant illisible. Les gros snapshots utilisent une pièce jointe JSON.
Le patch ne touche ni au jeton, ni à votre `.env`, ni aux snapshots de production.

## Configuration et permissions

Ajouter ou adapter dans l'environnement du bot :

```dotenv
ORGANISATION_CHANNEL_ID=
ORGANISATION_CHANNEL_NAME=organisation
CHANNEL_CONSOLE_ID=
CHANNEL_CONSOLE=console
ACTIVITE_VALIDATED_ROLE_ID=
ACTIVITE_IMAGE_PATH=activite.png
ACTIVITE_DEFAULT_DURATION_MINUTES=180
SYNC_SLASH_COMMANDS=1
ENABLE_MEMBERS_INTENT=1
```

Renseigner les identifiants réels des salons pour éviter les ambiguïtés de noms.
Sans identifiant de rôle validé, le nom historique `Membre validé d'Evolution` reste utilisé.
Le Staff est reconnu par le rôle `Staff`, Administrateur ou Gérer le serveur.
Ne pas changer le périmètre `SYNC_SLASH_GUILD_ID` existant pour appliquer ce correctif.

Dans le salon d'organisation : Voir le salon, Lire l'historique des messages,
Envoyer des messages, Intégrer des liens et Joindre des fichiers.
Dans `#console` : lecture, historique, envoi, fichiers et droit d'épingler le snapshot.
Pour les équipes : **Gérer les rôles**, avec le rôle du bot placé au-dessus des équipes.
Activer aussi l'intention privilégiée des membres dans le portail Discord.

Une image absente, trop lourde ou interdite par les permissions ne bloque pas
l'activité : l'annonce reste interactive et affiche un avertissement.
Le fichier est limité à la plus petite valeur entre 8 Mio et la limite connue du serveur.

## Appliquer le patch

Arrêter le bot et sauvegarder le code ainsi que le dernier snapshot d'activités
dans `#console`. Placer `evolution-activites-annonces.patch` à la racine du projet,
à côté de `main.py`, puis exécuter :

```bash
git apply --check evolution-activites-annonces.patch
git apply evolution-activites-annonces.patch
python -m pytest -q
```

La première commande ne modifie aucun fichier. Un conflit indique une base différente :
ne pas forcer avec `--reject` ou écraser des modifications locales sans examen.
Ne pas réappliquer les anciens correctifs : celui-ci vise précisément l'archive `(6)`.

Redémarrer avec le mécanisme habituel, sur **une seule instance**, et laisser la
synchronisation slash se terminer. Le patch ne modifie pas les dépendances habituelles.
Les valeurs de `.env.example` ne sont pas copiées automatiquement dans votre environnement.

Pour revenir en arrière, arrêter le bot, conserver le snapshot v3 actuel et réinstaller
l'ancien code avec une sauvegarde compatible choisie consciemment. `git apply -R`
ne remet que les fichiers, pas les données ni les rôles Discord. La version précédente
peut refuser le nouveau schéma. Ne pas écraser de nouvelles inscriptions avec une ancienne
sauvegarde sans accepter leur perte ; vérifier aussi les rôles temporaires encore présents.

## Recette réelle avant mise en service

Créer une sortie à deux places avec une durée courte. Vérifier l'image, l'inscription
de l'organisateur, le rôle et la présence dans un calendrier ouvert avant la création.
Faire rejoindre deux comptes : un inscrit, un en attente. Faire quitter le premier ;
vérifier la promotion, les rôles, l'annonce et le calendrier.

Tester `/activite rejoindre` sans option et sa sélection directe, puis `/activite quitter`.
Modifier l'horaire et vérifier le renommage. Redémarrer avant le départ et cliquer sur
l'annonce existante. Supprimer l'annonce et vérifier sa réparation.

Vérifier que le rôle subsiste à l'heure de début et disparaît après la fin prévue.
Tester une annulation, un refus de rôle, un retrait temporaire de l'accès à la console,
un formulaire invalide et un membre non autorisé. Aucune de ces erreurs ne doit produire
une confirmation trompeuse ni laisser une attente volontaire sans borne.

## Limites et vérifications

Les appels Discord ne forment pas une transaction atomique entre snapshot, rôle,
annonce et notifications. Les reprises réduisent les doublons sans garantir une
livraison exactement une fois après toute coupure. Une annonce trop ancienne peut
échapper à la recherche de récupération. Les notifications ne sont pas une file
de livraison garantie.

Un départ pendant l'arrêt du bot peut ne pas produire d'événement à la reprise.
Un membre absent du cache est recherché avant une attribution, mais le bot ne purge
pas arbitrairement les historiques sur la seule base du cache.

Les tests utilisent la vraie bibliothèque discord.py avec des réponses réseau simulées.
Ils ne valident pas vos permissions ni l'affichage dans votre serveur réel.
Voir `ACTIVITES-VALIDATION.md` pour les résultats et les exclusions exacts.

## Références techniques

Documentation Discord : https://docs.discord.com/developers/interactions/receiving-and-responding
Documentation discord.py : https://discordpy.readthedocs.io/en/stable/interactions/api.html
Rôles Discord : https://discordpy.readthedocs.io/en/stable/api.html
