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
- `annonces` : utilisé par `!annonce` et pour les sondages.
- `organisation` : pour la planification d'activités via `!activite`.
- `𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥` : canal public où le bot poste un message si les DM sont bloqués.
- `𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭` : annonces d'entrées ou de départs de la guilde.
- `𝐁𝐢𝐞𝐧𝐯𝐞𝐧𝐮𝐞` : messages d'arrivée et d'au revoir.
- `𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥-staff` : salon privé servant aux votes de promotion.
- `xplock-rondesasa-ronde` : salon dédié aux annonces de PL.

Si vous changez ces noms, pensez à mettre à jour les constantes correspondantes dans les fichiers Python du bot.

Le module d'accueil stocke aussi la liste des membres déjà salués dans
`welcome_data.json` pour éviter les doublons après un redémarrage.

## Installation

Clonez ce dépôt puis installez les dépendances :

```bash
pip install -r requirements.txt
```

Créez ensuite un fichier `.env` contenant au minimum votre `DISCORD_TOKEN` et la clé `GEMINI_API_KEY` pour l'IA. Lancez le bot avec :

```bash
python main.py
```

## Module Job

Le fichier `job.py` permet aux joueurs d'enregistrer leurs professions. Les dernières améliorations incluent :

- Migration des anciennes données basées sur les pseudos vers les identifiants Discord.
- Validation du niveau saisi (entre 1 et 200).
- Chargement de `jobs_data.json` depuis le salon `console`.
- Nouvelle commande `!job del <nom>` pour supprimer un métier.
- Gestion directe des noms contenant des espaces via `!job <nom du métier> <niveau>` (l'alias `add` reste valable).

