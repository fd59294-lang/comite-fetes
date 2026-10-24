"""Calculs des prêts de matériel aux associations : disponibilité sur une période, durées, statistiques."""
from __future__ import annotations

from datetime import date

import pandas as pd

from metier import nombres
from referentiel import MOIS, TABLES

STATUTS_ACTIFS = {"reserve", "en_cours"}


# ------------------------------------------------------------------ typage
def typer_prets(df: pd.DataFrame) -> pd.DataFrame:
    df = (df if not df.empty else pd.DataFrame(columns=TABLES["prets"])).copy()
    for col in ("date_debut", "date_fin_prevue", "date_depart", "date_retour"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in ("caution", "montant_location"):
        df[col] = nombres(df[col]).fillna(0.0)
    return df


def typer_lignes(df: pd.DataFrame) -> pd.DataFrame:
    df = (df if not df.empty else pd.DataFrame(columns=TABLES["prets_lignes"])).copy()
    df["quantite"] = nombres(df["quantite"]).fillna(0.0)
    df["quantite_rendue"] = nombres(df["quantite_rendue"])
    return df


def numero_suivant(prets: pd.DataFrame, jour: date) -> str:
    prefixe = f"P-{jour.year}-"
    deja = prets["numero"].astype(str)
    nums = [int(n.rsplit("-", 1)[1]) for n in deja if n.startswith(prefixe) and n.rsplit("-", 1)[1].isdigit()]
    return f"{prefixe}{(max(nums) + 1 if nums else 1):03d}"


# ------------------------------------------------------------------ occupation et disponibilité
def _chevauche(debut1, fin1, debut2, fin2) -> bool:
    return pd.notna(debut1) and pd.notna(fin1) and debut1 <= pd.Timestamp(fin2) and fin1 >= pd.Timestamp(debut2)


def occupation(prets: pd.DataFrame, lignes: pd.DataFrame, sorties: pd.DataFrame, debut: date, fin: date,
               exclure_pret: str | None = None) -> pd.DataFrame:
    """Quantités déjà engagées sur [debut, fin], par matériel : prêts réservés/en cours + sorties de manifestation.

    Renvoie un tableau materiel_id, quantite, motif (une ligne par engagement).
    """
    lignes_occ = []
    actifs = prets[prets["statut"].isin(STATUTS_ACTIFS)]
    if exclure_pret:
        actifs = actifs[actifs["id"] != exclure_pret]
    for p in actifs.itertuples():
        fin_p = p.date_fin_prevue
        if p.statut == "en_cours" and pd.notna(fin_p) and fin_p < pd.Timestamp(date.today()):
            fin_p = pd.Timestamp(fin)  # en retard : reste dehors tant qu'il n'est pas rendu
        if _chevauche(p.date_debut, fin_p, debut, fin):
            for l in lignes[lignes["pret_id"] == p.id].itertuples():
                lignes_occ.append({"materiel_id": l.materiel_id, "quantite": l.quantite,
                                   "motif": f"prêt {p.numero}"})
    if not sorties.empty:
        for s in sorties[sorties["en_cours"]].itertuples():
            fin_s = s.date_retour_prevue if pd.notna(s.date_retour_prevue) and \
                s.date_retour_prevue >= pd.Timestamp(date.today()) else pd.Timestamp(fin)
            if _chevauche(s.date_sortie, fin_s, debut, fin):
                lignes_occ.append({"materiel_id": s.materiel_id, "quantite": s.quantite,
                                   "motif": s.manifestation or s.emprunteur or "sortie"})
    return pd.DataFrame(lignes_occ, columns=["materiel_id", "quantite", "motif"])


def disponibilite_periode(materiel: pd.DataFrame, prets: pd.DataFrame, lignes: pd.DataFrame,
                          sorties: pd.DataFrame, debut: date, fin: date,
                          exclure_pret: str | None = None) -> pd.DataFrame:
    """Par matériel : quantité possédée (hors « Hors service »), engagée sur la période et disponible."""
    m = materiel.copy()
    m["quantite"] = nombres(m["quantite"]).fillna(0.0)
    m["utilisable"] = m["quantite"].where(m["etat"] != "Hors service", 0.0)
    occ = occupation(prets, lignes, sorties, debut, fin, exclure_pret)
    engage = occ.groupby("materiel_id")["quantite"].sum() if not occ.empty else pd.Series(dtype=float)
    motifs = occ.groupby("materiel_id")["motif"].apply(lambda x: ", ".join(sorted(set(x)))) if not occ.empty \
        else pd.Series(dtype=str)
    m["engage"] = nombres(m["id"].map(engage)).fillna(0.0)
    m["disponible"] = (m["utilisable"] - m["engage"]).clip(lower=0)
    m["motifs"] = m["id"].map(motifs).fillna("")
    return m


def quantites_pretees_maintenant(prets: pd.DataFrame, lignes: pd.DataFrame) -> pd.Series:
    """Matériel actuellement chez un emprunteur (prêts en cours), par materiel_id."""
    ids = prets.loc[prets["statut"] == "en_cours", "id"]
    x = lignes[lignes["pret_id"].isin(ids)]
    return x.groupby("materiel_id")["quantite"].sum() if not x.empty else pd.Series(dtype=float)


def fetes_sur_periode(manifestations: pd.DataFrame, debut: date, fin: date) -> list[str]:
    """Manifestations du comité habituellement organisées dans les mois couverts par la période."""
    mois = set(pd.date_range(pd.Timestamp(debut).replace(day=1), pd.Timestamp(fin), freq="MS").month)
    mois.add(fin.month)
    m = manifestations[manifestations["actif"].astype(str).str.lower() != "non"].copy()
    m["mois"] = nombres(m["mois"]).fillna(0).astype(int)
    return [f"{r.nom} ({MOIS[r.mois].lower()})" for r in m.itertuples() if r.mois in mois]


# ------------------------------------------------------------------ suivi
def duree_jours(p) -> int | None:
    depart = p["date_depart"] if pd.notna(p["date_depart"]) else p["date_debut"]
    if pd.isna(depart):
        return None
    if p["statut"] == "rendu" and pd.notna(p["date_retour"]):
        fin = p["date_retour"]
    elif p["statut"] == "en_cours":
        fin = pd.Timestamp(date.today())
    else:
        fin = p["date_fin_prevue"]
    return None if pd.isna(fin) else int((fin - depart).days) + 1


def en_retard(prets: pd.DataFrame) -> pd.DataFrame:
    aujourd_hui = pd.Timestamp(date.today())
    return prets[(prets["statut"] == "en_cours") & (prets["date_fin_prevue"] < aujourd_hui)]


def a_preparer(prets: pd.DataFrame, jours: int = 7) -> pd.DataFrame:
    """Réservations qui démarrent dans les `jours` prochains jours (ou dépassées sans remise)."""
    limite = pd.Timestamp(date.today()) + pd.Timedelta(days=jours)
    return prets[(prets["statut"] == "reserve") & (prets["date_debut"] <= limite)]


def tableau_suivi(prets: pd.DataFrame, lignes: pd.DataFrame, associations: pd.DataFrame,
                  materiel: pd.DataFrame) -> pd.DataFrame:
    """Une ligne par prêt : qui, quoi, quand, combien de temps, statut, retard."""
    noms_asso = associations.set_index("id")["nom"] if not associations.empty else pd.Series(dtype=str)
    noms_mat = materiel.set_index("id")["nom"] if not materiel.empty else pd.Series(dtype=str)
    res = []
    aujourd_hui = pd.Timestamp(date.today())
    for _, p in prets.iterrows():
        l = lignes[lignes["pret_id"] == p["id"]]
        articles = ", ".join(f"{noms_mat.get(r.materiel_id, '?')} ×{r.quantite:g}" for r in l.itertuples())
        retard = 0
        if p["statut"] == "en_cours" and pd.notna(p["date_fin_prevue"]) and p["date_fin_prevue"] < aujourd_hui:
            retard = (aujourd_hui - p["date_fin_prevue"]).days
        elif p["statut"] == "rendu" and pd.notna(p["date_retour"]) and pd.notna(p["date_fin_prevue"]):
            retard = max(0, (p["date_retour"] - p["date_fin_prevue"]).days)
        manquants = (l["quantite"] - l["quantite_rendue"].fillna(l["quantite"])).clip(lower=0).sum() \
            if p["statut"] == "rendu" else 0
        res.append({"id": p["id"], "numero": p["numero"], "association": noms_asso.get(p["association_id"], "?"),
                    "articles": articles, "pieces": l["quantite"].sum(), "date_debut": p["date_debut"],
                    "date_fin_prevue": p["date_fin_prevue"], "date_retour": p["date_retour"],
                    "duree": duree_jours(p), "statut": p["statut"], "retard": retard, "manquants": manquants})
    return pd.DataFrame(res, columns=["id", "numero", "association", "articles", "pieces", "date_debut",
                                      "date_fin_prevue", "date_retour", "duree", "statut", "retard", "manquants"])


def statistiques_associations(suivi: pd.DataFrame) -> pd.DataFrame:
    x = suivi[suivi["statut"] != "annule"]
    if x.empty:
        return pd.DataFrame(columns=["association", "prets", "jours", "pieces", "retards", "manquants", "dernier"])
    return (x.groupby("association")
              .agg(prets=("id", "count"), jours=("duree", "sum"), pieces=("pieces", "sum"),
                   retards=("retard", lambda r: int((r > 0).sum())), manquants=("manquants", "sum"),
                   dernier=("date_debut", "max"))
              .reset_index().sort_values("prets", ascending=False))


def materiel_le_plus_prete(prets: pd.DataFrame, lignes: pd.DataFrame, materiel: pd.DataFrame) -> pd.DataFrame:
    ids = prets.loc[prets["statut"].isin({"en_cours", "rendu"}), "id"]
    x = lignes[lignes["pret_id"].isin(ids)]
    if x.empty:
        return pd.DataFrame(columns=["materiel", "fois", "pieces"])
    noms = materiel.set_index("id")["nom"] if not materiel.empty else pd.Series(dtype=str)
    t = x.groupby("materiel_id").agg(fois=("pret_id", "nunique"), pieces=("quantite", "sum")).reset_index()
    t["materiel"] = t["materiel_id"].map(noms).fillna("?")
    return t[["materiel", "fois", "pieces"]].sort_values("fois", ascending=False)
