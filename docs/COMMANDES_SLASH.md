# Audit et mise à jour des commandes slash

## Périmètre et base exacte

Ce correctif s'applique à **DiscordEVOLUTION-main (2).zip**, **après**
`evolution-calendrier-affichage-direct.patch`. Il ne contient pas à nouveau ce patch.
Il conserve le calendrier sans formulaire, ses vues, filtres et inscriptions.

L'audit couvre les déclarations de commandes préfixées et natives, le catalogue slash,
le pont d'exécution, les validations, les contrôles d'accès, les erreurs et réponses,
les actions destructrices, l'aide, l'autocomplétion et la synchronisation distante.
Les modifications métier ciblent les problèmes observés dans ces parcours, sans
réécrire le stockage global du bot ou les intégrations externes.

Le catalogue complet hors IA représente **34 entrées racines et 77 actions finales**
si tous les modules optionnels non IA sont chargés. Il couvre 60 commandes préfixées.
En production, un module optionnel qui ne se charge pas n'est pas inventé dans l'aide.

## Décisions pour les utilisateurs

### Afficher, puis proposer les options

`/calendrier` et `/aide` n'ont aucun champ. L'aide est privée lors d'un appel slash,
classée par usage et paginée par sept commandes. Elle provient de l'arbre réellement
enregistré : pas de liens vers des fonctionnalités désactivées. Elle indique les
champs obligatoires ; les champs facultatifs peuvent rester vides.

Les créations conservent les champs réellement nécessaires : titre et date d'une
activité, question et choix d'un sondage, métier et niveau. Supprimer ces données du
menu pour obliger une conversation ou plusieurs formulaires augmenterait les étapes.

L'aide ne prétend pas remplacer les autorisations Discord : une catégorie Staff peut
être visible, mais les contrôles métier restent exécutés. L'administrateur peut régler
la visibilité des commandes dans les intégrations du serveur.

### IA hors ligne, sans perdre les anciens contenus

`ENABLE_AI_COMMANDS=0` est la valeur par défaut, même lorsque la variable est absente.
Une vieille clé encore présente dans l'environnement ne suffit pas à réactiver une API.

Les racines suivantes sont retirées des accès slash **et préfixés** lorsqu'indisponibles :
`ia`, `iahelp`, `iaend`, `bot`, `analyse`, `pl`, `event`, `iastaff`, `annonce`,
`annonce-model`, `annonce-config` et `organisation-model`.
`annonce-config` est également nettoyée si elle existe dans un ancien catalogue distant.

`/event` n'était pas autonome : son parcours de préparation appelait Gemini.
Le cog de suivi de ses événements reste chargé pour les données déjà publiées.
Le cog des annonces programmées reste lui aussi chargé : on conserve `/annonce-list`,
`/annonce-cancel` et les publications déjà programmées. Rien n'est supprimé du stockage.

`/organisation` et `/organisation-sync` restent disponibles. La génération d'annonce
utilise le modèle textuel intégré, sans appel OpenAI, lorsque l'IA est désactivée.

`/event-rapide` est retirée indépendamment de l'IA. Cette implémentation parallèle
stockait sorties et votes en mémoire seulement, et son expression d'ajout à un ensemble
retirait immédiatement le membre ajouté. Elle n'offrait pas la même persistance que les
parcours existants. Utiliser `/activite creer` ou `/sondage`. Le module ancien reste
dans le dépôt mais n'est plus chargé ; aucun contenu existant n'est migré automatiquement.

Pour réactiver ultérieurement : rétablir le fournisseur et son quota, configurer sa
clé, passer `ENABLE_AI_COMMANDS=1`, redémarrer puis synchroniser. Gemini utilise
`GOOGLE_API_KEY` ou `GEMINI_API_KEY` ; OpenAI utilise `OPENAI_API_KEY`.
IA Staff suit `IASTAFF_BACKEND` (Gemini par défaut, ou OpenAI). La présence d'une clé
ne certifie ni sa validité ni son quota : aucun appel payant de vérification n'est fait
au démarrage.

## Revue : problèmes et corrections

