"""Constantes du fichier planté de la suite e2e (spec e2e §4) — outil de test SEUL.

Partagées par le stub eD2k (couche A) et les fixtures de la couche B, pour que les deux parlent
du **même** fichier. Le binaire ``deploy/e2e/fixtures/planted.mp4`` est commité ; le hash est
calculé UNE FOIS depuis ce binaire (cf. ``deploy/e2e/fixtures/generate_planted.sh``) et figé ici
en constante (spec e2e §4.2 : ne JAMAIS recalculer depuis une re-génération ffmpeg).

Le test ``test_planted.py`` re-vérifie que la constante correspond bien au binaire commité (et
n'est PAS la MD4 du fichier vide ``31d6cfe0…``), donc toute dérive du binaire est attrapée.
"""

from __future__ import annotations

from pathlib import Path

# Nom planté (spec e2e §4.3) : satisfait is_video (.mp4) + segment_id (n°62 A) + keroro → la règle
# ``id_segment_exact`` (tier download) du matcher e2e, cible ``S2E062A``. C'est une MÉTADONNÉE
# (portée par le lien ed2k / le partage amuled) — indépendante du hash (qui dépend du contenu).
PLANTED_FILENAME = "Keroro n°62 A.mp4"

# Cible attendue de la décision de match (saison 2, épisode 62, segment A).
PLANTED_TARGET_ID = "S2E062A"

# Hash ed2k du contenu de planted.mp4 (calculé via md4.ed2k_hash sur le binaire commité, §4.2).
# Hex minuscule 32. SURTOUT PAS la MD4 du fichier vide (un 0-octet est « instantanément complet »
# côté amuled et jamais listé comme partfile actif, leçon du download_integration).
PLANTED_ED2K_HASH = "7d3ce5e6b6243999b4fed38bb7ae1c05"

# Chemin du binaire planté commité (relatif à la racine du dépôt).
PLANTED_PATH = Path(__file__).resolve().parents[4] / "deploy" / "e2e" / "fixtures" / "planted.mp4"

# Taille (octets) du binaire commité — figée pour le lien ed2k de la couche B.
PLANTED_SIZE = 14345
