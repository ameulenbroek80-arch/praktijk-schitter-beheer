#!/bin/bash
cd "$(dirname "$0")"
if [ -x ".venv/bin/python" ]; then
  .venv/bin/python MAAK_RELEASE.py
else
  python3 MAAK_RELEASE.py
fi
read -p "Druk op Enter om te sluiten..."
