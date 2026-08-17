# Praktijk Schitter Beheer

Versie 1.3.0 – Online Ready.

## Belangrijkste wijzigingen

- Nieuwe working title: **Praktijk Schitter Beheer**
- Applicatienaam en ondertitel zijn instelbaar in Instellingen
- Modulebeheer met aan/uit-schakelaars
- Uitschakelen wist geen gegevens
- Uitgeschakelde modules worden zowel in de UI als server-side geblokkeerd
- Standaard actief: Bedrijfsmiddelen, Accounts & codes, Registraties, Documenten en Signaleringen & taken
- Standaard uit: HR/dienstverband, Urenregistratie, Verlof en Medewerkersportaal
- Nieuwe volwaardige module **Accounts & codes**
  - persoonlijk, gedeeld of praktijkbreed
  - gebruikersnaam
  - wachtwoord/PIN/PUK/toegangscode/API-key/mail key/herstelcode
  - koppeling aan medewerker
  - URL en notities
  - geheimen worden pas na een expliciete server-call getoond
  - bekijken wordt gelogd in de auditlog

## Starten

Windows: `START_WINDOWS.bat`

macOS: `START_MAC.command`

De virtuele Python-omgeving wordt niet meegeleverd; de startscripts bouwen die lokaal op basis van `requirements.txt`.

## Productiestatus

Dit blijft een ontwikkelversie. Zet er nog geen echte productiegegevens in voordat hosting, sleutelbeheer, HTTPS/2FA en back-up/herstel definitief zijn ingericht.
\n\n## Nieuw in 0.5.2 — beveiligde setup en beheeracties\n\n- Eerste beheerder kan niet meer door de eerste willekeurige browserbezoeker worden aangemaakt.\n- Bij een lege installatie maakt de app lokaal `storage/EERSTE_START_CODE.txt` aan.\n- De eenmalige code uit dat bestand is verplicht bij het maken van de eerste beheerder.\n- Na succesvolle eerste configuratie wordt het codebestand automatisch verwijderd.\n- De `/setup`-route blijft gesloten zodra er een gebruiker bestaat.\n- Een nieuwe **beheerder** toevoegen vereist herbevestiging met het huidige wachtwoord van de ingelogde beheerder.\n- Een andere beheerder verwijderen of diens wachtwoord resetten vereist eveneens herbevestiging.\n- De bestaande bescherming tegen het verwijderen van de laatste beheerder blijft actief.\n- Nieuw release-script voor Windows en macOS; `.venv`, caches, logs, runtimegegevens en encryptiesleutels worden automatisch uitgesloten.\n\n### Eerste start\n\n1. Start de app.\n2. Open lokaal `storage/EERSTE_START_CODE.txt`.\n3. Vul de daarin vermelde code in op het eerste-configuratiescherm.\n4. Maak de eerste beheerder aan.\n5. Het setup-codebestand wordt daarna automatisch verwijderd.\n

## Nieuw in 0.5.2 – Modulebewust medewerkersdossier

Het medewerkersdossier volgt nu de modules die in Instellingen zijn ingeschakeld.

- HR / dienstverband uit:
  - tab Dienstverband verdwijnt
  - HR-velden verdwijnen uit het bewerkscherm en de samenvatting
- Bedrijfsmiddelen uit:
  - tab en dossierkaart verdwijnen
  - directe module-acties zijn server-side geblokkeerd
- Accounts & codes aan:
  - nieuw tabblad per medewerker met de persoonlijk gekoppelde accounts
  - geheimen worden daar niet automatisch onthuld; tonen gebeurt via de centrale beveiligde module
- Registraties en Documenten:
  - tabbladen en dossierkaarten volgen de moduleschakelaar
- Verlof uit:
  - verlofrechtvelden verdwijnen uit het medewerkerformulier
- Een handmatig opgevraagde uitgeschakelde tab wordt geweigerd en teruggeleid naar Overzicht.

Uitschakelen verwijdert geen opgeslagen gegevens. Bij opnieuw inschakelen worden de bestaande gegevens weer zichtbaar.


## Nieuw in 0.5.2 – Centrale bedrijfsmiddelen

Bedrijfsmiddelen zijn nu zelfstandige objecten in een centrale, versleutelde inventaris.

