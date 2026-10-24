"""Calculs de stock (consommables) et de matériel.

Principes :
- chaque mouvement porte une quantité SIGNÉE (+ entrée, − sortie) et une manifestation ;
- le stock est valorisé au coût moyen unitaire pondéré (CMUP), recalculé à chaque achat ;
- chaque sortie (vente, offert, perte, écart d'inventaire négatif) est valorisée au CMUP du moment
  et imputée à la manifestation où elle a lieu : c'est le « coût consommé » ;
- les achats reliés au stock sortent du résultat réel des manifestations ; seul le coût consommé y entre.
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from metier import exercice_de, nombres, nouvel_id
from referentiel import JOURS_ALERTE_DLC, TABLES

TYPES_ENTREE_VALORISEE = {"achat", "don_recu"}


# ================================================================== consommables
def typer_mouvements(df: pd.DataFrame) -> pd.DataFrame:
    df = (df if not df.empty else pd.DataFrame(columns=TABLES["mouvements_stock"])).copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["dlc"] = pd.to_datetime(df["dlc"], errors="coerce")
    for col in ("quantite", "cout_unitaire"):
        df[col] = nombres(df[col]).fillna(0.0)
    return df


def valoriser(mvts: pd.DataFrame) -> pd.DataFrame:
    """Ajoute à chaque mouvement : cmup (après mouvement), stock_qte, stock_val et cout_consomme.

    cout_consomme > 0 pour une sortie, < 0 pour un écart d'inventaire positif, 0 pour une entrée.
    """
    if mvts.empty:
        return mvts.assign(cmup=[], stock_qte=[], stock_val=[], cout_consomme=[])
    resultat = []
    for _, groupe in mvts.sort_values(["date", "saisi_le"]).groupby("article_id", sort=False):
        qte = val = cmup = 0.0
        for r in groupe.to_dict("records"):
            q, cout = float(r["quantite"]), 0.0
            if q > 0 and r["type"] in TYPES_ENTREE_VALORISEE:
                val += q * float(r["cout_unitaire"])
                qte += q
                cmup = val / qte if qte > 0 else float(r["cout_unitaire"])
            elif q > 0:  # écart d'inventaire positif : on retrouve du stock
                cout = -q * cmup
                qte += q
                val += q * cmup
            else:  # sortie
                cout = -q * cmup
                qte += q
                val = qte * cmup if qte > 0 else 0.0
            resultat.append({**r, "cmup": round(cmup, 4), "stock_qte": round(qte, 3),
                             "stock_val": round(val, 2), "cout_consomme": round(cout, 2)})
    return pd.DataFrame(resultat)


def stock_theorique(mvts: pd.DataFrame, article_id: str, jusqu_au: date | None = None) -> float:
    x = mvts[mvts["article_id"] == article_id]
    if jusqu_au is not None:
        x = x[x["date"] <= pd.Timestamp(jusqu_au)]
    return float(x["quantite"].sum())


def _prochaine_dlc(m: pd.DataFrame):
    """DLC du plus ancien lot encore présent (sorties imputées en premier entré, premier sorti)."""
    entrees = m[m["quantite"] > 0].sort_values("date")
    a_consommer = -m.loc[m["quantite"] < 0, "quantite"].sum()
    for r in entrees.itertuples():
        reste = r.quantite - a_consommer
        a_consommer = max(0.0, a_consommer - r.quantite)
        if reste > 0 and pd.notna(r.dlc):
            return r.dlc.date()
    return None


def etat_stock(articles: pd.DataFrame, mvts: pd.DataFrame) -> pd.DataFrame:
    """Une ligne par article actif : quantité, CMUP, valeur, alertes."""
    val = valoriser(mvts)
    lignes = []
    aujourd_hui = date.today()
    for a in articles.to_dict("records"):
        if str(a.get("actif", "oui")).lower() == "non":
            continue
        m = mvts[mvts["article_id"] == a["id"]]
        v = val[val["article_id"] == a["id"]] if not val.empty else val
        qte = float(m["quantite"].sum())
        cmup = float(v["cmup"].iloc[-1]) if len(v) else 0.0
        seuil = pd.to_numeric(a.get("seuil_alerte"), errors="coerce")
        dlc = _prochaine_dlc(m) if qte > 0 else None
        alertes = []
        if pd.notna(seuil) and qte <= seuil:
            alertes.append("stock bas")
        if qte < 0:
            alertes.append("stock négatif : vérifier les saisies")
        if dlc and (dlc - aujourd_hui).days <= JOURS_ALERTE_DLC:
            alertes.append("périmé" if dlc < aujourd_hui else f"DLC le {dlc:%d/%m/%Y}")
        lignes.append({"id": a["id"], "article": a["nom"], "famille": a["famille"], "unite": a["unite"],
                       "quantite": round(qte, 2), "cmup": round(cmup, 2), "valeur": round(max(qte, 0) * cmup, 2),
                       "prochaine_dlc": dlc, "alerte": ", ".join(alertes)})
    return pd.DataFrame(lignes, columns=["id", "article", "famille", "unite", "quantite", "cmup", "valeur",
                                         "prochaine_dlc", "alerte"])


def valeur_stock_au(mvts: pd.DataFrame, jour: date) -> float:
    """Valeur du stock à une date (pour le bilan de fin d'exercice)."""
    x = valoriser(mvts[mvts["date"] <= pd.Timestamp(jour)])
    if x.empty:
        return 0.0
    return float(x.groupby("article_id")["stock_val"].last().clip(lower=0).sum())


def cout_consomme(mvts: pd.DataFrame, exercice: int, mois_debut: int = 1) -> pd.Series:
    """Coût des consommables utilisés, par manifestation, sur un exercice."""
    v = valoriser(mvts)
    if v.empty:
        return pd.Series(dtype=float)
    v = v[v["date"].map(lambda d: exercice_de(d, mois_debut) if pd.notna(d) else None) == exercice]
    return v.groupby("manifestation")["cout_consomme"].sum()


def resultat_reel(synthese_tresorerie: pd.DataFrame, ops: pd.DataFrame, mvts: pd.DataFrame,
                  exercice: int, mois_debut: int = 1) -> pd.DataFrame:
    """Résultat par manifestation : les achats mis en stock sont remplacés par le coût consommé."""
    ids_stockes = set(mvts["operation_id"].astype(str)) - {""}
    x = ops[(ops["exercice"] == exercice) & ops["id"].isin(ids_stockes) & (ops["sens"] == "Dépense")]
    achats_stockes = x.groupby("manifestation")["montant"].sum()
    conso = cout_consomme(mvts, exercice, mois_debut)
    s = synthese_tresorerie.set_index("manifestation")[["Recettes", "Dépenses"]].copy()
    for manif in set(conso.index) - set(s.index):
        s.loc[manif] = [0.0, 0.0]
    s["Achats mis en stock"] = achats_stockes.reindex(s.index).fillna(0)
    s["Coût consommé"] = conso.reindex(s.index).fillna(0)
    s["Dépenses"] = s["Dépenses"] - s["Achats mis en stock"] + s["Coût consommé"]
    s["Résultat"] = s["Recettes"] - s["Dépenses"]
    return s.reset_index().sort_values("Résultat", ascending=False)


def bilan_manifestation(articles: pd.DataFrame, mvts: pd.DataFrame, manifestation: str,
                        debut: date, fin: date) -> pd.DataFrame:
    """Par article, sur une édition : ventes, offerts, pertes, écart d'inventaire et coût."""
    v = valoriser(mvts)
    if v.empty:
        return pd.DataFrame()
    v = v[(v["manifestation"] == manifestation) & (v["date"] >= pd.Timestamp(debut)) & (v["date"] <= pd.Timestamp(fin))]
    if v.empty:
        return pd.DataFrame()
    t = v.pivot_table(index="article_id", columns="type", values="quantite", aggfunc="sum", fill_value=0)
    t = t.reindex(columns=["achat", "vente", "offert", "perte", "inventaire"], fill_value=0)
    t["coût consommé"] = v.groupby("article_id")["cout_consomme"].sum()
    noms = articles.set_index("id")["nom"]
    t.index = t.index.map(lambda i: noms.get(i, i))
    t = t.rename(columns={"achat": "Reçu", "vente": "Vendu", "offert": "Offert", "perte": "Perte",
                          "inventaire": "Écart inventaire"})
    for c in ("Vendu", "Offert", "Perte"):
        t[c] = -t[c]
    return t.reset_index().rename(columns={"article_id": "Article"})


def mouvement(jour: date, article_id: str, type_: str, quantite: float, manifestation: str,
              utilisateur: str, cout_unitaire: float = 0.0, operation_id: str = "",
              dlc: date | None = None, commentaire: str = "") -> dict:
    return {"id": nouvel_id(), "date": jour.isoformat(), "article_id": article_id, "type": type_,
            "quantite": round(float(quantite), 3), "cout_unitaire": round(float(cout_unitaire), 4),
            "manifestation": manifestation, "operation_id": operation_id,
            "dlc": dlc.isoformat() if dlc else "", "commentaire": commentaire, "saisi_par": utilisateur,
            "saisi_le": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")}


# ================================================================== matériel
def typer_sorties(df: pd.DataFrame) -> pd.DataFrame:
    df = (df if not df.empty else pd.DataFrame(columns=TABLES["sorties_materiel"])).copy()
    for col in ("quantite", "quantite_retour", "caution", "montant_location"):
        df[col] = nombres(df[col]).fillna(0.0)
    for col in ("date_sortie", "date_retour_prevue", "date_retour"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    df["en_cours"] = df["date_retour"].isna()
    return df


def disponibilite(materiel: pd.DataFrame, sorties: pd.DataFrame, pretes: pd.Series | None = None) -> pd.DataFrame:
    """Disponibilité immédiate : possédé − sorti pour une manifestation − prêté à une association."""
    m = materiel.copy()
    for col in ("quantite", "valeur_unitaire"):
        m[col] = nombres(m[col]).fillna(0.0)
    dehors = sorties[sorties["en_cours"]].groupby("materiel_id")["quantite"].sum()
    m["en_sortie"] = nombres(m["id"].map(dehors)).fillna(0.0)
    if pretes is not None and not pretes.empty:
        m["en_sortie"] = m["en_sortie"] + nombres(m["id"].map(pretes)).fillna(0.0)
    m["disponible"] = m["quantite"] - m["en_sortie"]
    m["valeur"] = m["quantite"] * m["valeur_unitaire"]
    return m


def retards(sorties: pd.DataFrame) -> pd.DataFrame:
    return sorties[sorties["en_cours"] & (sorties["date_retour_prevue"] < pd.Timestamp(date.today()))]
