#!/bin/bash
# vallis-pdf — PDF with the platform's common look, reading PDF and pictures of HTML cards. One per server.
# Everything lives in /opt/vallis-pdf; the installer puts nothing into the system (no apt-get install, no system pip):
#   pandoc       official release, pinned version and sha256 (GitHub release digest)        → pandoc/
#   WeasyPrint   with Pillow: pinned wheels with hashes (requirements.txt), pip from its own pinned wheel → py/
#   poppler-utils (pdftotext, pdfinfo, pdftoppm, pdfimages), fonts-dejavu-core and the shared libraries the system
#                lacks: Ubuntu packages downloaded (apt-get download) and unpacked here                  → lib/, fonts/
#   schema picture: the browser of the BB browser tool (tools.yml «browser»), offline.
# The commands go to /usr/local/bin; `vallis-pdf --check` says which parts really start (tools status reads it).
#   install.sh [--prefix <dir>] [--bin <dir>] [--breakdown-seconds 180] [--breakdown-delivery photo-then-pdf|documents]
#   install.sh [--prefix <dir>] [--bin <dir>] remove
# --prefix: the folder of the installation (default /opt/vallis-pdf, one per server); a folder in an account's home
# installs for that account only, without root (--bin then defaults to <prefix>/bin — put it on that agent's PATH).
# The voice breakdown settings (the skill voice-breakdown reads them): <prefix>/voice-breakdown.env, its path from
# `vallis-pdf --settings`.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd); opt=/opt/vallis-pdf; seconds=180; delivery=photo-then-pdf
bin=/usr/local/bin; readers=(pdftotext pdfinfo pdftoppm pdfimages)
PANDOC_VERSION=3.14
PANDOC_SHA256_amd64=c5f4720cdefb3f7bdc5d07202ef579feef0d1370773c873804dd8fdaa8c4f1b8
PIP_WHEEL=pip-26.3-py3-none-any.whl
PIP_SHA256=f3b60b20ffaadd4415ed7957212a38ac63fa4c5fa375bd0f4a797777940fad13
ours() { [ "$(readlink "$1" 2>/dev/null)" = "$2" ]; }
action=install; bin_set=""
while [ $# -gt 0 ]; do
  case "$1" in
    remove) action=remove; shift;;
    --prefix) opt=${2%/}; shift 2;;
    --bin) bin=${2%/}; bin_set=1; shift 2;;
    --breakdown-seconds) seconds=$2; shift 2;;
    --breakdown-delivery) delivery=$2; shift 2;;
    *) echo "unknown option $1" >&2; exit 64;;
  esac
