# Evo — assistant conversationnel Evolution

Patch préparé pour l'archive `discordEVO.zip` fournie. Python 3.11 ou plus récent.
Le code ajoute une IA en lecture seule ; il ne remplace pas les commandes classiques.

## 1. Ce qui est installé

`/evo question:...` répond en français, en texte, avec un ton de camarade de guilde.
Aucun panneau, bouton ou formulaire n'est nécessaire. Exemple :

```text
/evo question:Où drop cette ressource avec 435 PP ?
[réponse d'Evo]
[Répondre au dernier message d'Evo] Et avec 600 PP ?
```

Pour continuer sans `/evo`, utiliser **Répondre** sur la dernière réponse publique
adressée à soi, dans le même salon et dans les 15 minutes. Les messages ordinaires
du salon et les réponses aux conversations d'un autre membre ne sont pas analysés.
La simple mention `@Evolution` n'est pas un déclencheur dans ce patch.

`/evo question:Il me reste combien de puits ? prive:true` consulte son propre atelier
`/exo` en réponse éphémère. Continuer une discussion privée en répétant `/evo` avec
`prive:true`, dans le même salon. Les mémoires publique et privée sont séparées.

`/evo-oublier` efface son contexte du bot et annule sa demande en cours, sans
génération IA. Les messages déjà publiés et les données déjà envoyées au fournisseur
ne sont pas supprimés par cette commande.

`/evo-budget` est réservé aux membres disposant de « Gérer le serveur ». Il affiche
le compteur, les réservations en attente, les tokens confirmés et l'activation
de Xixou, sans appeler le modèle. Aucune remise à zéro du compteur via Discord.

## 2. Les 17 outils

| Outil interne | Données utilisées / limites |
|---|---|
| `fiche_objet` | Fiche Moon-Bot, effets et détails Xixou lorsqu'ils sont disponibles. |
| `sources_drop` | Sources et zones Xixou ; calculs du `drop_calculator` existant. |
| `recette` | Recette du wiki, quantités multipliées en Python ; obtention des 8 premiers ingrédients au plus. |
| `monstre` | Grades du wiki et inventaire Xixou vérifié par identifiant ET nom. |
| `chercher_equipements` | Index d'effets Xixou rapproché du wiki ; 5 résultats maximum. |
| `comparer_objets` | Fiches de 2 ou 3 équipements, pas de prix inventés. |
| `candidats_exo` | Heuristique de remontage : nombre de lignes, puis lignes lourdes. |
| `ma_session_fm` | Uniquement l'atelier `/exo` actif du demandeur, et uniquement en privé. |
| `guide_fm` | Poids nominaux et limites du moteur existant, sans taux Ankama prétendument certifié. |
| `guilde` | Nom, description, nombre de membres Discord et salons publics accessibles. |
| `connaissances_guilde` | Faits publics validés dans `config/evo_knowledge.json`. |
| `membre` | Profil, personnages et métiers déclarés ; consentement d'administration pour les autres membres. |
| `artisans` | Métiers déclarés des membres encore présents sur le serveur. |
| `activites` | Sorties à venir effectivement publiées ; aucun brouillon ni audience Staff différente. |
| `conversation_salon` | Au plus 15 messages récents du salon courant, après activation explicite. |
| `aide_bot` | Commandes pertinentes déjà enregistrées ; aucune exécution automatique. |
| `demander_precision` | Demande une précision sans deuxième génération inutile. |

Les outils ne lancent pas des chaînes de commandes Discord. Ils utilisent les
services métier existants, avec paramètres stricts et contrôles Python indépendants
du texte généré. Evo n'a ni outil Staff, ni shell, ni SQL libre, ni lecture de fichiers
au choix du modèle, ni URL arbitraire, ni outil de modération.

Les bases historiques `PlayersCog` et `JobCog` sont globales au bot. Leur consultation
par Evo est donc volontairement coupée si le bot est connecté à plusieurs guildes :
cela évite de présenter les données d'un autre serveur comme celles d'Evolution.
Les profils du module `ProfilCog` restent cloisonnés par guilde et propriétaire.

## 3. Installation du patch

Depuis la racine du projet correspondant exactement au ZIP :

```bash
git switch -c feature/evo-luna
git apply --check evolution_luna.patch
git apply evolution_luna.patch
python -m unittest discover -s tests_evo -v
```

