# Activités de guilde : formulaire, fiches persistantes et calendrier commun

## Base et périmètre

Cette refonte est préparée pour `DiscordEVOLUTION-main (3).zip`, avec les améliorations
du calendrier et du catalogue slash déjà présentes dans cette archive. Le patch est
autonome par rapport à cette base : ne pas réappliquer les anciens correctifs.

Le module activités ne dépend d'aucune API d'IA, ne crée pas de nouvelle base de données
et n'ajoute aucune dépendance à `requirements.txt`. Le rendu du calendrier est conservé.
Les annonces rédigées par les membres restent libres : la fiche sert surtout à tenir
les inscriptions à jour et à rendre les sorties visibles dans le calendrier.

## Audit du code livré

| Constat dans l'archive | Décision appliquée |
| --- | --- |
| Le groupe `/activite` existait déjà ; créer plusieurs autres commandes aurait ajouté des doublons. | Conserver ses huit sous-commandes et placer les actions secondaires dans les boutons. |
| La création reposait sur des champs slash ou une ligne de texte à décomposer. | Formulaire court, aperçu privé, correction sans ressaisie complète et validation explicite. |
| Les inscriptions par réactions dépendaient de correspondances gardées en mémoire. | Boutons à identifiants stables, fiches enregistrées dans le snapshot et vues restaurées au redémarrage. |
| Les parcours commandes et réactions répétaient des règles d'inscription. | Une seule mutation de la liste, utilisée par les slash, les boutons, le calendrier et les anciens préfixes. |
| Une capacité fixe de huit ne couvrait pas toutes les sorties. | Capacité de 1 à 100, huit par défaut, organisateur compris ; file d'attente de 100 maximum. |
| La création imposait un rôle Discord et des mentions larges. | Aucun nouveau rôle d'activité, aucune mention générale automatique. |
| Les événements étaient supprimés dès leur démarrage. | Conserver les fiches et les listes dans l'historique ; bloquer les inscriptions après le début. |
| Une inscription pouvait être exposée avant la confirmation de sauvegarde. | Sauvegarder un candidat sous verrou puis seulement rendre son état visible. |
| Une restauration défaillante risquait de ressembler à une base vide. | Refuser les écritures si l'historique est inaccessible ou si le dernier snapshot est illisible. |
| Le stockage générique pouvait supprimer un ancien snapshot après un échec d'édition. | Stockage spécialisé non destructif pour les activités uniquement. |

Le registre devient la référence commune. La fiche publique et les calendriers sont
des vues de ce registre, pas des listes concurrentes.

## Parcours d'un membre

### Proposer une sortie

`/activite creer` n'a aucun argument dans le menu slash. Son envoi ouvre un formulaire
contenant cinq champs : titre, date et heure, lieu, places et précisions.
Seuls le titre et la date sont obligatoires ; les places sont préremplies à huit.

Exemples de date : `demain 21h`, `vendredi 20h30`, `18/09/2026 21:00`.
L'heure saisie est celle de Paris. Les dates passées, inexistantes, les heures impossibles
et les heures ambiguës des changements d'heure sont refusées avec un message explicatif.
Un jour et mois sans année désignent l'année courante, jamais une année devinée.

Le formulaire produit un aperçu privé. **Créer la sortie** enregistre la sortie,
inscrit son organisateur, puis publie la fiche dans le salon d'organisation.
**Corriger** reprend les valeurs déjà saisies. **Abandonner** n'enregistre rien.
Après un refus d'enregistrement, un brouillon permet de réessayer pendant la session.

La date relative est convertie en date explicite dès l'aperçu : une attente avant
confirmation ne déplace pas silencieusement une sortie de « demain » d'un jour.
Les brouillons privés expirent après dix minutes et ne survivent pas à un redémarrage.
Les activités confirmées, elles, sont persistantes.

### Partir d'une annonce existante

Dans le salon d'organisation, utiliser le menu du message, **Applications → Créer une activité**.
Le titre et les précisions sont préremplis à partir du texte ; renseigner la date,
vérifier les autres champs et confirmer. La fiche contient un lien vers l'annonce.

Ce menu fonctionne sur ses propres annonces textuelles. Le Staff peut aussi convertir
une annonce d'un autre membre ; le créateur de la fiche reste la personne qui confirme.
Il ne copie pas de message provenant d'un autre salon et ne déplace ni ne supprime
l'annonce d'origine. Il ne devine pas de date à partir du texte. Les annonces constituées
uniquement d'une image ou d'un embed passent par `/activite creer`.

