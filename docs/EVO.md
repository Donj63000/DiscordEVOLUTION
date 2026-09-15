# Evo — assistant conversationnel Evolution

Python 3.11 ou plus récent. Persistance du compteur dans le salon `#console` existant.
Evo consulte les outils du bot et effectue les modifications personnelles explicitement
demandées. Les commandes classiques restent disponibles.

## 1. Ce qui est installé

`/evo question:...` répond en français, en texte, avec un ton de camarade de guilde.
Aucun panneau, bouton ou formulaire n'est nécessaire. Exemple :

```text
/evo question:Où drop cette ressource avec 435 PP ?
[réponse d'Evo]
[Répondre au dernier message d'Evo] Et avec 600 PP ?
```

Pour continuer sans `/evo`, utiliser **Répondre** sur la dernière réponse d'Evo
adressée à soi, dans le même salon et dans les 15 minutes. Les messages ordinaires
du salon et les réponses aux conversations d'un autre membre ne sont pas analysés.
Une mention directe fonctionne également : `@Evolution Où drop cette ressource ?`.
Elle peut démarrer la première conversation, sans `/evo` préalable. Une mention seule
reçoit une invitation locale à poser une question, sans génération.

Toutes les réponses de conversation sont visibles par les personnes ayant accès au
salon, y compris les messages d'erreur. Les salons Staff sont acceptés, ainsi que les
fils, les publications de forum et les chats des salons vocaux. Le membre et le bot
doivent pouvoir lire et écrire ; un fil privé exige aussi leur appartenance au fil
ou la permission de gérer les fils. Les fils archivés doivent être rouverts.
`#console` et ses fils restent réservés au stockage. L'option `prive` est retirée.
Les ateliers personnels `/exo`
restent privés jusqu'au partage explicite de leur état courant. La mémoire est séparée
par serveur, salon et membre. Les mentions de rôle ou `@everyone` ne déclenchent rien.

`/evo-oublier` efface son contexte du bot et annule sa demande en cours, sans
génération IA. Les messages déjà publiés et les données déjà envoyées au fournisseur
ne sont pas supprimés par cette commande.

`/evo-budget` est réservé aux membres disposant de « Gérer le serveur ». Il affiche
le compteur, les réservations en attente, les tokens confirmés et l'activation
de Xixou, sans appeler le modèle. `/evo-budget initialiser:true` crée uniquement
le premier registre, après vérification de son absence. Un registre existant ou
illisible ne peut pas être remplacé. Les confirmations administratives restent
éphémères, tout comme celle de `/evo-oublier`.

### Rédaction et mode approfondi

L'IA rédige les réponses, y compris les salutations. Les outils calculent les taux,
quantités et comparaisons. Les suivis reconnus, comme « avec 600 PP » ou « j'en veux
5 », préparent directement les données puis demandent une seule rédaction IA.
Le nom et la référence vérifiés, la PP personnelle, la PP du groupe et le monstre
restent dans le contexte compact. Une confirmation du nom exact suffit ; les
suggestions approximatives demandent toujours un choix. Le nombre de membres du
serveur est lui aussi préparé directement, puis expliqué par l'IA en une génération.

Le niveau du personnage sert de plafond pour les équipements : un crâ niveau 200
peut recevoir une cape de niveau 191. Une plage d'objets explicitement demandée
reste respectée. Une famille comme « Plumes de Piou » présente jusqu'à six variantes
exactes et leurs zones, avec possibilité de préciser la couleur ensuite.

`/evo question:... approfondir:true` ou un message commençant par « approfondis »
autorise un spécialiste supplémentaire. Il reçoit un petit ensemble de faits et
rend une note de conseil au rédacteur, sans outils, écriture ou sous-délégation.
Ce mode est facultatif et partage exactement le même budget par demande. Une
demande simplement longue ou difficile ne l'active pas automatiquement.

### Actions personnelles et partage Exo

Evo peut inscrire ou désinscrire le demandeur d'une activité publiée, et ajouter,
actualiser ou supprimer ses métiers. Les ambiguïtés demandent une précision ; les
conditions des commandes natives, y compris la liste d'attente, restent applicables.
Les changements de métiers et d'activités ne sont confirmés qu'après sauvegarde
vérifiée dans `#console`. Une seule modification personnelle est permise par demande.

