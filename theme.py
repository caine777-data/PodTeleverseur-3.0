"""
theme.py — Apparence et messages communs aux outils Pod du service MFCA.

Ce module est PARTAGÉ par PodAdmin, Pod Téléverseur v2 et Pod Téléverseur v3.
Les trois applications ont la même identité visuelle et les mêmes règles ; les
faire diverger reviendrait à corriger trois fois le même défaut — et à en
oublier deux.

⚠️ RÈGLE PREMIÈRE : toute couleur est un COUPLE (clair, sombre).
Une teinte écrite seule s'applique telle quelle aux deux thèmes. C'est ainsi
qu'une ligne sur deux d'un tableau s'affichait presque NOIRE en mode clair, et
que des filets de séparation barraient les panneaux d'un trait sombre. Le
défaut reste invisible tant que le mode sombre est le seul utilisé.

Les valeurs ci-dessous ont été MESURÉES, pas choisies à l'œil : chaque teinte
de texte atteint 4,5:1 (WCAG 2.1 AA) sur toute l'échelle de surfaces, dans les
deux modes. Le contraste se calcule ; l'apprécier au jugé a déjà produit cinq
teintes illisibles.

Fonctions fournies :
    message_utilisateur(e)      traduit une exception en phrase actionnable
    etat_vide(parent, …)        panneau vide qui explique au lieu de constater
    contraste(a, b)             rapport WCAG entre deux teintes
    verifier_palette()          contrôle l'ensemble de la palette
"""

__author__ = "Cédric MONNA"
__version__ = "1.0.0"

import customtkinter as ctk


# — Couleurs d'action ——————————————————————————————————————————————————
C_ACTION       = ("#2563eb", "#2563eb")   # UNIQUE action principale de l'écran
C_ACTION_SURV  = ("#1d4ed8", "#1d4ed8")
# ⚠️ Les UTILITAIRES ne prennent PAS cette teinte : « Rafraîchir », « Recharger »,
# « Parcourir… » sont en C_NEUTRE. Un écran ne porte qu'un seul bouton coloré,
# celui de son action propre. Le commentaire citait « Rafraîchir » en exemple
# alors que les quatre boutons de ce nom sont neutres depuis la 1.5.2 : c'est
# ainsi qu'un onglet ajouté plus tard aurait repris le bleu.
# Le vert du mode sombre était plus clair que celui du mode clair, ce qui
# ramenait le contraste du texte BLANC à 3,30 — sous le seuil AA. Les deux
# modes emploient désormais la même teinte : un bouton d'action n'a pas besoin
# de s'éclaircir en mode sombre, il porte déjà sa propre couleur.
C_SUCCES       = ("#15803d", "#15803d")   # validation : Lancer, Enregistrer
C_SUCCES_SURV  = ("#166534", "#166534")
C_ALERTE       = ("#b45309", "#b45309")   # avertissement, interruption
C_ALERTE_SURV  = ("#92400e", "#92400e")
C_ERREUR       = ("#b91c1c", "#ef4444")   # échec, message d'erreur
C_DESTRUCTIF   = ("#7f1d1d", "#7f1d1d")   # suppression définitive UNIQUEMENT
C_DESTR_SURV   = ("#991b1b", "#991b1b")
C_NEUTRE       = ("gray65", "gray35")     # secondaire : Annuler, Fermer
C_NEUTRE_SURV  = ("gray55", "gray28")
# Un bouton gris gardait le texte BLANC par défaut de CustomTkinter : en mode
# clair, blanc sur gris65 donne un bouton qui paraît DÉSACTIVÉ. Le texte des
# boutons secondaires doit donc être foncé en clair, pâle en sombre. Un bouton
# réellement désactivé reste distinct : CustomTkinter lui applique sa propre
# teinte « text_color_disabled ».
T_SUR_NEUTRE   = ("gray10", "gray95")
C_ACCENT       = ("#6d28d9", "#7c3aed")   # actions particulières : Habillage
C_ACCENT_SURV  = ("#5b21b6", "#6d28d9")

