# Revue de `/exo objet` et correctif livré

Date : 13 septembre 2026
Base : archive fournie `DiscordEVOLUTION-main (8).zip`
Périmètre : commande `/exo`, sélection d'objet, moteur FM, interface,
observations, reprise JSON, tests et documentation associés.

## Conclusion

La version initiale avait une bonne séparation entre calcul, données, états
et interface, avec commandes privées, graine reproductible et import/export.
Mais son parcours de FM normale était bloqué par la saisie obligatoire de taux,
la ligne travaillée était artificiellement immunisée et un objectif exo seul
pouvait annoncer un objet fini alors que ses statistiques utiles avaient disparu.

Le correctif transforme ce parcours en atelier jouable à état continu : poser,
voir les pertes, remonter, retenter et vérifier tous les minimums de l'objet.
Il ajoute une meilleure traçabilité et réduit les actions ambiguës dans Discord.

Ce résultat reste un **simulateur pédagogique Rétro**, pas une reconstitution
certifiée du moteur Ankama. Les probabilités de remontage/over/exo léger sont
des formules locales nouvelles, documentées et explicitement non calibrées.
La table de poids historique du dépôt n'est pas changée sans preuve de version.
La fidélité statistique serveur reste donc une limite importante, pas un problème
que les tests logiciels pourraient certifier.

## Méthode et base des constats

Lecture du cog, de ses dépendances directes, du moteur, de la sérialisation,
du parsing, des rendus et des tests existants ; vérification des conventions
d'intégration du dépôt et de la documentation primaire `discord.py`.

Les références de lignes ci-dessous concernent **les fichiers de l'archive
d'origine**, avant application du patch. Les nouveaux numéros diffèrent.

Les tests purs ont été exécutés sur les vrais modules du projet. Aucun faux SDK
Discord n'a été créé pour obtenir artificiellement une suite verte. Les tests
d'interaction ajoutés restent à exécuter avec la dépendance réelle du bot.

## Constats prioritaires et résolution

| Priorité | Constat initial et impact joueur | Correctif |
| --- | --- | --- |
| Haute | `utils/exo_engine.py:236`, `rates_for` : pas de taux par défaut hors preset lourd. Remonter une ligne nécessite de renseigner SC/SN. | Modèle automatique explicite ; réglages personnalisés facultatifs et provenance des taux dans chaque événement. |
| Haute | `utils/exo_engine.py:254`, `allocate_loss` : exclusion systématique de la caractéristique travaillée. Elle ne peut pas perdre ses anciens points, même lors d'un EC. | Ligne réintégrée dans les pertes possibles ; calcul sur l'ancien jet, puis gain de SN ; tests de gains bruts et variations nettes. |
| Haute | `Session.reached` initial : objectif unique. PM présent pouvait signifier « terminé » avec PA absent. | Objectif principal et minimums de qualité ; Gelano PA/PM traité comme deux seuils ; indication de remontage après succès incomplet. |
| Haute | `exo.py:478`, `handle`, branche des lots : retour essentiellement limité au dernier essai. | Bilan de l'ensemble du lot, coûts/gains/pertes cumulés et motif d'arrêt. Aucun remontage gratuit entre les tentatives. |
| Haute | `utils/exo_engine.py:340`, `observe` : un déficit comptable pouvait être transformé en puits nul réputé connu. | Puits inconnu en cas de données incompatibles, poids inexpliqué visible et suivi séparé de la simulation. |
| Moyenne | `exo.py:165`, formulaire de jet/objectif : modifier le but passait par une remise à zéro du mode. | Formulaire Objectifs indépendant ; nouveau jet toujours possible, mais explicitement destructif et annulable. |
| Moyenne | `utils/exo_embeds.py:36`, journal : événements regroupés dans un champ borné, fin de texte susceptible d'être coupée. | Un événement détaillé par page ; navigation jusqu'aux 100 lignes conservées ; budget UTF-16 du payload testé. |
| Moyenne | Identifiants de composants statiques malgré le verrou par session ; ancien clic susceptible de s'exécuter sur le nouveau choix de rune. | Révision dans les composants et refus sous verrou d'une action périmée ; formulaire occupé refusé immédiatement. |
| Moyenne | Recalcul/publication du panneau et reconstruction pas entièrement protégés par le même rollback. | Session candidate copiée ; reconstruction et publication incluses dans le retour à l'état local précédent. |
| Moyenne | `utils/exo_data.py`, exclusions de dégâts trop larges : effet inconnu « Dommages renvoyés » susceptible d'être ignoré. | Reconnaissance ciblée des dégâts de base ; effet inconnu conservé et simulation bloquée plutôt que poids inventé. |
| Moyenne | `exo.py:282`, fermeture/expiration : arrêt du panneau sans snapshot de récupération automatiquement joint. | Tentative de joindre le JSON à fermeture, expiration, remplacement ou déchargement normal ; échec annoncé sans promesse de persistance. |
| Moyenne | `exo.py:663`, récupération d'objet et illustration sous budget réseau commun. | Délais séparés ; enrichissement et image facultatifs ; détail valide conservé. |
| Moyenne | Erreur après réponse slash différée envoyée en suivi, laissant la réponse originale sans résultat utile. | Édition de la réponse originale différée pour les erreurs d'ouverture. |
| Faible | Liste générale de statistiques avant les lignes utiles ; prix et taux exposés tôt ; images renvoyées à chaque action. | Lignes natives d'abord, rune conseillée, réglages avancés séparés, pièce jointe existante conservée. |

