# Mise à niveau IA d'Evolution — audit et installation

Date : 15 septembre 2026. Base : archive `DiscordEVOLUTION-main (11)(1).zip`.
Modèle conservé : `gpt-5.6-luna`. Aucune nouvelle dépendance de production.
Aucun appel réel à Discord, OpenAI ou Xixou effectué pendant cette intervention.

**Correctif complémentaire du 16 septembre 2026 :** voir
`EVO_METIERS_WEB_HOTFIX.md` pour la lecture synchronisée des métiers, le
cache Discord incomplet et la désactivation du seul Web en cas de plafond insuffisant.

## 1. Diagnostic de l'intégration existante

La base est sérieuse : API Responses, paramètres d'outils stricts, règles de
permission Python, mémoire séparée par membre/salon, préservation des éléments
de raisonnement entre appels, calculs de drop/recettes hors modèle, quotas et
réservations durables dans la console. Réécrire cela avec un agent générique
aurait fait perdre des protections utiles.

Les blocages identifiés sont concrets :

- `utils/evo_agent.py` interdisait explicitement la création d'activités dans
  le prompt. Aucun outil de création n'était exposé, bien que le module
  activités sache déjà créer et publier.
- Les adaptateurs métiers acceptaient certaines formes impératives mais pas
  plusieurs formulations naturelles avec infinitif. Le routage ne reconnaissait
  pas « tailleurs » et plusieurs autres pluriels. La recherche faisait une
  comparaison textuelle pouvant confondre des métiers voisins.
- `consulter_site` lisait deux pages de domaines autorisés ; ce n'était pas un
  moteur de recherche. Il était donc impossible de découvrir de nouvelles sources
  par une recherche générale.
- Le prompt imposait des réponses de une à quatre phrases et réservait tous les
  faits aux outils du jeu. Cela empêchait une aide généraliste naturelle.
  Le chargement de configuration plafonnait aussi l'objectif visible à 400 tokens.
- La recherche d'équipements était un classement de pièces, pas une proposition
  multi-emplacements. Les plafonds de trois générations et la seule relecture
  possible limitaient les recherches dépendantes.
- La sortie était tronquée à 1 800 unités UTF-16, même lorsque le contenu utile
  nécessitait plusieurs messages.

## 2. Ce qui est codé

### Actions : réutiliser les services, pas exécuter du texte Discord

`ActiviteCog.create_activity()` devient le service partagé de création.
`/activite creer` et le nouvel outil `creer_activite` l'utilisent. Il vérifie le
rôle, le serveur et les données, travaille sous verrou, persiste avant de
confirmer, et utilise une clé de création pour ne pas dupliquer une même demande
Discord. L'organisateur est toujours le membre authentifié ; aucun identifiant
de cible n'est accepté dans le schéma de l'outil.

La création IA vérifie en plus l'accès au salon d'organisation avant et après
la réservation de rédaction. Un validateur déterministe lie le titre, le lieu,
la description, la date, l'heure, la capacité et la durée au message actuel.
Les dates relatives sont interprétées par le parseur d'activités existant,
avec les règles Europe/Paris ; le modèle ne calcule pas une date calendaire.
Sans capacité/durée explicite : **8 places et 180 minutes**, annoncées comme
valeurs par défaut. Ce défaut IA est explicite et ne reprend pas une éventuelle
surcharge de `ACTIVITE_DEFAULT_DURATION_MINUTES`.

Un reçu est établi après sauvegarde et avant publication. Une publication
impossible ne signifie pas que la sortie a été annulée : le résultat indique
qu'elle est enregistrée et que sa publication reste en attente. Le mécanisme
natif de réparation du module activités reste en place.

Les formes « Peux-tu m'ajouter Tailleur 100 ? », « Tu peux me mettre Tailleur
niveau 100 ? » et « Peux-tu me retirer mon métier Tailleur ? » sont prises en
charge. Les contrôles d'identité, de métier nommé et de niveau exact subsistent.
« Ne le fais pas », les citations, les conditions et les demandes visant autrui
ne donnent pas d'autorisation.

Les déclarations sont recherchées par identité de métier, avec résolution
canonique et pluriels simples, et filtrées sur le niveau. Les noms sans compte
Discord confirmé sont affichés séparément comme déclarations non vérifiées ;
les bots et départs confirmés restent exclus.
**Un métier déclaré ne prouve pas une disponibilité en jeu.** Les données
d'annuaire restent soumises à l'option d'administration déjà existante.

