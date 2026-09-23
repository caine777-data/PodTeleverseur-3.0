# Pod Téléverseur

Application de bureau (Windows / macOS) destinée aux enseignants de l'Université
de Toulouse pour **déposer des vidéos par lot** sur l'instance Esup-Pod
`videos.utoulouse.fr` et **gérer leurs propres vidéos**, sans passer par
l'interface web.

Version légère dérivée de **PodAdmin** : les modules d'administration (groupes
d'accès, réaffectation, inventaire, modération…) ont été retirés.

---

## Fonctions

- **📂 Téléversement** — dépôt par lot (glisser-déposer), titres éditables,
  propriétaires additionnels communs, lancement automatique de l'encodage.
  Les gros fichiers (> 500 Mo) passent par le téléversement **par morceaux**
  (chunké) pour absorber les coupures de la passerelle.
- **🎞️ Mes vidéos** — gestion des vidéos **du propriétaire sélectionné**
  (uniquement les siennes) :
  - filtres (texte, statut, encodage, chaîne, type) ;
  - renommer, changer le **statut** (Brouillon / Public / Restreint) ;
  - changer le **type**, gérer les **co-propriétaires** ;
  - **sous-titres** (ajout / suppression de pistes `.vtt` / `.srt`) ;
  - **Remplacer** le fichier source puis **ré-encoder** (le titre, les chaînes
    et les droits sont conservés) ;
  - **supprimer** une vidéo ;
  - modification du **type en masse** sur les vidéos affichées.

  L'onglet **se rafraîchit automatiquement** quand on change de propriétaire
  (dans l'onglet Téléversement).
- **⚙️ Configuration** — connexion à l'instance (jeton) + choix du compte
  déposant.
- **📋 Journal** — historique horodaté des opérations.

---

## Installation (utilisateurs)

Télécharger le livrable adapté depuis la page **Releases** du dépôt :

| Système  | Fichier                          | Usage                                   |
|----------|----------------------------------|-----------------------------------------|
| Windows  | `PodTeleverseur-Setup.exe`       | Installeur (menu Démarrer + désinstalleur) |
| Windows  | `PodTeleverseur.exe`             | Version portable (sans installation)    |
| macOS    | `PodTeleverseur.dmg`             | Glisser l'app dans Applications         |
| macOS    | `PodTeleverseur-macOS.zip`       | Archive `.app`                          |

**macOS — premier lancement** : l'app n'étant pas signée, Gatekeeper la bloque.
Faire un **clic droit → Ouvrir**, ou Réglages Système → Confidentialité et
sécurité → « Ouvrir quand même », ou en Terminal : `xattr -cr /Applications/PodTeleverseur.app`.
Les runners macOS de GitHub étant Apple Silicon, l'app est **arm64**.

---

## Compilation (mainteneur)

Les exécutables sont fabriqués automatiquement par **GitHub Actions**
(`.github/workflows/build.yml`), pour Windows et macOS en un seul run.

- **Build manuel** : onglet *Actions* → *Build installers* → *Run workflow*.
  Récupérer les fichiers dans les *artifacts* du run.
- **Release** : pousser un tag `v*` (ex. `v3.0.0`) crée une *Release* GitHub
  avec tous les livrables attachés.

  ```bash
  git tag v3.0.0
  git push origin v3.0.0
  ```

> ⚠️ Le dossier caché `.github/` est souvent oublié lors d'un premier envoi.
> Vérifier qu'il est bien présent dans le dépôt (sinon le workflow n'existe pas).

### Compilation locale (optionnelle)

```bash
python -m pip install -r requirements.txt pyinstaller
# Windows :
python -m PyInstaller --onefile --windowed --name PodTeleverseur ^
  --collect-all customtkinter --collect-all keyring --collect-all tkinterdnd2 ^
  --add-data "assets;assets" app.py
# macOS/Linux : remplacer "assets;assets" par "assets:assets"
```

---

## Architecture (fichiers)

| Fichier                  | Rôle                                                        |
|--------------------------|-------------------------------------------------------------|
| `app.py`                 | Interface graphique (CustomTkinter) + logique des onglets   |
| `pod_api.py`             | Client de l'API REST Esup-Pod (jeton Bearer)                |
| `pod_chunked.py`         | Téléversement / remplacement **par morceaux** (session web) |
| `config.py`              | Configuration + stockage sécurisé du jeton (keyring OS)     |
| `verifier_mes_videos.py` | Sonde de diagnostic (filtre propriétaire côté serveur)      |
| `assets/`                | Logo Université de Toulouse + icônes                        |

---

## Sécurité — compte véhicule DEPOT

Le téléversement par morceaux (gros fichiers, et le **Remplacer**) exige une
**session web**. L'application embarque pour cela un compte véhicule **DEPOT**
(identifiants dans `config.py`). Ce compte a le **statut d'équipe** (is_staff),
ce qui lui permet de créer une vidéo puis de la réattribuer au bon propriétaire,
et de finaliser un remplacement de fichier sur la vidéo d'un enseignant.

