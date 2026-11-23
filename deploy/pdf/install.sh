#!/bin/bash
# vallis-pdf — PDF with the platform's common look (pandoc + WeasyPrint) and pictures of HTML cards through the browser
# of the BB browser tool (Chrome, offline). One per server.
#   install.sh [--breakdown-seconds 180] [--breakdown-delivery photo-then-pdf|documents]
#   install.sh remove
# The voice breakdown settings (the skill voice-breakdown reads them): /etc/vallis/voice-breakdown.env.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd); opt=/opt/vallis-pdf; seconds=180; delivery=photo-then-pdf
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 77; }
if [ "${1:-}" = remove ]; then rm -rf "$opt" /usr/local/bin/vallis-pdf; echo removed; exit 0; fi
while [ $# -gt 0 ]; do
  case "$1" in
    --breakdown-seconds) seconds=$2; shift 2;;
    --breakdown-delivery) delivery=$2; shift 2;;
    *) echo "unknown option $1" >&2; exit 64;;
  esac
done
[[ $seconds =~ ^[0-9]+$ ]] || { echo "seconds: a number" >&2; exit 64; }
case "$delivery" in photo-then-pdf|documents) ;; *) echo "delivery: photo-then-pdf or documents" >&2; exit 64;; esac
need=(); command -v pandoc >/dev/null || need+=(pandoc); command -v weasyprint >/dev/null || need+=(weasyprint)
python3 -c 'import PIL' 2>/dev/null || need+=(python3-pil); fc-list 2>/dev/null | grep -qi dejavu || need+=(fonts-dejavu-core)
if [ ${#need[@]} -gt 0 ]; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${need[@]}" >/dev/null \
    || { apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${need[@]}" >/dev/null; } \
    || { echo "packages not installed: ${need[*]}" >&2; exit 1; }
fi
install -d -m 755 "$opt" /etc/vallis
install -m 755 "$here/vallis-pdf" "$opt/vallis-pdf"
install -m 644 "$here/style.css" "$here/schema-template.html" "$opt/"
ln -sfn "$opt/vallis-pdf" /usr/local/bin/vallis-pdf
printf '# Voice breakdown (skill voice-breakdown): automatic from this length; delivery photo-then-pdf or documents.\nVOICE_BREAKDOWN_SECONDS=%s\nVOICE_BREAKDOWN_DELIVERY=%s\n' "$seconds" "$delivery" > /etc/vallis/voice-breakdown.env
chmod 644 /etc/vallis/voice-breakdown.env
echo "vallis-pdf installed (breakdown from $seconds s, delivery $delivery)"