| Priorité | Constat dans la base | Correction |
| --- | --- | --- |
| Haute | Fonctions IA et `/event` encore exposés sans service utilisable. | Politique centrale, chargement conditionnel, blocage des menus anciens et retrait distant ciblé. |
| Haute | La première réponse différée publique ne devient pas privée en ajoutant seulement `ephemeral=True`. | Réponses sensibles différées en privé ; si une attente publique existe, clôture neutre avant le détail privé. |
| Haute | Une réponse explicitement privée pouvait retomber dans le salon après expiration du jeton. | Caractère privé irréversible ; MP après expiration pour le pont, jamais de repli public. Si les MP échouent, aucune publication dans le salon. |
| Haute | Erreurs métier dispatchées sans retour assuré, erreurs doublonnées, texte d'exception arbitraire affichable. | Gestion centrale, priorité au gestionnaire métier, un retour d'erreur contrôlé, trace technique côté logs. |
| Haute | Suppressions et réinitialisations accessibles en une seule validation de slash. | Confirmation privée à usage unique, auteur et guilde vérifiés, droits recontrôlés au clic. |
| Haute | Une clôture de sondage pouvait perdre son suivi après un refus Discord, ou agir depuis un autre serveur. | Vérification du serveur, verrou, suppression du suivi seulement après clôture réussie ou message déjà supprimé. |
| Moyenne | Publication d'un sondage avant sauvegarde, réactions susceptibles d'échouer, absence de reçu. | Sauvegarde avant réactions, suivi conservé, reçu avec lien et avertissement en cas de sauvegarde indisponible. |
| Moyenne | Résultats de sondage republiés lors d'une reprise ; réaction du bot soustraite même absente. | Mise à jour du message original, clôture sérialisée, soustraction uniquement si la réaction du bot existe. |
| Moyenne | Réinterprétation de champs slash par un parseur texte : date dans un titre, description multiligne, description effacée à la modification. | Valeurs structurées pour création/modification d'activité ; description omise conservée. |
| Moyenne | Tailles non bornées, sondage `temps=` injecté dans le titre, choix doublonnés, durée incohérente. | Bornes avant effet de bord, validation des directives, des choix et des durées. |
| Moyenne | Heures locales inexistantes au changement d'heure, dates passées, bornes d'équipement inversées. | Validation avant création et avant requête distante ; Paris explicite pour les activités. |
| Moyenne | `/perco` lisait le stockage avant d'acquitter l'interaction. | Vérification des droits de modification avant les lectures, acquittement avant I/O, cooldown. |
| Moyenne | Aide statique longue, mélange d'anciens accès et de commandes indisponibles. | Guide paginé construit depuis le catalogue et raccourcis sans formulaire. |
| Moyenne | Commandes préfixées masquées/désactivées susceptibles d'être exposées en slash. | Exclusion explicite, y compris les parents masqués/désactivés. |
| Moyenne | Anciennes commandes globales/guilde encore visibles après changement du catalogue. | Nettoyage nominatif après synchronisation réussie, sans effacer les autres commandes ou menus contextuels. |
| Moyenne | ID de guilde mal configuré susceptible de changer implicitement la portée de publication. | Refus de publier ; aucun basculement silencieux en global. |
| Faible | Autocomplétion d'activités non chronologique, données malformées et suggestions inutiles. | Lecture normalisée, tri par date, exclusion du passé et des sorties complètes pour rejoindre, plafond 25 choix. |

Les confirmations couvrent : `/membre supprimer`, `/profil supprimer`,
`/stats reinitialiser`, `/resetwarnings`, `/activite annuler`, `/job nettoyer`,
`/annonce-cancel` et `/accueil reset`. Elles expirent après 90 secondes.
`/clear console` conserve son choix explicite de confirmation déjà présent.
Une annulation ou expiration ne modifie rien ; après une tentative confirmée, le bouton
reste consommé même si l'envoi d'un résultat échoue. Il faut vérifier l'état avant de
relancer manuellement une action dont Discord a interrompu la réponse.

## Architecture et sécurité

- `utils/command_policy.py` : disponibilité des services et points d'entrée retirés.
- `utils/slash_sync.py` : publication et suppression ciblée des anciennes définitions.
- `utils/slash_errors.py` : erreurs destinées à l'utilisateur séparées des exceptions techniques.
- `utils/slash_confirm.py` : confirmations privées.
- `utils/slash_help.py` : aide dynamique.
- `utils/slash_catalog.py` : schéma et validation des champs.
- `utils/slash_support.py` : contexte slash/composant, réponses et exécution réelle.
- `slash_commands.py` : construction de l'arbre, autocomplétion et raccordement.