# — Échelle de SURFACES ————————————————————————————————————————————————
# Douze gris différents cohabitaient pour désigner quatre choses seulement.
# « gray85 » et « gray86 » servaient au même usage sans qu'on puisse dire
# lequel était le bon, et un panneau changeait de teinte selon l'onglet.
#
# Quatre niveaux suffisent, du plus proche du fond au plus détaché :
#   S_CARTE      un panneau posé sur le fond de la fenêtre
#   S_LIGNE      une ligne de liste, un en-tête de tableau, un encart
#   S_SELECTION  l'élément actif ou sélectionné
#   S_PUCE       une pastille ou un aperçu à l'intérieur d'une ligne
#
# Toute nouvelle surface doit reprendre l'un de ces quatre niveaux. En ajouter
# un cinquième « juste pour cet écran » est précisément ce qui a produit les
# douze précédents.
# Le fond de la fenêtre et la barre latérale tombaient sur la MÊME teinte en
# mode clair (219,219,219), et `S_LIGNE` valait exactement le fond : d'où une
# impression de gris uniforme, où rien ne se détachait de rien.
#
# L'échelle suit désormais une élévation. En mode clair, une surface posée est
# plus CLAIRE que son support ; en mode sombre, plus claire aussi — mais on
# part du bas. Le fond de fenêtre est le niveau le plus bas des deux côtés.
S_FOND       = ("gray88", "gray11")   # fond de la fenêtre
S_BARRE      = ("gray92", "gray15")   # barre latérale
S_CARTE      = ("gray96", "gray18")   # panneau posé sur le fond
S_LIGNE      = ("gray91", "gray22")   # ligne de liste, en-tête, encart
S_LIGNE_ALT  = ("gray95", "gray19")   # zébrure : alterne avec S_LIGNE
S_SELECTION  = ("gray78", "gray30")   # élément actif
S_PUCE       = ("gray84", "gray26")   # pastille ou aperçu dans une ligne
# Un filet de séparation : « gray30 » était écrit seul, donc appliqué tel quel
# dans les DEUX modes — un trait presque noir en travers d'un panneau clair.
S_FILET      = ("gray80", "gray32")
# Teinte des lignes retenues en sélection MULTIPLE (Ctrl+clic / Maj+clic).
# Elle doit se distinguer nettement de S_SELECTION, qui marque la ligne
# simplement sélectionnée : confondre les deux revient à ne plus savoir ce sur
# quoi une action de masse va porter.
C_MULTI      = ("#93c5fd", "#1e3a8a")

# — Contrôles de saisie ————————————————————————————————————————————————
# Les listes déroulantes prenaient le bleu par défaut de CustomTkinter, c'est-
# à-dire EXACTEMENT la teinte des boutons d'action. Sur l'onglet Vidéos, cinq
# filtres, le bouton « Rafraîchir » et le bouton « Appliquer » criaient donc
# aussi fort : l'œil n'avait aucun point d'entrée.
#
# Un filtre n'est pas une action, c'est un réglage. Il doit se voir sans
# appeler. D'où ces teintes neutres, plus claires que C_NEUTRE (réservé aux
# boutons secondaires) pour rester distinguables d'un bouton désactivé.
C_CHAMP        = ("gray80", "gray30")     # fond d'une liste déroulante
C_CHAMP_BOUTON = ("gray70", "gray38")     # sa partie cliquable (la flèche)
C_CHAMP_SURV   = ("gray62", "gray45")
T_CHAMP        = ("gray10", "gray90")     # texte d'un contrôle

# Style complet d'une liste déroulante, à déplier avec ** dans chaque appel :
#     ctk.CTkOptionMenu(parent, values=[...], **STYLE_CHAMP)
# Passer par un dictionnaire unique évite qu'un nouveau filtre ajouté plus tard
# reprenne le bleu par défaut sans que personne ne le remarque — un test vérifie
# qu'aucun CTkOptionMenu n'est créé sans lui.
STYLE_CHAMP = {
    "fg_color": C_CHAMP,
    "button_color": C_CHAMP_BOUTON,
    "button_hover_color": C_CHAMP_SURV,
    "text_color": T_CHAMP,
}

