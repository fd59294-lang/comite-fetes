"""Rapports PDF d'assemblée générale (ReportLab + graphiques matplotlib)."""
from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402
import pandas as pd  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_CENTER, TA_RIGHT  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,  # noqa: E402
                                Spacer, Table, TableStyle)

import metier  # noqa: E402
import stock  # noqa: E402
from referentiel import CATEGORIES_DEPENSES, CATEGORIES_RECETTES  # noqa: E402

# ------------------------------------------------------------------ polices et couleurs
_DOSSIER_POLICES = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
pdfmetrics.registerFont(TTFont("DejaVu", os.path.join(_DOSSIER_POLICES, "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", os.path.join(_DOSSIER_POLICES, "DejaVuSans-Bold.ttf")))
pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold")

BLEU = colors.HexColor("#1F4E79")
BLEU_CLAIR = colors.HexColor("#DCE6F1")
GRIS = colors.HexColor("#F2F2F2")
GRIS_TEXTE = colors.HexColor("#595959")
ROUGE = "#C62828"

STYLES = {
    "titre": ParagraphStyle("titre", fontName="DejaVu-Bold", fontSize=24, leading=30, textColor=BLEU,
                            alignment=TA_CENTER),
    "sous_titre": ParagraphStyle("sous_titre", fontName="DejaVu", fontSize=14, leading=20,
                                 textColor=GRIS_TEXTE, alignment=TA_CENTER),
    "h1": ParagraphStyle("h1", fontName="DejaVu-Bold", fontSize=16, leading=22, textColor=BLEU,
                         spaceBefore=4, spaceAfter=10),
    "h2": ParagraphStyle("h2", fontName="DejaVu-Bold", fontSize=12, leading=16, textColor=BLEU,
                         spaceBefore=10, spaceAfter=6),
    "texte": ParagraphStyle("texte", fontName="DejaVu", fontSize=9.5, leading=14),
    "note": ParagraphStyle("note", fontName="DejaVu", fontSize=8, leading=11, textColor=GRIS_TEXTE),
    "cellule": ParagraphStyle("cellule", fontName="DejaVu", fontSize=8.5, leading=11),
    "cellule_d": ParagraphStyle("cellule_d", fontName="DejaVu", fontSize=8.5, leading=11, alignment=TA_RIGHT),
}

SECTIONS = {
    "synthese": "Synthèse de l'exercice",
    "compte_resultat": "Compte de résultat",
    "manifestations": "Bilan par manifestation",
    "situation": "Situation en fin d'exercice",
    "budget": "Budget prévisionnel",
    "activite": "Rapport d'activité",
}


def eur(x: float, signe: bool = False) -> str:
    s = f"{abs(x):,.2f}".replace(",", " ").replace(".", ",") + " €"
    if x < 0:
        return "−" + s
    return ("+" + s) if signe and x > 0 else s


# ------------------------------------------------------------------ données d'entrée
@dataclass
class DonneesRapport:
    nom_association: str
    exercice: int
    mois_debut: int
    operations: pd.DataFrame          # brut (table « operations »)
    mouvements: pd.DataFrame          # brut (table « mouvements_stock »)
    articles: pd.DataFrame
    materiel: pd.DataFrame
    sorties: pd.DataFrame
    previsionnel: pd.DataFrame
    manifestations: pd.DataFrame
    solde_debut_banque: float | None = None
    solde_debut_caisse: float | None = None
    faits_marquants: str = ""
    signataires: dict = field(default_factory=dict)   # {"Le président": "Nom", ...}
    sections: list = field(default_factory=lambda: list(SECTIONS))


def libelle_exercice(ex: int, mois_debut: int) -> str:
    return str(ex) if mois_debut == 1 else f"{ex}-{ex + 1}"


def bornes_exercice(ex: int, mois_debut: int) -> tuple[date, date]:
    debut = date(ex, mois_debut, 1)
    fin = (pd.Timestamp(debut) + pd.DateOffset(years=1) - pd.Timedelta(days=1)).date()
    return debut, fin


# ------------------------------------------------------------------ éléments graphiques
def _tableau(lignes: list[list], largeurs: list[float], entete: bool = True, totaux: tuple = (),
             alignements_droite: tuple = ()) -> Table:
    data = []
    for i, ligne in enumerate(lignes):
        cellules = []
        for j, v in enumerate(ligne):
            if isinstance(v, Paragraph):
                cellules.append(v)
                continue
            style = STYLES["cellule_d"] if j in alignements_droite else STYLES["cellule"]
            texte = str(v)
            if (entete and i == 0) or i in totaux:
                texte = f"<b>{texte}</b>"
            cellules.append(Paragraph(texte, style))
        data.append(cellules)
    t = Table(data, colWidths=largeurs, repeatRows=1 if entete else 0)
    style = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
             ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9D9D9")),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if entete:
        style += [("BACKGROUND", (0, 0), (-1, 0), BLEU), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]
        for j in range(len(largeurs)):
            if data and isinstance(data[0][j], Paragraph):
                data[0][j].style = ParagraphStyle("e", parent=data[0][j].style, textColor=colors.white)
    for i in totaux:
        style.append(("BACKGROUND", (0, i), (-1, i), BLEU_CLAIR))
    t.setStyle(TableStyle(style))
    return t


def _image(fig, largeur_cm: float) -> Image:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    img = Image(buf)
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth = largeur_cm * cm
    img.drawHeight = largeur_cm * cm * ratio
    return img


def _style_axes(ax):
    for cote in ("top", "right", "left"):
        ax.spines[cote].set_visible(False)
    ax.tick_params(axis="both", labelsize=8, colors="#404040")
    ax.grid(axis="x", color="#E0E0E0", linewidth=0.6)
    ax.set_axisbelow(True)


def graphique_resultats(s_n: pd.DataFrame, s_n1: pd.DataFrame, ex: int, mois_debut: int):
    noms = list(s_n["manifestation"])
    for m in s_n1["manifestation"]:
        if m not in noms:
            noms.append(m)
    noms = noms[::-1]
    n = s_n.set_index("manifestation")["Résultat"].reindex(noms).fillna(0)
    n1 = s_n1.set_index("manifestation")["Résultat"].reindex(noms).fillna(0)
    hauteur = max(2.4, 0.42 * len(noms) + 0.8)
    fig, ax = plt.subplots(figsize=(7.2, hauteur))
    y = range(len(noms))
    if not s_n1.empty:
        ax.barh([i - 0.2 for i in y], n1.values, height=0.38, color="#A9C4DE")
    ax.barh([i + (0.2 if not s_n1.empty else 0) for i in y], n.values, height=0.38 if not s_n1.empty else 0.55,
            color=["#1F4E79" if v >= 0 else ROUGE for v in n.values])
    ax.set_yticks(list(y), noms, fontsize=8)
    ax.axvline(0, color="#808080", linewidth=0.8)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f} €".replace(",", " ")))
    _style_axes(ax)
    poignees = [matplotlib.patches.Patch(color="#1F4E79", label=libelle_exercice(ex, mois_debut))]
    if not s_n1.empty:
        poignees.append(matplotlib.patches.Patch(color="#A9C4DE", label=libelle_exercice(ex - 1, mois_debut)))
    if (n < 0).any():
        poignees.append(matplotlib.patches.Patch(color=ROUGE, label="déficit"))
    ax.legend(handles=poignees, fontsize=8, frameon=False, loc="lower right")
    return fig


