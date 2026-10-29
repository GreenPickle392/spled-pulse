# Security

SPLED Pulse talks only to your controller over local Bluetooth. It has no
cloud access, opens no network ports, stores no credentials and has no
third-party Python dependencies beyond what Home Assistant ships.

Diagnostics downloads redact the controller's Bluetooth address.

If you find a security problem, please report it privately through
[GitHub security advisories](https://github.com/GreenPickle392/spled-pulse/security/advisories/new)
instead of opening a public issue.
