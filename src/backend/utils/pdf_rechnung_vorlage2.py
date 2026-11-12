"""
Rechnungs-PDF Vorlage 2 – „Baubotters“: Navy-Kopfband mit Amber-Akzent.

Layout:
  Kopfband (Navy) mit Wortmarke bzw. Logo links und Dokumenttyp rechts (RECHNUNG, ANGEBOT …),
  darunter ein schmaler Amber-Streifen. Folgeseiten bekommen ein schlankes Band.
  Anschriftenfeld nach DIN 5008, rechts daneben die Metadaten-Tabelle mit Amber-Oberkante.
  Positionstabelle mit Navy-Kopfzeile: Pos. | Leistung | Menge | Einzelpreis | (USt) | Netto.
  Summenblock rechts, der Endbetrag auf Amber-Fläche.
  Fußzeile mit drei Spalten: Firma | Steuerdaten | Bankverbindung.

Konventionen für Texte (alles optional):
  Einleitungstext: Beginnt er mit einer Zeile „# Betreff“, wird diese als Betreff (fett, größer)
  dargestellt; eine direkt folgende Zeile „## Unterzeile“ erscheint grau darunter.
  Positionsbeschreibung: Die erste Zeile ist der fette Titel der Leistung, weitere Zeilen
  erscheinen kleiner und grau darunter.

Schrift: DejaVu (wie alle Vorlagen, in der App mitgeliefert).
"""

import base64
import re
from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from utils.logo import logo_datei
from utils.pdf_rechnung_base import (
    RechnungPDFBase,
    _fmt_euro, _fmt_ust_satz, _iso_zu_de, _adresszeilen, _md, _logo_abmessungen,
    _person_bezeichnung, _ust_aufschluesselung, anrede_zeile, dokument_text,
    L_MARGIN, R_MARGIN, PAGE_W, NUTZ_W, FOOTER_H, ADRESS_Y,
)
from utils.pdf_shared import build_hr_zeile, epc_qr_bytes

# ---------------------------------------------------------------------------
# Farben (aus dem Baubotters-Rechnungsdesign)
# ---------------------------------------------------------------------------
NAVY        = (13, 27, 42)       # #0D1B2A
AMBER       = (245, 158, 11)     # #F59E0B
AMBER_HELL  = (253, 233, 184)    # #FDE9B8
GRAU        = (91, 101, 115)     # #5B6573
GRAU_TEXT   = (58, 70, 87)       # #3A4657
TEXT_MITTEL = (43, 55, 72)       # #2B3748
LINIE       = (201, 208, 217)    # #C9D0D9
LINIE_HELL  = (227, 231, 236)    # #E3E7EC
WEISS       = (255, 255, 255)

# Zweifarbige Wortmarken: Firmenname (klein geschrieben) → (weißer Teil, Amber-Teil).
# Alle anderen Namen werden einfarbig weiß gesetzt.
_WORTMARKEN = {"baubotters": ("Bau", "botters")}

BAND_H       = 27.0   # Höhe des Kopfbands auf Seite 1
STREIFEN_H   = 1.5    # Amber-Streifen unter dem Band
FOLGE_BAND_H = 11.0   # schlankes Band auf Folgeseiten
FOLGE_START  = 22.0   # y-Start des Inhalts auf Folgeseiten
PAD          = 2.2    # horizontaler Innenabstand in Tabellenzellen

_TITEL_WORT = {
    "Rechnung":          "RECHNUNG",
    "Abschlagsrechnung": "ABSCHLAGSRECHNUNG",
    "Gutschrift":        "GUTSCHRIFT",
    "Angebot":           "ANGEBOT",
    "Auftrag":           "AUFTRAGSBESTÄTIGUNG",
    "Proforma":          "PROFORMA-RECHNUNG",
    "Lieferschein":      "LIEFERSCHEIN",
}

_SUMMEN_LABEL = {
    "Rechnung":          "Rechnungsbetrag",
    "Abschlagsrechnung": "Abschlagsbetrag",
    "Gutschrift":        "Gutschriftsbetrag",
    "Angebot":           "Angebotssumme",
    "Auftrag":           "Auftragssumme",
    "Proforma":          "Gesamtbetrag",
}


def _eur(wert) -> str:
    """Euro-Betrag mit typografischem Minuszeichen (−) statt Bindestrich."""
    return _fmt_euro(wert).replace("-", "−", 1)


def _fmt_menge(wert) -> str:
    """27.5 → „27,5“, 10 → „10“, 0.250 → „0,25“."""
    try:
        d = Decimal(str(wert)).normalize()
        return format(d, "f").replace(".", ",")
    except Exception:
        return str(wert)


def _fmt_iban(iban: str) -> str:
    kompakt = "".join((iban or "").split())
    return " ".join(kompakt[i:i + 4] for i in range(0, len(kompakt), 4))


def _betreff_aufteilen(text: str) -> tuple[str, str, str]:
    """Zerlegt den Einleitungstext in (Betreff, Unterzeile, Rest).
    „# …“ in der ersten Zeile = Betreff, „## …“ direkt danach = Unterzeile."""
    zeilen = (text or "").splitlines()
    betreff = unterzeile = ""
    i = 0
    if zeilen and zeilen[0].startswith("# "):
        betreff = zeilen[0][2:].strip()
        i = 1
        if len(zeilen) > 1 and zeilen[1].startswith("## "):
            unterzeile = zeilen[1][3:].strip()
            i = 2
    return betreff, unterzeile, "\n".join(zeilen[i:]).strip()


