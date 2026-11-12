"""
Vorlage 2 („Baubotters – Navy & Amber“): Navy-Kopfband, Amber-Endbetrag, Betreff-Konvention im
Einleitungstext, Mehrseitigkeit, alle Dokumenttypen, Anbindung an PDF-Endpunkt, Vorlagenliste,
Demo-Vorschau und E-Rechnung (ZUGFeRD).
"""
import re
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from pypdf import PdfReader
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.pdf_vorlagen import demo_pdf, list_vorlagen
from api.rechnungen import _absender_snapshot, create_rechnung, rechnung_als_pdf
from api.schemas_rechnungen import RechnungCreate, RechnungspositionCreate
from database.connection import Base
from database.models import Journaleintrag, Kunde, Rechnung, Rechnungsposition, Unternehmen
from utils.pdf_rechnung_vorlage2 import (
    RechnungPDFVorlage2, _betreff_aufteilen, _fmt_iban, _fmt_menge, generate_rechnung_pdf_vorlage2,
)

UNT = {
    "firmenname": "Baubotters", "vorname": "Daniel", "nachname": "Nobs",
    "strasse": "Musterweg", "hausnummer": "1", "plz": "78073", "ort": "Bad Dürrheim",
    "land": "DE", "ust_idnr": "DE111111111", "steuernummer": "12/345/67890",
    "iban": "DE89370400440532013000", "bic": "COBADEFFXXX", "bank_name": "Beispielbank",
    "telefon": "", "email": "rechnung@example.com", "webseite": "", "ist_kleinunternehmer": False,
    "rechtsform": "Einzelunternehmen", "qr_zahlung_aktiv": False, "anrede_text": "Sehr geehrte Damen und Herren,",
}


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _text(pdf_bytes: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(BytesIO(pdf_bytes)).pages)


def _kompakt(text: str) -> str:
    """Gesperrt gesetzter Text (Kopfband) wird von pypdf evtl. mit Leerzeichen extrahiert."""
    return "".join(text.split())


def _rechnung(db, positionen=None, **felder) -> Rechnung:
    kunde = Kunde(firmenname="Unmüssig", z_hd="Friedrich Beispiel", strasse="Beispielstraße",
                  hausnummer="7", plz="68523", ort="Weinheim", land="DE")
    db.add(kunde)
    db.flush()
    positionen = positionen or [
        ("Zeichnerische Bearbeitung\nVektorgrundrisse für 28 Wohnungen", "27.5", "h", "60", "19"),
        ("Rendering\nVisualisierung für das Exposé, pauschal", "1", "psch.", "800", "19"),
        ("Erstauftragsrabatt", "1", "", "-500", "19"),
    ]
    netto = ust = Decimal("0")
    zeilen = []
    for nr, (beschr, menge, einheit, preis, satz) in enumerate(positionen, start=1):
        n = Decimal(menge) * Decimal(preis)
        u = (n * Decimal(satz) / 100).quantize(Decimal("0.01"))
        netto += n
        ust += u
        zeilen.append(Rechnungsposition(
            position_nr=nr, beschreibung=beschr, menge=Decimal(menge), einheit=einheit, netto=Decimal(preis),
            ust_satz=Decimal(satz), ust_betrag=u, brutto=n + u,
        ))
    basis = dict(
        typ="ausgang", rechnungsnummer="RE-2026-0001", datum=date(2026, 11, 10),
        leistung_von=date(2026, 10, 30), leistung_bis=date(2026, 11, 5), faellig_am=date(2026, 11, 24),
        kunde_id=kunde.id, netto_gesamt=netto, ust_gesamt=ust, brutto_gesamt=netto + ust, ist_entwurf=False,
    )
    basis.update(felder)
    rechnung = Rechnung(**basis)
    db.add(rechnung)
    db.flush()
    for z in zeilen:
        z.rechnung_id = rechnung.id
        db.add(z)
    db.commit()
    db.refresh(rechnung)
    return rechnung


# --- Hilfsfunktionen ------------------------------------------------------------------------------

