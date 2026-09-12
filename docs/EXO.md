# `/exo objet` — atelier de forgemagie Rétro

Profil du moteur : `retro-workshop-v2`. Sauvegardes : schéma JSON 2, avec
migration du schéma 1 `retro-nominal-v1`.

## Périmètre et honnêteté du simulateur

Cet atelier est un **simulateur pédagogique local**, pas une connexion à
l'atelier Ankama. Il conserve réellement le jet courant, les pertes, les gains,
le puits nominal, les runes consommées et les objectifs d'une tentative à l'autre.

La catégorie existante du bot est **Dofus Rétro**. Cette version ne prétend pas
reproduire Dofus Unity ou Touch. La table nominale du dépôt est conservée ; les
probabilités automatiques et la répartition des pertes sont des conventions
explicites, non calibrées sur des données de serveur. Les avertissements sont
visibles dans le panneau et le journal. Ne pas utiliser les résultats comme
prévision certifiée de coût ou de rentabilité en jeu.

Les équipements avec malus, effets inconnus ou bornes non interprétables ne
sont pas simulés automatiquement. Le suivi déclaratif reste disponible.
Aucune nouvelle dépendance, permission, base de données ou variable
d'environnement n'est ajoutée.

## Démarrer comme un joueur

1. Ouvrir `/exo` pour un Gelano de démonstration sans réseau, ou
   `/exo objet:Gelano`. Le panneau est personnel et éphémère.
2. Lire le jet affiché et les minimums à conserver. Par défaut le départ est au
   maximum naturel, avec un puits de zéro.
3. Choisir la caractéristique à travailler. Les lignes naturelles de l'objet
   passent avant la liste des autres caractéristiques. Le choix suggère une
   taille de rune ; il reste possible de choisir soi-même une rune normale,
   Pa ou Ra lorsqu'elle existe.
4. Cliquer sur **Poser ×1**. Aucun réglage de probabilité n'est nécessaire.
   Lire le résultat, les lignes modifiées et le puits.
5. Remonter les lignes abîmées, puis retenter l'exo. **Exporter** permet de
   reprendre ultérieurement avec `/exo reprise:<fichier.json>`.

`objectif` reste une option facultative : PA, PM ou Portée. Sans cette option,
l'atelier préfère un bonus absent, dans l'ordre PM, PA, Portée puis les autres
caractéristiques. Ainsi des bottes possédant déjà un PM ne démarrent pas avec un
faux « exo PM réussi ». Un objectif explicitement impossible n'est pas masqué :
le moteur expose son blocage.

### Exemple : un Gelano PA/PM, pas seulement un PM

Le but initial du Gelano est `pm=1 ; pa=1`. Si le PM passe alors que le PA a
sauté, le panneau indique **bonus principal obtenu, objet à remonter**. Il
invite à sélectionner le PA. Remettre ce PA est un remontage naturel, pas un
second exo à 1 %. Le PM existant reste dans le jet et peut tomber pendant cette
remontée : aucune réparation gratuite n'est effectuée entre deux tentatives.

L'objet n'est terminé que lorsque les deux seuils sont atteints. Les mêmes
règles s'appliquent aux objectifs de qualité d'un équipement plus complexe.

### Objectifs et nouveaux jets

Le bouton **Objectifs** accepte par exemple :

```text
pm=1 ; pa=1 ; fo=45 ; vi=180
```

La première ligne est le but principal ; les suivantes sont des minimums de
qualité. Ce formulaire ne modifie ni le jet, ni les compteurs, ni le journal.
Les objectifs de départ ajoutent les **minimums naturels strictement positifs**
des autres lignes, pas leurs maxima : il faut les augmenter pour exiger un jet
parfait. Les lignes naturelles dont le minimum vaut zéro ne sont pas imposées.

Les plafonds de ligne et de poids over/exo sont vérifiés avant d'enregistrer les
nouveaux objectifs. Une liste invalide ne remplace pas la précédente.

