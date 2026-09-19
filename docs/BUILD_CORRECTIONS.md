# Fiabilisation Evolution Build

## Objets possédés

L'optimiseur conserve les jets exacts des objets déclarés possédés parmi les
candidats, même sans verrouillage et dans les autres emplacements compatibles.
Chaque exemplaire ne couvre qu'un achat. Les restrictions FM, interdictions,
limites de recherche et exigences de prix compatibles restent applicables.

## Initiative et pods déclarés

Les statistiques nues déclarées restent connues tant que le personnage est sans
équipement. Avec des équipements, initiative et pods deviennent partiels : leurs
variations dérivées ne sont pas entièrement modélisées. Le calculateur commun
transmet ce statut aux fiches et comparaisons, y compris après réimport V1.

## Actualisation explicite

Le Staff utilise `/build actualiser catalogue:sorts` ou `catalogue:tous` pour
actualiser les sorts. Sans option, seuls les équipements sont actualisés.
Une panne conserve la dernière archive confirmée et le résultat indique la
famille en échec. Les attaques déjà sélectionnées gardent leur version.
