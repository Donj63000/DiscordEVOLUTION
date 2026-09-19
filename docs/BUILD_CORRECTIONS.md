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

## Retour à une archive existante

Réactiver une version déjà archivée met à jour l'index confirmé sans renvoyer ses
fragments. Une séquence A → B → A conserve donc A après redémarrage, pour chaque
famille de données versionnées. Un échec d'engagement conserve la version active.

## Envois interrompus

Chaque téléversement possède un journal `===BOTEVOBUILD=== upload` dans
`#console`. Avant d'envoyer un fragment, le bot confirme son intention dans ce
journal. Une réponse perdue déclenche une recherche complète des messages du bot,
puis une vérification des empreintes et des tailles avant réutilisation.
Les catalogues déjà engagés ne sont pas retéléchargés pour cette recherche.

Les fragments confirmés d'un téléversement interrompu peuvent être repris après
redémarrage. Si un fragment tenté reste introuvable, les nouveaux téléversements
sont suspendus et un contrôle Staff est demandé : un redémarrage ne contourne pas
cette protection. Ne pas effacer le journal pour forcer une nouvelle tentative.

Après engagement confirmé, le bot élimine les fragments orphelins vérifiés et les
journaux terminés. Il conserve les références actives, historiques et les envois
incomplets. Une recherche incomplète ou une permission refusée suspend ce nettoyage
et produit un diagnostic dans les logs. Les messages des autres auteurs et modules
restent protégés. `clear console` préserve aussi les journaux Build.
