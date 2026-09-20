# Audit et correctif — Evolution Build

**Projet examiné :** `DiscordEVOLUTION-main (16)(1).zip`
**Date de l'audit :** 21 septembre 2026
**Incident :** `/build creer` refuse le catalogue, puis un second message d'erreur générique apparaît.
**Livraison :** `evolution-build-hotfix.patch`, à appliquer à la version du projet fournie.

Empreinte SHA-256 de l'archive d'entrée :

```text
ab60d55bd296ca94633c747f33c92d2c54116f7a91c5241023fa355b64b07730
```

## 1. Conclusion et niveau de preuve

Deux défauts ont été reproduits dans les sources fournies : l'arrêt des tentatives de démarrage alors que le catalogue manque, et le traitement de la même exception par deux gestionnaires qui répondent chacun à l'utilisateur. Des fragilités supplémentaires concernent la reprise d'un transfert de catalogue, les délais de l'inventaire Discord et l'information disponible dans les diagnostics.

Le correctif ne remplace pas le stockage par une mémoire volatile. Il ne remet pas la base à zéro. Il conserve la racine vérifiée de `#console` comme référence de la sauvegarde.

La trace fournie est compatible avec une interruption entre le transfert de fichiers et l'enregistrement de leur référence. Elle ne permet pas d'identifier à elle seule la première erreur réseau, Discord ou source distante. Il manque notamment la pile d'exception de l'hébergeur et le contenu réel des pièces jointes.

### Ce que montrent les captures et l'extrait

La racine copiée, à la révision 1, comporte une entrée `latest.rules` mais aucune entrée `latest.catalog`. Elle référence un snapshot de règles de 12 690 octets. Un autre envoi annonce ensuite 7 624 918 octets, un découpage à 4 194 304 octets et deux fragments.

Les tailles attendues sont donc :

```text
fragment 1 = 4 194 304 octets
fragment 2 = 3 430 614 octets
total      = 7 624 918 octets
```

Ce découpage correspond au transport prévu par le code. L'extension `.part` et l'affichage « unknown » ne prouvent pas une anomalie. Les noms des fichiers ne permettent pas non plus de certifier leur contenu.

**Un journal `attempted: 2` annonce des tentatives, pas un engagement de la racine.** Même deux fragments présents ne prouvent pas que le catalogue a été validé et rendu actif. La racine copiée décrit un état observé ; l'extrait ne prouve pas qu'aucune modification ultérieure de ce message n'a eu lieu.

## 2. Étendue de l'examen

Le chemin suivi a été : chargement du Cog et enregistrement des commandes, démarrage différé, catalogue wiki/Xixou, normalisation et enrichissement, service de création, sauvegarde console, réponses d'interaction et gestionnaire global.

Les tests existants de `tests_build` ont également été exécutés : calculs, conditions, équipements, profils, historique, prix, recettes, partages, autorisations, limites des composants Discord, optimisation, stockage et reprise. Cela vérifie les cas couverts par ces tests, pas l'exactitude de toutes les données de jeu réelles.

Les modifications partagées avec le reste du bot sont limitées au contrat explicite de gestion des erreurs dans `utils/slash_errors.py` et `utils/slash_support.py`. Les suites `tests`, `tests_evo` et `tests_jobs` ont été comparées avant et après, avec les limites d'environnement détaillées plus loin.

Les fichiers du budget, des activités, des métiers et du Defender ne sont pas modifiés. Les détections de liens signalées dans l'extrait constituent un autre sujet ; ce correctif ne prétend pas les résoudre.

## 3. Défauts et corrections

Les numéros de ligne ci-dessous désignent les fichiers corrigés et servent de repères.

### A. Critique — Stockage prêt confondu avec catalogue utilisable

**Repères :** `build.py`, `start_when_ready` vers la ligne 70 et `initialize` vers la ligne 86 ; `utils/build/service.py`, `BuildService.new`.

Dans la version fournie, `initialize` positionne `ready=True` après l'ouverture du stockage et le démarrage du service, avant la fin de l'actualisation du catalogue. Si cette actualisation échoue, l'exception est capturée mais `ready` reste vrai. La boucle de démarrage quitte alors ses tentatives sur cette seule condition.