done
[ "$opt" != /opt/vallis-pdf ] && [ -z "$bin_set" ] && bin=$opt/bin
case "$opt" in /*) ;; *) echo "--prefix: an absolute path" >&2; exit 64;; esac
# Root only for the server's shared folders; a prefix and bin in one's own home need none.
writable() { local d=$1; while [ ! -e "$d" ]; do d=$(dirname "$d"); done; [ -w "$d" ]; }   # nearest existing folder
{ writable "$opt" && writable "$bin"; } || [ "$(id -u)" = 0 ] \
  || { echo "run as root (or give --prefix/--bin in a folder you can write)" >&2; exit 77; }
if [ "$action" = remove ]; then
  for r in "${readers[@]}"; do ours "$bin/$r" "$opt/poppler/$r" && rm -f "$bin/$r"; done
  ours "$bin/vallis-pdf" "$opt/vallis-pdf" && rm -f "$bin/vallis-pdf"; rm -rf "$opt"; echo removed; exit 0
fi
[[ $seconds =~ ^[0-9]+$ ]] || { echo "seconds: a number" >&2; exit 64; }
case "$delivery" in photo-then-pdf|documents) ;; *) echo "delivery: photo-then-pdf or documents" >&2; exit 64;; esac
[ "$(uname -m)" = x86_64 ] || { echo "the pinned builds are for Linux x86-64 only" >&2; exit 1; }
[ "$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')" = 3.12 ] \
  || { echo "the pinned wheels are for CPython 3.12" >&2; exit 1; }
work=$(mktemp -d) || exit 1; trap 'rm -rf "$work"' EXIT
fail() { echo "$*" >&2; exit 1; }
fetch() { curl -fsSL --retry 2 -o "$2" "$1" || fail "download of $1 failed"; }
checksum() { [ "$(sha256sum "$1" | cut -c1-64)" = "$2" ] || fail "checksum of $(basename "$1") does not match the pinned one; nothing installed"; }
new=$work/opt; install -d -m 755 "$new/lib" "$new/fonts" "$new/poppler" "$new/pandoc"

# 1. Ubuntu packages the system lacks, with the dependencies the system lacks: unpacked, never installed.
debs() {
  local list
  list=$(apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts --no-breaks --no-replaces \
           --no-enhances --no-pre-depends "$@" 2>/dev/null | grep -E '^[a-z0-9]' | sort -u |
         while read -r p; do dpkg -s "$p" >/dev/null 2>&1 || echo "$p"; done)
  for want in "$@"; do grep -qx "$want" <<<"$list" || list+=$'\n'"$want"; done   # always the named ones
  mkdir -p "$work/debs"
  (cd "$work/debs" && apt-get download $list >/dev/null 2>&1) \
    || (cd "$work/debs" && apt-get update -qq >/dev/null 2>&1 && apt-get download $list >/dev/null) \
    || fail "apt-get download failed: $(echo $list)"
}
libs=(libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 libfontconfig1)
missing=(); for p in "${libs[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
debs poppler-utils fonts-dejavu-core "${missing[@]}"
for d in "$work"/debs/*.deb; do dpkg-deb -x "$d" "$work/root"; done
find "$work/root" -name '*.so*' \( -type f -o -type l \) -exec cp -a {} "$new/lib/" \;
cp -a "$work/root/usr/share/fonts/truetype/dejavu/." "$new/fonts/"
for r in "${readers[@]}"; do
  install -m 755 "$work/root/usr/bin/$r" "$new/poppler/.$r"
  printf '#!/bin/sh\n# %s from poppler-utils with its own libraries; put by the vallis-pdf installer.\nd=$(dirname "$(readlink -f "$0")")\nexport LD_LIBRARY_PATH="$d/../lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\nexec "$d/.%s" "$@"\n' \
    "$r" "$r" > "$new/poppler/$r"
  chmod 755 "$new/poppler/$r"
done
# Fonts: the system's configuration plus our folder.
cat > "$new/fonts.conf" <<CONF
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
  <dir>$opt/fonts</dir>
  <cachedir>$opt/fonts/cache</cachedir>
</fontconfig>
CONF

# 2. pandoc: the official release.
fetch "https://github.com/jgm/pandoc/releases/download/$PANDOC_VERSION/pandoc-$PANDOC_VERSION-linux-amd64.tar.gz" "$work/pandoc.tgz"
checksum "$work/pandoc.tgz" "$PANDOC_SHA256_amd64"
tar -xzf "$work/pandoc.tgz" -C "$new/pandoc" --strip-components=1

# 3. WeasyPrint and Pillow: pinned wheels with hashes, pip from its own pinned wheel.
fetch "https://files.pythonhosted.org/packages/py3/p/pip/$PIP_WHEEL" "$work/$PIP_WHEEL"
checksum "$work/$PIP_WHEEL" "$PIP_SHA256"
python3 "$work/$PIP_WHEEL/pip" install -q --disable-pip-version-check --no-cache-dir --isolated --no-deps \
  --only-binary=:all: --require-hashes -r "$here/requirements.txt" --target "$new/py" \
  || fail "WeasyPrint wheels not installed"

install -m 755 "$here/vallis-pdf" "$new/vallis-pdf"
install -m 644 "$here/style.css" "$here/schema-template.html" "$new/"
# The new folder replaces the old one only when its parts start.
VALLIS_PDF_HOME=$new "$new/vallis-pdf" --check >"$work/check.json" 2>/dev/null
grep -q '"PDF": true' "$work/check.json" && grep -q '"чтение PDF": true' "$work/check.json" \
  || fail "put together, but does not start: $(cat "$work/check.json")"
rm -rf "$opt.old"; [ -d "$opt" ] && mv "$opt" "$opt.old"
mv "$new" "$opt"; rm -rf "$opt.old"; chmod 755 "$opt"
install -d -m 755 "$bin"
ln -sfn "$opt/vallis-pdf" "$bin/vallis-pdf"
# Reading PDF: our commands unless the server has its own on PATH.
for r in "${readers[@]}"; do
  if [ ! -e "$bin/$r" ] || ours "$bin/$r" "$opt/poppler/$r"; then
    own=$(PATH=${PATH//$bin:/} command -v "$r" 2>/dev/null)
    [ -n "$own" ] || ln -sfn "$opt/poppler/$r" "$bin/$r"
  fi
done
printf '# Voice breakdown (skill voice-breakdown): automatic from this length; delivery photo-then-pdf or documents.\nVOICE_BREAKDOWN_SECONDS=%s\nVOICE_BREAKDOWN_DELIVERY=%s\n' "$seconds" "$delivery" > "$opt/voice-breakdown.env"
chmod 644 "$opt/voice-breakdown.env"
echo "vallis-pdf installed in $opt (breakdown from $seconds s, delivery $delivery): $("$opt/vallis-pdf" --check 2>/dev/null)"