`/evo-exo partager:true` partage l'état courant de son atelier dans le salon courant.
L'accord est lié à l'atelier et à sa révision, expire au plus après 15 minutes et
est révoqué par `/evo-exo partager:false`, `/evo-oublier` ou un redémarrage. Une
modification effectuée dans le panneau privé exige un nouveau partage. Une rune
demandée à Evo dans sa propre simulation autorise son résultat et avance le partage
à cette nouvelle révision. Aucun export privé complet n'est transmis au modèle.
Les états Exo ne sont pas conservés dans l'historique conversationnel d'Evo.

Le rédacteur reçoit le résultat réellement confirmé. Si la génération échoue après
l'action, un reçu technique est publié sans rejouer l'action. Une erreur incertaine
d'édition du panneau Exo bloque le partage jusqu'à resynchronisation ou réouverture.

## 2. Les outils

| Outil interne | Données utilisées / limites |
|---|---|
| `fiche_objet` | Fiche Moon-Bot, effets et détails Xixou lorsqu'ils sont disponibles. |
| `sources_drop` | Sources et zones Xixou ; calculs du `drop_calculator` existant. |
| `recette` | Recette intégrale calculée en Python, affichée et enrichie par pages de 8 ingrédients. |
| `monstre` | Grades du wiki et inventaire Xixou vérifié par identifiant ET nom ; drops paginés. |
| `chercher_equipements` | Index d'effets Xixou rapproché du wiki ; 5 résultats maximum. |
| `comparer_objets` | Fiches de 2 ou 3 équipements et écarts de jets calculés en Python. |
| `candidats_exo` | Heuristique de remontage, également sur les objets explicitement sélectionnés. |
| `guide_fm` | Poids nominaux et limites du moteur existant, sans taux Ankama prétendument certifié. |
| `ma_session_fm` / `poser_rune` | État partagé et rune unique dans sa simulation personnelle. |
| `guilde` | Nom, description, nombre de membres Discord et salons publics accessibles. |
| `connaissances_guilde` | Faits publics validés dans `config/evo_knowledge.json`. |
| `membre` | Profil, personnages et métiers déclarés ; consentement d'administration pour les autres membres. |
| `artisans` | Métiers déclarés des membres encore présents sur le serveur. |
| `liste_metiers` | Liste paginée des métiers déclarés, nombre d'artisans et niveau maximal enregistré. |
| `activites` | Sorties à venir effectivement publiées ; aucun brouillon ni audience Staff différente. |
| `inscrire_activite` / `desinscrire_activite` | Inscription personnelle, règles natives et sauvegarde console. |
| `definir_mon_metier` / `supprimer_mon_metier` | Modification de ses métiers déclarés, avec sauvegarde vérifiée. |
| `conversation_salon` | Au plus 15 messages récents du salon courant, après activation explicite. |
| `aide_bot` | Commandes pertinentes déjà enregistrées ; aucune exécution automatique. |
| `demander_precision` | Demande une précision sans deuxième génération inutile. |

Les outils ne lancent pas des chaînes de commandes Discord. Ils utilisent les
services métier existants, avec paramètres stricts et contrôles Python indépendants
du texte généré. Evo n'a ni outil Staff, ni shell, ni SQL libre, ni lecture de fichiers
au choix du modèle, ni URL arbitraire, ni outil de modération.

Les fiches de monstres transmettent le niveau de chaque grade et les plages de
résistances calculées en Python. Les PV, PA ou PM absents de la source restent
explicitement inconnus. Pour le Crocabulia, le wiki fournit les niveaux 400 à 480
et les résistances, mais pas les PV/PA/PM ; réfléchir davantage ne complète pas
ces données manquantes.

Les bases historiques `PlayersCog` et `JobCog` sont globales au bot. Leur consultation
par Evo est donc volontairement coupée si le bot est connecté à plusieurs guildes :
cela évite de présenter les données d'un autre serveur comme celles d'Evolution.
Les profils du module `ProfilCog` restent cloisonnés par guilde et propriétaire.

## 3. Installation et tests

Depuis la racine du dépôt à jour :

```bash
pip install -r requirements.txt
python -m pytest
```

Evo réutilise `aiohttp`, `rapidfuzz` et `discord.py`, déjà déclarés. Les dépendances
SQL utilisées par d'autres modules restent installées. Evo emploie les endpoints HTTP officiels Responses
avec `aiohttp` pour maîtriser les relances, sans dépendre d'une version particulière
du SDK OpenAI.

Aucune génération OpenAI n'est lancée par ces tests. Les tests d'enregistrement
Discord n'établissent pas de connexion Discord, mais nécessitent `discord.py`.
La suite `tests_evo` est incluse dans la découverte pytest et dans la CI GitHub.

