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


# ── Étape 3 : en-tête de pod_chunked.py ────────────────────────────────────

class TestEnTetePodChunked:
    """On lit les attributs du MODULE importé, pas le texte du fichier : un
    commentaire ou une docstring mentionnant l'ancienne adresse ne doit ni
    faire échouer ni faire passer le test."""

    def test_contact_de_service(self):
        import pod_chunked
        assert pod_chunked.__contact__ == "support-pod@utoulouse.fr"

    def test_version_alignee_sur_la_source_unique(self):
        import pod_chunked
        import __version__ as v
        assert pod_chunked.__version__ == v.__version__


# ── Étape 4 : le jeton ne part que vers l'instance configurée, en HTTPS ────

class TestJetonLimiteALInstance:
    """Toute requête porte le jeton. Une URL absolue venue du serveur (champ
    `next`, `url` d'une vidéo…) ne doit être suivie que si elle vise l'hôte de
    l'instance, en HTTPS ; sinon PodAPIError AVANT toute émission."""

    def test_meme_hote_accepte(self):
        api = _api()
        u = "https://pod.exemple.fr/rest/videos/3/"
        assert api._abs(u) == u
        assert api._abs("/videos/") == "https://pod.exemple.fr/rest/videos/"

    def test_hote_different_de_casse_accepte(self):
        # Les noms d'hôte sont insensibles à la casse : pas de faux refus.
        api = _api()
        assert api._abs("https://POD.Exemple.fr/rest/x/")

    @pytest.mark.parametrize("url", [
        "https://attaquant.exemple.com/rest/videos/",
        "https://pod.exemple.fr.attaquant.com/rest/videos/",
        "https://pod.exemple.fr:8443/rest/videos/",
    ])
    def test_hote_etranger_refuse(self, url):
        with pytest.raises(PodAPIError):
            _api()._abs(url)

    def test_meme_hote_en_http_refuse(self):
        with pytest.raises(PodAPIError):
            _api()._abs("http://pod.exemple.fr/rest/videos/")

    def test_pagination_next_etranger_arrete_tout(self):
        premiere = FausseReponse({"results": [{"id": 1}],
                                  "next": "https://attaquant.exemple.com/rest/videos/?page=2"})
        api = _api([premiere])
        with pytest.raises(PodAPIError):
            api._paginate("/videos/")
        hotes = [a["url"] for a in api.session.appels]
        assert hotes == ["https://pod.exemple.fr/rest/videos/"], hotes

    def test_pagination_next_http_arrete_tout(self):
        premiere = FausseReponse({"results": [], "next": "http://pod.exemple.fr/rest/videos/?page=2"})
        api = _api([premiere])
        with pytest.raises(PodAPIError):
            api._paginate("/videos/")
        assert len(api.session.appels) == 1

    def test_pagination_normale_suit_next(self):
        p1 = FausseReponse({"results": [{"id": 1}],
                            "next": "https://pod.exemple.fr/rest/videos/?page=2"})
        p2 = FausseReponse({"results": [{"id": 2}], "next": None})
        api = _api([p1, p2])
        assert [v["id"] for v in api._paginate("/videos/")] == [1, 2]

    def test_patch_sur_url_video_etrangere_refuse(self):
        api = _api()
        with pytest.raises(PodAPIError):
            api.patch_video({"url": "https://attaquant.exemple.com/rest/videos/7/"},
                            {"is_draft": False})
        assert api.session.appels == []

    def test_remplacement_sur_url_video_etrangere_refuse(self, fichier_video):
        api = _api()
        with pytest.raises(PodAPIError):
            api.replace_video_file({"url": "https://attaquant.exemple.com/rest/videos/7/"},
                                   fichier_video)
        assert api.session.appels == []


# ── Étape 5 : position d'envoi par morceaux vérifiée ───────────────────────

class TestPositionEnvoiParMorceaux:
    """Fichier de 5 octets, morceaux de 2 → trois morceaux (0-1, 2-3, 4-4).
    `_send_one_chunk` et `_complete` sont remplacés sur l'INSTANCE : aucun
    réseau, et on observe exactement ce que la boucle envoie."""

    @staticmethod
    def _session(offsets_renvoyes):
        from pod_chunked import PodChunkedSession
        s = PodChunkedSession("https://pod.exemple.fr", "DEPOT", "x")
        s._logged_in = True
        s.envois = []
        s.finalisations = []
        reponses = iter(offsets_renvoyes)

        def faux_envoi(chunk, start, end, total, filename, upload_id, **kw):
            s.envois.append((start, end, bytes(chunk)))
            return {"upload_id": "U1", "offset": next(reponses)}

        def fausse_finalisation(upload_id, md5, target_slug=""):
            s.finalisations.append((upload_id, md5))
            return "slug-final"

        s._send_one_chunk = faux_envoi
        s._complete = fausse_finalisation
        return s

    @pytest.fixture
    def cinq_octets(self):
        fd, chemin = tempfile.mkstemp(suffix=".mp4")
        with os.fdopen(fd, "wb") as f:
            f.write(b"ABCDE")
        yield chemin
        os.remove(chemin)

    def test_serveur_coherent(self, cinq_octets):
        s = self._session([2, 4, 5])
        assert s.upload_video_chunked(cinq_octets, chunk_size=2) == "slug-final"
        assert s.envois == [(0, 1, b"AB"), (2, 3, b"CD"), (4, 4, b"E")]
        assert len(s.finalisations) == 1

    def test_offset_faux_au_deuxieme_morceau(self, cinq_octets):
        from pod_chunked import PodChunkedError
        s = self._session([2, 3, 5])      # le serveur dit 3 au lieu de 4
        with pytest.raises(PodChunkedError, match="Désaccord de position"):
            s.upload_video_chunked(cinq_octets, chunk_size=2)
        assert len(s.envois) == 2, s.envois       # aucun 3e envoi
        assert s.finalisations == []              # _complete jamais appelé

    def test_offset_en_avance_refuse_aussi(self, cinq_octets):
        from pod_chunked import PodChunkedError
        s = self._session([4, 4, 5])      # le serveur prétend avoir tout reçu
        with pytest.raises(PodChunkedError):
            s.upload_video_chunked(cinq_octets, chunk_size=2)
        assert len(s.envois) == 1 and s.finalisations == []
