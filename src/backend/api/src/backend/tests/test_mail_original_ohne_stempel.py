"""
Fork-Erweiterung: Eine bereits ausgegebene Rechnung (Original archiviert) kann auf Wunsch
als ORIGINAL ohne KOPIE-Stempel per Mail verschickt werden (MailSendenRequest.original).

Hintergrund: Wird das PDF einer Rechnung zum ersten Mal erzeugt (z. B. beim Ansehen/Drucken), gilt
sie als ausgegeben und jede weitere Ausgabe bekommt den KOPIE-Stempel - auch wenn die Rechnung noch
nie beim Kunden war. Mit original=True wird das archivierte Original unverändert angehängt.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.connection import Base
from database.models import Kunde, Rechnung, Rechnungsposition, Unternehmen
import api.mail as mail_modul
from api.mail import mail_senden, MailSendenRequest, _pdf_bytes_fuer


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    monkeypatch.setattr(mail_modul, "APP_DATA_DIR", tmp_path)

    unt = Unternehmen(
        firmenname="Test GmbH", strasse="Teststr.", hausnummer="1", plz="12345", ort="Testort",
        smtp_aktiv=True, smtp_host="smtp.example.com", smtp_port=587,
        smtp_user="test@example.com", smtp_passwort="geheim",
    )
    session.add(unt)

    kunde = Kunde(firmenname="Kunde GmbH", strasse="Kundenweg", hausnummer="1", plz="54321", ort="Kundenstadt")
    session.add(kunde)
    session.flush()

    rechnung = Rechnung(
        typ="ausgang", rechnungsnummer="RE-2026-1", datum=date(2026, 10, 1),
        kunde_id=kunde.id, netto_gesamt=Decimal("100.00"), ust_gesamt=Decimal("19.00"),
        brutto_gesamt=Decimal("119.00"), ist_entwurf=False, dokument_typ="Rechnung",
    )
    session.add(rechnung)
    session.flush()
    session.add(Rechnungsposition(
        rechnung_id=rechnung.id, position_nr=1, beschreibung="Beratung", menge=Decimal("1"),
        einheit="Stk.", netto=Decimal("100.00"), ust_satz=Decimal("19"), ust_betrag=Decimal("19.00"),
        brutto=Decimal("119.00"),
    ))
    session.commit()
    session.refresh(rechnung)

    yield session, rechnung.id, tmp_path
    session.close()


def _erstversand(session, rechnung_id):
    """Erster erfolgreicher Versand: archiviert das Original und markiert die Rechnung als ausgegeben."""
    with patch("smtplib.SMTP"):
        mail_senden(
            MailSendenRequest(an="a@example.com", betreff="R", text="T", rechnung_id=rechnung_id),
            db=session,
        )
    r = session.query(Rechnung).filter(Rechnung.id == rechnung_id).first()
    assert r.ausgegeben is True and r.original_pdf_pfad


def test_ohne_option_bleibt_es_bei_der_kopie(db):
    session, rechnung_id, _ = db
    _erstversand(session, rechnung_id)

    pdf, name, frisch = _pdf_bytes_fuer(rechnung_id, session)

    assert name.endswith("_Kopie.pdf")
    assert frisch is None
    assert pdf != (db[2] / session.query(Rechnung).get(rechnung_id).original_pdf_pfad).read_bytes()


def test_mit_option_wird_das_archivierte_original_unveraendert_angehaengt(db):
    session, rechnung_id, tmp_path = db
    _erstversand(session, rechnung_id)
    r = session.query(Rechnung).filter(Rechnung.id == rechnung_id).first()
    archiv = (tmp_path / r.original_pdf_pfad).read_bytes()
    ausgegeben_am = r.ausgegeben_am

    pdf, name, frisch = _pdf_bytes_fuer(rechnung_id, session, original=True)

    assert pdf == archiv                       # byte-identisch, kein KOPIE-Stempel
    assert name == "Rechnung_RE-2026-1.pdf"    # ohne "_Kopie"
    assert frisch is None                      # nichts neu archivieren
    session.refresh(r)
    assert r.ausgegeben_am == ausgegeben_am    # Status unberührt


def test_original_per_mail_senden_haengt_original_an(db):
    session, rechnung_id, tmp_path = db
    _erstversand(session, rechnung_id)
    r = session.query(Rechnung).filter(Rechnung.id == rechnung_id).first()
    archiv = (tmp_path / r.original_pdf_pfad).read_bytes()

    with patch("smtplib.SMTP") as mock_smtp, patch.object(
        mail_modul, "_build_message", wraps=mail_modul._build_message
    ) as bauen:
        srv = mock_smtp.return_value.__enter__.return_value
        mail_senden(
            MailSendenRequest(an="a@example.com", betreff="R", text="T", rechnung_id=rechnung_id, original=True),
            db=session,
        )
        assert srv.sendmail.called

    anhaenge = bauen.call_args.args[5]
    assert anhaenge == [(archiv, "Rechnung_RE-2026-1.pdf")]


def test_original_ohne_archiv_datei_gibt_klaren_fehler(db):
    session, rechnung_id, tmp_path = db
    _erstversand(session, rechnung_id)
    r = session.query(Rechnung).filter(Rechnung.id == rechnung_id).first()
    (tmp_path / r.original_pdf_pfad).unlink()

    with pytest.raises(HTTPException) as fehler:
        _pdf_bytes_fuer(rechnung_id, session, original=True)

    assert fehler.value.status_code == 404


def test_original_bei_noch_nicht_ausgegebener_rechnung_erzeugt_normal_das_original(db):
    """original=True darf den normalen Erstversand nicht verändern: Es gibt noch kein Archiv,
    also wird wie bisher frisch erzeugt und zum Archivieren zurückgemeldet."""
    session, rechnung_id, _ = db

    pdf, name, frisch = _pdf_bytes_fuer(rechnung_id, session, original=True)

    assert name == "Rechnung_RE-2026-1.pdf"
    assert frisch is not None
    assert pdf.startswith(b"%PDF")