def graphique_repartition(serie: pd.Series, titre: str):
    serie = serie[serie > 0].sort_values(ascending=False)
    if len(serie) > 6:
        serie = pd.concat([serie.iloc[:6], pd.Series({"Autres": serie.iloc[6:].sum()})])
    fig, ax = plt.subplots(figsize=(3.6, 3.0))
    palette = ["#1F4E79", "#2E75B6", "#5B9BD5", "#9DC3E6", "#BDD7EE", "#DEEBF7", "#C9C9C9"]
    if serie.empty:
        ax.text(0.5, 0.5, "Aucune donnée", ha="center", va="center", fontsize=9, color="#808080")
        ax.axis("off")
    else:
        ax.pie(serie.values, labels=None, colors=palette[:len(serie)], startangle=90, counterclock=False,
               wedgeprops={"width": 0.42, "edgecolor": "white"})
        ax.legend([f"{k} ({v / serie.sum():.0%})" for k, v in serie.items()], fontsize=6.5, frameon=False,
                  loc="upper center", bbox_to_anchor=(0.5, 0.02), ncol=1)
    ax.set_title(titre, fontsize=9, color="#1F4E79", fontweight="bold")
    return fig


# ------------------------------------------------------------------ calculs communs
def _nom_categorie(lib: str) -> str:
    return lib.split(" — ", 1)[1] if " — " in lib else lib


