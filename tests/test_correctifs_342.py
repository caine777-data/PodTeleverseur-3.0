"""Tests des correctifs de la 3.0.1, réintégrés en 3.4.2.

La 3.0.1 (audit de sécurité) n'avait jamais été poussée : ses correctifs ont
été réécrits sur le code 3.4.1. Chaque test ci-dessous a été éprouvé par
MUTATION : le défaut a été réintroduit, le test a échoué, puis le code a été
restauré. Aucun test n'accède au réseau : les sessions HTTP sont simulées.
"""
import os
import tempfile

import pytest

import pod_api
from pod_api import PodAPI, PodAPIError


# ── Outils communs : faux réseau ───────────────────────────────────────────

class FausseReponse:
    """Réponse HTTP minimale, suffisante pour PodAPI._json()."""

    def __init__(self, donnees=None, status=200, url="https://pod.exemple.fr/"):
        import json
        self.status_code = status
        self.url = url
        self._donnees = donnees if donnees is not None else {}
        self.text = json.dumps(self._donnees)

    def json(self):
        return self._donnees


class FausseSession:
    """Remplace requests.Session : enregistre chaque appel au lieu de l'émettre.

    `reponses` est une liste consommée dans l'ordre ; à défaut, on renvoie {}.
    Le corps multipart en streaming est lu jusqu'au bout, comme le ferait
    requests, afin que le fichier source soit réellement parcouru."""

    def __init__(self, reponses=None):
        self.appels = []
        self.reponses = list(reponses or [])
        self.headers = {}

    def _repondre(self, methode, url, **kw):
        self.appels.append({"methode": methode, "url": url, **kw})
        data = kw.get("data")
        if hasattr(data, "read"):
            while data.read(65536):
                pass
        if self.reponses:
            return self.reponses.pop(0)
        return FausseReponse({"url": url, "slug": "x"})

    def get(self, url, **kw):
        return self._repondre("GET", url, **kw)

    def post(self, url, **kw):
        return self._repondre("POST", url, **kw)

    def patch(self, url, **kw):
        return self._repondre("PATCH", url, **kw)

    def delete(self, url, **kw):
        return self._repondre("DELETE", url, **kw)

    def options(self, url, **kw):
        return self._repondre("OPTIONS", url, **kw)


def _api(reponses=None):
    api = PodAPI("https://pod.exemple.fr", "jeton-de-test")
    api.session = FausseSession(reponses)
    return api


@pytest.fixture
def fichier_video():
    fd, chemin = tempfile.mkstemp(suffix=".mp4")
    with os.fdopen(fd, "wb") as f:
        f.write(b"0123456789" * 10)
    yield chemin
    os.remove(chemin)


# ── Étape 1 : délais réseau finis sur les envois de fichier ────────────────

class TestDelaisReseauFinis:
    """Avec timeout=None, une connexion figée attendait indéfiniment et les
    ré-essais ne se déclenchaient jamais. Les quatre envois (création et
    remplacement, avec ou sans requests-toolbelt) doivent passer un délai fini."""

    @staticmethod
    def _verifier(appel):
        t = appel["timeout"]
        assert isinstance(t, tuple) and len(t) == 2, (
            f"{appel['methode']} {appel['url']} : délai {t!r}, attendu un "
            f"tuple (connexion, lecture) fini")
        assert all(isinstance(x, (int, float)) and x > 0 for x in t), t

    @pytest.mark.parametrize("toolbelt", [True, False])
    def test_creation(self, monkeypatch, fichier_video, toolbelt):
        if toolbelt and not pod_api.HAS_TOOLBELT:
            pytest.skip("requests-toolbelt absent")
        monkeypatch.setattr(pod_api, "HAS_TOOLBELT", toolbelt)
        api = _api()
        api.upload_video(fichier_video, "Titre", "https://pod.exemple.fr/rest/users/1/",
                         "https://pod.exemple.fr/rest/types/1/")
        (appel,) = [a for a in api.session.appels if a["methode"] == "POST"]
        self._verifier(appel)

    @pytest.mark.parametrize("toolbelt", [True, False])
    def test_remplacement(self, monkeypatch, fichier_video, toolbelt):
        if toolbelt and not pod_api.HAS_TOOLBELT:
            pytest.skip("requests-toolbelt absent")
        monkeypatch.setattr(pod_api, "HAS_TOOLBELT", toolbelt)
        api = _api()
        api.replace_video_file({"url": "https://pod.exemple.fr/rest/videos/7/"},
                               fichier_video)
        (appel,) = [a for a in api.session.appels if a["methode"] == "PATCH"]
        self._verifier(appel)


# ── Étape 2 : méthodes d'administration retirées ───────────────────────────

class TestMethodesAdministrationRetirees:
    """set_user_staff / set_user_groups modifiaient les droits d'un compte :
    rien à faire dans une appli d'enseignants. On vérifie la CLASSE réelle et
    l'arbre syntaxique du code (pas une recherche de sous-chaîne, qui serait
    trompée par le commentaire expliquant leur retrait)."""

    NOMS = ("set_user_staff", "set_user_groups")

    @pytest.mark.parametrize("nom", NOMS)
    def test_absente_de_la_classe(self, nom):
        assert not hasattr(PodAPI, nom), f"PodAPI.{nom} existe encore"

    def test_aucun_appel_ni_definition_dans_le_code(self):
        import ast
        racine = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        trouves = []
        for dossier, _, fichiers in os.walk(racine):
            if ".git" in dossier:
                continue
            for nom_fichier in fichiers:
                if not nom_fichier.endswith(".py"):
                    continue
                chemin = os.path.join(dossier, nom_fichier)
                with open(chemin, encoding="utf-8") as f:
                    arbre = ast.parse(f.read())
                for n in ast.walk(arbre):
                    nom = (n.name if isinstance(n, ast.FunctionDef)
                           else n.attr if isinstance(n, ast.Attribute) else None)
                    if nom in self.NOMS:
                        trouves.append(f"{nom_fichier}:{n.lineno} {nom}")
        assert not trouves, trouves