Conséquence : le module peut répondre à l'aide, être enregistré dans le menu et permettre certains accès au stockage tout en gardant `catalogs.latest=None`. La création échoue alors systématiquement, sans nouvelle tentative automatique.

**Correction :** la boucle ne termine normalement que lorsque le stockage est prêt **et** qu'un catalogue est disponible. Les reprises sont espacées de 30, 60, 120, 240 puis 300 secondes au maximum. Une annulation n'est pas absorbée. Un ancien catalogue restauré reste utilisable si une actualisation distante échoue ; le démarrage ne boucle pas indéfiniment dans ce cas.

Le booléen historique `ready` conserve sa fonction d'admission au stockage : il n'est pas remplacé par un indicateur qui interdirait inutilement la consultation des builds existants. Le diagnostic distingue désormais ces deux états.

### B. Élevée — Deux réponses pour une seule exception

**Repères :** `build.py`, `error` vers la ligne 147 ; `utils/slash_support.py`, `EvolutionCommandTree.on_error` vers la ligne 80.

Le gestionnaire du Cog Build répond à l'utilisateur. Le gestionnaire global répond ensuite à la même exception. Le code réel de `discord.py` 2.6.4 appelle bien les gestionnaires locaux, puis `CommandTree.on_error` ; un retour du premier ne supprime pas le second appel.

**Correction :** réutilisation de `send_interaction_error` et ajout de la clé `evolution_error_handled` dans `interaction.extras`, uniquement lorsque la livraison locale est confirmée. Le gestionnaire global s'abstient seulement lorsque cette clé vaut explicitement `True`.

Un simple `is_done()` ou un `defer()` ne suffit pas à considérer l'erreur traitée. Les autres commandes conservent leur gestionnaire global. Si l'envoi local échoue, le repli global reste possible.

Le helper existant termine une réponse privée encore en chargement en modifiant sa réponse originale. Un éventuel chargement public ne reçoit pas le détail privé : il est terminé avec un texte neutre et le détail est envoyé de façon éphémère.

### C. Élevée — Candidat de catalogue perdu après échec d'archivage

**Repère :** `utils/build/catalog.py`, `CatalogService._persist_pending` et `refresh`, vers les lignes 301 et 317.

Le catalogue normalisé n'était conservé que dans une variable locale avant l'enregistrement. Une erreur ou une annulation pendant cette étape imposait une nouvelle récupération des sources lors d'une autre tentative. Cela ne garantissait plus de retrouver les mêmes octets ni la même génération de catalogue pour reprendre les fragments existants.

**Correction :** conservation du candidat figé dans `_pending_candidate`. La tentative suivante réessaie cet objet et la même sérialisation canonique avant tout nouvel appel aux sources.

`latest` n'est publié qu'après le retour réussi de `put_snapshot`, donc après confirmation du stockage. Un ancien catalogue n'est pas remplacé lors d'un échec. Cette mémoire de reprise n'est pas une sauvegarde de builds et n'autorise pas des créations sur une version non engagée.

### D. Élevée — Aucun chemin de récupération d'un catalogue intégral non référencé

**Repères :** `utils/build/console_repository.py`, `recover_catalog` vers la ligne 833 ; `CatalogService.restore` vers la ligne 274.

La restauration lisait seulement le snapshot référencé par la racine. Un catalogue intégralement transféré, mais non engagé avant un arrêt, restait inutilisable à la reprise.

**Correction :** lorsqu'aucun catalogue actif n'existe, le repository recherche une récupération conservatrice. Il exige :

- un stockage ouvert, la bonne console, un auteur bot vérifié et un leadership valide ;
- un journal conforme et un ensemble complet de fragments, avec identités, tailles, ordre et empreintes cohérents ;
- un document reconnu comme catalogue, valide selon le modèle, non vide, sérialisé canoniquement, dont l'empreinte et les révisions des objets sont vérifiées ;
- un seul catalogue admissible, puis l'engagement de sa référence par la procédure normale de modification et de relecture de la racine.

Il n'y a pas de choix par date, de recalcul d'empreintes pour « réparer » des données altérées, ni de publication à partir d'un simple nom de fichier.