Dans **Réglages**, les nouveaux jets minimum, aléatoire et parfait redémarrent
uniquement la simulation : puits zéro, compteurs et journal de simulation
neufs. Les objectifs, prix, taux personnalisés et observations sont conservés.
Le bouton **Annuler** restaure l'état précédent.

**Modifier le jet** est différent d'**Objectifs** : c'est une déclaration de
nouveau point de départ. Les lignes naturelles omises passent à zéro et les
compteurs du mode courant sont remis à zéro si le jet ou le puits change
effectivement. Le formulaire demande aussi le
puits (`?` pour inconnu) et la graine du scénario. Exporter avant cette opération
pour conserver un historique indépendant.

## Comprendre les messages

Le bilan distingue trois choses : le gain de la rune acceptée, les pertes
brutes et la variation nette de chaque ligne.

| Résultat | Traitement du modèle |
| --- | --- |
| SC, succès critique | Le gain est ajouté ; pas de perte et pas de consommation de puits. |
| SN, succès neutre | Le poids de la rune est compensé, puis son gain est ajouté. |
| EC, échec | Le poids est compensé sans ajouter le gain de la rune. |

Les points déjà présents sur la ligne travaillée ne sont plus immunisés.
Exemple : une rune Fo acceptée en SN avec une perte de 1 Force peut produire
un gain brut de +1 et une variation nette de zéro. Le journal montre les deux
informations plutôt qu'un trompeur « +1 Force ».

Exemple comptable nominal, Ga Pme sur Gelano :

```text
Échec — Ga Pme
Gain : aucun
Perte : PA −1
PA : 1 → 0 (−1)
PM : 0 → 0 (0)
Puits : 0 → 10 (+10)
```

Le PA vaut 100 et la rune 90 dans ce profil : le reliquat vaut 10. Ce résultat
est un exemple de calcul du modèle, pas une garantie du prochain tirage.

Les messages montrent également le prix consommé et, dans l'historique
détaillé, les taux effectivement employés. Le compteur parle de **runes
utilisées**, qui comprend les échecs, et non de runes « passées ».

### Lots ×10 et ×100

Un lot applique des tentatives successives au même état mutable : dégâts,
puits, coût et probabilités sont réévalués à chaque rune. Son bilan comprend
toutes les runes réellement utilisées, le nombre de SC/SN/EC, les gains et
pertes cumulés, les variations nettes et la raison de l'arrêt.

Pour éviter un over involontaire, ces lots s'arrêtent au seuil de la ligne
sélectionnée, ou avant qu'une rune trop grosse le dépasse. Le seuil est celui
de l'objectif principal pour sa ligne ; pour une autre ligne, c'est au moins
son maximum naturel et le minimum de qualité demandé. La pose ×1 permet de
tenter volontairement un over, si les plafonds l'autorisent.

Un lot n'est **pas** un robot qui répare les autres lignes : lorsqu'une ligne
est remontée, le joueur choisit la suivante. Un arrêt après quelques essais
conserve ces essais et indique pourquoi le reste n'a pas été exécuté.

### Historique et annulation

Chaque mode conserve les 100 dernières tentatives. Le journal présente un
essai complet par page, avec navigation vers les plus anciens et les plus
récents. Les compteurs restent cumulatifs au-delà de 100 essais, mais les
anciennes lignes ne sont pas conservées indéfiniment.

Le jet de l'atelier affiche les variations nettes du dernier lot ; l'historique
permet de comprendre ses essais individuellement. Les textes de statut
exceptionnellement longs sont abrégés explicitement ; le détail des événements
conservés reste accessible dans le journal et l'export.

**Annuler** restaure un niveau d'état précédent, y compris graine, séquence,
prix consommé et journal. Rejouer le même essai dans la même version du moteur
redonne le même résultat. Ce n'est pas une annulation d'action dans Dofus.

## Réglages avancés et suivi réel

**Taux / prix rune** définit un prix unitaire et, facultativement, des
pourcentages SC et SN pour la combinaison caractéristique/taille sélectionnée.
Les deux champs vides réactivent le modèle automatique. EC est le complément
à 100 %. La convention historique de 1 % SC pour un exo PA/PM/Portée absent
reste prioritaire : le formulaire l'annonce explicitement.

