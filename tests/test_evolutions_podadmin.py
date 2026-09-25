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
    # État du lot (1.9.2) : sans lui, `_Rien` rendrait « vrai » l'arrêt
    # demandé et le lot s'arrêterait avant la première vidéo.
    import threading
    faux.depot_interrompu = threading.Event()
    faux.depot_en_cours = False
    faux.deposes_session = {}
    faux._cle_fichier = m.App._cle_fichier
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


# ── Écran de dépôt : terminées, relance, progression ───────────────────────
#
# Faux widgets plutôt qu'une nouvelle fenêtre App() : chaque instance Tk
# supplémentaire aggrave les erreurs Tk intermittentes du poste Windows.

class FauxWidget:
    """Bouton / barre minimal : mémorise son texte et s'il est affiché."""

    def __init__(self):
        self.visible = False
        self.texte = ""
        self.options = {}
        self.placements = 0          # nombre d'appels à pack (doublons visibles)

    def pack(self, *a, **k):
        self.visible = True
        self.placements += 1

    def pack_forget(self):
        self.visible = False

    def winfo_ismapped(self):
        return self.visible

    def configure(self, **k):
        self.options.update(k)
        self.texte = k.get("text", self.texte)

    def set(self, v):
        self.options["valeur"] = v


def _item(m, nom, *, done=False, slug="", error=""):
    it = m.UploadItem(os.path.join(tempfile.gettempdir(), nom))
    it.done, it.slug, it.error = done, slug, error
    return it


def _ecran(m, items):
    """Fausse instance portant les VRAIES méthodes de l'écran de dépôt."""
    f = _Rien()
    f.__dict__.update(items=items, journal=[], lancements=[])
    for nom in ("purge_btn", "retry_btn", "launch_btn", "global_msg",
                "file_progress", "file_progress_lbl", "batch_progress"):
        setattr(f, nom, FauxWidget())
    f.progression_visible = False
    f.depot_en_cours = False
    f._depot_fin = lambda: None
    f._log = f.journal.append
    f._refresh_list = lambda: None
    f._run = lambda fn, *a: f.lancements.append(a)
    for nom in ("_echecs_a_relancer", "_update_retry_button", "_maj_bouton_purge",
                "_retirer_terminees", "_afficher_progression", "_masquer_progression",
                "_on_batch_done", "_retry_failed", "_set_item_status"):
        setattr(f, nom, getattr(m.App, nom).__get__(f))
    return f


class TestEchecsARelancer:

    def test_seuls_les_vrais_echecs(self, module_app):
        m = module_app
        echec = _item(m, "echec.mp4", error="HTTP 500")
        items = [_item(m, "ok.mp4", done=True),
                 _item(m, "creee.mp4", slug="42-cours", error="réattribution échouée"),
                 _item(m, "jamais.mp4"),
                 echec]
        assert _ecran(m, items)._echecs_a_relancer() == [echec]

    def test_bouton_relance_compte_et_masque(self, module_app):
        m = module_app
        f = _ecran(m, [_item(m, "a.mp4", error="x"), _item(m, "b.mp4", error="y"),
                       _item(m, "c.mp4", slug="1-c", error="réattribution échouée")])
        f._update_retry_button()
        assert f.retry_btn.visible and "(2)" in f.retry_btn.texte
        f.items = [_item(m, "d.mp4", done=True)]
        f._update_retry_button()
        assert not f.retry_btn.visible

    def test_relance_ne_renvoie_que_les_echecs(self, module_app):
        m = module_app
        echec = _item(m, "echec.mp4", error="HTTP 500")
        creee = _item(m, "creee.mp4", slug="42-cours", error="réattribution échouée")
        creee.status = "⚠️ NON réattribuée"
        f = _ecran(m, [echec, creee])
        f.api = object()
        f._last_owner_url, f._last_type_url = PROF, "https://pod.exemple.fr/rest/types/1/"
        f._retry_failed()
        assert echec.status == "en attente"
        assert creee.status == "⚠️ NON réattribuée"        # alerte conservée
        assert len(f.lancements) == 1
        assert f.progression_visible                         # barres montrées

    def test_relance_sans_echec_ne_lance_rien(self, module_app):
        m = module_app
        f = _ecran(m, [_item(m, "ok.mp4", done=True)])
        f.api = object()
        f._last_owner_url, f._last_type_url = PROF, "https://pod.exemple.fr/rest/types/1/"
        f._retry_failed()
        assert f.lancements == []

    def test_lot_saute_une_video_deja_creee(self, module_app, monkeypatch, fichier_video):
        """Le doublon évité : une vidéo créée mais non réattribuée n'est pas
        renvoyée, ni par « Lancer » ni par « Relancer les échecs »."""
        api = APIEnvoiDirect(None)
        m = module_app
        monkeypatch.setattr(m, "PodChunkedSession", FauxDepot)
        FauxDepot.envois = []
        faux = _Rien()
        it = m.UploadItem(fichier_video)
        it.slug, it.error = "42-cours", "réattribution échouée"
        faux.__dict__.update(items=[it], config_data={}, api=api, journal=[],
                             additional_owner_urls=[], site_urls=[],
                             common_contributors=[])
        faux._ui = lambda fn, *a, **k: fn(*a, **k)
        faux._log = faux.journal.append
        faux._file_size = m.App._file_size
        import threading
        faux.depot_interrompu = threading.Event()
        faux.deposes_session = {}
        faux._cle_fichier = m.App._cle_fichier
        m.App._do_batch_upload(faux, PROF, "https://pod.exemple.fr/rest/types/1/")
        assert api.envois_directs == 0 and FauxDepot.envois == []
        assert not it.done