def test_fmt_menge():
    assert _fmt_menge(Decimal("27.5")) == "27,5"
    assert _fmt_menge(Decimal("10")) == "10"
    assert _fmt_menge(Decimal("0.2500")) == "0,25"
    assert _fmt_menge(Decimal("100")) == "100"


def test_fmt_iban():
    assert _fmt_iban("DE89370400440532013000") == "DE89 3704 0044 0532 0130 00"
    assert _fmt_iban("DE89 3704 0044 0532 0130 00") == "DE89 3704 0044 0532 0130 00"
    assert _fmt_iban("") == ""


def test_betreff_aufteilen():
    assert _betreff_aufteilen("# Betreff\n## Unter\n\nText") == ("Betreff", "Unter", "Text")
    assert _betreff_aufteilen("# Nur Betreff\nText") == ("Nur Betreff", "", "Text")
    assert _betreff_aufteilen("Kein Betreff\n## nicht als Unterzeile") == ("", "", "Kein Betreff\n## nicht als Unterzeile")
    assert _betreff_aufteilen("") == ("", "", "")


# --- Inhalt ---------------------------------------------------------------------------------------

def test_rechnung_zeigt_band_meta_positionen_und_summen(db):
    r = _rechnung(db)
    text = _text(generate_rechnung_pdf_vorlage2(r, UNT, ist_netto=True))
    assert "RECHNUNG" in _kompakt(text)
    for erwartet in ("RE-2026-0001", "10.11.2026", "30.10. – 05.11.2026", "24.11.2026", "Unmüssig",
                     "z.Hd. Friedrich Beispiel", "Zeichnerische Bearbeitung", "27,5 h", "60,00 €",
                     "1.650,00 €", "800,00 €", "−500,00 €", "1.950,00 €", "370,50 €", "2.320,50 €",
                     "Rechnungsbetrag", "DE89 3704 0044 0532 0130 00", "Steuernr.: 12/345/67890"):
        assert erwartet in text, erwartet
    assert text.count("Nettobetrag") == 1      # Summenblock genau einmal


def test_betreff_konvention_im_einleitungstext(db):
    r = _rechnung(db, einleitungstext="# Exposé Haus 8.2\n## Fichtestraße 33/35\n\nvielen Dank für den Auftrag.")
    text = _text(generate_rechnung_pdf_vorlage2(r, UNT))
    assert "Exposé Haus 8.2" in text
    assert "Fichtestraße 33/35" in text
    assert "vielen Dank für den Auftrag." in text
    assert "# Exposé" not in text and "## Fichtestraße" not in text


def test_zahlungshinweis_mit_faelligkeit_und_nummer(db):
    text = _text(generate_rechnung_pdf_vorlage2(_rechnung(db), UNT))
    assert "bis zum 24.11.2026" in text
    assert "RE-2026-0001" in text
    assert "ohne Abzug" in text


def test_eigener_zahlungshinweis_mit_platzhaltern(db):
    unt = {**UNT, "zahlungshinweis_text": "Bitte {betrag} bis {faellig_am} auf {iban} zahlen ({rechnungsnummer})."}
    text = _text(generate_rechnung_pdf_vorlage2(_rechnung(db), unt))
    assert "Bitte 2.320,50 € bis 24.11.2026 auf DE89 3704 0044 0532 0130 00 zahlen (RE-2026-0001)." in text


def test_kleinunternehmer_brutto_ohne_ust_zeilen(db):
    r = _rechnung(db, positionen=[("Webdesign", "1", "Pauschal", "800", "0")])
    text = _text(generate_rechnung_pdf_vorlage2(r, {**UNT, "ist_kleinunternehmer": True}, ist_netto=False))
    assert "Gemäß § 19 UStG wird keine Umsatzsteuer berechnet." in text
    assert "zzgl." not in text
    assert "BRUTTO" in text and "800,00 €" in text


def test_gemischte_steuersaetze_zeigen_ust_spalte_und_zeilen(db):
    r = _rechnung(db, positionen=[("Leistung A", "1", "Stk", "100", "19"), ("Leistung B", "1", "Stk", "100", "7")])
    text = _text(generate_rechnung_pdf_vorlage2(r, UNT, ist_netto=True))
    assert "zzgl. 19 % Umsatzsteuer" in text and "zzgl. 7 % Umsatzsteuer" in text
    assert "USt" in text and "19 %" in text and "7 %" in text


