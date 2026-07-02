import json

from download_verifier import egress
from download_verifier.config import AnalysisConfig

_CFG = AnalysisConfig.from_env({"EGRESS_CAP_BYTES": "256"})


def _valid(verdict: str = "clean") -> bytes:
    return json.dumps(
        {"verdict": verdict, "real_meta": {"container": "mp4"}, "checks": [{"name": "ffprobe"}]}
    ).encode()


def test_valid_egress_is_passed_through() -> None:
    verdict, real_meta, checks = egress.parse(_valid("clean"), 0, False, _CFG)
    assert verdict == "clean"
    assert real_meta == {"container": "mp4"}
    assert checks == [{"name": "ffprobe"}]


def test_each_enum_verdict_passes_through() -> None:
    for value in ("clean", "suspicious", "malicious"):
        assert egress.parse(_valid(value), 0, False, _CFG)[0] == value


def test_timed_out_is_suspicious() -> None:
    assert egress.parse(_valid(), 0, True, _CFG) == ("suspicious", {}, [])


def test_nonzero_returncode_is_suspicious() -> None:
    assert egress.parse(_valid(), 1, False, _CFG) == ("suspicious", {}, [])


def test_oversized_stdout_is_suspicious() -> None:
    huge = json.dumps({"verdict": "clean", "real_meta": {"p": "x" * 4096}, "checks": []}).encode()
    assert egress.parse(huge, 0, False, _CFG) == ("suspicious", {}, [])


def test_non_json_is_suspicious() -> None:
    assert egress.parse(b"{not json", 0, False, _CFG) == ("suspicious", {}, [])


def test_empty_stdout_is_suspicious() -> None:
    assert egress.parse(b"", 0, False, _CFG) == ("suspicious", {}, [])


def test_non_object_payload_is_suspicious() -> None:
    assert egress.parse(b"[1,2,3]", 0, False, _CFG) == ("suspicious", {}, [])


def test_missing_verdict_is_suspicious() -> None:
    payload = json.dumps({"real_meta": {}, "checks": []}).encode()
    assert egress.parse(payload, 0, False, _CFG) == ("suspicious", {}, [])


def test_non_string_verdict_is_suspicious() -> None:
    payload = json.dumps({"verdict": 1, "real_meta": {}, "checks": []}).encode()
    assert egress.parse(payload, 0, False, _CFG) == ("suspicious", {}, [])


def test_out_of_enum_verdict_is_suspicious() -> None:
    payload = json.dumps({"verdict": "error", "real_meta": {}, "checks": []}).encode()
    assert egress.parse(payload, 0, False, _CFG) == ("suspicious", {}, [])


def test_non_object_real_meta_is_suspicious() -> None:
    payload = json.dumps({"verdict": "clean", "real_meta": [], "checks": []}).encode()
    assert egress.parse(payload, 0, False, _CFG) == ("suspicious", {}, [])


def test_non_list_checks_is_suspicious() -> None:
    payload = json.dumps({"verdict": "clean", "real_meta": {}, "checks": {}}).encode()
    assert egress.parse(payload, 0, False, _CFG) == ("suspicious", {}, [])


# --- classify_outcome (observability#2): the outcome's TECHNICAL cause -----------------------


def test_classify_outcome_ok_for_valid_egress() -> None:
    assert egress.classify_outcome(_valid("clean"), 0, False, _CFG) == "ok"


def test_classify_outcome_each_valid_verdict_is_ok() -> None:
    for value in ("clean", "suspicious", "malicious"):
        assert egress.classify_outcome(_valid(value), 0, False, _CFG) == "ok"


def test_classify_outcome_timeout() -> None:
    # timed_out wins over everything else (first filter).
    assert egress.classify_outcome(_valid(), 0, True, _CFG) == "timeout"


def test_classify_outcome_nonzero_exit() -> None:
    assert egress.classify_outcome(_valid(), 1, False, _CFG) == "nonzero_exit"


def test_classify_outcome_egress_overflow() -> None:
    huge = json.dumps({"verdict": "clean", "real_meta": {"p": "x" * 4096}, "checks": []}).encode()
    assert egress.classify_outcome(huge, 0, False, _CFG) == "egress_overflow"


def test_classify_outcome_malformed_non_json() -> None:
    assert egress.classify_outcome(b"{not json", 0, False, _CFG) == "malformed"


def test_classify_outcome_malformed_non_object() -> None:
    assert egress.classify_outcome(b"[1,2,3]", 0, False, _CFG) == "malformed"


def test_classify_outcome_malformed_bad_verdict_value() -> None:
    payload = json.dumps({"verdict": "error", "real_meta": {}, "checks": []}).encode()
    assert egress.classify_outcome(payload, 0, False, _CFG) == "malformed"


def test_classify_outcome_malformed_non_string_verdict() -> None:
    payload = json.dumps({"verdict": 1, "real_meta": {}, "checks": []}).encode()
    assert egress.classify_outcome(payload, 0, False, _CFG) == "malformed"


def test_classify_outcome_malformed_bad_meta_or_checks_type() -> None:
    bad_meta = json.dumps({"verdict": "clean", "real_meta": [], "checks": []}).encode()
    assert egress.classify_outcome(bad_meta, 0, False, _CFG) == "malformed"
    bad_checks = json.dumps({"verdict": "clean", "real_meta": {}, "checks": {}}).encode()
    assert egress.classify_outcome(bad_checks, 0, False, _CFG) == "malformed"
