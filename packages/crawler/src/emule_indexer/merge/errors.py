"""``MergeError`` : erreur d'usage ou de fusion (message clair pour le CLI, jamais nu).

Le merge est un outil opérateur standalone (spec fusion §2) : il ne dépend pas du contrat
d'erreur des repositories. ``MergeError`` est sa propre exception (style ``ValueError`` :
un message lisible que ``__main__`` rend sur ``stderr`` avec un code de sortie non nul).
"""


class MergeError(Exception):
    """Usage invalide ou copie qui échoue (fail-fast, message clair pour l'opérateur)."""