def _ordre_categories(sens: str) -> list[str]:
    source = CATEGORIES_RECETTES if sens == "Recette" else CATEGORIES_DEPENSES
    return [f"{c} — {n}" for c, n in source]


def _par_categorie(ops: pd.DataFrame, ex: int, sens: str) -> pd.Series:
    x = ops[(ops["exercice"] == ex) & (ops["sens"] == sens)]
    return x.groupby("categorie")["montant"].sum()


# ------------------------------------------------------------------ sections
def _page_garde(d: DonneesRapport) -> list:
    ex = libelle_exercice(d.exercice, d.mois_debut)
    debut, fin = bornes_exercice(d.exercice, d.mois_debut)
    contenu = [Spacer(1, 6 * cm), Paragraph(d.nom_association, STYLES["titre"]), Spacer(1, 0.6 * cm),
               Paragraph("Assemblée générale", STYLES["sous_titre"]),
               Paragraph(f"Exercice {ex}", STYLES["sous_titre"]), Spacer(1, 0.3 * cm),
               Paragraph(f"du {debut:%d/%m/%Y} au {fin:%d/%m/%Y}",
                         ParagraphStyle("p", parent=STYLES["note"], alignment=TA_CENTER, fontSize=10)),
               Spacer(1, 3 * cm)]
    sommaire = [[Paragraph(f"{i}.  {SECTIONS[s]}", STYLES["texte"])] for i, s in enumerate(d.sections, start=1)]
    if sommaire:
        t = Table(sommaire, colWidths=[9 * cm], hAlign="CENTER")
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9D9D9"))]))
        contenu.append(t)
    contenu += [Spacer(1, 2 * cm),
                Paragraph(f"Document établi le {date.today():%d/%m/%Y}",
                          ParagraphStyle("d", parent=STYLES["note"], alignment=TA_CENTER)), PageBreak()]
    return contenu