### Rejoindre, attendre, quitter

**Rejoindre / Attente** inscrit directement quand une place est libre ; sinon le membre
rejoint la file d'attente. Les doublons sont refusés. **Quitter** retire de la bonne liste.
Une place libérée est attribuée dans l'ordre d'arrivée ; le membre promu reçoit une
notification ciblée. Une augmentation de capacité promeut aussi les premiers membres
en attente. La capacité ne peut pas descendre sous le nombre d'inscrits.

Les clics concurrents sont sérialisés à l'intérieur du processus du bot. Le départ
d'un membre du serveur nettoie ses inscriptions futures lorsque l'événement Discord
est reçu. Les listes des anciennes sorties ne sont pas réécrites à cette occasion.
Les rôles validés sont vérifiés à l'inscription ; une désinscription reste possible
après perte du rôle. Les membres en cache qui ne sont plus validés sont écartés de
l'attente lors des mutations concernées.

**Participants** fournit la liste complète au format texte, avec noms connus et
identifiants Discord. Les longues listes sont abrégées dans les embeds, pas dans le fichier.

### Retrouver et gérer ses sorties

`/activite liste` ouvre un panneau privé : à venir, mes inscriptions et attentes,
mes sorties organisées, historique et sorties annulées. La liste est paginée.
Un menu ouvre la fiche choisie ; un bouton ouvre directement une nouvelle création.

**Gérer** est réservé à l'organisateur et au Staff. Il propose la modification dans
un formulaire prérempli, l'annulation avec confirmation et la réparation de la fiche.
Deux formulaires de modification ouverts sur la même révision ne peuvent pas
s'écraser mutuellement. Les inscriptions sont conservées lors d'une modification.
L'annulation conserve elle aussi les listes ; elle n'efface pas les données.

Une ancienne fiche dont l'horaire est déjà passé peut être reprogrammée explicitement
par son organisateur ou le Staff. Créer une nouvelle sortie est préférable quand il
s'agit d'une nouvelle occurrence et que l'on souhaite garder l'ancienne dans l'historique.

### Commandes conservées

| Commande | Résultat |
| --- | --- |
| `/activite creer` | Ouvrir le formulaire sans argument slash. |
| `/activite liste` | Ouvrir le tableau personnel et ses filtres. |
| `/activite info identifiant` | Consulter une sortie, y compris passée ou annulée. |
| `/activite rejoindre identifiant` | S'inscrire ou rejoindre l'attente. |
| `/activite quitter identifiant` | Quitter les inscrits ou l'attente. |
| `/activite modifier identifiant` | Ouvrir le formulaire prérempli. |
| `/activite annuler identifiant` | Demander confirmation puis annuler. |
| `/activite aide` | Consulter le guide intégré. |
| `/calendrier` | Afficher directement le calendrier déjà existant. |

L'autocomplétion des inscriptions évite les doublons ; celle des modifications et
annulations privilégie les sorties que l'utilisateur est autorisé à gérer.
Les anciennes commandes `!activite creer Titre JJ/MM/AAAA HH:MM Description`,
`!activite modifier ID JJ/MM/AAAA HH:MM Description`, `join`, `leave`, `liste`,
`info` et `annuler` restent utilisables. La confirmation supplémentaire est proposée
par l'interface slash et le panneau Gérer, pas par la commande préfixée d'annulation.

`/organisation` reste l'outil d'aide à la rédaction d'une annonce ; il n'est pas un
deuxième registre d'inscriptions. Les décisions existantes de désactivation des
commandes IA et de `/event-rapide` sont conservées. Les anciens événements d'autres
modules ne sont pas fusionnés automatiquement.

## Calendrier, rappels et mentions

Le calendrier lit les mêmes horaires, lieux, capacités, inscrits et membres en attente.
Son filtre **Mes inscriptions** inclut l'attente ; ses fiches donnent accès à la fiche
publique et permettent de rejoindre une liste d'attente pleine côté inscrits.
Le rendu mois/semaine, les filtres, le cache d'images et l'affichage direct sont conservés.
Les calendriers déjà ouverts relisent les données lors d'une interaction ou d'Actualiser ;
ils ne sont pas tous réédités en arrière-plan à chaque inscription.