Un catalogue déjà actif reste prioritaire. Des archives existantes sans pointeur actif, plusieurs candidats valides ou un catalogue reconnaissable mais corrompu imposent un contrôle Staff. Des fragments manquants ne sont pas publiés. Les transferts incomplets ne sont pas convertis en catalogue vide. Les données personnelles non engagées ne sont jamais récupérées automatiquement par cette méthode.

La restauration du catalogue est aussi exécutée **avant** l'enregistrement des règles : une écriture ordinaire pouvait sinon déclencher le nettoyage de fragments encore non référencés. Après un engagement confirmé, le mécanisme normal de nettoyage reste en place ; il ne faut pas assimiler cela à une suppression préalable des sauvegardes.

### E. Moyenne à élevée — Un délai global de 30 secondes trop étroit

**Repères :** `ConsoleRepository._discover` vers la ligne 142 et `_upload_inventory` vers la ligne 437.

L'ancien délai unique encadrait toute la pagination de l'historique, puis plusieurs téléchargements. Deux lectures individuellement acceptables pouvaient donc échouer parce que leur durée cumulée dépassait ce même délai.

**Correction :** maintien de 30 secondes par lecture de pièce jointe, avec des budgets distincts de 120 secondes pour la découverte paginée et de 180 secondes pour l'inventaire complet. Les opérations restent bornées. Cela réduit ce mode d'échec ; cela ne garantit pas qu'un historique arbitrairement volumineux sera parcouru à temps.

La lecture des épingles accepte l'itérateur asynchrone de la bibliothèque testée et l'ancienne forme awaitable. L'historique complet reste utilisé pour détecter les racines absentes ou multiples : le patch ne remplace pas cette vérification par « prendre la dernière épingle ».

### F. Moyenne — Diagnostic trop générique, cause Discord perdue

**Repères :** `utils/build/diagnostics.py`, lignes 47 et 64 ; `build.py`, `diagnostic` vers la ligne 663 ; `ConsoleRepository._commit_root` vers la ligne 661.

Le message d'actualisation pouvait renvoyer à Xixou même lorsque l'échec concernait la sauvegarde console. Certains chemins ne journalisaient que le type d'erreur ; une exception lors de la modification de la racine perdait aussi sa causalité avant le message final.

**Correction :** phases explicites : configuration, sources, corrections, normalisation, panoplies, vérification, archivage et restauration. Le diagnostic indique le catalogue utilisable, le candidat en attente, la révision de racine connue, la présence de sa référence catalogue, les tentatives et les capacités wiki/Xixou.

Les nouvelles traces techniques gardent les types, les codes HTTP/Discord numériques et les emplacements de code. Elles n'incluent ni les messages bruts d'exception, ni les valeurs Pydantic, ni les lignes sources contenant éventuellement un secret. Lorsqu'une écriture de racine n'est pas confirmée, l'erreur de transport reste accessible dans la chaîne de causes.

La révision affichée est la dernière racine connue du repository, pas une promesse de lecture réseau instantanée. « Dernier incident console » est historique et peut rester visible après une reprise réussie.

## 4. Propriétés conservées

Le patch conserve le schéma de racine, les formats des snapshots, les identifiants et l'interface des commandes. Aucune migration destructive et aucune nouvelle dépendance ne sont ajoutées.

Restent en place : contrôles d'auteur et de serveur, autorisations des membres, leadership, empreintes, validation de la racine, contrôle des révisions concurrentes, reçus d'idempotence, quotas, historique, isolation des partages et refus des écritures incertaines.

Une racine absente alors que des messages Build existent n'est pas remplacée par une base vide. Une donnée illisible n'est pas masquée par un faux catalogue de secours.

Le patch améliore des cas de reprise précis ; un conflit réel de racine, des archives ambiguës, des droits retirés ou une source absente peuvent toujours nécessiter une intervention Staff.

## 5. Validation effectuée

### Résultats observés

