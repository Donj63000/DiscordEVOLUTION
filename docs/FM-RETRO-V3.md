# FM Rétro v3 — règles sourcées, transitions traçables, fidélité mesurable

## Statut de cette livraison

Cette version remplace les coefficients de conception v2, distingue le coût arrondi
de la puissance nominale, garde le déroulement rune par rune et permet de remplacer
le modèle de repli par des transitions réellement relevées. Elle **ne fournit pas
une calibration démontrée sur les serveurs Ankama**.

Le corpus livré est intentionnellement vide. Aucun ensemble de captures brutes,
suffisamment documenté et exploitable statistiquement, n'a été récupéré pendant cette
revue. Les exemples et les corpus fabriqués par les tests sont **synthétiques**.
Un million de poses conformes à notre modèle ne constitue pas un million de poses
observées dans le jeu. Le statut `server_fidelity_demonstrated` reste donc faux.

Le périmètre est la FM de caractéristiques, pour un maître niveau 100, héritage
1.29/Rétro. Les potions élémentaires, la chasse, la signature, les métiers de niveau
inférieur et les armes éthérées sans bornes fiables ne sont pas simulés. Les effets
inconnus bloquent le calcul. Les malus exigent un contexte empirique exact : nous ne
créons pas de coefficient de malus face à des sources contradictoires.

## 1. Référentiel et arbitrages

Le fichier `data/fm_retro/reference-v3.json` versionne ensemble les valeurs, les
sources, leur accessibilité, leur périmètre et leur niveau de preuve. Son SHA-256
est épinglé dans `utils/fm_retro_reference.py`. Modifier le fichier sans réviser
l'empreinte provoque une erreur, au lieu de changer discrètement les replays.

Sources consultées le **17 septembre 2026** :

