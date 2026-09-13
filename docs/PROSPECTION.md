# Prospection personnalisée des objets Dofus Rétro

## Utilisation

Dans `/objet` → **Drops**, le bouton **Prospection personnalisée** ouvre un
formulaire réservé à l'auteur de la recherche. Le même parcours est disponible
avec `!objet` et depuis une fiche d'équipement enrichie.

- **Ta PP** : saisir la valeur totale affichée en jeu, par exemple `435`,
  et non le bonus de l'équipement. Le calcul n'ajoute pas 100 ou 120 une seconde fois.
- **PP du groupe**, facultative : saisir le total qui compte pour débloquer
  le drop, personnage compris. En solo, répéter sa PP dans ce champ. Le laisser
  vide signifie « groupe inconnu », et non « combat en solo ».
- Pour retrouver les taux de base, rouvrir le formulaire et vider **les deux**
  champs avant de valider.

Les deux champs attendent des entiers décimaux non négatifs sans séparateur.
Les limites de saisie (10 000 PP personnelles et 100 000 PP de groupe) sont des
bornes de validation de l'outil, **pas des maxima officiels du jeu**. Le total
du groupe ne peut pas être inférieur à la PP du personnage. Une saisie invalide
reçoit une réponse privée et ne modifie pas la fiche.

Le résultat remplace l'affichage de la rubrique Drops du même message, en gardant
les taux de base et les sources. Les taux personnalisés ne sont donc pas privés
si la fiche d'origine est publique. Le paramètre appartient uniquement à cette
fiche : il est conservé pendant la pagination, les changements de rubrique et
les allers-retours vers la recette, mais n'est pas enregistré dans un profil.
Une nouvelle recherche repart sans personnalisation. Le formulaire expire au
bout de 180 secondes et ne permet pas de réactiver une fiche expirée.

La validation revient à la première page des drops recalculés. Les positions
des autres rubriques et la quantité à fabriquer sont préservées.

## Modèle de calcul

Pour un drop ordinaire, les taux Xixou sont des **pourcentages de base à 100 PP**.
On calcule, pour chaque niveau de monstre connu :

```text
taux du jet individuel (%) = min(100, taux de base (%) × PP personnelle / 100)
```

Exemple, avec les taux de la Feuille de Blop Multicolore Royal affichés dans la
fiche fournie :

| Niveau | Base | Avec 435 PP |
| --- | --- | --- |
| 150 | 10 % | 43,5 % |
| 155 | 11 % | 47,85 % |
| 160 | 12 % | 52,2 % |
| 165 | 13 % | 56,55 % |
| 170 | 14 % | 60,9 % |

Avec le seuil renseigné de 100 PP, les 435 PP personnelles suffisent à débloquer
ce drop. L'exemple suppose un monstre vaincu, l'éligibilité du personnage et
l'absence des bonus exclus ci-dessous. Les taux restent ceux de la source,
pas des constantes codées dans le calculateur.

Le module `utils/drop_calculator.py` utilise `Decimal` avec un contexte local :
aucun arrondi intermédiaire en virgule flottante et aucun arrondi d'affichage
à deux décimales qui ferait disparaître les drops rares. Par exemple,
`0.001 % × 435 / 100` donne **0,00435 %**, jamais 0 %.
Le calcul est exact pour les données et hypothèses du modèle ; il ne reproduit
pas un algorithme de tirage ou des arrondis internes non documentés du serveur.

### Seuil collectif, taux personnel

La PP totale du groupe sert uniquement au seuil ; elle n'est jamais utilisée
comme multiplicateur du taux individuel.

| Situation | Affichage |
| --- | --- |
| La PP personnelle suffit au seuil | Seuil atteint, même sans total de groupe |
| Le total saisi atteint le seuil | Seuil atteint |
| Le total saisi est sous le seuil | 0 %, avec l'explication du blocage |
| La PP personnelle est sous le seuil, total non fourni | Taux conditionnel, seuil non vérifiable |
| Seuil absent ou invalide dans la source | Taux conditionnel, donnée non renseignée |

Ainsi, avec 435 PP personnelles et un seuil de 1 000 PP, le groupe laissé vide
ne donne pas arbitrairement 0 %. Un groupe déclaré à 435 PP donne 0 %, et un
groupe déclaré à 1 000 PP débloque le taux individuel calculé avec **435**, pas
avec 1 000.

Le modèle suppose un personnage éligible au drop et des PP comptant effectivement
pour le seuil. Le bot ne connaît ni l'abonnement ni la composition du combat.
Ne pas inclure la PP d'un percepteur dans le total du groupe : elle ne contribue
pas au déblocage selon le guide Rétro cité ci-dessous. Les invocations et leurs
règles propres ne sont pas simulées.

### Quantité disponible et attribution

Une quantité maximale nulle donne 0 % lorsque le taux de base est connu.
Une quantité finie déclenche un avertissement : il existe un quota partagé,
avec attribution prioritaire aux plus fortes PP. Le **taux du jet individuel**
ne constitue alors pas la probabilité finale de recevoir l'objet.

Avec un seul exemplaire disponible, deux joueurs ne peuvent pas être traités
comme deux destinataires indépendants pouvant chacun repartir avec l'objet.
Calculer le résultat personnel final nécessiterait notamment la composition du
combat, la PP des autres participants et les règles d'attribution applicables.
Le champ « PP du groupe » n'apporte pas ces informations. Le calculateur ne
fabrique donc ni taux d'attribution exact, ni chance cumulée d'équipe.

Une quantité inconnue reste signalée comme inconnue ; elle n'est pas remplacée
par une quantité infinie. Le symbole `∞` est conservé lorsqu'il vient de Xixou.

### Exceptions et limites explicites