Une seule mutation reste permise par demande. Il n'y a pas d'outil « exécuter
n'importe quelle commande », de shell, de SQL libre ni de pouvoir de modération.
Les autres commandes du bot nécessitent leurs propres adaptateurs autorisés.

### Internet : recherche native OpenAI, séparée des actions

Le nouvel outil applicatif `rechercher_web` déclenche un appel Responses dédié
avec `web_search`. L'API OpenAI est la seule nouvelle destination fonctionnelle ;
aucune clé de moteur tiers n'est nécessaire. L'appel reçoit uniquement une
requête publique ciblée, pas le contexte Discord complet.

Deux périmètres sont proposés : général et Dofus Rétro. Pour Rétro, la recherche
filtre Moon-Bot, Xixou, le site Dofus Rétro, le support Ankama et JeuxOnline Dofus.
Les citations retournées sont vérifiées contre ce périmètre. Un domaine
autorisé n'est pas une garantie qu'un article concerne la bonne version :
le rédacteur doit encore distinguer Rétro, Dofus 2/3 et Touch.

Une recherche maximum par demande, un appel d'outil natif maximum dans cette
recherche, pas de parallélisme natif et aucune relance automatique.
Les annotations `url_citation` servent de provenance ; un texte sans source
exploitable ne devient pas une preuve Web. Les liens passent un validateur
d'affichage et ne donnent aucun droit de téléchargement arbitraire sur le
serveur du bot. L'ancien lecteur de pages conserve ses protections réseau.

Le rédacteur reçoit des extraits bornés, la date de consultation et les sources.
Il doit citer les faits Web. Un ajout déterministe de liens assure au moins
une citation visible lorsqu'il les omet ou que la réponse est tronquée.
Cela ne garantit pas une attribution parfaite de chaque phrase : vérifier les
premières réponses sur de vrais cas.

Les mentions Discord, longs identifiants numériques et secrets reconnaissables
sont refusés dans les requêtes ; les instructions interdisent aussi d'y placer
profils et pseudos. Ce filtrage n'est **pas** une garantie de détection universelle
des noms, données personnelles ou secrets déguisés.

### Items, équipements et monstres

Les données de jeu restent prioritaires dans les API existantes, sans remplacer
une identité exacte par un nom seulement ressemblant.

`proposer_stuff` sélectionne une base de huit emplacements depuis l'index Xixou
rapproché du wiki, avec deux anneaux distincts. La sélection est calculée en
Python : niveau plafond, interdictions de malus, priorités pondérées et
normalisées par emplacement. Une priorité secondaire n'est pas exigée sur
chacune des pièces. Les effets non interprétés sont exclus de cette sélection.

`analyser_stuff` résout les objets explicitement nommés, contrôle partiellement
les emplacements et niveaux, puis additionne leurs bornes de jets, malus compris.
Si des effets sont inconnus, la somme est explicitement partielle.
Si un objet ne possède pas de jets vérifiés, une somme globale n'est pas inventée.

**Ce n'est pas un solveur complet de panoplies.** Les bonus de panoplie, conditions
de classe, caractéristiques de base, parchottage, exos, interaction arme/bouclier,
prix HDV et disponibilité ne sont pas modélisés. Le système ne peut pas garantir
un objectif de PA/PM, une optimisation globale ou l'équipabilité finale.
La proposition doit servir de base vérifiable, pas d'ordre d'achat automatique.

Le routage reconnaît également « mobs », « boss » et résistances. Les fiches de
monstres restent celles du module existant. Le prompt sépare leurs statistiques
vérifiées des conseils tactiques et invite à une recherche complémentaire pour
les mécaniques absentes, au lieu d'inventer PV, sorts ou stratégies.

### Orchestration et réponses

Les sujets généraux stables peuvent recevoir une réponse sans appeler
artificiellement un outil du jeu. Les sujets opérationnels, les principales
intentions du jeu et les questions d'actualité sont orientés vers les outils.
Ce routage est heuristique ; il ne rend pas les réponses du modèle infaillibles.

La boucle accepte plusieurs séries de lectures, conserve les résultats exacts
déjà obtenus et s'arrête si un tour ne fait aucun progrès. Les limites configurables
sont au maximum six générations et douze outils. L'appel de recherche compte
aussi comme une génération. La rédaction conserve une réservation prioritaire.
Les identifiants d'appels dupliqués sont rejetés avant exécution.

Le prompt adapte la longueur au besoin. L'objectif visible peut monter à
1 400 tokens ; la sortie totale est bornée séparément. Les messages sont découpés
à 1 900 unités UTF-16, avec préservation des liens et fermeture/réouverture des
blocs de code. Les permissions sont revérifiées avant chaque envoi et les
mentions restent désactivées. Pour poursuivre une réponse multi-messages,
répondre à son **dernier** message.

