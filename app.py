#!/usr/bin/env python3
"""
app.py — Pod Téléverseur (interface graphique)
Université de Toulouse

Application de téléversement par lot pour l'instance Esup-Pod de
l'Université de Toulouse. Version légère destinée aux enseignants :
  • Téléversement par lot (glisser-déposer, titres éditables, propriétaires
    additionnels communs, lancement d'encodage automatique après l'envoi).
  • Mes vidéos : gestion des vidéos du propriétaire sélectionné (renommer,
    statut, type, co-propriétaires, sous-titres, remplacer le fichier &
    ré-encoder, supprimer). N'affiche que les vidéos de ce compte.
  • Configuration (connexion à l'instance + identifiant du compte déposant).
  • Journal des opérations.
Dérivée de PodAdmin : les modules d'administration (groupes, réaffectation,
inventaire…) ont été retirés ; « Mes vidéos » reprend l'essentiel de l'onglet
Vidéos de PodAdmin, borné au propriétaire courant.
"""

from __future__ import annotations

__author__      = "Cédric MONNA, Philippe BAQUÉ, Michel JACOB"
__contact__     = "support-pod@utoulouse.fr"
__institution__ = "Université de Toulouse"
from __version__ import __version__   # source unique (voir __version__.py)
__date__        = "2026"
__license__     = "Usage interne — Université de Toulouse"


import os
import re
import sys
import threading
import uuid
from datetime import datetime

import customtkinter as ctk
from tkinter import filedialog, messagebox

import config as cfg
import maj                     # vérification de mise à jour (dépôt public)
# Apparence et messages PARTAGÉS avec PodAdmin et le Téléverseur v2.
#
# L'import est global (`*`) à dessein : les constantes de palette sont
# employées des centaines de fois dans ce fichier, et les préfixer toutes
# rendrait le code illisible sans rien apprendre. Le module n'exporte que des
# couleurs et trois fonctions, tous en MAJUSCULES ou nommés sans ambiguïté.
from theme import *                                    # noqa: F401,F403
from theme import message_utilisateur, etat_vide, verifier_palette  # noqa: F401
from pod_api import PodAPI, PodAPIError, EnvoiAnnule, SUBTITLE_LANGS, SUBTITLE_KINDS
# Moteur de téléversement par morceaux via session web (gros fichiers > seuil).
from pod_chunked import PodChunkedSession, PodChunkedError
from pod_chunked import EnvoiAnnule as EnvoiAnnuleMorceaux

# Arrêts demandés par l'utilisateur (bouton 🛑 du Téléversement). Chacun des
# deux modules d'envoi a sa classe, pour rester indépendant de l'autre.
ANNULATIONS = (EnvoiAnnule, EnvoiAnnuleMorceaux)

# Pillow (fourni avec customtkinter) — pour afficher le logo
try:
    from PIL import Image as PILImage
    HAS_PIL = True
except Exception:
    HAS_PIL = False


def resource_path(rel: str) -> str:
    """Chemin d'une ressource, compatible PyInstaller (--onefile) et exécution directe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)

# Glisser-déposer (optionnel — l'appli fonctionne sans, via les boutons)
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except Exception:
    HAS_DND = False

APP_TITLE = "Pod Téléverseur — Université de Toulouse"
APP_VERSION = __version__

# Délai avant de rafraîchir une liste pendant la frappe dans un filtre (ms).
FILTER_DELAY_MS = 250

# Statut d'une vidéo → champs envoyés. Les trois états sont EXCLUSIFS :
# chaque choix envoie les DEUX booléens, pour ne jamais laisser une vidéo à
# la fois « brouillon » et « restreinte ».
STATUTS = {"Brouillon": {"is_draft": True, "is_restricted": False},
           "Public":    {"is_draft": False, "is_restricted": False},
           "Restreint": {"is_draft": False, "is_restricted": True}}

# Texte de la fenêtre de mise à jour OBLIGATOIRE. Volontairement neutre : il
# ne donne jamais la raison du blocage (voir `_bloquer_demarrage`).
MESSAGE_BLOCAGE = ("Une nouvelle version de Pod Téléverseur est nécessaire "
                   "pour continuer. Téléchargez-la et installez-la.")



# ════════════════════════════════════════════════════════════════════════════
#  MODÈLE : une entrée de la file d'attente
# ════════════════════════════════════════════════════════════════════════════

class UploadItem:
    """Une vidéo dans la file de téléversement.

    Regroupe le fichier à envoyer, son titre (modifiable), son état d'avancement
    et, une fois déposée, son slug et son URL. Les attributs `row`, `title_var`
    et `status_lbl` sont les widgets d'affichage, remplis au moment du rendu."""
    def __init__(self, path: str):
        """Crée une entrée de la file d'upload à partir d'un chemin de fichier."""
        self.path = path
        self.filename = os.path.basename(path)
        # Titre par défaut = nom de fichier sans extension, nettoyé
        base = os.path.splitext(self.filename)[0]
        self.title = base.replace("_", " ").replace("-", " ").strip()
        self.status = "en attente"     # en attente | en cours | terminé | échec
        self.done = False              # True dès qu'un envoi a réussi (pour ne pas
                                       # ré-uploader un succès lors d'une relance)
        # Envoi arrêté pendant l'attente qui suit un 504 : la vidéo a PEUT-ÊTRE
        # été créée au nom du compte DEPOT. Elle n'est plus jamais relancée
        # automatiquement — ce serait un doublon — tant que personne n'a vérifié.
        self.a_verifier = False
        self.slug = ""
        self.video_url = ""
        self.error = ""
        # widgets (remplis à l'affichage)
        self.row = None
        self.title_var = None
        self.status_lbl = None
        self.btn_retirer = None        # « ✕ » de la ligne, grisé pendant un lot


# ════════════════════════════════════════════════════════════════════════════
#  APPLICATION
# ════════════════════════════════════════════════════════════════════════════

# Base conditionnelle : mixe le moteur de glisser-déposer si disponible.
# tkinterdnd2 n'est pas toujours installé → on choisit la classe de base en
# conséquence, pour que l'appli fonctionne même sans glisser-déposer.
if HAS_DND:
    class _AppBase(ctk.CTk, TkinterDnD.DnDWrapper):
        """Classe de base de la fenêtre principale (avec glisser-déposer si tkinterdnd2 est disponible)."""
        pass          # CTk + capacité de glisser-déposer
else:
    class _AppBase(ctk.CTk):
        """Classe de base de la fenêtre principale (avec glisser-déposer si tkinterdnd2 est disponible)."""
        pass          # CTk seul (pas de glisser-déposer)


