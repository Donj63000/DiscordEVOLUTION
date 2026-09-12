# `/exo` — atelier personnel et probabilités de forgemagie Rétro

Version du profil : `retro-nominal-v1`. Audit des sources : 12 septembre 2026.

> Ce module n'est pas un émulateur certifié du serveur Ankama.
> Il sépare les données d'objet, les probabilités conditionnelles à un taux,
> une comptabilité nominale du puits et une simulation explicitement heuristique des pertes.
> Ne présentez pas ses pourcentages de perte comme les véritables taux du serveur.

## Installation sur cette version du bot

Ce patch est construit pour l'archive `DiscordEVOLUTION-main (7).zip`.
Depuis le répertoire contenant `main.py`, sur une copie sauvegardée et une branche propre :

```sh
git apply --check discord-evolution-exo-retro.patch
git apply discord-evolution-exo-retro.patch
python -m pip install -r requirements.txt
python -m pytest -q tests/test_exo_math.py tests/test_exo_engine.py tests/test_exo_data.py tests/test_exo_discord.py tests/test_exo_boundaries.py
python -m pytest
```

Aucune nouvelle dépendance n'est ajoutée. Le bot possède déjà `discord.py>=2.4,<3`,
`aiohttp`, Pillow, pytest et les clients Wiki/Xixou nécessaires.
Utiliser les versions de Python supportées par le dépôt et son environnement de déploiement.

La variable **existante** `XIXOU_API_KEY` est réutilisée par `DofusWikiCog`.
Ne pas ajouter de clé dans le code ou dans un export.
Sans clé Xixou, les données disponibles du Wiki servent de repli.
Sans aucun accès réseau, `/exo` ouvre tout de même une démonstration locale Gelano.

Redémarrer le bot. `main.py` charge `exo` après `dofus_wiki`, avant
`slash_commands` et la synchronisation habituelle de l'arbre.
La commande est native, réservée aux serveurs, et apparaît dans la catégorie
« Dofus Rétro » de `/aide`. Aucun alias préfixé `!exo` n'est ajouté.
Conserver le réglage de synchronisation slash déjà utilisé dans le projet.
Si la synchronisation est volontairement désactivée dans votre déploiement,
effectuer la synchronisation selon votre procédure existante.

Le module ne demande aucune permission Administrateur ni aucun nouvel intent.
Les images utilisent les permissions d'affichage habituelles du bot ; un refus
d'attacher le PNG déclenche un repli vers la miniature distante validée.

Pour retirer ce patch, avant toute modification ultérieure des mêmes fichiers :

```sh
git apply -R --check discord-evolution-exo-retro.patch
git apply -R discord-evolution-exo-retro.patch
```

Redémarrer et resynchroniser les commandes. Les exports personnels restent des fichiers
JSON, mais leur import nécessite ce module.

## Commandes

```text
/exo
/exo objet:Gelano objectif:Exo PM
/exo objet:<objet suggéré par Discord> objectif:Exo PA
/exo reprise:<fichier exo-retro-session.json>
```

`objectif` est un choix Discord, dont les valeurs internes sont `pm`, `pa` et `po`.
Un objectif différent, notamment une caractéristique ou un over, se définit ensuite
dans le formulaire « Jet / objectif ».

Sans objet, la démo est explicitement locale : elle n'affirme pas avoir consulté
Xixou et ne récupère aucune image. Sur une vraie fiche, l'illustration est obtenue
par le système d'images sécurisé déjà installé dans le bot.

La recherche est limitée aux bijoux, vêtements et catégories d'armes couvertes.
Un résultat flou ou plusieurs correspondances imposent une sélection explicite.
Les résultats sont paginés par 25, avec un maximum de 200 correspondances ;
préciser le nom lorsque cette borne est atteinte.

La recherche s'appuie sur l'index du Wiki existant : un équipement uniquement présent
chez Xixou et absent de cet index n'est pas découvrable par cette version.
Les correspondances Xixou exactes enrichissent ensuite la fiche choisie.

## Un parcours simple pour le joueur