Les rappels existants à 24 heures et à une heure restent automatiques. Une activité
créée tard ne déclenche pas tous les rappels manqués ; le rappel pertinent est envoyé.
Un changement d'horaire réinitialise les rappels. Les rappels mentionnent seulement
les inscrits, pas les membres en attente, et sont répartis en messages bornés.
Les promotions, modifications et annulations notifient les membres concernés.
Les créations et mises à jour des fiches ne mentionnent ni rôle, ni `@everyone`, ni `@here`.

« Début passé » signifie que l'heure de début est passée. Le bot ne connaît pas l'heure
de fin et ne prétend pas constater la fin effective d'une sortie.

## Sauvegarde, migration et reprise

Le message du bot marqué `===BOTACTIVITES===` dans `#console` reste la source de vérité.
Le schéma passe à la version 2, en conservant `date_str` et en ajoutant un instant UTC
`starts_at`, le serveur, la capacité, le lieu, l'attente, les révisions et les liens de fiche.
Les dates anciennes sans fuseau sont interprétées comme des heures de Paris.

La migration conserve les identifiants, les inscrits, les champs supplémentaires et
les activités annulées encore présentes. Les enregistrements malformés sont placés
dans `quarantine`, pas jetés. Elle ne peut pas ressusciter les sorties déjà supprimées
par l'ancien bot. Une version future du schéma ou un objet racine invalide bloque
la restauration au lieu d'être écrasé.

La restauration consulte les épingles et l'historique récent, puis l'historique plus
ancien si aucun snapshot n'est trouvé. Le cache local historique `activities_data.json`
n'est importé qu'en l'absence vérifiée de snapshot distant ; il ne remplace jamais
un snapshot distant illisible. Après migration, ce fichier reste un cache jetable.

Les écritures modifient le même message épinglé ; les gros snapshots passent en
pièce jointe JSON. Les contenus contenant des blocs de code ne cassent pas le JSON.
Un refus d'édition ne supprime pas le dernier message sauvegardé.
Si la sauvegarde échoue, le bot ne confirme pas la modification en mémoire.

La sauvegarde et la publication d'une fiche sont deux opérations Discord distinctes.
Si la fiche ne peut pas être publiée, la sortie reste enregistrée avec un état
de publication en attente ; le membre reçoit une explication et peut utiliser Réparer.
Une erreur d'actualisation après une inscription n'annule pas l'inscription confirmée.

Les boutons des fiches enregistrées sont réinstallés au démarrage. Pour les anciennes
fiches sans identifiants persistés, le bot cherche les messages reconnaissables parmi
les 200 derniers messages du salon d'organisation. Les plus anciennes restent accessibles
par `/activite info`, puis Gérer → Réparer / publier la fiche. Cette réparation peut créer
une nouvelle fiche ; les anciennes réactions ne constituent plus un mécanisme d'inscription.

## Configuration et permissions

Les réglages de salons existants restent prioritaires. Définir des identifiants est
recommandé pour éviter les ambiguïtés de noms :

```dotenv
CHANNEL_CONSOLE_ID=
CHANNEL_CONSOLE=console
ORGANISATION_CHANNEL_ID=
ORGANISATION_CHANNEL_NAME=organisation
ACTIVITE_VALIDATED_ROLE_ID=
ACTIVITE_HISTORY_LIMIT=200
SYNC_SLASH_COMMANDS=1
ENABLE_MEMBERS_INTENT=1
```

Sans `ACTIVITE_VALIDATED_ROLE_ID`, le nom historique `Membre validé d'Evolution`
reste utilisé. Le Staff est reconnu par le rôle `Staff`, Administrateur ou Gérer
le serveur. Le salon d'organisation doit être lisible par les membres concernés.

Dans `#console`, le bot doit pouvoir voir le salon, lire l'historique, envoyer des messages,
joindre des fichiers et épingler son snapshot. Dans le salon d'organisation, il doit
pouvoir voir, lire l'historique, envoyer des messages et intégrer des liens.
Les fichiers de participants demandent aussi la permission de joindre des fichiers
dans le salon où la commande est utilisée.

Les nouvelles activités n'exigent plus Gérer les rôles. Cette permission et une
hiérarchie compatible restent nécessaires pour attribuer ou supprimer les anciens
rôles d'activité encore enregistrés. Le refus de nettoyer un ancien rôle n'empêche
pas de conserver une activité passée.