| Vérification | Sources fournies | Sources corrigées |
|---|---:|---:|
| Suite Build existante | 429 réussites | conservée dans le total ci-dessous |
| 32 nouveaux cas de régression | 24 échecs, 8 réussites | 32 réussites |
| Suite Build complète, anciens + nouveaux tests | — | **461 réussites, aucun échec** |
| Autres suites collectables | 3 318 réussites, 7 échecs d'import, 1 ignoré | mêmes résultats |
| Sous-tests des autres suites | 125 réussites | 125 réussites |
| Précontrôle Build hors ligne | — | code de sortie 0 |

Les 24 échecs du nouveau fichier sur l'ancienne version ne sont pas « 24 bugs indépendants » : plusieurs déclinaisons vérifient le même défaut, et certains tests exigent les nouveaux diagnostics.

Parmi les nouveaux tests : reprise identique sans rappel des sources, annulation, cadence plafonnée, ancien catalogue préservé, récupération après redémarrage, ambiguïté, corruption, transfert incomplet, refus de leadership, erreur HTTP 403/code 50013, absence de fuite dans les nouvelles traces et découpage de données synthétiques ayant exactement la taille indiquée dans les logs.

Deux tests traversent **le véritable `CommandTree._call` de discord.py**, avec le Cog, les paramètres de `/build creer` et le service réels. Sans catalogue, un seul détail privé est livré. Après récupération d'un catalogue synthétique, la commande crée un build qui est retrouvé par un nouveau repository. Le transport Discord et l'affichage final sont simulés ; ce n'est pas une connexion au serveur réel.

### Limites de l'environnement

Exécution sous Python 3.13.5, discord.py 2.6.4, Pydantic 2.13.4, aiohttp 3.13.3, pytest 9.0.2 et pytest-asyncio 1.3.0. Cet environnement n'est pas une installation exacte des versions épinglées dans `requirements.txt`.

L'accès réseau d'installation étant indisponible, les modules Python purs nécessaires de discord.py et de certaines dépendances de test ont été récupérés depuis des entrées intactes de l'archive d'environnement incluse dans le projet. Leurs enregistrements ZIP ont été contrôlés par taille et CRC. La bibliothèque Discord n'a pas été remplacée par un faux module. Ces dépendances ne sont pas redistribuées dans la livraison.

Les sept échecs des suites générales sont les mêmes avant et après, dus à l'absence locale de `validators` ou `Werkzeug`. Deux fichiers entiers, `tests/test_alive.py` et `tests/test_main_evo_bot.py`, ont dû être exclus de la collecte faute de Werkzeug. Ils ne sont pas comptés comme tests réussis. Le détail des commandes et des résultats accompagne le correctif.

Les tests produisent aussi des avertissements existants, dont des dépréciations de composants Discord. Aucun test en serveur réel, aucune connexion au bot de production et aucune requête aux sources de jeu réelles n'ont été effectués. La suite complète doit être relancée dans l'environnement de déploiement avec ses dépendances installées.

Le workflow Build a été étendu pour réagir également aux changements des deux fichiers partagés de gestion des erreurs. Ses étapes de tests complets ne sont pas remplacées par nos exclusions d'environnement.

## 6. Application du correctif

Travailler sur une copie Git du projet, au niveau du dossier contenant `build.py`. Sauvegarder ou enregistrer les modifications locales avant de continuer. Placer le patch dans le dossier parent, ou adapter son chemin.

```bash
git status --short
git switch -c fix/build-catalog-recovery
git apply --check ../evolution-build-hotfix.patch
git apply ../evolution-build-hotfix.patch
git diff --check
```

Si le contrôle d'application échoue, ne pas forcer une application partielle : le patch est construit pour l'archive précisément identifiée en tête du rapport. Comparer la version et les modifications locales.

Dans l'environnement de développement ou de CI disposant des dépendances du projet :

```bash
python tools/build_preflight.py
python -m compileall -q build.py utils/build utils/slash_errors.py utils/slash_support.py tests_build
python -m pytest tests_build -q
python -m pytest tests tests_evo tests_jobs -q
```

Après validation, enregistrer les changements dans Git, déployer cette version et redémarrer le processus du bot par la procédure habituelle de l'hébergeur. Ce n'est pas un fichier à envoyer dans `#console`.

