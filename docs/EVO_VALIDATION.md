# Rapport de validation — Evo / Luna

## Adaptation à #console et conversations publiques — 15 septembre 2026

Le patch Luna intégré précédemment est adapté au stockage Discord du projet.
Le registre SQL et son script d'initialisation sont retirés. Les dépendances SQL
utilisées par les autres modules sont conservées.

### Couverture fonctionnelle

- Réservation confirmée dans un faux salon Discord avant toute génération ;
  règlement selon les tokens consommés, plafonds et quotas individuels.
- Concurrence locale, doublons, reprise après redémarrage, changement de mois,
  réservations non réglées, corruption, suppression et erreur d'écriture.
- Initialisation explicite du premier registre par un membre autorisé à gérer le
  serveur ; refus de réinitialisation et protection contre le nettoyage console.
- Mentions initiales, mention seule sans génération, suivi du dernier message,
  mention combinée à une réponse et isolation serveur/salon/membre.
- Réponses de conversation et erreurs publiques ; confirmations administratives
  éphémères. Ateliers privés `/exo` absents du catalogue d'outils.
- Salons publics par défaut, restriction facultative par IDs, exclusion des salons
  privés et de #console, détection automatique de l'unique serveur.
- Leadership vérifié avant écritures et génération, suspension après perte ou
  déconnexion, délai de 125 secondes après détection d'une ancienne instance.
- Verrou retrouvé au-delà des pages récentes et scan incrémental sans perte de
  messages après une erreur de pagination.

### Exécution locale

La suite complète du projet inclut `tests/` et `tests_evo/`, comme la CI.
Les transports OpenAI et Discord sont simulés : aucun coût de génération pour ces tests.

```text
python -m pytest -q -rs --tb=short --disable-warnings \
  -W error::pytest.PytestDeprecationWarning -p no:cacheprovider \
  --basetemp <répertoire temporaire neuf>
```

Sous Windows, l'exécution utilise `PYTHONUTF8=1` et des répertoires `TMP`/`TEMP`
isolés pour les exports UTF-8 et les fichiers temporaires. Le test historique de
`cogs.organisation`, ancien module retiré, reste ignoré.

Résultat final local : **2 067 tests réussis, 1 test historique ignoré** en
53,30 secondes avec Python 3.13.14. `git diff --check` passe également.
Les avertissements existants ne constituent pas des échecs. La CI exécute la
même découverte de tests sous Python 3.11 et 3.12.

La recette connectée utilise le service Render existant et le registre Discord ;
elle ne nécessite aucune base PostgreSQL. Les résultats de mise en service sont
rapportés séparément des tests simulés.

### Limites du contrôle

Le compteur reste une enveloppe applicative prudente, distincte de la facture
OpenAI. Il couvre Evo ; les autres modules ou programmes utilisant la même clé
restent hors compteur. Une réservation incertaine n'est pas effacée pour rendre
artificiellement du budget.

Le snapshot Discord suppose une seule instance active. Le délai de reprise et
les contrôles de leadership ne fournissent pas de transactions distribuées.
Les tests vérifient l'orchestration et les garde-fous, pas la justesse de toutes
les réponses futures du modèle ou l'exhaustivité des catalogues externes.

## Validation historique du patch initial

Avant cette adaptation, le patch `evolution_luna.patch` avait passé 1 993 tests
avec un test historique ignoré. Ce résultat concernait l'ancienne version SQL ;
il ne constitue pas la validation de l'implémentation actuelle.
