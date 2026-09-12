# Validation du patch annonces et équipes

## Base exacte

Archive fournie : `DiscordEVOLUTION-main (6).zip`

SHA-256 :
`31d940519dba7fdc491e94f726db2a53db4d194631efaf44efd58720aef742d2`

Le correctif vise les fichiers de cette archive, pas une ancienne version du dépôt.
L'image `activite.png` est déjà incluse dans cette base ; aucune nouvelle image
ni dépendance binaire n'est ajoutée par le patch.

Environnement utilisé : Python 3.13.5, vraie bibliothèque discord.py 2.6.4,
frontières réseau Discord simulées. Des dépendances Python incluses dans l'archive
ont été récupérées dans un dossier de test extérieur au projet, avec vérification
des données extraites. Elles ne font pas partie du patch. Aucune nouvelle dépendance
n'est ajoutée à `requirements.txt`.

## Résultat du passage élargi

**813 tests réussis, aucun échec parmi les tests exécutés, un test ignoré
et quatre tests désélectionnés.** Deux fichiers ont été exclus pour une dépendance
absente, comme détaillé plus bas.

Les nouveaux modules `test_activity_announcements.py` et
`test_activity_interaction_completion.py` couvrent 41 cas après paramétrage.
Deux cas de sélection directe dans le panneau ont aussi été ajoutés au parcours existant.

Les tests de rappels isolent désormais le service de rôles pour conserver leurs assertions
sur les seuls rappels. La création, l'attribution, le retrait et la suppression réels
du service sont testés séparément par les nouveaux scénarios de coordinateur.
Le test d'intégration calendrier vérifie toujours les véritables appels d'attribution
et de retrait via le chemin des commandes.

Les assertions de schéma ont été mises à jour pour les nouvelles options facultatives
et les libellés d'autocomplétion. Aucun test manquant de dépendance n'a été supprimé,
rendu conditionnel ou modifié pour masquer son échec d'import.

Les 17 fichiers Python nouveaux ou modifiés passent la compilation.
`git diff --check` ne signale pas d'erreur d'espacement.
Les avertissements du passage élargi proviennent de dépréciations Discord/Python
dans les dépendances et tests existants.

## Scénarios vérifiés

### Annonce, image et simplicité d'utilisation

Création avec publication automatique dans le salon configuré, image PNG jointe,
boutons persistants à identifiants stables, inscription du créateur et lien canonique.
Conservation de la même pièce jointe lors des éditions, une seule annonce publique
par activité dans les scénarios normaux et de double confirmation.

Image absente ou permission de fichier refusée : annonce toujours interactive,
avertissement visible et absence de perte du registre. Le chemin de l'image fonctionne
aussi lorsque le processus est lancé depuis un autre dossier.

`/activite rejoindre` sans identifiant ouvre une sélection explicite avec dates,
effectifs et places. Choisir dans le panneau exécute le vrai circuit contrôlé
d'inscription/désinscription et rafraîchit le panneau.

### Registre, concurrence et calendrier

Dernière place disputée par des inscriptions simultanées sans dépasser la capacité ;
file FIFO, promotion, doublons et conservation des listes lors d'une modification.
Le rôle n'est attribué qu'aux inscrits confirmés, jamais aux personnes en attente.

Un calendrier actif ouvert avant la création relit le registre après création et
inscription, puis affiche la nouvelle capacité. Une session arrêtée n'est plus modifiée.
Les tests existants de rendu, navigation, filtres, dates de Paris et cache restent exécutés.

Une édition Discord lente ne conserve pas le verrou des inscriptions.
Une sauvegarde interrompue n'expose pas un état confirmé incertain et bloque les écritures
jusqu'à restauration. Les autres tests de snapshots, migration et quarantaine sont conservés.

### Équipes temporaires et reprise

