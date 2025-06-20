# EvoDefender

EvoDefender est la cog de sécurité du projet **DiscordEVOLUTION**. Son rôle est de protéger le serveur en analysant chaque lien posté et en signalant les URLs potentiellement dangereuses.

## Fonctionnement général

- Surveillance de tous les messages pour détecter les URLs.
- Analyse de chaque lien avec **PhishTank**, **VirusTotal** et, si disponible, **Google Safe Browsing**.
- Extension des shortlinks et vérification de la présence d'adresses IP privées pour prévenir les attaques SSRF.
- Réponse automatique sous forme d'embed précisant le statut de chaque URL (SÛR, INDÉTERMINÉ ou DANGEREUX).
- Commande `!scan <url>` pour lancer manuellement une analyse.
- Journalisation et historisation chiffrée dans `historique_defender.db`.

## Mise en place

1. Créez un fichier `.env` contenant les clés API suivantes (facultatives mais recommandées) :
   - `VT_API_KEY` pour VirusTotal
   - `GSB_API_KEY` pour Google Safe Browsing
   - `PHISHTANK_APP_KEY` pour PhishTank
   - `FERNET_KEY` (**obligatoire** et fourni via la variable d'environnement `FERNET_KEY`)
2. Assurez-vous que le module `defender` est chargé par `main.py`.
3. Les fichiers `defender_discord.log` et `historique_defender.db` seront créés automatiquement à la première exécution.

## Personnalisation

- Modifiez `LISTE_BLANCHE_DOMAINE` dans `defender.py` pour ajouter vos domaines de confiance.
- La constante `SHORTLINK_DOMAINS` détermine quels raccourcisseurs sont automatiquement développés.
- `MAX_CONCURRENT_SCANS` fixe le nombre d'analyses simultanées.

## Historique et journaux

Chaque URL est chiffrée avant d'être insérée dans la base `historique_defender.db` avec son statut. Les journaux d'activité sont écrits dans `defender_discord.log` pour faciliter le débogage.

## Utilisation

Une fois le bot lancé, EvoDefender scanne automatiquement tout lien publié sur le serveur. Pour analyser manuellement une URL, utilisez la commande :

```bash
!scan <url>
```

Le bot répondra avec un embed récapitulant le verdict et affichera l'URL masquée en cas de danger.

