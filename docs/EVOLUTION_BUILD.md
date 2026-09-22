# Evolution Build — installation et exploitation

Version du moteur : `1.2.0-beta.1`, avec le stockage console, un simulateur d'attaque, des prix personnels et des échanges natifs avec `/exo`. Les règles gardent leur propre version et leurs indicateurs de validation.

## Maintenance du 22 septembre 2026

`/build` est retirée pour tous, Staff compris. Le verrou `BUILD_MAINTENANCE` de la politique des commandes empêche aussi le chargement du module, les interactions et les outils Build de Luna, même avec `BUILD_ENABLED=true`. Les sauvegardes dans `#console` restent intactes. La réactivation nécessite un correctif explicite.

Après déploiement et redémarrage, laisser `SYNC_SLASH_COMMANDS` et `SLASH_CLEANUP_RETIRED` actifs pour retirer les inscriptions globales et celles des serveurs. Vérifier les journaux de synchronisation ; un push GitHub seul ne confirme pas le retrait sur Discord.

## Portée et statut

Cette livraison intègre le builder manuel, la sauvegarde durable dans le canal Discord `#console`, les vues Discord, les jets personnalisés, la comparaison, les images, les échanges de jets avec `/exo`, les recettes et un optimiseur borné. Luna utilise le service métier, sans choisir un propriétaire et sans faire les calculs. Evolution Build n'utilise aucune base de données externe.

Le logiciel reste une bêta : les catalogues Xixou ont été audités via le vrai client API, mais aucune comparaison complète à un personnage réel étalon n'a été fournie. Les règles et données non confirmées restent signalées.

Audit API du 20 septembre 2026 : 6 362 équipements et 1 402 sorts, représentant 7 297 niveaux ; 1 545 niveaux présentent des lignes d'attaque interprétables avant prise en compte des effets spéciaux. Ces comptes ne garantissent ni la complétude des mécaniques de combat ni leur exactitude en jeu.

| Partie | Livrée | Réserve à connaître |
|---|---|---|
| Builder manuel | 16 emplacements, profil, objets, jets, remplacement, retrait, aperçu et confirmation | Les champs non couverts donnent des sommes partielles. |
| Calculs | Registre des contributions, dérivées couvertes, conditions bornées, panoplies par seuil total | Aucun relevé de référence en jeu fourni. |
| Panoplies | Moteur, validation et format de tables complets | **14 tables publiées (67 seuils) embarquées**, plus enrichissement HTML public borné. Les panoplies non reconnues restent inconnues ; aucun bonus déduit d’un nom seul. |
| Personnage | 12 classes, 72 règles de paliers, bases et progression documentées dans les règles | Bases, interaction parchottage/paliers et restrictions restent à comparer au jeu ; les sources et réserves sont conservées. |
| Sauvegarde | Racine épinglée dans #console, snapshots vérifiés, révisions, quotas, reçus idempotents | Une seule instance écrivante ; recette Discord réelle à effectuer sur une console distincte. |
| Partage | Révision figée, code de 7 jours, périmètre serveur, copie, révocation | Les nouvelles publications possèdent Voir/Copier via DynamicItem, réenregistrés au démarrage. Les anciens messages gardent leurs codes. |
| FM | Jets déclarés, exos, copie depuis sa session `/exo`, ouverture native de l'atelier | Le puits doit être déclaré explicitement ; aucune valeur déduite des jets. |
| Recettes | Décomposition récursive jusqu'aux matières premières, quantités multipliées, détection des cycles et limites | Une recette indisponible reste distincte d'une matière première connue ; aucun prix HDV inventé. |
| Optimiseur | Recherche multi-combinaisons, PA/PM/PO, slots verrouillés, diversité, processus séparé | Les résultats dépendant de données inconnues sont des pistes non certifiées. Aucun optimum global promis. |
| Coût | Carnet privé par serveur/date/jets, saisie guidée et budget d'optimisation | Prix saisis par le membre, jamais collectés ou inventés ; prix manquant signalé. |
| Dégâts | Sort/niveau ou arme, cible PvM/PvP, buffs, maîtrise, normal/critique/soins/vol de vie, comparaison | Une attaque isolée ; effets spéciaux non interprétés et données absentes restent non calculables. Formules non validées en jeu. |
| Luna | Douze outils : listes, fiches, recherche, brouillons, simulateur, prix, optimisation, recettes et transfert `/exo` | MP requis pour les cartes privées ; aucune publication de statistiques privées dans le salon IA. |