Le modèle est strictement `gpt-5.6-luna`, avec `reasoning.effort=medium` par défaut
pour l'analyse, la rédaction et le spécialiste. L'alias est vérifié et aucun modèle
de remplacement n'est choisi si son accès échoue. Ce temps de réflexion supplémentaire
ne remplace pas les vérifications des outils et ne garantit pas une réponse sans erreur.

## 4. Registre de budget dans #console

Le compteur est sauvegardé dans un message épinglé marqué `===BOTEVOBUDGET===`.
Le JSON passe en pièce jointe `evo_budget.json` lorsqu'il dépasse la taille d'un
message. Aucun fichier local et aucune base SQL ne font autorité pour Evo.

Le salon est résolu dans le serveur choisi, avec la priorité suivante :
`CHANNEL_CONSOLE_ID`, `CHANNEL_CONSOLE`, `CONSOLE_CHANNEL_NAME`, puis `console`.
Le nom `CONSOLE_CHANNEL_NAME` des installations existantes est donc réutilisé.

### Première activation

1. Mettre `EVO_ENABLED=1` dans Render et déployer le code à jour.
2. Exécuter `/evo-budget initialiser:true` avec la permission « Gérer le serveur ».
3. Vérifier `/evo-budget`, puis adresser une question publique au bot.

Le registre doit être accessible et épinglé. Le bot a besoin de lire l'historique,
d'envoyer des messages et pièces jointes, et de gérer les messages pour l'épinglage.
L'initialisation ne fait aucun appel OpenAI et refuse un registre existant ou
illisible. Le démarrage normal ne crée jamais un compteur vide.

Chaque réservation est sauvegardée et confirmée avant une génération payante.
Les écritures sont sérialisées dans l'instance ; la mémoire n'est mise à jour
qu'après confirmation. Le coût maximal reste compté après une erreur du fournisseur.
Un échec, timeout ou une annulation pendant la sauvegarde impose une relecture
avant toute nouvelle génération. Un message supprimé ou corrompu suspend Evo ;
restaurer le registre au lieu de recréer un budget vide.

Le nettoyage de console conserve le marqueur. Les mois précédents et leurs
réservations sont conservés ; une réponse tardive est réglée dans son mois d'origine.
Les écritures trop volumineuses sont refusées sans supprimer la sauvegarde existante.

### Une instance payante et reprise

Evo ne génère rien avant acquisition et vérification du verrou du bot dans
`#console`. Les contrôles sont refaits avant les écritures et avant la génération.
La déconnexion, une perte de verrou ou une lecture incertaine suspendent les demandes.

Si une ancienne instance est détectée au déploiement, Evo attend 125 secondes
(fenêtre maximale de traitement et heartbeat), revérifie le verrou puis restaure
le compteur. Les commandes classiques restent disponibles pendant cette attente.
Un snapshot Discord ne fournit pas de transaction distribuée entre plusieurs
instances actives : l'exploitation prévue utilise une seule instance payante.

## 5. Variables Render

La clé `OPENAI_API_KEY` existante est réutilisée. Ne pas la recopier dans le code.

```dotenv
EVO_ENABLED=1
EVO_MODEL=gpt-5.6-luna
EVO_REASONING_EFFORT=medium
# Facultatif si le bot appartient à un seul serveur.
EVO_GUILD_ID=
# Vide : toutes les discussions accessibles du serveur, sauf #console et ses fils.
EVO_CHANNEL_IDS=

EVO_MONTHLY_USD=2.00
EVO_DAILY_USD=0.12
EVO_REQUEST_USD=0.015
EVO_MAX_INPUT_TOKENS=7500
EVO_MAX_OUTPUT_TOKENS=400
EVO_ANALYSIS_MAX_OUTPUT_TOKENS=3000
EVO_WRITER_MAX_OUTPUT_TOKENS=3000
EVO_SPECIALIST_MAX_OUTPUT_TOKENS=1800
EVO_MAX_MODEL_CALLS=2
EVO_MAX_DEEP_MODEL_CALLS=3
EVO_MAX_TOOL_CALLS=5
EVO_USER_DAILY_CALLS=60
EVO_COOLDOWN_SECONDS=12

# 1 après information des membres pour les profils et métiers déclarés.
EVO_PUBLIC_MEMBER_DATA=1
EVO_PUBLIC_JOB_DATA=0
# Vide par défaut : aucune lecture des conversations du salon.
EVO_HISTORY_CHANNEL_IDS=

# Les anciennes IA ne passent pas par le compteur Evo.
ENABLE_AI_COMMANDS=0
EVO_ALLOW_LEGACY_AI=0
```

