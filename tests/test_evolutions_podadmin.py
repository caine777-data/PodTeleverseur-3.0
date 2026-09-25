"""Tests des évolutions du dépôt reprises de PodAdmin 1.9.1.

Même règle que pour les correctifs 3.4.2 : chaque test a été éprouvé par
MUTATION (défaut réintroduit → test rouge → code restauré). Aucun réseau :
API, session DEPOT et widgets sont simulés, et `_do_batch_upload` — le VRAI
code du lot — est rejoué sur une fausse instance.
"""
import os
import tempfile

import pytest

from test_correctifs_342 import (  # noqa: F401  (fixtures réutilisées)
    FausseAPI, FauxDepot, PROF, VEHICULE, _Rien, _video, fichier_video, module_app)


def _lot(m, monkeypatch, fichier, api, *, statut_final=0, seuil=1,
         discipline=""):
    """Rejoue _do_batch_upload sur une fausse instance, pour UN fichier.

    seuil=1 : le fichier passe par l'envoi par morceaux (véhicule DEPOT) ;
    seuil énorme : envoi direct par jeton (api.upload_video)."""
    monkeypatch.setattr(m, "PodChunkedSession", FauxDepot)
    monkeypatch.setattr(m.cfg, "CHUNK_THRESHOLD_BYTES", seuil)
    monkeypatch.setattr(m.cfg, "CHUNK_VERIFY_TIMEOUT_S", 0.2)
    monkeypatch.setattr(m.cfg, "CHUNK_VERIFY_INTERVAL_S", 0)
    FauxDepot.envois = []
    FauxDepot.statut_final = statut_final

    faux = _Rien()
    faux.__dict__.update(
        items=[m.UploadItem(fichier)], config_data={"url": "https://pod.exemple.fr"},
        vehicle_username="DEPOT", vehicle_password="x", vehicle_owner_url=VEHICULE,
        additional_owner_urls=[], site_urls=[], common_contributors=[],
        api=api, journal=[])
    faux._ui = lambda fn, *a, **k: fn(*a, **k)
    faux._log = faux.journal.append
    for nom in ("_file_size", "_nouveau_marqueur", "_est_coupure_reseau"):
        if hasattr(m.App, nom):
            setattr(faux, nom, getattr(m.App, nom))
    for nom in ("_verify_chunked_creation", "_deposer_par_morceaux",
                "_replier_sur_chunked"):
        if hasattr(m.App, nom):
            setattr(faux, nom, getattr(m.App, nom).__get__(faux))
    m.App._do_batch_upload(faux, PROF, "https://pod.exemple.fr/rest/types/1/",
                           discipline)
    return faux


class APIDisciplines(FausseAPI):
    """Enregistre les appels à set_disciplines."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.disciplines = []

    def set_disciplines(self, video, urls):
        self.disciplines.append((video, urls))


# ── Défaut : discipline rattachée par slug ─────────────────────────────────

class TestDisciplineParURL:
    """`/videos/<slug>/` peut répondre 404 : la discipline doit être posée
    sur l'URL de la vidéo (identifiant numérique), pas sur son slug."""

    DISC = "https://pod.exemple.fr/rest/discipline/3/"

    def test_depot_par_morceaux(self, module_app, monkeypatch, fichier_video):
        api = APIDisciplines(lambda m: [])
        faux = _lot(module_app, monkeypatch, fichier_video, api, discipline=self.DISC)
        assert faux.items[0].done
        assert api.disciplines == [
            ("https://pod.exemple.fr/rest/videos/slug-direct/", [self.DISC])]

    def test_depot_direct(self, module_app, monkeypatch, fichier_video):
        class API(APIDisciplines):
            def upload_video(self, *a, **k):
                return {"slug": "12-cours", "url": "https://pod.exemple.fr/rest/videos/12/"}
        api = API(lambda m: [])
        _lot(module_app, monkeypatch, fichier_video, api, seuil=10 ** 12,
             discipline=self.DISC)
        assert api.disciplines == [("https://pod.exemple.fr/rest/videos/12/", [self.DISC])]


# ── Défaut : propriétaire comparé par inclusion de chaîne ──────────────────

class TestProprietaireEgaliteStricte:
    """Après un 504, seule une vidéo du VÉHICULE peut être reprise. Comparer
    par inclusion confondait `/users/99` (véhicule) et `/users/999`."""

    def test_proprietaire_voisin_refuse(self, module_app, monkeypatch, fichier_video):
        autre = VEHICULE.rstrip("/") + "9/"          # …/users/999/
        api = FausseAPI(lambda m: [_video(m, "42", proprietaire=autre)])
        faux = _lot(module_app, monkeypatch, fichier_video, api, statut_final=504)
        assert api.patches == []
        assert not faux.items[0].done

    def test_proprietaire_absent_refuse(self, module_app, monkeypatch, fichier_video):
        api = FausseAPI(lambda m: [_video(m, "42", proprietaire="")])
        faux = _lot(module_app, monkeypatch, fichier_video, api, statut_final=504)
        assert api.patches == []

    def test_vehicule_accepte_avec_ou_sans_barre_finale(self, module_app, monkeypatch,
                                                         fichier_video):
        api = FausseAPI(lambda m: [_video(m, "42", proprietaire=VEHICULE.rstrip("/"))])
        faux = _lot(module_app, monkeypatch, fichier_video, api, statut_final=504)
        assert [p["owner"] for _, p in api.patches] == [PROF]
        assert faux.items[0].done


# ── Session DEPOT refusée hors HTTPS ───────────────────────────────────────