Le pont continue d'appeler `commands.Command.invoke`, avec contrôles globaux, contrôles
des groupes parents, conversions, cooldowns et hooks. Il n'appelle pas directement le
callback en contournant le framework. Les composants utilisent l'identité du membre
qui clique, jamais celle du bot ou de l'auteur du message.

Les commandes sont limitées au serveur et aux installations serveur. Les permissions
par défaut de Discord ne remplacent pas les contrôles métier. Le rôle Staff et les
permissions préexistantes sont conservés ; aucun droit `Administrateur` n'est imposé
arbitrairement à tout le catalogue pour tenter de masquer les commandes sensibles.

Les limites texte sont contrôlées en unités UTF-16 avant les écritures. Les identifiants
Discord sont des chaînes dans les options, validés en entier positif sur 64 bits.
Les liens d'import doivent désigner le serveur et le salon courants.
Les entrées que le parseur préfixé ne peut pas représenter fidèlement, notamment un
antislash terminal dans un jeton entre guillemets, sont refusées plutôt que transformées.

## Installation

Sauvegarder le code et vérifier les sauvegardes dans `#console`. Terminer d'abord
l'application du patch calendrier précédent. Placer le nouveau patch près de `main.py`.

```bash
git apply --check evolution-commandes-slash-audit.patch
git apply evolution-commandes-slash-audit.patch
python -m pytest
```

Ne pas forcer si `--check` échoue. Le correctif n'est pas basé sur le ZIP sans le dernier
patch calendrier et ne doit pas être appliqué deux fois.

Configuration de déploiement :

```env
ENABLE_AI_COMMANDS=0
SYNC_SLASH_COMMANDS=1
SLASH_CLEANUP_RETIRED=1
```

Conserver la valeur existante de `SYNC_SLASH_GUILD_ID` : ne pas changer simultanément de
périmètre. La variable peut rester vide si l'instance publie globalement. Une valeur
invalide bloque la publication. `SYNC_SLASH_COMMANDS=0` désactive aussi tout nettoyage.

Redémarrer l'instance existante. Vérifier les logs de chargement et de synchronisation,
puis fermer et rouvrir le sélecteur Discord. La synchronisation remplace le catalogue
du périmètre choisi. Le nettoyage complémentaire ne retire que les noms désactivés ;
les autres noms, les commandes contextuelles et les données du serveur sont préservés.
Un nettoyage de guilde échoué reste retentable à la reconnexion. Un nettoyage global
complémentaire échoué est signalé dans les logs et retenté au prochain démarrage/sync.

Pas de nouvelle dépendance, migration SQL ou suppression de fichier de données.
Le champ `guild_id` ajouté aux nouveaux sondages est rétrocompatible : pour les anciens,
le serveur est déduit du salon connu. Un ancien sondage dont le salon est inaccessible
n'est pas clôturé depuis un autre serveur.

### Retour arrière

```bash
git apply --reverse --check evolution-commandes-slash-audit.patch
git apply --reverse evolution-commandes-slash-audit.patch
```

Redémarrer et synchroniser après le retour arrière. Celui-ci restaure l'ancien code,
**pas** les actions déjà confirmées, les messages Discord ou l'état des données.
Le précédent patch calendrier reste appliqué. Les variables nouvelles peuvent être
retirées de l'environnement si l'on revient à l'ancienne version.

## Recette serveur avant production

1. Ouvrir `/aide` et `/calendrier` sans remplir de champ ; vérifier mobile et bureau,
   pagination, propriétaire des contrôles et expiration. Vérifier l'absence des IA,
   de `/event` et de `/event-rapide` dans le menu après synchronisation.
2. Depuis un membre normal puis un membre Staff : tenter une commande interdite,
   tester une suppression sur des données de recette, annuler, laisser expirer,
   puis confirmer. Retirer le rôle entre ouverture et clic : la mutation doit être refusée.
3. Créer et modifier une activité future, avec un titre contenant une autre date et
   une description sur plusieurs lignes. Modifier seulement la date : description conservée.
   Tester inscriptions, rôle d'activité et sauvegarde `#console`.