def test_bezahlte_rechnung_zeigt_zahlungseingang(db):
    r = _rechnung(db, zahlungsstatus="bezahlt", bezahlt_betrag=Decimal("2320.50"))
    db.add(Journaleintrag(
        datum=date(2026, 11, 20), belegnr="B1", beschreibung="Zahlung", zahlungsart="Bank", art="Einnahme",
        netto_betrag=Decimal("1950.00"), ust_satz=Decimal("19"), ust_betrag=Decimal("370.50"),
        brutto_betrag=Decimal("2320.50"), rechnung_id=r.id,
    ))
    db.commit()
    db.refresh(r)
    text = _text(generate_rechnung_pdf_vorlage2(r, UNT))
    assert "Dankend erhalten am 20.11.2026" in text
    assert "bis zum 24.11.2026" not in text


def test_eingangsrechnung_ohne_zahlungsaufforderung(db):
    from database.models import Lieferant
    lief = Lieferant(firmenname="Lieferant AG")
    db.add(lief)
    db.flush()
    r = _rechnung(db, typ="eingang", kunde_id=None, lieferant_id=lief.id)
    text = _text(generate_rechnung_pdf_vorlage2(r, UNT))
    assert "EINGANGSRECHNUNG" in _kompakt(text)
    assert "Bitte überweisen" not in text


@pytest.mark.parametrize("dokument_typ, erwartet", [
    ("Angebot", "ANGEBOT"), ("Auftrag", "AUFTRAGSBESTÄTIGUNG"), ("Gutschrift", "GUTSCHRIFT"),
    ("Proforma", "PROFORMA-RECHNUNG"), ("Lieferschein", "LIEFERSCHEIN"), ("Abschlagsrechnung", "ABSCHLAGSRECHNUNG"),
])
def test_dokumenttyp_im_kopfband(db, dokument_typ, erwartet):
    r = _rechnung(db, dokument_typ=dokument_typ)
    assert erwartet in _kompakt(_text(generate_rechnung_pdf_vorlage2(r, UNT)))


def test_storno_zeigt_bezug_und_negative_summen(db):
    r = _rechnung(db, storniert=True, storno_rechnungsnummer="ST-1", storno_grund="Irrtum")
    text = _text(generate_rechnung_pdf_vorlage2(r, UNT))
    assert "STORNORECHNUNG" in _kompakt(text)
    assert "Stornierung zu Rechnung RE-2026-0001" in text
    assert "Stornobegründung: Irrtum" in text
    assert "−2.320,50 €" in text


def test_entwurf_und_kopie_markierung(db):
    r = _rechnung(db)
    assert "ENTWURF" in _kompakt(_text(generate_rechnung_pdf_vorlage2(r, UNT, ist_entwurf=True)))
    assert "KOPIE" in _kompakt(_text(generate_rechnung_pdf_vorlage2(r, UNT, ist_kopie=True)))


def _png_unterschrift() -> str:
    """Kleine transparente Test-Unterschrift als data-URL."""
    import base64
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (400, 140), (0, 0, 0, 0))
    ImageDraw.Draw(im).line([(10, 120), (120, 20), (200, 110), (390, 30)], fill=(20, 24, 40, 255), width=6)
    buf = BytesIO()
    im.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _seiten_mit_bildern(pdf: bytes) -> int:
    return sum(len(list(seite.images)) for seite in PdfReader(BytesIO(pdf)).pages)


