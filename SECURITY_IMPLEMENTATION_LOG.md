# Journal des correctifs de sécurité

## Point de départ

- Autorisation : implémenter le plan accepté dans la conversation.
- Base : `936490bf235d6cb8522f0de23411388ebb15eb22`, état initial propre.
- Branche locale : `security/hardening-evolution`.
- Le lot initial autorisait les travaux locaux sans appel au bot de production,
  push ni déploiement. Après sa validation, l'utilisateur a demandé sa publication
  sur GitHub ; cette autorisation ne comprend pas de déploiement.

## Résultat final — 26 octobre 2026

Les neuf tâches sont implémentées et les validations locales sont réussies. À la
clôture du lot local, la CI GitHub était configurée mais son exécution distante
était **NON EXÉCUTÉE**. La publication demandée ensuite fait l'objet de la section
ci-dessous. Aucun test n'a visé la production.

| Tâche | État | Preuve principale |
|---|---|---|
| TASK-001 — politique de guilde | RÉUSSI | Gardes centrales, commandes et composants Discord ; tests de guilde étrangère/cache vide |
| TASK-002 — dépendances, locks, audits | RÉUSSI | Deux installations hachées propres ; quatre audits, 89 paquets chacun, zéro vulnérabilité |
| TASK-003 — Defender et santé HTTP | RÉUSSI | Resolver du transport, redirections et IP littérales filtrés ; santé HTTP et Gunicorn réels |
| TASK-004 — événements et persistance | RÉUSSI | Provenance HMAC, reprise Staff séparée du nettoyage ; roundtrip console et PostgreSQL réel |
| TASK-005 — outils IA | RÉUSSI | Droits actuels, rôles autorisés, confirmations liées au contexte à usage unique et commandes contrôlées |
| TASK-006 — propriété des fiches | RÉUSSI | Aucun transfert par nom ; guilde explicite, recrues et sauvegardes vides conservées |
| TASK-007 — exclusivité des MP | RÉUSSI | Réservation atomique, routage immuable, expiration/libération et isolation des réponses tickets |
| TASK-008 — données publiques et archive | RÉUSSI | Deux fichiers retirés du dépôt courant, exclusions Git et scan Gitleaks sans résultat |
| TASK-009 — intégration, CI, documentation | RÉUSSI localement | Suites complètes et isolées sur les deux Python, Monte-Carlo, compilation, preflight et documentation |

Les rapports finaux sont conservés dans `/workspace/scratch/security-audit/`.
Les anciens logs de diagnostic y restent disponibles ; seules les exécutions
finales nommées ci-dessous constituent les résultats d'acceptation.

## Validation finale exécutée

| Contrôle | Python 3.11.17 | Python 3.12.14 |
|---|---|---|
| Suite complète avec couverture | 4246 réussis, 125 sous-tests réussis, 1 ignoré | 4246 réussis, 125 sous-tests réussis, 1 ignoré |
| Suite sécurité exécutée séparément | 180 réussis, aucun ignoré | 180 réussis, aucun ignoré |
| Installation des 89 paquets depuis un environnement vide, hashes et `pip check` | RÉUSSI | RÉUSSI |
| Audit PyPI du verrou complet | 0 vulnérabilité, 0 dépendance ignorée | 0 vulnérabilité, 0 dépendance ignorée |
| Audit OSV du verrou complet | 0 vulnérabilité, 0 dépendance ignorée | 0 vulnérabilité, 0 dépendance ignorée |
| Monte-Carlo, 100000 tirages, seed 129 | RÉUSSI, 0 erreur de registre | RÉUSSI, 0 erreur de registre |
| Compilation des sources et tests | RÉUSSI | RÉUSSI |

Le seul test ignoré à la collecte est `tests/test_cogs_organisation_flow.py`,
conservé historiquement pour l'ancien module supprimé `cogs.organisation` ; ce
comportement existait avant le lot. Les parcours du module `organisation.py`
actuel sont exécutés. Aucune exception ni test ignoré de sécurité n'a été ajouté.
Les dépréciations pytest sont traitées comme erreurs ; les autres avertissements
existants restent visibles (75 sur Python 3.11, 78 sur Python 3.12).

La couverture globale de `--cov=.` vaut 82,90 % et 82,97 % respectivement ; elle
inclut les modules de test. Les rapports XML décrivent les lignes non couvertes.
Cette mesure ne constitue pas une preuve d'absence de toute vulnérabilité.

