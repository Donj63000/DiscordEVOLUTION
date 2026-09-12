# Fiches objets enrichies avec Xixou

Je complète les objets du catalogue actuel avec les drops, zones, récoltes,
conditions, panoplies et utilisations disponibles dans l'API Xixou.
Les commandes `/objet`, `!objet`, la sélection d'un équipement et le retour
depuis une recette utilisent le même enrichissement.

## Configuration locale

Je renseigne `XIXOU_API_KEY` dans `.env`, qui est ignoré par Git.
Une clé vide conserve les fiches de base et ne déclenche aucun appel Xixou.
`XIXOU_CACHE_TTL` vaut 3600 secondes et `XIXOU_TIMEOUT` vaut 10 secondes par défaut.
Je redémarre le bot après une modification de ces paramètres.

La clé reste dans l'en-tête `X-Api-Key` des appels vers les quatre catalogues fixes
de `https://xixou.io/api/v1/`. Les images publiques utilisent un client séparé,
sans cette clé. Les fiches affichent les sources et la date de génération des données.

Documentation de la source : [API Xixou](https://xixou.io/les-outils/api/).

## Consultation

La fiche commence par un résumé. Le menu propose Caractéristiques, Drops,
Zones et carte, Récolte et Utilisations. Précédent/Suivant parcourt les pages de
la rubrique. Le bouton recette conserve la quantité et permet de revenir à la
rubrique et à la page précédemment consultées. Retour aux résultats conserve
les filtres et la position dans la recherche.

Les drops indiquent les taux de base, leur détail par niveau lorsqu'il existe,
la prospection requise et la quantité maximale connue. Les niveaux signalés
comme suspects par Xixou portent la mention « à vérifier » ; les rangs inactifs
ne participent pas à la plage affichée. Il ne s'agit pas d'un calcul de chance
personnelle incluant prospection, challenges ou autres bonus.

Une donnée absente reste « non renseignée ». Les identités ambiguës sont écartées,
en conservant notamment les étoiles des viandes et les variantes de niveau.
Les objets ne sont pas associés automatiquement à un groupe de poissons ou à
une autre ressource simplement parce que leurs noms se ressemblent.

Les aperçus de zones surlignent les cellules connues, ou le polygone disponible.
Ils ne garantissent pas la présence du monstre sur une case précise. Les récoltes
présentent dix positions par page, avec les points connus et leurs liens.
Une zone sans géométrie reste consultable sous son nom sans emplacement inventé.

## Miniatures des objets

Je conserve l'illustration en haut à droite dans chaque rubrique, dans les recettes
et à côté des grandes cartes. La miniature préparée est un PNG de 256 × 256 pixels,
joint sous `objet.png` ; la carte utilise une pièce jointe indépendante.
Les changements de page, de rubrique ou de quantité réutilisent la même miniature
en mémoire. Une édition refusée restaure la rubrique et les valeurs précédentes.

Les images Xixou proviennent uniquement d'un objet identifié sans ambiguïté.
La table explicite de `utils/xixou_image_paths.py` reprend les dossiers des
configurations publiques Xixou : un même nom de fichier dans deux catégories
ne désigne pas forcément le même objet. Un PNG original est prioritaire ; un SVG
utilise sa version PNG publique dans `xixou-og`. Les paquets de cartes conservent
leur dossier particulier `tcg-images/paquets`.
Les trois noms Moon « Paquet de cartes : communes/rares/épiques » correspondent
explicitement aux noms Xixou sans deux-points : identifiants Moon 14050 à 14052,
catégorie Paquet de cartes et niveau 1 vérifiés. Cette exception ne retire pas
la ponctuation des autres noms et rejette tout identifiant Xixou contradictoire.

Si nécessaire, je charge l'index Moon à la demande pour chercher son illustration.
Toute adresse est ensuite vérifiée par le client d'images. Le délai total de cette
résolution est limité à cinq secondes. Si les pièces jointes sont refusées,
Discord reçoit l'adresse publique vérifiée comme miniature distante. Sans image
valide, la fiche reste utilisable sans illustration de remplacement.

Le client `utils/wiki_images.py` accepte uniquement les chemins HTTPS publics
autorisés, sans redirection ni authentification. Il vérifie le contenu PNG,
la limite de 1 Mio et les dimensions maximales de 2048 × 2048 pixels. Le traitement
Pillow s'exécute hors de la boucle Discord. Le recadrage retire les marges
exactement uniformes ou entièrement transparentes, sans seuil de couleur qui
risquerait de supprimer les rayons pâles du Cuivre. Les proportions, les pixels
partiellement transparents et les effets lumineux sont conservés.

Le cache d'images est limité à 128 entrées et 8 Mio, avec une validité d'une heure
et un secours signalé jusqu'à 24 heures. Les échecs sont mémorisés cinq minutes.
Les requêtes identiques sont mutualisées ; au maximum deux téléchargements et
un traitement d'image s'exécutent simultanément. L'autocomplétion ne télécharge
aucune image. Le déchargement attend les traitements en cours et ferme les sessions.

Sources des correspondances : [fiche et configuration équipements Xixou](https://xixou.io/encyclopedie/objets/gelano/),
[configuration publique des ressources](https://xixou.io/wp-admin/admin-ajax.php?action=xixou_ressources_ui_data&lang=fr).

## Cache, erreurs et ressources

Je charge les catalogues en arrière-plan au démarrage, avec deux téléchargements
simultanés au maximum. L'autocomplétion utilise le catalogue existant sans lancer
d'appel Xixou. Les actualisations partagées utilisent ETag et `If-None-Match`.
Une réponse invalide ne remplace pas une copie valide.
Les enrichissements calculés sont mutualisés et gardés dans un cache LRU de
128 fiches, invalidé lorsque les catalogues changent.

Lors d'une panne temporaire, une copie datant de moins de 24 heures reste
utilisable et est signalée comme ancienne. Les délais `Retry-After` sont respectés.
Sans données exploitables, les fiches de base restent accessibles. Les journaux
DEBUG des modules wiki/Xixou indiquent les familles, résultats et types d'erreurs,
sans journaliser les secrets ou le contenu des en-têtes.

La carte est calculée à la demande, hors de la boucle Discord, avec un seul rendu
simultané. Le rendu utilise les tuiles publiques nécessaires et un cache compressé
borné. L'image finale ne dépasse pas 1200 pixels. Une carte indisponible ou une
pièce jointe refusée laisse accessibles le texte et les liens disponibles.
Le déchargement ferme les clients, termine les rendus et empêche la création de
nouvelles vues pendant l'arrêt.

Ces caches sont des références externes temporaires en mémoire. Ils ne sont pas
des données de guilde et ne modifient pas la persistance dans `#console`.

## Validation

Les tests HTTP utilisent des réponses simulées ; la configuration locale de la
clé est retirée de l'environnement des tests par la fixture commune. Les parcours
Discord utilisent les vrais handlers et composants, avec les envois simulés.

Je lance la suite complète sous PowerShell avec un dossier temporaire neuf :

```powershell
$xixouTestTemp = Join-Path $env:TEMP ('evolution-xixou-tests-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python.exe -m pytest --basetemp $xixouTestTemp
```

Les scénarios couvrent les formats API, identités, petits taux, erreurs HTTP,
cache et concurrence, rubriques, quantités, expiration, arrêt pendant une requête,
pièces jointes, limites Discord et calibration des cartes. Pour les miniatures,
ils couvrent aussi les collisions de fichiers, exceptions de dossiers, PNG
corrompus ou tronqués, cache borné, chargement à froid de l'index Moon,
conservation des effets lumineux, fermeture pendant un téléchargement ou un
envoi, et coexistence de l'objet avec la carte dans les parcours Discord.

Validation complète du 12 septembre 2026 : **1275 tests réussis, 1 test ignoré**,
dont 448 nouveaux cas pour l'enrichissement Xixou et les miniatures (234 pour
l'ajout des images après la première intégration). Le passage final a duré
18,59 secondes, avec un répertoire temporaire neuf.
Les avertissements sont des dépréciations des dépendances et de tests existants.

La vérification des données réelles comprend Gelano, laine de Bouftou, bois de
Frêne, Cuivre, Dofus Vulbis, coiffe du Bouftou et les deux amulettes Turquoise.
Les cinq miniatures de Gelano, laine de Bouftou, bois de Frêne, Cuivre et paquet
de cartes communes ont également été obtenues depuis les vraies fiches Moon,
préparées par le client de production et inspectées visuellement. Elles mesurent
toutes 256 × 256 pixels, pèsent entre 21 et 70 ko et ont demandé entre 0,55 et
0,97 seconde par fiche lors de ce contrôle. Les images PNG sources et les SVG
convertis publiquement par Xixou sont donc tous deux couverts.
Les aperçus locaux montrent aussi les vrais contenus et pièces jointes du parcours
Gelano Résumé → Zones → Recette → Zones, avec quantité 3 conservée. Ces aperçus
reconstituent la présentation pour inspection ; ce ne sont pas des captures
de messages envoyés sur Discord.
Le déploiement et la configuration Render constituent l'étape suivante, après
validation locale ; les mesures Windows ne remplacent pas une mesure sur Render.

Le 12 septembre 2026, le chargement local hors connexion Discord a validé les
23 extensions configurées, 22 cogs et 33 commandes préfixées. Les appels aux
catalogues, miniatures et tuiles étaient réels ; les échanges Discord et la synchronisation
des commandes étaient bloqués ou simulés. Le pic mémoire Windows mesuré après
chargement et rendu de trois cartes était de **203,1 Mio**, sans données de guilde
de production. Les rendus ont pris entre **0,33 et 0,56 seconde** chacun.
Après fermeture du bot, la mémoire résidente était de 158,6 Mio. Aucun appel HTTP
vers Discord n'a été effectué. Les rapports JSON, PNG et journaux de validation
restent dans des dossiers temporaires Windows et ne sont pas suivis par Git.
