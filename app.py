"""Comité des Fêtes — logiciel de gestion (Streamlit).

Lancement local :  streamlit run app.py
"""
from __future__ import annotations

from datetime import date, datetime

import altair as alt
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Comité des Fêtes — Gestion", page_icon="🎪", layout="wide")

import metier  # noqa: E402
import stock
from commun import (flash, afficher_flash, backend, charger, rafraichir, eur, parametre, ecrire_parametre, journaliser,
                    initialiser_si_vide, operations, manifestations, mois_debut, peut_saisir, est_admin)
from ecrans_stock import page_materiel, page_stock
from referentiel import (APP_NOM, MANIFESTATION_GENERALE, MODES_PAIEMENT, MOIS, ROLES, ROLES_ADMIN,
                         ROLES_LIBELLES, SENS, TABLES, categories)
from stockage import lire_config


# ====================================================================== accès
def auth_configuree() -> bool:
    try:
        return "auth" in st.secrets
    except Exception:
        return False


def controler_acces() -> bool:
    conf = lire_config()
    if not auth_configuree():
        if conf.get("backend") == "google":
            st.error("Connexion Google non configurée (section [auth] absente des secrets) : accès bloqué "
                     "pour protéger les données de l'association. Voir le guide de mise en ligne, étapes 4 et 6.")
            return False
        st.session_state.update(email="essai.local", nom="Mode essai", role="president")
        return True
    if not st.user.is_logged_in:
        st.title(f"🎪 {APP_NOM}")
        st.write("Connectez-vous avec votre compte Google pour accéder au logiciel.")
        st.button("Se connecter avec Google", type="primary", on_click=st.login)
        return False
    email = str(st.user.email).lower()
    users = charger("utilisateurs")
    ligne = users[users["email"].str.lower() == email]
    if ligne.empty and email in (conf.get("admins") or []):
        backend().ajouter("utilisateurs", [{"email": email, "nom": st.user.get("name", email), "role": "president"}])
        rafraichir()
        ligne = charger("utilisateurs")
        ligne = ligne[ligne["email"].str.lower() == email]
    if ligne.empty:
        st.error(f"Le compte {email} n'est pas autorisé. Demandez au président ou au trésorier de vous ajouter.")
        st.button("Se déconnecter", on_click=st.logout)
        return False
    st.session_state.update(email=email, nom=ligne.iloc[0]["nom"] or email, role=ligne.iloc[0]["role"])
    return True


def sauvegarde_automatique() -> None:
    """Une sauvegarde Excel horodatée par jour, déclenchée à la première connexion."""
    aujourd_hui = date.today().isoformat()
    if st.session_state.get("sauvegarde_verifiee") == aujourd_hui:
        return
    st.session_state["sauvegarde_verifiee"] = aujourd_hui
    if parametre("derniere_sauvegarde") == aujourd_hui or charger("operations").empty:
        return
    try:
        faire_sauvegarde("automatique")
    except Exception as e:
        st.toast(f"Sauvegarde automatique impossible : {e}", icon="⚠️")


