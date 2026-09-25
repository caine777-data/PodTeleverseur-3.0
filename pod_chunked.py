#!/usr/bin/env python3
"""
pod_chunked.py — Téléversement par MORCEAUX (chunked) via session web Esup-Pod
=============================================================================
Module AUTONOME et réutilisable. Il n'a qu'un seul consommateur aujourd'hui
(la variante « PodAdmin eformation »), mais il est isolé dans son propre
fichier pour rester un « copier-coller propre » si un autre outil en a besoin
un jour. Il ne dépend PAS de pod_api.py : les deux mécanismes d'authentification
(token API d'un côté, session web de l'autre) sont volontairement séparés.

────────────────────────────────────────────────────────────────────────────
POURQUOI CE MODULE ?
    L'API REST monobloc (POST /rest/videos/ avec tout le fichier d'un coup) est
    coupée par nginx sur les GROS fichiers (SSLEOFError / 502 vers la fin).
    L'interface web de Pod, elle, passe : elle téléverse le fichier en MORCEAUX
    (chunks). Ce module reproduit EXACTEMENT ce que fait le navigateur.

CE QU'IL FAIT (protocole validé sur l'instance) :
    1. LOGIN LOCAL  — ouvre une session web (cookie sessionid) via le formulaire
       POST /accounts/login/ (identifiant + mot de passe + csrfmiddlewaretoken).
       ⚠️ Ne marche QU'AVEC un compte LOCAL. Les comptes SSO sont refusés par ce
       formulaire. Le compte « eformation » est local : c'est voulu.
    2. ENVOI DES MORCEAUX — POST /video/api/chunked_upload/, morceaux de 2 Mo,
       en multipart : champ fichier `the_file`, + csrfmiddlewaretoken, +
       upload_id (dès le 2e morceau, fourni par la réponse du 1er).
       En-têtes par morceau : Content-Range, Content-Disposition, X-CSRFToken,
       X-Requested-With, Referer, Origin. Réponse : {upload_id, offset, expires}.
    3. FINALISATION — POST /video/api/chunked_upload_complete/ (form-urlencoded)
       csrfmiddlewaretoken + upload_id + md5 (hex du fichier complet) + slug (vide)
       + transcript (vide). Réponse : {"redirlink": "/video/edit/<slug>/", ...}.
       → La vidéo est créée directement, au nom du COMPTE DE SESSION (eformation).

RÉSULTAT :
    upload_video_chunked(...) renvoie le SLUG de la vidéo créée. On peut ensuite,
    avec le TOKEN API (pod_api.py), poser les métadonnées par PATCH et lancer
    l'encodage — de sorte que le résultat final soit identique à un petit upload
    par token.

ROBUSTESSE :
    Chaque morceau ne fait que 2 Mo : en cas de coupure réseau/SSL, on RE-ENVOIE
    seulement ce morceau (jusqu'à `max_retries` fois, avec pause croissante), au
    lieu de relancer tout le fichier. C'est le cœur de la solution au 502.

⚠️ IMPORTANT — ce module CRÉE une vidéo réelle sur l'instance à chaque upload
   réussi (c'est le but). Pour un simple TEST du protocole, utilisez la sonde
   verifier_session_chunked.py sur l'instance de TEST, et supprimez ensuite la
   vidéo de sonde créée.
"""

from __future__ import annotations

__author__      = "Cédric MONNA"
# Adresse de service, pas une adresse personnelle : c'est elle que les
# utilisateurs doivent joindre, quel que soit le mainteneur du moment.
__contact__     = "support-pod@utoulouse.fr"
__institution__ = "Université de Toulouse — MFCA"
# Version lue dans la source unique, comme app.py et config.py : un « 0.1.0 »
# écrit en dur ici ne suivait plus aucune livraison.
from __version__ import __version__   # noqa: E402
__date__        = "2026"
__license__     = "Usage interne — Université de Toulouse"


import hashlib
import os
import re
import time
import unicodedata
from typing import Callable, Optional

import requests


# ── Constantes de protocole ───────────────────────────────────────────────
DEFAULT_CHUNK_SIZE = 2 * 1024 * 1024      # 2 Mo par morceau (valeur validée)

