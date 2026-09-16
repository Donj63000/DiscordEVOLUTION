# Correctif `/exo` / FM / intégration Evo

Base : `DiscordEVOLUTION-main (12).zip`
SHA-256 de l'archive : `b067e264503eeedca0d0c898acbbb0a987883b9d9ae65096f3f72a95e498bd12`
Date : 17 septembre 2026

## Objet du correctif

Ce patch corrige les incohérences de validation, d'import, de routage et de cache
reproduites pendant la revue. Il ajoute un contrat de conseil IA explicite et ses
tests de non-régression. Il ne remplace pas le moteur par des règles serveur
supposées, n'ajoute aucune dépendance et ne modifie ni base de données ni secret.

### F1 — Réalisme : qualifier les hypothèses, pas inventer une calibration

Le profil de tirage reste **`retro-workshop-v2`**. La constante `PROFILE`, les
poids, les gains, les coefficients des taux et l'algorithme de pertes ne changent
pas. Une même graine et une même séquence sur un état admissible gardent les mêmes
tirages. La référence documentaire/IA est séparée :
**`retro-workshop-v2-audit-1`**, dans `utils/exo_advice.py`.

`model_reference()` qualifie le modèle comme pédagogique, non calibré et non
certifié Ankama. Les poids existants (dont Vi 0,25 et So 20) sont conservés comme
conventions locales à vérifier. Le preset PA/PM/PO à 1 % reste une hypothèse
communautaire, pas une probabilité officielle démontrée. L'aide de l'atelier et les
outils IA exposent cette réserve.

La fidélité au jeu reste à mesurer sur des observations : version/serveur, fiche
naturelle, jets avant et après, rune et taille, résultat SC/SN/EC, puits connu ou
inconnu. Il faut conserver aussi les échecs, distinguer observation et simulation,
puis comparer probabilités, distributions de pertes et conservation du puits.
Aucune série d'observations serveur n'a été fournie pour effectuer cette calibration.

### F2 — Validation métier commune

`validate_item_jets()` vérifie toutes les lignes et le cumul over/exo, sans
interdire un maximum naturel dont le poids dépasse 101. Elle est appelée à
l'import de l'état simulé, à la saisie d'un jet simulé, au contrôle d'admissibilité
et avant engagement du nouvel état après une pose.

`validate_goals()` est commune aux objectifs UI, à l'import, à la création des
sessions et aux lots. La saisie UI garde ses seuils strictement positifs ; les
anciens seuils secondaires à zéro restent acceptés à l'import.

Un objectif explicite PA2 sur un Gelano est désormais refusé, au lieu de créer une
session impossible. Il n'est pas remplacé silencieusement par PM. L'ouverture avec
un objet recherché vérifie l'objectif sur cet objet, pas sur le Gelano provisoire.
Un objectif explicite est conservé pendant une recherche ambiguë, puis appliqué
lors du choix de la fiche.

Le suivi réel reste **déclaratif** : un jet hors profil local peut être conservé,
mais il est signalé comme tel et n'autorise pas une simulation. Les malus et effets
non interprétés restent enregistrables manuellement.

### F3 — Import accepté, données exploitables

Les quatre champs de poids des événements (`weight`, `unexplained_weight`,
`sink_before`, `sink_after`) sont normalisés en chaînes décimales canoniques à
l'import. Une virgule française est acceptée puis convertie ; `null` reste réservé
au puits inconnu. Le journal n'emporte plus une chaîne numérique qui cassera son
affichage ultérieur.

Tous les textes JSON, y compris les métadonnées et clés, sont vérifiés comme
exportables en UTF-8. Un surrogate isolé provoque une erreur utilisateur à la
frontière d'entrée. Les accents, caractères non BMP et paires Unicode valides sont
conservés. La vérification d'export ne boucle pas sur des conteneurs Python
circulaires : le sérialiseur continue à les rejeter.

### F4 — Routage et intention de pose

Le parseur d'autorisation est partagé dans `utils/exo_advice.py`. Ses expressions
d'autorisation sont conservées ; conseils, conditions, citations et demandes
multiples ne deviennent pas exécutables.

`schemas_for(..., current_request=question)` reconnaît la demande actuelle, même
quand le texte de routage contient du contexte JSON. « Pose une Force sur mon
objet » et « Pose une PA sur mon objet » reçoivent les outils FM. Le besoin d'un
résultat d'outil est également déclaré par `requires_evidence()`.

Le partage explicite, le propriétaire, le salon, la révision, l'expiration et la
correspondance entre rune demandée et rune posée restent contrôlés dans le pont Evo.

### F5 — Cache séparé par génération de lecture

Chaque lecture utilise une clé comprenant la génération courante. Une mutation
attend les lectures déjà lancées puis fait avancer cette génération, même lorsqu'un
échec peut survenir après sauvegarde. Les nouveaux appels ne peuvent donc pas
recycler un état antérieur.

Chaque appel conserve son propre résultat : une lecture avant la pose reste à sa
révision initiale ; la lecture après la pose obtient la nouvelle révision. Le cache
des mutations reste distinct : réutiliser un reçu ne rejoue pas la pose. Le mode
lecture seule refuse toujours les mutations, même déjà en cache.

L'invalidation est volontairement conservatrice pour toutes les lectures du tour,
pas seulement `ma_session_fm`, car leurs gardes peuvent dépendre du partage FM.
Une relecture compte dans `max_tools`. Lorsque cette limite est atteinte, le bot
renvoie une erreur de limite plutôt qu'un ancien jet.

Les gardes d'accès/consentement ne sont pas supprimées. Une révocation, expiration,
substitution du partage ou modification manuelle continue à bloquer la publication.

