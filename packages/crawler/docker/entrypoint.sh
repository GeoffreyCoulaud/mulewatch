#!/bin/sh
set -eu

/usr/local/bin/amule-config.py

exec s6-svscan /etc/services.d
