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


class TestFiltreProprietaire:
    """« Mes vidéos » ne doit afficher QUE les vidéos du propriétaire choisi.

    Le filtrage est fait deux fois : côté serveur (paramètre `owner`, si
    l'instance l'honore) et TOUJOURS côté client. Ces tests portent sur le
    filtre client, le seul qui soit garanti."""

    class _Filtre:
        """Objet minimal : ces deux méthodes ne dépendent pas de l'interface."""
        def __init__(self):
            import app as module_app
            self._video_owner_id = module_app.App._video_owner_id.__get__(self)
            self._video_belongs_to = module_app.App._video_belongs_to.__get__(self)
            self._role_sur_video = module_app.App._role_sur_video.__get__(self)
            self._correspond = module_app.App._correspond

    BASE = "https://exemple.invalid/rest"

    def _ids(self):
        return {f"{self.BASE}/users/42/", "42", "marie"}

    def test_formats_acceptes_du_champ_owner(self):
        """L'API renvoie `owner` sous plusieurs formes selon les endpoints."""
        f, ids = self._Filtre(), self._ids()
        for cas in ({"owner": f"{self.BASE}/users/42/"},
                    {"owner": f"{self.BASE}/users/42"},        # sans barre finale
                    {"owner": {"url": f"{self.BASE}/users/42/"}},  # imbriqué
                    {"owner": "marie"},
                    {"owner": 42}):
            assert f._video_belongs_to(cas, ids), f"non reconnu : {cas}"

    def test_un_autre_proprietaire_est_exclu(self):
        f, ids = self._Filtre(), self._ids()
        assert not f._video_belongs_to({"owner": f"{self.BASE}/users/7/"}, ids)

    def test_piege_de_l_identifiant_contenu_dans_un_autre(self):
        """⚠️ L'utilisateur 142 ne doit jamais être pris pour le 42 : une
        comparaison par « se termine par » ou par sous-chaîne les
        confondrait."""
        f, ids = self._Filtre(), self._ids()
        assert not f._video_belongs_to({"owner": f"{self.BASE}/users/142/"}, ids)

    def test_coproprietaire_inclus(self):
        """Demande explicite : les vidéos dont le compte est CO-propriétaire
        entrent aussi dans « Mes vidéos »."""
        f, ids = self._Filtre(), self._ids()
        assert f._video_belongs_to(
            {"owner": f"{self.BASE}/users/7/",
             "additional_owners": [f"{self.BASE}/users/42/"]}, ids)

    def test_owner_absent_ou_vide(self):
        """Sans propriétaire identifiable, on n'affiche PAS : en cas de doute,
        ne rien montrer vaut mieux que montrer la vidéo de quelqu'un d'autre."""
        f, ids = self._Filtre(), self._ids()
        assert not f._video_belongs_to({}, ids)
        assert not f._video_belongs_to({"owner": ""}, ids)

    def test_le_filtre_client_est_toujours_applique(self):
        """Garde-fou : le filtre serveur ne doit jamais être considéré comme
        suffisant — si l'instance ignore le paramètre, elle renvoie TOUTES
        les vidéos de la plateforme.

        ⚠️ On analyse le CODE avec `ast`, pas le texte : la docstring de la
        méthode cite « _video_belongs_to » pour expliquer la stratégie, et une
        simple recherche de sous-chaîne restait verte même après suppression
        de l'appel réel (vérifié par mutation)."""
        import ast
        import inspect

        import app as module_app
        arbre = ast.parse(inspect.getsource(module_app.App._do_myvids_load).strip())
        appels = {n.func.attr for n in ast.walk(arbre)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert "_video_belongs_to" in appels, (
            "le filtre client a disparu : une instance qui ignore le "
            "paramètre `owner` afficherait les vidéos de tout le monde")


class TestFiltreServeurFacultatif:
    """⚠️ INCIDENT RÉEL : sur videos.utoulouse.fr, le filtre `owner=<URL>` est
    refusé par le serveur (« Sélectionnez un choix valide », HTTP 400). Le code
    ne tentait que cette forme, et son échec faisait échouer TOUT le
    chargement : l'onglet « Mes vidéos » restait vide, avec un message
    d'erreur incompréhensible pour un enseignant.

    Le filtre serveur n'est qu'une optimisation : son refus doit mener à une
    lecture complète, re-filtrée côté client."""

    BASE = "https://exemple.invalid/rest"

    class _API:
        def __init__(self, accepte=None):
            self.accepte, self.appels = accepte, []

        def get_all_videos(self, progress_cb=None, extra_params=None):
            self.appels.append(extra_params)
            if extra_params and extra_params != self.accepte:
                raise Exception("HTTP 400 : owner : Sélectionnez un choix valide.")
            b = TestFiltreServeurFacultatif.BASE
            return [{"slug": "a", "owner": f"{b}/users/42/"},
                    {"slug": "b", "owner": f"{b}/users/7/"}]

    def _preparer(self, app, api):
        app.api = api
        url = f"{self.BASE}/users/42/"
        app._myvids_current_owner = lambda: (url, "marie")
        return url

    def test_un_refus_du_serveur_ne_bloque_pas_le_chargement(self, app):
        api = self._API(accepte=None)          # refuse TOUTES les formes
        url = self._preparer(app, api)
        videos = app._myvids_lire_videos(url, lambda n: None)
        assert videos, "le refus du filtre serveur a empêché le chargement"
        assert api.appels[-1] is None, (
            "pas de repli sur une lecture complète après les refus")

    def test_filtre_owner_seul_ne_suffit_plus(self, app):
        """⚠️ `owner=` ne renvoie que les vidéos POSSÉDÉES. Si le filtre de
        co-propriété est refusé, il faut une lecture complète — sinon les
        vidéos partagées disparaîtraient de « Mes vidéos »."""
        api = self._API(accepte={"owner": "42"})
        url = self._preparer(app, api)
        app._myvids_lire_videos(url, lambda n: None)
        assert api.appels[0] == {"owner": "42"}
        assert api.appels[-1] is None, "pas de lecture complète après le refus"

    def test_le_repli_reste_filtre_cote_client(self, app):
        """Après une lecture complète, seules les vidéos du propriétaire
        doivent subsister — le repli ne doit jamais tout afficher."""
        import app as module_app
        api = self._API(accepte=None)
        url = self._preparer(app, api)
        brut = app._myvids_lire_videos(url, lambda n: None)
        ids = {url, "42", "marie"}
        gardees = [v for v in brut
                   if module_app.App._video_belongs_to(app, v, ids)]
        assert [v["slug"] for v in gardees] == ["a"]


class TestDisciplineAuTeleversement:
    """Étape 4 : discipline commune au lot, comme dans PodAdmin.

    Classer au dépôt coûte un choix ; rattacher après coup coûte une reprise de
    centaines de vidéos. Le champ reste FACULTATIF."""

    def test_table_vide_annoncee_explicitement(self, app):
        app.discipline_map = {}
        app._rafraichir_menu_discipline()
        assert app.upload_discipline.get() == app.AUCUNE_DISCIPLINE

    def test_sans_discipline_ne_rattache_rien(self, app):
        app.discipline_map = {"Physique": "https://x/discipline/2/"}
        app._rafraichir_menu_discipline()
        app.upload_discipline.set(app.SANS_DISCIPLINE)
        assert app._discipline_choisie() == ""
        app.upload_discipline.set("Physique")
        assert app._discipline_choisie() == "https://x/discipline/2/"

    def test_le_rattachement_se_fait_en_liste(self):
        """⚠️ Relation MULTIPLE : une URL nue est refusée par l'API."""
        import inspect

        import pod_api
        source = inspect.getsource(pod_api.PodAPI.set_disciplines)
        assert '"discipline": list(discipline_urls)' in source

    def test_un_echec_ne_perd_pas_la_video(self):
        """La vidéo est déposée ; seul son classement manque."""
        import inspect

        import app as module_app
        source = inspect.getsource(module_app.App._do_batch_upload)
        bloc = source[source.index("set_disciplines") - 300:source.index("set_disciplines") + 300]
        assert "except Exception" in bloc

    def test_la_relance_transmet_la_discipline(self):
        """Sans cela, une vidéo relancée après échec perdrait son classement."""
        import inspect

        import app as module_app
        source = inspect.getsource(module_app.App._retry_failed)
        assert "_last_discipline_url" in source

    def test_discipline_a_cote_du_type(self, app):
        """Même ligne que le Type — et surtout pas la ligne 2, colonnes 2-3,
        déjà occupée par « Propriétaires additionnels »."""
        info_d = app.upload_discipline.grid_info()
        info_t = app.type_combo.grid_info()
        assert int(info_d["row"]) == int(info_t["row"])


import os as _os

RACINE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
os = _os


class TestHierarchieDesCouleurs:
    """Étape 5 : mêmes règles de couleur que PodAdmin.

    • aucun bouton ni menu laissé dans le bleu par défaut de CustomTkinter ;
    • un bouton gris porte un texte SOMBRE en mode clair — du blanc sur
      C_NEUTRE donne 2,44:1, illisible ;
    • aucune teinte seule (« gray », « #ef4444 ») : elle vaut pour les deux
      modes, et plusieurs tombaient sous le seuil de lisibilité en clair."""

    @staticmethod
    def _appels():
        import ast
        source = open(os.path.join(RACINE, "app.py"), encoding="utf-8").read()
        for n in ast.walk(ast.parse(source)):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr in ("CTkButton", "CTkOptionMenu", "CTkComboBox"):
                kws = {k.arg: k for k in n.keywords}
                yield n, kws, source

    def test_aucun_element_dans_le_bleu_par_defaut(self):
        import ast
        fautifs = []
        for n, kws, source in self._appels():
            deplie = any(k.arg is None for k in n.keywords)      # **STYLE_…
            if "fg_color" not in kws and not deplie:
                fautifs.append((n.lineno, n.func.attr))
        assert not fautifs, f"éléments dans le bleu par défaut : {fautifs}"

    def test_bouton_gris_avec_texte_sombre(self):
        import ast
        fautifs = []
        for n, kws, source in self._appels():
            if n.func.attr != "CTkButton" or "fg_color" not in kws:
                continue
            if "C_NEUTRE" in ast.get_source_segment(source, kws["fg_color"].value) \
                    and "text_color" not in kws:
                fautifs.append(n.lineno)
        assert not fautifs, f"boutons gris à texte blanc illisible, lignes {fautifs}"

    def test_aucune_teinte_seule(self):
        import re
        source = open(os.path.join(RACINE, "app.py"), encoding="utf-8").read()
        fautifs = re.findall(
            r'text_color="(?:gray\d*|#[0-9a-fA-F]{6})"'
            r'|_set_item_status\([^)\n]*"(?:gray\d*|#[0-9a-fA-F]{6})"\)', source)
        assert not fautifs, f"teintes seules : {fautifs[:5]}"


class TestAucunWidgetLuDepuisLeThreadDEnvoi:
    """Lire un widget Tk depuis un thread de travail provoque des plantages
    ALÉATOIRES (« main thread is not in main loop »). PodAdmin avait corrigé
    ce défaut ; la v3 lisait encore la visibilité et la case d'encodage
    depuis le thread d'envoi."""

    def test_l_envoi_ne_lit_aucun_widget(self):
        import ast
        import inspect

        import app as module_app
        arbre = ast.parse(inspect.getsource(module_app.App._do_batch_upload).strip())
        lectures = [n.lineno for n in ast.walk(arbre)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "get" and not n.args]
        assert not lectures, f"lecture de widget dans le thread, lignes {lectures}"



class TestCoProprieteDansMesVideos:
    """Les vidéos en co-propriété entrent dans « Mes vidéos » ; seule la
    suppression reste réservée au propriétaire (Pod la refuse sinon)."""

    B = "https://exemple.invalid/rest"

    @pytest.fixture(autouse=True)
    def _restaurer(self, app):
        """Instance PARTAGÉE : ce qu'on remplace ici doit être rendu, sinon un
        test placé après hériterait d'un `_run` qui n'exécute plus rien."""
        sauvegarde = {n: app.__dict__.get(n) for n in
                      ("_myvids_current_owner", "_run", "_myvids_set_msg")}
        yield
        for nom, valeur in sauvegarde.items():
            if valeur is None:
                app.__dict__.pop(nom, None)
            else:
                app.__dict__[nom] = valeur

    def _app(self, app):
        app._myvids_current_owner = lambda: (f"{self.B}/users/42/", "marie")
        return app

    def test_identifiants_calcules_depuis_une_url_a_barre_finale(self, app):
        """⚠️ Défaut réel : sur une URL finissant par « / », le numéro du
        compte n'était jamais extrait, et une vidéo dont `owner` est l'URL —
        la forme de l'API — n'était pas reconnue. Les tests précédents
        fournissaient un ensemble tout fait et ne pouvaient pas le voir : ce
        test passe par la VRAIE fonction."""
        a = self._app(app)
        ids = a._myvids_owner_ids()
        assert "42" in ids
        assert a._role_sur_video({"owner": f"{self.B}/users/42/"}, ids) == "proprietaire"

    def test_role_coproprietaire(self, app):
        a = self._app(app)
        ids = a._myvids_owner_ids()
        v = {"owner": f"{self.B}/users/7/", "additional_owners": [{"url": f"{self.B}/users/42/"}]}
        assert a._role_sur_video(v, ids) == "coproprietaire"

    def test_piege_142_en_copropriete(self, app):
        a = self._app(app)
        v = {"owner": f"{self.B}/users/7/", "additional_owners": [f"{self.B}/users/142/"]}
        assert a._role_sur_video(v, a._myvids_owner_ids()) is None

    def test_la_suppression_est_refusee_au_coproprietaire(self, app, monkeypatch):
        """⚠️ La boîte de confirmation est REMPLACÉE : sans cela, un garde-fou
        manquant ouvrait la vraie boîte, qui attendait un clic — la suite de
        tests se figeait au lieu d'échouer (vu par mutation)."""
        import app as module_app
        a = self._app(app)
        appels, confirmations = [], []
        a._run = lambda *x, **k: appels.append(x)
        a._myvids_set_msg = lambda *x, **k: None
        monkeypatch.setattr(module_app.messagebox, "askyesno",
                            lambda *x, **k: confirmations.append(x) or True)
        v = {"slug": "s", "title": "T", "owner": f"{self.B}/users/7/",
             "additional_owners": [f"{self.B}/users/42/"]}
        a._myvids_delete(v)
        assert not confirmations, "une confirmation de suppression a été demandée"
        assert not appels, "la suppression a été lancée pour un co-propriétaire"


class TestAide:
    """L'aide doit décrire l'application telle qu'elle est.

    Défauts trouvés à sa mise à jour : huit sauts de ligne écrits « \\\\n »,
    donc affichés tels quels, et deux boutons cités qui n'existaient pas
    (« Actualiser » pour « Rafraîchir », « Ouvrir sur le site » pour « Ouvrir
    dans le navigateur »)."""

    @staticmethod
    def _sections():
        source = open(os.path.join(RACINE, "app.py"), encoding="utf-8").read()
        d = source.index("        sections = [")
        f = source.index("        # Rendu automatique des sections")
        return source, source[d:f]

    def test_aucun_saut_de_ligne_affiche_en_clair(self):
        _, bloc = self._sections()
        assert "\\\\n" not in bloc, "l'aide affiche « \\\\n » au lieu d'aller à la ligne"

    def test_les_boutons_cites_existent(self):
        import re
        source, bloc = self._sections()
        reste = source.replace(bloc, "")
        cites = ["🔄 Rafraîchir", "Ouvrir dans le navigateur",
                 "Télécharger la mise à jour", "Supprimer cette vidéo", "Quitter"]
        for nom in cites:
            assert nom in bloc, f"l'aide ne cite plus « {nom} »"
            mots = nom.replace("🔄 ", "")
            assert re.search(r'text="[^"]*' + re.escape(mots), reste), (
                f"l'aide cite « {nom} », mais aucun bouton ne porte ce texte")

    def test_nouveautes_documentees(self):
        _, bloc = self._sections()
        for sujet in ("Discipline", "co-propriétaire", "OBLIGATOIRES", "Mode clair"):
            assert sujet in bloc, f"l'aide ne mentionne pas : {sujet}"


class TestAucuneExceptionBrute:
    """Analyse syntaxique reprise de PodAdmin : l'ancien test de la v3 ne
    cherchait qu'une forme et laissait passer les messages de « Mes vidéos »."""

    def test_aucun_message_brut_ne_subsiste(self):
        """Aucune exception insérée telle quelle dans un texte AFFICHÉ.

        ⚠️ Quatre versions successives de ce test ont laissé passer des cas.
        Les trois premières cherchaient des motifs textuels (« text=f… »,
        « ❌ … », puis une fenêtre de lignes autour de la chaîne) : chacune
        ratait une forme, la dernière parce qu'un appel au Journal situé
        juste au-dessus « couvrait » l'affichage qui le suivait.

        On analyse donc l'ARBRE SYNTAXIQUE : pour chaque chaîne formatée
        contenant une exception (e, exc, err), on remonte à l'instruction qui
        la contient réellement. Seules sont autorisées les destinations non
        affichées : le Journal (`_log`), un attribut `.error` gardé en
        mémoire, une variable de travail, ou une exception relancée."""
        import ast

        source = open(os.path.join(RACINE, "app.py"), encoding="utf-8").read()
        arbre = ast.parse(source)
        parents = {}
        for noeud in ast.walk(arbre):
            for enfant in ast.iter_child_nodes(noeud):
                parents[enfant] = noeud

        def contient_exception(js):
            for v in ast.walk(js):
                if isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name) \
                        and v.value.id in ("e", "exc", "err"):
                    return True
            return False

        def destination_autorisee(noeud):
            courant = noeud
            while courant in parents:
                precedent, courant = courant, parents[courant]
                # f"…".lower() : la chaîne est l'OBJET de la méthode, pas un
                # argument — on remonte jusqu'à ce qu'on en fait vraiment.
                if isinstance(courant, ast.Attribute) and courant.value is precedent:
                    continue
                if isinstance(courant, ast.Call) and courant.func is precedent:
                    continue
                if isinstance(courant, ast.Raise):
                    return True
                if isinstance(courant, (ast.Assign, ast.AugAssign)):
                    cibles = courant.targets if isinstance(courant, ast.Assign) \
                        else [courant.target]
                    for c in cibles:
                        if isinstance(c, ast.Attribute) and c.attr == "error":
                            return True
                        if isinstance(c, ast.Name) and c.id in ("texte", "detail"):
                            return True
                    return False
                if isinstance(courant, ast.Call):
                    f = courant.func
                    nom = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                    if nom in ("_log", "journal", "tracer"):
                        return True
                    # self._ui(self._log, f"…") : le Journal est le 1er argument
                    if nom == "_ui" and courant.args:
                        cible = courant.args[0]
                        if isinstance(cible, ast.Attribute) and cible.attr == "_log":
                            return True
                    return False
                if isinstance(courant, (ast.FunctionDef, ast.Module)):
                    return False
            return False

        fautifs = [(n.lineno, ast.get_source_segment(source, n)[:60])
                   for n in ast.walk(arbre)
                   if isinstance(n, ast.JoinedStr) and contient_exception(n)
                   and not destination_autorisee(n)]
        assert not fautifs, (
            f"exception(s) affichée(s) telles quelles (ligne, extrait) : {fautifs}")


