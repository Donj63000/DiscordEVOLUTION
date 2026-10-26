# Sécurité et reprise des événements

Ce guide accompagne les correctifs issus de l'audit du 26 octobre 2026. Le contrat
de changement est dans [EXECPLAN.md](../EXECPLAN.md) et les résultats exécutés dans
le [journal d'implémentation](../SECURITY_IMPLEMENTATION_LOG.md).

## Configuration du serveur

`BOT_GUILD_ID` désigne obligatoirement le serveur Evolution. Une valeur absente ou
invalide bloque la configuration. `EVO_GUILD_ID`, s'il est renseigné, doit être
identique. Les autres guildes ne sont pas des sources de données ni des destinations
d'actions : commandes, boutons, formulaires, listeners et accès aux sauvegardes
appliquent cette politique. Le bot ne quitte pas automatiquement les autres guildes.

Une connexion initiale avec un cache Discord vide ne permet aucun repli vers une
autre guilde. Les opérations qui attendent le serveur configuré sont différées
jusqu'à la disponibilité du cache.

## Événements et sauvegardes

Configurer `EVENT_PROVENANCE_KEY` dans le gestionnaire de secrets de l'hébergement :
32 octets aléatoires encodés en **64 caractères hexadécimaux**. Ne pas publier cette
clé et conserver sa valeur entre deux déploiements. Une clé absente ou invalide
rend les actions d'événements indisponibles ; les modules indépendants continuent.

Chaque nouvel événement possède ses propres ressources. Un rôle ou salon de même
nom n'est pas adopté. Une preuve HMAC lie les identifiants des ressources, la guilde
et l'échéance de nettoyage. Les changements de participants ne rendent pas la preuve
invalide ; une modification d'identifiant ou d'échéance exige une nouvelle preuve.
Les contrôles de permissions et de provenance sont refaits avant chaque mutation.

Avant la première reprise, conserver une sauvegarde privée des snapshots console et,
si utilisé, du stockage PostgreSQL. Les données anciennes ne sont ni attribuées ni
supprimées automatiquement par la migration.

Les anciens événements restent consultables, avec leurs actions suspendues. Le
Staff peut utiliser `/event-securite revalider evenement:<id>` sans réactiver les IA.
La commande présente les ressources exactes. La confirmation n'est valable que pour
son auteur et expire au bout de cinq minutes ; les droits sont revérifiés au clic.
Le demandeur doit disposer des droits actuels de gestion des rôles et du salon
concerné. Un rôle privilégié ou une ressource étrangère ne peut pas être adopté.

Pour un événement déjà terminé, **revalider ne supprime rien**. Le nettoyage fait
l'objet d'une confirmation distincte indiquant les ressources qui seront supprimées.
Sans confirmation explicite du nettoyage, un redémarrage ne doit pas le déclencher.

Changer ou perdre la clé invalide les anciennes preuves : rétablir la clé conservée
ou revalider les événements. Ne pas remplacer les preuves automatiquement sur la
seule base des noms des ressources.

## Outils IA Staff

Les anciennes IA restent soumises à leurs options d'activation ; l'installation du
correctif ne les active pas. Toutes les mutations proposées par l'IA exigent une
confirmation humaine, même si l'ancienne option `IASTAFF_CONFIRM_DESTRUCTIVE` vaut
`0`. Les lectures explicitement reconnues peuvent s'exécuter sans confirmation.

Une proposition indique l'action, les cibles et les valeurs. Son nonce est lié à
l'auteur, la guilde, le salon et les identifiants résolus ; il expire après au plus
300 secondes et ne peut servir qu'une fois. `IASTAFF_CONFIRM_TTL` peut raccourcir
cette durée. Les droits sont contrôlés de nouveau au moment de l'exécution.
Une réponse du modèle ne constitue jamais une confirmation humaine.

`IASTAFF_ALLOWED_ROLE_IDS` contient les IDs des seuls rôles ordinaires gérables par
l'IA. La liste vide refuse les opérations de rôle. L'inscription dans la liste ne
permet pas de contourner les permissions, les hiérarchies, les rôles gérés ou
l'interdiction des rôles privilégiés.

## Conversations privées et fiches joueurs

Un utilisateur ne peut ouvrir qu'un parcours de saisie structuré à la fois. Ticket,
avis, profil et création d'événement partagent cette réservation. L'accueil et la
conversation IA sont suspendus lorsqu'un tel parcours prend la main, puis se
reprennent explicitement. Le message qui termine le parcours ne doit jamais être
réutilisé comme message d'accueil, d'avis ou de conversation IA.

La réservation de saisie est libérée à la fin de la collecte, même si un ticket
reste ouvert auprès du Staff. Timeout, annulation, exception et déchargement doivent
également la libérer. Le contenu des MP ne doit pas être écrit dans les journaux.

Une fiche joueur appartient à son ID Discord. Un pseudo, surnom ou nom de personnage
identique ne transfère plus de fiche ni de mules. Les recrues provisoires restent
conservées sans être revendiquées automatiquement. Quitter une autre guilde ne
supprime pas la fiche Evolution.

## Defender et serveur de santé

Les sondes de liens utilisent uniquement HTTP/HTTPS sur les ports 80/443, sans
identifiants dans l'URL. Le resolver du connecteur filtre les adresses effectivement
utilisées, y compris les redirections et les IP littérales. L'ancienne option
`DEFENDER_BLOCK_PRIVATE_IPS=0` ne désactive pas cette protection réseau. Les échecs
de résolution ou les destinations refusées ne valent pas validation du lien.

Le serveur de santé conserve `/` et `/ready`. Il n'a pas besoin de corps compressés ;
la décompression automatique est désactivée et les encodages non pris en charge
sont refusés. Le choix du mode HTTP et les permissions réelles de l'hébergement
restent à vérifier lors du déploiement.

## Vérifications reproductibles

La CI installe les dépendances avec hashes sous Python 3.11 et 3.12, vérifie leur
cohérence et exécute la suite de sécurité séparément avec les vrais SDK. Elle lance
ensuite les tests existants et la validation Monte-Carlo. Un PostgreSQL jetable sert
aux tests d'aller-retour : ne jamais y substituer la base de production.

Les audits PyPI et OSV portent sur tous les paquets du verrou, dans un environnement
d'outils distinct. Une vulnérabilité, une dépendance ignorée ou une erreur de collecte
fait échouer le contrôle. Les JSON d'audit et les inventaires sont conservés en CI.

Les versions sources sont dans `requirements.txt`. Les verrous Linux sont générés
avec `pip-tools==7.6.2`, chacun sous son interpréteur, depuis un environnement
d'outils séparé. Utiliser `python -P -m piptools compile` pour éviter que le module
local `build.py` masque le paquet de construction homonyme. La CI et Render
installent les verrous committés ; ils ne les régénèrent pas au démarrage.

## Données publiques et retour à un état sûr

L'archive `CODEX.ZIP` et l'export `temp_players_data.json` sont retirés du dépôt
courant. Les exemples doivent utiliser des données synthétiques. Les anciennes
copies Git peuvent toujours contenir les fichiers : leur purge demande un chantier
séparé de réécriture d'historique.

En cas d'incident, désactiver les outils IA et le développement de liens, conserver
les sauvegardes et laisser les actions d'événements suspendues. Revenir à une
ancienne version vulnérable n'est pas un mécanisme de récupération sûr. Aucun
déploiement, retrait de données de production ou changement de secret n'est effectué
par les tests de ce dépôt.
