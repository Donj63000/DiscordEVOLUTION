# Correctif Evo : métiers et disponibilité du Web

Date : 16 septembre 2026.

## Base et portée

Ce correctif s'applique à l'archive `DiscordEVOLUTION-main (11)(1).zip`
**après** application de `evolution-evo-ai-upgrade.patch`. Ce n'est pas un
remplacement de la première mise à niveau. Il conserve le modèle, les
dépendances, les écritures métiers/activités et le registre budgétaire.
Il ne contient ni secret ni données du serveur.

La capture seule ne permet pas de déterminer l'état du cache, les clés du
registre, les arguments transmis par le modèle ou la configuration réellement
déployée. Les défauts ci-dessous sont en revanche présents dans cette base et
les deux chemins métiers ont été reproduits avec des données synthétiques.

## 1. Pourquoi /evo pouvait annoncer zéro mineur 100

Dans l'outil `artisans`, les données provenaient directement de
`JobCog.jobs_data`, sans passer par la synchronisation console utilisée par
`/job rechercher`. Ensuite, tout identifiant absent de `guild.get_member()`
était écarté. Cette méthode consulte un cache : un résultat absent ne prouve
pas que la personne a quitté le serveur.

La commande native pouvait afficher les déclarations persistées, alors que
l'IA recevait une liste vide après ce filtrage. Les anciennes fiches indexées
par pseudo plutôt que par identifiant Discord étaient également ignorées.
Enfin, aucune information de couverture ne distinguait une absence vérifiée
d'une lecture incomplète.

### Correction

`JobCog.read_jobs_snapshot()` fournit une copie isolée du registre sous le
verrou de mutation, après le rafraîchissement existant. `/job rechercher`,
`artisans` et `liste_metiers` utilisent ce service. Le délai de réutilisation
existant (`JOB_CONSOLE_SYNC_TTL`, 30 secondes par défaut) reste en place :
il ne s'agit pas d'une lecture distante forcée à chaque message.

Pour Evo, une absence du cache déclenche une vérification HTTP ciblée via
`fetch_member`, uniquement pour les déclarations répondant à la recherche.
Le bot ne télécharge pas tous les membres. Une réponse `Unknown Member`
(code 10007) confirme l'absence ; une permission refusée, une autre erreur
HTTP, une rupture réseau ou un délai dépassé laisse la déclaration non vérifiée.

Les lectures d'une même demande partagent leur snapshot et leurs vérifications.
Il y a au maximum 40 vérifications HTTP, avec 3 en parallèle, 4 secondes par
appel et une fenêtre globale de 8 secondes pour ces vérifications. La lecture
du snapshot est séparément bornée à 8 secondes. Les tâches sont annulées et
attendues à la sortie ; aucun travail réseau ne continue volontairement après
la demande. Les accès sont revérifiés après les attentes.

Les résultats exposent `verification_complete`, `declarations_non_verifiees`,
`lignes_invalides` et, pour les artisans, `absence_confirmee`. Le total compte
les résultats vérifiés, pas les déclarations dont l'appartenance reste inconnue.
Les alias sont rapprochés du catalogue métier et les doublons d'un même
membre/métier sont éliminés. Le niveau minimal est inclusif.

Pour des consultations simples telles que « Y a-t-il des mineurs 100 ? »,
le rendu copie directement les résultats de l'outil : aucune rédaction ni
comptage de tokens OpenAI n'est appelé pour cette réponse. Les contrôles
habituels d'accès, de configuration et d'admission d'Evo restent applicables.
Les demandes complexes continuent dans la boucle IA, avec consignes explicites
sur les erreurs et la couverture ; ce n'est pas une garantie d'infaillibilité
du modèle sur toutes les formulations libres.

### Données anciennes et limites

Les fiches sans identifiant Discord fiable ne sont pas attribuées à quelqu'un
sur la seule ressemblance d'un pseudo. Les niveaux mal typés ou hors limites
ne sont pas corrigés silencieusement. Ces déclarations sont signalées : faire
vérifier et rattacher les fiches par les commandes métiers habituelles, sous
contrôle du Staff et du propriétaire. Ce patch ne réécrit pas le registre.

La commande native reste une consultation des déclarations enregistrées ;
Evo exige en plus une appartenance vérifiable. Une différence reste donc
légitime pour d'anciens membres ou des fiches non rattachées, mais elle doit
être expliquée, et non transformée en « personne n'a déclaré ce métier ».

La restriction à un seul serveur est conservée pour l'annuaire historique,
dont le format n'est pas cloisonné par serveur. Le consentement à l'annuaire
reste obligatoire. La disponibilité pour jouer ou fabriquer un objet n'est
pas déduite du métier déclaré.

## 2. Pourquoi le réglage Web bloquait toute l'IA

`EvoConfig.from_env()` levait une erreur globale si le Web était activé avec
un plafond par demande inférieur à 0,03 USD ou moins de trois générations
normales. Le défaut par demande étant de 0,015 USD, activer seulement
`EVO_WEB_SEARCH_ENABLED=1` suffisait à produire le message de la capture.

La configuration générale peut désormais être chargée dans ce cas. Seule la
recherche Web est indisponible pour le mode concerné ; les outils locaux
restent utilisables. Le catalogue n'expose pas un outil Web inexécutable, une
instruction interne prévient le modèle, et `/evo-budget` affiche la raison
séparément pour le mode normal et le mode approfondi.

