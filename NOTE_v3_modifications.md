# Pod Téléverseur 3 — modifications apportées

## 1. Bouton renommé
« 🎬 Remplacer » → **« 🎬 Remplacer & ré-encoder »** (onglet Mes vidéos,
section Fichier source). Le bouton est élargi pour accueillir le libellé.

## 2. Fenêtre de progression bloquante (nouveau)
Nouvelle classe `ProgressModal` (fin de `app.py`). Pendant un remplacement :

- une fenêtre **« Veuillez patienter… »** s'ouvre par-dessus l'application ;
- elle affiche le titre de la vidéo, le fichier, la méthode (PATCH direct ou
  par morceaux), l'**étape en cours** et une **barre d'avancement** avec le
  détail « X / Y Mo envoyés » (même code couleur que le téléversement par lot) ;
- elle est **modale** (`grab_set`) : aucune autre manipulation n'est possible,
  ce qui évite d'interrompre l'envoi ;
- la **croix de fermeture est neutralisée** et le bouton « Fermer » reste
  désactivé tant que l'opération tourne ;
- en fin d'opération, la fenêtre se déverrouille et affiche le résultat
  (succès, échec, ou finalisation serveur en cours après un 504).

Étapes affichées : connexion au service de dépôt → envoi (barre) → lancement
du ré-encodage (animation continue, car la durée n'est pas mesurable).

**Garde-fou** : un bloc `finally` appelle `ensure_unlocked()`. Même en cas de
sortie imprévue, la modale se déverrouille — une fenêtre modale restée bloquée
rendrait l'application inutilisable.

## 3. Nouvelle icône
`assets/icon.svg`, `icon_master.png`, `icon.ico`, `icon.icns` régénérés.

- **Famille conservée** : fond bleu arrondi, flèche de téléversement blanche,
  écran vidéo — on reconnaît Pod Téléverseur au premier coup d'œil.
- **Ce qui change** : un **badge orange au crayon** en bas à droite, qui
  signale la nouveauté de la v3 (gestion de ses propres vidéos). L'orange
  reprend la couleur des actions de gestion dans l'application.
- Vérifié lisible de 16×16 à 1024×1024.

> Remarque : l'icône présente dans le dépôt avant cette modification était
> celle de **PodAdmin eformation** (fond gris, triangle vert, badge engrenage) —
> son propre commentaire indiquait « identité eformation ». Elle a été
> remplacée par l'icône de la famille Téléverseur.

## 4. Aide et documentation

- Fenêtre Aide : 3 nouvelles rubriques (8. Mes vidéos, 9. Remplacer & ré-encoder,
  10. Supprimer une vidéo) ; les suivantes ont été renumérotées (11 à 14).
- Fenêtre À propos : description actualisée (téléversement + gestion). Les trois
  auteurs, la version 3.0.0, l établissement, le contact et la licence s affichent.
- Commentaires : couverture portée de 89 % à **100 %** (224/224 fonctions et
  classes documentées), commentaires uniquement — aucun changement fonctionnel
  (vérifié par comparaison du squelette AST avant/après).

## 3.4.2 — correctifs de la 3.0.1 réintégrés

La 3.0.1 (25/08/2026, à la suite d'un audit de sécurité) n'avait jamais été
poussée sur le dépôt : les versions 3.1.0 à 3.4.1 ont été construites sans
ses correctifs. Ils ont été réécrits sur le code 3.4.1. Chaque test a été
éprouvé par mutation : le défaut a été réintroduit, le test a échoué, puis le
code a été restauré. Tests dans `tests/test_correctifs_342.py`.

| Défaut | Correctif | Test |
|---|---|---|
| Envois de fichier en `timeout=None` : une connexion figée attendait indéfiniment et la relance automatique ne se déclenchait jamais | `UPLOAD_TIMEOUT = (30, 600)` (connexion, silence maximal) sur le POST de création et le PATCH de remplacement, avec et sans requests-toolbelt | `TestDelaisReseauFinis` |
| `set_user_staff` / `set_user_groups` présentes dans une appli d'enseignants | Supprimées (jamais appelées) | `TestMethodesAdministrationRetirees` |
| En-tête de `pod_chunked.py` : adresse personnelle, version « 0.1.0 » écrite en dur | `support-pod@utoulouse.fr`, version lue dans `__version__.py` | `TestEnTetePodChunked` |
| 🟠 Jeton envoyé à tout hôte désigné par le serveur (champ `next`, URL de vidéo…), y compris en `http://` | `_abs()` refuse toute URL absolue hors HTTPS ou d'un autre hôte que l'instance ; la pagination et le PATCH de remplacement y passent aussi | `TestJetonLimiteALInstance` |
| 🔴 Position confirmée par le serveur adoptée telle quelle lors d'un envoi par morceaux (fichier corrompu) | Arrêt immédiat (`PodChunkedError`) si l'offset confirmé ≠ `end + 1`, sans envoyer la suite ni finaliser | `TestPositionEnvoiParMorceaux` |
| 🔴 Après un 504, vidéo retrouvée par NOM DE FICHIER sur le compte DEPOT partagé : deux dépôts simultanés de même nom → mauvais propriétaire | Marqueur unique `upid` + 8 hex dans le nom transmis (création seulement) ; recherche sur ce marqueur ; si plusieurs vidéos le portent, **aucune** réattribution, alerte dans le Journal | `TestMarqueurDansLeNomTransmis`, `TestReattributionApres504` |
| « Mes vidéos » pas rafraîchie après un dépôt | `_myvids_mark_stale()` (vide aussi la sélection multiple), appelée si au moins une vidéo a réussi | `TestMesVideosApresDepot` |
| Filtre non recalculé après une modification (une vidéo passée en Public restait sous le filtre Brouillon) | `_do_myvids_patch` appelle `_myvids_apply_filter` | `TestFiltreApresModification` |
| Workflow : jobs de compilation autorisés à écrire dans le dépôt | `permissions: contents: read` à la racine ; `release` garde `contents: write` | `TestPermissionsWorkflow` |

**Seuil d'envoi par morceaux abaissé de 500 Mo à 150 Mo**
(`config.CHUNK_THRESHOLD_BYTES`) : davantage de fichiers passent par la voie
par morceaux, plus robuste face aux coupures de la passerelle. L'aide
(rubrique 7) et le README annoncent le nouveau seuil ; un test vérifie que
l'aide reste alignée sur la configuration.

**Effet visible, accepté** : Pod dérive le slug (l'adresse) de la vidéo du nom
de fichier transmis. Le marqueur apparaît donc dans l'adresse des vidéos
déposées par morceaux (fichiers de plus de 150 Mo), par exemple
`…/video/1234-cours-amphi_upid1a2b3c4d/`. Le **titre**, lui, reste correct
(il est posé ensuite par PATCH).

### Évolutions du dépôt reprises de PodAdmin 1.9.1

Tests dans `tests/test_evolutions_podadmin.py`, chacun éprouvé par mutation.

| Évolution | Pourquoi | Test |
|---|---|---|
| 🔴 Discipline posée sur l'**URL** de la vidéo, plus sur son slug | `/videos/<slug>/` peut répondre 404 : le classement échouait, seulement noté au Journal | `TestDisciplineParURL` |
| 🔴 Propriétaire comparé par **égalité stricte** après un 504 | L'inclusion de chaîne confondait `/users/99` et `/users/999` et acceptait une vidéo sans propriétaire | `TestProprietaireEgaliteStricte` |
| Session DEPOT refusée si l'adresse n'est pas en `https://` | Le mot de passe du compte partagé ne doit jamais circuler en clair | `TestSessionDepotHTTPS` |
| **Repli automatique sur l'envoi par morceaux** après une coupure de l'envoi direct | La passerelle coupe un envoi qui dure plus d'une minute environ (SSLEOFError), quelle que soit la taille ; réessayer à l'identique échoue toujours. Un refus du serveur (400, 403…) n'est jamais rejoué | `TestReconnaissanceCoupure`, `TestRepliSurEnvoiParMorceaux` |
| Un seul chemin de création par morceaux (`_deposer_par_morceaux`) | Dans PodAdmin, la copie du repli appelait mal la reprise après 504 (TypeError, puis doublon à la relance) | `TestRepliSurEnvoiParMorceaux` |
| 🔴 « Relancer les échecs » ne renvoie plus une vidéo **déjà créée** | Une vidéo « NON réattribuée » n'était pas « terminée » : la relance en créait une seconde. Défaut trouvé à cette occasion, absent de PodAdmin | `TestEchecsARelancer` |
| Bouton « ✅ Retirer les N terminées » | Repartir après un lot sans perdre les échecs à relancer | `TestRetirerLesTerminees` |
| Barres de progression masquées au repos, bilan « Vous pouvez les retirer » | Deux barres vides en permanence n'informaient de rien | `TestProgressionEtBilan` |

Non repris : la vérification du compte DEPOT avant l'envoi (ses identifiants
sont embarqués dans `config.py`, toujours présents).

### Évolutions du dépôt reprises de PodAdmin 1.9.2

| Évolution | Pourquoi | Test |
|---|---|---|
| Bouton **🛑 Interrompre** (visible pendant un lot), qui arrête aussi la vidéo en cours | Un gros fichier peut prendre une demi-heure, l'attente après un 504 jusqu'à 30 min. L'arrêt a lieu avant la finalisation : aucune vidéo créée à moitié | `TestArretEnvoiDirect`, `TestArretEnvoiParMorceaux`, `TestLotInterrompu` |
| Vidéo « ⚠️ à vérifier » si l'arrêt tombe pendant l'attente d'un 504 | Elle existe peut-être : jamais renvoyée seule (exclue aussi des échecs à relancer, point propre au Téléverseur) ; repère écrit au Journal | `TestLotInterrompu`, `TestAttenteApresFinalisation` |
| Attente de **3 min** après un 502/503 (`CHUNK_VERIFY_TIMEOUT_502_S`), 30 min après un 504 | Un 502 réel (25/09/2026) : la vidéo n'est jamais apparue, et 30 min d'attente bloquaient tout le lot | `TestAttenteApresFinalisation` |
| Pendant un lot : ajout permis (la vidéo part à la suite), retraits bloqués | Retirer une ligne décalait la liste et faisait sauter la vidéo suivante sans rien dire | `TestListeProtegeePendantUnLot`, `TestLotInterrompu` |
| Ajout de fichiers : chemins normalisés, bilan exact, confirmation avant de renvoyer un fichier déjà envoyé dans la session | Sous Windows, le même fichier entrait deux fois selon son écriture ; un fichier retiré puis réajouté créait un doublon sur Pod | `TestAjoutDeFichiers` |

### Propriétaire désigné par l'identifiant universitaire

La sonde `verifier_identifiant.py` (26/09/2026) a montré qu'un jeton
d'enseignant voit TOUT l'annuaire (41 comptes) : la liste permettait de
choisir n'importe quel collègue, ou un compte local comme DEPOT, comme
propriétaire — et c'est ce choix qui décide des vidéos que « Mes vidéos »
permet de modifier.

| Changement | Pourquoi | Test |
|---|---|---|
| Le propriétaire est **saisi** (identifiant universitaire), plus choisi dans la liste : onglet Configuration, bouton « 🎯 Saisir l'identifiant… », étape 2 de l'assistant | Plus d'erreur de compte, plus d'annuaire affiché | `TestFenetreDeSaisie`, `TestOngletConfiguration` |
| Format strict `aaa0000a` (3 lettres, 4 chiffres, 1 lettre) | Les 32 comptes d'usagers le suivent tous ; les 9 autres sont des comptes locaux d'administration, écartés d'office | `TestFormat` |
| Recherche exacte (`?username=` + égalité vérifiée côté client) | `?search=` est plein texte (12 comptes pour un identifiant complet) | `TestRechercheExacte`, `TestResolution` |
| Envoi refusé si le propriétaire enregistré n'a pas ce format | Un poste configuré avant cette version peut pointer vers un compte local | `TestGardeFouAuLancement` |

Inchangé : les **co-propriétaires** se choisissent toujours dans la liste de
toutes les personnes (chacun associe qui il veut), et « Mes vidéos » affiche
les vidéos du compte ET celles dont il est co-propriétaire.

⚠️ **Limite** : la saisie évite les erreurs, mais ne protège pas contre un
usage malveillant. La sonde a montré que le jeton testé peut **modifier la
vidéo d'un collègue** : les droits viennent du jeton, et se règlent côté Pod.

## À tester sur l'instance
0. **3.4.2** — Déposer un gros fichier (> 150 Mo) sur l'instance de TEST :
   vérifier que le slug porte le marqueur `upid…`, que le titre est propre et
   que la vidéo est bien réattribuée à l'enseignant choisi. Si possible,
   provoquer un 504 à la finalisation pour vérifier la réattribution par
   marqueur. Vérifier aussi que la **discipline** choisie est bien rattachée
   (défaut corrigé : elle pouvait être perdue sans alerte).
0 bis. **3.4.2** — Sur une connexion lente, déposer un fichier de 100 à 150 Mo :
   si l'envoi direct est coupé, le Journal doit afficher « Bascule automatique
   sur l'envoi par morceaux » et la vidéo doit arriver au bon propriétaire.
1. Petit fichier (< 150 Mo) : la modale affiche « Étape 1/2 — Envoi… », la barre
   se remplit, puis « Étape finale — Lancement du ré-encodage », puis le succès.
2. Gros fichier (> 150 Mo) : même chose en 3 étapes (voie chunkée DEPOT).
3. Pendant l'envoi : vérifier qu'aucun clic n'est possible sur la fenêtre
   principale et que la croix ne ferme pas la modale.
4. Cas 504 : la modale doit se clore avec le message « le serveur termine son
   assemblage », sans relancer l'encodage automatiquement.

## Blocage à distance (repris de PodAdmin)

Champ **blocage** dans Run workflow : « bloquer » rend toutes les copies
installées inutilisables (voile « Pod Téléverseur n'est pas disponible »,
seul Quitter reste possible) ; « débloquer » les rétablit. Ne compile rien :
écrit seulement `etat.json` sur `podteleverseur-releases`. Par défaut « ne rien
changer ». Détails : `BLOCAGE.md`. Tests : `tests/test_blocage_distant.py`
(28 tests, 21 mutations).

Propre au Téléverseur : un téléversement en cours est interrompu proprement
au moment du blocage (même arrêt que le bouton 🛑).

À tester sur GitHub, une fois la branche poussée : Run workflow → blocage =
bloquer, vérifier `etat.json` sur le dépôt public et le voile sur un poste ;
puis débloquer.
