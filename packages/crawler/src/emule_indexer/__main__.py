"""Shim de paquet : rend ``python -m emule_indexer`` opérant (spec §2/§4/§9.4).

``python -m emule_indexer`` exécute le ``__main__`` du PAQUET (ce fichier), pas celui du
sous-paquet ``composition``. On ré-exporte ``main`` (point d'entrée réel, dans
``composition.__main__``) et on l'appelle sous ``__name__ == "__main__"``.
"""

from emule_indexer.composition.__main__ import main

__all__ = ["main"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
