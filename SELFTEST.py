from __future__ import annotations

import re
import sys
from pathlib import Path

from jinja2 import Environment

BASE_DIR = Path(__file__).resolve().parent
EXPECTED_VERSION = "2.9.0"

errors = []
warnings = []

app_file = BASE_DIR / "app.py"
source = app_file.read_text(encoding="utf-8")

# Version
match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', source)
version = match.group(1) if match else None
if version != EXPECTED_VERSION:
    errors.append(f"APP_VERSION is {version!r}, verwacht {EXPECTED_VERSION!r}")

# Python syntax
try:
    compile(source, str(app_file), "exec")
except Exception as exc:
    errors.append(f"app.py compileert niet: {exc}")

# Jinja syntax
env = Environment()
templates = sorted((BASE_DIR / "templates").glob("*.html"))
for template in templates:
    try:
        env.parse(template.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"Templatefout in {template.name}: {exc}")

# Required route functions in source.
required_functions = [
    "login", "logout", "dashboard", "employees", "employee_detail",
    "assets_overview", "asset_detail", "registrations_overview",
    "credentials", "documents", "settings_page", "audit_log", "health",
    "action_center", "onboarding_detail", "onboarding_start", "offboarding_detail",
]
for name in required_functions:
    if not re.search(rf"^def {re.escape(name)}\s*\(", source, flags=re.M):
        errors.append(f"Kernfunctie ontbreekt: {name}")

# Required 1.0 building blocks.
checks = {
    "modulepersistentie": "MODULES_FILE = STORAGE_DIR / \"modules.enc\"",
    "centrale assets": "def load_assets()",
    "centrale registraties": "def load_registrations()",
    "credentials": "def load_credentials()",
    "bruteforcebescherming": "MAX_LOGIN_ATTEMPTS",
    "auditlog": "def log_action(",
    "backup": "def settings_backup(",
}
for label, needle in checks.items():
    if needle not in source:
        errors.append(f"1.0-controle ontbreekt: {label}")

# Start scripts must explicitly identify this release.
for name in ("START_WINDOWS.bat", "START_MAC.command"):
    path = BASE_DIR / name
    if not path.exists():
        errors.append(f"{name} ontbreekt")
        continue
    text = path.read_text(encoding="utf-8")
    if EXPECTED_VERSION not in text:
        errors.append(f"{name} controleert niet expliciet op {EXPECTED_VERSION}")
    if "/api/health" not in text:
        errors.append(f"{name} voert geen health/version-check uit")

# Launch scripts must always use the real IPv4 loopback address.
for name in ("START_WINDOWS.bat", "START_MAC.command"):
    text = (BASE_DIR / name).read_text(encoding="utf-8")
    if "127.0.0.1" not in text:
        errors.append(f"{name} gebruikt niet 127.0.0.1 als loopbackadres.")
    for bad in ("127.1.0.0", "127.1.1.0"):
        if bad in text:
            errors.append(f"{name} bevat een beschadigd loopbackadres: {bad}")

# Footer must use the central version variable, not a literal.
base_template = (BASE_DIR / "templates" / "base.html").read_text(encoding="utf-8")
if "{{ app_version }}" not in base_template:
    errors.append("Footer gebruikt niet de centrale app_version.")

# Jinja dictionary keys named "items" must use bracket notation.
# `workflow.items` resolves to dict.items() and crashes only when the workflow exists.
for template_name in ("onboarding.html", "offboarding.html", "actions.html"):
    template_text = (BASE_DIR / "templates" / template_name).read_text(encoding="utf-8")
    bad_patterns = ("workflow.items", "w.items")
    for bad in bad_patterns:
        if bad in template_text:
            errors.append(f"{template_name} bevat onveilige Jinja-notatie {bad!r}; gebruik ['items'].")

# Detect the old hardcoded login title.
login_template = (BASE_DIR / "templates" / "login.html").read_text(encoding="utf-8")
if "Personeelsadministratie" in login_template:
    errors.append("Inlogscherm bevat nog hardcoded 'Personeelsadministratie'.")


# 1.3 Online Ready checks.
if 'SCHITTER_STORAGE_DIR' not in source:
    errors.append("1.3: configureerbare opslagmap ontbreekt.")
if 'os.environ.get("PORT")' not in source:
    errors.append("1.3: Render PORT-ondersteuning ontbreekt.")