Les prix sont déclarés par le joueur. Zéro signifie coût non renseigné, pas
gratuité en jeu. Aucun prix HDV en temps réel n'est téléchargé.

**Simulation ↔ suivi** sépare entièrement le bac à sable des observations.
Avant de noter une observation, déclarer le jet réel. Saisir ensuite le
résultat SC/SN/EC et les pertes brutes, par exemple `pa=1 ; vi=3`.
Les pertes sont des quantités positives ; l'atelier les soustrait lui-même.

Un puits initial inconnu reste inconnu. Si les pertes déclarées ne compensent
pas la rune et le puits connu, le résultat est signalé comme inexpliqué et le
puits devient inconnu au lieu d'être artificiellement fixé à zéro. Les malus et
effets non pris en charge interdisent également d'inférer un puits fiable.

Ce mode ne lit pas le client Dofus, ne reconnaît pas automatiquement une capture
d'écran et ne vérifie pas la véracité d'un export. L'observation est déclarative.

L'onglet probabilités/budget garde le modèle géométrique et les campagnes
Monte-Carlo existants. Son taux constant et son forfait de remontage ne pilotent
pas l'atelier à jets évolutifs. Une campagne budgétaire n'est pas une succession
de remontages détaillés de l'équipement.

## Spécification du profil nominal v2

### Poids et gains

Source de vérité locale : `utils/exo_engine.py`, dictionnaire `STATS`.
Le poids de rune vaut poids par point multiplié par son gain.

| Caractéristique | Poids par point | Gains disponibles |
| --- | ---: | --- |
| PA / PM / Portée | 100 / 90 / 51 | 1 |
| Vitalité | 0,25 | 3 / 10 / 30 |
| Force, Intelligence, Agilité, Chance | 1 | 1 / 3 / 10 |
| Sagesse, Prospection | 3 | 1 / 3 / 10 |
| Initiative | 0,1 | 10 / 30 / 100 |
| Pods bonus | 0,25 | 10 / 30 / 100 |
| Dommages, Soins | 20 | 1 |
| Coups critiques, Invocations | 30 | 1 |
| Dommages en pourcentage | 2 | 1 / 3 / 10 |
| Résistances fixes des cinq éléments | 2 | 1 |
| Résistances en pourcentage des cinq éléments | 6 | 1 |

**Ces valeurs sont celles du dépôt d'origine, pas une certification de toutes
les versions de Dofus.** Des guides publics donnent d'autres valeurs pour
Vitalité et Soins. Le patch ne remplace pas une table entière à partir d'une
page communautaire dont le périmètre/version est ambigu. Une calibration
ultérieure doit préciser version, source, gains de runes et migration des
anciens puits, et changer l'identifiant de profil si nécessaire.

Le dépassement d'une ligne au-delà du maximum naturel est refusé lorsque son
poids total dépasserait 101. Le poids cumulé over/exo du jet proposé est lui
aussi limité à 101. Ces contrôles nominaux sont conservateurs et ne prétendent
pas couvrir toutes les exceptions historiques du jeu.

### Probabilités automatiques : formules locales, non serveur

Pour une rune de gain `g`, soit `c` la valeur actuelle positive de sa ligne,
`m` son maximum naturel positif, `w` le poids de rune et `S` le surplus
over/exo actuel. On définit :

```text
pressure = poids naturel actuellement présent / poids naturel maximum
           (lignes plafonnées à leur maximum ; zéro si dénominateur nul)
undersized = min(1, max(0, c / (20*g) - 1))
```

Les formules de conception sont les suivantes :

```text
Remontage naturel :
  fill = c / m
  succès = 0,97 - 0,22*fill - 0,12*pressure - 0,40*undersized
  part critique des succès = 0,55 + 0,30*(1-fill)

Over :
  over = (c + g - m) * poids_par_point / 101
  succès = 0,55 * max(0,02, 1-over) * (1 - 0,45*undersized)
  part critique des succès = 0,35

Exo léger :
  succès = 0,45 * exp(-w/35)
           * (1 - 0,65*min(1, S/101)) * (1 - 0,35*pressure)
  part critique des succès = 0,40
```