**Ne pas effacer, désépingler ou reconstruire manuellement la racine `===BOTEVOBUILD===`. Ne pas supprimer les `.part` pour « débloquer » le démarrage.** Conserver une sauvegarde accessible de ces données avant intervention.

Le bot doit pouvoir accéder à la bonne console, parcourir son historique, envoyer et relire ses messages et fichiers, et épingler sa racine. Vérifier les droits effectifs du salon et le verrou d'instance sans accorder globalement Administrateur comme contournement.

`/build diagnostic` et `/build actualiser` sont réservés par le code aux membres disposant de la gestion du serveur. `BUILD_ENABLED` doit être actif. Le chargement distant des équipements nécessite le Cog wiki et son client Xixou activé, notamment `XIXOU_API_KEY` côté hébergeur. Ne pas poster cette clé dans Discord. Un catalogue existant correctement archivé peut être restauré sans nouvel appel source.

## 7. Recette en serveur après déploiement

Commencer dans un salon de test avec un membre autorisé.

1. Consulter `/build diagnostic`. Attendre `catalogue utilisable : True` et une racine connue référençant le catalogue. Une actualisation déjà en cours n'est pas, à elle seule, un échec.
2. Si le catalogue manque encore, lire la phase et la cause. Corriger l'accès indiqué puis utiliser `/build actualiser catalogue:equipements`. La commande conserve sa limite existante d'une relance par minute et refuse une actualisation concurrente.
3. Exécuter `/build creer nom:Test-audit classe:enutrof niveau:200`. Vérifier l'ouverture privée du build, puis sa présence dans `/build mes`.
4. Équiper un objet, modifier un jet, vérifier l'aperçu et confirmer. Contrôler les détails et l'historique, puis redémarrer le bot et retrouver le même build.
5. Vérifier avec un autre compte que l'accès direct au build privé est refusé. Tester le partage seulement après confirmation explicite, sans données réelles sensibles.

Pour éprouver une indisponibilité ou une corruption, utiliser un environnement de test et des données synthétiques. Ne pas retirer les permissions ni altérer les fragments du serveur de production pour cette recette.

La création et la consultation doivent désormais distinguer une attente de catalogue d'un stockage indisponible. Si la récupération trouve plusieurs archives admissibles ou une corruption, le refus explicite est voulu : examiner les données, ne pas choisir arbitrairement le fichier le plus récent.

## 8. Retour arrière et limites fonctionnelles

Avant toute modification ultérieure des fichiers corrigés, le patch peut être inversé sur une branche propre :

```bash
git apply -R --check ../evolution-build-hotfix.patch
git apply -R ../evolution-build-hotfix.patch
```

Si le correctif a été enregistré dans un commit dédié, privilégier le retour arrière normal de ce commit après revue. Ne pas mélanger les deux méthodes.

Aucune restauration manuelle de la console n'est nécessaire pour le seul retour du code : les formats restent compatibles. Les builds ou snapshots engagés depuis le déploiement ne doivent pas être effacés pour revenir à l'ancien code. Celui-ci retrouverait toutefois ses défauts de démarrage et de réponses.

Le précontrôle conserve explicitement les indicateurs de règles non validées du projet : bases, paliers, dérivées et restrictions ne sont pas certifiés. Les tests synthétiques ne prouvent pas l'équivalence exacte avec DofusBook ou les valeurs de chaque objet de Dofus Retro. Ce correctif remet en état le parcours et sa sauvegarde ; il ne transforme pas des données non qualifiées en données de jeu garanties.

## 9. Références techniques primaires

- Code source de discord.py 2.6.4, `discord/app_commands/tree.py`, méthode `_call` : https://raw.githubusercontent.com/Rapptz/discord.py/v2.6.4/discord/app_commands/tree.py
- Documentation de l'interaction, de `extras` et de `edit_original_response` : https://discordpy.readthedocs.io/en/stable/interactions/api.html
- Documentation Discord des réponses différées et des interactions : https://docs.discord.com/developers/interactions/receiving-and-responding

Les conclusions concernant le projet lui-même proviennent des fichiers livrés, des captures et des exécutions de tests décrites, non d'une observation du serveur en production.
