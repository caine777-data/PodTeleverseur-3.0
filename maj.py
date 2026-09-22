"""
maj.py — Vérification de la disponibilité d'une nouvelle version
================================================================
L'application interroge, au démarrage, un petit fichier `version.json` hébergé
sur un dépôt GitHub PUBLIC. Si une version plus récente existe, elle affiche un
bandeau discret avec un lien de téléchargement.

PRINCIPES
---------
• **Jamais bloquant PAR DÉFAUT.** Pas de réseau, fichier absent, serveur
  injoignable, contenu illisible : la vérification échoue en silence et
  l'application démarre normalement. Une vérification qui empêche de
  travailler PAR ACCIDENT serait pire que pas de vérification du tout.
  Ce principe connaît UNE exception volontaire et choisie au cas par cas :
  le champ `obligatoire`, décrit plus bas — réservé aux cas où continuer
  présenterait un vrai risque technique.
• **Aucun secret.** Le dépôt interrogé est public et ne contient que le fichier
  de version et les exécutables. Le dépôt de code, lui, reste privé (il contient
  le mot de passe du compte véhicule).
• **Informer, pas contraindre — sauf décision explicite et publiée.** Par
  défaut, même une version très ancienne n'empêche pas d'utiliser
  l'application : le ton du message se durcit, mais rien n'est jamais bloqué.
  Un utilisateur empêché de travailler à un mauvais moment appellerait le
  support — à juste titre. Le champ `obligatoire` permet de sortir de ce
  principe pour une publication précise, quand continuer serait dangereux.

FORMAT ATTENDU (version.json)
-----------------------------
```json
{
  "version": "1.1.0",
  "url": "https://github.com/caine777-data/podteleverseur-releases/releases/latest",
  "notes": "Correction de l'affichage des groupes d'accès.",
  "version_minimale": "1.0.0",
  "obligatoire": false
}
```
Seul `version` est obligatoire.

`version_minimale` désigne la version en dessous de laquelle l'application est
considérée comme périmée (par exemple après une rotation du mot de passe du
compte véhicule, qui rend les anciennes versions incapables de téléverser les
gros fichiers).

`obligatoire` décide de la RÉACTION une fois ce seuil franchi :
  • false (par défaut) — le bandeau devient insistant, l'application démarre
    normalement. C'est le principe « informer, pas contraindre » ci-dessus.
  • true — l'application affiche une fenêtre BLOQUANTE avant toute autre
    chose et ne démarre pas. Réservé aux cas où continuer serait dangereux
    (ex. le mot de passe du compte véhicule a changé et un dépôt échouerait
    en abîmant des fichiers à moitié envoyés). Ce n'est PAS un dispositif de
    licence ou de contrôle d'accès : il ne s'active que sur un vrai risque
    technique, choisi au cas par cas lors de chaque publication, et un
    réseau coupé ne le déclenche jamais (voir `recuperer_info`, qui renvoie
    alors None — aucune information, donc aucun blocage possible).
"""

__author__ = "Cédric MONNA"

import json
import urllib.request


def comparer_versions(a: str, b: str) -> int:
    """Compare deux numéros de version. Renvoie -1, 0 ou 1 (a<b, a==b, a>b).

    ATTENTION au piège : comparer des CHAÎNES donnerait "1.10.0" < "1.9.0",
    puisque le caractère « 1 » précède « 9 ». On compare donc les nombres
    composante par composante.

    Les suffixes non numériques (« 1.2.0-beta ») sont ignorés, et les longueurs
    différentes sont complétées par des zéros (« 1.2 » équivaut à « 1.2.0 »).
    """
    def morceaux(v: str) -> list:
        """Découpe « 1.10.0 » en [1, 10, 0], en ignorant tout suffixe."""
        out = []
        for bloc in str(v or "0").strip().split("."):
            chiffres = ""
            for c in bloc:
                if c.isdigit():
                    chiffres += c
                else:
                    break            # on s'arrête au premier caractère non numérique
            out.append(int(chiffres) if chiffres else 0)
        return out

    ma, mb = morceaux(a), morceaux(b)
    taille = max(len(ma), len(mb))
    ma += [0] * (taille - len(ma))       # « 1.2 » == « 1.2.0 »
    mb += [0] * (taille - len(mb))
    for x, y in zip(ma, mb):
        if x != y:
            return -1 if x < y else 1
    return 0


