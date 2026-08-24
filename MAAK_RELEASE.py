from pathlib import Path
import zipfile, re, shutil

ROOT = Path(__file__).resolve().parent
VERSION = "1.4.0"
OUTPUT = ROOT.parent / f"Praktijk_Schitter_Beheer_v{VERSION}_release.zip"

EXCLUDE_DIRS = {".venv", "__pycache__", ".git", ".idea", ".vscode"}
EXCLUDE_NAMES = {
    ".data.key", "users.json", "employees.enc", "settings.enc", "credentials.enc",
    "tasks.enc", "leave.enc", "leave_requests.enc", "time_entries.enc", "audit.log",
    "app.log", ".server.pid", "EERSTE_START_CODE.txt"
}

def include(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return False
    if path.name in EXCLUDE_NAMES:
        return False
    if "storage" in rel.parts and "documents" in rel.parts and path.name != ".gitkeep":
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
