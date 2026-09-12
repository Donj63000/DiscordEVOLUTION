# Refonte du calendrier des activités

## Objectif et périmètre

Le calendrier ouvre le mois en cours avec un aperçu graphique et la liste des
activités dans Discord. Une vue hebdomadaire reste disponible. Il concerne
les activités de `ActiviteCog`, créées avec `/activite creer` ou `!activite creer`.

Le patch est construit sur l'archive `DiscordEVOLUTION-main (1).zip` fournie.
Il ne modifie ni les identifiants d'activité, ni le format JSON, ni les marqueurs
de sauvegarde dans `#console`. Aucun nouveau paquet n'est ajouté aux dépendances.

## Diagnostic de l'ancienne version

| Problème constaté dans le code | Correction |
| --- | --- |
| Une seule échelle de consultation | Mois courant par défaut ; agenda de la semaine au choix |
| Texte minuscule et coupé, sur un fond décoratif chargé | Informations en texte Discord ; PNG opaque et contrasté en complément |
| Copie des événements conservée à l'ouverture | Relecture du stockage en mémoire à chaque navigation et actualisation |
| Détails et inscriptions absents du calendrier | Fiche privée et boutons reliés aux commandes d'activité existantes |
| Rendu Matplotlib synchrone dans le parcours interactif | Rendu Pillow dans un thread, concurrence et cache limités |
| Bouton poubelle ambigu et fin de session peu explicite | Bouton Fermer, message conservé et expiration signalée |

## Utilisation

Tape simplement `/calendrier` pour ouvrir le mois en cours, déterminé avec la date
du jour en Europe/Paris. Aucune date ni autre option n’est demandée à l’ouverture.
Les paramètres restent facultatifs et se choisissent dans le menu `/` de Discord.

```text
/calendrier
/calendrier vue:semaine
/calendrier date:11/09/2026
/calendrier filtre:inscrit prive:True
/calendrier vue:mois date:01/10/2026 filtre:disponibles
```

| Paramètre | Valeurs | Par défaut |
| --- | --- | --- |
| `vue` | `semaine`, `mois` | `mois` |
| `date` | `JJ/MM/AAAA`, entre 1970 et 2100 | Aujourd'hui à Paris |
| `filtre` | `toutes`, `inscrit`, `disponibles` | `toutes` |
| `prive` | Booléen Discord, vrai ou faux | Faux : message visible dans le salon |

La semaine commence le lundi. Les dates fixes sont affichées en Europe/Paris ;
la fiche donne aussi un horodatage affiché dans le fuseau du lecteur par Discord.
Les délais relatifs, comme « dans deux heures », sont laissés au client Discord.

Les filtres signifient respectivement : toutes les activités non annulées de la
période ; les activités auxquelles tu participes ; les activités non commencées
ayant au moins une place. Ce dernier filtre peut inclure une activité à laquelle
tu es déjà inscrit, puisqu'il indique les places restantes dans le groupe.

La capacité reste celle du bot : **8 membres**. `GROUP_CAPACITY`, dans
`utils/calendar_data.py`, est la constante commune utilisée par l'affichage et
par l'alias historique `activite.MAX_GROUP_SIZE`.

Les boutons permettent de changer de période, revenir à aujourd'hui, actualiser,
saisir une date, parcourir les pages ou atteindre la prochaine activité correspondant
au filtre. Cette dernière action place aussi la pagination sur la bonne activité.

L'agenda contient au maximum six activités par page. Le menu affiche les activités
de cette page ; les pages suivantes restent accessibles, sans limite arbitraire
du nombre total d'activités consultables.

Le menu d'activité ouvre une **fiche privée** : titre, description, organisateur,
participants, statut, commandes alternatives et boutons Rejoindre / Quitter.
Le bouton Texte complet permet de consulter les textes abrégés dans un fichier
UTF-8 privé, dans la limite de 1 Mio.

Le PNG mensuel montre au maximum deux activités par jour et un indicateur `+N`
pour les suivantes. Il est un aperçu, pas la seule source d'information :
les listes paginées et les fiches restent disponibles pour toutes les activités.

Le préfixe historique `!calendrier` ouvre lui aussi le mois en cours sans argument :

```text
!calendrier
!calendrier mois 01/10/2026 toutes
!calendrier semaine "" inscrit
```

