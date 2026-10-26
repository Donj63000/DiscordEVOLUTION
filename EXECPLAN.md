# EXECPLAN — Correctifs de sécurité

## 1. Objectif
Corriger les failles de l'audit du 26 octobre 2026 et leurs régressions, sans
intervenir sur la production.

## 2. Version analysée
`Donj63000/DiscordEVOLUTION`, `main`, `936490bf235d6cb8522f0de23411388ebb15eb22`.

## 3. Statut du plan
Implémentation autorisée par l'utilisateur. Les résultats effectifs sont consignés
dans `SECURITY_IMPLEMENTATION_LOG.md` ; ce plan ne vaut pas preuve de validation.

## 4. Périmètre
Bot réservé au serveur Evolution, Defender, événements, IA Staff, fiches joueurs,
parcours privés, dépendances, CI et données publiques signalées. Le lot initial
est réalisé localement, sans déploiement, test de production ou réécriture
d'historique. Après validation locale, l'utilisateur a également autorisé la
publication des correctifs sur GitHub, sur une branche avec pull request.

## 5. Invariants
Une action refusée ne provoque aucun effet ; les noms ne prouvent pas la propriété ;
un MP appartient à un parcours ; les fonctions existantes restent disponibles dans
Evolution ; les callbacks et tâches différées appliquent les mêmes protections.

## 6. Architecture actuelle
Python 3.11/3.12, discord.py, commandes préfixées/slash, collecteurs MP, snapshots
`#console`, stockage PostgreSQL facultatif pour les événements et serveur de santé.

## 7. Constats techniques
Validation DNS séparée du transport, snapshots événements non authentifiés,
contrôles IA insuffisants, fusion de fiches par nom, données globales entre guildes,
collectes MP concurrentes et dépendances anciennes. La restauration doit aussi
préserver les dates/provenances et ne pas adopter de ressources homonymes.

## 8. Exigences
REQ-001 : guilde unique. REQ-002 : sorties réseau publiques validées.
REQ-003 : ressources événements authentifiées. REQ-004 : permissions et
confirmation humaine IA. REQ-005 : propriété joueurs intransférable par nom.
REQ-006 : routage MP exclusif. REQ-007 : zéro avis connu dans les graphes résolus.
REQ-008 : retrait des données privées/archives signalées du dépôt courant.
REQ-009 : CI et non-régression des fonctions conservées.

## 9. Architecture cible
Politique centrale de guilde, resolver filtrant dans le connecteur, provenance HMAC
des ressources d'événement, contrôles IA hors modèle, coordinateur MP partagé et
deux verrous complets avec hashes.

## 10. Contrats techniques
`BOT_GUILD_ID` obligatoire, cohérent avec `EVO_GUILD_ID` si présent.
`EVENT_PROVENANCE_KEY` : 32 octets aléatoires encodés en 64 caractères hexadécimaux.
`IASTAFF_ALLOWED_ROLE_IDS` : liste vide par défaut, rôles privilégiés toujours refusés.
Les joueurs reçoivent une guilde explicite. Les signatures couvrent les IDs de
ressources et dates UTC, pas les objets transitoires ni les participants.

## 11. Plan d'implémentation
TASK-001 : gardes commandes, callbacks, listeners, persistance et tâches par guilde,
sans repli vers la première guilde ni erreur due au cache vide avant connexion.
TASK-002 : mises à jour corrigées, SDK OpenAI/google-genai <2, `protobuf<6`, locks
Linux Python 3.11 et 3.12, installation par hashes et audits PyPI/OSV sans exception.
TASK-003 : validation des IP réellement utilisées, IP littérales et chaque
redirection ; ports 80/443, sans userinfo ; aucune option ne désactive la sécurité.
TASK-004 : snapshots du bot, ressources uniques, provenance signée, roundtrip JSON
console/Postgres, rollback limité aux créations de la tentative. Les anciens events
restent consultables et sans action jusqu'à revalidation Staff. Pour un event
expiré, la revalidation ne supprime rien : nettoyage confirmé séparément.
TASK-005 : identité et droits revérifiés, lectures explicitement recensées, toutes
mutations confirmées, nonces liés au contexte et IDs, usage unique et TTL 300s,
contrôles des commandes réellement exécutés, historique toujours non fiable.
TASK-006 : suppression de la fusion de fiches, conservation des IDs et recrues,
lecture sans mutation, guilde requise, snapshots vides valides, départ étranger ignoré.
TASK-007 : réservation MP atomique avant instructions, routage immuable avant
distribution, IA/accueil suspendus, libération conditionnelle au jeton dans finally.
TASK-008 : retirer CODEX.ZIP et temp_players_data.json, exclusions et scan de secrets.
TASK-009 : tests avec SDK réels, PostgreSQL jetable, CI stricte et documentation.

