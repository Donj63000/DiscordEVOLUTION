# DiscordEVOLUTION

Ce dépôt contient le bot Discord utilisé sur le serveur **EVOLUTION**. Il gère l'accueil des nouveaux joueurs, les tickets d'aide, les métiers en jeu et fournit aussi des commandes alimentées par Google Gemini.
Ce bot Discord est destiné aux guildes évoluant sur **Dofus Retro**. Il a été développé par **Coca** (sans lien avec la marque de sodas !), membre de la guilde Evolution sur le serveur Boune.

## Préparation du serveur Discord

Pour que toutes les fonctionnalités fonctionnent correctement, le serveur doit disposer des rôles et des salons suivants :

### Rôles requis
- **Staff** : donne accès aux commandes d'administration et permet de prendre en charge les tickets.
- **Membre validé d'Evolution** : rôle appliqué aux membres officiels, nécessaire pour certaines commandes (ex. `!activite`).
- **Invités/Invité** : rôle optionnel pour les visiteurs temporaires.
- **Vétéran** : utilisé par le module de promotion `up.py`.

### Salons textuels attendus
- `console` : salon privé où le bot sauvegarde/charge ses fichiers JSON.
- `ticket` : réception des tickets créés avec la commande `!ticket`.
- `annonces` : utilisé par `!annonce`, `!annoncestaff` et pour les sondages.
- `organisation` : pour la planification d'activités via `!activite`.
- `𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥` : canal public où le bot poste un message si les DM sont bloqués.
- `𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭` : annonces d'entrées ou de départs de la guilde.
- `𝐁𝐢𝐞𝐧𝐯𝐞𝐧𝐮𝐞` : messages d'arrivée et d'au revoir.
- `𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥-staff` : salon privé servant aux votes de promotion.
- `xplock-rondesasa-ronde` : salon dédié aux annonces de PL.

Si vous changez ces noms, pensez à mettre à jour les constantes correspondantes dans les fichiers Python du bot.

Le module d'accueil stocke aussi la liste des membres déjà salués dans
`welcome_data.json` pour éviter les doublons après un redémarrage.

### Permissions du bot

Pour fonctionner correctement, le bot doit disposer de plusieurs autorisations
sur le serveur :

- **Gérer les événements** afin de créer ou modifier les événements planifiés.
- **Gérer les rôles** pour attribuer le rôle temporaire *Participants événement*.
- **Envoyer des messages** et **Gérer les messages** dans les salons listés
  précédemment, notamment `#organisation` et `#console`.

Veillez également à ce que le bot puisse ouvrir des messages privés aux
utilisateurs et qu'il soit placé assez haut dans la hiérarchie des rôles pour
créer le rôle temporaire.

### Fichiers JSON de sauvegarde

Plusieurs modules utilisent des fichiers `*.json` pour persister leurs données :
`activities_data.json`, `jobs_data.json`, `players_data.json`,
`promotions_data.json`, `stats_data.json`, `warnings_data.json` et
`welcome_data.json`. Ces fichiers sont **créés automatiquement** lors de la
première exécution du bot. Ils sont enregistrés à côté des modules Python et ne
sont donc pas suivis par Git. À chaque sauvegarde, leur contenu est également
publié dans le salon `console` pour servir de sauvegarde distante.

Le module de statistiques conserve lui aussi son état dans ce salon : un message
épinglé contient le JSON complet et est mis à jour régulièrement. Le fichier
`stats_data.json` n'est donc qu'un cache local provisoire.

Des exemples anonymisés sont fournis dans le répertoire
[`examples`](examples/) pour illustrer le format attendu de chaque fichier.

## Installation

Clonez ce dépôt puis installez les dépendances :

```bash
pip install -r requirements.txt
```

Créez ensuite un fichier `.env` contenant au minimum votre `DISCORD_TOKEN`, la clé `GOOGLE_API_KEY` pour l'IA et une `FERNET_KEY` utilisée par `defender.py` pour chiffrer les URL. Cette clé est **obligatoire** et doit être fournie via la variable d'environnement `FERNET_KEY` (par exemple `FERNET_KEY=...`).
Vous pouvez générer cette clé avec :

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Lancez ensuite le bot avec :

```bash
python main.py
```
## Hébergement et persistance

