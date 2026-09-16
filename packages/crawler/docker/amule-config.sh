#!/bin/sh
# One-shot, run by the entrypoint before any service starts: create the amule user, take
# ownership of the bind mounts, write a minimal amule.conf if the operator has none, and
# reconcile the EC password on every boot.
set -eu

: "${PUID:?PUID is required}"
: "${PGID:?PGID is required}"
: "${AMULE_EC_PASSWORD:?AMULE_EC_PASSWORD is required}"
: "${WEBUI_PWD:?WEBUI_PWD is required}"

config_dir=/home/amule/.aMule
conf="$config_dir/amule.conf"

# -o tolerates a uid/gid that a Debian system account already holds: PUID/PGID exist to match the
# host's ownership of the bind mounts, and uniqueness inside the container buys us nothing.
getent group amule >/dev/null || groupadd -o -g "$PGID" amule
getent passwd amule >/dev/null ||
	useradd -o -u "$PUID" -g "$PGID" -M -d /home/amule -s /usr/sbin/nologin amule

# Not recursive: these are bind mounts that can hold hundreds of gigabytes of part files, and the
# operator owns their contents. Only the mount points themselves have to be ours.
mkdir -p "$config_dir" /downloads/incoming /downloads/temp
chown "$PUID:$PGID" /home/amule "$config_dir" /downloads/incoming /downloads/temp

# ECPassword is a Cfg_Str_Encrypted field, which hashes on GUI input only; on load it takes the
# string as it stands, so the file must already hold the digest.
digest=$(printf %s "$AMULE_EC_PASSWORD" | md5sum | cut -d' ' -f1)

# aMule reads its settings through wxConfig, so an absent key takes its declared default. Only the
# settings whose 3.0.1 default is wrong for us go in here — ECPort's default is already 4712.
if [ ! -f "$conf" ]; then
	cat >"$conf" <<CONF
[eMule]
IncomingDir=/downloads/incoming
TempDir=/downloads/temp

[ExternalConnect]
AcceptExternalConnections=1
ECPassword=$digest
CONF
else
	# AMULE_EC_PASSWORD is the source of truth, so ECPassword is reconciled on EVERY boot — the
	# crawler and amuleweb read the live variable, and a file left holding a stale digest would
	# lock both of them out of a daemon that looks perfectly healthy. Every other key stays the
	# operator's to edit. Section-aware: wxConfig keys are only unique within their section.
	awk -v digest="$digest" '
		/^\[/ {
			# Leaving [ExternalConnect] without having seen the key: add it before moving on.
			if (in_section && !done) { print "ECPassword=" digest; done = 1 }
			in_section = ($0 == "[ExternalConnect]")
		}
		in_section && /^ECPassword=/ { print "ECPassword=" digest; done = 1; next }
		{ print }
		END {
			if (in_section && !done) { print "ECPassword=" digest; done = 1 }
			if (!done) { print ""; print "[ExternalConnect]"; print "ECPassword=" digest }
		}
	' "$conf" >"$conf.new"
	mv "$conf.new" "$conf"
fi

chown "$PUID:$PGID" "$conf"
chmod 600 "$conf"
