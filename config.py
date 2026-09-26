#!/usr/bin/env python3
"""
config.py — Configuration et stockage sécurisé des identifiants (Pod Téléverseur).

• L'URL de l'instance + les préférences → fichier JSON (~/.pod_televerseur.json)
• Le TOKEN → coffre-fort natif de l'OS via keyring
  (Windows Credential Manager / macOS Keychain). Jamais en clair sur disque.

⚠️ Le token est stocké sous une clé DIFFÉRENTE de celle de « PodAdmin » : les
   deux applications peuvent cohabiter sur un même poste sans se marcher dessus.
"""

from __future__ import annotations

__author__      = "Cédric MONNA, Philippe BAQUÉ, Michel JACOB"
__contact__     = "support-pod@utoulouse.fr"
__institution__ = "Université de Toulouse"
from __version__ import __version__   # source unique (voir __version__.py)
__date__        = "2026"
__license__     = "Usage interne — Université de Toulouse"


import json
import os

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".pod_televerseur.json")
KEYRING_SERVICE = "PodTeleverseur-UToulouse"     # ≠ "PodAdmin-UToulouse"
KEYRING_TOKEN_KEY = "service_token"

# ── Identifiant universitaire du propriétaire des vidéos ────────────────────
# L'enseignant SAISIT son identifiant au lieu de choisir dans l'annuaire de
# tous les comptes. Format relevé par la sonde verifier_identifiant.py
# (26/09/2026) : les 32 comptes d'usagers sont TOUS « 3 lettres, 4 chiffres,
# 1 lettre » (ex. abc1234a). Les 9 autres comptes sont des comptes LOCAUX
# d'administration (DEPOT…) : le format strict les écarte d'office, si bien
# qu'aucun dépôt ne peut être attribué à l'un d'eux.
IDENTIFIANT_FORMAT = r"^[a-z]{3}[0-9]{4}[a-z]$"

# ── Compte VÉHICULE embarqué (session web pour le chunké des gros fichiers) ──
# Compte LOCAL sans privilège, servant UNIQUEMENT à ouvrir la session web du
# téléversement par morceaux. La vidéo naît à son nom puis est AUSSITÔT
# réattribuée au propriétaire choisi (via le token is_staff de l'enseignant).
#
# ⚠️ SÉCURITÉ : ces identifiants sont embarqués dans l'application distribuée aux
#    enseignants, donc techniquement EXTRACTIBLES d'un exe. C'est acceptable
#    UNIQUEMENT parce que ce compte est LOCAL et SANS PRIVILÈGE : au pire, un
#    curieux pourrait déposer une vidéo au nom de ce compte — rien de plus.
#    → Ce compte ne doit JAMAIS être superutilisateur ni staff.
#    → En cas de rotation du mot de passe, il faut recompiler et redistribuer.
VEHICLE_USERNAME = "DEPOT"
VEHICLE_PASSWORD = "V&xehx7WB!iBWLoL%97HDjK&kg"

# ── Bascule vers le téléversement par morceaux (chunked) ──────────────────
CHUNK_THRESHOLD_BYTES = 150 * 1024 * 1024      # 150 Mo
CHUNK_SIZE_BYTES      = 2 * 1024 * 1024         # 2 Mo par morceau

# ── Vérification « lancer puis vérifier » après un 504 de finalisation ────
# nginx peut couper avant la fin de l'assemblage serveur ; Pod termine en fond.
# Fenêtre de 30 min (gros fichiers > 2 Go).
CHUNK_VERIFY_TIMEOUT_S  = 1800   # 30 minutes
CHUNK_VERIFY_INTERVAL_S = 15     # secondes entre deux sondages

# Après un 502 ou un 503, en revanche, l'attente est COURTE (repris de PodAdmin
# 1.9.2). Ces codes ne disent pas « Pod est encore en train de travailler »
# (c'est le 504) mais « Pod a répondu par une erreur, ou n'a pas traité la
# demande ». Constaté le 25/09/2026 : un 502 est tombé 44 s après le début de
# l'envoi, et la vidéo n'est jamais apparue. Attendre 30 min pour rien
# bloquait tout le lot ; trois minutes suffisent à rattraper le cas où Pod
# aurait malgré tout terminé.
CHUNK_VERIFY_TIMEOUT_502_S = 180   # 3 minutes

