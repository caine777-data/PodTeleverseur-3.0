#!/usr/bin/env python3
"""
pod_api.py — Client de l'API REST Esup-Pod v4
Client API REST Esup-Pod — Pod Téléverseur (Université de Toulouse)
Forké de « Pod Téléverseur » : tout le client d'upload est conservé, une
section ADMINISTRATION est ajoutée en dessous.

Particularités Esup-Pod gérées ici :
  • Auth par en-tête  Authorization: Token <token>
  • Les relations (owner, type, additional_owners, channel, theme…) sont des
    URLs, pas des IDs
  • Upload multipart en STREAMING (gros fichiers, sans charger en RAM)
  • L'upload ne lance PAS l'encodage → appel séparé launch_encode_view

⚠️ Cette appli vise un compte SUPERUTILISATEUR (lister les comptes, agir sur
   des vidéos qui ne vous appartiennent pas, créer des chaînes…).
"""

from __future__ import annotations

__author__      = "Cédric MONNA, Philippe BAQUÉ, Michel JACOB"
__contact__     = "support-pod@utoulouse.fr"
__institution__ = "Université de Toulouse"
__version__     = "3.0.0"
__date__        = "2026"
__license__     = "Usage interne — Université de Toulouse"


import os
import re
import time
import requests
from urllib.parse import urlsplit
from typing import Callable, Optional

try:
    # Permet un upload streamé avec callback de progression
    from requests_toolbelt.multipart.encoder import (
        MultipartEncoder, MultipartEncoderMonitor
    )
    HAS_TOOLBELT = True
except ImportError:
    HAS_TOOLBELT = False

# Délai des envois de fichier (POST de création, PATCH de remplacement) :
# 30 s pour ÉTABLIR la connexion, puis 600 s maximum SANS RECEVOIR UN OCTET.
# Ce n'est pas une durée totale : un envoi de plusieurs Go qui progresse ne
# l'atteint jamais. Avec timeout=None, une connexion bloquée attendait
# indéfiniment et la relance automatique (3 essais) ne se déclenchait jamais,
# puisqu'aucune exception n'était levée.
UPLOAD_TIMEOUT = (30, 600)


class PodAPIError(Exception):
    """Erreur renvoyée par l'API Pod (avec code HTTP et corps de réponse)."""
    def __init__(self, message: str, status: int = 0, body: str = ""):
        # status : code HTTP (401, 403, 400…) ; body : corps de réponse (diagnostic).
        super().__init__(message)
        self.status = status
        self.body = body


class EnvoiAnnule(Exception):
    """Envoi arrêté à la demande de l'utilisateur — ce n'est PAS une erreur.
    (Repris de PodAdmin 1.9.2.)

    Distincte de `PodAPIError` pour ne jamais être confondue avec une panne :
    une annulation ne doit ni déclencher le repli sur l'envoi par morceaux, ni
    compter parmi les « échecs » à relancer.

    `a_verifier` : vrai si la vidéo a PEUT-ÊTRE été créée malgré l'arrêt
    (attente interrompue après un 504) — la relancer créerait un doublon."""
    def __init__(self, message: str = "Envoi interrompu à votre demande.",
                 a_verifier: bool = False):
        super().__init__(message)
        self.a_verifier = a_verifier