Activer aussi l'intention privilégiée des membres dans le portail Discord pour
recevoir les départs et disposer du cache attendu. La synchronisation des commandes
est nécessaire pour retirer les anciens champs de création/modification et ajouter
le menu contextuel. Ne pas changer de périmètre global/guilde pour ce seul correctif :
conserver `SYNC_SLASH_GUILD_ID` tel qu'il est configuré.

## Installation et retour arrière

Arrêter l'instance du bot. Copier le dernier snapshot d'activités de `#console` et le
cache local existant dans un emplacement sûr. Conserver une copie du code de départ.
Depuis la racine du projet extrait de l'archive fournie :

```bash
git apply --check /chemin/vers/evolution-activites-refonte.patch
git apply /chemin/vers/evolution-activites-refonte.patch
python -m pytest -q
```

La vérification `git apply --check` ne modifie aucun fichier. En cas de conflit,
ne pas appliquer avec `--reject` ni écraser des modifications locales : la base n'est
pas exactement celle attendue. Le patch ne modifie ni le jeton, ni le contenu de `.env`,
ni vos snapshots de production. Les nouvelles variables de `.env.example` ne sont
pas injectées automatiquement dans votre configuration.

Redémarrer ensuite avec le mécanisme habituel, sur **une seule instance du bot**,
et vérifier les journaux de migration et de synchronisation. Exécuter la recette
ci-dessous dans un serveur de test avant de généraliser.

Pour revenir en arrière, arrêter le bot, conserver également le snapshot v2 courant,
réappliquer l'ancien code et restaurer la sauvegarde antérieure choisie consciemment.
Un simple `git apply -R` remet le code, pas les données. Ne pas restaurer un ancien
snapshot par-dessus les nouvelles inscriptions sans accepter explicitement leur perte.

## Recette Discord à effectuer

1. Avec un membre validé, ouvrir `/activite creer`, saisir un titre et `demain 21h`,
   laisser les autres champs par défaut, vérifier l'aperçu puis confirmer.
   Vérifier l'inscription automatique, la fiche sans ping et la présence au calendrier.
2. Régler deux places, organisateur compris. Faire rejoindre deux autres comptes :
   un doit être inscrit, l'autre en attente. Libérer une place et vérifier la promotion.
3. Modifier le lieu et l'heure ; vérifier les fiches, le calendrier actualisé, les
   inscriptions conservées et la notification limitée aux intéressés.
4. Tester une date invalide, un membre non validé et une modification par un non-organisateur.
   Vérifier les refus et l'absence d'écriture intempestive.
5. Redémarrer le bot, puis cliquer sur les boutons déjà publiés. Supprimer une fiche
   et utiliser Réparer depuis une nouvelle consultation `/activite info`.
6. Retirer temporairement les permissions d'écriture de `#console`, tenter une inscription,
   vérifier le refus puis rétablir les permissions et réessayer.
7. Annuler avec confirmation ; vérifier l'historique et les listes conservées.
   Tester aussi le menu de création depuis une annonce et son lien d'origine.

## Limites explicites

Le verrou protège un processus, pas plusieurs réplicas concurrents du bot.
Discord ne fournit pas ici de transaction atomique regroupant snapshot, fiche et notifications :
une coupure au mauvais instant peut laisser une publication à réparer ou répéter un rappel,
notamment après un redémarrage. Les références persistées, les clés de création, la recherche
de fiches récentes et les contrôles de révision réduisent ces risques, sans promettre
un traitement exactement une fois sur plusieurs processus.

Les notifications de changement ne sont pas une file de livraison garantie.
Un membre quittant le serveur pendant l'arrêt du bot ne génère pas forcément un événement
de départ à la reprise. Le patch ne lance pas de purge massive fondée sur un cache incomplet.
Les historiques sont conservés sans durée automatique de rétention ; surveiller la taille
du snapshot et traiter une éventuelle quarantaine avec le Staff.
La limite de fichier Discord du serveur est vérifiée avant sauvegarde.

Les tests locaux utilisent la vraie bibliothèque Discord avec des frontières réseau simulées.
Ils ne certifient pas les permissions, la propagation du catalogue ni le comportement
visuel dans votre serveur réel. Voir `ACTIVITES-VALIDATION.md` pour les résultats exacts.

## Références techniques

Documentation officielle consultée pour les modalités d'interaction et les vues persistantes :
- https://discordpy.readthedocs.io/en/stable/interactions/api.html
- https://docs.discord.com/developers/interactions/receiving-and-responding
