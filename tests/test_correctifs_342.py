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


# ── Étape 6 : réattribution après un 504 guidée par un marqueur unique ─────

class TestMarqueurDansLeNomTransmis:
    """Le marqueur est ajouté au nom transmis en CRÉATION, jamais en
    REMPLACEMENT (la vidéo cible y est désignée par son slug)."""

    @staticmethod
    def _noms_transmis(chemin, **kw):
        from pod_chunked import PodChunkedSession
        s = PodChunkedSession("https://pod.exemple.fr", "DEPOT", "x")
        s._logged_in = True
        noms = []

        def faux_envoi(chunk, start, end, total, filename, upload_id, **k):
            noms.append(filename)
            return {"upload_id": "U1", "offset": end + 1}

        s._send_one_chunk = faux_envoi
        s._complete = lambda upload_id, md5, target_slug="": "slug"
        s.upload_video_chunked(chemin, chunk_size=64, **kw)
        return set(noms)

    def test_creation_porte_le_marqueur(self, fichier_video):
        base = os.path.splitext(os.path.basename(fichier_video))[0]
        noms = self._noms_transmis(fichier_video, marqueur="upid0badcafe")
        assert noms == {f"{base}_upid0badcafe.mp4"}, noms

    def test_remplacement_sans_marqueur(self, fichier_video):
        noms = self._noms_transmis(fichier_video, marqueur="upid0badcafe",
                                   target_slug="123-cours")
        assert noms == {os.path.basename(fichier_video)}, noms


@pytest.fixture
def module_app():
    try:
        import app as m
    except Exception as e:                        # pas d'affichage disponible
        pytest.skip(f"interface indisponible : {e}")
    return m


class _Rien:
    """Objet absorbant : tout attribut ou appel renvoie un autre _Rien. Tient
    lieu des widgets Tk, que la logique de dépôt ne fait que mettre à jour."""

    def __getattr__(self, nom):
        return _Rien()

    def __call__(self, *a, **k):
        return _Rien()


class FauxDepot:
    """Remplace PodChunkedSession : n'envoie rien ; la finalisation est
    coupée avec le code `statut_final` (0 = succès direct)."""
    envois = []
    statut_final = 504

    def __init__(self, *a, **k):
        pass

    def login(self):
        pass

    def close(self):
        pass

    def upload_video_chunked(self, chemin, **kw):
        FauxDepot.envois.append(kw)
        if FauxDepot.statut_final:
            from pod_chunked import PodChunkedError
            raise PodChunkedError("Gateway Timeout", status=FauxDepot.statut_final)
        return "slug-direct"


VEHICULE = "https://pod.exemple.fr/rest/users/99/"
PROF = "https://pod.exemple.fr/rest/users/7/"


class FausseAPI:
    """API Pod simulée. `candidats(marqueur)` renvoie ce que la recherche
    trouvera : le marqueur n'étant connu qu'une fois l'envoi lancé, les
    candidats sont construits à la volée à partir du marqueur transmis."""

    def __init__(self, candidats):
        self.candidats = candidats
        self.recherches = []
        self.patches = []

    def search_videos(self, params):
        self.recherches.append(params.get("search"))
        return self.candidats(FauxDepot.envois[-1].get("marqueur", ""))

    def get_video_by_slug(self, slug):
        return {"slug": slug, "url": f"https://pod.exemple.fr/rest/videos/{slug}/"}

    def patch_video(self, video, payload):
        self.patches.append((video.get("slug"), payload))

    def __getattr__(self, nom):                  # discipline, encodage… : sans objet
        return lambda *a, **k: None


def _video(marqueur, slug, proprietaire=VEHICULE):
    return {"id": int(slug.split("-")[0]), "slug": f"{slug}-{marqueur}",
            "title": f"cours {marqueur}", "owner": proprietaire,
            "url": f"https://pod.exemple.fr/rest/videos/{slug}/"}


def _depot_gros_fichier(m, monkeypatch, fichier, candidats, statut_final=504):
    """Rejoue _do_batch_upload (le VRAI code) sur une fausse instance : un
    seul gros fichier, finalisation coupée par `statut_final`."""
    monkeypatch.setattr(m, "PodChunkedSession", FauxDepot)
    monkeypatch.setattr(m.cfg, "CHUNK_THRESHOLD_BYTES", 1)
    monkeypatch.setattr(m.cfg, "CHUNK_VERIFY_TIMEOUT_S", 0.2)
    monkeypatch.setattr(m.cfg, "CHUNK_VERIFY_INTERVAL_S", 0)
    FauxDepot.envois = []
    FauxDepot.statut_final = statut_final

    faux = _Rien()
    faux.__dict__.update(
        items=[m.UploadItem(fichier)], config_data={"url": "https://pod.exemple.fr"},
        vehicle_username="DEPOT", vehicle_password="x", vehicle_owner_url=VEHICULE,
        additional_owner_urls=[], site_urls=[], common_contributors=[],
        api=FausseAPI(candidats), journal=[])
    faux._ui = lambda fn, *a, **k: fn(*a, **k)
    faux._log = faux.journal.append
    faux._file_size = m.App._file_size
    faux._nouveau_marqueur = m.App._nouveau_marqueur
    faux._verify_chunked_creation = m.App._verify_chunked_creation.__get__(faux)
    m.App._do_batch_upload(faux, PROF, "https://pod.exemple.fr/rest/types/1/")
    return faux, FauxDepot.envois[-1].get("marqueur", "")