def recuperer_info(url: str, timeout: float = 5.0, journal=None) -> dict | None:
    """Télécharge et analyse le fichier de version. Renvoie None en cas d'échec.

    Toute erreur est absorbée : réseau coupé, adresse fausse, dépôt supprimé,
    JSON malformé… La vérification est un confort, pas une dépendance.

    `journal` : fonction appelée avec un message en cas d'échec. Sans elle,
    l'échec est totalement silencieux — pratique pour l'utilisateur, mais
    impossible à diagnostiquer. C'est précisément ce qui a rendu difficile
    l'analyse d'une panne sur macOS.

    DEUX MOYENS D'ACCÈS, dans cet ordre :

    1. `requests`, quand il est disponible — c'est le cas ici, l'application
       s'en sert déjà pour l'API. Il embarque ses propres certificats
       racine (certifi), ce qui le rend fiable partout.
    2. `urllib` en repli.

    Pourquoi cet ordre : `urllib` s'appuie sur les certificats du système.
    Sous Windows cela fonctionne, mais dans une application macOS compilée les
    certificats racine sont souvent introuvables, et la requête échoue avec une
    erreur de vérification TLS — sans que rien ne l'indique.
    """
    if not url:
        return None                      # vérification désactivée

    entetes = {"User-Agent": "PodAdmin-MAJ", "Accept": "application/json"}
    brut = None

    # 1. Voie principale : requests (certificats embarqués)
    try:
        import requests
        r = requests.get(url, headers=entetes, timeout=timeout)
        if r.status_code != 200:
            if journal:
                journal(f"vérification de mise à jour : HTTP {r.status_code}")
            return None
        brut = r.text
    except ImportError:
        pass                             # on tente urllib plus bas
    except Exception as e:
        if journal:
            journal(f"vérification de mise à jour impossible ({type(e).__name__}) : {e}")
        return None

    # 2. Repli : urllib
    if brut is None:
        try:
            requete = urllib.request.Request(url, headers=entetes)
            with urllib.request.urlopen(requete, timeout=timeout) as reponse:
                if getattr(reponse, "status", 200) != 200:
                    return None
                brut = reponse.read().decode("utf-8", "replace")
        except Exception as e:
            if journal:
                journal(f"vérification de mise à jour impossible ({type(e).__name__}) : {e}")
            return None

    try:
        donnees = json.loads(brut)
    except Exception as e:
        if journal:
            journal(f"fichier de version illisible : {e}")
        return None
    if not isinstance(donnees, dict) or not donnees.get("version"):
        if journal:
            journal("fichier de version présent mais sans numéro exploitable.")
        return None
    return donnees


def etat_mise_a_jour(version_actuelle: str, url: str, timeout: float = 5.0,
                     journal=None, infos: dict | None = None) -> dict | None:
    """Compare la version installée à celle publiée.

    Renvoie None si tout va bien (à jour, ou vérification impossible), sinon un
    dictionnaire :
        {"version": "1.1.0", "url": "...", "notes": "...", "urgent": False}

    `urgent` est vrai lorsque la version installée est antérieure à la
    `version_minimale` annoncée. `obligatoire` (même seuil, réaction
    différente) indique si l'appelant doit BLOQUER le démarrage plutôt que se
    contenter d'un bandeau insistant — voir le format documenté en tête de
    fichier.

    `infos` : si l'appelant a DÉJÀ récupéré le fichier de version (via
    `recuperer_info`) pour un autre usage — par exemple distinguer "à jour
    confirmé" d'un simple échec réseau — il peut le transmettre ici pour
    éviter un second appel réseau redondant. Sinon, il est téléchargé ici.
    """
    if infos is None:
        infos = recuperer_info(url, timeout, journal=journal)
    if not infos:
        return None
    derniere = str(infos.get("version", "")).strip()
    if comparer_versions(version_actuelle, derniere) >= 0:
        return None                      # déjà à jour (ou en avance : build local)

    minimale = str(infos.get("version_minimale", "") or "").strip()
    urgent = bool(minimale) and comparer_versions(version_actuelle, minimale) < 0
    # L'obligation n'a de sens QUE si le seuil est lui-même franchi : un
    # « obligatoire: true » dans le fichier ne doit jamais forcer un blocage
    # pour un simple retard mineur, hors de portée de version_minimale.
    obligatoire = urgent and bool(infos.get("obligatoire", False))

    return {
        "version": derniere,
        "url": str(infos.get("url", "") or ""),
        "notes": str(infos.get("notes", "") or ""),
        "urgent": urgent,
        "obligatoire": obligatoire,
    }
