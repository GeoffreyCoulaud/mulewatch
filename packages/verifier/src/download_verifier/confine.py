"""Analysis child's kernel ring: per-child seccomp-bpf filter (blocklist).

The ``Confiner`` installs a default-``ALLOW`` seccomp filter that DENYs a small set of
network/dangerous syscalls (cf. kernel ring spec §4) — it shrinks the kernel attack surface of a
ffprobe/clamscan 0-day and cuts intra-container lateral movement. The filter is INHERITED by the
grandchild (fork/exec under ``no_new_privs``). The ``Confiner`` is INJECTABLE: the PROD impl
installs the real filter via ``pyseccomp`` (``# pragma: no cover`` — covered by
analysis_integration); tests inject a no-op. NO capability required: ``no_new_privs`` is already
set by the container (``no-new-privileges:true``, compose.yaml) — see spec §3.

Fail-open ASSUMED (spec §10): a ring-install failure (``no_new_privs`` not set outside a
container, ``libseccomp`` missing, kernel without seccomp) logs a warning and continues WITHOUT
the filter — it must NEVER turn a healthy media into ``suspicious`` (seccomp is a defense-in-depth
layer, not the only barrier).
"""

import contextlib
import errno
import logging
from typing import Protocol

_LOG = logging.getLogger(__name__)

# Syscalls denied with ERRNO(EPERM): the caller handles the failure (fewer false positives than
# KILL). ``ptrace`` is handled separately (KILL_PROCESS — an unambiguous attack signal, spec §4).
_DENY_EPERM = (
    "socket",
    "socketcall",
    "connect",
    "bind",
    "listen",
    "accept",
    "accept4",
    "process_vm_readv",
    "process_vm_writev",
    "bpf",
    "userfaultfd",
)


class Confiner(Protocol):
    """Install the kernel ring on the current process. Injected for tests."""

    def __call__(self) -> None: ...


class ProdConfiner:
    """PROD ``Confiner``: real seccomp filter (covered by analysis_integration)."""

    def __call__(self) -> None:  # pragma: no cover
        try:
            import pyseccomp

            filt = pyseccomp.SyscallFilter(pyseccomp.ALLOW)  # blocklist: allow by default
            for name in _DENY_EPERM:
                # syscall missing on this arch (e.g. socketcall on x86-64) → OSError ignored.
                with contextlib.suppress(OSError):
                    filt.add_rule(pyseccomp.ERRNO(errno.EPERM), name)
            filt.add_rule(pyseccomp.KILL_PROCESS, "ptrace")
            filt.load()  # applies to the current thread (no_new_privs already set → no privilege)
        except (OSError, ImportError) as exc:
            # controlled fail-open: never a false suspicious — we continue without the filter (the
            # other rings hold: internal:true, RO, rlimits, cap_drop).
            _LOG.warning("seccomp filter not installed (fail-open): %s", exc)
            return


class NoopConfiner:
    """No-op ``Confiner``: installs NO filter. Default when the ring is disabled/unavailable."""

    def __call__(self) -> None:
        return None
