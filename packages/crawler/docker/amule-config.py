#!/usr/bin/python3
"""Run once by the entrypoint, as root, before any service starts.

Creates the `amule` user, takes ownership of the bind mounts, writes a minimal amule.conf if
the operator has none, and rewrites ECPassword from AMULE_EC_PASSWORD on every boot.
"""

import configparser
import hashlib
import os
import subprocess
import sys

HOME_DIR = "/home/amule"
CONFIG_DIR = "/home/amule/.aMule"
INCOMING_DIR = "/downloads/incoming"
TEMP_DIR = "/downloads/temp"


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"{name} is required")
    return value


def missing(*getent_args: str) -> bool:
    return subprocess.run(["getent", *getent_args], stdout=subprocess.DEVNULL).returncode != 0


puid = required("PUID")
pgid = required("PGID")
ec_password = required("AMULE_EC_PASSWORD")
required("WEBUI_PWD")

conf_path = os.path.join(CONFIG_DIR, "amule.conf")

# -o tolerates a uid/gid a Debian system account already holds: PUID/PGID are there to match the
# host's ownership of the bind mounts, and uniqueness inside the container buys us nothing.
if missing("group", "amule"):
    subprocess.run(["groupadd", "-o", "-g", pgid, "amule"], check=True)
if missing("passwd", "amule"):
    useradd = ["useradd", "-o", "-u", puid, "-g", pgid, "-M", "-d", HOME_DIR]
    subprocess.run([*useradd, "-s", "/usr/sbin/nologin", "amule"], check=True)

# Not recursive: these bind mounts can hold hundreds of gigabytes of part files, and the operator
# owns their contents. Only the mount points themselves have to be ours.
owned = [HOME_DIR, CONFIG_DIR, INCOMING_DIR, TEMP_DIR]
for directory in owned:
    os.makedirs(directory, exist_ok=True)
for directory in owned:
    os.chown(directory, int(puid), int(pgid))

# ECPassword is read as-is at load time (only the GUI hashes what you type), so the file has to
# already hold the digest.
digest = hashlib.md5(ec_password.encode()).hexdigest()

# RawConfigParser: no %-interpolation, so a password or a path containing % survives.
# optionxform=str: aMule keys are case-sensitive (ECPassword, not ecpassword).
# strict=False: a hand-edited file with a duplicate key must not abort the boot.
parser = configparser.RawConfigParser(strict=False)
parser.optionxform = str
if not parser.read(conf_path):
    # aMule reads its settings through wxConfig, so an absent key takes its declared default.
    # Only the settings whose 3.0.1 default is wrong for us go in (ECPort already defaults to 4712).
    parser["eMule"] = {"IncomingDir": INCOMING_DIR, "TempDir": TEMP_DIR}
    parser["ExternalConnect"] = {"AcceptExternalConnections": "1"}

# The variable is the source of truth, so this is reconciled on EVERY boot: a file left holding a
# stale digest locks the crawler and amuleweb out of a daemon that looks perfectly healthy. Every
# other key stays the operator's to edit -- except his comments, which configparser drops on
# rewrite. aMule rewrites the file itself on its first save anyway.
if not parser.has_section("ExternalConnect"):
    parser.add_section("ExternalConnect")
parser["ExternalConnect"]["ECPassword"] = digest

# aMule writes `Key=value`, not `Key = value`.
with open(conf_path, "w") as handle:
    parser.write(handle, space_around_delimiters=False)

os.chown(conf_path, int(puid), int(pgid))
os.chmod(conf_path, 0o600)
