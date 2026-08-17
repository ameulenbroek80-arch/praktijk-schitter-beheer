#!/bin/bash
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ]; then
  echo "Start de applicatie eerst eenmaal zodat de Python-omgeving wordt aangemaakt."
  exit 1
fi
.venv/bin/python SELFTEST.py
