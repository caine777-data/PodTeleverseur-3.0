"""Blocage à distance — interrupteur manuel, INDÉPENDANT de la mise à jour.

Repris de PodAdmin (voir BLOCAGE.md) : invisible tant que rien n'est bloqué,
seule une réponse RÉSEAU RÉELLE change l'état, et ce qui est confirmé une fois
est mémorisé localement pour résister à une coupure réseau volontaire.

Chaque test a été éprouvé par mutation. Aucun réseau réel, sauf un test qui
vérifie justement qu'une adresse injoignable ne lève rien. La configuration
du poste n'est jamais touchée (CONFIG_PATH redirigé vers un fichier jetable).
"""
import json
import os

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _boutons(widget):
    """Tous les CTkButton descendants de `widget`, quelle que soit la profondeur."""
    import customtkinter as ctk
    trouves = []
    for enfant in widget.winfo_children():
        if isinstance(enfant, ctk.CTkButton):
            trouves.append(enfant)
        trouves.extend(_boutons(enfant))
    return trouves


def _config_isolee(tmp_path, monkeypatch):
    """Redirige config.CONFIG_PATH vers un fichier jetable : jamais le vrai
    fichier de configuration de la machine qui exécute les tests."""
    import config as cfg
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "config_test.json"))
    return cfg


# ── Lecture de etat.json ───────────────────────────────────────────────────

class TestLectureEtatDistant:
    """`maj.etat_blocage` : jamais bloquant, jamais ambigu."""

    @staticmethod
    def _simuler(monkeypatch, donnees):
        import maj
        monkeypatch.setattr(maj, "_telecharger_json", lambda *a, **k: donnees)
        return maj.etat_blocage("https://x.invalid/etat.json")

    def test_bloque_et_debloque(self, monkeypatch):
        assert self._simuler(monkeypatch, {"bloque": True}) is True
        assert self._simuler(monkeypatch, {"bloque": False}) is False

    def test_aucune_reponse_exploitable_ne_bloque_ni_ne_debloque(self, monkeypatch):
        """⚠️ Un None doit rester un None : jamais un déblocage implicite."""
        assert self._simuler(monkeypatch, None) is None                # réseau coupé
        assert self._simuler(monkeypatch, {}) is None                  # champ absent
        assert self._simuler(monkeypatch, {"bloque": "oui"}) is None   # pas un booléen
        assert self._simuler(monkeypatch, {"bloque": 1}) is None       # pas un booléen

    def test_reseau_reellement_indisponible(self):
        """Bout-en-bout, sans simulation : une adresse injoignable ne lève
        rien et renvoie None ; une adresse vide désactive la vérification."""
        import maj
        assert maj.etat_blocage("https://exemple.invalid/etat.json", timeout=1) is None
        assert maj.etat_blocage("") is None

    def test_adresse_sur_le_depot_public_du_televerseur(self):
        import config as cfg
        assert cfg.BLOCAGE_URL.endswith("/podteleverseur-releases/main/etat.json")
        assert cfg.BLOCAGE_URL.startswith("https://")

    def test_la_mise_a_jour_lit_toujours_version_json(self, monkeypatch):
        """Le téléchargement a été mis en commun : version.json doit toujours
        exiger un numéro de version, etat.json non."""
        import maj
        monkeypatch.setattr(maj, "_telecharger_json", lambda *a, **k: {"bloque": True})
        assert maj.recuperer_info("https://x.invalid/version.json") is None
        monkeypatch.setattr(maj, "_telecharger_json", lambda *a, **k: {"version": "3.4.2"})
        assert maj.recuperer_info("https://x.invalid/version.json") == {"version": "3.4.2"}


# ── Mémorisation locale ────────────────────────────────────────────────────

class TestVerrouLocalDuBlocageDistant:

    def test_rien_au_depart(self, tmp_path, monkeypatch):
        cfg = _config_isolee(tmp_path, monkeypatch)
        assert cfg.blocage_distant_actif() is False

    def test_enregistrement_puis_lecture(self, tmp_path, monkeypatch):
        cfg = _config_isolee(tmp_path, monkeypatch)
        cfg.enregistrer_blocage_distant(True)
        assert cfg.blocage_distant_actif() is True
        cfg.enregistrer_blocage_distant(False)
        assert cfg.blocage_distant_actif() is False

    def test_le_verrou_survit_a_un_echec_reseau_simule(self, tmp_path, monkeypatch):
        """Tout le sens du mécanisme : couper le réseau ne débloque pas."""
        cfg = _config_isolee(tmp_path, monkeypatch)
        cfg.enregistrer_blocage_distant(True)
        import maj
        monkeypatch.setattr(maj, "etat_blocage", lambda *a, **k: None)
        assert cfg.blocage_distant_actif() is True

    def test_seul_un_vrai_booleen_compte(self, tmp_path, monkeypatch):
        """Un fichier de configuration bricolé (« blocage_distant": "non" »)
        ne doit pas être lu comme un blocage : seul True bloque."""
        cfg = _config_isolee(tmp_path, monkeypatch)
        with open(cfg.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"blocage_distant": "non"}, f)
        assert cfg.blocage_distant_actif() is False


