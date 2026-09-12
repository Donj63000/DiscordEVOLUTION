# Calendrier : affichage direct et interface simplifiée

## Utilisation

Saisir `/calendrier`, sélectionner la commande puis l'envoyer. Aucun argument,
sous-commande ou formulaire n'est nécessaire. La description du menu slash est :

> Afficher directement le calendrier des activités, sans rien remplir.

La commande ouvre **le mois courant, selon la date de Paris**, dans le salon.
Les quatre anciennes options `vue`, `date`, `filtre` et `prive` ne sont plus
déclarées dans son schéma Discord. Une sous-commande « afficher » n'a pas été
ajoutée : elle imposerait une étape inutile.

Le choix d'une commande dans le sélecteur natif ne constitue pas son envoi :
l'utilisateur doit toujours valider la commande dans Discord. L'application ne
remplace pas le sélecteur natif par un bouton personnalisé.

## Interface après l'ouverture

La première ligne contient quatre boutons : **Précédent**, **Aujourd'hui**,
**Suivant**, et **Semaine** ou **Mois**, selon la vue affichée.

Le menu **Choisir une activité** ouvre une fiche privée avec les détails et
l'inscription. Il n'apparaît pas quand la période filtrée est vide.

Le menu **Filtres et options** regroupe les actions secondaires :

| Choix | Résultat |
| --- | --- |
| Toutes les activités | Enlever le filtre de la période. |
| Mes inscriptions | Voir ses activités ; depuis une vue publique, ouvrir une copie privée. |
| Places disponibles | Garder les activités futures avec une place libre. |
| Prochaine activité | Aller à la bonne période et à la bonne page de la prochaine sortie filtrée. |
| Actualiser | Relire les activités, inscriptions, modifications et annulations. |
| Aller à une date | Ouvrir volontairement le formulaire JJ/MM/AAAA. |
| Ouvrir en privé | Créer une session indépendante, réservée à la personne qui clique. |
| Fermer le calendrier | Retirer les contrôles, sans supprimer de message ni d'activité. |

Le choix « Prochaine activité » est masqué lorsqu'il n'y en a pas. « Ouvrir en
privé » n'est pas proposé dans une session déjà privée.

Une page contient au maximum six activités. Les boutons **Activités précédentes**
et **Activités suivantes** n'apparaissent que lorsqu'il existe plusieurs pages.
L'interface habituelle contient trois lignes de composants, quatre avec
pagination et deux sans activité. Il n'y a plus cinq lignes en permanence.

Les titres, descriptions courtes, places disponibles et délais relatifs restent
lisibles dans le texte Discord. L'identifiant technique d'une activité reste
accessible au pied de sa fiche, mais ne surcharge plus chaque entrée de l'agenda.
Les descriptions complètes restent consultables dans les fiches.

## Calendrier public et données personnelles

La navigation d'un calendrier public appartient à son auteur. Les autres membres
peuvent toutefois :

- consulter les fiches et utiliser leurs propres boutons d'inscription ;
- choisir **Ouvrir en privé** ou **Mes inscriptions** pour obtenir leur propre vue.

Un clic sur une fiche utilise toujours l'identité de la personne qui clique,
jamais celle de l'auteur du calendrier. Les contrôles d'inscription existants
restent exécutés : rôle validé, capacité, début passé, doublons, rôles d'activité,
sauvegarde locale et publication dans `#console`.

Une vue publique affiche les places et les statuts généraux, pas un statut
« Inscrit » qui pourrait être pris pour celui de tous ses lecteurs.
Une vue privée affiche bien le statut personnel de son propriétaire.

La copie privée conserve la période et relit les données pour son propre
utilisateur. Ses filtres, ses pages, son verrou et son expiration sont indépendants.
Elle partage uniquement la source de données et le cache de rendu. Elle ne
remplace jamais le calendrier public et n'en supprime pas les contrôles.

Dans une fiche, seul le bouton pertinent est affiché : **S'inscrire** ou
**Se désinscrire**. Le bouton **Texte complet** apparaît uniquement lorsque le
titre ou la description est abrégé, y compris pour des textes chargés d'emoji
ou de syntaxe Markdown. La longueur tient compte des unités UTF-16.