def test_unterschriftsblock_gruss_unterschrift_name_titel(db):
    r = _rechnung(db, schlusstext="Vielen Dank.\n\nMit freundlichen Grüßen\n\n**Daniel Nobs**")
    unt = {**UNT, "berufsbezeichnung": "Gründer & Geschäftsführer",
           "unterschrift_bild": _png_unterschrift(), "unterschrift_auf_rechnung": True}
    pdf = generate_rechnung_pdf_vorlage2(r, unt)
    text = _text(pdf)
    assert len(PdfReader(BytesIO(pdf)).pages) == 1
    assert _seiten_mit_bildern(pdf) == 1                      # Unterschrift genau einmal (nicht zusätzlich unten)
    assert text.count("Daniel Nobs") >= 1
    i_gruss, i_name, i_titel = (text.index(x) for x in ("Mit freundlichen Grüßen", "Daniel Nobs", "Gründer & Geschäftsführer"))
    assert i_gruss < i_name < i_titel


def test_titel_ohne_unterschrift_und_ohne_namenszeile(db):
    r = _rechnung(db, schlusstext="Vielen Dank für Ihren Auftrag.")
    unt = {**UNT, "berufsbezeichnung": "Gründer & Geschäftsführer"}
    pdf = generate_rechnung_pdf_vorlage2(r, unt)
    text = _text(pdf)
    assert _seiten_mit_bildern(pdf) == 0
    assert text.index("Vielen Dank für Ihren Auftrag.") < text.index("Daniel Nobs") < text.index("Gründer & Geschäftsführer")


def test_unterschrift_nur_wenn_aktiviert_und_nicht_bei_lieferschein(db):
    r = _rechnung(db, schlusstext="Mit freundlichen Grüßen\n\n**Daniel Nobs**")
    aus = {**UNT, "unterschrift_bild": _png_unterschrift(), "unterschrift_auf_rechnung": False}
    assert _seiten_mit_bildern(generate_rechnung_pdf_vorlage2(r, aus)) == 0
    an = {**aus, "unterschrift_auf_rechnung": True}
    assert _seiten_mit_bildern(generate_rechnung_pdf_vorlage2(r, an)) == 1
    ls = _rechnung(db, dokument_typ="Lieferschein", rechnungsnummer="LS-1",
                   schlusstext="Mit freundlichen Grüßen\n\n**Daniel Nobs**")
    assert _seiten_mit_bildern(generate_rechnung_pdf_vorlage2(ls, an)) == 0


def test_unterschriftsblock_bleibt_zusammen_bei_langer_rechnung(db):
    positionen = [(f"Leistung {i}\nBeschreibung {i}", "1", "Std", "50", "19") for i in range(1, 31)]
    r = _rechnung(db, positionen=positionen, schlusstext="Mit freundlichen Grüßen\n\n**Daniel Nobs**")
    unt = {**UNT, "berufsbezeichnung": "Gründer & Geschäftsführer",
           "unterschrift_bild": _png_unterschrift(), "unterschrift_auf_rechnung": True}
    pdf = generate_rechnung_pdf_vorlage2(r, unt)
    seiten = PdfReader(BytesIO(pdf)).pages
    letzte = seiten[-1].extract_text()
    assert "Mit freundlichen Grüßen" in letzte and "Daniel Nobs" in letzte and "Gründer & Geschäftsführer" in letzte
    assert _seiten_mit_bildern(pdf) == 1


def test_helles_logo_ohne_weisse_flaeche(db, tmp_path):
    from PIL import Image
    hell = tmp_path / "hell.png"
    Image.new("RGBA", (200, 40), (255, 255, 255, 255)).save(hell)
    dunkel = tmp_path / "dunkel.png"
    Image.new("RGBA", (200, 40), (10, 10, 10, 255)).save(dunkel)
    foto = tmp_path / "foto.jpg"
    Image.new("RGB", (200, 40), (250, 250, 250)).save(foto)
    assert RechnungPDFVorlage2._logo_ist_hell(hell) is True
    assert RechnungPDFVorlage2._logo_ist_hell(dunkel) is False
    assert RechnungPDFVorlage2._logo_ist_hell(foto) is False      # nicht transparent -> weiße Fläche behalten