def _synthese(d: DonneesRapport, ops: pd.DataFrame, mvts: pd.DataFrame) -> list:
    ex = d.exercice
    def tot(e, sens):
        return ops.loc[(ops["exercice"] == e) & (ops["sens"] == sens), "montant"].sum()
    rec, dep = tot(ex, "Recette"), tot(ex, "Dépense")
    rec1, dep1 = tot(ex - 1, "Recette"), tot(ex - 1, "Dépense")
    a_n1 = (ops["exercice"] == ex - 1).any()
    lignes = [["", libelle_exercice(ex, d.mois_debut)] + ([libelle_exercice(ex - 1, d.mois_debut), "Évolution"] if a_n1 else [])]
    for lib, v, v1 in (("Recettes", rec, rec1), ("Dépenses", dep, dep1), ("Résultat", rec - dep, rec1 - dep1)):
        lignes.append([lib, eur(v)] + ([eur(v1), eur(v - v1, signe=True)] if a_n1 else []))
    larg = [5 * cm, 3.6 * cm] + ([3.6 * cm, 3.6 * cm] if a_n1 else [])
    contenu = [Paragraph(SECTIONS["synthese"], STYLES["h1"]),
               _tableau(lignes, larg, totaux=(3,), alignements_droite=(1, 2, 3)), Spacer(1, 0.5 * cm)]
    resultat = rec - dep
    phrase = (f"L'exercice se termine par un <b>{'excédent' if resultat >= 0 else 'déficit'}</b> de "
              f"<b>{eur(abs(resultat))}</b>")
    if a_n1:
        ecart = resultat - (rec1 - dep1)
        phrase += f", soit {eur(ecart, signe=True)} par rapport à l'exercice précédent"
    contenu.append(Paragraph(phrase + ".", STYLES["texte"]))
    s_n = metier.synthese_manifestations(ops, ex)
    fetes = s_n[s_n["manifestation"] != "Fonctionnement général"]
    if not fetes.empty:
        meilleure = fetes.iloc[0]
        contenu.append(Paragraph(f"La manifestation la plus rentable est <b>{meilleure['manifestation']}</b> "
                                 f"({eur(meilleure['Résultat'])}).", STYLES["texte"]))
    if not s_n.empty:
        contenu += [Paragraph("Résultat par manifestation (trésorerie)", STYLES["h2"]),
                    _image(graphique_resultats(s_n, metier.synthese_manifestations(ops, ex - 1), ex, d.mois_debut), 16)]
    rep = Table([[_image(graphique_repartition(_par_categorie(ops, ex, "Recette").rename(_nom_categorie), "Origine des recettes"), 8),
                  _image(graphique_repartition(_par_categorie(ops, ex, "Dépense").rename(_nom_categorie), "Nature des dépenses"), 8)]],
                colWidths=[8.5 * cm, 8.5 * cm])
    contenu += [Spacer(1, 0.3 * cm), KeepTogether([rep]), PageBreak()]
    return contenu


def _compte_resultat(d: DonneesRapport, ops: pd.DataFrame) -> list:
    ex = d.exercice
    a_n1 = (ops["exercice"] == ex - 1).any()
    contenu = [Paragraph(SECTIONS["compte_resultat"], STYLES["h1"]),
               Paragraph("Présentation selon le plan comptable des associations (comptabilité de trésorerie).",
                         STYLES["note"]), Spacer(1, 0.3 * cm)]
    totaux = {}
    for sens, titre in (("Dépense", "Charges"), ("Recette", "Produits")):
        n, n1 = _par_categorie(ops, ex, sens), _par_categorie(ops, ex - 1, sens)
        entete = [titre, "Compte", libelle_exercice(ex, d.mois_debut)] + ([libelle_exercice(ex - 1, d.mois_debut)] if a_n1 else [])
        lignes = [entete]
        for lib in _ordre_categories(sens):
            v, v1 = float(n.get(lib, 0)), float(n1.get(lib, 0))
            if v == 0 and v1 == 0:
                continue
            code = lib.split(" — ")[0]
            lignes.append([_nom_categorie(lib), code, eur(v)] + ([eur(v1)] if a_n1 else []))
        totaux[sens] = (n.sum(), n1.sum())
        lignes.append([f"Total des {titre.lower()}", "", eur(n.sum())] + ([eur(n1.sum())] if a_n1 else []))
        larg = [8.2 * cm, 1.8 * cm, 3.4 * cm] + ([3.4 * cm] if a_n1 else [])
        contenu += [_tableau(lignes, larg, totaux=(len(lignes) - 1,), alignements_droite=(2, 3)), Spacer(1, 0.5 * cm)]
    res, res1 = totaux["Recette"][0] - totaux["Dépense"][0], totaux["Recette"][1] - totaux["Dépense"][1]
    lignes = [["Résultat de l'exercice", "", eur(res)] + ([eur(res1)] if a_n1 else []),
              ["", "", "excédent" if res >= 0 else "déficit"] + (["excédent" if res1 >= 0 else "déficit"] if a_n1 else [])]
    larg = [8.2 * cm, 1.8 * cm, 3.4 * cm] + ([3.4 * cm] if a_n1 else [])
    contenu += [_tableau(lignes, larg, entete=False, totaux=(0,), alignements_droite=(2, 3))]
    a_classer = ops[(ops["exercice"] == ex) & ops["categorie"].str.contains("À classer", regex=False)]
    if not a_classer.empty:
        contenu.append(Spacer(1, 0.3 * cm))
        contenu.append(Paragraph(f"⚠ {len(a_classer)} opération(s) pour {eur(a_classer['montant'].sum())} restent "
                                 "« à classer » : pensez à leur attribuer une catégorie avant l'AG.", STYLES["note"]))
    contenu.append(PageBreak())
    return contenu


