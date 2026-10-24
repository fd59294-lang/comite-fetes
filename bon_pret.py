"""Bon de prêt de matériel (PDF à signer par les deux parties)."""
from __future__ import annotations

import io
from datetime import date

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from rapports import BLEU, BLEU_CLAIR, STYLES, _tableau, eur
from referentiel import CONDITIONS_PRET, STATUTS_PRET


def _d(v) -> str:
    return v.strftime("%d/%m/%Y") if pd.notna(v) else "…… / …… / ……"


def generer_bon(nom_association: str, contact_comite: str, pret: pd.Series, association: pd.Series | None,
                lignes: pd.DataFrame, materiel: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm,
                            bottomMargin=1.8 * cm, title=f"Bon de prêt {pret['numero']}", author=nom_association)
    titre = ParagraphStyle("t", parent=STYLES["h1"], fontSize=18, leading=22, spaceAfter=2)
    h = []
    entete = Table([[Paragraph(f"<b>{nom_association}</b><br/>{contact_comite}", STYLES["texte"]),
                     Paragraph(f"<b>Bon de prêt n° {pret['numero']}</b><br/>Statut : "
                               f"{STATUTS_PRET.get(pret['statut'], pret['statut'])}",
                               ParagraphStyle("r", parent=STYLES["texte"], alignment=2))]],
                   colWidths=[9 * cm, 8 * cm])
    entete.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("LINEBELOW", (0, 0), (-1, 0), 1, BLEU), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    h += [entete, Spacer(1, 0.5 * cm), Paragraph("Prêt de matériel", titre)]

    a = association if association is not None else pd.Series(dtype=object)
    emprunteur = [[Paragraph("<b>Emprunteur</b>", STYLES["cellule"]),
                   Paragraph(str(a.get("nom", "")), STYLES["cellule"])],
                  [Paragraph("Responsable", STYLES["cellule"]), Paragraph(str(a.get("contact", "")), STYLES["cellule"])],
                  [Paragraph("Téléphone / e-mail", STYLES["cellule"]),
                   Paragraph(" — ".join(x for x in (str(a.get("telephone", "")), str(a.get("email", ""))) if x),
                             STYLES["cellule"])],
                  [Paragraph("Adresse", STYLES["cellule"]), Paragraph(str(a.get("adresse", "")), STYLES["cellule"])],
                  [Paragraph("<b>Période</b>", STYLES["cellule"]),
                   Paragraph(f"du <b>{_d(pret['date_debut'])}</b> au <b>{_d(pret['date_fin_prevue'])}</b> (retour prévu)",
                             STYLES["cellule"])]]
    t = Table(emprunteur, colWidths=[4.5 * cm, 12.5 * cm])
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9D9D9")),
                           ("BACKGROUND", (0, 0), (0, -1), BLEU_CLAIR), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    h += [t, Spacer(1, 0.5 * cm), Paragraph("Matériel prêté", STYLES["h2"])]

    noms = materiel.set_index("id")
    tab = [["Matériel", "Qté", "État au départ", "Valeur unit.", "Qté rendue", "État au retour"]]
    valeur_totale = 0.0
    for l in lignes.itertuples():
        nom = noms["nom"].get(l.materiel_id, "?") if not noms.empty else "?"
        vu = pd.to_numeric(str(noms["valeur_unitaire"].get(l.materiel_id, "")).replace(",", "."), errors="coerce") \
            if not noms.empty else float("nan")
        vu = 0.0 if pd.isna(vu) else float(vu)
        valeur_totale += vu * l.quantite
        rendu = "" if pd.isna(l.quantite_rendue) else f"{l.quantite_rendue:g}"
        tab.append([nom, f"{l.quantite:g}", l.etat_depart or "Bon", eur(vu) if vu else "—", rendu,
                    l.etat_retour or ""])
    tab.append(["Valeur totale du matériel prêté", "", "", eur(valeur_totale), "", ""])
    h.append(_tableau(tab, [5.4 * cm, 1.3 * cm, 2.6 * cm, 2.6 * cm, 2.1 * cm, 3.0 * cm], totaux=(len(tab) - 1,),
                      alignements_droite=(1, 3, 4)))
    conditions = []
    if pret["caution"] > 0:
        conditions.append(f"Caution demandée : <b>{eur(pret['caution'])}</b> (chèque non encaissé, restitué au retour "
                          "du matériel complet et en bon état).")
    conditions.append(f"Prix de location : <b>{eur(pret['montant_location'])}</b>." if pret["montant_location"] > 0
                      else "Prêt gracieux.")
    if str(pret.get("commentaire", "")).strip():
        conditions.append("Observations : " + str(pret["commentaire"]).replace("<", "&lt;"))
    h += [Spacer(1, 0.4 * cm)] + [Paragraph(c, STYLES["texte"]) for c in conditions]
    h += [Paragraph("Conditions", STYLES["h2"]), Paragraph(CONDITIONS_PRET, STYLES["note"])]

    def bloc(titre_bloc, date_bloc):
        cell = lambda qui: Paragraph(f"<b>{qui}</b><br/>Nom : ……………………………<br/><br/>Signature :<br/><br/><br/>",  # noqa: E731
                                     STYLES["texte"])
        t = Table([[Paragraph(f"<b>{titre_bloc}</b> — le {date_bloc}", STYLES["texte"]), ""],
                   [cell(f"Pour {nom_association}"), cell("Pour l'emprunteur")]], colWidths=[8.5 * cm, 8.5 * cm])
        t.setStyle(TableStyle([("SPAN", (0, 0), (1, 0)), ("BOX", (0, 0), (-1, -1), 0.5, BLEU),
                               ("BACKGROUND", (0, 0), (-1, 0), BLEU_CLAIR), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        return t

    h += [Spacer(1, 0.5 * cm),
          KeepTogether([bloc("Remise du matériel", _d(pret["date_depart"]) if pd.notna(pret["date_depart"])
                             else _d(pret["date_debut"]))]),
          Spacer(1, 0.4 * cm),
          KeepTogether([bloc("Retour du matériel", _d(pret["date_retour"]))]),
          Spacer(1, 0.3 * cm),
          Paragraph(f"Document édité le {date.today():%d/%m/%Y} — un exemplaire pour chaque partie.", STYLES["note"])]
    doc.build(h)
    return buf.getvalue()
