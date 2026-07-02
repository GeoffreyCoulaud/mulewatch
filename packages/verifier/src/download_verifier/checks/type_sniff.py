"""``type_sniff`` check (analysis spec §5 — DA7): ABSOLUTE-DANGER detection.

We sniff the first bytes (the caller already passes at most ``header_bytes``) without ever
comparing against the declared extension (the eD2k name is hostile). Classification:
- known media container → ``clean``;
- executable / script (ELF, PE/MZ, Mach-O, ``#!`` shebang) → ``malicious`` (a video that is
  actually a binary is a deliberate deception);
- archive (zip/rar/7z…) → ``suspicious`` (plausible, but not a video);
- unknown / inconclusive → ``clean`` (ffprobe will decide).
``sniffed_type`` (the detected mime or ``None``) goes into ``meta`` in every case.

Implementation note — executable/archive detection happens in TWO LEVELS:

1. ``_EXECUTABLE_MAGICS`` / ``_ARCHIVE_MAGICS``: magic bytes checked BEFORE puremagic.
   Empirical rationale (puremagic 2.2.0) — what puremagic returns for these magics:
   - ELF (``\\x7fELF``) → ``''`` (empty string, inconclusive).
   - Mach-O, all variants → ``''``.
   - Shebang ``#!`` → ``PureError`` (no signature).
   - PE/MZ (``MZ``) → ``application/vnd.microsoft.portable-executable`` — a mime that CONTAINS
     ``executable``. ``_classify`` nevertheless DELIBERATELY has no branch for this mime:
     ``_EXECUTABLE_MAGICS`` is the explicit net that catches ALL executables upstream, we never
     rely on the puremagic mime to classify them ``malicious``.
   - ZIP (``PK\\x03\\x04``) → ``application/vnd.openxmlformats-officedocument
     .wordprocessingml.document`` (DOCX), never a mime containing ``zip`` (cf. ``_ARCHIVE_MAGICS``
     for the detail of the ZIP variants).
   All these cases are thus caught here, before the puremagic call, with ``sniffed_type=None``.

2. ``_classify`` — on the puremagic mime for the cases not covered by the magic bytes:
   - Media: ``video/``/``audio/`` prefix or specific markers.
   - Archive: ``rar``, ``7z``, etc. markers  (RAR v4=``application/x-rar-compressed``,
     RAR v5=``application/vnd.rar``, 7z=``application/x-7z-compressed``).
   No executable branch here: no realistic executable reaches ``_classify`` —
   ELF/Mach-O/shebang/PE are all caught by ``_EXECUTABLE_MAGICS`` (or PureError) upstream
   (cf. point 1, PE/MZ). An ``application/x-*executable`` branch would therefore be dead.
"""

import puremagic
from puremagic import PureError

from download_verifier.checks.base import CheckOutcome, Status

# Executable/script magics (defense-in-depth: we do not depend on the puremagic mime alone,
# the shebang in particular has no reliable binary magic).
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

# ZIP archives: magic bytes checked before puremagic. The guard is needed for BOTH variants,
# but for TWO distinct reasons (empirical puremagic 2.2.0):
#   - PK\x03\x04 → puremagic returns a DOCX mime (openxmlformats), NEVER a mime containing
#     "zip" → invisible to _ARCHIVE_MARKERS, so it would be classified clean without this guard;
#   - PK\x05\x06 / PK\x07\x08 → puremagic returns "application/zip", but "zip" is ABSENT from
#     _ARCHIVE_MARKERS → would also be classified clean without this guard.
_ARCHIVE_MAGICS: tuple[bytes, ...] = (
    b"PK\x03\x04",  # ZIP local-file-header (and ZIP-based formats: DOCX, XLSX, ODF, JAR…)
    b"PK\x05\x06",  # empty ZIP (end-of-central-directory only)
    b"PK\x07\x08",  # spanned ZIP (data-descriptor)
)

# Archive mimes/extensions detected by puremagic (complement to the magic-bytes level).
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

# Media container/stream mimes.
_MEDIA_PREFIXES: tuple[str, ...] = ("video/", "audio/")
# Defense-in-depth: fallback (substring) for media mimes that do NOT start with "video/"/"audio/"
# — non-standard formats or future puremagic versions (e.g. "application/x-matroska",
# "application/ogg"). Do NOT remove during a cleanup: these markers are not redundant with
# _MEDIA_PREFIXES (they catch precisely the exceptions).
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
    """Sniff ``header`` and classify its absolute danger (spec §5/DA7)."""
    if not header:
        # input-trust#0 short-circuit: ``puremagic.from_string(b"")`` raises ``PureValueError``,
        # which does NOT inherit from ``PureError`` → the except would not catch it and a 0-byte
        # file would crash the child. No information to extract from an empty header;
        # ffprobe will see the absence of media and decide.
        return CheckOutcome(name="type_sniff", status="clean", meta={"sniffed_type": None})
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
