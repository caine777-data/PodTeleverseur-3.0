"""Apparence de Pod Téléverseur — palette partagée et mode clair/sombre.

Ces tests sont les PREMIERS de cette application : elle n'en avait aucun.
Ils couvrent d'abord ce qui vient d'être porté depuis PodAdmin, là où une
erreur ne se voit pas à la lecture du code.
"""
import re

import pytest


class TestPalettePartagee:
    """La palette vient de `theme.py`, commun aux trois outils Pod.

    Les faire diverger reviendrait à corriger trois fois le même défaut — et à
    en oublier deux."""

    def test_la_palette_respecte_le_seuil_daccessibilite(self):
        """⚠️ Le contraste se CALCULE. L'apprécier à l'œil a déjà produit cinq
        teintes illisibles dans PodAdmin, dont un rouge à 2,66:1."""
        import theme
        fautifs = theme.verifier_palette()
        assert not fautifs, "contrastes insuffisants : " + " ; ".join(fautifs)

    def test_aucune_couleur_appliquee_aux_deux_modes(self):
        """⚠️ Une teinte écrite SEULE s'applique telle quelle en clair ET en
        sombre. L'application en comptait 124 avant le portage : chacune
        serait devenue illisible dès l'ouverture du mode clair."""
        import app as module_app
        source = open(module_app.__file__.replace(".pyc", ".py"),
                      encoding="utf-8").read()
        fautifs = re.findall(
            r'\w*color\s*=\s*"(?:#[0-9a-fA-F]{6}|gray\d+)"', source)
        assert not fautifs, (
            f"{len(fautifs)} couleur(s) appliquée(s) aux deux modes : "
            f"{sorted(set(fautifs))[:5]}")

    def test_les_constantes_viennent_bien_du_module_commun(self):
        import app as module_app
        import theme
        for nom in ("C_ACTION", "C_NEUTRE", "S_CARTE", "T_SECONDAIRE",
                    "T_SUR_NEUTRE"):
            assert getattr(module_app, nom) == getattr(theme, nom), (
                f"{nom} diverge du module partagé")


class TestModeClairSombre:
    """L'application était figée en mode sombre."""

    def test_la_bascule_change_de_mode(self, app):
        import customtkinter as ctk
        depart = ctk.get_appearance_mode()
        app._basculer_theme()
        app.update()
        assert ctk.get_appearance_mode() != depart
        app._basculer_theme()
        app.update()
        assert ctk.get_appearance_mode() == depart

    def test_le_bouton_annonce_le_mode_vers_lequel_on_va(self, app):
        """« Mode clair » sur fond sombre se comprend sans hésiter ; l'inverse
        obligerait à réfléchir."""
        import customtkinter as ctk
        for _ in range(2):
            sombre = ctk.get_appearance_mode().lower() == "dark"
            texte = app.theme_btn.cget("text").lower()
            attendu = "clair" if sombre else "sombre"
            assert attendu in texte, (
                f"mode {ctk.get_appearance_mode()}, bouton « {texte} »")
            app._basculer_theme()
            app.update()

    def test_le_choix_est_enregistre(self, app):
        """Un enseignant qui préfère le mode clair ne doit pas le redemander à
        chaque dépôt."""
        import customtkinter as ctk

        import config as cfg
        app._basculer_theme()
        app.update()
        assert cfg.load_theme() == ctk.get_appearance_mode().lower()
        app._basculer_theme()
        app.update()
        assert cfg.load_theme() == ctk.get_appearance_mode().lower()

    def test_une_preference_illisible_retombe_sur_sombre(self):
        """Sombre par défaut : c'était le seul mode avant la 3.1, un
        utilisateur qui n'a jamais touché au réglage retrouve son application."""
        import config as cfg
        cfg.save_theme("n'importe quoi")
        assert cfg.load_theme() == "dark"


