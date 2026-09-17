<p align="center">
  <img src="assets/github-hero.svg" alt="Evolution BOT — Dofus Rétro sur Boune. Vos équipements, vos sorties et votre guilde, à portée de commande dans Discord." width="100%">
</p>

<p align="center">
  Le bot Discord de la guilde <strong>EVOLUTION</strong> sur <strong>Dofus Rétro — Boune</strong>.<br>
  Trouve ton équipement, prépare ton exo, retrouve un artisan et réunis ta guilde.
</p>

<p align="center">
  <a href="#fonctionnalites"><strong>Découvrir les fonctionnalités</strong></a> &nbsp; · &nbsp;
  <a href="#commandes"><strong>Les commandes / utiles</strong></a> &nbsp; · &nbsp;
  <a href="#documentation"><strong>Ouvrir les guides</strong></a>
</p>

<a id="fonctionnalites"></a>
## Un compagnon pour chaque moment de jeu

<table>
  <tr>
    <td width="50%" valign="top">
      <h3>📖 Explorer l’encyclopédie</h3>
      <p>Retrouve les caractéristiques d’un objet, les ingrédients d’une recette ou les résistances d’un monstre. Filtre les équipements selon ton niveau.</p>
      <a href="#encyclopedie">Objets, recettes et monstres →</a>
    </td>
    <td width="50%" valign="top">
      <h3>💎 Préparer ses drops</h3>
      <p>Consulte les sources de drop, les zones et les récoltes. Personnalise les taux avec ta prospection lorsque les données Xixou sont disponibles.</p>
      <a href="#prospection">Drops et prospection →</a>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <h3>🔨 Travailler son exo</h3>
      <p>Pose des runes, observe les gains et les pertes, suis le puits et ajuste tes objectifs dans un atelier de forgemagie pédagogique.</p>
      <a href="#forgemagie">Atelier exo et runes →</a>
    </td>
    <td width="50%" valign="top">
      <h3>🗓️ Jouer ensemble</h3>
      <p>Propose une sortie, inscris-toi en un clic et retrouve les rendez-vous dans le calendrier. Les listes d’attente et les rappels suivent le groupe.</p>
      <a href="#sorties">Activités et calendrier →</a>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <h3>🛠️ Retrouver sa guilde</h3>
      <p>Enregistre tes métiers, trouve un artisan et rassemble ton personnage principal, tes mules et ton profil au même endroit.</p>
      <a href="#metiers">Métiers →</a> &nbsp; · &nbsp; <a href="#personnages">Personnages →</a>
    </td>
    <td width="50%" valign="top">
      <h3>🛡️ Faire vivre le serveur</h3>
      <p>Consulte les percepteurs et le classement, contacte le Staff en privé et donne ton avis. Le bot accompagne aussi l’accueil et la modération.</p>
      <a href="#guilde">Vie de guilde →</a> &nbsp; · &nbsp; <a href="#staff-ia">Outils Staff →</a>
    </td>
  </tr>
</table>

<a id="commandes"></a>
## Les commandes / utiles

**Tape `/`, sélectionne Evolution BOT et laisse Discord te guider.** Les champs sont
proposés dans le menu ; les recherches d’objets, de monstres et de métiers suggèrent
des noms pendant la saisie. **`/aide` est la référence des commandes disponibles sur
ton serveur**, selon les modules activés. Les permissions restent appliquées.

### 🧭 Découvrir le bot

| Commande | Utilité |
| --- | --- |
| `/aide` | Ouvrir l’aide interactive et découvrir les commandes disponibles. |
| `/regles` | Lire le règlement de la guilde. |

**Quatre commandes à essayer :**

```text
/objet nom:Gelano
/recette objet:Gelano quantite:3
/equipement type:Chapeau niveau:100
/exo objet:Gelano
```

<a id="encyclopedie"></a>
### 📖 Équipements et ressources

Prépare ton équipement avec des **fiches illustrées**, des résultats paginés et des
recettes dont tu peux ajuster la quantité directement dans le message.

| Commande | Utilité |
| --- | --- |
| `/objet` | Consulter l’image, le niveau, la description et les caractéristiques d’un objet. |
| `/recette` | Calculer les ingrédients nécessaires pour fabriquer de 1 à 10 000 exemplaires. |
| `/equipement` | Chercher par type, niveaux minimum et maximum, et nom facultatif. |
| `/monstre` | Consulter les PV, PA, PM et résistances selon les niveaux renseignés. |

<a id="prospection"></a>
#### 💎 Où le trouver, et avec quelle prospection ?

