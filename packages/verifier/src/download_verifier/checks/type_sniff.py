"""Check ``type_sniff`` (spec analysis §5 — DA7) : détection de DANGER ABSOLU.

On sniffe les premiers octets (le caller passe déjà au plus ``header_bytes``) sans jamais
comparer à l'extension déclarée (le nom eD2k est hostile). Classement :
- conteneur média connu → ``clean`` ;
- exécutable / script (ELF, PE/MZ, Mach-O, shebang ``#!``) → ``malicious`` (une vidéo qui est
  en fait un binaire est une tromperie délibérée) ;
- archive (zip/rar/7z…) → ``suspicious`` (plausible, mais pas une vidéo) ;
- inconnu / non concluant → ``clean`` (ffprobe tranchera).
``sniffed_type`` (le mime détecté ou ``None``) va dans ``meta`` dans tous les cas.

Note implémentation — la détection exécutable/archive se fait EN DEUX NIVEAUX :

1. ``_EXECUTABLE_MAGICS`` / ``_ARCHIVE_MAGICS`` : magic bytes vérifiés AVANT puremagic.
   Rationale empirique (puremagic 2.2.0) — ce que puremagic renvoie pour ces magics :
   - ELF (``\\x7fELF``) → ``''`` (chaîne vide, non concluant).
   - Mach-O toutes variantes → ``''``.
   - Shebang ``#!`` → ``PureError`` (aucune signature).
   - PE/MZ (``MZ``) → ``application/vnd.microsoft.portable-executable`` — un mime qui CONTIENT
     ``executable``. ``_classify`` n'a néanmoins VOLONTAIREMENT aucune branche pour ce mime :
     ``_EXECUTABLE_MAGICS`` est le filet explicite qui capte TOUS les exécutables en amont, on
     ne se repose jamais sur le mime puremagic pour les classer ``malicious``.
   - ZIP (``PK\\x03\\x04``) → ``application/vnd.openxmlformats-officedocument
     .wordprocessingml.document`` (DOCX), jamais un mime contenant ``zip`` (cf. ``_ARCHIVE_MAGICS``
     pour le détail des variantes ZIP).
   Tous ces cas sont donc captés ici, avant l'appel à puremagic, avec ``sniffed_type=None``.

2. ``_classify`` — sur le mime puremagic pour les cas non couverts par les magic bytes :
   - Média : préfixe ``video/``/``audio/`` ou marqueurs spécifiques.
   - Archive : marqueurs ``rar``, ``7z``, etc.  (RAR v4=``application/x-rar-compressed``,
     RAR v5=``application/vnd.rar``, 7z=``application/x-7z-compressed``).
   Aucune branche exécutable ici : aucun exécutable réaliste n'atteint ``_classify`` —
   ELF/Mach-O/shebang/PE sont tous captés par ``_EXECUTABLE_MAGICS`` (ou PureError) en amont
   (cf. point 1, PE/MZ). Une branche ``application/x-*executable`` y serait donc morte.
"""

import puremagic
from puremagic import PureError

from download_verifier.checks.base import CheckOutcome, Status

# Magiques d'exécutables/scripts (defense-in-depth : on ne dépend pas du seul mime puremagic,
# le shebang en particulier n'a pas de magique binaire fiable).
_EXECUTABLE_MAGICS: tuple[bytes, ...] = (
    b"\x7fELF",  # ELF (Linux/BSD)
    b"MZ",  # PE/COFF (Windows .exe/.dll)
    b"\xfe\xed\xfa\xce",  # Mach-O 32-bit big-endian
    b"\xfe\xed\xfa\xcf",  # Mach-O 64-bit big-endian
    b"\xce\xfa\xed\xfe",  # Mach-O 32-bit little-endian
    b"\xcf\xfa\xed\xfe",  # Mach-O 64-bit little-endian
    b"\xca\xfe\xba\xbe",  # Mach-O universal (fat) binary
    b"#!",  # shebang (script)
)

# Archives ZIP : magic bytes vérifiés avant puremagic. Le guard est nécessaire pour les DEUX
# variantes, mais pour DEUX raisons distinctes (empirique puremagic 2.2.0) :
#   - PK\x03\x04 → puremagic renvoie un mime DOCX (openxmlformats), JAMAIS un mime contenant
#     «zip» → invisible à _ARCHIVE_MARKERS, donc serait classé clean sans ce guard ;
#   - PK\x05\x06 / PK\x07\x08 → puremagic renvoie «application/zip», mais «zip» est ABSENT de
#     _ARCHIVE_MARKERS → seraient également classés clean sans ce guard.
_ARCHIVE_MAGICS: tuple[bytes, ...] = (
    b"PK\x03\x04",  # ZIP local-file-header (et formats ZIP-based : DOCX, XLSX, ODF, JAR…)
    b"PK\x05\x06",  # ZIP vide (end-of-central-directory seul)
    b"PK\x07\x08",  # ZIP spanned (data-descriptor)
)

# Mimes/extensions d'archives détectés par puremagic (complément au niveau magic bytes).
_ARCHIVE_MARKERS: tuple[str, ...] = (
    "x-rar",
    "rar",
    "x-7z",
    "7z",
    "x-tar",
    "gzip",
    "x-bzip",
    "x-xz",
)

# Mimes de conteneurs/flux média.
_MEDIA_PREFIXES: tuple[str, ...] = ("video/", "audio/")
# Défense-en-profondeur : repli (substring) pour des mimes média qui ne commencent PAS par
# «video/»/«audio/» — formats non-standard ou futures versions de puremagic (ex.
# «application/x-matroska», «application/ogg»). NE PAS supprimer lors d'un nettoyage : ces
# marqueurs ne sont pas redondants avec _MEDIA_PREFIXES (ils captent justement les exceptions).
_MEDIA_MARKERS: tuple[str, ...] = (
    "matroska",
    "mp4",
    "quicktime",
    "mpeg",
    "ogg",
    "webm",
    "x-msvideo",
)


def sniff(header: bytes) -> CheckOutcome:
    """Sniffe ``header`` et classe son danger absolu (spec §5/DA7)."""
    if header.startswith(_EXECUTABLE_MAGICS):
        return CheckOutcome(name="type_sniff", status="malicious", meta={"sniffed_type": None})
    if header.startswith(_ARCHIVE_MAGICS):
        return CheckOutcome(name="type_sniff", status="suspicious", meta={"sniffed_type": None})
    try:
        mime = puremagic.from_string(header, mime=True)
    except PureError:
        return CheckOutcome(name="type_sniff", status="clean", meta={"sniffed_type": None})
    status = _classify(mime)
    return CheckOutcome(name="type_sniff", status=status, meta={"sniffed_type": mime})


def _classify(mime: str) -> Status:
    lowered = mime.lower()
    if lowered.startswith(_MEDIA_PREFIXES) or any(m in lowered for m in _MEDIA_MARKERS):
        return "clean"
    if any(marker in lowered for marker in _ARCHIVE_MARKERS):
        return "suspicious"
    return "clean"