Si le bot appartient à plusieurs serveurs, renseigner `EVO_GUILD_ID`. Pour limiter
Evo à certains salons, renseigner leurs IDs séparés par des virgules dans
`EVO_CHANNEL_IDS`. L'ID d'un parent inclut ses fils accessibles. Cette liste ne
contourne jamais les permissions Discord et n'autorise pas `#console` ou ses fils.
Les messages privés restent exclus ; les fils et publications de forum accessibles
suivent les permissions décrites plus haut.

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
   la sortie autorisée est réservé sous verrou local puis sauvegardé dans `#console`.
4. Seulement après confirmation de la sauvegarde et nouvelle vérification du
   leadership, le bot envoie `/responses`.
5. L'usage retourné ajuste la réservation ; une anomalie supérieure au maximum
   prévu bloque le mois pour contrôle.

Deux générations normales, ou trois au total en mode approfondi, et cinq outils
maximum par demande. La réponse demandée reste courte : environ 400 tokens visibles,
et 250 pour la note du spécialiste. Avec `medium`, la limite API inclut aussi les
tokens de raisonnement : 3 000 pour l'analyse, 3 000 pour la rédaction et 1 800 pour
le spécialiste. Toute cette enveloppe est réservée avant génération ; le règlement
compte la sortie totale réellement consommée, raisonnement compris. Une réponse
interrompue par cette limite ne déclenche ni action partielle ni relance payante.
Les autres limites sont 7 500 tokens d'entrée, 110 secondes de traitement, deux demandes
simultanées globalement, une par membre et 12 secondes entre ses demandes.
Un membre déjà servi peut laisser une autre question en attente, pendant 120 secondes
au maximum, avec deux attentes au total. Chaque réponse reste dans le salon de la
question. L'attente ne lance aucune génération ; permissions, leadership et budget
sont revérifiés au démarrage. `/evo-oublier` annule aussi la demande en attente.
`EVO_USER_DAILY_CALLS=60` signifie **60 générations**, pas 60 conversations :
une demande peut consommer plusieurs générations.

Avant une mutation ou un spécialiste, la rédaction finale reçoit une réservation
durable couvrant son entrée maximale et sa sortie autorisée. Le spécialiste n'est
appelé que si l'enveloppe commune restante le permet ; sinon le rédacteur répond
en mode normal. Chaque rôle emploie le même registre, le même quota personnel et
un identifiant de réservation distinct. `EVO_MAX_OUTPUT_TOKENS` règle le souhait
de longueur visible, plafonné à 400, et non l'enveloppe de raisonnement. Le nombre
d'appels normaux reste plafonné à deux. `EVO_REASONING_EFFORT` accepte `none`, `low`
ou `medium` ; `none` reprend les petites enveloppes sans raisonnement.

Au tarif standard actuel, 1 000 tokens supplémentaires de raisonnement représentent
0,0012 USD de coût fournisseur, soit 0,12 USD pour cent réponses consommant chacune
ce supplément. Il s'agit d'un exemple, pas d'un surcoût fixe associé à `medium`.
Les plafonds mensuel, journalier et par demande restent inchangés.

Un timeout, une annulation, une erreur HTTP ou l'absence de compteurs d'usage
conserve le maximum réservé par prudence. C'est volontairement pessimiste,
notamment sur un refus 401/404/429 qui peut ne rien coûter au fournisseur.
Aucune relance réseau automatique, aucun changement automatique de modèle, aucun
agent de fond, aucun outil OpenAI hébergé payant. Si le comptage préalable est
indisponible, la génération est refusée.

Une anomalie de comptage, des appels déjà en vol à une frontière de mois, une
modification tarifaire ou une intervention sur le registre empêchent de promettre un
plafond de facture au centime près. Ajouter une limite de dépense sur un **projet
OpenAI dédié**, désactiver la recharge automatique si elle n'est pas souhaitée, et
surveiller le tableau de facturation. Une simple alerte de budget ne suffit pas ;
la documentation fournisseur précise aussi un possible délai d'application.