Les anciens `proposer_stuff` et `analyser_stuff` restent disponibles comme outils historiques **explicitement limités aux jets**, sans être présentés comme un second moteur de personnage complet. Les nouveaux `build_*` utilisent le nouveau service.


## Parcours guidés disponibles

Le manuel s’ouvre depuis `/build mes` : menu de builds, création en trois champs, profil guidé, navigation des emplacements, recherche facultative par nom, tri par caractéristique, niveau minimum, pagination des résultats et éditeur de jets numérique. Les doubles fenêtres restent protégées par les contrôles de révision. `/build comparer` renvoie maintenant une fiche et un TXT lisibles, en plus du JSON avancé.

La fiche montre les seize emplacements (plus de coupure silencieuse des derniers Dofus). Détails permet de parcourir statistiques, résistances, panoplies et réserves ; le TXT indique les valeurs de jets réellement utilisées et les exos. Les images restent produites à partir du même rapport.

Après une erreur initiale de stockage, `/build actualiser` permet au staff de réessayer sans blocage préalable « non initialisé ». Une panne de catalogue ne supprime pas la copie archivée dans #console.

### Complément de panoplies

`BUILD_SET_ENRICHMENT=true` (défaut) active un chargement public Xixou indépendant de la clé API équipements : index de chemins autorisés, HTTPS, pas de redirection, pas d’URL fournie par un joueur, 2 Mio maximum par page, 20 pages / 25 secondes maximum par actualisation, temporisation entre pages et attente de dix minutes après un échec individuel. L’index est mis en cache pendant 24 heures. Une page tronquée, une identité ambiguë, un effet non compris ou un seuil manquant est refusé intégralement. Les tables déjà importées et les 14 tables locales restent utilisables.

Le chargement est progressif, pas un téléchargement systématique de toutes les panoplies au démarrage. `/build actualiser` poursuit les manquantes (commande limitée en fréquence) ; `/build diagnostic` les compte. Les tables déjà archivées ne sont pas rafraîchies aveuglément : une évolution publiée doit être vérifiée et introduite dans une correction sourcée. Désactiver `BUILD_SET_ENRICHMENT` supprime les accès HTML supplémentaires, pas les accès API équipements existants.

Les sources locales sont fusionnées en priorité avec les tables archivées. **Les builds existants sont figés** : `/build migrer` propose l’intégration des nouvelles données après confirmation. Remplir le catalogue ne réécrit pas silencieusement leurs anciens jets.

### Tests opérateur, sans secrets

```bash
python tools/build_preflight.py
# Réseau OPTIONNEL : comparer les 14 tables à leurs pages publiques actuelles.
python tools/build_preflight.py --public-panoplies
```

Le premier contrôle est local et ne touche pas aux données du bot. Il liste les versions des bibliothèques, les sources, les seuils et l’état non certifié des règles. Il sort avec le code 1 si une dépendance requise manque. Le second exécute un contrôle HTML en lecture seule ; une divergence ou un changement de structure est affiché, jamais appliqué automatiquement. **Il ne remplace pas un relevé en jeu.**

Le JSON d’export conserve son schéma 1. Les métadonnées de source et l’importeur ont une nouvelle empreinte ; les anciens snapshots restent lisibles. Les commandes existantes restent au nombre de 25, sans ajouter un deuxième groupe concurrent.

### Éléments qui restent hors validation complète

Le simulateur couvre une attaque isolée et n'est pas un moteur complet de combat. Les bases, paliers/interaction parchottage, dérivées, restrictions et formules d'attaque conservent leurs indicateurs non certifiés tant qu’il n’existe pas de cas étalons en jeu. Les tests synthétiques ne doivent jamais servir à passer ces indicateurs à vrai. Pas de prix HDV inventés ni d’exos générés automatiquement. La connexion Discord réelle, les permissions de #console et l’API équipements/sorts authentifiée demandent une recette dans l’environnement du projet.

## Vérifier une installation du dépôt

Installer les dépendances dans l'environnement Python du projet puis exécuter les contrôles locaux :

```bash
python -m pip install -r requirements.txt
python -m pytest tests_build -q
```

Ne pas réappliquer l'ancien patch V15 par-dessus cette version : les changements sont intégrés au dépôt. Les tests locaux ne démarrent pas le bot et ne modifient aucune console Discord réelle.

## Démarrage avec la console Discord

