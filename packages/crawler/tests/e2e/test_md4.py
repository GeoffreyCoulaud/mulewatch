"""Tests de la MD4 pure (RFC 1320) + du hash ed2k — outils de test (100 % branche locale)."""

from __future__ import annotations

import pytest

from tests.e2e.md4 import ED2K_CHUNK_SIZE, ed2k_hash, md4

# Suite de test officielle RFC 1320, Appendix A.5 (« MD4 test suite »).
_RFC1320_VECTORS = [
    ("", "31d6cfe0d16ae931b73c59d7e0c089c0"),
    ("a", "bde52cb31de33e46245e05fbdbd6fb24"),
    ("abc", "a448017aaf21d8525fc10ae87aa6729d"),
    ("message digest", "d9130a8164549fe818874806e1c7014b"),
    ("abcdefghijklmnopqrstuvwxyz", "d79e1c308aa5bbcdeea8ed63df412da9"),
    (
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
        "043f8582f241db351ce627e153e7f0e4",
    ),
    (
        "12345678901234567890123456789012345678901234567890123456789012345678901234567890",
        "e33b4ddc9c38f2199c3e7b164fcc0536",
    ),
]


@pytest.mark.parametrize(("message", "expected"), _RFC1320_VECTORS)
def test_md4_matches_rfc1320_vectors(message: str, expected: str) -> None:
    assert md4(message.encode("ascii")) == expected


def test_md4_multiblock_input_crosses_padding_boundary() -> None:
    # 120 octets > 1 bloc (64) → exerce la boucle multi-blocs ET le padding qui déborde
    # (len % 64 == 56 impose un bloc de padding supplémentaire). Vérifié contre une 2e impl MD4.
    assert md4(b"x" * 120) == "d8f9adfc41ec43552619606c70cfd287"


def test_md4_block_exactly_56_bytes_needs_extra_padding_block() -> None:
    # 56 octets : après le 0x80 il ne reste pas la place des 8 octets de longueur dans le même
    # bloc → un bloc de padding entier est ajouté (branche ``while len % 64 != 56``).
    assert md4(b"a" * 56) == "d5f9a9e9257077a5f08b0b92f348b0ad"


def test_ed2k_hash_single_chunk_is_plain_md4() -> None:
    data = b"hello world"
    assert ed2k_hash(data) == md4(data)


def test_ed2k_hash_empty_is_md4_of_empty() -> None:
    assert ed2k_hash(b"") == "31d6cfe0d16ae931b73c59d7e0c089c0"


def test_ed2k_hash_multi_chunk_is_md4_of_concatenated_chunk_digests() -> None:
    # > 1 chunk → exerce la branche multi-chunk : MD4 de la concat des MD4 de chunks.
    data = b"\x00" * (ED2K_CHUNK_SIZE + 1)
    chunk0 = bytes.fromhex(md4(data[:ED2K_CHUNK_SIZE]))
    chunk1 = bytes.fromhex(md4(data[ED2K_CHUNK_SIZE:]))
    assert ed2k_hash(data) == md4(chunk0 + chunk1)


def test_ed2k_hash_exactly_one_chunk_stays_single_branch() -> None:
    # Limite : exactement ED2K_CHUNK_SIZE octets reste mono-chunk (``<=`` dans ed2k_hash).
    data = b"z" * ED2K_CHUNK_SIZE
    assert ed2k_hash(data) == md4(data)
