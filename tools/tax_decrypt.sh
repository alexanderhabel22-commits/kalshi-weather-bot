#!/usr/bin/env bash
# Unpack the encrypted tax ledger into a private folder (never into the repo).
#   tools/tax_decrypt.sh /path/to/tax-ledger-private.pem /mnt/project-files/tax/
set -euo pipefail
KEY="$1"; DEST="$2"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
openssl pkeyutl -decrypt -inkey "$KEY" -pkeyopt rsa_padding_mode:oaep \
  -in "$HERE/reports/tax-ledger.key.enc" -out "$TMP/k.bin"
openssl enc -d -aes-256-cbc -pbkdf2 -in "$HERE/reports/tax-ledger.tgz.enc" -out "$TMP/tax.tgz" -pass "file:$TMP/k.bin"
mkdir -p "$DEST"
tar -C "$TMP" -xzf "$TMP/tax.tgz"
cp "$TMP"/tax/* "$DEST"/
echo "tax ledger unpacked to $DEST"