Le patch ne modifie pas `requirements.txt` : `aiohttp`, `asyncpg`, `rapidfuzz` et
`discord.py` sont déjà déclarés. Il emploie les endpoints HTTP officiels Responses
avec `aiohttp` pour maîtriser les relances, sans dépendre d'une version particulière
du SDK OpenAI.

Aucune génération OpenAI n'est lancée par ces tests. Les tests d'enregistrement
Discord n'établissent pas de connexion Discord, mais nécessitent `discord.py`.
Ils sont explicitement ignorés lorsque ce paquet est absent.

Le modèle est strictement `gpt-5.6-luna`, avec `reasoning.effort=none`. L'alias est
vérifié et aucun modèle de remplacement n'est choisi si son accès échoue.

## 4. Prérequis indispensable : le registre de budget

Sur Render, une base **PostgreSQL durable** est obligatoire. Le disque d'un service
gratuit Render est éphémère : un compteur JSON/SQLite local ne constitue pas une
protection fiable entre redéploiements.

Le module utilise `EVO_DATABASE_URL`, ou `DATABASE_URL` si la première variable est
vide. Il ne change pas les tables des autres fonctions du bot. Deux tables sont
ajoutées : `evo_budget_buckets` et `evo_budget_reservations`.

Les éventuels frais de la base ne sont pas inclus dans le budget OpenAI. Vérifier
les limites et la durée de conservation du plan choisi : le PostgreSQL gratuit
Render documente notamment une expiration après 30 jours. Une base expirée fait
arrêter Evo ; elle ne doit pas être recréée vide pour contourner le compteur.

### Initialisation unique

Renseigner le DSN dans l'environnement, puis exécuter **une seule fois** :

```bash
python -m scripts.evo_init_budget --postgres
```

Cette commande n'appelle pas OpenAI. Elle refuse un registre déjà existant, plutôt
que de l'écraser. Elle exige les droits SQL de création des deux tables. En
fonctionnement courant, Evo utilise seulement SELECT, INSERT et UPDATE sur ces
tables et ne recrée pas un registre disparu.

Sur un service Render gratuit sans shell, on peut utiliser temporairement le build
suivant, avec `EVO_ENABLED=0` pendant cette initialisation :

```bash
pip install -r requirements.txt && python -m scripts.evo_init_budget --postgres
```

Dès que cette initialisation a réussi, remettre le build habituel
`pip install -r requirements.txt`, puis activer Evo et redéployer. **Ne pas conserver
l'initialiseur dans le build ni dans la commande de démarrage** : son refus d'un
registre existant ferait échouer les builds suivants. Autre possibilité : exécuter
l'initialiseur depuis un poste pouvant accéder à la base avec son DSN externe.

Ne jamais mettre un DSN ou une clé dans le dépôt, dans une question Discord ou dans
une capture. Utiliser l'environnement du processus. L'initialiseur n'affiche pas le
DSN ni le détail brut d'une erreur de connexion.

Conserver les sauvegardes du registre. Restaurer une sauvegarde trop ancienne,
supprimer les tables ou changer de base ferait perdre des dépenses enregistrées :
aucun compteur local ne peut garantir la facture après cette intervention.

## 5. Variables Render

La clé `OPENAI_API_KEY` existante est réutilisée. Ne pas la recopier dans le code.

```dotenv
EVO_ENABLED=1
EVO_MODEL=gpt-5.6-luna
EVO_GUILD_ID=REMPLACER_PAR_ID_DU_SERVEUR
EVO_CHANNEL_IDS=REMPLACER_PAR_ID_DU_SALON

# Renseigner le DSN dans Render, ou utiliser DATABASE_URL déjà configurée.
EVO_DATABASE_URL=REMPLACER_PAR_DSN_POSTGRESQL

EVO_MONTHLY_USD=2.00
EVO_DAILY_USD=0.12
EVO_REQUEST_USD=0.015
EVO_MAX_INPUT_TOKENS=7500
EVO_MAX_OUTPUT_TOKENS=600
EVO_MAX_MODEL_CALLS=3
EVO_MAX_TOOL_CALLS=5
EVO_USER_DAILY_CALLS=60
EVO_COOLDOWN_SECONDS=12

# 1 après information des membres pour les profils et métiers déclarés.
EVO_PUBLIC_MEMBER_DATA=1
# Vide par défaut : aucune lecture des conversations du salon.
EVO_HISTORY_CHANNEL_IDS=

# Les anciennes IA ne passent pas par le compteur Evo.
ENABLE_AI_COMMANDS=0
EVO_ALLOW_LEGACY_AI=0
```

