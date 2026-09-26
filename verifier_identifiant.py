#!/usr/bin/env python3
"""
verifier_identifiant.py — Sonde : retrouver l'enseignant par son identifiant
============================================================================
Objectif : décider si Pod Téléverseur peut demander à l'enseignant son
IDENTIFIANT UNIVERSITAIRE (ex. « abc1234a ») au lieu de lui présenter la liste
de TOUS les comptes de la plateforme pour choisir le propriétaire des vidéos.

POURQUOI CETTE QUESTION
-----------------------
Aujourd'hui, le propriétaire n'est reconnu automatiquement que si le jeton ne
voit qu'UN compte. Les jetons de l'université en voient visiblement beaucoup :
l'application affiche alors l'annuaire complet, et l'enseignant peut choisir
n'importe quel collègue — par erreur, ou non. Et c'est ce choix qui décide des
vidéos que l'onglet « Mes vidéos » permet de modifier.

CE QUE LA SONDE VÉRIFIE
-----------------------
1. Ce que voit le jeton : un seul compte, ou tout l'annuaire ?
2. La recherche par identifiant renvoie-t-elle EXACTEMENT le bon compte ?
   (La recherche Pod est « plein texte » : un identifiant peut aussi figurer
   dans d'autres champs, ou en préfixer un autre.)
3. Les noms d'utilisateur suivent-ils tous le même format ? (Un contrôle de
   saisie trop strict bloquerait les comptes hors format.)
4. Ce jeton peut-il MODIFIER la vidéo d'un collègue ? C'est la vraie question
   de sécurité : si oui, un identifiant saisi n'est qu'un garde-fou contre
   l'erreur, pas une protection, puisque les droits viennent du jeton.

MODE : LECTURE SEULE. Aucune vidéo, aucun compte n'est créé ni modifié.
Le point 4 utilise une requête OPTIONS, qui ne change rien : pour une page de
détail, Django REST Framework n'annonce l'action « PUT » que si le compte a
réellement le droit de modifier CET objet.

CONFIDENTIALITÉ : la sonde n'affiche aucun nom ni adresse de collègue. Les
formats d'identifiants sont résumés par leur « forme » (lettres → a,
chiffres → 0 : « abc1234a » devient « aaa0000a »).

À LANCER avec le jeton d'un ENSEIGNANT (tel qu'il est distribué aux
utilisateurs du Téléverseur), et SON identifiant.

AUTONOME : aucun autre fichier du projet n'est nécessaire.

    python verifier_identifiant.py
"""

__author__ = "Cédric MONNA"
__contact__ = "support-pod@utoulouse.fr"
__version__ = "1.0.0"

import re
from collections import Counter

# Forme attendue d'un identifiant universitaire, exprimée sur sa « forme »
# anonyme (voir `forme`) : 3 lettres, 4 chiffres, et éventuellement une lettre
# finale (« abc1234a » → « aaa0000a »). À ajuster selon le résultat de la
# section 3.
FORME_ATTENDUE = re.compile(r"^a{3}0{4}a?$")

# Nombre maximal de comptes lus pour la statistique de format (section 3) :
# assez pour être représentatif, sans parcourir tout l'annuaire.
ECHANTILLON_MAX = 2000


def charger_requests():
    """Importe `requests`, en proposant de l'installer si nécessaire."""
    try:
        import requests
        return requests
    except ImportError:
        pass
    print("La bibliothèque « requests » est nécessaire et n'est pas installée.")
    if input("L'installer maintenant ? (o/N) : ").strip().lower() not in ("o", "oui", "y"):
        print("\nInstallation manuelle :   python -m pip install requests")
        return None
    import subprocess
    import sys as _sys
    try:
        subprocess.check_call([_sys.executable, "-m", "pip", "install", "requests"])
        import requests
        return requests
    except Exception as e:
        print(f"[X] Installation impossible : {e}")
        return None


def titre(texte):
    print(f"\n{'=' * 66}\n  {texte}\n{'=' * 66}")


def forme(identifiant: str) -> str:
    """Forme anonyme d'un identifiant : lettres → a, chiffres → 0."""
    return "".join("a" if c.isalpha() else "0" if c.isdigit() else c
                   for c in identifiant.lower())


def resultats(donnees):
    """Liste des résultats, que la réponse soit paginée ou non."""
    if isinstance(donnees, dict):
        return donnees.get("results") or []
    return donnees or []


