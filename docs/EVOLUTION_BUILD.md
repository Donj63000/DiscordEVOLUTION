# Evolution Build — installation et exploitation

Version livrée : `1.0.0-beta.1`. Module développé contre `DiscordEVOLUTION-main (14)(1).zip`.

## Portée et statut

Cette livraison contient du code exécutable et des tests, pas un plan ni des fichiers de fonctions vides. Elle intègre le builder manuel, un stockage PostgreSQL dédié, les vues Discord, les jets personnalisés, la comparaison, les images, les échanges de jets avec `/exo`, les recettes et un optimiseur borné. Luna utilise le service métier, sans choisir un propriétaire et sans faire les calculs.

**Ce n'est pas un clone de DofusBook entièrement certifié.** Le logiciel est une bêta : le catalogue authentifié Xixou et un personnage réel étalon n'ont pas pu être testés dans l'environnement de fabrication du patch. Les règles et données non confirmées sont signalées, et ne sont pas silencieusement considérées comme exactes.

| Partie | Livrée | Réserve à connaître |
|---|---|---|
| Builder manuel | 16 emplacements, profil, objets, jets, remplacement, retrait, aperçu et confirmation | Les champs non couverts donnent des sommes partielles. |
| Calculs | Registre des contributions, dérivées couvertes, conditions bornées, panoplies par seuil total | Aucun relevé de référence en jeu fourni. |
| Panoplies | Moteur, validation et format de tables complets | **Aucune table réelle de bonus n'est embarquée dans les overrides vides.** Un nom de panoplie ne fournit pas ses bonus. |
| Personnage | 12 classes, 72 règles de paliers transcrites dans le fichier de règles | Bases, interaction parchottage/paliers et restrictions restent non certifiées ; PV de base inconnus. |
| Sauvegarde | PostgreSQL, révisions, transactions, quotas, reçus idempotents | Base durable à configurer ; intégration PostgreSQL à exécuter dans la CI. |
| Partage | Révision figée, code de 7 jours, périmètre serveur, copie, révocation | Pas de boutons publics persistants : les codes fonctionnent avec `/build ouvrir` et `/build copier`. |
| FM | Jets déclarés, exos, copie depuis sa propre session `/exo`, export des valeurs | `vers-exo` exporte des valeurs pour recopie, **pas** une session native à importer automatiquement. |
| Recettes | Agrégation des recettes actuelles du wiki | Pas de décomposition récursive ni de prix HDV. |
| Optimiseur | Recherche multi-combinaisons, PA/PM/PO, slots verrouillés, diversité, processus séparé | Les résultats dépendant de données inconnues sont des pistes non certifiées. Aucun optimum global promis. |
| Coût | Contrat métier acceptant prix et budget, validation de prix absents | Pas de collecte de prix ni de menu de saisie du marché ; prix/budget disponibles par l'API Python du module. |
| Dégâts | Calcul expérimental de lignes explicitement fournies | **Pas de catalogue de sorts ni de simulateur complet sorts/critique/soins.** Les formules ne sont pas validées en jeu. |
| Luna | Sept outils, résultats privés, propositions à confirmer | MP requis pour les cartes privées ; aucune publication de statistiques privées dans le salon IA. |

Les anciens `proposer_stuff` et `analyser_stuff` restent disponibles comme outils historiques **explicitement limités aux jets**, sans être présentés comme un second moteur de personnage complet. Les nouveaux `build_*` utilisent le nouveau service.

## Appliquer le patch

Depuis la racine du dépôt, sauvegarder/committer d'abord les modifications locales. Le patch n'est pas conçu pour être forcé sur une version différente.

```bash
git switch -c feature/evolution-build
git apply --check EVOLUTION_BUILD_INTEGRATION.patch
git apply EVOLUTION_BUILD_INTEGRATION.patch
python -m pip install -r requirements.txt
python -m compileall -q build.py utils/build tests_build
python -m pytest tests_build -q
```

Si `git apply --check` échoue, ne pas utiliser `--reject` pour obtenir artificiellement un déploiement partiel. Repartir de l'archive de référence ou résoudre le décalage sur une branche. Les dépendances nécessaires étaient déjà présentes dans `requirements.txt` : aucune nouvelle bibliothèque n'est ajoutée.

Le patch n'a ni démarré ton bot ni modifié Render/GitHub/Discord. Aucun token ni mot de passe personnel n'est inclus.

## Premier essai, sans base externe

Variables d'environnement du processus du bot :

```dotenv
BUILD_ENABLED=true
BUILD_BACKEND=memory
BUILD_AI_ENABLED=false
BUILD_OPTIMIZER_ENABLED=false
```

Conserver la configuration Xixou existante : `XIXOU_API_KEY` doit autoriser l'API équipements. Ne pas recopier cette clé dans un message Discord ou dans le dépôt.