Remplacer les valeurs `REMPLACER_...` : elles ne sont pas des identifiants valides.
Plusieurs salons : IDs séparés par des virgules. Seuls les salons textuels de la
guilde configurée sont acceptés ; MP, threads et forums sont exclus de cette V1.

La politique des anciennes IA est modifiée de façon explicite : tant que
`EVO_ENABLED=1`, les fournisseurs gérés par `ai_service_enabled` restent désactivés
sauf consentement supplémentaire `EVO_ALLOW_LEGACY_AI=1`. Garder ce dernier à 0.
Cette protection ne couvre ni un autre programme utilisant la même clé, ni un
module personnalisé ne passant pas par cette politique.

L'encyclopédie de base réutilise `DofusWikiCog`. Les drops enrichis, zones et
classements globaux par caractéristiques nécessitent **`XIXOU_API_KEY`**, déjà
utilisée par le projet. Sans elle ou si le catalogue est indisponible, Evo le
signale ; il ne fabrique pas une liste d'équipements ou un taux de drop.

`OPENAI_BASE_URL`, les réglages des anciennes IA et leurs modèles ne sont pas
réutilisés. La clé ne part que vers `https://api.openai.com/v1`, sans redirection.
Un éventuel `OPENAI_PROJECT` ou `OPENAI_PROJECT_ID` doit correspondre au projet de la
clé. Aucun appel n'est fait à un serveur IA tiers.

Les intents Discord « Server Members » et « Message Content » doivent être
autorisés pour l'annuaire complet et les suivis par réponse au message. `/evo`
lui-même utilise l'interaction slash, pas la lecture générale des messages.
Conserver la synchronisation slash existante du bot ; le cog est chargé avant
l'adaptateur `slash_commands`.

## 6. Protection financière

**2,00 USD n'est pas 2,50 EUR.** Le défaut est volontairement prudent. Les taxes,
conversions monétaires et dépenses d'autres modules/projets sont hors compteur.
Les mois et jours du compteur sont en UTC, pas à minuit heure française.

Le budget affiché est une enveloppe de sécurité, pas la facture du fournisseur.
Tarifs de référence vérifiés le 15 septembre 2026 : Luna standard à 0,20 USD/M
tokens d'entrée et 1,20 USD/M en sortie. Le code compte **toutes** les entrées au
tarif conservateur d'écriture de cache de 0,25 USD/M, plus **15 % de marge** sur
l'ensemble. Les tarifs doivent être revérifiés lors d'une mise à jour du modèle.
Aucun gain de cache n'est nécessaire pour respecter le compteur.

Avant chaque génération :
1. Le compteur durable est consulté.
2. `/responses/input_tokens` compte l'entrée complète, y compris instructions et
   schémas des outils ; pas d'approximation « caractères / 4 ».
3. Le coût maximal de l'entrée comptée (+64 tokens de marge technique) et de toute
   la sortie autorisée est réservé atomiquement.
4. Seulement après le commit, le bot envoie `/responses`.
5. L'usage retourné ajuste la réservation ; une anomalie supérieure au maximum
   prévu bloque le mois pour contrôle.

Trois générations et cinq outils maximum par demande, 600 tokens de sortie par
génération, 7 500 tokens d'entrée, 110 secondes de traitement, deux demandes
simultanées globalement, une par membre et 12 secondes entre ses demandes.
`EVO_USER_DAILY_CALLS=60` signifie **60 générations**, pas 60 conversations :
une demande peut consommer plusieurs générations.

Un timeout, une annulation, une erreur HTTP ou l'absence de compteurs d'usage
conserve le maximum réservé par prudence. C'est volontairement pessimiste,
notamment sur un refus 401/404/429 qui peut ne rien coûter au fournisseur.
Aucune relance réseau automatique, aucun changement automatique de modèle, aucun
agent de fond, aucun outil OpenAI hébergé payant. Si le comptage préalable est
indisponible, la génération est refusée.