def lire(sess, url, params=None):
    """GET → (code HTTP, JSON ou None)."""
    r = sess.get(url, params=params, timeout=30)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


# ── 1. Ce que voit le jeton ────────────────────────────────────────────────

def comptes_visibles(sess, rest):
    """Nombre de comptes visibles par le jeton (None si inaccessible)."""
    titre("1. CE QUE VOIT LE JETON")
    code, d = lire(sess, f"{rest}/users/", {"limit": 1})
    if code != 200:
        print(f"   /rest/users/ → HTTP {code} : le jeton ne peut pas lister les comptes.")
        return None
    total = d.get("count") if isinstance(d, dict) else len(resultats(d))
    print(f"   Comptes visibles : {total}")
    if total == 1:
        print("   → Le jeton ne voit QUE son propre compte : le Téléverseur le")
        print("     reconnaît déjà automatiquement (piste 1), sans rien demander.")
    else:
        print("   → Le jeton voit l'annuaire : l'application ne peut pas savoir")
        print("     seule à qui il appartient (Pod n'a pas de /me).")
    return total


# ── 2. Recherche par identifiant ───────────────────────────────────────────

def chercher(sess, rest, identifiant):
    """Cherche le compte par identifiant ; renvoie le compte exact ou None."""
    titre("2. RECHERCHE PAR IDENTIFIANT")
    ident = identifiant.strip().lower()
    code, d = lire(sess, f"{rest}/users/", {"search": ident, "limit": 50})
    if code != 200:
        print(f"   Recherche → HTTP {code}.")
        return None
    res = resultats(d)
    exacts = [u for u in res if str(u.get("username", "")).lower() == ident]
    print(f"   ?search={ident} → {len(res)} résultat(s), "
          f"dont {len(exacts)} au nom d'utilisateur EXACT.")
    if len(res) > len(exacts):
        print(f"   ⚠ {len(res) - len(exacts)} autre(s) résultat(s) : la recherche est "
              "plein texte. L'application devra exiger l'égalité exacte.")

    # Un filtre exact côté serveur éviterait de dépendre de la recherche.
    for param in ("username", "username__iexact"):
        c2, d2 = lire(sess, f"{rest}/users/", {param: ident, "limit": 5})
        r2 = resultats(d2) if c2 == 200 else []
        total = d2.get("count") if (c2 == 200 and isinstance(d2, dict)) else len(r2)
        fiable = (c2 == 200 and len(r2) == 1
                  and str(r2[0].get("username", "")).lower() == ident)
        if fiable:
            note = "  ← filtre exact FIABLE"
        elif c2 == 200 and total and total > 1:
            note = "  (paramètre ignoré par le serveur)"
        else:
            note = ""
        print(f"   ?{param}={ident} → HTTP {c2}, {total} résultat(s){note}")

    # Un préfixe ne doit pas suffire : on vérifie que la recherche ne
    # renverrait pas plusieurs comptes pour un identifiant tronqué.
    c3, d3 = lire(sess, f"{rest}/users/", {"search": ident[:5], "limit": 1})
    if c3 == 200 and isinstance(d3, dict):
        print(f"   ?search={ident[:5]} (identifiant tronqué) → {d3.get('count')} "
              "résultat(s) : d'où l'exigence d'égalité exacte.")

    if len(exacts) == 1:
        u = exacts[0]
        print(f"\n   ✓ Compte trouvé : {u.get('username')}  ({u.get('url', '?')})")
        print(f"     Champs disponibles : {', '.join(sorted(u.keys()))}")
        return u
    if not exacts:
        print("\n   ✗ Aucun compte ne porte exactement cet identifiant.")
    else:
        print("\n   ✗ PLUSIEURS comptes portent cet identifiant : ambigu.")
    return None


# ── 3. Format des noms d'utilisateur ───────────────────────────────────────

def formats(sess, rest):
    """Proportion des comptes au format attendu, et formes rencontrées."""
    titre("3. FORMAT DES NOMS D'UTILISATEUR")
    formes = Counter()
    url = f"{rest}/users/"
    params = {"limit": 100}
    lus = 0
    while url and lus < ECHANTILLON_MAX:
        code, d = lire(sess, url, params)
        if code != 200:
            print(f"   Lecture interrompue (HTTP {code}).")
            break
        for u in resultats(d):
            formes[forme(str(u.get("username", "")))] += 1
            lus += 1
        url = d.get("next") if isinstance(d, dict) else None
        params = None                     # `next` porte déjà ses paramètres
    if not lus:
        print("   Aucun compte lu.")
        return None
    conformes = sum(n for f, n in formes.items() if FORME_ATTENDUE.match(f))
    print(f"   Comptes lus : {lus}"
          + (" (échantillon)" if lus >= ECHANTILLON_MAX else ""))
    print(f"   Au format « 3 lettres + 4 chiffres (+ 1 lettre) » : "
          f"{conformes} ({100 * conformes // lus} %)")
    print("   Formes les plus fréquentes (lettres → a, chiffres → 0) :")
    for f, n in formes.most_common(8):
        print(f"      {f:20} {n}")
    return conformes, lus