4. Publier un sondage, vérifier son reçu privé et son suivi, voter puis clôturer.
   Tester une erreur de permissions sur un salon de recette et vérifier que le suivi
   n'est pas perdu. Vérifier restauration après redémarrage.
5. Tester `/perco`, un cooldown, un refus de droits, un parcours en MP avec MP ouverts
   puis fermés, et `/organisation` sans clés d'API. Aucun détail sensible ne doit être
   publié dans le salon suite à un échec.
6. Vérifier les logs et les anciennes copies de commandes dans les autres périmètres.
   Ne pas lancer deux versions du bot en parallèle pour cette recette.

## Limites et points conservés

Le stockage de plusieurs modules reste conçu autour d'une instance de guilde et de
`#console`. Ce patch ne transforme pas tout le bot en application multi-tenant et
n'apporte pas une transaction commune entre disque, console, rôle et message Discord.
Une sauvegarde indisponible peut encore nécessiter une intervention opérateur.

Les données activités, événements conversationnels et organisation restent distinctes ;
le calendrier n'agrège pas automatiquement leurs stockages. Le nettoyage historique
des activités et l'expiration des anciennes vues au redémarrage sont inchangés.

Les heures ambiguës lors du retour à l'heure d'hiver utilisent le premier décalage
(fold=0), comme le stockage historique sans fuseau explicite ; les heures inexistantes
au printemps sont refusées. La suppression explicite du contenu d'une description
n'est pas ajoutée : un champ omis à la modification signifie conserver.

Les parcours hérités qui lisent les MP/messages nécessitent toujours les intents
appropriés. La refonte n'est pas une conversion de tous ces assistants en modales.
Les guides spécialisés préfixés restent disponibles ; `/aide` est désormais le point
d'entrée slash actualisé.

Aucune connexion réelle à un serveur Discord ni requête IA n'est effectuée pendant les
tests livrés. Les services externes, quotas, scopes effectifs, hiérarchie de rôles,
rendu des clients Discord et installations multi-serveurs demandent la recette ci-dessus.

## Validation automatisée

Les **96 nouveaux cas de test** sont regroupés dans :
`tests/test_slash_audit.py`, `tests/test_slash_confirm_sync.py` et
`tests/test_slash_workflows_audit.py`. Ils passent tous, y compris l'inventaire,
les clés et opt-ins, la synchronisation, les confirmations concurrentes, les permissions
retirées entre ouverture et clic, les réponses privées, les refus Discord sur les
sondages, le calcul des votes et les champs structurés des activités.

Comparaison dans le même environnement Python 3.13.5, discord.py 2.6.4 :

| Suite complète | Avant l'audit (calendrier déjà appliqué) | Après l'audit |
| --- | ---: | ---: |
| Réussis | 608 | 704 |
| Échecs | 4 | 4 |
| Erreurs de collecte | 2 | 2 |
| Ignorés | 1 | 1 |

Les quatre échecs et deux erreurs sont identiques : `validators` et `werkzeug`
sont absents de l'environnement de préparation. Ils sont déjà déclarés dans
`requirements.txt`. Aucune suppression de test ni ajout de skip ne masque ces blocages.
La suite complète n'est donc **pas entièrement validée**.

Les nouveaux tests de schéma isolent le validateur d'URL et le parseur de langage naturel ;
ils ne testent pas ces bibliothèques. Les tests unitaires du démarrage isolent seulement
le serveur de santé HTTP. Les tests utilisent la vraie bibliothèque discord.py pour
les arbres, paramètres, contextes, commandes, permissions et composants ; aucun appel
réseau Discord ou IA n'est effectué.

Commandes de contrôle :

```bash
python -m pytest tests/test_slash_audit.py tests/test_slash_confirm_sync.py tests/test_slash_workflows_audit.py
python -m pytest --continue-on-collection-errors
```

Les environnements Python 3.11/3.12 indiqués par le projet restent à vérifier dans
votre CI habituelle ; la préparation de ce correctif a utilisé Python 3.13.5.

## Inventaire complet hors IA

Les champs optionnels sont conservés lorsqu'ils apportent une action utile. Ce tableau
est issu du schéma produit par les déclarations du projet ; il ne suppose pas les
modules optionnels effectivement démarrés sur votre hébergement.

