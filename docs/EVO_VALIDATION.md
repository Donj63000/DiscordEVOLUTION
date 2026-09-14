# Rapport de validation — Evo / Luna

## Intégration dans le dépôt — 15 septembre 2026

Le patch `evolution_luna.patch` a été appliqué sans conflit à la branche `main`
du dépôt DiscordEVOLUTION. La découverte pytest inclut désormais `tests_evo`,
y compris dans la CI GitHub existante.

Les vérifications d'intégration ajoutées couvrent le chargement conditionnel
d'Evo avant l'adaptateur slash, la synchronisation avec Evo activé ou désactivé,
les réponses explicites au dernier message du membre, la séparation public/privé,
le retrait des permissions pendant la génération et l'annulation par `/evo-oublier`.

Deux défauts de données ont été corrigés avec régressions reproduites avant correction :
les drops de monstres excluent les rangs inactifs et signalent les niveaux suspects ;
l'index d'équipements rejette les identifiants explicitement invalides.

La commande réelle d'initialisation SQLite a été exécutée sur un fichier de test
neuf ; une seconde exécution refuse de remplacer ce registre.
L'initialiseur ferme désormais explicitement sa connexion : les tests Windows ont
révélé un fichier encore verrouillé après initialisation. Deux régressions vérifient
la fermeture sans dépendre du ramasse-miettes et le retrait d'un fichier partiel
après une erreur SQL.

Validation finale locale : **1 993 tests réussis, 1 test historique ignoré** en
38,80 secondes, avec Python 3.13.14 et discord.py installé. Le test ignoré concerne
l'ancien module `cogs.organisation`, retiré du projet. Les 80 tests Evo sont exécutés.
Les avertissements existants de dépréciation ne sont pas des échecs.

```text
python -m pytest -q -rs --tb=short --disable-warnings \
  -W error::pytest.PytestDeprecationWarning -p no:cacheprovider \
  --basetemp <répertoire temporaire neuf>
```

Sous Windows, l'exécution utilise `PYTHONUTF8=1` et des répertoires `TMP`/`TEMP`
isolés. Cela évite l'ancien cache pytest inaccessible et aligne la lecture des
exports UTF-8 des tests Enquête sur l'environnement Linux du serveur.

Les appels Discord et OpenAI restent simulés. La connexion PostgreSQL de production,
l'accès au modèle avec la clé du serveur et la recette Render restent à effectuer
selon `docs/EVO.md`. Le patch conserve `EVO_ENABLED=0` par défaut.

Les sections suivantes conservent le rapport historique de préparation de l'archive.

## Préparation de l'archive

Archive source : `discordEVO.zip`
SHA-256 : `10710f5f1527946cffcab7b78edd0b54883fccd61430f694847136c5515cba35`
Date de préparation : 15 septembre 2026.

## Vérifications réalisées hors ligne

La suite dédiée `tests_evo` a exécuté 72 cas : **70 réussis, 2 ignorés**.
Les deux cas ignorés concernent l'enregistrement natif et la politique Discord ;
`discord.py` n'est pas installé dans l'environnement de préparation.

Les vérifications couvrent notamment : réservations avant génération, transactions
SQLite concurrentes sur deux instances, doublons, limites mensuelles/journalières
et individuelles, reprise après réouverture, passage de mois, réponse tardive,
réservation conservée après timeout, refus de modèle inattendu, outils payants
interdits, schémas stricts, paramètres hors bornes, injection par pseudo, sources
autorisées, isolation des mémoires par membre et par audience, contexte compact,
clarifications en un appel, boucle d'outils limitée, PP, identités d'objets,
classements, exos natifs exclus, atelier FM privé, métiers, activités et opt-in
de lecture de l'historique.

**444 tests existants** ont également réussi, sans modification de leurs assertions :
`test_drop_calculator.py`, `test_exo_engine.py`, `test_exo_math.py`,
`test_exo_data.py`, `test_exo_workshop.py`, `test_xixou_image_paths.py`.

Pour exécuter ces tests existants ici, leurs fichiers ont été copiés dans un
répertoire temporaire isolé : le `conftest.py` global du projet importe Discord,
indisponible dans cet environnement. Les modules métier testés sont ceux du projet
modifié. Cela ne constitue pas l'exécution de toute la suite historique du bot.

Les sources Python ajoutées et modifiées ont été compilées, et la configuration
JSON a été parsée. La préparation a utilisé Python 3.13.
Les sources sont écrites pour Python 3.11+ ; les autres versions n'ont pas été
exécutées ici.

Le patch a été vérifié avec `git diff --check` et `git apply --check`, puis appliqué
à une nouvelle extraction de l'archive source. Les fichiers appliqués ont été
comparés octet par octet aux fichiers préparés.

## Ce qui n'a pas été validé en conditions réelles

Aucune génération n'a été envoyée à OpenAI et aucun token du compte utilisateur
n'a été utilisé pour ces essais. Les transports IA sont simulés dans les tests.
La documentation officielle confirme le modèle et les endpoints utilisés ; cela
ne vérifie pas l'accès au modèle avec la clé de l'utilisateur.

Aucune connexion Discord, synchronisation slash distante ou publication dans la
guilde n'a été réalisée. Les tests optionnels Discord doivent être rejoués dans
l'environnement du projet avec ses dépendances installées.

Le pilote `asyncpg` et un serveur PostgreSQL réel ne sont pas disponibles ici.
La logique du registre a été testée avec des transactions SQLite réelles, mais
**les verrous PostgreSQL et la connexion de production restent à vérifier** sur
une base de test. Les tables doivent être initialisées explicitement avant usage.

Aucun appel authentifié aux catalogues Xixou de l'utilisateur n'a été réalisé.
Les adaptateurs sont fondés sur les structures du ZIP et vérifiés avec des
fixtures synthétiques. Leur couverture réelle dépend des catalogues disponibles,
de la clé Xixou, des données du serveur et de la version déployée.

Les tests ne démontrent pas que Luna répondra correctement à toutes les questions
Dofus, ni qu'un classement heuristique sera toujours le meilleur conseil en jeu.
La recette de déploiement dans `EVO.md` inclut des comparaisons avec les commandes
existantes du bot.

Aucune modification n'a été poussée vers GitHub ou déployée sur Render.
Aucune limite de dépense n'a été configurée dans le tableau de bord OpenAI.

## Exécution dans le projet

```bash
python -m unittest discover -s tests_evo -v
```

Après installation de toutes les dépendances du projet, cette commande exécute
aussi les deux tests d'enregistrement/politique Discord, sans connexion Discord
et sans génération payante. Les 444 tests métier sélectionnés peuvent être
rejoués dans leur emplacement habituel avec pytest lorsque les dépendances du
`conftest.py` du projet sont installées.

## Limites financières

Le registre est une barrière applicative conservatrice, pas une garantie absolue
sur la facture en euros. Il dépend de sa conservation, des tarifs configurés,
du modèle retourné et de l'usage rapporté. Il ne couvre pas les appels des anciennes
IA, d'autres programmes ou d'autres projets. Utiliser également un projet OpenAI
dédié, une limite fournisseur et les contrôles de facturation.

Une erreur/annulation peut laisser une réservation pessimiste. C'est intentionnel :
le code ne rend pas du budget sur la seule hypothèse qu'un appel n'a rien coûté.
