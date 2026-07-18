#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verifier_mes_videos.py — Sonde de diagnostic (LECTURE SEULE) pour l'onglet
« Mes vidéos » du futur Pod Téléverseur v3.

OBJECTIF
--------
Déterminer, sur l'instance de TEST, si l'API REST d'Esup-Pod sait filtrer les
vidéos par propriétaire côté SERVEUR (paramètre de requête), afin que l'onglet
« Mes vidéos » ne scanne pas toute l'instance à chaque ouverture.

La sonde n'écrit RIEN : elle ne fait que des GET. Aucune vidéo n'est créée,
modifiée ni supprimée.

CE QUE LA SONDE TESTE
---------------------
1. Que le token fonctionne et compte total des vidéos visibles.
2. Résout un compte propriétaire (par username ou URL fournie).
3. Essaie plusieurs variantes de filtre serveur et, pour CHACUNE, vérifie que
   TOUTES les vidéos renvoyées appartiennent bien au propriétaire demandé :
      • /videos/?owner=<URL du compte>
      • /videos/?owner=<id numérique>
      • /videos/?owner__username=<username>
      • /videos/?owner=<username>
   Un filtre est déclaré FIABLE si la 1re page ne contient QUE des vidéos du
   bon propriétaire (et idéalement moins que le total de l'instance).

CONCLUSION
----------
La sonde imprime la ou les variantes fiables. Si aucune ne l'est, l'onglet
« Mes vidéos » devra charger toutes les vidéos puis filtrer côté client
(correct mais plus lourd) — ce qui reste parfaitement fonctionnel.

USAGE
-----
    python verifier_mes_videos.py
Puis répondre aux quelques questions (URL, token, propriétaire à tester).
Utiliser de préférence le token is_staff d'un ENSEIGNANT réel et l'un de ses
comptes comme propriétaire de test.
"""

from __future__ import annotations

import sys
import json
import getpass
from urllib.parse import urlencode

try:
    import requests
except ImportError:
    print("Le module 'requests' est requis :  pip install requests")
    sys.exit(1)


# ───────────────────────────── Utilitaires ──────────────────────────────

def _demander(question: str, defaut: str = "") -> str:
    """Pose une question en proposant une valeur par défaut."""
    suffixe = f" [{defaut}]" if defaut else ""
    rep = input(f"{question}{suffixe} : ").strip()
    return rep or defaut


def _owner_id(video: dict):
    """Identifiant du propriétaire d'une vidéo, quel que soit le format renvoyé
    par l'API (URL absolue, dict imbriqué, username ou id numérique)."""
    o = video.get("owner")
    if isinstance(o, dict):
        return o.get("url") or o.get("username") or ""
    return o if o is not None else ""


def _norm(x) -> str:
    """Normalise un identifiant pour comparaison (str, sans slash final)."""
    return str(x).rstrip("/")


# ─────────────────────────── Client minimal ─────────────────────────────

class Sonde:
    """Sonde de diagnostic de l'onglet « Mes vidéos » : vérifie le filtrage des vidéos par propriétaire."""
    def __init__(self, base_url: str, token: str, verify_ssl: bool = True):
        """Initialise la sonde : URL de l'instance, token et vérification TLS."""
        self.base = base_url.rstrip("/")
        self.rest = f"{self.base}/rest"
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Token {token}",
            "Accept": "application/json",
        })
        self.verify = verify_ssl

    def get(self, endpoint: str, params: dict | None = None) -> dict:
        """GET brut sur un endpoint relatif ; renvoie le JSON (ou lève)."""
        url = endpoint if endpoint.startswith("http") else f"{self.rest}{endpoint}"
        r = self.s.get(url, params=params, timeout=30, verify=self.verify)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code} sur {r.url}\n{r.text[:300]}")
        return r.json() if r.text else {}

    # — Total des vidéos visibles (champ 'count' de la pagination) —
    def total_videos(self) -> int:
        d = self.get("/videos/", {"limit": 1})
        return d.get("count", 0) if isinstance(d, dict) else 0

    # — Résolution d'un compte : renvoie le dict utilisateur ou None —
    def resoudre_user(self, terme: str) -> dict | None:
        """Cherche un compte par username (recherche plein-texte) ou reconnaît
        une URL /rest/users/<id>/ déjà fournie."""
        terme = terme.strip()
        if terme.startswith("http"):
            try:
                return self.get(terme)   # l'URL pointe directement le compte
            except Exception:
                return {"url": terme, "username": terme.rstrip("/").split("/")[-1]}
        d = self.get("/users/", {"search": terme, "limit": 50})
        results = d.get("results", d) if isinstance(d, dict) else d
        # Correspondance exacte de username d'abord, sinon 1er résultat
        for u in (results or []):
            if u.get("username", "").lower() == terme.lower():
                return u
        return (results or [None])[0]


# ─────────────────────────── Test d'un filtre ───────────────────────────