class PodAPI:
    """Client de l'API REST Esup-Pod, authentifié par TOKEN.

    Toutes les opérations « métadonnées » passent par ici : lister les types,
    chaînes, thèmes, utilisateurs ; créer/mettre à jour des vidéos ; lancer
    l'encodage ; poser des crédits. Les relations (owner, type, sites…) sont
    exprimées par des URLs absolues, jamais par des identifiants numériques."""

    def __init__(self, base_url: str, token: str, verify_ssl: bool = True):
        # base_url : racine de l'instance (ex. https://videos.utoulouse.fr)
        # token    : jeton d'API ; il hérite des droits du compte qui l'a généré.
        # verify_ssl : vérification du certificat TLS (True en production).
        self.base_url = base_url.rstrip("/")
        self.rest = f"{self.base_url}/rest"          # racine de l'API REST (/rest)
        # Hôte de référence : seules les URL absolues de CET hôte reçoivent le
        # jeton (voir _abs).
        self._netloc = urlsplit(self.base_url).netloc.lower()
        self.token = token
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        # Le token est injecté une fois pour toutes dans l'en-tête Authorization.
        self.session.headers.update({"Authorization": f"Token {token}"})

    # ╔══════════════════════════════════════════════════════════════════╗
    # ║  HELPERS                                                          ║
    # ╚══════════════════════════════════════════════════════════════════╝

    def _abs(self, endpoint_or_url: str) -> str:
        """Accepte un endpoint relatif (/videos/) OU une URL absolue de l'API.

        Toute requête de la session porte le jeton (en-tête Authorization).
        Or les URL absolues viennent souvent du SERVEUR : champ `next` de la
        pagination, relations `owner`, `url` d'une vidéo, d'une chaîne, d'une
        piste… Les suivre aveuglément enverrait le jeton à n'importe quel hôte
        qu'une réponse désignerait, ou en clair si elle était en http://.
        On n'accepte donc une URL absolue que si elle est en HTTPS et vise
        EXACTEMENT l'hôte de l'instance configurée ; sinon PodAPIError, avant
        toute émission."""
        s = str(endpoint_or_url)
        parties = urlsplit(s)
        if not (parties.scheme or parties.netloc):
            return f"{self.rest}{s}"                 # chemin relatif à l'API
        if parties.scheme.lower() != "https":
            raise PodAPIError(
                f"URL refusée (schéma « {parties.scheme or '?'} », HTTPS exigé) : {s}")
        if parties.netloc.lower() != self._netloc:
            raise PodAPIError(
                f"URL refusée (hôte « {parties.netloc} » différent de l'instance "
                f"« {self._netloc} ») : {s}")
        return s

    def _json(self, resp: requests.Response):
        """Transforme une réponse HTTP en données Python.
        Lève PodAPIError si le code est >= 400 (en conservant le corps pour
        diagnostic) ; renvoie sinon le JSON décodé, le texte brut, ou None."""
        if resp.status_code >= 400:
            raise PodAPIError(
                f"HTTP {resp.status_code} sur {resp.url}",
                status=resp.status_code,
                body=resp.text[:1000],
            )
        if resp.text:
            try:
                return resp.json()
            except ValueError:
                return resp.text
        return None

    def _get(self, endpoint: str, params: dict | None = None):
        """Requête GET (lecture) sur l'API. `params` = filtres de requête."""
        r = self.session.get(self._abs(endpoint), params=params,
                             headers={"Accept": "application/json"},
                             timeout=30, verify=self.verify_ssl)
        return self._json(r)

    def _post(self, endpoint: str, json=None, data=None):
        """Requête POST (création). Corps en JSON ou en form-data selon l'appel."""
        r = self.session.post(self._abs(endpoint), json=json, data=data,
                             headers={"Accept": "application/json"},
                             timeout=30, verify=self.verify_ssl)
        return self._json(r)

    def _patch(self, endpoint: str, json=None, data=None):
        """Requête PATCH (mise à jour PARTIELLE : seuls les champs fournis changent)."""
        r = self.session.patch(self._abs(endpoint), json=json, data=data,
                             headers={"Accept": "application/json"},
                             timeout=30, verify=self.verify_ssl)
        return self._json(r)

    def _delete(self, endpoint: str):
        """Requête DELETE (suppression). Renvoie True si le serveur répond 204."""
        r = self.session.delete(self._abs(endpoint),
                             headers={"Accept": "application/json"},
                             timeout=30, verify=self.verify_ssl)
        if r.status_code >= 400:
            raise PodAPIError(f"HTTP {r.status_code} sur {r.url}",
                              status=r.status_code, body=r.text[:1000])
        return True  # 204 No Content attendu en cas de succès

    def _options(self, endpoint: str) -> dict:
        """Requête OPTIONS : renvoie le SCHÉMA d'une ressource (champs requis,
        types…). Sert à découvrir ce qu'une instance attend réellement."""
        r = self.session.options(self._abs(endpoint),
                             headers={"Accept": "application/json"},
                             timeout=20, verify=self.verify_ssl)
        return self._json(r) or {}

    def _paginate(self, endpoint: str, params: dict | None = None,
                  max_pages: int = 300,
                  progress_cb: Optional[Callable[[int], None]] = None) -> list[dict]:
        """Suit le champ 'next' jusqu'au bout. progress_cb(nb_cumulé) optionnel."""
        items: list[dict] = []
        url = self._abs(endpoint)
        p = dict(params or {})
        p.setdefault("limit", 100)
        pages = 0
        while url and pages < max_pages:
            r = self.session.get(url, params=(p if pages == 0 else None),
                                 headers={"Accept": "application/json"},
                                 timeout=30, verify=self.verify_ssl)
            data = self._json(r)
            if isinstance(data, dict):
                items.extend(data.get("results", []))
                # URL absolue de la page suivante, fournie par le serveur :
                # validée par _abs (même hôte, HTTPS) AVANT la requête
                # suivante, qui porterait sinon le jeton n'importe où.
                suivante = data.get("next")
                url = self._abs(suivante) if suivante else None
            else:
                items.extend(data or [])
                url = None
            pages += 1
            if progress_cb:
                progress_cb(len(items))
        return items

    # ╔══════════════════════════════════════════════════════════════════╗
    # ║  TÉLÉVERSEUR (conservé tel quel)                                  ║
    # ╚══════════════════════════════════════════════════════════════════╝

    # ── 0. Connexion ──────────────────────────────────────────────────────

    def test_connection(self) -> int:
        """Renvoie le nombre de vidéos accessibles (valide le token)."""
        data = self._get("/videos/", {"limit": 1})
        return data.get("count", 0) if isinstance(data, dict) else 0

    # ── 1. Utilisateurs (résolution owner) ────────────────────────────────

    def search_users(self, query: str) -> list[dict]:
        """Recherche des utilisateurs → liste de dicts {username, url, ...}."""
        data = self._get("/users/", {"search": query, "limit": 25})
        return data.get("results", []) if isinstance(data, dict) else (data or [])

    def find_user_by_username(self, username: str) -> dict | None:
        """Le compte dont le nom d'utilisateur est EXACTEMENT `username`, ou None.

        Filtre serveur `?username=` : la sonde verifier_identifiant.py a établi
        qu'il renvoie exactement le compte demandé, alors que `?search=` est
        plein texte (12 comptes pour un identifiant complet) et que
        `?username__iexact=` est ignoré. On vérifie malgré tout l'égalité côté
        client : si le serveur ignorait un jour le filtre, il renverrait tout
        l'annuaire, et le premier résultat ne serait pas le bon compte. Plus
        d'un compte exact → None (ambigu : on refuse plutôt que deviner)."""
        cible = str(username or "").strip().lower()
        if not cible:
            return None
        data = self._get("/users/", {"username": cible, "limit": 5})
        comptes = data.get("results", []) if isinstance(data, dict) else (data or [])
        exacts = [u for u in comptes
                  if str(u.get("username", "")).strip().lower() == cible]
        return exacts[0] if len(exacts) == 1 else None

    def get_all_users(self, max_pages: int = 80) -> list[dict]:
        """Récupère TOUS les utilisateurs en suivant la pagination de l'API."""
        return self._paginate("/users/", {"limit": 100}, max_pages=max_pages)

    def whoami(self) -> dict | None:
        """Tente d'identifier, de façon SÛRE, le compte PROPRIÉTAIRE du token.

        Esup-Pod n'expose AUCUN endpoint « utilisateur courant » (pas de /me) :
        vérifié dans la documentation officielle v4. On déduit donc l'identité
        par des indices — mais on ne renvoie un compte QUE s'il n'existe qu'UN
        SEUL candidat possible. Objectif : ne JAMAIS risquer d'attribuer un
        dépôt au mauvais enseignant (une mésattribution serait pire que pas de
        détection du tout).

        • Piste 1 — si /rest/users/ ne renvoie qu'UN SEUL compte, c'est
          forcément le propriétaire du token (cas d'un token personnel non-admin
          que l'API restreint à lui-même). Indice fiable à 100 %.

        • Piste 2 — sinon, si TOUTES les vidéos visibles ont un UNIQUE
          propriétaire, c'est très probablement lui (token personnel qui ne voit
          que ses propres vidéos). Indice probable (voir réserve ci-dessous).

        Renvoie le dict du compte {username, url, ...} ou None si indéterminé.
        Un token ADMIN ou STAFF qui voit plusieurs comptes / plusieurs
        propriétaires renvoie None → on conserve le choix manuel.

        ⚠️ Réserve sur la Piste 2 : un compte « équipe » (is_staff) peut, selon
        la configuration de l'instance, voir les vidéos d'AUTRES utilisateurs.
        Si un tel token ne voyait qu'une seule vidéo et qu'elle appartenait à un
        collègue, la Piste 2 se tromperait. C'est pourquoi l'appli traite la
        Piste 2 comme une SUGGESTION (à confirmer) et non comme une attribution
        automatique. Seule la Piste 1 déclenche l'attribution automatique.
        Le diagnostic verifier.py (section 6) permet de mesurer le comportement
        réel d'un token staff sur l'instance.
        """
        # ── Piste 1 : un seul compte visible ? ──────────────────────────────
        try:
            data = self._get("/users/", {"limit": 2})
            if isinstance(data, dict):
                if data.get("count") == 1 and data.get("results"):
                    u = dict(data["results"][0])
                    u["_detection"] = "piste1"   # fiable → attribution auto
                    return u
        except Exception:
            pass

        # ── Piste 2 : un unique propriétaire parmi les vidéos visibles ? ─────
        try:
            data = self._get("/videos/", {"limit": 100})
            vids = data.get("results", []) if isinstance(data, dict) else (data or [])
            owners = set()
            for v in vids:
                o = v.get("owner")
                # 'owner' peut être une URL (str) ou un objet imbriqué {url:…}
                if isinstance(o, dict):
                    o = o.get("url") or o.get("username")
                if o:
                    owners.add(o)
            if len(owners) == 1:
                ref = next(iter(owners))
                # Si c'est une URL de compte, on récupère le username pour l'affichage
                if isinstance(ref, str) and ref.startswith("http"):
                    try:
                        u = self._get(ref)        # _get accepte une URL absolue
                        if isinstance(u, dict) and u.get("username"):
                            u = dict(u)
                            u["_detection"] = "piste2"   # probable → suggestion
                            return u
                    except Exception:
                        pass
                    return {"username": "", "url": ref, "_detection": "piste2"}
        except Exception:
            pass

        return None

    # ── 2. Types / chaînes / sites ────────────────────────────────────────

    def get_types(self) -> list[dict]:
        """Liste les TYPES de vidéo de l'instance (ex. Cours, Conférence…)."""
        data = self._get("/types/", {"limit": 100})
        return data.get("results", []) if isinstance(data, dict) else (data or [])

    def get_channels(self) -> list[dict]:
        """Liste toutes les CHAÎNES (paginé)."""
        return self._paginate("/channels/", {"limit": 200})

    def get_sites(self) -> list[dict]:
        """Sites de l'instance (requis pour l'upload multi-établissements)."""
        data = self._get("/sites/", {"limit": 100})
        return data.get("results", []) if isinstance(data, dict) else (data or [])

    # ── 3. Upload d'une vidéo (streaming + progression) ───────────────────

    def upload_video(
        self,
        file_path: str,
        title: str,
        owner_url: str,
        type_url: str,
        *,
        main_lang: str = "fr",
        cursus: str = "0",
        is_draft: bool = True,
        description: str = "",
        additional_owner_urls: Optional[list[str]] = None,
        site_urls: Optional[list[str]] = None,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        retry_cb: Optional[Callable[[int, int, str], None]] = None,
        max_attempts: int = 3,
        annuler: Optional[Callable[[], bool]] = None,
    ) -> dict:
        """
        Téléverse une vidéo. Renvoie le dict de la vidéo créée (avec 'slug', 'url').

        progress_cb(bytes_envoyés, bytes_total) est appelé pendant l'envoi.
        N'amorce PAS l'encodage (voir launch_encoding).

        annuler() : consulté à chaque bloc lu pendant l'envoi (avec
        requests-toolbelt) et avant chaque tentative ; s'il renvoie vrai,
        l'envoi s'arrête aussitôt (EnvoiAnnule). Le fichier n'étant pas arrivé
        en entier, le serveur ne crée aucune vidéo. Une fois tout le fichier
        transmis, l'attente de la réponse n'est plus interruptible (bornée par
        UPLOAD_TIMEOUT).

        RELANCE AUTOMATIQUE (nouveauté) : les gros fichiers échouent parfois à
        cause d'une coupure réseau/SSL transitoire en cours d'envoi (ex.
        « EOF occurred in violation of protocol », « Max retries exceeded »).
        Ce n'est pas un bug du code : c'est la connexion qui tombe. On réessaie
        donc automatiquement jusqu'à `max_attempts` fois, avec une pause
        croissante (2 s, 4 s…). À chaque nouvelle tentative :
          • on ROUVRE le fichier et on RECONSTRUIT l'encodeur MultipartEncoder,
            car le flux de la tentative précédente a déjà été consommé ;
          • on appelle retry_cb(tentative, total, message) — s'il est fourni —
            pour tracer la relance dans le Journal et mettre à jour le statut
            de la vidéo (ex. « ⟳ essai 2 »).
        Après épuisement des tentatives, on lève une PodAPIError explicite.

        NOTE : si un fichier échoue SYSTÉMATIQUEMENT aux 3 tentatives, ce n'est
        pas une coupure passagère mais une limite plus dure (taille, quota,
        proxy). La vraie solution serait alors l'upload par morceaux (chunked,
        drf-chunked-upload) — hors périmètre de cette relance simple.
        """
        if not os.path.isfile(file_path):
            raise PodAPIError(f"Fichier introuvable : {file_path}")

        # Champs communs à toutes les tentatives (hors fichier, rouvert à chaque fois).
        base_fields = [
            ("owner", owner_url),
            ("type", type_url),
            ("title", title[:250]),
            ("main_lang", main_lang),
            ("cursus", str(cursus)),
            ("is_draft", "true" if is_draft else "false"),
        ]
        if description:
            base_fields.append(("description", description))
        for url in (additional_owner_urls or []):
            base_fields.append(("additional_owners", url))
        for url in (site_urls or []):
            base_fields.append(("sites", url))

        filename = os.path.basename(file_path)

        # Erreurs considérées comme « transitoires » → on retente.
        transient_errors = (
            requests.exceptions.ConnectionError,   # inclut SSLError (sous-classe)
            requests.exceptions.SSLError,
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.Timeout,
        )

        last_exc = None
        for attempt in range(1, max_attempts + 1):
            if annuler and annuler():
                raise EnvoiAnnule()
            # Une tentative = ouvrir le fichier, (re)construire l'encodeur, POSTer.
            f = open(file_path, "rb")
            try:
                fields = list(base_fields)
                fields.append(("video", (filename, f, "application/octet-stream")))

                if HAS_TOOLBELT:
                    encoder = MultipartEncoder(fields=fields)
                    total = encoder.len

                    def _cb(monitor):
                        # Callback de progression du flux multipart : remonte le
                        # nombre d'octets déjà lus/envoyés au reste de l'appli.
                        # Appelé par la lecture même du flux : lever ici arrête
                        # l'envoi au bloc suivant, sans attendre la fin du fichier.
                        if annuler and annuler():
                            raise EnvoiAnnule()
                        if progress_cb:
                            progress_cb(monitor.bytes_read, total)

                    monitor = MultipartEncoderMonitor(encoder, _cb)
                    headers = {"Content-Type": monitor.content_type}
                    r = self.session.post(f"{self.rest}/videos/", data=monitor,
                                         headers=headers, timeout=UPLOAD_TIMEOUT,
                                         verify=self.verify_ssl)
                else:
                    # Repli sans streaming (charge en mémoire) si toolbelt absent
                    files = {"video": (filename, f, "application/octet-stream")}
                    data = {k: v for k, v in fields if k != "video"}
                    r = self.session.post(f"{self.rest}/videos/", data=data,
                                         files=files, timeout=UPLOAD_TIMEOUT,
                                         verify=self.verify_ssl)
                # Succès réseau : on renvoie le JSON (peut lever si code HTTP >= 400,
                # mais ce n'est PAS une erreur transitoire → pas de relance).
                return self._json(r)

            except transient_errors as e:
                # Coupure réseau/SSL : on retente si des essais restent.
                last_exc = e
                if attempt < max_attempts:
                    if retry_cb:
                        retry_cb(attempt, max_attempts,
                                 f"coupure réseau/SSL, nouvel essai dans "
                                 f"{2 * attempt} s…")
                    time.sleep(2 * attempt)   # pause croissante : 2 s, puis 4 s
                    continue
                # Plus d'essai disponible → on sort de la boucle pour lever l'erreur.
            finally:
                f.close()

        # Toutes les tentatives ont échoué sur une erreur transitoire.
        raise PodAPIError(
            f"Échec après {max_attempts} tentatives (coupure réseau/SSL). "
            f"Dernière erreur : {last_exc}")

    # ── 4. Lancer l'encodage ──────────────────────────────────────────────

    def launch_encoding(self, slug: str):
        return self._get("/launch_encode_view/", {"slug": slug})

    # ── 5. Propriétaires additionnels (PATCH) ─────────────────────────────

    def set_additional_owners(self, video, owner_urls: list[str]) -> dict:
        """Remplace la liste des propriétaires additionnels d'une vidéo
        (réf = dict vidéo, URL, id ou slug)."""
        return self._patch(self._video_endpoint(video),
                           json={"additional_owners": list(owner_urls)})

    # ── 6. Contributeurs (crédits) ────────────────────────────────────────

    def add_contributor(self, video_url: str, name: str, email: str = "",
                       role: str = "author", weblink: str = "") -> dict:
        """Ajoute un CONTRIBUTEUR (crédit libre) à une vidéo : nom + rôle +
        e-mail + lien. N'a pas besoin d'être un compte Pod."""
        data = {
            "video": video_url,
            "name": name,
            "email_address": email,
            "role": role,
            "weblink": weblink,
        }
        r = self.session.post(f"{self.rest}/contributors/", data=data,
                            timeout=30, verify=self.verify_ssl)
        return self._json(r)

    def get_contributors(self, video_id: int) -> list[dict]:
        """Liste les contributeurs (crédits) déjà posés sur une vidéo."""
        data = self._get("/contributors/", {"video": video_id})
        return data.get("results", []) if isinstance(data, dict) else (data or [])

    # ╔══════════════════════════════════════════════════════════════════╗
    # ║  ADMINISTRATION (nouveau)                                         ║
    # ╚══════════════════════════════════════════════════════════════════╝

    # ── A. Comptes — lecture seule ────────────────────────────────────────
    # set_user_staff / set_user_groups ont été retirées (3.4.2) : jamais
    # appelées ici, elles donnaient à une appli d'enseignants le moyen de
    # modifier les droits d'un compte. Elles relèvent de PodAdmin.

    def get_user(self, user_url: str) -> dict:
        """Détail d'un compte à partir de son URL (champ 'url' du compte)."""
        return self._get(user_url)

    # ── B. Vidéos en masse — inventaire, réaffectation, nettoyage ─────────
    # Diagnostic : PATCH + DELETE autorisés, owner / is_draft modifiables.
    # Champs de statut utiles : encoded, encoding_in_progress,
    #                           get_encoding_step, is_draft, duration.

    def get_all_videos(self, max_pages: int = 300,
                       progress_cb: Optional[Callable[[int], None]] = None,
                       extra_params: dict | None = None) -> list[dict]:
        """Scan complet de l'instance (paginé). progress_cb(nb_cumulé) optionnel."""
        return self._paginate("/videos/", extra_params, max_pages, progress_cb)

    def search_videos(self, params: dict) -> list[dict]:
        """GET filtré sur /videos/ (ex. {'owner': <url>} ou {'search': 'mot'})."""
        data = self._get("/videos/", params)
        return data.get("results", []) if isinstance(data, dict) else (data or [])

    def get_video_by_slug(self, slug: str):
        """Retrouve le dict d'une vidéo à partir de son SLUG (None si introuvable).
        Nécessaire après une création par upload chunké (qui ne renvoie qu'un slug)
        pour récupérer l'objet complet (avec son 'url') et pouvoir le PATCHer.
        Lecture seule : filtre serveur ?slug= puis recherche ?search= ; dans les
        deux cas, correspondance EXACTE du slug."""
        slug = str(slug).strip().strip("/")
        if not slug:
            return None
        for params in ({"slug": slug, "limit": 20}, {"search": slug, "limit": 20}):
            try:
                results = self.search_videos(params)
            except Exception:
                results = []
            for v in results:
                if str(v.get("slug", "")).strip("/") == slug:
                    return v
        return None

    # ── Résolution de l'endpoint d'une vidéo ──────────────────────────────
    # IMPORTANT : l'API indexe les vidéos par ID NUMÉRIQUE (/rest/videos/108/),
    # PAS par slug. On accepte donc une « référence » souple :
    #   • un dict vidéo  -> on prend son champ 'url' (absolu, fourni par l'API)
    #   • une URL (http) -> utilisée telle quelle
    #   • un id numérique-> /videos/<id>/
    #   • à défaut, un slug (peu fiable : peut donner 404 sur le détail)
    def _video_endpoint(self, ref) -> str:
        """Construit l'URL de détail d'une vidéo à partir d'une référence souple."""
        if isinstance(ref, dict):
            if ref.get("url"):
                return ref["url"]                 # URL absolue donnée par l'API
            ref = ref.get("id")                   # repli sur l'id numérique
        s = str(ref)
        if s.startswith("http"):
            return s                              # déjà une URL absolue
        return f"/videos/{s}/"                     # id numérique (ou slug en dernier recours)

    def get_video(self, video) -> dict:
        """Détail d'une vidéo (réf = dict vidéo, URL, id ou slug)."""
        return self._get(self._video_endpoint(video))

    def patch_video(self, video, payload: dict) -> dict:
        """PATCH générique d'une vidéo (réf = dict vidéo, URL, id ou slug).
        Les relations (owner, channel…) sont des URLs ou des listes d'URLs."""
        return self._patch(self._video_endpoint(video), json=payload)

    def replace_video_file(self, video, file_path: str, *,
                           progress_cb: Optional[Callable[[int, int], None]] = None,
                           retry_cb: Optional[Callable[[int, int, str], None]] = None,
                           max_retries: int = 3) -> dict:
        """Remplace le fichier source d'une vidéo EXISTANTE par PATCH multipart streamé.

        Le champ `video` d'une vidéo est modifiable (confirmé par OPTIONS) : on
        envoie un nouveau fichier sur l'URL de détail de la vidéo. Tout le reste
        (slug, titre, chaînes, droits, propriétaire…) est CONSERVÉ : seul le
        média change.

        ⚠️ Cette voie « PATCH direct » convient aux fichiers SOUS le seuil de
        bascule chunké (cf. config.CHUNK_THRESHOLD_BYTES). Au-delà, la passerelle
        nginx coupe la requête monobloc (502) : il faut passer par le
        remplacement CHUNKÉ (PodChunkedSession.upload_video_chunked avec
        target_slug=<slug>), géré côté application.

        Robustesse identique à l'upload : envoi STREAMÉ (le fichier n'est jamais
        chargé entièrement en mémoire) + ré-essais sur coupure réseau/SSL. Le
        fichier est ré-ouvert à CHAQUE tentative (un flux déjà lu ne peut pas
        être rejoué).

        N'AMORCE PAS l'encodage : appeler launch_encoding(slug) ensuite.

        Paramètres :
          • video        : référence souple (dict vidéo, URL, id ou slug) ;
          • file_path    : chemin du nouveau fichier vidéo ;
          • progress_cb  : callback(octets_envoyés, octets_total) — progression ;
          • retry_cb     : callback(tentative, total, message) — sur ré-essai ;
          • max_retries  : nombre de tentatives avant abandon (défaut 3).
        """
        if not os.path.isfile(file_path):
            raise PodAPIError(f"Fichier introuvable : {file_path}")
        endpoint = self._video_endpoint(video)
        # L'endpoint peut être relatif (/videos/<id>/) ou déjà absolu (champ
        # `url` renvoyé par le serveur) : _abs construit l'URL complète et
        # refuse un hôte étranger ou du http://, car ce PATCH porte le jeton.
        target = self._abs(endpoint)
        filename = os.path.basename(file_path)

        def _one_attempt():
            # Une tentative = ouverture fraîche du fichier + envoi streamé.
            f = open(file_path, "rb")
            try:
                fields = [("video", (filename, f, "application/octet-stream"))]
                if HAS_TOOLBELT:
                    # Encodeur multipart streamé + moniteur de progression.
                    encoder = MultipartEncoder(fields=fields)
                    total = encoder.len

                    def _cb(monitor):
                        """Callback de progression du flux multipart (octets déjà envoyés)."""
                        if progress_cb:
                            progress_cb(monitor.bytes_read, total)

                    monitor = MultipartEncoderMonitor(encoder, _cb)
                    headers = {"Content-Type": monitor.content_type}
                    # Délai fini (UPLOAD_TIMEOUT) : un gros PATCH peut être long,
                    # mais tant que des octets circulent le délai de lecture ne
                    # court pas ; une connexion figée lève Timeout, ce qui
                    # déclenche les ré-essais ci-dessous au lieu d'attendre à
                    # l'infini.
                    r = self.session.patch(target, data=monitor, headers=headers,
                                           timeout=UPLOAD_TIMEOUT, verify=self.verify_ssl)
                else:
                    # Repli sans requests-toolbelt : pas de progression fine,
                    # mais l'envoi fonctionne (requests gère le multipart).
                    files = {"video": (filename, f, "application/octet-stream")}
                    r = self.session.patch(target, files=files,
                                           timeout=UPLOAD_TIMEOUT, verify=self.verify_ssl)
                return self._json(r)
            finally:
                f.close()

        # Erreurs « transitoires » : on ré-essaie (le serveur/le réseau peut
        # avoir hoqueté). Les erreurs HTTP 4xx/5xx « logiques » sont levées par
        # _json et ne sont PAS ré-essayées ici.
        transient = (
            requests.exceptions.ConnectionError,
            requests.exceptions.SSLError,
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.Timeout,
        )
        for attempt in range(1, max_retries + 1):
            try:
                return _one_attempt()
            except transient as e:
                if attempt < max_retries:
                    if retry_cb:
                        retry_cb(attempt, max_retries, str(e)[:120])
                    time.sleep(2 * attempt)      # petit délai croissant
                    continue
                raise PodAPIError(
                    f"Échec après {max_retries} tentatives (coupure réseau/SSL). "
                    f"Dernière erreur : {e}", 0, str(e))

    def set_video_owner(self, video, owner_url: str,
                        additional_owner_urls: Optional[list[str]] = None) -> dict:
        """Réaffecte le propriétaire (et, en option, les co-propriétaires)."""
        payload: dict = {"owner": owner_url}
        if additional_owner_urls is not None:
            payload["additional_owners"] = list(additional_owner_urls)
        return self.patch_video(video, payload)

    def set_video_draft(self, video, value: bool) -> dict:
        """Bascule une vidéo en brouillon (True) ou non (False)."""
        return self.patch_video(video, {"is_draft": bool(value)})

    def set_video_restricted(self, video, value: bool) -> dict:
        """Bascule l'accès restreint (connexion requise) d'une vidéo."""
        return self.patch_video(video, {"is_restricted": bool(value)})

    def assign_video_to_channels(self, video, channel_urls: list[str],
                                 theme_urls: Optional[list[str]] = None) -> dict:
        """Place une vidéo dans une/des chaîne(s) (et thème(s)) — champs M2M."""
        payload: dict = {"channel": list(channel_urls)}
        if theme_urls is not None:
            payload["theme"] = list(theme_urls)
        return self.patch_video(video, payload)

    def delete_video(self, video) -> bool:
        """⚠️ Suppression définitive d'une vidéo (DELETE)."""
        return self._delete(self._video_endpoint(video))

    # Aides au module Nettoyage (logique pure, testable sans réseau) ───────

    @staticmethod
    def is_unencoded(video: dict) -> bool:
        """Vidéo jamais encodée et pas en cours d'encodage."""
        return (not video.get("encoded", False)
                and not video.get("encoding_in_progress", False))

    @staticmethod
    def is_stale_draft(video: dict, before_iso: str) -> bool:
        """Brouillon dont la date d'ajout est antérieure à before_iso (AAAA-MM-JJ)."""
        return bool(video.get("is_draft")) and \
            str(video.get("date_added", ""))[:10] < before_iso

    # ── C. Chaînes & thèmes ───────────────────────────────────────────────
    # Diagnostic : POST autorisé. Chaîne requiert title + themes ;
    #              thème requiert title + channel (URL) ; thèmes hiérarchiques
    #              via parentId.

    def get_disciplines(self) -> list[dict]:
        """Liste les disciplines de l'instance (reprise de PodAdmin)."""
        data = self._get("/discipline/", {"limit": 200})
        return data.get("results", []) if isinstance(data, dict) else (data or [])

    def set_disciplines(self, video, discipline_urls: list[str]) -> dict:
        """Remplace les disciplines d'une vidéo (réf = dict, URL, id ou slug).

        ⚠️ Relation MULTIPLE : on envoie une LISTE d'URLs, même pour une seule
        discipline. Établi par sonde dans PodAdmin — une URL nue est refusée.
        """
        return self._patch(self._video_endpoint(video),
                           json={"discipline": list(discipline_urls)})

    def get_themes(self) -> list[dict]:
        """Liste tous les THÈMES (sous-catégories de chaînes), paginé."""
        return self._paginate("/themes/", {"limit": 300})

    def create_channel(self, title: str, theme_urls: Optional[list[str]] = None, *,
                       description: str = "", visible: bool = True,
                       site_url: Optional[str] = None,
                       owner_urls: Optional[list[str]] = None, **extra) -> dict:
        """Crée une chaîne. 'themes' est requis par l'API (liste, vide si aucune)."""
        payload: dict = {"title": title, "themes": list(theme_urls or [])}
        if description:
            payload["description"] = description
        payload["visible"] = bool(visible)
        if site_url:
            payload["site"] = site_url
        if owner_urls is not None:
            payload["owners"] = list(owner_urls)
        payload.update(extra)
        return self._post("/channels/", json=payload)

    def patch_channel(self, channel: str, payload: dict) -> dict:
        """Met à jour une chaîne (réf = URL ou id). `payload` = champs à changer."""
        ep = channel if str(channel).startswith("http") else f"/channels/{channel}/"
        return self._patch(ep, json=payload)

    def create_theme(self, title: str, channel_url: str, *,
                     parent_url: Optional[str] = None,
                     description: str = "", **extra) -> dict:
        """Crée un thème rattaché à une chaîne (channel_url). parent_url = sous-thème."""
        payload: dict = {"title": title, "channel": channel_url}
        if parent_url:
            payload["parentId"] = parent_url
        if description:
            payload["description"] = description
        payload.update(extra)
        return self._post("/themes/", json=payload)

    def patch_theme(self, theme: str, payload: dict) -> dict:
        """Met à jour un thème (réf = URL ou id). `payload` = champs à changer."""
        ep = theme if str(theme).startswith("http") else f"/themes/{theme}/"
        return self._patch(ep, json=payload)

    def delete_channel(self, channel: str) -> bool:
        """⚠️ Supprime une chaîne (et ses thèmes côté serveur). DELETE."""
        ep = channel if str(channel).startswith("http") else f"/channels/{channel}/"
        return self._delete(ep)

    def delete_theme(self, theme: str) -> bool:
        """⚠️ Supprime un thème. DELETE."""
        ep = theme if str(theme).startswith("http") else f"/themes/{theme}/"
        return self._delete(ep)

    # ── C-bis. Sous-titres (tracks + files + folders) ─────────────────────
    # Diagnostic (sondes) : l'API expose /rest/tracks/ (POST autorisé) dont le
    # champ `src` pointe vers un fichier /rest/files/<id>/. Déposer un sous-titre
    # se fait donc en 3 temps :
    #   1) trouver (ou créer) le DOSSIER de la vidéo  -> /rest/folders/ (name = slug)
    #   2) TÉLÉVERSER le .vtt dans ce dossier         -> /rest/files/ (multipart)
    #   3) créer la PISTE qui pointe vers ce fichier  -> /rest/tracks/
    # Valeurs confirmées : kind ∈ {subtitles, captions} ; lang = code ISO 2 lettres.
    # Le fichier déposé doit être du WebVTT ; un .srt est converti à la volée.

    def get_tracks(self, video) -> list[dict]:
        """Liste les pistes de sous-titres/légendes d'une vidéo.
        `video` = dict vidéo (ou URL/id). On filtre sur l'URL de la vidéo."""
        vurl = str(self._video_endpoint(video)).rstrip("/")
        out = []
        # /tracks/ ne propose pas forcément de filtre serveur : on parcourt et on trie.
        for t in self._paginate("/tracks/"):
            if str(t.get("video")).rstrip("/") == vurl:
                out.append(t)
        return out

    def find_or_create_folder(self, name: str, owner_url: str) -> str:
        """Renvoie l'URL du dossier `name` (= slug de la vidéo) appartenant à
        `owner_url`. Le crée s'il n'existe pas. Les fichiers d'une vidéo sont
        rangés dans un dossier portant son slug, propriété de son propriétaire."""
        owner_n = str(owner_url).rstrip("/")
        # Recherche : on parcourt les dossiers et on compare nom + propriétaire.
        for f in self._paginate("/folders/"):
            if f.get("name") == name and str(f.get("owner")).rstrip("/") == owner_n:
                return f.get("url")
        # Absent -> création (POST /folders/ : name + owner suffisent)
        created = self._post("/folders/", json={"name": name, "owner": owner_url})
        return created.get("url")

    def upload_subtitle_file(self, folder_url: str, name: str, vtt_bytes: bytes,
                             filename: str, created_by_url: str) -> dict:
        """Téléverse un fichier .vtt (en mémoire) dans un dossier, via POST
        multipart sur /rest/files/. Renvoie le fichier créé (avec son `url`)."""
        # multipart : le binaire dans `files`, les champs relationnels dans `data`
        files = {"file": (filename, vtt_bytes, "text/vtt")}
        data = {"folder": folder_url, "name": name, "created_by": created_by_url}
        r = self.session.post(self._abs("/files/"), data=data, files=files,
                              headers={"Accept": "application/json"},
                              timeout=120, verify=self.verify_ssl)
        return self._json(r)

    def add_track(self, video, lang: str, kind: str = "subtitles",
                  file_url: Optional[str] = None) -> dict:
        """Crée une piste sur /rest/tracks/ liant une vidéo à un fichier de
        sous-titres. kind ∈ {subtitles, captions} ; lang = code ISO (fr, en…)."""
        payload: dict = {
            "video": self._video_endpoint(video),   # URL absolue de la vidéo
            "lang": lang,
            "kind": kind,
        }
        if file_url:
            payload["src"] = file_url                # URL du fichier /files/<id>/
        return self._post("/tracks/", json=payload)

    def delete_track(self, track) -> bool:
        """⚠️ Supprime une piste de sous-titres (DELETE /rest/tracks/<id>/).
        `track` = id, URL ou dict track."""
        if isinstance(track, dict):
            track = track.get("url") or track.get("id")
        ep = track if str(track).startswith("http") else f"/tracks/{track}/"
        return self._delete(ep)

    def add_subtitle(self, video: dict, lang: str, kind: str, file_path: str) -> dict:
        """Orchestration complète : ajoute un sous-titre à une vidéo à partir
        d'un fichier .vtt OU .srt (converti automatiquement).
        Enchaîne : lecture/conversion -> dossier -> upload -> création de piste.
        Renvoie la piste (track) créée. `video` doit contenir 'slug' et 'owner'."""
        # 1) Lecture du fichier. 'utf-8-sig' retire un éventuel BOM en tête.
        with open(file_path, "rb") as fh:
            raw = fh.read()
        text = raw.decode("utf-8-sig", errors="replace")

        # Conversion / validation selon l'extension
        if file_path.lower().endswith(".srt"):
            text = srt_to_vtt(text)                       # SRT -> WebVTT
        elif not text.lstrip().startswith("WEBVTT"):
            # Garde-fou : un .vtt valide commence par l'en-tête WEBVTT
            raise PodAPIError("Fichier .vtt invalide : il doit commencer par "
                              "l'en-tête « WEBVTT ».")
        vtt_bytes = text.encode("utf-8")

        # 2) Dossier de la vidéo (= son slug), appartenant à SON propriétaire
        slug = video.get("slug") or "video"
        owner_url = video.get("owner")
        if not owner_url:
            raise PodAPIError("Propriétaire de la vidéo introuvable (champ 'owner').")
        folder_url = self.find_or_create_folder(slug, owner_url)

        # 3) Téléversement du .vtt (nom à la convention Pod : subtitle_<lang>_<horodatage>)
        from datetime import datetime
        name = f"subtitle_{lang}_{datetime.now():%Y%m%d-%H%M%S}"
        f = self.upload_subtitle_file(folder_url, name, vtt_bytes,
                                      name + ".vtt", owner_url)

        # 4) Création de la piste qui référence le fichier téléversé
        return self.add_track(video, lang, kind, f.get("url"))

    # ── D. Découverte de schéma (OPTIONS) ─────────────────────────────────
    # Pour s'adapter à l'instance plutôt que supposer (réflexe « diagnostic »).

    def options_schema(self, endpoint: str, verb: str = "POST") -> dict:
        """Schéma des champs pour un verbe (POST création / PUT modification)."""
        return self._options(endpoint).get("actions", {}).get(verb, {})

    def required_fields(self, endpoint: str, verb: str = "POST") -> list[str]:
        """Liste des champs marqués 'required' par l'instance pour ce verbe."""
        schema = self.options_schema(endpoint, verb)
        return [name for name, meta in schema.items() if meta.get("required")]

    def allowed_methods(self, endpoint: str) -> str:
        """En-tête Allow (méthodes HTTP autorisées) d'un endpoint."""
        r = self.session.options(self._abs(endpoint),
                             headers={"Accept": "application/json"},
                             timeout=20, verify=self.verify_ssl)
        return r.headers.get("Allow", "")