def _manifestations(d: DonneesRapport, ops: pd.DataFrame, mvts: pd.DataFrame) -> list:
    ex = d.exercice
    s_n = metier.synthese_manifestations(ops, ex)
    contenu = [Paragraph(SECTIONS["manifestations"], STYLES["h1"])]
    if s_n.empty:
        return contenu + [Paragraph("Aucune opération sur cet exercice.", STYLES["texte"]), PageBreak()]
    reel = stock.resultat_reel(s_n, ops, mvts, ex, d.mois_debut).set_index("manifestation")
    s_n1 = metier.synthese_manifestations(ops, ex - 1).set_index("manifestation")
    avec_stock = not mvts.empty
    entete = ["Manifestation", "Recettes", "Dépenses", "Résultat"] + (["Résultat réel*"] if avec_stock else []) + \
             ([f"Résultat {libelle_exercice(ex - 1, d.mois_debut)}"] if not s_n1.empty else [])
    lignes = [entete]
    for r in s_n.itertuples(index=False):
        m = r.manifestation
        ligne = [m, eur(r.Recettes), eur(r.Dépenses), eur(r.Résultat)]
        if avec_stock:
            ligne.append(eur(float(reel["Résultat"].get(m, r.Résultat))))
        if not s_n1.empty:
            ligne.append(eur(float(s_n1["Résultat"].get(m, 0))) if m in s_n1.index else "—")
        lignes.append(ligne)
    total = ["Total", eur(s_n["Recettes"].sum()), eur(s_n["Dépenses"].sum()), eur(s_n["Résultat"].sum())]
    if avec_stock:
        total.append(eur(float(reel["Résultat"].sum())))
    if not s_n1.empty:
        total.append(eur(float(s_n1["Résultat"].sum())))
    lignes.append(total)
    nb = len(entete)
    larg = [5.2 * cm] + [(17 - 5.2) / (nb - 1) * cm] * (nb - 1)
    contenu += [_tableau(lignes, larg, totaux=(len(lignes) - 1,), alignements_droite=tuple(range(1, nb)))]
    if avec_stock:
        contenu.append(Spacer(1, 0.2 * cm))
        contenu.append(Paragraph("* Résultat réel : les achats de boissons et d'alimentation mis en stock sont remplacés "
                                 "par le coût de ce qui a été réellement consommé lors de la manifestation ; les "
                                 "invendus restent en stock et sont imputés à la manifestation où ils sont vendus.",
                                 STYLES["note"]))
    # détail par manifestation
    for r in s_n.itertuples(index=False):
        x = ops[(ops["exercice"] == ex) & (ops["manifestation"] == r.manifestation)]
        detail = x.groupby(["sens", "categorie"])["montant"].sum().reset_index()
        lignes = [["Poste", "Recettes", "Dépenses"]]
        for _, l in detail.sort_values(["sens", "montant"], ascending=[False, False]).iterrows():
            lignes.append([_nom_categorie(l["categorie"]), eur(l["montant"]) if l["sens"] == "Recette" else "",
                           eur(l["montant"]) if l["sens"] == "Dépense" else ""])
        lignes.append(["Résultat", "", eur(r.Résultat)])
        dates = x["date"].dropna()
        periode = f" — {dates.min():%d/%m/%Y}" + (f" au {dates.max():%d/%m/%Y}" if dates.max() != dates.min() else "") \
            if not dates.empty else ""
        bloc = [Paragraph(f"{r.manifestation}{periode}", STYLES["h2"]),
                _tableau(lignes, [9 * cm, 4 * cm, 4 * cm], totaux=(len(lignes) - 1,), alignements_droite=(1, 2))]
        if avec_stock and r.manifestation in reel.index and reel.loc[r.manifestation, "Coût consommé"] != 0:
            rr = reel.loc[r.manifestation]
            bloc.append(Paragraph(
                f"Stock : achats mis en stock {eur(rr['Achats mis en stock'])}, coût réellement consommé "
                f"{eur(rr['Coût consommé'])} → résultat réel <b>{eur(rr['Résultat'])}</b>.", STYLES["note"]))
        contenu.append(KeepTogether(bloc))
    contenu.append(PageBreak())
    return contenu


