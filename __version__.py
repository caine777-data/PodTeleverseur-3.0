"""
Source UNIQUE de la version de Pod Téléverseur v3.
==================================================
La version était définie dans l'en-tête de CHAQUE fichier : à la reprise de
cette v3, `app.py` annonçait 3.0.0 pendant que `config.py` disait 3.1.0 — or
c'est `app.py` qui fait foi pour l'application.

Le même écart, en v2, aurait fait croire à l'application qu'elle était
éternellement en retard : elle aurait signalé en permanence une mise à jour
vers elle-même.

Tous les modules importent désormais la version d'ici, et le workflow de
compilation la lit dans ce fichier. `version.txt` (métadonnées de l'exécutable
Windows) reste à mettre à jour à la main : un test vérifie la concordance.
"""

__version__ = "3.4.2"

# (majeur, mineur, correctif, build) pour les métadonnées Windows.
VERSION_TUPLE = tuple(int(x) for x in __version__.split(".")) + (0,)