## Aperçu mensuel

Le PNG est un repère visuel, pas un second agenda miniature. Chaque jour occupé
affiche son nombre d'activités et **le premier horaire du jour, à Paris**.
Les titres minuscules, nécessairement tronqués dans sept petites colonnes,
ne sont plus dessinés : ils restent dans le texte et le menu Discord.

Le fond est opaque et contrasté. Le jour courant possède un contour et un repère
distincts. Les jours du week-end sont légèrement différenciés. La grille est
moins haute ; les mois de quatre, cinq et six semaines sont pris en charge.
Le calendrier n'utilise pas d'image distante ni de nouvelle police à télécharger.

En vue semaine, aucun PNG n'est généré ni joint. Le passage du mois à la semaine
retire l'ancienne pièce jointe.

## Robustesse et performances

Les interactions sont acquittées avant le rendu ou l'attente du verrou.
Le rendu Pillow reste exécuté hors de la boucle asynchrone avec deux travailleurs
simultanés au maximum. Le cache existant reste plafonné à 24 images et 8 Mio.

Le contenu du PNG ne dépend plus des titres ni des participants. Changer une
description, un titre ou une inscription réutilise donc l'image lorsque les
événements effectivement affichés n'ont pas changé. Les textes et statuts,
eux, sont relus. Un filtre qui change les événements visibles change la clé du PNG.

Lors d'une édition, une image de même nom signé par son contenu conserve la
pièce jointe déjà publiée par Discord : elle n'est pas renvoyée inutilement.
Les fichiers temporaires en mémoire sont fermés après envoi, réutilisation
ou erreur.

La commande initiale et les copies privées utilisent la même procédure d'envoi.
Un refus d'image déclenche un repli vers la liste native. Un échec définitif
arrête la nouvelle session. Une erreur de relecture restaure le précédent
instantané sans rappeler en boucle une source défaillante.

Un calendrier public mémorise aussi le message porté par ses interactions,
plutôt que de dépendre indéfiniment du jeton de son tout premier webhook.
Après dix minutes d'inactivité, l'expiration tente de retirer les contrôles et
d'indiquer la fin de session. Une suppression du message ou une perte de
permissions peut empêcher son édition ; l'échec est journalisé.

Les sessions ne sont pas persistantes : après un redémarrage, relancer
`/calendrier`. Ce patch ne modifie pas le stockage des activités.

## Compatibilité et périmètre

Aucune dépendance n'est ajoutée. Les composants utilisés sont ceux de
`discord.ui.View`, compatibles avec la contrainte existante `discord.py>=2.4,<3`.
Le schéma et les interactions simulées ont été exécutés avec discord.py 2.6.4.

`!calendrier` reste utilisable sans argument. Les arguments historiques de la
commande à préfixe sont conservés pour ne pas casser les usages existants :

```text
!calendrier
!calendrier semaine
!calendrier mois 01/10/2026 toutes
!calendrier semaine "" inscrit
```

Ces arguments ne sont **pas** des options slash. Les anciennes commandes du type
`/calendrier vue:semaine` doivent être remplacées par `/calendrier`, puis par les
boutons du message. Une vue privée se demande depuis le menu du calendrier.

Le calendrier affiche le stockage d'`ActiviteCog`. Il ne fusionne pas les données
distinctes de `/event` ou `/organisation`. Le nettoyage historique des activités
passées est inchangé ; ce calendrier ne constitue donc pas un historique archivé.
La protection du stockage partagé entre serveurs reste inchangée.

Les données ne se mettent pas à jour en temps réel dans tous les messages déjà
ouverts : naviguer ou utiliser **Actualiser** relit la source.

## Fichiers du patch

