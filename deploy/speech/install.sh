#!/bin/bash
# Speech service of the platform: one per server, for the assistant, employees and agents of this server.
#
#   install.sh [--organization <organization.yml>] [--profiles ru,multi] [--order codex-first|local-only]
#              [--lang ru] [--ru-port 9003] [--multi-port 9002]
#   install.sh status | remove [--purge] | router   (router: only the voice-to-text router, nothing else touched)
#
# Settings come from the organization file (section speech: {profiles: [...], order: ...}, language), options
# override it; defaults: profiles ru, order codex-first, language ru.
# Profiles: ru — Russian (GigaAM v3, ONNX int8) with language ID (VoxLingua107, ONNX); multi — Tatar and other
# languages (omniASR CTC 300M v2, int8). Models are downloaded from their original open sources at pinned revisions
# and checked; ru models are converted to ONNX once in a temporary environment that is removed afterwards.
# No network or an unreachable source: state "needs_setup" with the reason, the installer does not fail.
# Models start on the first request (systemd socket on loopback) and exit when idle: no memory used at rest.
# State for `setup.py tools status`: /var/lib/bb-speech/state.json.
set -uo pipefail
export LC_ALL=C.UTF-8 DEBIAN_FRONTEND=noninteractive
here=$(cd "$(dirname "$0")" && pwd)
opt=/opt/bb-speech; var=/var/lib/bb-speech; conf_dir=/etc/bb-speech; units=/etc/systemd/system
state_file=$var/state.json

# Pinned sources (revision + checksum). Changing a model = new revision and checksums here.
GIGAAM_REPO=ai-sage/GigaAM-v3                                  # Sber, MIT; branch e2e_ctc
GIGAAM_REV=cec030b4c4f35d928e4a9044a3bdb29ebd499fac
GIGAAM_FILES=(config.json=git:0ec7d08e5a6d3089406d3c8c1827a4c2f4fa6fbe
  modeling_gigaam.py=git:36221634c2c0dd0043eb26454b79e0e7aa061b25
  pytorch_model.bin=sha256:9801f83ef3979779982c4f83ec1c234d7571e8c1a5aa2bfc0b09ceb97a16cd75
  tokenizer.model=sha256:0b9a1960898fbfdf5424ab852ea17445eb3da960fba23e977ff100eb0054fbc8)
LID_REPO=speechbrain/lang-id-voxlingua107-ecapa                # Apache-2.0
LID_REV=0253049ae131d6a4be1c4f0d8b0ff483a0f8c8e9
LID_FILES=(hyperparams.yaml=git:27d80047d277c45575779937a92cb829d4158528
  label_encoder.txt=git:addb319892122ba2e7ddb4d01e0c87c4833dab38
  embedding_model.ckpt=sha256:ab750d5c06d713477045fa798fab5d33e959dbc0dfe4de510a9a47844c79a19a
  classifier.ckpt=sha256:a50d9024ff58d317031c9787d4c6c614d454a87a8ef32f9d36338cd3ff57adbc
  normalizer.ckpt=sha256:c369e01dfa2e0d84c6b116f33c7b94f1fe28c061642086538e93cde3d97c26ef
  config.json=git:6fad8e826fd6808012326b33e4beb8d4e083d808)
MULTI_REPO=aadel4/omniASR-CTC-300M-v2                         # Apache-2.0
MULTI_REV=f2b3c39a62c061c27623c3f2d803294fbb954324
MULTI_FILES=(config.json=git:7f0121297f04fba469c1d523848f1a3f43670f27
  vocab.json=git:e0ea10d7cf681ff17bd0f4401ee604d925322f0e
  model.safetensors=sha256:606d849a55b746e698976b8a979b597f4902be7a799ecf723dd283d6210e5871)
TORCH='torch==2.11.0'; TORCHAUDIO='torchaudio==2.11.0'; TORCH_INDEX=https://download.pytorch.org/whl/cpu
TRANSFORMERS='transformers==5.19.0'; ORT='onnxruntime==1.31.0'