| Commande | Champs obligatoires | Champs facultatifs | Rôle de la commande |
| --- | --- | --- | --- |
| `/accueil aide` | Aucun | Aucun | Consulter les commandes de suivi de l’accueil des membres. |
| `/accueil relance` | `membre` | Aucun | Staff : relancer le parcours d’accueil d’un membre. |
| `/accueil reset` | `membre` | Aucun | Staff : réinitialiser le suivi d’accueil d’un membre. |
| `/accueil statut` | `membre` | Aucun | Staff : consulter l’avancement de l’accueil d’un membre. |
| `/activite aide` | Aucun | Aucun | Consulter le guide des activités. |
| `/activite annuler` | `identifiant` | Aucun | Annuler une activité que tu organises. |
| `/activite creer` | `titre`, `date` | `description` | Créer une activité et ouvrir les inscriptions. |
| `/activite info` | `identifiant` | Aucun | Consulter les détails d’une activité. |
| `/activite liste` | Aucun | Aucun | Consulter les prochaines activités. |
| `/activite modifier` | `identifiant`, `date` | `description` | Modifier la date et la description d’une activité. |
| `/activite quitter` | `identifiant` | Aucun | Te désinscrire d’une activité. |
| `/activite rejoindre` | `identifiant` | Aucun | T’inscrire à une activité. |
| `/aide` | Aucun | Aucun | Découvrir les commandes et les guides d’Evolution BOT. |
| `/annonce-cancel` | `identifiant` | Aucun | Staff : annuler une annonce programmée. |
| `/annonce-list` | Aucun | Aucun | Staff : consulter les annonces programmées. |
| `/avis` | Aucun | Aucun | Donner ton avis sur la guilde en message privé. |
| `/calendrier` | Aucun | Aucun | Afficher directement le calendrier des activités, sans rien remplir. |
| `/clear aide` | Aucun | Aucun | Staff : consulter les précautions du nettoyage de console. |
| `/clear console` | `confirmation` | Aucun | Staff : nettoyer la console en préservant les données du bot. |
| `/close_sondage` | `message` | Aucun | Clôturer un sondage que tu as créé, ou en tant que Staff. |
| `/defenderstatus` | Aucun | Aucun | Administration : consulter l’état des services de sécurité. |
| `/equipement` | `type` | `niveau`, `niveau_min`, `nom` | Chercher des équipements par type, tranche de niveaux et nom. |
| `/job aide` | Aucun | Aucun | Consulter le guide des métiers. |
| `/job ajouter` | `metier`, `niveau` | Aucun | Ajouter ou mettre à jour un métier. |
| `/job joueur` | `membre` | Aucun | Consulter les métiers d’un membre. |
| `/job liste` | Aucun | Aucun | Consulter tous les métiers et les artisans. |
| `/job mes-metiers` | Aucun | Aucun | Consulter tes métiers et leurs niveaux. |
| `/job metiers` | Aucun | Aucun | Consulter les noms de métiers disponibles. |
| `/job nettoyer` | Aucun | Aucun | Staff : retirer les joueurs absents du serveur. |
| `/job rechercher` | `metier` | Aucun | Trouver les artisans qui exercent un métier. |
| `/job supprimer` | `metier` | Aucun | Retirer un métier de ta fiche. |
| `/ladder` | Aucun | `options` | Consulter le classement des profils de la guilde. |
| `/membre ajouter-mule` | `personnage` | Aucun | Ajouter une mule à ta fiche de membre. |
| `/membre liste` | Aucun | Aucun | Consulter tous les joueurs et leurs personnages. |
| `/membre moi` | Aucun | Aucun | Consulter ton personnage principal et tes mules. |
| `/membre principal` | `personnage` | Aucun | Enregistrer ou modifier ton personnage principal. |
| `/membre retirer-mule` | `personnage` | Aucun | Retirer une mule de ta fiche de membre. |
| `/membre supprimer` | `personnage` | Aucun | Staff : supprimer la fiche d’un joueur et ses mules. |
| `/membre voir` | Aucun | `joueur` | Consulter une fiche de membre. |
| `/monstre` | `nom` | Aucun | Consulter les statistiques et résistances d'un monstre Rétro. |
| `/musique` | Aucun | `recherche` | Lancer une musique ou une playlist dans ton salon vocal. |
| `/objet` | `nom` | Aucun | Consulter les caractéristiques d'un objet Dofus Rétro. |
| `/organisation` | Aucun | Aucun | Staff : préparer une sortie avec le formulaire, sans IA obligatoire. |
| `/organisation-sync` | Aucun | Aucun | Staff : recharger les événements enregistrés dans la console. |
| `/perco` | Aucun | `etat` | Voir le statut des percepteurs ; le Staff peut le modifier. |
| `/ping` | Aucun | Aucun | Vérifier que le bot répond. |
| `/profil importer` | `message` | Aucun | Importer tes statistiques depuis un message du salon. |
| `/profil modifier` | Aucun | Aucun | Créer ou mettre à jour ton profil avec un guide en message privé. |
| `/profil rechercher` | `recherche` | Aucun | Rechercher un profil par nom de personnage. |
| `/profil score` | Aucun | `joueur` | Comprendre le calcul du score d’un profil. |
| `/profil supprimer` | Aucun | `personnage` | Supprimer ton profil, ou celui d’un autre joueur en tant que Staff. |
| `/profil voir` | Aucun | `joueur` | Consulter les caractéristiques et le score d’un joueur. |
| `/recette` | `objet` | `quantite` | Calculer les ressources nécessaires pour fabriquer un objet. |
| `/recrutement` | `personnage` | Aucun | Staff : enregistrer un nouveau joueur dans la guilde. |
| `/regles` | Aucun | Aucun | Lire le règlement de la guilde Evolution. |
| `/resetwarnings` | `membre` | Aucun | Staff : effacer les avertissements d’un membre. |
| `/rune aide` | Aucun | Aucun | Consulter le guide de calcul des runes. |
| `/rune calculer` | `jet`, `statistique` | Aucun | Calculer les probabilités de runes pour un jet. |
| `/scan` | `lien` | Aucun | Vérifier la sécurité d’un lien avec Defender. |
| `/sondage` | `titre`, `choix` | `duree` | Publier un sondage avec plusieurs choix. |
| `/staff` | Aucun | Aucun | Afficher les membres du Staff. |
| `/stats activer` | Aucun | Aucun | Staff : activer la collecte des statistiques. |
| `/stats classement` | Aucun | `options` | Consulter le classement des profils de la guilde. |
| `/stats desactiver` | Aucun | Aucun | Staff : désactiver la collecte des statistiques. |
| `/stats membres` | Aucun | Aucun | Consulter les statistiques d’activité des membres. |
| `/stats messages` | Aucun | Aucun | Consulter les statistiques des messages par salon. |
| `/stats modifications` | Aucun | Aucun | Consulter les statistiques des messages modifiés. |
| `/stats presence` | Aucun | Aucun | Consulter les statistiques de présence. |
| `/stats reactions` | Aucun | Aucun | Consulter les statistiques des réactions. |
| `/stats reinitialiser` | Aucun | Aucun | Staff : remettre à zéro les statistiques du serveur. |
| `/stats suppressions` | Aucun | Aucun | Consulter les statistiques des messages supprimés. |
| `/stats tout` | Aucun | Aucun | Consulter toutes les statistiques du serveur. |
| `/stats vocal` | Aucun | Aucun | Consulter les statistiques des salons vocaux. |
| `/stats voir` | Aucun | Aucun | Consulter le tableau de bord des statistiques du serveur. |
| `/ticket` | Aucun | Aucun | Contacter le Staff : ouvrir un échange privé guidé. |
| `/veteran` | Aucun | Aucun | Staff : consulter les candidats Vétéran et les promouvoir par bouton. |
| `/warnings` | `membre` | Aucun | Staff : consulter les avertissements d’un membre. |

## Références techniques

- Discord, réception et réponses aux interactions : https://docs.discord.com/developers/interactions/receiving-and-responding
- Discord, commandes d'application : https://docs.discord.com/developers/interactions/application-commands
- discord.py, arbre, synchronisation, permissions et réponses : https://discordpy.readthedocs.io/en/stable/interactions/api.html
- discord.py, cycle des commandes préfixées : https://discordpy.readthedocs.io/en/stable/ext/commands/api.html

La réponse initiale d'une interaction doit être acquittée rapidement. Le caractère
éphémère fixé au différé ne peut pas être changé en réutilisant simplement le premier
followup. Les commandes de guilde et globales forment des périmètres distincts.