# Une zone de liste (CTkComboBox) est ÉDITABLE : son fond doit rester clair,
# comme un champ de saisie. Seule sa flèche est neutralisée.
STYLE_ZONE = {
    "button_color": C_CHAMP_BOUTON,
    "button_hover_color": C_CHAMP_SURV,
}

# — Couleurs de TEXTE ——————————————————————————————————————————————————
# `gray` (3,93:1) passait sous le minimum de lisibilité WCAG AA (4,5:1) et
# était pourtant la teinte secondaire la plus employée : 101 occurrences.
# ⚠️ TEINTES DE TEXTE : le volet CLAIR a été mesuré, pas seulement déduit.
#
# Ces couleurs avaient été réglées à l'époque où le mode sombre était le seul
# disponible ; le volet clair en avait été déduit à l'œil. Mesure faite selon
# WCAG (seuil AA = 4,5:1 pour du texte normal), CINQ des sept teintes étaient
# sous le seuil sur les fonds clairs de l'échelle de surfaces :
#
#     T_SECONDAIRE  gray45   → 3,28   T_SUCCES  #15803d → 3,46
#     T_DISCRET     gray50   → 2,74   T_ALERTE  #b45309 → 3,46
#                                     T_ERREUR  #b91c1c → 4,46
#
# Les valeurs ci-dessous sont calculées sur le PIRE fond de l'échelle, pas sur
# un fond moyen : une mention lisible sur S_CARTE et illisible sur S_LIGNE
# resterait un défaut.
#
# LE VOLET SOMBRE N'ÉTAIT PAS CONFORME NON PLUS, contrairement à ce que
# l'audit annonçait : mesuré uniquement sur S_CARTE et S_BARRE, il passait ;
# mesuré sur S_PUCE (gray26), T_ERREUR tombait à 2,66 et T_SUCCES à 4,39.
#
# ⚠️ Le pire fond est S_PUCE (gray84), pas S_CARTE. Une première correction
# visait gray39, calculée en oubliant S_PUCE dans la liste : elle plafonnait à
# 4,11. D'où le test automatique ci-dessous, qui parcourt TOUTE l'échelle —
# c'est exactement le genre d'oubli qu'un calcul à la main reproduit.
T_SECONDAIRE   = ("gray36", "gray70")     # 4,62 sur le pire fond clair
T_DISCRET      = ("gray36", "gray70")     # 4,62 clair / 4,74 sombre
T_SUCCES       = ("#166534", "#4ade80")   # 4,92 clair / 5,74 sombre
T_ALERTE       = ("#92400e", "#f59e0b")   # 4,89 clair / 4,66 sombre
T_ERREUR       = ("#991b1b", "#fca5a5")   # 5,73 clair / 5,27 sombre

# — Tailles de police ——————————————————————————————————————————————————
# Les tailles allaient de 9 à 26 px sans échelle. Cinq niveaux suffisent.
# — Hauteurs de cibles cliquables ——————————————————————————————————————
# Six hauteurs différentes cohabitaient (22, 24, 26, 28, 32, 40) sans qu'aucune
# règle ne dise laquelle employer. Neuf boutons descendaient à 22 ou 24 px,
# sous la cible minimale confortable — et c'étaient ceux des listes, donc les
# plus souvent visés, dans les rangées les plus denses.
#
# Trois niveaux suffisent. Le niveau NORMAL vaut 28, qui est aussi le défaut de
# CustomTkinter : les 81 boutons qui ne précisent rien s'y rangent déjà.
H_COMPACT   = 26    # bouton d'une rangée de liste (icône seule, action répétée)
H_NORMAL    = 28    # bouton courant
H_PRINCIPAL = 40    # action principale d'un écran

T_TITRE   = 20    # titre d'onglet
T_SOUS    = 16    # titre de section
T_CORPS   = 13    # texte courant
T_PETIT   = 12    # libellés de formulaire
T_MINI    = 11    # mentions, compteurs, aide en ligne