if 'host = "0.0.0.0" if IS_PRODUCTION' not in source:
    errors.append("1.3: productie-bindadres ontbreekt.")
for required_file in ("render.yaml", ".gitignore", "ONLINE_MET_RENDER.md", "STOP_ALLE_PSB_WINDOWS.bat"):
    if not (BASE_DIR / required_file).exists():
        errors.append(f"1.3: {required_file} ontbreekt.")
render_text = (BASE_DIR / "render.yaml").read_text(encoding="utf-8") if (BASE_DIR / "render.yaml").exists() else ""
for needle in ("SCHITTER_STORAGE_DIR", "/var/data", "gunicorn", "disk:"):
    if needle not in render_text:
        errors.append(f"1.3: render.yaml mist {needle!r}.")
gitignore = (BASE_DIR / ".gitignore").read_text(encoding="utf-8") if (BASE_DIR / ".gitignore").exists() else ""
for needle in ("storage/*", "*.enc", "*.key", ".env"):
    if needle not in gitignore:
        errors.append(f"1.3: .gitignore mist bescherming voor {needle!r}.")

# 1.3.1 secure online bootstrap checks.
if 'SCHITTER_SETUP_CODE' not in source:
    errors.append("1.3.1: SCHITTER_SETUP_CODE ondersteuning ontbreekt.")
if 'IS_PRODUCTION and not expected_setup_code' not in source:
    errors.append("1.3.1: online setup faalt niet veilig zonder setupcode.")
render_text = (BASE_DIR / "render.yaml").read_text(encoding="utf-8")
if "SCHITTER_SETUP_CODE" not in render_text or "sync: false" not in render_text:
    errors.append("1.3.1: Render setup-secret is niet handmatig geheim geconfigureerd.")

# 1.3.2 Mail & branding checks.
if "def _branded_email_html(" not in source:
    errors.append("1.3.2: centrale HTML-mailtemplate ontbreekt.")
if "msg.add_alternative(body_html" not in source:
    errors.append("1.3.2: HTML-alternatief wordt niet aan e-mail toegevoegd.")
if 'cid="<psb-logo>"' not in source:
    errors.append("1.3.2: inline logo voor e-mail ontbreekt.")
if "def _invite_email_content(" not in source:
    errors.append("1.3.2: uitnodigingsmail gebruikt de nieuwe mailbasis niet.")

# 1.3.3 Branding & polish checks.
if 'COPYRIGHT_OWNER = "AM | Software as a Hobby"' not in source:
    errors.append("1.3.5: developer-copyrightnaam ontbreekt.")
if "preheader:" not in source or "mso-hide:all" not in source:
    errors.append("1.3.3: e-mailpreheader ontbreekt.")
if "background:linear-gradient" in source:
    errors.append("1.3.3: oude gradient-huisstijllijn staat nog in de mailtemplate.")
base_template = (BASE_DIR / "templates" / "base.html").read_text(encoding="utf-8")
if "© {{ copyright_year }} {{ copyright_owner }}" not in base_template:
    errors.append("1.3.3: copyright ontbreekt in applicatiefooter.")
if "Gegevens lokaal opgeslagen in de programmamap" in base_template:
    errors.append("1.3.3: online onjuiste lokale opslagtekst staat nog in footer.")

# 1.3.3 application footer branding check.
style_source = (BASE_DIR / "static" / "style.css").read_text(encoding="utf-8")
if ".statusbar::after" not in style_source or "linear-gradient(90deg,var(--pink)" not in style_source:
    errors.append("1.3.4: dunne vierkleurige huisstijllijn ontbreekt onder de applicatiefooter.")
if ".statusbar{position:fixed" not in style_source or "background:#fff" not in style_source:
    errors.append("1.3.4: rustige lichte applicatiefooter ontbreekt.")

# 1.3.5 Mobile & identity checks.
base_template = (BASE_DIR / "templates" / "base.html").read_text(encoding="utf-8")
style_source = (BASE_DIR / "static" / "style.css").read_text(encoding="utf-8")
for required in ("favicon.ico", "favicon.png"):
    if not (BASE_DIR / "static" / required).exists():
        errors.append(f"1.3.5: {required} ontbreekt.")
if 'data-mobile-nav-open' not in base_template or 'data-mobile-nav-close' not in base_template:
    errors.append("1.3.5: mobiele navigatiebediening ontbreekt.")