def _situation(d: DonneesRapport, ops: pd.DataFrame, mvts: pd.DataFrame) -> list:
    ex = d.exercice
    debut, fin = bornes_exercice(ex, d.mois_debut)
    x = ops[ops["exercice"] == ex]
    contenu = [Paragraph(SECTIONS["situation"], STYLES["h1"]), Paragraph("Trésorerie", STYLES["h2"])]
    if d.solde_debut_banque is not None or d.solde_debut_caisse is not None:
        lignes = [["", f"Au {debut:%d/%m/%Y}", "Entrées", "Sorties", f"Au {fin:%d/%m/%Y}"]]
        total_debut = total_fin = 0.0
        for lib, solde, especes in (("Banque", d.solde_debut_banque, False), ("Caisse (espèces)", d.solde_debut_caisse, True)):
            if solde is None:
                continue
            y = x[(x["mode_paiement"] == "Espèces") == especes]
            entrees = y.loc[y["sens"] == "Recette", "montant"].sum()
            sorties = y.loc[y["sens"] == "Dépense", "montant"].sum()
            fin_solde = solde + entrees - sorties
            total_debut += solde
            total_fin += fin_solde
            lignes.append([lib, eur(solde), eur(entrees), eur(sorties), eur(fin_solde)])
        lignes.append(["Total", eur(total_debut), "", "", eur(total_fin)])
        contenu += [_tableau(lignes, [4.2 * cm, 3.2 * cm, 3.2 * cm, 3.2 * cm, 3.2 * cm], totaux=(len(lignes) - 1,),
                             alignements_droite=(1, 2, 3, 4)),
                    Paragraph("Soldes de fin calculés à partir des soldes d'ouverture saisis et des opérations "
                              "enregistrées (paiements en espèces = caisse, autres modes = banque). "
                              "À rapprocher des relevés bancaires et du comptage de caisse.", STYLES["note"])]
    else:
        contenu.append(Paragraph("Soldes d'ouverture non renseignés : la situation de trésorerie n'est pas calculée.",
                                 STYLES["note"]))

    contenu.append(Paragraph("Stock de boissons et d'alimentation", STYLES["h2"]))
    if mvts.empty or d.articles.empty:
        contenu.append(Paragraph("Aucun stock suivi.", STYLES["note"]))
    else:
        v = stock.valoriser(mvts[mvts["date"] <= pd.Timestamp(fin)])
        derniers = v.sort_values(["date", "saisi_le"]).groupby("article_id").last() if not v.empty else pd.DataFrame()
        art = d.articles.set_index("id")
        lignes = [["Article", "Famille", "Quantité", "Coût moyen", "Valeur"]]
        total = 0.0
        for aid, r in derniers.iterrows():
            if r["stock_qte"] <= 0:
                continue
            valeur = max(r["stock_val"], 0)
            total += valeur
            lignes.append([art["nom"].get(aid, aid), art["famille"].get(aid, ""), f"{r['stock_qte']:g}",
                           eur(r["cmup"]), eur(valeur)])
        lignes.append(["Total", "", "", "", eur(total)])
        if len(lignes) == 2:
            contenu.append(Paragraph(f"Aucun stock au {fin:%d/%m/%Y}.", STYLES["note"]))
        else:
            contenu += [_tableau(lignes, [5.8 * cm, 3.4 * cm, 2.4 * cm, 2.6 * cm, 2.8 * cm], totaux=(len(lignes) - 1,),
                                 alignements_droite=(2, 3, 4)),
                        Paragraph(f"Stock valorisé au coût moyen pondéré au {fin:%d/%m/%Y}. Il sera consommé lors des "
                              "prochaines manifestations.", STYLES["note"])]

    contenu.append(Paragraph("Matériel de l'association", STYLES["h2"]))
    if d.materiel.empty:
        contenu.append(Paragraph("Aucun matériel inventorié.", STYLES["note"]))
    else:
        dispo = stock.disponibilite(d.materiel, stock.typer_sorties(d.sorties))
        par_cat = dispo.groupby("categorie").agg(pieces=("quantite", "sum"), valeur=("valeur", "sum")).reset_index()
        lignes = [["Catégorie", "Nombre de pièces", "Valeur"]]
        for r in par_cat.itertuples(index=False):
            lignes.append([r.categorie, f"{r.pieces:g}", eur(r.valeur)])
        lignes.append(["Total", f"{par_cat['pieces'].sum():g}", eur(par_cat["valeur"].sum())])
        contenu.append(_tableau(lignes, [8 * cm, 4 * cm, 5 * cm], totaux=(len(lignes) - 1,), alignements_droite=(1, 2)))
        a_reparer = dispo[dispo["etat"].isin(["À réparer", "Hors service"])]
        if not a_reparer.empty:
            contenu.append(Paragraph("À réparer ou hors service : " + ", ".join(
                f"{r.nom} ({r.etat.lower()})" for r in a_reparer.itertuples()) + ".", STYLES["note"]))
    contenu.append(PageBreak())
    return contenu