Variables d'environnement du processus du bot :

```dotenv
BUILD_ENABLED=true
BUILD_AI_ENABLED=false
BUILD_OPTIMIZER_ENABLED=false
```

Conserver la configuration Xixou existante : `XIXOU_API_KEY` doit autoriser l'API équipements. Ne pas recopier cette clé dans un message Discord ou dans le dépôt.

Redémarrer le bot. Le chargement de `build` est branché dans `main.py` avant Evo et l'adaptateur slash. Utiliser la synchronisation de commandes déjà existante ; ne pas ajouter un deuxième bot ou un second arbre de commandes.

Commencer par `/build diagnostic`, puis `/build creer`. L'initialisation peut télécharger le catalogue ; `/build actualiser` permet au staff de réessayer sans redémarrer. Les membres ayant `Gérer le serveur` peuvent utiliser ces deux commandes de diagnostic/actualisation.

Le canal configuré par `CHANNEL_CONSOLE_ID` ou les noms de console existants est l'unique stockage durable. Il doit rester privé : les membres n'ont pas besoin d'y accéder, mais ses lecteurs peuvent consulter les snapshots. Le bot doit pouvoir lire le salon et son historique, envoyer des messages et pièces jointes, et gérer les messages pour épingler sa racine.

Le dépôt mémoire existe uniquement pour les tests injectés. Le démarrage normal ne choisit jamais ce dépôt ; les anciens paramètres `BUILD_BACKEND`, `BUILD_DATABASE_URL` et `BUILD_AUTO_MIGRATE` ne sélectionnent plus de stockage externe. Il n'y a aucune migration SQL Build à exécuter.

## Stockage durable dans #console

Le stockage utilise le même canal que le verrou singleton du bot. Une seule instance écrivante doit fonctionner ; une console ambiguë ou un verrou non vérifiable suspend le module.

Cette version prend en charge les builds des membres du serveur qui héberge cette console. La présence du bot dans d'autres serveurs ne change pas le salon choisi par le verrou vérifié ; les opérations Build de ces autres serveurs sont refusées. Les clés serveur/propriétaire maintiennent l'isolation des données mais n'activent pas un hébergement multi-serveur dans une même console.

```dotenv
BUILD_ENABLED=true
BUILD_MAX_PER_USER=20
BUILD_MAX_VIEWS=500
BUILD_AI_ENABLED=false
BUILD_OPTIMIZER_ENABLED=false
```

La racine `===BOTEVOBUILD=== root` est le seul message Build épinglé. Elle référence les snapshots personnels et les versions immuables des catalogues, règles, sorts ou attaques. Les fragments `===BOTEVOBUILD=== blob` restent accessibles par identifiant de message ; le module ne conserve pas d'URL temporaire de pièce jointe. Le nettoyage `!clear console` protège ce marqueur.

Chaque snapshot personnel regroupe les builds, les 20 dernières révisions par build, les partages et états de publication, les reçus d'opérations conservés 30 jours et les prix personnels. Une modification prépare et relit un nouveau snapshot, puis engage son pointeur dans la racine et relit celle-ci. Le bot confirme uniquement l'état relu et vérifié. Un catalogue de plusieurs mégaoctets n'est donc pas renvoyé à chaque clic.

Les fragments sont bornés à la limite du serveur et à 4 Mio chacun, avec contrôle SHA-256 par fragment et sur le contenu assemblé. Un snapshot logique est limité à 24 Mio ; la racine est limitée à 4 Mio et à la limite des pièces jointes. Atteindre une borne refuse l'écriture sans supprimer l'état précédent. Les anciennes générations personnelles sont supprimées seulement après engagement confirmé ; les suppressions refusées sont reprises depuis la liste durable de nettoyage. Les versions de calcul archivées restent conservées.

Une lecture refusée, une dernière génération corrompue ou absente, plusieurs racines ou une racine disparue alors que des fragments existent bloquent le stockage. Aucun ancien snapshot n'est sélectionné silencieusement : une suppression confirmée ne doit pas ressusciter. Si l'accusé d'une écriture est perdu, la prochaine lecture réconcilie la racine et le reçu avant de réessayer ; ne pas créer plusieurs builds pour contourner une erreur réseau.

Les mutations sont sérialisées et contrôlent les révisions. Discord ne fournit pas de transaction ni de comparaison-échange côté serveur ; les vérifications du singleton avant et après engagement exigent une seule instance active et suspendent les opérations en cas de perte de leadership. Les tests simulent ces situations mais ne prouvent pas une garantie de transactions entre plusieurs processus indépendants.

