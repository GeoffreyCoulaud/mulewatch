"""Construction d'un lien ed2k PURE (spec download §3/§5 — DÉCISION D2).

Domaine PUR : aucune I/O. Le lien a la forme ``ed2k://|file|<nom>|<taille>|<hash>|/`` (le
format consommé par ``EC_OP_ADD_LINK``, réf. EC §5). Le ``|`` est le SÉPARATEUR DE CHAMPS :
un nom de fichier hostile pourrait, s'il contenait un ``|``, injecter un champ et casser le
cadrage du lien. On échappe donc le nom par percent-encoding UTF-8 (``urllib.parse.quote``),
en gardant un jeu sûr lisible — l'espace devient ``%20``, le ``|`` devient ``%7C``, les
caractères de contrôle et les non-ASCII sont neutralisés. Seuls les 5 séparateurs STRUCTURELS
du lien (``|file|`` … ``|/``) restent des ``|``.
"""

from urllib.parse import quote

# Jeu gardé NON échappé : lisible et sûr (pas d'espace, pas de ``|``, pas de contrôle). Le
# reste passe en percent-encoding (l'espace → ``%20``, le canon ed2k attendu par le test).
# ``/`` n'est PAS dans le jeu sûr (un nom n'est jamais un chemin ici).
_SAFE_NAME_CHARS = ".()[]-_"


def build_ed2k_link(filename: str, size_bytes: int, ed2k_hash: str) -> str:
    """Lien ed2k pour un fichier (spec §5). Le nom est échappé (``|`` → ``%7C``, etc.)."""
    safe_name = quote(filename, safe=_SAFE_NAME_CHARS)
    return f"ed2k://|file|{safe_name}|{size_bytes}|{ed2k_hash}|/"