Aucun budget ni nombre d'appels n'est augmenté automatiquement.
Les véritables erreurs de configuration (valeur non numérique, option invalide,
plafond requête supérieur au plafond journalier, etc.) restent bloquantes.
Les réservations, le contrôle réel des dépenses et les quotas d'appels restent
obligatoires. Une configuration Web éligible ne garantit pas qu'une demande
sera réalisable avec le budget restant.

## 3. Appliquer et déployer

Dans une copie de travail sauvegardée, à la racine du dépôt contenant déjà
la première mise à niveau :

```bash
git switch -c correctif-evo-metiers-web
git apply --check evolution-evo-metiers-web-hotfix.patch
git apply evolution-evo-metiers-web-hotfix.patch
```

Adapter le chemin du patch. Si `--check` échoue, ne pas forcer avec `--reject` :
vérifier la révision et les modifications locales. Depuis l'archive d'origine
non modifiée, appliquer d'abord `evolution-evo-ai-upgrade.patch`.

Enregistrer les fichiers modifiés dans votre dépôt, déployer la branche
utilisée par l'hébergement, puis redémarrer le bot. Le patch n'altère pas les
variables déjà enregistrées dans Render.

Pour autoriser uniquement l'annuaire, sans ouvrir les autres profils :

```dotenv
EVO_PUBLIC_JOB_DATA=1
EVO_PUBLIC_MEMBER_DATA=0
```

Aucun accès Internet ni relèvement de budget n'est nécessaire pour consulter
cet annuaire. Pour activer volontairement la recherche Web en mode normal :

```dotenv
EVO_WEB_SEARCH_ENABLED=1
EVO_REQUEST_USD=0.03
EVO_MAX_MODEL_CALLS=3
```

Le mode approfondi utilise son propre `EVO_MAX_DEEP_MODEL_CALLS`, également au
moins égal à 3 pour proposer le Web. Garder les plafonds journalier/mensuel
choisis par le Staff et la hiérarchie requête <= jour <= mois.
Il est possible de garder le Web désactivé avec `EVO_WEB_SEARCH_ENABLED=0`.

Vérifier également `ENABLE_MEMBERS_INTENT=1` (déjà la valeur par défaut dans
`main.py`) et l'activation correspondante de **Server Members Intent** dans
le portail développeur Discord. L'autorisation doit être disponible des deux
côtés ; pour les applications soumises à approbation, respecter cette procédure.
Le correctif ne suppose toutefois plus que le cache contient tous les artisans.
Il ne nécessite pas d'activer Presence Intent pour deviner une disponibilité.

Ne pas effacer `===BOTJOBS===`, les déclarations métier ou `===BOTEVOBUDGET===`.
Ne pas réinitialiser le compteur budgétaire pour contourner le message Web.
Ce correctif n'introduit aucune migration de données.

## 4. Vérifier après redémarrage

Comparer `/job rechercher Mineur` et une demande `/evo` contenant
« Y a-t-il des mineurs 100 ? ». Les artisans effectivement vérifiables de
niveau 100 doivent apparaître ; les niveaux inférieurs ne doivent pas être
comptés. Une déclaration non vérifiable doit entraîner un avertissement.

Vérifier aussi une recherche inexistante, une recherche « tailleurs 100 »,
puis un ajout/retrait autorisé suivi d'une nouvelle consultation. Les écritures
ne sont pas modifiées par ce correctif et restent soumises à leurs validations.

Avec le Web activé mais le plafond à 0,015 USD, `/evo-budget` doit rester
accessible et signaler le Web indisponible. Une consultation métier autorisée
doit rester possible. Avec une configuration Web suffisante, vérifier une
recherche publique et ses sources, sous réserve des budgets et autorisations
fournisseur effectifs.

Les logs de lecture donnent :
`evo jobs read trigger_id=... verified=... unresolved=... invalid=... fetches=...`.
Ils permettent de distinguer résultats vérifiés et couverture incomplète,
sans journaliser les noms ni les secrets.

## 5. Tests et limites de validation

Les nouveaux tests sont dans `tests_evo/test_jobs_consistency_hotfix.py` et
`tests_evo/test_web_config_hotfix.py`. Les fixtures et certains tests métiers
existants ont été adaptés au service de snapshot.

Dans l'environnement normal du projet, avec les dépendances de test :

```bash
python -m pytest -q tests_evo/test_jobs_consistency_hotfix.py \
  tests_evo/test_web_config_hotfix.py tests/test_job_command.py
python -m pytest -q tests_evo
```

La validation de cette livraison est détaillée dans le rapport externe
`evolution-evo-metiers-web-validation.txt`. `discord.py` n'étant pas installé
dans l'environnement de travail, une doublure temporaire d'import Discord
a servi à exécuter la sélection de tests de logique, hors du patch.
Le vrai code de l'agent, du budget et des services métier est exécuté ; les
transports et les interfaces Discord sont simulés. Aucune validation réelle
du Gateway, des interactions, d'OpenAI ou de votre registre de production
n'a été effectuée. Il faut effectuer la recette après déploiement.

## Références techniques

Documentation officielle discord.py : cache des membres, Gateway Intents et
récupération ciblée des membres, consultée le 16 septembre 2026 :
`https://discordpy.readthedocs.io/en/stable/intents.html`

Documentation Discord : distinction des codes Unknown Member / Unknown Guild,
consultée le 16 septembre 2026 :
`https://docs.discord.com/developers/topics/opcodes-and-status-codes`

Les seuils financiers mentionnés ici sont des règles applicatives du bot,
pas une affirmation du tarif courant du fournisseur.