- Centrale inventaris (`assets.enc`)
- Statussen: Vrij, Uitgegeven, Reparatie, Afgeschreven en Kwijt
- Zoeken en filteren op status/type/medewerker/serienummer
- Middelen toevoegen zonder ze direct aan een medewerker te koppelen
- Uitgeven aan een medewerker
- Retour nemen en vervolgstatus kiezen
- Volledige uitgiftehistorie per middel
- Verwachte retourdatum
- Medewerkersdossier toont alleen de momenteel toegewezen middelen
- PIN/PUK blijven beveiligde velden
- Auditlog voor aanmaken, wijzigen, uitgeven, retour en onthullen van geheimen
- Bestaande middelen uit oudere medewerkersdossiers worden bij eerste start automatisch gemigreerd naar de centrale inventaris
- Bij migratie blijven toewijzing en bekende uitgiftedata behouden

Uitschakelen van de module Bedrijfsmiddelen verwijdert de inventaris niet.


## Nieuw in 0.5.2 – Module-instellingen hotfix

- Moduleschakelaars slaan nu direct op zodra ze worden omgezet.
- Na succesvol opslaan wordt de pagina automatisch opnieuw opgebouwd zodat menu en dossier direct veranderen.
- Module-instellingen blijven na uitloggen, opnieuw inloggen en herstarten behouden.
- Het opslaan van algemene praktijkinstellingen kan modules niet meer per ongeluk terugzetten omdat checkboxvelden ontbreken.
- Bij een fout wordt de schakelaar teruggezet naar de vorige toestand en verschijnt een foutmelding.
- Autosave gebruikt dezelfde CSRF-bescherming via een beveiligde request-header.


## Nieuw in 0.5.2 – definitieve modulepersistentie + dynamische login

De moduleschakelaars zijn technisch losgekoppeld van de overige applicatie-instellingen.

- Modulekeuzes staan voortaan apart in versleuteld `modules.enc`.
- Bij eerste start van v0.5.2 worden bestaande modulekeuzes uit `settings.enc` automatisch gemigreerd.
- Algemene instellingen kunnen modulekeuzes niet langer overschrijven.
- Elke moduleschakelaar schrijft rechtstreeks naar `modules.enc`.
- Na opslaan wordt de waarde opnieuw van schijf gelezen voordat de app succes terugmeldt.
- De modulekeuze blijft daardoor behouden bij navigeren, uitloggen, opnieuw inloggen en herstarten.
- Login, eerste setup en uitnodigingsscherm gebruiken voortaan de ingestelde applicatienaam.
- Het vaste opschrift `Personeelsadministratie` is van het inlogscherm verwijderd.


## Nieuw in 0.5.3 – moduleschakelaars opnieuw opgebouwd

De AJAX/autosave-implementatie uit 0.5.1/0.5.2 is verwijderd. Die bleek door foutieve scriptinjectie niet betrouwbaar.

- Iedere moduleschakelaar is nu een zelfstandig normaal HTML POST-formulier.
- Een klik post exact één gewenste waarde (`0` of `1`) naar de server.
- De server schrijft naar het aparte versleutelde `modules.enc`.
- De server leest `modules.enc` daarna opnieuw en controleert de opgeslagen waarde.
- Daarna volgt een gewone redirect naar Instellingen; er is geen JavaScript-fetch of timing meer nodig.
- Hierdoor werkt de switch ook betrouwbaar bij herladen, uitloggen en herstarten.
- Het versienummer is gecentraliseerd in `APP_VERSION` en de footer leest voortaan diezelfde waarde.


## Nieuw in 0.5.4 – betrouwbare instantie-/versiecontrole

Er kon ongemerkt een oudere verborgen Python-server op poort 5050 blijven draaien.
Daardoor opende een nieuwe uitgepakte versie in de browser soms feitelijk de oude applicatie.

Dit is nu opgelost:
- START_WINDOWS.bat en START_MAC.command zoeken eerst een vrije poort (5050–5099).
- Als 5050 bezet is, wordt duidelijk gemeld dat waarschijnlijk nog een oude instantie actief is.
- De nieuwe versie start dan op een andere vrije poort.
- Vóór de browser opent, wordt `/api/health` gecontroleerd.
- Alleen als die exact versie 0.5.4 meldt, wordt de browser geopend.
- De actieve poort en PID worden in de lokale storage-map vastgelegd.
- STOP_WINDOWS.bat en STOP_MAC.command stoppen alleen de server die vanuit die specifieke map is gestart.
- Het versienummer in de footer komt uit dezelfde centrale APP_VERSION als de health-check.