def _budget(d: DonneesRapport, ops: pd.DataFrame) -> list:
    cible = d.exercice + 1
    lib = libelle_exercice(cible, d.mois_debut)
    contenu = [Paragraph(f"{SECTIONS['budget']} {lib}", STYLES["h1"])]
    p = d.previsionnel[metier.nombres(d.previsionnel["exercice"]) == cible] if not d.previsionnel.empty else d.previsionnel
    if p.empty:
        actives = d.manifestations[d.manifestations["actif"].astype(str).str.lower() != "non"]["nom"].tolist()
        base = metier.base_previsionnelle(ops, actives, cible, "moyenne3", 3, 0)
        contenu.append(Paragraph("Aucun budget enregistré pour cet exercice : proposition calculée automatiquement "
                                 "(moyenne des trois dernières éditions, dépenses +3 %). Vous pouvez l'ajuster "
                                 "dans l'écran « Prévisionnel ».", STYLES["note"]))
        p = base.assign(commentaire="")
    else:
        p = p.assign(recettes=metier.nombres(p["recettes"]).fillna(0), depenses=metier.nombres(p["depenses"]).fillna(0))
    p = p.assign(resultat=p["recettes"] - p["depenses"])
    lignes = [["Manifestation", "Recettes prévues", "Dépenses prévues", "Résultat prévu", "Commentaire"]]
    for r in p.itertuples(index=False):
        lignes.append([r.manifestation, eur(r.recettes), eur(r.depenses), eur(r.resultat), str(r.commentaire or "")])
    lignes.append(["Total", eur(p["recettes"].sum()), eur(p["depenses"].sum()), eur(p["resultat"].sum()), ""])
    contenu += [Spacer(1, 0.2 * cm),
                _tableau(lignes, [4.6 * cm, 2.9 * cm, 2.9 * cm, 2.9 * cm, 3.7 * cm], totaux=(len(lignes) - 1,),
                         alignements_droite=(1, 2, 3)),
                Spacer(1, 0.4 * cm),
                Paragraph(f"Budget présenté au vote de l'assemblée générale pour l'exercice {lib}.", STYLES["texte"]),
                PageBreak()]
    return contenu