Le succès est borné entre 1 % et 98 % et arrondi à six décimales ; SC est ce
succès multiplié par sa part critique, SN est le reste, EC son complément.
L'exo PA/PM/Portée absent utilise séparément le preset 1 % SC, 0 % SN.

Ces coefficients fournissent une jouabilité cohérente et des taux qui réagissent
au jet. **Ils ne proviennent pas d'une formule Ankama vérifiée ni d'un ajustement
statistique à des logs réels.** Aucun niveau de métier, focus, changement de
version ou effet caché de serveur n'est modélisé. Les taux personnalisés servent
à comparer des hypothèses ; ils ne certifient pas ces hypothèses.

La rune conseillée utilise un repère local de 20 fois le gain, puis réduit la
taille pour ne pas dépasser le nombre de points manquants. C'est une aide au
choix, pas une optimisation économique démontrée.

### Pertes, puits et invariants

Pour SN ou EC, l'ordre conventionnel est : surplus des autres lignes, puits,
puis lignes positives, **y compris les points déjà présents sur la ligne
travaillée**. Les surplus sont mélangés et les lignes positives sont tirées
uniformément. Le coût de rune est compensé avant l'ajout du gain de SN.
La répartition est donc explicitement heuristique.

Le prélèvement se fait en points entiers ; tout excédent de poids augmente le
puits. S'il n'existe plus assez de poids à prélever, le moteur n'invente ni
statistique négative ni puits négatif : il journalise le poids non compensé.
L'existence d'un tel résultat dans ce bac à sable n'en fait pas une règle serveur.

Les poids sont des `Decimal`. Le tirage dépend de la graine, de la séquence et
du profil. Les parcours de dictionnaires sont normalisés pour qu'un ordre de
clés JSON différent ne change pas un tirage.

Un calcul valide est construit avant mutation : dépassement de limites,
paramètres invalides ou erreur de validation ne laissent pas de demi-tentative.

## Catalogue, parsing et sauvegardes

Le chemin existant est conservé : catalogue/cache du cog wiki, détail d'objet,
enrichissement éventuel Xixou et illustration facultative. Les API de catalogue
apportent des fiches et effets ; elles ne sont pas appelées pour lancer les
tirages du moteur local.

Les nombres français avec séparateurs de milliers, certains tirets Unicode et
des variantes de résistance sont normalisés. Les effets inconnus restent
visibles et bloquent la simulation. La reconnaissance des dégâts de base d'une
arme est limitée à des libellés identifiables : un « Dommages renvoyés »
inconnu ne disparaît plus dans une exclusion trop large.

Les images et enrichissements sont facultatifs : délais séparés de 20 secondes
pour le détail, 8 pour l'enrichissement et 5 pour l'image. Leur indisponibilité
ne supprime pas un objet dont le détail exploitable a déjà été récupéré.
Une illustration déjà jointe est conservée plutôt que renvoyée à chaque rune.

Le schéma 2 exporte les deux états, graine, objectifs, prix, taux, budgets et
jusqu'à 100 événements par mode. Les nouveaux événements contiennent les jets
avant/après des lignes touchées, le gain accepté, les pertes, les puits, le prix,
les taux utilisés et le profil. La taille maximale est **512 Kio** ; l'export
passe en JSON compact avant de refuser un fichier trop volumineux, sans tronquer
silencieusement le journal.

Le schéma 1 est repris en conservant jets, compteurs et ancien objectif simple.
Un avertissement explique que les prochains tirages utilisent le moteur v2.
L'ancien journal n'est pas réécrit en prétendant connaître des jets avant/après
qu'il ne stockait pas. Les anciens puits sont conservés car la table nominale
ne change pas.