La confidentialité utilise les réponses privées Discord et nécessite donc la
commande `/calendrier`, pas une commande avec préfixe.

## Sessions et accès

Le message public est consultable par le salon, mais seuls les boutons de l'auteur
sont utilisables par cet auteur. Un autre membre reçoit une invitation privée à
ouvrir son propre calendrier. Les fiches et confirmations d'inscription restent
privées, même si l'agenda est public.

Une session expire après dix minutes sans interaction autorisée. Les contrôles sont
désactivés et le pied de page est mis à jour lorsque Discord permet encore l'édition.
Fermer retire les contrôles mais ne supprime ni le message ni les activités.

La navigation relit les données, mais il n'y a pas d'actualisation automatique
périodique des messages déjà publiés. Après une inscription, la fiche est actualisée ;
utilise Actualiser pour mettre à jour un agenda ouvert en parallèle.

Les boutons ne sont pas persistants après un redémarrage du bot. Relance
`/calendrier` après un redémarrage ou l'expiration d'une session. Les activités,
elles, continuent d'utiliser la persistance existante.

Les actions Rejoindre / Quitter passent par les mêmes contrôles que `!activite` :
rôle validé, contrôles globaux, capacité, doublons et sauvegardes. Le contexte
représente le membre qui clique, jamais l'auteur bot du message. Les confirmations
désactivent les mentions notifiantes. Une inscription via la commande join est
désormais également refusée si l'heure de début est déjà passée.

## Architecture et robustesse

| Fichier | Responsabilité |
| --- | --- |
| `utils/calendar_data.py` | Instantanés immuables, validation, dates, filtres, pagination, textes bornés |
| `utils/calendar_view.py` | Agenda, interactions, formulaire de date, fiches, sessions et erreurs |
| `calendrier.py` | Dessin Pillow, mesure des titres en pixels, génération PNG et cache |
| `activite.py` | Source des activités, commande, permissions, réutilisation des actions existantes |
| `utils/slash_catalog.py` | Quatre options explicites et conservation des valeurs positionnelles par défaut |
| `utils/slash_support.py` | Confidentialité décidée avant l'accusé de réception et exécution contrôlée des boutons |

Les interactions de navigation sont acquittées avant le rendu ou l'attente d'un
verrou. Chaque session sérialise ses modifications pour éviter deux navigations
qui se chevauchent. Une erreur d'édition ne laisse pas volontairement la date de
navigation désynchronisée de l'état précédent.

Le rendu mensuel s'exécute avec `asyncio.to_thread`. Deux rendus simultanés au
maximum sont autorisés par instance de renderer. Le cache LRU contient au maximum
24 images et 8 Mio de PNG, avec des signatures de clé de taille fixe. Les titres,
horaires, activités visibles et le jour courant interviennent dans l'invalidation.

Les pièces jointes sont remplacées à chaque édition ; passer du mois à la semaine
retire l'ancienne image. Les flux de fichiers sont fermés après utilisation.
Si le rendu ou l'envoi de l'image échoue, une tentative d'affichage sans image
conserve l'agenda et ses menus. Une panne générale de Discord reste une erreur
signalée, pas une garantie de publication.

Les champs, titres et options sont bornés, y compris pour les caractères représentés
par deux unités UTF-16. Le rendu utilise des mesures en pixels pour les ellipses.
Les mentions, contrôles invisibles et marqueurs de direction sont neutralisés.
Une activité invalide est ignorée individuellement et comptabilisée dans un
avertissement ; les détails sont journalisés au niveau DEBUG.

Les polices sont recherchées parmi celles déjà installées, notamment celles de
Matplotlib, avec un repli Pillow. Aucun téléchargement n'a lieu pendant une commande
et aucune police n'est livrée dans ce patch. Le texte Discord reste la référence
pour les titres longs ou les émojis non couverts par la police du PNG.

## Permissions

Pour un agenda public dans le salon, conserve les droits Voir le salon, Envoyer
des messages et Intégrer des liens. Joindre des fichiers est nécessaire pour le
PNG mensuel et l'export Texte complet, mais pas pour consulter l'agenda textuel.

Les droits de gestion des rôles et la hiérarchie nécessaires aux inscriptions sont
ceux du fonctionnement existant. Le patch ne contourne pas ces restrictions.
Le bot doit toujours pouvoir lire et écrire dans son salon `#console`.