class TestReattributionApres504:

    def test_marqueur_genere_en_creation(self, module_app, monkeypatch, fichier_video):
        import re
        _, marqueur = _depot_gros_fichier(module_app, monkeypatch, fichier_video,
                                          lambda m: [], statut_final=0)
        assert re.fullmatch(r"upid[0-9a-f]{8}", marqueur), marqueur

    def test_marqueur_different_a_chaque_envoi(self, module_app):
        vus = {module_app.App._nouveau_marqueur() for _ in range(50)}
        assert len(vus) == 50

    def test_un_candidat_reattribue(self, module_app, monkeypatch, fichier_video):
        faux, marqueur = _depot_gros_fichier(
            module_app, monkeypatch, fichier_video, lambda m: [_video(m, "42")])
        assert faux.api.recherches and set(faux.api.recherches) == {marqueur}
        assert [p["owner"] for _, p in faux.api.patches] == [PROF]
        assert faux.items[0].done

    def test_deux_candidats_aucune_reattribution(self, module_app, monkeypatch, fichier_video):
        faux, marqueur = _depot_gros_fichier(
            module_app, monkeypatch, fichier_video,
            lambda m: [_video(m, "42"), _video(m, "43")])
        assert faux.api.patches == []
        assert not faux.items[0].done
        alertes = [l for l in faux.journal if marqueur in l and "AUCUNE" in l]
        assert alertes, faux.journal

    def test_zero_candidat_comportement_conserve(self, module_app, monkeypatch, fichier_video):
        faux, _ = _depot_gros_fichier(module_app, monkeypatch, fichier_video, lambda m: [])
        assert faux.api.patches == []
        assert not faux.items[0].done
        assert "Gateway Timeout" in faux.items[0].error

    def test_homonyme_sans_marqueur_ignore(self, module_app, monkeypatch, fichier_video):
        """Le cas qui a motivé le correctif : un autre poste dépose au même
        moment un fichier de MÊME NOM. Sa vidéo ne porte pas notre marqueur ;
        elle ne doit pas nous être attribuée, même si la recherche la renvoie."""
        base = os.path.splitext(os.path.basename(fichier_video))[0]
        homonyme = {"id": 5, "slug": "5-" + base, "title": base, "owner": VEHICULE,
                    "url": "https://pod.exemple.fr/rest/videos/5/"}
        faux, _ = _depot_gros_fichier(module_app, monkeypatch, fichier_video,
                                      lambda m: [homonyme])
        assert faux.api.patches == []


# ── Étape 7 : « Mes vidéos » invalidée après un dépôt réussi ───────────────

class TestMesVideosApresDepot:
    """Vraie fenêtre (fixture `app` du conftest) ; seul le chargement réseau
    `_myvids_load` est remplacé par un compteur."""

    PROPRIO = "https://pod.exemple.fr/rest/users/42"

    @pytest.fixture
    def fen(self, app, monkeypatch):
        chargements = []
        monkeypatch.setattr(app, "api", object())
        monkeypatch.setattr(app, "_myvids_current_owner", lambda: (self.PROPRIO, "marie"))
        monkeypatch.setattr(app, "_myvids_load", lambda: chargements.append(1))
        monkeypatch.setattr(app, "items", [])
        # État « déjà chargé », avec une sélection multiple en cours.
        app.myvids_loaded = True
        app.myvids_videos = [{"slug": "v0"}, {"slug": "v1"}]
        app.myvids_filtered = list(app.myvids_videos)
        app.myvids_multi = ["v0", "v1"]
        app.myvids_selected = app.myvids_videos[0]
        yield app, chargements
        app.myvids_loaded = False
        app.myvids_videos, app.myvids_filtered, app.myvids_multi = [], [], []
        app.myvids_selected = None
        app._show_tab("upload")
        app.update()

    def test_onglet_visible_recharge_immediatement(self, fen):
        app, chargements = fen
        app._show_tab("myvids")
        app.update()
        chargements.clear()
        app._on_batch_done(1, 1)
        assert chargements == [1]
        assert "nouveau dépôt" in app.myvids_status.cget("text")

    def test_onglet_cache_recharge_a_la_prochaine_ouverture(self, fen):
        app, chargements = fen
        app._show_tab("upload")
        app.update()
        chargements.clear()
        app._on_batch_done(2, 3)
        assert chargements == []                  # rien tant qu'il est caché
        assert app.myvids_loaded is False
        app._show_tab("myvids")
        app.update()
        assert chargements == [1]

    def test_lot_entierement_echoue_sans_invalidation(self, fen):
        app, chargements = fen
        app._show_tab("myvids")
        app.update()
        chargements.clear()
        app._on_batch_done(0, 2)
        assert chargements == []
        assert app.myvids_loaded is True
        assert app.myvids_multi == ["v0", "v1"]

    def test_selection_multiple_videe(self, fen):
        app, _ = fen
        app._show_tab("upload")
        app.update()
        app._on_batch_done(1, 1)
        assert app.myvids_multi == []
        assert app.myvids_selected is None
        assert app.myvids_videos == [] and app.myvids_filtered == []