# Chemins (relatifs à la base de l'instance) du protocole web de Pod.
PATH_LOGIN            = "/accounts/login/"
PATH_VIDEO_ADD        = "/video/add/"                       # page « Ajouter une vidéo »
PATH_CHUNK_UPLOAD     = "/video/api/chunked_upload/"        # envoi des morceaux
PATH_CHUNK_COMPLETE   = "/video/api/chunked_upload_complete/"  # finalisation

# Erreurs réseau « transitoires » qui justifient de RE-ENVOYER un morceau.
TRANSIENT_ERRORS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.SSLError,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.Timeout,
)

# Codes HTTP de PASSERELLE (nginx) transitoires : le serveur amont (Pod) a mis
# trop de temps ou était momentanément indisponible. Renvoyer le MÊME morceau est
# sans risque (le serveur suit l'offset), donc on ré-essaie sur ces codes.
GATEWAY_ERRORS = (502, 503, 504)


class PodChunkedError(Exception):
    """Erreur du client chunké (login, envoi d'un morceau, finalisation…)."""
    def __init__(self, message: str, status: int = 0, body: str = ""):
        """Construit l'erreur chunkée (status = code HTTP, body = corps de réponse)."""
        super().__init__(message)
        self.status = status
        self.body = body


class PodChunkedSession:
    """Client de téléversement par morceaux via session web Esup-Pod.

    Usage typique :
        s = PodChunkedSession(base_url, username, password)
        s.login()                              # ouvre la session (cookie)
        slug = s.upload_video_chunked(path, progress_cb=..., retry_cb=...)
        # -> ensuite, avec le token API : PATCH métadonnées + launch_encoding(slug)
    """

    def __init__(self, base_url: str, username: str, password: str,
                 verify_ssl: bool = True):
        """Initialise la session web du compte véhicule (URL de base, identifiant, mot de passe)."""
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        # En-tête User-Agent « navigateur » : certaines configs Pod/nginx sont
        # tatillonnes sur les requêtes sans UA. On imite un navigateur standard.
        self.session.headers.update({
            "User-Agent": ("Mozilla/5.0 (PodAdmin-eformation) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"),
        })
        self._logged_in = False

    # ── Helpers internes ──────────────────────────────────────────────────

    def _url(self, path: str) -> str:
        """Construit une URL absolue à partir d'un chemin relatif de l'instance."""
        return f"{self.base_url}{path}"

    def _csrf_cookie(self) -> str:
        """Valeur courante du cookie CSRF (nom Django : 'csrftoken')."""
        return self.session.cookies.get("csrftoken", "") or ""

    @staticmethod
    def _extract_csrf_input(html: str) -> str:
        """Extrait la valeur du champ caché <input name='csrfmiddlewaretoken' …>
        d'une page HTML. Renvoie '' si introuvable."""
        m = re.search(
            r'name=["\']csrfmiddlewaretoken["\']\s+value=["\']([^"\']+)["\']', html)
        if not m:
            # Ordre des attributs parfois inversé (value avant name)
            m = re.search(
                r'value=["\']([^"\']+)["\']\s+name=["\']csrfmiddlewaretoken["\']', html)
        return m.group(1) if m else ""

    def _get_csrf_from_page(self, path: str) -> str:
        """Ouvre une page (GET) pour amorcer le cookie CSRF et récupérer le
        jeton de formulaire caché. Renvoie le csrfmiddlewaretoken (jeton de
        formulaire) ; à défaut, se rabat sur la valeur du cookie csrftoken."""
        r = self.session.get(self._url(path), timeout=30, verify=self.verify_ssl)
        token = self._extract_csrf_input(r.text or "")
        return token or self._csrf_cookie()

    # ── 1. Connexion (login local → cookie de session) ────────────────────

    def login(self) -> None:
        """Ouvre une session web par login LOCAL. Lève PodChunkedError en cas
        d'échec (identifiants faux, ou compte SSO refusé par le formulaire).

        Signal de succès fiable : présence du cookie 'sessionid' après le POST.
        """
        # (a) GET du formulaire de login → cookie csrftoken + jeton de formulaire.
        form_token = self._get_csrf_from_page(PATH_LOGIN)
        cookie_token = self._csrf_cookie()
        if not (form_token or cookie_token):
            raise PodChunkedError(
                "Impossible d'obtenir un jeton CSRF sur la page de login "
                "(la page /accounts/login/ est-elle bien accessible ?).")

        # (b) POST des identifiants. Django, en HTTPS, VÉRIFIE l'en-tête Referer :
        #     il doit correspondre à l'origine → on le fournit explicitement.
        data = {
            "username": self.username,
            "password": self.password,
            "csrfmiddlewaretoken": form_token or cookie_token,
        }
        headers = {
            "Referer": self._url(PATH_LOGIN),
            "Origin": self.base_url,
        }
        try:
            r = self.session.post(self._url(PATH_LOGIN), data=data,
                                  headers=headers, timeout=30,
                                  verify=self.verify_ssl)
        except TRANSIENT_ERRORS as e:
            raise PodChunkedError(f"Connexion réseau impossible au login : {e}")

        # (c) Vérification : un login Django réussi pose un cookie 'sessionid'
        #     (et redirige, 302 → page suivie automatiquement par requests).
        if not self.session.cookies.get("sessionid"):
            # Message d'aide : distinguer identifiants faux vs compte SSO.
            hint = ""
            body = (r.text or "")
            low = body.lower()
            if "sso" in low or "cas" in low or "shibboleth" in low:
                hint = (" — ce compte semble passer par le SSO ; or le login "
                        "local n'accepte QUE les comptes locaux (comme eformation).")
            elif ("mot de passe" in low or "password" in low
                  or "incorrect" in low or "invalide" in low):
                hint = " — identifiant ou mot de passe probablement incorrect."
            raise PodChunkedError(
                f"Échec du login local (aucune session ouverte){hint}",
                status=r.status_code, body=body[:500])

        self._logged_in = True

    # ── 2. + 3. Téléversement par morceaux puis finalisation ──────────────

    def upload_video_chunked(
        self,
        file_path: str,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        retry_cb: Optional[Callable[[int, int, str], None]] = None,
        max_retries: int = 4,
        target_slug: str = "",
    ) -> str:
        """Téléverse `file_path` en morceaux via la session web, puis finalise.
        Renvoie le SLUG de la vidéo.

        Deux usages selon `target_slug` :
          • target_slug == ""       → CRÉATION d'une vidéo neuve (au nom de la session) ;
          • target_slug == "<slug>" → REMPLACEMENT du fichier de la vidéo existante
            de ce slug (finalisation avec ce slug au lieu d'un slug vide).
            Comportement validé par sonde : Pod édite alors la vidéo cible.

        progress_cb(octets_envoyés, octets_total) : suivi de progression.
        retry_cb(tentative, total, message)       : appelé avant un ré-essai.
        max_retries : nombre d'essais par morceau (2 Mo) en cas de coupure.

        N'amorce PAS l'encodage : au code appelant de lancer launch_encoding ensuite.
        """
        if not self._logged_in:
            raise PodChunkedError("Session non ouverte : appelez login() d'abord.")
        if not os.path.isfile(file_path):
            raise PodChunkedError(f"Fichier introuvable : {file_path}")

        total = os.path.getsize(file_path)
        # ⚠️ Les en-têtes HTTP (Content-Disposition) doivent être encodables en
        # latin-1 : un nom de fichier accentué (é, à, ü…) déclenche sinon une
        # erreur « 'latin-1' codec can't encode… ». On envoie donc un nom ASCII
        # « propre ». Sans conséquence sur le résultat : pour une CRÉATION, le
        # vrai titre est posé ensuite par PATCH ; pour un REMPLACEMENT, la vidéo
        # cible conserve son titre. Le nom transmis n'est qu'indicatif.
        filename = self._ascii_filename(os.path.basename(file_path))
        md5 = hashlib.md5()          # calculé en un seul passage, pendant l'envoi

        upload_id: Optional[str] = None
        offset = 0                   # nombre d'octets déjà confirmés par le serveur

        with open(file_path, "rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                md5.update(chunk)
                start = offset
                end = offset + len(chunk) - 1     # borne INCLUSIVE (norme HTTP)

                # Envoi du morceau, avec ré-essais isolés sur ce seul morceau.
                resp = self._send_one_chunk(
                    chunk, start, end, total, filename, upload_id,
                    retry_cb=retry_cb, max_retries=max_retries)

                # La réponse fournit l'upload_id (au 1er morceau) et l'offset.
                if isinstance(resp, dict):
                    if resp.get("upload_id"):
                        upload_id = resp["upload_id"]
                    if resp.get("offset") is not None:
                        offset = int(resp["offset"])
                    else:
                        offset = end + 1
                else:
                    offset = end + 1

                if progress_cb:
                    progress_cb(min(offset, total), total)

        if not upload_id:
            raise PodChunkedError(
                "Aucun upload_id renvoyé par le serveur : le protocole chunké "
                "n'a pas démarré comme attendu (à re-valider avec la sonde).")

        # Finalisation → création effective de la vidéo, renvoie le slug.
        # Finalisation → création (slug vide) OU remplacement (slug cible fourni).
        return self._complete(upload_id, md5.hexdigest(), target_slug=target_slug)

    def _send_one_chunk(self, chunk: bytes, start: int, end: int, total: int,
                        filename: str, upload_id: Optional[str], *,
                        retry_cb: Optional[Callable[[int, int, str], None]],
                        max_retries: int) -> dict:
        """Envoie UN morceau (POST multipart). Ré-essaie ce seul morceau en cas
        de coupure réseau/SSL. Renvoie le corps JSON de la réponse (dict)."""
        crange = f"bytes {start}-{end}/{total}"
        # X-CSRFToken = valeur du cookie csrftoken (ce que fait le navigateur en JS).
        csrf = self._csrf_cookie()
        headers = {
            "Content-Range": crange,
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-CSRFToken": csrf,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": self._url(PATH_VIDEO_ADD),
            "Origin": self.base_url,
            "Accept": "application/json",
        }
        for attempt in range(1, max_retries + 1):
            try:
                # multipart : le binaire dans `files`, les champs texte dans `data`.
                data = {"csrfmiddlewaretoken": csrf}
                if upload_id:
                    data["upload_id"] = upload_id     # obligatoire dès le 2e morceau
                files = {"the_file": (filename, chunk, "application/octet-stream")}
                r = self.session.post(
                    self._url(PATH_CHUNK_UPLOAD), data=data, files=files,
                    headers=headers, timeout=120, verify=self.verify_ssl)
                # Passerelle en difficulté (502/503/504) → on ré-essaie ce morceau.
                if r.status_code in GATEWAY_ERRORS:
                    if attempt < max_retries:
                        if retry_cb:
                            retry_cb(attempt, max_retries, f"HTTP {r.status_code} (passerelle)")
                        time.sleep(2 * attempt)
                        continue
                    raise PodChunkedError(
                        f"Morceau {crange} : passerelle en échec (HTTP {r.status_code}) "
                        f"après {max_retries} tentatives — surcharge/serveur momentané.",
                        status=r.status_code, body=(r.text or "")[:400])
                if r.status_code >= 400:
                    raise PodChunkedError(
                        f"Morceau refusé ({crange}) : HTTP {r.status_code}",
                        status=r.status_code, body=(r.text or "")[:400])
                try:
                    return r.json() if r.text else {}
                except ValueError:
                    return {}
            except TRANSIENT_ERRORS as e:
                # Coupure réseau/SSL : on RE-ENVOIE ce morceau (pas tout le fichier).
                if attempt < max_retries:
                    if retry_cb:
                        retry_cb(attempt, max_retries, str(e)[:120])
                    time.sleep(2 * attempt)      # pause croissante : 2s, 4s, 6s…
                    continue
                raise PodChunkedError(
                    f"Échec d'envoi du morceau {crange} après {max_retries} "
                    f"tentatives (coupure réseau/SSL). Dernière erreur : {e}")
        # Ne devrait jamais être atteint (la boucle sort par return ou raise).
        raise PodChunkedError(f"Échec inattendu sur le morceau {crange}.")

    def _complete(self, upload_id: str, md5_hex: str, target_slug: str = "") -> str:
        """Finalise l'upload. Renvoie le slug extrait du redirlink.
        Si `target_slug` est fourni, on finalise avec ce slug → Pod REMPLACE le
        fichier de la vidéo existante au lieu d'en créer une neuve."""
        csrf = self._csrf_cookie()
        headers = {
            "X-CSRFToken": csrf,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": self._url(PATH_VIDEO_ADD),
            "Origin": self.base_url,
            "Accept": "application/json",
        }
        # form-urlencoded (pas de multipart ici). slug vide = création ;
        # slug renseigné = remplacement du fichier de la vidéo cible.
        data = {
            "csrfmiddlewaretoken": csrf,
            "upload_id": upload_id,
            "md5": md5_hex,
            "slug": target_slug or "",
            "transcript": "",
        }
        try:
            # Délai généreux (10 min) : la finalisation réassemble tous les
            # morceaux et vérifie le md5 sur TOUT le fichier — long sur un gros
            # fichier. On laisse à Pod le temps de répondre plutôt que d'abandonner
            # avant nginx.
            r = self.session.post(self._url(PATH_CHUNK_COMPLETE), data=data,
                                  headers=headers, timeout=600,
                                  verify=self.verify_ssl)
        except TRANSIENT_ERRORS as e:
            # Timeout côté client : la finalisation a peut-être RÉUSSI côté serveur.
            raise PodChunkedError(
                "Délai dépassé pendant la finalisation. Le serveur a peut-être "
                "quand même terminé : vérifiez si la vidéo apparaît côté web avant "
                f"de recommencer. (détail : {e})",
                status=504)

        # 502/503/504 : passerelle en timeout. Sur la FINALISATION, on ne ré-essaie
        # PAS automatiquement (risque de doublon) : le traitement a pu aboutir côté
        # serveur même si nginx a rendu la main. On le signale explicitement.
        if r.status_code in GATEWAY_ERRORS:
            raise PodChunkedError(
                f"Finalisation : la passerelle a expiré (HTTP {r.status_code}). "
                "C'est une limite côté SERVEUR (nginx attend la fin de l'assemblage "
                "du gros fichier par Pod). La vidéo a peut-être QUAND MÊME été créée : "
                "vérifiez côté web avant de recommencer.",
                status=r.status_code, body=(r.text or "")[:400])
        if r.status_code >= 400:
            raise PodChunkedError(
                f"Finalisation refusée : HTTP {r.status_code}",
                status=r.status_code, body=(r.text or "")[:400])
        try:
            body = r.json() if r.text else {}
        except ValueError:
            body = {}

        redir = str(body.get("redirlink", "")) if isinstance(body, dict) else ""
        slug = self._slug_from_redirlink(redir)
        if not slug:
            raise PodChunkedError(
                "Finalisation OK mais slug introuvable dans la réponse "
                f"(redirlink={redir!r}).",
                status=r.status_code, body=(r.text or "")[:400])
        return slug

    @staticmethod
    def _slug_from_redirlink(redirlink: str) -> str:
        """Extrait le slug de « /video/edit/<slug>/ » (ou « /video/<slug>/ »)."""
        if not redirlink:
            return ""
        m = re.search(r"/video/(?:edit/)?([^/]+)/?", redirlink)
        return m.group(1) if m else ""

    @staticmethod
    def _ascii_filename(name: str) -> str:
        """Rend un nom de fichier sûr pour les en-têtes HTTP (latin-1) :
        décompose les caractères accentués et retire les diacritiques
        (« é » → « e », « à » → « a »…), puis remplace tout caractère non-ASCII
        résiduel par « _ ». Garde une extension lisible. Jamais vide."""
        # NFKD : « é » (précomposé) OU « e + accent combinant » → « e » + accent,
        # puis on retire les marques combinantes (catégorie Unicode « Mn »).
        decomposed = unicodedata.normalize("NFKD", name or "")
        sans_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
        # Tout ce qui reste hors ASCII imprimable → « _ ».
        ascii_name = "".join(c if 32 <= ord(c) < 127 else "_" for c in sans_accents)
        # Guillemets interdits dans un filename="..." → on les neutralise aussi.
        ascii_name = ascii_name.replace('"', "_").strip()
        return ascii_name or "video.bin"

    # ── Confort ───────────────────────────────────────────────────────────

    def close(self) -> None:
        """Ferme la session HTTP (libère la connexion)."""
        try:
            self.session.close()
        except Exception:
            pass