def faire_sauvegarde(origine: str) -> str:
    tables = {t: charger(t) for t in TABLES}
    contenu = metier.export_excel(tables)
    nom = f"sauvegarde_{datetime.now():%Y-%m-%d_%Hh%M}.xlsx"
    lieu = backend().enregistrer_fichier(
        "Sauvegardes", nom, contenu, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    ecrire_parametre("derniere_sauvegarde", date.today().isoformat())
    journaliser("sauvegarde", f"{origine} : {nom}")
    return lieu


# ====================================================================== pages
def page_tableau_de_bord():
    ops = operations()
    st.header("Tableau de bord")
    exercices = sorted(ops["exercice"].unique(), reverse=True) if not ops.empty else []
    if not exercices:
        st.info("Aucune opération pour l'instant. Commencez par **Saisir une opération** ou **Importer un fichier Excel**.")
        return
    ex = st.selectbox("Exercice", exercices, format_func=lambda e: _libelle_exercice(e))
    n, n1 = ops[ops["exercice"] == ex], ops[ops["exercice"] == ex - 1]

    def tot(df, sens):
        return df.loc[df["sens"] == sens, "montant"].sum()

    c1, c2, c3 = st.columns(3)
    rec, dep = tot(n, "Recette"), tot(n, "Dépense")
    a_comparer = not n1.empty
    c1.metric("Recettes", eur(rec), delta=eur(rec - tot(n1, "Recette")) if a_comparer else None)
    c2.metric("Dépenses", eur(dep), delta=eur(dep - tot(n1, "Dépense")) if a_comparer else None, delta_color="inverse")
    c3.metric("Résultat", eur(rec - dep),
              delta=eur((rec - dep) - (tot(n1, "Recette") - tot(n1, "Dépense"))) if a_comparer else None)

    _alertes_tableau_de_bord()

    st.subheader("Résultat par manifestation")
    lecture = st.radio("Lecture", ["Trésorerie", "Résultat réel"], horizontal=True, label_visibility="collapsed",
                       help="Trésorerie : les achats restent sur la manifestation pour laquelle ils ont été payés. "
                            "Résultat réel : chaque manifestation supporte le coût de ce qu'elle a consommé ; "
                            "les invendus suivent à la manifestation suivante.")
    s = _synthese(ops, ex, lecture).assign(Exercice=str(ex))
    if a_comparer:
        s = pd.concat([s, _synthese(ops, ex - 1, lecture).assign(Exercice=str(ex - 1))])
    if lecture == "Résultat réel":
        mvts = stock.typer_mouvements(charger("mouvements_stock"))
        fin_ex = date(ex + (1 if mois_debut() > 1 else 0), (mois_debut() - 2) % 12 + 1, 1) + pd.offsets.MonthEnd(0)
        st.caption(f"Stock de consommables en fin d'exercice : **{eur(stock.valeur_stock_au(mvts, fin_ex.date()))}** "
                   "(non encore consommé, donc absent des résultats ci-dessous).")
    graphe = alt.Chart(s).mark_bar().encode(
        y=alt.Y("manifestation:N", title=None, axis=alt.Axis(labelLimit=220), sort="-x"),
        x=alt.X("Résultat:Q", title="Résultat (€)"),
        yOffset=alt.YOffset("Exercice:N", sort="descending"),
        color=alt.Color("Exercice:N", sort="descending", scale=alt.Scale(range=["#1F4E79", "#A9C4DE"]),
                        legend=alt.Legend(orient="top", title=None)),
        tooltip=["manifestation", "Exercice", alt.Tooltip("Recettes:Q", format=",.2f"),
                 alt.Tooltip("Dépenses:Q", format=",.2f"), alt.Tooltip("Résultat:Q", format=",.2f")],
    ).properties(height=max(220, 55 * s["manifestation"].nunique()))
    st.altair_chart(graphe, width="stretch")

    st.subheader("Résultat cumulé au fil de l'année")
    courbes = []
    for e, df in ((ex, n), (ex - 1, n1)):
        if df.empty:
            continue
        m = df.assign(mois=((df["date"].dt.month - mois_debut()) % 12) + 1).groupby("mois")["signe"].sum()
        m = m.reindex(range(1, 13), fill_value=0).cumsum()
        courbes.append(pd.DataFrame({"mois": [MOIS[((i - 1 + mois_debut() - 1) % 12) + 1] for i in m.index],
                                     "ordre": m.index, "cumul": m.values, "Exercice": str(e)}))
    if courbes:
        c = pd.concat(courbes)
        st.altair_chart(alt.Chart(c).mark_line(point=True).encode(
            x=alt.X("mois:N", sort=alt.SortField("ordre"), title=None),
            y=alt.Y("cumul:Q", title="Résultat cumulé (€)"),
            color=alt.Color("Exercice:N", sort="descending", scale=alt.Scale(range=["#1F4E79", "#A9C4DE"]),
                            legend=alt.Legend(orient="top", title=None)),
            tooltip=["Exercice", "mois", alt.Tooltip("cumul:Q", format=",.2f")],
        ).properties(height=280), width="stretch")

    prev = charger("previsionnel")
    prev = prev[metier.nombres(prev["exercice"]) == ex]
    if not prev.empty:
        st.subheader("Prévu / réalisé")
        reel = _synthese(ops, ex, lecture).set_index("manifestation")
        lignes = []
        for r in prev.to_dict("records"):
            for lib, cle_prev, cle_reel in (("Recettes", "recettes", "Recettes"), ("Dépenses", "depenses", "Dépenses")):
                lignes.append({"manifestation": r["manifestation"], "type": lib, "Prévu": float(r[cle_prev] or 0),
                               "Réalisé": float(reel[cle_reel].get(r["manifestation"], 0))})
        pr = pd.DataFrame(lignes).melt(id_vars=["manifestation", "type"], var_name="Version", value_name="montant")
        st.altair_chart(alt.Chart(pr).mark_bar().encode(
            y=alt.Y("manifestation:N", title=None, axis=alt.Axis(labelLimit=220)), x=alt.X("montant:Q", title="€"),
            yOffset="Version:N", color=alt.Color("Version:N", scale=alt.Scale(range=["#C9C9C9", "#2E7D32"]),
                                                 legend=alt.Legend(orient="top", title=None)),
            column=alt.Column("type:N", title=None),
            tooltip=["manifestation", "type", "Version", alt.Tooltip("montant:Q", format=",.2f")],
        ).properties(width=320), width="content")


def _synthese(ops: pd.DataFrame, exercice: int, lecture: str) -> pd.DataFrame:
    s = metier.synthese_manifestations(ops, exercice)
    if lecture == "Résultat réel":
        mvts = stock.typer_mouvements(charger("mouvements_stock"))
        s = stock.resultat_reel(s, ops, mvts, exercice, mois_debut())[["manifestation", "Recettes", "Dépenses", "Résultat"]]
    return s


def _alertes_tableau_de_bord():
    alertes = []
    articles = charger("articles")
    if not articles.empty:
        etat = stock.etat_stock(articles, stock.typer_mouvements(charger("mouvements_stock")))
        alertes += [f"Stock : {r.article} — {r.alerte}" for r in etat[etat["alerte"] != ""].itertuples()]
    sorties = stock.typer_sorties(charger("sorties_materiel"))
    noms = charger("materiel").set_index("id")["nom"] if not charger("materiel").empty else pd.Series(dtype=str)
    alertes += [f"Matériel non rendu : {noms.get(r.materiel_id, '?')} ×{r.quantite:g} "
                f"({r.emprunteur or r.manifestation}, prévu le {r.date_retour_prevue:%d/%m/%Y})"
                for r in stock.retards(sorties).itertuples()]
    if alertes:
        with st.container(border=True):
            st.markdown("**⚠️ À traiter**")
            for a in alertes[:10]:
                st.markdown(f"- {a}")


def _libelle_exercice(e: int) -> str:
    return str(e) if mois_debut() == 1 else f"{e}-{e + 1}"


def _formulaire_operation(cle: str, defaut: dict | None = None) -> dict | None:
    d = defaut or {}
    manifs = manifestations(actives_seulement=not defaut)
    if d.get("manifestation") and d["manifestation"] not in manifs:
        manifs.append(d["manifestation"])
    sens = st.radio("Sens", SENS, horizontal=True, key=f"{cle}_sens",
                    index=SENS.index(d["sens"]) if d.get("sens") in SENS else 0)
    with st.form(cle, clear_on_submit=not defaut):
        c1, c2 = st.columns(2)
        jour = c1.date_input("Date", value=d.get("date") or date.today(), format="DD/MM/YYYY")
        manif = c2.selectbox("Manifestation", manifs,
                             index=manifs.index(d["manifestation"]) if d.get("manifestation") in manifs else 0)
        cats = categories(sens)
        cat = c1.selectbox("Catégorie", cats, index=cats.index(d["categorie"]) if d.get("categorie") in cats else 0)
        montant = c2.number_input("Montant (€)", min_value=0.0, step=1.0, format="%.2f",
                                  value=float(d.get("montant") or 0.0))
        libelle = st.text_input("Libellé", value=d.get("libelle", ""), placeholder="ex. Boissons buvette brocante")
        c3, c4, c5 = st.columns(3)
        mode = c3.selectbox("Mode de paiement", MODES_PAIEMENT,
                            index=MODES_PAIEMENT.index(d["mode_paiement"]) if d.get("mode_paiement") in MODES_PAIEMENT else 0)
        tiers = c4.text_input("Tiers", value=d.get("tiers", ""), placeholder="fournisseur, donateur…")
        piece = c5.text_input("N° de pièce", value=d.get("piece", ""), placeholder="facture, ticket…")
        valide = st.form_submit_button("Enregistrer", type="primary")
    if not valide:
        return None
    if montant <= 0 or not libelle.strip():
        st.error("Le montant et le libellé sont obligatoires.")
        return None
    return metier.nouvelle_operation(jour, manif, sens, cat, libelle, montant, mode, tiers, piece,
                                     st.session_state["email"], mois_debut())


def page_saisie():
    st.header("Saisir une opération")
    if not peut_saisir():
        st.warning("Votre profil est en lecture seule.")
        return
    op = _formulaire_operation("saisie")
    if op:
        backend().ajouter("operations", [op])
        journaliser("saisie", f"{op['sens']} {op['montant']} € — {op['manifestation']} — {op['libelle']}")
        rafraichir()
        st.success(f"Enregistré : {op['sens'].lower()} de {eur(op['montant'])} pour « {op['manifestation']} ».")


def page_operations():
    st.header("Opérations")
    ops = operations()
    if ops.empty:
        st.info("Aucune opération.")
        return
    c1, c2, c3, c4 = st.columns(4)
    ex = c1.selectbox("Exercice", ["Tous"] + sorted(ops["exercice"].unique(), reverse=True))
    manif = c2.selectbox("Manifestation", ["Toutes"] + sorted(ops["manifestation"].unique()))
    sens = c3.selectbox("Sens", ["Tous"] + SENS)
    texte = c4.text_input("Recherche", placeholder="libellé, tiers, pièce…")
    v = ops
    if ex != "Tous":
        v = v[v["exercice"] == ex]
    if manif != "Toutes":
        v = v[v["manifestation"] == manif]
    if sens != "Tous":
        v = v[v["sens"] == sens]
    if texte:
        t = texte.lower()
        v = v[v[["libelle", "tiers", "piece", "categorie"]].astype(str).apply(
            lambda col: col.str.lower().str.contains(t, regex=False)).any(axis=1)]
    v = v.sort_values(["date", "saisi_le", "id"], ascending=False, kind="stable")
    st.caption(f"{len(v)} opération(s) — recettes {eur(v.loc[v['sens'] == 'Recette', 'montant'].sum())}, "
               f"dépenses {eur(v.loc[v['sens'] == 'Dépense', 'montant'].sum())}")
    st.dataframe(
        v[["date", "manifestation", "sens", "categorie", "libelle", "montant", "mode_paiement", "tiers", "piece"]],
        hide_index=True, width="stretch",
        column_config={"date": st.column_config.DateColumn("Date", format="DD/MM/YYYY"),
                       "montant": st.column_config.NumberColumn("Montant", format="%.2f €"),
                       "manifestation": "Manifestation", "sens": "Sens", "categorie": "Catégorie",
                       "libelle": "Libellé", "mode_paiement": "Paiement", "tiers": "Tiers", "piece": "Pièce"})

    if not peut_saisir() or v.empty:
        return
    st.divider()
    st.subheader("Corriger ou supprimer une opération")
    choix = st.selectbox("Opération", v["id"].tolist(), key="operation_a_corriger",
                         format_func=lambda i: _resume(v[v["id"] == i].iloc[0]))
    ligne = v[v["id"] == choix].iloc[0]
    defaut = {**ligne.to_dict(), "date": ligne["date"].date() if pd.notna(ligne["date"]) else date.today()}
    corrigee = _formulaire_operation(f"edition_{choix}", defaut)
    if corrigee:
        brut = charger("operations").copy()
        corrigee.update(id=choix, saisi_par=ligne["saisi_par"], saisi_le=ligne["saisi_le"])
        for col, val in corrigee.items():
            brut.loc[brut["id"] == choix, col] = str(val)
        backend().remplacer("operations", brut)
        journaliser("correction", _resume(ligne))
        rafraichir()
        flash("Opération corrigée.")
        st.rerun()
    if est_admin():
        confirmer = st.checkbox("Je confirme la suppression définitive de cette opération")
        if st.button("Supprimer", disabled=not confirmer):
            backend().supprimer_ids("operations", [choix])
            journaliser("suppression", _resume(ligne))
            rafraichir()
            flash("Opération supprimée.")
            st.rerun()


def _resume(r) -> str:
    d = r["date"].strftime("%d/%m/%Y") if pd.notna(r["date"]) else "?"
    return f"{d} · {r['manifestation']} · {r['sens']} {eur(float(r['montant']))} · {r['libelle']} · réf. {r['id'][:6]}"


def page_manifestations():
    st.header("Manifestations")
    st.caption("Chaque manifestation récurrente est un modèle : ses éditions successives se comparent d'une année sur l'autre.")
    m = charger("manifestations").copy()
    m["mois"] = metier.nombres(m["mois"]).fillna(0).astype(int).map(lambda i: MOIS[i])
    m["actif"] = m["actif"].astype(str).str.lower() != "non"
    edite = st.data_editor(
        m, hide_index=True, width="stretch", disabled=["nom"] if est_admin() else True,
        column_config={"nom": "Manifestation", "mois": st.column_config.SelectboxColumn("Mois habituel", options=MOIS),
                       "actif": st.column_config.CheckboxColumn("Active"), "description": "Description"})
    if est_admin() and st.button("Enregistrer les modifications"):
        edite["mois"] = edite["mois"].map(lambda x: MOIS.index(x) if x in MOIS else 0)
        edite["actif"] = edite["actif"].map(lambda b: "oui" if b else "non")
        backend().remplacer("manifestations", edite)
        journaliser("manifestations", "mise à jour de la liste")
        rafraichir()
        st.success("Liste enregistrée.")
    if not est_admin():
        return
    c1, c2 = st.columns(2)
    with c1.form("nouvelle_manif", clear_on_submit=True):
        st.markdown("**Nouvelle manifestation**")
        nom = st.text_input("Nom")
        mois = st.selectbox("Mois habituel", MOIS)
        desc = st.text_input("Description")
        if st.form_submit_button("Ajouter") and nom.strip():
            if nom.strip().lower() in [x.lower() for x in charger("manifestations")["nom"]]:
                st.error("Cette manifestation existe déjà.")
            else:
                backend().ajouter("manifestations", [{"nom": nom.strip(), "mois": MOIS.index(mois),
                                                       "actif": "oui", "description": desc}])
                journaliser("manifestations", f"ajout « {nom.strip()} »")
                rafraichir()
                flash(f"Manifestation « {nom.strip()} » ajoutée.")
                st.rerun()
    with c2.form("renommer_manif"):
        st.markdown("**Renommer** (l'historique suit)")
        ancien = st.selectbox("Manifestation", manifestations())
        nouveau = st.text_input("Nouveau nom")
        if st.form_submit_button("Renommer") and nouveau.strip() and ancien != MANIFESTATION_GENERALE:
            for table, col in (("manifestations", "nom"), ("operations", "manifestation"),
                               ("previsionnel", "manifestation")):
                df = charger(table).copy()
                df.loc[df[col] == ancien, col] = nouveau.strip()
                backend().remplacer(table, df)
            journaliser("manifestations", f"« {ancien} » renommée « {nouveau.strip()} »")
            rafraichir()
            flash(f"« {ancien} » renommée « {nouveau.strip()} », historique compris.")
            st.rerun()


def page_previsionnel():
    st.header("Budget prévisionnel")
    ops = operations()
    annee = st.number_input("Exercice à prévoir", min_value=2000, max_value=2100, step=1,
                            value=int(ops["exercice"].max()) + 1 if not ops.empty else date.today().year)
    c1, c2, c3 = st.columns(3)
    methode = c1.radio("Base de calcul", ["moyenne3", "derniere"],
                       format_func={"moyenne3": "Moyenne des 3 dernières éditions",
                                    "derniere": "Dernière édition"}.get)
    hausse = c2.slider("Hausse des prix sur les dépenses (%)", -20, 30, 3)
    evol = c3.slider("Évolution des recettes (%)", -30, 30, 0,
                     help="Fréquentation, tarifs… Mettre une valeur négative pour un scénario prudent (météo).")
    base = metier.base_previsionnelle(ops, manifestations(actives_seulement=True), int(annee), methode, hausse, evol)
    existant = charger("previsionnel")
    existant = existant[metier.nombres(existant["exercice"]) == annee]
    if not existant.empty:
        st.info("Un prévisionnel est déjà enregistré pour cet exercice : il est affiché ci-dessous. "
                "Modifiez les montants puis enregistrez pour le mettre à jour.")
        base = base.drop(columns=["recettes", "depenses", "resultat"]).merge(
            existant[["manifestation", "recettes", "depenses", "commentaire"]], on="manifestation", how="left")
        base[["recettes", "depenses"]] = base[["recettes", "depenses"]].apply(metier.nombres).fillna(0)
    if "commentaire" not in base:
        base["commentaire"] = ""
    st.caption("La proposition vient de l'historique ; ajustez chaque ligne à la main si besoin.")
    edite = st.data_editor(
        base[["manifestation", "historique", "recettes", "depenses", "commentaire"]], hide_index=True,
        width="stretch", disabled=["manifestation", "historique"] if est_admin() else True,
        column_config={"manifestation": "Manifestation", "historique": "Basé sur",
                       "recettes": st.column_config.NumberColumn("Recettes prévues", format="%.2f €"),
                       "depenses": st.column_config.NumberColumn("Dépenses prévues", format="%.2f €"),
                       "commentaire": "Commentaire"})
    edite["resultat"] = edite["recettes"] - edite["depenses"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Recettes prévues", eur(edite["recettes"].sum()))
    c2.metric("Dépenses prévues", eur(edite["depenses"].sum()))
    c3.metric("Résultat prévu", eur(edite["resultat"].sum()))
    st.altair_chart(alt.Chart(edite).mark_bar().encode(
        y=alt.Y("manifestation:N", title=None, axis=alt.Axis(labelLimit=220), sort="-x"), x=alt.X("resultat:Q", title="Résultat prévu (€)"),
        color=alt.condition(alt.datum.resultat >= 0, alt.value("#2E7D32"), alt.value("#C62828")),
        tooltip=["manifestation", alt.Tooltip("resultat:Q", format=",.2f")]).properties(height=max(220, 40 * len(edite))),
        width="stretch")
    if est_admin() and st.button("Enregistrer ce prévisionnel", type="primary"):
        autres = charger("previsionnel")
        autres = autres[metier.nombres(autres["exercice"]) != annee]
        maj = datetime.now().strftime("%Y-%m-%d %H:%M")
        neuf = edite.assign(exercice=int(annee), maj_le=maj)[["exercice", "manifestation", "recettes", "depenses",
                                                               "commentaire", "maj_le"]]
        backend().remplacer("previsionnel", pd.concat([autres, neuf.astype(str)], ignore_index=True))
        journaliser("prévisionnel", f"exercice {annee} enregistré")
        rafraichir()
        st.success("Prévisionnel enregistré. Il apparaît dans le tableau de bord (prévu / réalisé).")


def page_import():
    st.header("Importer un fichier Excel")
    st.download_button("Télécharger le modèle d'import", metier.modele_import_excel(),
                       file_name="modele_import_comite.xlsx")
    if not peut_saisir():
        st.warning("Votre profil est en lecture seule.")
        return
    fichier = st.file_uploader("Fichier Excel à importer (.xlsx)", type=["xlsx", "xls"])
    if not fichier:
        st.caption("Le fichier est contrôlé ligne par ligne avant toute écriture : rien n'est enregistré sans votre validation.")
        return
    apercu, erreurs = metier.analyser_import(fichier, manifestations(), operations(), mois_debut())
    for e in erreurs:
        st.error(e)
    if apercu.empty:
        return
    compte = apercu["statut"].value_counts()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Prêtes", int(compte.get("OK", 0)))
    c2.metric("Avec avertissement", int(compte.get("Avertissement", 0)))
    c3.metric("Doublons", int(compte.get("Doublon", 0)))
    c4.metric("En erreur", int(compte.get("Erreur", 0)))
    st.dataframe(apercu, hide_index=True, width="stretch",
                 column_config={"date": st.column_config.DateColumn("Date", format="DD/MM/YYYY"),
                                "montant": st.column_config.NumberColumn("Montant", format="%.2f €")})
    inclure_doublons = st.checkbox("Importer quand même les doublons")
    nouvelles = sorted(set(apercu.loc[apercu["statut"] != "Erreur", "manifestation"]) - set(manifestations()))
    if nouvelles:
        st.info("Manifestations qui seront créées : " + ", ".join(nouvelles))
    a_importer = metier.lignes_a_importer(apercu, inclure_doublons, st.session_state["email"])
    if st.button(f"Importer {len(a_importer)} opération(s)", type="primary", disabled=not a_importer):
        if nouvelles:
            backend().ajouter("manifestations", [{"nom": n, "mois": 0, "actif": "oui", "description": "créée à l'import"}
                                                 for n in nouvelles])
        backend().ajouter("operations", a_importer)
        try:
            backend().enregistrer_fichier("Imports", f"{datetime.now():%Y-%m-%d_%Hh%M}_{fichier.name}",
                                          fichier.getvalue(), fichier.type or "application/octet-stream")
        except Exception as e:
            st.warning(f"Import réussi, mais le fichier n'a pas pu être archivé : {e}")
        journaliser("import", f"{len(a_importer)} opérations depuis {fichier.name}")
        rafraichir()
        st.success(f"{len(a_importer)} opération(s) importée(s). Le fichier d'origine est archivé dans « Imports ».")


def page_sauvegarde():
    st.header("Sauvegarde et export")
    derniere = parametre("derniere_sauvegarde", "jamais")
    st.write(f"Dernière sauvegarde automatique dans le Drive : **{derniere}**. "
             "Une sauvegarde Excel horodatée est faite chaque jour à la première connexion.")
    tables = {t: charger(t) for t in TABLES}
    st.download_button("Télécharger toutes les données (Excel)", metier.export_excel(tables),
                       file_name=f"comite_export_{date.today():%Y-%m-%d}.xlsx", type="primary")
    if peut_saisir() and st.button("Sauvegarder maintenant dans le Drive"):
        lieu = faire_sauvegarde("manuelle")
        st.success(f"Sauvegarde enregistrée : {lieu}")
    with st.expander("Journal des actions"):
        st.dataframe(charger("journal").iloc[::-1], hide_index=True, width="stretch")


def page_utilisateurs():
    st.header("Utilisateurs")
    st.caption("Seules les adresses Google listées ici peuvent ouvrir le logiciel.")
    u = charger("utilisateurs")
    edite = st.data_editor(u, num_rows="dynamic", hide_index=True, width="stretch",
                           column_config={"email": "Adresse Google", "nom": "Nom",
                                          "role": st.column_config.SelectboxColumn(
                                              "Rôle", options=ROLES, required=True)})
    st.caption(" · ".join(f"{r} = {l}" for r, l in ROLES_LIBELLES.items()))
    if st.button("Enregistrer", type="primary"):
        edite = edite.dropna(subset=["email"])
        edite["email"] = edite["email"].str.strip().str.lower()
        if not edite["role"].isin(ROLES_ADMIN).any():
            st.error("Il faut au moins un président ou un trésorier.")
            return
        backend().remplacer("utilisateurs", edite)
        journaliser("utilisateurs", f"{len(edite)} compte(s)")
        rafraichir()
        st.success("Utilisateurs enregistrés.")


def page_parametres():
    st.header("Paramètres")
    with st.form("parametres"):
        nom = st.text_input("Nom de l'association", value=parametre("nom_association", "Comité des Fêtes"))
        mois = st.selectbox("Début de l'exercice comptable", MOIS[1:], index=mois_debut() - 1,
                            help="Janvier = année civile. À changer avant la première saisie.")
        if st.form_submit_button("Enregistrer", type="primary"):
            ecrire_parametre("nom_association", nom)
            ecrire_parametre("mois_debut_exercice", MOIS.index(mois))
            journaliser("paramètres", f"{nom}, exercice débutant en {mois}")
            st.success("Paramètres enregistrés.")
    st.caption(f"Stockage : {backend().nom}")


# ====================================================================== lancement
def page_confidentialite():
    """Page publique (sans connexion) exigée par Google pour l'écran de consentement."""
    nom = "le Comité des Fêtes de Haussy"
    st.title("Règles de confidentialité")
    st.markdown(f"""
Ce logiciel est l'outil interne de gestion de **{nom}** (comptabilité, stock, matériel, assemblée générale).
Il est réservé aux membres du bureau de l'association.

**Données traitées.** Pour la connexion, seuls l'adresse e-mail et le nom du compte Google sont utilisés,
afin de vérifier que la personne est autorisée. Les données de gestion de l'association (opérations,
stock, matériel) sont enregistrées dans le Google Drive de l'association.

**Accès au Drive.** Le logiciel ne peut lire et modifier que les fichiers qu'il a lui-même créés
(autorisation Google « drive.file ») : il n'a aucun accès au reste du Drive.

**Partage.** Aucune donnée n'est vendue, cédée ni transmise à des tiers. Aucune publicité, aucun traçage.

**Durée et droits.** Les comptes d'accès sont supprimés dès qu'un membre quitte le bureau. Pour toute
question ou demande de suppression : feteshaussy@gmail.com.
""")


def main():
    if st.query_params.get("page") == "confidentialite":
        page_confidentialite()
        return
    if not controler_acces():
        return
    initialiser_si_vide()
    sauvegarde_automatique()
    with st.sidebar:
        st.markdown(f"### 🎪 {parametre('nom_association', 'Comité des Fêtes')}")
        st.caption(f"{st.session_state['nom']} · {ROLES_LIBELLES.get(st.session_state['role'], '')}")
        if auth_configuree():
            st.button("Se déconnecter", on_click=st.logout)
        else:
            st.warning("Mode essai : données locales, sans connexion.")
    pages = {
        "Pilotage": [st.Page(page_tableau_de_bord, title="Tableau de bord", icon="📊", default=True),
                     st.Page(page_previsionnel, title="Prévisionnel", icon="🔮", url_path="previsionnel")],
        "Comptabilité": [st.Page(page_saisie, title="Saisir une opération", icon="✏️", url_path="saisie"),
                         st.Page(page_operations, title="Opérations", icon="📒", url_path="operations"),
                         st.Page(page_import, title="Import Excel", icon="📥", url_path="import")],
        "Logistique": [st.Page(page_stock, title="Stock boissons et alimentation", icon="🍺", url_path="stock"),
                       st.Page(page_materiel, title="Matériel", icon="⛺", url_path="materiel")],
        "Association": [st.Page(page_manifestations, title="Manifestations", icon="🎪", url_path="manifestations"),
                        st.Page(page_sauvegarde, title="Sauvegarde et export", icon="💾", url_path="sauvegarde")],
    }
    if est_admin():
        pages["Administration"] = [st.Page(page_utilisateurs, title="Utilisateurs", icon="👥", url_path="utilisateurs"),
                                   st.Page(page_parametres, title="Paramètres", icon="⚙️", url_path="parametres")]
    navigation = st.navigation(pages)
    afficher_flash()
    navigation.run()


if __name__ == "__main__":
    main()