class TestRetirerLesTerminees:

    def test_bouton_affiche_le_nombre(self, module_app):
        m = module_app
        f = _ecran(m, [_item(m, "a.mp4", done=True), _item(m, "b.mp4", done=True),
                       _item(m, "c.mp4", error="x")])
        f._maj_bouton_purge()
        assert f.purge_btn.visible and "Retirer les 2 terminées" in f.purge_btn.texte
        f.items = f.items[:1]
        f._maj_bouton_purge()
        assert "Retirer les 1 terminée" in f.purge_btn.texte
        assert not f.purge_btn.texte.endswith("s")

    def test_bouton_masque_sans_terminee(self, module_app):
        m = module_app
        f = _ecran(m, [_item(m, "c.mp4", error="x")])
        f.purge_btn.visible = True
        f._maj_bouton_purge()
        assert not f.purge_btn.visible

    def test_retire_les_terminees_garde_les_echecs(self, module_app):
        m = module_app
        echec, attente = _item(m, "e.mp4", error="x"), _item(m, "w.mp4")
        f = _ecran(m, [_item(m, "a.mp4", done=True), echec, attente])
        f._retirer_terminees()
        assert f.items == [echec, attente]


class TestProgressionEtBilan:

    def test_barres_masquees_au_repos_et_idempotentes(self, module_app):
        m = module_app
        f = _ecran(m, [])
        f._afficher_progression()
        f._afficher_progression()
        assert f.progression_visible and f.file_progress.visible
        # Un second pack ferait descendre la barre en fin de cadre, sous un
        # autre widget : chaque barre ne doit être placée qu'une fois.
        assert [w.placements for w in (f.file_progress, f.file_progress_lbl,
                                       f.batch_progress)] == [1, 1, 1]
        f._masquer_progression()
        assert not f.progression_visible and not f.batch_progress.visible

    def test_fin_de_lot_masque_les_barres(self, module_app):
        m = module_app
        f = _ecran(m, [_item(m, "a.mp4", done=True)])
        f._afficher_progression()
        f._on_batch_done(1, 1)
        assert not f.progression_visible
        assert "Vous pouvez les retirer" in f.global_msg.texte
        assert f.purge_btn.visible and not f.retry_btn.visible

    def test_fin_de_lot_partielle(self, module_app):
        m = module_app
        f = _ecran(m, [_item(m, "a.mp4", done=True), _item(m, "b.mp4", error="x")])
        f._on_batch_done(1, 2)
        assert "1/2" in f.global_msg.texte
        assert "retirer" not in f.global_msg.texte
        assert f.retry_btn.visible and "(1)" in f.retry_btn.texte