class TestSelectionMultiple:
    """Étape 6 : sélection multiple dans « Mes vidéos » et actions de lot."""

    B = "https://exemple.invalid/rest"

    @pytest.fixture(autouse=True)
    def _preparer(self, app):
        sauvegarde = {n: app.__dict__.get(n) for n in ("_myvids_current_owner", "api", "_run")}
        app._myvids_current_owner = lambda: (f"{self.B}/users/42/", "marie")
        app.myvids_videos = [
            {"slug": f"v{i}", "title": f"V{i}", "owner": f"{self.B}/users/42/",
             "is_draft": False, "channel": []} for i in range(4)] + [
            {"slug": "co", "title": "Partagée", "owner": f"{self.B}/users/7/",
             "additional_owners": [f"{self.B}/users/42/"], "is_draft": False, "channel": []}]
        app.myvids_filtered = list(app.myvids_videos)
        app.myvids_multi, app.myvids_selected = [], None
        # _run exécuté tout de suite : le lot devient synchrone et vérifiable.
        app._run = lambda fn, *x: fn(*x)
        yield
        app.myvids_multi, app.myvids_selected = [], None
        for nom, valeur in sauvegarde.items():
            if valeur is None:
                app.__dict__.pop(nom, None)
            else:
                app.__dict__[nom] = valeur

    def test_ctrl_clic_emporte_la_video_ouverte(self, app):
        app.myvids_selected = app.myvids_videos[0]
        app._myvids_toggle_multi(app.myvids_videos[2])
        assert app.myvids_multi == ["v0", "v2"]

    def test_maj_clic_prend_la_plage(self, app):
        app._myvids_ancre = "v1"
        app._myvids_plage_multi(app.myvids_videos[3])
        assert app.myvids_multi == ["v1", "v2", "v3"]

    def test_clic_simple_annule_la_selection(self, app):
        app.myvids_multi = ["v0", "v1"]
        app._myvids_select(app.myvids_videos[2])
        assert app.myvids_multi == []

    def test_le_relachement_du_ctrl_clic_est_bloque(self):
        """⚠️ CTkButton agit au RELÂCHEMENT : sans ce blocage, un Ctrl+clic
        lançait aussi une sélection simple (piège rencontré dans PodAdmin)."""
        source = open(os.path.join(RACINE, "app.py"), encoding="utf-8").read()
        assert '"<Control-ButtonRelease-1>", lambda e: "break"' in source
        assert '"<Shift-ButtonRelease-1>", lambda e: "break"' in source

    def test_la_suppression_en_lot_ignore_la_copropriete(self, app, monkeypatch):
        import app as module_app
        supprimees = []

        class _API:
            def delete_video(self, v):
                supprimees.append(v["slug"])
        app.api = _API()
        monkeypatch.setattr(module_app.messagebox, "askyesno", lambda *x, **k: True)
        app._myvids_tout_selectionner()
        app._myvids_lot_supprimer()
        assert "co" not in supprimees, "une vidéo en co-propriété a été supprimée"
        assert sorted(supprimees) == ["v0", "v1", "v2", "v3"]

    def test_un_echec_n_arrete_pas_le_lot(self, app):
        """Chaque vidéo est traitée INDÉPENDAMMENT."""
        faites = []

        def action(v):
            if v["slug"] == "v1":
                raise RuntimeError("HTTP 500")
            faites.append(v["slug"])
        app._do_myvids_lot(app.myvids_videos[:3], action, lambda v: None, "test")
        assert faites == ["v0", "v2"], "le lot s'est arrêté au premier échec"

    def test_statut_en_lot_envoie_les_deux_booleens(self, app, monkeypatch):
        """Les trois statuts sont exclusifs : chaque choix envoie les DEUX
        champs, pour ne jamais laisser une vidéo brouillon ET restreinte."""
        import app as module_app
        envois = []

        class _API:
            def patch_video(self, v, p):
                envois.append(dict(p))
        app.api = _API()
        monkeypatch.setattr(module_app.messagebox, "askyesno", lambda *x, **k: True)
        app.myvids_multi = ["v0", "v1"]
        app._myvids_lot_statut("Restreint")
        assert envois == [{"is_draft": False, "is_restricted": True}] * 2

    def test_une_video_seule_a_disciplines_et_chaines_themes(self):
        import inspect

        import app as module_app
        source = inspect.getsource(module_app.App._myvids_render_detail)
        assert "_myvids_edit_disciplines" in source
        assert "_myvids_edit_channels" in source


class TestBarreEnMasseRetiree:
    """La barre « Modifier en masse » a été retirée (PodAdmin et v3) : le type
    d'un lot passe par la sélection, avec le nombre de vidéos dans le bouton."""

    def test_barre_absente(self, app):
        assert not hasattr(app, "myvids_mass_type")

    def test_le_bouton_de_type_du_lot_porte_le_nombre(self):
        import inspect

        import app as module_app
        source = inspect.getsource(module_app.App._myvids_render_lot)
        assert 'f"Appliquer le type à {len(vids)} vidéos"' in source