## Limites du stockage existant

Le stockage d'`ActiviteCog` est global et ne contient pas de `guild_id`. Pour ne
pas exposer ce même calendrier sur un autre serveur, la nouvelle commande refuse
les serveurs autres que celui de la première console résolue, lorsque le bot est
présent sur plusieurs serveurs. Une résolution ambiguë sans console bloque
également cette ouverture. Cela ne constitue pas une migration multi-serveur des
autres commandes du bot.

Les événements de `/event`, `/event-rapide` ou `/organisation` ne sont pas fusionnés
automatiquement avec les activités : leurs parcours et leurs stockages diffèrent.

La boucle historique de nettoyage peut retirer une activité dès que son heure de
début est atteinte. Le patch ne change pas cette politique et ne reconstitue pas
d'archives. Une période passée peut donc être vide. Aucune durée n'étant stockée,
la fiche utilise « Début passé » et n'invente pas un statut « en cours » ou « terminé ».

Les dates historiques sans fuseau sont interprétées comme des heures de Paris,
comme dans le code de rappels existant. Ce patch ne migre pas la saisie des heures
ambiguës ou inexistantes lors des changements d'heure.

## Installation et retour arrière

Fais une sauvegarde du code et vérifie la présence d'un snapshot valide des
activités dans `#console`. Conserve aussi le fichier local `activities_data.json`
lorsqu'il existe. Travaille de préférence dans une branche dédiée.

Place le patch à la racine du projet, à côté de `main.py`, puis exécute :

```bash
git apply --check evolution-calendrier-refonte.patch
git apply evolution-calendrier-refonte.patch
```

Si la vérification échoue, ne force pas l'application : ton code diffère de
l'archive de référence. Compare les changements avant de les intégrer.

Active l'environnement Python habituel du bot. Le README du dépôt vise Python
3.11 ou 3.12. Aucune dépendance nouvelle n'est nécessaire ; sur un environnement
incomplet, installe les dépendances déjà déclarées :

```bash
python -m pip install -r requirements.txt
python -m pytest
```

Pour contrôler plus particulièrement la refonte :

```bash
python -m pytest -q tests/test_calendar_data.py tests/test_calendar_render.py tests/test_calendar_view.py tests/test_calendar_integration.py tests/test_activite_init.py tests/test_activite_reminders.py tests/test_slash_commands.py
```

Redémarre ensuite l'instance existante, ou redéploie-la sur l'hébergement habituel.
Ne démarre pas une deuxième instance concurrente avec le même token.

La nouvelle définition slash doit être synchronisée. `SYNC_SLASH_COMMANDS=1`
active la synchronisation prévue par `main.py` et est déjà le comportement par
défaut du projet. `SYNC_SLASH_GUILD_ID` peut cibler un serveur de test. Vérifie
dans les logs la réussite de la synchronisation et la présence des quatre options
dans Discord. Aucun nouvel intent privilégié n'est demandé par ce calendrier.

Pour retirer le patch, avant d'autres modifications sur ces mêmes fichiers :

```bash
git apply --reverse --check evolution-calendrier-refonte.patch
git apply --reverse evolution-calendrier-refonte.patch
```

Redémarre ensuite le bot et resynchronise les commandes. Ce retour arrière concerne
le code ; il n'annule pas les inscriptions effectuées entre-temps.

## Recette à effectuer sur un serveur de test

1. Ouvrir `/calendrier` sans argument sur ordinateur et mobile : mois courant à Paris,
   aperçu mensuel et liste lisible ; basculer ensuite vers la vue semaine.
2. Vérifier une semaine vide, plusieurs activités le même jour, plus de six
   activités, un titre long, des accents et une description avec des mentions.
3. Changer de période et de filtre, saisir une date invalide, revenir à aujourd'hui
   et utiliser Prochaine activité. Vérifier le retrait de l'image au retour en semaine.
4. Modifier ou annuler une activité depuis une autre commande puis actualiser.
   Ouvrir une fiche déjà périmée et vérifier qu'aucune inscription invalide n'est faite.
5. Rejoindre puis quitter avec un membre validé ; vérifier les participants,
   le rôle d'activité et le snapshot dans `#console`. Tester le refus pour un
   membre sans rôle et pour la dernière place prise simultanément par deux membres.
