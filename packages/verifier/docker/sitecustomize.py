"""Verifier image site customization: make libseccomp discoverable on musl (Alpine).

Imported automatically at interpreter startup (this file sits on ``PYTHONPATH`` — see the
verifier ``Dockerfile``). It is a DEPLOYMENT/ENVIRONMENT shim, deliberately NOT part of the
application code: it keeps ``download_verifier.confine`` free of any musl-specific workaround.

Why it is needed: on musl, ``ctypes.util.find_library`` resolves libraries through gcc/ld/
``ldconfig -p`` — none present in the minimal runtime image — so it returns ``None`` even though
``/usr/lib/libseccomp.so.2`` IS installed (``apk add libseccomp``) and loadable by its soname.
``pyseccomp`` resolves libseccomp through ``find_library`` at import, so we point that lookup at
the soname when the toolchain-based resolution fails. On glibc ``find_library`` already works, so
this is a no-op there (we only override when the original lookup returns ``None``).
"""

import ctypes.util

_orig_find_library = ctypes.util.find_library


def _find_library(name: str) -> str | None:
    resolved = _orig_find_library(name)
    if resolved is None and name == "seccomp":
        return "libseccomp.so.2"
    return resolved


ctypes.util.find_library = _find_library