## Nieuw in 0.6.0

### Lokale server netjes afsluiten
Wanneer de app via START_WINDOWS.bat of START_MAC.command lokaal is gestart:
- Uitloggen beëindigt de sessie én sluit de lokale server.
- De gebruikte poort komt daardoor weer vrij.
- Op een toekomstige gehoste installatie sluit uitloggen alleen de sessie; de server blijft dan uiteraard draaien.

### Dashboard volledig modulebewust
- Bedrijfsmiddelen uit = kaart én kolom verdwijnen ook van Dashboard.
- HR uit = contractkaart en HR-kolommen verdwijnen.
- Documenten uit = Recente documenten verdwijnt.
- Registraties/signalen uit = bijbehorende aandachtspunten verdwijnen.

### Centrale registraties
- Registraties staan voortaan in een eigen versleuteld `registrations.enc`.
- Bestaande registraties worden automatisch gemigreerd.
- Centraal overzicht voor alle medewerkers.
- Status Actief / Loopt af / Verlopen / Inactief.
- Waarschuwingstermijn per registratie, met terugval op de algemene standaard.
- Zoeken en filteren.
- Centrale wijzigingen en verwijdering.
- Medewerkersdossier toont alleen de registraties van die medewerker.


## Nieuw in 0.6.1 – Registraties hotfix

- Crash bij openen van Registraties opgelost.
- Oorzaak: `registrations.html` gebruikte de Jinja-macro `choice_field` zonder deze uit `_macros.html` te importeren.
- Versiecontrole en startscripts bijgewerkt naar 0.6.1.


## Nieuw in 0.7.0 – Accounts & codes als volwaardig register

- Persoonlijke accounts: gekoppeld aan precies één medewerker.
- Gedeelde accounts: gekoppeld aan meerdere medewerkers.
- Praktijkbrede accounts/sleutels: zonder medewerkerkoppeling.
- Categorieën: Account, Technische sleutel, Licentie, Toegangscode, Overig.
- Geheimsoorten uitgebreid met o.a. Licentiesleutel.
- Filteren op scope en soort geheim.
- Accountgegevens wijzigen zonder het bestaande geheim zichtbaar te maken.
- Geheim vervangen door alleen een nieuwe waarde in te vullen.
- Geheim tonen en geheim kopiëren worden afzonderlijk in de auditlog vastgelegd.
- Bestaande credentials worden automatisch compatibel gemaakt met de nieuwe medewerkerkoppelingen.
- Medewerkersdossier toont zowel persoonlijke als gedeelde accounts waaraan de medewerker is gekoppeld.


# Praktijk Schitter Beheer 1.0.0

Dit is de eerste feature-complete lokale release.

## Kernmodules
- Medewerkers
- Bedrijfsmiddelen met centrale inventaris, toewijzing, retour en historie
- Accounts & codes met persoonlijk/gedeeld/praktijkbreed bereik
- Registraties met verloopbewaking en waarschuwingstermijnen
- Versleutelde documenten
- Taken & signaleringen
- Auditlog
- Back-updownload
- Modulebeheer
- Dynamische applicatienaam en ondertitel
- Configureerbare keuzelijsten

## Nieuw voor 1.0
- Dashboard combineert signaleringen uit registraties, bedrijfsmiddelen, taken en optioneel HR.
- Retourdata en garantiedata van bedrijfsmiddelen kunnen op het dashboard signaleren.
- Verlopen registraties worden als urgent aandachtspunt getoond.
- Type bedrijfsmiddel, categorie account/code en soort geheim zijn vanuit Instellingen te beheren.
- `SELFTEST.py` controleert Python, Jinja-templates, kernroutes en releaseversie.
- `SELFTEST_WINDOWS.bat` en `SELFTEST_MAC.command` maken de zelftest met één klik uitvoerbaar.