Redémarrer le bot. Le chargement de `build` est branché dans `main.py` avant Evo et l'adaptateur slash. Utiliser la synchronisation de commandes déjà existante ; ne pas ajouter un deuxième bot ou un second arbre de commandes.

Commencer par `/build diagnostic`, puis `/build creer`. L'initialisation peut télécharger le catalogue ; `/build actualiser` permet au staff de réessayer sans redémarrer. Les membres ayant `Gérer le serveur` peuvent utiliser ces deux commandes de diagnostic/actualisation.

**Mode mémoire : rien ne survit à un redémarrage.** Ce mode est un essai volontaire, jamais un repli automatique après une panne PostgreSQL. Les cartes l'indiquent. Exporter le JSON garde le profil et les jets mais pas une copie complète des catalogues/règles.

Pour réimporter après redémarrage mémoire, `catalogue_actuel:true` est une option explicite de `/build importer` : elle utilise la nouvelle table de panoplies, exige que toutes les révisions d'objets correspondent exactement, conserve le jeu de règles demandé et présente une prévisualisation. Sans cette option, les versions archivées doivent encore exister dans le stockage. Un export JSON seul n'est donc **pas** un remplacement d'une sauvegarde des instantanés PostgreSQL.

## Stockage de production PostgreSQL

Configurer une base durable dédiée ou un accès autorisé. Le module n'ouvre pas d'abonnement et ne suppose pas qu'une base gratuite est pérenne.

```dotenv
BUILD_ENABLED=true
BUILD_BACKEND=postgres
BUILD_DATABASE_URL=postgresql://UTILISATEUR:MOT_DE_PASSE@HOTE:5432/BASE
BUILD_AUTO_MIGRATE=false
BUILD_MAX_PER_USER=20
BUILD_MAX_VIEWS=500
BUILD_AI_ENABLED=false
BUILD_OPTIMIZER_ENABLED=false
```

`BUILD_DATABASE_URL` est indépendant de `DATABASE_URL` : le module ne modifie pas les événements, métiers, activités ou le registre budgétaire de Luna.

La migration initiale est additive, dans `migrations/build/001_initial.sql`. Elle crée uniquement les tables préfixées `evolution_build`. Faire une sauvegarde avant toute migration. Deux méthodes possibles :

```bash
# Sur une base dédiée, avec les outils PostgreSQL installés :
psql "$BUILD_DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/build/001_initial.sql
```

Ou activer `BUILD_AUTO_MIGRATE=true` pour **un premier démarrage contrôlé**, puis remettre `false`. Une version de schéma inconnue n'est pas écrasée. Le schéma attendu est la version 1, dans le `search_path` PostgreSQL standard/public.

Si la base est inaccessible, une mutation échoue explicitement. Aucune annonce « sauvegardé » et aucun remplacement silencieux par une sauvegarde RAM. Les transactions utilisent le contrôle de révision et un verrou par serveur/propriétaire, y compris entre deux connexions/processus.

Historique : les 20 dernières révisions par build sont conservées. Reçus d'opérations SQL : 30 jours, nettoyés au fil des nouvelles écritures. La mémoire de démonstration borne ses reçus à 10 000 opérations. Le module n'implémente pas de bouton de restauration d'historique ; les données sont conservées pour l'exploitation.

Les instantanés de catalogue/règles restent archivés. Aucun nettoyage automatique destructeur de ces instantanés n'est appliqué ; surveiller la taille de la base avant un éventuel archivage décidé par l'exploitant.

## Parcours utilisateur

```text
/build creer nom:Mon Enu classe:enutrof niveau:200
/build mes
/build ouvrir build:<choix dans l'autocomplétion>
```

Depuis la carte : **Équipement → emplacement → recherche → objet → prévisualisation → Confirmer et enregistrer**. La prévisualisation montre les écarts de caractéristiques et recalcule la panoplie complète. Deux modifications concurrentes ne s'écrasent pas : la seconde reçoit un conflit et doit être préparée à nouveau.

L'autocomplétion reste locale et privée. Après un démarrage à froid, `/build mes` ou l'ouverture d'un build chauffe la liste. Les UUID restent utilisables directement. La recherche accepte des noms proches ; l'équipement exige une référence ou un nom exact unique.

Les vues durent dix minutes, les formulaires cinq minutes, avec deux vues actives maximum par membre. Ouvrir une troisième vue ferme la plus ancienne sans supprimer le build. Les codes de partage restent stockés indépendamment des vues.

### Profil

Les champs `allocated` / « Points dépensés » sont des **points consommés**, pas un nombre de caractéristiques déjà obtenu. Le budget maximal est `5 × (niveau − 1)`. Le parchottage est séparé.

Exemple de forme, **pas un personnage réel validé** :

```json
{"cha": 300, "sa": 90}
```

