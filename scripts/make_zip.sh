#!/usr/bin/env bash
# Usage: bash scripts/make_zip.sh cp1   -> share-subs-cp1.zip in the project root
set -e
cd "$(dirname "$0")/../.."
name="share-subs-$1.zip"
rm -f "share-subs/$name"
zip -qr "share-subs/$name" share-subs -x '*/node_modules/*' '*/__pycache__/*' '*.pyc' 'share-subs/.env' '*.zip' '*/.pytest_cache/*' '*/dist/*'
echo "share-subs/$name"