Le contexte est limité à deux échanges précédents, un bref structuré des références
et contraintes, et 15 minutes d'inactivité. L'ordre mémorisé correspond aux objets
effectivement nommés dans la réponse publiée. Il n'y a ni embeddings, ni base vectorielle,
ni recherche web facturée. Les catalogues HTTP et leurs caches déjà présents sont
réutilisés. Les petits messages « salut », « merci » et « confidentialité » nécessitent
une génération sans outils. Une mention seule et les erreurs techniques restent locales.

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

`EVO_PUBLIC_JOB_DATA=1` permet uniquement l'annuaire des métiers déjà déclarés,
comme « Qui est tailleur niveau 100 ? » ou « Liste les métiers de la guilde »,
sans activer la lecture des autres profils.
Ce réglage peut rester à 0 si `EVO_PUBLIC_MEMBER_DATA=1` autorise déjà cet annuaire.
La protection des données historiques lorsque le bot rejoint plusieurs serveurs
reste applicable aux deux réglages.

Pour le résumé du salon, ajouter uniquement les salons convenus dans
`EVO_HISTORY_CHANNEL_IDS`, qui doit être un sous-ensemble de `EVO_CHANNEL_IDS`
lorsque cette restriction supplémentaire est renseignée.
L'outil ne lit que le salon courant et revérifie les permissions de lecture de
l'historique pour le demandeur et le bot. Les sources d'un autre salon privé ne
sont pas recopiées dans une réponse publique.

### Information à publier aux membres

> Evo est un assistant IA. Tes questions, le contexte court de ta discussion avec
> lui et les résultats nécessaires des outils sont transmis à OpenAI. Ne lui envoie
> pas de secrets ni d'informations sensibles. Toutes les réponses sont publiques.
> `/evo-oublier` efface son contexte dans le bot. La lecture de
> l'historique n'est possible que dans les salons expressément annoncés par le Staff.

`store=false` est utilisé pour les Responses. Ce réglage **ne constitue pas une
garantie de zéro conservation chez OpenAI** ; les politiques de données et les
éventuels journaux de sécurité du fournisseur restent distincts. Les logs ajoutés
par Evo ne contiennent ni texte de conversation ni clé. Le registre dans `#console`
conserve des coûts et des identifiants techniques ; les membres y sont
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
- Evo modifie uniquement les inscriptions et métiers du demandeur, ainsi que sa
  simulation FM partagée. Les actions sur des tiers, sanctions et créations de
  sorties restent accessibles par les commandes natives avec leurs permissions.

## 9. Recette de validation après déploiement

Commencer dans un seul salon avec peu de membres et conserver les plafonds par défaut.

| Essai | Résultat attendu |
|---|---|
| `/evo-budget initialiser:true` en Staff, registre absent | Premier message épinglé créé, aucun appel OpenAI. |
| Répéter l'initialisation | Refus de remplacer le registre. |
| `/evo-budget` en Staff | Compteur lisible, pas de génération ni remise à zéro. |
| La même commande sans permission | Accès refusé. |
| `/evo question:salut` | Réponse rédigée par l'IA, une génération sans outils. |
| Mentionner le bot avec une question dès la première conversation | Réponse publique dans le salon. |
| Mentionner uniquement le bot | Invitation publique à poser une question, sans génération. |
| Répondre à Evo en le mentionnant aussi | Une seule réponse. |
| Mentionner Evo dans un salon Staff accessible | Réponse visible dans ce salon, contexte séparé des autres salons. |
| Mentionner Evo dans un fil privé accessible ou un chat vocal | Même conversation, selon les permissions du membre et du bot. |
| Mentionner Evo dans `#console` ou un de ses fils | Aucun traitement conversationnel. |
| Demander le drop d'une ressource connue | Source exacte ; comparer avec `/objet`. |
| Répondre « et avec 600 PP ? » | Même ressource, calcul Python puis une rédaction IA. |
| Après le Turquoise, « avec 515 de PP sur le CM, groupe à 3000 PP » | Référence conservée, deux PP distinctes, pas de confirmation répétée. |
| « Plumes de Piou, quelles zones ? », puis une couleur | Variantes exactes, puis détail de la couleur choisie. |
| « Cape pour crâ niveau 200 » | Recherche jusqu'au niveau 200, incluant les capes de niveau inférieur. |
| « Quelles stats et résistances du Crocabulia ? » | Niveau selon le grade, résistances vérifiées et valeurs absentes signalées. |
| « Combien de membres sur le Discord ? » | Nombre courant fourni par Discord, puis rédaction IA. |
| Demander une coiffe terre niveau 120 | Critères et limites explicites, uniquement objets présents. |
| Demander le plus simple à exo PA | Heuristique de remontage ; pas de probabilité inventée. |
| Demander sa session FM sans partage | Invitation à partager explicitement, aucun détail privé consulté. |
| Partager puis demander son puits | Valeur exacte, uniquement dans le salon du partage. |
| Révoquer pendant une génération FM | État privé absent de la réponse publiée. |
| « Ajoute Bûcheron 100 à mon profil » | Sauvegarde confirmée puis réponse IA. |
| « Approfondis ce choix » | Un spécialiste maximum, dans la même enveloppe. |
| Demander qui exerce un métier | Membres actuels déclarés uniquement, si opt-in activé. |
| Demander un résumé dans un salon non autorisé | Refus de lire son historique. |
| Un autre membre répond au message d'Evo | Aucune reprise de la mémoire du premier membre. |
| Demander un rôle ou une sanction | Aucune action ; explication/commande manuelle éventuelle. |
| `/evo-oublier`, puis question de suivi | Ancien contexte absent. |
| Redémarrer Render, consulter `/evo-budget` | Dépenses inchangées ; contexte conversationnel volatil perdu. |
| Relancer avec un verrou d'ancienne instance | Suspension pendant 125 secondes, puis contrôle et restauration. |