class TestAucuneSuperposition:
    """⚠️ Tk superpose SANS PRÉVENIR deux widgets sur la même cellule de
    grille : aucune erreur, juste un affichage illisible. Le défaut a été
    trouvé deux fois dans PodAdmin, dont une fois sur un avertissement de
    sécurité."""

    @staticmethod
    def _boite(w):
        return (w.winfo_rootx(), w.winfo_rooty(),
                w.winfo_rootx() + w.winfo_width(),
                w.winfo_rooty() + w.winfo_height())

    def test_tous_les_onglets(self, app):
        import app as module_app
        fautifs = []

        def controler(conteneur, onglet):
            # ⚠️ Ne PAS filtrer sur `winfo_class()` : il renvoie « Frame »
            # pour tous les widgets CustomTkinter, CTkOptionMenu compris.
            enfants = [w for w in conteneur.winfo_children()
                       if w.winfo_ismapped() and w.winfo_manager() == "grid"
                       and w.winfo_class() != "Canvas"
                       and type(w) is not module_app.ctk.CTkFrame]
            for i, w1 in enumerate(enfants):
                for w2 in enfants[i + 1:]:
                    a1, b1, c1, d1 = self._boite(w1)
                    a2, b2, c2, d2 = self._boite(w2)
                    if a1 < c2 and a2 < c1 and b1 < d2 and b2 < d1:
                        fautifs.append(f"{onglet} : deux widgets superposés")
            for enfant in conteneur.winfo_children():
                if enfant.winfo_ismapped():
                    controler(enfant, onglet)

        for cle in app.tabs:
            app._show_tab(cle)
            app.update()
            app.update_idletasks()
            controler(app.tabs[cle], cle)
        assert not fautifs, "\n  ".join(fautifs)


class TestOnglets:
    """Garde-fou de structure : les quatre onglets doivent rester joignables."""

    ATTENDUS = {"upload", "myvids", "config", "log"}

    def test_les_quatre_onglets_existent(self, app):
        assert self.ATTENDUS <= set(app.tabs), (
            f"onglets manquants : {self.ATTENDUS - set(app.tabs)}")

    def test_chaque_onglet_s_affiche(self, app):
        for cle in app.tabs:
            app._show_tab(cle)
            app.update()
            assert app.tabs[cle].winfo_ismapped(), f"« {cle} » ne s'affiche pas"


class TestMessagesDErreur:
    """Une exception ne doit jamais être montrée telle quelle.

    Les utilisateurs de cette application sont des ENSEIGNANTS. Un
    « HTTPSConnectionPool(host=…): Max retries exceeded » affiché en pleine
    figure ne dit ni ce qui s'est passé, ni quoi faire — et produit un appel au
    support."""

    def test_aucune_exception_brute_a_l_ecran(self):
        import app as module_app
        source = open(module_app.__file__.replace(".pyc", ".py"),
                      encoding="utf-8").read()
        fautifs = re.findall(
            r'configure\(\s*text=f"[^"]*\{(?:e|err|exc)\}', source)
        assert not fautifs, (
            f"{len(fautifs)} exception(s) affichée(s) telles quelles : {fautifs}")

    def test_les_pannes_reseau_sont_traduites(self):
        """Le message que voyait l'utilisateur, en entier."""
        import theme
        texte = theme.message_utilisateur(Exception(
            "HTTPSConnectionPool(host='videos.utoulouse.fr', port=443): "
            "Max retries exceeded with url: /rest/videos/"))
        assert "injoignable" in texte.lower()
        assert "HTTPSConnectionPool" not in texte

    def test_le_jeton_refuse_est_explicite(self):
        """Cas le plus fréquent chez un enseignant : un jeton périmé."""
        import pod_api
        import theme
        texte = theme.message_utilisateur(
            pod_api.PodAPIError("HTTP 401", 401, ""))
        assert "jeton" in texte.lower()
        assert "HTTP" not in texte

    def test_signaler_journalise_le_detail(self):
        """Le détail technique doit rester disponible pour le support."""
        import inspect

        import app as module_app
        source = inspect.getsource(module_app.App._signaler)
        assert "self._log(" in source, "le détail n'est pas journalisé"
        assert "body" in source, "le corps de la réponse n'est pas journalisé"
