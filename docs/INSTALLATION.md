# Installation et exploitation

[Retour au README](../README.md)

Ce guide décrit une instance du bot adaptée à votre serveur. Le dépôt fournit le code ;
le jeton, les salons, les rôles et les services externes se configurent dans votre environnement.

## 1. Préparer Python

Utilisez Python **3.11 ou 3.12**, les versions vérifiées par la CI.

```bash
git clone https://github.com/Donj63000/DiscordEVOLUTION.git
cd DiscordEVOLUTION
python -m venv .venv
```

**Windows / PowerShell**

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

**Linux / macOS**

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Modifiez ensuite `.env`. Les valeurs d'exemple doivent être remplacées par vos valeurs ;
laissez vides les clés des services que vous n'utilisez pas. Ne publiez pas ce fichier.

## 2. Configurer l'application Discord

Créez ou utilisez votre application dans le [Discord Developer Portal](https://discord.com/developers/applications),
puis renseignez son jeton dans `DISCORD_TOKEN`.

Pour les réglages par défaut du projet, activez dans **Bot > Privileged Gateway Intents** :

| Réglage Discord | Variable du projet | Valeur par défaut |
| --- | --- | --- |
| Message Content Intent | `ENABLE_MESSAGE_CONTENT_INTENT` | `1`, pour le préfixe et les parcours qui lisent les messages. |
| Server Members Intent | `ENABLE_MEMBERS_INTENT` | `1`, pour le suivi des membres. |
| Presence Intent | `ENABLE_PRESENCE_INTENT` | `0`, à activer des deux côtés seulement si utilisé. |

Les intents demandés par le code doivent correspondre à ceux autorisés dans le portail.
Voir la [documentation Discord sur les intents](https://docs.discord.com/developers/events/gateway#gateway-intents).

Installez l'application sur le serveur avec les scopes **`bot`** et **`applications.commands`**.
Les membres doivent pouvoir utiliser les commandes d'application dans les salons concernés.

Préparez ensuite :

- Un rôle nommé **`Staff`**. Plusieurs modules utilisent encore ce nom directement ;
  `IASTAFF_ROLE` ne renomme pas uniformément les contrôles de tout le projet.
- Un salon texte privé **`console`**, accessible au bot et au Staff.
- Pour le bot : accès aux salons utilisés, lecture de l'historique, envoi de messages,
  embeds et fichiers ; permissions de gestion des messages et des épingles dans `#console`.
- Selon les fonctionnalités activées : gestion des salons, des rôles et des événements.
  Le rôle du bot doit être au-dessus des rôles qu'il doit gérer.

Le bot peut créer `#console` si `CONSOLE_AUTO_CREATE=1` et s'il dispose des permissions
nécessaires. Le nom `console` reste le choix compatible avec l'ensemble des modules.

## 3. Renseigner les variables utiles

La liste complète se trouve dans [`.env.example`](../.env.example).

| Variable | Quand la renseigner |
| --- | --- |
| `DISCORD_TOKEN` | **Obligatoire** pour démarrer le client Discord. |
| `ENABLE_AI_COMMANDS` | `0` par défaut : aucune commande dépendant de l'IA n'est publiée. `1` exige aussi la clé du fournisseur concerné. |
| `OPENAI_API_KEY` | Pour les parcours OpenAI explicitement réactivés ; `/organisation` fonctionne sans IA. |
| `GOOGLE_API_KEY` | Pour Gemini, notamment `/event`, les conversations IA et IA Staff avec le backend de la configuration d'exemple. |
| `FERNET_KEY` | Pour conserver l'historique local chiffré de Defender ; sans clé, cet historique est désactivé. |
| `SYNC_SLASH_COMMANDS` | `1` pour synchroniser les commandes au démarrage. |
| `SYNC_SLASH_GUILD_ID` | ID du serveur de développement ; vide pour la synchronisation globale. Un ID invalide bloque la publication au lieu de la rendre globale. |
| `SLASH_CLEANUP_RETIRED` | `1` : après une synchronisation réussie, retirer les anciennes commandes désactivées dans les autres périmètres, sans vider les catalogues. |
| `BOT_PREFIX` | `!` par défaut. |
| `IASTAFF_ENABLE_TOOLS` | `1` pour activer le catalogue d'outils de l'assistant Staff. |
| `DOFUS_WIKI_CACHE_TTL` | Durée du cache frais du wiki : `3600` secondes par défaut. |
| `DOFUS_WIKI_TIMEOUT` | Délai d'une requête HTTP du wiki : `10` secondes par défaut. |

Les clés IA ne sont pas requises pour utiliser l'encyclopédie, les métiers et les autres
commandes sans IA. Chaque service activé doit disposer de sa propre configuration.
La commande musicale nécessite aussi **FFmpeg** disponible dans le `PATH` de la machine.

Pour générer une clé Fernet :

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Conservez cette clé si vous souhaitez relire l'historique déjà chiffré.
Les données de guilde restent sauvegardées dans `#console` ; l'historique local Defender
ne remplace pas ce stockage.

## 4. Démarrer et vérifier

```bash
python main.py
```

Au démarrage, le bot charge ses extensions, synchronise les commandes si configuré
et acquiert un verrou d'instance dans `#console`.

Sur le serveur de test, vérifiez :

1. `/ping` et `!ping`.
2. `/aide` et l'apparition des quatre commandes d'encyclopédie.
3. Une recherche `/objet`, une recette et le bouton de modification de quantité.
4. Le chargement des données de guilde et l'accès du Staff aux fonctions réservées.

Pour travailler sur le code sans connecter le bot :

```bash
python -m pytest
```

## 5. Nom, logo et commandes slash

Le logo du projet est [`assets/evolution-bot.png`](../assets/evolution-bot.png).

Avec les valeurs suivantes, le bot synchronise son nom d'utilisateur, son avatar et
l'icône de son application :

```dotenv
SYNC_BOT_IDENTITY=1
BOT_DISPLAY_NAME=Evolution BOT
BOT_AVATAR_PATH=assets/evolution-bot.png
SYNC_SLASH_COMMANDS=1
```

Le **nom de l'application** se règle séparément dans le Developer Portal, rubrique
**General Information** : utilisez **Evolution BOT**. Le nom affiché dans un serveur
peut aussi dépendre d'un surnom. [Explication Discord](https://support-dev.discord.com/hc/en-us/articles/6129090215959-How-Do-I-Change-My-Bot-s-Name).

Les empreintes de l'identité appliquée sont conservées dans le snapshot `===BOTBRANDING===`
de `#console`. La synchronisation attend que ce salon soit disponible et que le verrou
de l'instance active soit acquis.

Les commandes globales et les changements d'identité peuvent demander un délai de
propagation. Pour un serveur de développement, `SYNC_SLASH_GUILD_ID` permet une
synchronisation ciblée. Les permissions Staff restent contrôlées à l'exécution ;
leur visibilité se règle aussi dans les intégrations du serveur.

## 6. Render et UptimeRobot

Le projet associe un bot Discord et un petit serveur HTTP de suivi.

| Élément | Rôle |
| --- | --- |
| **Render** | Héberge le processus Python et expose le port HTTP. |
| **`main.py`** | Lance le bot Discord et, par défaut, démarre aussi le serveur HTTP. |
| **`alive.py`** | Sert la route HTTP `GET /`. |
| **UptimeRobot** | Interroge l'URL publique pour suivre sa disponibilité HTTP. |

Pour une instance en **Web Service Python** sur Render :

Choisissez Python 3.11 ou 3.12 pour ce service, puis appliquez les réglages suivants.

| Réglage | Valeur |
| --- | --- |
| Build Command | `python -m pip install -r requirements.txt` |
| Start Command | `python main.py` |
| `ALIVE_IN_PROCESS` | `1` |
| `ALIVE_SERVER` | `gunicorn` |
| `ALIVE_WORKERS` | `1` |
| `ALIVE_THREADS` | `4` |
| Secrets | Variables d'environnement Render, notamment `DISCORD_TOKEN`. |

Le serveur écoute sur `0.0.0.0` et utilise la variable `PORT` fournie par Render.
La configuration d'exemple utilise `wsgiref` pour le local ; choisissez explicitement
`gunicorn` sur Render. Voir le [guide des Web Services Render](https://render.com/docs/web-services).

Configurez dans UptimeRobot un moniteur HTTP(S) vers `https://votre-service.onrender.com/`.
Le choix de l'intervalle et la disponibilité continue dépendent de vos offres d'hébergement
et de monitoring.

**Le ping HTTP ne vérifie pas la connexion du bot à Discord.** La route renvoie un texte
fixe : complétez cette surveillance par les logs Render et une commande `/ping` sur le serveur.

Pour lancer uniquement le serveur HTTP, par exemple dans un service séparé :

```bash
gunicorn alive:app --bind 0.0.0.0:$PORT
```

Cette commande ne lance pas le bot Discord. Si les services sont séparés,
démarrez le bot avec `python main.py` et `ALIVE_IN_PROCESS=0`.

## Dépannage rapide

| Symptôme | À vérifier |
| --- | --- |
| Les commandes `/` sont absentes | Synchronisation dans les logs, scopes d'installation, permissions du salon et éventuel `SYNC_SLASH_GUILD_ID`. |
| Le préfixe `!` ne répond pas | `BOT_PREFIX`, Message Content Intent dans le code et dans le portail. |
| Les commandes Staff sont refusées | Rôle `Staff`, permissions du bot et hiérarchie des rôles. |
| Les données semblent manquer après redémarrage | Accès à `#console`, messages de sauvegarde et logs de chargement. |
| L'encyclopédie signale une indisponibilité | Accès à l'API publique ; le bot utilise un cache de secours récent lorsqu'il en dispose. |
| Render répond mais le bot est hors ligne | Start Command, jeton, intents, chargement des extensions et verrou d'instance. |
| Une commande IA n'apparaît plus | Retrait volontaire par défaut : `ENABLE_AI_COMMANDS=0`. Réactiver exige un fournisseur disponible, sa clé, `ENABLE_AI_COMMANDS=1` et un redémarrage/synchronisation. `/event` dépend de Gemini. |

N'effacez pas manuellement les snapshots de `#console` pour résoudre un problème de
démarrage. Les [détails de persistance](ARCHITECTURE.md#persistance) expliquent leur rôle.