try:
    import keyring
    HAS_KEYRING = True
except ImportError:
    HAS_KEYRING = False


DEFAULTS = {
    "url": "https://videos.utoulouse.fr",
    "type_url": "",          # URL du type par défaut (ex : .../rest/types/1/)
    "main_lang": "fr",
    "cursus": "0",
    "is_draft": True,
    "agent_username": "",    # qui dépose (devient owner) — onglet Téléversement
    "agent_owner_url": "",   # URL résolue de l'agent
}


def load_config() -> dict:
    """Charge la configuration (URL + préférences) depuis le fichier JSON personnel."""
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


# ── Mise à jour OBLIGATOIRE : mémorisation locale du blocage confirmé ──────
#
# Principe retenu (« modèle 1 durci », choisi après discussion sur les
# risques d'un blocage qui dépendrait d'une disponibilité réseau
# permanente) :
#
#   1. Tant que le serveur n'a jamais confirmé l'obligation pour CETTE
#      version installée, l'application démarre normalement — un réseau
#      coupé, un dépôt GitHub injoignable ou un token expiré ne doivent
#      JAMAIS empêcher tout le monde de travailler.
#   2. Le jour où le serveur RÉPOND et confirme l'obligation pour la
#      version installée, ce fait est enregistré ICI, localement. Aux
#      lancements suivants, le blocage s'applique MÊME SANS RÉSEAU : on
#      empêche ainsi qu'une personne notifiée une fois contourne le
#      blocage en coupant simplement sa connexion ensuite.
#   3. Le verrou local ne vaut QUE pour la version qui l'a déclenché : dès
#      que l'application est mise à jour vers une version qui n'est plus
#      concernée, elle redémarre normalement sans avoir besoin du réseau
#      pour "prouver" qu'elle est à jour.
#
# Ce mécanisme protège contre un contournement volontaire une fois notifié ;
# il ne transforme jamais une panne réseau générale en arrêt total du
# service pour des postes qui n'ont jamais été notifiés.

def enregistrer_blocage_confirme(version_bloquee: str, version_minimale: str,
                                 url: str = "", notes: str = "") -> None:
    """Mémorise qu'un blocage a été confirmé par le serveur pour cette version.

    `url` et `notes` sont conservées pour que la fenêtre bloquante rejouée
    HORS LIGNE (voir `blocage_local_actif`) garde son lien de téléchargement
    et son message — sans elles, un lancement sans réseau afficherait un
    blocage muet, sans moyen d'agir.

    Appelée uniquement après une réponse RÉSEAU RÉELLE et positive du
    serveur (voir `maj.etat_mise_a_jour`) — jamais de manière spéculative."""
    try:
        cfg = load_config()
        cfg["maj_obligatoire_version"] = str(version_bloquee)
        cfg["maj_obligatoire_minimale"] = str(version_minimale)
        cfg["maj_obligatoire_url"] = str(url or "")
        cfg["maj_obligatoire_notes"] = str(notes or "")
        save_config(cfg)
    except Exception:
        pass          # ne jamais lever depuis un enregistrement de confort


def blocage_local_actif(version_actuelle: str) -> dict | None:
    """Renvoie les infos du blocage mémorisé SI il s'applique encore à la
    version actuellement lancée (dict avec version/url/notes), sinon None.

    Ne s'applique que si `version_actuelle` correspond exactement à la
    version qui avait été bloquée : une mise à jour vers une version plus
    récente lève le verrou local automatiquement, sans avoir besoin du
    réseau pour le constater."""
    try:
        cfg = load_config()
        bloquee = str(cfg.get("maj_obligatoire_version", "") or "")
        if bloquee and bloquee == str(version_actuelle):
            minimale = str(cfg.get("maj_obligatoire_minimale", "") or "")
            if minimale:
                return {
                    "version": minimale,
                    "url": str(cfg.get("maj_obligatoire_url", "") or ""),
                    "notes": str(cfg.get("maj_obligatoire_notes", "") or ""),
                }
    except Exception:
        pass
    return None


def lever_blocage_local() -> None:
    """Efface le verrou local (utilisé quand une version plus récente est
    détectée : le blocage n'a plus lieu d'être, autant nettoyer le fichier)."""
    try:
        cfg = load_config()
        cfg.pop("maj_obligatoire_version", None)
        cfg.pop("maj_obligatoire_minimale", None)
        cfg.pop("maj_obligatoire_url", None)
        cfg.pop("maj_obligatoire_notes", None)
        save_config(cfg)
    except Exception:
        pass


