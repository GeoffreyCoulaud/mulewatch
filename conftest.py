"""Conftest RACINE du workspace virtuel — NE PAS lancer `pytest` ici.

Le gate est PAR PAQUET : `cd packages/<pkg> && uv run pytest` (chaque paquet a sa propre
`[tool.pytest.ini_options]` : désélection des marqueurs d'intégration + 100 % branch coverage).

Un `uv run pytest` nu DEPUIS la racine n'a pas de config pytest : il collecterait les deux
arbres SANS coverage et SANS désélectionner les marqueurs d'intégration — un run faussement
« propre » qui contourne la hard rule des 100 % branch. On l'empêche en ignorant `packages/*`
à la collecte : depuis la racine, `pytest` ne collecte donc AUCUN test (exit 5).

Ce conftest est au-dessus de la rootdir de chaque paquet ; il n'est PAS chargé quand on lance
pytest depuis `packages/<pkg>`, donc les runs per-paquet sont intacts.
"""

collect_ignore_glob = ["packages/*"]