# ── Démarrage ──────────────────────────────────────────────────────────────

class TestOrdreAuDemarrage:
    """Le verrou local est consulté avant l'auto-connexion (sinon on pourrait
    interagir avant l'affichage du blocage), et la surveillance est planifiée
    avant le `return` anticipé de la mise à jour obligatoire (sinon un poste
    bloqué par la mise à jour ne lirait jamais etat.json)."""

    @staticmethod
    def _corps_init():
        with open(os.path.join(RACINE, "app.py"), encoding="utf-8") as f:
            source = f.read()
        deb = source.index("def __init__(self):")
        fin = source.index("\n    def ", deb + 20)
        return source[deb:fin]

    def test_verrou_local_avant_l_auto_connexion(self):
        corps = self._corps_init()
        pos = corps.find("if cfg.blocage_distant_actif():")
        assert pos != -1, "le blocage distant mémorisé n'est pas consulté au démarrage"
        assert pos < corps.find("self._run(self._auto_connect)")

    def test_surveillance_avant_le_retour_de_la_maj_obligatoire(self):
        corps = self._corps_init()
        pos = corps.find("self.after(2000, self._surveiller_blocage)")
        assert pos != -1, "la surveillance du blocage n'est pas planifiée"
        assert pos < corps.find("blocage_local = cfg.blocage_local_actif(APP_VERSION)")


# ── Application du blocage (vraie fenêtre, fixture `app` du conftest) ──────

class TestApplicationDuBlocage:

    def test_rien_a_l_ecran_par_defaut(self, app):
        assert app._voile_blocage is None

    def test_une_panne_reseau_ne_change_rien(self, app, tmp_path, monkeypatch):
        _config_isolee(tmp_path, monkeypatch)
        app._appliquer_blocage(None)
        app.update()
        assert app._voile_blocage is None

    def test_blocage_puis_deblocage(self, app, tmp_path, monkeypatch):
        cfg = _config_isolee(tmp_path, monkeypatch)
        try:
            app._appliquer_blocage(True)
            app.update()
            assert app._voile_blocage is not None
            assert cfg.blocage_distant_actif() is True

            app._appliquer_blocage(None)          # une panne ne débloque pas
            app.update()
            assert app._voile_blocage is not None

            app._appliquer_blocage(False)
            app.update()
            assert app._voile_blocage is None
            assert cfg.blocage_distant_actif() is False
        finally:
            app._appliquer_blocage(False)
            app.update()

    def test_le_voile_recouvre_toute_la_fenetre(self, app, tmp_path, monkeypatch):
        _config_isolee(tmp_path, monkeypatch)
        try:
            app._appliquer_blocage(True)
            app.update()
            info = app._voile_blocage.place_info()
            assert info.get("relwidth") == "1" and info.get("relheight") == "1", info
        finally:
            app._appliquer_blocage(False)
            app.update()

    def test_le_bouton_quitter_est_present(self, app, tmp_path, monkeypatch):
        _config_isolee(tmp_path, monkeypatch)
        try:
            app._appliquer_blocage(True)
            app.update()
            boutons = _boutons(app._voile_blocage)
            assert [b.cget("text") for b in boutons] == ["Quitter"], (
                "le voile doit offrir UNE seule issue : « Quitter »")
            # Créé ne suffit pas : il doit être AFFICHÉ, sinon il n'y a plus
            # aucune issue à l'écran.
            assert boutons[0].winfo_ismapped(), "le bouton « Quitter » n'est pas affiché"
        finally:
            app._appliquer_blocage(False)
            app.update()

    def test_les_fenetres_secondaires_sont_fermees(self, app, tmp_path, monkeypatch):
        """Une fenêtre restée ouverte par-dessus le voile permettrait de
        continuer à s'en servir malgré le blocage."""
        import customtkinter as ctk
        _config_isolee(tmp_path, monkeypatch)
        fen = ctk.CTkToplevel(app)
        try:
            app._appliquer_blocage(True)
            app.update()
            assert not fen.winfo_exists()
        finally:
            app._appliquer_blocage(False)
            app.update()

    def test_un_televersement_en_cours_est_interrompu(self, app, tmp_path, monkeypatch):
        """Propre au Téléverseur : l'envoi ne continue pas derrière le voile."""
        _config_isolee(tmp_path, monkeypatch)
        monkeypatch.setattr(app, "depot_en_cours", True)
        app.depot_interrompu.clear()
        try:
            app._appliquer_blocage(True)
            app.update()
            assert app.depot_interrompu.is_set()
        finally:
            app.depot_interrompu.clear()
            app._appliquer_blocage(False)
            app.update()

    def test_sans_televersement_rien_n_est_arme(self, app, tmp_path, monkeypatch):
        _config_isolee(tmp_path, monkeypatch)
        monkeypatch.setattr(app, "depot_en_cours", False)
        app.depot_interrompu.clear()
        try:
            app._appliquer_blocage(True)
            app.update()
            assert not app.depot_interrompu.is_set()
        finally:
            app._appliquer_blocage(False)
            app.update()