Ouvrir `/exo objet:Gelano`, choisir l'objectif PM, puis lire le jet affiché.
**Ce n'est pas le jet de votre objet : c'est le maximum théorique de la fiche.**
Le puits initial de la simulation est **supposé égal à zéro**.

« Jet / objectif » permet de déclarer un autre point de départ, par exemple :

```text
Jet : pa=1
Puits nominal : 0
Objectif : pm=1
Graine : 42
```

Les clés utilisables sont visibles devant les lignes du panneau.
Le formulaire accepte aussi les noms usuels : `force`, `vitalité`, `chance`, etc.
Les lignes omises dans une saisie complète sont remises à zéro.
Les entiers négatifs sont acceptés pour représenter un malus, mais bloquent
l'atelier automatique. Les nombres décimaux ne sont pas des jets valides.

Choisir ensuite la caractéristique à travailler dans la liste, puis la taille
de rune. Le menu « Autres caractéristiques » donne accès aux lignes suivantes.
Toutes les caractéristiques ne possèdent pas trois tailles.

Pour les exos lourds admissibles du preset, « Passer ×1 » réalise un essai.
« ×10 » et « ×100 » conservent le jet endommagé entre essais et s'arrêtent
lorsque l'objectif déclaré est atteint ou qu'une nouvelle tentative est bloquée.
Ils **ne remontent pas automatiquement** l'objet.
L'objectif est un seuil de caractéristique, pas une garantie que les autres jets
sont conservés : un PM obtenu sur un Gelano ayant perdu son PA n'est pas un
Gelano PA/PM terminé.

« Annuler » restaure la dernière action modifiant le jet ou les paramètres,
avec sa séquence aléatoire ; il ne constitue pas un historique d'annulation illimité.
Exporter avant un changement d'objet, une nouvelle commande `/exo` ou une modification
manuelle de jet qui remet les compteurs du mode courant à zéro.

Dans « Jet / objectif », changer seulement l'objectif ou la graine conserve le jet,
le puits, la séquence, les tentatives, les dépenses et le journal. Valider le formulaire
sans changement, ajouter une ligne à zéro ou omettre une ligne déjà nulle les conserve
également. Une modification effective du jet ou du puits réinitialise uniquement le
mode courant ; le message de validation le précise et « Annuler » restaure l'état précédent.

## Les quatre écrans

### Atelier

L'écran affiche les jets actuels face aux intervalles naturels, les lignes exo/over,
le poids nominal de la rune, le puits déclaré ou inconnu, les contraintes du profil,
les taux utilisés et la dépense de runes simulée.

Les taux sont distingués par leur provenance :

* **Preset communautaire lourd** : PA, PM ou PO absent de la fiche naturelle,
  ligne actuelle à zéro et tentative admissible dans le profil. SC = 1 %, SN = 0 %,
  EC = 99 %. Le réglage de taux personnalisé ne remplace pas ce preset.
* **Bac à sable personnalisé** : pour le remontage, l'over et les autres exos,
  l'utilisateur doit saisir SC et SN. EC vaut `100 − SC − SN`.
  Aucune formule inconnue n'est remplacée par un taux deviné.
* **Observation** : le joueur saisit ce qu'il a vu dans le jeu, sans tirage aléatoire.

Remettre un PA naturellement présent sur un Gelano n'est donc **pas** traité comme
un exo PA à 1 %. Pour simuler ce remontage, il faut déclarer des hypothèses de taux ;
pour un suivi réel, il faut consigner son résultat observé.

« Taux / prix rune » conserve les hypothèses et le prix par rune précise
(caractéristique et taille). Il ne recycle pas silencieusement le même prix
pour une Ga Pa et une petite rune de vitalité. Le prix zéro signifie « non renseigné /
gratuit dans ce scénario », pas « prix réel connu ».

« Risque du modèle » réalise 1 500 essais depuis **le même instantané** du jet.
Il affiche la fréquence d'une perte sur chaque ligne, le poids moyen perdu et
le nombre de situations que la compensation nominale n'explique pas.
Il ne consomme ni rune, ni puits, ni séquence aléatoire de l'atelier.
Ces fréquences sont celles de l'heuristique décrite ci-dessous et fluctuent
avec l'échantillon : ce ne sont pas des taux de perte Ankama.

