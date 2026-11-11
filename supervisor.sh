#!/bin/sh
set -eu
PID_DIR=/run/nrm
CONFIG=/etc/nrm/config.json
LOG=/var/log/nrm.log
BIN=/usr/local/bin/sing-box
export GOMEMLIMIT="${GOMEMLIMIT:-40MiB}"
export GOGC="${GOGC:-50}"
export GOMAXPROCS="${GOMAXPROCS:-1}"
mkdir -p "$PID_DIR"
while [ -f "$PID_DIR/enabled" ]; do
  "$BIN" run -c "$CONFIG" >>"$LOG" 2>&1 &
  child=$!
  echo "$child" > "$PID_DIR/child.pid"
  wait "$child" || true
  rm -f "$PID_DIR/child.pid"
  [ -f "$PID_DIR/enabled" ] || break
  sleep 2
done