## 12. Configuration de référence
`.env.example` documente uniquement des placeholders. Les secrets et données de
production ne sont jamais nécessaires aux tests ni publiés.

## 13. Stratégie de tests
Suite de sécurité isolée avec les SDK réels ; seules les API distantes sont mockées.
Couverture : guilde étrangère et cache vide, DNS privé/mixte et redirections,
preuves modifiées et ressources homonymes, roundtrips de stockage réels, matrice
permissions/confirmations IA, non-réattribution joueurs, concurrence/routage MP,
HTTP compressé et cycle de santé. Suite historique et Monte-Carlo conservées.

## 14. Critères d'acceptation
Chaque refus produit zéro mutation ; les parcours autorisés fonctionnent ; les
régressions disposent de preuves ; les graphes installés ne présentent aucun avis
connu ; aucune suppression d'ancien événement n'est implicite.

## 15. Compatibilité et migrations
Pas de migration multi-guilde. Préserver propriétaires, recrues et sauvegardes.
Provenance additive aux événements, conversion idempotente, originaux conservés
jusqu'à sauvegarde confirmée. Une rotation de clé impose revalidation.

## 16. Sécurité et robustesse
Refus explicites et fermés sur erreurs de droits, provenance, configuration ou
stockage. Aucun secret, contenu de MP ni nonce utilisable dans les journaux.

## 17. Règles d'exécution pour Codex
Lire les instructions applicables, préserver les changements utilisateur et mener
chaque tâche jusqu'aux tests. Journaliser commandes et résultats. Statuts : RÉUSSI,
ÉCHOUÉ, NON EXÉCUTÉ, BLOQUÉ. Aucun contrôle non exécuté ne vaut réussite.

## 18. Gestion des écarts
Les adaptations transitives préservant contrats, sécurité et compatibilité sont
permises et documentées. Aucun contournement de test ou avis ignoré n'est accepté.

## 19. Matrice de traçabilité
| Exigence | Tâche | Validation |
|---|---|---|
| REQ-001 | 001 | Guilde étrangère refusée à toutes les entrées |
| REQ-002 | 003 | Transport/DNS et redirections filtrés |
| REQ-003 | 004 | Provenance, création unique et stockage réel |
| REQ-004 | 005 | Droits et confirmation à usage unique |
| REQ-005 | 006 | Aucun transfert entre IDs |
| REQ-006 | 007 | Concurrence et propriétaire immuable du MP |
| REQ-007 | 002 | Installations hachées et audits complets |
| REQ-008 | 008 | Inspection Git et scanner secrets |
| REQ-009 | 009 | Matrice Python complète et non-régression |

## 20. Definition of Done
Ne déclarer terminé que lorsque chaque exigence obligatoire a une preuve, que la
CI et les audits sont verts sur les deux Python et que la procédure d'exploitation
est documentée. Un blocage n'est pas une exigence satisfaite.

## 21. Incertitudes restantes
Configuration, versions et données de production non inspectées. Les valeurs de
configuration et revalidations opérateur restent des étapes de déploiement.

## 22. Message de lancement Codex
Implémenter TASK-001 à TASK-009, vérifier chaque exigence, préserver les données et
signaler honnêtement les écarts. La publication GitHub est désormais autorisée
par la demande ultérieure de l'utilisateur ; le déploiement reste hors périmètre.