Nom avec titre et date, limites Unicode, absence de permissions accordées,
créateur inclus, attribution/retrait après clic et promotion après départ d'un membre.
Le rôle subsiste au début et est supprimé à la fin prévue, avec historique conservé.
Une modification renomme le même rôle et recalcule la fin.

Annulation, rôle supprimé manuellement, membre absent du cache, permissions insuffisantes,
échec de suppression puis nouvelle tentative. Refus de suppression d'un rôle géré,
doté de permissions ou trop haut dans la hiérarchie.

Reprise d'un rôle au nom de préparation persisté après remplacement du gestionnaire,
nettoyage des doublons de préparation et absence d'adoption d'un rôle permanent
portant seulement le même nom final.

Annonce supprimée puis réparée ; annonce envoyée dont le lien n'a pas pu être sauvegardé,
retrouvée ensuite sans nouvel envoi dans la fenêtre de récupération.

### Fin du chargement des interactions

Les nouveaux tests simulent explicitement les types de réponse Discord :
différé avec chargement et différé de mise à jour d'un composant.

Première réponse privée terminée par édition de l'original, fichiers convertis en
pièces jointes d'édition, réponses suivantes en suivi privé. Un différé de composant
ne remplace pas l'annonce publique par une confirmation privée.

Commande suspendue indéfiniment annulée à l'échéance et remplacée par une erreur,
y compris lorsque discord.py intercepte l'annulation. Une limite atteinte après
sauvegarde indique que la modification est enregistrée et déconseille la recréation.
Une exception inattendue dans un contrôle global reçoit aussi une réponse.

## Ce qui n'a pas pu être validé

La commande complète `python -m pytest -q` a été tentée après préparation des dépendances.
Sa collecte s'arrête sur `tests/test_alive.py` et `tests/test_main_evo_bot.py` :
`werkzeug` n'est pas disponible dans cet environnement.

Quatre tests restent désélectionnés dans le passage élargi :

- `test_slash_catalog_covers_every_installed_command_with_valid_discord_schema`
  importe `validators`, également absent ;
- les deux paramètres de `test_startup_syncs_slash_commands_by_default`
  et `test_startup_can_disable_sync_and_loads_catalog_last` importent `werkzeug`.

Ces dépendances sont déjà déclarées dans le projet. L'installation en ligne n'était
pas disponible ici. Le test alternatif du catalogue, qui isole son validateur de liens,
passe ; il ne valide pas le fonctionnement du validateur absent.

Le test ignoré est celui de l'ancien module `cogs/organisation`, déjà désactivé dans
la base. Aucun nouveau marqueur de skip n'est ajouté.

**La suite complète du dépôt n'est donc pas présentée comme entièrement validée.**
Il faut la relancer dans l'environnement normal du projet avec ses dépendances.

Aucune connexion au bot de production, publication réelle, modification de rôle
sur le serveur ou vérification visuelle dans le client Discord n'a été effectuée.
La recette réelle est décrite dans `ACTIVITES.md`.

## Reproduire

Avec les dépendances habituelles du projet :

```bash
python -m pytest -q
```

Passage élargi correspondant à l'environnement de préparation :

```bash
python -m pytest -q --color=no \
  --ignore=tests/test_alive.py \
  --ignore=tests/test_main_evo_bot.py \
  -k 'not test_slash_catalog_covers_every_installed_command_with_valid_discord_schema and not test_startup_syncs_slash_commands_by_default and not test_startup_can_disable_sync_and_loads_catalog_last'
```

Scénarios nouveaux ciblés :

```bash
python -m pytest -q tests/test_activity_announcements.py tests/test_activity_interaction_completion.py
```

Le patch est construit comme un diff Git complet contre la base fournie.
Son application est vérifiée dans une copie vierge du code de départ, puis les fichiers
appliqués sont comparés aux fichiers préparés et compilés. Les chemins de tests de
préparation, dépendances récupérées et fichiers temporaires ne sont pas livrés.
