import pytest

from download_verifier.config import AnalysisConfig


def test_from_env_uses_defaults_when_empty() -> None:
    cfg = AnalysisConfig.from_env({})
    assert cfg.enabled_checks == ("type_sniff", "ffprobe")
    assert cfg.ffprobe_path == "ffprobe"
    assert cfg.clamscan_path == "clamscan"
    assert cfg.clamav_db_dir == "/clamav-db"
    assert cfg.timeout_s == 30.0
    # clamav OFF par défaut → rlimits de base INCHANGÉS.
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
    # config-validation#4 : une typo ('clamv', 'type-sniff') ne doit PAS être silencieusement
    # ignorée — sinon fail-open : zéro check exécuté → verdict 'clean' pour TOUT fichier (même
    # malveillant). Fail-fast au chargement contre l'enum fermé KNOWN_CHECKS.
    with pytest.raises(ValueError, match="clamv"):
        AnalysisConfig.from_env({"ENABLED_CHECKS": "type_sniff,clamv"})


def test_from_env_rejects_unparsable_int() -> None:
    with pytest.raises(ValueError):
        AnalysisConfig.from_env({"RLIMIT_CPU_S": "not-an-int"})


def test_from_env_rejects_unparsable_float() -> None:
    with pytest.raises(ValueError):
        AnalysisConfig.from_env({"ANALYSIS_TIMEOUT_S": "soon"})


def test_from_env_rejects_non_positive_timeout() -> None:
    # config-validation#3 : timeout_s <= 0 → communicate(timeout=) expire immédiatement → tout
    # fichier 'suspicious'. Plancher fail-fast nommant la variable.
    with pytest.raises(ValueError, match="ANALYSIS_TIMEOUT_S"):
        AnalysisConfig.from_env({"ANALYSIS_TIMEOUT_S": "0"})


def test_from_env_rejects_infinite_cpu_rlimit() -> None:
    # RLIMIT_CPU_S=-1 == RLIM_INFINITY : le garde CPU serait DÉSARMÉ silencieusement (pire qu'un
    # crash). Tout rlimit doit être > 0.
    with pytest.raises(ValueError, match="RLIMIT_CPU_S"):
        AnalysisConfig.from_env({"RLIMIT_CPU_S": "-1"})


def test_from_env_rejects_zero_address_space_rlimit() -> None:
    # RLIMIT_AS_BYTES=0 → le child ne peut pas exec (OSError au Popen, traité en transitoire).
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
    # clamav dans enabled_checks, AUCUN override explicite → défauts conditionnels relevés (§6.2).
    cfg = AnalysisConfig.from_env({"ENABLED_CHECKS": "type_sniff,ffprobe,clamav"})
    assert cfg.rlimit_as_bytes == 1536 * 1024 * 1024
    assert cfg.rlimit_cpu_s == 120


def test_clamav_enabled_respects_clamav_override() -> None:
    # clamav ON + overrides _CLAMAV explicites → ces valeurs custom gagnent.
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
    # clamav ON MAIS override explicite (non suffixé) → prioritaire sur le défaut conditionnel.
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
    # clamav absent + aucun override → baseline 512 Mio / 20 s (INCHANGÉ).
    cfg = AnalysisConfig.from_env({"ENABLED_CHECKS": "type_sniff,ffprobe"})
    assert cfg.rlimit_as_bytes == 512 * 1024 * 1024
    assert cfg.rlimit_cpu_s == 20


def test_seccomp_enabled_defaults_true() -> None:
    # ring noyau ON par défaut (prod conteneur : no_new_privs posé).
    assert AnalysisConfig.from_env({}).seccomp_enabled is True


def test_seccomp_enabled_parsed_false() -> None:
    # override dev/CI bare-metal : "0"/"false"/"no" → désactivé.
    assert AnalysisConfig.from_env({"SECCOMP_ENABLED": "0"}).seccomp_enabled is False
    assert AnalysisConfig.from_env({"SECCOMP_ENABLED": "false"}).seccomp_enabled is False
    assert AnalysisConfig.from_env({"SECCOMP_ENABLED": "no"}).seccomp_enabled is False


def test_seccomp_enabled_parsed_true() -> None:
    # "1"/"true"/"yes" → activé explicitement.
    assert AnalysisConfig.from_env({"SECCOMP_ENABLED": "1"}).seccomp_enabled is True
    assert AnalysisConfig.from_env({"SECCOMP_ENABLED": "true"}).seccomp_enabled is True
    assert AnalysisConfig.from_env({"SECCOMP_ENABLED": "yes"}).seccomp_enabled is True


def test_seccomp_enabled_invalid_raises() -> None:
    # valeur non booléenne → ValueError (fail-fast, cohérent avec _parse_int).
    with pytest.raises(ValueError):
        AnalysisConfig.from_env({"SECCOMP_ENABLED": "maybe"})
