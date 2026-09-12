# Validation du correctif activités

## Base

Archive : `DiscordEVOLUTION-main (3).zip`

SHA-256 de l'archive fournie :
`5135ffadf06107735f9eaf9d0326b179fa448fa38eb28dce4f332ffd9a45dc1a`

Environnement de validation : Python 3.13.5, discord.py 2.6.4, tests sans accès
à un vrai serveur Discord. Des dépendances Python déjà présentes dans l'archive
ont été utilisées hors du projet pour les tests, sans être ajoutées au patch.
Aucune nouvelle dépendance n'est introduite.

## Résultats réellement obtenus

La vérification élargie donne **770 tests réussis, zéro échec parmi les tests
exécutés, un test ignoré et quatre tests désélectionnés**.
Deux fichiers de tests ont été exclus de cette exécution pour le motif détaillé ci-dessous.

Le nouveau fichier `tests/test_activity_workflow.py` contient **67 cas de tests
après paramétrage**. Les tests existants du calendrier, des rappels, des commandes,
des confirmations et de la synchronisation ont aussi été exécutés.
Les assertions liées à l'ancien formulaire, au refus des groupes pleins et à la
suppression automatique des activités passées ont été adaptées au nouveau comportement ;
elles n'ont pas été remplacées par des assertions moins exigeantes sur les permissions
ou la sauvegarde.

La compilation des **19 fichiers Python nouveaux ou modifiés** réussit.
`git diff --check` ne signale pas d'erreur d'espacement.
Les 1 351 avertissements du passage élargi sont des avertissements de dépréciation
dans la bibliothèque Discord et des tests existants, pas des échecs.
L'ignoré est le test historique de l'ancien module `cogs/organisation`, déjà désactivé
dans la base ; aucun nouveau marqueur de skip n'a été ajouté.

### Principaux cas couverts

- Formulaire sans paramètres slash, réponses privées, aperçu sans écriture puis
  confirmation via le véritable circuit de commandes.
- Conservation du brouillon après une erreur, clé de création stable et double
  confirmation sans double inscription ni double création.
- Règles globales, rôle validé, droits de l'organisateur et du Staff, cloisonnement
  entre serveurs et interdiction de copier une annonce depuis un autre salon.
- Dernière place disputée par des clics simultanés, attente ordonnée, promotion,
  capacité, doublons, sortie d'un membre et historique conservé.
- Dates françaises rapides, limites UTF-16, changements d'heure, révisions concurrentes,
  modification sans perte d'inscrits, annulation conservant les listes.
- Refus d'écriture sans nouvel état exposé, panne de lecture distincte d'une base vide,
  dernier snapshot illisible, migration conservant les extensions et quarantaine.
- Snapshot JSON inline et en fichier, contenu comprenant des blocs de code,
  échec d'épinglage, ancienne sauvegarde conservée après un refus d'édition.
- Boutons persistants, fiche supprimée puis republiée, canal inaccessible sans effacement
  de son pointeur, rappels pour cent inscrits sans mention générale.
- Calendrier : capacité réelle, lieu, lien de fiche, attente, filtres et navigation.
- Menu contextuel et aide intégrée avec le schéma produit par discord.py.

## Limites de la suite complète

`python -m pytest -q` a été tenté. La collecte s'arrête sur deux erreurs :
`tests/test_alive.py` et `tests/test_main_evo_bot.py`, car `werkzeug` est absent
de cet environnement de préparation.

Les quatre tests suivants ne peuvent pas être validés tels quels ici :

- `test_slash_catalog_covers_every_installed_command_with_valid_discord_schema` :
  import de `validators` manquant ;
- `test_startup_syncs_slash_commands_by_default[None]` ;
- `test_startup_syncs_slash_commands_by_default[12345]` ;
- `test_startup_can_disable_sync_and_loads_catalog_last` :
  ces trois cas importent le serveur de santé et dépendent de `werkzeug`.

Ces dépendances figurent déjà dans `requirements.txt`. Les tests n'ont pas été
supprimés ni rendus conditionnels pour masquer ces limites. Le test d'inventaire
alternatif déjà présent dans le projet isole son validateur d'URL ; il valide
le schéma Discord, pas le fonctionnement de ce validateur.

Il n'est donc pas exact de présenter la suite complète du dépôt comme entièrement validée.
Elle doit être relancée dans l'environnement normal du projet avec toutes ses dépendances.

## Commandes de reproduction

Après installation des dépendances habituelles, lancer d'abord :

```bash
python -m pytest -q
```

Commande du passage élargi dans l'environnement de préparation :

```bash
python -m pytest -q --color=no -rs \
  --ignore=tests/test_alive.py \
  --ignore=tests/test_main_evo_bot.py \
  -k 'not test_slash_catalog_covers_every_installed_command_with_valid_discord_schema and not test_startup_syncs_slash_commands_by_default and not test_startup_can_disable_sync_and_loads_catalog_last'
```

Test ciblé du nouveau parcours :

```bash
python -m pytest -q tests/test_activity_workflow.py
```

## Vérifications avant mise en service

Aucune connexion à votre bot, modification de votre serveur, publication réelle ou
vérification de vos permissions Discord n'a été effectuée. Le correctif doit passer
la recette dans `ACTIVITES.md` : création, attente, modification, annulation, redémarrage,
suppression/réparation d'une fiche et retrait temporaire de l'accès à la console.

Le patch est construit comme un diff Git complet contre les fichiers de l'archive.
L'application à une copie vierge et la compilation des fichiers après application
sont vérifiées lors de la préparation de la livraison.