Les tests et explications font partie du correctif ; il ne s'agit pas seulement
d'une proposition d'interface.

## Parcours obtenu

### Première utilisation

`/exo` démarre toujours sans réseau sur le Gelano de démonstration.
`/exo objet:...` exploite le catalogue existant. L'option `objectif` reste
facultative ; sans précision, une caractéristique absente est proposée.
L'utilisateur choisit une ligne, une rune et pose directement sa rune.

Le panneau présente le jet courant complet, les minimums de qualité, le puits,
le coût, le type de résultat et la prochaine action utile. Les paramètres
avancés n'empêchent plus de commencer.

### Une séance de FM, pas des essais indépendants

Une tentative utilise exactement l'état laissé par la précédente. Les dégâts
sur les caractéristiques et le puits ne sont pas effacés. Les lots ×10/×100
s'arrêtent au seuil de la ligne choisie ou avant un dépassement involontaire.
Le joueur choisit ensuite une autre ligne à remonter.

Un Gelano avec PM mais sans PA n'est pas fini. Remettre son PA utilise le
chemin de remontage naturel, en conservant le PM existant dans l'état et donc
dans les pertes possibles.

L'onglet de probabilités géométriques demeure un outil indépendant : son
forfait de remontage ne répare pas magiquement l'objet dans l'atelier.

### Explication d'une rune

Le résultat différencie le gain accepté, les pertes brutes, les transitions
avant/après et leur variation nette. Cela couvre notamment un SN qui regagne
ce qu'il vient de perdre sur sa propre ligne. Le journal enregistre le profil,
les taux du tirage, le prix et les puits.

Un lot montre tous ses gains/pertes, et pas seulement le dernier événement.
Le journal permet de revenir aux essais individuels conservés.

### Reprise et sécurité d'utilisation

Le JSON v2 reprend la simulation et les observations, les objectifs, paramètres
et historiques conservés. Les v1 restent importables ; une notice annonce
le changement de moteur pour les prochains tirages et la conservation de
l'ancien objectif simple.

Les imports contrôlent types, bornes, tailles, doublons JSON et cohérence des
nouveaux événements. Cela protège le traitement du fichier, mais n'en fait pas
une preuve authentique de résultats Dofus.

Les fermetures tentent une sauvegarde jointe. Un arrêt brutal du bot ne garantit
aucune sauvegarde : exporter demeure nécessaire.

## Validation réellement effectuée

Le sous-ensemble autonome contient **189 tests réussis** dans l'environnement
de revue, dont trois tests paramétrés exécutant ensemble **1 500 scénarios
déterministes** de comptabilité des gains, pertes et puits. Il couvre notamment :

- SC/SN/EC, perte de la ligne travaillée, précision décimale, plafonds, atomicité
  des erreurs, conservation du jet et calcul de risque sans mutation ;
- objectifs combinés, remontage de PA, lots bornés, bilan cumulé, nouveaux jets,
  changements d'objectif et reprise de la séquence ;
- parsing français/Unicode, effets inconnus, import v1/v2 et fichiers incohérents ;
- véritables payloads de présentation, toutes les pages d'un journal de 100
  essais, objets denses, emoji, mentions et limites de taille ;
- compilation des modules de production sans import SDK, longueur des libellés
  de formulaires et conservation des trois options facultatives de `/exo`.

Commande exécutée :

```bash
python -m pytest --noconftest --color=no -q \
  tests/test_exo_engine.py tests/test_exo_math.py tests/test_exo_data.py \
  tests/test_exo_workshop.py tests/test_exo_presentation.py tests/test_exo_structure.py
```