Lorsque l’enrichissement **Xixou** est configuré, les fiches objets proposent aussi
les **drops**, **zones et cartes**, **positions de récolte**, conditions, panoplies
et utilisations disponibles dans la source.

**Le parcours :** `/objet` → **Drops** → **Prospection personnalisée**.
Saisis ta PP totale et, si tu le souhaites, celle du groupe : la fiche affiche les
taux de base et les taux de jet individuel recalculés, avec le contrôle du seuil de groupe.
Il s’agit d’un bouton de la fiche, pas d’une commande supplémentaire.

Les données absentes restent signalées. Les taux personnalisés excluent les challenges,
étoiles et bonus serveur ; les quotas partagés et les exceptions sont indiqués.

[Guide des fiches enrichies](docs/XIXOU.md) · [Comprendre la prospection](docs/PROSPECTION.md)

<a id="forgemagie"></a>
### 🔨 Atelier exo et runes

Le moteur FM Rétro v3 distingue règles sourcées, hypothèses et observations.
L'historique complet et les outils de validation sont décrits dans
[FM-RETRO-V3.md](docs/FM-RETRO-V3.md). Les distributions livrées restent non
calibrées sur un corpus de jeu ; les tests Monte-Carlo ne constituent pas une
certification serveur.


Travaille le jet d’un objet **d’une tentative à l’autre** : pose de runes, gains et
pertes, suivi du puits, objectifs et historique. Exporte ton atelier pour le reprendre
plus tard avec l’option `reprise` de `/exo`.

| Commande | Utilité |
| --- | --- |
| `/exo` | Ouvrir un atelier personnel : Gelano de démonstration, objet choisi ou reprise d’un export. |
| `/rune calculer` | Estimer les probabilités d’obtention de runes au brisage à partir d’un jet et d’une statistique. |

L’atelier exo est un **simulateur pédagogique** : ses résultats ne garantissent pas
les probabilités ou les coûts réels en jeu. Les estimations de brisage sont indicatives.

[Découvrir l’atelier et ses objectifs](docs/EXO.md)

<a id="metiers"></a>
### 🛠️ Métiers et artisans

Trouve la bonne personne pour ton craft et rends tes propres métiers visibles à la guilde.

| Commande | Utilité |
| --- | --- |
| `/job ajouter` | Enregistrer un métier ou mettre son niveau à jour. |
| `/job mes-metiers` | Retrouver tes métiers et leurs niveaux. |
| `/job rechercher` | Trouver les artisans qui exercent un métier précis. |
| `/job joueur` | Consulter les métiers d’un membre. |
| `/job liste` | Parcourir les métiers et les artisans enregistrés. |
| `/job supprimer` | Retirer un métier de ta fiche. |

<a id="sorties"></a>
### 🗓️ Sorties et calendrier

Crée une sortie depuis un **formulaire avec aperçu privé**, puis publie sa fiche
d’inscription. Les membres rejoignent le groupe ou la liste d’attente ; les rappels
et le calendrier suivent les mêmes activités. Ces parcours fonctionnent **sans IA**.

| Commande | Utilité |
| --- | --- |
| `/calendrier` | Ouvrir directement le mois en cours, puis changer de vue ou filtrer dans le message. |
| `/activite creer` | Préparer une sortie et sa fiche d’inscription. |
| `/activite liste` | Voir les prochaines sorties et les places disponibles. |
| `/activite info` | Retrouver les détails et les inscrits d’une activité. |
| `/activite rejoindre` | Choisir une sortie et s’inscrire, ou rejoindre sa liste d’attente. |
| `/activite quitter` | Retrouver et quitter l’une de tes inscriptions. |
| `/activite modifier` | Modifier une sortie dans un formulaire prérempli — organisateur ou Staff. |
| `/activite annuler` | Annuler une sortie après confirmation — organisateur ou Staff. |
| `/sondage` | Faire voter la guilde entre plusieurs propositions. |

La création et l’inscription aux activités nécessitent le rôle de membre validé
configuré sur le serveur, ou l’accès Staff. Les boutons des fiches permettent aussi
de s’inscrire et de se désinscrire directement.

[Guide des activités](docs/ACTIVITES.md) · [Guide du calendrier](docs/CALENDRIER.md)

<a id="personnages"></a>
### 👤 Personnages et profils

Fais connaître tes personnages, retrouve ceux des autres membres et consulte le classement.

| Commande | Utilité |
| --- | --- |
| `/membre principal` | Enregistrer ou modifier ton personnage principal. |
| `/membre ajouter-mule` | Ajouter une mule à ta fiche. |
| `/membre retirer-mule` | Retirer une mule de ta fiche. |
| `/membre moi` | Voir ton personnage principal et tes mules. |
| `/membre liste` | Parcourir les joueurs et leurs personnages. |
| `/profil voir` | Consulter ton profil ou celui d’un joueur. |
| `/profil modifier` | Créer ou compléter ton profil avec un parcours guidé en privé. |
| `/profil rechercher` | Retrouver un profil par nom de personnage. |
| `/ladder` | Consulter le classement des profils de la guilde. |