class App(_AppBase):
    """Fenêtre principale de Pod Téléverseur.

    Assemble l'interface (barre latérale + onglets Téléversement, Configuration,
    Journal…), gère la connexion à l'instance (token), le scan et le dépôt des
    vidéos par lot, et — pour les fichiers > 150 Mo — la bascule vers le
    téléversement par morceaux via le compte véhicule DEPOT puis la réattribution
    au propriétaire choisi. Toutes les opérations réseau tournent dans des threads
    séparés ; les mises à jour d'interface repassent par le thread principal via
    les helpers `_run` (tâche de fond) et `_ui` (mise à jour d'affichage)."""
    def __init__(self):
        """Initialise la fenêtre, charge config + token, construit l'UI et tente une connexion auto."""
        super().__init__()
        # Initialiser le moteur glisser-déposer (tkdnd)
        self.dnd_ok = False
        if HAS_DND:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
                self.dnd_ok = True
            except Exception:
                self.dnd_ok = False

        self.title(APP_TITLE)
        self.geometry("1180x760")
        self.minsize(1000, 660)

        self.config_data = cfg.load_config()
        self.token = cfg.load_token()
        # Compte VÉHICULE embarqué (DEPOT) : session web pour le chunké des gros
        # fichiers. Les enseignants ne le voient ni ne le saisissent.
        self.vehicle_username = cfg.VEHICLE_USERNAME
        self.vehicle_password = cfg.VEHICLE_PASSWORD
        self.vehicle_owner_url = ""      # URL Pod du véhicule (résolue à la connexion)
        self.api: PodAPI | None = None

        self.types: list[dict] = []
        self.type_map: dict[str, str] = {}     # titre → url
        self.site_urls: list[str] = []         # sites (requis à l'upload)
        self.items: list[UploadItem] = []
        # Arrêt du lot de téléversement (bouton 🛑, repris de PodAdmin 1.9.2).
        self.depot_interrompu = threading.Event()
        # Vrai pendant un lot. La boucle d'envoi parcourt `self.items` par
        # INDEX : retirer une ligne en cours de route décalait la liste et
        # faisait sauter, sans rien dire, la vidéo suivante. Pendant un lot, on
        # peut donc AJOUTER (elles partent à la suite) mais pas retirer.
        self.depot_en_cours = False
        # Fichiers envoyés depuis l'ouverture de l'application :
        # clé normalisée du chemin → (heure, slug). Sert à prévenir avant de
        # renvoyer un fichier déjà envoyé puis retiré de la liste (doublon sur
        # Pod). Mémoire de SESSION seulement : elle disparaît à la fermeture.
        self.deposes_session: dict[str, tuple[str, str]] = {}
        self.all_users: list[dict] = []        # liste complète Pod (pour sélection owner)
        self.additional_owner_urls: list[str] = []
        self.additional_owner_map: dict[str, str] = {}   # url → libellé (pour ré-ouverture)
        self.common_contributors: list[dict] = []

        # Liaisons (« hooks ») utilisées par l'assistant de première utilisation
        # pour réagir au résultat de la connexion. None = aucun assistant en cours.
        self._post_connect_ok = None    # appelé après une connexion réussie
        self._post_connect_err = None   # appelé après un échec de connexion

        # Bandeau « nouvelle version » : AUCUN widget créé ici. Un conteneur
        # vide réservé d'avance se dessinait en CARRÉ NOIR sur macOS.
        self.maj_bandeau = None

        self._build_ui()
        self._show_tab("upload")

        # ── Blocage à distance (interrupteur manuel, repris de PodAdmin,
        # INDÉPENDANT de la mise à jour obligatoire ci-dessous) : contrôle local
        # immédiat — un blocage déjà confirmé s'applique sans attendre le
        # réseau —, puis surveillance périodique. Placé AVANT le contrôle de
        # mise à jour obligatoire et avant son `return` : les deux mécanismes
        # restent orthogonaux, l'un ne conditionne jamais l'autre.
        self._voile_blocage = None
        if cfg.blocage_distant_actif():
            self._bloquer_application()
        self.after(2000, self._surveiller_blocage)

        # ⚠️ CONTRÔLE LOCAL DU BLOCAGE, EN TOUT PREMIER — avant l'auto-connexion
        # et avant l'assistant : un blocage déjà confirmé par le serveur doit
        # s'appliquer SANS ATTENDRE le réseau, sinon la personne pourrait
        # interagir avec l'application pendant la vérification.
        blocage_local = cfg.blocage_local_actif(APP_VERSION)
        if blocage_local:
            self._bloquer_demarrage({
                "version": blocage_local["version"],
                "url": blocage_local["url"],
                "notes": blocage_local["notes"],
                "urgent": True,
                "obligatoire": True,
            })
            self.after(2000, self._verifier_maj)
            return

        # Démarrage :
        #   • token déjà enregistré → reconnexion automatique silencieuse ;
        #   • aucun token (= première utilisation sur ce poste) → on ouvre
        #     l'assistant guidé qui prend l'enseignant par la main.
        if self.config_data.get("url") and self.token:
            self._run(self._auto_connect)
        elif not self.token:
            self.after(300, self._first_run_wizard)

        # Vérification de mise à jour, DIFFÉRÉE et en arrière-plan : elle ne
        # doit jamais retarder l'ouverture de la fenêtre.
        self.after(2000, self._verifier_maj)

    # ── Threading helpers ────────────────────────────────────────────────

    def _run(self, fn, *a):
        """Lance une fonction dans un thread d'arrière-plan (pour ne pas geler l'interface)."""
        threading.Thread(target=fn, args=a, daemon=True).start()

    def _ui(self, fn, *a, **kw):
        """Planifie une mise à jour d'interface dans le thread principal Tk (thread-safe)."""
        self.after(0, lambda: fn(*a, **kw))

    # ── Construction de l'interface ──────────────────────────────────────

    def _build_ui(self):
        """Construit la barre latérale (logo, état, navigation) et la zone de contenu."""
        # Sidebar
        self.sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        # En-tête : logo Université de Toulouse sur bandeau blanc (repli texte si absent)
        logo_loaded = False
        if HAS_PIL:
            try:
                logo_path = resource_path(os.path.join("assets", "logo_ut.png"))
                if os.path.exists(logo_path):
                    pil = PILImage.open(logo_path)
                    W = 178
                    H = round(W * pil.height / pil.width)
                    self.logo_img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(W, H))
                    card = ctk.CTkFrame(self.sidebar, fg_color="white", corner_radius=8)
                    card.pack(padx=12, pady=(18, 6), fill="x")
                    ctk.CTkLabel(card, image=self.logo_img, text="").pack(padx=10, pady=10)
                    logo_loaded = True
            except Exception:
                logo_loaded = False

        if not logo_loaded:
            ctk.CTkLabel(self.sidebar, text="Université de Toulouse",
                         font=ctk.CTkFont(size=13, weight="bold")).pack(pady=(20, 0), padx=14)

        ctk.CTkLabel(self.sidebar, text="📂  Pod Téléverseur",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(2, 0), padx=14)

        # État connexion
        box = ctk.CTkFrame(self.sidebar, fg_color=S_LIGNE, corner_radius=8)
        box.pack(padx=12, pady=14, fill="x")
        self.status_dot = ctk.CTkLabel(box, text="⚫", font=ctk.CTkFont(size=13))
        self.status_dot.pack(side="left", padx=8, pady=6)
        self.status_lbl = ctk.CTkLabel(box, text="Non connecté",
                                       font=ctk.CTkFont(size=11), text_color=T_SECONDAIRE)
        self.status_lbl.pack(side="left")

        # Agent identifié
        self.agent_lbl = ctk.CTkLabel(self.sidebar, text="", font=ctk.CTkFont(size=11),
                                      text_color=T_SECONDAIRE, wraplength=190, justify="left")
        self.agent_lbl.pack(padx=14, pady=(0, 6), anchor="w")

        ctk.CTkFrame(self.sidebar, height=1, fg_color=C_NEUTRE).pack(fill="x", padx=12, pady=4)

        self.nav_btns = {}
        for label, key in [
            ("📂   Téléversement", "upload"),
            ("🎞️   Mes vidéos",   "myvids"),
            ("⚙️   Configuration", "config"),
            ("📋   Journal",       "log"),
        ]:
            b = ctk.CTkButton(self.sidebar, text=label, anchor="w", height=40,
                              fg_color="transparent", text_color=("gray10", "gray90"),
                              hover_color=("gray75", "gray28"),
                              font=ctk.CTkFont(size=13),
                              command=lambda k=key: self._show_tab(k))
            b.pack(fill="x", padx=6, pady=2)
            self.nav_btns[key] = b

        # Bouton « Aide » (fenêtre d'explications, pas un onglet)
        ctk.CTkButton(self.sidebar, text="❓   Aide", anchor="w", height=40,
                      fg_color="transparent", text_color=("gray10", "gray90"),
                      hover_color=("gray75", "gray28"),
                      font=ctk.CTkFont(size=13),
                      command=self._show_help).pack(fill="x", padx=6, pady=2)

        # Bouton « À propos » (fenêtre d'information, pas un onglet)
        ctk.CTkButton(self.sidebar, text="ℹ️   À propos", anchor="w", height=40,
                      fg_color="transparent", text_color=("gray10", "gray90"),
                      hover_color=("gray75", "gray28"),
                      font=ctk.CTkFont(size=13),
                      command=self._show_about).pack(fill="x", padx=6, pady=2)

        ctk.CTkLabel(self.sidebar, text=f"v{APP_VERSION}",
                     font=ctk.CTkFont(size=9), text_color=T_DISCRET).pack(side="bottom", pady=10)

        # Bascule clair / sombre.
        #
        # L'application était figée en mode sombre. Le choix est enregistré
        # dans la configuration : un enseignant qui préfère le mode clair ne
        # doit pas avoir à le redemander à chaque dépôt.
        self.theme_btn = ctk.CTkButton(
            self.sidebar, text="", width=150, height=26,
            font=ctk.CTkFont(size=11), fg_color=C_NEUTRE,
            hover_color=C_NEUTRE_SURV, text_color=T_SUR_NEUTRE,
            command=self._basculer_theme)
        self.theme_btn.pack(side="bottom", pady=(6, 2))
        self._maj_libelle_theme()

        # Zone principale
        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content.pack(side="right", fill="both", expand=True, padx=14, pady=14)

        self.tabs = {}
        self._build_tab_upload()
        self._build_tab_myvids()
        self._build_tab_config()
        self._build_tab_log()

    def _basculer_theme(self):
        """Passe du mode sombre au mode clair, et inversement."""
        nouveau = "light" if ctk.get_appearance_mode().lower() == "dark" else "dark"
        ctk.set_appearance_mode(nouveau)
        self._maj_libelle_theme()
        try:
            cfg.save_theme(nouveau)
        except Exception:
            pass          # une préférence non enregistrée ne doit rien casser
        self._log(f"Thème : mode {'clair' if nouveau == 'light' else 'sombre'}.")

    def _maj_libelle_theme(self):
        """Le bouton annonce le mode VERS lequel il bascule, pas le mode
        courant : « Mode clair » sur fond sombre se comprend sans hésiter."""
        sombre = ctk.get_appearance_mode().lower() == "dark"
        try:
            self.theme_btn.configure(text="☀  Mode clair" if sombre
                                     else "🌙  Mode sombre")
        except Exception:
            pass

    def _show_tab(self, key: str):
        """Affiche l'onglet `key` et met en surbrillance son bouton de navigation."""
        for f in self.tabs.values():
            f.pack_forget()
        self.tabs[key].pack(fill="both", expand=True)
        for k, b in self.nav_btns.items():
            b.configure(fg_color=("gray75", "gray24") if k == key else "transparent")
        # Chargement PARESSEUX de « Mes vidéos » : on ne lit les vidéos que la
        # première fois qu'on ouvre l'onglet (ou si le propriétaire a changé
        # depuis), pour ne pas solliciter le serveur inutilement au démarrage.
        if key == "myvids":
            self._myvids_on_shown()

    # ═════════════════════════════════════════════════════════════════════
    #  ONGLET TÉLÉVERSEMENT
    # ═════════════════════════════════════════════════════════════════════

    def _build_tab_upload(self):
        """Construit l'onglet Téléversement (sélection, réglages communs, liste, lancement)."""
        frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.tabs["upload"] = frame

        ctk.CTkLabel(frame, text="📂  Téléversement par lot",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", pady=(0, 10))

        # — Barre de sélection —
        sel = ctk.CTkFrame(frame, fg_color="transparent")
        sel.pack(fill="x", pady=(0, 6))

        ctk.CTkButton(sel, text="➕  Ajouter des fichiers", width=190,
                      command=self._add_files, fg_color=C_NEUTRE, hover_color=C_NEUTRE_SURV, text_color=T_SUR_NEUTRE).pack(side="left", padx=(0, 8))
        ctk.CTkButton(sel, text="📁  Ajouter un dossier", width=190,
                      command=self._add_folder, fg_color=C_NEUTRE, hover_color=C_NEUTRE_SURV, text_color=T_SUR_NEUTRE).pack(side="left", padx=(0, 8))
        self.clear_btn = ctk.CTkButton(
            sel, text="🗑  Vider la liste", width=140,
            fg_color=C_NEUTRE, hover_color=C_NEUTRE_SURV,
            command=self._clear_items, text_color=T_SUR_NEUTRE)
        self.clear_btn.pack(side="left")

        # « Retirer les terminées » (repris de PodAdmin) — n'apparaît que s'il
        # y a de quoi retirer. Après un lot, les lignes envoyées n'ont plus
        # d'usage, mais il fallait les supprimer UNE PAR UNE ; « Vider la
        # liste » ne convient pas, car elle emporte aussi les échecs à relancer.
        self.purge_btn = ctk.CTkButton(
            sel, text="✅  Retirer les terminées", width=200,
            fg_color=C_NEUTRE, hover_color=C_NEUTRE_SURV,
            text_color=T_SUR_NEUTRE, command=self._retirer_terminees)
        # (pas de .pack ici : posé par `_maj_bouton_purge`)

        self.count_lbl = ctk.CTkLabel(sel, text="0 vidéo(s)", text_color=T_SECONDAIRE,
                                      font=ctk.CTkFont(size=11))
        self.count_lbl.pack(side="right")

        # — Réglages communs (appliqués à tout le lot) —
        common = ctk.CTkFrame(frame)
        common.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(common, text="Réglages communs au lot",
                     font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=4,
                                                           padx=12, pady=(10, 4), sticky="w")

        ctk.CTkLabel(common, text="Type :").grid(row=1, column=0, padx=(12, 4), pady=8, sticky="e")
        self.type_combo = ctk.CTkComboBox(common, values=["(chargement…)"], width=200, **STYLE_ZONE)
        self.type_combo.grid(row=1, column=1, padx=4, pady=8, sticky="w")

        # Discipline commune au lot — FACULTATIVE, à côté du Type (les deux
        # classements de la vidéo), comme dans PodAdmin.
        #
        # ⚠️ Ligne 1 et non ligne 2 : « Propriétaires additionnels » occupe
        # déjà la ligne 2, colonnes 2-3, et Tk superpose sans prévenir deux
        # widgets placés sur la même cellule.
        #
        # Facultative : l'imposer pousserait à choisir au hasard, ce qui donne
        # l'illusion d'un classement — pire que pas de classement.
        ctk.CTkLabel(common, text="Discipline :").grid(
            row=1, column=2, padx=(20, 4), pady=8, sticky="e")
        self.upload_discipline = ctk.CTkOptionMenu(
            common, width=200, values=[self.AUCUNE_DISCIPLINE], **STYLE_CHAMP)
        self.upload_discipline.set(self.AUCUNE_DISCIPLINE)
        self.upload_discipline.grid(row=1, column=3, padx=4, pady=8, sticky="w")

        ctk.CTkLabel(common, text="Visibilité :").grid(row=2, column=0, padx=(12, 4), pady=8, sticky="e")
        self.visibility_combo = ctk.CTkComboBox(
            common, width=200, values=["Brouillon / Privé", "Public"], **STYLE_ZONE)
        self.visibility_combo.set("Brouillon / Privé")
        self.visibility_combo.grid(row=2, column=1, padx=4, pady=8, sticky="w")

        self.encode_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(common, text="Lancer l'encodage après le téléversement",
                        variable=self.encode_var).grid(row=3, column=0, columnspan=2,
                                                        padx=12, pady=(0, 6), sticky="w")

        # — Propriétaire des vidéos (choix EXPLICITE et OBLIGATOIRE) —
        # On impose un choix explicite du compte propriétaire avant tout envoi :
        # plus aucune attribution « devinée » automatiquement. Tant qu'aucun
        # propriétaire n'est défini, l'envoi est bloqué (voir _start_upload).
        ctk.CTkLabel(common, text="Propriétaire des vidéos :",
                     font=ctk.CTkFont(weight="bold")).grid(
            row=4, column=0, columnspan=4, padx=12, pady=(6, 0), sticky="w")
        ctk.CTkButton(common, text="🎯  Saisir l'identifiant…", width=240,
                      command=self._choose_upload_owner, fg_color=C_NEUTRE, hover_color=C_NEUTRE_SURV, text_color=T_SUR_NEUTRE).grid(
            row=5, column=0, columnspan=2, padx=12, pady=(2, 10), sticky="w")
        self.owner_status_lbl = ctk.CTkLabel(common, text="⚠️ à définir avant l'envoi",
                                             text_color=T_ALERTE,
                                             font=ctk.CTkFont(size=12, weight="bold"))
        self.owner_status_lbl.grid(row=5, column=2, columnspan=2, padx=12, pady=(2, 10),
                                   sticky="w")

        # Propriétaires additionnels communs
        ctk.CTkButton(common, text="👥  Propriétaires additionnels…", width=240,
                      fg_color=C_NEUTRE, hover_color=C_NEUTRE_SURV,
                      command=self._edit_additional_owners, text_color=T_SUR_NEUTRE).grid(
            row=2, column=2, columnspan=2, padx=12, pady=(0, 6), sticky="w")
        self.add_owners_lbl = ctk.CTkLabel(common, text="aucun", text_color=T_SECONDAIRE,
                                           font=ctk.CTkFont(size=11))
        self.add_owners_lbl.grid(row=3, column=2, columnspan=2, padx=12, pady=(0, 8), sticky="w")

        common.columnconfigure(3, weight=1)

        # — Tableau des vidéos (titres éditables) —
        hint = ("Vérifiez / corrigez les titres avant l'envoi  —  "
                "💡 vous pouvez aussi glisser-déposer fichiers et dossiers ci-dessous :"
                if getattr(self, "dnd_ok", False) else
                "Vérifiez / corrigez les titres avant l'envoi :")
        ctk.CTkLabel(frame, text=hint, font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(2, 2))

        self.list_frame = ctk.CTkScrollableFrame(frame, height=240)
        self.list_frame.pack(fill="both", expand=True)

        # Activer le glisser-déposer sur la zone de liste
        if getattr(self, "dnd_ok", False):
            try:
                self.list_frame.drop_target_register(DND_FILES)
                self.list_frame.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:
                pass

        empty_text = ("Aucune vidéo.\nGlissez-déposez ici des fichiers ou des dossiers,\n"
                      "ou utilisez les boutons ci-dessus."
                      if getattr(self, "dnd_ok", False) else
                      "Aucune vidéo.\nUtilisez « Ajouter des fichiers » ou « Ajouter un dossier ».")
        self._empty_hint = ctk.CTkLabel(self.list_frame, text=empty_text, text_color=T_SECONDAIRE)
        self._empty_hint.pack(pady=40)

        # — Lancement + progression —
        launch = ctk.CTkFrame(frame, fg_color="transparent")
        launch.pack(fill="x", pady=(8, 0))

        self.launch_btn = ctk.CTkButton(
            launch, text="🚀  Lancer le téléversement", height=40,
            fg_color=C_SUCCES, hover_color=C_SUCCES_SURV,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._start_upload)
        self.launch_btn.pack(side="left")

        # Bouton « Relancer les échecs » : créé maintenant mais NON affiché
        # (pack_forget). Il n'apparaît qu'après un lot comportant des échecs,
        # et re-tente uniquement les vidéos en échec (voir _on_batch_done).
        self.retry_btn = ctk.CTkButton(
            launch, text="🔄  Relancer les échecs", height=40,
            fg_color=C_ALERTE, hover_color=C_ALERTE_SURV,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._retry_failed)
        self.retry_btn.pack(side="left", padx=(8, 0))
        self.retry_btn.pack_forget()   # masqué par défaut

        # Arrêt du lot en cours. Affiché UNIQUEMENT pendant un lot : présent au
        # repos, il laisserait croire qu'il y a quelque chose à arrêter. Gris et
        # non rouge : il n'efface rien, il arrête.
        self.upload_stop_btn = ctk.CTkButton(
            launch, text="🛑  Interrompre", height=40,
            fg_color=C_NEUTRE, hover_color=C_NEUTRE_SURV, text_color=T_SUR_NEUTRE,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._depot_interrompre)

        self.global_msg = ctk.CTkLabel(launch, text="", text_color=T_SECONDAIRE,
                                       font=ctk.CTkFont(size=12))
        self.global_msg.pack(side="left", padx=14)

        # — Progression (fichier courant + lot) —
        #
        # Les deux barres restaient affichées en permanence, à zéro : deux
        # traits inertes qui n'informaient de rien tant qu'aucun envoi n'était
        # en cours. Elles sont CRÉÉES ici mais pas placées :
        # `_afficher_progression()` les montre au lancement d'un lot,
        # `_masquer_progression()` les retire à la fin (repris de PodAdmin).
        #
        # ⚠️ Widgets créés une fois pour toutes et masqués par `pack_forget`,
        # sans cadre conteneur vide : un CTkFrame vide transparent se dessine
        # en CARRÉ NOIR sur macOS (ennui déjà rencontré dans PodAdmin).
        self.file_progress = ctk.CTkProgressBar(frame)
        self.file_progress.set(0)
        self.file_progress_lbl = ctk.CTkLabel(frame, text="", text_color=T_SECONDAIRE,
                                              font=ctk.CTkFont(size=10))
        self.batch_progress = ctk.CTkProgressBar(frame, progress_color=C_SUCCES)
        self.batch_progress.set(0)
        self.progression_visible = False

        # État initial du propriétaire (reflète un éventuel compte déjà enregistré).
        self._refresh_owner_status()

    def _depot_debut(self):
        """Arme l'arrêt, affiche le bouton 🛑 et verrouille les retraits."""
        self.depot_interrompu.clear()
        self.depot_en_cours = True
        self._maj_verrou_liste()
        self.upload_stop_btn.configure(state="normal", text="🛑  Interrompre")
        if not self.upload_stop_btn.winfo_ismapped():
            self.upload_stop_btn.pack(side="left", padx=(8, 0), before=self.global_msg)

    def _maj_verrou_liste(self):
        """Grise (lot en cours) ou réactive tout ce qui RETIRE des lignes.
        L'ajout reste permis : un fichier ajouté pendant un lot part à la
        suite, dans le même lot."""
        etat = "disabled" if self.depot_en_cours else "normal"
        boutons = [getattr(self, "clear_btn", None), getattr(self, "purge_btn", None)]
        boutons += [it.btn_retirer for it in self.items]
        for b in boutons:
            if b is None:
                continue
            try:
                b.configure(state=etat)
            except Exception:
                pass      # ligne détruite entre-temps (liste reconstruite)

    def _depot_interrompre(self):
        """Demande l'arrêt du lot de téléversement.

        L'arrêt porte AUSSI sur la vidéo en cours : un gros fichier peut
        prendre une demi-heure, et l'attente après un 504 jusqu'à 30 min.
        L'envoi s'arrête au bloc suivant ; rien n'est laissé à moitié côté
        serveur, puisque la vidéo n'est créée qu'une fois le fichier complet."""
        self.depot_interrompu.set()
        self.upload_stop_btn.configure(state="disabled", text="⏳  Arrêt en cours…")
        self.global_msg.configure(text="Arrêt demandé…", text_color=T_ALERTE)
        self._log("🛑 Arrêt du téléversement demandé.")

    def _depot_fin(self):
        """Retire le bouton 🛑 et déverrouille les retraits à la fin du lot."""
        self.depot_en_cours = False
        self._maj_verrou_liste()
        if self.upload_stop_btn.winfo_ismapped():
            self.upload_stop_btn.pack_forget()

    def _afficher_progression(self):
        """Fait apparaître les deux barres de progression (début d'un lot).
        Idempotente : appelée deux fois, elle ne place rien en double."""
        if self.progression_visible:
            return
        self.file_progress.pack(fill="x", pady=(8, 0))
        self.file_progress_lbl.pack(anchor="w")
        self.batch_progress.pack(fill="x", pady=(4, 0))
        self.progression_visible = True

    def _masquer_progression(self):
        """Retire les barres de l'affichage (fin d'un lot). Elles ne sont pas
        détruites : elles resserviront au lot suivant."""
        if not self.progression_visible:
            return
        self.batch_progress.pack_forget()
        self.file_progress_lbl.pack_forget()
        self.file_progress.pack_forget()
        self.progression_visible = False

    # ── Ajout de fichiers / dossier ──────────────────────────────────────

    def _add_files(self):
        """Ouvre un sélecteur de fichiers et ajoute les vidéos choisies à la file."""
        paths = filedialog.askopenfilenames(
            title="Choisir des vidéos",
            filetypes=[("Vidéos", "*.mp4 *.mov *.avi *.mkv *.webm *.m4v *.wmv *.flv *.mpg *.mpeg"),
                       ("Tous les fichiers", "*.*")])
        self._add_paths(paths)

    def _add_folder(self):
        """Scanne un dossier (récursivement) et ajoute toutes les vidéos trouvées à la file."""
        folder = filedialog.askdirectory(title="Choisir un dossier de vidéos")
        if not folder:
            return
        found = []
        for root, _dirs, files in os.walk(folder):
            for name in files:
                if os.path.splitext(name)[1].lower() in cfg.VIDEO_EXTENSIONS:
                    found.append(os.path.join(root, name))
        if not found:
            self.global_msg.configure(text="Aucune vidéo trouvée dans ce dossier.",
                                      text_color=T_ALERTE)
            return
        self._add_paths(sorted(found))

    def _on_drop(self, event):
        """Glisser-déposer : ajoute les vidéos des fichiers/dossiers déposés."""
        try:
            paths = self.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        found = []
        for p in paths:
            p = p.strip().strip("{}")
            if not p:
                continue
            if os.path.isdir(p):
                for root, _d, names in os.walk(p):
                    for n in names:
                        if os.path.splitext(n)[1].lower() in cfg.VIDEO_EXTENSIONS:
                            found.append(os.path.join(root, n))
            elif os.path.isfile(p) and os.path.splitext(p)[1].lower() in cfg.VIDEO_EXTENSIONS:
                found.append(p)
        if found:
            self._show_tab("upload")
            # Le bilan (ajoutées, déjà présentes…) est affiché par _add_paths :
            # l'ancien message annonçait `len(found)` ajoutées, même quand
            # aucune ne l'était.
            self._add_paths(sorted(found))
        else:
            self.global_msg.configure(
                text="Aucune vidéo reconnue dans les éléments déposés.", text_color=T_ALERTE)

    @staticmethod
    def _cle_fichier(path: str) -> str:
        """Forme comparable d'un chemin : sous Windows, le sélecteur de fichiers
        et le glisser-déposer ne l'écrivent pas pareil (« C:/x » / « C:\\x »,
        casse). Comparés tels quels, le même fichier entrait deux fois."""
        return os.path.normcase(os.path.abspath(path))

    def _add_paths(self, paths) -> int:
        """Ajoute des chemins à la file, puis dit EXACTEMENT ce qui s'est passé.
        (Repris de PodAdmin 1.9.2.)

        Trois cas écartés ou signalés :
          • déjà dans la liste → ignoré (on le dit, au lieu de ne rien faire) ;
          • déjà envoyé depuis l'ouverture de l'application, puis retiré de la
            liste → confirmation : le renvoyer créerait un second exemplaire
            sur Pod. Cette mémoire est celle de la SESSION ;
          • ajout pendant un lot → permis : le fichier part à la suite.
        Renvoie le nombre de vidéos réellement ajoutées."""
        if not paths:
            return 0      # sélecteur annulé : rien à dire
        dans_liste = {self._cle_fichier(it.path) for it in self.items}
        nouveaux, deja_liste, deja_envoyes = [], 0, []
        for p in paths:
            cle = self._cle_fichier(p)
            if cle in dans_liste:
                deja_liste += 1
                continue
            dans_liste.add(cle)          # éviter les doublons dans un même ajout
            if cle in self.deposes_session:
                deja_envoyes.append(p)
            else:
                nouveaux.append(p)

        refuses = 0
        if deja_envoyes:
            lignes = []
            for p in deja_envoyes[:5]:
                heure, slug = self.deposes_session[self._cle_fichier(p)]
                lignes.append(f"  • {os.path.basename(p)} — {heure}"
                              + (f" ({slug})" if slug else ""))
            if len(deja_envoyes) > 5:
                lignes.append(f"  • … et {len(deja_envoyes) - 5} autre(s)")
            if messagebox.askyesno(
                    "Fichier déjà envoyé",
                    f"{len(deja_envoyes)} fichier(s) ont déjà été envoyés depuis "
                    "l'ouverture de Pod Téléverseur :\n\n" + "\n".join(lignes) +
                    "\n\nLes renvoyer créera un SECOND exemplaire sur Pod.\n"
                    "Les ajouter quand même ?"):
                nouveaux += deja_envoyes
            else:
                refuses = len(deja_envoyes)

        for p in nouveaux:
            self.items.append(UploadItem(p))
        if nouveaux:
            self._refresh_list()
            self._log(f"{len(nouveaux)} vidéo(s) ajoutée(s) à la file.")

        bilan = f"{len(nouveaux)} vidéo(s) ajoutée(s)"
        if self.depot_en_cours and nouveaux:
            bilan += " — elles partiront à la suite du lot en cours"
        if deja_liste:
            bilan += f", {deja_liste} déjà dans la liste"
        if refuses:
            bilan += f", {refuses} déjà envoyée(s) non ajoutée(s)"
        self.global_msg.configure(text=bilan + ".",
                                  text_color=T_SUCCES if nouveaux else T_ALERTE)
        return len(nouveaux)

    def _clear_items(self):
        """Vide la file d'attente et rafraîchit l'affichage."""
        if self.depot_en_cours:
            return      # vider en plein lot arrêtait l'envoi et effaçait sa ligne
        self.items.clear()
        self._refresh_list()

    def _retirer_terminees(self):
        """Retire les vidéos ENVOYÉES, et elles seules. Les échecs restent :
        ce sont eux qu'on voudra relancer, et les perdre obligerait à
        re-sélectionner les fichiers un par un."""
        if self.depot_en_cours:
            return
        avant = len(self.items)
        self.items = [it for it in self.items if not it.done]
        retirees = avant - len(self.items)
        if retirees:
            self._refresh_list()
            self._log(f"{retirees} vidéo(s) envoyée(s) retirée(s) de la liste.")

    def _maj_bouton_purge(self):
        """Affiche « Retirer les N terminées » avec leur nombre, ou le cache.
        Le nombre compte : il dit exactement ce qui va disparaître."""
        bouton = getattr(self, "purge_btn", None)
        if bouton is None:
            return
        n = sum(1 for it in self.items if it.done)
        if n:
            bouton.configure(text=f"✅  Retirer les {n} terminée{'s' if n > 1 else ''}")
            if not bouton.winfo_ismapped():
                bouton.pack(side="left", padx=(8, 0))
        elif bouton.winfo_ismapped():
            bouton.pack_forget()

    def _echecs_a_relancer(self) -> list:
        """Vidéos en ÉCHEC, qu'on peut renvoyer sans risque de doublon.

        On se fonde sur l'état (booléen `done`, `slug`, `error`), jamais sur
        le libellé affiché. Sont exclues :
          • les vidéos déposées (`done`) ;
          • celles qui EXISTENT déjà sur le serveur sans avoir été
            réattribuées (`slug` connu) : les renvoyer créerait une SECONDE
            vidéo, la première restant au nom du compte DEPOT ;
          • celles jamais tentées (pas d'erreur) : ce ne sont pas des échecs ;
          • celles « à vérifier » (attente d'un 504 interrompue) : elles ont
            une erreur mais pas de slug, et existent PEUT-ÊTRE sur Pod ; les
            renvoyer risquerait un doublon."""
        return [it for it in self.items
                if not it.done and not it.slug and not it.a_verifier and it.error]

    def _update_retry_button(self):
        """Affiche « Relancer les échecs (N) » s'il y a des échecs, sinon le masque."""
        n = len(self._echecs_a_relancer())
        if n:
            self.retry_btn.configure(text=f"🔄  Relancer les échecs ({n})")
            if not self.retry_btn.winfo_ismapped():
                self.retry_btn.pack(side="left", padx=(8, 0), before=self.global_msg)
        elif self.retry_btn.winfo_ismapped():
            self.retry_btn.pack_forget()

    def _refresh_list(self):
        """Reconstruit le tableau des vidéos en attente (nom, titre éditable, état)."""
        for w in self.list_frame.winfo_children():
            w.destroy()

        if not self.items:
            empty_text = ("Aucune vidéo.\nGlissez-déposez ici des fichiers ou des dossiers,\n"
                          "ou utilisez les boutons ci-dessus."
                          if getattr(self, "dnd_ok", False) else
                          "Aucune vidéo.\nUtilisez « Ajouter des fichiers » ou « Ajouter un dossier ».")
            ctk.CTkLabel(self.list_frame, text=empty_text, text_color=T_SECONDAIRE).pack(pady=40)
            self.count_lbl.configure(text="0 vidéo(s)")
            self._maj_bouton_purge()
            return

        # En-tête
        hdr = ctk.CTkFrame(self.list_frame, fg_color=S_LIGNE, corner_radius=4)
        hdr.pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(hdr, text="Fichier", width=230, anchor="w",
                     font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=8, pady=4)
        ctk.CTkLabel(hdr, text="Titre (éditable)", anchor="w",
                     font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=8, expand=True, fill="x")
        ctk.CTkLabel(hdr, text="État", width=110,
                     font=ctk.CTkFont(size=11, weight="bold")).pack(side="right", padx=8)

        for i, it in enumerate(self.items):
            row = ctk.CTkFrame(self.list_frame,
                               fg_color=S_CARTE if i % 2 == 0 else "gray14", corner_radius=4)
            row.pack(fill="x", pady=1)
            it.row = row

            # nom de fichier (tronqué) + taille
            try:
                size_mb = os.path.getsize(it.path) / (1024 * 1024)
                size_txt = f"{size_mb:.0f} Mo"
            except OSError:
                size_txt = "?"
            fname = it.filename if len(it.filename) <= 28 else it.filename[:25] + "…"
            ctk.CTkLabel(row, text=f"{fname}\n{size_txt}", width=230, anchor="w",
                         justify="left", font=ctk.CTkFont(size=11)).pack(side="left", padx=8, pady=4)

            # titre éditable
            it.title_var = ctk.StringVar(value=it.title)
            it.title_var.trace_add("write",
                                    lambda *_x, item=it: setattr(item, "title", item.title_var.get()))
            ctk.CTkEntry(row, textvariable=it.title_var).pack(
                side="left", padx=8, pady=6, expand=True, fill="x")

            # bouton supprimer (grisé pendant un lot : voir `depot_en_cours`)
            it.btn_retirer = ctk.CTkButton(
                row, text="✕", width=28, height=26,
                fg_color=C_NEUTRE, hover_color=C_DESTR_SURV,
                command=lambda item=it: self._remove_item(item), text_color=T_SUR_NEUTRE)
            it.btn_retirer.pack(side="right", padx=4)

            # état
            it.status_lbl = ctk.CTkLabel(row, text=it.status, width=100,
                                         text_color=T_DISCRET, font=ctk.CTkFont(size=11))
            it.status_lbl.pack(side="right", padx=6)

        self.count_lbl.configure(text=f"{len(self.items)} vidéo(s)")
        self._maj_bouton_purge()
        self._maj_verrou_liste()

    def _remove_item(self, item: UploadItem):
        """Retire une vidéo de la file et rafraîchit l'affichage."""
        if self.depot_en_cours:
            return      # bouton grisé ; garde-fou si l'appel vient d'ailleurs
        if item in self.items:
            self.items.remove(item)
            self._refresh_list()

    def _set_item_status(self, item: UploadItem, status: str, color=T_DISCRET):
        """Met à jour le libellé d'état d'une vidéo dans la liste."""
        item.status = status
        if item.status_lbl:
            item.status_lbl.configure(text=status, text_color=color)

    # ── Propriétaires additionnels communs ───────────────────────────────

    def _edit_additional_owners(self):
        """Ouvre OwnerPicker pour choisir les co-propriétaires communs au lot."""
        if not self.api:
            self.global_msg.configure(text="Connectez-vous d'abord (onglet Configuration).",
                                      text_color=T_ALERTE)
            return
        OwnerPicker(self, on_done=self._on_owners_picked,
                    preselected=dict(self.additional_owner_map))

    def _on_owners_picked(self, urls: list[str], labels: list[str]):
        """Callback d'OwnerPicker : mémorise les co-propriétaires choisis et met à jour le libellé."""
        self.additional_owner_urls = urls
        self.additional_owner_map = dict(zip(urls, labels))
        if urls:
            self.add_owners_lbl.configure(text=", ".join(labels)[:60], text_color=T_SUCCES)
        else:
            self.add_owners_lbl.configure(text="aucun", text_color=T_SECONDAIRE)

    # ── Propriétaire des vidéos (choix explicite et obligatoire) ─────────

    def _refresh_owner_status(self):
        """Met à jour le libellé d'état du propriétaire dans l'onglet Téléversement.

        • Aucun propriétaire défini  → « ⚠️ à définir avant l'envoi » (orange).
        • Propriétaire défini         → « ✅ [nom] » (vert).
        Appelée à la construction de l'onglet et à chaque changement de compte
        (choix manuel, présélection, détection automatique)."""
        if not hasattr(self, "owner_status_lbl"):
            return
        owner_url = self.config_data.get("agent_owner_url", "")
        name = self.config_data.get("agent_username", "")
        if owner_url:
            self.owner_status_lbl.configure(text=f"✅ {name or 'défini'}",
                                            text_color=T_SUCCES)
        else:
            self.owner_status_lbl.configure(text="⚠️ à définir avant l'envoi",
                                            text_color=T_ALERTE)
        # RAFRAÎCHISSEMENT AUTOMATIQUE de l'onglet « Mes vidéos » : comme TOUT
        # changement de propriétaire (choix manuel, présélection, détection auto,
        # onglet Configuration) passe par ce point unique, c'est ici qu'on
        # prévient l'onglet que le compte affiché a peut-être changé.
        self._myvids_notify_owner_changed()

    # Le propriétaire n'est plus choisi dans l'annuaire de TOUS les comptes :
    # l'enseignant SAISIT son identifiant universitaire (ex. abc1234a). La
    # sonde verifier_identifiant.py (26/09/2026) a montré qu'un jeton
    # d'enseignant voit tout l'annuaire : la liste permettait de choisir
    # n'importe quel collègue — ou un compte local d'administration comme
    # DEPOT —, et c'est ce choix qui décide des vidéos que « Mes vidéos »
    # permet de modifier. La saisie garde l'enseignant de l'erreur ; elle ne
    # remplace pas les droits du jeton, qui se règlent côté Pod.
    #
    # Les CO-propriétaires, eux, restent choisis dans la liste complète :
    # chacun peut associer qui il veut à ses vidéos (OwnerPicker).

    @staticmethod
    def _normaliser_identifiant(texte) -> str:
        """Identifiant tel qu'on le compare : sans espaces, en minuscules."""
        return str(texte or "").strip().lower()

    @staticmethod
    def _identifiant_valide(texte) -> bool:
        """Vrai si `texte` a la forme d'un identifiant universitaire.
        Écarte d'office les comptes locaux d'administration (DEPOT…)."""
        return bool(re.match(cfg.IDENTIFIANT_FORMAT, App._normaliser_identifiant(texte)))

    def _resoudre_identifiant(self, texte):
        """(Thread) Retrouve le compte Pod d'un identifiant universitaire.

        Renvoie (compte, "") si le compte existe, sinon (None, message à
        afficher). Aucune approximation : format strict, puis égalité exacte
        du nom d'utilisateur (voir PodAPI.find_user_by_username)."""
        ident = self._normaliser_identifiant(texte)
        if not self._identifiant_valide(ident):
            return None, (f"« {ident or '(vide)'} » n'est pas un identifiant universitaire : "
                          "3 lettres, 4 chiffres et 1 lettre (ex. abc1234d).")
        if not self.api:
            return None, "Connectez-vous d'abord (onglet Configuration)."
        try:
            compte = self.api.find_user_by_username(ident)
        except Exception as e:
            return None, f"Recherche impossible : {message_utilisateur(e)}"
        if not compte or not compte.get("url"):
            return None, (f"Aucun compte de la plateforme ne porte l'identifiant « {ident} ». "
                          "Vérifiez-le ; s'il est exact, contactez le support.")
        return compte, ""

    def _demander_identifiant(self, intro: str, apres=None):
        """Ouvre la fenêtre de saisie de l'identifiant ; `apres()` est appelée
        une fois le propriétaire enregistré."""
        if not self.api:
            self.global_msg.configure(text="Connectez-vous d'abord (onglet Configuration).",
                                      text_color=T_ALERTE)
            self._show_tab("config")
            return

        def trouve(compte):
            self._pick_agent(compte)
            self._log(f"Propriétaire des vidéos défini : {compte.get('username', '')}.")
            if apres:
                apres()

        actuel = self.config_data.get("agent_username", "")
        IdentifiantDialog(self, on_found=trouve, intro=intro,
                          prefill=actuel if self._identifiant_valide(actuel) else "")

    def _choose_upload_owner(self):
        """Demande l'identifiant universitaire du PROPRIÉTAIRE des vidéos."""
        self._demander_identifiant(
            "Au nom de quel compte les vidéos seront-elles déposées ?\n"
            "Saisissez VOTRE identifiant universitaire (ex. abc1234d).\n"
            "Ce choix s'applique à tout le lot.")

    # ── Lancement du téléversement ───────────────────────────────────────

    def _start_upload(self):
        """Vérifie les prérequis (connexion, propriétaire OBLIGATOIRE, type) puis lance le lot."""
        if not self.api:
            self.global_msg.configure(text="Non connecté. Voir l'onglet Configuration.",
                                      text_color=T_ERREUR)
            return
        if not self.items:
            self.global_msg.configure(text="Aucune vidéo à téléverser.", text_color=T_ALERTE)
            return

        # BLOCAGE VOLONTAIRE : aucun envoi tant que le propriétaire n'est pas
        # explicitement choisi. On prévient clairement et on ouvre le sélecteur,
        # sans rien envoyer (évite tout dépôt au mauvais nom).
        owner_url = self.config_data.get("agent_owner_url", "")
        if not owner_url:
            self.global_msg.configure(
                text="⚠️ Choisissez d'abord le propriétaire des vidéos.",
                text_color=T_ALERTE)
            self._choose_upload_owner()
            return
        # Un propriétaire enregistré AVANT la saisie par identifiant peut être
        # un compte local (DEPOT, compte d'admin…) : on exige un identifiant
        # universitaire, sans quoi on redemande. Pas d'envoi en attendant.
        if not self._identifiant_valide(self.config_data.get("agent_username", "")):
            self.global_msg.configure(
                text="⚠️ Le propriétaire enregistré n'est pas un identifiant universitaire : "
                     "saisissez le vôtre.",
                text_color=T_ALERTE)
            self._choose_upload_owner()
            return

        type_title = self.type_combo.get()
        type_url = self.type_map.get(type_title, "")
        if not type_url:
            self.global_msg.configure(text="Sélectionnez un type valide.", text_color=T_ALERTE)
            return

        # Mémorise propriétaire et type pour une éventuelle relance des échecs.
        self._last_owner_url = owner_url
        self._last_type_url = type_url
        # Discipline lue ICI, dans le thread principal (lire un widget Tk depuis
        # un thread n'est pas fiable), et mémorisée : sans cela, une vidéo
        # relancée après échec perdrait son classement.
        self._last_discipline_url = self._discipline_choisie()

        self.launch_btn.configure(state="disabled")
        self.retry_btn.configure(state="disabled")
        self._afficher_progression()
        self.batch_progress.set(0)
        self._last_is_draft = self.visibility_combo.get().startswith("Brouillon")
        self._last_do_encode = bool(self.encode_var.get())
        self._depot_debut()
        self._run(self._do_batch_upload, owner_url, type_url, self._last_discipline_url,
                  self._last_is_draft, self._last_do_encode)

    @staticmethod
    def _file_size(path: str) -> int:
        """Taille d'un fichier en octets (0 si illisible)."""
        try:
            return os.path.getsize(path)
        except Exception:
            return 0

    @staticmethod
    def _nouveau_marqueur() -> str:
        """Marqueur unique d'un envoi par morceaux : « upid » + 8 hexadécimaux.
        Il remplace la recherche par NOM DE FICHIER, qui confondait deux dépôts
        simultanés de même nom sur le compte DEPOT partagé (et pouvait donc
        attribuer la vidéo d'un enseignant à un autre)."""
        return "upid" + uuid.uuid4().hex[:8]

    def _verify_chunked_creation(self, marqueur: str, creator_owner_url: str,
                                 annuler=None, delai_s=None):
        """(Thread) Après un 504 à la finalisation, Pod termine la création côté
        serveur. On sonde l'API sur le MARQUEUR de cet envoi jusqu'à voir la
        vidéo, créée par le VÉHICULE. Renvoie le dict vidéo, ou None après
        expiration de la fenêtre de vérification.

        Garde-fou : si PLUSIEURS vidéos portent le marqueur, on ne sait plus
        laquelle est la bonne. Plutôt que d'en réattribuer une au hasard (et
        risquer de donner la vidéo d'un collègue), on lève PodChunkedError :
        aucune réattribution, échec franc et alerte dans le Journal.

        `annuler()` est consulté en continu (la pause de sondage est découpée) :
        l'attente s'arrête en moins d'une seconde. La vidéo a pu être créée
        malgré tout : l'annulation porte donc `a_verifier` et le marqueur à
        rechercher, pour qu'on la retrouve au lieu de la renvoyer en double.

        `delai_s` : durée maximale d'attente (défaut : CHUNK_VERIFY_TIMEOUT_S,
        30 min — le cas du 504 ; voir CHUNK_VERIFY_TIMEOUT_502_S pour le 502)."""
        import time as _t
        m = marqueur.lower()
        deadline = _t.time() + (delai_s if delai_s is not None
                                else cfg.CHUNK_VERIFY_TIMEOUT_S)

        def verifier_arret():
            if annuler and annuler():
                raise EnvoiAnnule(
                    "Attente interrompue : la vidéo a peut-être été créée au nom du "
                    f"compte DEPOT. Le support peut la retrouver sur Pod en cherchant "
                    f"« {marqueur} » ; ne la renvoyez pas avant cette vérification.",
                    a_verifier=True)

        while _t.time() < deadline:
            verifier_arret()
            try:
                cands = self.api.search_videos({"search": marqueur, "limit": 25})
            except Exception:
                cands = []
            retenues = []
            for v in cands:
                # La recherche Pod porte sur plusieurs champs : on exige que le
                # marqueur figure bien dans le titre ou le slug (issus du nom
                # de fichier transmis), pas seulement « quelque part ».
                if m not in f"{v.get('title', '')} {v.get('slug', '')}".lower():
                    continue
                own = v.get("owner")
                own_str = own if isinstance(own, str) else (
                    own.get("url", "") if isinstance(own, dict) else "")
                # Égalité STRICTE (comme PodAdmin) : une inclusion de chaîne
                # confondait `/users/1` et `/users/18`, et laissait passer une
                # vidéo sans propriétaire connu.
                vehicule = str(creator_owner_url or "").rstrip("/")
                if vehicule and str(own_str).rstrip("/") != vehicule:
                    continue
                retenues.append(v)
            if len(retenues) > 1:
                slugs = ", ".join(str(v.get("slug")) for v in retenues)
                self._ui(self._log,
                         f"⚠️⚠️ {len(retenues)} vidéos portent le marqueur {marqueur} "
                         f"({slugs}) : AUCUNE n'est réattribuée. Elles restent au "
                         f"nom du compte DEPOT — à vérifier côté web.")
                raise PodChunkedError(
                    f"Plusieurs vidéos portent le marqueur {marqueur} : "
                    f"réattribution annulée par prudence ({slugs}).")
            if retenues:
                v = retenues[0]
                self._ui(self._log, f"✓ Vidéo apparue après finalisation serveur : {v.get('slug')}")
                return v
            remaining = max(0, int(deadline - _t.time()))
            self._ui(self.global_msg.configure,
                     text=f"⏳ Finalisation côté serveur (gros fichier)… vérification, "
                          f"{remaining//60} min {remaining%60}s restantes",
                     text_color=T_ALERTE)
            fin_pause = _t.time() + cfg.CHUNK_VERIFY_INTERVAL_S
            while _t.time() < fin_pause:
                verifier_arret()
                _t.sleep(min(0.25, max(0.0, fin_pause - _t.time())))
        return None

    @staticmethod
    def _est_coupure_reseau(err: Exception) -> bool:
        """Cette erreur vient-elle d'une coupure de connexion (et non d'un refus
        du serveur) ? Repris de PodAdmin.

        On ne replie sur l'envoi par morceaux QUE dans ce cas. Un refus métier
        (400 champ manquant, 403 droits insuffisants…) échouerait de la même
        façon par morceaux : le rejouer ferait perdre du temps et risquerait
        de créer un doublon.

        Signature typique de la coupure par la passerelle :
        « SSLEOFError: EOF occurred in violation of protocol »."""
        texte = f"{getattr(err, 'body', '')} {err}".lower()
        indices = ("sslerror", "ssleoferror", "eof occurred",
                   "connection aborted", "connection reset",
                   "max retries exceeded", "connectionerror",
                   "remotedisconnected", "broken pipe")
        # `status` vaut 0 quand aucune réponse HTTP n'a été reçue (vraie coupure).
        sans_reponse = getattr(err, "status", 0) in (0, 502, 503, 504)
        return sans_reponse and any(i in texte for i in indices)

    def _deposer_par_morceaux(self, chunked, it, progress, on_retry, annuler=None):
        """(Thread) Envoie une vidéo NEUVE par morceaux, avec reprise après 504.

        Point de passage UNIQUE de toute création par morceaux (gros fichier,
        et repli après coupure de l'envoi direct). Dans PodAdmin, ces deux
        chemins avaient chacun leur copie de la reprise, et l'une appelait
        `_verify_chunked_creation` avec de mauvais arguments : un 504 dans le
        repli levait une TypeError, la vidéo — peut-être bien créée — était
        affichée en échec, et « Relancer les échecs » en créait une seconde.

        Renvoie (slug, vidéo) ; la vidéo vaut None si l'API ne la retrouve pas.
        `annuler()` : arrêt demandé par l'utilisateur (voir `_depot_interrompre`)."""
        # Marqueur NEUF pour chaque envoi (une relance en génère un autre) :
        # aucune vidéo existante ne peut le porter, d'où plus besoin de relever
        # au préalable les ids déjà présents.
        marqueur = self._nouveau_marqueur()
        video = None
        try:
            slug = chunked.upload_video_chunked(
                it.path, chunk_size=cfg.CHUNK_SIZE_BYTES,
                progress_cb=progress, retry_cb=on_retry, marqueur=marqueur,
                annuler=annuler)
        except PodChunkedError as ce:
            # La passerelle a coupé la finalisation : Pod termine côté serveur.
            # On attend que la vidéo apparaisse plutôt que de conclure à l'échec.
            if ce.status not in (502, 503, 504):
                raise
            # 504 : la passerelle a cessé d'attendre, Pod continue → attente
            # longue. 502/503 : Pod a échoué ou n'a rien traité → attente courte
            # (voir CHUNK_VERIFY_TIMEOUT_502_S dans config.py).
            delai = (cfg.CHUNK_VERIFY_TIMEOUT_S if ce.status == 504
                     else cfg.CHUNK_VERIFY_TIMEOUT_502_S)
            # Le marqueur est ÉCRIT au Journal : si l'attente échoue ou si
            # l'application est fermée entre-temps, c'est le seul moyen de
            # retrouver la vidéo côté web.
            self._ui(self._log,
                     f"⏳ {it.title} : finalisation coupée (HTTP {ce.status}) — "
                     f"vérification pendant {delai // 60} min au plus "
                     f"(repère à rechercher si besoin : {marqueur})…")
            self._ui(self._set_item_status, it, "⏳ finalisation serveur", T_ALERTE)
            video = self._verify_chunked_creation(marqueur, self.vehicle_owner_url,
                                                  annuler, delai_s=delai)
            if not video:
                self._ui(self._log,
                         f"❌ {it.title} : aucune vidéo apparue en {delai // 60} min "
                         f"après le HTTP {ce.status}. Faites vérifier sur Pod (repère "
                         f"{marqueur}) avant de relancer.")
                raise
            slug = video.get("slug", "")
        if video is None:
            video = self.api.get_video_by_slug(slug)
        return slug, video

    def _do_batch_upload(self, owner_url: str, type_url: str, discipline_url: str = "",
                         is_draft: bool = True, do_encode: bool = True):
        """(Thread) Téléverse chaque vidéo, ajoute les crédits, lance l'encodage, suit la progression.

        Les vidéos déjà réussies (it.done) sont ignorées : cette méthode sert
        aussi bien au 1ᵉʳ envoi qu'à la RELANCE des seuls échecs."""
        # `is_draft` et `do_encode` arrivent en ARGUMENTS, lus dans le thread
        # principal par l'appelant : lire un widget Tk depuis ce thread de
        # travail provoque des plantages aléatoires (« main thread is not in
        # main loop ») — défaut déjà corrigé dans PodAdmin.
        total = len(self.items)
        ok = 0
        chunked = None      # session véhicule DEPOT, ouverte à la 1re nécessité
        # Consulté par les envois eux-mêmes (bloc par bloc, morceau par
        # morceau, pendant l'attente après un 504) : voir `_depot_interrompre`.
        annuler = self.depot_interrompu.is_set
        interrompu = False

        # Boucle par INDEX sur la liste VIVANTE : un fichier ajouté pendant le
        # lot (permis, voir `_add_paths`) part à la suite, et le total affiché
        # le suit. Les RETRAITS sont bloqués pendant le lot (`depot_en_cours`) :
        # un retrait décalait les index et faisait sauter la vidéo suivante.
        idx = 0
        while idx < len(self.items):
            it = self.items[idx]
            idx += 1
            total = len(self.items)
            if annuler():
                interrompu = True
                break
            # On saute les vidéos déjà téléversées avec succès (utile en relance).
            if it.done:
                ok += 1
                self._ui(self.batch_progress.set, idx / total)
                continue
            # Vidéo CRÉÉE mais non réattribuée (slug connu, pas « done ») :
            # la renvoyer en créerait une seconde, la première restant au nom
            # du compte DEPOT. Elle se règle à la main, pas par un renvoi.
            if it.slug:
                self._ui(self.batch_progress.set, idx / total)
                continue
            # …et celles dont l'existence est incertaine (attente d'un 504
            # interrompue) : les renvoyer risquerait un doublon.
            if it.a_verifier:
                self._ui(self.batch_progress.set, idx / total)
                continue

            self._ui(self._set_item_status, it, "en cours", T_SECONDAIRE)
            self._ui(self.file_progress.set, 0)
            self._ui(self.global_msg.configure,
                     text=f"Téléversement {idx}/{total} : {it.title}", text_color=T_SECONDAIRE)

            def progress(sent, tot, item=it):
                # Callback de progression : met à jour la barre du fichier en cours
                # (fraction envoyée) et le libellé « Mo envoyés / Mo total ».
                frac = sent / tot if tot else 0
                self._ui(self.file_progress.set, frac)
                self._ui(self.file_progress_lbl.configure,
                         text=f"{item.filename} — {sent/1024/1024:.0f} / {tot/1024/1024:.0f} Mo")

            # Callback de relance : tracé dans le Journal + statut « ⟳ essai N ».
            def on_retry(attempt, total_attempts, message, item=it):
                """Callback de relance : trace la nouvelle tentative après une coupure réseau."""
                self._ui(self._set_item_status, item,
                         f"⟳ essai {attempt + 1}/{total_attempts}", "#f59e0b")
                self._ui(self._log,
                         f"Relance {item.title} : {message} (essai {attempt}/{total_attempts})")

            big = self._file_size(it.path) > cfg.CHUNK_THRESHOLD_BYTES
            # Voie empruntée : par morceaux pour un gros fichier, OU en repli
            # quand l'envoi direct a été coupé (voir plus bas).
            par_morceaux = big
            try:
                if not big:
                    # ── Fichier sous le seuil : upload classique par TOKEN ──
                    try:
                        video = self.api.upload_video(
                            it.path, it.title or it.filename, owner_url, type_url,
                            main_lang=self.config_data.get("main_lang", "fr"),
                            cursus=self.config_data.get("cursus", "0"),
                            is_draft=is_draft,
                            additional_owner_urls=self.additional_owner_urls,
                            site_urls=self.site_urls,
                            progress_cb=progress,
                            retry_cb=on_retry,
                            annuler=annuler,
                        )
                    except PodAPIError as e:
                        # REPLI AUTOMATIQUE SUR L'ENVOI PAR MORCEAUX (repris de
                        # PodAdmin). Même sous le seuil, la passerelle coupe un
                        # envoi monobloc qui dure plus d'environ une minute
                        # (« SSLEOFError: EOF occurred in violation of
                        # protocol ») : ce qui compte est la DURÉE, donc le
                        # débit montant du poste, pas la taille. Réessayer à
                        # l'identique échoue invariablement ; la voie par
                        # morceaux est faite pour résister à ces coupures.
                        # Un refus du serveur (400, 403…) n'est PAS rejoué.
                        if not self._est_coupure_reseau(e):
                            raise
                        self._ui(self._log,
                                 f"⚠️ {it.title} : envoi direct coupé par le serveur. "
                                 "Bascule automatique sur l'envoi par morceaux…")
                        self._ui(self._set_item_status, it, "⟳ envoi par morceaux", T_ALERTE)
                        par_morceaux = True
                    else:
                        it.slug = video.get("slug", "") if isinstance(video, dict) else ""
                        it.video_url = video.get("url", "") if isinstance(video, dict) else ""

                if par_morceaux:
                    # ── MORCEAUX via le VÉHICULE DEPOT (embarqué) ──
                    if chunked is None:
                        chunked = PodChunkedSession(
                            self.config_data.get("url", ""),
                            self.vehicle_username, self.vehicle_password)
                        chunked.login()
                        self._ui(self._log, "Session véhicule ouverte (upload chunké).")
                    if big:
                        self._ui(self._log,
                                 f"Gros fichier (> {cfg.CHUNK_THRESHOLD_BYTES//1024//1024} Mo) : "
                                 f"bascule chunkée pour {it.title}.")
                    # 1) Envoi par morceaux → vidéo créée au nom du VÉHICULE.
                    slug, video = self._deposer_par_morceaux(chunked, it, progress, on_retry,
                                                             annuler)
                    it.slug = slug
                    it.video_url = video.get("url", "") if isinstance(video, dict) else ""
                    # 2) RÉATTRIBUTION au propriétaire choisi + métadonnées (token enseignant).
                    #    Si le PATCH owner échoue, la vidéo reste au nom du véhicule :
                    #    on le signale FORT (jamais en silence).
                    if video:
                        patch = {
                            "owner": owner_url,                      # ← propriétaire CHOISI
                            "title": it.title or it.filename,
                            "type": type_url,
                            "is_draft": is_draft,
                            "main_lang": self.config_data.get("main_lang", "fr"),
                            "cursus": self.config_data.get("cursus", "0"),
                        }
                        if self.additional_owner_urls:
                            patch["additional_owners"] = list(self.additional_owner_urls)
                        try:
                            self.api.patch_video(video, patch)
                        except Exception as e:
                            it.error = f"réattribution échouée : {e}"
                            self._ui(self._set_item_status, it, "⚠️ NON réattribuée", T_ERREUR)
                            self._ui(self._log,
                                     f"⚠️⚠️ {it.title} : vidéo créée (slug={slug}) mais NON "
                                     f"réattribuée à {owner_url} — RESTE au nom du véhicule ! "
                                     f"Détail : {e}")
                            self._ui(self.batch_progress.set, idx / total)
                            continue
                    else:
                        self._ui(self._log,
                                 f"⚠️ Vidéo créée (slug={slug}) mais introuvable via l'API pour "
                                 "réattribution — à vérifier côté web.")

                # Discipline — rattachée APRÈS création, par PATCH (relation
                # multiple : une LISTE d'URLs). Un échec ne fait PAS échouer le
                # dépôt : la vidéo est déposée, seul son classement manque.
                if discipline_url and it.slug:
                    try:
                        # L'URL de la vidéo, pas son slug : l'API indexe les
                        # vidéos par identifiant numérique, et `/videos/<slug>/`
                        # peut répondre 404 (voir PodAPI._video_endpoint). Le
                        # classement échouait alors, seulement noté au Journal.
                        self.api.set_disciplines(it.video_url or it.slug, [discipline_url])
                    except Exception as e:
                        self._ui(self._log, f"Discipline non rattachée ({it.title}) : {e}")

                # Contributeurs communs
                for c in self.common_contributors:
                    try:
                        self.api.add_contributor(it.video_url, c["name"], c.get("email", ""),
                                                 c.get("role", "author"), c.get("weblink", ""))
                    except Exception as e:
                        self._ui(self._log, f"Contributeur non ajouté ({it.title}) : {e}")

                # Encodage
                if do_encode and it.slug:
                    try:
                        self.api.launch_encoding(it.slug)
                    except Exception as e:
                        self._ui(self._log, f"Encodage non lancé ({it.title}) : {e}")

                it.done = True            # marque le succès (ne sera pas relancé)
                it.error = ""
                ok += 1
                # Mémoire de session : prévient avant un renvoi, même si la
                # ligne est retirée de la liste entre-temps (voir _add_paths).
                self.deposes_session[self._cle_fichier(it.path)] = (
                    datetime.now().strftime("%H:%M"), it.slug)
                self._ui(self._set_item_status, it, "✅ terminé", T_SUCCES)
                self._ui(self._log,
                         f"Téléversé{' (chunké)' if par_morceaux else ''} : {it.title}  (slug={it.slug})")

            except ANNULATIONS as e:
                # Arrêt demandé : ni un échec (rien à « relancer »), ni une
                # erreur à afficher en rouge. On s'arrête là pour tout le lot.
                interrompu = True
                if getattr(e, "a_verifier", False):
                    it.a_verifier = True
                    it.error = str(e)
                    # Peut-être créée : même précaution qu'un envoi réussi si
                    # le fichier est retiré puis réajouté.
                    self.deposes_session[self._cle_fichier(it.path)] = (
                        datetime.now().strftime("%H:%M"), "peut-être créée, à vérifier")
                    self._ui(self._set_item_status, it, "⚠️ à vérifier", T_ALERTE)
                    self._ui(self._log, f"⚠️ {it.title} : {e}")
                else:
                    self._ui(self._set_item_status, it, "⏹ interrompu", T_ALERTE)
                    self._ui(self._log,
                             f"⏹ {it.title} : envoi interrompu — aucune vidéo créée.")
                self._ui(self.batch_progress.set, idx / total)
                break
            except PodChunkedError as e:
                it.error = f"{e} — {e.body}"
                self._ui(self._set_item_status, it, "❌ échec", T_ERREUR)
                self._ui(self._log, f"ÉCHEC chunké {it.title} : {e} | {e.body[:200]}")
            except PodAPIError as e:
                it.error = f"{e} — {e.body}"
                self._ui(self._set_item_status, it, "❌ échec", T_ERREUR)
                self._ui(self._log, f"ÉCHEC {it.title} : {e} | {e.body[:200]}")
            except Exception as e:
                it.error = str(e)
                self._ui(self._set_item_status, it, "❌ échec", T_ERREUR)
                self._ui(self._log, f"ÉCHEC {it.title} : {e}")

            self._ui(self.batch_progress.set, idx / total)

        # Fermeture propre de la session véhicule si elle a été ouverte.
        if chunked is not None:
            chunked.close()

        self._ui(self._on_batch_done, ok, len(self.items), interrompu)

    def _on_batch_done(self, ok: int, total: int, interrompu: bool = False):
        """Réactive l'interface, affiche le bilan et gère le bouton « Relancer les échecs »."""
        self.launch_btn.configure(state="normal")
        self.retry_btn.configure(state="normal")
        self._depot_fin()
        self.file_progress.set(0)
        self.file_progress_lbl.configure(text="")
        self._masquer_progression()
        reussite = (ok == total)
        if interrompu:
            reste = sum(1 for it in self.items
                        if not it.done and not it.a_verifier and not it.slug)
            a_verifier = sum(1 for it in self.items if it.a_verifier)
            texte = f"⏹  Interrompu : {ok}/{total} vidéo(s) téléversée(s)."
            if reste:
                texte += f" « Lancer le téléversement » reprend les {reste} restante(s)."
            if a_verifier:
                texte += f" ⚠️ {a_verifier} à vérifier (voir le Journal)."
            self.global_msg.configure(text=texte, text_color=T_ALERTE)
            self._log(f"Lot interrompu : {ok}/{total} réussis.")
        else:
            self.global_msg.configure(
                text=(f"✅  Terminé : {ok} vidéo(s) téléversée(s). "
                      f"Vous pouvez les retirer de la liste."
                      if reussite else
                      f"Terminé : {ok}/{total} vidéo(s) téléversée(s)."),
                text_color=T_SUCCES if reussite else T_ALERTE)
            self._log(f"Lot terminé : {ok}/{total} réussis.")

        # « Relancer les échecs (N) » : seulement les vrais échecs, jamais une
        # vidéo déjà créée (voir _echecs_a_relancer).
        self._update_retry_button()
        # « Retirer les N terminées » : à jour avec le nouveau bilan.
        self._maj_bouton_purge()

        # Au moins une vidéo déposée : « Mes vidéos » n'est plus à jour. Un lot
        # entièrement échoué n'a rien changé côté serveur : on n'invalide pas.
        if ok:
            self._myvids_mark_stale()

    def _retry_failed(self):
        """Relance UNIQUEMENT les vidéos en échec, sans re-sélectionner de fichiers.

        Réutilise le propriétaire et le type déjà choisis. Les vidéos réussies
        (it.done) sont ignorées par _do_batch_upload."""
        if not self.api:
            return
        # Propriétaire et type mémorisés lors du dernier lancement.
        owner_url = getattr(self, "_last_owner_url", "") or self.config_data.get("agent_owner_url", "")
        type_url = getattr(self, "_last_type_url", "") or self.type_map.get(self.type_combo.get(), "")
        if not owner_url or not type_url:
            self.global_msg.configure(text="Propriétaire ou type manquant pour la relance.",
                                      text_color=T_ALERTE)
            return
        echecs = self._echecs_a_relancer()
        if not echecs:
            self.global_msg.configure(text="Aucune vidéo en échec.", text_color=T_SECONDAIRE)
            return
        # Remet les échecs en « en attente » pour un affichage propre. Une
        # vidéo créée mais non réattribuée garde son alerte : elle ne sera pas
        # renvoyée (le lot la saute, voir _do_batch_upload).
        for it in echecs:
            self._set_item_status(it, "en attente", T_SECONDAIRE)
        self.launch_btn.configure(state="disabled")
        self.retry_btn.configure(state="disabled")
        self.retry_btn.pack_forget()
        # La relance n'emprunte pas `_start_upload` : sans cet appel, les
        # barres resteraient masquées pendant tout le renvoi.
        self._afficher_progression()
        self._log(f"Relance de {len(echecs)} vidéo(s) en échec…")
        self._depot_debut()
        self._run(self._do_batch_upload, owner_url, type_url,
                  getattr(self, "_last_discipline_url", ""),
                  self.visibility_combo.get().startswith("Brouillon"),
                  bool(self.encode_var.get()))

    # ═════════════════════════════════════════════════════════════════════
    #  ONGLET « MES VIDÉOS »
    # ═════════════════════════════════════════════════════════════════════
    #
    #  But : offrir à l'enseignant la gestion de SES vidéos (celles du compte
    #  choisi comme « propriétaire » dans l'onglet Téléversement), directement
    #  depuis le Téléverseur — sans passer par l'interface web de Pod.
    #
    #  Cet onglet reprend le panneau « Vidéos » de PodAdmin :
    #     • liste filtrable (texte, statut, encodage, chaîne, type) ;
    #     • panneau détail/actions : renommer, statut (Brouillon/Public/
    #       Restreint), type, co-propriétaires, sous-titres, REMPLACER le
    #       fichier & ré-encoder, supprimer ;
    #     • sélection multiple : statut, type, disciplines, chaînes et
    #       thèmes, suppression, sur les vidéos sélectionnées.
    #
    #  DIFFÉRENCES VOULUES avec PodAdmin :
    #     • PAS d'affectation à des groupes d'accès (bloc retiré) ;
    #     • PAS de bouton « Chaînes… » (édition) — le filtre par chaîne et
    #       l'affichage restent, mais on ne modifie plus les chaînes ici ;
    #     • l'onglet n'affiche QUE les vidéos du PROPRIÉTAIRE SÉLECTIONNÉ
    #       (agent_owner_url), et se rafraîchit automatiquement quand ce choix
    #       change.
    #
    #  REMPLACER un gros fichier (> seuil) : le chunké exige une session web.
    #  On réutilise le compte véhicule DEPOT (embarqué), qui a le statut
    #  d'équipe (is_staff) — il peut donc finaliser un remplacement chunké
    #  (target_slug) sur la vidéo d'un enseignant, exactement comme il crée
    #  déjà les vidéos avant réaffectation.

    def _build_tab_myvids(self):
        """Construit l'onglet « Mes vidéos » (liste + panneau détail/actions)."""
        frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.tabs["myvids"] = frame

        ctk.CTkLabel(frame, text="🎞️  Mes vidéos",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", pady=(0, 4))
        # Bandeau « propriétaire courant » : rappelle DE QUI on affiche les
        # vidéos. Mis à jour automatiquement à chaque changement de compte.
        self.myvids_owner_lbl = ctk.CTkLabel(
            frame, text="", text_color=T_SECONDAIRE, font=ctk.CTkFont(size=12),
            justify="left", wraplength=860)
        self.myvids_owner_lbl.pack(anchor="w", pady=(0, 8))

        # — Ligne : rafraîchir + statut de chargement —
        top = ctk.CTkFrame(frame, fg_color="transparent")
        top.pack(fill="x")
        self.myvids_refresh_btn = ctk.CTkButton(
            top, text="🔄  Rafraîchir", fg_color=C_NEUTRE, hover_color=C_NEUTRE_SURV,
            text_color=T_SUR_NEUTRE,
            # Gris et non vert : utilitaire, comme dans PodAdmin. Le seul
            # bouton coloré d'un écran doit être son action principale.
            command=self._myvids_load)
        self.myvids_refresh_btn.pack(side="left")
        # Sélection multiple : Ctrl+clic et Maj+clic dans la liste, ou ce bouton.
        ctk.CTkButton(top, text="☑  Tout sélectionner", width=160, fg_color=C_NEUTRE,
                      hover_color=C_NEUTRE_SURV, text_color=T_SUR_NEUTRE,
                      command=self._myvids_tout_selectionner).pack(side="left", padx=(8, 0))
        self.myvids_status = ctk.CTkLabel(top, text="(non chargé)", text_color=T_SECONDAIRE,
                                          font=ctk.CTkFont(size=11))
        self.myvids_status.pack(side="left", padx=10)

        # — Ligne : filtres (identiques à PodAdmin, moins rien) —
        filt = ctk.CTkFrame(frame, fg_color="transparent")
        filt.pack(fill="x", pady=(8, 4))
        self.myvids_text = ctk.CTkEntry(filt, placeholder_text="🔍 titre / slug…")
        self.myvids_text.pack(side="left", fill="x", expand=True)
        self.myvids_text.bind("<KeyRelease>", lambda e: self._myvids_apply_filter())
        self.myvids_statut = ctk.CTkOptionMenu(
            filt, width=130, values=["Tous statuts", "Brouillon", "Public", "Restreinte"],
            command=lambda _c: self._myvids_apply_filter(), **STYLE_CHAMP)
        self.myvids_statut.set("Tous statuts")
        self.myvids_statut.pack(side="left", padx=6)
        self.myvids_encode = ctk.CTkOptionMenu(
            filt, width=150, values=["Tout encodage", "Encodées", "Non-encodées"],
            command=lambda _c: self._myvids_apply_filter(), **STYLE_CHAMP)
        self.myvids_encode.set("Tout encodage")
        self.myvids_encode.pack(side="left", padx=6)
        self.myvids_chan = ctk.CTkOptionMenu(filt, width=170, values=["Toutes chaînes"],
                                             command=lambda _c: self._myvids_apply_filter(), **STYLE_CHAMP)
        self.myvids_chan.set("Toutes chaînes")
        self.myvids_chan.pack(side="left", padx=6)
        self.myvids_type = ctk.CTkOptionMenu(filt, width=150, values=["Tous types"],
                                             command=lambda _c: self._myvids_apply_filter(), **STYLE_CHAMP)
        self.myvids_type.set("Tous types")
        self.myvids_type.pack(side="left", padx=6)

        # La barre « Modifier en masse » (type des vidéos AFFICHÉES) a été
        # RETIRÉE : le type d'un lot passe par la sélection (« Tout
        # sélectionner » puis le panneau de lot), avec confirmation — un seul
        # chemin pour la même action, comme dans PodAdmin.

        # — Corps : liste (gauche) + détail (droite) —
        body = ctk.CTkFrame(frame, fg_color="transparent")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=2)
        body.columnconfigure(1, weight=3)
        body.rowconfigure(1, weight=1)

        self.myvids_count_lbl = ctk.CTkLabel(body, text="", text_color=T_SECONDAIRE,
                                             font=ctk.CTkFont(size=11), anchor="w")
        self.myvids_count_lbl.grid(row=0, column=0, sticky="w", pady=(0, 2))
        self.myvids_list = ctk.CTkScrollableFrame(body, label_text="Mes vidéos")
        self.myvids_list.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self.myvids_detail = ctk.CTkScrollableFrame(body, label_text="Détail / actions")
        self.myvids_detail.grid(row=1, column=1, sticky="nsew", padx=(6, 0))

        # — Données (caches internes de l'onglet) —
        self.myvids_videos = []          # vidéos du propriétaire (déjà filtrées owner)
        self.myvids_channels = []        # chaînes (pour le filtre + l'affichage)
        self.myvids_themes = []          # thèmes (sélecteur chaînes & thèmes)
        self.myvids_multi = []           # slugs de la sélection multiple, dans l'ordre
        self._myvids_ancre = None        # point de départ d'une sélection Maj+clic
        self.myvids_chan_by_url = {}     # URL chaîne → titre
        self.myvids_filtered = []        # sous-ensemble affiché (après filtres)
        self.myvids_selected = None      # vidéo en cours d'édition
        self.myvids_owner_url = ""       # URL du propriétaire actuellement chargé
        self.myvids_loaded = False       # a-t-on déjà chargé pour ce propriétaire ?

        self._myvids_update_header()     # affiche le bandeau propriétaire
        self._myvids_render_detail()     # message d'invite dans le panneau droit

    # ── Propriétaire courant & rafraîchissement automatique ────────────────

    def _myvids_current_owner(self):
        """Renvoie (owner_url_normalisée, username) du propriétaire sélectionné
        dans l'onglet Téléversement, ou ("", "") si aucun n'est défini."""
        url = str(self.config_data.get("agent_owner_url", "")).rstrip("/")
        name = self.config_data.get("agent_username", "")
        return url, name

    def _myvids_owner_user(self):
        """Retrouve le dict complet du compte propriétaire dans la liste des
        utilisateurs déjà chargée (pour connaître son username/id). Peut renvoyer
        None si la liste n'est pas encore prête."""
        owner_url, _ = self._myvids_current_owner()
        for u in (self.all_users or []):
            if str(u.get("url", "")).rstrip("/") == owner_url:
                return u
        return None

    def _myvids_owner_ids(self) -> set:
        """Ensemble des identifiants qui « valent » le propriétaire courant
        (URL, username, id numérique), pour comparer de façon robuste au champ
        `owner` des vidéos, quel que soit le format renvoyé par l'API."""
        owner_url, name = self._myvids_current_owner()
        ids = set()
        if owner_url:
            # ⚠️ Barre finale retirée AVANT d'extraire l'id : l'URL de l'API
            # finit par « / », et `split("/")[-1]` donnait alors une chaîne
            # VIDE — le numéro du compte n'était jamais ajouté, et une vidéo
            # dont le champ `owner` est l'URL n'était pas reconnue.
            nette = str(owner_url).rstrip("/")
            ids.update({owner_url, nette, nette.split("/")[-1]})
        if name:
            ids.add(name)
        return {x for x in ids if x}

    def _myvids_update_header(self):
        """Met à jour le bandeau « propriétaire courant » et l'état des boutons.
        Sans propriétaire ou sans connexion, on guide l'utilisateur au lieu de
        laisser une liste vide inexpliquée."""
        if not hasattr(self, "myvids_owner_lbl"):
            return
        owner_url, name = self._myvids_current_owner()
        if not self.api:
            self.myvids_owner_lbl.configure(
                text="⚠️  Non connecté. Connectez-vous dans l'onglet Configuration, "
                     "puis choisissez le propriétaire dans l'onglet Téléversement.",
                text_color=T_ALERTE)
            self.myvids_refresh_btn.configure(state="disabled")
        elif not owner_url:
            self.myvids_owner_lbl.configure(
                text="⚠️  Aucun propriétaire sélectionné. Ouvrez l'onglet Téléversement "
                     "et cliquez sur « 🎯 Saisir l'identifiant… ». Cet onglet affichera "
                     "alors les vidéos de ce compte et celles dont il est co-propriétaire.",
                text_color=T_ALERTE)
            self.myvids_refresh_btn.configure(state="disabled")
        else:
            self.myvids_owner_lbl.configure(
                text=f"Vidéos du compte : {name or owner_url}   "
                     "(vidéos dont ce compte est propriétaire ou co-propriétaire).",
                text_color=T_SECONDAIRE)
            self.myvids_refresh_btn.configure(state="normal")

    def _myvids_notify_owner_changed(self):
        """Appelée à CHAQUE changement de propriétaire (via _refresh_owner_status).
        Si le compte a changé, on invalide le cache et on rafraîchit :
          • immédiatement si l'onglet est actuellement visible ;
          • sinon paresseusement, à la prochaine ouverture de l'onglet.
        """
        # L'onglet peut ne pas être encore construit (appel très tôt au démarrage).
        if not hasattr(self, "myvids_owner_url"):
            return
        new_owner, _ = self._myvids_current_owner()
        self._myvids_update_header()
        if new_owner == self.myvids_owner_url and self.myvids_loaded:
            return                       # même propriétaire déjà chargé : rien à faire
        # Le propriétaire a changé (ou n'a jamais été chargé) : on invalide.
        self.myvids_loaded = False
        self.myvids_selected = None
        self.myvids_videos = []
        self.myvids_filtered = []
        if hasattr(self, "myvids_list"):
            for w in self.myvids_list.winfo_children():
                w.destroy()
            self._myvids_render_detail()
            self.myvids_status.configure(text="(à rafraîchir)", text_color=T_SECONDAIRE)
        # Rafraîchissement LIVE si l'onglet est à l'écran, sinon au prochain affichage.
        try:
            visible = self.tabs["myvids"].winfo_ismapped()
        except Exception:
            visible = False
        if visible and self.api and new_owner:
            self._myvids_load()

    def _myvids_mark_stale(self):
        """Appelée après un dépôt réussi (thread principal). Même invalidation
        que _myvids_notify_owner_changed, mais SANS le court-circuit « même
        propriétaire » : le compte n'a pas changé, c'est son CONTENU qui a
        changé. Sans elle, les vidéos qu'on vient de déposer n'apparaissaient
        pas dans « Mes vidéos » avant un rafraîchissement manuel.
          • rechargement immédiat si l'onglet est à l'écran ;
          • sinon au prochain affichage (_myvids_on_shown voit loaded=False).
        """
        if not hasattr(self, "myvids_owner_url"):
            return                       # onglet pas encore construit
        self.myvids_loaded = False
        self.myvids_selected = None
        self.myvids_videos = []
        self.myvids_filtered = []
        # La sélection multiple désigne des vidéos de l'ANCIENNE liste : la
        # garder ferait agir une action de lot sur des lignes disparues.
        self.myvids_multi = []
        if hasattr(self, "myvids_list"):
            for w in self.myvids_list.winfo_children():
                w.destroy()
            self._myvids_render_detail()
            self.myvids_status.configure(text="(à rafraîchir — nouveau dépôt)",
                                         text_color=T_SECONDAIRE)
        try:
            visible = self.tabs["myvids"].winfo_ismapped()
        except Exception:
            visible = False
        owner_url, _ = self._myvids_current_owner()
        if visible and self.api and owner_url:
            self._myvids_load()

    def _myvids_on_shown(self):
        """Appelée quand on ouvre l'onglet. Charge les vidéos si nécessaire
        (première ouverture pour ce propriétaire), sinon ne fait rien."""
        self._myvids_update_header()
        owner_url, _ = self._myvids_current_owner()
        if self.api and owner_url and not self.myvids_loaded:
            self._myvids_load()

    # ── Chargement (vidéos du propriétaire + chaînes) ──────────────────────

    def _myvids_load(self):
        """Déclenche le (re)chargement des vidéos du propriétaire courant."""
        if not self.api:
            self.myvids_status.configure(text="Connectez-vous d'abord.", text_color=T_ALERTE)
            return
        owner_url, _ = self._myvids_current_owner()
        if not owner_url:
            self.myvids_status.configure(text="Choisissez d'abord un propriétaire.",
                                         text_color=T_ALERTE)
            return
        self.myvids_status.configure(text="⏳  Chargement…", text_color=T_SECONDAIRE)
        self._run(self._do_myvids_load, owner_url)

    def _do_myvids_load(self, owner_url):
        """(Thread) Récupère les vidéos du propriétaire + les chaînes.

        Stratégie de filtrage par propriétaire, ROBUSTE quel que soit le
        comportement du serveur :
          1) on demande à l'API de filtrer côté serveur via extra_params
             {'owner': <url>} — si l'instance le sait faire, on ne rapatrie que
             les vidéos du compte (rapide) ;
          2) DANS TOUS LES CAS on re-filtre côté client avec _video_belongs_to :
             ainsi, même si le serveur ignore le paramètre et renvoie tout, on
             n'affiche jamais que les vidéos du bon propriétaire (correction
             garantie ; cf. sonde verifier_mes_videos.py pour savoir si le
             filtre serveur est honoré et éviter le scan complet)."""
        try:
            def prog(n):
                self._ui(self.myvids_status.configure,
                         text=f"⏳  {n} vidéos lues…", text_color=T_SECONDAIRE)
            # 1) Tentative de filtre serveur — PUREMENT FACULTATIVE.
            raw = self._myvids_lire_videos(owner_url, prog)
            # 2) Filtre client : on ne garde que les vidéos du propriétaire.
            owner_ids = self._myvids_owner_ids()
            videos = [v for v in raw if self._video_belongs_to(v, owner_ids)]

            try:
                channels = self.api.get_channels()
            except Exception:
                channels = []
            # Thèmes : même moment. Un échec n'empêche rien — le sélecteur
            # montre alors les chaînes seules.
            try:
                self.myvids_themes = self.api.get_themes()
            except Exception as e:
                self.myvids_themes = []
                self._ui(self._log, f"Thèmes non chargés : {e}")

            self.myvids_videos = videos
            self.myvids_channels = channels
            self.myvids_chan_by_url = {str(c.get("url", "")).rstrip("/"): c.get("title", "?")
                                       for c in channels}
            self.myvids_owner_url = owner_url
            self.myvids_loaded = True
            self._ui(self._myvids_refresh_channel_menu)
            self._ui(self._myvids_refresh_type_menu)
            self._ui(self._myvids_apply_filter)
            self._ui(self.myvids_status.configure,
                     text=f"✅  {len(videos)} vidéo(s)  ·  chargé à "
                          f"{datetime.now().strftime('%H:%M')}",
                     text_color=T_SUCCES)
            self._ui(self._log, f"Mes vidéos : {len(videos)} vidéo(s) pour "
                                f"{self.config_data.get('agent_username','?')}.")
        except Exception as e:
            self._signaler(self.myvids_status, e, "Chargement « Mes vidéos »")

    def _myvids_lire_videos(self, owner_url: str, prog):
        """Lit les vidéos, en tentant d'abord un filtre côté serveur.

        ⚠️ Le filtre serveur est une OPTIMISATION, jamais une condition de
        succès. Le format attendu varie d'une instance à l'autre : sur
        videos.utoulouse.fr, `owner=<URL>` est refusé avec « Sélectionnez un
        choix valide » (HTTP 400). Le code ne tentait que cette forme, et son
        échec faisait échouer tout le chargement : l'onglet n'affichait plus
        aucune vidéo, avec un message d'erreur incompréhensible.

        On essaie donc les formes connues l'une après l'autre (voir la sonde
        `verifier_mes_videos.py`), puis on se rabat sur une lecture COMPLÈTE.
        Dans tous les cas, l'appelant re-filtre côté client : le résultat
        affiché est identique, seule la quantité de données lues change.
        """
        numero = str(owner_url).rstrip("/").split("/")[-1]
        _, nom = self._myvids_current_owner()
        variantes = []
        if numero.isdigit():
            variantes.append(("owner = id numérique", {"owner": numero}))
        if owner_url:
            variantes.append(("owner = URL complète", {"owner": owner_url}))
        if nom:
            variantes.append(("owner__username", {"owner__username": nom}))

        possedees = self._premier_filtre_accepte(variantes, prog)
        if possedees is None:
            self._ui(self._log, "Aucun filtre serveur accepté : lecture complète "
                                "du fonds, puis filtrage dans l'application.")
            return self.api.get_all_videos(progress_cb=prog)

        # ⚠️ Le filtre `owner` ne renvoie que les vidéos POSSÉDÉES : les vidéos
        # en CO-PROPRIÉTÉ n'y figurent jamais. On tente le filtre équivalent
        # sur `additional_owners` ; s'il est refusé, seule une lecture
        # complète garantit de ne pas les perdre.
        variantes_co = []
        if numero.isdigit():
            variantes_co.append(("additional_owners = id numérique",
                                 {"additional_owners": numero}))
        if owner_url:
            variantes_co.append(("additional_owners = URL complète",
                                 {"additional_owners": owner_url}))
        copossedees = self._premier_filtre_accepte(variantes_co, prog)
        if copossedees is None:
            self._ui(self._log, "Filtre de co-propriété refusé : lecture "
                                "complète, pour ne perdre aucune vidéo partagée.")
            return self.api.get_all_videos(progress_cb=prog)

        vues, fusion = set(), []
        for v in list(possedees) + list(copossedees):
            cle = v.get("slug") or id(v)
            if cle not in vues:
                vues.add(cle)
                fusion.append(v)
        return fusion

    def _premier_filtre_accepte(self, variantes, prog):
        """Première forme de filtre acceptée par le serveur, ou None."""
        for libelle, params in variantes:
            try:
                return self.api.get_all_videos(progress_cb=prog,
                                               extra_params=params)
            except Exception as e:
                self._ui(self._log,
                         f"Filtre serveur « {libelle} » refusé "
                         f"({e.__class__.__name__}) — on essaie autrement.")
        return None

    def _myvids_refresh_channel_menu(self):
        """Remplit le filtre par chaîne avec les chaînes chargées."""
        vals = ["Toutes chaînes"] + sorted(self.myvids_chan_by_url.values(), key=str.lower)
        self.myvids_chan.configure(values=vals)
        self.myvids_chan.set("Toutes chaînes")

    def _myvids_refresh_type_menu(self):
        """Remplit le filtre par type avec les types.
        Sans danger si appelé avant que les types soient chargés."""
        titles = sorted((self.type_map or {}).keys(), key=str.lower)
        if hasattr(self, "myvids_type"):
            self.myvids_type.configure(values=["Tous types"] + titles)
            if self.myvids_type.get() not in (["Tous types"] + titles):
                self.myvids_type.set("Tous types")

    # ── Identité du propriétaire d'une vidéo ───────────────────────────────

    def _video_owner_id(self, video):
        """Identifiant du propriétaire d'une vidéo, quel que soit le format
        renvoyé par l'API (URL absolue, dict imbriqué, username ou id)."""
        o = video.get("owner")
        if isinstance(o, dict):                     # owner imbriqué {url, username…}
            return o.get("url") or o.get("username") or ""
        return o if o is not None else ""           # URL (str), username ou id

    @staticmethod
    def _correspond(valeur, owner_ids: set) -> bool:
        """Une référence de compte (URL, id, username, ou dict {url, username})
        désigne-t-elle le compte courant ? Comparaison sans barre finale."""
        if isinstance(valeur, dict):
            return any(App._correspond(valeur.get(k), owner_ids)
                       for k in ("url", "username", "id") if valeur.get(k))
        v = str(valeur or "").rstrip("/")
        if not v:
            return False
        ids = {str(i).rstrip("/") for i in owner_ids}
        return v in ids or v.split("/")[-1] in ids

    def _role_sur_video(self, video, owner_ids: set):
        """« proprietaire », « coproprietaire », ou None.

        Les deux rôles entrent dans « Mes vidéos » ; seul le propriétaire peut
        SUPPRIMER (Pod le refuse à un co-propriétaire)."""
        if self._correspond(self._video_owner_id(video), owner_ids):
            return "proprietaire"
        autres = video.get("additional_owners") or []
        if not isinstance(autres, (list, tuple)):
            autres = [autres]
        if any(self._correspond(u, owner_ids) for u in autres):
            return "coproprietaire"
        return None

    def _video_belongs_to(self, video, owner_ids: set) -> bool:
        """Vrai si le compte est propriétaire OU co-propriétaire de la vidéo."""
        return self._role_sur_video(video, owner_ids) is not None

    def _myvids_owner_label(self, v) -> str:
        """Nom lisible du propriétaire d'une vidéo (pour l'affichage)."""
        oid = str(self._video_owner_id(v)).rstrip("/")
        for u in (self.all_users or []):
            if str(u.get("url", "")).rstrip("/") == oid or u.get("username") == oid:
                return u.get("username", oid)
        return oid or "—"

    # ── Filtrage ───────────────────────────────────────────────────────────

    def _myvids_apply_filter(self, *_):
        """Applique les filtres (statut/encodage/chaîne/type/texte) au cache."""
        if not self.myvids_videos:
            self.myvids_count_lbl.configure(text="Cliquez sur « 🔄 Rafraîchir ».")
            for w in self.myvids_list.winfo_children():
                w.destroy()
            return

        vids = self.myvids_videos
        # Filtre statut
        st = self.myvids_statut.get()
        if st == "Brouillon":
            vids = [v for v in vids if v.get("is_draft")]
        elif st == "Public":
            vids = [v for v in vids if not v.get("is_draft")]
        elif st == "Restreinte":
            vids = [v for v in vids if v.get("is_restricted")]
        # Filtre encodage
        enc = self.myvids_encode.get()
        if enc == "Encodées":
            vids = [v for v in vids if v.get("encoded")]
        elif enc == "Non-encodées":
            vids = [v for v in vids if PodAPI.is_unencoded(v)]
        # Filtre chaîne (on compare l'URL de la chaîne)
        ch = self.myvids_chan.get()
        if ch and ch != "Toutes chaînes":
            wanted = [u for u, t in self.myvids_chan_by_url.items() if t == ch]
            def in_chan(v):
                """Teste si une vidéo appartient à la chaîne sélectionnée dans le filtre."""
                cs = v.get("channel") or []
                if isinstance(cs, str):
                    cs = [cs]
                cs = [str(c).rstrip("/") for c in cs]
                return any(w in cs for w in wanted)
            vids = [v for v in vids if in_chan(v)]
        # Filtre type (valeur unique : on compare l'URL du type)
        ty = self.myvids_type.get() if hasattr(self, "myvids_type") else "Tous types"
        if ty and ty != "Tous types":
            turl = str((self.type_map or {}).get(ty, "")).rstrip("/")
            def has_type(v):
                """Teste si une vidéo correspond au type sélectionné dans le filtre."""
                vt = v.get("type")
                vt = vt.get("url") if isinstance(vt, dict) else vt
                return str(vt).rstrip("/") == turl
            vids = [v for v in vids if has_type(v)]
        # Filtre texte (titre / slug)
        txt = self.myvids_text.get().strip().lower()
        if txt:
            def hay(v):
                """Concatène les champs d'une vidéo en une chaîne, pour la recherche plein-texte."""
                return f"{v.get('title','')} {v.get('slug','')}".lower()
            vids = [v for v in vids if txt in hay(v)]

        self.myvids_filtered = vids
        self._render_myvids_list()

    def _render_myvids_list(self):
        """Reconstruit la liste de gauche (plafonnée pour ne pas figer Tk)."""
        for w in self.myvids_list.winfo_children():
            w.destroy()
        self.myvids_count_lbl.configure(text=f"{len(self.myvids_filtered)} vidéo(s).")

        if not self.myvids_filtered:
            ctk.CTkLabel(self.myvids_list, text="Aucune vidéo ne correspond.",
                         text_color=T_SECONDAIRE).pack(pady=10)
            return

        CAP = 300
        sel_slug = self.myvids_selected.get("slug") if self.myvids_selected else None
        for v in self.myvids_filtered[:CAP]:
            slug = v.get("slug", "?")
            is_sel = slug == sel_slug
            title = (v.get("title") or "(sans titre)")[:48]
            tag = "📝" if v.get("is_draft") else "🌐"      # brouillon / public
            if self._role_sur_video(v, self._myvids_owner_ids()) == "coproprietaire":
                tag += " 👥"                                 # co-propriété
            en_lot = slug in self.myvids_multi
            btn = ctk.CTkButton(
                self.myvids_list, text=f"{tag}  {title}", anchor="w", height=H_NORMAL,
                fg_color=C_MULTI if en_lot else (("gray75", "gray30") if is_sel else "transparent"),
                text_color=("gray10", "gray90"), hover_color=("gray75", "gray28"),
                font=ctk.CTkFont(size=12),
                command=lambda vv=v: self._myvids_select(vv))
            btn.pack(fill="x", pady=1)
            # ⚠️ CTkButton déclenche sa commande au RELÂCHEMENT du clic : bloquer
            # le seul <Control-Button-1> ne suffit pas, le relâchement lançait
            # quand même une sélection simple (piège rencontré dans PodAdmin).
            btn.bind("<Control-Button-1>", lambda e, vv=v: self._myvids_toggle_multi(vv))
            btn.bind("<Shift-Button-1>", lambda e, vv=v: self._myvids_plage_multi(vv))
            btn.bind("<Control-ButtonRelease-1>", lambda e: "break")
            btn.bind("<Shift-ButtonRelease-1>", lambda e: "break")
        if len(self.myvids_filtered) > CAP:
            ctk.CTkLabel(self.myvids_list,
                         text=f"… +{len(self.myvids_filtered) - CAP} autres. Affinez le filtre.",
                         text_color=T_SECONDAIRE).pack(pady=4)

    # ── Sélection multiple ──────────────────────────────────────────────

    @staticmethod
    def _rel_urls(value, normalise: bool = True) -> list:
        """URLs d'une relation (chaîne, thème, discipline) : le champ peut être
        une URL, une liste d'URLs ou d'objets imbriqués selon le sérialiseur."""
        if not value:
            return []
        if not isinstance(value, (list, tuple)):
            value = [value]
        sortie = []
        for x in value:
            u = x.get("url", "") if isinstance(x, dict) else str(x)
            if u:
                sortie.append(u.rstrip("/") if normalise else u)
        return sortie

    def _myvids_toggle_multi(self, v):
        """Ctrl+clic : ajoute ou retire une vidéo de la sélection."""
        slug = v.get("slug")
        if not self.myvids_multi and self.myvids_selected:
            # Le premier Ctrl+clic emporte la vidéo déjà ouverte : c'est le
            # comportement attendu d'un gestionnaire de fichiers.
            premier = self.myvids_selected.get("slug")
            if premier and premier != slug:
                self.myvids_multi.append(premier)
        if slug in self.myvids_multi:
            self.myvids_multi.remove(slug)
        else:
            self.myvids_multi.append(slug)
        self._myvids_ancre = slug
        self._myvids_apres_selection()
        return "break"

    def _myvids_plage_multi(self, v):
        """Maj+clic : sélectionne toute la plage depuis la dernière vidéo cliquée."""
        ordre = [x.get("slug") for x in self.myvids_filtered]
        ancre = self._myvids_ancre or (self.myvids_selected or {}).get("slug")
        cible = v.get("slug")
        if ancre in ordre and cible in ordre:
            i, j = sorted((ordre.index(ancre), ordre.index(cible)))
            for slug in ordre[i:j + 1]:
                if slug not in self.myvids_multi:
                    self.myvids_multi.append(slug)
        else:
            self.myvids_multi = [cible]
        self._myvids_apres_selection()
        return "break"

    def _myvids_tout_selectionner(self):
        """Sélectionne toutes les vidéos AFFICHÉES (après filtres)."""
        self.myvids_multi = [x.get("slug") for x in self.myvids_filtered if x.get("slug")]
        self._myvids_apres_selection()

    def _myvids_vider_selection(self):
        self.myvids_multi = []
        self._myvids_apres_selection()

    def _myvids_apres_selection(self):
        if len(self.myvids_multi) == 1:
            # Une seule vidéo : on revient au panneau de détail classique.
            slug = self.myvids_multi[0]
            self.myvids_selected = next(
                (x for x in self.myvids_videos if x.get("slug") == slug), None)
            self.myvids_multi = []
        self._render_myvids_list()
        self._myvids_render_detail()

    def _myvids_lot(self) -> list:
        par_slug = {x.get("slug"): x for x in self.myvids_videos}
        return [par_slug[s] for s in self.myvids_multi if s in par_slug]

    def _myvids_render_lot(self):
        """Panneau des actions sur la sélection multiple."""
        vids = self._myvids_lot()
        ids = self._myvids_owner_ids()
        n_co = sum(1 for x in vids if self._role_sur_video(x, ids) == "coproprietaire")
        d = self.myvids_detail
        ctk.CTkLabel(d, text=f"{len(vids)} vidéos sélectionnées",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=4, pady=(4, 0))
        apercu = ", ".join((x.get("title") or "?")[:30] for x in vids[:4])
        ctk.CTkLabel(d, text=apercu + ("…" if len(vids) > 4 else ""), wraplength=420,
                     justify="left", text_color=T_SECONDAIRE,
                     font=ctk.CTkFont(size=11)).pack(anchor="w", padx=4)
        ctk.CTkLabel(d, text="Ctrl+clic : ajouter ou retirer · Maj+clic : plage · "
                             "clic simple : revenir à une vidéo.",
                     text_color=T_DISCRET, font=ctk.CTkFont(size=10)).pack(anchor="w", padx=4, pady=(2, 6))

        def titre(t):
            ctk.CTkLabel(d, text=t, anchor="w", font=ctk.CTkFont(size=12, weight="bold")
                         ).pack(anchor="w", padx=4, pady=(10, 2))

        titre("Statut")
        seg = ctk.CTkSegmentedButton(d, values=list(STATUTS))
        seg.pack(fill="x", padx=4)
        seg.configure(command=lambda choix: self._myvids_lot_statut(choix, seg))

        titre("Classement")
        ligne = ctk.CTkFrame(d, fg_color="transparent")
        ligne.pack(fill="x", padx=4)
        types = sorted(getattr(self, "type_map", {}) or {}, key=str.lower) or ["(aucun type)"]
        menu_type = ctk.CTkOptionMenu(ligne, width=180, values=types, **STYLE_CHAMP)
        menu_type.pack(side="left")
        ctk.CTkButton(ligne, text=f"Appliquer le type à {len(vids)} vidéos", fg_color=C_NEUTRE,
                      hover_color=C_NEUTRE_SURV, text_color=T_SUR_NEUTRE,
                      command=lambda: self._myvids_lot_type(menu_type.get())
                      ).pack(side="left", padx=(6, 0))
        ctk.CTkButton(d, text="🏷️  Disciplines…", fg_color=C_NEUTRE, hover_color=C_NEUTRE_SURV,
                      text_color=T_SUR_NEUTRE, command=self._myvids_lot_disciplines
                      ).pack(anchor="w", padx=4, pady=(6, 0))

        titre("Relations")
        ctk.CTkButton(d, text="🗂  Chaînes et thèmes…", fg_color=C_NEUTRE,
                      hover_color=C_NEUTRE_SURV, text_color=T_SUR_NEUTRE,
                      command=self._myvids_lot_chaines).pack(anchor="w", padx=4)

        ctk.CTkLabel(d, text="Zone sensible", anchor="w",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=T_ERREUR).pack(anchor="w", padx=4, pady=(14, 2))
        possedees = len(vids) - n_co
        if possedees:
            ctk.CTkButton(d, text=f"🗑  Supprimer {possedees} vidéo(s)",
                          fg_color=C_DESTRUCTIF, hover_color=C_DESTR_SURV,
                          command=self._myvids_lot_supprimer).pack(anchor="w", padx=4)
        if n_co:
            ctk.CTkLabel(d, text=f"👥  {n_co} vidéo(s) en co-propriété : elles ne peuvent "
                                 "pas être supprimées et seront ignorées par la "
                                 "suppression.",
                         text_color=T_SECONDAIRE, font=ctk.CTkFont(size=11),
                         wraplength=400, justify="left").pack(anchor="w", padx=4, pady=(4, 0))

        ctk.CTkButton(d, text="Désélectionner tout", fg_color=C_NEUTRE,
                      hover_color=C_NEUTRE_SURV, text_color=T_SUR_NEUTRE,
                      command=self._myvids_vider_selection).pack(anchor="w", padx=4, pady=(14, 0))
        self.myvids_msg = ctk.CTkLabel(d, text="", text_color=T_SECONDAIRE,
                                       wraplength=420, justify="left")
        self.myvids_msg.pack(anchor="w", padx=4, pady=(8, 4))

    # — Actions de lot —

    def _myvids_lot_statut(self, choix, seg=None):
        vids = self._myvids_lot()
        if not messagebox.askyesno("Statut en lot",
                                   f"Passer {len(vids)} vidéo(s) en « {choix} » ?"):
            if seg is not None:
                seg.set("")
            return
        payload = STATUTS[choix]
        self._myvids_lancer_lot(vids, lambda v: self.api.patch_video(v, payload),
                                lambda v: v.update(payload), f"statut → {choix.lower()}")

    def _myvids_lot_type(self, titre_type):
        url = (getattr(self, "type_map", {}) or {}).get(titre_type)
        if not url:
            self._myvids_set_msg("Aucun type disponible.", T_ALERTE)
            return
        vids = self._myvids_lot()
        if not messagebox.askyesno("Type en lot",
                                   f"Donner le type « {titre_type} » à {len(vids)} vidéo(s) ?"):
            return
        self._myvids_lancer_lot(vids, lambda v: self.api.patch_video(v, {"type": url}),
                                lambda v: v.update({"type": url}), f"type → {titre_type}")

    def _myvids_liste_disciplines(self):
        return [{"url": u, "title": t} for t, u in
                sorted((getattr(self, "discipline_map", {}) or {}).items(),
                       key=lambda x: x[0].lower())]

    def _myvids_lot_disciplines(self):
        disciplines = self._myvids_liste_disciplines()
        if not disciplines:
            self._myvids_set_msg("Aucune discipline définie sur la plateforme.", T_ALERTE)
            return
        vids = self._myvids_lot()

        def appliquer(urls, libelles):
            urls = list(urls)
            texte = ", ".join(libelles) if libelles else "aucune"
            if not messagebox.askyesno(
                    "Disciplines en lot",
                    f"Donner les disciplines « {texte} » à {len(vids)} vidéo(s) ?\n\n"
                    "Leurs disciplines actuelles seront REMPLACÉES."):
                return
            self._myvids_lancer_lot(vids, lambda v: self.api.set_disciplines(v, urls),
                                    lambda v: v.update({"discipline": urls}),
                                    f"disciplines → {texte}")

        ChannelPicker(self, disciplines, on_done=appliquer,
                      title=f"Disciplines pour {len(vids)} vidéo(s)",
                      consigne="Cochez les disciplines à donner aux vidéos "
                               "sélectionnées. Elles REMPLACERONT leurs disciplines "
                               "actuelles.",
                      vide="Aucune discipline.")

    def _myvids_lot_chaines(self):
        vids = self._myvids_lot()

        def appliquer(chaines, themes):
            choix = messagebox.askyesnocancel(
                "Chaînes et thèmes en lot",
                f"{len(chaines)} chaîne(s) et {len(themes)} thème(s) pour "
                f"{len(vids)} vidéo(s).\n\n"
                "Oui : AJOUTER aux chaînes et thèmes existants de chaque vidéo.\n"
                "Non : REMPLACER — les chaînes et thèmes existants seront perdus.\n"
                "Annuler : ne rien faire.")
            if choix is None:
                return
            mode = "ajouter" if choix else "remplacer"
            dispo = bool(self.myvids_themes)

            def action(v):
                finales, th = calculer_chaines_themes(
                    self._rel_urls(v.get("channel"), normalise=False),
                    self._rel_urls(v.get("theme"), normalise=False),
                    chaines, themes, mode, themes_disponibles=dispo)
                self.api.assign_video_to_channels(v, finales, theme_urls=th)
                v["_maj_lot"] = {"channel": finales, **({"theme": th} if th is not None else {})}

            self._myvids_lancer_lot(vids, action,
                                    lambda v: v.update(v.pop("_maj_lot", {})),
                                    f"chaînes et thèmes ({'ajout' if choix else 'remplacement'})")

        ChainesThemesPicker(
            self, self.myvids_channels, self.myvids_themes, on_done=appliquer,
            title=f"Chaînes et thèmes pour {len(vids)} vidéo(s)",
            consigne="Cochez les chaînes et, si besoin, les thèmes à appliquer aux "
                     "vidéos sélectionnées. Cocher un thème coche sa chaîne.")

    def _myvids_lot_supprimer(self):
        """Suppression en lot : les vidéos en CO-PROPRIÉTÉ sont ignorées (Pod
        la refuse à un co-propriétaire). Double confirmation."""
        ids = self._myvids_owner_ids()
        vids = [x for x in self._myvids_lot()
                if self._role_sur_video(x, ids) == "proprietaire"]
        if not vids:
            self._myvids_set_msg("Aucune vidéo supprimable dans la sélection.", T_ALERTE)
            return
        if not messagebox.askyesno(
                "⚠️  Supprimer des vidéos",
                f"Supprimer DÉFINITIVEMENT {len(vids)} vidéo(s) ?\n\n"
                "Il n'y a pas de corbeille sur Pod."):
            return
        if not messagebox.askyesno("Confirmation",
                                   f"Dernière confirmation : {len(vids)} vidéo(s) "
                                   "seront effacées. Continuer ?"):
            return

        def apres(v):
            if v in self.myvids_videos:
                self.myvids_videos.remove(v)
            if v.get("slug") in self.myvids_multi:
                self.myvids_multi.remove(v.get("slug"))

        self._myvids_lancer_lot(vids, lambda v: self.api.delete_video(v), apres,
                                "suppression")

    def _myvids_lancer_lot(self, vids, action, apres, libelle):
        self._myvids_set_msg(f"⏳  {libelle} : {len(vids)} vidéo(s) en cours…", T_SECONDAIRE)
        self._run(self._do_myvids_lot, list(vids), action, apres, libelle)

    def _do_myvids_lot(self, vids, action, apres, libelle):
        """(Thread) Applique `action` à chaque vidéo, INDÉPENDAMMENT : un échec
        n'arrête pas les suivantes. `apres` met à jour le cache local."""
        ok, echecs = 0, []
        for v in vids:
            try:
                action(v)
                apres(v)
                ok += 1
            except Exception as e:
                echecs.append(v.get("title") or v.get("slug") or "?")
                self._ui(self._log, f"❌ {libelle} — {v.get('slug')} : {e}")
        self._ui(self._log, f"Lot « {libelle} » : {ok} réussie(s), {len(echecs)} échec(s).")
        texte = f"✅  {libelle} : {ok} vidéo(s)."
        if echecs:
            texte += f" {len(echecs)} échec(s) : {', '.join(echecs[:3])}"
            texte += "…" if len(echecs) > 3 else ""
            texte += " (détail dans le Journal)."
        self._ui(self._myvids_apply_filter)
        self._ui(self._myvids_render_detail)
        self._ui(self._myvids_set_msg, texte, T_ALERTE if echecs else T_SUCCES)

    # — Une seule vidéo : disciplines, chaînes et thèmes —

    def _myvids_edit_disciplines(self, v):
        disciplines = self._myvids_liste_disciplines()
        if not disciplines:
            self._myvids_set_msg("Aucune discipline définie sur la plateforme.", T_ALERTE)
            return
        actuelles = {_norm_url(u) for u in self._rel_urls(v.get("discipline"), normalise=False)}
        pre = {d["url"]: d["title"] for d in disciplines if _norm_url(d["url"]) in actuelles}

        def appliquer(urls, libelles):
            texte = ", ".join(libelles) if libelles else "aucune"
            self._myvids_patch(v, {"discipline": list(urls)}, f"disciplines → {texte}")

        ChannelPicker(self, disciplines, on_done=appliquer,
                      title=f"Disciplines — {v.get('title', '')}", preselected=pre,
                      consigne="Cochez les disciplines de cette vidéo. Décocher les retire.",
                      vide="Aucune discipline.")

    def _myvids_edit_channels(self, v):
        def appliquer(chaines, themes):
            self._myvids_patch(v, {"channel": list(chaines), "theme": list(themes)},
                               f"{len(chaines)} chaîne(s), {len(themes)} thème(s)")

        ChainesThemesPicker(
            self, self.myvids_channels, self.myvids_themes, on_done=appliquer,
            title=f"Chaînes et thèmes — {v.get('title', '')}",
            chaines_pre=self._rel_urls(v.get("channel"), normalise=False),
            themes_pre=self._rel_urls(v.get("theme"), normalise=False))

    def _myvids_select(self, v):
        """Sélectionne une vidéo et affiche son panneau de détail.

        Un clic simple ANNULE la sélection multiple : c'est le comportement de
        tous les gestionnaires de fichiers."""
        self.myvids_multi = []
        self._myvids_ancre = v.get("slug")
        self.myvids_selected = v
        self._render_myvids_list()      # met à jour la surbrillance
        self._myvids_render_detail()

    # ── Panneau de détail / actions ────────────────────────────────────────

    def _myvids_render_detail(self):
        """Reconstruit le panneau de droite pour la vidéo sélectionnée."""
        for w in self.myvids_detail.winfo_children():
            w.destroy()
        if len(self.myvids_multi) >= 2:
            self._myvids_render_lot()
            return
        v = self.myvids_selected
        if not v:
            ctk.CTkLabel(self.myvids_detail,
                         text="Sélectionnez une vidéo dans la liste pour l'éditer.",
                         text_color=T_SECONDAIRE).pack(pady=14)
            return

        slug = v.get("slug", "?")

        # — Informations —
        ctk.CTkLabel(self.myvids_detail, text=v.get("title", "(sans titre)"),
                     font=ctk.CTkFont(size=15, weight="bold"),
                     wraplength=420, justify="left").pack(anchor="w", padx=4, pady=(4, 0))
        chans = v.get("channel") or []
        if isinstance(chans, str):
            chans = [chans]
        chan_names = ", ".join(self.myvids_chan_by_url.get(str(c).rstrip("/"), "?")
                               for c in chans) or "(aucune)"
        info = (f"slug : {slug}\n"
                f"durée : {v.get('duration_in_time', '—')}     "
                f"encodée : {'oui' if v.get('encoded') else 'non'}\n"
                f"chaînes : {chan_names}")
        ctk.CTkLabel(self.myvids_detail, text=info, justify="left", anchor="w",
                     text_color=T_SECONDAIRE, font=ctk.CTkFont(size=12)).pack(
            anchor="w", padx=4, pady=(2, 4))

        # Lecture : pas de lecteur intégré, on ouvre la page Pod dans le navigateur.
        ctk.CTkButton(self.myvids_detail, text="▶  Ouvrir dans le navigateur",
                      width=210, height=28, fg_color=C_ACCENT, hover_color=C_ACCENT_SURV,
                      command=lambda s=slug: self._myvids_open_in_browser(s)).pack(
            anchor="w", padx=4, pady=(0, 10))

        # — Renommer —
        ren = ctk.CTkFrame(self.myvids_detail, fg_color="transparent")
        ren.pack(fill="x", padx=4)
        self.myvids_title_entry = ctk.CTkEntry(ren)
        self.myvids_title_entry.insert(0, v.get("title", ""))
        self.myvids_title_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(ren, text="Renommer", width=90, fg_color=C_NEUTRE,
                      command=lambda: self._myvids_rename(v), text_color=T_SUR_NEUTRE).pack(side="left", padx=6)

        # — Statut (bouton segmenté : les 3 états sont EXCLUSIFS) —
        #   Brouillon = is_draft True
        #   Public    = is_draft False ET is_restricted False
        #   Restreint = is_draft False ET is_restricted True
        # Chaque choix envoie les DEUX booléens cohérents d'un coup.
        ctk.CTkLabel(self.myvids_detail, text="Statut", anchor="w",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(
            anchor="w", padx=4, pady=(10, 2))

        def _status_of(vid):
            """Renvoie le libellé de statut d'une vidéo (Brouillon / Public / Restreint)."""
            if vid.get("is_draft"):
                return "Brouillon"
            return "Restreint" if vid.get("is_restricted") else "Public"
        status_seg = ctk.CTkSegmentedButton(
            self.myvids_detail, values=["Brouillon", "Public", "Restreint"])
        status_seg.set(_status_of(v))
        status_seg.pack(fill="x", padx=4, pady=(0, 2))
        ctk.CTkLabel(self.myvids_detail,
                     text="Restreint = visible mais connexion requise.",
                     font=ctk.CTkFont(size=10), text_color=T_DISCRET).pack(anchor="w", padx=6)

        def _apply_status(choice):
            """Applique le statut choisi dans le menu à la vidéo affichée."""
            payload = {"Brouillon": {"is_draft": True, "is_restricted": False},
                       "Public":    {"is_draft": False, "is_restricted": False},
                       "Restreint": {"is_draft": False, "is_restricted": True}}[choice]
            v.update(payload)                       # MAJ cache local immédiate
            self._myvids_patch(v, payload, f"statut → {choice.lower()}")
        status_seg.configure(command=_apply_status)

        # NB : le bloc « Restreindre à des groupes d'accès » de PodAdmin est
        # VOLONTAIREMENT absent ici (fonction non offerte dans le Téléverseur).

        # — Type —
        # Classement : type ET disciplines côte à côte, comme dans PodAdmin.
        ctk.CTkLabel(self.myvids_detail, text="Classement", anchor="w",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(
            anchor="w", padx=4, pady=(12, 2))
        ligne_classement = ctk.CTkFrame(self.myvids_detail, fg_color="transparent")
        ligne_classement.pack(fill="x", padx=4)
        cur_url = v.get("type")
        cur_url = cur_url.get("url") if isinstance(cur_url, dict) else cur_url
        url_to_title = {str(u).rstrip("/"): t for t, u in (self.type_map or {}).items()}
        cur_title = url_to_title.get(str(cur_url).rstrip("/"), "(non défini)")
        titles = sorted((self.type_map or {}).keys(), key=str.lower) or ["(aucun type)"]
        type_menu = ctk.CTkOptionMenu(ligne_classement, width=200, values=titles, **STYLE_CHAMP)
        type_menu.set(cur_title if cur_title in titles else titles[0])
        type_menu.pack(side="left")
        ctk.CTkButton(ligne_classement, text="🏷️  Disciplines…", fg_color=C_NEUTRE,
                      hover_color=C_NEUTRE_SURV, text_color=T_SUR_NEUTRE,
                      command=lambda: self._myvids_edit_disciplines(v)).pack(side="left", padx=(6, 0))
        par_url = {_norm_url(u): t for t, u in (getattr(self, "discipline_map", {}) or {}).items()}
        noms = [par_url.get(_norm_url(u), "?") for u in self._rel_urls(v.get("discipline"), normalise=False)]
        ctk.CTkLabel(self.myvids_detail, text="Disciplines : " + (", ".join(noms) or "aucune"),
                     text_color=T_SECONDAIRE, font=ctk.CTkFont(size=11), anchor="w",
                     justify="left", wraplength=360).pack(anchor="w", padx=4, pady=(4, 0))

        def _apply_type(choice):
            """Applique le type choisi dans le menu à la vidéo affichée."""
            new_url = (self.type_map or {}).get(choice)
            if new_url and str(new_url).rstrip("/") != str(cur_url).rstrip("/"):
                v["type"] = new_url
                self._myvids_patch(v, {"type": new_url}, f"type → {choice}")
        type_menu.configure(command=_apply_type)

        # — Relations : co-propriétaires (le bouton « Chaînes… » est RETIRÉ) —
        ctk.CTkLabel(self.myvids_detail, text="Relations", anchor="w",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(
            anchor="w", padx=4, pady=(12, 2))
        rel = ctk.CTkFrame(self.myvids_detail, fg_color="transparent")
        rel.pack(fill="x", padx=4)
        ctk.CTkButton(rel, text="👥  Co-propriétaires…", fg_color=C_NEUTRE,
                      command=lambda: self._myvids_edit_owners(v), text_color=T_SUR_NEUTRE).pack(side="left")
        ctk.CTkButton(rel, text="🗂  Chaînes et thèmes…", fg_color=C_NEUTRE,
                      hover_color=C_NEUTRE_SURV, text_color=T_SUR_NEUTRE,
                      command=lambda: self._myvids_edit_channels(v)).pack(side="left", padx=(6, 0))

        # — Sous-titres —
        ctk.CTkLabel(self.myvids_detail, text="Sous-titres", anchor="w",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(
            anchor="w", padx=4, pady=(12, 2))
        self.myvids_subs = ctk.CTkFrame(self.myvids_detail, fg_color="transparent")
        self.myvids_subs.pack(fill="x", padx=4)
        ctk.CTkLabel(self.myvids_subs, text="Chargement…", text_color=T_SECONDAIRE,
                     font=ctk.CTkFont(size=11)).pack(anchor="w")
        ctk.CTkButton(self.myvids_detail, text="➕  Ajouter un sous-titre (.vtt / .srt)",
                      fg_color=C_NEUTRE,
                      command=lambda: self._myvids_sub_add_dialog(v), text_color=T_SUR_NEUTRE).pack(
            anchor="w", padx=4, pady=(6, 0))
        self._run(self._myvids_sub_load, v)   # charge les pistes en arrière-plan

        # — Fichier source : REMPLACER + ré-encoder —
        ctk.CTkLabel(self.myvids_detail, text="Fichier source", anchor="w",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(
            anchor="w", padx=4, pady=(14, 2))
        ctk.CTkLabel(self.myvids_detail,
                     text="Remplace le fichier vidéo par un nouveau puis relance l'encodage. "
                          "La vidéo garde son titre, ses chaînes, ses droits… seul le média change.",
                     text_color=T_SECONDAIRE, font=ctk.CTkFont(size=11),
                     justify="left", wraplength=360).pack(anchor="w", padx=4)
        ctk.CTkButton(self.myvids_detail, text="🎬  Remplacer & ré-encoder",
                      width=230,
                      fg_color=C_ALERTE, hover_color=C_ALERTE_SURV,
                      command=lambda: self._myvids_replace_source(v)).pack(
            anchor="w", padx=4, pady=(4, 0))

        # — Suppression (zone sensible) —
        ctk.CTkLabel(self.myvids_detail, text="Zone sensible", anchor="w",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=T_ERREUR).pack(anchor="w", padx=4, pady=(14, 2))
        if self._role_sur_video(v, self._myvids_owner_ids()) == "coproprietaire":
            # Pod refuse la suppression à un co-propriétaire : on l'explique
            # au lieu de proposer un bouton qui échouerait.
            ctk.CTkLabel(self.myvids_detail,
                         text="👥  Vous êtes co-propriétaire de cette vidéo : vous "
                              "pouvez la modifier, mais seul son propriétaire peut "
                              "la supprimer.",
                         text_color=T_SECONDAIRE, font=ctk.CTkFont(size=11),
                         wraplength=360, justify="left", anchor="w").pack(
                anchor="w", padx=4, pady=(0, 8))
        else:
            ctk.CTkButton(self.myvids_detail, text="🗑  Supprimer cette vidéo",
                          fg_color=C_DESTRUCTIF, hover_color=C_DESTR_SURV,
                          command=lambda: self._myvids_delete(v)).pack(
                anchor="w", padx=4, pady=(0, 8))

        # Zone de message du panneau
        self.myvids_msg = ctk.CTkLabel(self.myvids_detail, text="", text_color=T_SECONDAIRE,
                                       font=ctk.CTkFont(size=11), wraplength=420,
                                       justify="left")
        self.myvids_msg.pack(anchor="w", padx=4, pady=(4, 8))

    def _myvids_set_msg(self, text, color):
        """Affiche un message dans le panneau détail (si encore présent)."""
        if hasattr(self, "myvids_msg") and self.myvids_msg.winfo_exists():
            self.myvids_msg.configure(text=text, text_color=color)

    def _myvids_open_in_browser(self, slug):
        """Ouvre la page publique Pod de la vidéo dans le navigateur par défaut."""
        import webbrowser
        base = str(self.config_data.get("url", "")).rstrip("/") or "https://videos.utoulouse.fr"
        try:
            webbrowser.open(f"{base}/video/{slug}/")
            self._myvids_set_msg(f"Ouverture de {slug} dans le navigateur…", "gray")
        except Exception as e:
            self._myvids_set_msg(f"Impossible d'ouvrir le navigateur : {message_utilisateur(e)}", T_ALERTE)

    # ── Modifications simples (PATCH) ──────────────────────────────────────

    def _myvids_rename(self, v):
        """Renomme la vidéo si le titre a changé."""
        new = self.myvids_title_entry.get().strip()
        if new and new != v.get("title"):
            self._myvids_patch(v, {"title": new}, f"titre → {new}")

    def _myvids_patch(self, v, payload, msg):
        """Applique un PATCH sur la vidéo (en arrière-plan) puis rafraîchit."""
        self._run(self._do_myvids_patch, v, payload, msg)

    def _do_myvids_patch(self, v, payload, msg):
        """(Thread) PATCH de la vidéo + mise à jour du cache et de l'affichage."""
        slug = v.get("slug", "")
        try:
            self.api.patch_video(v, payload)
            v.update(payload)                      # met à jour le cache local
            self._ui(self._log, f"✏ {slug} : {msg}")
            self._ui(self._myvids_set_msg, f"✅  {msg}", T_SUCCES)
            self._ui(self._myvids_render_detail)
            # Refiltrer, pas seulement redessiner : une vidéo qui ne correspond
            # plus au filtre actif (ex. passée en Public alors qu'on affiche
            # les brouillons) doit quitter la liste, comme après une
            # suppression ou une action de lot.
            self._ui(self._myvids_apply_filter)
        except Exception as e:
            self._ui(self._log, f"❌ {slug} : {e}")
            self._ui(self._myvids_set_msg, f"❌  {message_utilisateur(e)}", T_ERREUR)
            self._ui(self._myvids_render_detail)

    def _myvids_edit_owners(self, v):
        """Ouvre le sélecteur d'utilisateurs pour choisir les CO-propriétaires."""
        user_by_url = {str(u.get("url", "")).rstrip("/"): u for u in (self.all_users or [])}
        pre = {}
        for ourl in (v.get("additional_owners") or []):
            u = user_by_url.get(str(ourl).rstrip("/"))
            pre[ourl] = self._user_label(u) if u else ourl
        OwnerPicker(self, on_done=lambda urls, labels: self._myvids_apply_owners(v, urls),
                    title="Co-propriétaires de la vidéo", preselected=pre)

    def _myvids_apply_owners(self, v, urls):
        """Enregistre les co-propriétaires choisis (PATCH additional_owners)."""
        self._run(self._do_myvids_patch, v, {"additional_owners": list(urls)},
                  f"{len(urls)} co-propriétaire(s)")

    def _myvids_sub_load(self, v):
        """(Thread) Charge les pistes de sous-titres de la vidéo puis les affiche."""
        try:
            tracks = self.api.get_tracks(v)
            self._ui(self._myvids_sub_render, v, tracks)
        except Exception as e:
            self._ui(self._myvids_sub_render, v, None, str(e))

    def _myvids_sub_render(self, v, tracks, err=None):
        """Affiche la liste des pistes existantes (langue · type · 🗑)."""
        if not hasattr(self, "myvids_subs") or not self.myvids_subs.winfo_exists():
            return
        for w in self.myvids_subs.winfo_children():
            w.destroy()
        if err:
            # `err` arrive ici sous forme de texte : on le traduit quand même,
            # `message_utilisateur` acceptant aussi bien une exception qu'un
            # message déjà formé.
            ctk.CTkLabel(self.myvids_subs,
                         text=f"❌ {message_utilisateur(Exception(str(err)))}",
                         text_color=T_ERREUR,
                         font=ctk.CTkFont(size=11)).pack(anchor="w")
            return
        if not tracks:
            ctk.CTkLabel(self.myvids_subs, text="Aucun sous-titre.", text_color=T_SECONDAIRE,
                         font=ctk.CTkFont(size=11)).pack(anchor="w")
            return
        langs = dict(SUBTITLE_LANGS)
        kinds = dict(SUBTITLE_KINDS)
        for t in tracks:
            row = ctk.CTkFrame(self.myvids_subs, fg_color=("gray85", "gray17"),
                               corner_radius=6)
            row.pack(fill="x", pady=2)
            lang = langs.get(t.get("lang"), t.get("lang"))
            kind = kinds.get(t.get("kind"), t.get("kind"))
            ctk.CTkLabel(row, text=f"{lang} · {kind}", anchor="w",
                         font=ctk.CTkFont(size=12)).pack(side="left", padx=10, pady=5,
                                                         fill="x", expand=True)
            ctk.CTkButton(row, text="🗑", width=34, fg_color=C_DESTRUCTIF,
                          hover_color=C_DESTR_SURV,
                          command=lambda tt=t: self._myvids_sub_delete(v, tt)).pack(
                side="right", padx=6)

    def _myvids_sub_add_dialog(self, v):
        """Fenêtre d'ajout d'un sous-titre : langue + type + fichier .vtt/.srt."""
        if not self.api:
            return
        win = ctk.CTkToplevel(self)
        win.title("Ajouter un sous-titre")
        win.geometry("440x300")
        _focus_toplevel(win, self)

        ctk.CTkLabel(win, text=f"Vidéo : {(v.get('title') or '')[:50]}",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(
            padx=16, pady=(16, 10), anchor="w")

        ctk.CTkLabel(win, text="Langue :").pack(padx=16, anchor="w")
        lang_labels = [f"{lbl} ({code})" for code, lbl in SUBTITLE_LANGS]
        lang_menu = ctk.CTkOptionMenu(win, values=lang_labels, width=260, **STYLE_CHAMP)
        lang_menu.set("Français (fr)")
        lang_menu.pack(padx=16, pady=(0, 8), anchor="w")

        ctk.CTkLabel(win, text="Type :").pack(padx=16, anchor="w")
        kind_menu = ctk.CTkOptionMenu(win, values=[lbl for _c, lbl in SUBTITLE_KINDS],
                                      width=260, **STYLE_CHAMP)
        kind_menu.set("Sous-titres")
        kind_menu.pack(padx=16, pady=(0, 8), anchor="w")

        path_var = {"p": None}
        path_lbl = ctk.CTkLabel(win, text="Aucun fichier choisi.", text_color=T_SECONDAIRE,
                                font=ctk.CTkFont(size=11), wraplength=400, justify="left")

        def choose():
            """Ouvre le sélecteur et mémorise le choix de l'utilisateur."""
            p = filedialog.askopenfilename(
                title="Choisir un fichier de sous-titres",
                filetypes=[("Sous-titres", "*.vtt *.srt"), ("Tous", "*.*")])
            if p:
                path_var["p"] = p
                path_lbl.configure(text=os.path.basename(p), text_color="white")

        ctk.CTkButton(win, text="📄  Choisir un fichier .vtt / .srt",
                      command=choose, fg_color=C_NEUTRE, text_color=T_SUR_NEUTRE).pack(padx=16, pady=(4, 2), anchor="w")
        path_lbl.pack(padx=16, anchor="w")

        def valider():
            """Valide la saisie de la fenêtre et déclenche l'action correspondante."""
            lang_code = SUBTITLE_LANGS[lang_labels.index(lang_menu.get())][0]
            kind_code = next(c for c, lbl in SUBTITLE_KINDS if lbl == kind_menu.get())
            path = path_var["p"]
            if not path:
                path_lbl.configure(text="⚠️ Choisissez d'abord un fichier.",
                                   text_color=T_ALERTE)
                return
            if not path.lower().endswith((".vtt", ".srt")):
                path_lbl.configure(text="⚠️ Le fichier doit être .vtt ou .srt.",
                                   text_color=T_ALERTE)
                return
            win.destroy()
            self._run(self._myvids_sub_do_add, v, lang_code, kind_code, path)

        ctk.CTkButton(win, text="Ajouter", fg_color=C_SUCCES, hover_color=C_SUCCES_SURV,
                      command=valider).pack(pady=14)

    def _myvids_sub_do_add(self, v, lang, kind, path):
        """(Thread) Téléverse le fichier, crée la piste, rafraîchit la liste."""
        try:
            self.api.add_subtitle(v, lang, kind, path)   # conversion .srt incluse
            self._ui(self._log, f"➕ Sous-titre ajouté ({lang}/{kind}) à {v.get('slug')}")
            self._ui(self._myvids_set_msg, "✅  Sous-titre ajouté.", T_SUCCES)
            self._run(self._myvids_sub_load, v)
        except Exception as e:
            self._ui(self._log, f"❌ Ajout sous-titre {v.get('slug')} : {e}")
            self._ui(self._myvids_set_msg, f"❌  {message_utilisateur(e)}", T_ERREUR)

    def _myvids_sub_delete(self, v, track):
        """Supprime une piste de sous-titres après confirmation."""
        langs = dict(SUBTITLE_LANGS)
        lib = langs.get(track.get("lang"), track.get("lang"))
        if not messagebox.askyesno("Supprimer le sous-titre",
                                   f"Supprimer la piste « {lib} » de cette vidéo ?"):
            return
        self._run(self._myvids_sub_do_delete, v, track)

    def _myvids_sub_do_delete(self, v, track):
        """(Thread) DELETE de la piste puis rafraîchit la liste."""
        try:
            self.api.delete_track(track)
            self._ui(self._log, f"🗑 Sous-titre supprimé ({track.get('lang')}) de {v.get('slug')}")
            self._run(self._myvids_sub_load, v)
        except Exception as e:
            self._ui(self._log, f"❌ Suppression sous-titre : {e}")
            self._ui(self._myvids_set_msg, f"❌  {message_utilisateur(e)}", T_ERREUR)

    # ── Remplacer le fichier source + ré-encoder ───────────────────────────

    def _myvids_replace_source(self, v):
        """Remplace le fichier source d'une vidéo puis relance l'encodage.
        Demande le fichier + double confirmation (opération destructive).

        Deux voies selon la taille :
          • ≤ seuil : PATCH direct streamé via le token de l'enseignant ;
          • > seuil : remplacement CHUNKÉ via la session web du compte véhicule
            DEPOT (statut d'équipe), qui finalise avec le slug cible."""
        if not self.api:
            self._myvids_set_msg("Connectez-vous d'abord.", T_ALERTE)
            return
        path = filedialog.askopenfilename(
            title="Choisir le nouveau fichier vidéo",
            filetypes=[("Vidéos", "*.mp4 *.mov *.avi *.mkv *.webm *.m4v *.wmv *.flv"),
                       ("Tous les fichiers", "*.*")])
        if not path:
            return
        size = os.path.getsize(path)
        size_mo = size / 1024 / 1024
        gros = size > cfg.CHUNK_THRESHOLD_BYTES
        # Le remplacement chunké s'appuie sur le compte véhicule DEPOT (session
        # web). Ses identifiants sont embarqués (config.py) : normalement toujours
        # présents. On garde tout de même un garde-fou clair.
        if gros and not (self.vehicle_username and self.vehicle_password):
            self._myvids_set_msg(
                f"⚠️  Fichier > {cfg.CHUNK_THRESHOLD_BYTES//1024//1024} Mo : compte "
                "véhicule (DEPOT) requis pour le remplacement par morceaux.", "#f59e0b")
            return
        voie = (f"par MORCEAUX (session DEPOT) car > "
                f"{cfg.CHUNK_THRESHOLD_BYTES//1024//1024} Mo"
                if gros else "par PATCH direct")
        if not messagebox.askyesno(
                "⚠️  Remplacer le fichier source",
                f"Remplacer le fichier de « {v.get('title')} » par :\n"
                f"{os.path.basename(path)}  ({size_mo:.0f} Mo)\n"
                f"Méthode : {voie}.\n\n"
                "L'ancien fichier sera écrasé et la vidéo ré-encodée. "
                "Le titre, les chaînes et les droits sont conservés.\n\n"
                "Cette action ne peut pas être annulée. Continuer ?"):
            return
        self._myvids_set_msg("⏳  Envoi du nouveau fichier…", "gray")
        # Fenêtre MODALE de progression : elle bloque toute autre manipulation
        # pendant l'envoi (une action concurrente couperait le téléversement)
        # et montre l'avancement, comme pour le téléversement par lot.
        modal = ProgressModal(
            self,
            title="Remplacer & ré-encoder",
            subtitle=f"« {v.get('title', '')} »\n{os.path.basename(path)} "
                     f"({size_mo:.0f} Mo) — méthode {voie}.",
            intro="Préparation de l'envoi…")
        self._run(self._do_myvids_replace_source, v, path, modal)

    def _do_myvids_replace_source(self, v, path, modal=None):
        """(Thread) Remplace le fichier (PATCH direct ou chunké) puis ré-encode.

        `modal` : fenêtre ProgressModal ouverte par l'appelant. Elle est mise à
        jour à chaque étape et TOUJOURS clôturée en fin de traitement (y compris
        en cas d'erreur), pour ne jamais laisser l'interface bloquée."""
        slug = v.get("slug", "")
        fname = os.path.basename(path)

        def progress(sent, tot):
            """Callback de progression : avance la barre et affiche les Mo envoyés."""
            txt = f"⏳  Envoi {fname} — {sent/1024/1024:.0f}/{tot/1024/1024:.0f} Mo"
            self._ui(self._myvids_set_msg, txt, "gray")
            if modal:
                self._ui(modal.set_progress, (sent / tot if tot else 0),
                         f"{sent/1024/1024:.0f} / {tot/1024/1024:.0f} Mo envoyés")

        def on_retry(attempt, total_try, err):
            """Callback de relance : trace la nouvelle tentative après une coupure réseau."""
            self._ui(self._log,
                     f"⟳ Nouvelle tentative {attempt}/{total_try} (remplacement {slug})…")
            self._ui(self._myvids_set_msg,
                     f"⟳  Coupure réseau — nouvelle tentative {attempt}…", "#f59e0b")
            if modal:
                self._ui(modal.set_phase,
                         f"⟳  Coupure réseau — nouvelle tentative {attempt}/{total_try}…",
                         "#f59e0b")

        try:
            gros = os.path.getsize(path) > cfg.CHUNK_THRESHOLD_BYTES
            if gros:
                # ── Gros fichier : remplacement par MORCEAUX via session DEPOT ──
                # On finalise le chunké en passant le SLUG cible → Pod REMPLACE
                # le fichier de la vidéo existante (pas de nouvel enregistrement).
                self._ui(self._log,
                         f"🎬 Remplacement chunké (> {cfg.CHUNK_THRESHOLD_BYTES//1024//1024} Mo) "
                         f"pour {slug} ({fname})…")
                if modal:
                    self._ui(modal.set_phase, "Connexion au service de dépôt…")
                chunked = PodChunkedSession(self.config_data.get("url", ""),
                                            self.vehicle_username, self.vehicle_password)
                chunked.login()
                if modal:
                    self._ui(modal.set_phase, "Étape 1/3 — Envoi du fichier par morceaux…")
                try:
                    returned = chunked.upload_video_chunked(
                        path, chunk_size=cfg.CHUNK_SIZE_BYTES,
                        progress_cb=progress, retry_cb=on_retry,
                        target_slug=slug)          # ← slug cible = remplacement
                except PodChunkedError as ce:
                    # 502/503/504 à la finalisation : Pod termine côté serveur.
                    # On NE relance PAS l'encodage automatiquement (le fichier
                    # n'est peut-être pas encore assemblé) : on informe.
                    if ce.status in (502, 503, 504):
                        self._ui(self._log,
                                 f"⏳ Remplacement {slug} : finalisation coupée par la passerelle "
                                 f"(HTTP {ce.status}) — Pod termine côté serveur.")
                        self._ui(self._myvids_set_msg,
                                 "⏳  Remplacement en cours de finalisation côté serveur "
                                 "(jusqu'à ~10 min). Vérifiez côté web, puis relancez "
                                 "l'encodage via le même bouton.", "#f59e0b")
                        if modal:
                            self._ui(modal.finish, False,
                                     "Le fichier est envoyé, mais le serveur termine encore "
                                     "son assemblage (cela peut prendre ~10 min). Vérifiez la "
                                     "vidéo sur le site, puis relancez le ré-encodage si besoin.")
                        return
                    raise
                finally:
                    chunked.close()
                if returned and returned != slug:
                    # Sécurité : un slug différent = vidéo neuve créée au lieu
                    # d'un remplacement. On le signale clairement.
                    self._ui(self._log,
                             f"⚠️ Le remplacement a renvoyé un slug différent ({returned}) : "
                             "une vidéo neuve a peut-être été créée. À vérifier côté web.")
            else:
                # ── Fichier ≤ seuil : PATCH direct streamé ──
                if modal:
                    self._ui(modal.set_phase, "Étape 1/2 — Envoi du nouveau fichier…")
                self.api.replace_video_file(v, path, progress_cb=progress, retry_cb=on_retry)

            self._ui(self._log, f"🎬 Fichier remplacé pour {slug} ({fname}).")
            # Relancer l'encodage sur le nouveau fichier.
            if modal:
                # L'avancement n'est plus mesurable ici → animation continue.
                self._ui(modal.set_phase, "Étape finale — Lancement du ré-encodage…")
                self._ui(modal.set_indeterminate, True)
            try:
                self.api.launch_encoding(slug)
                v["encoded"] = False
                v["encoding_in_progress"] = True
                self._ui(self._log, f"⚙ Ré-encodage lancé pour {slug}.")
                self._ui(self._myvids_set_msg,
                         "✅  Fichier remplacé, ré-encodage lancé.", "#22c55e")
                if modal:
                    self._ui(modal.finish, True,
                             "Fichier remplacé et ré-encodage lancé. La vidéo sera disponible "
                             "sur le site à la fin de l'encodage (cela peut prendre un moment).")
            except Exception as e:
                self._ui(self._myvids_set_msg,
                         f"Fichier remplacé, mais encodage non lancé : {message_utilisateur(e)}", T_ALERTE)
                self._ui(self._log, f"❌ Encodage non lancé ({slug}) : {e}")
                if modal:
                    self._ui(modal.finish, False,
                             f"Fichier remplacé, mais le ré-encodage n'a pas pu être lancé : {message_utilisateur(e)}")
            self._ui(self._myvids_render_detail)
        except Exception as e:
            self._ui(self._myvids_set_msg, f"❌  {message_utilisateur(e)}", T_ERREUR)
            self._ui(self._log, f"❌ Remplacement {slug} : {e}")
            if modal:
                self._ui(modal.finish, False, f"Le remplacement a échoué : {message_utilisateur(e)}")
        finally:
            # Quoi qu'il arrive, la modale doit être déverrouillée : une fenêtre
            # modale restée bloquée rendrait l'application inutilisable.
            if modal:
                self._ui(modal.ensure_unlocked)

    # ── Suppression ────────────────────────────────────────────────────────

    def _myvids_delete(self, v):
        """Supprime la vidéo après DOUBLE confirmation (irréversible)."""
        # Défense en profondeur : le bouton est masqué pour un co-propriétaire,
        # mais on refuse aussi ici — Pod renverrait de toute façon une erreur.
        if self._role_sur_video(v, self._myvids_owner_ids()) == "coproprietaire":
            self._myvids_set_msg("Seul le propriétaire peut supprimer cette vidéo.", T_ALERTE)
            return
        if not messagebox.askyesno(
                "⚠️  Supprimer la vidéo",
                f"Supprimer DÉFINITIVEMENT « {v.get('title')} » ?\n\n"
                "Cette action est IRRÉVERSIBLE (pas de corbeille sur Pod)."):
            return
        if not messagebox.askyesno("Dernière confirmation",
                                   "Confirmez-vous la suppression de cette vidéo ?"):
            return
        self._run(self._do_myvids_delete, v)

    def _do_myvids_delete(self, v):
        """(Thread) DELETE de la vidéo puis retrait des caches et de l'affichage."""
        slug = v.get("slug", "")
        try:
            self.api.delete_video(v)
            if v in self.myvids_videos:
                self.myvids_videos.remove(v)
            self.myvids_selected = None
            self._ui(self._log, f"🗑 Vidéo supprimée : {slug}")
            self._ui(self._myvids_render_detail)
            self._ui(self._myvids_apply_filter)
        except Exception as e:
            self._ui(self._log, f"❌ Suppression {slug} : {e}")
            self._ui(self._myvids_set_msg, f"❌  {message_utilisateur(e)}", T_ERREUR)

    def _build_tab_config(self):
        """Construit l'onglet Configuration (connexion API + choix de l'agent déposant)."""
        frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.tabs["config"] = frame

        ctk.CTkLabel(frame, text="⚙️  Configuration",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", pady=(0, 10))

        # — Connexion API —
        api_box = ctk.CTkFrame(frame)
        api_box.pack(fill="x")
        ctk.CTkLabel(api_box, text="Connexion à l'instance Pod (token personnel ou de service)",
                     font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=3,
                                                           padx=12, pady=(12, 4), sticky="w")

        ctk.CTkLabel(api_box, text="URL :", width=110, anchor="e").grid(row=1, column=0, padx=8, pady=8)
        self.url_entry = ctk.CTkEntry(api_box, width=430)
        self.url_entry.insert(0, self.config_data.get("url", ""))
        self.url_entry.grid(row=1, column=1, padx=8, pady=8, sticky="ew")

        ctk.CTkLabel(api_box, text="Token :", width=110, anchor="e").grid(row=2, column=0, padx=8, pady=8)
        self.token_entry = ctk.CTkEntry(api_box, width=430, show="*")
        if self.token:
            self.token_entry.insert(0, self.token)
        self.token_entry.grid(row=2, column=1, padx=8, pady=8, sticky="ew")

        self.show_token = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(api_box, text="Afficher", variable=self.show_token,
                        command=lambda: self.token_entry.configure(
                            show="" if self.show_token.get() else "*")).grid(row=2, column=2, padx=4)

        btn_row = ctk.CTkFrame(api_box, fg_color="transparent")
        btn_row.grid(row=3, column=1, columnspan=2, padx=8, pady=10, sticky="w")
        ctk.CTkButton(btn_row, text="🔌  Tester & se connecter", fg_color=C_SUCCES,
                      hover_color=C_SUCCES_SURV, command=self._connect).pack(side="left")
        ctk.CTkButton(btn_row, text="🚪  Oublier le token / Se déconnecter", width=260,
                      fg_color=C_NEUTRE, hover_color=C_DESTR_SURV,
                      command=self._forget_token, text_color=T_SUR_NEUTRE).pack(side="left", padx=10)
        api_box.columnconfigure(1, weight=1)

        self.config_msg = ctk.CTkLabel(frame, text="", font=ctk.CTkFont(size=12))
        self.config_msg.pack(anchor="w", pady=4)

        ctk.CTkFrame(frame, height=1, fg_color=C_NEUTRE).pack(fill="x", pady=8)

        # — Propriétaire des vidéos : identifiant universitaire —
        # Plus d'annuaire ici (voir _normaliser_identifiant) : une saisie, un
        # contrôle de format, puis une recherche EXACTE sur la plateforme.
        agent_box = ctk.CTkFrame(frame)
        agent_box.pack(fill="x")
        ctk.CTkLabel(agent_box, text="Propriétaire des vidéos (votre identifiant universitaire)",
                     font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=3,
                                                           padx=12, pady=(12, 2), sticky="w")
        ctk.CTkLabel(agent_box,
                     text="Les vidéos déposées appartiendront à ce compte Pod. "
                          "« Mes vidéos » affiche ses vidéos et celles dont il est co-propriétaire.",
                     text_color=T_SECONDAIRE, font=ctk.CTkFont(size=11)).grid(
            row=1, column=0, columnspan=3, padx=12, pady=(0, 6), sticky="w")

        self.agent_entry = ctk.CTkEntry(agent_box, width=220,
                                        placeholder_text="ex. abc1234d")
        self.agent_entry.grid(row=2, column=0, padx=(12, 8), pady=8, sticky="w")
        actuel = self.config_data.get("agent_username", "")
        if self._identifiant_valide(actuel):
            self.agent_entry.insert(0, actuel)
        self.agent_entry.bind("<Return>", lambda e: self._valider_identifiant_config())
        self.agent_valider_btn = ctk.CTkButton(
            agent_box, text="✅  Valider", width=130,
            fg_color=C_SUCCES, hover_color=C_SUCCES_SURV,
            command=self._valider_identifiant_config)
        self.agent_valider_btn.grid(row=2, column=1, padx=8, pady=8, sticky="w")

        self.agent_msg = ctk.CTkLabel(agent_box, text="", text_color=T_SECONDAIRE,
                                      font=ctk.CTkFont(size=11), wraplength=760,
                                      justify="left")
        self.agent_msg.grid(row=3, column=0, columnspan=3, padx=12, pady=(0, 10), sticky="w")
        agent_box.columnconfigure(2, weight=1)

        # — Aide token —
        help_box = ctk.CTkFrame(frame, fg_color=S_CARTE, corner_radius=8)
        help_box.pack(fill="x", pady=8)
        ctk.CTkLabel(help_box, text="ℹ️  Obtenir le token",
                     font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=14, pady=(10, 2))
        ctk.CTkLabel(
            help_box,
            text="Le token est fourni par le service informatique, ou créé par un "
                 "administrateur dans  <URL>/admin/authtoken/  → « Add token ».\n"
                 "⚠️ Le token hérite des droits du compte associé. Il est stocké chiffré "
                 "dans le coffre-fort de votre système (Keychain / Credential Manager), "
                 "par poste — jamais dans l'application.",
            justify="left", text_color=T_SECONDAIRE, wraplength=820).pack(anchor="w", padx=14, pady=(0, 12))

    def _valider_identifiant_config(self):
        """Onglet Configuration : valide l'identifiant saisi (thread principal),
        puis le recherche sur la plateforme en arrière-plan."""
        texte = self.agent_entry.get()        # lu ICI, jamais depuis un thread
        if not self._identifiant_valide(texte):
            _, message = self._resoudre_identifiant(texte)     # message de format
            self.agent_msg.configure(text=f"❌  {message}", text_color=T_ERREUR)
            return
        self.agent_valider_btn.configure(state="disabled")
        self.agent_msg.configure(text="⏳  Recherche du compte…", text_color=T_SECONDAIRE)

        def travail():
            compte, message = self._resoudre_identifiant(texte)
            self._ui(fin, compte, message)

        def fin(compte, message):
            self.agent_valider_btn.configure(state="normal")
            if compte:
                self._pick_agent(compte)
                self.agent_msg.configure(
                    text=f"✅  Propriétaire des vidéos : {compte.get('username', '')}",
                    text_color=T_SUCCES)
            else:
                self.agent_msg.configure(text=f"❌  {message}", text_color=T_ERREUR)

        self._run(travail)

    def _forget_token(self):
        """Efface le token de ce poste et se déconnecte."""
        cfg.clear_token()
        self.token = ""
        self.api = None
        self.all_users = []
        if hasattr(self, "token_entry"):
            self.token_entry.delete(0, "end")
        self._set_status(False)
        self.config_msg.configure(
            text="🚪  Token effacé de ce poste. Saisissez-le à nouveau pour vous reconnecter.",
            text_color=T_ALERTE)
        self._log("Token effacé du poste — déconnexion.")

    def _connect(self):
        """Lit URL + token saisis et lance la connexion en arrière-plan."""
        url = self.url_entry.get().strip()
        token = self.token_entry.get().strip()
        if not url or not token:
            self.config_msg.configure(text="URL et token requis.", text_color=T_ERREUR)
            return
        self.config_msg.configure(text="⏳  Connexion…", text_color=T_SECONDAIRE)
        self._run(self._do_connect, url, token)

    def _do_connect(self, url, token):
        """(Thread) Teste la connexion à l'instance puis bascule l'UI selon le résultat."""
        try:
            api = PodAPI(url, token)
            count = api.test_connection()
            self._ui(self._on_connected, api, url, token, count)
        except Exception as e:
            self._signaler(self.config_msg, e, "Connexion")
            self._ui(self._set_status, False)
            # Assistant de première utilisation : remonter l'erreur dans sa fenêtre.
            if self._post_connect_err:
                self._ui(self._post_connect_err, str(e))

    def _on_connected(self, api, url, token, count):
        """Connexion réussie : mémorise le client, enregistre le token, charge types et comptes."""
        self.api = api
        self.token = token
        self.config_data["url"] = url
        cfg.save_token(token)
        cfg.save_config(self.config_data)
        self._set_status(True)
        self.config_msg.configure(text=f"✅  Connecté — {count} vidéo(s) accessibles.",
                                  text_color=T_SUCCES)
        self._run(self._load_types)
        self._run(self._load_all_users)
        self._run(self._resolve_vehicle_owner)   # URL Pod de DEPOT (pour la vérif post-504)
        # Assistant de première utilisation : signaler la réussite (ferme l'étape 1).
        if self._post_connect_ok:
            cb = self._post_connect_ok
            self._post_connect_ok = None
            self._post_connect_err = None
            cb()

    def _resolve_vehicle_owner(self):
        """(Thread) Résout l'URL Pod du compte véhicule DEPOT (correspondance
        EXACTE de l'identifiant), pour reconnaître après un 504 la vidéo qu'il
        vient de créer. Aucun repli sur un autre compte."""
        uname = (self.vehicle_username or "").strip()
        if not (self.api and uname):
            return
        try:
            found = None
            for u in (self.api.search_users(uname) or []):
                if (u.get("username", "") or "").strip().lower() == uname.lower():
                    found = u
                    break
            self.vehicle_owner_url = found["url"] if (found and found.get("url")) else ""
            if not self.vehicle_owner_url:
                self._ui(self._log,
                         f"⚠️ Compte véhicule « {uname} » non résolu — récupération après 504 moins précise.")
        except Exception as e:
            self._ui(self._log, f"⚠️ Résolution du compte véhicule impossible : {e}")

    def _auto_connect(self):
        """(Thread) Reconnexion automatique au démarrage si un token est déjà enregistré."""
        try:
            api = PodAPI(self.config_data["url"], self.token)
            count = api.test_connection()
            self._ui(self._on_auto_ok, api, count)
        except Exception:
            self._ui(self._set_status, False)

    def _on_auto_ok(self, api, count):
        """Reconnexion auto réussie : active l'état connecté et charge types et comptes."""
        self.api = api
        self._set_status(True)
        u = self.config_data.get("agent_username", "")
        if u:
            self.agent_lbl.configure(text=f"Dépôt au nom de :\n{u}")
        self._refresh_owner_status()
        self._run(self._load_types)
        self._run(self._load_all_users)
        self._run(self._resolve_vehicle_owner)

    # ── Assistant de première utilisation ────────────────────────────────

    def _first_run_wizard(self):
        """Assistant de PREMIÈRE UTILISATION (aucun token enregistré sur le poste).

        But : guider un enseignant non informaticien en deux étapes simples.
          • Étape 1 (cette fenêtre) : coller le token et se connecter. L'adresse
            de l'instance est déjà renseignée (modifiable si vraiment nécessaire).
          • Étape 2 : le choix du compte déposant s'ouvre automatiquement après la
            connexion (sauf si le compte est détecté avec certitude).
        Un lien « Configurer manuellement » ferme l'assistant et bascule sur
        l'onglet Configuration, pour les cas particuliers.
        """
        win = ctk.CTkToplevel(self)
        win.title("Bienvenue — première utilisation")
        win.geometry("540x470")
        win.resizable(False, False)
        _focus_toplevel(win, self)

        # — Logo (réutilise le mécanisme de la fenêtre À propos) —
        if HAS_PIL:
            try:
                logo_path = resource_path(os.path.join("assets", "logo_ut.png"))
                if os.path.exists(logo_path):
                    pil = PILImage.open(logo_path)
                    W = 150
                    H = round(W * pil.height / pil.width)
                    img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(W, H))
                    win._wiz_img = img   # référence pour éviter le ramasse-miettes
                    card = ctk.CTkFrame(win, fg_color="white", corner_radius=8)
                    card.pack(padx=20, pady=(16, 4))
                    ctk.CTkLabel(card, image=img, text="").pack(padx=10, pady=8)
            except Exception:
                pass

        # — Titre + intro —
        ctk.CTkLabel(win, text="Bienvenue dans Pod Téléverseur",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(4, 0))
        ctk.CTkLabel(win, text="Étape 1 sur 2 — Connexion",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=T_SECONDAIRE).pack(pady=(2, 0))
        ctk.CTkLabel(win, text="Collez le token fourni par le service informatique,\n"
                              "puis cliquez sur « Se connecter ».",
                     justify="center", text_color=T_SECONDAIRE,
                     font=ctk.CTkFont(size=12)).pack(pady=(2, 10))

        form = ctk.CTkFrame(win, fg_color="transparent")
        form.pack(fill="x", padx=24)

        # — Adresse de l'instance (pré-remplie, discrète) —
        ctk.CTkLabel(form, text="Adresse (déjà renseignée) :",
                     text_color=T_DISCRET, font=ctk.CTkFont(size=11)).pack(anchor="w")
        url_entry = ctk.CTkEntry(form)
        url_entry.insert(0, self.config_data.get("url", "https://videos.utoulouse.fr"))
        url_entry.pack(fill="x", pady=(0, 8))

        # — Token (champ principal, masqué + case « Afficher ») —
        ctk.CTkLabel(form, text="Token :",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w")
        token_entry = ctk.CTkEntry(form, show="*", placeholder_text="collez le token ici")
        token_entry.pack(fill="x", pady=(0, 2))
        show_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(form, text="Afficher le token", variable=show_var,
                        font=ctk.CTkFont(size=11),
                        command=lambda: token_entry.configure(show="" if show_var.get() else "*")
                        ).pack(anchor="w", pady=(0, 6))

        # — Message d'état / d'erreur —
        msg = ctk.CTkLabel(win, text="", font=ctk.CTkFont(size=12), wraplength=480)
        msg.pack(pady=(2, 6))

        # — Logique de connexion (réutilise le flux existant via les hooks) —
        def do_connect():
            """Lance la connexion à l'instance depuis l'assistant de démarrage."""
            url = url_entry.get().strip()
            token = token_entry.get().strip()
            if not url or not token:
                msg.configure(text="Merci de coller le token avant de continuer.",
                              text_color=T_ERREUR)
                return
            msg.configure(text="⏳  Connexion…", text_color=T_SECONDAIRE)

            def on_ok():
                # Connexion réussie → on ferme l'étape 1. L'étape 2 (choix du
                # compte déposant) s'ouvrira automatiquement quand la liste des
                # comptes sera chargée (via _after_detection).
                try:
                    win.destroy()
                except Exception:
                    pass
                self._show_tab("upload")

            def on_err(e):
                """Callback d'échec de connexion : affiche l'erreur dans l'assistant."""
                msg.configure(
                    text=f"❌  {message_utilisateur(e)}",
                    text_color=T_ERREUR)
                self._log(f"Assistant de connexion : {e.__class__.__name__}: {e}")

            self._post_connect_ok = on_ok
            self._post_connect_err = on_err
            # Tenir l'onglet Configuration cohérent avec ce qui est saisi ici.
            if hasattr(self, "url_entry"):
                self.url_entry.delete(0, "end"); self.url_entry.insert(0, url)
            if hasattr(self, "token_entry"):
                self.token_entry.delete(0, "end"); self.token_entry.insert(0, token)
            self._run(self._do_connect, url, token)

        def manual():
            # Sortie de secours : annuler l'assistant et aller dans Configuration.
            self._post_connect_ok = None
            self._post_connect_err = None
            try:
                win.destroy()
            except Exception:
                pass
            self._show_tab("config")

        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(fill="x", padx=24, pady=(4, 12))
        ctk.CTkButton(btns, text="Se connecter", height=40,
                      fg_color=C_SUCCES, hover_color=C_SUCCES_SURV,
                      font=ctk.CTkFont(size=14, weight="bold"),
                      command=do_connect).pack(fill="x")
        ctk.CTkButton(btns, text="Configurer manuellement", height=30,
                      fg_color="transparent", text_color=T_DISCRET,
                      hover_color=("gray80", "gray25"),
                      font=ctk.CTkFont(size=11), command=manual).pack(fill="x", pady=(6, 0))

        # La croix de fermeture équivaut à « Configurer manuellement ».
        win.protocol("WM_DELETE_WINDOW", manual)
        # Entrée = se connecter (confort).
        token_entry.bind("<Return>", lambda e: do_connect())

    def _set_status(self, ok: bool):
        """Met à jour l'indicateur de connexion (pastille + libellé) de la barre latérale."""
        self.status_dot.configure(text="🟢" if ok else "🔴")
        self.status_lbl.configure(text="Connecté" if ok else "Non connecté",
                                  text_color=T_SUCCES if ok else "#ef4444")

    # Libellés du menu Discipline : explicite quand la table est vide (un menu
    # vide se prend pour une panne de chargement).
    AUCUNE_DISCIPLINE = "(aucune discipline définie)"
    SANS_DISCIPLINE = "(sans discipline)"

    def _rafraichir_menu_discipline(self):
        titres = sorted(getattr(self, "discipline_map", {}), key=str.lower)
        try:
            if titres:
                self.upload_discipline.configure(values=[self.SANS_DISCIPLINE] + titres)
                if self.upload_discipline.get() not in [self.SANS_DISCIPLINE] + titres:
                    self.upload_discipline.set(self.SANS_DISCIPLINE)
            else:
                self.upload_discipline.configure(values=[self.AUCUNE_DISCIPLINE])
                self.upload_discipline.set(self.AUCUNE_DISCIPLINE)
        except Exception as e:
            self._log(f"Rafraîchissement du menu Discipline : {e}")

    def _discipline_choisie(self) -> str:
        """URL de la discipline choisie, ou "" (lue dans le thread principal)."""
        return (getattr(self, "discipline_map", {}) or {}).get(
            self.upload_discipline.get(), "")

    def _load_types(self):
        """(Thread) Charge les types de vidéo et les sites (champ requis à l'upload)."""
        try:
            self.types = self.api.get_types()
            self.type_map = {t.get("title", f"type-{t.get('id')}"): t.get("url", "")
                             for t in self.types}
            titles = list(self.type_map.keys()) or ["(aucun type)"]
            self._ui(self.type_combo.configure, values=titles)
            self._ui(self.type_combo.set, titles[0])
            # Peupler aussi les menus « type » de l'onglet Mes vidéos (filtre +
            # modification en masse), au cas où l'onglet a été construit avant
            # que les types soient disponibles.
            self._ui(self._myvids_refresh_type_menu)
        except Exception as e:
            self._ui(self._log, f"Impossible de charger les types : {e}")
        # Disciplines : même moment, même mécanique que dans PodAdmin.
        try:
            self.disciplines = self.api.get_disciplines()
            self.discipline_map = {d.get("title"): d.get("url", "")
                                   for d in self.disciplines if d.get("title")}
        except Exception as e:
            self.disciplines, self.discipline_map = [], {}
            self._ui(self._log, f"Impossible de charger les disciplines : {e}")
        self._ui(self._rafraichir_menu_discipline)
        # Sites (champ requis à l'upload sur instance multi-établissements)
        try:
            sites = self.api.get_sites()
            self.site_urls = [s.get("url", "") for s in sites if s.get("url")]
            if self.site_urls:
                names = ", ".join(s.get("name", s.get("domain", "?")) for s in sites)
                self._ui(self._log, f"Site(s) détecté(s) : {names}")
            else:
                self._ui(self._log, "⚠️ Aucun site retourné par /rest/sites/ — l'upload pourrait échouer.")
        except Exception as e:
            self._ui(self._log, f"Impossible de charger les sites : {e}")

    def _load_all_users(self):
        """(Thread) Charge tous les comptes Pod (paginé).

        L'annuaire ne sert plus à choisir le PROPRIÉTAIRE (identifiant saisi),
        mais reste nécessaire aux CO-propriétaires (OwnerPicker) et aux
        libellés de « Mes vidéos »."""
        if not self.api:
            return
        self._ui(self._log, "Chargement des utilisateurs (/rest/users/)…")
        try:
            users = self.api.get_all_users()
            users.sort(key=lambda u: (u.get("username") or "").lower())
            self.all_users = users
            ag = self.config_data.get("agent_username", "")
            if ag:
                self._ui(self._refresh_owner_status)
            elif not self.config_data.get("owner_prompt_seen"):
                # 1ʳᵉ connexion sans compte enregistré et fenêtre jamais montrée :
                # on tente la détection automatique (Piste 1), sinon on ouvrira la
                # fenêtre « Choisissez le compte déposant ». Une seule fois : ensuite
                # l'utilisateur passe par l'onglet Configuration.
                self._detect_token_owner()
            if users:
                self._ui(self._log, f"Utilisateurs chargés : {len(users)}.")
            else:
                self._ui(self._log, "⚠️ /rest/users/ a renvoyé 0 utilisateur — le choix des "
                                    "co-propriétaires sera vide (vérifiez les droits du token).")
        except Exception as e:
            self._ui(self._log, f"⚠️ Chargement des utilisateurs impossible : {message_utilisateur(e)}")

    def _user_label(self, u: dict) -> str:
        """Libellé lisible d'un compte : « identifiant — Prénom Nom »."""
        return f"{u.get('username','?')} — {u.get('first_name','')} {u.get('last_name','')}".strip()

    def _pick_agent(self, user: dict):
        """Enregistre le compte choisi comme propriétaire par défaut des dépôts."""
        self.config_data["agent_username"] = user.get("username", "")
        self.config_data["agent_owner_url"] = user.get("url", "")
        cfg.save_config(self.config_data)
        self.agent_lbl.configure(text=f"Dépôt au nom de :\n{user.get('username','')}")
        self.config_msg.configure(
            text=f"✅  Propriétaire des vidéos : {user.get('username','')}", text_color=T_SUCCES)
        if hasattr(self, "agent_entry"):
            self.agent_entry.delete(0, "end")
            self.agent_entry.insert(0, user.get("username", ""))
        self._refresh_owner_status()   # met à jour l'état dans l'onglet Téléversement

    # ── Détection automatique du propriétaire du token ───────────────────

    # ── Détection / choix du compte déposant ─────────────────────────────

    def _detect_token_owner(self):
        """(Thread) Détecte le propriétaire du token, puis enchaîne sur le thread
        principal (attribution automatique ou fenêtre de choix).

        S'appuie sur PodAPI.whoami(), SÛRE par construction : elle ne renvoie un
        compte que s'il est le seul candidat possible (sinon None). On délègue la
        suite à _after_detection() qui décide quoi faire selon le résultat.
        """
        if not self.api:
            return
        try:
            me = self.api.whoami()      # None si token admin/staff multi-comptes
        except Exception:
            me = None
        self._ui(self._after_detection, me)

    def _after_detection(self, me):
        """(Thread principal Tk) Suite de la détection du propriétaire du token.

        • Piste 1 (fiable, token personnel ne voyant qu'un compte) → on attribue
          automatiquement le compte déposant : zéro clic pour l'enseignant.
        • Tous les autres cas (token admin/staff, ou Piste 2 « probable ») → on
          OUVRE la fenêtre de choix du compte déposant, en pré-remplissant la
          suggestion éventuelle. C'est la solution déterministe : l'utilisateur
          choisit explicitement, aucune mésattribution possible.
        """
        if me and me.get("_detection") == "piste1" and me.get("url"):
            self._apply_detected_owner(me)     # attribution automatique
            return

        # On note que la fenêtre a été présentée, pour ne pas la rouvrir à chaque
        # connexion. L'utilisateur pourra toujours changer le compte plus tard
        # depuis l'onglet Configuration.
        self.config_data["owner_prompt_seen"] = True
        cfg.save_config(self.config_data)

        suggestion = me.get("username", "") if me else ""
        if suggestion:
            self._log(f"Compte probablement propriétaire du token : {suggestion} "
                      f"(pré-rempli dans la fenêtre de choix, à confirmer).")
        self._prompt_pick_owner(suggestion)

    def _apply_detected_owner(self, me: dict):
        """Attribue automatiquement le compte déposant (cas Piste 1, fiable).

        Appelé uniquement quand whoami() est certain de l'identité (le token ne
        voit qu'un seul compte). On enregistre le compte, on met à jour le bandeau
        et on pré-remplit le filtre de l'onglet Configuration.
        """
        username = me.get("username", "")
        url = me.get("url", "")
        self.config_data["agent_username"] = username
        self.config_data["agent_owner_url"] = url
        cfg.save_config(self.config_data)
        self.agent_lbl.configure(text=f"Dépôt au nom de :\n{username}  (détecté)")
        if hasattr(self, "agent_entry"):
            self.agent_entry.delete(0, "end")
            self.agent_entry.insert(0, username)
        self._refresh_owner_status()
        self._log(f"Propriétaire du token détecté automatiquement : {username}.")

    def _prompt_pick_owner(self, suggestion: str = ""):
        """Demande l'identifiant du compte déposant après la 1ʳᵉ connexion
        (étape 2 de l'assistant), quand il n'a pas pu être détecté. Une
        suggestion n'est pré-remplie que si elle a la forme d'un identifiant."""
        if not self.api:
            return

        def trouve(compte):
            self._pick_agent(compte)
            self._log(f"Compte déposant choisi : {compte.get('username', '')}.")
            self._show_tab("upload")   # on enchaîne directement sur le téléversement

        IdentifiantDialog(
            self, on_found=trouve, title="Votre identifiant universitaire",
            intro=("Au nom de quel compte les vidéos seront-elles déposées ?\n"
                   "Saisissez VOTRE identifiant universitaire (ex. abc1234d).\n"
                   "Vous pourrez le changer depuis l'onglet « Configuration »."),
            prefill=suggestion if self._identifiant_valide(suggestion) else "")

    def _build_tab_log(self):
        """Construit l'onglet Journal (zone de texte horodatée + bouton Effacer)."""
        frame = ctk.CTkFrame(self.content, fg_color="transparent")
        self.tabs["log"] = frame
        top = ctk.CTkFrame(frame, fg_color="transparent")
        top.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(top, text="📋  Journal", font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
        ctk.CTkButton(top, text="🗑 Effacer", width=100, fg_color=C_NEUTRE,
                      hover_color=C_NEUTRE_SURV, command=self._clear_log, text_color=T_SUR_NEUTRE).pack(side="right")
        self.log_box = ctk.CTkTextbox(frame, font=ctk.CTkFont(family="Consolas", size=11))
        self.log_box.pack(fill="both", expand=True)
        self.log_box.configure(state="disabled")
        self._log("Application démarrée.")

    def _verifier_maj(self):
        """Lance la vérification de mise à jour en ARRIÈRE-PLAN.

        Appelée peu après le démarrage. Tout se passe dans un thread : si le
        réseau est absent ou le serveur injoignable, l'application n'attend rien
        et l'utilisateur ne voit rien."""
        def travail():
            """(Thread) Interroge le fichier de version publié."""
            def tracer(message):
                """Consigne un échec de vérification dans le Journal.

                Sans cette trace, une panne était indétectable : la vérification
                échouait en silence et l'utilisateur ne voyait simplement jamais
                de bandeau, sans pouvoir en connaître la raison."""
                self._ui(self._log, f"ℹ Mise à jour — {message}")

            # Un SEUL appel réseau, réutilisé pour les deux besoins : comparer
            # les versions, et distinguer "vérification impossible" de "à jour
            # confirmé" (voir plus bas) — sans quoi il aurait fallu interroger
            # le serveur deux fois à chaque démarrage.
            try:
                donnees = maj.recuperer_info(
                    getattr(cfg, "UPDATE_URL", ""),
                    getattr(cfg, "UPDATE_TIMEOUT_S", 5),
                    journal=tracer)
            except Exception as e:
                donnees = None
                self._ui(self._log, f"ℹ Mise à jour — vérification interrompue : {e}")

            try:
                info = maj.etat_mise_a_jour(
                    APP_VERSION,
                    getattr(cfg, "UPDATE_URL", ""),
                    getattr(cfg, "UPDATE_TIMEOUT_S", 5),
                    journal=tracer, infos=donnees)
            except Exception as e:
                info = None              # jamais bloquant
                self._ui(self._log, f"ℹ Mise à jour — vérification interrompue : {e}")

            if info and info.get("obligatoire"):
                # Blocage : PAS de bandeau, une fenêtre modale à la place.
                # Réservé aux cas où continuer serait dangereux — voir maj.py.
                #
                # ⚠️ On MÉMORISE ce blocage localement (voir config.py) : le
                # serveur vient de répondre, en direct, que cette version est
                # bloquée. Ce fait doit désormais tenir MÊME SANS RÉSEAU, pour
                # empêcher qu'une personne notifiée une fois contourne le
                # blocage en coupant simplement sa connexion ensuite.
                cfg.enregistrer_blocage_confirme(
                    APP_VERSION, info.get("version", ""),
                    info.get("url", ""), info.get("notes", ""))
                self._ui(self._bloquer_demarrage, info)
            elif info:
                self._ui(self._afficher_bandeau_maj, info)
            else:
                # `info` est None ici pour DEUX raisons possibles : vérification
                # impossible (réseau coupé, `donnees` est None) OU version
                # confirmée à jour (`donnees` contient une réponse valide). On
                # ne lève le verrou local QUE dans le second cas : lever un
                # verrou parce que le réseau était simplement absent romprait
                # tout le principe du blocage local.
                if donnees and donnees.get("version"):
                    cfg.lever_blocage_local()
                    self._ui(self._log,
                             f"ℹ Mise à jour — version {APP_VERSION} : aucune "
                             f"plus récente.")
                else:
                    self._ui(self._log,
                             "ℹ Mise à jour — vérification impossible "
                             "(réseau indisponible) ; le verrou local, s'il "
                             "existe, n'est pas modifié.")
        self._run(travail)

    # ── Blocage à distance (interrupteur manuel) ────────────────────────────
    #
    # Indépendant de la mise à jour obligatoire : ce mécanisme ne dépend
    # d'aucun numéro de version. Piloté depuis GitHub Actions (workflow
    # "Build installers" → champ "blocage", PARAMÉTRÉ PAR DÉFAUT SUR « ne rien
    # changer »), il ne fait qu'écrire `etat.json` sur le dépôt public — voir
    # BLOCAGE.md. Invisible tant que rien n'est bloqué : ni bandeau ni message.

    def _surveiller_blocage(self):
        """Lit l'état de blocage publié, EN ARRIÈRE-PLAN, puis se replanifie.

        Ne concurrence jamais le démarrage ni l'usage normal : l'appli
        n'attend jamais cette réponse, et une panne réseau ne change rien à
        l'état affiché (voir `_appliquer_blocage`)."""
        if not getattr(cfg, "BLOCAGE_URL", ""):
            return

        def travail():
            try:
                bloque = maj.etat_blocage(
                    getattr(cfg, "BLOCAGE_URL", ""),
                    getattr(cfg, "BLOCAGE_TIMEOUT_S", 5))
            except Exception:
                bloque = None          # jamais bloquant
            self._ui(self._appliquer_blocage, bloque)
        self._run(travail)
        self.after(getattr(cfg, "BLOCAGE_PERIODE_MS", 3600 * 1000), self._surveiller_blocage)

    def _appliquer_blocage(self, bloque):
        """Seule une réponse RÉSEAU RÉELLE change l'état ; `bloque=None`
        (réseau coupé, dépôt injoignable, adresse désactivée…) ne modifie
        RIEN — ni pour bloquer, ni pour débloquer."""
        if bloque is None:
            return
        if bloque != cfg.blocage_distant_actif():
            cfg.enregistrer_blocage_distant(bloque)
        if bloque:
            self._bloquer_application()
        elif self._voile_blocage is not None:
            try:
                self._voile_blocage.destroy()
            except Exception:
                pass
            self._voile_blocage = None
            self._log("ℹ Blocage à distance levé : application de nouveau disponible.")

    def _bloquer_application(self):
        """Recouvre TOUTE la fenêtre : plus rien n'est utilisable, sauf
        Quitter. Message volontairement neutre, sans raison donnée — ce
        n'est ni une fenêtre d'erreur ni un dispositif de licence, seulement
        un interrupteur d'urgence actionné à la main (voir BLOCAGE.md).

        Les fenêtres secondaires ouvertes sont fermées : les laisser par-dessus
        le voile permettrait de continuer à s'en servir.

        Propre au Téléverseur : un lot de téléversement en cours reçoit la
        demande d'arrêt (bouton 🛑). Sans cela, l'envoi continuerait derrière
        le voile, alors que l'utilisation est suspendue. L'arrêt est propre :
        aucune vidéo n'est créée à moitié (voir `_depot_interrompre`)."""
        if getattr(self, "depot_en_cours", False):
            self.depot_interrompu.set()
            self._log("🛑 Blocage à distance : le téléversement en cours est interrompu.")
        for fenetre in self.winfo_children():
            if isinstance(fenetre, ctk.CTkToplevel):
                try:
                    fenetre.destroy()
                except Exception:
                    pass
        if self._voile_blocage is None:
            self._voile_blocage = ctk.CTkFrame(self, fg_color=self.cget("fg_color"),
                                               corner_radius=0)
            centre = ctk.CTkFrame(self._voile_blocage, fg_color="transparent")
            centre.place(relx=0.5, rely=0.5, anchor="center")
            ctk.CTkLabel(centre, text="Pod Téléverseur n'est pas disponible",
                         font=ctk.CTkFont(size=17, weight="bold"),
                         text_color=T_ERREUR).pack()
            ctk.CTkLabel(centre, text="L'utilisation de l'application est suspendue.",
                         font=ctk.CTkFont(size=13), text_color=T_SECONDAIRE
                         ).pack(pady=(8, 24))
            # Seule issue, comme pour la mise à jour obligatoire : on peut
            # toujours quitter proprement (voir l'incident `focus_force` noté
            # dans `_bloquer_demarrage` — même prudence ici : aucun focus forcé).
            ctk.CTkButton(centre, text="Quitter", width=160, height=H_PRINCIPAL,
                          fg_color=C_ACTION, hover_color=C_ACTION_SURV,
                          font=ctk.CTkFont(size=13, weight="bold"),
                          command=self._quitter_depuis_blocage).pack()
            self._log("⚠️ Blocage à distance activé : application suspendue.")
        self._voile_blocage.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._voile_blocage.lift()
        self._voile_blocage.focus_set()

    def _bloquer_demarrage(self, info: dict):
        """Fenêtre modale, SANS échappatoire, en cas de mise à jour obligatoire.

        ⚠️ Différence fondamentale avec `_afficher_bandeau_maj` : ici on ne
        propose pas, on empêche. Réservé aux cas où continuer présenterait un
        vrai risque technique (ex. le mot de passe du compte véhicule a changé
        et un dépôt échouerait en abîmant des fichiers à moitié envoyés) —
        jamais un usage de contrôle d'accès ou de licence : ce n'est ni prévu
        ni fiable pour ça (l'utilisateur garde la main sur son poste et sur le
        fichier `config.json`).

        La fenêtre n'a PAS de bouton "Annuler" qui permettrait de revenir à
        l'application normalement : le seul geste possible est de télécharger
        la mise à jour, ou de QUITTER complètement l'application (bouton
        dédié, et fermeture système normale — voir ⚠️ ci-dessous).

        ⚠️ LEÇON D'UN INCIDENT RÉEL (à ne jamais reproduire) : une première
        version de cette fenêtre appelait `win.focus_force()` en boucle
        toutes les 400 ms, dans l'intention d'empêcher un simple Alt+Tab de
        rendre la fenêtre principale utilisable en tâche de fond. En usage
        réel sur Windows, cette boucle a empêché jusqu'à ALT+F4 de fonctionner
        : la seule issue restante était de tuer le processus depuis le
        gestionnaire de tâches. `grab_set()` SEUL suffit à empêcher toute
        interaction avec le contenu de l'application pendant que la modale
        est affichée — c'est son rôle documenté en Tkinter — sans jamais
        interférer avec les raccourcis et contrôles du système
        d'exploitation lui-même. Un blocage qui empêche même de FERMER
        l'application est un risque plus grave que celui qu'il cherchait à
        éviter : quelqu'un dans une situation urgente doit TOUJOURS pouvoir
        au moins quitter proprement."""
        try:
            win = ctk.CTkToplevel(self)
            win.title("Mise à jour requise")
            # Centrée sur la fenêtre principale : ouverte en (0,0), elle
            # passait inaperçue sur un grand écran, et l'appli semblait
            # simplement figée. 260 px suffisent (mesuré : le bouton
            # « Quitter » finit à 218 px).
            largeur, hauteur = 440, 260
            try:
                self.update_idletasks()
                x = self.winfo_rootx() + max(0, (self.winfo_width() - largeur) // 2)
                y = self.winfo_rooty() + max(0, (self.winfo_height() - hauteur) // 2)
                win.geometry(f"{largeur}x{hauteur}+{x}+{y}")
            except Exception:
                win.geometry(f"{largeur}x{hauteur}")
            win.resizable(False, False)

            # La croix de CETTE fenêtre modale ferme l'application ENTIÈRE
            # (comme le bouton "Quitter" ci-dessous), plutôt que de ne rien
            # faire : ne rien faire du tout laisserait quelqu'un sans AUCUNE
            # réaction visible à son clic, ce qui est déroutant et n'apporte
            # rien — le blocage empêche déjà toute utilisation normale.
            win.protocol("WM_DELETE_WINDOW", self._quitter_depuis_blocage)

            ctk.CTkLabel(win, text="⚠️  Mise à jour requise",
                         font=ctk.CTkFont(size=17, weight="bold"),
                         text_color=T_ERREUR).pack(pady=(24, 8))
            # Message FIXE et NEUTRE : la fenêtre de blocage ne donne jamais
            # la raison de la mise à jour obligatoire. Le champ `notes` de
            # version.json (saisi dans le formulaire de publication) n'est
            # volontairement PAS affiché ici — il reste réservé au bandeau
            # de mise à jour ordinaire.
            ctk.CTkLabel(win, text=MESSAGE_BLOCAGE, wraplength=380, justify="center",
                         font=ctk.CTkFont(size=13)).pack(padx=24, pady=(0, 6))
            ctk.CTkLabel(
                win,
                text=f"Version installée : {APP_VERSION}\n"
                     f"Version requise : {info.get('version', '?')}",
                text_color=T_SECONDAIRE, justify="center",
                font=ctk.CTkFont(size=11)).pack(pady=(0, 16))

            # ⚠️ Le bouton est TOUJOURS présent, jamais conditionnel à
            # `info.get("url")`. Dans le circuit normal, le workflow renseigne
            # toujours l'URL — mais un `version.json` corrompu, modifié à la
            # main, ou un ancien verrou local sans URL enregistrée ne doivent
            # JAMAIS produire une fenêtre bloquante sans la moindre issue :
            # ce serait un blocage total, sans moyen d'agir. On retombe alors
            # sur la page générique des Releases (config.UPDATE_FALLBACK_URL).
            lien = info.get("url") or getattr(
                cfg, "UPDATE_FALLBACK_URL",
                "https://github.com/caine777-data/podteleverseur-releases/releases/latest")
            ctk.CTkButton(
                win, text="Télécharger la mise à jour", height=36,
                fg_color=C_ACTION, hover_color=C_ACTION_SURV,
                font=ctk.CTkFont(size=13, weight="bold"),
                command=lambda u=lien: self._ouvrir_lien_maj(u)
                ).pack(fill="x", padx=32, pady=(0, 8))

            # Issue TOUJOURS disponible : quitter proprement. Un blocage qui
            # empêcherait même de fermer l'application serait plus dangereux
            # que le risque qu'il cherche à prévenir (voir la note ⚠️ plus haut).
            ctk.CTkButton(
                win, text="Quitter", height=30,
                fg_color="transparent", text_color=T_SECONDAIRE,
                hover_color=("gray85", "gray25"),
                font=ctk.CTkFont(size=12),
                command=self._quitter_depuis_blocage
                ).pack(fill="x", padx=32, pady=(0, 4))

            # Mise au premier plan UNE SEULE FOIS, via le helper commun à
            # toutes les fenêtres secondaires de l'appli : `-topmost` retiré
            # après 150 ms, focus donné une fois, puis `grab_set` (qui empêche
            # d'utiliser le contenu de l'application). Sans cette mise au
            # premier plan, la fenêtre pouvait s'ouvrir DERRIÈRE la fenêtre
            # principale : l'appli paraissait figée, sans message visible.
            # ⚠️ Jamais de boucle qui reprend le focus : voir la leçon
            # documentée ci-dessus (ALT+F4 rendu inopérant).
            _focus_toplevel(win, self)

            self._log(f"⚠️ Mise à jour obligatoire : version {APP_VERSION} "
                      f"bloquée (minimum requis : {info.get('version', '?')}).")
        except Exception as e:
            # Un échec de CONSTRUCTION de la fenêtre ne doit jamais planter
            # l'application ni, à l'inverse, la laisser silencieusement
            # utilisable sans que personne ne le sache : on trace fort.
            self._log(f"❌ Impossible d'afficher le blocage de mise à jour "
                      f"obligatoire : {e}")

    def _afficher_bandeau_maj(self, info: dict):
        """Affiche le bandeau annonçant une nouvelle version.

        Volontairement NON bloquant, même quand la version installée est
        périmée : le ton se durcit (couleur, libellé), mais l'application reste
        pleinement utilisable. Empêcher quelqu'un de travailler à un mauvais
        moment coûterait plus cher que le retard de mise à jour."""
        urgent = bool(info.get("urgent"))
        # Couples (clair, sombre) de la palette partagée, et non des teintes
        # écrites seules : elles s'appliqueraient telles quelles aux deux
        # thèmes. Le test de palette ne les verrait d'ailleurs pas, puisqu'elles
        # transitent par une variable plutôt que par un paramètre `*_color`.
        couleur = C_ALERTE if urgent else C_ACTION
        # Texte blanc sur fond coloré : le fond étant le même dans les deux
        # modes, le blanc aussi. Écrit en couple pour respecter la règle — une
        # teinte seule ferait échouer le test de palette, à juste titre.
        blanc = ("#ffffff", "#ffffff")
        survol = ("#e5e7eb", "#e5e7eb")
        titre = ("⚠️  Version obsolète" if urgent
                 else f"⬆️  Version {info['version']} disponible")

        # Un éventuel bandeau précédent est retiré avant d'en poser un nouveau.
        if self.maj_bandeau is not None:
            try:
                self.maj_bandeau.destroy()
            except Exception:
                pass
            self.maj_bandeau = None

        # Le bandeau est créé DIRECTEMENT dans la barre latérale, sans cadre
        # conteneur : c'est ce conteneur transparent qui apparaissait en carré
        # noir sur macOS.
        cadre = ctk.CTkFrame(self.sidebar, fg_color=couleur, corner_radius=6)
        cadre.pack(side="bottom", fill="x", padx=8, pady=(0, 2))
        self.maj_bandeau = cadre
        ctk.CTkLabel(cadre, text=titre, font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=blanc, wraplength=190,
                     justify="left").pack(anchor="w", padx=8, pady=(6, 2))
        if urgent:
            ctk.CTkLabel(cadre,
                         text=f"La version {info['version']} corrige un point important. "
                              "Mettez à jour dès que possible.",
                         font=ctk.CTkFont(size=10), text_color=blanc,
                         wraplength=190, justify="left").pack(anchor="w", padx=8)
        elif info.get("notes"):
            ctk.CTkLabel(cadre, text=info["notes"], font=ctk.CTkFont(size=10),
                         text_color=blanc, wraplength=190,
                         justify="left").pack(anchor="w", padx=8)
        if info.get("url"):
            # Bouton clair sur fond coloré. Les couleurs sont données en
            # hexadécimal plutôt que par leur nom : les noms symboliques
            # (« white », « gray90 ») ne sont pas rendus de la même façon
            # partout, et macOS s'en accommode mal.
            ctk.CTkButton(cadre, text="Télécharger", height=26,
                          fg_color=blanc, text_color=couleur,
                          hover_color=survol,
                          font=ctk.CTkFont(size=11, weight="bold"),
                          command=lambda u=info["url"]: self._ouvrir_lien_maj(u)
                          ).pack(fill="x", padx=8, pady=(6, 8))
        else:
            # Simple marge basse. On ajuste l'espacement du dernier libellé
            # plutôt que d'ajouter un widget vide, qui pouvait laisser une
            # trace visible sur certains systèmes.
            cadre.configure(height=0)      # laisse le contenu fixer la hauteur
        self._log(f"⬆️ Version {info['version']} disponible"
                  + (" (mise à jour recommandée sans délai)." if urgent else "."))

    def _ouvrir_lien_maj(self, url: str):
        """Ouvre la page de téléchargement de la nouvelle version."""
        try:
            import webbrowser
            webbrowser.open(url)
            self._log("Page de téléchargement ouverte.")
        except Exception as e:
            self._log(f"❌ Ouverture du lien de mise à jour : {e}")

    def _quitter_depuis_blocage(self):
        """Ferme l'application ENTIÈRE depuis la fenêtre de blocage obligatoire.

        Le blocage empêche d'UTILISER l'application, jamais de la FERMER :
        c'est le seul geste toujours garanti, quoi qu'il arrive par ailleurs
        (réseau, serveur, formulaire de publication mal rempli). Voir la note
        d'incident dans `_bloquer_demarrage`.

        `self.destroy()` sur la fenêtre RACINE ferme aussi ses enfants
        (dont cette modale) — pas besoin de les détruire un par un."""
        try:
            self.destroy()
        except Exception:
            pass
        try:
            self.quit()          # ceinture et bretelles : sort de mainloop()
        except Exception:
            pass

    def _signaler(self, widget, e: Exception, contexte: str = ""):
        """Affiche une erreur COMPRÉHENSIBLE et journalise le DÉTAIL technique.

        Les utilisateurs de cette application sont des enseignants. Un message
        comme « HTTPSConnectionPool(host=…): Max retries exceeded with url… »
        ne leur dit ni ce qui s'est passé, ni quoi faire — et produit un appel
        au support.

        Un seul appel pour les deux, afin qu'on ne puisse plus faire l'un sans
        l'autre : le détail part au Journal, où il reste disponible pour le
        diagnostic et pour un signalement à `support-pod@utoulouse.fr`.

        Sûre depuis un thread : l'affichage passe par `_ui`.
        """
        try:
            self._ui(widget.configure, text=f"❌  {message_utilisateur(e)}",
                     text_color=T_ERREUR)
        except Exception:
            pass
        detail = f"{e.__class__.__name__}: {e}"
        statut = getattr(e, "status", 0)
        if statut:
            detail += f"  [HTTP {statut}]"
        corps = (getattr(e, "body", "") or "").strip().replace("\n", " ")
        if corps:
            detail += f"  corps={corps[:300]}"
        self._log(f"{contexte + ' : ' if contexte else ''}{detail}")

    def _log(self, msg: str):
        """Ajoute une ligne horodatée au journal."""
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{ts}]  {msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _clear_log(self):
        """Vide le journal."""
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    # ═════════════════════════════════════════════════════════════════════
    #  FENÊTRE « À PROPOS »
    # ═════════════════════════════════════════════════════════════════════

    def _show_about(self):
        """Ouvre une petite fenêtre d'information sur l'application.

        Les informations affichées proviennent des métadonnées du module
        (__version__, __author__, etc.) définies en haut de ce fichier :
        une seule source de vérité à mettre à jour pour changer la version."""
        win = ctk.CTkToplevel(self)
        win.title("À propos")
        win.geometry("440x520")
        win.resizable(False, False)
        _focus_toplevel(win, self)   # amène la fenêtre au premier plan (modale)

        # — En-tête : logo (si présent) sur bandeau blanc —
        if HAS_PIL:
            try:
                logo_path = resource_path(os.path.join("assets", "logo_ut.png"))
                if os.path.exists(logo_path):
                    pil = PILImage.open(logo_path)
                    W = 200
                    H = round(W * pil.height / pil.width)
                    about_img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(W, H))
                    card = ctk.CTkFrame(win, fg_color="white", corner_radius=8)
                    card.pack(padx=20, pady=(20, 8))
                    # On garde une référence sur la fenêtre pour éviter que l'image
                    # soit récupérée par le ramasse-miettes (sinon elle disparaît).
                    win._about_img = about_img
                    ctk.CTkLabel(card, image=about_img, text="").pack(padx=12, pady=12)
            except Exception:
                pass

        # — Nom + version —
        ctk.CTkLabel(win, text="Pod Téléverseur",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(6, 0))
        ctk.CTkLabel(win, text=f"version {__version__}",
                     font=ctk.CTkFont(size=12), text_color=T_SECONDAIRE).pack(pady=(0, 10))

        # — Description courte —
        ctk.CTkLabel(
            win,
            text="Téléversement par lot de vidéos et gestion\n"
                 "de vos vidéos sur l'instance Esup-Pod\n"
                 "de l'Université de Toulouse.",
            justify="center", text_color=T_SECONDAIRE,
            font=ctk.CTkFont(size=12)).pack(pady=(0, 12))

        # — Bloc « Développé par » : les trois auteurs, un par ligne —
        dev = ctk.CTkFrame(win, fg_color=S_CARTE, corner_radius=8)
        dev.pack(fill="x", padx=20, pady=(0, 8))
        ctk.CTkLabel(dev, text="Développé par",
                     font=ctk.CTkFont(size=11, weight="bold")).pack(pady=(8, 2))
        # __author__ contient les auteurs séparés par des virgules : on les
        # affiche un par ligne pour une lecture claire.
        for nom in [a.strip() for a in __author__.split(",") if a.strip()]:
            ctk.CTkLabel(dev, text=nom, font=ctk.CTkFont(size=12),
                         text_color=T_SECONDAIRE).pack(pady=0)
        ctk.CTkLabel(dev, text="", height=4).pack()   # petite marge basse

        # — Informations (lignes « étiquette : valeur ») —
        info = ctk.CTkFrame(win, fg_color=S_CARTE, corner_radius=8)
        info.pack(fill="x", padx=20)
        lignes = [
            ("Établissement", __institution__),
            ("Contact",  __contact__),
            ("Instance", "videos.utoulouse.fr"),
            ("Licence",  __license__),
        ]
        for i, (cle, val) in enumerate(lignes):
            ctk.CTkLabel(info, text=f"{cle} :", anchor="e", width=110,
                         font=ctk.CTkFont(size=11, weight="bold")).grid(
                row=i, column=0, padx=(12, 6), pady=4, sticky="e")
            ctk.CTkLabel(info, text=val, anchor="w",
                         font=ctk.CTkFont(size=11), text_color=T_SECONDAIRE).grid(
                row=i, column=1, padx=(0, 12), pady=4, sticky="w")
        info.columnconfigure(1, weight=1)

        # — Bouton Fermer —
        ctk.CTkButton(win, text="Fermer", width=120,
                      command=win.destroy, fg_color=C_NEUTRE, hover_color=C_NEUTRE_SURV, text_color=T_SUR_NEUTRE).pack(pady=16)

    # ═════════════════════════════════════════════════════════════════════
    #  FENÊTRE « AIDE »
    # ═════════════════════════════════════════════════════════════════════

    def _show_help(self):
        """Ouvre une fenêtre d'aide expliquant, section par section, chaque
        fonction de l'application.

        Le contenu est défini dans une liste `sections` de tuples (titre, texte) :
        pour ajouter ou modifier une rubrique, il suffit d'éditer cette liste —
        la mise en page (titre en gras + paragraphe) est générée automatiquement.
        La fenêtre est défilable (CTkScrollableFrame) pour s'adapter à la longueur
        du texte sans dépasser l'écran.
        """
        win = ctk.CTkToplevel(self)
        win.title("Aide — Pod Téléverseur")
        win.geometry("640x640")
        _focus_toplevel(win, self)

        # Titre de la fenêtre
        ctk.CTkLabel(win, text="❓  Aide — Pod Téléverseur",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(14, 2))
        ctk.CTkLabel(win, text="Guide des fonctions, de la connexion au dépôt des vidéos.",
                     text_color=T_SECONDAIRE, font=ctk.CTkFont(size=12)).pack(pady=(0, 8))

        # Zone défilable qui contiendra toutes les rubriques
        body = ctk.CTkScrollableFrame(win, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        # ── Contenu de l'aide : (titre de section, texte explicatif) ──────────
        # Chaque texte est volontairement rédigé simplement, pour des utilisateurs
        # non informaticiens (enseignants).
        sections = [
            ("1. Premiers pas — se connecter",
             "À la toute première utilisation, un assistant s'ouvre "
             "automatiquement : collez le token fourni par le service informatique "
             "(l'adresse de l'instance est déjà renseignée) et cliquez sur "
             "« Se connecter ». L'assistant enchaîne ensuite sur le choix du compte "
             "déposant.\n\n"
             "Par la suite, la connexion est automatique au lancement. Vous pouvez "
             "toujours revoir ces réglages dans l'onglet « Configuration ».\n\n"
             "Le token remplace l'identifiant et le mot de passe : il donne les mêmes "
             "droits que le compte auquel il est rattaché."),

            ("2. Votre identifiant universitaire (compte déposant)",
             "Les vidéos doivent être déposées au nom d'un compte (leur futur "
             "propriétaire). À la première connexion :\n"
             "• si l'application reconnaît votre compte avec certitude, elle le "
             "sélectionne automatiquement ;\n"
             "• sinon, elle vous demande votre IDENTIFIANT UNIVERSITAIRE : 3 lettres, "
             "4 chiffres et 1 lettre (ex. abc1234d). Elle vérifie qu'un compte de la "
             "plateforme porte exactement cet identifiant.\n\n"
             "Ce choix est mémorisé sur ce poste. Vous pouvez le changer à tout "
             "moment dans l'onglet « Configuration », ce qui est utile si vous "
             "déposez pour plusieurs enseignants."),

            ("3. Ajouter des vidéos",
             "Dans l'onglet « Téléversement », trois façons d'ajouter des fichiers :\n"
             "• « Ajouter des fichiers » : sélection manuelle ;\n"
             "• « Ajouter un dossier » : ajoute toutes les vidéos qu'il contient ;\n"
             "• Glisser-déposer : faites glisser fichiers ou dossiers directement "
             "sur la liste.\n\n"
             "Les doublons sont automatiquement ignorés, et l'application dit combien "
             "de vidéos ont été ajoutées. Réajouter un fichier déjà envoyé depuis "
             "l'ouverture de l'application demande confirmation : le renvoyer "
             "créerait un second exemplaire sur la plateforme. Chaque vidéo apparaît dans "
             "la liste avec un titre modifiable — corrigez-le avant l'envoi si besoin. "
             "Le bouton « Retirer » enlève une vidéo de la liste (sans la supprimer "
             "de votre disque). Après un envoi, « ✅ Retirer les N terminées » enlève "
             "d'un coup les vidéos envoyées et garde celles en échec, pour pouvoir "
             "les relancer."),

            ("4. Réglages communs au lot",
             "Avant de lancer l'envoi, vous définissez des réglages appliqués à "
             "toutes les vidéos du lot :\n"
             "• Type de vidéo (capsule d'enseignement, tutoriel, etc.) ;\n"
             "• Discipline (facultatif) : le domaine d'enseignement de la vidéo, "
             "choisi dans la liste de l'université. Laissez « (sans discipline) » "
             "si aucune ne convient ; « (aucune discipline définie) » signifie que "
             "la liste n'a pas encore été créée sur la plateforme ;\n"
             "• Visibilité : « Brouillon/Privé » (la vidéo reste invisible au public) "
             "ou « Public » ;\n"
             "• « Lancer l'encodage après le téléversement » (cochée par défaut) : "
             "l'application demande l'encodage automatiquement une fois la vidéo "
             "envoyée. Sans encodage, la vidéo n'est pas lisible en ligne ;\n"
             "• Propriétaires additionnels : d'autres comptes Pod autorisés à "
             "modifier les vidéos (facultatif)."),

            ("5. Propriétaire des vidéos (obligatoire)",
             "Vous devez indiquer le compte PROPRIÉTAIRE des vidéos avant tout "
             "envoi, en saisissant son identifiant universitaire via le bouton "
             "« 🎯 Saisir l'identifiant… ». "
             "L'état affiché à côté indique :\n"
             "• « ⚠️ à définir avant l'envoi » (orange) tant qu'aucun compte n'est "
             "choisi ;\n"
             "• « ✅ [nom] » (vert) une fois le compte défini.\n\n"
             "Si vous lancez l'envoi sans propriétaire, ou avec un compte qui n'est "
             "pas un identifiant universitaire, l'application NE téléverse RIEN et "
             "vous le demande : ce blocage est volontaire, pour éviter tout dépôt "
             "au mauvais nom. Ce propriétaire s'applique à tout le lot.\n\n"
             "Les propriétaires additionnels, eux, se choisissent dans la liste de "
             "toutes les personnes de la plateforme (bouton « 👥 Propriétaires "
             "additionnels… »)."),

            ("6. Lancer le téléversement",
             "Cliquez sur « Lancer le téléversement ». Deux barres de progression "
             "apparaissent pendant l'envoi : l'avancement du fichier en cours et "
             "celui du lot. Chaque "
             "vidéo passe par : envoi → (si la case est cochée) lancement de "
             "l'encodage. L'état de chaque vidéo s'affiche en face de son titre.\n\n"
             "Les gros fichiers sont gérés automatiquement (voir la rubrique "
             "suivante) : vous n'avez rien de particulier à faire. Évitez de fermer "
             "l'application pendant un envoi en cours.\n\n"
             "Pendant l'envoi, vous pouvez AJOUTER des vidéos : elles partent à la "
             "suite, dans le même lot. En revanche, « ✕ », « Vider la liste » et "
             "« Retirer les terminées » sont grisés jusqu'à la fin.\n\n"
             "« 🛑 Interrompre » (visible pendant l'envoi) arrête le lot en quelques "
             "secondes, y compris la vidéo en cours. Aucune vidéo n'est créée à "
             "moitié ; « Lancer le téléversement » reprend ensuite les vidéos "
             "restantes."),

            ("7. Gros fichiers (plus de 150 Mo)",
             "Au-delà de 150 Mo, l'application bascule automatiquement sur un envoi "
             "par petits morceaux, plus robuste pour les gros fichiers. C'est "
             "totalement transparent : vous déposez comme d'habitude, la vidéo finit "
             "bien au nom du propriétaire que vous avez choisi.\n\n"
             "Sur un très gros fichier, la finalisation peut prendre plusieurs "
             "minutes côté serveur. Il se peut que l'application affiche "
             "« ⏳ Finalisation côté serveur… vérification » : c'est NORMAL. "
             "Laissez-la travailler (jusqu'à une trentaine de minutes pour les "
             "fichiers les plus lourds) — elle reprend automatiquement la main dès "
             "que la vidéo est prête, puis lance l'encodage. Ne relancez pas l'envoi "
             "pendant ce temps. Si le serveur signale une erreur (codes 502 ou 503) "
             "plutôt qu'un simple délai dépassé, l'attente est limitée à quelques "
             "minutes : dans ce cas, la vidéo n'est en général pas créée.\n\n"
             "Si vous interrompez pendant cette attente, la vidéo passe "
             "« ⚠️ à vérifier » : elle existe peut-être déjà. Elle n'est jamais "
             "renvoyée seule ; le Journal indique le repère que le support pourra "
             "rechercher sur la plateforme.\n\n"
             "Si un message rouge « NON réattribuée » apparaît, la vidéo a bien été "
             "envoyée mais n'a pas pu être remise à son propriétaire : signalez-le au "
             "support, qui pourra corriger le propriétaire."),

            ("8. Mes vidéos — gérer vos vidéos déposées",
             "L'onglet « Mes vidéos » affiche UNIQUEMENT les vidéos du compte "
             "déposant sélectionné — celles dont il est propriétaire, et celles "
             "dont il est co-propriétaire (repérées par 👥 dans la liste). Vous ne "
             "voyez jamais les vidéos des autres. "
             "Cliquez sur « 🔄 Rafraîchir » pour charger la liste, puis sur une vidéo "
             "pour ouvrir son panneau de détail à droite. Après un dépôt réussi, la "
             "liste se met à jour d'elle-même : aussitôt si l'onglet est ouvert, "
             "sinon à sa prochaine ouverture.\n\n"
             "Depuis ce panneau, vous pouvez : renommer la vidéo, changer son "
             "statut (brouillon / public / restreint) et son type, ajouter des "
             "co-propriétaires, gérer les sous-titres, remplacer le fichier vidéo, "
             "ou supprimer la vidéo.\n\n"
             "Depuis la section Classement, « 🏷️ Disciplines… » choisit les "
             "disciplines de la vidéo ; dans Relations, « 🗂 Chaînes et thèmes… » "
             "la place dans des chaînes et leurs thèmes (cocher un thème coche sa "
             "chaîne).\n\n"
             "SÉLECTION MULTIPLE : Ctrl+clic ajoute ou retire une vidéo, Maj+clic "
             "sélectionne une plage, « ☑ Tout sélectionner » prend toutes les "
             "vidéos affichées. Le panneau de droite propose alors d'agir sur tout "
             "le lot : statut, type, disciplines, chaînes et thèmes (en ajout ou en "
             "remplacement), suppression. Une confirmation est toujours demandée. "
             "Les vidéos en co-propriété (👥) sont ignorées par la suppression. "
             "Un clic simple revient à une seule vidéo.\n\n"
             "Les filtres en haut (texte, chaîne, type, statut) permettent de "
             "retrouver rapidement une vidéo quand la liste est longue. Le bouton "
             "« Ouvrir dans le navigateur » affiche la vidéo dans votre navigateur."),

            ("9. Remplacer & ré-encoder un fichier",
             "Cette action remplace le FICHIER VIDÉO d'une vidéo existante, sans rien "
             "changer d'autre : le titre, l'adresse (lien), les chaînes, les droits et "
             "les sous-titres sont conservés. Pratique pour corriger une vidéo déjà "
             "partagée sans avoir à rediffuser un nouveau lien.\n\n"
             "Choisissez le nouveau fichier, confirmez, puis LAISSEZ TRAVAILLER "
             "L'APPLICATION : une fenêtre « Veuillez patienter… » s'affiche avec "
             "l'avancement. Elle bloque volontairement le reste de l'application, car "
             "toute autre manipulation interromprait l'envoi. Ne la fermez pas : elle "
             "se déverrouille toute seule à la fin, et le ré-encodage est lancé "
             "automatiquement.\n\n"
             "Attention : l'ancien fichier est définitivement écrasé. Pendant le "
             "ré-encodage, la vidéo peut apparaître indisponible quelques minutes sur "
             "le site : c'est normal."),

            ("10. Supprimer une vidéo",
             "Le bouton « Supprimer cette vidéo » (zone sensible, en rouge) efface "
             "DÉFINITIVEMENT la vidéo de la plateforme. Il n'y a pas de corbeille sur "
             "Pod : une suppression est irréversible. Une double confirmation est "
             "demandée. En cas de doute, préférez passer la vidéo en « brouillon » : "
             "elle devient invisible sans être perdue.\n\n"
             "Seul le PROPRIÉTAIRE peut supprimer une vidéo. Sur une vidéo dont "
             "vous êtes co-propriétaire (👥), le bouton n'apparaît pas : vous pouvez "
             "la modifier, mais pas la supprimer."),

            ("11. En cas d'échec réseau (relance)",
             "Sur les gros fichiers, l'envoi peut échouer à cause d'une coupure "
             "réseau passagère — ce n'est pas un défaut de l'application. Trois "
             "protections existent :\n"
             "• Relance automatique : chaque vidéo est réessayée jusqu'à 3 fois "
             "(le statut affiche « ⟳ essai 2 »). Ne vous inquiétez donc pas d'un "
             "échec momentané, l'application retente seule.\n"
             "• Bascule automatique : si le serveur coupe un envoi direct (fréquent "
             "sur une connexion lente, même pour un fichier de moins de 150 Mo), "
             "l'application renvoie la vidéo par petits morceaux, sans rien vous "
             "demander (le statut affiche « ⟳ envoi par morceaux »).\n"
             "• Bouton « 🔄 Relancer les échecs » : s'il reste des vidéos en échec "
             "après le lot, ce bouton apparaît avec leur nombre. Il ne retente que "
             "les échecs (les vidéos déjà réussies ne sont pas renvoyées) et "
             "disparaît quand tout est passé. Une vidéo « NON réattribuée » ou "
             "« ⚠️ à vérifier » n'est jamais renvoyée : elle existe (peut-être) déjà "
             "sur la plateforme, et la renvoyer en créerait une seconde (voir la "
             "rubrique 7). Une vidéo « ⏹ interrompu » n'est pas un échec : "
             "« Lancer le téléversement » la reprend.\n\n"
             "Si une même vidéo échoue à chaque fois, c'est probablement une limite "
             "plus dure (taille, réseau de l'établissement) : signalez-le au "
             "support."),

            ("12. Journal",
             "L'onglet « Journal » conserve l'historique horodaté des opérations "
             "(connexions, envois, encodages, erreurs). En cas de problème, c'est la "
             "première chose à consulter. Le bouton « Effacer » vide l'affichage "
             "(sans effet sur les vidéos déjà déposées)."),

            ("13. Sécurité du token",
             "Votre token est stocké dans le coffre-fort sécurisé de votre système "
             "(Gestionnaire d'identifiants Windows / Trousseau macOS), jamais en clair "
             "ni dans l'application. Il reste sur ce poste : copier le programme sur un "
             "autre ordinateur n'emporte aucun identifiant.\n\n"
             "Le bouton « Oublier le token / Se déconnecter » (onglet Configuration) "
             "efface le token de ce poste."),

            ("14. Mises à jour",
             "Au démarrage, l'application vérifie s'il existe une version plus "
             "récente. Si c'est le cas, un bandeau apparaît en bas de la barre "
             "latérale avec un bouton « Télécharger » : installez la nouvelle "
             "version quand cela vous convient.\n\n"
             "Certaines mises à jour sont OBLIGATOIRES : une fenêtre « Mise à jour "
             "requise » s'affiche alors dès l'ouverture et l'application ne peut "
             "plus être utilisée. Cliquez sur « Télécharger la mise à jour », "
             "installez-la, puis relancez l'application. « Quitter » ferme "
             "l'application si vous devez le faire plus tard. Cette fenêtre "
             "réapparaît tant que la mise à jour n'est pas installée, même sans "
             "connexion internet."),

            ("15. Mode clair ou sombre",
             "Le bouton en bas de la barre latérale bascule entre le mode clair et "
             "le mode sombre. Votre choix est mémorisé pour les prochaines "
             "ouvertures."),

            ("16. Problèmes courants",
             "• « 0 utilisateur » lors du chargement des comptes : le token n'a pas le "
             "droit de lister les utilisateurs. Le dépôt reste possible, mais la "
             "recherche de comptes est limitée — voyez avec le service informatique.\n"
             "• Erreur de connexion : vérifiez l'adresse de l'instance et la validité "
             "du token.\n"
             "• La vidéo n'est pas lisible après l'envoi : vérifiez que la case "
             "d'encodage était cochée ; l'encodage peut prendre du temps côté serveur.\n\n"
             "Pour tout problème persistant : support-pod@utoulouse.fr."),
        ]

        # Rendu automatique des sections (titre en gras + paragraphe justifié).
        for titre, texte in sections:
            bloc = ctk.CTkFrame(body, fg_color=S_CARTE, corner_radius=8)
            bloc.pack(fill="x", pady=6)
            ctk.CTkLabel(bloc, text=titre, anchor="w",
                         font=ctk.CTkFont(size=14, weight="bold")).pack(
                fill="x", padx=12, pady=(10, 2))
            ctk.CTkLabel(bloc, text=texte, anchor="w", justify="left",
                         wraplength=560, text_color=T_SECONDAIRE,
                         font=ctk.CTkFont(size=12)).pack(fill="x", padx=12, pady=(0, 12))

        # Bouton Fermer
        ctk.CTkButton(win, text="Fermer", width=120,
                      command=win.destroy, fg_color=C_NEUTRE, hover_color=C_NEUTRE_SURV, text_color=T_SUR_NEUTRE).pack(pady=(0, 14))



# Reprises de PodAdmin (sélecteurs éprouvés, même comportement).

def _norm_url(u) -> str:
    """URL d'une relation, sans barre finale ; accepte un dict {url: …}."""
    if isinstance(u, dict):
        u = u.get("url", "")
    return str(u or "").rstrip("/")


def calculer_chaines_themes(actuelles, actuels_themes, chaines, themes, mode,
                            themes_disponibles: bool = True):
    """Chaînes et thèmes FINAUX d'une vidéo lors d'une affectation en lot.

    Renvoie (chaines_finales, themes_finaux) ; `themes_finaux` vaut None quand
    le champ `theme` ne doit pas être modifié.

      • « ajouter » : ajout aux chaînes et thèmes de CHAQUE vidéo (ils
        diffèrent d'une vidéo à l'autre). Sans thème choisi, les thèmes
        existants ne sont pas touchés.
      • « remplacer » : la vidéo reçoit EXACTEMENT la sélection. Cela purge
        aussi les thèmes des chaînes retirées — auparavant, seules les chaînes
        étaient remplacées, et une vidéo sortie d'une chaîne gardait un thème
        pointant vers une chaîne qu'elle n'avait plus.

    ⚠️ Si les thèmes n'ont pas pu être chargés (`themes_disponibles` faux),
    personne n'a pu en choisir : « remplacer » ne touche alors PAS au champ
    `theme`, au lieu de tous les effacer sans qu'on ait pu les voir.

    Dédoublonnage sur l'URL sans barre finale, en gardant la première forme
    rencontrée : « …/1 » et « …/1/ » désignent la même chaîne."""
    def fusion(*listes):
        vues, sortie = set(), []
        for liste in listes:
            for u in liste or []:
                n = _norm_url(u)
                if n and n not in vues:
                    vues.add(n)
                    sortie.append(u)
        return sortie

    if mode == "ajouter":
        finales = fusion(actuelles, chaines)
        themes_finaux = fusion(actuels_themes, themes) if themes else None
    else:
        finales = fusion(chaines)
        themes_finaux = fusion(themes) if themes_disponibles else None
    return finales, themes_finaux


class ChainesThemesPicker(ctk.CTkToplevel):
    """Chaînes ET thèmes d'une vidéo, dans une seule fenêtre.

    Chaque chaîne est suivie de ses thèmes, en retrait. Deux règles de
    cohérence — les mêmes que l'onglet Chaînes & thèmes :
      • cocher un thème coche aussi sa chaîne : un thème n'a de sens que si la
        vidéo figure dans la chaîne qui le porte ;
      • décocher une chaîne retire ses thèmes.

    `on_done(urls_chaines, urls_themes)` au Valider. La sélection REMPLACE les
    chaînes et thèmes de la vidéo.

    ⚠️ Une chaîne ou un thème déjà présents sur la vidéo mais ABSENTS des
    listes chargées (chaîne invisible, liste incomplète) sont conservés tels
    quels : les perdre en silence au premier Valider serait une destruction
    que rien à l'écran ne laisse deviner."""

    def __init__(self, master, channels, themes, on_done,
                 title="Chaînes et thèmes", chaines_pre=None, themes_pre=None,
                 consigne="Cochez les chaînes où la vidéo doit apparaître, et "
                          "si besoin ses thèmes. Cocher un thème coche sa chaîne."):
        super().__init__(master)
        self.on_done = on_done
        self.channels = sorted(channels or [], key=lambda c: str(c.get("title", "")).lower())
        self._url_chaine = {_norm_url(c.get("url")): c.get("url", "") for c in self.channels}
        self._titre_chaine = {_norm_url(c.get("url")): c.get("title", "?") for c in self.channels}
        self.themes_par_chaine: dict[str, list] = {}
        self._url_theme, self._chaine_du_theme = {}, {}
        for t in themes or []:
            n_t, n_c = _norm_url(t.get("url")), _norm_url(t.get("channel"))
            if not n_t:
                continue
            self._url_theme[n_t] = t.get("url", "")
            self._chaine_du_theme[n_t] = n_c
            self.themes_par_chaine.setdefault(n_c, []).append(t)
        for liste in self.themes_par_chaine.values():
            liste.sort(key=lambda t: str(t.get("title", "")).lower())

        self.chaines = set()
        for u in chaines_pre or []:
            n = _norm_url(u)
            self._url_chaine.setdefault(n, u if isinstance(u, str) else n)
            self.chaines.add(n)
        self.themes = set()
        for u in themes_pre or []:
            n = _norm_url(u)
            self._url_theme.setdefault(n, u if isinstance(u, str) else n)
            self.themes.add(n)

        self.title(title)
        self.geometry("500x580")
        _focus_toplevel(self, master)
        ctk.CTkLabel(self, text=consigne, justify="left",
                     wraplength=460).pack(padx=14, pady=(14, 8), anchor="w")
        self.filter = ctk.CTkEntry(self, placeholder_text="🔍 chaîne ou thème…")
        self.filter.pack(fill="x", padx=14)
        self.filter.bind("<KeyRelease>", lambda e: self._render_differe())
        self.listbox = ctk.CTkScrollableFrame(self, height=360, fg_color=S_CARTE)
        self.listbox.pack(fill="both", expand=True, padx=14, pady=8)
        self.chosen_lbl = ctk.CTkLabel(self, text="", text_color=T_SECONDAIRE,
                                       wraplength=460, justify="left")
        self.chosen_lbl.pack(padx=14, anchor="w")
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=10)
        self.bouton_defaut = ctk.CTkButton(
            btns, text="Valider", fg_color=C_SUCCES, hover_color=C_SUCCES_SURV,
            command=self._validate)
        self.bouton_defaut.pack(side="right")
        ctk.CTkButton(btns, text="Annuler", fg_color=C_NEUTRE, hover_color=C_NEUTRE_SURV,
                      command=self.destroy, text_color=T_SUR_NEUTRE).pack(side="right", padx=8)
        self._render()
        self._update_chosen()

    def _render_differe(self):
        job = getattr(self, "_render_job", None)
        if job:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._render_job = self.after(FILTER_DELAY_MS, self._render)

    def _render(self):
        """Chaînes filtrées, chacune suivie de ses thèmes en retrait.

        Une chaîne s'affiche si son titre OU l'un de ses thèmes correspond au
        filtre ; dans le second cas, seuls les thèmes correspondants sont
        montrés, pour qu'on retrouve un thème sans connaître sa chaîne."""
        flt = self.filter.get().strip().lower()
        for w in self.listbox.winfo_children():
            w.destroy()
        affiches = 0
        for c in self.channels:
            n_c = _norm_url(c.get("url"))
            titre = str(c.get("title", "?"))
            themes = self.themes_par_chaine.get(n_c, [])
            chaine_ok = not flt or flt in titre.lower()
            themes_vus = themes if chaine_ok else [
                t for t in themes if flt in str(t.get("title", "")).lower()]
            if not chaine_ok and not themes_vus:
                continue
            affiches += 1
            sel = n_c in self.chaines
            ctk.CTkButton(self.listbox, text=("☑  " if sel else "☐  ") + titre,
                          anchor="w", height=H_NORMAL,
                          fg_color=S_SELECTION if sel else "transparent",
                          text_color=("gray10", "gray90"), hover_color=("gray75", "gray28"),
                          font=ctk.CTkFont(size=12, weight="bold"),
                          command=lambda n=n_c: self._toggle_chaine(n)).pack(fill="x", pady=1)
            for t in themes_vus:
                n_t = _norm_url(t.get("url"))
                sel_t = n_t in self.themes
                ctk.CTkButton(self.listbox,
                              text=("☑  " if sel_t else "☐  ") + "↳ " + str(t.get("title", "?")),
                              anchor="w", height=H_COMPACT,
                              fg_color=S_SELECTION if sel_t else "transparent",
                              text_color=T_SECONDAIRE, hover_color=("gray75", "gray28"),
                              font=ctk.CTkFont(size=11),
                              command=lambda nt=n_t: self._toggle_theme(nt)
                              ).pack(fill="x", padx=(28, 0), pady=0)
        if not affiches:
            ctk.CTkLabel(self.listbox, text="Aucune chaîne.",
                         text_color=T_SECONDAIRE).pack(pady=8)

    def _toggle_chaine(self, n_c: str):
        if n_c in self.chaines:
            self.chaines.discard(n_c)
            # Décocher une chaîne retire ses thèmes.
            self.themes = {t for t in self.themes if self._chaine_du_theme.get(t) != n_c}
        else:
            self.chaines.add(n_c)
        self._render()
        self._update_chosen()

    def _toggle_theme(self, n_t: str):
        if n_t in self.themes:
            self.themes.discard(n_t)
        else:
            self.themes.add(n_t)
            n_c = self._chaine_du_theme.get(n_t)
            if n_c:
                self.chaines.add(n_c)       # cocher un thème coche sa chaîne
        self._render()
        self._update_chosen()

    def _update_chosen(self):
        nc, nt = len(self.chaines), len(self.themes)
        if not nc:
            texte = "Sélection : aucune chaîne"
        else:
            noms = sorted(self._titre_chaine.get(n, "(chaîne non listée)") for n in self.chaines)
            texte = f"Sélection : {nc} chaîne(s), {nt} thème(s) — " + ", ".join(noms[:4])
            if nc > 4:
                texte += "…"
        self.chosen_lbl.configure(text=texte)

    def _validate(self):
        """Renvoie les URLs d'origine. Un thème dont la chaîne n'est plus
        cochée est écarté ; un thème dont la chaîne est INCONNUE (hors des
        listes chargées) est conservé, faute de pouvoir juger."""
        chaines = [self._url_chaine.get(n, n) for n in sorted(self.chaines)]
        themes = [self._url_theme.get(n, n) for n in sorted(self.themes)
                  if self._chaine_du_theme.get(n) is None
                  or self._chaine_du_theme.get(n) in self.chaines]
        try:
            self.on_done(chaines, themes)
        finally:
            self.destroy()


class ChannelPicker(ctk.CTkToplevel):
    """Sélecteur multi-chaînes (sur le modèle d'OwnerPicker).
    `channels` : liste de dicts {url, title}. `on_done(urls, labels)` au Valider."""

    def __init__(self, master, channels, on_done, title="Chaînes",
                 preselected: dict | None = None,
                 consigne: str = "Cochez les chaînes où la vidéo doit apparaître.",
                 vide: str = "Aucune chaîne."):
        """Construit la fenêtre de sélection (liste à cocher + filtre).

        Générique malgré son nom : elle coche n'importe quels éléments
        `{url, title}`. Elle sert aussi aux DISCIPLINES, qui ont la même forme
        et qu'une vidéo peut porter en plusieurs exemplaires — d'où les
        paramètres `consigne` et `vide`, plutôt qu'une seconde fenêtre
        quasi identique à maintenir."""
        super().__init__(master)
        self._vide = vide
        self.on_done = on_done
        self.channels = channels or []
        self.selected: dict[str, str] = dict(preselected or {})   # url → titre
        self.title(title)
        self.geometry("460x520")
        _focus_toplevel(self, master)

        ctk.CTkLabel(self, text=consigne, justify="left",
                     wraplength=420).pack(padx=14, pady=(14, 8), anchor="w")

        self.filter = ctk.CTkEntry(self, placeholder_text="🔍 titre…")
        self.filter.pack(fill="x", padx=14)
        # Temporisation : évite de reconstruire toute la liste à chaque caractère.
        self.filter.bind("<KeyRelease>", lambda e: self._render_differe())

        self.listbox = ctk.CTkScrollableFrame(self, height=320, fg_color=S_CARTE)
        self.listbox.pack(fill="both", expand=True, padx=14, pady=8)

        self.chosen_lbl = ctk.CTkLabel(self, text="Sélection : aucune", text_color=T_SECONDAIRE,
                                       wraplength=420, justify="left")
        self.chosen_lbl.pack(padx=14, anchor="w")

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=10)
        # Désigné comme action par défaut : Entrée déclenche « Valider ».
        self.bouton_defaut = ctk.CTkButton(
            btns, text="Valider", fg_color=C_SUCCES, hover_color=C_SUCCES_SURV,
            command=self._validate)
        self.bouton_defaut.pack(side="right")
        ctk.CTkButton(btns, text="Annuler", fg_color=C_NEUTRE, hover_color=C_NEUTRE_SURV,
                      command=self.destroy, text_color=T_SUR_NEUTRE).pack(side="right", padx=8)

        self._render()
        self._update_chosen()

    def _render_differe(self):
        """Replanifie l'affichage après une courte pause de frappe (voir
        App._debounce) : une seule reconstruction au lieu d'une par caractère."""
        job = getattr(self, "_render_job", None)
        if job:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._render_job = self.after(FILTER_DELAY_MS, self._render)

    def _render(self):
        """Affiche la liste filtrée (cases à cocher)."""
        flt = self.filter.get().strip().lower()
        for w in self.listbox.winfo_children():
            w.destroy()
        matches = [c for c in self.channels
                   if not flt or flt in (c.get("title", "")).lower()]
        for c in matches:
            url = c.get("url", "")
            sel = url in self.selected
            ctk.CTkButton(self.listbox, text=("☑  " if sel else "☐  ") + c.get("title", "?"),
                          anchor="w", height=28,
                          fg_color=S_SELECTION if sel else "transparent",
                          text_color=("gray10", "gray90"), hover_color=("gray75", "gray28"),
                          font=ctk.CTkFont(size=12),
                          command=lambda cc=c: self._toggle(cc)).pack(fill="x", pady=1)
        if not matches:
            ctk.CTkLabel(self.listbox, text=self._vide, text_color=T_SECONDAIRE).pack(pady=8)

    def _toggle(self, c: dict):
        """Coche/décoche une chaîne dans la sélection."""
        url = c.get("url", "")
        if not url:
            return
        if url in self.selected:
            del self.selected[url]
        else:
            self.selected[url] = c.get("title", "?")
        self._render()
        self._update_chosen()

    def _update_chosen(self):
        """Met à jour le libellé récapitulant la sélection courante."""
        if self.selected:
            self.chosen_lbl.configure(text="Sélection : " + ", ".join(self.selected.values()),
                                      text_color=T_SUCCES)
        else:
            self.chosen_lbl.configure(text="Sélection : aucune", text_color=T_SECONDAIRE)

    def _validate(self):
        """Renvoie la sélection à l'appelant (on_done) puis ferme la fenêtre."""
        self.on_done(list(self.selected.keys()), list(self.selected.values()))
        self.destroy()


# ════════════════════════════════════════════════════════════════════════════
#  FENÊTRE : sélection de propriétaires additionnels
# ════════════════════════════════════════════════════════════════════════════

def _focus_toplevel(win, master=None):
    """Amène une fenêtre secondaire au premier plan, lui donne le focus et la
    rend modale (focus capturé jusqu'à fermeture). Corrige le cas où une
    CTkToplevel s'ouvre derrière la fenêtre principale.
    Les appels sont légèrement différés (after) car la fenêtre n'est pas encore
    dessinée à l'instant de sa création."""
    try:
        if master is not None:
            win.transient(master)          # la fenêtre reste au-dessus de son parent
    except Exception:
        pass
    win.lift()
    win.attributes("-topmost", True)        # passe au-dessus, le temps de s'afficher
    # On retire 'topmost' juste après (sinon elle resterait au-dessus de TOUTES
    # les applications), puis on capture le focus.
    win.after(150, lambda: (win.attributes("-topmost", False), win.focus_force()))
    win.after(200, lambda: win.grab_set())  # modale : bloque la fenêtre principale


class ProgressModal(ctk.CTkToplevel):
    """Fenêtre MODALE de progression, pour les opérations longues à ne pas
    interrompre (remplacement d'un fichier source + ré-encodage).

    Pourquoi une modale : pendant un remplacement, toute autre manipulation
    (changer de vidéo, actualiser la liste, relancer l'action…) peut couper
    l'envoi en cours. Cette fenêtre capture le focus (`grab_set`) et neutralise
    la croix de fermeture tant que l'opération tourne : l'utilisateur ne peut
    donc rien faire d'autre que patienter, et voit l'avancement.

    Cycle de vie :
      • création (thread principal) → `set_phase()` / `set_progress()` pendant
        le travail (appelés depuis le thread via App._ui) ;
      • `finish(ok, message)` en fin d'opération : la fenêtre se déverrouille,
        affiche le résultat et propose un bouton « Fermer ».
    """

    def __init__(self, master, title: str = "Opération en cours",
                 intro: str = "", subtitle: str = ""):
        """Construit la fenêtre modale de progression (titre, sous-titre, étape initiale)."""
        super().__init__(master)
        self.master_app = master
        self._done = False                 # opération terminée ? (pilote la fermeture)
        self.title(title)
        self.geometry("470x250")
        self.resizable(False, False)
        # Tant que l'opération tourne, la croix de fermeture est NEUTRALISÉE :
        # fermer la fenêtre laisserait un envoi orphelin en arrière-plan.
        self.protocol("WM_DELETE_WINDOW", self._on_close_attempt)

        ctk.CTkLabel(self, text="⏳  Veuillez patienter…",
                     font=ctk.CTkFont(size=17, weight="bold")).pack(
            anchor="w", padx=20, pady=(18, 2))

        self.subtitle_lbl = ctk.CTkLabel(
            self, text=subtitle, text_color=T_SECONDAIRE, font=ctk.CTkFont(size=12),
            wraplength=420, justify="left")
        self.subtitle_lbl.pack(anchor="w", padx=20, pady=(0, 8))

        # Phase courante (envoi / finalisation / ré-encodage…)
        self.phase_lbl = ctk.CTkLabel(self, text=intro, font=ctk.CTkFont(size=13),
                                      wraplength=420, justify="left")
        self.phase_lbl.pack(anchor="w", padx=20, pady=(0, 6))

        # Barre d'avancement (même code couleur que le téléversement par lot)
        self.bar = ctk.CTkProgressBar(self, progress_color=C_SUCCES)
        self.bar.pack(fill="x", padx=20)
        self.bar.set(0)

        # Détail chiffré sous la barre (Mo envoyés / Mo total)
        self.detail_lbl = ctk.CTkLabel(self, text="", text_color=T_SECONDAIRE,
                                       font=ctk.CTkFont(size=11))
        self.detail_lbl.pack(anchor="w", padx=20, pady=(4, 0))

        # Rappel : ne pas interrompre (masqué une fois l'opération finie)
        self.warn_lbl = ctk.CTkLabel(
            self, text="Ne fermez pas cette fenêtre et ne lancez pas d'autre action : "
                       "cela interromprait l'envoi.",
            text_color=T_ALERTE, font=ctk.CTkFont(size=11),
            wraplength=420, justify="left")
        self.warn_lbl.pack(anchor="w", padx=20, pady=(10, 0))

        # Bouton de fermeture : désactivé jusqu'à la fin de l'opération.
        self.close_btn = ctk.CTkButton(self, text="Fermer", width=110,
                                       state="disabled", command=self._close_now, fg_color=C_NEUTRE, hover_color=C_NEUTRE_SURV, text_color=T_SUR_NEUTRE)
        self.close_btn.pack(anchor="e", padx=20, pady=(10, 14))

        _focus_toplevel(self, master)      # au premier plan + modale (grab_set)

    # ── Mises à jour (appelées depuis le thread de travail via App._ui) ────

    def set_phase(self, text: str, color: str = None):
        """Change le libellé de l'étape en cours (envoi, finalisation, encodage…)."""
        if not self.winfo_exists():
            return
        self.phase_lbl.configure(text=text, **({"text_color": color} if color else {}))

    def set_progress(self, fraction: float, detail: str = ""):
        """Positionne la barre (0 à 1) et le détail chiffré sous la barre."""
        if not self.winfo_exists():
            return
        self.bar.set(max(0.0, min(1.0, fraction)))
        if detail:
            self.detail_lbl.configure(text=detail)

    def set_indeterminate(self, on: bool = True):
        """Bascule en animation continue quand l'avancement n'est pas mesurable
        (finalisation côté serveur : on ne sait pas combien de temps il reste)."""
        if not self.winfo_exists():
            return
        try:
            if on:
                self.bar.configure(mode="indeterminate")
                self.bar.start()
            else:
                self.bar.stop()
                self.bar.configure(mode="determinate")
        except Exception:
            pass

    def finish(self, ok: bool, message: str):
        """Fin de l'opération : déverrouille la fenêtre et affiche le résultat."""
        if not self.winfo_exists():
            return
        self._done = True
        self.set_indeterminate(False)
        self.bar.set(1.0 if ok else self.bar.get())
        self.phase_lbl.configure(text=("✅  " if ok else "❌  ") + message,
                                 text_color=T_SUCCES if ok else "#ef4444")
        self.warn_lbl.configure(text="Opération terminée. Vous pouvez fermer cette fenêtre.",
                                text_color=T_SECONDAIRE)
        self.close_btn.configure(state="normal")
        try:
            self.grab_release()            # rend la main à la fenêtre principale
        except Exception:
            pass

    def ensure_unlocked(self):
        """FILET DE SÉCURITÉ : déverrouille la fenêtre si l'opération s'est
        terminée sans passer par `finish()` (voie de sortie imprévue). Sans ce
        garde-fou, une modale restée « grabbed » figerait toute l'application."""
        if not self.winfo_exists() or self._done:
            return
        self.finish(False, "Opération terminée de façon inattendue. "
                           "Vérifiez la vidéo sur le site et le Journal.")

    # ── Fermeture ─────────────────────────────────────────────────────────

    def _on_close_attempt(self):
        """Clic sur la croix : ignoré tant que l'opération n'est pas terminée."""
        if self._done:
            self._close_now()

    def _close_now(self):
        """Ferme réellement la fenêtre (après la fin de l'opération)."""
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


class IdentifiantDialog(ctk.CTkToplevel):
    """Saisie de l'identifiant universitaire du propriétaire des vidéos.

    Le format est contrôlé tout de suite (message immédiat) ; la recherche du
    compte sur la plateforme se fait en arrière-plan, et la fenêtre ne se
    ferme que si le compte existe."""

    def __init__(self, master: App, on_found, title="Propriétaire des vidéos",
                 intro: str = "", prefill: str = ""):
        super().__init__(master)
        self.master_app = master
        self.on_found = on_found
        self.title(title)
        self.geometry("470x250")
        _focus_toplevel(self, master)

        ctk.CTkLabel(self, text=intro or "Saisissez votre identifiant universitaire.",
                     justify="left").pack(padx=14, pady=(14, 8), anchor="w")
        self.entry = ctk.CTkEntry(self, width=220, placeholder_text="ex. abc1234d")
        self.entry.pack(padx=14, anchor="w")
        if prefill:
            self.entry.insert(0, prefill)
        self.entry.bind("<Return>", self._valider)
        self.msg = ctk.CTkLabel(self, text="", wraplength=440, justify="left",
                                text_color=T_SECONDAIRE, font=ctk.CTkFont(size=11))
        self.msg.pack(padx=14, pady=(6, 0), anchor="w")

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=12, side="bottom")
        self.btn = ctk.CTkButton(btns, text="Valider", fg_color=C_SUCCES,
                                 hover_color=C_SUCCES_SURV, command=self._valider)
        self.btn.pack(side="right")
        ctk.CTkButton(btns, text="Annuler", fg_color=C_NEUTRE, hover_color=C_NEUTRE_SURV,
                      command=self.destroy, text_color=T_SUR_NEUTRE).pack(side="right", padx=8)
        self.after(100, self.entry.focus_set)

    def _valider(self, *_):
        """Contrôle du format (immédiat), puis recherche en arrière-plan."""
        texte = self.entry.get()              # lu ICI, dans le thread principal
        if not App._identifiant_valide(texte):
            _, message = self.master_app._resoudre_identifiant(texte)
            self.msg.configure(text=f"❌  {message}", text_color=T_ERREUR)
            return
        self.btn.configure(state="disabled")
        self.msg.configure(text="⏳  Recherche du compte…", text_color=T_SECONDAIRE)
        self.master_app._run(self._chercher, texte)

    def _chercher(self, texte):
        """(Thread) Recherche exacte du compte sur la plateforme."""
        compte, message = self.master_app._resoudre_identifiant(texte)
        self.master_app._ui(self._resultat, compte, message)

    def _resultat(self, compte, message):
        """(Thread principal) Ferme la fenêtre si le compte existe, sinon explique."""
        try:
            if not self.winfo_exists():
                return                         # fenêtre fermée entre-temps
        except Exception:
            return
        if compte:
            self.on_found(compte)
            self.destroy()
            return
        self.btn.configure(state="normal")
        self.msg.configure(text=f"❌  {message}", text_color=T_ERREUR)


class OwnerPicker(ctk.CTkToplevel):
    """Sélecteur multi-utilisateurs : même système que l'agent (liste + filtre + clic)."""

    def __init__(self, master: App, on_done, title="Propriétaires additionnels",
                 preselected: dict | None = None, single: bool = False, on_single=None,
                 intro: str = "", prefilter: str = ""):
        # Paramètres ajoutés :
        #   • intro     : texte d'introduction personnalisé (sinon texte par défaut).
        #   • prefilter : valeur pré-remplie dans le champ de filtre (ex. suggestion
        #                 issue de la détection automatique du propriétaire du token).
        super().__init__(master)
        self.master_app = master
        self.on_done = on_done
        self.single = single
        self.on_single = on_single
        self.title(title)
        self.geometry("500x560")
        _focus_toplevel(self, master)
        self.selected: dict[str, str] = dict(preselected or {})   # url → libellé

        # Texte d'introduction : personnalisé si fourni, sinon valeur par défaut
        # adaptée au mode (sélection unique ou multiple).
        if not intro:
            intro = ("Cliquez sur un utilisateur pour le choisir." if single else
                     "Cochez les comptes Pod à ajouter comme propriétaires\n"
                     "additionnels. Filtrez la liste puis cliquez pour (dé)cocher.")
        ctk.CTkLabel(self, text=intro, justify="left").pack(padx=14, pady=(14, 8), anchor="w")

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=14)
        self.filter = ctk.CTkEntry(bar, placeholder_text="🔍 nom / identifiant…")
        self.filter.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.filter.bind("<KeyRelease>", lambda e: self._render())
        ctk.CTkButton(bar, text="🔄", width=40, command=self._reload, fg_color=C_NEUTRE, hover_color=C_NEUTRE_SURV, text_color=T_SUR_NEUTRE).pack(side="left")
        # Pré-remplissage éventuel du filtre (suggestion de détection).
        if prefilter:
            self.filter.insert(0, prefilter)

        self.count_lbl = ctk.CTkLabel(self, text="", text_color=T_SECONDAIRE, font=ctk.CTkFont(size=11))
        self.count_lbl.pack(anchor="w", padx=14, pady=(4, 0))

        self.listbox = ctk.CTkScrollableFrame(self, height=320)
        self.listbox.pack(fill="both", expand=True, padx=14, pady=8)

        self.chosen_lbl = ctk.CTkLabel(self, text="Sélection : aucun", text_color=T_SECONDAIRE,
                                       wraplength=460, justify="left")
        # Le récapitulatif de sélection n'a de sens qu'en mode multiple.
        if not self.single:
            self.chosen_lbl.pack(padx=14, anchor="w")

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=10)
        if self.single:
            # En sélection unique, un clic sur un compte valide directement :
            # le bouton « Valider » est inutile, on ne garde qu'« Annuler ».
            ctk.CTkButton(btns, text="Annuler", fg_color=C_NEUTRE, hover_color=C_NEUTRE_SURV,
                          command=self.destroy, text_color=T_SUR_NEUTRE).pack(side="right")
        else:
            ctk.CTkButton(btns, text="Valider", fg_color=C_SUCCES, hover_color=C_SUCCES_SURV,
                          command=self._validate).pack(side="right")
            ctk.CTkButton(btns, text="Annuler", fg_color=C_NEUTRE, hover_color=C_NEUTRE_SURV,
                          command=self.destroy, text_color=T_SUR_NEUTRE).pack(side="right", padx=8)

        self.after(80, self._init_list)

    def _init_list(self):
        """Affiche la liste si les comptes sont déjà chargés, sinon déclenche un chargement."""
        if self.master_app.all_users:
            self._render()
            self._update_chosen()
        else:
            self.count_lbl.configure(text="⏳  Chargement des utilisateurs…")
            self._reload()

    def _reload(self):
        """(Thread) Charge la liste des comptes si nécessaire, puis rafraîchit l'affichage."""
        def work():
            """(Thread) Charge les données nécessaires puis rafraîchit l'affichage."""
            try:
                if not self.master_app.all_users:
                    users = self.master_app.api.get_all_users()
                    users.sort(key=lambda u: (u.get("username") or "").lower())
                    self.master_app.all_users = users
                self.after(0, self._render)
                self.after(0, self._update_chosen)
            except Exception as e:
                # `e` est capturé par valeur dans le lambda : sans le
                # paramètre par défaut, la variable de boucle aurait changé
                # avant l'exécution différée.
                self.after(0, lambda exc=e: self.count_lbl.configure(
                    text=f"❌  {message_utilisateur(exc)}", text_color=T_ERREUR))
        threading.Thread(target=work, daemon=True).start()

    def _label(self, u: dict) -> str:
        """Libellé lisible d'un compte."""
        return f"{u.get('username','?')} — {u.get('first_name','')} {u.get('last_name','')}".strip()

    def _render(self):
        """Affiche la liste filtrée (cases à cocher)."""
        flt = self.filter.get().strip().lower()
        for w in self.listbox.winfo_children():
            w.destroy()
        users = self.master_app.all_users
        if not users:
            ctk.CTkLabel(self.listbox, text="Liste non disponible.", text_color=T_SECONDAIRE).pack(pady=10)
            return
        matches = [u for u in users if not flt or flt in self._label(u).lower()]
        CAP = 300
        for u in matches[:CAP]:
            url = u.get("url", "")
            sel = url in self.selected
            if self.single:
                prefix = "   "
            else:
                prefix = "☑  " if sel else "☐  "
            ctk.CTkButton(self.listbox, text=prefix + self._label(u), anchor="w",
                          fg_color=("gray75", "gray30") if (sel and not self.single) else "transparent",
                          text_color=("gray10", "gray90"), hover_color=("gray75", "gray28"),
                          height=28, font=ctk.CTkFont(size=12),
                          command=lambda uu=u: self._toggle(uu)).pack(fill="x", pady=1)
        self.count_lbl.configure(text=f"{len(matches)} affiché(s) sur {len(users)} — "
                                      f"{len(self.selected)} sélectionné(s)", text_color=T_SECONDAIRE)
        if len(matches) > CAP:
            ctk.CTkLabel(self.listbox, text=f"… affinez le filtre ({len(matches) - CAP} de plus)",
                         text_color=T_SECONDAIRE).pack(pady=4)
        elif not matches:
            ctk.CTkLabel(self.listbox, text="Aucun résultat.", text_color=T_SECONDAIRE).pack(pady=8)

    def _toggle(self, u: dict):
        """Coche/décoche un compte (ou valide directement en mode sélection unique)."""
        if self.single:
            if self.on_single:
                self.on_single(u)
            self.destroy()
            return
        url = u.get("url", "")
        if not url:
            return
        if url in self.selected:
            del self.selected[url]
        else:
            self.selected[url] = self._label(u)
        self._render()
        self._update_chosen()

    def _update_chosen(self):
        """Met à jour le libellé récapitulant la sélection courante."""
        if self.selected:
            self.chosen_lbl.configure(text="Sélection : " + ", ".join(self.selected.values()),
                                      text_color=T_SUCCES)
        else:
            self.chosen_lbl.configure(text="Sélection : aucun", text_color=T_SECONDAIRE)

    def _validate(self):
        """Renvoie la sélection à l'appelant (on_done) puis ferme la fenêtre."""
        self.on_done(list(self.selected.keys()), list(self.selected.values()))
        self.destroy()


# ════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Mode enregistré par l'utilisateur, sombre par défaut.
    ctk.set_appearance_mode(cfg.load_theme())
    ctk.set_default_color_theme("blue")
    app = App()
    app.mainloop()
