from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION = "2.8.0"
OUTPUT = ROOT.parent / f"Praktijk_Schitter_Beheer_v{VERSION}_release.zip"

# Alles onder storage/ is runtime-data: versleutelde gegevens, sleutels
# (.data.key, .session.key), logbestanden, het opstartcodebestand, enz. De
# app maakt storage/ (en storage/documents/) zelf automatisch aan bij de
# eerste start (zie STORAGE_DIR.mkdir(...) in app.py), dus dit hoeft nooit in
# een releasezip te zitten. Door de hele map categorisch uit te sluiten kan
# een nieuw bestand dat later aan storage/ wordt toegevoegd nooit meer per
# ongeluk meegezipt worden (in tegenstelling tot de oude aanpak met een
# handmatig bijgehouden lijst van uitgesloten bestandsnamen, die al was
# achtergebleven bij nieuwere opslagbestanden zoals .session.key,
# assets.enc, audit.enc, modules.enc en registrations.enc).
EXCLUDE_DIRS = {".venv", "__pycache__", ".git", ".idea", ".vscode", "storage"}


def include(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return False
    if path.suffix in {".pyc", ".log"}:
        return False
    return True


if OUTPUT.exists():
    OUTPUT.unlink()
with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as zf:
    for path in sorted(ROOT.rglob("*")):
        if path.is_file() and include(path):
            zf.write(path, Path("praktijk_schitter_beheer") / path.relative_to(ROOT))
print(f"Release gemaakt: {OUTPUT}")
