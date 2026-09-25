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

**Effet visible, accepté** : Pod dérive le slug (l'adresse) de la vidéo du nom
de fichier transmis. Le marqueur apparaît donc dans l'adresse des vidéos
déposées par morceaux (fichiers de plus de 500 Mo), par exemple
`…/video/1234-cours-amphi_upid1a2b3c4d/`. Le **titre**, lui, reste correct
(il est posé ensuite par PATCH).

## À tester sur l'instance
0. **3.4.2** — Déposer un gros fichier (> 500 Mo) sur l'instance de TEST :
   vérifier que le slug porte le marqueur `upid…`, que le titre est propre et
   que la vidéo est bien réattribuée à l'enseignant choisi. Si possible,
   provoquer un 504 à la finalisation pour vérifier la réattribution par
   marqueur.
1. Petit fichier (< 500 Mo) : la modale affiche « Étape 1/2 — Envoi… », la barre
   se remplit, puis « Étape finale — Lancement du ré-encodage », puis le succès.
2. Gros fichier (> 500 Mo) : même chose en 3 étapes (voie chunkée DEPOT).
3. Pendant l'envoi : vérifier qu'aucun clic n'est possible sur la fenêtre
   principale et que la croix ne ferme pas la modale.
4. Cas 504 : la modale doit se clore avec le message « le serveur termine son
   assemblage », sans relancer l'encodage automatiquement.