profiles=""; order=""; lang=""; ru_port=9003; multi_port=9002; organization=""; action=install; purge=0
case "${1:-}" in status|remove|router) action=$1; shift;; esac
while [ $# -gt 0 ]; do
  case "$1" in
    --organization) organization=$2; shift 2;;
    --profiles) profiles=$2; shift 2;;
    --order) order=$2; shift 2;;
    --lang) lang=$2; shift 2;;
    --ru-port) ru_port=$2; shift 2;;
    --multi-port) multi_port=$2; shift 2;;
    --purge) purge=1; shift;;
    *) echo "unknown option $1" >&2; exit 64;;
  esac
done
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 77; }

if [ "$action" = status ]; then cat "$state_file" 2>/dev/null || echo '{"state":"needs_setup","reason":"not installed"}'; exit 0; fi
if [ "$action" = router ]; then
  # A new release's router (formats, retries) without touching models, ports or units.
  [ -d "$opt" ] || { echo "speech service not installed: run install.sh first" >&2; exit 66; }
  install -m 755 "$here/voice-to-text" "$opt/voice-to-text.new" && mv "$opt/voice-to-text.new" "$opt/voice-to-text" \
    && ln -sfn "$opt/voice-to-text" /usr/local/bin/voice-to-text && echo "speech router updated"; exit $?
fi
if [ "$action" = remove ]; then
  systemctl disable -q --now bb-speech-ru.socket bb-speech-multi.socket 2>/dev/null
  systemctl stop bb-speech-ru.service bb-speech-multi.service 2>/dev/null
  rm -f $units/bb-speech-{ru,multi}.{socket,service} /usr/local/bin/voice-to-text; systemctl daemon-reload
  rm -rf "$opt"; [ "$purge" = 1 ] && rm -rf "$var" "$conf_dir"
  echo "speech service removed"; exit 0
fi

# Settings: organization file, then options, then defaults.
if [ -n "$organization" ]; then
  read -r org_profiles org_order org_lang < <(python3 - "$organization" <<'EOF'
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
try:
    import yaml
    d = yaml.safe_load(text) or {}
except ImportError:                        # no PyYAML: the two simple fields we need
    d = {"language": (re.search(r"^language:\s*([\w-]+)", text, re.M) or [None, ""])[1], "speech": {}}
    m = re.search(r"^speech:\s*\n((?:[ \t]+.*\n?)*)", text, re.M)
    if m:
        p = re.search(r"profiles:\s*\[([^\]]*)\]", m.group(1)); o = re.search(r"order:\s*([\w-]+)", m.group(1))
        d["speech"] = {"profiles": [x.strip() for x in p.group(1).split(",")] if p else None,
                       "order": o.group(1) if o else None}
s = d.get("speech") or {}
print(",".join(s.get("profiles") or []) or "-", s.get("order") or "-", d.get("language") or "-")
EOF
)
  [ "${org_profiles:--}" != - ] && profiles=${profiles:-$org_profiles}
  [ "${org_order:--}" != - ] && order=${order:-$org_order}
  [ "${org_lang:--}" != - ] && lang=${lang:-$org_lang}