# ── Workflow : « Run workflow » → champ blocage ───────────────────────────

class TestWorkflowBlocage:
    """On analyse le YAML réel (pas une recherche de sous-chaîne)."""

    @pytest.fixture
    def w(self):
        yaml = pytest.importorskip("yaml")
        with open(os.path.join(RACINE, ".github", "workflows", "build.yml"),
                  encoding="utf-8") as f:
            return yaml.safe_load(f)

    @staticmethod
    def _entrees(w):
        # PyYAML lit la clé `on:` comme le booléen True.
        return (w.get("on") or w.get(True))["workflow_dispatch"]["inputs"]

    def test_champ_blocage_par_defaut_ne_rien_changer(self, w):
        champ = self._entrees(w)["blocage"]
        assert champ["type"] == "choice"
        assert champ["options"] == ["ne rien changer", "bloquer", "débloquer"]
        assert champ["default"] == "ne rien changer"

    @pytest.mark.parametrize("job", ["build-windows", "build-macos", "release"])
    def test_rien_n_est_compile_ni_publie_pendant_un_blocage(self, w, job):
        condition = w["jobs"][job].get("if", "")
        assert "inputs.blocage != 'bloquer'" in condition, condition
        assert "inputs.blocage != 'débloquer'" in condition, condition

    def test_la_publication_reste_conditionnee_a_la_version(self, w):
        condition = w["jobs"]["release"]["if"]
        assert "startsWith(github.ref, 'refs/tags/v') || inputs.version != ''" in condition

    def test_job_blocage_seulement_sur_demande(self, w):
        job = w["jobs"]["blocage"]
        assert job["if"] == "inputs.blocage == 'bloquer' || inputs.blocage == 'débloquer'"
        assert job["permissions"] == {"contents": "read"}

    def test_job_blocage_ecrit_etat_json_sur_le_depot_public(self, w):
        etape = w["jobs"]["blocage"]["steps"][0]
        env, script = etape["env"], etape["run"]
        assert env["RELEASES_TOKEN"] == "${{ secrets.RELEASES_TOKEN }}"
        assert "podteleverseur-releases" in env["DEPOT"]
        assert env["BLOQUE"] == "${{ inputs.blocage == 'bloquer' }}"
        assert "public/etat.json" in script and "git push" in script
        assert "version.json" not in script          # ne touche pas à la MAJ

    @pytest.mark.parametrize("valeur, attendu", [("true", True), ("false", False)])
    def test_le_fichier_ecrit_est_lu_par_l_application(self, w, monkeypatch,
                                                       valeur, attendu):
        """Chaîne complète : ce que le workflow écrit (printf réel du script,
        rejoué en Python) est exactement ce que `maj.etat_blocage` comprend."""
        import re
        import maj
        script = w["jobs"]["blocage"]["steps"][0]["run"]
        (motif,) = re.findall(r"printf '([^']*)'", script)
        contenu = motif.replace("\\n", "\n") % valeur
        monkeypatch.setattr(maj, "_telecharger_json", lambda *a, **k: json.loads(contenu))
        assert maj.etat_blocage("https://x.invalid/etat.json") is attendu
