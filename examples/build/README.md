# Références du builder

`reference_synthetic_v1.json` décrit des cas **synthétiques**, calculés depuis le profil de règles documentées du 20 septembre 2026. Ce fichier ne contient ni capture ni relevé d'un personnage dans Dofus. Il ne doit jamais être utilisé pour marquer une règle « validée en jeu ».

Le chargeur `utils.build.reference_cases.load_reference_cases` vérifie le format version 1, les limites et les profils. `check_reference_cases` compare les métriques attendues au moteur commun ; il indique séparément les écarts, les valeurs partielles et la nature de la preuve fournie. Le catalogue vide correspondant est obtenu par `freeze_catalog(())` ; les règles sont `load_rules()` à la version citée dans le cas. Un identifiant de version différent exige de garder l'ancien instantané ou de migrer explicitement le cas.

Pour ajouter une observation réelle, utiliser `origin: "in_game"`, indiquer la version officielle du jeu, une date ISO 8601 avec fuseau dans `observed_at` et au moins une URL HTTPS vers une capture accessible dans `evidence`. Renseigner l'état du personnage, ses jets exacts et les statistiques affichées. Le chargeur exige ces références mais ne prétend pas authentifier une capture : la comparaison et la revue humaine restent nécessaires.

La recette à constituer doit couvrir personnages nus et équipés, niveaux 1/99/100/200, seuils de paliers, parchottage, malus, panoplies, restrictions et jets déclarés. Aucune observation réelle n'était fournie lors de cette livraison.

## Audit de la source

Exécuter `python tools/build_audit.py --output rapport_catalogue.json` pour produire un rapport public détaillé. La clé déjà configurée est envoyée uniquement en en-tête au client Xixou ; elle n'est jamais écrite dans le rapport. Cet outil ne modifie ni Discord ni le catalogue actif du bot. Le fichier de sortie est un diagnostic facultatif, pas une base de données.

L'audit du 20 septembre 2026 a reçu 6 593 lignes depuis l'API, dont 75 dragodindes, 171 familiers, 302 boucliers et 14 Dofus. Le catalogue comprend aussi des entrées saisonnières et des panoplies qui ne sont pas des objets équipables : leurs exclusions sont explicites. Chaque diagnostic conserve code, nom, catégorie, référence éventuelle, texte, provenance et empreinte de la ligne. Les bonus non pris en charge restent inconnus.

Les règles documentées reposent sur les [tables de classes Xixou](https://xixou.io/guides/classes/) et les [formules publiques de son builder](https://xixou.io/wp-content/plugins/xixou-builder/assets/js/xb-builder-v2.js). Ce sont les sources de calcul utilisées ; cela ne remplace pas une comparaison au jeu officiel. Le profil conserve tous ses indicateurs `*_verified` à `false`.
