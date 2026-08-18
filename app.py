from __future__ import annotations

import csv
import hashlib
import html as html_lib
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import smtplib
import sys
import threading
import time
import uuid
import zipfile
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from flask import (
    Flask, Response, abort, flash, jsonify, redirect, render_template,
    request, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent

# Lokaal blijft opslag gewoon in ./storage staan.
# Op Render wijzen we SCHITTER_STORAGE_DIR naar de gekoppelde Persistent Disk,
# bijvoorbeeld /var/data. Zo blijft dezelfde code lokaal én online bruikbaar.
STORAGE_DIR = Path(os.environ.get("SCHITTER_STORAGE_DIR", str(BASE_DIR / "storage"))).expanduser().resolve()
DOCUMENTS_DIR = STORAGE_DIR / "documents"
AUTH_FILE = STORAGE_DIR / "users.json"
SETUP_CODE_FILE = STORAGE_DIR / "EERSTE_START_CODE.txt"
KEY_FILE = STORAGE_DIR / ".data.key"
SESSION_KEY_FILE = STORAGE_DIR / ".session.key"
DATA_FILE = STORAGE_DIR / "employees.enc"
SETTINGS_FILE = STORAGE_DIR / "settings.enc"
MODULES_FILE = STORAGE_DIR / "modules.enc"

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)

