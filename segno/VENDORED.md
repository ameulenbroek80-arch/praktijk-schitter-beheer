Deze map bevat de broncode van het pure-Python pakket "segno" (QR-code
generator), hier bewust *meegeleverd* in de projectmap in plaats van als
pip-dependency in requirements.txt.

Reden: START_WINDOWS.bat installeert requirements.txt alleen bij de
allereerste start (wanneer .venv nog niet bestaat). Een nieuwe regel in
requirements.txt zou dus niet vanzelf bij bestaande installaties belanden.
Door de broncode hier direct neer te zetten werkt de tweefactorauthenticatie
(QR-code tonen) meteen, zonder dat André `.venv` hoeft te verwijderen of
handmatig `pip install` hoeft te draaien.

Herkomst: PyPI-pakket "segno" versie 1.6.6, door Lars Heuer, BSD-3-Clause
licentie (zie LICENSE in deze map). Alleen de kernmodules zijn meegenomen
(niet cli.py, dat command-line gereedschap is dat deze app niet gebruikt).
Ongewijzigde originele broncode - geen aanpassingen gedaan.
