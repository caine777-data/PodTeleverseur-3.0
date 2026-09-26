# Blocage à distance

Un interrupteur qui rend toutes les copies installées de Pod Téléverseur
inutilisables, puis à nouveau utilisables. Repris de PodAdmin.

Il est **indépendant de la mise à jour obligatoire** (voir
[MISE_EN_PLACE_MISES_A_JOUR.md](MISE_EN_PLACE_MISES_A_JOUR.md)). La mise à jour
obligatoire ne se déclenche que si une version publiée dépasse un seuil. Le
blocage ne se déclenche que sur une action manuelle et explicite, dans l'onglet
Actions. **Jamais tout seul.**

## Ce que voient les utilisateurs

- **Normalement** : rien. L'application lit l'état en arrière-plan au démarrage,
  puis toutes les heures, et n'attend jamais la réponse.
- **Bloquée** : la fenêtre est entièrement recouverte par « Pod Téléverseur
  n'est pas disponible — L'utilisation de l'application est suspendue », avec
  un seul bouton, **Quitter**.
  - Les fenêtres secondaires ouvertes sont fermées.
  - Un téléversement en cours est **interrompu proprement**, comme avec le
    bouton 🛑 : aucune vidéo n'est créée à moitié.
  - On peut toujours fermer l'application (croix, Alt+F4).
- **Débloquée** : l'application redevient normale, sans rien réinstaller, au
  lancement suivant, ou dans l'heure si elle est déjà ouverte.

## Règles

- Seule une **réponse du serveur** change l'état. Réseau coupé, dépôt
  injoignable, fichier absent ou illisible : rien ne change, ni blocage ni
  déblocage.
- Un blocage reçu est **mémorisé sur le poste** (`"blocage_distant": true` dans
  `~/.pod_televerseur.json`) : couper le réseau ne le contourne pas. Il n'est
  levé que par un « débloquer » lu sur le serveur.
- Un poste qui n'a jamais reçu le blocage (resté hors ligne) n'est pas bloqué :
  il le sera à sa première connexion.
- Délai : GitHub garde le fichier en cache jusqu'à 5 minutes ; une application
  déjà ouverte le relit dans l'heure.
- Seules les versions qui connaissent ce mécanisme le lisent : **à partir de la
  3.4.2**. Les postes restés en v2 ne sont pas concernés tant qu'ils ne sont pas
  mis à jour.
- Ce n'est pas une protection forte : une personne qui sait modifier
  `~/.pod_televerseur.json` et couper le réseau peut s'en affranchir.

## Installation

**Rien à faire.** Le mécanisme réutilise le dépôt public
`podteleverseur-releases` et le secret `RELEASES_TOKEN` déjà configurés pour la
mise à jour : le fichier d'état, `etat.json`, est publié à côté de
`version.json`, sur le même dépôt, avec le même jeton.

L'adresse est déjà renseignée dans `config.py` :

```python
BLOCAGE_URL = ("https://raw.githubusercontent.com/"
               "caine777-data/podteleverseur-releases/main/etat.json")
```

## Bloquer ou débloquer

Dépôt Pod Téléverseur → **Actions** → workflow **Build installers** → **Run
workflow** → champ **blocage** : choisir **bloquer** ou **débloquer** → **Run
workflow**.

Ce choix **ne compile rien et ne publie aucune version**. Le run écrit
seulement `etat.json` (`{"bloque": true}` ou `false`) sur le dépôt public, en
moins d'une minute. Les autres champs du formulaire (version, notes,
obligatoire…) sont ignorés.

Laisser **« ne rien changer »** (valeur par défaut) pour une compilation ou une
publication normale. Sans action volontaire dans Actions, ce blocage ne
s'active donc jamais.

## Désactiver complètement la vérification

Mettre `BLOCAGE_URL = ""` dans `config.py` : plus aucune lecture réseau.

## Dans le code

- `maj.py` : `etat_blocage()` interroge `etat.json` et renvoie `True`, `False`
  ou `None` (jamais bloquant). Le téléchargement est mis en commun avec la
  mise à jour dans `_telecharger_json`.
- `config.py` : `BLOCAGE_URL`, `BLOCAGE_PERIODE_MS`, `BLOCAGE_TIMEOUT_S` ;
  mémorisation locale via `enregistrer_blocage_distant()` et
  `blocage_distant_actif()`.
- `app.py`, classe `App` : `_surveiller_blocage()` (thread, puis toutes les
  `BLOCAGE_PERIODE_MS`), `_appliquer_blocage()`, `_bloquer_application()`.
  Contrôle local dès le démarrage, avant la mise à jour obligatoire et avant
  l'auto-connexion.
- `.github/workflows/build.yml` : entrée `blocage` (`ne rien changer` /
  `bloquer` / `débloquer`), job `blocage` (n'écrit que `etat.json`), et garde
  sur les jobs de compilation et de publication, qui s'effacent quand le
  blocage est actionné.
- Tests : `tests/test_blocage_distant.py`.
