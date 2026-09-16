#!/bin/sh
set -eu

/usr/local/bin/amule-config.sh

exec s6-svscan /etc/services.d