# Rôles de contributeur usuels dans Esup-Pod
CONTRIBUTOR_ROLES = [
    "author", "actor", "designer", "consultant",
    "editor", "speaker", "soundman", "writer", "publisher",
]


# ── Sous-titres : conversion SRT -> WebVTT ────────────────────────────────
def srt_to_vtt(text: str) -> str:
    """Convertit un sous-titrage SRT en WebVTT.
    Deux seules différences en pratique :
      1) on ajoute l'en-tête « WEBVTT » en première ligne ;
      2) les millisecondes SRT utilisent une virgule (00:00:01,000) là où le
         VTT utilise un point (00:00:01.000)."""
    # Normalise les fins de ligne (Windows/Mac -> Unix)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Virgule -> point dans les codes temps (hh:mm:ss,mmm -> hh:mm:ss.mmm)
    text = re.sub(r"(\d\d:\d\d:\d\d),(\d{3})", r"\1.\2", text)
    # Ajoute l'en-tête WEBVTT s'il manque
    if not text.lstrip().startswith("WEBVTT"):
        text = "WEBVTT\n\n" + text.lstrip("\n")
    return text


# ── Sous-titres : langues acceptées par l'API (code ISO -> libellé) ───────
# Sous-ensemble usuel (l'API en accepte beaucoup plus ; voir verifier_tracks.py).
SUBTITLE_LANGS = [
    ("fr", "Français"), ("en", "Anglais"), ("es", "Espagnol"),
    ("de", "Allemand"), ("it", "Italien"), ("pt", "Portugais"),
    ("ar", "Arabe"), ("zh", "Chinois"), ("ru", "Russe"),
    ("ja", "Japonais"), ("nl", "Hollandais"), ("ca", "Catalan"),
    ("oc", "Occitan"),
]

# Types de piste acceptés par l'API (champ `kind`)
SUBTITLE_KINDS = [("subtitles", "Sous-titres"), ("captions", "Légendes")]