Le bouton **Historique / restaurer** restaure explicitement une version dans une nouvelle révision. L'import `catalogue_actuel:true` réévalue les références d'objets et présente une prévisualisation ; un export JSON individuel ne remplace pas les versions de calcul archivées dans #console.

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

`/build profil` ouvre le panel guidé : choisir une caractéristique, puis saisir deux nombres (capital dépensé et parchottage). « Saisie groupée / stats nues » accepte `Chance: 300`, une ligne par caractéristique, et conserve la compatibilité avec l’ancien JSON. Les paramètres optionnels `alignement` et `grade` préparent plutôt la modification de ces valeurs, après confirmation. Ils ne changent rien dans le jeu.

### Jets et FM

**Équipement → emplacement → Modifier les jets** propose des champs numériques par pages de cinq effets, un sélecteur de mode, un formulaire d’exo et une prévisualisation avant confirmation. Aucun JSON obligatoire. Deux formulaires de jets concurrents ne s’écrasent pas : un brouillon périmé est refusé. Le rapport TXT fournit aussi les références exactes des effets pour le mode expert `/build jets` : `e0`, `e1`, etc. Ces références sont liées à la révision de l'objet.

```text
/build jets ... mode:« Naturels personnalisés » valeurs_json:[{"ref":"e0","value":25}]
```

Toutes les lignes statiques naturelles doivent être fournies en mode personnalisé. Un jet hors plage naturelle nécessite « FM déclarée ». Exemple de ligne supplémentaire :

```json
[{"stat":"pm","value":1}]
```

Un over remplace la valeur finale de la ligne naturelle. Il n'est pas ajouté une deuxième fois en exo. `/build depuis-exo` copie les jets de la session appartenant au même membre, sous verrou, sans changer la session.

`/build vers-exo` prépare les jets de l'objet choisi, puis propose **Renseigner le puits**. Saisir le puits connu, y compris zéro seulement s'il est réellement nul, puis confirmer **Ouvrir /exo avec ces jets**. Le bot ouvre une session native de l'atelier pour le même membre et le même serveur. La session précédente n'est remplacée qu'après ouverture réussie. Les jets courants et l'identité de l'objet sont contrôlés ; le puits n'est jamais estimé à partir des caractéristiques. Le JSON reste disponible pour inspection.

### Simuler une attaque et comparer deux builds

Depuis une fiche, cliquer **Simulateur**, ou lancer `/build degats` sans ses anciens paramètres manuels. Choisir **Choisir un sort**, saisir un nom et un niveau de sort de 1 à 6, puis sélectionner le résultat ; **Arme équipée** utilise l'objet actuellement porté. Le catalogue de sorts Xixou se charge à la demande et s'archive dans #console. Une source indisponible garde la dernière archive valide ; une identité ambiguë ou un niveau absent n'est pas deviné.

**Cible / buffs / maîtrise** ouvre un formulaire : une résistance par ligne (`terre 10 20`, pour 10 fixes et 20 %, ou `terre 10 20 5 10` avec bonus PvP), buffs tels que `Force: 100`, mode `pvm`/`pvp`, maîtrise/coefficient d'arme et PV cible/manquants. Les valeurs saisies sont des hypothèses du scénario ; une donnée offensive absente du personnage reste inconnue.

**Calculer** affiche les intervalles et moyennes disponibles en normal et critique, dégâts, soins et vol de vie. Le résultat exporte l'attaque figée, le scénario et les réserves en JSON. **Comparer deux builds** applique la même attaque et le même scénario à un autre build personnel. Toute modification du build depuis l'ouverture impose de rouvrir le simulateur. Les scénarios des vues sont temporaires ; seuls les catalogues et définitions d'attaque sont archivés. Les règles spéciales non interprétées ne sont jamais remplacées par zéro.

### Prix personnels et budget

Depuis la fiche, **Mes prix** propose les objets équipés avec leurs jets actuels. Choisir l'objet, saisir serveur Dofus, kamas et date avec fuseau (ou laisser vide pour maintenant), puis confirmer. Le prix est privé dans les commandes et persiste dans #console avec la révision d'objet et l'empreinte des jets exacts. **Voir / supprimer mes prix** permet de parcourir le carnet et de confirmer une suppression. Limite : 1 000 prix personnels ; aucune collecte HDV automatique.