# ════════════════════════════════════════════════════════════════════════════
#  PodAdmin 1.9.2 : interruption, attente courte après 502, liste protégée
# ════════════════════════════════════════════════════════════════════════════

class Compteur:
    """annuler() qui devient vrai au n-ième appel (0 = tout de suite)."""

    def __init__(self, a_partir_de):
        self.appels = 0
        self.seuil = a_partir_de

    def __call__(self):
        self.appels += 1
        return self.appels > self.seuil


# ── Moteurs d'envoi : l'arrêt est pris en compte ───────────────────────────

class TestArretEnvoiDirect:

    def test_arret_pendant_le_flux(self, monkeypatch):
        """Avec requests-toolbelt, l'arrêt tombe au bloc suivant : le flux
        n'est pas lu jusqu'au bout, et c'est EnvoiAnnule — pas une panne."""
        import pod_api
        if not pod_api.HAS_TOOLBELT:
            pytest.skip("requests-toolbelt absent")
        from test_correctifs_342 import FausseSession
        fd, chemin = tempfile.mkstemp(suffix=".mp4")
        with os.fdopen(fd, "wb") as f:
            f.write(os.urandom(3 * 1024 * 1024))
        try:
            api = pod_api.PodAPI("https://pod.exemple.fr", "jeton")
            api.session = FausseSession()
            with pytest.raises(pod_api.EnvoiAnnule):
                api.upload_video(chemin, "t", PROF, "https://pod.exemple.fr/rest/types/1/",
                                 annuler=Compteur(2))
        finally:
            os.remove(chemin)

    def test_arret_avant_envoi_aucune_requete(self, fichier_video):
        import pod_api
        from test_correctifs_342 import FausseSession
        api = pod_api.PodAPI("https://pod.exemple.fr", "jeton")
        api.session = FausseSession()
        with pytest.raises(pod_api.EnvoiAnnule):
            api.upload_video(fichier_video, "t", PROF, "https://pod.exemple.fr/rest/types/1/",
                             annuler=lambda: True)
        assert api.session.appels == []

    def test_annulation_n_est_pas_une_panne(self, module_app):
        """Elle ne doit ni déclencher le repli, ni compter comme échec."""
        import pod_api
        assert not issubclass(pod_api.EnvoiAnnule, pod_api.PodAPIError)
        assert not module_app.App._est_coupure_reseau(pod_api.EnvoiAnnule())


class TestArretEnvoiParMorceaux:

    @staticmethod
    def _session(annuler_apres):
        from pod_chunked import PodChunkedSession
        s = PodChunkedSession("https://pod.exemple.fr", "DEPOT", "x")
        s._logged_in = True
        s.envois, s.finalisations = [], []

        def faux_envoi(chunk, start, end, total, filename, upload_id, **k):
            s.envois.append(start)
            return {"upload_id": "U1", "offset": end + 1}

        s._send_one_chunk = faux_envoi
        s._complete = lambda *a, **k: s.finalisations.append(1) or "slug"
        return s

    @pytest.fixture
    def cinq_octets(self):
        fd, chemin = tempfile.mkstemp(suffix=".mp4")
        with os.fdopen(fd, "wb") as f:
            f.write(b"ABCDE")
        yield chemin
        os.remove(chemin)

    def test_arret_entre_deux_morceaux(self, cinq_octets):
        from pod_chunked import EnvoiAnnule
        s = self._session(1)
        with pytest.raises(EnvoiAnnule):
            s.upload_video_chunked(cinq_octets, chunk_size=2, annuler=Compteur(1))
        assert s.envois == [0] and s.finalisations == []

    def test_arret_juste_avant_la_finalisation(self, cinq_octets):
        """Tous les morceaux sont partis, mais la finalisation (qui CRÉE la
        vidéo) n'est pas lancée : aucune vidéo n'existe."""
        from pod_chunked import EnvoiAnnule
        s = self._session(0)
        # 3 morceaux + 1 contrôle de fin de fichier = 4 appels, puis arrêt.
        with pytest.raises(EnvoiAnnule):
            s.upload_video_chunked(cinq_octets, chunk_size=2, annuler=Compteur(4))
        assert len(s.envois) == 3 and s.finalisations == []

    def test_arret_entre_deux_essais_d_un_morceau(self, monkeypatch):
        """Sur une liaison qui coupe sans cesse, l'arrêt n'attend pas la fin
        des ré-essais du morceau."""
        import requests
        import pod_chunked
        from pod_chunked import EnvoiAnnule, PodChunkedSession
        monkeypatch.setattr(pod_chunked.time, "sleep", lambda s: None)
        s = PodChunkedSession("https://pod.exemple.fr", "DEPOT", "x")
        essais = []

        def post(*a, **k):
            essais.append(1)
            raise requests.exceptions.ConnectionError("Connection reset by peer")
        s.session.post = post
        with pytest.raises(EnvoiAnnule):
            s._send_one_chunk(b"AB", 0, 1, 2, "v.mp4", None, retry_cb=None,
                              max_retries=4, annuler=lambda: True)
        assert essais == [1]