<a id="guilde"></a>
### 🛡️ Vie de guilde

| Commande | Utilité |
| --- | --- |
| `/perco` | Consulter le statut des percepteurs ; sa modification est réservée au Staff. |
| `/musique` | **Staff** : lancer une musique ou une playlist dans un salon vocal. |
| `/ticket` | Ouvrir un échange privé avec le Staff. |
| `/staff` | Retrouver les membres du Staff. |
| `/avis` | Donner ton avis sur la guilde en message privé. |
| `/stats voir` | Afficher le tableau de bord des statistiques du serveur. |

<a id="staff-ia"></a>
<details>
<summary><strong>🔐 Staff et fonctions IA optionnelles</strong></summary>

### Accompagner et organiser la guilde

Ces commandes sont réservées au Staff et restent soumises aux permissions du serveur.

| Commande | Utilité |
| --- | --- |
| `/organisation` | Préparer une sortie avec un formulaire guidé, sans IA obligatoire. |
| `/recrutement` | Enregistrer un nouveau joueur dans la guilde. |
| `/veteran` | Consulter les candidats Vétéran et les promouvoir par bouton. |
| `/warnings` | Consulter les avertissements d’un membre. |
| `/annonce-list` | Retrouver les annonces déjà programmées. |
| `/annonce-cancel` | Annuler une annonce programmée. |

### Assistants IA — désactivés par défaut

Ces fonctions apparaissent uniquement lorsque l’IA est explicitement réactivée et
que le fournisseur concerné est configuré. Elles complètent les outils de guilde.

| Commande | Utilité |
| --- | --- |
| `/ia` | Ouvrir une conversation privée avec l’assistant IA. |
| `/iastaff` | **Staff** : demander de l’aide ou une action à l’assistant du serveur, selon les outils activés. |
| `/annonce` | **Staff** : préparer une annonce avec l’aide de l’IA. |
| `/event` | **Staff** : préparer un événement Discord avec un parcours guidé en message privé. |

[Disponibilité des commandes et réglages](docs/COMMANDES_SLASH.md)

</details>

<a id="documentation"></a>
## Pour aller plus loin

| Guide | Ce que tu y trouveras |
| --- | --- |
| [Atelier exo](docs/EXO.md) | Runes, objectifs, puits, historique et reprise de session. |
| [Fiches objets enrichies](docs/XIXOU.md) | Drops, zones, cartes, récoltes et navigation. |
| [Prospection personnalisée](docs/PROSPECTION.md) | PP personnelle, seuil de groupe et limites du calcul. |
| [Activités](docs/ACTIVITES.md) | Création, inscriptions, liste d’attente et rappels. |
| [Calendrier](docs/CALENDRIER.md) | Vues, filtres et navigation entre les sorties. |

<a id="installation"></a>
Pour héberger le bot : [installation et exploitation](docs/INSTALLATION.md).
Pour comprendre le projet : [architecture et données](docs/ARCHITECTURE.md).

<a id="contribuer"></a>
### Contribuer

Une idée ou un problème ? [Ouvre une issue](https://github.com/Donj63000/DiscordEVOLUTION/issues)
avec la commande concernée, le résultat attendu et les étapes de reproduction.
Pour contribuer au code, consulte l’architecture, ajoute des tests pour les changements
de comportement et lance `python -m pytest`. Les données de guilde restent dans
`#console` et les secrets hors du dépôt.

---

<p align="center">
  <img src="assets/evolution-bot.png" alt="Logo original Evolution BOT" width="80"><br>
  <strong>EVOLUTION · Dofus Rétro · Boune</strong><br>
  Un projet de <strong>Coca</strong> et des contributeurs · <a href="LICENSE">Licence MIT</a>
</p>

<p align="center">
  <a href="https://github.com/Donj63000/DiscordEVOLUTION/actions/workflows/ci.yml"><img src="https://github.com/Donj63000/DiscordEVOLUTION/actions/workflows/ci.yml/badge.svg?branch=main" alt="État des tests du projet"></a>
</p>

Les fiches s’appuient sur le [Wiki Dofus Rétro communautaire](https://wiki.moon-bot.io/)
et, lorsque l’intégration est activée, [Xixou](https://xixou.io/).
Dofus est une marque d’Ankama ; Evolution BOT est un projet communautaire indépendant.