if "Uitloggen" not in base_template:
    errors.append("1.3.5: mobiele uitlogtekst ontbreekt.")
if "body.mobile-nav-open .sidebar" not in style_source:
    errors.append("1.3.5: mobiele off-canvas sidebar CSS ontbreekt.")
if "overflow-x:auto" not in style_source:
    errors.append("1.3.5: mobiele tabel-scroll ontbreekt.")

# 1.3.6 controlled Excel migration checks.
if "def _parse_employee_excel(" not in source:
    errors.append("1.3.6: gecontroleerde Excel-parser ontbreekt.")
if "EXCEL_IMPORT_EXPECTED_ROWS" not in source:
    errors.append("1.3.6: Excel-structuurcontrole ontbreekt.")
if "def _create_excel_import_backup(" not in source or "__manifest__.json" not in source:
    errors.append("1.3.6: herstelpunt/rollback voor Excel-import ontbreekt.")
if "Conflict bij account/code" not in source:
    errors.append("1.3.6: conflictstop voor bestaande geheime waarden ontbreekt.")
if "settings_import_excel_confirm" not in source:
    errors.append("1.3.6: bevestigde Excel-import route ontbreekt.")
if "openpyxl" not in (BASE_DIR / "requirements.txt").read_text(encoding="utf-8").lower():
    errors.append("1.3.6: openpyxl dependency ontbreekt.")
for template_name in ("import_excel.html", "import_excel_result.html"):
    if not (BASE_DIR / "templates" / template_name).exists():
        errors.append(f"1.3.6: {template_name} ontbreekt.")

# 1.4.0 functional polish + PWA checks.
if 'def about_page()' not in source:
    errors.append("1.4.0: About-route ontbreekt.")
if 'def web_manifest()' not in source or 'def service_worker()' not in source:
    errors.append("1.4.0: PWA manifest/service-worker routes ontbreken.")
if 'if module_enabled("hr_employment"):' not in source or 'e["function"] = _resolve_choice_field' not in source:
    errors.append("1.4.0: functie/HR-formulierbescherming ontbreekt.")
base_template = (BASE_DIR / "templates" / "base.html").read_text(encoding="utf-8")
dashboard_template = (BASE_DIR / "templates" / "dashboard.html").read_text(encoding="utf-8")
employee_form_template = (BASE_DIR / "templates" / "employee_form.html").read_text(encoding="utf-8")
if 'rel="manifest"' not in base_template or "serviceWorker.register" not in base_template:
    errors.append("1.4.0: PWA registratie ontbreekt in base.html.")
if 'href="{{ url_for(\'about_page\') }}"' not in base_template:
    errors.append("1.4.0: About ontbreekt in navigatie.")
if "stat-card-link" not in dashboard_template:
    errors.append("1.4.0: dashboardkaarten zijn niet klikbaar.")
if "choice_field('function'" not in employee_form_template:
    errors.append("1.4.0: Functie ontbreekt in basisformulier.")
for required_file in (
    "templates/about.html",
    "static/about-ai-assistant.png",
    "static/service-worker.js",
    "static/pwa/icon-192.png",
    "static/pwa/icon-512.png",
    "static/pwa/icon-maskable-512.png",
    "static/pwa/apple-touch-icon.png",
):
    if not (BASE_DIR / required_file).exists():
        errors.append(f"1.4.0: {required_file} ontbreekt.")
sw_text = (BASE_DIR / "static" / "service-worker.js").read_text(encoding="utf-8")
if 'url.pathname.startsWith("/static/")' not in sw_text:
    errors.append("1.4.0: service worker cachet niet uitsluitend statische assets.")
if 'Employee pages' not in sw_text or "application data always use the network" not in sw_text:
    errors.append("1.4.0: privacyregel voor PWA-cache ontbreekt.")

print("Praktijk Schitter Beheer – SELFTEST")
print("=" * 42)
print(f"Versie: {version or 'onbekend'}")
print(f"Templates gecontroleerd: {len(templates)}")

if warnings:
    print("\nWaarschuwingen:")
    for item in warnings:
        print(" -", item)

if errors:
    print("\nFOUTEN:")
    for item in errors:
        print(" -", item)
    print("\nSELFTEST MISLUKT")
    sys.exit(1)

print("\nSELFTEST GESLAAGD ✓")