# ── Attente après une finalisation coupée ──────────────────────────────────

class TestAttenteApresFinalisation:

    def _faux(self, m, candidats=lambda: []):
        faux = _Rien()
        faux.__dict__.update(journal=[])
        faux._ui = lambda fn, *a, **k: fn(*a, **k)
        faux._log = faux.journal.append

        class API:
            def search_videos(self, params):
                return candidats()
        faux.api = API()
        return faux

    def test_attente_interrompue_rapidement(self, module_app, monkeypatch):
        import time
        m = module_app
        monkeypatch.setattr(m.cfg, "CHUNK_VERIFY_INTERVAL_S", 30)
        faux = self._faux(m)
        compteur = Compteur(3)
        debut = time.time()
        with pytest.raises(m.EnvoiAnnule) as exc:
            m.App._verify_chunked_creation(faux, "upid0badcafe", VEHICULE,
                                           annuler=compteur, delai_s=600)
        assert time.time() - debut < 3            # pas les 30 s de la pause
        assert exc.value.a_verifier                # la vidéo existe PEUT-ÊTRE
        assert "upid0badcafe" in str(exc.value)    # repère à rechercher

    def test_delai_respecte(self, module_app, monkeypatch):
        import time
        m = module_app
        monkeypatch.setattr(m.cfg, "CHUNK_VERIFY_INTERVAL_S", 0.05)
        # Défaut réduit à 5 s : si `delai_s` était ignoré, le test échouerait
        # en 5 s au lieu d'attendre les 30 min réelles.
        monkeypatch.setattr(m.cfg, "CHUNK_VERIFY_TIMEOUT_S", 5)
        debut = time.time()
        assert m.App._verify_chunked_creation(self._faux(m), "upid0badcafe", VEHICULE,
                                              delai_s=0.3) is None
        assert time.time() - debut < 2

    @pytest.mark.parametrize("statut, attendu", [(504, "CHUNK_VERIFY_TIMEOUT_S"),
                                                  (502, "CHUNK_VERIFY_TIMEOUT_502_S"),
                                                  (503, "CHUNK_VERIFY_TIMEOUT_502_S")])
    def test_attente_longue_seulement_sur_504(self, module_app, monkeypatch,
                                              fichier_video, statut, attendu):
        m = module_app
        delais = []
        faux = _Rien()
        faux.__dict__.update(journal=[], vehicle_owner_url=VEHICULE)
        faux._ui = lambda fn, *a, **k: fn(*a, **k)
        faux._log = faux.journal.append
        faux._nouveau_marqueur = m.App._nouveau_marqueur
        faux._verify_chunked_creation = (
            lambda marqueur, veh, annuler=None, delai_s=None: delais.append(delai_s))
        FauxDepot.envois, FauxDepot.statut_final = [], statut
        with pytest.raises(m.PodChunkedError):
            m.App._deposer_par_morceaux(faux, FauxDepot(), m.UploadItem(fichier_video),
                                        None, None)
        assert delais == [getattr(m.cfg, attendu)]
        # Le repère est écrit au Journal : seul moyen de retrouver la vidéo si
        # l'application est fermée entre-temps.
        assert any(FauxDepot.envois[0]["marqueur"] in l and "vérification pendant" in l
                   for l in faux.journal), faux.journal

    def test_attente_502_courte(self):
        import config
        assert config.CHUNK_VERIFY_TIMEOUT_502_S <= 300 < config.CHUNK_VERIFY_TIMEOUT_S