### F6 — Contrat du guide et de la session

Le guide conserve `poids_nominaux` pour compatibilité mais précise son unité. Il
fournit également chaque rune avec son nom, sa taille, son gain, son poids par
point et son poids total. Le parseur des mentions distingue « Pa Fo », « Ra Fo »
et « PA ».

Le snapshot partagé fournit l'admissibilité selon le profil, les motifs de blocage,
les paramètres SC/SN/EC effectivement applicables à la prochaine pose et la référence
du modèle. Un tirage bloqué ou un suivi déclaratif ne fournit pas de taux présenté
comme utilisable. Le preset lourd prévaut toujours sur les taux forcés ignorés.

La graine, les prix, le budget, le journal et l'autre mode ne sont pas ajoutés à la
lecture partagée. Les tests vérifient aussi le passage dans le sérialiseur borné des
outils. Le prompt de l'agent distingue poids unitaire/total, taux de scénario et
révisions avant/après.

### F7 — Cohérence du journal conservé

L'import remonte depuis le snapshot courant et contrôle les lignes touchées et le
puits des événements conservés. La fin de journal doit être consécutive et se
terminer à la séquence courante. Les variations d'une ligne antérieure ne sont pas
oubliées simplement parce que la dernière rune touchait une autre ligne.

Les 100 dernières lignes d'une longue session restent importables ; l'import ne
suppose pas disposer de toute l'histoire. Les deltas v1, même sans `changes`,
permettent de reprendre le parcours de migration existant. Les SC avec pertes et
certains compteurs d'échecs contradictoires sont également refusés.

Ce contrôle ne prouve ni l'origine d'un jet, ni la vérité d'un historique déclaré.
Les lignes jamais touchées dans la portion conservée n'ont pas de valeur initiale
indépendante à comparer.

## Compatibilité et déploiement

Le format d'export reste JSON v2. Les sauvegardes v1/v2 **valides selon les
contrôles ci-dessus** restent prises en charge. Les anciens fichiers incohérents
acceptés par erreur peuvent maintenant être refusés : aucune correction silencieuse
de jets, suppression de journal ou migration de base n'est effectuée.

Les tests qui attendaient volontairement la création de PA2 sur un Gelano sont
adaptés au nouveau contrat. Un test de bilan de lot changeait sa fiche d'objet
sans actualiser des objectifs devenus incompatibles : sa fixture est corrigée,
sans retirer son contrôle sur les pertes et les coûts.

Avant de redémarrer le bot, faire exporter les sessions en cours : elles sont
éphémères. Sauvegarder les changements locaux et travailler sur une branche dédiée.
Le patch vise précisément l'archive indiquée ; une version différente doit être
comparée, pas forcée avec `--reject`.

Depuis la racine du projet, placer le fichier `.patch` à cet emplacement :

```bash
git apply --check DiscordEVOLUTION-exo-fm-corrections.patch
git apply DiscordEVOLUTION-exo-fm-corrections.patch
```

Le patch n'exécute aucune installation ni commande distante. Redémarrer le bot avec
le mécanisme de déploiement déjà utilisé par le projet, après la recette.

Pour revenir en arrière sans écraser d'autres changements :

```bash
git apply --reverse --check DiscordEVOLUTION-exo-fm-corrections.patch
git apply --reverse DiscordEVOLUTION-exo-fm-corrections.patch
```

L'inversion exige que les lignes concernées n'aient pas été modifiées entre-temps.

## Tests

### Série autonome effectivement exécutée

```bash
python -m pytest --noconftest --color=no -q \
  tests/test_exo_engine.py tests/test_exo_math.py tests/test_exo_data.py \
  tests/test_exo_workshop.py tests/test_exo_presentation.py tests/test_exo_structure.py \
  tests/test_exo_review_core.py tests/test_exo_review_ai_isolated.py
```

Résultat de préparation : **531 tests réussis**, sous Python 3.13.5 :
**419 tests autonomes/structurels et 112 tests IA isolés**. Les tests IA isolés
chargent les corps de fonctions originaux par AST et utilisent des doublures pour
le contexte/panneau ; ils ne sont pas une validation du SDK ou du réseau Discord.

Le scénario lecture → pose → lecture est vérifié dans un même lot et sur trois
tours : révisions **0 → 1 → 1**, un seul essai, état avant conservé, PA perdu
correctement visible après la pose. Les tests couvrent également duplication,
révocation, changement de révision, remplacement du partage, expiration,
lecture seule, limite d'outils et annulation des lectures en attente.

### Recette native à exécuter dans l'environnement du bot

```bash
python -m pytest -q \
  tests/test_exo_discord.py tests/test_exo_boundaries.py \
  tests_evo/test_exo.py tests_evo/test_exo_review.py \
  tests_evo/test_adaptive_tool_round.py
python -m pytest -q
```

Ces tests natifs n'ont **pas pu être exécutés dans l'environnement de préparation** :
la collecte échoue sur `ModuleNotFoundError: No module named 'discord'`.
Les nouveaux fichiers ont été compilés, mais ni l'enregistrement Discord réel,
ni une conversation avec un fournisseur IA, ni un serveur de jeu n'ont été sollicités.

La CI du projet reste la référence pour la suite complète avec les dépendances
déclarées, notamment sur ses versions Python 3.11 et 3.12. Aucune réussite de cette
CI n'est revendiquée ici.

La recette manuelle doit vérifier l'import puis chaque onglet et le réexport,
le refus de PA2 en simulation, la conservation explicite en suivi, le choix d'objet
avec objectif PA, le partage Evo et la pose unique, puis le refus après révocation.