Les imports refusent les types incohérents, valeurs non finies, clés JSON
répétées, profils inconnus, incohérences de bilan/compteurs et objectifs primaires
répétés dans les seuils secondaires. Un snapshot reste déclaratif : il n'est
ni signé ni garanti par le serveur. L'import ne charge aucune URL arbitraire.

## Cycle de vie Discord

Le panneau ferme après 10 minutes d'inactivité ou 14 minutes depuis sa création.
La limite dure ménage une marge avant l'expiration du jeton d'interaction.
Fermeture explicite, expiration, remplacement du panneau et déchargement normal
du cog tentent de joindre une sauvegarde JSON au message fermé.

Cette sauvegarde est **au mieux** : une suppression de message, une erreur HTTP,
un arrêt brutal du processus ou une indisponibilité de Discord peut l'empêcher.
Exporter régulièrement reste nécessaire. Il n'y a pas de persistance
automatique sur disque, de reprise de bouton après redémarrage ou d'envoi en MP.

Chaque composant porte la révision du panneau. Deux clics arrivant sur le même
ancien état ne consomment pas silencieusement deux runes. Les traitements sont
sérialisés par session ; les formulaires périmés sont refusés. Lorsqu'une action
est en cours, l'ouverture d'un formulaire reçoit immédiatement une réponse.

Les calculs s'appliquent à une copie de session. Une erreur de reconstruction
du panneau ou de publication restaure l'état local précédent. Cela ne constitue
pas une transaction distribuée : une coupure réseau après acceptation par
Discord peut toujours nécessiter de rouvrir le panneau.

Le rendu est construit dans `exo_presentation.py` sous forme de payload testable,
puis converti par `discord.Embed.from_dict`. Les limites de taille sont comptées
en unités UTF-16, les champs longs sont répartis et les mentions désactivées.

## Architecture

| Fichier | Responsabilité |
| --- | --- |
| `exo.py` | Commande, vues, formulaires, verrouillage, révisions et publication. |
| `utils/exo_engine.py` | Profil nominal, tirages, poids, pertes, puits et observations. |
| `utils/exo_workshop.py` | Objectifs, nouveaux jets et lots guidés. |
| `utils/exo_session.py` | États séparés et validation des sauvegardes. |
| `utils/exo_feedback.py` | Messages de bilan individuel et cumulé. |
| `utils/exo_presentation.py` | Rendu pur, pagination et budgets de taille. |
| `utils/exo_embeds.py` | Adaptateur Discord du rendu pur. |
| `utils/exo_data.py` | Parsing conservateur des fiches d'objet. |
| `utils/exo_math.py` | Modèle probabiliste/budgétaire distinct, inchangé. |

## Validation et recette de déploiement

Dans l'environnement du bot, avec les dépendances du dépôt :

```bash
python -m pytest
```

Un sous-ensemble autonome permet de vérifier moteur, parsing, export et vrais
payloads de présentation sans charger `tests/conftest.py`, qui importe Discord :

```bash
python -m pytest --noconftest -q \
  tests/test_exo_engine.py tests/test_exo_math.py tests/test_exo_data.py \
  tests/test_exo_workshop.py tests/test_exo_presentation.py tests/test_exo_structure.py
```

Ce sous-ensemble ne remplace pas les tests `test_exo_discord.py`,
`test_exo_boundaries.py`, le reste du dépôt ou une recette connectée. Le rapport
`EXO-REVIEW.md` distingue les vérifications exécutées des tests non exécutables
dans l'environnement de revue.

Sur un serveur de test, vérifier au minimum : ouverture par commande et
autocomplete ; import v1/v2 ; Gelano dont le PM passe après perte du PA ;
remontage d'une ligne naturelle ; lots avec arrêt et prix cumulé ; objectifs
modifiés sans remise à zéro ; navigation des 100 événements ; deux clics
rapides et formulaire périmé ; export avant/après fermeture ; expiration
inactive et limite dure ; catalogue lent avec image indisponible ; séparation
observation/simulation ; tentative d'action par un autre utilisateur.

