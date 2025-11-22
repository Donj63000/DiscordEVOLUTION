# DiscordEVOLUTION

Bot Discord complet utilisé sur le serveur **EVOLUTION** (guilde Dofus Retro). Il automatise l'accueil, les annonces, les tickets, la planification d'événements, la gestion des métiers et propose des assistants IA (Gemini, OpenAI). Le projet a été développé par **Coca**, membre de la guilde Evolution sur Boune.

## Sommaire

- [Fonctionnalités principales](#fonctionnalités-principales)
- [Architecture des modules](#architecture-des-modules)
- [Préparation du serveur Discord](#préparation-du-serveur-discord)
- [Installation](#installation)
- [Configuration (.env)](#configuration-env)
- [Démarrage](#démarrage)
- [Commandes clés](#commandes-clés)
- [Persistance et sauvegardes](#persistance-et-sauvegardes)
- [Tests](#tests)
- [Licence](#licence)

## Fonctionnalités principales

- **Accueil et modération** : salutations automatiques, départs, filtrage des insultes, avertissements et timeouts.
- **Tickets et annonces** : création de tickets privés, annonces publiques ou staff, sondages.
- **Gestion des métiers et profils** : enregistrement des métiers (`!job`), profils joueurs (`!profil`), ladder et score de puissance.
- **Activités et événements** : planification via `!activite`, `!organisation` (assistant IA) ou `!event` (DM guidés), avec publication dans `#organisation`.
- **Assistants IA** : Gemini (Google) et OpenAI alimentent `!organisation`, `!iastaff` et certaines synthèses d'événements.
- **Statistiques et promotions** : modules `stats.py` et `up.py` pour suivre l'activité et gérer les montées en grade.

## Architecture des modules

- `main.py` : point d'entrée, configure le bot et charge les cogs.
- `cogs/` : commandes métiers et interactions Discord (profils, annonces, tickets, musique, etc.).
- `utils/` : stockage, sérialisation vers `#console`, helpers OpenAI/Gemini, dates, logs.
- `models/` : schémas de données (par exemple `event_data.py`).
- `examples/` : exemples anonymisés de JSON persistant.
- `tests/` : couverture Pytest (notamment `tests/test_main_evo_bot.py`, `tests/test_iastaff_*`, `tests/test_event_data.py`).

## Préparation du serveur Discord

### Rôles requis
- **Staff** : commandes d'administration, tickets et événements.
- **Membre validé d'Evolution** : rôle appliqué aux membres officiels (utilisé par `!activite`, `!ladder`, etc.).
- **Invités/Invité** : rôle optionnel pour les visiteurs.
- **Vétéran** : utilisé par le module de promotion `up.py`.

### Salons textuels attendus
- `console` : sauvegarde/chargement des fichiers JSON du bot.
- `ticket` : réception des tickets (`!ticket`).
- `annonces` : annonces publiques et sondages (`!annonce`, `!annoncestaff`).
- `organisation` : briefs d'activités (`!activite`, `!organisation`, `!event`).
- `𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥` : messages publics si les DM sont bloqués.
- `𝐑𝐞𝐜𝐫𝐮𝐭𝐞𝐦𝐞𝐧𝐭` : entrées et départs de la guilde.
- `𝐁𝐢𝐞𝐧𝐯𝐞𝐧𝐮𝐞` : messages d'accueil et d'au revoir.
- `𝐆𝐞́𝐧𝐞́𝐫𝐚𝐥-staff` : votes de promotion.
- `xplock-rondesasa-ronde` : annonces de PL.

Adaptez les constantes dans les fichiers Python si vous renommez ces salons ou rôles. Le module d'accueil conserve la liste des membres déjà salués dans `welcome_data.json` pour éviter les doublons après un redémarrage.

### Permissions du bot

- **Gérer les événements** pour créer ou modifier les événements planifiés.
- **Gérer les rôles** pour attribuer le rôle temporaire *Participants événement*.
- **Envoyer** et **gérer les messages** dans les salons listés ci-dessus.
- Accès aux messages privés et position hiérarchique suffisante pour créer des rôles.

## Installation

Clonez le dépôt puis installez les dépendances :

```bash
pip install -r requirements.txt
```

## Configuration (.env)

Créez un fichier `.env` avec au minimum :

- `DISCORD_TOKEN` (obligatoire)
- `GOOGLE_API_KEY` pour les appels Gemini
- `FERNET_KEY` pour le chiffrement des URL (générer via `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)

Ajoutez selon vos besoins :

- `OPENAI_API_KEY` (+ `OPENAI_STAFF_MODEL`, `OPENAI_FORCE_ORG`, `OPENAI_ORG_ID`) pour les assistants IA.
- `IASTAFF_*` pour configurer les outils et le contexte du module `iastaff.py`.
- `ORGANISATION_*` pour la planification IA (`!organisation`).
- `DATABASE_URL` si vous stockez les événements dans PostgreSQL (sinon persistance dans `#console`).
- `PROFILE_*`, `SCORE_*`, `PROFILE_SCORE_WEIGHTS` pour ajuster le ladder.

## Démarrage

```bash
python main.py
```

Pour maintenir le bot éveillé en production (Render), un micro-serveur Flask est exposé dans `alive.py` et peut être pingé par UptimeRobot.

## Commandes clés

- `!welcome` / automatisme d'accueil (messages dans `#𝐁𝐢𝐞𝐧𝐯𝐞𝐧𝐮𝐞`).
- `!ticket <objet>` : ouvre un ticket privé dans `#ticket`.
- `!annonce`, `!annoncestaff`, `!sondage` : annonces et sondages dans `#annonces`.
- `!activite` : planification d'activités avec formulaire Discord.
- `!organisation` : assistant IA en salon pour rédiger un brief d'événement (OpenAI).
- `!event` : planification complète en DM avec résumé Gemini puis publication dans `#organisation`.
- `!job <métier> <niveau>` / `!job del <nom>` : gestion des métiers (persistés dans `jobs_data.json`).
- `!profil set`, `!profil stats`, `!ladder`, `!ladder class <classe>`, `!ladder all` : profils et score de puissance.
- `!iastaff <message>` : assistant Staff IA (outils Discord si `IASTAFF_ENABLE_TOOLS=1`).
- `!warnings`, `!resetwarnings` : modération et sanctions automatiques.
- `!up` : gestion des promotions (rôle **Vétéran**).

Chaque cog applique ses propres contrôles de rôles/permissions ; en cas d'échec, le bot répond avec une erreur explicite.

## Persistance et sauvegardes

- Fichiers créés automatiquement : `activities_data.json`, `jobs_data.json`, `players_data.json`, `promotions_data.json`, `stats_data.json`, `warnings_data.json`, `welcome_data.json`.
- Les fichiers résident à côté des modules Python et ne sont pas suivis par Git. À chaque sauvegarde, leur contenu est aussi publié dans le salon `#console` (sauvegarde distante).
- Le module de statistiques maintient un message épinglé dans `#console` ; `stats_data.json` sert de cache local.
- Des exemples anonymisés se trouvent dans [`examples`](examples/).

## Tests

Lancez l'ensemble de la suite :

```bash
python -m pytest
```

Les tests couvrent notamment les commandes IA, la validation des événements (`tests/test_event_data.py`) et le comportement du bot principal.

## Licence

Projet distribué sous licence MIT. Voir [LICENSE](LICENSE) pour plus de détails.