Une anomalie de comptage, des appels déjà en vol à une frontière de mois, une
modification tarifaire ou une intervention sur la base empêchent de promettre un
plafond de facture au centime près. Ajouter une limite de dépense sur un **projet
OpenAI dédié**, désactiver la recharge automatique si elle n'est pas souhaitée, et
surveiller le tableau de facturation. Une simple alerte de budget ne suffit pas ;
la documentation fournisseur précise aussi un possible délai d'application.

Le contexte est limité à deux échanges précédents, quelques références de
résultats et 15 minutes d'inactivité. Il n'y a ni embeddings, ni base vectorielle,
ni recherche web facturée. Les catalogues HTTP et leurs caches déjà présents sont
réutilisés. Les petits messages « salut », « merci » et « confidentialité » sont
traités localement sans génération.

## 7. Connaissances publiques de la guilde

Le fichier livré est vide pour ne rien inventer. Renseigner des informations
**publiques**, exactes et approuvées par le Staff :

```json
{
  "guild_id": "ID_DU_SERVEUR",
  "updated_at": "2026-09-15",
  "facts": [
    {
      "title": "Organisation des sorties",
      "keywords": ["sortie", "organisation", "inscription"],
      "text": "REMPLACER PAR UNE INFORMATION RÉELLE VALIDÉE PAR LE STAFF."
    }
  ]
}
```

La fiche est refusée si son `guild_id` ne correspond pas. Au plus 4 faits pertinents
sont fournis au modèle ; pas de réinjection du document complet à chaque question.
Ne pas y mettre des avertissements, notes Staff, adresses personnelles, mots de
passe, anecdotes privées ou interprétations sur la personnalité des membres.

`EVO_PUBLIC_MEMBER_DATA=1` autorise la consultation des informations Dofus déjà
déclarées dans les profils et métiers du bot. Il n'autorise pas les tickets, MP,
messages supprimés, logs de modération, historique de présence ou statistiques
globales pouvant agréger des salons privés.

Pour le résumé du salon, ajouter uniquement les salons convenus dans
`EVO_HISTORY_CHANNEL_IDS`, qui doit être un sous-ensemble de `EVO_CHANNEL_IDS`.
L'outil ne lit que le salon courant et revérifie les permissions de lecture de
l'historique pour le demandeur et le bot. Les sources d'un autre salon privé ne
sont pas recopiées dans une réponse publique.

### Information à publier aux membres

> Evo est un assistant IA. Tes questions, le contexte court de ta discussion avec
> lui et les résultats nécessaires des outils sont transmis à OpenAI. Ne lui envoie
> pas de secrets ni d'informations sensibles. Les réponses sont publiques sauf
> `prive:true`. `/evo-oublier` efface son contexte dans le bot. La lecture de
> l'historique n'est possible que dans les salons expressément annoncés par le Staff.

`store=false` est utilisé pour les Responses. Ce réglage **ne constitue pas une
garantie de zéro conservation chez OpenAI** ; les politiques de données et les
éventuels journaux de sécurité du fournisseur restent distincts. Les logs ajoutés
par ce patch ne contiennent ni texte de conversation, ni clé, ni DSN. Le registre
SQL conserve des coûts et des identifiants techniques ; les membres y sont
pseudonymisés par hachage, ce qui ne doit pas être présenté comme une anonymisation
irréversible. Les anciens modules du bot conservent leurs propres politiques.

## 8. Qualité des conseils et limites assumées

- Les taux, quantités et le puits proviennent du code/données, pas d'un calcul mental
  du modèle. Un taux individuel conditionnel n'est pas un rendement de farm horaire.
- « Meilleur équipement » correspond au tri des jets maximums selon les priorités
  explicites. Ce n'est pas un solveur de stuff global intégrant panoplies, conditions,
  classe, prix, exos et toutes les contraintes de personnage.
- « Plus facile à exo » est une heuristique explicable de remontage, pas un taux de
  passage supérieur ni un score scientifique. Les objets avec bonus déjà natif ou
  effets non interprétés sont écartés de ce classement.
- Le simulateur FM est estimatif ; ses probabilités ne sont pas des données Ankama
  certifiées. Puits inconnu reste inconnu.
- Aucune recherche Internet générale, actualité Ankama, cotation HDV ou navigation
  libre n'est ajoutée. Aucun coût `web_search` caché.
- Le modèle peut encore mal interpréter une question ou un résultat. Les tests
  automatiques vérifient l'orchestration et les garde-fous, pas la justesse de toutes
  ses futures recommandations. Comparer les premiers résultats à `/objet` et `/exo`.