fi
profiles=${profiles:-ru}; order=${order:-codex-first}; lang=${lang:-ru}
case "$order" in codex-first|local-only) ;; *) echo "order: codex-first or local-only" >&2; exit 64;; esac
for p in ${profiles//,/ }; do case "$p" in ru|multi) ;; *) echo "unknown profile $p (ru, multi)" >&2; exit 64;; esac; done

problems=()
need() { problems+=("$1"); echo "[speech] needs setup: $1" >&2; }
write_state() {
  local state=installed reason=""
  [ ${#problems[@]} -gt 0 ] && { state=needs_setup; reason=$(IFS=';'; echo "${problems[*]}"); }
  mkdir -p "$var"
  python3 - "$state_file" "$state" "$reason" "$profiles" "$order" "$lang" <<'EOF'
import json, sys
path, state, reason, profiles, order, lang = sys.argv[1:]
json.dump({"state": state, "reason": reason, "profiles": profiles.split(","), "order": order, "lang": lang},
          open(path, "w"), ensure_ascii=False)
EOF
  chmod 644 "$state_file"; echo "[speech] state: $state${reason:+ — $reason}"
}

# Base: ffmpeg (audio decoding in the model servers), venv.
if ! command -v ffmpeg >/dev/null || ! python3 -c 'import ensurepip' 2>/dev/null; then
  { apt-get update -qq && apt-get install -y -qq --no-install-recommends ffmpeg python3-venv >/dev/null; } \
    || need "packages ffmpeg and python3-venv are not installed (apt failed)"
fi
install -d -m 755 "$opt" "$var" "$var/models" "$conf_dir"
install -m 755 "$here/voice-to-text" "$opt/voice-to-text"; ln -sfn "$opt/voice-to-text" /usr/local/bin/voice-to-text
install -m 755 "$here/fetch.py" "$opt/fetch.py"
cat > "$conf_dir/speech.env" <<EOF
# Speech service settings (deploy/speech/install.sh). Order: codex-first or local-only.
SPEECH_ORDER=$order
SPEECH_PROFILES=$profiles
SPEECH_LANG=$lang
SPEECH_RU_URL=http://127.0.0.1:$ru_port
SPEECH_MULTI_URL=http://127.0.0.1:$multi_port
SPEECH_RU_MIN=0.60
EOF
chmod 644 "$conf_dir/speech.env"

venv() {  # venv <dir> <import check> <pip args...>
  local dir=$1 check=$2; shift 2
  [ -x "$dir/bin/pip" ] || python3 -m venv "$dir" || return 1
  "$dir/bin/python" -c "$check" 2>/dev/null || "$dir/bin/pip" install -q --no-cache-dir "$@"
}
fetch() {  # fetch <repo> <rev> <dir> <files...>: 3 = unreachable, 4 = checksum mismatch
  "$opt/fetch.py" hf "$@"
}
port_free() {  # the port is free or already ours
  local port=$1 unit=$2
  ! ss -ltnH "sport = :$port" | grep -q . || systemctl is-active -q "$unit"
}

unit_files() {  # unit_files <name> <port> <memory max> <idle seconds> <nice>
  local name=$1 port=$2 mem=$3 idle=$4 nice=$5
  cat > "$units/bb-speech-$name.socket" <<EOF
[Unit]
Description=Speech service: $name model (loopback socket)

[Socket]
ListenStream=127.0.0.1:$port
Accept=no
Backlog=16

[Install]
WantedBy=sockets.target
EOF
  cat > "$units/bb-speech-$name.service" <<EOF
[Unit]
Description=Speech service: $name model (starts on request, exits when idle)
Requires=bb-speech-$name.socket
After=bb-speech-$name.socket

[Service]
Type=simple
DynamicUser=yes
ExecStart=$opt/$name/venv/bin/python $opt/$name/server.py
Environment=MODEL_DIR=$var/models/$name
Environment=PORT=$port
Environment=NUM_THREADS=2
Environment=OMP_NUM_THREADS=2
Environment=MALLOC_ARENA_MAX=2
Environment=IDLE_TIMEOUT_SEC=$idle
Environment=MAX_AUDIO_SEC=1800
Environment=HOME=/tmp
Environment=HF_HUB_OFFLINE=1
Environment=TRANSFORMERS_OFFLINE=1
Environment=PYTHONUNBUFFERED=1
MemoryMax=$mem
CPUQuota=150%
Nice=$nice
Restart=on-failure
RestartSec=3
TimeoutStopSec=30
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
EOF
}

build_ru_models() {
  local m=$var/models/ru src=$var/src b=$var/build rc
  [ -s "$m/gigaam.int8.onnx" ] && [ -s "$m/lid.onnx" ] && [ -s "$m/tokenizer.model" ] && return 0
  echo "[speech] ru: downloading GigaAM and VoxLingua107, converting to ONNX (temporary environment, ~2.5 GB RAM)"
  fetch "$GIGAAM_REPO" "$GIGAAM_REV" "$src/gigaam" "${GIGAAM_FILES[@]}"; rc=$?
  [ $rc = 0 ] || { need "ru: GigaAM not downloaded ($([ $rc = 3 ] && echo 'source unreachable' || echo 'checksum mismatch'))"; return 1; }
  fetch "$LID_REPO" "$LID_REV" "$src/lid" "${LID_FILES[@]}"; rc=$?
  [ $rc = 0 ] || { need "ru: VoxLingua107 not downloaded ($([ $rc = 3 ] && echo 'source unreachable' || echo 'checksum mismatch'))"; return 1; }
  rm -rf "$b"; python3 -m venv "$b" || { need "ru: python3 -m venv failed"; return 1; }
  if ! { "$b/bin/pip" install -q --no-cache-dir "$TORCH" "$TORCHAUDIO" --index-url "$TORCH_INDEX" &&
         "$b/bin/pip" install -q --no-cache-dir "$TRANSFORMERS" "hydra-core==1.3.*" "omegaconf==2.3.*" soundfile \
           sentencepiece onnx onnxscript "$ORT" "speechbrain==1.*"; }; then
    rm -rf "$b"; need "ru: build packages not installed (pip, network)"; return 1
  fi
  local t=$m/tmp; mkdir -p "$t"
  if (cd /tmp && HOME="$b" LID_SOURCE="$src/lid" nice -n 10 "$b/bin/python" "$here/export_models.py" lid "$t") &&
     (cd /tmp && HOME="$b" GIGAAM_SOURCE="$src/gigaam" nice -n 10 "$b/bin/python" "$here/export_models.py" gigaam "$t") &&
     (cd /tmp && HOME="$b" nice -n 10 "$b/bin/python" "$here/export_models.py" gigaam-quantize "$t"); then
    mv "$t"/{gigaam.int8.onnx,tokenizer.model,lid.onnx,lid_labels.txt} "$m/" && rm -rf "$t" "$b" "$src/gigaam" "$src/lid"
  else
    rm -rf "$t" "$b"; need "ru: model conversion failed (see the output above)"; return 1
  fi
}

install_ru() {
  local d=$opt/ru
  install -d -m 755 "$d" "$var/models/ru"
  venv "$d/venv" 'import numpy, onnxruntime, sentencepiece' numpy "$ORT" sentencepiece \
    || { need "ru: runtime packages not installed (pip, network)"; return 1; }
  install -m 755 "$here/ru_server.py" "$d/server.py"
  build_ru_models || return 1
  port_free "$ru_port" bb-speech-ru.socket || { need "ru: port $ru_port is busy"; return 1; }
  unit_files ru "$ru_port" 1000M 600 10
}

install_multi() {
  local d=$opt/multi m=$var/models/multi rc
  install -d -m 755 "$d" "$m"
  venv "$d/venv" 'import torch, transformers, numpy' "$TORCH" --index-url "$TORCH_INDEX" \
    && venv "$d/venv" 'import transformers, numpy' "$TRANSFORMERS" numpy \
    || { need "multi: runtime packages not installed (pip, network)"; return 1; }
  install -m 755 "$here/multi_server.py" "$d/server.py"; install -m 755 "$here/multi_quantize.py" "$d/quantize.py"
  if [ ! -s "$m/model_int8.pt" ]; then
    echo "[speech] multi: downloading omniASR and quantizing to int8 (a few minutes, ~2.5 GB RAM)"
    fetch "$MULTI_REPO" "$MULTI_REV" "$var/src/multi" "${MULTI_FILES[@]}"; rc=$?
    [ $rc = 0 ] || { need "multi: omniASR not downloaded ($([ $rc = 3 ] && echo 'source unreachable' || echo 'checksum mismatch'))"; return 1; }
    MODEL_REPO="$MULTI_REPO@$MULTI_REV" NUM_THREADS=2 PYTHONWARNINGS=ignore nice -n 10 \
      "$d/venv/bin/python" "$d/quantize.py" "$var/src/multi" "$m" || { need "multi: quantization failed"; return 1; }
    rm -rf "$var/src/multi"
  fi
  port_free "$multi_port" bb-speech-multi.socket || { need "multi: port $multi_port is busy"; return 1; }
  unit_files multi "$multi_port" 2500M 120 15
}

enabled=()
for p in ${profiles//,/ }; do
  if "install_$p"; then enabled+=("bb-speech-$p.socket"); fi
done
find "$opt" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
chown -R root:root "$opt" "$var/models"; chmod -R a+rX "$opt" "$var/models"
for p in ru multi; do                       # a profile no longer chosen: its socket goes away, models stay
  case ",$profiles," in *",$p,"*) ;; *) systemctl disable -q --now "bb-speech-$p.socket" 2>/dev/null
    rm -f "$units/bb-speech-$p".{socket,service};; esac
done
systemctl daemon-reload
for u in "${enabled[@]}"; do
  systemctl enable -q --now "$u" || need "${u%.socket}: socket did not start"
  systemctl --no-block try-restart "${u%.socket}.service" 2>/dev/null   # new code is picked up on the next start
done
rmdir "$var/src" 2>/dev/null   # downloads are removed after the build
write_state
exit 0