Le bot fonctionne sur un micro serveur gratuit hébergé chez [render.com](https://render.com). Un petit serveur Flask défini dans `alive.py` est régulièrement pingé par **UptimeRobot** afin de le maintenir éveillé. Comme cet hébergement ne propose qu'un stockage éphémère, toutes les données enregistrées localement sont perdues à chaque redémarrage. Seules les informations sauvegardées sur Discord (par exemple dans le salon `#console`) sont conservées.


## Module Job

Le fichier `job.py` permet aux joueurs d'enregistrer leurs professions. Les dernières améliorations incluent :

- Migration des anciennes données basées sur les pseudos vers les identifiants Discord.
- Validation du niveau saisi (entre 1 et 200).
- Chargement de `jobs_data.json` depuis le salon `console`.
- Nouvelle commande `!job del <nom>` pour supprimer un métier.
- Gestion directe des noms contenant des espaces via `!job <nom du métier> <niveau>` (l'alias `add` reste valable).

## Planification d'événements

Le nouveau module `event_conversation.py` fournit la commande `!event` destinée au rôle **Staff**. Lorsque vous l'utilisez :

- Le bot ouvre une conversation privée pour recueillir les détails de l'événement. Envoyez plusieurs messages puis terminez par `terminé`.
- Le transcript est résumé par Gemini, puis un aperçu vous est présenté avec des boutons pour confirmer ou annuler.
- Après validation, un événement planifié Discord est créé et un message d'inscription est posté dans `#organisation` avec mention du rôle *Membre validé*.
- Les participants obtiennent un rôle temporaire qui est supprimé à la fin de l'événement.

Pour que cette commande fonctionne sans accroc, le serveur doit :
- comporter un salon `#organisation` où l'annonce sera publiée ;
- disposer d'un salon `#console` pour la persistance si aucune base PostgreSQL
  n'est configurée ;
- posséder un rôle `Staff` (seuls ses membres peuvent lancer `!event`) ;
- permettre aux membres de recevoir des messages privés du bot, sinon celui-ci
  les avertira dans `#𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥` pour qu'ils débloquent leurs DM.

Les événements créés et l'état des conversations sont sauvegardés via `EventStore`. Par défaut, les données sont publiées dans le salon `console`, mais si la variable d'environnement `DATABASE_URL` est définie, elles sont stockées dans PostgreSQL.

### Architecture interne

La fonctionnalité s'appuie sur plusieurs modules :

- `event_conversation.py` : le cog qui orchestre la discussion privée et crée l'événement planifié.
- `utils/storage.py` : un `EventStore` capable de persister les événements et conversations soit dans PostgreSQL, soit dans le salon `#console`.
- `models/event_data.py` : la structure de données commune utilisée pour sauvegarder chaque événement.

L'ancienne commande `!event` de `ia.py` a été supprimée au profit de ce flux guidé par Gemini. Un jeu de tests (`tests/test_event_data.py`) vérifie la validation du modèle `EventData`. Vous pouvez lancer tous les tests avec :

```bash
pytest -q
```

## Modération automatique

Le module `moderation.py` supprime les messages contenant des insultes graves,
de la discrimination ou des menaces. Les mots surveillés sont détectés même si
des espaces ou de la ponctuation sont insérés entre les lettres afin de
contourner la modération. L'auteur reçoit un avertissement en privé et
l'incident est consigné dans le salon `𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥-staff`. Après deux
avertissements, le membre est automatiquement sanctionné par un timeout d'une
heure. Les commandes `!warnings` et `!resetwarnings` (réservées au rôle
**Staff**) permettent de consulter ou remettre à zéro le compteur d'un membre.


## Score de puissance et ladder

Un score de puissance borné sur 1000 est calculé pour chaque profil en
combinant la caractéristique principale, la polyvalence, la vitalité, le
niveau, les PA/PM, la sagesse et l'initiative. La commande `!ladder` affiche le
classement de la guilde selon ce score. Exemples :

- `!ladder` : top 10 de la guilde ;
- `!ladder 15` : top 15 (max 20) ;
- `!ladder class iop` : filtre sur une classe ;
- `!ladder all` : export CSV complet en plus de l'embed.

### Variables d'environnement

```bash
# MODE de normalisation des stats dans le score
PROFILE_BAR_MODE=guild       # guild | local | fixed
PROFILE_BAR_FIXED_MAX=2000   # si fixed

# Bornes PA/PM
SCORE_PA_MIN=6
SCORE_PA_MAX=12
SCORE_PM_MIN=3
SCORE_PM_MAX=6

# Pondérations (JSON). Laisse vide pour les défauts.
PROFILE_SCORE_WEIGHTS='{"ELM_MAX":0.42,"ELM_OTH":0.13,"VIT":0.15,"LVL":0.10,"PA":0.07,"PM":0.05,"WIS":0.04,"INIT":0.04}'

# Taille des barres (affichage profil)
PROFILE_BAR_WIDTH=18
PROFILE_ANSI=0
PROFILE_COMPACT=0
```

### Contrôles qualité rapides

- `!ladder` : affiche le top 10 sans erreur même si des stats manquent ;
- `!ladder 15` : agrandit l'embed (limité à 20) ;
- `!ladder class iop` : filtre par classe ;
- `!ladder all` : ajoute un fichier `ladder.csv`.

### Message d'annonce

```
**Nouveau : Ladder de guilde `!ladder`** 🏆
Un score est maintenant calculé automatiquement à partir de vos profils (stat principale, vitalité, niveau, PA/PM, sagesse, initiative).
Tapez `!ladder` pour voir le **classement** de la guilde, `!ladder class iop` pour filtrer par classe, ou `!ladder all` pour l’export complet.
Mettez à jour votre profil avec `!profil set` / `!profil stats` — vos points montent (ou descendent 😈) en direct !
```

## Licence

Ce projet est distribué sous la licence MIT. Consultez le fichier [LICENSE](LICENSE) pour plus de détails.