Le panneau **Optimiser** permet **Objectif / contraintes** : caractéristique ou dégâts, minimums (`PA: 10, PM: 5`), emplacements verrouillés et objets déclarés possédés. **Budget / serveur** sélectionne le carnet et le plafond ; le bouton FM règle l'autorisation de conserver les jets déclarés. Seuls les objets déclarés possédés sont gratuits. Un prix absent, une autre version d'objet ou des jets différents empêchent de certifier le budget ; le moteur ne prend pas un objet inconnu pour un achat gratuit. L'objectif dégâts utilise l'attaque et la cible préparées par le parcours du simulateur lorsqu'elles sont fournies.

### Confidentialité et partage

Les builds sont privés dans les commandes. Les commandes, boutons et appels IA revérifient le serveur et le propriétaire réels. Le staff n'a pas de commande de lecture des builds privés des autres membres. Toute personne autorisée à lire #console peut toutefois lire ses snapshots : la confidentialité dépend aussi des permissions du salon.

`/build partager` exige une confirmation dans le salon destinataire. Un code opaque est valable sept jours, seulement dans le serveur d'origine, et pointe sur une révision figée. Modifier le build ne réécrit pas sa publication. `/build copier` crée un nouveau build privé après confirmation.

Le code du partage est haché dans l'index des partages ; le reçu d'opération conserve le code pour gérer une répétition de la confirmation. Les lecteurs autorisés de #console peuvent donc le retrouver.

La publication passe par `pending → sending → sent/failed`. La réservation `sending` est engagée dans #console avant l'envoi public et reste bloquante après un redémarrage. Après une réponse réseau perdue, le bot recherche un unique message du même bot et du même code dans les sept derniers jours du salon, avec une limite de 30 secondes. S'il le retrouve, il confirme `sent` sans nouvel envoi. Aucun résultat, plusieurs résultats ou historique inaccessible laissent l'état incertain ; réessayer la même confirmation relance cette vérification. Aucun second message n'est envoyé automatiquement. L'envoi public et la sauvegarde ne constituent pas une transaction unique.

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

`data/build/catalog_overrides_v1.json` contient 14 tables transcrites depuis les pages publiques Xixou ; le registre et les sources figurent dans `docs/EVOLUTION_BUILD_SOURCES.md`. Cela valide la transcription publiée, PAS les règles dans le jeu. Les corrections d’objets restent vides. Ajouter des tables sourcées selon `ItemTemplate`, `SetDefinition`, `SetTier`, `Effect` ; une correction d’objet exige `source`, conservée dans `correction_sources`.

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

`python tools/build_audit.py` effectue un audit réseau en lecture seule du catalogue authentifié et du wiki ; `--output chemin.json` enregistre facultativement le rapport public. Une clé absente ou un accès refusé produit un état indisponible sans remplacer de données dans #console. Ce contrôle n'est pas une validation des valeurs en jeu.

Le module `utils/build/reference_cases.py` accepte des cas synthétiques et des observations en jeu dans des catégories distinctes. Une observation en jeu exige version du jeu, date avec fuseau et preuve HTTPS consultable. Le contrôle compare les valeurs attendues et marque les métriques partielles ; fournir une observation ne bascule pas automatiquement les indicateurs de certification.

## Tests et recette avant ouverture

```bash
python -m pytest tests_build -q
python -m pytest tests tests_evo tests_jobs tests_build
```

`tests_build/test_console_repository.py` utilise des messages Discord simulés : restauration sans fichier local, révisions concurrentes, accusés perdus, corruption, fragmentation, permissions refusées, réservation de publication, prix privés et nettoyage après engagement. Aucune connexion Discord ni base externe n'est requise. Le workflow `.github/workflows/evolution-build.yml` installe les dépendances et lance ces tests ainsi que l'intégration des interfaces discord.py ; il ne prétend pas exécuter une recette réseau réelle. La CI principale découvre aussi `tests_build` grâce à `pytest.ini`.

### Recette Discord réelle distincte de la production

Utiliser un bot et un serveur de recette, une console privée distincte, un salon de publication et deux membres A/B. Ne pas pointer une seconde instance de test vers la console de production.

Créer une application Discord et un bot de recette distincts, les inviter uniquement dans ce serveur, puis injecter son token et une clé Xixou autorisée dans les secrets de son environnement. Ne jamais recopier les secrets dans #console, les rapports ou ce fichier. Utiliser les dépendances et la procédure de synchronisation de commandes déjà présentes dans le projet.

