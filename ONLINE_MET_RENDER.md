# Praktijk Schitter Beheer 1.3 – online zetten via GitHub + Render

Deze versie kan **lokaal blijven draaien zoals voorheen** én is voorbereid voor Render.

## Wat verandert er online?

De programmacode staat in GitHub. Render haalt die code op en draait de app.
De echte praktijkgegevens staan **niet in GitHub**. Ze komen op een Render Persistent Disk.

Schema:

GitHub (alleen code) → Render webservice → `/var/data` (permanente, versleutelde gegevens)

## Belangrijk vóór echte gegevens

1. Gebruik een **betaalde Render webservice met Persistent Disk**. Zonder Persistent Disk verdwijnen lokale bestanden bij een nieuwe deployment.
2. Gebruik maar **één webservice-instance** en één Gunicorn worker. De huidige bestandsopslag is bewust eenvoudig en niet bedoeld voor meerdere serverinstances tegelijk.
3. Maak na de eerste inrichting via **Instellingen → Back-up downloaden** regelmatig een back-up en bewaar die buiten Render.
4. Zet geen `.enc`, `.key`, `users.json`, setupcodes of `.env` in Git. `.gitignore` beschermt hiertegen, maar controleer altijd wat je commit.
5. Gebruik voor echte praktijkgegevens HTTPS. Render levert HTTPS op het Render-adres en ondersteunt later een eigen domein.

## GitHub – eerste keer

Maak een **private repository** aan, bijvoorbeeld `praktijk-schitter-beheer`.

Open in deze programmamap een terminal:

    git init
    git add .
    git status

Controleer bij `git status`: je hoort GEEN bestanden uit `storage` met persoonsgegevens of sleutels te zien.

Daarna:

    git commit -m "Praktijk Schitter Beheer v1.3.0"
    git branch -M main
    git remote add origin <jouw-private-repository>
    git push -u origin main

## Render – eerste keer

De makkelijkste route is de `render.yaml` in deze map (Render Blueprint).

1. In Render: **New → Blueprint**.
2. Koppel de private GitHub-repository.
3. Render leest `render.yaml`.
4. Controleer dat er een Persistent Disk `praktijk-schitter-data` op `/var/data` wordt aangemaakt.
5. Deploy.

De app gebruikt online automatisch:
- `SCHITTER_STORAGE_DIR=/var/data`
- `SCHITTER_ENV=production`
- een door Render gemaakte `SCHITTER_SESSION_SECRET`
- HTTPS-only sessiecookies
- Gunicorn als productieserver

## Eerste beheerder online

Bij de allereerste start maakt de app een eerste-startcode in:

    /var/data/EERSTE_START_CODE.txt

Die code moet veilig uit de Render disk/logische beheeromgeving worden opgehaald voordat de eerste beheerder kan worden aangemaakt. **Zet deze code nooit in Git.**

Voor de eerste echte productie-inrichting kunnen we dit bootstrap-proces in een volgende security-stap nog gebruiksvriendelijker maken met een eenmalige Render environment variable.

## Eigen domein

Als alles op het tijdelijke Render-adres goed werkt, kan daarna bijvoorbeeld
`beheer.jouwdomein.nl` aan de Render-webservice worden gekoppeld.

## Lokaal

Windows: `START_WINDOWS.bat`
Mac: `START_MAC.command`

Windows extra:
`STOP_ALLE_PSB_WINDOWS.bat` stopt achtergebleven lokale PSB-servers op poort 5050–5099, maar alleen als het Python-processen zijn die `app.py` draaien.