# ── 4. Droits du jeton sur la vidéo d'un collègue ──────────────────────────

def peut_modifier(sess, url_video):
    """(annoncé, code) : l'action PUT est-elle annoncée par OPTIONS ?"""
    r = sess.options(url_video, timeout=30)
    try:
        actions = (r.json() or {}).get("actions") or {}
    except ValueError:
        actions = {}
    return ("PUT" in actions), r.status_code


def droits(sess, rest, compte):
    """Le jeton peut-il modifier une vidéo qui n'appartient pas au compte ?"""
    titre("4. DROITS DU JETON SUR LA VIDÉO D'UN COLLÈGUE")
    moi = str((compte or {}).get("url", "")).rstrip("/")
    code, d = lire(sess, f"{rest}/videos/", {"limit": 100})
    if code != 200:
        print(f"   /rest/videos/ → HTTP {code}.")
        return None
    videos = resultats(d)
    total = d.get("count") if isinstance(d, dict) else len(videos)
    print(f"   Vidéos visibles par le jeton : {total}")

    def proprio(v):
        o = v.get("owner")
        o = o.get("url", "") if isinstance(o, dict) else (o or "")
        return str(o).rstrip("/")

    a_moi = next((v for v in videos if moi and proprio(v) == moi), None)
    autre = next((v for v in videos if proprio(v) and proprio(v) != moi), None)

    if a_moi and a_moi.get("url"):
        ok, c = peut_modifier(sess, a_moi["url"])
        print(f"   Sa propre vidéo       : OPTIONS HTTP {c} → modification "
              f"{'ANNONCÉE' if ok else 'non annoncée'}")
    else:
        print("   Sa propre vidéo       : aucune dans l'échantillon.")

    if not autre or not autre.get("url"):
        print("   Vidéo d'un collègue   : aucune visible → le jeton ne voit que")
        print("                           les siennes (bon signe).")
        return False
    ok, c = peut_modifier(sess, autre["url"])
    print(f"   Vidéo d'un collègue   : OPTIONS HTTP {c} → modification "
          f"{'ANNONCÉE ⚠' if ok else 'non annoncée'}")
    return ok


def obtenir_jeton() -> str:
    """Le jeton, sans obliger à le taper ni à le coller.

    La console Windows refuse souvent de coller dans une saisie masquée : un
    jeton de 40 caractères devient alors impossible à fournir. Trois sources,
    dans l'ordre :
      1. un fichier « jeton.txt » posé à côté de la sonde — lu puis SUPPRIMÉ
         aussitôt, pour ne pas laisser traîner le jeton sur le disque ;
      2. le jeton déjà enregistré par Pod Téléverseur sur ce poste (coffre-fort
         Windows), après confirmation ;
      3. à défaut, la saisie masquée."""
    import os
    fichier = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jeton.txt")
    if os.path.exists(fichier):
        with open(fichier, encoding="utf-8-sig") as f:
            jeton = f.read().strip()
        try:
            os.remove(fichier)
            print("Jeton lu dans jeton.txt (fichier supprimé aussitôt).")
        except OSError:
            print("Jeton lu dans jeton.txt. ⚠ Supprimez ce fichier vous-même.")
        if jeton:
            return jeton

    try:
        import keyring
        jeton = keyring.get_password("PodTeleverseur-UToulouse", "service_token") or ""
    except Exception:
        jeton = ""
    if jeton:
        print("Un jeton est déjà enregistré par Pod Téléverseur sur ce poste.")
        print("  ⚠ La sonde doit tourner avec le jeton d'un ENSEIGNANT : si c'est")
        print("    un jeton d'administrateur, la section 4 ne dira rien d'utile.")
        if input("L'utiliser ? (O/n) : ").strip().lower() in ("", "o", "oui", "y"):
            return jeton

    return saisir_jeton()