| Fichier | Rôle |
| --- | --- |
| `utils/slash_catalog.py` | Commande slash sans option, description directe. |
| `utils/slash_support.py` | Suppression de l'ancien réglage slash de confidentialité. |
| `activite.py` | Envoi initial mutualisé, compatibilité de la commande à préfixe. |
| `utils/calendar_view.py` | Contrôles compacts, copies privées, fiches, permissions, pièces jointes. |
| `calendrier.py` | Aperçu mensuel plus lisible et cache adapté au contenu dessiné. |
| `utils/calendar_data.py` | Statut public explicite, sans identifiant utilisateur fictif. |
| `help.py`, `README.md` | Aide cohérente avec le nouveau parcours. |
| `tests/test_calendar_direct.py` | Régressions spécifiques à l'affichage direct et aux copies privées. |
| `tests/test_calendar_view.py` | Assertions adaptées aux nouveaux composants et à l'expiration. |
| `tests/test_calendar_integration.py` | Schéma slash sans option et compatibilité du préfixe. |
| `tests/test_calendar_render.py` | Dimensions de la nouvelle grille. |
| `docs/CALENDRIER.md` | Cette documentation. |

## Installation

Ce patch est basé uniquement sur **DiscordEVOLUTION-main (2).zip**. Il s'ajoute
à la refonte déjà présente dans cette archive. Ne pas réappliquer l'ancien patch
de refonte pour installer cette mise à jour.

Sauvegarder le code et les activités avant le déploiement. Placer
`evolution-calendrier-affichage-direct.patch` à la racine du projet, à côté de
`main.py`. Dans ce répertoire :

```bash
git apply --check evolution-calendrier-affichage-direct.patch
git apply evolution-calendrier-affichage-direct.patch
python -m pytest
```

Si le contrôle d'application échoue, ne pas forcer : les fichiers locaux diffèrent
de la base fournie. Examiner les modifications avant toute résolution de conflit.

Exécuter les tests dans l'environnement habituel du projet, avec ses dépendances
déjà installées. L'installation habituelle reste :

```bash
python -m pip install -r requirements.txt
```

Redémarrer ou redéployer ensuite l'instance existante du bot. Ne pas démarrer une
seconde instance en parallèle.

### Synchronisation indispensable

Conserver `SYNC_SLASH_COMMANDS=1` au redémarrage pour republier le schéma
**sans les quatre options**. Cette variable est activée par défaut dans le code.

Conserver le périmètre de synchronisation déjà utilisé : `SYNC_SLASH_GUILD_ID`
pour un serveur de test, ou son absence pour les commandes globales. Une commande
de serveur ancienne peut masquer la version globale ; ne pas basculer arbitrairement
de périmètre et ne pas supprimer toutes les commandes pour résoudre ce problème.

Vérifier les logs « Slash commands synchronisées ». Fermer puis rouvrir le
sélecteur de commandes Discord. Au besoin, recharger le client. Le code seul
ne peut pas modifier une ancienne définition qui n'a pas été resynchronisée.

### Retour arrière

```bash
git apply --reverse --check evolution-calendrier-affichage-direct.patch
git apply --reverse evolution-calendrier-affichage-direct.patch
```

Redémarrer et resynchroniser à nouveau pour restaurer aussi l'ancien schéma slash.

## Recette Discord avant production

1. Vérifier sur ordinateur et mobile que `/calendrier` ne propose aucun champ,
   ouvre le mois courant à Paris et ne déclenche aucun formulaire.
2. Vérifier les changements de période, le bouton Semaine/Mois, puis une période
   vide et une période de plus de six activités.
3. Avec un second membre, ouvrir une fiche du calendrier public. Vérifier que son
   inscription concerne bien son compte et que la navigation du premier reste protégée.
4. Ouvrir **Mes inscriptions** et **Ouvrir en privé**. Vérifier la visibilité privée,
   l'indépendance des filtres et la persistance des inscriptions dans `#console`.
5. Tester les cas groupe complet, activité annulée, activité commencée, rôle non
   validé, permission de pièce jointe absente et description très longue.
6. Tester le formulaire volontaire **Aller à une date**, la fermeture et
   l'expiration. Aucune de ces actions ne doit supprimer d'activité.

Les tests automatisés sont sans connexion Discord : les vérifications de rendu
du client mobile, de permissions réelles, d'attribution de rôles et de persistance
sur votre serveur restent nécessaires.

## Références techniques

- Application Commands : https://docs.discord.com/developers/interactions/application-commands
- Interactions : https://docs.discord.com/developers/interactions/receiving-and-responding
- discord.py : https://discordpy.readthedocs.io/en/stable/interactions/api.html
