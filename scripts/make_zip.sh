#!/usr/bin/env bash
# Usage: bash scripts/make_zip.sh cp1   -> forked-wallets-cp1.zip in the project root
set -e
cd "$(dirname "$0")/../.."
name="forked-wallets-$1.zip"
rm -f "forked-wallets/$name"
zip -qr "forked-wallets/$name" forked-wallets -x '*/node_modules/*' '*/__pycache__/*' '*.pyc' 'forked-wallets/.env' '*.zip' '*/.pytest_cache/*' '*/dist/*'
echo "forked-wallets/$name"