### Probabilités et budget

Cet écran est indépendant de la simulation du jet. Le joueur choisit un taux
fixe `p`, un plafond `n`, une probabilité cible, un nombre de campagnes et une graine.
Il ne certifie pas que l'objectif est réalisable sur l'objet sélectionné.

Pour des essais indépendants, de même probabilité :

```text
P(au moins une réussite en n essais) = 1 − (1 − p)^n
P(aucune réussite en n essais)       = (1 − p)^n
E[T] sans plafond                   = 1 / p
n pour atteindre une probabilité c  = ceil(log(1 − c) / log(1 − p))
E[min(T, n)]                        = [1 − (1 − p)^n] / p
```

Les cas `p = 0`, `p = 1`, `n = 0` et une cible de 100 % sont traités séparément.
Les quantiles utilisent une précision décimale adaptée et un nombre fixe de corrections
d'arrondi, même pour une probabilité très faible importée depuis un export JSON.
Le rendu de cet écran se fait hors de la boucle Discord, avec au plus deux calculs
simultanés partagés avec les campagnes et les estimations de risque.
L'espérance de 100 essais à 1 % n'est pas une garantie à la centième rune :
avec `p = 0,01`, on obtient environ **63,397 %** de chance d'au moins un succès
en 100 essais ; la médiane vaut 69, le seuil de 90 % vaut 230, de 95 % vaut 299,
et de 99 % vaut 459. Les échecs précédents ne changent pas le prochain `p`
dans ce modèle indépendant.

Les cinq coûts sont déclarés en kamas entiers : objet, préparation initiale,
rune exo, remontage entre deux essais, budget disponible.

```text
C(0) = 0
C(n ≥ 1) = objet + préparation + n × rune + (n − 1) × remontage
E[C(T)] = objet + préparation + (rune + remontage) / p − remontage
E[C(min(T,n))] =
    objet + préparation + rune × E[min(T,n)]
    + remontage × (E[min(T,n)] − 1), pour n ≥ 1
```

Le remontage n'est pas facturé après le dernier essai et la préparation n'est
pas refacturée à chaque rune. L'achat de l'objet peut être mis à zéro si vous
le possédez déjà. Un plafond de zéro correspond à une campagne non commencée.
La valeur de revente finale et une éventuelle remise en état après abandon ne
sont pas comptées.

Les campagnes s'arrêtent au premier succès ou au plafond.
Le taux de campagnes réussies, un intervalle de Wilson à 95 %, les essais consommés,
leur médiane/P95 et le coût moyen sont calculés sur **toutes** les campagnes.
Les campagnes sans succès ne sont pas retirées du dénominateur.
Les quantiles d'essais consommés avec plafond ne sont pas présentés comme
les quantiles du délai de réussite sans plafond.

Le prix de remontage est un **forfait choisi par le joueur**. Ce mode ne prétend
pas reconstruire automatiquement un jet avec une séquence de runes optimisée.
Le budget ne limite pas le nombre de runes dans l'atelier ; il sert aux calculs
de campagnes. Il n'existe pas de flux de prix HDV dans cette intégration.

### Journal et suivi déclaratif

« Mode : simu ↔ suivi » alterne entre deux états entièrement distincts :
jets, puits, tentatives, dépenses et journaux simulés ne se mélangent pas
avec les observations.

En suivi, il faut d'abord déclarer le jet réel dans « Jet / objectif ».
Le puits initial reste inconnu (`?`) tant qu'un point de départ explicite
n'est pas saisi. « Noter un résultat » accepte SC, SN ou EC et les quantités
de caractéristiques effectivement perdues :

```text
Résultat : EC
Pertes : pa=1; vi=3
```

Les pertes sont des quantités positives, comptées après le gain éventuel de
la rune sur un SN. Un SC avec des pertes est rejeté.
Les observations ne vérifient pas le journal de jeu et ne constituent pas une preuve.
Les compteurs de passages mélangent éventuellement différentes runes et différents
jets : leur moyenne ne permet pas d'inférer un taux serveur unique.