Le chargement de l'extension `exo` et sa catégorie sont déjà présents dans le
dépôt. Après application du patch, redémarrer/recharger selon le mécanisme
habituel du projet et utiliser sa procédure habituelle de synchronisation des
commandes pour actualiser les descriptions. Aucun nouveau sous-groupe slash
n'est créé : `objet`, `objectif` et `reprise` restent des options de `/exo`.

## Références et statut

Consultation le 13 septembre 2026.

- Documentation primaire `discord.py`, Interactions API :
  https://discordpy.readthedocs.io/en/stable/interactions/api.html
  Jetons, réponses différées, édition éphémère, pièces jointes et composants.
- Guide publié par Xixou :
  https://xixou.io/guides/poids-des-runes/
  Comparaison communautaire seulement, pas spécification Ankama. Notamment,
  cette page affiche des poids de Soins/Vitalité différents de ceux du dépôt ;
  elle ne sert pas à certifier la table locale ni les probabilités.
- Le code du dépôt et les tests du patch sont la référence exacte du comportement
  de ce profil. Aucune formule serveur Ankama authentifiée n'a été établie dans
  cette revue.

## Compatibilité avec les corrections précédentes

L'intégration de ce patch sur la branche actuelle conserve les corrections du
12 septembre 2026. Dans « Modifier le jet », un changement d'objectif ou de graine
seul conserve les jets, le puits, les tentatives, les dépenses et les journaux.
Les lignes absentes et les lignes nulles sont équivalentes. Seul un changement
effectif de jet ou de puits remet l'état du mode courant à zéro ; l'annulation
restaure aussi les objectifs multiples et le bilan affiché.

Les quantiles gardent leur calcul décimal à précision adaptée, sans boucle de
correction non bornée. Le rendu probabiliste reste hors de la boucle Discord,
sous la limite de deux calculs simultanés, avec contrôle d'expiration et retour
à l'état précédent en cas d'annulation ou d'échec de publication.

L'ouverture d'un formulaire, y compris « Objectifs », conserve le refus immédiat
lorsque l'atelier travaille déjà, ainsi que la vérification de révision ajoutée
par la v2. Les effets de chance et les cinq résistances restent reconnus.

## Validation d'intégration locale du 13 septembre 2026

Le patch `exo_objet_fm_retro_v2.patch` a été intégré sur `main`, après le commit
`178a6e3`, en conservant les corrections précédentes. Les conflits concernaient
le contrôleur Discord, son fichier de tests et ce guide. Le parsing combiné
préserve aussi les apostrophes typographiques avant la normalisation ASCII.

- Tests dédiés `/exo` : **319 réussis**, en 13,45 secondes.
- Suite complète : **1 594 réussis, 1 ignoré**, en 34,42 secondes.
- Les tests utilisent le véritable SDK `discord.py`, avec les échanges Discord
  et les réponses des catalogues simulés ; aucune clé réelle n'est nécessaire.
- Les nouveaux tests du patch couvrent notamment 1 500 scénarios déterministes
  de comptabilité, les objectifs multiples, les lots, les sauvegardes v1/v2,
  les miniatures, les limites Discord et les interactions concurrentes.
- Le déchargement pendant un rendu vérifie maintenant que la sauvegarde v2
  attend le retour au dernier état validé, sans publier le résultat expiré.
- Le diff ne contient ni marqueur de conflit, ni secret local, ni fichier
  temporaire ajouté. Les avertissements de dépréciation restent non bloquants.

Chaque exécution utilise un nouveau répertoire `--basetemp` sous le dossier
temporaire Windows. Journaux :

- `evolution-exo-v2-targeted-905a78a2f8aa4138a55ecf993641cae3.log`
- `evolution-exo-v2-full-1ef12b2c30374b0d94e35c4470cdf3b5.log`

Cette validation complète celle de l'environnement d'origine décrite dans
`EXO-REVIEW.md`. Aucun essai manuel dans Discord ni déploiement n'a été effectué
pendant cette intégration. Les tests valident le modèle du patch, pas sa
fidélité statistique au moteur Ankama.