def saisir_jeton() -> str:
    """Saisie du jeton où le COLLER fonctionne (Ctrl+V ou clic droit).

    `getpass` masque la saisie, mais dans la console Windows il lit les
    touches une à une : Ctrl+V y devient un caractère parasite, et le jeton,
    copié depuis le site, ne peut pas être collé. On utilise donc une saisie
    normale, puis on EFFACE l'écran pour que le jeton ne reste pas affiché.
    La frappe est affichée par la console elle-même, sans passer par
    `sys.stdout` : le jeton n'entre jamais dans le rapport texte."""
    import os
    print("Collez le jeton de l'ENSEIGNANT (Ctrl+V ou clic droit), puis Entrée.")
    print("Il sera effacé de l'écran aussitôt.")
    jeton = input("Jeton : ").strip()
    os.system("cls" if os.name == "nt" else "clear")
    if jeton:
        print(f"Jeton reçu (…{jeton[-4:]}), effacé de l'écran.\n")
    return jeton


def run():
    requests = charger_requests()
    if requests is None:
        return

    base = (input("URL de l'instance [https://videos.utoulouse.fr] : ").strip()
            or "https://videos.utoulouse.fr").rstrip("/")
    if not base.lower().startswith("https://"):
        print("[X] Adresse refusée : le jeton ne doit circuler qu'en HTTPS.")
        return
    token = obtenir_jeton()
    if not token:
        print("[X] Jeton vide.")
        return
    identifiant = input("Identifiant universitaire de CET enseignant (ex. abc1234a) : ").strip()

    rest = f"{base}/rest"
    sess = requests.Session()
    sess.headers.update({"Authorization": f"Token {token}",
                         "Accept": "application/json"})

    visibles = comptes_visibles(sess, rest)
    compte = chercher(sess, rest, identifiant) if identifiant else None
    stats = formats(sess, rest) if visibles and visibles > 1 else None
    collegue = droits(sess, rest, compte)

    titre("CONCLUSION")
    if visibles == 1:
        print("   ➜  Rien à développer : le jeton ne voit que son compte, le")
        print("      Téléverseur le reconnaît déjà automatiquement.")
    elif compte:
        print("   ➜  RÉALISABLE : l'identifiant retrouve le compte sans ambiguïté")
        print("      (à condition d'exiger l'égalité exacte).")
        if stats:
            conformes, lus = stats
            if conformes < lus:
                print(f"      ⚠ {lus - conformes} compte(s) hors format : le contrôle de")
                print("        saisie ne doit pas bloquer, seulement guider.")
    else:
        print("   ➜  À REVOIR : l'identifiant ne retrouve pas le compte (voir 2).")

    if collegue:
        print()
        print("   ⚠  Ce jeton peut MODIFIER la vidéo d'un collègue. Demander")
        print("      l'identifiant évitera les erreurs, mais ne protégera pas :")
        print("      les droits viennent du jeton. La vraie protection se règle")
        print("      côté Pod (jetons d'enseignants sans droits « équipe »).")
    elif collegue is False:
        print()
        print("   ✓  Ce jeton ne peut pas modifier la vidéo d'un collègue : les")
        print("      droits sont déjà bornés côté Pod.")
    print("\n   Transmettez cette sortie complète.\n")


class Double:
    """Écrit à la fois à l'écran et dans le rapport : la sortie n'a pas à être
    copiée-collée, il suffit de transmettre le fichier. Le jeton n'y figure
    jamais (voir `saisir_jeton`)."""

    def __init__(self, ecran, fichier):
        self.ecran, self.fichier = ecran, fichier

    def write(self, texte):
        self.ecran.write(texte)
        self.fichier.write(texte)

    def flush(self):
        self.ecran.flush()
        self.fichier.flush()


if __name__ == "__main__":
    import os
    import sys
    rapport = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "rapport_verifier_identifiant.txt")
    with open(rapport, "w", encoding="utf-8") as f:
        sys.stdout = Double(sys.__stdout__, f)
        sys.stderr = Double(sys.__stderr__, f)   # une éventuelle erreur aussi
        try:
            run()
        except KeyboardInterrupt:
            print("\nInterrompu.")
        except Exception:
            import traceback
            print("\n[ERREUR] La sonde a rencontré un problème :\n")
            traceback.print_exc()
        finally:
            sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
    print(f"\nRapport enregistré dans :\n   {rapport}")
    input("\nAppuyez sur Entrée pour fermer…")