## 3. Configuration à appliquer

Appliquer le patch ne remplace pas les variables déjà définies dans Render.
La recherche Web reste désactivée par défaut pour conserver le consentement
budgétaire et éviter une activation payante implicite.

Configuration de départ proposée, à reporter dans l'environnement :

```dotenv
EVO_ENABLED=1
EVO_MODEL=gpt-5.6-luna
EVO_PUBLIC_JOB_DATA=1
EVO_PUBLIC_MEMBER_DATA=0
EVO_MAX_OUTPUT_TOKENS=1000
EVO_MAX_RESPONSE_CHARS=6000
EVO_MAX_MODEL_CALLS=5
EVO_MAX_DEEP_MODEL_CALLS=6
EVO_MAX_TOOL_CALLS=10

# À activer seulement après validation du coût :
EVO_WEB_SEARCH_ENABLED=1
EVO_REQUEST_USD=0.03
```

Conserver `EVO_MONTHLY_USD` et `EVO_DAILY_USD` aux montants décidés par le Staff.
Les valeurs par défaut restent 2,00 USD/mois et 0,12 USD/jour. Ce sont de petites
enveloppes : elles ne suffisent pas à un usage intensif du Web.
`EVO_REQUEST_USD=0.03` est un maximum par demande, pas une facture systématique ;
une requête complexe peut être refusée avant son dépassement.

Une clé `OPENAI_API_KEY` et l'accès effectif au modèle/outils sont nécessaires.
`XIXOU_API_KEY` reste nécessaire pour les classements et propositions fondés sur
l'index enrichi. Les APIs indisponibles ne sont pas remplacées par des chiffres inventés.

Pour les activités : configurer `ORGANISATION_CHANNEL_ID` ou le nom du salon ;
vérifier le rôle validé (`ACTIVITE_VALIDATED_ROLE_ID` ou rôle historique), les
permissions de lecture/écriture, intégration de liens et pièces jointes du bot.
Vérifier aussi sa permission de gestion des rôles et sa position hiérarchique
pour les rôles d'équipe. L'IA n'octroie aucune de ces permissions.

## 4. Coûts et registre

D'après la documentation OpenAI consultée le 15 septembre 2026, la recherche
Web standard coûte 10 USD pour 1 000 appels, soit 0,01 USD par appel, plus les
tokens applicables. Le registre ajoute ce forfait et conserve sa marge
conservatrice de 15 %. Les tarifs doivent être revérifiés s'ils changent.

Le comptage préalable des tokens de la requête ne couvre pas les contenus que
le moteur va ensuite trouver. La réservation ajoute une provision de 16 000
tokens d'entrée pour ce contenu. **Ce n'est pas une limite dure imposée par
OpenAI**, ni une garantie absolue de facture maximale. Si l'usage réel dépasse
la réservation, il est enregistré et l'IA se bloque pour contrôle.

En cas d'appel soumis dont l'usage reste incertain, la réservation est conservée :
pas de retry automatique et pas de remise à zéro. Les garde-fous applicatifs
ne remplacent pas les contrôles de dépense du compte/projet fournisseur.

Le format de base du registre est conservé ; un champ optionnel `web_calls`
est ajouté aux réservations Web réglées. Les anciens registres se relisent.
**Revenir à l'ancien code après une recherche n'est pas transparent** : son
validateur ne connaît pas ce champ et peut refuser le registre. Ne jamais
effacer le registre ni restaurer une copie antérieure pour faire disparaître
des dépenses. Préférer désactiver `EVO_WEB_SEARCH_ENABLED` sans retirer le patch ;
un retour complet nécessite une migration qui conserve le comptage.

## 5. Installation

Depuis la racine du dépôt correspondant à l'archive, après sauvegarde des
modifications locales et dans une branche dédiée :

```bash
git switch -c amelioration-evo-ia
git apply --check evolution-evo-ai-upgrade.patch
git apply evolution-evo-ai-upgrade.patch
```

Adapter le chemin du patch si nécessaire. Ne pas employer `--reject` pour
forcer des modifications sur une autre révision : traiter d'abord les conflits.
Mettre à jour les variables d'environnement, redémarrer le bot, puis vérifier
`/evo-budget`. Ne lancer `initialiser:true` que si aucun registre n'existe encore.