# ── Le lot face à l'arrêt ──────────────────────────────────────────────────

def _lot_items(m, monkeypatch, items, api, *, seuil=GROS, annuler=None):
    """Comme `_lot`, mais pour plusieurs vidéos et avec un arrêt programmable."""
    import threading
    monkeypatch.setattr(m, "PodChunkedSession", FauxDepot)
    monkeypatch.setattr(m.cfg, "CHUNK_THRESHOLD_BYTES", seuil)
    monkeypatch.setattr(m.cfg, "CHUNK_VERIFY_INTERVAL_S", 0)
    # Attentes après 504/502 raccourcies : un défaut qui renverrait une vidéo
    # doit faire échouer le test, pas le bloquer 30 minutes.
    monkeypatch.setattr(m.cfg, "CHUNK_VERIFY_TIMEOUT_S", 0.2)
    monkeypatch.setattr(m.cfg, "CHUNK_VERIFY_TIMEOUT_502_S", 0.2)
    FauxDepot.envois = []          # état de classe : remis à zéro à chaque lot
    faux = _Rien()
    faux.__dict__.update(
        items=items, config_data={"url": "https://pod.exemple.fr"},
        vehicle_username="DEPOT", vehicle_password="x", vehicle_owner_url=VEHICULE,
        additional_owner_urls=[], site_urls=[], common_contributors=[],
        api=api, journal=[], bilans=[])
    faux.depot_interrompu = threading.Event()
    faux.deposes_session = {}
    faux._ui = lambda fn, *a, **k: fn(*a, **k)
    faux._log = faux.journal.append
    faux._on_batch_done = lambda *a: faux.bilans.append(a)
    for nom in ("_file_size", "_nouveau_marqueur", "_est_coupure_reseau", "_cle_fichier"):
        setattr(faux, nom, getattr(m.App, nom))
    for nom in ("_verify_chunked_creation", "_deposer_par_morceaux", "_echecs_a_relancer",
                "_set_item_status"):
        setattr(faux, nom, getattr(m.App, nom).__get__(faux))
    if annuler:
        annuler(faux)
    m.App._do_batch_upload(faux, PROF, "https://pod.exemple.fr/rest/types/1/")
    return faux


def _fichiers(n):
    chemins = []
    for i in range(n):
        fd, c = tempfile.mkstemp(suffix=f"_{i}.mp4")
        with os.fdopen(fd, "wb") as f:
            f.write(b"x" * 10)
        chemins.append(c)
    return chemins


@pytest.fixture
def trois_fichiers():
    chemins = _fichiers(3)
    yield chemins
    for c in chemins:
        os.remove(c)


class APIQuiArrete(APIEnvoiDirect):
    """Envoi direct réussi, mais le 1er envoi déclenche l'arrêt du lot."""

    def __init__(self, faux_ref):
        super().__init__(None)
        self.faux_ref = faux_ref

    def upload_video(self, *a, **k):
        self.faux_ref[0].depot_interrompu.set()
        return super().upload_video(*a, **k)


