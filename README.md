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
