#!/bin/sh
# Runnable check for amule-config.sh, the only place where a stale EC password can lock the
# crawler out of a healthy-looking daemon. No framework: run it by hand after touching the awk.
#
#   sh packages/crawler/docker/amule-config.test.sh
#
# It rewrites the script's absolute paths into a temp root and stubs the commands that need a
# real system (user creation, ownership), so it exercises the config logic and nothing else.
set -eu

script=$(dirname "$0")/amule-config.sh
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT

mkdir -p "$root/bin"
for stub in groupadd useradd chown chmod; do
	printf '#!/bin/sh\nexit 0\n' >"$root/bin/$stub"
	chmod +x "$root/bin/$stub"
done
# getent must report "absent" so the script takes its user-creation path.
printf '#!/bin/sh\nexit 2\n' >"$root/bin/getent"
chmod +x "$root/bin/getent"
PATH="$root/bin:$PATH"
export PATH PUID=1000 PGID=1000 AMULE_EC_PASSWORD=hunter2 WEBUI_PWD=irrelevant

sed -e "s#^config_dir=.*#config_dir=$root/home/.aMule#" \
	-e "s#/downloads#$root/downloads#g" "$script" >"$root/under-test.sh"

conf="$root/home/.aMule/amule.conf"
digest=$(printf %s hunter2 | md5sum | cut -d' ' -f1)
failures=0

check() { # check <name> <expected> <actual>
	if [ "$2" = "$3" ]; then
		echo "ok   - $1"
	else
		echo "FAIL - $1"
		echo "       expected: $2"
		echo "       actual:   $3"
		failures=$((failures + 1))
	fi
}

run() { sh "$root/under-test.sh"; }
ec_password() { awk '/^\[/ { s = $0 } s == "[ExternalConnect]" && /^ECPassword=/' "$conf"; }
reset() { rm -rf "$root/home"; mkdir -p "$root/home/.aMule"; }

# 1. No file at all: the full minimal config is written.
reset && rm -f "$conf" && run
check "fresh install writes the digest" "ECPassword=$digest" "$(ec_password)"
check "fresh install sets the incoming dir" "IncomingDir=$root/downloads/incoming" \
	"$(grep '^IncomingDir=' "$conf")"

# 2. A stale digest is replaced, and the operator's other edits survive.
reset
cat >"$conf" <<CONF
[eMule]
IncomingDir=$root/downloads/incoming
MaxUpload=42

[ExternalConnect]
AcceptExternalConnections=1
ECPassword=0000000000000000000000000000dead
ECPort=4712
CONF
run
check "a stale digest is replaced" "ECPassword=$digest" "$(ec_password)"
check "an operator edit survives" "MaxUpload=42" "$(grep '^MaxUpload=' "$conf")"
check "a sibling key survives" "ECPort=4712" "$(grep '^ECPort=' "$conf")"

# 3. The section exists but the key does not: it is inserted before the next section.
reset
printf '[ExternalConnect]\nECPort=4712\n\n[eMule]\nMaxUpload=42\n' >"$conf"
run
check "a missing key is inserted into its section" "ECPassword=$digest" "$(ec_password)"
check "the following section is not swallowed" "MaxUpload=42" "$(grep '^MaxUpload=' "$conf")"

# 4. No [ExternalConnect] at all: the section is appended.
reset
printf '[eMule]\nMaxUpload=42\n' >"$conf"
run
check "a missing section is appended" "ECPassword=$digest" "$(ec_password)"

# 5. A same-named key in another section must not be mistaken for ours.
reset
printf '[Obfuscation]\nECPassword=notthisone\n\n[ExternalConnect]\nECPort=4712\n' >"$conf"
run
check "a same-named key elsewhere is left alone" "ECPassword=notthisone" \
	"$(awk '/^\[/ { s = $0 } s == "[Obfuscation]" && /^ECPassword=/' "$conf")"
check "ours is still written" "ECPassword=$digest" "$(ec_password)"

# 6. Running twice must not drift.
reset && rm -f "$conf" && run && first=$(cat "$conf") && run
check "a second boot changes nothing" "$first" "$(cat "$conf")"

# 7. A missing required variable must kill the boot rather than start half-configured.
reset && rm -f "$conf"
status=0
( unset AMULE_EC_PASSWORD; sh "$root/under-test.sh" ) >/dev/null 2>&1 || status=$?
check "a missing password aborts" "nonzero" "$([ "$status" -ne 0 ] && echo nonzero || echo zero)"

echo
[ "$failures" -eq 0 ] && echo "all checks passed" && exit 0
echo "$failures check(s) failed"
exit 1
