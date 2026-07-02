from download_verifier.checks.type_sniff import sniff


def test_elf_binary_is_malicious() -> None:
    outcome = sniff(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64)
    assert outcome.name == "type_sniff"
    assert outcome.status == "malicious"


def test_pe_mz_executable_is_malicious() -> None:
    outcome = sniff(b"MZ\x90\x00" + b"\x00" * 64)
    assert outcome.status == "malicious"


def test_macho_executable_is_malicious() -> None:
    # Mach-O 64-bit little-endian magic 0xCFFAEDFE.
    outcome = sniff(b"\xcf\xfa\xed\xfe" + b"\x00" * 64)
    assert outcome.status == "malicious"


def test_shebang_script_is_malicious() -> None:
    outcome = sniff(b"#!/bin/sh\necho pwned\n")
    assert outcome.status == "malicious"


def test_zip_archive_is_suspicious() -> None:
    outcome = sniff(b"PK\x03\x04" + b"\x00" * 64)
    assert outcome.status == "suspicious"


def test_rar_archive_is_suspicious() -> None:
    outcome = sniff(b"Rar!\x1a\x07\x00" + b"\x00" * 64)
    assert outcome.status == "suspicious"


def test_7z_archive_is_suspicious() -> None:
    outcome = sniff(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 64)
    assert outcome.status == "suspicious"


def test_matroska_container_is_clean() -> None:
    outcome = sniff(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
    assert outcome.status == "clean"


def test_avi_container_is_clean() -> None:
    outcome = sniff(b"RIFF\x00\x00\x00\x00AVI LIST" + b"\x00" * 32)
    assert outcome.status == "clean"


def test_mp4_container_is_clean() -> None:
    outcome = sniff(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32)
    assert outcome.status == "clean"


def test_plain_text_is_clean() -> None:
    outcome = sniff(b"just some random text, not a media\n")
    assert outcome.status == "clean"


def test_unknown_bytes_are_clean_via_pure_error() -> None:
    # inconclusive bytes: puremagic raises PureError → clean (ffprobe will decide).
    outcome = sniff(b"\x00\x01\x02")
    assert outcome.status == "clean"
    assert outcome.meta["sniffed_type"] is None


def test_empty_header_is_clean_without_crashing() -> None:
    # input-trust#0 regression: puremagic.from_string(b"") raises PureValueError, which
    # does NOT inherit from PureError → the except misses it → child crash. We short-circuit
    # an empty header (a 0-byte file in quarantine) → clean (ffprobe will see an absence
    # of media and decide).
    outcome = sniff(b"")
    assert outcome.status == "clean"
    assert outcome.meta["sniffed_type"] is None


def test_meta_carries_sniffed_type_when_known() -> None:
    outcome = sniff(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
    assert isinstance(outcome.meta["sniffed_type"], str)


def test_meta_sniffed_type_is_none_for_executable() -> None:
    # Executables are caught before puremagic: sniffed_type stays None.
    outcome = sniff(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 64)
    assert outcome.meta["sniffed_type"] is None


def test_meta_sniffed_type_is_none_for_archive_magic() -> None:
    # Archives caught by magic bytes: sniffed_type stays None.
    outcome = sniff(b"PK\x03\x04" + b"\x00" * 64)
    assert outcome.meta["sniffed_type"] is None


def test_other_known_type_is_clean() -> None:
    # PDF recognized by puremagic (application/pdf): neither media nor archive → clean
    # (ffprobe will determine it is not a valid media).
    outcome = sniff(b"%PDF-1.4\n")
    assert outcome.status == "clean"
    assert outcome.meta["sniffed_type"] == "application/pdf"