Environnement de revue : Python 3.13.5, pytest installé localement.
Ce n'est pas une reproduction complète de l'environnement de production
décrit par `requirements.txt`.

### Ce qui n'a pas été validé ici

`python -m pytest` ne peut pas charger `tests/conftest.py`, car le module
`discord` est absent. L'installation de `discord.py` n'a pas abouti : résolution
réseau indisponible dans l'environnement d'exécution. Il est donc incorrect
d'affirmer que tous les tests du dépôt, les interactions SDK ou le bot en ligne
ont été exécutés avec succès.

`tests/test_exo_discord.py` est enrichi de tests pour clics concurrents,
formulaires périmés, rollback, sauvegarde à fermeture/expiration, navigation,
pièces jointes et erreurs de catalogue. Ces tests, ainsi que
`tests/test_exo_boundaries.py`, doivent être lancés avec le vrai SDK.

Aucun jeton de bot, serveur Discord connecté, mesure de latence réelle,
test visuel mobile ou comparaison statistique contre des séries de résultats
Ankama n'a été utilisé. Les tests prouvent les invariants du modèle implémenté,
pas sa conformité statistique au jeu.

## Décisions de conception et limites restantes

**Taux et pertes.** Les coefficients automatiques sont des choix de conception.
La sélection des pertes reste une heuristique uniforme avec ordre de priorité
nominal. La chance réelle selon serveur, métier, objet, ligne, rune et version
n'est pas reproduite avec une calibration démontrée. Le preset lourd à 1 %
est une hypothèse communautaire déjà présente, pas une garantie individuelle.

**Poids.** La table initiale est conservée, notamment Vitalité 0,25 et Soins 20.
Le guide Xixou consulté affiche des valeurs différentes. Sans référentiel
versionné faisant autorité, changer seulement ces nombres aurait introduit
une nouvelle prétention de fidélité et modifié la signification des anciens
puits. Une validation métier des poids et exceptions de la version cible reste
nécessaire avant de présenter l'outil comme simulateur fidèle au serveur.

**Couverture.** Effets inconnus et malus n'ont pas de calcul automatique
inventé. Pas de profil Unity/Touch, transfert en jeu, inventaire de runes limité,
prix HDV automatique, moteur d'optimisation du coût ou calibration par métier.

**Conservation.** Les 100 derniers événements par mode sont conservés, pas un
historique illimité. Le JSON n'est pas signé. La sauvegarde jointe à expiration
est au mieux et ne couvre pas un arrêt brutal. Le rollback local ne peut pas
garantir l'atomicité d'une réponse HTTP dont le résultat réseau est incertain.

## Application du patch et retour arrière

Le patch est construit contre l'archive fournie, sans inclure les données
d'exécution, secrets, images du dépôt ou changements d'autres fonctionnalités.
Copier le fichier `.patch` à la racine d'un checkout propre correspondant à
cette version, puis :

```bash
git switch -c amelioration/exo-fm
git apply --check exo_objet_fm_retro_v2.patch
git apply exo_objet_fm_retro_v2.patch
python -m pytest
```

Dans l'environnement du bot, installer les dépendances habituelles du dépôt
si elles ne sont pas déjà présentes. Ne pas considérer le sous-ensemble
`--noconftest` comme un remplacement de la suite complète en intégration.

Effectuer la recette manuelle décrite dans `EXO.md`, puis redémarrer/recharger
le bot et actualiser les commandes avec la procédure existante du projet.
Les noms des options ne changent pas.

Avant de revenir à l'ancienne version, récupérer les exports v1 encore utiles :
l'ancien moteur ne sait pas importer les nouveaux fichiers v2. Pour retirer
le correctif, sans modifications locales ultérieures sur les mêmes lignes :

```bash
git apply --reverse --check exo_objet_fm_retro_v2.patch
git apply --reverse exo_objet_fm_retro_v2.patch
```

## Références

Les constats fonctionnels proviennent des fichiers de l'archive ; les formules
et comportements exacts sont documentés dans `EXO.md` et les modules du patch.

Documentation primaire de l'intégration Discord :
https://discordpy.readthedocs.io/en/stable/interactions/api.html

Comparaison communautaire de poids, non utilisée comme certification Ankama :
https://xixou.io/guides/poids-des-runes/

Pages consultées le 13 septembre 2026. Aucune formule serveur authentifiée
n'a été déduite de ces pages.