def test_lange_rechnung_ueber_mehrere_seiten(db):
    positionen = [(f"Leistung {i}\nBeschreibung zu Leistung {i}", "1", "Std", "50", "19") for i in range(1, 41)]
    r = _rechnung(db, positionen=positionen)
    pdf = generate_rechnung_pdf_vorlage2(r, UNT)
    seiten = PdfReader(BytesIO(pdf)).pages
    assert len(seiten) >= 3
    text = _text(pdf)
    assert text.count("Nettobetrag") == 1              # Summenblock nur einmal, am Ende
    assert text.count("Bankverbindung") == len(seiten)  # Fußzeile auf jeder Seite
    for i in (1, 20, 40):
        assert f"Leistung {i}" in text
    assert f"Seite {len(seiten)} / {len(seiten)}" in text
    # Positionsbeschreibung und Betrag dürfen nicht auf verschiedene Seiten zerrissen werden
    for seite in seiten:
        t = seite.extract_text()
        titel = set(re.findall(r"€Leistung (\d+)", t))   # pypdf liefert Beträge vor dem Titel derselben Zeile
        beschreibung = set(re.findall(r"Beschreibung zu Leistung (\d+)", t))
        assert titel == beschreibung and titel


def test_lange_namen_brechen_das_layout_nicht(db):
    unt = {**UNT, "firmenname": "Sehr Lange Firmenbezeichnung Ingenieurgesellschaft Bau und Planung mbH & Co. KG"}
    r = _rechnung(db, dokument_typ="Auftrag")
    pdf = generate_rechnung_pdf_vorlage2(r, unt)
    assert pdf.startswith(b"%PDF")


# --- Anbindung an die App -------------------------------------------------------------------------

def test_vorlagenliste_enthaelt_vorlage_2():
    eintrag = next(v for v in list_vorlagen() if v["id"] == 2)
    assert "Baubotters" in eintrag["name"]


def test_demo_vorschau_vorlage_2(db):
    db.add(Unternehmen(firmenname="Baubotters", strasse="Musterweg", hausnummer="1", plz="78073", ort="Bad Dürrheim"))
    db.commit()
    resp = demo_pdf(vorlage=2, db=db)
    assert resp.media_type == "application/pdf"
    text = _text(resp.body)
    assert "RECHNUNG" in _kompakt(text) and "Rechnungsbetrag" in text
    # Demo: 3 Std × 60 € = 180 € netto (vorher stand dort der Positionsbetrag als Einzelpreis)
    assert "540,00 €" not in text


def test_pdf_endpunkt_nutzt_vorlage_aus_unternehmenseinstellung(db):
    unt = Unternehmen(firmenname="Baubotters", strasse="Musterweg", hausnummer="1", plz="78073", ort="Bad Dürrheim",
                      ust_idnr="DE111111111", pdf_vorlage=2)
    db.add(unt)
    db.commit()
    payload = RechnungCreate(
        typ="ausgang", datum=date(2026, 11, 10), partner_freitext="Testkunde",
        positionen=[RechnungspositionCreate(beschreibung="Beratung", menge=Decimal("1"), einheit="Stk.",
                                            netto="100.00", ust_satz="19")],
    )
    resp = create_rechnung(payload, db)
    pdf = rechnung_als_pdf(resp.id, db=db)
    text = _text(pdf.body)
    assert "ENTWURF" in _kompakt(text)          # Navy-Band mit Entwurfsmarke = Vorlage 2
    assert "Rechnungsbetrag" in text and "119,00 €" in text
    # gleiche Rechnung mit Standardvorlage sieht anders aus
    unt.pdf_vorlage = 0
    db.commit()
    standard = _text(rechnung_als_pdf(resp.id, db=db).body)
    assert "Rechnungsbetrag" not in standard


def test_zugferd_mit_vorlage_2(db):
    from utils.zugferd import generate_zugferd_pdf
    r = _rechnung(db)
    unt = {**UNT, "pdf_vorlage": 2, "email": "rechnung@example.com", "telefon": "0123 456"}
    pdf = generate_zugferd_pdf(r, unt)
    reader = PdfReader(BytesIO(pdf))
    assert "RECHNUNG" in _kompakt(reader.pages[0].extract_text())
    assert "Rechnungsbetrag" in reader.pages[-1].extract_text()
    assert any(name.endswith(".xml") for name in reader.attachments), list(reader.attachments)