> ⚠️ **Conséquence** : les identifiants DEPOT étant embarqués dans l'exécutable
> distribué, ils sont techniquement **extractibles**. Comme DEPOT est is_staff,
> quiconque récupère l'exe obtient une session web disposant des droits d'équipe
> sur l'instance. Le dépôt de ce code doit donc rester **privé** et la diffusion
> de l'exécutable maîtrisée (usage interne, enseignants de confiance).

Le **jeton API** de l'utilisateur, lui, n'est jamais embarqué : il est stocké
dans le coffre-fort de l'OS (Windows Credential Manager / macOS Keychain), par
poste, avec repli fichier à permissions restreintes.

---

## Diagnostic

Avant d'ajouter ou de fiabiliser une fonction, valider le protocole API par
sonde plutôt que supposer. `verifier_mes_videos.py` (lecture seule) teste si
l'instance sait filtrer les vidéos par propriétaire côté serveur (`?owner=`),
ce qui permettrait à l'onglet « Mes vidéos » d'éviter un scan complet.

```bash
python verifier_mes_videos.py
```

---

*Développé par Cédric MONNA (support-pod@utoulouse.fr) — service MFCA,
Université de Toulouse. Usage interne, non redistribuable.*

---

## Apparence — module partagé `theme.py`

`theme.py` est **commun à PodAdmin, au Téléverseur v2 et au Téléverseur v3**.
Les faire diverger reviendrait à corriger trois fois le même défaut — et à en
oublier deux. Une correction de palette faite ici bénéficie aux trois outils.

⚠️ **Toute couleur est un COUPLE `(clair, sombre)`.** Une teinte écrite seule
s'applique telle quelle aux deux thèmes. L'application en comptait **124** avant
le portage : chacune serait devenue illisible dès l'ouverture du mode clair.
Un test l'interdit désormais.

⚠️ **Le contraste se calcule, il ne s'apprécie pas à l'œil.** Toutes les teintes
de texte atteignent 4,5:1 (WCAG 2.1 AA) sur l'ensemble de l'échelle de surfaces,
dans les deux modes ; `theme.verifier_palette()` le contrôle et un test l'appelle.

## Mode clair / sombre

L'application était figée en mode sombre. Un bouton de bascule figure en pied de
barre latérale, et le choix est **enregistré** : un enseignant qui préfère le
mode clair ne doit pas le redemander à chaque dépôt. Sombre reste le défaut —
c'était le seul mode avant la 3.1.

## Tests

Premiers tests de cette application (elle n'en avait aucun) :

```bash
python -m pytest tests/ -q
```

Ils couvrent la palette, la bascule de thème, la structure des onglets et
l'absence de superposition de widgets. ⚠️ Pour ce dernier point, **ne pas
filtrer sur `winfo_class()`** : il renvoie « Frame » pour tous les widgets
CustomTkinter, `CTkOptionMenu` compris — un filtre sur cette base ne compare
plus rien et laisse passer le défaut qu'il devait détecter.


---

## Mises à jour (depuis la 3.2.0)

Même dispositif que Pod Téléverseur v2, pour que cette version puisse le
remplacer sans changer les habitudes :

- **Bandeau** en pied de barre latérale quand une version plus récente existe.
- **Mise à jour obligatoire** (`obligatoire: true` dans `version.json`) : une
  fenêtre modale empêche d'utiliser l'application. Message fixe et neutre
  (`MESSAGE_BLOCAGE`), sans raison ; deux issues seulement — télécharger ou
  quitter (la croix et Alt+F4 quittent aussi).
- **Verrou local** : un blocage confirmé par le serveur reste actif même sans
  réseau, pour qu'on ne puisse pas le contourner en coupant la connexion. Il ne
  vaut que pour la version qui l'a déclenché.

⚠️ **La version n'est définie qu'à UN endroit : `__version__.py`.** À la
reprise de cette v3, `app.py` annonçait 3.0.0 et `config.py` 3.1.0 — c'est
`app.py` qui fait foi, l'application se serait donc crue en retard en
permanence. `version.txt` doit concorder : un test le vérifie.

La mise en place (dépôt public, jeton, secret) et la procédure de publication
obligatoire sont décrites dans **`MISE_EN_PLACE_MISES_A_JOUR.md`**.

## Onglet « Mes vidéos »

N'affiche **que** les vidéos du propriétaire sélectionné. Le filtrage est
demandé au serveur (paramètre `owner`) **et refait systématiquement côté
client** : si l'instance ignorait ce paramètre, elle renverrait toutes les
vidéos de la plateforme. ⚠️ Ne jamais supprimer le filtre client
(`TestFiltreProprietaire`). Être co-propriétaire ne suffit pas : seul le
propriétaire principal compte.

⚠️ **Le filtre serveur est facultatif.** Le format accepté pour `owner` varie
d'une instance à l'autre : sur videos.utoulouse.fr, `owner=<URL>` est refusé
(« Sélectionnez un choix valide »). Une version antérieure ne tentait que cette
forme, et son échec vidait tout l'onglet. L'application essaie désormais l'id
numérique, l'URL, puis `owner__username`, et se rabat sur une lecture complète
— toujours re-filtrée côté client (`TestFiltreServeurFacultatif`).