# Wanneer de app zonder consolevenster wordt gestart (via pythonw.exe, zodat er
# geen storend zwart schermpje verschijnt) zijn sys.stdout/sys.stderr None.
# Zonder deze omleiding zou elke print() of onverwachte foutmelding de app
# laten crashen. In dat geval loggen we in plaats daarvan naar storage/app.log,
# zodat er bij problemen nog iets na te kijken is.
if sys.stdout is None or sys.stderr is None:
    _headless_log = open(STORAGE_DIR / "app.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = _headless_log
    sys.stderr = _headless_log

ALLOWED_EXTENSIONS = {
    "pdf", "doc", "docx", "xls", "xlsx", "jpg", "jpeg", "png", "txt"
}
ALLOWED_PHOTO_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Instelbare keuzelijsten: velden die met een select + "anders, namelijk..."
# werken in plaats van vrije tekst, zodat invoer consistent blijft maar
# nooit op slot zit.
CHOICE_LIST_FIELDS = [
    {"key": "function", "label": "Functie"},
    {"key": "work_location", "label": "Werklocatie"},
    {"key": "provider", "label": "Provider (telefoon)"},
    {"key": "os", "label": "Besturingssysteem"},
    {"key": "registration_type", "label": "Type opleiding/registratie"},
    {"key": "asset_type", "label": "Type bedrijfsmiddel"},
    {"key": "credential_category", "label": "Categorie account/code"},
    {"key": "credential_secret_type", "label": "Soort geheim"},
]

MAX_LOGIN_ATTEMPTS = 8
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_LOCKOUT_SECONDS = 5 * 60
AUDIT_MAX_ENTRIES = 5000
APP_VERSION = "1.3.5"
COPYRIGHT_OWNER = "AM | Software as a Hobby"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

IS_RENDER = bool(os.environ.get("RENDER"))
IS_PRODUCTION = os.environ.get("SCHITTER_ENV", "").lower() == "production" or IS_RENDER
if IS_PRODUCTION:
    app.config["PREFERRED_URL_SCHEME"] = "https"


# ---------- Sleutel- en sessiebeheer ----------
#
# De .data.key (voor de personeelsgegevens) en .session.key (voor het
# ondertekenen van sessiecookies) worden bewust apart van elkaar gehouden en
# nooit hergebruikt: als de sessiesleutel ooit zou lekken, blijft de
# personeelsdata alsnog versleuteld.

def _get_or_create_key(path: Path) -> bytes:
    if not path.exists():
        path.write_bytes(Fernet.generate_key())
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return path.read_bytes().strip()


def _get_fernet() -> Fernet:
    return Fernet(_get_or_create_key(KEY_FILE))


# SCHITTER_SESSION_SECRET blijft ondersteund voor wie de sleutel liever zelf
# beheert (bijvoorbeeld bij meerdere serverprocessen), maar zonder die
# omgevingsvariabele valt de app terug op een sleutel die één keer wordt
# aangemaakt en bewaard in storage/.session.key. Zo blijven gebruikers
# ingelogd na een herstart van de app, in plaats van dat iedereen bij elke
# herstart wordt uitgelogd.
app.secret_key = os.environ.get("SCHITTER_SESSION_SECRET") or _get_or_create_key(SESSION_KEY_FILE)

app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Zet SCHITTER_FORCE_HTTPS=1 zodra de app achter HTTPS draait (bijv. op een
# domein), zodat sessiecookies nooit onversleuteld over het netwerk gaan.
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION or os.environ.get("SCHITTER_FORCE_HTTPS") == "1"


# ---------- Encryptie / persistentie ----------

def _load_encrypted(path: Path, default):
    if not path.exists():
        return default
    try:
        decrypted = _get_fernet().decrypt(path.read_bytes())
        return json.loads(decrypted.decode("utf-8"))
    except (InvalidToken, json.JSONDecodeError):
        raise RuntimeError(f"Versleuteld bestand kon niet veilig worden gelezen: {path.name}")


def _save_encrypted(path: Path, data) -> None:
    raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    encrypted = _get_fernet().encrypt(raw)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(encrypted)
    temp.replace(path)


def load_employees() -> list[dict]:
    data = _load_encrypted(DATA_FILE, [])
    return data if isinstance(data, list) else []


def save_employees(employees: list[dict]) -> None:
    _save_encrypted(DATA_FILE, employees)


def default_settings():
    return {
        "custom_field_definitions": [
            {
                "id": "skj",
                "label": "SKJ-nummer",
                "section": "Opleiding & registratie",
                "type": "text"
            },
            {
                "id": "emergency_relation",
                "label": "Relatie noodcontact",
                "section": "Contact",
                "type": "text"
            },
        ],
        "app_name": "Praktijk Schitter Beheer",
        "app_subtitle": "Bedrijfsmiddelen, toegang & registraties",
        "modules": {
            "assets": True,
            "credentials": True,
            "registrations": True,
            "documents": True,
            "signals": True,
            "hr_employment": False,
            "hours": False,
            "leave": False,
            "employee_portal": False
        },
        "workflow_templates": {
            "onboarding": [
                "Startdatum en praktische afspraken bevestigen",
                "Laptop / werkplek regelen",
                "Telefoon of mobiele toegang regelen",
                "E-mailaccount en basisaccounts aanmaken",
                "Toegang tot gedeelde systemen regelen",
                "Sleutels, passen en fysieke toegang regelen",
                "Benodigde registraties controleren",
                "Benodigde documenten controleren",
            ],
            "offboarding": [
                "Laatste werkdag bevestigen",
                "E-mail en overige toegang controleren",
                "Sleutels, passen en fysieke toegang controleren",
                "Openstaande documenten en afspraken controleren",
            ],
        },
        "choice_lists": {
            "function": [
                "Orthopedagoog-generalist", "GZ-psycholoog", "Orthopedagoog in opleiding",
                "Praktijkondersteuner", "Officemanager / secretariaat", "Directeur / eigenaar",
            ],
            "work_location": [
                "Hoofdlocatie", "Nevenlocatie", "Extern / bij cliënt", "Thuiswerkend",
            ],
            "provider": ["KPN", "T-Mobile", "Vodafone", "Odido", "Simpel", "Youfone"],
            "os": ["Windows 11", "Windows 10", "macOS", "iOS", "Android", "ChromeOS"],
            "registration_type": [
                "SKJ-registratie", "BIG-registratie", "VOG", "Diploma", "BHV", "Vertrouwenspersoon",
            ],
            "asset_type": [
                "Telefoon", "Laptop", "Tablet", "Sleutel", "Toegangspas", "Token", "Monitor", "Overig",
            ],
            "credential_category": [
                "Account", "Technische sleutel", "Licentie", "Toegangscode", "Overig",
            ],
            "credential_secret_type": [
                "Wachtwoord", "PIN", "PUK", "Toegangscode", "API-key", "Mail key",
                "Herstelcode", "Licentiesleutel", "Overig",
            ],
        },
    }


def load_settings() -> dict:
    data = _load_encrypted(SETTINGS_FILE, default_settings())
    if not isinstance(data, dict):
        return default_settings()
    defaults = default_settings()
    data.setdefault("custom_field_definitions", [])
    data.setdefault("app_name", defaults["app_name"])
    data.setdefault("app_subtitle", defaults["app_subtitle"])
    data.setdefault("modules", {})
    for key, enabled in defaults["modules"].items():
        data["modules"].setdefault(key, enabled)
    data.setdefault("workflow_templates", {})
    for key, values in defaults["workflow_templates"].items():
        data["workflow_templates"].setdefault(key, values.copy())
    data.setdefault("choice_lists", {})
    for key, values in defaults["choice_lists"].items():
        data["choice_lists"].setdefault(key, values)
    data.setdefault("smtp_host", "smtp.strato.de")
    data.setdefault("smtp_port", "465")
    data.setdefault("smtp_encryption", "ssl")
    data.setdefault("smtp_username", "")
    data.setdefault("smtp_password", "")
    data.setdefault("smtp_from_email", "")
    data.setdefault("smtp_from_name", "")
    return data


def _resolve_choice_field(form, field_key: str) -> str:
    """Select-met-'anders'-velden posten `<key>_choice` en `<key>_other`;
    een gekozen lijstoptie wint, anders valt terug op vrije tekst."""
    choice = form.get(f"{field_key}_choice", "").strip()
    other = form.get(f"{field_key}_other", "").strip()
    return choice or other


def save_settings(settings: dict) -> None:
    _save_encrypted(SETTINGS_FILE, settings)


def load_module_settings() -> dict:
    """Load module switches from a dedicated encrypted file.

    On first use, migrate any existing module values from settings.enc.
    Keeping module switches in their own file prevents unrelated settings
    saves from ever overwriting them.
    """
    defaults = default_settings()["modules"]
    if MODULES_FILE.exists():
        data = _load_encrypted(MODULES_FILE, defaults.copy())
        if not isinstance(data, dict):
            data = {}
    else:
        legacy_settings = load_settings()
        legacy_modules = legacy_settings.get("modules", {})
        data = {
            key: bool(legacy_modules.get(key, default_value))
            for key, default_value in defaults.items()
        }
        _save_encrypted(MODULES_FILE, data)

    # New modules introduced by later versions get their defined default.
    changed = False
    for key, default_value in defaults.items():
        if key not in data:
            data[key] = default_value
            changed = True
    if changed:
        _save_encrypted(MODULES_FILE, data)
    return data


def save_module_settings(modules: dict) -> None:
    defaults = default_settings()["modules"]
    clean = {
        key: bool(modules.get(key, default_value))
        for key, default_value in defaults.items()
    }
    _save_encrypted(MODULES_FILE, clean)



# ---------- Modulair applicatiebeheer ----------

MODULE_REGISTRY = [
    {"key":"assets", "label":"Bedrijfsmiddelen", "group":"Basis", "description":"Telefoons, laptops, sleutels, passen en overige middelen."},
    {"key":"credentials", "label":"Accounts & codes", "group":"Basis", "description":"Accounts, wachtwoorden, PIN/PUK, sleutels en herstelcodes."},
    {"key":"registrations", "label":"Registraties", "group":"Basis", "description":"SKJ, NVO, VOG, BHV en andere registraties met verloopdata."},
    {"key":"documents", "label":"Documenten", "group":"Basis", "description":"Versleutelde documenten in personeelsdossiers."},
    {"key":"signals", "label":"Signaleringen & taken", "group":"Basis", "description":"Aandachtspunten, deadlines en taken."},
    {"key":"hr_employment", "label":"HR / dienstverband", "group":"HR", "description":"Uitgebreide contract- en dienstverbandgegevens."},
    {"key":"hours", "label":"Urenregistratie", "group":"HR", "description":"Urenregistratie voor medewerkers en beheerders."},
    {"key":"leave", "label":"Verlof", "group":"HR", "description":"Verlofrecht, aanvragen en goedkeuring."},
    {"key":"employee_portal", "label":"Medewerkersportaal", "group":"Portaal", "description":"Eigen login en persoonlijke omgeving voor medewerkers."},
]

def module_enabled(key: str) -> bool:
    return bool(load_module_settings().get(key, False))

def module_required(key: str):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not module_enabled(key):
                flash("Deze module staat momenteel uitgeschakeld.", "error")
                return redirect(url_for("dashboard"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


EMPLOYEE_TAB_MODULES = {
    "employment": "hr_employment",
    "assets": "assets",
    "credentials": "credentials",
    "documents": "documents",
    "registrations": "registrations",
}

def employee_tab_available(tab: str) -> bool:
    """Tabs without a module mapping are part of the permanent employee core."""
    required_module = EMPLOYEE_TAB_MODULES.get(tab)
    return True if required_module is None else module_enabled(required_module)


# ---------- E-mail (SMTP) ----------
#
# De SMTP-gegevens (inclusief wachtwoord) staan net als de rest van de
# praktijkinstellingen versleuteld in settings.enc. Er wordt bewust geen
# apart pakket voor e-mail toegevoegd: alles hier gebeurt met de
# ingebouwde smtplib, zodat de app geen extra afhankelijkheden nodig heeft.


def _branded_email_html(
    *,
    title: str,
    message: str,
    eyebrow: str = "",
    button_label: str = "",
    button_url: str = "",
    details: list[tuple[str, str]] | None = None,
    footer_note: str = "Dit is een automatisch verzonden bericht vanuit Praktijk Schitter Beheer.",
    preheader: str = "",
) -> str:
    """Bouw één herbruikbare, mailclient-vriendelijke Praktijk Schitter-template.

    Inline CSS + presentatietabellen blijven bewust de basis vanwege Outlook.
    De vierkleurige huisstijllijn staat conform de huisstijl onderaan.
    """
    settings = load_settings()
    app_name = settings.get("app_name", "Praktijk Schitter Beheer")
    app_subtitle = settings.get("app_subtitle", "Bedrijfsmiddelen, toegang & registraties")
    year = datetime.now().year

    safe_title = html_lib.escape(title)
    safe_message = html_lib.escape(message).replace("\n", "<br>")
    safe_eyebrow = html_lib.escape(eyebrow)
    safe_app_name = html_lib.escape(app_name)
    safe_subtitle = html_lib.escape(app_subtitle)
    safe_footer = html_lib.escape(footer_note)
    safe_owner = html_lib.escape(COPYRIGHT_OWNER)
    safe_preheader = html_lib.escape(preheader or f"{title} — {app_name}")

    detail_rows = ""
    for label, value in details or []:
        detail_rows += (
            '<tr>'
            f'<td style="padding:6px 0;color:#647477;font-size:13px;width:140px;vertical-align:top;">{html_lib.escape(str(label))}</td>'
            f'<td style="padding:6px 0;color:#183235;font-size:13px;font-weight:700;vertical-align:top;">{html_lib.escape(str(value))}</td>'
            '</tr>'
        )

    details_block = ""
    if detail_rows:
        details_block = (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="margin:24px 0 0;background:#f5fbfa;border:1px solid #dceeed;border-radius:12px;">'
            '<tr><td style="padding:16px 18px;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            f'{detail_rows}'
            '</table></td></tr></table>'
        )

    button_block = ""
    if button_label and button_url:
        safe_label = html_lib.escape(button_label)
        safe_url = html_lib.escape(button_url, quote=True)
        button_block = (
            '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:26px 0 4px;">'
            '<tr><td style="background:#17a5a3;border-radius:10px;">'
            f'<a href="{safe_url}" style="display:inline-block;padding:13px 20px;color:#ffffff;'
            'font-family:Arial,Helvetica,sans-serif;font-size:14px;font-weight:700;text-decoration:none;">'
            f'{safe_label}</a></td></tr></table>'
        )

    eyebrow_block = (
        f'<div style="font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;'
        f'color:#9d7629;margin-bottom:8px;">{safe_eyebrow}</div>'
        if safe_eyebrow else ""
    )

    # Geen CSS-gradient: vier echte cellen renderen betrouwbaarder in Outlook.
    brand_line = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">'
        '<tr>'
        '<td width="25%" height="6" style="background:#eb6091;font-size:0;line-height:0;">&nbsp;</td>'
        '<td width="25%" height="6" style="background:#17a5a3;font-size:0;line-height:0;">&nbsp;</td>'
        '<td width="25%" height="6" style="background:#82D5D1;font-size:0;line-height:0;">&nbsp;</td>'
        '<td width="25%" height="6" style="background:#9d7629;font-size:0;line-height:0;">&nbsp;</td>'
        '</tr></table>'
    )

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width">
  <meta name="x-apple-disable-message-reformatting">
</head>
<body style="margin:0;padding:0;background:#f3f6f6;font-family:Arial,Helvetica,sans-serif;color:#183235;">
  <div style="display:none!important;visibility:hidden;opacity:0;color:transparent;height:0;width:0;overflow:hidden;mso-hide:all;">
    {safe_preheader}&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;
  </div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;background:#f3f6f6;">
    <tr><td align="center" style="padding:28px 12px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="width:100%;max-width:640px;background:#ffffff;border:1px solid #e1eaea;border-radius:18px;overflow:hidden;">
        <tr>
          <td style="padding:26px 34px 18px;border-bottom:1px solid #edf1f1;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="vertical-align:middle;">
                  <img src="cid:psb-logo" alt="{safe_app_name}" width="190" style="display:block;max-width:190px;width:100%;height:auto;border:0;">
                </td>
                <td align="right" style="vertical-align:middle;color:#7a8a8d;font-size:11px;line-height:1.4;padding-left:16px;">
                  {safe_subtitle}
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:34px;">
            {eyebrow_block}
            <h1 style="margin:0 0 14px;color:#183235;font-size:26px;line-height:1.25;">{safe_title}</h1>
            <div style="font-size:15px;line-height:1.7;color:#4c6164;">{safe_message}</div>
            {details_block}
            {button_block}
          </td>
        </tr>
        <tr>
          <td style="padding:18px 34px;background:#f9fbfb;border-top:1px solid #edf1f1;color:#829093;font-size:11px;line-height:1.55;">
            <strong style="color:#17a5a3;">{safe_app_name}</strong><br>
            {safe_footer}<br>
            <span style="color:#9aa5a7;">© {year} {safe_owner}. Alle rechten voorbehouden.</span>
          </td>
        </tr>
        <tr><td style="padding:0;">{brand_line}</td></tr>
      </table>
      <div style="max-width:640px;padding:14px 24px 0;color:#9aa5a7;font-size:10px;line-height:1.5;text-align:center;">
        Dit bericht kan vertrouwelijke informatie bevatten. Deel het alleen met de bedoelde ontvanger.
      </div>
    </td></tr>
  </table>
</body>
</html>"""


def send_email(
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> tuple[bool, str | None]:
    if not to_email:
        return False, "Geen e-mailadres opgegeven."

    settings = load_settings()
    host = settings.get("smtp_host", "").strip()
    port_raw = settings.get("smtp_port", "465").strip() or "465"
    encryption = settings.get("smtp_encryption", "ssl")
    username = settings.get("smtp_username", "").strip()
    password = settings.get("smtp_password", "")
    from_email = settings.get("smtp_from_email", "").strip() or username
    from_name = (
        settings.get("smtp_from_name", "").strip()
        or settings.get("app_name", "").strip()
        or "Praktijk Schitter Beheer"
    )

    if not host or not from_email:
        return False, "E-mail is nog niet geconfigureerd (zie Instellingen › E-mail)."

    try:
        port = int(port_raw)
    except ValueError:
        return False, "De ingestelde SMTP-poort is ongeldig."

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    msg.set_content(body_text)

    if body_html:
        msg.add_alternative(body_html, subtype="html")
        logo_path = BASE_DIR / "static" / "logo.png"
        if logo_path.exists():
            try:
                html_part = msg.get_payload()[-1]
                html_part.add_related(
                    logo_path.read_bytes(),
                    maintype="image",
                    subtype="png",
                    cid="<psb-logo>",
                    filename="praktijk-schitter-logo.png",
                    disposition="inline",
                )
            except Exception:
                # Een ontbrekend/ongeldig logo mag e-mailverzending nooit blokkeren.
                pass

    try:
        if encryption == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
        with server:
            if encryption == "starttls":
                server.starttls()
            if username:
                server.login(username, password)
            server.send_message(msg)
        return True, None
    except Exception as exc:
        return False, str(exc)


def _invite_email_content(name: str, practice_name: str, link: str, hours_valid: int = 48) -> tuple[str, str]:
    body_text = (
        f"Hoi {name},\n\n"
        f"Er is een account voor je aangemaakt in {practice_name}.\n\n"
        f"Stel via deze link je wachtwoord in:\n{link}\n\n"
        f"Deze link is {hours_valid} uur geldig. Heb je dit account niet verwacht, "
        f"dan kun je deze e-mail negeren.\n\n"
        f"Met vriendelijke groet,\n{practice_name}"
    )
    body_html = _branded_email_html(
        eyebrow="Uitnodiging",
        title=f"Welkom, {name}",
        message=(
            f"Er is een account voor je aangemaakt in {practice_name}. "
            "Gebruik de knop hieronder om je eigen wachtwoord in te stellen."
        ),
        button_label="Wachtwoord instellen",
        button_url=link,
        details=[
            ("Geldigheid link", f"{hours_valid} uur"),
            ("Account", name),
        ],
        footer_note="Heb je dit account niet verwacht? Dan kun je deze e-mail veilig negeren.",
        preheader=f"Je account voor {practice_name} staat klaar. Stel binnen {hours_valid} uur je wachtwoord in.",
    )
    return body_text, body_html

def _hash_invite_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _create_invite(user: dict, hours_valid: int = 48) -> str:
    """Genereert een eenmalige setup-link-token voor `user` en zet die (gehasht) klaar.

    De ruwe token wordt teruggegeven zodat de aanroepende route hem in een
    e-mail en/of op het scherm kan tonen; alleen de hash wordt bewaard.
    """
    token = secrets.token_urlsafe(32)
    user["invite_token_hash"] = _hash_invite_token(token)
    user["invite_expires_at"] = (datetime.now() + timedelta(hours=hours_valid)).isoformat(timespec="seconds")
    user["invite_pending"] = True
    # Zolang de uitnodiging openstaat, kan er sowieso niet mee ingelogd worden.
    user["password_hash"] = generate_password_hash(secrets.token_hex(32))
    return token


def load_users() -> list[dict]:
    if not AUTH_FILE.exists():
        return []
    try:
        return json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def save_users(users: list[dict]) -> None:
    AUTH_FILE.write_text(
        json.dumps(users, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )



def _generate_setup_code() -> str:
    """Maak een handmatig invoerbare eenmalige code voor de eerste beheerder."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    chunks = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3)]
    return "PSB-" + "-".join(chunks)


def ensure_first_setup_code() -> str:
    """Geef de eenmalige code voor de eerste beheerder terug.

    Online komt deze uitsluitend uit SCHITTER_SETUP_CODE.
    Lokaal blijft de bestaande eerste-startcode in storage werken.
    """
    if load_users():
        try:
            SETUP_CODE_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        return ""

    env_code = os.environ.get("SCHITTER_SETUP_CODE", "").strip()
    if IS_PRODUCTION:
        return env_code
    if env_code:
        return env_code

    if SETUP_CODE_FILE.exists():
        try:
            for line in SETUP_CODE_FILE.read_text(encoding="utf-8").splitlines():
                if line.startswith("CODE="):
                    code = line.split("=", 1)[1].strip()
                    if code:
                        return code
        except OSError:
            pass

    code = _generate_setup_code()
    SETUP_CODE_FILE.write_text(
        "Praktijk Schitter Beheer — eenmalige eerste-startcode\n\n"
        "Deze code is alleen nodig om de ALLEREERSTE beheerder aan te maken.\n"
        "Na succesvolle configuratie wordt dit bestand automatisch verwijderd.\n"
        "Deel deze code niet met medewerkers of onbevoegden.\n\n"
        f"CODE={code}\n",
        encoding="utf-8",
    )
    try:
        os.chmod(SETUP_CODE_FILE, 0o600)
    except OSError:
        pass
    return code

def verify_current_admin_password(password: str) -> bool:
    """Herbevestig gevoelige beheeracties met het wachtwoord van de huidige beheerder."""
    if not password:
        return False
    user = current_user()
    if not user or user.get("role") != "Beheerder":
        return False
    password_hash = user.get("password_hash")
    return bool(password_hash and check_password_hash(password_hash, password))


def current_user():
    email = session.get("user_email")
    if not email:
        return None
    return next((u for u in load_users() if u.get("email") == email), None)


def is_admin():
    user = current_user()
    return bool(user and user.get("role") == "Beheerder")


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_email" not in session:
            return redirect(url_for("login"))
        if not is_admin():
            flash("Je hebt geen toegang tot dit onderdeel.", "error")
            return redirect(url_for("employee_portal"))
        return view(*args, **kwargs)
    return wrapped


def get_employee_for_current_user():
    user = current_user()
    if not user or user.get("role") != "Medewerker":
        return None
    employee_id = user.get("employee_id")
    if not employee_id:
        return None
    emp = find_employee(employee_id)
    return normalize_employee(emp) if emp else None


# ---------- Gelijktijdige toegang ----------
#
# De ontwikkelserver verwerkt standaard één verzoek tegelijk, maar zodra
# deze app ooit met threading of achter een productie-WSGI-server draait,
# kunnen twee beheerders elkaars wijzigingen overschrijven (lezen - wijzigen
# - wegschrijven van hetzelfde bestand). Deze lock dwingt af dat zulke
# lees-wijzig-schrijf-acties nooit door elkaar heen lopen.

_storage_lock = threading.RLock()


def synchronized(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        with _storage_lock:
            return view(*args, **kwargs)
    return wrapped


# ---------- Auditlog ----------

def log_action(action: str, detail: str = "", target: str = "", user: str | None = None) -> None:
    entry = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "user": user or session.get("user_name") or session.get("user_email") or "systeem",
        "action": action,
        "target": target,
        "detail": detail,
    }
    with _storage_lock:
        entries = load_module("audit")
        entries.append(entry)
        if len(entries) > AUDIT_MAX_ENTRIES:
            entries = entries[-AUDIT_MAX_ENTRIES:]
        save_module("audit", entries)


# ---------- CSRF-bescherming ----------
#
# Elk formulier dat wijzigingen aanbrengt (POST) bevat een verborgen
# csrf_token-veld. Zonder geldig token wordt de actie geweigerd. Dit
# voorkomt dat een kwaadwillende pagina, terwijl een beheerder is
# ingelogd, ongemerkt acties in deze app laat uitvoeren.

def _ensure_csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(24)
    return session["csrf_token"]


@app.context_processor
def inject_csrf():
    return {"csrf_token": _ensure_csrf_token}


@app.context_processor
def inject_app_configuration():
    settings = load_settings()
    return {
        "app_name": settings.get("app_name", "Praktijk Schitter Beheer"),
        "app_subtitle": settings.get("app_subtitle", "Bedrijfsmiddelen, toegang & registraties"),
        "enabled_modules": load_module_settings(),
        "module_enabled": module_enabled,
        "app_version": APP_VERSION,
        "copyright_owner": COPYRIGHT_OWNER,
        "copyright_year": datetime.now().year,
    }


@app.before_request
def csrf_protect():
    _ensure_csrf_token()
    if request.method == "POST":
        token = session.get("csrf_token", "")
        form_token = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
        if not token or not form_token or not secrets.compare_digest(token, form_token):
            flash("Je sessie was verlopen of ongeldig. Probeer het nog eens.", "error")
            if "user_email" in session:
                return redirect(request.referrer or url_for("dashboard"))
            return redirect(url_for("login"))
    return None


# ---------- Inlogpogingen beperken ----------

_login_attempts: dict[str, dict] = {}
_login_attempts_lock = threading.Lock()


def _login_key() -> str:
    email = request.form.get("email", "").strip().lower()
    return f"{request.remote_addr}:{email}"


def _is_locked_out(key: str) -> tuple[bool, int]:
    with _login_attempts_lock:
        info = _login_attempts.get(key)
        if not info:
            return False, 0
        locked_until = info.get("locked_until", 0)
        if locked_until and locked_until > time.time():
            return True, int(locked_until - time.time())
        return False, 0


def _register_login_failure(key: str) -> None:
    with _login_attempts_lock:
        now = time.time()
        info = _login_attempts.get(key)
        if not info or now - info["first"] > LOGIN_WINDOW_SECONDS:
            info = {"count": 0, "first": now, "locked_until": 0}
        info["count"] += 1
        if info["count"] >= MAX_LOGIN_ATTEMPTS:
            info["locked_until"] = now + LOGIN_LOCKOUT_SECONDS
        _login_attempts[key] = info


def _clear_login_failures(key: str) -> None:
    with _login_attempts_lock:
        _login_attempts.pop(key, None)


# ---------- Helpers ----------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_email" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def find_employee(employee_id: str, employees=None):
    employees = employees if employees is not None else load_employees()
    return next((e for e in employees if e["id"] == employee_id), None)


def normalize_employee(employee: dict) -> dict:
    defaults = {
        "first_name": "", "last_name": "", "function": "", "email": "", "phone": "",
        "address": "", "postal_code": "", "city": "", "birth_date": "",
        "emergency_name": "", "emergency_phone": "",
        "start_date": "", "contract_end": "", "hours": "", "status": "In dienst",
        "employment_type": "", "salary_scale": "", "work_email": "",
        "work_location": "", "manager": "", "notes": "",
        "documents": [], "assets": [], "registrations": [], "custom_fields": {},
        "annual_leave_hours": "0", "carryover_leave_hours": "0",
        "photo": None,
    }
    for k, v in defaults.items():
        employee.setdefault(k, v if not isinstance(v, list) else [])
    return employee


def calculate_stats(employees: list[dict]) -> dict:
    employees = [normalize_employee(e) for e in employees]
    active = [e for e in employees if e.get("status") == "In dienst"]
    inactive = [e for e in employees if e.get("status") == "Uit dienst"]

    today = date.today()
    expiring = 0
    for e in active:
        end_date = e.get("contract_end")
        if not end_date:
            continue
        try:
            end = date.fromisoformat(end_date)
            delta = (end - today).days
            if 0 <= delta <= 60:
                expiring += 1
        except ValueError:
            pass

    doc_count = sum(len(e.get("documents", [])) for e in employees)
    asset_count = len(load_assets()) if module_enabled("assets") else 0
    return {
        "total": len(employees),
        "active": len(active),
        "inactive": len(inactive),
        "expiring": expiring,
        "documents": doc_count,
        "assets": asset_count,
    }


def _validate_employee_fields(e: dict) -> list[str]:
    errors = []
    if e.get("email") and not EMAIL_RE.match(e["email"]):
        errors.append("Het privé e-mailadres ziet er niet geldig uit.")
    if e.get("work_email") and not EMAIL_RE.match(e["work_email"]):
        errors.append("Het zakelijke e-mailadres ziet er niet geldig uit.")
    for field, label in [
        ("birth_date", "Geboortedatum"),
        ("start_date", "Datum in dienst"),
        ("contract_end", "Einddatum contract"),
    ]:
        value = e.get(field)
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                errors.append(f"{label} is geen geldige datum (verwacht formaat jjjj-mm-dd).")
    for field, label in [
        ("annual_leave_hours", "Jaarrecht verlof"),
        ("carryover_leave_hours", "Meegenomen verlof"),
    ]:
        value = e.get(field)
        if value:
            try:
                float(value)
            except ValueError:
                errors.append(f"{label} moet een getal zijn.")
    return errors


# ---------- Praktijkbrede modules ----------

def load_module(name):
    data = _load_encrypted(STORAGE_DIR / f"{name}.enc", [])
    return data if isinstance(data, list) else []

def save_module(name, data):
    _save_encrypted(STORAGE_DIR / f"{name}.enc", data)


CREDENTIAL_SCOPES = ["Persoonlijk", "Gedeeld", "Praktijkbreed"]
CREDENTIAL_SECRET_TYPES = ["Wachtwoord", "PIN", "PUK", "Toegangscode", "API-key", "Mail key", "Herstelcode", "Licentiesleutel", "Overig"]

def load_credentials():
    records = load_module("credentials")
    changed = False
    for record in records:
        record.setdefault("service", "")
        record.setdefault("scope", "Persoonlijk")
        record.setdefault("employee_id", "")
        record.setdefault("linked_employee_ids", [])
        # Migrate old single-employee storage into the new flexible list.
        if record.get("employee_id") and record["employee_id"] not in record["linked_employee_ids"]:
            record["linked_employee_ids"].append(record["employee_id"])
            changed = True
        record.setdefault("username", "")
        record.setdefault("secret_type", "Wachtwoord")
        record.setdefault("secret", "")
        record.setdefault("url", "")
        record.setdefault("category", "Account")
        record.setdefault("status", "Actief")
        record.setdefault("notes", "")
        record.setdefault("last_verified_at", "")
        record.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
        record.setdefault("updated_at", record["created_at"])
    if changed:
        save_module("credentials", records)
    return records

def save_credentials(records):
    save_module("credentials", records)

def credential_employee_names(record, employees_map):
    ids = record.get("linked_employee_ids", [])
    names = []
    for employee_id in ids:
        employee = employees_map.get(employee_id)
        if employee:
            names.append(f"{employee.get('first_name','')} {employee.get('last_name','')}".strip())
    return names


ASSET_STATUSES = ["Vrij", "Uitgegeven", "Reparatie", "Afgeschreven", "Kwijt"]

def ensure_assets_migrated():
    """One-time migration from employee-embedded assets to the central inventory."""
    settings = load_settings()
    if settings.get("assets_v2_migrated"):
        return

    central = load_module("assets")
    known_ids = {a.get("id") for a in central}
    employees_list = load_employees()
    changed = False

    for employee in employees_list:
        normalize_employee(employee)
        employee_name = f"{employee.get('first_name','')} {employee.get('last_name','')}".strip()
        legacy_assets = list(employee.get("assets", []))
        for legacy in legacy_assets:
            if legacy.get("id") in known_ids:
                continue
            asset = dict(legacy)
            asset.setdefault("id", str(uuid.uuid4()))
            asset.setdefault("category", "Overig")
            asset.setdefault("name", "Bedrijfsmiddel")
            asset.setdefault("assignment_history", [])
            asset.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
            asset["updated_at"] = datetime.now().isoformat(timespec="seconds")

            if legacy.get("returned_at"):
                asset["status"] = "Vrij"
                asset["assigned_employee_id"] = ""
                asset["assignment_history"].append({
                    "employee_id": employee["id"],
                    "employee_name": employee_name,
                    "assigned_at": legacy.get("issued_at", ""),
                    "returned_at": legacy.get("returned_at", ""),
                    "note": "Gemigreerd uit personeelsdossier"
                })
            else:
                asset["status"] = "Uitgegeven"
                asset["assigned_employee_id"] = employee["id"]
                asset["assignment_history"].append({
                    "employee_id": employee["id"],
                    "employee_name": employee_name,
                    "assigned_at": legacy.get("issued_at", ""),
                    "returned_at": "",
                    "note": "Gemigreerd uit personeelsdossier"
                })
            central.append(asset)
            known_ids.add(asset["id"])

        if legacy_assets:
            employee["assets"] = []
            changed = True

    if changed:
        save_employees(employees_list)
    save_module("assets", central)
    settings["assets_v2_migrated"] = True
    save_settings(settings)
    if changed:
        log_action("assets_migrated", detail=f"{len(central)} middelen in centrale inventaris")


def load_assets():
    ensure_assets_migrated()
    data = load_module("assets")
    for asset in data:
        asset.setdefault("status", "Vrij")
        asset.setdefault("assigned_employee_id", "")
        asset.setdefault("assignment_history", [])
        asset.setdefault("category", "Overig")
        asset.setdefault("name", "Bedrijfsmiddel")
        asset.setdefault("brand", "")
        asset.setdefault("model", "")
        asset.setdefault("serial_number", "")
        asset.setdefault("asset_number", "")
        asset.setdefault("imei", "")
        asset.setdefault("phone_number", "")
        asset.setdefault("provider", "")
        asset.setdefault("pin", "")
        asset.setdefault("puk", "")
        asset.setdefault("os", "")
        asset.setdefault("purchase_date", "")
        asset.setdefault("warranty_until", "")
        asset.setdefault("return_due", "")
        asset.setdefault("accessories", "")
        asset.setdefault("notes", "")
    return data


def save_assets(data):
    save_module("assets", data)


def ensure_registrations_migrated():
    """Migrate legacy registrations embedded in employee records to registrations.enc."""
    settings = load_settings()
    if settings.get("registrations_v2_migrated"):
        return

    central = load_module("registrations")
    known_ids = {r.get("id") for r in central}
    employees_list = load_employees()
    changed = False

    for employee in employees_list:
        normalize_employee(employee)
        legacy_regs = list(employee.get("registrations", []))
        for legacy in legacy_regs:
            if legacy.get("id") in known_ids:
                continue
            reg = dict(legacy)
            reg.setdefault("id", str(uuid.uuid4()))
            reg["employee_id"] = employee["id"]
            reg.setdefault("warning_days", "")
            reg.setdefault("status", "Actief")
            reg.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
            reg["updated_at"] = datetime.now().isoformat(timespec="seconds")
            central.append(reg)
            known_ids.add(reg["id"])

        if legacy_regs:
            employee["registrations"] = []
            changed = True

    if changed:
        save_employees(employees_list)
    save_module("registrations", central)
    settings["registrations_v2_migrated"] = True
    save_settings(settings)
    if changed:
        log_action("registrations_migrated", detail=f"{len(central)} registraties in centraal register")


def load_registrations():
    ensure_registrations_migrated()
    data = load_module("registrations")
    for reg in data:
        reg.setdefault("employee_id", "")
        reg.setdefault("type", "Registratie")
        reg.setdefault("number", "")
        reg.setdefault("institution", "")
        reg.setdefault("obtained_at", "")
        reg.setdefault("expires_at", "")
        reg.setdefault("warning_days", "")
        reg.setdefault("status", "Actief")
        reg.setdefault("notes", "")
    return data


def save_registrations(data):
    save_module("registrations", data)


def registration_warning_days(reg):
    raw = str(reg.get("warning_days") or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    try:
        return max(0, int(load_settings().get("default_registration_warning_days", 90)))
    except (TypeError, ValueError):
        return 90


def asset_employee_name(asset, employees_map):
    employee = employees_map.get(asset.get("assigned_employee_id"))
    if not employee:
        return ""
    return f"{employee.get('first_name','')} {employee.get('last_name','')}".strip()




@app.route("/assets")
@admin_required
@module_required("assets")
def assets_overview():
    assets = load_assets()
    employees = [normalize_employee(e) for e in load_employees()]
    employees_map = {e["id"]: e for e in employees}

    q = request.args.get("q", "").strip().lower()
    status_filter = request.args.get("status", "").strip()
    category_filter = request.args.get("category", "").strip()

    rows = []
    for asset in assets:
        row = dict(asset)
        row["employee_name"] = asset_employee_name(asset, employees_map)
        haystack = " ".join([
            asset.get("category",""), asset.get("name",""), asset.get("brand",""),
            asset.get("model",""), asset.get("serial_number",""), asset.get("asset_number",""),
            asset.get("phone_number",""), row["employee_name"], asset.get("status","")
        ]).lower()
        if q and q not in haystack:
            continue
        if status_filter and asset.get("status") != status_filter:
            continue
        if category_filter and asset.get("category") != category_filter:
            continue
        rows.append(row)

    rows.sort(key=lambda a:(a.get("status",""), a.get("category","").lower(), a.get("name","").lower()))
    categories = sorted({a.get("category","Overig") for a in assets if a.get("category")})
    stats = {
        "total": len(assets),
        "issued": sum(1 for a in assets if a.get("status") == "Uitgegeven"),
        "free": sum(1 for a in assets if a.get("status") == "Vrij"),
        "attention": sum(1 for a in assets if a.get("status") in {"Reparatie","Kwijt"}),
    }
    return render_template("assets.html", assets=rows, employees=employees, categories=categories,
                           statuses=ASSET_STATUSES, stats=stats, q=q,
                           status_filter=status_filter, category_filter=category_filter,
                           settings=load_settings())


@app.route("/assets/add", methods=["POST"])
@admin_required
@module_required("assets")
@synchronized
def asset_central_add():
    assets = load_assets()
    name = request.form.get("name", "").strip()
    if not name:
        flash("Geef het bedrijfsmiddel een naam.", "error")
        return redirect(url_for("assets_overview"))

    assigned_employee_id = request.form.get("assigned_employee_id", "").strip()
    issued_at = request.form.get("issued_at", "").strip()
    employees_map = {e["id"]: normalize_employee(e) for e in load_employees()}
    employee = employees_map.get(assigned_employee_id)

    asset = {
        "id": str(uuid.uuid4()),
        "category": _resolve_choice_field(request.form, "asset_type") or "Overig",
        "name": name,
        "brand": request.form.get("brand", "").strip(),
        "model": request.form.get("model", "").strip(),
        "serial_number": request.form.get("serial_number", "").strip(),
        "asset_number": request.form.get("asset_number", "").strip(),
        "imei": request.form.get("imei", "").strip(),
        "phone_number": request.form.get("phone_number", "").strip(),
        "provider": _resolve_choice_field(request.form, "provider"),
        "pin": request.form.get("pin", "").strip(),
        "puk": request.form.get("puk", "").strip(),
        "os": _resolve_choice_field(request.form, "os"),
        "purchase_date": request.form.get("purchase_date", "").strip(),
        "warranty_until": request.form.get("warranty_until", "").strip(),
        "return_due": request.form.get("return_due", "").strip(),
        "accessories": request.form.get("accessories", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "status": "Uitgegeven" if employee else "Vrij",
        "assigned_employee_id": assigned_employee_id if employee else "",
        "assignment_history": [],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if employee:
        asset["assignment_history"].append({
            "employee_id": employee["id"],
            "employee_name": f"{employee.get('first_name','')} {employee.get('last_name','')}".strip(),
            "assigned_at": issued_at or date.today().isoformat(),
            "returned_at": "",
            "note": "Eerste uitgifte"
        })
    assets.append(asset)
    save_assets(assets)
    log_action("asset_created", detail=name, target=asset["id"])
    flash("Bedrijfsmiddel toegevoegd.", "success")
    return redirect(url_for("asset_detail", asset_id=asset["id"]))


@app.route("/assets/<asset_id>")
@admin_required
@module_required("assets")
def asset_detail(asset_id):
    asset = next((a for a in load_assets() if a.get("id") == asset_id), None)
    if not asset:
        abort(404)
    employees = sorted(
        [normalize_employee(e) for e in load_employees()],
        key=lambda e:(e.get("last_name","").lower(), e.get("first_name","").lower())
    )
    employees_map = {e["id"]: e for e in employees}
    asset = dict(asset)
    asset["employee_name"] = asset_employee_name(asset, employees_map)
    return render_template("asset_detail.html", asset=asset, employees=employees, statuses=ASSET_STATUSES)


@app.route("/assets/<asset_id>/edit", methods=["POST"])
@admin_required
@module_required("assets")
@synchronized
def asset_edit(asset_id):
    assets = load_assets()
    asset = next((a for a in assets if a.get("id") == asset_id), None)
    if not asset:
        abort(404)
    for field in [
        "category","name","brand","model","serial_number","asset_number","imei",
        "phone_number","provider","os","purchase_date","warranty_until",
        "return_due","accessories","notes"
    ]:
        if field in request.form:
            asset[field] = request.form.get(field, "").strip()

    pin_new = request.form.get("pin_new", "")
    puk_new = request.form.get("puk_new", "")
    if pin_new:
        asset["pin"] = pin_new.strip()
    if puk_new:
        asset["puk"] = puk_new.strip()

    requested_status = request.form.get("status", asset.get("status","Vrij")).strip()
    if requested_status in ASSET_STATUSES and requested_status != "Uitgegeven":
        # Status Uitgegeven is controlled by assignment; other statuses may be set manually.
        if not asset.get("assigned_employee_id"):
            asset["status"] = requested_status
    asset["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_assets(assets)
    log_action("asset_updated", detail=asset.get("name",""), target=asset_id)
    flash("Bedrijfsmiddel bijgewerkt.", "success")
    return redirect(url_for("asset_detail", asset_id=asset_id))


@app.route("/assets/<asset_id>/assign", methods=["POST"])
@admin_required
@module_required("assets")
@synchronized
def asset_assign(asset_id):
    assets = load_assets()
    asset = next((a for a in assets if a.get("id") == asset_id), None)
    if not asset:
        abort(404)
    if asset.get("assigned_employee_id"):
        flash("Dit middel is al uitgegeven. Neem het eerst retour.", "error")
        return redirect(url_for("asset_detail", asset_id=asset_id))

    employee_id = request.form.get("employee_id", "").strip()
    employee = find_employee(employee_id)
    if not employee:
        flash("Kies een geldige medewerker.", "error")
        return redirect(url_for("asset_detail", asset_id=asset_id))
    employee = normalize_employee(employee)
    employee_name = f"{employee.get('first_name','')} {employee.get('last_name','')}".strip()

    asset["assigned_employee_id"] = employee_id
    asset["status"] = "Uitgegeven"
    asset["return_due"] = request.form.get("return_due", "").strip()
    asset.setdefault("assignment_history", []).append({
        "employee_id": employee_id,
        "employee_name": employee_name,
        "assigned_at": request.form.get("issued_at", "").strip() or date.today().isoformat(),
        "returned_at": "",
        "note": request.form.get("assignment_note", "").strip()
    })
    asset["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_assets(assets)
    log_action("asset_assigned", detail=f"{asset.get('name','')} → {employee_name}", target=asset_id)
    flash(f"Bedrijfsmiddel uitgegeven aan {employee_name}.", "success")
    return redirect(url_for("asset_detail", asset_id=asset_id))


@app.route("/assets/<asset_id>/return", methods=["POST"])
@admin_required
@module_required("assets")
@synchronized
def asset_return(asset_id):
    assets = load_assets()
    asset = next((a for a in assets if a.get("id") == asset_id), None)
    if not asset:
        abort(404)
    employee_id = asset.get("assigned_employee_id")
    if not employee_id:
        flash("Dit middel is niet uitgegeven.", "error")
        return redirect(url_for("asset_detail", asset_id=asset_id))

    employee = find_employee(employee_id)
    employee_name = ""
    if employee:
        employee = normalize_employee(employee)
        employee_name = f"{employee.get('first_name','')} {employee.get('last_name','')}".strip()

    history = asset.setdefault("assignment_history", [])
    for item in reversed(history):
        if item.get("employee_id") == employee_id and not item.get("returned_at"):
            item["returned_at"] = request.form.get("returned_at", "").strip() or date.today().isoformat()
            note = request.form.get("return_note", "").strip()
            if note:
                item["return_note"] = note
            break

    asset["assigned_employee_id"] = ""
    asset["return_due"] = ""
    asset["status"] = request.form.get("next_status", "Vrij")
    if asset["status"] not in {"Vrij","Reparatie","Afgeschreven"}:
        asset["status"] = "Vrij"
    asset["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_assets(assets)
    log_action("asset_returned", detail=f"{asset.get('name','')} ← {employee_name}", target=asset_id)
    flash("Bedrijfsmiddel retour genomen.", "success")
    return redirect(url_for("asset_detail", asset_id=asset_id))


@app.route("/assets/<asset_id>/reveal/<field>", methods=["POST"])
@admin_required
@module_required("assets")
def asset_central_secret_reveal(asset_id, field):
    if field not in {"pin", "puk"}:
        abort(404)
    asset = next((a for a in load_assets() if a.get("id") == asset_id), None)
    if not asset:
        abort(404)
    log_action("asset_secret_revealed", detail=field, target=asset_id)
    return jsonify({"ok": True, "value": asset.get(field, "")})


@app.route("/assets/<asset_id>/delete", methods=["POST"])
@admin_required
@module_required("assets")
@synchronized
def asset_central_delete(asset_id):
    assets = load_assets()
    asset = next((a for a in assets if a.get("id") == asset_id), None)
    if not asset:
        abort(404)
    if asset.get("assigned_employee_id"):
        flash("Een uitgegeven middel kan niet worden verwijderd. Neem het eerst retour.", "error")
        return redirect(url_for("asset_detail", asset_id=asset_id))
    assets = [a for a in assets if a.get("id") != asset_id]
    save_assets(assets)
    log_action("asset_deleted", detail=asset.get("name",""), target=asset_id)
    flash("Bedrijfsmiddel verwijderd.", "success")
    return redirect(url_for("assets_overview"))



@app.route("/registrations")
@admin_required
@module_required("registrations")
def registrations_overview():
    regs = load_registrations()
    employees = [normalize_employee(e) for e in load_employees()]
    employees_map = {e["id"]: e for e in employees}
    q = request.args.get("q", "").strip().lower()
    status_filter = request.args.get("status", "").strip()
    today = date.today()

    rows = []
    for reg in regs:
        row = dict(reg)
        emp = employees_map.get(reg.get("employee_id"))
        row["employee_name"] = f"{emp.get('first_name','')} {emp.get('last_name','')}".strip() if emp else ""
        row["days_left"] = None
        row["warning_days_effective"] = registration_warning_days(reg)
        if reg.get("expires_at"):
            try:
                row["days_left"] = (date.fromisoformat(reg["expires_at"]) - today).days
            except ValueError:
                pass

        if row["days_left"] is not None and row["days_left"] < 0:
            row["display_status"] = "Verlopen"
        elif row["days_left"] is not None and row["days_left"] <= row["warning_days_effective"]:
            row["display_status"] = "Loopt af"
        else:
            row["display_status"] = reg.get("status","Actief")

        haystack = " ".join([
            reg.get("type",""), reg.get("number",""), reg.get("institution",""),
            row["employee_name"], reg.get("notes","")
        ]).lower()
        if q and q not in haystack:
            continue
        if status_filter and row["display_status"] != status_filter:
            continue
        rows.append(row)

    rows.sort(key=lambda r:(r.get("expires_at") or "9999-12-31", r.get("employee_name","").lower()))
    stats = {
        "total": len(regs),
        "expiring": sum(1 for r in rows if r.get("display_status") == "Loopt af"),
        "expired": sum(1 for r in rows if r.get("display_status") == "Verlopen"),
        "active": sum(1 for r in rows if r.get("display_status") == "Actief"),
    }
    return render_template("registrations.html", registrations=rows, employees=employees,
                           stats=stats, q=q, status_filter=status_filter,
                           settings=load_settings())


@app.route("/registrations/add", methods=["POST"])
@admin_required
@module_required("registrations")
@synchronized
def registration_central_add():
    employee_id = request.form.get("employee_id", "").strip()
    employee = find_employee(employee_id)
    reg_type = _resolve_choice_field(request.form, "registration_type")
    if not employee_id or not employee:
        flash("Kies een medewerker.", "error")
        return redirect(url_for("registrations_overview"))
    if not reg_type:
        flash("Vul een type registratie in.", "error")
        return redirect(url_for("registrations_overview"))

    regs = load_registrations()
    reg = {
        "id": str(uuid.uuid4()),
        "employee_id": employee_id,
        "type": reg_type,
        "number": request.form.get("number", "").strip(),
        "institution": request.form.get("institution", "").strip(),
        "obtained_at": request.form.get("obtained_at", "").strip(),
        "expires_at": request.form.get("expires_at", "").strip(),
        "warning_days": request.form.get("warning_days", "").strip(),
        "status": request.form.get("status", "Actief").strip() or "Actief",
        "notes": request.form.get("notes", "").strip(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    regs.append(reg)
    save_registrations(regs)
    log_action("registration_created", detail=reg_type, target=reg["id"])
    flash("Registratie toegevoegd.", "success")
    return redirect(url_for("registrations_overview"))


@app.route("/registrations/<registration_id>/edit", methods=["POST"])
@admin_required
@module_required("registrations")
@synchronized
def registration_central_edit(registration_id):
    regs = load_registrations()
    reg = next((r for r in regs if r.get("id") == registration_id), None)
    if not reg:
        abort(404)
    reg["number"] = request.form.get("number", "").strip()
    reg["institution"] = request.form.get("institution", "").strip()
    reg["obtained_at"] = request.form.get("obtained_at", "").strip()
    reg["expires_at"] = request.form.get("expires_at", "").strip()
    reg["warning_days"] = request.form.get("warning_days", "").strip()
    reg["status"] = request.form.get("status", "Actief").strip() or "Actief"
    reg["notes"] = request.form.get("notes", "").strip()
    reg["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_registrations(regs)
    log_action("registration_updated", detail=reg.get("type",""), target=registration_id)
    flash("Registratie bijgewerkt.", "success")
    return redirect(url_for("registrations_overview"))


@app.route("/registrations/<registration_id>/delete", methods=["POST"])
@admin_required
@module_required("registrations")
@synchronized
def registration_central_delete(registration_id):
    regs = load_registrations()
    reg = next((r for r in regs if r.get("id") == registration_id), None)
    regs = [r for r in regs if r.get("id") != registration_id]
    save_registrations(regs)
    if reg:
        log_action("registration_deleted", detail=reg.get("type",""), target=registration_id)
    flash("Registratie verwijderd.", "success")
    return redirect(url_for("registrations_overview"))



@app.route("/credentials")
@admin_required
@module_required("credentials")
def credentials():
    records = load_credentials()
    employees = sorted(
        [normalize_employee(e) for e in load_employees()],
        key=lambda e:(e.get("last_name","").lower(), e.get("first_name","").lower())
    )
    employees_map = {e["id"]: e for e in employees}
    q = request.args.get("q", "").strip().lower()
    scope_filter = request.args.get("scope", "").strip()
    type_filter = request.args.get("secret_type", "").strip()

    rows = []
    for record in records:
        row = dict(record)
        row["employee_names"] = credential_employee_names(record, employees_map)
        if record.get("scope") == "Praktijkbreed":
            row["owner_label"] = "Praktijk / algemeen"
        elif row["employee_names"]:
            row["owner_label"] = ", ".join(row["employee_names"])
        else:
            row["owner_label"] = "Niet gekoppeld"

        haystack = " ".join([
            record.get("service",""), record.get("username",""), record.get("scope",""),
            record.get("secret_type",""), record.get("category",""), row["owner_label"],
            record.get("notes",""), record.get("url","")
        ]).lower()
        if q and q not in haystack:
            continue
        if scope_filter and record.get("scope") != scope_filter:
            continue
        if type_filter and record.get("secret_type") != type_filter:
            continue
        rows.append(row)

    rows.sort(key=lambda r:(r.get("service","").lower(), r.get("username","").lower()))
    stats = {
        "total": len(records),
        "personal": sum(1 for r in records if r.get("scope") == "Persoonlijk"),
        "shared": sum(1 for r in records if r.get("scope") == "Gedeeld"),
        "practice": sum(1 for r in records if r.get("scope") == "Praktijkbreed"),
    }
    return render_template(
        "credentials.html",
        records=rows,
        employees=employees,
        q=q,
        scope_filter=scope_filter,
        type_filter=type_filter,
        scopes=CREDENTIAL_SCOPES,
        secret_types=load_settings().get("choice_lists", {}).get("credential_secret_type", CREDENTIAL_SECRET_TYPES),
        stats=stats,
        settings=load_settings()
    )


@app.route("/credentials/add", methods=["POST"])
@admin_required
@module_required("credentials")
@synchronized
def credential_add():
    records = load_credentials()
    service = request.form.get("service", "").strip()
    scope = request.form.get("scope", "Persoonlijk").strip()
    linked_ids = request.form.getlist("linked_employee_ids")

    if not service:
        flash("Vul een dienst of systeemnaam in.", "error")
        return redirect(url_for("credentials"))
    if scope not in CREDENTIAL_SCOPES:
        scope = "Persoonlijk"
    if scope == "Persoonlijk" and len(linked_ids) != 1:
        flash("Koppel een persoonlijk account aan precies één medewerker.", "error")
        return redirect(url_for("credentials"))
    if scope == "Gedeeld" and not linked_ids:
        flash("Koppel een gedeeld account aan minimaal één medewerker.", "error")
        return redirect(url_for("credentials"))
    if scope == "Praktijkbreed":
        linked_ids = []

    record = {
        "id": str(uuid.uuid4()),
        "service": service,
        "scope": scope,
        "employee_id": linked_ids[0] if scope == "Persoonlijk" and linked_ids else "",
        "linked_employee_ids": linked_ids,
        "username": request.form.get("username", "").strip(),
        "secret_type": request.form.get("secret_type", "Wachtwoord").strip(),
        "secret": request.form.get("secret", ""),
        "url": request.form.get("url", "").strip(),
        "category": request.form.get("category", "Account").strip() or "Account",
        "status": request.form.get("status", "Actief").strip() or "Actief",
        "notes": request.form.get("notes", "").strip(),
        "last_verified_at": request.form.get("last_verified_at", "").strip(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    records.append(record)
    save_credentials(records)
    log_action("credential_created", detail=service, target=record["id"])
    flash("Account/code toegevoegd.", "success")
    return redirect(url_for("credentials"))


@app.route("/credentials/<credential_id>/edit", methods=["POST"])
@admin_required
@module_required("credentials")
@synchronized
def credential_edit(credential_id):
    records = load_credentials()
    record = next((r for r in records if r.get("id") == credential_id), None)
    if not record:
        abort(404)

    scope = request.form.get("scope", record.get("scope","Persoonlijk")).strip()
    linked_ids = request.form.getlist("linked_employee_ids")
    if scope not in CREDENTIAL_SCOPES:
        scope = record.get("scope","Persoonlijk")
    if scope == "Persoonlijk" and len(linked_ids) != 1:
        flash("Koppel een persoonlijk account aan precies één medewerker.", "error")
        return redirect(url_for("credentials"))
    if scope == "Gedeeld" and not linked_ids:
        flash("Koppel een gedeeld account aan minimaal één medewerker.", "error")
        return redirect(url_for("credentials"))
    if scope == "Praktijkbreed":
        linked_ids = []

    record["service"] = request.form.get("service", record.get("service","")).strip()
    record["scope"] = scope
    record["employee_id"] = linked_ids[0] if scope == "Persoonlijk" and linked_ids else ""
    record["linked_employee_ids"] = linked_ids
    record["username"] = request.form.get("username", "").strip()
    record["secret_type"] = request.form.get("secret_type", "Wachtwoord").strip()
    record["url"] = request.form.get("url", "").strip()
    record["category"] = request.form.get("category", "Account").strip() or "Account"
    record["status"] = request.form.get("status", "Actief").strip() or "Actief"
    record["notes"] = request.form.get("notes", "").strip()
    record["last_verified_at"] = request.form.get("last_verified_at", "").strip()
    new_secret = request.form.get("new_secret", "")
    if new_secret:
        record["secret"] = new_secret
    record["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_credentials(records)
    log_action("credential_updated", detail=record.get("service",""), target=credential_id)
    flash("Account/code bijgewerkt.", "success")
    return redirect(url_for("credentials"))


@app.route("/credentials/<credential_id>/reveal", methods=["POST"])
@admin_required
@module_required("credentials")
def credential_reveal(credential_id):
    record = next((r for r in load_credentials() if r.get("id") == credential_id), None)
    if not record:
        return jsonify({"ok":False,"error":"Niet gevonden"}), 404
    log_action("credential_revealed", detail=record.get("service", ""), target=credential_id)
    return jsonify({"ok":True,"secret":record.get("secret", "")})


@app.route("/credentials/<credential_id>/copy", methods=["POST"])
@admin_required
@module_required("credentials")
def credential_copy(credential_id):
    record = next((r for r in load_credentials() if r.get("id") == credential_id), None)
    if not record:
        return jsonify({"ok":False,"error":"Niet gevonden"}), 404
    log_action("credential_copied", detail=record.get("service", ""), target=credential_id)
    return jsonify({"ok":True,"secret":record.get("secret", "")})


@app.route("/credentials/<credential_id>/delete", methods=["POST"])
@admin_required
@module_required("credentials")
@synchronized
def credential_delete(credential_id):
    records = load_credentials()
    record = next((r for r in records if r.get("id") == credential_id), None)
    records = [r for r in records if r.get("id") != credential_id]
    save_credentials(records)
    if record:
        log_action("credential_deleted", detail=record.get("service", ""), target=credential_id)
    flash("Account/code verwijderd.", "success")
    return redirect(url_for("credentials"))


@app.route("/documents")
@admin_required
@module_required("documents")
def documents():
    docs = []
    for e in [normalize_employee(x) for x in load_employees()]:
        for d in e.get("documents", []):
            docs.append({**d, "employee_id": e["id"],
                         "employee_name": f"{e.get('first_name','')} {e.get('last_name','')}".strip()})
    docs.sort(key=lambda x: x.get("uploaded_at",""), reverse=True)
    return render_template("documents.html", documents=docs)

@app.route("/leave", methods=["GET","POST"])
@admin_required
@synchronized
@module_required("leave")
def leave():
    records = load_module("leave")
    employees = [normalize_employee(x) for x in load_employees()]
    if request.method == "POST":
        eid = request.form.get("employee_id","")
        emp = find_employee(eid, employees)
        if not emp:
            flash("Kies een medewerker.", "error")
        else:
            records.append({
                "id": str(uuid.uuid4()), "employee_id": eid,
                "employee_name": f"{emp.get('first_name','')} {emp.get('last_name','')}".strip(),
                "type": request.form.get("type","Vakantie"),
                "start_date": request.form.get("start_date",""),
                "end_date": request.form.get("end_date",""),
                "hours": request.form.get("hours",""),
                "status": request.form.get("status","Gepland"),
                "notes": request.form.get("notes",""),
                "created_at": datetime.now().isoformat(timespec="seconds")
            })
            save_module("leave", records)
            flash("Verlofregistratie toegevoegd.", "success")
            return redirect(url_for("leave"))
    records.sort(key=lambda x: x.get("start_date",""), reverse=True)
    return render_template("leave.html", records=records, employees=employees)

@app.route("/leave/<rid>/delete", methods=["POST"])
@admin_required
@synchronized
@module_required("leave")
def leave_delete(rid):
    save_module("leave", [r for r in load_module("leave") if r.get("id") != rid])
    flash("Verlofregistratie verwijderd.", "success")
    return redirect(url_for("leave"))

@app.route("/tasks", methods=["GET","POST"])
@admin_required
@synchronized
@module_required("signals")
def tasks():
    records = load_module("tasks")
    employees = [normalize_employee(x) for x in load_employees()]
    if request.method == "POST":
        eid = request.form.get("employee_id","")
        emp = find_employee(eid, employees) if eid else None
        records.append({
            "id": str(uuid.uuid4()), "title": request.form.get("title","").strip(),
            "employee_id": eid,
            "employee_name": f"{emp.get('first_name','')} {emp.get('last_name','')}".strip() if emp else "",
            "due_date": request.form.get("due_date",""),
            "priority": request.form.get("priority","Normaal"),
            "status": "Open", "notes": request.form.get("notes",""),
            "created_at": datetime.now().isoformat(timespec="seconds")
        })
        save_module("tasks", records)
        flash("Taak toegevoegd.", "success")
        return redirect(url_for("tasks"))
    records.sort(key=lambda x: (x.get("status")=="Afgerond", x.get("due_date") or "9999-12-31"))
    return render_template("tasks.html", records=records, employees=employees)

@app.route("/tasks/<tid>/toggle", methods=["POST"])
@admin_required
@synchronized
@module_required("signals")
def task_toggle(tid):
    records = load_module("tasks")
    task = next((r for r in records if r.get("id")==tid), None)
    if task:
        task["status"] = "Open" if task.get("status")=="Afgerond" else "Afgerond"
        save_module("tasks", records)
    return redirect(url_for("tasks"))

@app.route("/tasks/<tid>/delete", methods=["POST"])
@admin_required
@synchronized
@module_required("signals")
def task_delete(tid):
    save_module("tasks", [r for r in load_module("tasks") if r.get("id") != tid])
    flash("Taak verwijderd.", "success")
    return redirect(url_for("tasks"))



@app.route("/employees/<employee_id>/onboarding")
@admin_required
@module_required("signals")
def onboarding_detail(employee_id):
    employee = find_employee(employee_id)
    if not employee:
        abort(404)
    employee = normalize_employee(employee)
    workflows = load_module("onboarding")
    workflow = next((w for w in workflows if w.get("employee_id") == employee_id), None)
    return render_template("onboarding.html", employee=employee, workflow=workflow)


@app.route("/employees/<employee_id>/onboarding/start", methods=["POST"])
@admin_required
@module_required("signals")
@synchronized
def onboarding_start(employee_id):
    employee = find_employee(employee_id)
    if not employee:
        abort(404)
    employee = normalize_employee(employee)
    workflows = load_module("onboarding")
    if next((w for w in workflows if w.get("employee_id") == employee_id), None):
        flash("Voor deze medewerker bestaat al een indienstworkflow.", "info")
        return redirect(url_for("onboarding_detail", employee_id=employee_id))

    settings = load_settings()
    items = []
    for title in settings.get("workflow_templates", {}).get("onboarding", []):
        items.append({"id": str(uuid.uuid4()), "kind": "standard", "title": title, "reference_id": "", "done": False})

    # Slimme voorstellen op basis van reeds gekoppelde gegevens.
    if module_enabled("assets"):
        for asset in load_assets():
            if asset.get("assigned_employee_id") == employee_id:
                items.append({"id": str(uuid.uuid4()), "kind": "asset", "title": f"Controleer uitgegeven bedrijfsmiddel: {asset.get('name','Bedrijfsmiddel')}", "reference_id": asset.get("id",""), "done": False})
    if module_enabled("credentials"):
        for cred in load_credentials():
            if employee_id in cred.get("linked_employee_ids", []):
                items.append({"id": str(uuid.uuid4()), "kind": "credential", "title": f"Controleer toegang: {cred.get('service','Account')}", "reference_id": cred.get("id",""), "done": False})
    if module_enabled("registrations"):
        for reg in load_registrations():
            if reg.get("employee_id") == employee_id:
                items.append({"id": str(uuid.uuid4()), "kind": "registration", "title": f"Controleer registratie: {reg.get('type','Registratie')}", "reference_id": reg.get("id",""), "done": False})

    workflow = {
        "id": str(uuid.uuid4()),
        "employee_id": employee_id,
        "employee_name": f"{employee.get('first_name','')} {employee.get('last_name','')}".strip(),
        "start_date": request.form.get("start_date","").strip(),
        "status": "Open",
        "items": items,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "created_by": session.get("user_name",""),
    }
    workflows.append(workflow)
    save_module("onboarding", workflows)
    log_action("onboarding_started", detail=workflow["employee_name"], target=employee_id)
    flash("Indienstworkflow gestart.", "success")
    return redirect(url_for("onboarding_detail", employee_id=employee_id))


@app.route("/employees/<employee_id>/onboarding/<item_id>/toggle", methods=["POST"])
@admin_required
@module_required("signals")
@synchronized
def onboarding_toggle(employee_id, item_id):
    workflows = load_module("onboarding")
    workflow = next((w for w in workflows if w.get("employee_id") == employee_id), None)
    if not workflow:
        abort(404)
    item = next((i for i in workflow.get("items", []) if i.get("id") == item_id), None)
    if not item:
        abort(404)
    item["done"] = not item.get("done", False)
    if all(i.get("done") for i in workflow.get("items", [])):
        workflow["status"] = "Afgerond"
        workflow["completed_at"] = datetime.now().isoformat(timespec="seconds")
    else:
        workflow["status"] = "Open"
        workflow.pop("completed_at", None)
    save_module("onboarding", workflows)
    log_action("onboarding_item_toggled", detail=item.get("title",""), target=employee_id)
    return redirect(url_for("onboarding_detail", employee_id=employee_id))


@app.route("/employees/<employee_id>/onboarding/reset", methods=["POST"])
@admin_required
@module_required("signals")
@synchronized
def onboarding_reset(employee_id):
    workflows = [w for w in load_module("onboarding") if w.get("employee_id") != employee_id]
    save_module("onboarding", workflows)
    log_action("onboarding_removed", target=employee_id)
    flash("Indienstworkflow verwijderd.", "success")
    return redirect(url_for("employee_detail", employee_id=employee_id))


@app.route("/employees/<employee_id>/offboarding")
@admin_required
@module_required("signals")
def offboarding_detail(employee_id):
    employee = find_employee(employee_id)
    if not employee:
        abort(404)
    employee = normalize_employee(employee)
    workflows = load_module("offboarding")
    workflow = next((w for w in workflows if w.get("employee_id") == employee_id), None)

    assets = [a for a in load_assets() if a.get("assigned_employee_id") == employee_id] if module_enabled("assets") else []
    credentials = [c for c in load_credentials() if employee_id in c.get("linked_employee_ids", [])] if module_enabled("credentials") else []
    registrations = [r for r in load_registrations() if r.get("employee_id") == employee_id] if module_enabled("registrations") else []

    return render_template(
        "offboarding.html",
        employee=employee,
        workflow=workflow,
        assets=assets,
        credentials=credentials,
        registrations=registrations,
    )


@app.route("/employees/<employee_id>/offboarding/start", methods=["POST"])
@admin_required
@module_required("signals")
@synchronized
def offboarding_start(employee_id):
    employee = find_employee(employee_id)
    if not employee:
        abort(404)
    employee = normalize_employee(employee)
    workflows = load_module("offboarding")
    existing = next((w for w in workflows if w.get("employee_id") == employee_id), None)
    if existing:
        flash("Voor deze medewerker bestaat al een uitdienstworkflow.", "info")
        return redirect(url_for("offboarding_detail", employee_id=employee_id))

    items = []
    if module_enabled("assets"):
        for asset in load_assets():
            if asset.get("assigned_employee_id") == employee_id:
                items.append({
                    "id": str(uuid.uuid4()),
                    "kind": "asset",
                    "title": f"Bedrijfsmiddel retour: {asset.get('name','Bedrijfsmiddel')}",
                    "reference_id": asset.get("id",""),
                    "done": False,
                })

    if module_enabled("credentials"):
        for cred in load_credentials():
            if employee_id in cred.get("linked_employee_ids", []):
                action = "Persoonlijk account afsluiten" if cred.get("scope") == "Persoonlijk" else "Toegang tot gedeeld account controleren"
                items.append({
                    "id": str(uuid.uuid4()),
                    "kind": "credential",
                    "title": f"{action}: {cred.get('service','Account')}",
                    "reference_id": cred.get("id",""),
                    "done": False,
                })

    if module_enabled("registrations"):
        for reg in load_registrations():
            if reg.get("employee_id") == employee_id:
                items.append({
                    "id": str(uuid.uuid4()),
                    "kind": "registration",
                    "title": f"Registratie controleren/archiveren: {reg.get('type','Registratie')}",
                    "reference_id": reg.get("id",""),
                    "done": False,
                })

    standard_titles = load_settings().get("workflow_templates", {}).get("offboarding", [])
    for title in standard_titles:
        items.append({"id": str(uuid.uuid4()), "kind": "standard", "title": title, "reference_id": "", "done": False})

    workflow = {
        "id": str(uuid.uuid4()),
        "employee_id": employee_id,
        "employee_name": f"{employee.get('first_name','')} {employee.get('last_name','')}".strip(),
        "last_working_day": request.form.get("last_working_day","").strip(),
        "status": "Open",
        "items": items,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "created_by": session.get("user_name",""),
    }
    workflows.append(workflow)
    save_module("offboarding", workflows)
    log_action("offboarding_started", detail=workflow["employee_name"], target=employee_id)
    flash("Uitdienstworkflow gestart.", "success")
    return redirect(url_for("offboarding_detail", employee_id=employee_id))


@app.route("/employees/<employee_id>/offboarding/<item_id>/toggle", methods=["POST"])
@admin_required
@module_required("signals")
@synchronized
def offboarding_toggle(employee_id, item_id):
    workflows = load_module("offboarding")
    workflow = next((w for w in workflows if w.get("employee_id") == employee_id), None)
    if not workflow:
        abort(404)
    item = next((i for i in workflow.get("items",[]) if i.get("id") == item_id), None)
    if not item:
        abort(404)
    item["done"] = not item.get("done", False)
    if all(i.get("done") for i in workflow.get("items",[])):
        workflow["status"] = "Afgerond"
        workflow["completed_at"] = datetime.now().isoformat(timespec="seconds")
    else:
        workflow["status"] = "Open"
        workflow.pop("completed_at", None)
    save_module("offboarding", workflows)
    log_action("offboarding_item_toggled", detail=item.get("title",""), target=employee_id)
    return redirect(url_for("offboarding_detail", employee_id=employee_id))


@app.route("/employees/<employee_id>/offboarding/reset", methods=["POST"])
@admin_required
@module_required("signals")
@synchronized
def offboarding_reset(employee_id):
    workflows = [w for w in load_module("offboarding") if w.get("employee_id") != employee_id]
    save_module("offboarding", workflows)
    log_action("offboarding_removed", target=employee_id)
    flash("Uitdienstworkflow verwijderd.", "success")
    return redirect(url_for("employee_detail", employee_id=employee_id))


@app.route("/settings", methods=["GET","POST"])
@admin_required
@synchronized
def settings_page():
    settings = load_settings()
    if request.method == "POST":
        settings["practice_name"] = request.form.get("practice_name","Praktijk Schitter").strip()
        settings["app_name"] = request.form.get("app_name","Praktijk Schitter Beheer").strip() or "Praktijk Schitter Beheer"
        settings["app_subtitle"] = request.form.get("app_subtitle","").strip()
        settings["domain"] = request.form.get("domain","").strip()
        settings["default_contract_warning_days"] = request.form.get("default_contract_warning_days","60")
        settings["default_registration_warning_days"] = request.form.get("default_registration_warning_days","90")
        save_settings(settings)
        log_action("settings_updated")
        flash("Instellingen opgeslagen.", "success")
        return redirect(url_for("settings_page"))
    return render_template("settings.html", settings=settings, users=load_users(),
                           employees=[normalize_employee(e) for e in load_employees()],
                           choice_list_fields=CHOICE_LIST_FIELDS,
                           module_registry=MODULE_REGISTRY,
                           module_settings=load_module_settings())



@app.route("/settings/modules/<module_key>", methods=["POST"])
@admin_required
@synchronized
def settings_module_toggle(module_key):
    valid_modules = {m["key"] for m in MODULE_REGISTRY}
    if module_key not in valid_modules:
        abort(404)

    raw_enabled = request.form.get("enabled", "")
    if raw_enabled not in {"0", "1"}:
        flash("Ongeldige module-instelling.", "error")
        return redirect(url_for("settings_page"))

    enabled = raw_enabled == "1"
    modules = load_module_settings()
    modules[module_key] = enabled
    save_module_settings(modules)

    # Belangrijk: lees exact hetzelfde bestand opnieuw vanaf schijf.
    persisted = load_module_settings().get(module_key)
    label = next((m["label"] for m in MODULE_REGISTRY if m["key"] == module_key), module_key)

    if persisted != enabled:
        log_action("module_save_failed", detail=label, target=module_key)
        flash(f"De instelling voor {label} kon niet worden opgeslagen.", "error")
        return redirect(url_for("settings_page"))

    log_action(
        "module_enabled" if enabled else "module_disabled",
        detail=label,
        target=module_key
    )
    flash(f"{label} is {'ingeschakeld' if enabled else 'uitgeschakeld'}.", "success")
    return redirect(url_for("settings_page"))


@app.route("/settings/choice-lists/add", methods=["POST"])
@admin_required
@synchronized
def choice_list_add():
    field = request.form.get("field", "")
    value = request.form.get("value", "").strip()
    valid_keys = {f["key"] for f in CHOICE_LIST_FIELDS}
    if field not in valid_keys or not value:
        flash("Ongeldige keuzelijst of lege waarde.", "error")
        return redirect(url_for("settings_page"))
    settings = load_settings()
    lst = settings.setdefault("choice_lists", {}).setdefault(field, [])
    if value in lst:
        flash("Deze optie staat al in de lijst.", "error")
    else:
        lst.append(value)
        save_settings(settings)
        log_action("choice_list_option_added", detail=value, target=field)
        flash("Optie toegevoegd.", "success")
    return redirect(url_for("settings_page"))


@app.route("/settings/choice-lists/remove", methods=["POST"])
@admin_required
@synchronized
def choice_list_remove():
    field = request.form.get("field", "")
    value = request.form.get("value", "")
    settings = load_settings()
    lst = settings.get("choice_lists", {}).get(field, [])
    if value in lst:
        lst.remove(value)
        save_settings(settings)
        log_action("choice_list_option_removed", detail=value, target=field)
        flash("Optie verwijderd.", "success")
    return redirect(url_for("settings_page"))



@app.route("/settings/workflows/add", methods=["POST"])
@admin_required
@synchronized
def workflow_template_add():
    workflow_type = request.form.get("workflow_type", "").strip()
    title = request.form.get("title", "").strip()
    if workflow_type not in {"onboarding", "offboarding"} or not title:
        flash("Ongeldige workflowstap.", "error")
        return redirect(url_for("settings_page"))
    settings = load_settings()
    steps = settings.setdefault("workflow_templates", {}).setdefault(workflow_type, [])
    if title in steps:
        flash("Deze stap bestaat al.", "info")
    else:
        steps.append(title)
        save_settings(settings)
        log_action("workflow_template_step_added", detail=title, target=workflow_type)
        flash("Workflowstap toegevoegd.", "success")
    return redirect(url_for("settings_page"))


@app.route("/settings/workflows/remove", methods=["POST"])
@admin_required
@synchronized
def workflow_template_remove():
    workflow_type = request.form.get("workflow_type", "").strip()
    title = request.form.get("title", "")
    if workflow_type not in {"onboarding", "offboarding"}:
        abort(400)
    settings = load_settings()
    steps = settings.setdefault("workflow_templates", {}).setdefault(workflow_type, [])
    if title in steps:
        steps.remove(title)
        save_settings(settings)
        log_action("workflow_template_step_removed", detail=title, target=workflow_type)
        flash("Workflowstap verwijderd.", "success")
    return redirect(url_for("settings_page"))


@app.route("/settings/workflows/move", methods=["POST"])
@admin_required
@synchronized
def workflow_template_move():
    workflow_type = request.form.get("workflow_type", "").strip()
    title = request.form.get("title", "")
    direction = request.form.get("direction", "")
    if workflow_type not in {"onboarding", "offboarding"} or direction not in {"up", "down"}:
        abort(400)
    settings = load_settings()
    steps = settings.setdefault("workflow_templates", {}).setdefault(workflow_type, [])
    if title in steps:
        idx = steps.index(title)
        new_idx = idx - 1 if direction == "up" else idx + 1
        if 0 <= new_idx < len(steps):
            steps[idx], steps[new_idx] = steps[new_idx], steps[idx]
            save_settings(settings)
    return redirect(url_for("settings_page"))


@app.route("/settings/users/add", methods=["POST"])
@admin_required
@synchronized
def user_add():
    users = load_users()
    name = request.form.get("name","").strip()
    email = request.form.get("email","").strip().lower()
    role = request.form.get("role","Beheerder")
    employee_id = request.form.get("employee_id","").strip()
    method = request.form.get("method", "invite")
    password = request.form.get("password","")

    if not name or not email:
        flash("Naam en e-mailadres zijn verplicht.", "error")
        return redirect(url_for("settings_page"))
    if not EMAIL_RE.match(email):
        flash("Dit e-mailadres ziet er niet geldig uit.", "error")
        return redirect(url_for("settings_page"))
    if any(u.get("email")==email for u in users):
        flash("Deze gebruiker bestaat al.", "error")
        return redirect(url_for("settings_page"))
    if role == "Medewerker" and not employee_id:
        flash("Koppel een medewerker aan het medewerkersaccount.", "error")
        return redirect(url_for("settings_page"))
    if method == "manual" and len(password) < 10:
        flash("Kies een wachtwoord van minimaal 10 tekens, of stuur een setup-link per e-mail.", "error")
        return redirect(url_for("settings_page"))
    if role == "Beheerder":
        admin_password = request.form.get("admin_password", "")
        if not verify_current_admin_password(admin_password):
            log_action("admin_creation_reauth_failed", detail=email)
            flash("Bevestig het toevoegen van een beheerder met je eigen huidige wachtwoord.", "error")
            return redirect(url_for("settings_page"))

    user = {
        "id": str(uuid.uuid4()), "name": name, "email": email, "role": role,
        "employee_id": employee_id if role == "Medewerker" else "",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

    if method == "manual":
        user["password_hash"] = generate_password_hash(password)
        users.append(user)
        save_users(users)
        log_action("user_created", detail=email, target=role)
        flash(f"{role} toegevoegd met een handmatig ingesteld wachtwoord.", "success")
        return redirect(url_for("settings_page"))

    token = _create_invite(user)
    users.append(user)
    save_users(users)
    practice_name = load_settings().get("practice_name", "Praktijk Schitter")
    invite_link = url_for("accept_invite", token=token, _external=True)
    invite_text, invite_html = _invite_email_content(name, practice_name, invite_link)
    sent, error = send_email(
        email,
        f"Toegang tot {load_settings().get('app_name', 'Praktijk Schitter Beheer')}",
        invite_text,
        invite_html,
    )
    log_action("user_invited", detail=email, target=role)
    if sent:
        flash(f"{role} toegevoegd. Uitnodiging met setup-link is verzonden naar {email}.", "success")
    else:
        flash(
            f"{role} toegevoegd, maar de uitnodigingsmail kon niet worden verzonden ({error}). "
            f"Deel deze setup-link handmatig (48 uur geldig): {invite_link}",
            "error"
        )
    return redirect(url_for("settings_page"))


@app.route("/settings/users/<user_id>/resend-invite", methods=["POST"])
@admin_required
@synchronized
def user_resend_invite(user_id):
    users = load_users()
    user = next((u for u in users if u.get("id") == user_id), None)
    if not user:
        flash("Gebruiker niet gevonden.", "error")
        return redirect(url_for("settings_page"))

    token = _create_invite(user)
    save_users(users)
    practice_name = load_settings().get("practice_name", "Praktijk Schitter")
    invite_link = url_for("accept_invite", token=token, _external=True)
    invite_text, invite_html = _invite_email_content(user.get("name", ""), practice_name, invite_link)
    sent, error = send_email(
        user["email"],
        f"Toegang tot {load_settings().get('app_name', 'Praktijk Schitter Beheer')}",
        invite_text,
        invite_html,
    )
    log_action("invite_resent", detail=user.get("email", ""))
    if sent:
        flash(f"Nieuwe setup-link verzonden naar {user['email']}.", "success")
    else:
        flash(
            f"E-mail kon niet worden verzonden ({error}). "
            f"Deel deze setup-link handmatig (48 uur geldig): {invite_link}",
            "error"
        )
    return redirect(url_for("settings_page"))


@app.route("/settings/email", methods=["POST"])
@admin_required
@synchronized
def settings_email_save():
    settings = load_settings()
    settings["smtp_host"] = request.form.get("smtp_host", "").strip()
    settings["smtp_port"] = request.form.get("smtp_port", "465").strip() or "465"
    encryption = request.form.get("smtp_encryption", "ssl")
    settings["smtp_encryption"] = encryption if encryption in {"ssl", "starttls", "none"} else "ssl"
    settings["smtp_username"] = request.form.get("smtp_username", "").strip()
    new_password = request.form.get("smtp_password", "")
    if new_password:
        settings["smtp_password"] = new_password
    settings["smtp_from_email"] = request.form.get("smtp_from_email", "").strip()
    settings["smtp_from_name"] = request.form.get("smtp_from_name", "").strip()
    save_settings(settings)
    log_action("smtp_settings_updated")
    flash("E-mailinstellingen opgeslagen.", "success")
    return redirect(url_for("settings_page"))


@app.route("/settings/email/test", methods=["POST"])
@admin_required
@synchronized
def settings_email_test():
    to = request.form.get("test_email", "").strip() or session.get("user_email", "")
    settings = load_settings()
    app_name = settings.get("app_name", "Praktijk Schitter Beheer")
    sent_at = datetime.now().strftime("%d-%m-%Y om %H:%M")
    test_text = (
        f"Goed nieuws! De e-mailinstellingen van {app_name} werken.\n\n"
        f"Dit testbericht is verzonden op {sent_at}.\n"
        f"Versie: {APP_VERSION}\n\n"
        "Je hoeft niets te doen."
    )
    test_html = _branded_email_html(
        eyebrow="E-mailtest geslaagd",
        title="De e-mailinstellingen werken 🎉",
        message=(
            "Dit testbericht is rechtstreeks vanuit de applicatie verzonden. "
            "Als je dit ziet, is de SMTP-configuratie correct en kunnen toekomstige "
            "signaleringen dezelfde huisstijl gebruiken."
        ),
        details=[
            ("Verzonden", sent_at),
            ("Applicatie", app_name),
            ("Versie", APP_VERSION),
        ],
        footer_note="Dit is een testbericht. Je hoeft hierop niet te reageren.",
        preheader=f"E-mailtest geslaagd — de SMTP-configuratie van {app_name} werkt correct.",
    )
    sent, error = send_email(
        to,
        f"Testmail — {app_name}",
        test_text,
        test_html,
    )
    log_action("smtp_test_sent" if sent else "smtp_test_failed", detail=to)
    if sent:
        flash(f"Testmail verzonden naar {to}.", "success")
    else:
        flash(f"Testmail versturen mislukt: {error}", "error")
    return redirect(url_for("settings_page"))


@app.route("/uitnodiging/<token>", methods=["GET", "POST"])
@synchronized
def accept_invite(token):
    users = load_users()
    token_hash = _hash_invite_token(token)
    user = next((u for u in users if u.get("invite_token_hash") == token_hash), None)

    expired = False
    if user:
        expires = user.get("invite_expires_at")
        if expires:
            try:
                expired = datetime.fromisoformat(expires) < datetime.now()
            except ValueError:
                expired = True

    if not user or expired:
        return render_template("accept_invite.html", invalid=True, expired=expired and bool(user))

    if request.method == "POST":
        password = request.form.get("password", "")
        password_repeat = request.form.get("password_repeat", "")
        if len(password) < 10:
            flash("Het wachtwoord moet minimaal 10 tekens zijn.", "error")
            return render_template("accept_invite.html", user=user)
        if password != password_repeat:
            flash("De twee wachtwoorden komen niet overeen.", "error")
            return render_template("accept_invite.html", user=user)

        user["password_hash"] = generate_password_hash(password)
        user["invite_token_hash"] = None
        user["invite_expires_at"] = None
        user["invite_pending"] = False
        save_users(users)
        log_action("invite_accepted", detail=user.get("email", ""), user=user.get("name", ""))
        flash("Wachtwoord ingesteld. Je kunt nu inloggen.", "success")
        return redirect(url_for("login"))

    return render_template("accept_invite.html", user=user)


@app.route("/settings/users/<user_id>/delete", methods=["POST"])
@admin_required
@synchronized
def user_delete(user_id):
    users = load_users()
    target = next((u for u in users if u.get("id") == user_id), None)
    if not target:
        flash("Gebruiker niet gevonden.", "error")
        return redirect(url_for("settings_page"))
    if target.get("email") == session.get("user_email"):
        flash("Je kunt je eigen account niet verwijderen. Vraag een andere beheerder.", "error")
        return redirect(url_for("settings_page"))
    admins = [u for u in users if u.get("role") == "Beheerder"]
    if target.get("role") == "Beheerder" and len(admins) <= 1:
        flash("Dit is de enige beheerder; verwijderen kan niet zonder de praktijk buiten te sluiten.", "error")
        return redirect(url_for("settings_page"))
    if target.get("role") == "Beheerder":
        if not verify_current_admin_password(request.form.get("admin_password", "")):
            log_action("admin_delete_reauth_failed", detail=target.get("email", ""))
            flash("Bevestig het verwijderen van een beheerder met je eigen huidige wachtwoord.", "error")
            return redirect(url_for("settings_page"))
    users = [u for u in users if u.get("id") != user_id]
    save_users(users)
    log_action("user_deleted", detail=target.get("email",""), target=target.get("role",""))
    flash("Gebruiker verwijderd.", "success")
    return redirect(url_for("settings_page"))


@app.route("/settings/users/<user_id>/reset-password", methods=["POST"])
@admin_required
@synchronized
def user_reset_password(user_id):
    users = load_users()
    target = next((u for u in users if u.get("id") == user_id), None)
    new_password = request.form.get("new_password", "")
    if not target:
        flash("Gebruiker niet gevonden.", "error")
    elif len(new_password) < 10:
        flash("Het nieuwe wachtwoord moet minimaal 10 tekens zijn.", "error")
    elif target.get("role") == "Beheerder" and not verify_current_admin_password(request.form.get("admin_password", "")):
        log_action("admin_password_reset_reauth_failed", detail=target.get("email", ""))
        flash("Bevestig het resetten van een beheerderswachtwoord met je eigen huidige wachtwoord.", "error")
    else:
        target["password_hash"] = generate_password_hash(new_password)
        save_users(users)
        log_action("user_password_reset", detail=target.get("email",""))
        flash(f"Wachtwoord van {target.get('name','')} is gewijzigd. Geef dit veilig door.", "success")
    return redirect(url_for("settings_page"))


@app.route("/settings/backup")
@admin_required
@synchronized
def settings_backup():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(STORAGE_DIR.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(STORAGE_DIR.parent))
    buffer.seek(0)
    log_action("backup_downloaded")
    filename = f"backup_praktijk_schitter_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return Response(
        buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.route("/audit")
@admin_required
def audit_log():
    entries = sorted(load_module("audit"), key=lambda x: x.get("timestamp",""), reverse=True)
    return render_template("audit.html", entries=entries[:300], total=len(entries))


@app.route("/account/wachtwoord", methods=["GET", "POST"])
@login_required
@synchronized
def account_password():
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        new_password_repeat = request.form.get("new_password_repeat", "")

        if not check_password_hash(user["password_hash"], current_password):
            flash("Je huidige wachtwoord klopt niet.", "error")
        elif len(new_password) < 10:
            flash("Het nieuwe wachtwoord moet minimaal 10 tekens zijn.", "error")
        elif new_password != new_password_repeat:
            flash("De twee nieuwe wachtwoorden komen niet overeen.", "error")
        else:
            users = load_users()
            for u in users:
                if u.get("email") == user["email"]:
                    u["password_hash"] = generate_password_hash(new_password)
            save_users(users)
            log_action("own_password_changed")
            flash("Wachtwoord gewijzigd.", "success")
            return redirect(url_for("index"))
    return render_template("account_password.html")


# ---------- Auth ----------

@app.route("/")
def index():
    if not load_users():
        return redirect(url_for("setup"))
    if "user_email" not in session:
        return redirect(url_for("login"))
    user = current_user()
    if user and user.get("role") == "Medewerker":
        return redirect(url_for("employee_portal"))
    return redirect(url_for("dashboard"))


@app.route("/setup", methods=["GET", "POST"])
@synchronized
def setup():
    # Zodra er een gebruiker bestaat is de bootstrap-route definitief gesloten.
    if load_users():
        return redirect(url_for("login"))

    expected_setup_code = ensure_first_setup_code()
    if IS_PRODUCTION and not expected_setup_code:
        flash("De online eerste inrichting is nog niet vrijgegeven. Stel SCHITTER_SETUP_CODE in bij Render → Environment.", "error")
        return render_template("setup.html"), 503

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        setup_code = request.form.get("setup_code", "").strip().upper()
        expected_code = expected_setup_code.strip().upper()

        if not secrets.compare_digest(setup_code, expected_code):
            log_action("setup_code_rejected", detail=email or "onbekend")
            flash("De eerste-startcode is niet juist.", "error")
            return render_template("setup.html")
        if not name or not email or len(password) < 10:
            flash("Vul alle velden in en gebruik een wachtwoord van minimaal 10 tekens.", "error")
            return render_template("setup.html")
        if not EMAIL_RE.match(email):
            flash("Dit e-mailadres ziet er niet geldig uit.", "error")
            return render_template("setup.html")

        save_users([{
            "id": str(uuid.uuid4()),
            "name": name,
            "email": email,
            "password_hash": generate_password_hash(password),
            "role": "Beheerder",
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }])
        try:
            SETUP_CODE_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        log_action("setup_admin_created", detail=email, user=name)
        flash("Eerste beheerder veilig aangemaakt. Je kunt nu aanmelden.", "success")
        return redirect(url_for("login"))

    return render_template("setup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not load_users():
        return redirect(url_for("setup"))

    if request.method == "POST":
        key = _login_key()
        locked, wait_seconds = _is_locked_out(key)
        if locked:
            minutes = max(1, wait_seconds // 60 + 1)
            flash(f"Te veel mislukte inlogpogingen. Probeer het over {minutes} minuten opnieuw.", "error")
            return render_template("login.html")

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = next((u for u in load_users() if u["email"] == email), None)

        if not user or not check_password_hash(user["password_hash"], password):
            _register_login_failure(key)
            log_action("login_failed", detail=email)
            flash("E-mailadres of wachtwoord is niet juist.", "error")
            return render_template("login.html")

        _clear_login_failures(key)
        session.clear()
        session.permanent = True
        _ensure_csrf_token()
        session["user_email"] = user["email"]
        session["user_name"] = user["name"]
        session["user_role"] = user.get("role", "Beheerder")
        log_action("login_success", user=user["name"])
        if user.get("role") == "Medewerker":
            return redirect(url_for("employee_portal"))
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    if "user_email" in session:
        log_action("logout")
    session.clear()

    # Bij lokaal starten via START_WINDOWS/START_MAC sluiten we na uitloggen
    # ook de lokale server af, zodat de poort vrijkomt. Op een gehoste server
    # blijft uitloggen uiteraard alleen de gebruikerssessie beëindigen.
    if os.environ.get("PSB_LOCAL_LAUNCH") == "1":
        def _shutdown_local_instance():
            time.sleep(0.35)
            os._exit(0)
        threading.Thread(target=_shutdown_local_instance, daemon=True).start()
        return render_template("logged_out.html")

    return redirect(url_for("login"))


# ---------- Dashboard / medewerkers ----------


def collect_action_items():
    """Build one practice-wide action list from all active modules."""
    employees = [normalize_employee(e) for e in load_employees()]
    employees_map = {e["id"]: e for e in employees}
    today = date.today()
    items = []

    if module_enabled("hr_employment"):
        for e in employees:
            end_date = e.get("contract_end")
            if not end_date or e.get("status") != "In dienst":
                continue
            try:
                days = (date.fromisoformat(end_date) - today).days
            except ValueError:
                continue
            if days <= 60:
                items.append({
                    "source": "contract",
                    "severity": "danger" if days < 0 else "warning",
                    "title": f"Contract {e.get('first_name','')} {e.get('last_name','')}",
                    "detail": f"{abs(days)} dagen verlopen" if days < 0 else f"Loopt af over {days} dagen",
                    "href": url_for("employee_detail", employee_id=e["id"], tab="employment"),
                    "due_date": end_date,
                    "sort_days": days,
                })

    if module_enabled("registrations"):
        for reg in load_registrations():
            expiry = reg.get("expires_at")
            if not expiry:
                continue
            try:
                days = (date.fromisoformat(expiry) - today).days
            except ValueError:
                continue
            warning_days = registration_warning_days(reg)
            if days > warning_days:
                continue
            emp = employees_map.get(reg.get("employee_id"))
            emp_name = f"{emp.get('first_name','')} {emp.get('last_name','')}".strip() if emp else ""
            items.append({
                "source": "registration",
                "severity": "danger" if days < 0 else "warning",
                "title": f"{reg.get('type','Registratie')} {emp_name}".strip(),
                "detail": f"{abs(days)} dagen verlopen" if days < 0 else f"Verloopt over {days} dagen",
                "href": url_for("registrations_overview", q=emp_name),
                "due_date": expiry,
                "sort_days": days,
            })

    if module_enabled("assets"):
        for asset in load_assets():
            if asset.get("status") in {"Reparatie", "Kwijt"}:
                items.append({
                    "source": "asset",
                    "severity": "danger" if asset.get("status") == "Kwijt" else "warning",
                    "title": f"{asset.get('name','Bedrijfsmiddel')} · {asset.get('status')}",
                    "detail": "Bedrijfsmiddel vraagt aandacht",
                    "href": url_for("asset_detail", asset_id=asset["id"]),
                    "due_date": "",
                    "sort_days": -500,
                })

            if asset.get("status") == "Uitgegeven" and asset.get("return_due"):
                try:
                    days = (date.fromisoformat(asset["return_due"]) - today).days
                except ValueError:
                    days = None
                if days is not None and days <= 30:
                    items.append({
                        "source": "asset",
                        "severity": "danger" if days < 0 else "warning",
                        "title": f"Retour {asset.get('name','Bedrijfsmiddel')}",
                        "detail": f"{abs(days)} dagen te laat" if days < 0 else f"Over {days} dagen verwacht",
                        "href": url_for("asset_detail", asset_id=asset["id"]),
                        "due_date": asset.get("return_due",""),
                        "sort_days": days,
                    })

            if asset.get("warranty_until"):
                try:
                    days = (date.fromisoformat(asset["warranty_until"]) - today).days
                except ValueError:
                    days = None
                if days is not None and 0 <= days <= 30:
                    items.append({
                        "source": "asset",
                        "severity": "info",
                        "title": f"Garantie {asset.get('name','Bedrijfsmiddel')}",
                        "detail": f"Loopt af over {days} dagen",
                        "href": url_for("asset_detail", asset_id=asset["id"]),
                        "due_date": asset.get("warranty_until",""),
                        "sort_days": days + 1000,
                    })

    if module_enabled("signals"):
        for task in load_module("tasks"):
            if task.get("status") == "Afgerond":
                continue
            due = task.get("due_date","")
            days = 9999
            detail = task.get("priority","Normaal")
            severity = "info"
            if due:
                try:
                    days = (date.fromisoformat(due) - today).days
                    detail = f"{abs(days)} dagen te laat" if days < 0 else f"Over {days} dagen"
                    severity = "danger" if days < 0 else ("warning" if days <= 7 else "info")
                except ValueError:
                    pass
            items.append({
                "source": "task",
                "severity": severity,
                "title": task.get("title") or "Taak",
                "detail": detail,
                "href": url_for("tasks"),
                "due_date": due,
                "sort_days": days,
            })

        for workflow in load_module("onboarding"):
            open_items = [i for i in workflow.get("items", []) if not i.get("done")]
            if open_items:
                emp = employees_map.get(workflow.get("employee_id"))
                emp_name = f"{emp.get('first_name','')} {emp.get('last_name','')}".strip() if emp else workflow.get("employee_name","")
                items.append({
                    "source": "workflow",
                    "workflow_type": "onboarding",
                    "severity": "info",
                    "title": f"Indienstworkflow {emp_name}",
                    "detail": f"{len(open_items)} actie(s) nog open",
                    "href": url_for("onboarding_detail", employee_id=workflow.get("employee_id")),
                    "due_date": workflow.get("start_date",""),
                    "sort_days": 0,
                })

        for workflow in load_module("offboarding"):
            open_items = [i for i in workflow.get("items", []) if not i.get("done")]
            if open_items:
                emp = employees_map.get(workflow.get("employee_id"))
                emp_name = f"{emp.get('first_name','')} {emp.get('last_name','')}".strip() if emp else workflow.get("employee_name","")
                items.append({
                    "source": "workflow",
                    "severity": "warning",
                    "title": f"Uitdienstworkflow {emp_name}",
                    "detail": f"{len(open_items)} actie(s) nog open",
                    "href": url_for("offboarding_detail", employee_id=workflow.get("employee_id")),
                    "due_date": workflow.get("last_working_day",""),
                    "sort_days": 0,
                })

    items.sort(key=lambda x: ({"danger":0,"warning":1,"info":2}.get(x.get("severity"),3), x.get("sort_days",99999), x.get("title","")))
    return items


@app.route("/actions")
@admin_required
@module_required("signals")
def action_center():
    items = collect_action_items()
    counts = {
        "total": len(items),
        "danger": sum(1 for i in items if i.get("severity") == "danger"),
        "warning": sum(1 for i in items if i.get("severity") == "warning"),
        "info": sum(1 for i in items if i.get("severity") == "info"),
    }
    onboarding_workflows = load_module("onboarding")
    offboarding_workflows = load_module("offboarding")
    return render_template("actions.html", items=items, counts=counts,
                           onboarding_workflows=onboarding_workflows,
                           offboarding_workflows=offboarding_workflows)


@app.route("/dashboard")
@admin_required
def dashboard():
    employees = [normalize_employee(e) for e in load_employees()]
    stats = calculate_stats(employees)

    sorted_employees = sorted(
        employees,
        key=lambda e: (e.get("last_name", "").lower(), e.get("first_name", "").lower())
    )

    attention = []
    today = date.today()
    employees_map = {e["id"]: e for e in employees}

    # HR / contractsignaleringen
    if module_enabled("hr_employment"):
        for e in employees:
            end_date = e.get("contract_end")
            if end_date and e.get("status") == "In dienst":
                try:
                    days = (date.fromisoformat(end_date) - today).days
                except ValueError:
                    continue
                if 0 <= days <= 60:
                    attention.append({
                        "kind": "contract",
                        "severity": "warning",
                        "title": f"Contract {e.get('first_name','')} {e.get('last_name','')}",
                        "detail": f"Loopt af over {days} dagen",
                        "href": url_for("employee_detail", employee_id=e["id"], tab="employment"),
                        "sort_days": days,
                    })

    # Registraties
    if module_enabled("registrations"):
        for reg in load_registrations():
            expiry = reg.get("expires_at")
            if not expiry:
                continue
            try:
                days = (date.fromisoformat(expiry) - today).days
            except ValueError:
                continue
            warning_days = registration_warning_days(reg)
            emp = employees_map.get(reg.get("employee_id"))
            emp_name = f"{emp.get('first_name','')} {emp.get('last_name','')}".strip() if emp else ""
            if days < 0:
                attention.append({
                    "kind": "registration",
                    "severity": "danger",
                    "title": f"{reg.get('type','Registratie')} {emp_name}".strip(),
                    "detail": f"Verlopen sinds {abs(days)} dagen",
                    "href": url_for("registrations_overview"),
                    "sort_days": -10000 + days,
                })
            elif days <= warning_days:
                attention.append({
                    "kind": "registration",
                    "severity": "warning",
                    "title": f"{reg.get('type','Registratie')} {emp_name}".strip(),
                    "detail": f"Verloopt over {days} dagen",
                    "href": url_for("registrations_overview"),
                    "sort_days": days,
                })

    # Bedrijfsmiddelen: retour- en garantiedata
    asset_counts = {}
    if module_enabled("assets"):
        for asset in load_assets():
            employee_id = asset.get("assigned_employee_id")
            if employee_id:
                asset_counts[employee_id] = asset_counts.get(employee_id, 0) + 1

            return_due = asset.get("return_due")
            if return_due and asset.get("status") == "Uitgegeven":
                try:
                    days = (date.fromisoformat(return_due) - today).days
                except ValueError:
                    days = None
                if days is not None and days <= 30:
                    label = f"Retour {asset.get('name','Bedrijfsmiddel')}"
                    detail = f"{abs(days)} dagen te laat" if days < 0 else f"Over {days} dagen verwacht"
                    attention.append({
                        "kind": "asset",
                        "severity": "danger" if days < 0 else "warning",
                        "title": label,
                        "detail": detail,
                        "href": url_for("asset_detail", asset_id=asset["id"]),
                        "sort_days": days if days >= 0 else -9000 + days,
                    })

            warranty = asset.get("warranty_until")
            if warranty:
                try:
                    days = (date.fromisoformat(warranty) - today).days
                except ValueError:
                    days = None
                if days is not None and 0 <= days <= 30:
                    attention.append({
                        "kind": "asset",
                        "severity": "info",
                        "title": f"Garantie {asset.get('name','Bedrijfsmiddel')}",
                        "detail": f"Loopt af over {days} dagen",
                        "href": url_for("asset_detail", asset_id=asset["id"]),
                        "sort_days": days + 1000,
                    })

    # Handmatige taken
    if module_enabled("signals"):
        for task in load_module("tasks"):
            if task.get("status") == "Afgerond" or not task.get("due_date"):
                continue
            try:
                days = (date.fromisoformat(task["due_date"]) - today).days
            except ValueError:
                continue
            if days <= 30:
                attention.append({
                    "kind": "task",
                    "severity": "danger" if days < 0 else "info",
                    "title": task.get("title") or "Taak",
                    "detail": f"{abs(days)} dagen te laat" if days < 0 else f"Over {days} dagen",
                    "href": url_for("tasks"),
                    "sort_days": days if days >= 0 else -8000 + days,
                })

    attention = collect_action_items()

    recent_docs = []
    if module_enabled("documents"):
        for e in employees:
            for d in e.get("documents", []):
                recent_docs.append({
                    **d,
                    "employee_name": f"{e.get('first_name', '')} {e.get('last_name', '')}".strip(),
                    "employee_id": e["id"]
                })
    recent_docs.sort(key=lambda d: d.get("uploaded_at", ""), reverse=True)

    return render_template(
        "dashboard.html",
        employees=sorted_employees,
        stats=stats,
        attention=attention[:8],
        recent_docs=recent_docs[:5],
        asset_counts=asset_counts,
        today=datetime.now(),
    )

@app.route("/employees")
@admin_required
def employees():
    items = [normalize_employee(e) for e in load_employees()]
    q = request.args.get("q", "").strip().lower()
    if q:
        items = [
            e for e in items
            if q in f"{e.get('first_name','')} {e.get('last_name','')} {e.get('function','')}".lower()
        ]
    items.sort(key=lambda e: (e.get("last_name", "").lower(), e.get("first_name", "").lower()))
    return render_template("employees.html", employees=items, q=q)


@app.route("/employees/export.csv")
@admin_required
def employees_export():
    items = [normalize_employee(e) for e in load_employees()]
    items.sort(key=lambda e: (e.get("last_name", "").lower(), e.get("first_name", "").lower()))
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([
        "Voornaam", "Achternaam", "Functie", "Privé e-mail", "Zakelijk e-mail", "Telefoon",
        "Startdatum", "Einddatum contract", "Uren per week", "Type dienstverband", "Status",
        "Werklocatie", "Leidinggevende"
    ])
    for e in items:
        writer.writerow([
            e["first_name"], e["last_name"], e["function"], e["email"], e["work_email"],
            e["phone"], e["start_date"], e["contract_end"], e["hours"], e["employment_type"],
            e["status"], e["work_location"], e["manager"]
        ])
    log_action("employees_exported")
    csv_bytes = buffer.getvalue().encode("utf-8-sig")
    filename = f"medewerkers_{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        csv_bytes,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


def employee_from_form(existing=None):
    e = normalize_employee(existing or {"id": str(uuid.uuid4())})
    fields = [
        "first_name", "last_name", "email", "phone",
        "address", "postal_code", "city", "birth_date",
        "emergency_name", "emergency_phone",
        "start_date", "contract_end", "hours", "status",
        "employment_type", "salary_scale", "work_email",
        "manager", "notes", "annual_leave_hours", "carryover_leave_hours"
    ]
    for field in fields:
        e[field] = request.form.get(field, "").strip()

    e["function"] = _resolve_choice_field(request.form, "function")
    e["work_location"] = _resolve_choice_field(request.form, "work_location")

    e["updated_at"] = datetime.now().isoformat(timespec="seconds")
    e.setdefault("created_at", e["updated_at"])
    return e


@app.route("/employees/new", methods=["GET", "POST"])
@admin_required
@synchronized
def employee_new():
    if request.method == "POST":
        employee = employee_from_form()
        errors = []
        if not employee["first_name"] or not employee["last_name"]:
            errors.append("Voornaam en achternaam zijn verplicht.")
        errors += _validate_employee_fields(employee)

        if errors:
            for err in errors:
                flash(err, "error")
            return render_template("employee_form.html", employee=employee, is_new=True, settings=load_settings())

        employees_list = load_employees()
        employees_list.append(employee)
        save_employees(employees_list)
        log_action(
            "employee_created",
            detail=f"{employee['first_name']} {employee['last_name']}",
            target=employee["id"],
        )
        flash("Medewerker toegevoegd.", "success")
        return redirect(url_for("employee_detail", employee_id=employee["id"]))

    return render_template("employee_form.html", employee=None, is_new=True, settings=load_settings())


@app.route("/employees/<employee_id>")
@admin_required
def employee_detail(employee_id):
    employee = find_employee(employee_id)
    if not employee:
        return "Medewerker niet gevonden", 404
    employee = normalize_employee(employee)
    settings = load_settings()
    tab = request.args.get("tab", "overview")
    allowed_tabs = {"overview", "personal", "custom", "notes"}
    allowed_tabs.update(tab_name for tab_name in EMPLOYEE_TAB_MODULES if employee_tab_available(tab_name))
    if tab not in allowed_tabs:
        flash("Dit onderdeel staat momenteel uitgeschakeld.", "error")
        return redirect(url_for("employee_detail", employee_id=employee_id, tab="overview"))

    employee_credentials = []
    if module_enabled("credentials"):
        employee_credentials = [
            c for c in load_credentials()
            if employee_id in c.get("linked_employee_ids", [])
        ]

    employee_assets = []
    if module_enabled("assets"):
        employee_assets = [
            a for a in load_assets()
            if a.get("assigned_employee_id") == employee_id
        ]

    employee_registrations = []
    if module_enabled("registrations"):
        employee_registrations = [
            r for r in load_registrations()
            if r.get("employee_id") == employee_id
        ]

    return render_template(
        "employee_detail.html",
        employee=employee,
        settings=settings,
        active_tab=tab,
        employee_credentials=employee_credentials,
        employee_assets=employee_assets,
        employee_registrations=employee_registrations
    )


@app.route("/employees/<employee_id>/edit", methods=["GET", "POST"])
@admin_required
@synchronized
def employee_edit(employee_id):
    employees_list = load_employees()
    employee = find_employee(employee_id, employees_list)
    if not employee:
        return "Medewerker niet gevonden", 404

    if request.method == "POST":
        updated = employee_from_form(employee)
        errors = []
        if not updated["first_name"] or not updated["last_name"]:
            errors.append("Voornaam en achternaam zijn verplicht.")
        errors += _validate_employee_fields(updated)

        if errors:
            for err in errors:
                flash(err, "error")
            return render_template("employee_form.html", employee=updated, is_new=False, settings=load_settings())

        save_employees(employees_list)
        log_action(
            "employee_updated",
            detail=f"{updated['first_name']} {updated['last_name']}",
            target=employee_id,
        )
        flash("Wijzigingen opgeslagen.", "success")
        return redirect(url_for("employee_detail", employee_id=employee_id))

    return render_template("employee_form.html", employee=normalize_employee(employee), is_new=False, settings=load_settings())


@app.route("/employees/<employee_id>/delete/confirm")
@admin_required
def employee_delete_confirm(employee_id):
    employee = find_employee(employee_id)
    if not employee:
        return "Medewerker niet gevonden", 404
    return render_template("employee_delete_confirm.html", employee=normalize_employee(employee))


@app.route("/employees/<employee_id>/delete", methods=["POST"])
@admin_required
@synchronized
def employee_delete(employee_id):
    employees_list = load_employees()
    employee = find_employee(employee_id, employees_list)
    if not employee:
        return "Medewerker niet gevonden", 404
    employee = normalize_employee(employee)
    name = f"{employee.get('first_name','')} {employee.get('last_name','')}".strip()

    employees_list = [e for e in employees_list if e["id"] != employee_id]
    save_employees(employees_list)

    employee_dir = DOCUMENTS_DIR / employee_id
    if employee_dir.exists():
        shutil.rmtree(employee_dir, ignore_errors=True)

    users = load_users()
    remaining_users = [u for u in users if u.get("employee_id") != employee_id]
    if len(remaining_users) != len(users):
        save_users(remaining_users)

    log_action("employee_deleted", detail=name, target=employee_id)
    flash(f"Dossier van {name} is verwijderd.", "success")
    return redirect(url_for("employees"))


# ---------- Documenten ----------

@app.route("/employees/<employee_id>/documents", methods=["POST"])
@admin_required
@module_required("documents")
@synchronized
def document_upload(employee_id):
    employees_list = load_employees()
    employee = find_employee(employee_id, employees_list)
    if not employee:
        return "Medewerker niet gevonden", 404
    normalize_employee(employee)

    uploaded = request.files.get("document")
    doc_type = request.form.get("doc_type", "Overig").strip() or "Overig"

    if not uploaded or not uploaded.filename:
        flash("Kies eerst een bestand.", "error")
        return redirect(url_for("employee_detail", employee_id=employee_id, tab="documents"))

    ext = uploaded.filename.rsplit(".", 1)[-1].lower() if "." in uploaded.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        flash("Dit bestandstype is niet toegestaan.", "error")
        return redirect(url_for("employee_detail", employee_id=employee_id, tab="documents"))

    original = secure_filename(uploaded.filename) or "document"
    content_type = uploaded.mimetype or mimetypes.guess_type(original)[0] or "application/octet-stream"
    raw = uploaded.read()

    employee_dir = DOCUMENTS_DIR / employee_id
    employee_dir.mkdir(parents=True, exist_ok=True)

    # Documenten worden versleuteld op schijf gezet, met dezelfde sleutel
    # als de personeelsgegevens. Wie alleen de documentenmap in handen
    # krijgt (bijv. via een back-up of gedeelde schijf) kan de inhoud dus
    # niet zomaar lezen.
    stored_name = f"{uuid.uuid4().hex}.enc"
    encrypted = _get_fernet().encrypt(raw)
    (employee_dir / stored_name).write_bytes(encrypted)

    employee["documents"].append({
        "id": str(uuid.uuid4()),
        "title": original,
        "type": doc_type,
        "stored_name": stored_name,
        "encrypted": True,
        "content_type": content_type,
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
        "uploaded_by": session.get("user_name", ""),
    })
    employee["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_employees(employees_list)
    log_action("document_uploaded", detail=original, target=employee_id)
    flash("Document toegevoegd.", "success")
    return redirect(url_for("employee_detail", employee_id=employee_id, tab="documents"))


@app.route("/employees/<employee_id>/documents/<document_id>")
@admin_required
@module_required("documents")
@synchronized
def document_download(employee_id, document_id):
    employees_list = load_employees()
    employee = find_employee(employee_id, employees_list)
    if not employee:
        return "Medewerker niet gevonden", 404
    normalize_employee(employee)
    doc = next((d for d in employee.get("documents", []) if d["id"] == document_id), None)
    if not doc:
        return "Document niet gevonden", 404

    folder = DOCUMENTS_DIR / employee_id
    file_path = folder / doc["stored_name"]
    if not file_path.exists():
        return "Bestand niet gevonden op schijf", 404

    raw = file_path.read_bytes()
    if doc.get("encrypted"):
        try:
            content = _get_fernet().decrypt(raw)
        except InvalidToken:
            return "Document kon niet worden ontsleuteld.", 500
    else:
        # Ouder, nog onversleuteld document (bijvoorbeeld uit versie 0.2.x):
        # bij het openen alsnog versleutelen en de oude platte-tekstversie
        # vervangen, zodat elk document na één keer bekijken beveiligd is.
        content = raw
        try:
            new_stored_name = f"{uuid.uuid4().hex}.enc"
            (folder / new_stored_name).write_bytes(_get_fernet().encrypt(content))
            for d in employee.get("documents", []):
                if d.get("id") == document_id:
                    d["stored_name"] = new_stored_name
                    d["encrypted"] = True
            save_employees(employees_list)
            file_path.unlink(missing_ok=True)
        except OSError:
            pass

    content_type = doc.get("content_type") or mimetypes.guess_type(doc["title"])[0] or "application/octet-stream"
    log_action("document_downloaded", detail=doc["title"], target=employee_id)

    ascii_name = doc["title"].encode("ascii", "ignore").decode("ascii") or "document"
    response = Response(content, mimetype=content_type)
    response.headers["Content-Disposition"] = (
        f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(doc['title'])}"
    )
    return response


@app.route("/employees/<employee_id>/documents/<document_id>/delete", methods=["POST"])
@admin_required
@module_required("documents")
@synchronized
def document_delete(employee_id, document_id):
    employees_list = load_employees()
    employee = find_employee(employee_id, employees_list)
    if not employee:
        return "Medewerker niet gevonden", 404
    normalize_employee(employee)
    doc = next((d for d in employee.get("documents", []) if d["id"] == document_id), None)
    if not doc:
        flash("Document niet gevonden.", "error")
        return redirect(url_for("employee_detail", employee_id=employee_id, tab="documents"))

    file_path = DOCUMENTS_DIR / employee_id / doc["stored_name"]
    try:
        file_path.unlink(missing_ok=True)
    except OSError:
        pass

    employee["documents"] = [d for d in employee["documents"] if d["id"] != document_id]
    save_employees(employees_list)
    log_action("document_deleted", detail=doc.get("title", ""), target=employee_id)
    flash("Document verwijderd.", "success")
    return redirect(url_for("employee_detail", employee_id=employee_id, tab="documents"))


# ---------- Pasfoto ----------
#
# De foto wordt, net als documenten, versleuteld opgeslagen in de
# documentenmap van de medewerker (en dus automatisch mee opgeruimd als het
# hele dossier wordt verwijderd). Er wordt bewust niet verkleind of
# bijgesneden op de server (dat zou een extra afhankelijkheid vergen) — de
# weergave gebeurt met CSS, dus een niet-vierkante foto oogt gewoon prima.

def _can_view_employee_photo(employee_id: str) -> bool:
    if is_admin():
        return True
    user = current_user()
    return bool(user and user.get("role") == "Medewerker" and user.get("employee_id") == employee_id)


@app.route("/employees/<employee_id>/photo", methods=["POST"])
@admin_required
@synchronized
def employee_photo_upload(employee_id):
    employees_list = load_employees()
    employee = find_employee(employee_id, employees_list)
    if not employee:
        return "Medewerker niet gevonden", 404
    normalize_employee(employee)
    tab = request.form.get("tab", "overview")

    uploaded = request.files.get("photo")
    if not uploaded or not uploaded.filename:
        flash("Kies eerst een foto.", "error")
        return redirect(url_for("employee_detail", employee_id=employee_id, tab=tab))

    ext = uploaded.filename.rsplit(".", 1)[-1].lower() if "." in uploaded.filename else ""
    if ext not in ALLOWED_PHOTO_EXTENSIONS:
        flash("Gebruik een JPG, PNG, WEBP of GIF voor de pasfoto.", "error")
        return redirect(url_for("employee_detail", employee_id=employee_id, tab=tab))

    content_type = uploaded.mimetype or mimetypes.guess_type(uploaded.filename)[0] or "image/jpeg"
    raw = uploaded.read()

    employee_dir = DOCUMENTS_DIR / employee_id
    employee_dir.mkdir(parents=True, exist_ok=True)

    old_photo = employee.get("photo")
    if old_photo and old_photo.get("stored_name"):
        try:
            (employee_dir / old_photo["stored_name"]).unlink(missing_ok=True)
        except OSError:
            pass

    stored_name = f"photo_{uuid.uuid4().hex}.enc"
    (employee_dir / stored_name).write_bytes(_get_fernet().encrypt(raw))

    employee["photo"] = {
        "stored_name": stored_name,
        "content_type": content_type,
        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_employees(employees_list)
    log_action("employee_photo_updated", target=employee_id)
    flash("Pasfoto bijgewerkt.", "success")
    return redirect(url_for("employee_detail", employee_id=employee_id, tab=tab))


@app.route("/employees/<employee_id>/photo/delete", methods=["POST"])
@admin_required
@synchronized
def employee_photo_delete(employee_id):
    employees_list = load_employees()
    employee = find_employee(employee_id, employees_list)
    if not employee:
        return "Medewerker niet gevonden", 404
    normalize_employee(employee)
    tab = request.form.get("tab", "overview")

    photo = employee.get("photo")
    if photo and photo.get("stored_name"):
        try:
            (DOCUMENTS_DIR / employee_id / photo["stored_name"]).unlink(missing_ok=True)
        except OSError:
            pass
    employee["photo"] = None
    save_employees(employees_list)
    log_action("employee_photo_deleted", target=employee_id)
    flash("Pasfoto verwijderd.", "success")
    return redirect(url_for("employee_detail", employee_id=employee_id, tab=tab))


@app.route("/employees/<employee_id>/photo")
@login_required
@synchronized
def employee_photo(employee_id):
    if not _can_view_employee_photo(employee_id):
        abort(403)
    employee = find_employee(employee_id)
    if not employee:
        abort(404)
    normalize_employee(employee)
    photo = employee.get("photo")
    if not photo or not photo.get("stored_name"):
        abort(404)
    file_path = DOCUMENTS_DIR / employee_id / photo["stored_name"]
    if not file_path.exists():
        abort(404)
    try:
        content = _get_fernet().decrypt(file_path.read_bytes())
    except InvalidToken:
        abort(500)
    response = Response(content, mimetype=photo.get("content_type") or "image/jpeg")
    response.headers["Cache-Control"] = "private, max-age=300"
    return response


@app.context_processor
def inject_sidebar_photo():
    user = current_user()
    if user and user.get("role") == "Medewerker" and user.get("employee_id"):
        emp = find_employee(user["employee_id"])
        if emp and emp.get("photo"):
            return {"sidebar_photo_employee_id": user["employee_id"]}
    return {"sidebar_photo_employee_id": None}


# ---------- Bedrijfsmiddelen ----------


@app.route("/employees/<employee_id>/assets/add", methods=["POST"])
@admin_required
@module_required("assets")
@synchronized
def asset_add(employee_id):
    """Compatibility route: add directly to central inventory and assign to employee."""
    employee = find_employee(employee_id)
    if not employee:
        abort(404)
    employee = normalize_employee(employee)
    assets = load_assets()
    name = request.form.get("name", "").strip()
    if not name:
        flash("Geef het bedrijfsmiddel een naam.", "error")
        return redirect(url_for("employee_detail", employee_id=employee_id, tab="assets"))
    asset = {
        "id": str(uuid.uuid4()),
        "category": _resolve_choice_field(request.form, "asset_type") or "Overig",
        "name": name,
        "brand": request.form.get("brand", "").strip(),
        "model": request.form.get("model", "").strip(),
        "serial_number": request.form.get("serial_number", "").strip(),
        "asset_number": request.form.get("asset_number", "").strip(),
        "imei": request.form.get("imei", "").strip(),
        "phone_number": request.form.get("phone_number", "").strip(),
        "provider": _resolve_choice_field(request.form, "provider"),
        "pin": request.form.get("pin", "").strip(),
        "puk": request.form.get("puk", "").strip(),
        "os": _resolve_choice_field(request.form, "os"),
        "purchase_date": request.form.get("purchase_date", "").strip(),
        "warranty_until": request.form.get("warranty_until", "").strip(),
        "return_due": request.form.get("return_due", "").strip(),
        "accessories": request.form.get("accessories", "").strip(),
        "notes": request.form.get("asset_notes", "").strip(),
        "status": "Uitgegeven",
        "assigned_employee_id": employee_id,
        "assignment_history": [{
            "employee_id": employee_id,
            "employee_name": f"{employee.get('first_name','')} {employee.get('last_name','')}".strip(),
            "assigned_at": request.form.get("issued_at", "").strip() or date.today().isoformat(),
            "returned_at": "",
            "note": "Toegevoegd vanuit personeelsdossier"
        }],
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds")
    }
    assets.append(asset)
    save_assets(assets)
    log_action("asset_created", detail=name, target=asset["id"])
    flash("Bedrijfsmiddel toegevoegd en uitgegeven.", "success")
    return redirect(url_for("employee_detail", employee_id=employee_id, tab="assets"))


@app.route("/employees/<employee_id>/assets/<asset_id>/delete", methods=["POST"])
@admin_required
@module_required("assets")
def asset_delete(employee_id, asset_id):
    flash("Bedrijfsmiddelen worden nu centraal beheerd. Neem het middel daar retour of verwijder het daar.", "info")
    return redirect(url_for("asset_detail", asset_id=asset_id))


@app.route("/employees/<employee_id>/assets/<asset_id>/reveal/<field>", methods=["POST"])
@admin_required
@module_required("assets")
def asset_secret_reveal(employee_id, asset_id, field):
    return asset_central_secret_reveal(asset_id, field)


# ---------- Opleidingen / registraties ----------


@app.route("/employees/<employee_id>/registrations/add", methods=["POST"])
@admin_required
@module_required("registrations")
@synchronized
def registration_add(employee_id):
    employee = find_employee(employee_id)
    if not employee:
        abort(404)
    reg_type = _resolve_choice_field(request.form, "registration_type")
    if not reg_type:
        flash("Vul een type opleiding of registratie in.", "error")
        return redirect(url_for("employee_detail", employee_id=employee_id, tab="registrations"))

    regs = load_registrations()
    regs.append({
        "id": str(uuid.uuid4()),
        "employee_id": employee_id,
        "type": reg_type,
        "number": request.form.get("number", "").strip(),
        "institution": request.form.get("institution", "").strip(),
        "obtained_at": request.form.get("obtained_at", "").strip(),
        "expires_at": request.form.get("expires_at", "").strip(),
        "warning_days": request.form.get("warning_days", "").strip(),
        "status": "Actief",
        "notes": request.form.get("registration_notes", "").strip(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    })
    save_registrations(regs)
    log_action("registration_created", detail=reg_type, target=employee_id)
    flash("Registratie toegevoegd.", "success")
    return redirect(url_for("employee_detail", employee_id=employee_id, tab="registrations"))


@app.route("/employees/<employee_id>/registrations/<registration_id>/delete", methods=["POST"])
@admin_required
@module_required("registrations")
@synchronized
def registration_delete(employee_id, registration_id):
    regs = [r for r in load_registrations() if r.get("id") != registration_id]
    save_registrations(regs)
    log_action("registration_deleted", target=registration_id)
    flash("Registratie verwijderd.", "success")
    return redirect(url_for("employee_detail", employee_id=employee_id, tab="registrations"))


# ---------- Extra velden ----------

@app.route("/employees/<employee_id>/custom-fields/save", methods=["POST"])
@admin_required
@synchronized
def custom_fields_save(employee_id):
    employees_list = load_employees()
    employee = find_employee(employee_id, employees_list)
    if not employee:
        return "Medewerker niet gevonden", 404
    normalize_employee(employee)

    settings = load_settings()
    for definition in settings.get("custom_field_definitions", []):
        employee["custom_fields"][definition["id"]] = request.form.get(
            f"custom_{definition['id']}", ""
        ).strip()

    save_employees(employees_list)
    log_action("custom_fields_saved", target=employee_id)
    flash("Extra gegevens opgeslagen.", "success")
    return redirect(url_for("employee_detail", employee_id=employee_id, tab="custom"))


@app.route("/settings/custom-fields/add", methods=["POST"])
@admin_required
@synchronized
def custom_field_definition_add():
    settings = load_settings()
    label = request.form.get("label", "").strip()
    section = request.form.get("section", "Extra gegevens").strip() or "Extra gegevens"
    field_type = request.form.get("field_type", "text").strip()

    if not label:
        flash("Geef het extra veld een naam.", "error")
        return redirect(request.referrer or url_for("dashboard"))

    field_id = uuid.uuid4().hex
    settings["custom_field_definitions"].append({
        "id": field_id,
        "label": label,
        "section": section,
        "type": field_type if field_type in {"text", "date", "number", "textarea"} else "text"
    })
    save_settings(settings)
    log_action("custom_field_definition_added", detail=label)
    flash(f"Extra veld '{label}' toegevoegd.", "success")
    return redirect(request.referrer or url_for("dashboard"))


# ---------- Medewerkersportal ----------

def load_time_entries():
    return load_module("time_entries")

def save_time_entries(data):
    save_module("time_entries", data)

def load_leave_requests():
    return load_module("leave_requests")

def save_leave_requests(data):
    save_module("leave_requests", data)

def leave_balance(employee_id):
    employee = find_employee(employee_id)
    if not employee:
        return {"entitlement": 0.0, "carryover": 0.0, "approved": 0.0, "pending": 0.0, "remaining": 0.0}
    employee = normalize_employee(employee)
    try:
        entitlement = float(employee.get("annual_leave_hours") or 0)
    except ValueError:
        entitlement = 0.0
    try:
        carryover = float(employee.get("carryover_leave_hours") or 0)
    except ValueError:
        carryover = 0.0

    approved = 0.0
    pending = 0.0
    for req in load_leave_requests():
        if req.get("employee_id") != employee_id:
            continue
        try:
            hours = float(req.get("hours") or 0)
        except ValueError:
            hours = 0.0
        if req.get("status") == "Goedgekeurd":
            approved += hours
        elif req.get("status") == "In behandeling":
            pending += hours

    return {
        "entitlement": entitlement,
        "carryover": carryover,
        "approved": approved,
        "pending": pending,
        "remaining": entitlement + carryover - approved
    }


@app.route("/mijn")
@login_required
@module_required("employee_portal")
def employee_portal():
    employee = get_employee_for_current_user()
    if not employee:
        if is_admin():
            return redirect(url_for("dashboard"))
        return "Geen medewerker gekoppeld aan dit account.", 403

    entries = [x for x in load_time_entries() if x.get("employee_id") == employee["id"]]
    entries.sort(key=lambda x: x.get("date",""), reverse=True)
    requests = [x for x in load_leave_requests() if x.get("employee_id") == employee["id"]]
    requests.sort(key=lambda x: x.get("created_at",""), reverse=True)
    balance = leave_balance(employee["id"])
    total_recent_hours = 0.0
    for item in entries[:8]:
        try:
            total_recent_hours += float(item.get("hours") or 0)
        except ValueError:
            pass
    return render_template("employee_portal.html", employee=employee, entries=entries[:8],
                           requests=requests[:8], balance=balance, total_recent_hours=total_recent_hours)


@app.route("/mijn/uren", methods=["GET","POST"])
@login_required
@synchronized
@module_required("hours")
def my_hours():
    employee = get_employee_for_current_user()
    if not employee:
        return redirect(url_for("dashboard"))

    entries = load_time_entries()
    if request.method == "POST":
        date_value = request.form.get("date","")
        hours = request.form.get("hours","").strip()
        category = request.form.get("category","Gewerkt")
        description = request.form.get("description","").strip()

        try:
            hv = float(hours)
            if hv <= 0 or hv > 24:
                raise ValueError
        except ValueError:
            flash("Vul een geldig aantal uren in.", "error")
            return redirect(url_for("my_hours"))

        try:
            date.fromisoformat(date_value)
        except ValueError:
            flash("Vul een geldige datum in.", "error")
            return redirect(url_for("my_hours"))

        entries.append({
            "id": str(uuid.uuid4()),
            "employee_id": employee["id"],
            "date": date_value,
            "hours": hours,
            "category": category,
            "description": description,
            "created_at": datetime.now().isoformat(timespec="seconds")
        })
        save_time_entries(entries)
        flash("Uren opgeslagen.", "success")
        return redirect(url_for("my_hours"))

    own = [x for x in entries if x.get("employee_id")==employee["id"]]
    own.sort(key=lambda x: x.get("date",""), reverse=True)
    return render_template("my_hours.html", employee=employee, entries=own)


@app.route("/mijn/uren/<entry_id>/delete", methods=["POST"])
@login_required
@synchronized
@module_required("hours")
def my_hours_delete(entry_id):
    employee = get_employee_for_current_user()
    if not employee:
        return redirect(url_for("dashboard"))
    entries = load_time_entries()
    entries = [x for x in entries if not (x.get("id")==entry_id and x.get("employee_id")==employee["id"])]
    save_time_entries(entries)
    flash("Urenregel verwijderd.", "success")
    return redirect(url_for("my_hours"))


@app.route("/mijn/verlof", methods=["GET","POST"])
@login_required
@synchronized
@module_required("leave")
def my_leave():
    employee = get_employee_for_current_user()
    if not employee:
        return redirect(url_for("dashboard"))

    requests = load_leave_requests()
    if request.method == "POST":
        start_date = request.form.get("start_date","")
        end_date = request.form.get("end_date","")
        hours = request.form.get("hours","").strip()
        notes = request.form.get("notes","").strip()

        try:
            hv = float(hours)
            if hv <= 0:
                raise ValueError
        except ValueError:
            flash("Vul een geldig aantal verlofuren in.", "error")
            return redirect(url_for("my_leave"))

        for value, label in [(start_date, "Startdatum"), (end_date, "Einddatum")]:
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    flash(f"{label} is geen geldige datum.", "error")
                    return redirect(url_for("my_leave"))

        requests.append({
            "id": str(uuid.uuid4()),
            "employee_id": employee["id"],
            "employee_name": f"{employee.get('first_name','')} {employee.get('last_name','')}".strip(),
            "start_date": start_date,
            "end_date": end_date,
            "hours": hours,
            "notes": notes,
            "status": "In behandeling",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "decided_at": "",
            "decided_by": ""
        })
        save_leave_requests(requests)
        flash("Verlofaanvraag ingediend.", "success")
        return redirect(url_for("my_leave"))

    own = [x for x in requests if x.get("employee_id")==employee["id"]]
    own.sort(key=lambda x: x.get("created_at",""), reverse=True)
    return render_template("my_leave.html", employee=employee, requests=own,
                           balance=leave_balance(employee["id"]))


@app.route("/leave-requests")
@admin_required
@module_required("leave")
def leave_requests_admin():
    requests = load_leave_requests()
    requests.sort(key=lambda x: (x.get("status") != "In behandeling", x.get("created_at","")), reverse=False)
    return render_template("leave_requests_admin.html", requests=requests)


@app.route("/leave-requests/<request_id>/<decision>", methods=["POST"])
@admin_required
@synchronized
@module_required("leave")
def leave_request_decide(request_id, decision):
    if decision not in {"approve","reject"}:
        return "Ongeldige actie", 400
    requests = load_leave_requests()
    req = next((x for x in requests if x.get("id")==request_id), None)
    if req:
        req["status"] = "Goedgekeurd" if decision=="approve" else "Afgewezen"
        req["decided_at"] = datetime.now().isoformat(timespec="seconds")
        req["decided_by"] = session.get("user_name","")
        save_leave_requests(requests)
        log_action(
            "leave_request_approved" if decision == "approve" else "leave_request_rejected",
            detail=req.get("employee_name", ""),
        )
        flash("Verlofaanvraag bijgewerkt.", "success")
    return redirect(url_for("leave_requests_admin"))


@app.route("/hours-admin")
@admin_required
@module_required("hours")
def hours_admin():
    entries = load_time_entries()
    employees = {e["id"]: normalize_employee(e) for e in load_employees()}
    for x in entries:
        emp = employees.get(x.get("employee_id"))
        x["employee_name"] = f"{emp.get('first_name','')} {emp.get('last_name','')}".strip() if emp else "Onbekend"
    entries.sort(key=lambda x: x.get("date",""), reverse=True)
    return render_template("hours_admin.html", entries=entries)


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "version": APP_VERSION,
        "app": "Praktijk Schitter Beheer",
        "instance": BASE_DIR.name,
        "environment": "render" if IS_RENDER else ("production" if IS_PRODUCTION else "local"),
        "persistent_storage": bool(os.environ.get("SCHITTER_STORAGE_DIR")),
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT") or os.environ.get("PSB_PORT", "5050"))
    host = "0.0.0.0" if IS_PRODUCTION else "127.0.0.1"
    print()
    print("Praktijk Schitter Beheer")
    print("---------------------------")
    print(f"Versie {APP_VERSION} · gestart {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · PID {os.getpid()}")
    if not IS_PRODUCTION:
        print(f"Open in je browser: http://127.0.0.1:{port}")
        print("Stoppen: Ctrl+C (of STOP_WINDOWS.bat als de app zonder consolevenster draait)")
    print()
    app.run(host=host, port=port, debug=False)