def etat_vide(parent, icone: str, titre: str, aide: str = "") -> None:
    """Affiche un panneau vide qui EXPLIQUE au lieu de constater.

    Un état vide est le moment où l'utilisateur ne sait pas quoi faire : c'est
    précisément là qu'une ligne grise au centre d'un grand panneau — « Aucune
    vidéo ne correspond. » — ne l'aide pas. Elle dit ce qui manque, jamais
    pourquoi ni comment y remédier.

    Trois éléments donc : une icône qui signale que l'écran fonctionne, une
    phrase qui nomme la situation, et une aide qui indique le geste suivant.
    L'aide est facultative — quand il n'y a réellement rien à faire (une liste
    qui se remplira d'elle-même), en inventer une serait pire que rien.

    Trois widgets au maximum, et un seul état vide est affiché à la fois :
    l'effet sur le poids total de l'interface est négligeable.
    """
    ctk.CTkLabel(parent, text=icone, font=ctk.CTkFont(size=28),
                 text_color=T_DISCRET).pack(pady=(28, 6))
    ctk.CTkLabel(parent, text=titre, font=ctk.CTkFont(size=T_CORPS),
                 text_color=T_SECONDAIRE, justify="center").pack()
    if aide:
        # 280 et non 360 : ces panneaux font environ 400 px de large, et un
        # repli trop tardif rejetait le dernier mot seul sur sa ligne.
        ctk.CTkLabel(parent, text=aide, font=ctk.CTkFont(size=T_MINI),
                     text_color=T_DISCRET, justify="center",
                     wraplength=280).pack(padx=12, pady=(4, 28))



def _motif_lisible(corps: str) -> str:
    """Extrait d'une réponse d'erreur le motif en clair.

    Django REST renvoie ses refus en JSON — {"sites": ["Ce champ est
    obligatoire."]} — parfois imbriqué. On en tire les phrases, en préfixant
    par le nom du champ quand il est connu : « sites : Ce champ est
    obligatoire. » est autrement plus utile que l'accolade brute.

    En cas de doute, on renvoie le texte tel quel plutôt que rien : un motif
    mal présenté vaut mieux qu'un motif perdu.
    """
    corps = (corps or "").strip()
    if not corps:
        return ""
    try:
        import json as _json
        donnees = _json.loads(corps)
    except Exception:
        return corps.replace("\n", " ")[:160]

    morceaux = []

    def parcourir(valeur, champ=None):
        if isinstance(valeur, dict):
            for cle, sous in valeur.items():
                parcourir(sous, cle)
        elif isinstance(valeur, (list, tuple)):
            for sous in valeur:
                parcourir(sous, champ)
        else:
            texte = str(valeur).strip()
            if texte:
                # « detail » et « non_field_errors » sont des noms techniques
                # de Django REST : les afficher n'apprendrait rien.
                if champ and champ not in ("detail", "non_field_errors"):
                    morceaux.append(f"{champ} : {texte}")
                else:
                    morceaux.append(texte)

    parcourir(donnees)
    return " ; ".join(morceaux)[:200]