- Le bot ne réalise aucune inscription, modification de profil, sanction ou action
  Staff. Un membre doit employer la commande Discord autorisée pour agir.

## 9. Recette de validation après déploiement

Commencer dans un seul salon avec peu de membres et conserver les plafonds par défaut.

| Essai | Résultat attendu |
|---|---|
| `/evo-budget` en Staff | Compteur lisible, pas de génération ni remise à zéro. |
| La même commande sans permission | Accès refusé. |
| `/evo question:salut` | Réponse locale, aucune consommation de génération. |
| Demander le drop d'une ressource connue | Source exacte ; comparer avec `/objet`. |
| Répondre « et avec 600 PP ? » | Même ressource, nouveau calcul PP. |
| Demander une coiffe terre niveau 120 | Critères et limites explicites, uniquement objets présents. |
| Demander le plus simple à exo PA | Heuristique de remontage ; pas de probabilité inventée. |
| Demander sa session FM en public | Invitation à passer `prive:true`, aucun détail privé affiché. |
| Demander qui exerce un métier | Membres actuels déclarés uniquement, si opt-in activé. |
| Demander un résumé dans un salon non autorisé | Refus de lire son historique. |
| Un autre membre répond au message d'Evo | Aucune reprise de la mémoire du premier membre. |
| Demander un rôle ou une sanction | Aucune action ; explication/commande manuelle éventuelle. |
| `/evo-oublier`, puis question de suivi | Ancien contexte absent. |
| Redémarrer Render, consulter `/evo-budget` | Dépenses inchangées ; contexte conversationnel volatil perdu. |

Ne pas tester le plafond en le remplissant réellement de requêtes payantes : les
tests locaux de concurrence, doublon, erreur et limite simulent ces situations.
Pour vérifier PostgreSQL, utiliser une base de test distincte, jamais supprimer
le registre de production.

Désactivation : `EVO_ENABLED=0`, conserver `ENABLE_AI_COMMANDS=0`, redéployer et
resynchroniser les commandes. Garder le registre pour toute réactivation du même
mois. Une demande déjà envoyée à OpenAI ne peut pas être « dé-facturée ».

## 10. Architecture et maintenance

`evo.py` : commandes, suivi par réponse Discord, permissions, concurrence et mémoire.
`utils/evo_agent.py` : boucle Responses/function calling, comptage et transport bornés.
`utils/evo_budget.py` : registre PostgreSQL, transactions, réservations et SQLite local.
`utils/evo_tools.py` : adaptateurs métier en lecture seule.
`utils/evo_equipment.py` : index et classements déterministes.
`utils/evo_safety.py` : schémas, bornes, sources, redaction et permissions.
`utils/evo_config.py` : configuration stricte, opt-in indépendant.
`utils/xixou_api.py` : petits adaptateurs publics ajoutés, clients existants conservés.
`main.py` et `utils/command_policy.py` : chargement natif et séparation des anciennes IA.
`tests_evo/` : tests hors ligne dédiés ; `docs/EVO_VALIDATION.md` décrit ce qui a été vérifié.

Pour développer localement, hors Render uniquement :

```bash
python -m scripts.evo_init_budget /chemin/absolu/evo.sqlite3
```

Configurer ensuite `EVO_SQLITE_PATH` avec ce fichier existant, et laisser les DSN
PostgreSQL vides. Le fichier ne doit pas être supprimé pour regagner du budget.
Ce mode sert aux essais ; la production Render utilise PostgreSQL.

## Références techniques consultées

- Modèle et function calling : https://developers.openai.com/api/docs/models/gpt-5.6-luna
- Boucle d'outils : https://developers.openai.com/api/docs/guides/function-calling
- Comptage exact de l'entrée : https://developers.openai.com/api/docs/guides/token-counting
- Tarifs : https://developers.openai.com/api/docs/pricing
- Limites de dépenses : https://developers.openai.com/api/docs/guides/spend-limits
- Contrôles de données : https://developers.openai.com/api/docs/guides/your-data
- Render gratuit : https://render.com/docs/free
- Interactions Discord : https://discordpy.readthedocs.io/en/stable/interactions/api.html

Les limites et tarifs du fournisseur doivent être revérifiés lors des mises à jour.
