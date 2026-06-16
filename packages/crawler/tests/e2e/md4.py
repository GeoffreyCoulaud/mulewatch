"""MD4 PURE (RFC 1320) — outil de test SEUL (calcul du hash ed2k du fichier planté).

Pourquoi pas ``hashlib`` : OpenSSL 3 retire MD4 du provider par défaut, donc
``hashlib.new("md4")`` lève ``ValueError`` sur les runners modernes. On évite aussi
``pycryptodome`` (zéro dépendance de prod ajoutée — cf. brief). Cette implémentation est un
**outil de test** : elle n'est dans aucun import de ``emule_indexer`` et n'est pas mesurée par
``--cov=emule_indexer``. Elle est néanmoins écrite avec rigueur et testée contre les vecteurs
RFC 1320 (toutes branches).

L'algorithme suit RFC 1320 §3 : padding (bit ``0x80`` + zéros jusqu'à ``≡ 448 mod 512`` + longueur
en bits sur 64 bits little-endian), puis pour chaque bloc de 512 bits, 3 rondes de 16 opérations
sur 4 mots de 32 bits ``A, B, C, D`` (little-endian).
"""

from __future__ import annotations

import struct

_MASK32 = 0xFFFFFFFF

# Décalages (RFC 1320 §3.4) : ronde 1 ([s11..s14] = 3,7,11,19), ronde 2 (3,5,9,13),
# ronde 3 (3,9,11,15). On les liste par ronde, indexés par ``i % 4``.
_S1 = (3, 7, 11, 19)
_S2 = (3, 5, 9, 13)
_S3 = (3, 9, 11, 15)


def _rotl(value: int, bits: int) -> int:
    """Rotation circulaire à gauche d'un mot de 32 bits."""
    value &= _MASK32
    return ((value << bits) | (value >> (32 - bits))) & _MASK32


def _f(x: int, y: int, z: int) -> int:
    """Fonction auxiliaire F de la ronde 1 (RFC 1320 §3.4)."""
    return (x & y) | (~x & z)


def _g(x: int, y: int, z: int) -> int:
    """Fonction auxiliaire G de la ronde 2."""
    return (x & y) | (x & z) | (y & z)


def _h(x: int, y: int, z: int) -> int:
    """Fonction auxiliaire H de la ronde 3."""
    return x ^ y ^ z


def _pad(message: bytes) -> bytes:
    """Padding RFC 1320 §3.1/§3.2 : ``0x80`` + zéros + longueur en bits (64 bits LE)."""
    bit_len = (len(message) * 8) & 0xFFFFFFFFFFFFFFFF
    padded = message + b"\x80"
    while len(padded) % 64 != 56:
        padded += b"\x00"
    padded += struct.pack("<Q", bit_len)
    return padded


def md4(message: bytes) -> str:
    """MD4 d'un message en octets → digest hexadécimal minuscule (32 caractères)."""
    a, b, c, d = 0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476

    padded = _pad(message)
    for offset in range(0, len(padded), 64):
        block = padded[offset : offset + 64]
        x = list(struct.unpack("<16I", block))
        aa, bb, cc, dd = a, b, c, d

        # Ronde 1 : ordre des mots = 0..15, fonction F, constante additive nulle.
        for i in range(16):
            shift = _S1[i % 4]
            a = _rotl((a + _f(b, c, d) + x[i]) & _MASK32, shift)
            a, b, c, d = d, a, b, c

        # Ronde 2 : ordre i = 0,4,8,12,1,5,…, fonction G, constante 0x5A827999.
        order2 = (0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15)
        for i in range(16):
            shift = _S2[i % 4]
            k = order2[i]
            a = _rotl((a + _g(b, c, d) + x[k] + 0x5A827999) & _MASK32, shift)
            a, b, c, d = d, a, b, c

        # Ronde 3 : ordre i = 0,8,4,12,2,10,…, fonction H, constante 0x6ED9EBA1.
        order3 = (0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15)
        for i in range(16):
            shift = _S3[i % 4]
            k = order3[i]
            a = _rotl((a + _h(b, c, d) + x[k] + 0x6ED9EBA1) & _MASK32, shift)
            a, b, c, d = d, a, b, c

        a = (a + aa) & _MASK32
        b = (b + bb) & _MASK32
        c = (c + cc) & _MASK32
        d = (d + dd) & _MASK32

    return struct.pack("<4I", a, b, c, d).hex()


# Taille d'un chunk ed2k (RFC ed2k / aMule) : 9 728 000 octets. En dessous, le hash ed2k d'un
# fichier est simplement la MD4 de son unique chunk (cf. spec e2e §4.2).
ED2K_CHUNK_SIZE = 9_728_000


def ed2k_hash(data: bytes) -> str:
    """Hash ed2k d'un contenu (spec e2e §4.2).

    Un seul chunk (data < ``ED2K_CHUNK_SIZE``) → MD4 du chunk. Plusieurs chunks → MD4 de la
    concaténation des MD4 de chaque chunk de 9 728 000 octets. Couvre les deux branches.
    """
    if len(data) <= ED2K_CHUNK_SIZE:
        return md4(data)
    digests = b""
    for offset in range(0, len(data), ED2K_CHUNK_SIZE):
        chunk = data[offset : offset + ED2K_CHUNK_SIZE]
        digests += bytes.fromhex(md4(chunk))
    return md4(digests)
