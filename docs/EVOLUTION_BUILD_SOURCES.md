# Evolution Build — registre des données de panoplies

Version 1.1.0-beta.2 — transcription des pages publiques Xixou consultées le 19 septembre 2026.

**14 panoplies, 67 seuils, 53 seuils avec bonus et 14 seuils explicites à une pièce sans bonus.**
Ces tables sont des bonus TOTAUX par nombre de pièces, pas des incréments à additionner.
Les cas chiffrés des tests correspondent aux pages publiées ; ce ne sont pas des observations dans le client de jeu.

| Panoplie | Pièces / seuils | Source primaire |
|---|---:|---|
| Panoplie Ancestrale | 6 | https://xixou.io/encyclopedie/panoplies/panoplie-ancestrale/ |
| Panoplie Blop Multicolore Royale | 4 | https://xixou.io/encyclopedie/panoplies/panoplie-blop-multicolore-royale/ |
| Panoplie des Sous-bois | 3 | https://xixou.io/encyclopedie/panoplies/panoplie-des-sous-bois/ |
| Panoplie du Bouftou | 7 | https://xixou.io/encyclopedie/panoplies/panoplie-du-bouftou/ |
| Panoplie du Bworker Berserker | 4 | https://xixou.io/encyclopedie/panoplies/panoplie-du-bworker-berserker/ |
| Panoplie du Bworker Gladiateur | 4 | https://xixou.io/encyclopedie/panoplies/panoplie-du-bworker-gladiateur/ |
| Panoplie du Chêne Mou | 6 | https://xixou.io/encyclopedie/panoplies/panoplie-du-chene-mou/ |
| Panoplie du Jeune Aventurier | 6 | https://xixou.io/encyclopedie/panoplies/panoplie-du-jeune-aventurier/ |
| Panoplie du Meulou | 6 | https://xixou.io/encyclopedie/panoplies/panoplie-du-meulou/ |
| Panoplie du Minotot | 7 | https://xixou.io/encyclopedie/panoplies/panoplie-du-minotot/ |
| Panoplie du Prespic | 4 | https://xixou.io/encyclopedie/panoplies/panoplie-du-prespic/ |
| Panoplie Ougah | 4 | https://xixou.io/encyclopedie/panoplies/panoplie-ougah/ |
| Panoplie Souveraine | 3 | https://xixou.io/encyclopedie/panoplies/panoplie-souveraine/ |
| Panoplie Ventouse | 3 | https://xixou.io/encyclopedie/panoplies/panoplie-ventouse/ |

## Décisions de transcription

Les catégories sont conservées : « +30 en vie » de la panoplie Bouftou est stocké en points de vie (`pv`), pas en vitalité (`vi`). Le malus reste signé, le renvoi est distinct des dommages, et les résistances en pourcentage sont distinctes des résistances fixes. Les bonus des panoplies Bworker ne sont pas intervertis : Gladiateur utilise les lignes publiées Force/Agilité/PA et Berserker Intelligence/Chance/PM.

Le parseur public requiert l’identité de la page, les bornes annoncées et tous les seuils. La mise à une pièce sans bonus n’est justifiée que par une source annonçant les bonus à partir de deux pièces. Un effet inconnu entraîne un refus de la table complète, plutôt qu’un faux zéro.

## Niveau de preuve

• Données numériques embarquées : pages publiques consultées et transcription contrôlée.
• Tests HTML : contrats synthétiques couvrant structure, troncature, champs inconnus et sécurité. Aucune prétention de capture HTML réelle ou de recette de toutes les pages au moyen de ce parseur.
• API équipements authentifiée : non appelée pendant cette livraison, aucune clé utilisée.
• Personnages réels : aucun relevé fourni ; règles non certifiées inchangées.

Pour valider le HTML réel dans le déploiement : `python tools/build_preflight.py --public-panoplies`. Le contrôle ne modifie ni le fichier local ni les snapshots de #console. Une modification du site peut exiger d’adapter le parseur sans changer les calculs du domaine.

Documentation technique : https://xixou.io/les-outils/api/ ; https://discordpy.readthedocs.io/en/stable/interactions/api.html#discord.ui.DynamicItem . L’attribution Xixou est conservée dans les fiches et les rapports.