class TestSessionDepotHTTPS:
    """Le mot de passe du compte véhicule (partagé par tous les postes) part
    dans le formulaire de login : jamais sur une adresse en clair."""

    @pytest.mark.parametrize("url", ["http://pod.exemple.fr", "HTTP://pod.exemple.fr/",
                                     "pod.exemple.fr", ""])
    def test_adresse_non_https_refusee(self, url):
        from pod_chunked import PodChunkedError, PodChunkedSession
        with pytest.raises(PodChunkedError, match="https"):
            PodChunkedSession(url, "DEPOT", "secret")

    @pytest.mark.parametrize("url", ["https://pod.exemple.fr", "HTTPS://pod.exemple.fr/"])
    def test_adresse_https_acceptee(self, url):
        from pod_chunked import PodChunkedSession
        s = PodChunkedSession(url, "DEPOT", "secret")
        assert s._logged_in is False             # rien n'est ouvert à la création


# ── Repli automatique sur l'envoi par morceaux ─────────────────────────────

GROS = 10 ** 12          # seuil inatteignable : le fichier part en envoi direct


def _erreur_coupure_reelle(monkeypatch, fichier):
    """Produit la VRAIE PodAPIError que lève pod_api après trois coupures
    SSL, plutôt qu'un message écrit à la main dans le test."""
    import requests
    import pod_api
    monkeypatch.setattr(pod_api.time, "sleep", lambda s: None)

    class SessionCoupee:
        headers = {}

        def post(self, *a, **k):
            raise requests.exceptions.SSLError(
                "EOF occurred in violation of protocol (_ssl.c:2427)")

    api = pod_api.PodAPI("https://pod.exemple.fr", "jeton")
    api.session = SessionCoupee()
    with pytest.raises(pod_api.PodAPIError) as exc:
        api.upload_video(fichier, "t", "https://pod.exemple.fr/rest/users/7/",
                         "https://pod.exemple.fr/rest/types/1/")
    return exc.value


class APIEnvoiDirect(FausseAPI):
    """upload_video lève l'erreur fournie (ou réussit si None)."""

    def __init__(self, erreur, candidats=lambda m: []):
        super().__init__(candidats)
        self.erreur = erreur
        self.envois_directs = 0

    def upload_video(self, *a, **k):
        self.envois_directs += 1
        if self.erreur:
            raise self.erreur
        return {"slug": "1-direct", "url": "https://pod.exemple.fr/rest/videos/1/"}


class TestReconnaissanceCoupure:

    def test_coupure_ssl_reelle_reconnue(self, module_app, monkeypatch, fichier_video):
        err = _erreur_coupure_reelle(monkeypatch, fichier_video)
        assert module_app.App._est_coupure_reseau(err)

    @pytest.mark.parametrize("status", [400, 403, 404, 500])
    def test_refus_du_serveur_non_reconnu(self, module_app, status):
        from pod_api import PodAPIError
        # Même avec un mot trompeur dans le corps : c'est une RÉPONSE du serveur.
        err = PodAPIError(f"HTTP {status}", status=status, body="connection reset")
        assert not module_app.App._est_coupure_reseau(err)

    def test_erreur_quelconque_sans_indice_non_reconnue(self, module_app):
        from pod_api import PodAPIError
        assert not module_app.App._est_coupure_reseau(PodAPIError("Fichier introuvable : x"))


class TestRepliSurEnvoiParMorceaux:

    def test_coupure_bascule_et_reattribue(self, module_app, monkeypatch, fichier_video):
        api = APIEnvoiDirect(_erreur_coupure_reelle(monkeypatch, fichier_video))
        faux = _lot(module_app, monkeypatch, fichier_video, api, seuil=GROS)
        assert api.envois_directs == 1
        assert len(FauxDepot.envois) == 1                     # repli effectué
        assert FauxDepot.envois[0]["marqueur"].startswith("upid")
        assert [p["owner"] for _, p in api.patches] == [PROF]  # réattribuée
        assert faux.items[0].done
        assert any("Bascule automatique" in l for l in faux.journal)

    def test_repli_puis_504_reprend_la_video(self, module_app, monkeypatch, fichier_video):
        """Le cas qui plantait dans PodAdmin avant la mise en commun : un 504
        pendant le REPLI doit suivre la même reprise par marqueur."""
        api = APIEnvoiDirect(_erreur_coupure_reelle(monkeypatch, fichier_video),
                             candidats=lambda m: [_video(m, "42")])
        faux = _lot(module_app, monkeypatch, fichier_video, api, seuil=GROS,
                    statut_final=504)
        assert set(api.recherches) == {FauxDepot.envois[0]["marqueur"]}
        assert [p["owner"] for _, p in api.patches] == [PROF]
        assert faux.items[0].done

    def test_refus_du_serveur_pas_de_repli(self, module_app, monkeypatch, fichier_video):
        from pod_api import PodAPIError
        api = APIEnvoiDirect(PodAPIError("HTTP 400 sur /videos/", status=400,
                                         body='{"type": ["requis"]}'))
        faux = _lot(module_app, monkeypatch, fichier_video, api, seuil=GROS)
        assert FauxDepot.envois == []                          # aucun repli
        assert not faux.items[0].done

    def test_envoi_direct_reussi_sans_morceaux(self, module_app, monkeypatch, fichier_video):
        api = APIEnvoiDirect(None)
        faux = _lot(module_app, monkeypatch, fichier_video, api, seuil=GROS)
        assert FauxDepot.envois == [] and api.patches == []
        assert faux.items[0].done and faux.items[0].slug == "1-direct"