Les **paquets de cartes** et les **Boucliers Trophées** des Dungeon Farmers
gardent leur taux de base : les annonces Ankama citées ci-dessous indiquent
qu'il n'est pas augmenté par la PP. La détection est volontairement étroite :
catégorie « Paquet de cartes », ou catégorie « Bouclier » avec un nom commençant
par « Bouclier Trophée ». Elle ne s'applique pas à tous les boucliers, ni à tous
les objets dont le seuil vaudrait zéro. Aucun taux de 5 %, 25 % ou autre n'est
imposé : le taux vient toujours de Xixou. Une période d'événement ou une autre
condition d'obtention reste à respecter.

Pour un **objet de quête ou de mission**, aucun taux personnel n'est inventé :
les conditions spécifiques ne sont pas décrites par les champs de drop
disponibles. Les taux source restent consultables.

Ce calcul porte sur un jet, pour un monstre vaincu, **hors challenges, étoiles,
bonus de serveur/événement et autres modificateurs de combat**. Il ne prétend
pas vérifier toutes les conditions spéciales d'obtention. Un objet spécial
futur mal catégorisé devra recevoir une règle documentée dans `item_drop_rule`,
ou être déclaré non calculable, plutôt qu'une exception inférée de son taux.

Les niveaux absents ne sont pas interpolés. Un taux invalide reste
« Non calculable », distinct d'un vrai zéro. Les rangs inactifs restent exclus
par le normaliseur Xixou existant ; les niveaux suspects gardent « à vérifier ».
La plage agrégée est conservée même si certains rangs actifs n'ont pas de niveau,
afin qu'un seul niveau connu ne réduise pas artificiellement la plage affichée.

## Intégration technique

`utils/drop_calculator.py` ne dépend ni de Discord ni du réseau : paramètres
immuables, validation, règles d'exception et arithmétique pure.
`utils/wiki_embeds.py` conserve les données source et juxtapose les taux.
`dofus_wiki.py` gère le bouton et le formulaire avec le verrou, les contrôles
d'auteur, l'expiration et la gestion d'erreurs existants.

Seule la rubrique Drops est reconstruite. Les enrichissements Xixou partagés
restent immuables ; aucune requête API ni recherche d'image supplémentaire
n'est déclenchée par un recalcul. La miniature en mémoire est réutilisée, avec
le repli existant si Discord refuse les pièces jointes. Les autres rubriques
conservent leurs pages.

L'édition de message et la mise à jour des paramètres suivent la transaction de
navigation existante. Un échec ou une annulation restaure la configuration,
les pages et les positions précédentes. Les soumissions sont acquittées avant
d'attendre le verrou ; l'ouverture d'un formulaire pendant une édition reçoit
une réponse immédiate invitant à réessayer, car l'ouverture du formulaire doit
être la réponse initiale à son interaction. Les formulaires sont libérés après
validation, erreur, expiration, annulation ou déchargement du cog.

Aucune dépendance, commande slash, variable d'environnement ou donnée persistée
dans `#console` n'est ajoutée. Les données de drop restent soumises à la
configuration Xixou existante. En l'absence de drops exploitables, le bouton
n'est pas affiché.

## Sources vérifiées le 13 septembre 2026

1. [JeuxOnLine — La Prospection, guide Dofus Rétro (23 septembre 2019)](https://dofusretro.jeuxonline.info/article/14796/prospection) :
   formule linéaire, seuil collectif, quota et priorité de partage.
2. [Xixou — Calculateurs, rubrique Drop & Prospection](https://xixou.io/calculateurs/) :
   taux de base à 100 PP, formule individuelle et plafond de 100 %. L'explication
   annexe de la page contient un exemple contradictoire sur le seuil 800 ;
   il n'est pas repris. Le calcul utilise la formule explicite, recoupée avec
   JeuxOnLine, et ne reprend pas les formules de cumul ignorant les quotas.
3. [Ankama — mise à jour 1.39, Tragic Circus (23 novembre 2022)](https://www.dofus-retro.com/fr/mmorpg/actualites/maj/1549562-tragic-circus) :
   indépendance des paquets de cartes vis-à-vis de la PP et des challenges.
4. [Ankama — Dungeon Farmer : les Blops Royaux (18 mars 2026)](https://www.dofus-retro.com/fr/mmorpg/actualites/news/1768359-dungeon-farmer-blops-royaux) :
   taux d'obtention des boucliers non modifié par PP/challenges.
5. [Documentation de l'API Xixou](https://xixou.io/les-outils/api/) :
   provenance des données de l'enrichissement existant.

Les annonces Ankama ont été confirmées par leurs extraits indexés ; leur lecture
directe automatisée est bloquée par le site. Il ne s'agit pas d'une validation
du code serveur du jeu.

## Tests et recette manuelle

Les tests dédiés couvrent les nombres exacts, les saisies invalides, le seuil,
les exceptions, les pages longues, les sources, la réinitialisation,
l'isolation entre vues, le parcours slash, les images, l'expiration,
le déchargement, les échecs d'édition et les interactions concurrentes :

```bash
python -m pytest -q tests/test_drop_calculator.py tests/test_prospecting_embeds.py tests/test_prospecting_navigation.py
python -m pytest
```

Pour la recette Discord, ouvrir la Feuille de Blop Multicolore Royal dans
`/objet`, choisir Drops, saisir `435` et vérifier chaque niveau. Aller dans une
autre rubrique, consulter la recette, puis revenir aux drops et rouvrir le
formulaire : la PP doit être préremplie. Vider les deux champs doit restaurer
les taux de base. Sur un objet à seuil supérieur à 435 PP, vérifier séparément
le groupe inconnu, le solo déclaré et le groupe qui atteint le seuil.