Le PostgreSQL 16 de validation est jetable et exposé uniquement sur l'interface
locale. Le test crée puis détruit un schéma unique ; le JSONB, les dates et les
signatures sont réellement enregistrés et rechargés via asyncpg. Le test refuse
les remplacements et suppressions d'une ligne appartenant à une guilde étrangère.
Le conteneur local a été arrêté et supprimé à la fin des vérifications.

Autres contrôles réussis : `tools/build_preflight.py` sans réseau ni dépendance
absente, Ruff F821 sur les modules applicatifs modifiés, `git diff --check`, et
Gitleaks 8.30.1 sur l'arborescence courante (environ 32,6 Mo, aucune fuite trouvée).
La validation Monte-Carlo vérifie les invariants du logiciel ; elle ne certifie
pas le comportement du serveur de jeu.

Fichiers de preuve : `full-py311.log/xml`, `full-py312.log/xml`,
`coverage-py311.xml`, `coverage-py312.xml`, `security-final-py311.log/xml`,
`security-final-py312.log/xml`, `fm-model-py311.json`, `fm-model-py312.json`,
`audit-py311-{pypi,osv}.json`, `audit-py312-{pypi,osv}.json`,
`clean-install-py311.log`, `clean-install-py312.log`, `build-preflight.log`,
`ruff-f821.log`, `ruff-ttl.log` et `gitleaks-complete.log/json`.
`acceptance-summary.json` rassemble les résultats JUnit, les résumés d'audit et
les empreintes SHA-256 des quatre rapports d'audit.

## Détails des corrections et décisions d'intégration

- La guilde autorisée est obligatoire et n'est jamais inférée du cache. Les
  commandes préfixées/slash, les composants avant leur distribution par
  discord.py, les services, listeners et tâches respectent ce périmètre.
- Les sondes Defender utilisent le resolver filtrant du connecteur réel.
  Les résolutions mixtes, adresses privées, IPv4 mappées et destinations
  interdites échouent sans devenir des liens déclarés sûrs. La santé HTTP refuse
  les corps encodés et les charges supérieures à 4096 octets.
- Les événements ont des ressources propres et une preuve stable ; ils ne
  récupèrent jamais un rôle ou salon homonyme. La reprise native
  `/event-securite revalider` fonctionne sans IA et laisse les événements expirés
  sans nettoyage armé. `/event-securite nettoyer` exige une autre confirmation.
  Les erreurs de sauvegarde restaurent l'état mémoire et n'activent aucune
  action. Les conversations de collecte ne sont plus restaurées après reboot.
- Les droits des acteurs et les rôles Discord sont rechargés avant les opérations
  sensibles. Les propositions IA contiennent des cibles normalisées et figées ;
  un nonce lie auteur, guilde, salon et action pendant au plus 300 secondes.
  Toutes les mutations exigent confirmation, même avec l'ancien flag désactivé.
  Le réglage historique du TTL est plafonné à 300 secondes et peut être réduit ;
  deux tests de configuration en processus séparés vérifient ces deux contrats.
  Le message de confirmation affiche désormais les secondes exactes. Après ce
  dernier ajustement d'affichage, les 62 tests IA concernés sont de nouveau
  réussis sous chaque Python (`ttl-confirmations-final-py311.log` et
  `ttl-confirmations-final-py312.log`).
  L'adaptateur de commandes exécute les checks, conversions, cooldowns,
  concurrence et hooks ; l'historique reste du contenu utilisateur non fiable.
- Les fiches passent par une API avec guilde explicite et copies en lecture.
  Aucun rapprochement de pseudo ne transfère données, métiers ou mules entre IDs.
- Les MP sont attribués avant la distribution du message aux consommateurs.
  Une ancienne génération ne libère pas une nouvelle réservation ; le dernier
  message d'une collecte n'est pas réutilisé par l'IA ou l'accueil. La relecture
  a aussi remplacé l'attente Staff commune des tickets par un formulaire lié au
  ticket et à son auteur, pour empêcher les réponses croisées.
- Les fixtures historiques déclarent leur guilde synthétique ; les checks
  réels restent exercés. Les helpers AST Exo incluent désormais le vrai garde
  de guilde, avec un test négatif sans mutation. Les SDK applicatifs ne sont pas
  remplacés par de faux modules dans la suite de sécurité isolée.
- La restriction d'installation de `/event-securite` est explicitement limitée
  à la guilde. Une constante de mentions mal orthographiée dans `annonce.py`,
  détectée pendant le contrôle statique, a également été corrigée.

## TASK-002 — dépendances et vérifications réalisées