Si le puits initial est inconnu, il reste inconnu. Sur une fiche incomplète ou
comportant un malus non couvert, le calcul du puits observé est également rendu
indéterminé plutôt que d'ignorer les effets non suivis.
Les lignes reconnues peuvent toujours servir de carnet de suivi partiel.

### Guide

Le quatrième écran explique ces distinctions et lie les sources.
L'avertissement de simulation nominale reste affiché en pied de tous les écrans
de session.

## Comptabilité et règles du profil nominal

Les calculs de poids utilisent `Decimal`, pas une accumulation de flottants.
Cela garantit la cohérence **interne du modèle** ; cela ne reproduit pas les
arrondis et anomalies propres au serveur Rétro.

Pour les lignes couvertes :

```text
SC : ajout du gain ; autres lignes et puits inchangés.
SN : ajout du gain ; compensation du poids de la rune.
EC : aucun gain ; compensation du poids de la rune.
Puits suivant hors SC = max(0, puits précédent + poids perdu − poids rune).
```

Exemple nominal seulement : une Ga Pme de poids 90 échoue et fait perdre
un PA de poids 100, sans autre changement et avec un puits initial nul.
Le modèle obtient `100 − 90 = 10`, pas un puits de 100.

Le puits n'est **jamais** calculé comme « poids du jet parfait moins poids actuel ».
Il dépend d'une suite d'événements et d'un point de départ. Le module ne déduit
pas non plus une règle de conservation du puits après échange, reconnexion ou
déplacement de l'objet.

Les plafonds implémentés sont des contraintes du profil nominal :
poids cumulé des surplus over/exo limité à 101 ; pour une ligne au-delà du
maximum naturel, poids de la **ligne entière** limité à 101.
Une ligne naturellement plus lourde peut atteindre son maximum naturel,
mais n'est pas autorisée à dépasser celui-ci par cette règle.
Les caractéristiques natives ne sont pas comptées comme un exo supplémentaire.

La simulation des pertes suit une convention explicite :

1. Elle retire d'abord le surplus over/exo des autres lignes, dans un ordre tiré au sort.
2. Elle consomme ensuite le puits disponible.
3. Elle choisit uniformément une autre ligne positive et y retire le nombre
   de points nécessaires, dans la limite des points présents, puis recommence
   si nécessaire. La ligne de la rune tentée est exclue de cette allocation.
4. Un excédent de poids retiré devient du puits ; un déficit non compensé est signalé.

**Cette sélection des lignes et l'exclusion de la ligne travaillée sont des choix
pédagogiques, pas des règles serveur démontrées.** Une vraie distribution de pertes
ne peut pas être annoncée à partir de cet algorithme.

### Poids nominaux utilisés

| Caractéristique | Poids d'un point | Gains disponibles |
| --- | ---: | --- |
| PA | 100 | 1 |
| PM | 90 | 1 |
| Portée | 51 | 1 |
| Vitalité | 0,25 | 3 / 10 / 30 |
| Force, intelligence, agilité, chance | 1 | 1 / 3 / 10 |
| Sagesse | 3 | 1 / 3 / 10 |
| Prospection | 3 | 1 / 3 / 10 |
| Initiative | 0,1 | 10 / 30 / 100 |
| Bonus pods | 0,25 | 10 / 30 / 100 |
| Dommages | 20 | 1 |
| Soins | 20 | 1 |
| Coups critiques | 30 | 1 |
| Invocations | 30 | 1 |
| Dommages en pourcentage | 2 | 1 / 3 / 10 |
| Résistance fixe par élément | 2 | 1 |
| Résistance en pourcentage par élément | 6 | 1 |

Ces valeurs sont un référentiel nominal historique choisi pour ce profil, non
un export du serveur. Certaines tables publiques consultées diffèrent, notamment
sur la vitalité et les soins. Aucun poids n'est lu depuis le champ `pods` d'un
équipement : ce champ représente son encombrement, pas son poids de forgemagie.

