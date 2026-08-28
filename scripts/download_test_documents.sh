#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
target_dir="${1:-$project_dir/test-documents}"
temporary_dir=$(mktemp -d)
trap 'rm -rf "$temporary_dir"' EXIT HUP INT TERM

curl -fL --retry 3 --max-time 120 \
  'https://nvlpubs.nist.gov/nistpubs/CSWP/NIST.CSWP.29.pdf' \
  -o "$temporary_dir/nist-csf-2.0.pdf"

curl -fL --retry 3 --max-time 120 \
  'https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-218.pdf' \
  -o "$temporary_dir/nist-ssdf-1.1.pdf"

curl -fL --retry 3 --max-time 120 \
  'https://www.nist.gov/itl/ai-risk-management-framework/ai-risk-management-framework-faqs' \
  -o "$temporary_dir/nist-ai-rmf-faq.html"

curl -fL --retry 3 --max-time 120 \
  'https://airc.nist.gov/airmf-resources/airmf/5-sec-core/' \
  -o "$temporary_dir/nist-ai-rmf-core.html"

curl -fL --retry 3 --max-time 120 \
  'https://raw.githubusercontent.com/spdx/license-list-data/main/json/licenses.json' \
  -o "$temporary_dir/spdx-licenses.json"

for pdf_file in "$temporary_dir"/*.pdf; do
  signature=$(LC_ALL=C head -c 5 "$pdf_file")
  if [ "$signature" != '%PDF-' ]; then
    echo "Invalid PDF response: $pdf_file" >&2
    exit 1
  fi
done

python3 -m json.tool "$temporary_dir/spdx-licenses.json" >/dev/null

for html_file in "$temporary_dir"/*.html; do
  if ! grep -Eiq '<!doctype html|<html' "$html_file"; then
    echo "Invalid HTML response: $html_file" >&2
    exit 1
  fi
done

mkdir -p "$target_dir"
for downloaded_file in "$temporary_dir"/*; do
  cp "$downloaded_file" "$target_dir/"
done

echo "Downloaded and validated 5 files in $target_dir"
ls -lh "$target_dir"