Exemple d'environnement non secret ; remplacer l'identifiant fictif par celui de la console de recette :

```dotenv
BUILD_ENABLED=true
BUILD_AI_ENABLED=false
BUILD_OPTIMIZER_ENABLED=true
EVO_ENABLED=false
CHANNEL_CONSOLE=console-recette
CHANNEL_CONSOLE_ID=123456789012345678
```

Lancer le processus du bot dans cet environnement. L'absence de service IA ne doit pas empêcher la sauvegarde Build sous le verrou de cette console. Garder une seule instance de recette active.

1. Accorder au bot lecture, historique, envoi, pièces jointes et gestion des messages. Vérifier qu'une seule racine Build est épinglée et que les fragments ne prennent pas d'épingles supplémentaires.
2. Avec A, créer puis modifier un build, saisir un prix et publier une révision figée. Avec B, vérifier que `/build mes` et les prix restent propres à B, que l'identifiant privé de A est refusé et qu'un code explicitement partagé est lisible/copiable.
3. Arrêter puis redémarrer le bot sans conserver ses fichiers locaux. Vérifier build, historique, catalogue figé, prix et partage. Ouvrir deux éditeurs du même build : le premier engagement réussit, le second doit signaler le conflit.
4. Retirer temporairement l'autorisation d'envoi ou d'épinglage, vérifier le refus sans confirmation fictive, rétablir les droits et réessayer. Sur la console de recette uniquement, altérer un fragment actif ou supprimer la racine : l'ouverture doit se bloquer, jamais repartir vide ou sur une ancienne génération.
5. Vérifier qu'un partage réservé mais incertain n'est pas renvoyé après redémarrage. Supprimer un build, redémarrer et confirmer que son historique et ses partages ne réapparaissent pas. Vérifier que `!clear console` préserve racine et fragments Build ainsi que les autres modules.
6. Ouvrir le simulateur, choisir un sort et son niveau, saisir une cible puis comparer deux builds ; vérifier export et réserves des effets inconnus. Saisir deux prix pour des jets différents, tester le budget avec et sans prix manquant, puis un échange Build → `/exo` → Build avec puits déclaré. Confirmer chaque modification et contrôler que le second membre ne peut pas agir sur ces vues.
7. Consigner date, version déployée et résultats observés, puis vérifier séparément les calculs sur un personnage réellement relevé en jeu.

Cette recette réelle reste à effectuer ; les tests locaux et la CI ne la remplacent pas.

## Sauvegarde et retour arrière

Conserver la racine épinglée et tous les fragments qu'elle référence dans #console. Le JSON exporté d'un build permet son échange mais ne contient pas une sauvegarde intégrale du catalogue, des règles, des reçus ou des prix. Il n'existe pas de deuxième base ni de fichier local utilisé comme secours durable.

En cas de corruption, suspendre Build et faire examiner la dernière génération par le Staff. Aucun outil de restauration automatique de messages supprimés n'est fourni ; ne pas recopier une ancienne racine pour débloquer le module, au risque de réintroduire des données supprimées. Une restauration manuelle éventuelle doit conserver ou remapper explicitement les références de messages et être éprouvée dans le serveur de recette avant toute intervention en production.

Pour arrêter le module sans perdre ses données : `BUILD_ENABLED=false`, redémarrage puis synchronisation habituelle. Pour couper seulement l'optimisation ou Luna : désactiver leurs indicateurs respectifs. Ne supprimer aucun message Build référencé.

Pour revenir à une version antérieure du code, utiliser un commit connu et compatible avec le schéma console. Ne pas appliquer à l'envers l'ancien patch V15 : il ne représente plus l'ensemble de cette livraison.

## Sources techniques et règles

Les paliers transcrits sont documentés dans `data/build/retro_rules_v1.json`, avec la source `https://xixou.io/guides/classes/`. Le contrat API étudié est `https://xixou.io/les-outils/api/`. Les documentations techniques utilisées sont celles de discord.py, Discord Interactions et Python asyncio. Ces documents techniques ne remplacent pas des observations en jeu.

Les données Xixou sont créditées visiblement dans l'interface et les exports. Le builder ne collecte aucun inventaire du client Dofus, n'achète rien, n'équipe rien en jeu et ne se connecte pas à un compte Ankama.
