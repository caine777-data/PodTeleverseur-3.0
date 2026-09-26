"""Configuration commune des tests de Pod Téléverseur."""
import os
import sys

import pytest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)


@pytest.fixture(scope="module")
def app():
    """Instancie l'application une fois pour tout le module.

    Le réseau est neutralisé : ces tests ne doivent joindre aucune instance
    Pod, et une connexion automatique les rendrait dépendants du serveur."""
    import tempfile
    os.environ.setdefault("HOME", tempfile.mkdtemp())
    try:
        import app as module_app
    except Exception as e:                        # pas d'affichage disponible
        pytest.skip(f"interface indisponible : {e}")
        # `_first_run_wizard` est programmé 300 ms APRÈS le démarrage : il
    # surgit au premier `update()` d'un test ultérieur, qui prend alors « la
    # première fenêtre secondaire » pour la sienne.
    # `_surveiller_blocage` aussi : il lirait le VRAI etat.json sur Internet.
    for nom in ("_auto_connect", "_verifier_maj", "_check_update",
                "_first_run_wizard", "_surveiller_blocage"):
        if hasattr(module_app.App, nom):
            setattr(module_app.App, nom, lambda s, *a, **k: None)
    a = module_app.App()
    a.update()
    # L'assistant d'accueil s'ouvre au premier lancement : il masquerait la
    # fenêtre principale et fausserait toute mesure de position.
    for enfant in list(a.winfo_children()):
        try:
            if enfant.winfo_class() == "Toplevel":
                enfant.destroy()
        except Exception:
            pass
    a.update()
    yield a
    try:
        a.destroy()
    except Exception:
        pass