class TestLotInterrompu:

    def test_arret_entre_deux_videos(self, module_app, monkeypatch, trois_fichiers):
        m = module_app
        ref = [None]
        api = APIQuiArrete(ref)
        items = [m.UploadItem(c) for c in trois_fichiers]
        faux = _lot_items(m, monkeypatch, items, api,
                          annuler=lambda f: ref.__setitem__(0, f))
        assert api.envois_directs == 1              # la 2e n'est pas partie
        assert [it.done for it in items] == [True, False, False]
        ok, total, interrompu = faux.bilans[-1]
        assert (ok, total, interrompu) == (1, 3, True)

    def test_arret_pendant_une_video_n_est_pas_un_echec(self, module_app, monkeypatch,
                                                        trois_fichiers):
        from pod_api import EnvoiAnnule
        m = module_app
        items = [m.UploadItem(c) for c in trois_fichiers]
        faux = _lot_items(m, monkeypatch, items, APIEnvoiDirect(EnvoiAnnule()))
        assert items[0].status.startswith("⏹")
        assert items[0].error == ""                 # pas une erreur
        assert faux._echecs_a_relancer() == []      # rien à « relancer »
        assert FauxDepot.envois == []               # pas de repli par morceaux
        assert faux.bilans[-1][2] is True

    def test_attente_504_interrompue_video_a_verifier(self, module_app, monkeypatch,
                                                      trois_fichiers):
        """Arrêt pendant l'attente qui suit un 504 : la vidéo existe peut-être.
        Elle est marquée « à vérifier », jamais relancée, jamais renvoyée."""
        m = module_app
        FauxDepot.envois, FauxDepot.statut_final = [], 504
        items = [m.UploadItem(c) for c in trois_fichiers]
        faux = _lot_items(m, monkeypatch, items, FausseAPI(lambda mq: []), seuil=1,
                          annuler=lambda f: setattr(
                              f, "_verify_chunked_creation",
                              lambda *a, **k: (_ for _ in ()).throw(
                                  m.EnvoiAnnule("x", a_verifier=True))))
        it = items[0]
        assert it.a_verifier and not it.done and it.status.startswith("⚠️")
        assert faux._echecs_a_relancer() == []
        assert m.App._cle_fichier(it.path) in faux.deposes_session
        # Un nouveau lot ne la renvoie pas.
        FauxDepot.envois = []
        _lot_items(m, monkeypatch, [it], FausseAPI(lambda mq: []), seuil=1)
        assert FauxDepot.envois == []

    def test_video_ajoutee_pendant_le_lot_part_a_la_suite(self, module_app, monkeypatch,
                                                          trois_fichiers):
        m = module_app
        items = [m.UploadItem(trois_fichiers[0])]

        class API(APIEnvoiDirect):
            def upload_video(self, *a, **k):
                if len(items) == 1:
                    items.append(m.UploadItem(trois_fichiers[1]))   # ajout en cours de lot
                return super().upload_video(*a, **k)
        api = API(None)
        faux = _lot_items(m, monkeypatch, items, api)
        assert api.envois_directs == 2 and all(it.done for it in items)
        assert faux.bilans[-1][:2] == (2, 2)        # le total suit la liste

    def test_envoi_reussi_memorise_pour_la_session(self, module_app, monkeypatch,
                                                   trois_fichiers):
        m = module_app
        items = [m.UploadItem(trois_fichiers[0])]
        faux = _lot_items(m, monkeypatch, items, APIEnvoiDirect(None))
        heure, slug = faux.deposes_session[m.App._cle_fichier(trois_fichiers[0])]
        assert slug == "1-direct"


# ── Ajout de fichiers et liste protégée pendant un lot ─────────────────────

def _ecran_ajout(m, items=None, en_cours=False):
    f = _ecran(m, items or [])
    f.depot_en_cours = en_cours
    f.deposes_session = {}
    f.dnd_ok = False
    f._cle_fichier = m.App._cle_fichier
    for nom in ("_add_paths", "_clear_items", "_remove_item", "_maj_verrou_liste",
                "_depot_debut", "_depot_fin", "_depot_interrompre"):
        setattr(f, nom, getattr(m.App, nom).__get__(f))
    import threading
    f.depot_interrompu = threading.Event()
    f.clear_btn = FauxWidget()
    f.upload_stop_btn = FauxWidget()
    return f


