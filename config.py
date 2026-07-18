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
__version__     = "3.0.0"
__date__        = "2026"
__license__     = "Usage interne — Université de Toulouse"


import json
import os

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".pod_televerseur.json")
KEYRING_SERVICE = "PodTeleverseur-UToulouse"     # ≠ "PodAdmin-UToulouse"
KEYRING_TOKEN_KEY = "service_token"

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
CHUNK_THRESHOLD_BYTES = 500 * 1024 * 1024      # 500 Mo
CHUNK_SIZE_BYTES      = 2 * 1024 * 1024         # 2 Mo par morceau

# ── Vérification « lancer puis vérifier » après un 504 de finalisation ────
# nginx peut couper avant la fin de l'assemblage serveur ; Pod termine en fond.
# Fenêtre de 30 min (gros fichiers > 2 Go).
CHUNK_VERIFY_TIMEOUT_S  = 1800   # 30 minutes
CHUNK_VERIFY_INTERVAL_S = 15     # secondes entre deux sondages

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
