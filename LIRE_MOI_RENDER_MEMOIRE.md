# Correctif mémoire Render — patch Git

Version cible : `DiscordEVOLUTION-main (17).zip`, à la racine du dépôt.

Le patch réduit les copies permanentes des archives Build, ajoute le mode HTTP
`aiohttp` sans processus Gunicorn et des relevés mémoire. Les sauvegardes
Discord, commandes, intents, caches membres/messages et dépendances sont conservés.

## Application

Partir du projet original fourni, pas de l'archive déjà corrigée. Enregistrer
les modifications locales dans un commit avant l'application. Depuis le dossier
contenant `main.py`, avec le fichier patch placé dans ce même dossier :

```sh
git apply --check DiscordEVOLUTION_Render_512Mo.patch
git apply DiscordEVOLUTION_Render_512Mo.patch
git diff --check
```

Si le contrôle échoue, ne pas forcer : le patch peut être déjà appliqué ou le
dépôt peut avoir évolué. Examiner les fichiers indiqués par Git.

## Activation sur Render

Conserver `python main.py` comme commande de démarrage et régler :

```dotenv
ALIVE_IN_PROCESS=1
ALIVE_SERVER=aiohttp
MEMORY_LOG_INTERVAL=60
```

Ces variables doivent être renseignées dans Render ; `.env.example` n'active
pas ce mode dans un service existant. Conserver tous les autres secrets.
Ne pas remplacer le véritable `.env` et ne pas lancer un second serveur HTTP.

Le détail du diagnostic, des tests, des limites et du retour arrière se trouve
dans [le guide mémoire](docs/RENDER_MEMOIRE_512.md).