6. Tester `prive:True`, les boutons depuis un autre compte, Fermer et dix minutes
   d'inactivité. Vérifier qu'aucune de ces actions ne supprime une activité.
7. Retirer Joindre des fichiers : le mois doit rester consultable en texte.
   Retirer Intégrer des liens : le bot doit expliquer la permission manquante.
8. Redémarrer le bot, rouvrir `/calendrier` et vérifier le rechargement des données
   et les inscriptions sauvegardées. Les anciennes sessions ne sont pas persistantes.

## Validation de la préparation du patch

Les quatre nouveaux fichiers de tests contiennent **115 cas exécutés avec succès**.
La sélection calendrier + activités + tests slash exécutables dans l'environnement
de préparation donne **210 tests réussis**, avec quatre tests slash écartés car leurs
dépendances globales ne sont pas disponibles dans cet environnement.

La suite complète a également été lancée sans interrompre l'exécution aux erreurs
de collecte, sur l'archive originale puis sur le code modifié :

| Version | Réussis | Échecs | Erreurs de collecte | Ignorés |
| --- | ---: | ---: | ---: | ---: |
| Archive originale | 430 | 4 | 3 | 1 |
| Avec la refonte | 545 | 4 | 3 | 1 |

Les quatre échecs et trois erreurs concernent les mêmes dépendances absentes :
`flask`, `async_timeout` et `validators`. Ils sont présents avant et après le patch.
Cela ne valide pas les modules qui n'ont pas pu être importés.

L'environnement de préparation utilise Python 3.13, discord.py 2.6.4, Pillow 12.3.0,
pytest 9.0.2 et pytest-asyncio 1.3.0. Il ne reproduit donc pas intégralement les
versions épinglées ni la matrice Python 3.11/3.12 de la CI du projet. Les échanges
réseau Discord sont simulés dans les tests ; aucun essai sur un serveur Discord
réel n'a été effectué. Relance la suite complète et la recette avant production.

## Validation dans le dépôt le 11 septembre 2026

L'application du patch a été vérifiée sur la branche principale au commit
`d904fde`. Les empreintes Git des douze fichiers livrés correspondent exactement
à celles du patch fourni ; aucune correction fonctionnelle n'a été nécessaire.

| Vérification locale | Résultat |
| --- | --- |
| Suite complète avant application | 454 réussis, 1 ignoré |
| Calendrier, activités, rappels et commandes slash après application | 214 réussis |
| Suite complète après application | 569 réussis, 1 ignoré |
| Compilation des sources et des tests | Réussie |
| Inspection des PNG de février 2021, septembre 2026 et août 2026 | Validée |

Les images vérifiées couvrent les mois de quatre, cinq et six semaines, les titres
longs, plusieurs activités le même jour et l'indicateur de débordement `+N`.
Les tests supplémentaires apportent 115 cas, sans retrait de tests existants.

La validation locale utilise Windows, Python 3.13.14, pytest 8.3.5 et
pytest-asyncio 0.26.0. La suite complète applique aussi
`-W error::pytest.PytestDeprecationWarning`, comme la CI. Les avertissements de
dépréciation existants restent visibles ; aucun échec de test n'a été ignoré.

Sur cet ordinateur, l'ancien répertoire temporaire pytest et son cache présentent
des erreurs de permissions. Les tests utilisent donc `-p no:cacheprovider` et
`--basetemp` avec un nouveau répertoire temporaire dédié à chaque exécution,
sans modifier les permissions ni les données du projet.

La CI Python 3.11/3.12 doit réussir sur le commit à publier avant l'avancement de
`main`. Les tests Discord utilisent des simulations : cette validation ne vaut
pas recette sur un serveur réel ni confirmation du redéploiement du bot.

## Références techniques

- Discord, réponse aux interactions et délais :
  https://docs.discord.com/developers/interactions/receiving-and-responding
- discord.py, vues, réponses privées et remplacement des pièces jointes :
  https://discordpy.readthedocs.io/en/stable/interactions/api.html
- Python, exécution dans un thread depuis asyncio :
  https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread
- Pillow, mesures et dessin du texte :
  https://pillow.readthedocs.io/en/stable/reference/ImageDraw.html
