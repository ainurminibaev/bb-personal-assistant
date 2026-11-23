#!/bin/bash
# vallis-pdf — PDF with the platform's common look (pandoc + WeasyPrint) and pictures of HTML cards through the browser
# of the BB browser tool (Chrome, offline); reading PDF — poppler-utils (pdftotext, pdfinfo, pdftoppm, pdfimages) in
# its own folder: the Ubuntu packages are downloaded (apt-get download) and unpacked there, nothing goes into the
# system. One per server.
#   install.sh [--breakdown-seconds 180] [--breakdown-delivery photo-then-pdf|documents]
#   install.sh remove
# The voice breakdown settings (the skill voice-breakdown reads them): /etc/vallis/voice-breakdown.env.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd); opt=/opt/vallis-pdf; seconds=180; delivery=photo-then-pdf
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 77; }
readers=(pdftotext pdfinfo pdftoppm pdfimages)
if [ "${1:-}" = remove ]; then
  for r in "${readers[@]}"; do [ "$(readlink "/usr/local/bin/$r" 2>/dev/null)" = "$opt/poppler/$r" ] && rm -f "/usr/local/bin/$r"; done
  rm -rf "$opt" /usr/local/bin/vallis-pdf; echo removed; exit 0
fi
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
# Reading PDF: poppler-utils into $opt/poppler unless the server already has it on PATH.
poppler() {
  local tmp root pkgs
  tmp=$(mktemp -d) || return 1; root=$tmp/root
  # poppler-utils and those of its dependencies the system does not have (only they are unpacked here).
  pkgs=$(apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts --no-breaks --no-replaces \
           --no-enhances --no-pre-depends poppler-utils 2>/dev/null | grep -E '^[a-z0-9]' | sort -u |
         while read -r p; do [ "$p" = poppler-utils ] || ! dpkg -s "$p" >/dev/null 2>&1 && echo "$p"; done)
  (cd "$tmp" && apt-get download $pkgs >/dev/null 2>&1) \
    || { apt-get update -qq && (cd "$tmp" && apt-get download $pkgs >/dev/null); } \
    || { echo "poppler-utils: download failed" >&2; rm -rf "$tmp"; return 1; }
  for d in "$tmp"/*.deb; do dpkg-deb -x "$d" "$root"; done
  rm -rf "$opt/poppler"; install -d -m 755 "$opt/poppler/lib"
  find "$root" -name '*.so*' \( -type f -o -type l \) -exec cp -a {} "$opt/poppler/lib/" \;
  for r in "${readers[@]}"; do
    install -m 755 "$root/usr/bin/$r" "$opt/poppler/.$r"
    printf '#!/bin/sh\n# %s from poppler-utils with its own libraries; put by the vallis-pdf installer.\nexport LD_LIBRARY_PATH="%s${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\nexec "%s" "$@"\n' \
      "$r" "$opt/poppler/lib" "$opt/poppler/.$r" > "$opt/poppler/$r"
    chmod 755 "$opt/poppler/$r"; ln -sfn "$opt/poppler/$r" "/usr/local/bin/$r"
  done
  rm -rf "$tmp"
  "$opt/poppler/pdftotext" -v >/dev/null 2>&1 || { echo "poppler-utils: put in place but pdftotext does not start" >&2; return 1; }
}
if command -v pdftotext >/dev/null && [ "$(readlink -f "$(command -v pdftotext)")" != "$opt/poppler/pdftotext" ]; then
  reading="system"
else
  poppler || exit 1; reading="$opt/poppler"
fi
install -m 755 "$here/vallis-pdf" "$opt/vallis-pdf"
install -m 644 "$here/style.css" "$here/schema-template.html" "$opt/"
ln -sfn "$opt/vallis-pdf" /usr/local/bin/vallis-pdf
printf '# Voice breakdown (skill voice-breakdown): automatic from this length; delivery photo-then-pdf or documents.\nVOICE_BREAKDOWN_SECONDS=%s\nVOICE_BREAKDOWN_DELIVERY=%s\n' "$seconds" "$delivery" > /etc/vallis/voice-breakdown.env
chmod 644 /etc/vallis/voice-breakdown.env
echo "vallis-pdf installed (breakdown from $seconds s, delivery $delivery; reading PDF: $reading)"