Le mode « Stats nues déclarées » contient les statistiques hors équipement déjà calculées, parchottage compris. Il impose de vider les champs points dépensés et parchottage. Une statistique omise demeure inconnue ; elle n'est pas traitée comme nulle.

Les clés disponibles sont dans `utils/build/models.py::STAT_LABELS`. Les abréviations principales sont `vi`, `sa`, `fo`, `ine`, `cha`, `age`, `pa`, `pm`, `po`, `pv`, `pp`, `do`, `pui`, `so`, `cc`. Les résistances fixes sont `r_ne`, `r_te`, `r_fe`, `r_ea`, `r_ai` ; les pourcentages utilisent `rp_...`.

`/build profil` ouvre le formulaire. Les paramètres optionnels `alignement` et `grade` préparent plutôt la modification de ces valeurs, après confirmation. Ils ne changent rien dans le jeu.

### Jets et FM

Le bouton Détails fournit les références exactes des effets : `e0`, `e1`, etc. Ces références sont liées à la révision de l'objet.

```text
/build jets ... mode:« Naturels personnalisés » valeurs_json:[{"ref":"e0","value":25}]
```

Toutes les lignes statiques naturelles doivent être fournies en mode personnalisé. Un jet hors plage naturelle nécessite « FM déclarée ». Exemple de ligne supplémentaire :

```json
[{"stat":"pm","value":1}]
```

Un over **remplace** la valeur finale de la ligne naturelle. Il n'est pas ajouté une deuxième fois en exo. `/build depuis-exo` copie les jets de la session appartenant au même membre, sous verrou, sans changer la session. `/build vers-exo` exporte des valeurs à recopier, pas une commande automatique de FM ni un fichier de session native.

### Confidentialité et partage

Les builds sont privés par défaut. Les commandes, boutons et appels IA revérifient le serveur et le propriétaire réels. Le staff n'a pas de commande de lecture des builds privés des autres membres. L'administrateur de la base garde naturellement son accès d'infrastructure.

`/build partager` exige une confirmation dans le salon destinataire. Un code opaque est valable sept jours, seulement dans le serveur d'origine, et pointe sur une révision figée. Modifier le build ne réécrit pas sa publication. `/build copier` crée un nouveau build privé après confirmation.

Le code du partage est haché dans la table des partages ; le reçu d'opération conserve le code pour gérer une répétition de la confirmation. Il ne doit donc pas être traité comme un secret inaccessible aux administrateurs de base.

La publication passe par `pending → sending → sent/failed`. Une opération incertaine n'est **pas renvoyée automatiquement** : le message peut avoir été reçu par Discord alors que la réponse réseau s'est perdue. Vérifier le salon, révoquer au besoin le code, puis créer volontairement un nouveau partage. SQL et Discord ne sont pas une transaction distribuée.

`/build depublier` révoque le code. `/build supprimer` confirme puis purge le build, son historique et ses partages. Une image déjà téléchargée ou un message déjà copié ne peut pas être rappelé. Les anciennes publications ne sont pas automatiquement supprimées du salon.

### Optimiseur et Luna

Activer séparément après la recette manuelle :

```dotenv
BUILD_OPTIMIZER_ENABLED=true
BUILD_AI_ENABLED=true
```

Le manuel et `/build optimiser` n'appellent aucun modèle. Les outils Luna consomment le budget IA existant, sans le modifier.

```text
/build optimiser build:<mon build> objectif:pp pa_min:10 pm_min:5 po_min:0
```

Les emplacements verrouillés sont séparés par des virgules, par exemple `anneau_1,arme`. Un slot vide verrouillé reste vide. `exos_autorises:true` autorise la conservation des jets FM verrouillés, **ne génère pas des exos imaginaires**.

Le moteur explore plusieurs combinaisons avec des limites de candidats, de largeur, d'expansions et de durée. Il exécute le calcul final commun pour chaque finaliste. Les résultats `DATA_INCOMPLETE` sont des pistes non certifiées, pas des builds garantis. `NO_SOLUTION_FOUND` et `RESOURCE_LIMIT` ne prouvent aucune impossibilité.

Par défaut : un processus de recherche à la fois, aucune file infinie, 8 secondes de budget interne et une limite d'arrêt externe. Les profils et slots verrouillés ne sont pas modifiés sans accord. Les propositions ne sont appliquées qu'au clic du propriétaire.

Les outils Luna livrent les listes, fiches, comparaisons et propositions privées en MP. Leur sortie vers l'IA publique ne contient pas les statistiques de ces builds. Si les MP sont fermés, le bot demande d'utiliser `/build` ; il ne publie pas les données à la place.

## Qualifier le catalogue et les règles

`utils/build/catalog.py` accepte l'enveloppe équipements Xixou et exige un rapprochement ID/nom/niveau/catégorie certain avec le wiki. Les identités contradictoires sont exclues. Il n'invente pas un endpoint panoplies ou sorts. Un catalogue vide/invalide ne remplace pas un instantané sain.