Le patch ne contient aucune clé, donnée de production, nouvelle base de données,
dépendance tierce ou modification des fichiers de données métier.
Le nom du modèle est conservé.

## 6. Validation technique et limites de cette intervention

Les tests fournis utilisent des données synthétiques et des transports simulés.
De nouveaux scénarios couvrent les intentions françaises, permissions révoquées,
sauvegarde avant reçu, publication échouée, création concurrente identique,
frais Web persistés, citations, filtrage des requêtes, boucles de lecture,
limites configurées, sommes de stuff et découpage Unicode.

Dans cet environnement, `discord.py` et plusieurs dépendances d'intégration
ne sont pas installés ; leur téléchargement n'était pas possible. Les tests
d'agent, budget et logique métier ont été exécutés avec des doublures temporaires
d'import Discord et de présentation, **extérieures au patch**. Ces doublures
ne remplacent pas les fonctions de l'agent, du budget, des métiers ou du service
de création qui sont testées.

Résultat final de cette sélection : **511 tests réussis et 107 sous-tests**.
Cela ne constitue ni la suite complète du projet ni une validation réelle des
interactions, de l'enregistrement des commandes, de la facturation, du délai
fournisseur ou de la qualité de chaque future réponse du modèle.
Les chiffres historiques de `EVO_VALIDATION.md` proviennent de l'archive, pas
de cette exécution.

Les sources Python modifiées sont contrôlées syntaxiquement et le patch est
vérifié sur une copie intacte de la révision fournie. Pour la validation dans
l'environnement de développement normal, installer les dépendances du projet
et de test puis lancer :

```bash
python -m pytest -q tests_evo/test_upgrade_intents_messages.py \
  tests_evo/test_upgrade_search_builds.py tests_evo/test_upgrade_activity_actions.py
python -m pytest -q tests_evo
python -m pytest -q
```

Les dépendances de développement du projet et `pytest-asyncio` doivent être
présentes ; les tests d'intégration existants peuvent nécessiter leurs autres
bibliothèques habituelles. Aucun de ces tests ne doit recevoir de secret réel.

## 7. Recette sur un serveur de test

| Demande ou situation | Vérification attendue |
|---|---|
| « Y a-t-il des tailleurs 100 ? » | Annuaire activé, niveau ≥ 100 ; comptes identifiés et noms déclarés non vérifiés séparés, sans disponibilité inventée. |
| « Peux-tu m'ajouter Tailleur 100 ? » | Son propre profil est sauvegardé ; `/job` confirme le changement. |
| « Peux-tu me retirer mon métier Tailleur ? » | Seul le métier demandé du demandeur est supprimé. |
| « Ne m'ajoute pas Tailleur 100 » | Aucune mutation. |
| « Crée une sortie Crocabulia demain à 21h, 6 places pendant 2h » | Bonne date Paris, organisateur inscrit, annonce ou état d'attente explicite. |
| Même demande Discord rejouée | Pas de deuxième activité pour cette même clé ; un nouveau message est une nouvelle demande. |
| Publication Discord refusée après sauvegarde | Activité conservée ; ne pas demander de la recréer. |
| Rôle validé absent ou retiré en cours de demande | Pas de création. |
| « Propose un stuff feu niveau 120, priorité intelligence puis sagesse » | Objets vérifiés sous le plafond, limites et emplacements manquants affichés. |
| Comparaison de monstres et recherche d'une mécanique | Données API distinguées des conseils et sources Web ; version du jeu indiquée. |
| Recherche Web désactivée ou budget insuffisant | Limite explicitée, pas d'appel payant non autorisé. |
| Réponse longue avec URL et emoji | Tous les messages passent ; liens cliquables et mentions neutralisées. |

Commencer dans un seul salon avec un petit groupe. Comparer les mutations aux
commandes natives et les données aux fiches. Les API externes, leurs contenus,
leurs quotas et les autorisations effectives de votre compte restent à tester
dans votre déploiement.

## 8. Références API consultées

- Modèle : https://developers.openai.com/api/docs/models/gpt-5.6-luna
- Function calling : https://developers.openai.com/api/docs/guides/function-calling
- Recherche Web et citations : https://developers.openai.com/api/docs/guides/tools-web-search
- Tarification : https://developers.openai.com/api/docs/pricing

Choix d'architecture : schémas stricts et outils dédiés, non exécution de commandes
libres ; données structurées prioritaires ; recherche isolée avec provenance ;
budget commun préservé ; traitements irréversibles confirmés seulement après
sauvegarde. Les tests en production doivent confirmer ces comportements avec
les véritables APIs et permissions.