Ne pas tester le plafond en le remplissant réellement de requêtes payantes : les
tests locaux de concurrence, doublon, erreur et limite simulent ces situations.
Les tests du registre utilisent un faux salon Discord pour les pertes et corruptions.
Ne jamais supprimer le registre de production pour ces essais.

Désactivation : `EVO_ENABLED=0`, conserver `ENABLE_AI_COMMANDS=0`, redéployer et
resynchroniser les commandes. Garder le registre pour toute réactivation du même
mois. Une demande déjà envoyée à OpenAI ne peut pas être « dé-facturée ».

## 10. Architecture et maintenance

`evo.py` : commandes, suivi par réponse Discord, permissions, concurrence et mémoire.
`utils/evo_agent.py` : boucle Responses/function calling, comptage et transport bornés.
`utils/evo_budget.py` : compteurs, réservations et état confirmé sous verrou local.
`utils/evo_budget_store.py` : sauvegarde stricte et restauration depuis `#console`.
`utils/evo_tools.py` : outils bornés et catalogue ciblé selon la question.
`utils/evo_actions.py` : actions personnelles et services métier partagés.
`utils/evo_exo.py` : consentement ponctuel, état minimal et simulation privée.
`utils/evo_memory.py` : références structurées et suivis reconnus localement.
`utils/evo_equipment.py` : index et classements déterministes.
`utils/evo_monsters.py` : grades, plages de statistiques et données manquantes.
`utils/evo_safety.py` : schémas, bornes, sources, redaction et permissions.
`utils/evo_config.py` : configuration stricte, opt-in indépendant.
`utils/xixou_api.py` : petits adaptateurs publics ajoutés, clients existants conservés.
`main.py` et `utils/command_policy.py` : chargement natif et séparation des anciennes IA.
`tests_evo/` : tests hors ligne dédiés ; `docs/EVO_VALIDATION.md` décrit ce qui a été vérifié.

La matrice des 50 exemples et de leurs limites figure dans [EVO_EXAMPLES.md](EVO_EXAMPLES.md).

Les tests locaux utilisent un faux salon Discord et des transports OpenAI simulés.
Ils n'ont besoin ni d'une base SQL ni d'une clé réseau réelle. Une exécution connectée
du bot doit utiliser son propre serveur de test pour ne pas concurrencer Render.

## Références techniques consultées

- Modèle et function calling : https://developers.openai.com/api/docs/models/gpt-5.6-luna
- Boucle d'outils : https://developers.openai.com/api/docs/guides/function-calling
- Raisonnement et tokens de sortie : https://developers.openai.com/api/docs/guides/reasoning
- Comptage exact de l'entrée : https://developers.openai.com/api/docs/guides/token-counting
- Tarifs : https://developers.openai.com/api/docs/pricing
- Limites de dépenses : https://developers.openai.com/api/docs/guides/spend-limits
- Contrôles de données : https://developers.openai.com/api/docs/guides/your-data
- Render gratuit : https://render.com/docs/free
- Interactions Discord : https://discordpy.readthedocs.io/en/stable/interactions/api.html

Les limites et tarifs du fournisseur doivent être revérifiés lors des mises à jour.