class RechnungPDFVorlage2(RechnungPDFBase):
    """Vorlage 2: Navy/Amber-Design „Baubotters“."""

    _faellig_label      = "Zahlbar bis"
    _ln_nach_positionen = 4
    _ln_nach_summen     = 5
    _ln_nach_19         = 2

    def __init__(self, unternehmen: dict, rechnung,
                 ist_kopie: bool = False, ist_entwurf: bool = False, ist_netto: bool = True):
        super().__init__(unternehmen, rechnung,
                         ist_kopie=ist_kopie, ist_entwurf=ist_entwurf, ist_netto=ist_netto)
        self.c_margin = 0          # Innenabstände setzen wir selbst (PAD)
        self.alias_nb_pages()      # „{nb}“ in der Fußzeile = Gesamtseitenzahl

    # -------------------------------------------------------------------------
    # Hilfsfunktionen
    # -------------------------------------------------------------------------

    def _dok_typ(self) -> str:
        return getattr(self._r, "dokument_typ", "Rechnung") or "Rechnung"

    def _titel_wort(self) -> str:
        r = self._r
        dt = self._dok_typ()
        if getattr(r, "storniert", False) and dt in ("Rechnung", "Abschlagsrechnung"):
            return "STORNORECHNUNG"
        if dt == "Rechnung" and r.typ == "eingang":
            return "EINGANGSRECHNUNG"
        return _TITEL_WORT.get(dt, dt.upper())

    def _nummer(self) -> str:
        r = self._r
        if getattr(r, "storniert", False) and self._dok_typ() in ("Rechnung", "Abschlagsrechnung"):
            return getattr(r, "storno_rechnungsnummer", None) or r.rechnungsnummer or ""
        return r.rechnungsnummer or ""

    def _firmenname(self) -> str:
        unt = self._unt
        return (unt.get("firmenname")
                or " ".join(filter(None, [unt.get("vorname"), unt.get("nachname")]))
                or "RechnungsFee")

    def _text(self, x: float, y: float, w: float, h: float, text: str, *, align: str = "L",
              size: float = 9, style: str = "", farbe=NAVY, min_size: float = 6.5):
        """Eine Zeile Text; zu lange Texte werden schrittweise verkleinert."""
        self.set_font("DejaVu", style, size)
        s = size
        while s > min_size and self.get_string_width(text) > w:
            s -= 0.25
            self.set_font("DejaVu", style, s)
        self.set_text_color(*farbe)
        self.set_xy(x, y)
        self.cell(w, h, text, align=align)

    @staticmethod
    def _logo_ist_hell(pfad) -> bool:
        """True, wenn das Logo ein PNG mit transparentem Hintergrund und überwiegend hellen Pixeln ist
        (dann würde eine weiße Fläche es unsichtbar machen)."""
        try:
            if str(pfad).lower().endswith(".svg"):
                return False
            from PIL import Image
            with Image.open(pfad) as im:
                if im.mode not in ("RGBA", "LA", "PA") and "transparency" not in im.info:
                    return False
                rgba = im.convert("RGBA")
                rgba.thumbnail((200, 200))
                roh = rgba.tobytes()
            gewicht = summe = 0.0
            for i in range(0, len(roh), 4):
                if roh[i + 3] > 128:
                    summe += 0.299 * roh[i] + 0.587 * roh[i + 1] + 0.114 * roh[i + 2]
                    gewicht += 1
            return gewicht > 0 and summe / gewicht > 170
        except Exception:
            return False

    def _wortmarke(self, x: float, y_mitte: float, size: float):
        name = self._firmenname()
        teile = _WORTMARKEN.get(name.strip().lower())
        self.set_font("DejaVu", "B", size)
        h = size * 0.5
        if teile:
            w1 = self.get_string_width(teile[0])
            self.set_text_color(*WEISS)
            self.set_xy(x, y_mitte - h / 2)
            self.cell(w1, h, teile[0])
            self.set_text_color(*AMBER)
            self.cell(self.get_string_width(teile[1]) + 1, h, teile[1])
        else:
            self.set_text_color(*WEISS)
            self.set_xy(x, y_mitte - h / 2)
            self.cell(self.get_string_width(name) + 1, h, name)

    # -------------------------------------------------------------------------
    # Kopf- und Fußzeile
    # -------------------------------------------------------------------------

    def header(self):
        if self.page_no() == 1:
            self._header_seite1()
        else:
            self._header_folgeseite()

    def _header_seite1(self):
        unt = self._unt
        rechts = PAGE_W - R_MARGIN

        self.set_fill_color(*NAVY)
        self.rect(0, 0, PAGE_W, BAND_H, style="F")
        self.set_fill_color(*AMBER)
        self.rect(0, BAND_H, PAGE_W, STREIFEN_H, style="F")

        # Links: Logo (auf weißer Fläche) oder Wortmarke
        logo = logo_datei(unt.get("logo_pfad"))
        logo_breite = 0.0
        if logo:
            try:
                hell = self._logo_ist_hell(logo)   # helles Logo mit transparentem Grund: direkt aufs Navy-Band
                lw, lh = _logo_abmessungen(str(logo), max_h=BAND_H - 10, max_w=72 if hell else 60)
                if lw > 0:
                    if hell:
                        self.image(str(logo), x=L_MARGIN, y=(BAND_H - lh) / 2, w=lw, h=lh)
                        logo_breite = lw
                    else:
                        self.set_fill_color(*WEISS)
                        self.rect(L_MARGIN, (BAND_H - lh) / 2 - 2, lw + 6, lh + 4, style="F")
                        self.image(str(logo), x=L_MARGIN + 3, y=(BAND_H - lh) / 2, w=lw, h=lh)
                        logo_breite = lw + 6
            except Exception:
                logo_breite = 0.0
        if not logo_breite:
            self._wortmarke(L_MARGIN, BAND_H / 2, 21)
            self.set_font("DejaVu", "B", 21)
            logo_breite = self.get_string_width(self._firmenname()) + 2

        # Rechts: Dokumenttyp, gesperrt gesetzt; schrumpft, falls er neben der Wortmarke nicht passt
        titel = self._titel_wort()
        frei = (rechts - L_MARGIN) - logo_breite - 8
        groesse, sperrung = 15.0, 0.9
        while True:
            self.set_font("DejaVu", "B", groesse)
            self.set_char_spacing(sperrung)
            breite = self.get_string_width(titel)
            if breite <= frei or groesse <= 8:
                break
            groesse -= 0.5
            sperrung = max(0.2, sperrung - 0.04)
        self.set_text_color(*WEISS)
        marke_y = BAND_H / 2 - 4.5 if (self._ist_entwurf or self._ist_kopie) else BAND_H / 2 - 3.5
        self.set_xy(rechts - breite, marke_y)
        self.cell(breite, 7, titel, align="R")
        self.set_char_spacing(0)

        if self._ist_entwurf or self._ist_kopie:
            self._text(rechts - 60, marke_y + 8, 60, 4, "ENTWURF" if self._ist_entwurf else "KOPIE",
                       align="R", size=7.5, style="B", farbe=AMBER)

        self.set_text_color(0, 0, 0)
        self.set_y(ADRESS_Y)

    def _header_folgeseite(self):
        rechts = PAGE_W - R_MARGIN
        self.set_fill_color(*NAVY)
        self.rect(0, 0, PAGE_W, FOLGE_BAND_H, style="F")
        self.set_fill_color(*AMBER)
        self.rect(0, FOLGE_BAND_H, PAGE_W, 1.0, style="F")
        self._wortmarke(L_MARGIN, FOLGE_BAND_H / 2, 12)
        nr = self._nummer()
        self._text(rechts - 110, FOLGE_BAND_H / 2 - 2.5, 110, 5,
                   f"{self._titel_wort().title()} {nr}".strip() + f"  ·  Seite {self.page_no()}",
                   align="R", size=8.5, style="B", farbe=WEISS)
        self.set_text_color(0, 0, 0)
        self.set_y(FOLGE_START)

    def footer(self):
        unt = self._unt
        self.set_y(-FOOTER_H)
        y0 = self.get_y()
        self.set_fill_color(*AMBER)
        self.rect(L_MARGIN, y0, 14, 0.8, style="F")
        self.set_draw_color(*LINIE)
        self.line(L_MARGIN, y0 + 0.8, PAGE_W - R_MARGIN, y0 + 0.8)

        col_w = NUTZ_W / 3
        lh = 3.7
        start_y = y0 + 2.6

        firmenname = unt.get("firmenname") or ""
        inhaber    = " ".join(filter(None, [unt.get("vorname"), unt.get("nachname")]))
        strasse    = f"{unt.get('strasse', '')} {unt.get('hausnummer', '')}".strip()
        plz_ort    = f"{unt.get('plz', '')} {unt.get('ort', '')}".strip()
        telefon    = unt.get("telefon") or ""
        email      = unt.get("email") or ""
        webseite   = unt.get("webseite") or ""
        ust_id     = unt.get("ust_idnr") or ""
        steuernr   = unt.get("steuernummer") or ""
        iban       = _fmt_iban(unt.get("iban") or "")
        bic        = unt.get("bic") or ""
        bank       = unt.get("bank_name") or ""

        def _spalte(x: float, zeilen: list[tuple[str, bool]]):
            y = start_y
            for text, fett in zeilen:
                if not text:
                    continue
                self._text(x, y, col_w - 3, lh, text, size=7, style="B" if fett else "",
                           farbe=NAVY if fett else TEXT_MITTEL, min_size=5.5)
                y += lh

        name = firmenname or inhaber
        _spalte(L_MARGIN, [
            (name, True),
            (inhaber if firmenname and inhaber else "", False),
            (strasse, False), (plz_ort, False),
            (email, False),
            (f"Tel. {telefon}" if telefon else (webseite if webseite else ""), False),
        ])

        person = f"{_person_bezeichnung(unt.get('rechtsform') or '')} {inhaber}" if (firmenname and inhaber) else ""
        _spalte(L_MARGIN + col_w, [
            ("Steuerdaten", True),
            (f"Steuernr.: {steuernr}" if steuernr else "", False),
            (f"USt-IdNr.: {ust_id}" if ust_id else "", False),
            (build_hr_zeile(unt) or "", False),
            (unt.get("kammer_mitgliedschaft") or "", False),
            (person, False),
        ])

        _spalte(L_MARGIN + 2 * col_w, [
            ("Bankverbindung", True),
            (bank, False),
            (f"IBAN: {iban}" if iban else "", False),
            (f"BIC: {bic}" if bic else "", False),
            (f"Seite {self.page_no()} / {{nb}}  ·  {self._druckdatum}", False),
        ])
        self.set_text_color(0, 0, 0)

    # -------------------------------------------------------------------------
    # Anschrift + Metadaten
    # -------------------------------------------------------------------------

    def _meta_zeilen(self) -> list[tuple[str, str]]:
        r = self._r
        dt = self._dok_typ()
        partner_obj = r.kunde if r.typ == "ausgang" else r.lieferant
        ist_storno = getattr(r, "storniert", False) and dt == "Rechnung"
        zeilen: list[tuple[str, str]] = []

        partner_nr = getattr(partner_obj, "kundennummer", None) or getattr(partner_obj, "lieferantennummer", None)
        if partner_nr:
            zeilen.append(("Kundennr." if r.typ == "ausgang" else "Lieferantennr.", str(partner_nr)))

        if ist_storno:
            nr_label = "Stornorechnung-Nr."
        else:
            nr_label = {
                "Angebot": "Angebotsnr.", "Lieferschein": "Lieferscheinnr.", "Proforma": "Proforma-Nr.",
                "Auftrag": "Auftragsnr.", "Gutschrift": "Gutschriftnr.",
            }.get(dt, "Rechnungsnummer" if r.typ == "ausgang" else "Eingangsrechn.-Nr.")
        zeilen.append((nr_label, self._nummer() or "—"))

        if ist_storno:
            storno_datum = getattr(r, "storno_datum", None)
            if storno_datum:
                zeilen.append(("Stornodatum", _iso_zu_de(str(storno_datum))))
            zeilen.append(("Originaldatum", _iso_zu_de(str(r.datum))))
        else:
            datum_label = {
                "Angebot": "Angebotsdatum", "Auftrag": "Auftragsdatum", "Proforma": "Proformadatum",
                "Lieferschein": "Lieferdatum", "Gutschrift": "Gutschriftdatum",
            }.get(dt, "Rechnungsdatum")
            zeilen.append((datum_label, _iso_zu_de(str(r.datum))))

        if r.leistung_von and r.leistung_bis:
            von, bis = _iso_zu_de(str(r.leistung_von)), _iso_zu_de(str(r.leistung_bis))
            if von[-4:] == bis[-4:]:
                von = von[:-4]          # „30.10. – 05.11.2026“
            zeilen.append(("Leistungszeitraum", f"{von} – {bis}"))
        elif r.leistung_von and str(r.leistung_von) != str(r.datum):
            zeilen.append(("Leistungsdatum", _iso_zu_de(str(r.leistung_von))))

        if getattr(r, "kunden_bestellnummer", None):
            zeilen.append(("Bestellnummer", r.kunden_bestellnummer))

        if dt == "Angebot":
            gueltig_bis = getattr(r, "gueltig_bis", None)
            if gueltig_bis:
                zeilen.append(("Gültig bis", _iso_zu_de(str(gueltig_bis))))
        elif r.faellig_am:
            zeilen.append((self._faellig_label, _iso_zu_de(str(r.faellig_am))))

        la = getattr(r, "_lieferadresse", None)
        if la:
            strasse_nr = " ".join(filter(None, [la.strasse, la.hausnummer]))
            plz_ort = " ".join(filter(None, [la.plz, la.ort]))
            zeilen.append((la.bezeichnung or "Lieferadresse", ", ".join(filter(None, [strasse_nr, plz_ort]))))
        if getattr(r, "_quell_angebot_nr", None):
            zeilen.append(("Angebot", r._quell_angebot_nr))
        if getattr(r, "_quell_auftrag_nr", None):
            zeilen.append(("Auftrag", r._quell_auftrag_nr))
        return zeilen

    def _render_kopf(self) -> tuple[float, float]:
        r = self._r
        unt = self._unt

        # Absender-Kurzzeile über dem Anschriftenfeld
        absender_kurz = " · ".join(filter(None, [
            unt.get("firmenname") or " ".join(filter(None, [unt.get("vorname"), unt.get("nachname")])),
            f"{unt.get('strasse', '')} {unt.get('hausnummer', '')}".strip(),
            f"{unt.get('plz', '')} {unt.get('ort', '')}".strip(),
        ]))
        self._text(L_MARGIN, ADRESS_Y, 90, 4.5, absender_kurz, size=6.5, farbe=GRAU, min_size=5)
        self.set_draw_color(*LINIE)
        self.line(L_MARGIN, ADRESS_Y + 5, L_MARGIN + 90, ADRESS_Y + 5)

        partner_obj = r.kunde if r.typ == "ausgang" else r.lieferant
        adresszeilen = _adresszeilen(partner_obj)
        if not adresszeilen and r.partner_freitext:
            adresszeilen = [r.partner_freitext]
            strasse = getattr(r, "partner_strasse", None) or ""
            hausnr  = getattr(r, "partner_hausnummer", None) or ""
            plz     = getattr(r, "partner_plz", None) or ""
            ort     = getattr(r, "partner_ort", None) or ""
            land    = (getattr(r, "partner_land", None) or "DE").upper()
            if strasse:
                adresszeilen.append(f"{strasse} {hausnr}".strip())
            if plz or ort:
                adresszeilen.append(f"{plz} {ort}".strip())
            if land != "DE":
                adresszeilen.append(land)
        emp_y = ADRESS_Y + 6.5
        for i, zeile in enumerate(adresszeilen):
            self._text(L_MARGIN, emp_y + i * 5.5, 90, 5.5, zeile, size=10 if i == 0 else 9.5,
                       style="B" if i == 0 else "", min_size=7)
        emp_bottom = emp_y + len(adresszeilen) * 5.5

        # Metadaten-Tabelle mit Amber-Oberkante
        meta_x = L_MARGIN + 95
        meta_w = PAGE_W - R_MARGIN - meta_x
        lbl_w = 34.0
        zeile_h = 6.6
        self.set_fill_color(*AMBER)
        self.rect(meta_x, ADRESS_Y, meta_w, 0.9, style="F")
        y = ADRESS_Y + 1.6
        for lbl, wert in self._meta_zeilen():
            self._text(meta_x, y, lbl_w, zeile_h, lbl, size=8, farbe=GRAU, min_size=6)
            self._text(meta_x + lbl_w, y, meta_w - lbl_w, zeile_h, wert, align="R", size=9, style="B")
            y += zeile_h
            self.set_draw_color(*LINIE_HELL)
            self.line(meta_x, y, meta_x + meta_w, y)
        return emp_bottom, y

    # -------------------------------------------------------------------------
    # Titelbereich: Storno-/Gutschrift-Bezug, Betreff, Anrede, Einleitung
    # -------------------------------------------------------------------------

    def _render_titel(self):
        r = self._r
        dt = self._dok_typ()
        ist_storno = getattr(r, "storniert", False) and dt in ("Rechnung", "Abschlagsrechnung")
        self.set_text_color(*GRAU)
        if ist_storno:
            self.set_font("DejaVu", "", 9)
            self.cell(0, 5, f"Stornierung zu Rechnung {r.rechnungsnummer or '–'} vom {_iso_zu_de(str(r.datum))}",
                      new_x="LMARGIN", new_y="NEXT")
            grund = getattr(r, "storno_grund", None)
            if grund:
                self.set_font("DejaVu", "", 8.5)
                self.multi_cell(0, 5, f"Stornobegründung: {grund}", new_x="LMARGIN", new_y="NEXT", align="L")
            self.ln(2)
        elif dt == "Gutschrift":
            original = getattr(r, "_gutschrift_original_nr", None)
            if original:
                self.set_font("DejaVu", "", 9)
                self.cell(0, 5, f"Gutschrift zu Rechnung {original}", new_x="LMARGIN", new_y="NEXT")
                self.ln(2)

    def _render_nach_titel(self):
        text = dokument_text(self._r, self._unt, "einleitungstext")
        betreff, unterzeile, rest = _betreff_aufteilen(text)

        if betreff:
            self.set_x(L_MARGIN)
            self.set_font("DejaVu", "B", 11.5)
            self.set_text_color(*NAVY)
            self.multi_cell(NUTZ_W, 5.8, betreff, new_x="LMARGIN", new_y="NEXT", align="L")
            if unterzeile:
                self.set_font("DejaVu", "", 8.5)
                self.set_text_color(*GRAU)
                self.multi_cell(NUTZ_W, 4.6, unterzeile, new_x="LMARGIN", new_y="NEXT", align="L")
            self.ln(3)

        anrede = anrede_zeile(self._r, self._unt, nur_wenn_konfiguriert=True)
        if anrede:
            self.set_x(L_MARGIN)
            self.set_font("DejaVu", "", 9.5)
            self.set_text_color(*NAVY)
            self.cell(0, 6, anrede, new_x="LMARGIN", new_y="NEXT")
            self.ln(1 if rest else 4)
        if rest:
            self.set_x(L_MARGIN)
            self.set_font("DejaVu", "", 9)
            self.set_text_color(*NAVY)
            self.multi_cell(NUTZ_W, 5, _md(rest), markdown=True, align="L")
            self.ln(3)
        elif not betreff and not anrede:
            self.ln(2)
        self._render_bemerkung_erechnung()

    # -------------------------------------------------------------------------
    # Positionstabelle
    # -------------------------------------------------------------------------

    def _render_positionen(self):
        r = self._r
        ist_lieferschein = self._dok_typ() == "Lieferschein"
        ist_storno = getattr(r, "storniert", False) and not ist_lieferschein
        vorzeichen = Decimal("-1") if ist_storno else Decimal("1")

        sieht_ust = len({_fmt_ust_satz(p.ust_satz) for p in r.positionen}) > 1 \
            or any(getattr(p, "differenzbesteuerung", False) for p in r.positionen)

        if ist_lieferschein:
            breiten = [12.0, 0.0, 32.0]
            kopf    = ["POS.", "LEISTUNG", "MENGE"]
            ausr    = ["L", "L", "R"]
        else:
            breiten = [12.0, 0.0, 24.0, 30.0] + ([14.0] if sieht_ust else []) + [32.0]
            kopf    = ["POS.", "LEISTUNG", "MENGE", "EINZELPREIS"] + (["USt"] if sieht_ust else []) \
                      + ["NETTO" if self._ist_netto else "BRUTTO"]
            ausr    = ["L", "L", "R", "R"] + (["R"] if sieht_ust else []) + ["R"]
        breiten[1] = NUTZ_W - sum(breiten)
        desc_w = breiten[1] - 2 * PAD
        spalten_x = [L_MARGIN + sum(breiten[:i]) for i in range(len(breiten))]

        KOPF_H, TITEL_H, SUB_H, ZEILE_PAD = 7.0, 4.4, 3.9, 2.6

        def _tabellenkopf():
            y = self.get_y()
            self.set_fill_color(*NAVY)
            self.rect(L_MARGIN, y, NUTZ_W, KOPF_H, style="F")
            self.set_char_spacing(0.2)
            for i, h in enumerate(kopf):
                self._text(spalten_x[i] + PAD, y, breiten[i] - 2 * PAD, KOPF_H, h, align=ausr[i],
                           size=7.2, style="B", farbe=WEISS, min_size=5.5)
            self.set_char_spacing(0)
            self.set_xy(L_MARGIN, y + KOPF_H)

        def _teile(pos) -> tuple[str, str]:
            zeilen = (pos.beschreibung or "").strip().splitlines()
            titel = zeilen[0].strip() if zeilen else ""
            sub = [z.strip() for z in zeilen[1:] if z.strip()]
            code = pos.artikel.artikelcode if getattr(pos, "artikel", None) and pos.artikel.artikelcode else ""
            if code:
                sub.insert(0, f"Art.-Nr. {code}")
            return titel, "\n".join(sub)

        def _hoehe(titel: str, sub: str, mit_rabatt: bool) -> float:
            self.set_font("DejaVu", "B", 9)
            n1 = len(self.multi_cell(desc_w, TITEL_H, titel, dry_run=True, output="LINES", align="L")) if titel else 1
            n2 = 0
            if sub:
                self.set_font("DejaVu", "", 8)
                n2 = len(self.multi_cell(desc_w, SUB_H, sub, dry_run=True, output="LINES", align="L"))
            return 2 * ZEILE_PAD + n1 * TITEL_H + n2 * SUB_H + (4.6 if mit_rabatt else 0)

        _tabellenkopf()
        for pos in r.positionen:
            titel, sub = _teile(pos)
            rabatt_proz = getattr(pos, "rabatt_prozent", Decimal("0")) or Decimal("0")
            mit_rabatt = rabatt_proz > 0 and not ist_lieferschein
            hoehe = _hoehe(titel, sub, mit_rabatt)
            if self._umbruch_vor_position(hoehe, KOPF_H):
                _tabellenkopf()

            y0 = self.get_y()
            ytext = y0 + ZEILE_PAD
            menge = Decimal(str(pos.menge))
            einheit = (pos.einheit or "").strip()
            menge_txt = f"{'-' if ist_storno else ''}{_fmt_menge(menge)} {einheit}".strip()
            nr = str(getattr(pos, "position_nr", "") or "")

            self._text(spalten_x[0] + PAD, ytext, breiten[0] - 2 * PAD, TITEL_H, nr, size=8.5, farbe=GRAU)
            self._text(spalten_x[2] + PAD, ytext, breiten[2] - 2 * PAD, TITEL_H, menge_txt, align="R",
                       size=8.5, farbe=NAVY)
            if not ist_lieferschein:
                ust_label = "§25a" if getattr(pos, "differenzbesteuerung", False) else f"{_fmt_ust_satz(pos.ust_satz)} %"
                if self._ist_netto:
                    gesamt_vor = pos.netto * pos.menge * vorzeichen
                    gesamt_eff = (pos.brutto - pos.ust_betrag) * vorzeichen
                else:
                    gesamt_vor = pos.netto * pos.menge * vorzeichen
                    gesamt_eff = pos.brutto * vorzeichen
                self._text(spalten_x[3] + PAD, ytext, breiten[3] - 2 * PAD, TITEL_H, _eur(pos.netto),
                           align="R", size=8.5)
                if sieht_ust:
                    self._text(spalten_x[4] + PAD, ytext, breiten[4] - 2 * PAD, TITEL_H, ust_label,
                               align="R", size=8.5)
                self._text(spalten_x[-1] + PAD, ytext, breiten[-1] - 2 * PAD, TITEL_H, _eur(gesamt_vor),
                           align="R", size=8.5, style="B")

            # Beschreibung: Titel fett, Rest klein und grau
            self.set_xy(spalten_x[1] + PAD, ytext)
            self.set_font("DejaVu", "B", 9)
            self.set_text_color(*NAVY)
            self.multi_cell(desc_w, TITEL_H, titel, new_x="LEFT", new_y="NEXT", align="L")
            if sub:
                self.set_x(spalten_x[1] + PAD)
                self.set_font("DejaVu", "", 8)
                self.set_text_color(*GRAU_TEXT)
                self.multi_cell(desc_w, SUB_H, sub, new_x="LEFT", new_y="NEXT", align="L")

            y_ende = y0 + hoehe
            if mit_rabatt:
                rabatt_abs = gesamt_vor - gesamt_eff
                yr = y_ende - 4.6 - ZEILE_PAD + 0.6
                self._text(spalten_x[1] + PAD, yr, breiten[1] + sum(breiten[2:-1]) - 2 * PAD, 4.2,
                           f"{rabatt_proz:.4g} % Rabatt", size=7.5, farbe=GRAU)
                self._text(spalten_x[-1] + PAD, yr, breiten[-1] - 2 * PAD, 4.2,
                           f"− {_eur(abs(rabatt_abs))}", align="R", size=7.5, farbe=GRAU)
            self.set_draw_color(*LINIE)
            self.line(L_MARGIN, y_ende, L_MARGIN + NUTZ_W, y_ende)
            self.set_xy(L_MARGIN, y_ende)

        # Geometrie für Basisklassen-Kompatibilität (der Summenblock ist hier eigen)
        self._sum_x     = PAGE_W - R_MARGIN - 92
        self._sum_lbl_w = 60.0
        self._sum_val_w = 32.0

    # -------------------------------------------------------------------------
    # Summenblock
    # -------------------------------------------------------------------------

    def _render_summenblock(self):
        r = self._r
        unt = self._unt
        dt = self._dok_typ()
        ist_storno = getattr(r, "storniert", False) and dt == "Rechnung"
        s = Decimal("-1") if ist_storno else Decimal("1")

        aufschluesselung = _ust_aufschluesselung(r.positionen)
        rabatt_proz   = getattr(r, "rabatt_prozent", Decimal("0")) or Decimal("0")
        rabatt_betrag = getattr(r, "rabatt_betrag", None)
        hat_rabatt = bool(rabatt_proz or rabatt_betrag)

        if hat_rabatt:
            q4, q2 = Decimal("0.0001"), Decimal("0.01")
            pos_netto = sum((Decimal(str(p.brutto)) - Decimal(str(p.ust_betrag))) for p in r.positionen).quantize(q4)
            pos_ust   = sum(Decimal(str(p.ust_betrag)) for p in r.positionen).quantize(q4)
            pos_brutto = (pos_netto + pos_ust).quantize(q4)
            if rabatt_betrag:
                rabatt_brutto = Decimal(str(rabatt_betrag)).quantize(q2)
                rabatt_netto  = (pos_netto - r.netto_gesamt).quantize(q2)
                rabatt_lbl = "Abzug"
            else:
                rabatt_netto  = (pos_netto * rabatt_proz / 100).quantize(q2)
                rabatt_brutto = (pos_brutto * rabatt_proz / 100).quantize(q2)
                rabatt_lbl = f"Rabatt {rabatt_proz:.4g} %"

        zeilen: list[tuple[str, str, str]] = []   # (Label, Wert, Stil: n|f|g)
        hat_ust = r.ust_gesamt != 0
        if self._ist_netto:
            if hat_rabatt:
                zeilen.append(("Zwischensumme netto", _eur(pos_netto * s), "n"))
                zeilen.append((rabatt_lbl, f"−{_eur(rabatt_netto)}", "n"))
            if hat_rabatt or hat_ust:
                zeilen.append(("Nettobetrag", _eur(r.netto_gesamt * s), "f"))
            if hat_ust:
                for satz, _netto, ust_sum in aufschluesselung:
                    if satz > 0:
                        zeilen.append((f"zzgl. {_fmt_ust_satz(satz)} % Umsatzsteuer", _eur(ust_sum * s), "n"))
        else:
            if hat_rabatt:
                zeilen.append(("Zwischensumme", _eur(pos_brutto * s), "n"))
                zeilen.append((rabatt_lbl, f"−{_eur(rabatt_brutto)}", "n"))
            if not unt.get("ist_kleinunternehmer") and hat_ust:
                zeilen.append(("Nettobetrag", _eur(r.netto_gesamt * s), "g"))
                zeilen.append(("enthaltene USt", _eur(r.ust_gesamt * s), "g"))

        endlabel = _SUMMEN_LABEL.get(dt, "Gesamtbetrag")
        endwert = _eur(r.brutto_gesamt * s)

        breite = 92.0
        x = PAGE_W - R_MARGIN - breite
        zeile_h, end_h = 6.4, 10.0
        gesamt_h = zeile_h * len(zeilen) + end_h
        if self.get_y() + gesamt_h > self.page_break_trigger:
            self.add_page()
        self.ln(1)

        y = self.get_y()
        for label, wert, stil in zeilen:
            farbe = GRAU if stil == "g" else NAVY
            fett = "B" if stil == "f" else ""
            self._text(x + PAD, y, breite * 0.58, zeile_h, label, size=8.8, style=fett, farbe=farbe)
            self._text(x + breite * 0.58, y, breite * 0.42 - PAD, zeile_h, wert, align="R", size=8.8,
                       style=fett, farbe=farbe)
            y += zeile_h
            self.set_draw_color(*LINIE_HELL)
            self.line(x, y, x + breite, y)

        self.set_fill_color(*AMBER)
        self.rect(x, y, breite, end_h, style="F")
        self._text(x + PAD + 0.5, y, breite * 0.5, end_h, endlabel, size=10.5, style="B", farbe=NAVY)
        self._text(x + breite * 0.45, y, breite * 0.55 - PAD - 0.5, end_h, endwert, align="R", size=11.5,
                   style="B", farbe=NAVY, min_size=8)
        self.set_xy(L_MARGIN, y + end_h)

    # -------------------------------------------------------------------------
    # Zahlungsblock
    # -------------------------------------------------------------------------

    def _render_zahlungsblock(self):
        r = self._r
        unt = self._unt
        if r.typ != "ausgang":
            return
        dt = self._dok_typ()
        iban = unt.get("iban") or ""
        bic = unt.get("bic") or ""
        status = str(getattr(r, "zahlungsstatus", "offen") or "offen")
        zahlungen = [z for z in (getattr(r, "journaleintraege", None) or []) if not getattr(z, "storniert", False)]
        faellig = _iso_zu_de(str(r.faellig_am)) if r.faellig_am else None
        ist_gutschrift = dt == "Gutschrift" or r.brutto_gesamt < 0

        absaetze: list[str] = []   # Markdown, ** = fett
        if status in ("bezahlt", "teilweise") and zahlungen:
            art_labels = {"Bar": "Barzahlung", "Karte": "Kartenzahlung", "PayPal": "PayPal", "Bank": "Überweisung"}
            gruppen: dict[tuple[str, str], Decimal] = {}
            for z in zahlungen:
                key = (str(z.datum), str(getattr(z, "zahlungsart", "")))
                gruppen[key] = gruppen.get(key, Decimal("0")) + z.brutto_betrag
            for (datum, art), summe in gruppen.items():
                art_label = art_labels.get(art, art)
                if ist_gutschrift:
                    praefix = "Betrag zurückerstattet am" if status == "bezahlt" and len(gruppen) == 1 \
                        else "Teilbetrag zurückerstattet am"
                else:
                    praefix = "Dankend erhalten am" if status == "bezahlt" and len(gruppen) == 1 \
                        else "Teilbetrag erhalten am"
                absaetze.append(f"**Zahlung:** {praefix} {_iso_zu_de(datum)} per {art_label}: "
                                f"**{_eur(abs(summe))}**")
        elif ist_gutschrift:
            absaetze.append(f"**Erstattung:** Der Betrag von {_eur(abs(r.brutto_gesamt))} wird Ihnen erstattet.")
        elif unt.get("zahlungshinweis_aktiv", True) and iban:
            eigener = (unt.get("zahlungshinweis_text") or "").strip()
            if eigener:
                hinweis = eigener
                kunde = getattr(r, "kunde", None)
                kunde_name = (getattr(kunde, "firmenname", None)
                              or " ".join(filter(None, [getattr(kunde, "vorname", None), getattr(kunde, "nachname", None)])))
                for platzhalter, wert in {
                    "{rechnungsnummer}": r.rechnungsnummer or "", "{betrag}": _eur(r.brutto_gesamt),
                    "{faellig_am}": faellig or "sofort", "{iban}": _fmt_iban(iban), "{bic}": bic,
                    "{kunde}": kunde_name or "", "{firmenname}": self._firmenname(),
                }.items():
                    hinweis = hinweis.replace(platzhalter, wert)
                absaetze.append(hinweis)
            else:
                frist = f"bis zum **{faellig}**" if faellig else "umgehend"
                nr = f" **{r.rechnungsnummer}**" if r.rechnungsnummer else ""
                absaetze.append(f"**Zahlung:** Bitte überweisen Sie den Rechnungsbetrag {frist} ohne Abzug "
                                f"unter Angabe der Rechnungsnummer{nr} auf das unten genannte Konto.")
        sk_proz = getattr(r, "skonto_prozent", None)
        sk_tage = getattr(r, "skonto_tage", None)
        if sk_proz and sk_tage and status not in ("bezahlt", "teilweise") and not ist_gutschrift:
            sk_betrag = (r.brutto_gesamt * Decimal(str(sk_proz)) / 100).quantize(Decimal("0.01"))
            sk_frist = r.datum + timedelta(days=int(sk_tage))
            absaetze.append(f"Bei Zahlung bis {_iso_zu_de(str(sk_frist))} gewähren wir {Decimal(str(sk_proz)):.4g} % Skonto: "
                            f"Zahlbetrag {_eur(r.brutto_gesamt - sk_betrag)}.")
        if not absaetze:
            return

        qr = None
        if unt.get("qr_zahlung_aktiv", False) and iban and status not in ("bezahlt", "teilweise") and not ist_gutschrift:
            qr = epc_qr_bytes(iban, bic, self._firmenname(), float(r.brutto_gesamt), r.rechnungsnummer or "")
        qr_w = 26.0
        text_w = NUTZ_W - (qr_w + 6 if qr else 0)

        self.set_font("DejaVu", "", 8.5)
        text_h = sum(
            len(self.multi_cell(text_w, 4.7, a, dry_run=True, output="LINES", markdown=True, align="L")) * 4.7 + 1.6
            for a in absaetze
        )
        block_h = max(text_h, qr_w + 6 if qr else 0)
        if self.get_y() + block_h > self.page_break_trigger:
            self.add_page()
        y = self.get_y()
        self.set_xy(L_MARGIN, y)
        self.set_text_color(*TEXT_MITTEL)
        for a in absaetze:
            self.set_x(L_MARGIN)
            self.set_font("DejaVu", "", 8.5)
            self.multi_cell(text_w, 4.7, a, markdown=True, new_x="LMARGIN", new_y="NEXT", align="L")
            self.ln(1.6)
        if qr:
            qx = L_MARGIN + NUTZ_W - qr_w
            self.image(BytesIO(qr), x=qx, y=y, w=qr_w, h=qr_w)
            self._text(qx - 3, y + qr_w + 0.5, qr_w + 6, 3.5, "Per Banking-App zahlen", align="C",
                       size=6, farbe=GRAU, min_size=5)
            self.set_y(max(self.get_y(), y + qr_w + 5))
        self.set_text_color(0, 0, 0)

    def _schlusstext_layout(self) -> tuple[float, float, float]:
        return 2, L_MARGIN, NUTZ_W

    # -------------------------------------------------------------------------
    # Schlusstext mit Unterschriftsblock: Gruß → Unterschrift → Name → Titel
    # -------------------------------------------------------------------------

    SIG_MAX_W = 42.0                      # größte Breite/Höhe der Unterschrift in mm
    SIG_MAX_H = 15.0

    def _unterschrift_bytes(self) -> bytes | None:
        """Unterschrift als PNG/JPG-Bytes - nur wenn hinterlegt, aktiviert und für den Dokumenttyp sinnvoll."""
        unt = self._unt
        b64 = unt.get("unterschrift_bild") or ""
        if not b64 or not unt.get("unterschrift_auf_rechnung"):
            return None
        dt = getattr(self._r, "dokument_typ", "Rechnung") or "Rechnung"
        if dt == "Lieferschein" or (getattr(self._r, "storniert", False) and dt == "Rechnung"):
            return None
        try:
            return base64.b64decode(b64.split(",", 1)[-1])
        except Exception:
            return None

    @staticmethod
    def _norm(text: str) -> str:
        return re.sub(r"[*_\s]+", " ", text or "").strip().lower()

    def _unterschrift_abmessungen(self, daten: bytes) -> tuple[float, float]:
        from PIL import Image
        with Image.open(BytesIO(daten)) as im:
            bw, bh = im.size
        if not bw or not bh:
            return 0.0, 0.0
        faktor = min(self.SIG_MAX_W / bw, self.SIG_MAX_H / bh)
        return bw * faktor, bh * faktor

    def _render_schlusstext(self):
        """Wie die Basisklasse, nur mit Unterschriftsblock. Steht der Name der Inhaberin/des Inhabers
        als eigene Zeile im Schlusstext, kommt die Unterschrift davor und der Titel (Berufsbezeichnung)
        danach; fehlt der Name, wird der Block unter den Text gesetzt. Ohne Unterschrift und ohne
        Titel bleibt alles wie bisher."""
        unt = self._unt
        text = dokument_text(self._r, unt, "schlusstext")
        name = " ".join(filter(None, [(unt.get("vorname") or "").strip(), (unt.get("nachname") or "").strip()]))
        titel = (unt.get("berufsbezeichnung") or "").strip()
        bild = self._unterschrift_bytes()
        if not name or not (bild or titel):
            return super()._render_schlusstext()

        zeilen = (text or "").strip("\n").split("\n") if (text or "").strip() else []
        idx = next((i for i, z in enumerate(zeilen) if self._norm(z) == name.lower()), None)
        if idx is None:
            davor, danach = zeilen, []
            if davor and davor[-1].strip():
                davor = davor + [""]
        else:
            davor, danach = zeilen[:idx], zeilen[idx + 1:]
        # Titel schon im Text? Dann nicht doppelt setzen.
        if titel and danach and self._norm(danach[0]) == self._norm(titel):
            danach = danach[1:]
        while davor and not davor[-1].strip():
            davor.pop()

        try:
            sw, sh = self._unterschrift_abmessungen(bild) if bild else (0.0, 0.0)
        except Exception:
            bild, sw, sh = None, 0.0, 0.0

        abstand, x, breite = self._schlusstext_layout()
        self.set_font("DejaVu", "", 9)
        text_davor = "\n".join(davor).strip()
        h_davor = 0.0
        if text_davor:
            try:
                h_davor = float(self.multi_cell(breite, 5, _md(text_davor), markdown=True, align="L",
                                                dry_run=True, output="HEIGHT"))
            except Exception:
                h_davor = 5.0 * (text_davor.count("\n") + 2)
        block_h = abstand + h_davor + (sh + 1.5 if bild else 3.0) + 5.0 + (4.5 if titel else 0.0)
        if self.get_y() + block_h > self.page_break_trigger:
            self.add_page()
        elif abstand:
            self.ln(abstand)

        self.set_text_color(*TEXT_MITTEL)
        if text_davor:
            self.set_x(x)
            self.multi_cell(breite, 5, _md(text_davor), markdown=True, align="L")
            self.ln(1.0)
        if bild and sw > 0:
            y = self.get_y()
            self.image(BytesIO(bild), x=x, y=y, w=sw, h=sh)
            self.set_y(y + sh + 0.5)
            self._unterschrift_im_schlusstext = True   # Basisklasse hängt sie dann nicht noch einmal unten an
        else:
            self.ln(2.0)
        self.set_x(x)
        self.set_font("DejaVu", "B", 9.5)
        self.set_text_color(*NAVY)
        self.cell(breite, 5, name, new_x="LMARGIN", new_y="NEXT")
        if titel:
            self.set_x(x)
            self.set_font("DejaVu", "", 8)
            self.set_text_color(*GRAU)
            self.cell(breite, 4.5, titel, new_x="LMARGIN", new_y="NEXT")
        text_danach = "\n".join(danach).strip()
        if text_danach:
            self.ln(1.0)
            self.set_x(x)
            self.set_font("DejaVu", "", 9)
            self.set_text_color(*TEXT_MITTEL)
            self.multi_cell(breite, 5, _md(text_danach), markdown=True, align="L")
        self.set_text_color(0, 0, 0)
        self.ln(2)


# ---------------------------------------------------------------------------
# Öffentliche Funktion
# ---------------------------------------------------------------------------

def generate_rechnung_pdf_vorlage2(
    rechnung, unternehmen: dict,
    ist_kopie: bool = False,
    ist_entwurf: bool = False,
    ist_netto: bool = True,
    mit_output_intent: bool = False,
) -> bytes:
    pdf = RechnungPDFVorlage2(unternehmen, rechnung,
                              ist_kopie=ist_kopie, ist_entwurf=ist_entwurf, ist_netto=ist_netto)
    if mit_output_intent:
        # sRGB-OutputIntent für PDF/A-3 (ZUGFeRD) - wie in der Standardvorlage
        from fpdf.enums import OutputIntentSubType
        from fpdf.output import PDFICCProfile
        from fpdf.util import builtin_srgb2014_bytes
        pdf.add_output_intent(
            OutputIntentSubType.PDFA,
            output_condition_identifier="sRGB",
            output_condition="IEC 61966-2-1:1999",
            registry_name="http://www.color.org",
            dest_output_profile=PDFICCProfile(contents=builtin_srgb2014_bytes(), n=3, alternate="DeviceRGB"),
            info="sRGB2014 (v2)",
        )
    return pdf.render()
