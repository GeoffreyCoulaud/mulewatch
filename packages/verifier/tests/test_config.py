import pytest

from download_verifier.config import AnalysisConfig


def test_from_env_uses_defaults_when_empty() -> None:
    cfg = AnalysisConfig.from_env({})
    assert cfg.enabled_checks == ("type_sniff", "ffprobe")
    assert cfg.ffprobe_path == "ffprobe"
    assert cfg.clamscan_path == "clamscan"
    assert cfg.clamav_db_dir == "/clamav-db"
    assert cfg.timeout_s == 30.0
    # clamav OFF by default → base rlimits UNCHANGED.
    assert cfg.rlimit_cpu_s == 20
    assert cfg.rlimit_as_bytes == 512 * 1024 * 1024
    assert cfg.rlimit_nproc == 64
    assert cfg.rlimit_nofile == 64
    assert cfg.rlimit_fsize_bytes == 16 * 1024 * 1024
    assert cfg.egress_cap_bytes == 65536
    assert cfg.header_bytes == 4096
    assert cfg.quarantine_dir == "/quarantine"


def test_from_env_overrides_each_field() -> None:
    cfg = AnalysisConfig.from_env(
        {
            "ENABLED_CHECKS": "type_sniff",
            "FFPROBE_PATH": "/usr/bin/ffprobe",
            "CLAMSCAN_PATH": "/usr/bin/clamscan",
            "CLAMAV_DB_DIR": "/var/lib/clamav",
            "ANALYSIS_TIMEOUT_S": "12.5",
            "RLIMIT_CPU_S": "9",
            "RLIMIT_AS_BYTES": "1048576",
            "RLIMIT_NPROC": "7",
            "RLIMIT_NOFILE": "33",
            "RLIMIT_FSIZE_BYTES": "2048",
            "EGRESS_CAP_BYTES": "4096",
            "HEADER_BYTES": "512",
            "QUARANTINE_DIR": "/data/quarantine",
        }
    )
    assert cfg.enabled_checks == ("type_sniff",)
    assert cfg.ffprobe_path == "/usr/bin/ffprobe"
    assert cfg.clamscan_path == "/usr/bin/clamscan"
    assert cfg.clamav_db_dir == "/var/lib/clamav"
    assert cfg.timeout_s == 12.5
    assert cfg.rlimit_cpu_s == 9
    assert cfg.rlimit_as_bytes == 1048576
    assert cfg.rlimit_nproc == 7
    assert cfg.rlimit_nofile == 33
    assert cfg.rlimit_fsize_bytes == 2048
    assert cfg.egress_cap_bytes == 4096
    assert cfg.header_bytes == 512
    assert cfg.quarantine_dir == "/data/quarantine"


def test_enabled_checks_splits_and_strips() -> None:
    cfg = AnalysisConfig.from_env({"ENABLED_CHECKS": " type_sniff , ffprobe "})
    assert cfg.enabled_checks == ("type_sniff", "ffprobe")


def test_from_env_rejects_empty_enabled_checks() -> None:
    with pytest.raises(ValueError):
        AnalysisConfig.from_env({"ENABLED_CHECKS": "  ,  "})


def test_from_env_rejects_unknown_check_name() -> None:
    # config-validation#4: a typo ('clamv', 'type-sniff') must NOT be silently ignored —
    # otherwise fail-open: zero checks run → 'clean' verdict for EVERY file (even a malicious
    # one). Fail-fast at load time against the closed enum KNOWN_CHECKS.
    with pytest.raises(ValueError, match="clamv"):
        AnalysisConfig.from_env({"ENABLED_CHECKS": "type_sniff,clamv"})


def test_from_env_rejects_unparsable_int() -> None:
    with pytest.raises(ValueError):
        AnalysisConfig.from_env({"RLIMIT_CPU_S": "not-an-int"})


def test_from_env_rejects_unparsable_float() -> None:
    with pytest.raises(ValueError):
        AnalysisConfig.from_env({"ANALYSIS_TIMEOUT_S": "soon"})


def test_from_env_rejects_non_positive_timeout() -> None:
    # config-validation#3: timeout_s <= 0 → communicate(timeout=) expires immediately → every
    # file 'suspicious'. Fail-fast floor naming the variable.
    with pytest.raises(ValueError, match="ANALYSIS_TIMEOUT_S"):
        AnalysisConfig.from_env({"ANALYSIS_TIMEOUT_S": "0"})


def test_from_env_rejects_infinite_cpu_rlimit() -> None:
    # RLIMIT_CPU_S=-1 == RLIM_INFINITY: the CPU guard would be silently DISARMED (worse than a
    # crash). Every rlimit must be > 0.
    with pytest.raises(ValueError, match="RLIMIT_CPU_S"):
        AnalysisConfig.from_env({"RLIMIT_CPU_S": "-1"})


def test_from_env_rejects_zero_address_space_rlimit() -> None:
    # RLIMIT_AS_BYTES=0 → the child cannot exec (OSError at Popen, handled as transient).
    with pytest.raises(ValueError, match="RLIMIT_AS_BYTES"):
        AnalysisConfig.from_env({"RLIMIT_AS_BYTES": "0"})


def test_from_env_rejects_non_positive_egress_cap() -> None:
    with pytest.raises(ValueError, match="EGRESS_CAP_BYTES"):
        AnalysisConfig.from_env({"EGRESS_CAP_BYTES": "-1"})


