"""
Regressionstest für Issue #435: die konfigurierte Unterschrift (Einstellungen → Unternehmen →
Unterschrift) erschien bisher nur in Rechnung, Gutschrift und Abschlagsrechnung - nicht aber in
Angebot, Proforma und Auftrag. Ursache: embed_unterschrift() in RechnungPDFBase.render() wurde
beim Einführen dieser drei Dokumenttypen jeweils pauschal mit der (für diese Typen korrekten)
Zahlungsblock-Ausnahme mitausgeschlossen, ohne dass die Unterschrift separat betrachtet wurde.

Lieferschein (eigene Empfangsbestätigung statt Unterschrift) und eine Stornorechnung (bewusste
Entscheidung aus Issue #178: "Kein Zahlungsblock, keine Unterschrift") bleiben unverändert ohne
Unterschrift.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.connection import Base
from database.models import Kunde, Rechnung, Rechnungsposition
from utils.pdf_rechnung import generate_rechnung_pdf
from utils.pdf_rechnung_vorlage1 import generate_rechnung_pdf_vorlage1
from utils.pdf_rechnung_vorlage2 import generate_rechnung_pdf_vorlage2


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


UNT_DICT = {
    "firmenname": "Test GmbH", "vorname": "", "nachname": "",
    "strasse": "Teststr.", "hausnummer": "1", "plz": "12345", "ort": "Testort",
    "land": "DE", "ust_idnr": "DE111111111", "steuernummer": "", "iban": "", "bic": "",
    "telefon": "", "email": "", "webseite": "", "ist_kleinunternehmer": False,
    "unterschrift_bild": "data:image/png;base64,iVBORw0KGgo=",
    "unterschrift_auf_rechnung": True,
}


def _rechnung(db, dokument_typ: str, storniert: bool = False) -> Rechnung:
    kunde = Kunde(firmenname="Kundin", strasse="Weg", hausnummer="1", plz="12345", ort="Ort", land="DE")
    db.add(kunde)
    db.flush()
    netto = Decimal("100.00")
    ust = Decimal("19.00")
    rechnung = Rechnung(
        typ="ausgang", rechnungsnummer="DOK-1", datum=date(2026, 10, 13),
        kunde_id=kunde.id, netto_gesamt=netto, ust_gesamt=ust,
        brutto_gesamt=netto + ust, ist_entwurf=False,
        dokument_typ=dokument_typ, storniert=storniert,
    )
    db.add(rechnung)
    db.flush()
    db.add(Rechnungsposition(
        rechnung_id=rechnung.id, position_nr=1, beschreibung="Leistung", menge=Decimal("1"),
        einheit="Stk.", netto=netto, ust_satz=Decimal("19"), ust_betrag=ust, brutto=netto + ust,
    ))
    db.commit()
    db.refresh(rechnung)
    return rechnung


@pytest.mark.parametrize("dokument_typ", ["Rechnung", "Angebot", "Proforma", "Auftrag", "Gutschrift", "Abschlagsrechnung"])
@pytest.mark.parametrize("generate", [generate_rechnung_pdf, generate_rechnung_pdf_vorlage1, generate_rechnung_pdf_vorlage2])
def test_unterschrift_erscheint_in_allen_vertragsrelevanten_dokumenten(db, dokument_typ, generate):
    rechnung = _rechnung(db, dokument_typ)
    with patch("utils.pdf_rechnung_base.embed_unterschrift") as mock_embed:
        generate(rechnung, UNT_DICT)
    mock_embed.assert_called_once()


@pytest.mark.parametrize("generate", [generate_rechnung_pdf, generate_rechnung_pdf_vorlage1, generate_rechnung_pdf_vorlage2])
def test_unterschrift_fehlt_weiterhin_bei_lieferschein(db, generate):
    rechnung = _rechnung(db, "Lieferschein")
    with patch("utils.pdf_rechnung_base.embed_unterschrift") as mock_embed:
        generate(rechnung, UNT_DICT)
    mock_embed.assert_not_called()


@pytest.mark.parametrize("generate", [generate_rechnung_pdf, generate_rechnung_pdf_vorlage1, generate_rechnung_pdf_vorlage2])
def test_unterschrift_fehlt_weiterhin_bei_stornorechnung(db, generate):
    rechnung = _rechnung(db, "Rechnung", storniert=True)
    with patch("utils.pdf_rechnung_base.embed_unterschrift") as mock_embed:
        generate(rechnung, UNT_DICT)
    mock_embed.assert_not_called()