# ── Token : coffre-fort de l'OS si possible, sinon fichier local ──────────


def load_theme() -> str:
    """Renvoie le mode d'apparence enregistré : « dark » ou « light ».

    Sombre par défaut, qui était le seul mode avant la version 3.1 : un
    utilisateur qui n'a jamais touché au réglage retrouve l'application telle
    qu'il l'a connue."""
    valeur = str(load_config().get("theme", "dark")).lower()
    return valeur if valeur in ("dark", "light") else "dark"


def save_theme(mode: str) -> None:
    """Enregistre le mode d'apparence.

    Silencieux en cas d'échec : ne pas pouvoir retenir une préférence
    d'affichage ne doit jamais empêcher de travailler."""
    try:
        cfg = load_config()
        cfg["theme"] = "light" if str(mode).lower() == "light" else "dark"
        save_config(cfg)
    except Exception:
        pass


def save_config(cfg: dict) -> None:
    """Sauvegarde la configuration. Le token n'est JAMAIS écrit dans le JSON."""
    safe = {k: v for k, v in cfg.items() if k != "token"}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(safe, f, indent=2, ensure_ascii=False)


# ── Token : coffre-fort de l'OS si possible, sinon fichier local ──────────

def _token_file() -> str:
    """Chemin de repli pour le token si le coffre-fort de l'OS est indisponible."""
    return os.path.join(os.path.expanduser("~"), ".pod_televerseur_token")


def save_token(token: str) -> str:
    """Enregistre le token. Renvoie 'keyring' ou 'file' selon le moyen utilisé."""
    if HAS_KEYRING:
        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_TOKEN_KEY, token)
            try:
                p = _token_file()
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
            return "keyring"
        except Exception:
            pass  # backend indisponible → on bascule sur le fichier
    try:
        path = _token_file()
        with open(path, "w", encoding="utf-8") as f:
            f.write(token)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        return "file"
    except Exception:
        return ""


def load_token() -> str:
    """Lit le token depuis le coffre-fort de l'OS, sinon depuis le fichier de repli."""
    if HAS_KEYRING:
        try:
            t = keyring.get_password(KEYRING_SERVICE, KEYRING_TOKEN_KEY)
            if t:
                return t
        except Exception:
            pass
    path = _token_file()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""


def clear_token() -> None:
    """Efface le token du poste (coffre-fort de l'OS + fichier de repli)."""
    if HAS_KEYRING:
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_TOKEN_KEY)
        except Exception:
            pass
    path = _token_file()
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass


# Extensions vidéo reconnues lors du scan de dossier (onglet Téléversement)
VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v",
    ".wmv", ".flv", ".mpg", ".mpeg", ".ts", ".mts",
}


# ════════════════════════════════════════════════════════════════════════════
#  MISE À JOUR
# ════════════════════════════════════════════════════════════════════════════
# Fichier consulté au démarrage pour savoir si une version plus récente existe.
#
# Il est hébergé sur un dépôt PUBLIC distinct (podteleverseur-releases), et non
# sur le dépôt du code, qui est privé : un enseignant n'a évidemment pas accès
# à ce dernier, et la page de téléchargement doit lui rester accessible sans
# compte GitHub.
#
# Le workflow de compilation réécrit ce fichier à chaque publication : il n'y a
# rien à modifier à la main.
UPDATE_URL = ("https://raw.githubusercontent.com/"
              "caine777-data/podteleverseur-releases/main/version.json")

# Page de secours si jamais un `version.json` (ou un verrou local ancien)
# n'a pas d'URL renseignée. Sans cela, une fenêtre de blocage OBLIGATOIRE se
# retrouverait sans AUCUN moyen d'agir — un blocage total sans issue, ce
# qu'aucune configuration ne doit jamais produire.
UPDATE_FALLBACK_URL = "https://github.com/caine777-data/podteleverseur-releases/releases/latest"

# Délai maximal accordé à la vérification. Volontairement court : elle ne doit
# JAMAIS retarder le démarrage, ni l'empêcher si le réseau est lent ou coupé.
UPDATE_TIMEOUT_S = 5
