"""CI guard checks that keep our OpenVEX ``not_affected`` claims honest over time.

Dev/CI tooling only: this package is never installed into a production image. It
reads the ``security/*.vex.openvex.json`` documents and fails the gate when a VEX
claim goes stale.
"""
