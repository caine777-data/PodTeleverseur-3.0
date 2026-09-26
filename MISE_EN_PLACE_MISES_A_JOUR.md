# Mise en place des mises à jour — Pod Téléverseur

À faire **une seule fois**. Ensuite, chaque publication met à jour les postes
installés sans aucune intervention manuelle.

## Principe

```
Dépôt PRIVÉ  PodTeleverseur-2.0          Dépôt PUBLIC  podteleverseur-releases
  code source                               version.json   ← lu par l'application
  workflow de compilation  ──publie──►      Releases       ← page « Télécharger »
```

Le code reste privé. Seuls les exécutables et le petit fichier `version.json`
sont publics : un enseignant doit pouvoir télécharger sans compte GitHub.

## Étape 1 — Créer le dépôt public

Sur GitHub, compte `caine777-data` :

- **New repository** → nom : `podteleverseur-releases`
- **Public**
- Cocher **Add a README file** — ⚠️ indispensable : le workflow clone ce dépôt,
  et un dépôt totalement vide ne se clone pas.

## Étape 2 — Créer le jeton d'écriture

Le jeton automatique de GitHub Actions n'a aucun droit en dehors du dépôt où il
s'exécute. Il en faut un autre pour écrire sur le dépôt public.

GitHub → photo de profil → **Settings** → **Developer settings** →
**Personal access tokens** → **Fine-grained tokens** → **Generate new token** :

- Nom : `podteleverseur-releases`
- Expiration : 1 an (noter la date : à l'échéance, la notification cessera
  silencieusement)
- Repository access : **Only select repositories** → `podteleverseur-releases`
- Permissions → Repository permissions → **Contents : Read and write**

Copier le jeton affiché — il ne sera plus visible ensuite.

> Le même jeton peut servir à PodAdmin si le dépôt `podadmin-releases` est ajouté
> à sa liste de dépôts autorisés. Sinon, en créer un second.

## Étape 3 — Enregistrer le jeton dans le dépôt PRIVÉ

Dans le dépôt **du code** (et non le public) : **Settings** → **Secrets and
variables** → **Actions** → **New repository secret** :

- Name : `RELEASES_TOKEN`
- Secret : le jeton copié à l'étape 2

## Étape 4 — Publication normale (bandeau, non bloquant)

Onglet **Actions** → **Run workflow** :

- **version** : écrire `OUI` — ⚠️ champ VIDE = compilation d'essai, rien n'est
  publié. C'est le piège le plus fréquent.
- **notes** : la phrase affichée dans le bandeau (facultatif)
- **obligatoire** : laisser **décoché**
- **version_minimale** : laisser **vide**

Le numéro est lu dans `__version__.py` : ce qui est saisi dans le formulaire ne
sert qu'à déclencher la publication.

## Étape 4 bis — Publication OBLIGATOIRE (fenêtre bloquante)

⚠️ **À réserver aux cas où les anciennes versions ne doivent plus être
utilisées.**

Onglet **Actions** → **Run workflow** :

- **version** : `OUI`
- **notes** : facultatif. ⚠️ Ce texte n'apparaît PAS dans la fenêtre de
  blocage, qui affiche toujours le même message neutre, sans raison :
  « Une nouvelle version de Pod Téléverseur est nécessaire pour continuer.
  Téléchargez-la et installez-la. »
- **obligatoire** : **cocher**
- **version_minimale** :
  - laisser **vide** → TOUT poste non encore sur cette version sera bloqué
    (le seuil devient la version publiée elle-même) ;
  - ou préciser une version antérieure précise (ex. `2.5.0`) si seules les
    versions plus anciennes que celle-ci doivent être bloquées, les autres
    recevant un bandeau normal.

### Comportement côté poste enseignant

1. **Poste connecté au moment du lancement** : le serveur confirme le
   blocage → fenêtre modale immédiate, message neutre, deux seules issues :
   « Télécharger la mise à jour » ou « Quitter » (la croix et Alt+F4
   quittent aussi). Ce blocage est alors **mémorisé localement** sur ce
   poste.
2. **Poste relancé plus tard, même SANS réseau** : le blocage **reste actif**
   — c'est volontaire, pour empêcher qu'une personne déjà notifiée contourne
   le blocage en coupant sa connexion. Le blocage ne se lève que par une
   mise à jour réelle vers une version qui n'est plus concernée.
3. **Poste jamais encore connecté au moment d'un blocage** : tant que le
   serveur n'a jamais pu répondre, ce poste démarre normalement — un réseau
   absent ne peut jamais DÉCLENCHER un nouveau blocage, seulement le
   maintenir une fois qu'il a été confirmé.

## Étape 4 ter — Bloquer ou débloquer toutes les copies installées

Indépendant de la mise à jour : champ **blocage** du formulaire (« bloquer » /
« débloquer » ; laisser « ne rien changer » pour une publication). Ne compile
rien, ne publie aucune version : écrit seulement `etat.json` sur le dépôt
public. Détails dans **`BLOCAGE.md`**.

## Étape 5 — Vérifier

1. Le dépôt public contient un `version.json` à la bonne version, et une
   Release avec **deux fichiers** (un installeur Windows, un `.dmg` macOS —
   plus de version portable depuis la 2.2.x).
2. Ouvrir dans un navigateur :
   `https://raw.githubusercontent.com/caine777-data/podteleverseur-releases/main/version.json`
   et vérifier que `obligatoire` et `version_minimale` correspondent à ce qui
   a été saisi dans le formulaire.
3. Lancer une version **antérieure** du Téléverseur :
   - publication normale → le bandeau doit apparaître dans les deux
     secondes ; Onglet Journal : une ligne « Mise à jour — … » confirme que
     la vérification a eu lieu ;
   - publication obligatoire → une fenêtre bloquante doit apparaître
     immédiatement, sans possibilité de la fermer par la croix.

⚠️ Une version **égale ou plus récente** n'affiche ni bandeau ni blocage :
c'est normal. Pour tester, il faut une version plus ancienne sous la main.

## En cas de problème

| Symptôme | Cause probable |
|---|---|
| Aucune Release créée | champ `version` laissé vide |
| Release créée, pas de `version.json` | secret `RELEASES_TOKEN` absent ou mal nommé |
| Échec « 403 » | jeton sans droit *Contents : Read and write* sur le dépôt public |
| Échec au clonage | dépôt public créé sans README |
| Bandeau jamais affiché | version installée ≥ version publiée ; voir le Journal |
| Blocage jamais affiché malgré `obligatoire` coché | (1) `version_minimale` non franchie par la version installée testée ; (2) la version testée n'était pas ANTÉRIEURE à la version publiée — une version égale ou supérieure ne se bloque jamais elle-même ; (3) régression corrigée en 2.3.0 : le formulaire n'était pas transmis au fichier `version.json` généré — vérifier que le fichier publié contient bien `"obligatoire": true` |
| Une personne reste bloquée alors qu'une correction vient d'être publiée en NON obligatoire | normal : le verrou local persiste tant que la version installée n'a pas réellement changé — seule une vraie mise à jour le lève |