- RÉUSSI : résolution des 89 dépendances sur Python 3.11.17 et 3.12.14,
  verrous complets avec hashes, installations dans deux environnements vides,
  `pip check` et correspondance exacte des inventaires installés aux verrous.
- RÉUSSI : quatre audits stricts, deux graphes × services PyPI et OSV,
  zéro vulnérabilité et zéro dépendance ignorée. Rapports conservés dans
  `/workspace/scratch/security-audit/audit-py3*.json`.
- Les SDK résolus sont discord.py 2.7.1, OpenAI 1.109.1 et google-genai 1.55.0.
- La génération utilise `python -P -m piptools compile` : le `build.py` local
  masque autrement le paquet `build` de l'outil.

## Compatibilité du transport HTTP des tests

Aucun release aioresponses publié jusqu'à 0.7.9 ne transmet le nouveau paramètre
obligatoire `stream_writer` d'aiohttp 3.14. Un adaptateur exclusivement destiné aux
tests est donc placé dans `tests_support/http_mock.py`, via l'option publique
`response_class`. Il fournit un upload synthétique de zéro octet et un protocole
mémoire au véritable `StreamReader`, et transmet la classe aux `CallbackResult`.
Aucune signature globale aiohttp ni aucun code applicatif n'est modifié.
Les limites de taille, callbacks, timeouts et assertions existantes sont conservés.

- RÉUSSI : 543 tests HTTP/wiki/Xixou/navigation/slash et contrats de l'adaptateur
  sous chacun des deux Python ; cinq contrats vérifient notamment l'absence
  d'altération globale du SDK, les corps, JSON, erreurs, callbacks et gros buffers.
- RÉUSSI : suites complémentaires Evo/jobs/build/mémoire, 1582 tests et 107
  sous-tests sous chacun des deux Python. Les fixtures déclarent désormais
  explicitement la guilde synthétique ; les refus inter-guildes restent vérifiés.
- Preuves : `http-compat-py311-final.log`, `http-compat-py312-final.log`,
  `integration-py311.log`, `integration-py312-round4.log` dans le dossier d'audit.

Ces résultats ciblés sont complétés par les deux suites intégrales finales
ci-dessus.

## Reproduction

Depuis un environnement Linux vide avec le Python correspondant, installer le
verrou avec `python -m pip install --require-hashes -r requirements/linux-py312.lock`
(ou `linux-py311.lock`), puis exécuter `python -m pip check`. Configurer
`TEST_DATABASE_URL` vers une base PostgreSQL locale jetable avant les tests ;
ne jamais utiliser une base de production.

```bash
python -m pytest tests_security -q -W error::pytest.PytestDeprecationWarning
python -m pytest -q -W error::pytest.PytestDeprecationWarning --cov=. --cov-report=xml
python tools/build_preflight.py
python tools/fm_retro_validate.py monte-carlo --draws 100000 --seed 129 --output fm-model.json
```

Les commandes d'audit et de génération des verrous sont dans
[`requirements/README.md`](requirements/README.md), les équivalents CI dans
[`.github/workflows/ci.yml`](.github/workflows/ci.yml).

## Exploitation restant à effectuer lors d'un déploiement

À la clôture du lot local, aucun push, déploiement ni changement de configuration
distante n'avait été effectué. La configuration et la recette Discord
de production sont **NON EXÉCUTÉES**. Renseigner `BOT_GUILD_ID`, conserver une
`EVENT_PROVENANCE_KEY` privée valide et, si des opérations de rôle IA sont voulues,
la liste `IASTAFF_ALLOWED_ROLE_IDS`. Les sauvegardes anciennes doivent être
conservées avant revalidation Staff ; aucune reprise ne doit purger implicitement
les événements expirés. Voir [`docs/SECURITY.md`](docs/SECURITY.md).

`CODEX.ZIP` et `temp_players_data.json` sont retirés de l'arborescence suivie ;
les anciennes copies dans l'historique Git ne sont pas purgées. Le fichier
`data/profiles.json` était vide et demeure inchangé. Aucun contenu utilisateur ni
secret réel n'est nécessaire aux validations et aucune donnée de production n'a
été modifiée.

## Publication GitHub autorisée

Après la validation locale, l'utilisateur a demandé : « Quand tu as fini met a
jour sur github ». Les correctifs sont donc préparés pour publication sur la
branche `security/hardening-evolution` de `Donj63000/DiscordEVOLUTION`, avec une
pull request vers `main`. La configuration de production et le déploiement
restent hors périmètre. Les exécutions GitHub Actions et leurs artefacts fourniront
les preuves distantes en complément des validations locales de ce journal.
