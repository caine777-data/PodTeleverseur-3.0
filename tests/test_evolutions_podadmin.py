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