`data/build/catalog_overrides_v1.json` est volontairement vide : pas de données de jeu inventées. Ajouter des tables **réellement vérifiées et sourcées**, suivant les modèles `ItemTemplate`, `SetDefinition`, `SetTier`, `Effect`. Une correction d'objet exige `source`, conservée dans `correction_sources`.

Forme illustrative **synthétique, ne pas recopier comme bonus réel** :

```json
{
  "schema_version": 1,
  "items": {},
  "sets": [{
    "ref": "identifiant-normalise-de-la-panoplie",
    "name": "EXEMPLE SYNTHETIQUE",
    "source": "reference-du-releve-verifie",
    "tiers": [
      {"pieces": 1, "effects": []},
      {"pieces": 2, "effects": [{"ref": "bonus2", "kind": "stat", "stat": "fo", "low": 20, "high": 20}]},
      {"pieces": 3, "effects": [{"ref": "bonus3", "kind": "stat", "stat": "fo", "low": 40, "high": 40}]}
    ]
  }]
}
```

Le bonus de trois pièces est **40**, pas 20 + 40. Un seuil manquant reste inconnu ; même l'absence de bonus à une pièce doit être documentée. Les bonus statiques de panoplie doivent avoir des bornes égales.

Le profil fourni reste `beta`. Les drapeaux `*_verified` sont faux. **Ne pas les passer à vrai pour faire disparaître les avertissements.** Ajouter des relevés en jeu et les tests associés avant de qualifier les règles. Les fixtures embarquées dans `tests_build` sont expressément synthétiques, pas des observations officielles.

Après modification de règles ou d'overrides, redémarrer/actualiser, puis utiliser `/build migrer` pour proposer l'évolution d'un build. Aucune réécriture silencieuse des anciens jets. Une migration de jets impossible à rapprocher exactement est refusée.

## Tests et recette avant ouverture

```bash
python -m pytest tests_build -q
python -m pytest tests tests_evo tests_jobs tests_build
```

Les tests PostgreSQL nécessitent une **base de test dédiée**, jamais la production :

```bash
export BUILD_TEST_DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/build_test'
python -m pytest tests_build/test_postgres_integration.py -q
```

Le workflow `.github/workflows/evolution-build.yml` fournit PostgreSQL 16 et les dépendances du projet sur une PR concernée ou un déclenchement manuel. La CI principale découvre aussi `tests_build` grâce à `pytest.ini`.

La recette réelle doit couvrir : apparition des 25 commandes ; création/édition/réouverture ; boutons et formulaires ; utilisateur tiers refusé ; comparaison ; MP Luna ; redémarrage avec conservation PostgreSQL ; double modification ; échec de publication ; restauration de base ; relevé en jeu d'un personnage et de ses bonus de panoplie.

La compilation Python et les tests sans Discord ne suffisent pas à prouver cette recette réelle.

## Sauvegarde et retour arrière

Sauvegarder la base entière dédiée afin de conserver les instantanés, règles, révisions et reçus :

```bash
pg_dump --dbname="$BUILD_DATABASE_URL" --format=custom --file=evolution-build.dump
# Restaurer vers UNE BASE DE RECETTE VIDE, jamais écraser la production par défaut :
pg_restore --dbname="$BUILD_RESTORE_TEST_URL" --no-owner --no-privileges evolution-build.dump
```

Vérifier les comptes de lignes et ouvrir des builds représentatifs sur cette base isolée avant d'appeler la sauvegarde restaurable. Cette livraison n'a pas effectué une restauration de ta base réelle.

Pour arrêter le module sans perdre ses données : `BUILD_ENABLED=false`, redémarrage puis synchronisation habituelle. Pour couper seulement l'optimisation ou Luna : désactiver leurs indicateurs respectifs. Ne supprimer aucune table.

Pour retirer le code, sur une branche sans modifications ultérieures incompatibles :

```bash
git apply --reverse --check EVOLUTION_BUILD_INTEGRATION.patch
git apply --reverse EVOLUTION_BUILD_INTEGRATION.patch
```

## Sources techniques et règles

Les paliers transcrits sont documentés dans `data/build/retro_rules_v1.json`, avec la source `https://xixou.io/guides/classes/`. Le contrat API étudié est `https://xixou.io/les-outils/api/`. Les documentations techniques utilisées sont celles de discord.py, Discord Interactions, asyncpg et Python asyncio. Ces documents techniques ne remplacent pas des observations en jeu.

Les données Xixou sont créditées visiblement dans l'interface et les exports. Le builder ne collecte aucun inventaire du client Dofus, n'achète rien, n'équipe rien en jeu et ne se connecte pas à un compte Ankama.
