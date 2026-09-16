#!/bin/sh
# PID 1. Prepares aMule's configuration once, then hands the container over to s6.
set -eu

/usr/local/bin/amule-config.sh

exec s6-svscan /etc/services.d
