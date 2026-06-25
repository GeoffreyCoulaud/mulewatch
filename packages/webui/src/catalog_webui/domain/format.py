"""Formatage pur pour l'affichage (spec webui §4/§7). Aucun I/O.

NB : la construction du lien eD2k (avec percent-encoding du nom) vit dans
``catalog_matching.ed2k_link`` — paquet partagé crawler+webui — pour empêcher la
divergence webui/crawler sur le format canonique (régression webui-security#0 : le
webui interpolait le filename brut, un ``|`` hostile cassait le cadrage du lien)."""


def short_hash(ed2k_hash: str) -> str:
    """Hash tronqué pour l'affichage (8 premiers caractères + ellipse)."""
    if len(ed2k_hash) <= 8:
        return ed2k_hash
    return f"{ed2k_hash[:8]}…"