Ne pas assimiler l'ensemble des mécaniques de DOFUS moderne ou de Touch à Rétro.
Les lignes renvoi, pièges, bonus de sorts, effets spéciaux et effets non reconnus
ne sont pas prises en charge par cet atelier automatique.
Fuite, tacle, transcendance et autres mécanismes hors périmètre ne sont pas ajoutés.
Les dégâts de base d'une arme reconnus sont conservés comme effets immuables ;
la conversion élémentaire d'une arme n'est pas simulée.
Dofus, familiers, montures, ressources, boucliers et outils sont exclus de la recherche
de cette version, sans prétendre que toutes les catégories exclues sont toujours
impossibles à modifier dans chaque version du jeu.

## Ce que l'API permet réellement

Sources consultées :

- Documentation de l'éditeur du catalogue :
  <https://xixou.io/les-outils/api/>
- Index JSON public :
  <https://xixou.io/api/v1/index.json>
- Guide/tableau publié par Xixou :
  <https://xixou.io/guides/poids-des-runes/>
- Guide communautaire Rétro hébergé sur le forum du jeu :
  <https://www.dofus-retro.com/fr/forum/11-aide-communautaire/1516-guide-forgemagie-retro>
- Documentation technique Discord :
  <https://discordpy.readthedocs.io/en/stable/interactions/api.html>

L'index public décrit cinq familles : monstres, équipements, ressources, sorts
et carte. L'API est en lecture seule et expose notamment les effets naturels
et recettes d'équipements. Aucun endpoint de forgemagie ni formule de probabilité
SC/SN/EC n'y est documenté. L'absence d'endpoint documenté n'est pas une preuve
sur un éventuel fonctionnement interne du site.

Le guide Xixou est une publication de fans, pas une spécification Ankama ;
sa table et certaines indications ne concordent pas avec tous les guides
historiques. Le guide du forum est communautaire lui aussi ; sa consultation
complète est soumise à une protection anti-robot dans l'environnement de l'audit.
Il ne faut donc pas transformer un résultat de recherche ou un tableau de fans
en promesse de reproduction exacte de la version actuelle.

Le preset à 1 % s'appuie sur la convention communautaire documentée pour les
exos lourds. La formule de remontage et la distribution exacte des pertes
ne sont **pas vérifiées** par cette livraison.
Le nommage « profil nominal » et les avertissements visibles sont donc nécessaires.

## Intégration technique et sécurité

`ExoCog` emprunte `DofusWikiCog.client`, `enrichment_client` et le résolveur d'images.
Il n'ajoute ni session HTTP ni requête munie de la clé vers un autre hôte.
Il conserve les restrictions d'origine, d'identité, de taille et de cache des
clients existants. Le client Xixou envoie sa clé dans l'en-tête existant,
pas dans les exports, les logs de ce module ou les URLs d'images.
La mention Xixou reste liée et visible lorsque ses données sont affichées.

La fiche Xixou est prioritaire si le client existant a vérifié sa correspondance
et renvoie des effets. À défaut, la fiche Wiki est utilisée et le repli est affiché.
Les effets inconnus et les doublons ambigus ne sont pas supprimés silencieusement :
ils désactivent les pertes automatiques.
Les libellés « à la chance » et les résistances fixes ou en pourcentage aux cinq
éléments sont reconnus, y compris les pluriels et les apostrophes échappées du Wiki.

Chaque panneau est privé (`ephemeral`), réservé à son auteur, avec un verrou
par session. Les formulaires mémorisent une révision ; une soumission devenue
obsolète est refusée. Les mutations ne sont validées qu'après publication du
nouvel état. Les simulations lourdes partent d'un instantané et s'exécutent
dans un thread, avec au plus deux calculs concurrents.
Pendant une action en cours, demander un formulaire reçoit immédiatement une réponse
privée invitant à réessayer. Le formulaire déjà ouvert reste intact. Un rendu de
probabilités terminé après expiration ou déchargement n'est pas publié.

Bornes : 120 ateliers/ouvertures simultanés, un par utilisateur et serveur ;
deux ouvertures par 15 secondes ; 100 essais par clic ; 1 000 000 d'essais
au plafond d'une campagne ; 50 000 campagnes ; 1 500 échantillons de risque ;
100 lignes de journal conservées par mode ; export de 128 Kio maximum.
Les dépenses et les jets sont bornés et les NaN/infinis sont rejetés.