def tester_variante(sonde: Sonde, libelle: str, params: dict,
                    owner_ids: set, total: int) -> None:
    """Exécute un GET /videos/ avec `params`, puis analyse la 1re page :
    combien de vidéos, et sont-elles TOUTES du bon propriétaire ?"""
    print(f"\n── Variante : {libelle}")
    print(f"   GET /videos/?{urlencode(params)}")
    try:
        d = sonde.get("/videos/", params)
    except Exception as e:
        print(f"   ❌ Échec : {e}")
        return

    if not isinstance(d, dict):
        print("   ⚠️  Réponse non paginée (format inattendu).")
        return

    count = d.get("count", "?")
    results = d.get("results", [])
    n = len(results)
    # Combien des vidéos renvoyées appartiennent réellement au propriétaire ?
    bonnes = sum(1 for v in results if _norm(_owner_id(v)) in owner_ids)
    autres = n - bonnes

    print(f"   count annoncé : {count}   ·   vidéos en 1re page : {n}")
    print(f"   du bon propriétaire : {bonnes}   ·   d'autres comptes : {autres}")

    if n == 0:
        print("   → Aucune vidéo. Filtre peut-être trop strict, ou compte sans vidéo.")
    elif autres == 0 and (isinstance(count, int) and count < total):
        print("   ✅ FIABLE : le serveur filtre bien par propriétaire "
              f"(count {count} < total {total}).")
    elif autres == 0:
        print("   ✅ Cohérent : que des vidéos du bon propriétaire "
              "(mais count ≈ total, à confirmer sur un compte à peu de vidéos).")
    else:
        print("   ❌ IGNORÉ : le serveur renvoie aussi des vidéos d'autres comptes "
              "→ ce paramètre n'est PAS pris en compte, filtrage client nécessaire.")


# ───────────────────────────────── Main ─────────────────────────────────

def main() -> None:
    """Point d'entrée de la sonde : pose les questions puis enchaîne les tests."""
    print("=" * 70)
    print("  Sonde « Mes vidéos » — filtre propriétaire côté serveur (READ-ONLY)")
    print("=" * 70)
    print("À lancer de préférence sur l'INSTANCE DE TEST.\n")

    base = _demander("URL de l'instance", "https://videos.utoulouse.fr")
    token = getpass.getpass("Token (is_staff d'un enseignant) : ").strip()
    if not token:
        print("Token vide, abandon.")
        return
    verify = _demander("Vérifier le certificat SSL ? (o/n)", "o").lower().startswith("o")
    if not verify:
        try:
            import urllib3
            urllib3.disable_warnings()
        except Exception:
            pass

    sonde = Sonde(base, token, verify_ssl=verify)

    # 1) Connexion + total
    print("\n[1] Test de connexion…")
    try:
        total = sonde.total_videos()
        print(f"    ✅ Token OK. Total des vidéos visibles : {total}")
    except Exception as e:
        print(f"    ❌ Connexion impossible : {e}")
        return

    # 2) Résolution du propriétaire de test
    terme = _demander("\n[2] Propriétaire à tester (username ou URL /rest/users/<id>/)")
    if not terme:
        print("    Aucun propriétaire fourni, abandon.")
        return
    user = sonde.resoudre_user(terme)
    if not user:
        print("    ❌ Compte introuvable.")
        return

    owner_url = _norm(user.get("url", ""))
    username = user.get("username", "")
    # id numérique déduit de l'URL (…/rest/users/42/ → 42)
    owner_num = owner_url.split("/")[-1] if owner_url else ""
    # Ensemble des identifiants qui « valent » ce propriétaire (pour comparer)
    owner_ids = {x for x in {owner_url, username, owner_num} if x}
    print(f"    ✅ Compte : username='{username}'  url='{owner_url}'  id='{owner_num}'")

    # 3) Test des variantes de filtre serveur
    print("\n[3] Essai des variantes de filtre serveur…")
    if owner_url:
        tester_variante(sonde, "owner = URL complète",
                        {"owner": user.get("url", ""), "limit": 100},
                        owner_ids, total)
    if owner_num:
        tester_variante(sonde, "owner = id numérique",
                        {"owner": owner_num, "limit": 100}, owner_ids, total)
    if username:
        tester_variante(sonde, "owner__username = username",
                        {"owner__username": username, "limit": 100},
                        owner_ids, total)
        tester_variante(sonde, "owner = username",
                        {"owner": username, "limit": 100}, owner_ids, total)

    # 4) Repli : chargement complet + filtrage client (pour référence)
    print("\n[4] Référence filtrage CLIENT (sans paramètre serveur)…")
    print("    (on lit la 1re page brute et on compte celles du propriétaire)")
    try:
        d = sonde.get("/videos/", {"limit": 100})
        results = d.get("results", []) if isinstance(d, dict) else (d or [])
        mine = [v for v in results if _norm(_owner_id(v)) in owner_ids]
        print(f"    Sur {len(results)} vidéos lues (page 1), "
              f"{len(mine)} appartiennent au propriétaire.")
        print("    → Le filtrage client fonctionne toujours ; il suffit de paginer "
              "toute l'instance puis de ne garder que ces vidéos.")
    except Exception as e:
        print(f"    ❌ {e}")

    print("\n" + "=" * 70)
    print("  BILAN")
    print("=" * 70)
    print("• Si une variante est marquée ✅ FIABLE → l'utiliser dans get_all_videos")
    print("  via extra_params (ex. {'owner': <url>}), on évite le scan complet.")
    print("• Si tout est ❌ IGNORÉ → charger toutes les vidéos et filtrer côté")
    print("  client avec _video_belongs_to (correct, un peu plus lent).")
    print("• Dans les DEUX cas, l'onglet « Mes vidéos » sera correct : le filtre")
    print("  client garantit qu'aucune vidéo d'autrui ne s'affiche.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrompu.")