def _activite(d: DonneesRapport, ops: pd.DataFrame) -> list:
    ex = d.exercice
    x = ops[(ops["exercice"] == ex)]
    contenu = [Paragraph(SECTIONS["activite"], STYLES["h1"])]
    if d.faits_marquants.strip():
        contenu.append(Paragraph("Faits marquants", STYLES["h2"]))
        for para in d.faits_marquants.strip().split("\n"):
            if para.strip():
                contenu.append(Paragraph(para.strip().replace("&", "&amp;").replace("<", "&lt;"), STYLES["texte"]))
    contenu.append(Paragraph("Manifestations de l'exercice", STYLES["h2"]))
    lignes = [["Manifestation", "Date(s)", "Recettes", "Résultat"]]
    s = metier.synthese_manifestations(ops, ex).set_index("manifestation")
    ordre = x[x["manifestation"] != "Fonctionnement général"].groupby("manifestation")["date"].min().sort_values()
    for m, d0 in ordre.items():
        d1 = x.loc[x["manifestation"] == m, "date"].max()
        dates = f"{d0:%d/%m/%Y}" + (f" au {d1:%d/%m/%Y}" if pd.notna(d1) and d1 != d0 else "") if pd.notna(d0) else ""
        lignes.append([m, dates, eur(float(s["Recettes"].get(m, 0))), eur(float(s["Résultat"].get(m, 0)))])
    if len(lignes) == 1:
        contenu.append(Paragraph("Aucune manifestation enregistrée.", STYLES["note"]))
    else:
        contenu.append(_tableau(lignes, [6 * cm, 4.6 * cm, 3.2 * cm, 3.2 * cm], alignements_droite=(2, 3)))
        contenu.append(Paragraph("Dates déduites des opérations enregistrées pour chaque manifestation.", STYLES["note"]))
    return contenu


def _signatures(d: DonneesRapport) -> list:
    if not d.signataires:
        return []
    cellules = [[Paragraph(f"<b>{role}</b><br/>{nom}<br/><br/><br/>Signature", STYLES["texte"])
                 for role, nom in d.signataires.items()]]
    t = Table(cellules, colWidths=[17 * cm / len(d.signataires)] * len(d.signataires))
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return [Spacer(1, 1.2 * cm), Paragraph(f"Fait le {date.today():%d/%m/%Y}", STYLES["texte"]), Spacer(1, 0.4 * cm),
            KeepTogether([t])]


# ------------------------------------------------------------------ assemblage
def generer_pdf(d: DonneesRapport) -> bytes:
    ops = metier.typer_operations(d.operations)
    mvts = stock.typer_mouvements(d.mouvements)
    buf = io.BytesIO()
    titre_pied = f"{d.nom_association} — Assemblée générale {libelle_exercice(d.exercice, d.mois_debut)}"

    def pied(canvas, doc):
        if doc.page == 1:
            return
        canvas.saveState()
        canvas.setFont("DejaVu", 7.5)
        canvas.setFillColor(GRIS_TEXTE)
        canvas.drawString(2 * cm, 1.2 * cm, titre_pied)
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Page {doc.page}")
        canvas.setStrokeColor(BLEU)
        canvas.setLineWidth(0.6)
        canvas.line(2 * cm, A4[1] - 1.4 * cm, A4[0] - 2 * cm, A4[1] - 1.4 * cm)
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm,
                            bottomMargin=2 * cm, title=titre_pied, author=d.nom_association)
    histoire = _page_garde(d)
    for s in d.sections:
        if s == "synthese":
            histoire += _synthese(d, ops, mvts)
        elif s == "compte_resultat":
            histoire += _compte_resultat(d, ops)
        elif s == "manifestations":
            histoire += _manifestations(d, ops, mvts)
        elif s == "situation":
            histoire += _situation(d, ops, mvts)
        elif s == "budget":
            histoire += _budget(d, ops)
        elif s == "activite":
            histoire += _activite(d, ops)
    while histoire and isinstance(histoire[-1], PageBreak):
        histoire.pop()
    histoire += _signatures(d)
    doc.build(histoire, onFirstPage=pied, onLaterPages=pied)
    return buf.getvalue()