| Identifiant | Document et utilisation | Limite de la vérification |
|---|---|---|
| `ankama127-relay` | [Ponza Divizion, reproduction du message attribué à Oopah du 18/05/2009](https://ponzadivizion.wordpress.com/aide-forgemagie/) : cinq enveloppes SC/SN/EC, distinctions des résultats. | Texte reproduit lu ; original du devblog non récupéré. Le guide mêle plusieurs époques. Les anciens exemples Gelano et anciens plafonds non vérifiés pour la 1.27 ne servent pas d'oracles. |
| `alterya129` | [Alterya, Tout sur la Forgemagie en 1.29, 16/11/2017](https://alterya.over-blog.com/2017/11/tout-sur-la-forgemagie-en-1.29.html) : exemple Ra Vi de coût 8, perte d'un PA donnant 92 de puits ; repère communautaire de taille de rune. | Extraits indexés consultés ; accès complet indisponible. Ce n'est pas une série de captures avec effectifs. |
| `jol129` | [Discussion de joueurs, La forgemagie 1.29](https://forums.jeuxonline.info/sujet/1343510/la-forgemagie-1-29) : coûts Vi/Pa Vi/Ra Vi annoncés à 1/3/8. | Extraits indexés seulement ; pas de distribution observée exploitable. |
| `retro-lab` | [Fashionista, Smithmagic Lab Rétro](https://dofusfashionista.gg/retro/forgemagie/) : catalogue communautaire, comparaison des poids et tailles. | C'est un autre modèle estimatif, pas une preuve du code serveur. Certaines explications et certains poids contredisent d'autres références ; ses probabilités ne sont pas recopiées. |
| `retro-sink-observations` | [Témoignages Rétro du 03/02/2020](https://www.dofus.com/fr/forum/1747-actualite/2319341-question-fonctionnement-puit-dofus-retro) : pertes signalées malgré le reliquat. | Extraits indexés, sans effectifs ni captures complètes. Contre-indication à une priorité universelle du puits, pas un taux de contournement à inventer. |
| `excluded-modern` | [Guide JeuxOnLine mis à jour en 2016](https://dofus.jeuxonline.info/article/235/forgemagie). | Les gains de Vi 5/15/50 ne correspondent pas au profil Rétro retenu ; ce catalogue n'est pas importé. |

### Valeurs et incertitudes importantes

- Les poids PA 100, PM 90, PO 51, Vi 0,25, So 20 et les valeurs non contestées du
  profil existant sont conservés. Les gains Vi restent 3/10/30. Le **coût débité**
  passe à 1/3/8, distinct de la puissance nominale 0,75/2,5/7,5.
- L'arrondi entier supérieur est appliqué au coût de pose, pas individuellement à
  chaque point perdu. L'extension de cet arrondi aux Pods est explicitement une
  extrapolation à vérifier, non une mesure spécifique aux Pods.
- Les poids de résistances restent **2 fixes / 6 en pourcentage**. Fashionista
  affiche 5/4, mais cette divergence n'a pas été corroborée indépendamment :
  modifier une règle existante sur cette seule base aurait été injustifié.
  Le statut `unresolved_retained_baseline` est visible dans le référentiel et le guide IA.
- Pi, Pi Per et Do Ren sont ajoutées au catalogue. La Ra Prospe n'est plus proposée ;
  aucun tier Ra n'a été retenu dans le catalogue Rétro consulté. Pa Prospe reste disponible.
- Les plafonds existants sont conservés : poids total d'une ligne hors maximum
  naturel limité à 101 et surplus cumulé limité à 101. La seconde interprétation
  reste une convention conservatrice du profil, pas un théorème de validité serveur.
- Les malus font l'objet de versions contradictoires concernant le passage au
  positif et le reliquat. La v3 n'impose ni un arrêt universel à zéro ni un multiplicateur arbitraire.

Les sources du tableau sont des repères de provenance, **pas un corpus de calibration**.
Une valeur retenue n'est pas automatiquement une règle certifiée Ankama.

## 2. Architecture et sélection du modèle

Le chemin d'exécution reste :

`/exo ou outil IA → simulate_batch → attempt → transaction de session → panneau`

L'IA ne tire aucune rune, ne choisit aucune perte et ne modifie aucun compteur
elle-même. Les autorisations existantes (propriétaire, salon, partage explicite,
expiration, révision, verrou, une action demandée) et l'invalidation du cache après
mutation sont conservées.

Les responsabilités sont séparées :

| Module | Responsabilité |
|---|---|
| `fm_retro_reference` | Référentiel immuable et empreinte. |
| `fm_retro_model` | Modèle de repli explicite, basé sur cinq enveloppes historiques. |
| `fm_retro_observations` | Validation stricte, partition et index du corpus local. |
| `exo_engine` | Admissibilité, choix du modèle, calcul et commit atomique d'une pose. |
| `exo_session` | Sérialisation v3, contrôle des transitions et continuité de l'historique. |
| `fm_retro_audit` / `fm_retro_statistics` | Tests logiciels et comparaison à des séances tenues hors apprentissage. |
| `fm_retro_limits` | Admission des historiques en mémoire, y compris l'annulation. |

Ordre de sélection, sans taux personnalisé :
1. Contexte d'apprentissage couvert : tirage **joint** d'une transition observée.
2. Exo PA/PM/PO absent naturellement et à zéro : convention communautaire 1 % SC,
   0 % SN, 99 % EC. Ce n'est pas la règle de remontage d'une ligne naturelle.
3. Autres situations admissibles : interpolation historique de repli.

Un taux personnalisé utilise le scénario de laboratoire et désactive l'échantillon
empirique pour cette pose. L'exception existante des exos PA/PM/PO demeure : leurs
taux personnalisés ne remplacent pas la convention lourde. Rien n'apprend depuis
ces scénarios.

### Modèle probabiliste de repli

Les triplets historiques retenus sont 66/34/0 pour le remontage simple facile,
43/50/7 au parfait simple, 15/50/35 pour le remontage complexe difficile,
32/50/18 et 1/0/99 aux extrêmes de création d'effet.

**Ces cinq repères ne donnent pas une formule complète.** La surface interpolée
dans `fm_retro_model.py` est donc une hypothèse explicite. Elle remplace les anciens
coefficients `.97`, `.22`, `.12`, etc., mais ne prétend pas être la formule d'Ankama.

Elle utilise la qualité des autres lignes, la proximité du maximum naturel, le
surplus et le rapport entre jet courant et gain de la rune. Les lignes naturellement
fixes n'ont pas de zone d'approche 80 %-100 %. Le puits ne bonifie pas arbitrairement
la probabilité. La qualité des autres lignes est un proxy : le niveau de l'objet,
sa complexité exacte et d'éventuelles variables serveur non observées ne sont pas
identifiés par cette formule. Le seuil communautaire de confort ne garantit pas
l'optimalité d'une stratégie.

### Transitions et reliquat

SC applique le gain sans perte ni consommation de puits. En repli SN/EC, le coût
est compensé par les pertes et le reliquat. Un excédent de poids perdu crée du
puits ; une dette impossible à absorber sur un objet vide est enregistrée, sans
puits négatif. Gain brut, pertes et variation nette restent trois notions distinctes.

La sélection des pertes de repli privilégie les lignes en surplus autres que la
ligne ciblée, puis le puits, puis les lignes positives avec pondération par leur
puissance disponible. Une perte peut emporter des points naturels de la ligne
choisie. **Cet ordre précis et cette pondération ne sont pas calibrés.** Ils ne
prouvent pas qu'une ligne tombe à la bonne fréquence en jeu. Les signalements de
pertes malgré le puits ne sont pas transformés en pourcentage inventé.

Un tirage empirique remplace toute la transition : SC/SN/EC, jet final et puits
sont échantillonnés ensemble, sans recomposer indépendamment leurs marginales.
Un écart du relevé à notre bilan comptable est conservé et affiché, jamais corrigé
pour rendre artificiellement l'observation conforme au modèle.

Le suivi manuel ne tire rien. Un reliquat inexpliqué ou un calcul de malus
incertain devient inconnu. Un SC conserve toutefois le puits connu même en présence
d'un malus, puisqu'il ne comporte aucune compensation.

## 3. Corpus d'observations et activation

Par défaut : `data/fm_retro/observations.json`, **zéro observation**.

Un corpus supplémentaire est un fichier administré localement, sélectionné par
`EXO_FM_CORPUS=/chemin/absolu/corpus.json`. Aucun message Discord ni outil IA ne
peut sélectionner ce chemin ou alimenter ce fichier. Redémarrer le bot après
changement. Garder chaque version archivée avec son empreinte.

Le chargeur refuse les clés inconnues ou dupliquées, les textes invalides,
les valeurs non finies, les pertes impossibles, les SC avec perte de puits,
les identifiants répétés et les séances présentes dans les deux partitions.
Il exige **un seul serveur et une seule version exacte** par corpus, et une
attestation du niveau de métier 100.

L'authenticité d'une capture reste à vérifier humainement. Une URL HTTPS n'est
pas une signature du serveur, et le champ `origin` ne transforme pas une déclaration
en preuve. Une sélection de vidéos ne montrant que les réussites biaiserait le corpus :
collecter des séances complètes, avec échecs et remontages, selon un protocole fixé
avant de regarder les résultats.

### Format d'une ligne

Cet exemple est **inventé pour expliquer le schéma**, et n'est pas livré comme mesure :

```json
{
  "schema": 1,
  "reference": "retro-documented-v3",
  "id": "exemple-schema",
  "description": "EXEMPLE SYNTHÉTIQUE — remplacer par des captures vérifiées.",
  "observations": [{
    "id": "capture-001:1",
    "session": "capture-001",
    "split": "train",
    "origin": "game_observation",
    "source": {
      "url": "https://example.invalid/capture-a-remplacer",
      "server": "SERVEUR-A-RENSEIGNER",
      "game_version": "1.29.1",
      "profession_level": 100,
      "date": "2026-09-17"
    },
    "item": {
      "name": "Objet exemple",
      "token": "identifiant-du-catalogue",
      "bounds": {"fo": [1, 50]}
    },
    "before": {"jets": {"fo": 20}, "sink": "10"},
    "rune": {"stat": "fo", "tier": 0},
    "outcome": "SN",
    "after": {"jets": {"fo": 21}, "sink": "9"}
  }]
}
```

Un puits inconnu s'écrit `null`. Ne pas l'inventer à partir du seul jet.
Les nombres de puits connus sont des chaînes décimales à deux décimales maximum.

Le contexte comprend l'identifiant de l'objet, tous les intervalles naturels,
l'ensemble du jet avant, le puits avant et le tier exact de la rune. Les zéros
explicites sont normalisés. Les effectifs d'objets différents ne sont pas mélangés.
Il faut au moins **100 poses d'apprentissage connues dans ce contexte exact** pour
activer le tirage joint. Ce seuil est une politique de service, pas une preuve de
précision ; effectif et intervalles de Wilson restent visibles. Une transition vers
un état non couvert revient au repli, ou bloque pour un malus non couvert.

### Extraire un suivi manuel

```bash
python tools/fm_retro_validate.py collect exo-retro-session.json \
  --session capture-2026-001 \
  --split train \
  --corpus-id captures-retro-v1 \
  --url https://votre-preuve.example/capture-2026-001 \
  --server SERVEUR_REEL \
  --game-version 1.29.1 \
  --profession-level 100 \
  --date 2026-09-17 \
  --output captures-001.json
```

Remplacer serveur, version, date et URL par les valeurs réelles de la capture.
La commande n'extrait **que** le mode suivi v3, jamais les essais simulés.
Elle laisse les puits à `null` : les puits calculés par le suivi proviennent du
moteur et ne sont pas une observation indépendante. Le curateur ne doit les remplir
qu'après reconstruction indépendante et documentée depuis une capture complète.
Les lignes à puits inconnu restent conservées mais ne servent pas au replay joint.

Réunir ensuite les tableaux `observations` dans un corpus homogène, en gardant les
identifiants uniques. Choisir la partition par **séance entière** et avant
l'ajustement ; ne pas sélectionner les meilleures poses pour l'apprentissage.
Les sorties de commande sont exclusives : un fichier existant n'est jamais écrasé.

## 4. Mesurer, sans auto-certification

### Monte-Carlo logiciel

```bash
python tools/fm_retro_validate.py monte-carlo \
  --draws 1000000 --seed 129 \
  --output fm-monte-carlo.json
```

Ce contrôle appelle réellement le moteur avec le corpus embarqué sur tous les tiers, avec/sans puits,
en exo lourd, en over, en remontage complexe et avec un exo déjà présent. Il contrôle les marginales SC/SN/EC contre les taux du
modèle, à six écarts-types, et les bilans de chaque pose. Les cas comptables
conditionnels sont exécutés séparément. `passed=true` signifie conformité
logicielle au modèle, **pas fidélité au jeu**. Le job CI ajoute 100 000 poses par
version Python testée.

Les cas `reference-cases-v3.json` illustrent notamment PA perdu sur Ra Vi (92),
PA perdu sur Pa Sa (91), EC Ga Pme emportant le PA (10), SC sans consommation,
SN sur puits et reliquat inexpliqué sur objet vide. Seul le premier reprend un
exemple du guide ; les autres sont des régressions conditionnelles du bilan.
Le résultat et les pertes y sont imposés : ce ne sont pas des fréquences observées.

### Comparaison aux séances tenues hors apprentissage

```bash
python tools/fm_retro_validate.py evaluate \
  --corpus captures-retro-v1.json \
  --draws-per-context 5000 \
  --max-contexts 100 \
  --output fm-validation-externe.json
```

Le chargeur exclut les poses de partition `validation` du modèle. Le rapport
compare le moteur effectivement servi à ces poses, et indique :
effectifs et couverture, taux SC/SN/EC avec intervalles de Wilson, score de Brier,
log-loss, distance en variation totale de la transition jointe et du puits,
fréquences de perte de chaque ligne et puits moyen.

Les contextes bloqués, les puits inconnus et les contextes omis par la limite de
calcul sont explicitement comptés. Une observation de probabilité prédite nulle
reste visible ; la log-loss est alors `null` et le compteur d'impossibilités augmente,
au lieu de remplacer zéro par un epsilon opportuniste.

Codes de sortie :
- `0` : commande réussie, ou comparaison produite sur un holdout déclaré ; **pas
  un verdict de fidélité**, les métriques doivent être interprétées.
- `1` : entrée invalide, erreur de fichier ou Monte-Carlo logiciel non conforme.
- `2` : aucune donnée externe exploitable pour cette comparaison.

Les distances empiriques dépendent des effectifs et de la rareté des transitions.
Les intervalles binomiaux supposent l'indépendance ; des poses d'une même séance
peuvent être corrélées. Ne pas présenter ces intervalles comme une garantie
simultanée sur tous les objets ni comme une validation de stratégies de remontage.
Une telle validation nécessite aussi des séances complètes tenues hors ajustement
et un protocole fixé à l'avance.

## 5. Sauvegardes, limites et compatibilité

Les exports sont maintenant en **schema 3** et incluent :
jet complet avant/après, résultat, pertes, gain brut, puits avant/après, coût effectif,
puissance nominale, bilan, origine du modèle, empreintes du référentiel et du corpus.
La prochaine pose reste reproductible à graine, séquence, modèle et version Python identiques.
Changer la graine pendant une séance ne réécrit pas son historique ; l'export n'est
pas un enregistrement des tirages internes du serveur.

Chaque mode conserve son historique complet, jusqu'à **8192 poses ou 3 Mio JSON**,
selon la première limite atteinte. La pose suivante est refusée atomiquement :
pas de jet, compteur ou dépense modifié, pas de ligne supprimée. Exporter puis
ouvrir une nouvelle séance à cette limite. Les exports deux modes sont limités
à 8 Mio, avec sérialisation compacte si nécessaire.

Le budget global des historiques est de 32 Mio JSON, annulations comprises.
L'admission réserve avant la publication Discord et restaure la réservation lors
d'un échec ou d'une annulation. Ce n'est pas une limite exacte de mémoire Python :
prévoir aussi objets, index, copies transactionnelles et corpus (32 Mio JSON /
100 000 relevés maximum). La session reste éphémère, comme auparavant ; sauvegarder
avant redémarrage ou expiration.

Les sauvegardes v1/v2 compatibles restent lisibles et réexportables **en lecture
seule pour leur ancien état**. Le puits calculé avec les anciens coûts ne doit pas
être converti fictivement en puits v3. Exporter l'archive puis redéclarer un jet/puits
ou choisir un nouveau départ pour simuler. Une redéclaration ouvre une nouvelle
chaîne d'historique. Les anciens fichiers contenant un tier désormais absent,
par exemple Ra Prospe, sont refusés plutôt que transformés silencieusement.

La v3 vérifie une chaîne complète, ses compteurs et ses transitions ; un journal
tronqué ou contradictoire est refusé. Les exports v3 exigent l'empreinte du modèle
archivé : le corpus n'est pas incorporé à chaque sauvegarde personnelle. Ne pas
remplacer un corpus de production sans conserver sa version antérieure.

## 6. Recette et déploiement

Aucune dépendance supplémentaire, aucun nouveau secret, aucun appel réseau ajouté
par le moteur. Le catalogue et les contrôles de partage IA existants sont conservés.
Les nouveaux fichiers `data/fm_retro/` doivent être déployés avec le code.

Avant redémarrage : sauvegarder les ateliers en cours. Appliquer le patch sur
l'archive `DiscordEVOLUTION-main (12)(1).zip`, qui contient déjà les corrections v2.

Tests autonomes :

```bash
python -m pytest --noconftest \
  tests/test_exo_engine.py tests/test_exo_workshop.py tests/test_exo_review_core.py \
  tests/test_exo_math.py tests/test_exo_data.py tests/test_exo_presentation.py \
  tests/test_exo_structure.py tests/test_exo_review_ai_isolated.py \
  tests/test_fm_retro_v3.py tests/test_fm_retro_integration_isolated.py
```

Dans l'environnement complet du bot :

```bash
python -m pytest tests/test_exo_discord.py tests/test_exo_boundaries.py \
  tests_evo/test_exo.py tests_evo/test_exo_review.py
```

Les tests isolés exécutent les corps originaux des fonctions de routage,
transactions et outils IA, avec doublures du contexte et de la publication.
Ils ne remplacent pas les tests natifs du SDK. La recette de préparation a été
faite sans `discord.py` : aucune connexion réelle à Discord ni à un modèle IA
distant n'a été testée.

Contrôler ensuite sur un serveur de test : ouverture, pose et lots, coût,
journal long, export/import, import v2 bloqué puis redéclaration, partage IA,
lecture/pose/relecture, annulation et expiration du panneau.

## Critère avant de revendiquer une fidélité serveur

Ne pas supprimer les avertissements sur la seule base des tests logiciels.
Il faut un corpus authentifié et représentatif, une couverture mesurée des
contextes réellement rencontrés et des erreurs acceptables sur des séances
indépendantes. La livraison rend cette confrontation possible et reproductible ;
elle ne fabrique pas les observations qui manquent.