## Scope 1.0
HR, verlof, urenregistratie en het medewerkersportaal blijven optionele modules. Ze zijn geen onderdeel van de primaire 1.0-workflow en staan standaard uit.

## Voor echt internetgebruik
De lokale 1.0 is bedoeld voor gebruik in een afgeschermde omgeving. Voor publicatie op een eigen domein zijn HTTPS, serverbeheer en bij voorkeur een centrale database de volgende infrastructuurstap.


## Nieuw in 1.1.0 – Actiecentrum & eerste workflow

- Nieuw centraal Actiecentrum.
- Automatische signaleringen uit:
  - verlopen/bijna verlopen registraties;
  - bedrijfsmiddelen in Reparatie/Kwijt;
  - retourdata van bedrijfsmiddelen;
  - aflopende garanties;
  - handmatige taken;
  - optionele contractsignalen.
- Acties worden op urgentie gesorteerd.
- Uitdienstworkflow per medewerker.
- Checklist wordt automatisch opgebouwd uit toegewezen bedrijfsmiddelen, gekoppelde accounts/toegangen en registraties.
- Standaard controlepunten worden automatisch toegevoegd.
- Checklist kan stap voor stap worden afgevinkt en sluit automatisch af bij 100%.
- Open workflows verschijnen in het Actiecentrum.


## 1.1.1 – Startscript hotfix

- Windows- en Mac-startscripts volledig opnieuw opgebouwd.
- Loopbackadres expliciet vastgezet op `127.0.0.1`.
- Fout opgelost waarbij een automatische versievervanging `127.0.0.1` kon verminken tot `127.1.1.0`.
- Starttekst gebruikt nu dezelfde `EXPECTED_VERSION` als de health-check.
- Health-check probeert tot 10 seconden, zodat een wat tragere eerste start niet onterecht als fout wordt gemeld.
- SELFTEST controleert voortaan expliciet of het loopbackadres intact is.
- Generieke zoek/vervang-logica voor versienummers wordt niet meer gebruikt voor startscriptinhoud.


## 1.2.0 – Indienst & configureerbare workflows
- Indienstworkflow per medewerker.
- Eigen startdatum per workflow.
- Automatische aanvulling met reeds gekoppelde bedrijfsmiddelen, accounts/toegangen en registraties.
- Indienst- en uitdienstsjablonen zijn in Instellingen zelf te beheren.
- Stappen kunnen worden toegevoegd, verwijderd en in volgorde worden gewijzigd.
- Een gestart traject blijft ongewijzigd als het sjabloon later wordt aangepast.
- Indienst- en uitdiensttrajecten verschijnen samen in het Actiecentrum.


## 1.2.1 – Workflow hotfix
- Internal Server Error bij starten van zowel indienst- als uitdienstworkflow opgelost.
- Oorzaak: Jinja interpreteerde `workflow.items` als de Python-methode `dict.items()` in plaats van als de checklistlijst.
- Workflowtemplates gebruiken nu expliciet `workflow['items']`.
- SELFTEST controleert voortaan op deze specifieke Jinja-valkuil.


## 1.3.0 – Online Ready

- Dezelfde app draait lokaal én op Render.
- Opslagmap is instelbaar via `SCHITTER_STORAGE_DIR`; lokaal blijft `storage/` de standaard.
- `render.yaml` toegevoegd voor een Render Blueprint met Persistent Disk.
- Gunicorn toegevoegd voor productiehosting.
- Productie gebruikt HTTPS-only sessiecookies.
- Git-veilige `.gitignore`: personeelsgegevens, encryptiesleutels, logs en secrets worden uitgesloten.
- Handleiding `ONLINE_MET_RENDER.md` toegevoegd.
- Windows-startscript ruimt achtergebleven lokale Praktijk Schitter-processen op voordat een nieuwe versie start.
- Nieuw `STOP_ALLE_PSB_WINDOWS.bat` om achtergebleven PSB-servers in één keer te stoppen.
- Hardcoded oud versienummer in de consolemelding verwijderd; console gebruikt voortaan `APP_VERSION`.

## 1.3.1 – veilige online eerste inrichting
- Online eerste beheerder vereist `SCHITTER_SETUP_CODE` uit Render Environment.
- Zonder die secret blijft de setup veilig gesloten.
- Lokaal blijft de bestaande eerste-startcode werken.