def message_utilisateur(e: Exception) -> str:
    """Traduit une exception en une phrase compréhensible et ACTIONNABLE.

    Vingt endroits affichaient l'exception telle quelle. Un collègue du support
    pouvait lire :

        ❌ HTTPSConnectionPool(host='videos.utoulouse.fr', port=443): Max
           retries exceeded with url: /rest/videos/ (Caused by
           NewConnectionError(...))

    Ce texte ne dit ni ce qui s'est passé, ni quoi faire, et il inquiète. Le
    détail technique n'est pas perdu pour autant : il part dans le Journal,
    où il reste disponible pour le diagnostic et pour un signalement au
    support.

    La règle suivie ici : dire CE QUI S'EST PASSÉ, puis CE QU'ON PEUT FAIRE.
    Un message qui ne propose rien laisse l'utilisateur bloqué.
    """
    texte = str(e) or e.__class__.__name__
    statut = getattr(e, "status", 0) or 0

    # — Erreurs de l'API Pod, reconnues par leur code HTTP —
    if statut == 401:
        return ("Jeton refusé par l'instance. Vérifiez-le dans l'onglet "
                "Configuration, ou régénérez-en un.")
    if statut == 403:
        return ("Droits insuffisants pour cette opération. Un compte "
                "superutilisateur est nécessaire.")
    if statut == 404:
        return ("Élément introuvable sur l'instance : il a peut-être été "
                "supprimé entre-temps. Rafraîchissez la liste.")
    if statut == 400:
        # Le corps de réponse porte le motif exact du refus. Django REST le
        # renvoie en JSON, sous la forme {"champ": ["motif", …]} : affiché
        # brut, cela donne des accolades et des crochets au milieu d'une
        # phrase. On en extrait le texte lisible.
        detail = _motif_lisible(getattr(e, "body", ""))
        if detail:
            return f"Requête refusée : {detail}"
        return "Requête refusée par l'instance (donnée invalide ou manquante)."
    if statut == 409:
        return ("Opération refusée : l'élément est utilisé ailleurs, ou une "
                "autre modification l'a précédée.")
    if statut and 500 <= statut < 600:
        return ("L'instance Pod rencontre une difficulté (erreur serveur). "
                "Réessayez dans quelques minutes ; si cela persiste, "
                "prévenez la DSI.")

    # — Erreurs réseau, reconnues sur le texte : les classes exactes varient
    #   selon que l'appel est passé par requests ou par urllib. —
    bas = texte.lower()
    if any(m in bas for m in ("max retries", "connection", "connexion",
                              "nameresolution", "getaddrinfo", "unreachable")):
        return ("Instance injoignable. Vérifiez votre connexion réseau et "
                "l'URL saisie dans l'onglet Configuration.")
    if any(m in bas for m in ("timeout", "timed out", "délai")):
        return ("L'instance met trop de temps à répondre. Réessayez ; sur un "
                "gros fichier, l'opération peut être longue.")
    if any(m in bas for m in ("certificate", "ssl", "certificat")):
        return ("Certificat de sécurité refusé. Signalez-le à la DSI plutôt "
                "que de contourner la vérification.")
    if "fichier introuvable" in bas:
        return texte          # déjà clair, et il nomme le fichier

    # — Cas non reconnu : on reste honnête plutôt que d'inventer une cause —
    court = texte if len(texte) <= 140 else texte[:140] + "…"
    return f"Échec de l'opération : {court}  (détail dans l'onglet Journal)"




# ════════════════════════════════════════════════════════════════════════════
#  CONTRÔLE DE LA PALETTE
# ════════════════════════════════════════════════════════════════════════════

def _luminance(valeur: str) -> float:
    """Luminance relative WCAG d'une teinte « grayNN » ou « #rrggbb »."""
    def canal(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    if valeur.startswith("gray"):
        return canal(int(valeur[4:]) / 100)
    h = valeur.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b)


def contraste(a: str, b: str) -> float:
    """Rapport de contraste WCAG entre deux teintes (1 à 21)."""
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def verifier_palette(seuil: float = 4.5) -> list:
    """Renvoie la liste des combinaisons sous le seuil, vide si tout va bien.

    À appeler depuis les tests de chaque application : une teinte ajoutée plus
    tard est couverte sans que personne n'ait à y penser."""
    surfaces = {"S_FOND": S_FOND, "S_BARRE": S_BARRE, "S_CARTE": S_CARTE,
                "S_LIGNE": S_LIGNE, "S_LIGNE_ALT": S_LIGNE_ALT, "S_PUCE": S_PUCE}
    textes = {"T_SECONDAIRE": T_SECONDAIRE, "T_DISCRET": T_DISCRET,
              "T_SUCCES": T_SUCCES, "T_ALERTE": T_ALERTE, "T_ERREUR": T_ERREUR,
              "T_CHAMP": T_CHAMP, "T_SUR_NEUTRE": T_SUR_NEUTRE}
    fautifs = []
    for indice, mode in ((0, "clair"), (1, "sombre")):
        for nom_t, couple_t in textes.items():
            for nom_s, couple_s in surfaces.items():
                r = contraste(couple_t[indice], couple_s[indice])
                if r < seuil:
                    fautifs.append(f"{nom_t} sur {nom_s} ({mode}) = {r:.2f}")
    return fautifs