def test_config_is_frozen() -> None:
    cfg = AnalysisConfig.from_env({})
    with pytest.raises(AttributeError):
        cfg.timeout_s = 1.0  # type: ignore[misc]


def test_clamav_enabled_raises_rlimits_to_defaults() -> None:
    # clamav in enabled_checks, NO explicit override → conditional defaults raised (§6.2).
    cfg = AnalysisConfig.from_env({"ENABLED_CHECKS": "type_sniff,ffprobe,clamav"})
    assert cfg.rlimit_as_bytes == 1536 * 1024 * 1024
    assert cfg.rlimit_cpu_s == 120


def test_clamav_enabled_respects_clamav_override() -> None:
    # clamav ON + explicit _CLAMAV overrides → these custom values win.
    cfg = AnalysisConfig.from_env(
        {
            "ENABLED_CHECKS": "clamav",
            "RLIMIT_AS_BYTES_CLAMAV": "2000000000",
            "RLIMIT_CPU_S_CLAMAV": "240",
        }
    )
    assert cfg.rlimit_as_bytes == 2000000000
    assert cfg.rlimit_cpu_s == 240


def test_explicit_rlimit_wins_over_clamav_default() -> None:
    # clamav ON BUT explicit override (non-suffixed) → takes priority over the conditional default.
    cfg = AnalysisConfig.from_env(
        {
            "ENABLED_CHECKS": "clamav",
            "RLIMIT_AS_BYTES": "1234",
            "RLIMIT_CPU_S": "5",
        }
    )
    assert cfg.rlimit_as_bytes == 1234
    assert cfg.rlimit_cpu_s == 5


def test_clamav_off_keeps_baseline_rlimits() -> None:
    # clamav absent + no override → baseline 512 MiB / 20 s (UNCHANGED).
    cfg = AnalysisConfig.from_env({"ENABLED_CHECKS": "type_sniff,ffprobe"})
    assert cfg.rlimit_as_bytes == 512 * 1024 * 1024
    assert cfg.rlimit_cpu_s == 20


def test_clamav_enabled_raises_timeout_to_default() -> None:
    # sandbox-confinement#1: with clamav the CPU budget rises to 120 s; the wall-clock timeout must
    # FOLLOW (otherwise the scan is killed at 30 s by communicate → false positives on slow healthy
    # media).
    cfg = AnalysisConfig.from_env({"ENABLED_CHECKS": "type_sniff,ffprobe,clamav"})
    assert cfg.timeout_s == 150.0  # >= clamav CPU budget (120 s) + margin


def test_clamav_timeout_respects_clamav_override() -> None:
    cfg = AnalysisConfig.from_env({"ENABLED_CHECKS": "clamav", "ANALYSIS_TIMEOUT_S_CLAMAV": "200"})
    assert cfg.timeout_s == 200.0


def test_explicit_timeout_wins_over_clamav_default() -> None:
    # explicit override (non-suffixed) takes priority over the conditional default (like rlimits).
    cfg = AnalysisConfig.from_env({"ENABLED_CHECKS": "clamav", "ANALYSIS_TIMEOUT_S": "90"})
    assert cfg.timeout_s == 90.0


def test_seccomp_enabled_defaults_true() -> None:
    # kernel ring ON by default (prod container: no_new_privs set).
    assert AnalysisConfig.from_env({}).seccomp_enabled is True


def test_seccomp_enabled_parsed_false() -> None:
    # bare-metal dev/CI override: "0"/"false"/"no" → disabled.
    assert AnalysisConfig.from_env({"SECCOMP_ENABLED": "0"}).seccomp_enabled is False
    assert AnalysisConfig.from_env({"SECCOMP_ENABLED": "false"}).seccomp_enabled is False
    assert AnalysisConfig.from_env({"SECCOMP_ENABLED": "no"}).seccomp_enabled is False


def test_seccomp_enabled_parsed_true() -> None:
    # "1"/"true"/"yes" → explicitly enabled.
    assert AnalysisConfig.from_env({"SECCOMP_ENABLED": "1"}).seccomp_enabled is True
    assert AnalysisConfig.from_env({"SECCOMP_ENABLED": "true"}).seccomp_enabled is True
    assert AnalysisConfig.from_env({"SECCOMP_ENABLED": "yes"}).seccomp_enabled is True


def test_seccomp_enabled_invalid_raises() -> None:
    # non-boolean value → ValueError (fail-fast, consistent with _parse_int).
    with pytest.raises(ValueError):
        AnalysisConfig.from_env({"SECCOMP_ENABLED": "maybe"})


def test_seccomp_enabled_accepts_any_casing_and_on_off() -> None:
    # config-validation#5 regression: `SECCOMP_ENABLED=True` (Python casing) used to raise
    # ValueError instead of being accepted. Now strip().lower() + ``on``/``off``.
    for raw in ("True", "TRUE", " true ", "Yes", "ON", "1"):
        assert AnalysisConfig.from_env({"SECCOMP_ENABLED": raw}).seccomp_enabled is True
    for raw in ("False", "FALSE", "No", "OFF", "0"):
        assert AnalysisConfig.from_env({"SECCOMP_ENABLED": raw}).seccomp_enabled is False


def test_seccomp_enabled_invalid_lists_accepted_literals() -> None:
    # The error message must list the accepted literals (the operator knows what to fix).
    with pytest.raises(ValueError, match="true.*false"):
        AnalysisConfig.from_env({"SECCOMP_ENABLED": "maybe"})