La session expire après dix minutes d'inactivité et quatorze minutes maximum.
Elle n'est pas un patrimoine de guilde : aucune mutation de `#console`,
aucun inventaire de jeu, aucun fichier de sauvegarde serveur ou nouveau
stockage permanent n'est introduit.
Le redémarrage perd les sessions non exportées.

L'export JSON comprend profil, objet déclaré, bornes, deux états, journaux,
taux personnalisés, prix, objectif, paramètres de campagne, graine et séquence.
Il n'embarque ni clé, ni identifiant utilisateur, ni image, ni code exécutable.
L'import revalide les types, bornes et séparations des modes.
Il ne fait pas confiance à une URL fournie par le fichier et n'utilise pas `pickle`.

Un import reste **déclaratif** : le catalogue et l'image ne sont pas retéléchargés,
et le panneau l'indique. Modifier manuellement ce JSON ne permet pas de prouver
un résultat obtenu dans le jeu.
La reproductibilité signifie mêmes paramètres, même ordre de données et même
version du profil/moteur ; elle n'est pas une prédiction du prochain jet réel.

## Recette manuelle de déploiement

Vérifier en serveur de test :

- `/exo` fonctionne sans clé ; les boutons d'un autre utilisateur ne modifient rien.
- `/exo objet:Gelano` affiche la fiche et, si disponible, l'image réelle.
  Vérifier la provenance Xixou/repli Wiki et les dates de cache si elles sont signalées.
- Un exo PM utilise le preset de 1 % ; un remontage PA natif ne l'utilise pas.
- Modifier le jet, simuler, annuler, exporter et reprendre.
- Basculer en suivi, déclarer un jet puis saisir une observation ;
  revenir en simulation et vérifier l'absence de mélange.
- En probabilités, vérifier les résultats 63,397 % / 69 / 299 pour `p=1 %`
  et un plafond de 100.
- Tester l'expiration, le rechargement du module et une panne des catalogues.

Les tests automatisés ajoutés n'ouvrent aucune connexion Discord et n'utilisent
pas de clé Xixou réelle. Les tests de schéma utilisent de véritables objets
`discord.py` avec des entrées/sorties réseau simulées.
La validation automatisée ne remplace pas cette recette sur votre serveur.

## Validation locale des corrections du 12 septembre 2026

Les quatre anomalies de la revue ont été corrigées : quantile non borné,
libellés de chance/résistances non reconnus, historique effacé par un simple
changement d'objectif et formulaire bloqué derrière un chargement.

- Tests dédiés `/exo` : **188 réussis**, dont 73 nouveaux cas de non-régression.
- Suite complète : **1 463 réussis, 1 ignoré**, en 25,01 secondes.
- Les neuf cas du script de reproduction de la revue passent désormais.
- Les tests automatiques restent hors ligne. Les processus qui vérifient le rendu
  des probabilités extrêmes ont un délai maximal de huit secondes.
- La suite utilise un nouveau `--basetemp` sous le répertoire temporaire Windows.
  Journal complet : `evolution-exo-fixed-full-279d7607d3c642c788e1e4de6347945d.log`.
  Des avertissements de dépréciation restent présents ; aucun échec de test.

Une vérification réseau distincte a exercé `ExoCog.load_item` avec les clients
Wiki, Xixou et images existants, puis les quatre écrans de chaque fiche :

| Objet | Caractéristiques reconnues | Repli Moon | Miniature PNG préparée |
| --- | ---: | --- | ---: |
| Gelano | 1 | Bornes identiques | 30 043 octets |
| Anneau du Dragon Cochon | 10 | Bornes identiques | 25 883 octets |
| Voile d'encre | 10 | Bornes identiques | 17 174 octets |

Les trois fiches permettent la simulation automatique. Leurs quatre écrans
respectent les limites des embeds et conservent la miniature. Les envois Discord
ont été simulés : cette vérification n'est pas une recette manuelle dans le client
Discord. Les contrôles de diff et de secrets locaux n'ont signalé aucune anomalie.