class TestAjoutDeFichiers:

    def test_meme_fichier_ecrit_autrement_non_duplique(self, module_app, trois_fichiers):
        m = module_app
        f = _ecran_ajout(m)
        c = trois_fichiers[0]
        autre_ecriture = c.replace("\\", "/")
        if os.name == "nt":
            autre_ecriture = autre_ecriture.upper()
        assert f._add_paths([c]) == 1
        assert f._add_paths([autre_ecriture]) == 0
        assert len(f.items) == 1
        assert "1 déjà dans la liste" in f.global_msg.texte

    def test_bilan_exact(self, module_app, trois_fichiers):
        m = module_app
        f = _ecran_ajout(m)
        f._add_paths(trois_fichiers[:1])
        assert f._add_paths(trois_fichiers) == 2
        assert f.global_msg.texte == "2 vidéo(s) ajoutée(s), 1 déjà dans la liste."

    @pytest.mark.parametrize("reponse, attendu", [(True, 1), (False, 0)])
    def test_fichier_deja_envoye_demande_confirmation(self, module_app, monkeypatch,
                                                      trois_fichiers, reponse, attendu):
        m = module_app
        questions = []
        monkeypatch.setattr(m.messagebox, "askyesno",
                            lambda *a, **k: questions.append(a) or reponse)
        f = _ecran_ajout(m)
        f.deposes_session[m.App._cle_fichier(trois_fichiers[0])] = ("10:42", "12-cours")
        assert f._add_paths([trois_fichiers[0]]) == attendu
        assert len(questions) == 1 and "SECOND exemplaire" in questions[0][1]
        if not reponse:
            assert "déjà envoyée(s) non ajoutée(s)" in f.global_msg.texte

    def test_ajout_pendant_un_lot_annonce(self, module_app, trois_fichiers):
        m = module_app
        f = _ecran_ajout(m, en_cours=True)
        assert f._add_paths([trois_fichiers[0]]) == 1
        assert "à la suite du lot en cours" in f.global_msg.texte

    def test_selecteur_annule_ne_dit_rien(self, module_app):
        f = _ecran_ajout(module_app)
        assert f._add_paths(()) == 0
        assert f.global_msg.texte == ""


class TestListeProtegeePendantUnLot:

    def test_retraits_bloques(self, module_app, trois_fichiers):
        m = module_app
        items = [m.UploadItem(c) for c in trois_fichiers]
        items[0].done = True
        f = _ecran_ajout(m, list(items), en_cours=True)
        f._remove_item(items[1])
        f._clear_items()
        f._retirer_terminees()
        assert f.items == items

    def test_boutons_grises_puis_reactives(self, module_app, trois_fichiers):
        m = module_app
        items = [m.UploadItem(c) for c in trois_fichiers]
        for it in items:
            it.btn_retirer = FauxWidget()
        f = _ecran_ajout(m, items)
        f._depot_debut()
        tous = [f.clear_btn, f.purge_btn] + [it.btn_retirer for it in items]
        assert all(b.options.get("state") == "disabled" for b in tous)
        assert f.upload_stop_btn.visible
        f._depot_fin()
        assert all(b.options.get("state") == "normal" for b in tous)
        assert not f.upload_stop_btn.visible and not f.depot_en_cours

    def test_interrompre_arme_l_arret(self, module_app):
        f = _ecran_ajout(module_app)
        f._depot_debut()
        assert not f.depot_interrompu.is_set()
        f._depot_interrompre()
        assert f.depot_interrompu.is_set()
        assert f.upload_stop_btn.options.get("state") == "disabled"
        f._depot_debut()                           # un nouveau lot repart propre
        assert not f.depot_interrompu.is_set()

    def test_bilan_interrompu(self, module_app, trois_fichiers):
        m = module_app
        items = [m.UploadItem(c) for c in trois_fichiers]
        items[0].done = True
        items[1].a_verifier, items[1].error = True, "attente interrompue"
        f = _ecran_ajout(m, items)
        f._on_batch_done(1, 3, True)
        assert "Interrompu : 1/3" in f.global_msg.texte
        assert "reprend les 1 restante(s)" in f.global_msg.texte
        assert "1 à vérifier" in f.global_msg.texte
        assert not f.retry_btn.visible             # « à vérifier » n'est pas un échec
