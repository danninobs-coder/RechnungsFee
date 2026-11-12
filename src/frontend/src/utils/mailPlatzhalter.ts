import type { Rechnung, Unternehmen } from '../api/client'

/** Anrede, wenn weder am Kunden noch in den Firmeneinstellungen etwas hinterlegt ist. */
export const MAIL_ANREDE_STANDARD = 'Sehr geehrte Damen und Herren,'

const ANSPRACHE: Record<string, string> = { herr: 'Herr', frau: 'Frau' }
const SEHR_GEEHRTE: Record<string, string> = { herr: 'Sehr geehrter Herr', frau: 'Sehr geehrte Frau' }

type AnredeQuelle = Pick<
  Rechnung,
  'kunde_anrede' | 'kunde_anrede_individuell' | 'kunde_vorname' | 'kunde_nachname' | 'kunde_firmenname' | 'partner_freitext'
>

/**
 * Anrede für Mailvorlagen ({anrede}). Gleiche Reihenfolge wie die Anrede im PDF
 * (backend/utils/pdf_rechnung_base.py, anrede_zeile): eigene Anrede am Kunden > globaler Anrede-Satz
 * der Firma > "Sehr geehrte Damen und Herren,". Platzhalter in der Anrede: {vorname} {nachname}
 * {firma} {anrede} (Herr/Frau) {sehr_geehrte}. Fehlt der Name für einen benutzten Platzhalter,
 * greift die Ersatzanrede.
 */
export function anredeZeile(dok: AnredeQuelle, unt: Unternehmen | null | undefined): string {
  const individuell = (dok.kunde_anrede_individuell ?? '').trim()
  const global = (unt?.anrede_text ?? '').trim()
  const vorlage = individuell || global || MAIL_ANREDE_STANDARD
  const fallback = (unt?.anrede_fallback ?? '').trim() || MAIL_ANREDE_STANDARD

  const vorname = (dok.kunde_vorname ?? '').trim()
  const nachname = (dok.kunde_nachname ?? '').trim()
  const firma = (dok.kunde_firmenname ?? '').trim() || (dok.partner_freitext ?? '').trim()
  const geschlecht = (dok.kunde_anrede ?? '').trim().toLowerCase()
  const werte: Record<string, string> = {
    '{vorname}': vorname,
    '{nachname}': nachname,
    '{firma}': firma,
    '{anrede}': ANSPRACHE[geschlecht] ?? '',
    '{sehr_geehrte}': SEHR_GEEHRTE[geschlecht] ?? `Guten Tag ${vorname}`.trim(),
  }

  const benutzt = Object.keys(werte).filter(p => vorlage.includes(p))
  const hatName = Boolean(vorname || nachname)
  if (
    (benutzt.some(p => ['{vorname}', '{nachname}', '{sehr_geehrte}'].includes(p)) && !hatName) ||
    (benutzt.includes('{anrede}') && !werte['{anrede}']) ||
    (benutzt.includes('{firma}') && !firma)
  ) {
    return fallback
  }

  let text = vorlage
  for (const [platzhalter, wert] of Object.entries(werte)) text = text.split(platzhalter).join(wert)
  return text.replace(/ {2,}/g, ' ').replace(/ +([,.!])/g, '$1').trim()
}

function mengeDe(menge: string): string {
  const zahl = parseFloat(menge)
  if (!Number.isFinite(zahl)) return menge
  return String(Math.round(zahl * 100) / 100).replace('.', ',')
}

/**
 * Abgerechnete Leistungen für Mailvorlagen ({leistungen}): je Position eine Zeile mit der ersten Zeile der
 * Beschreibung (der fette Titel im PDF). Bei Stunden o. Ä. steht die Menge dahinter, z. B. "(27,5 h)".
 * Abzüge und Rabatte (negativer Preis) werden weggelassen.
 */
export function leistungenListe(dok: Pick<Rechnung, 'positionen'>): string {
  const zeilen: string[] = []
  for (const p of dok.positionen ?? []) {
    if ((parseFloat(p.netto) || 0) < 0) continue
    const titel = (p.beschreibung ?? '').split('\n').map(z => z.trim()).find(z => z) ?? ''
    if (!titel) continue
    const einheit = (p.einheit ?? '').trim()
    const mitMenge = einheit && !['psch.', 'pauschal', 'stück', 'stk', 'stk.'].includes(einheit.toLowerCase())
    zeilen.push(`– ${titel}${mitMenge ? ` (${mengeDe(p.menge)} ${einheit})` : ''}`)
  }
  return zeilen.join('\n')
}

/** Ersetzt {anrede} und {leistungen} in einem Mailtext oder Betreff. */
export function ersetzeAnredeUndLeistungen(
  vorlage: string,
  dok: AnredeQuelle & Pick<Rechnung, 'positionen'>,
  unt: Unternehmen | null | undefined,
): string {
  return vorlage
    .split('{anrede}').join(anredeZeile(dok, unt))
    .split('{leistungen}').join(leistungenListe(dok))
}
