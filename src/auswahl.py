# src/auswahl.py
from pathlib import Path
import time
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy.sparse as sp
from scipy.optimize import Bounds, LinearConstraint, milp

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

def lade_daten(radius_m, ortsteile_namen=None, ortsteile_datei=None):
    eingabe_seg = DATA_DIR / "abdeckung_segmente.gpkg"
    eingabe_paare = DATA_DIR / f"abdeckung_R{radius_m}.csv"
    eingabe_ort = DATA_DIR / "daten_ort.gpkg"
    
    segmente = gpd.read_file(eingabe_seg, layer="segmente")
    gebaeude = gpd.read_file(eingabe_seg, layer="gebaeude")
    paare = pd.read_csv(eingabe_paare)
    
    # Filterung auf Basis der Auswahl aus Schritt 1b einbeziehen
    if ortsteile_namen and ortsteile_datei:
        datei_pfad = DATA_DIR / ortsteile_datei
        if datei_pfad.exists():
            g_teile = gpd.read_file(datei_pfad, layer="ortsteile")
            g_auswahl = g_teile[g_teile["name"].isin(ortsteile_namen)]
            if not g_auswahl.empty:
                union_geom = g_auswahl.geometry.union_all()
                zentroids = gebaeude.geometry.centroid
                mask = zentroids.within(union_geom)
                gebaeude = gebaeude[mask].copy()
                
                # Paare ebenfalls auf die gefilterten Gebäude einschränken
                gueltige_geb_ids = set(gebaeude["geb_id"])
                paare = paare[paare["geb_id"].isin(gueltige_geb_ids)].reset_index(drop=True)

    try:
        grenze = gpd.read_file(eingabe_ort, layer="ortsgrenze")
    except Exception:
        grenze = None
    return segmente, gebaeude, paare, grenze

def gewichte_bestimmen(gebaeude, gewicht_cap_quantil=0.99):
    g = gebaeude.set_index("geb_id")
    w = g["gewicht"].where(g["relevant"], 0.0).astype(float)
    if gewicht_cap_quantil is not None:
        positiv = w[w > 0]
        if len(positiv):
            w = w.clip(upper=positiv.quantile(gewicht_cap_quantil))
    return w

def modell_bauen(segmente, paare, w):
    seg_ids = segmente["seg_id"].to_numpy()
    spalte = {s: i for i, s in enumerate(seg_ids)}

    je_geb = (
        paare.sort_values(["geb_id", "seg_id"]).groupby("geb_id")["seg_id"].apply(tuple)
    )
    df = pd.DataFrame({"segs": je_geb, "w": w.reindex(je_geb.index).fillna(0.0).to_numpy()})
    df["n"] = 1
    gruppen = df.groupby("segs", sort=False).agg(w=("w", "sum"), n=("n", "sum")).reset_index()

    zeilen, spalten = [], []
    for z, segs in enumerate(gruppen["segs"]):
        for s in segs:
            zeilen.append(z)
            spalten.append(spalte[s])
    A = sp.csc_matrix(
        (np.ones(len(zeilen)), (zeilen, spalten)), shape=(len(gruppen), len(seg_ids))
    )
    return A, gruppen["w"].to_numpy(), gruppen["n"].to_numpy(), gruppen["segs"], seg_ids

def loese_milp(A, gw, laenge, ziel_gewicht, zeitlimit, mip_gap=0.01):
    G, S = A.shape
    c = np.concatenate([laenge, np.zeros(G)])
    M = sp.hstack([-A, sp.identity(G, format="csc")], format="csc")
    c1 = LinearConstraint(M, -np.inf, 0.0)
    c2 = LinearConstraint(
        sp.csc_matrix(np.concatenate([np.zeros(S), gw]).reshape(1, -1)), ziel_gewicht, np.inf
    )
    integ = np.concatenate([np.ones(S), np.zeros(G)])
    res = milp(
        c,
        constraints=[c1, c2],
        integrality=integ,
        bounds=Bounds(0, 1),
        options={"time_limit": zeitlimit, "mip_rel_gap": mip_gap, "disp": False},
    )
    if res.x is None:
        return None, res
    return res.x[:S] > 0.5, res

def greedy(A, gw, laenge, ziel_gewicht):
    G, S = A.shape
    gewaehlt = np.zeros(S, dtype=bool)
    bedeckt = np.zeros(G, dtype=bool)
    erreicht = 0.0
    while erreicht < ziel_gewicht:
        gewinn = A.T @ (gw * (~bedeckt))
        score = gewinn / laenge
        score[gewaehlt] = -1.0
        s = int(np.argmax(score))
        if gewinn[s] <= 0:
            break
        gewaehlt[s] = True
        zeilen = A.indices[A.indptr[s]:A.indptr[s + 1]]
        neu = zeilen[~bedeckt[zeilen]]
        erreicht += gw[neu].sum()
        bedeckt[zeilen] = True
    return gewaehlt

def bewerten(gewaehlt, A, gw, gn, laenge, w_gesamt_relevant, n_gesamt_relevant):
    bedeckt = (A @ gewaehlt.astype(float)) > 0
    w_bed = gw[bedeckt].sum()
    return {
        "segmente": int(gewaehlt.sum()),
        "laenge_km": float(laenge[gewaehlt].sum() / 1000),
        "anteil_erreichbar_gew": float(w_bed / gw.sum()) if gw.sum() > 0 else 0,
        "anteil_gesamt_gew": float(w_bed / w_gesamt_relevant) if w_gesamt_relevant > 0 else 0,
        "anteil_gesamt_anzahl": float(gn[bedeckt].sum() / n_gesamt_relevant) if n_gesamt_relevant > 0 else 0,
    }

def erstelle_auswahl_karte(segmente, gebaeude, gewaehlt_mask, abgedeckt_ids, grenze, radius_m):
    fig, ax = plt.subplots(figsize=(10, 10))
    if grenze is not None:
        grenze.boundary.plot(ax=ax, color="red", linewidth=1)
    rel = gebaeude[gebaeude["relevant"]]
    if not rel[rel["geb_id"].isin(abgedeckt_ids)].empty:
        rel[rel["geb_id"].isin(abgedeckt_ids)].plot(ax=ax, color="lightgrey", linewidth=0)
    if not rel[~rel["geb_id"].isin(abgedeckt_ids)].empty:
        rel[~rel["geb_id"].isin(abgedeckt_ids)].plot(ax=ax, color="crimson", linewidth=0)
    if not segmente[~gewaehlt_mask].empty:
        segmente[~gewaehlt_mask].plot(ax=ax, color="lightsteelblue", linewidth=0.4)
    if not segmente[gewaehlt_mask].empty:
        segmente[gewaehlt_mask].plot(ax=ax, color="navy", linewidth=1.6)
    ax.set_title(f"Zu befahrende Segmente (dunkelblau), R = {radius_m} m")
    ax.set_axis_off()
    return fig

def führe_auswahl_aus(radius_m=80, zielabdeckung=0.95, gewicht_cap_quantil=0.99, zeitlimit_s=120, ortsteile_namen=None, ortsteile_datei=None):
    segmente, gebaeude, paare, grenze = lade_daten(radius_m, ortsteile_namen, ortsteile_datei)
    
    if len(gebaeude[gebaeude["relevant"]]) == 0:
        raise ValueError("Keine relevanten Gebäude im gewählten Einsatzgebiet gefunden.")

    w = gewichte_bestimmen(gebaeude, gewicht_cap_quantil)
    relevant_ids = gebaeude.loc[gebaeude["relevant"], "geb_id"]
    w_gesamt = float(w.reindex(relevant_ids).sum())
    n_gesamt = int(len(relevant_ids))

    A, gw, gn, gruppen_segs, seg_ids = modell_bauen(segmente, paare, w)
    laenge = np.maximum(segmente["length"].to_numpy(dtype=float), 1.0)

    # Pflichtsegmente
    einzel = np.where(np.diff(A.tocsr().indptr) == 1)[0]
    pflicht_spalten = set()
    for z in einzel:
        pflicht_spalten.add(int(A.tocsr().indices[A.tocsr().indptr[z]]))

    ziel = zielabdeckung * gw.sum()
    t0 = time.time()
    gewaehlt, res = loese_milp(A, gw, laenge, ziel, zeitlimit_s)
    dauer = time.time() - t0
    
    g_greedy = greedy(A, gw, laenge, ziel)
    kz_greedy = bewerten(g_greedy, A, gw, gn, laenge, w_gesamt, n_gesamt)

    if gewaehlt is None:
        gewaehlt = g_greedy
        quelle = "Greedy (Optimierer ohne Lösung)"
    else:
        quelle = "MILP (HiGHS)"
    kz = bewerten(gewaehlt, A, gw, gn, laenge, w_gesamt, n_gesamt)

    # Export
    seg_out = segmente.copy()
    seg_out["gewaehlt"] = gewaehlt
    seg_out["pflicht"] = [i in pflicht_spalten for i in range(len(seg_ids))]
    gewaehlte_ids = set(seg_ids[gewaehlt])
    abgedeckt = paare[paare["seg_id"].isin(gewaehlte_ids)]["geb_id"].unique()

    geb_out = gebaeude.copy()
    geb_out["abgedeckt"] = geb_out["geb_id"].isin(abgedeckt)
    
    seg_out.to_file(DATA_DIR / "auswahl_segmente.gpkg", layer="segmente", driver="GPKG")
    geb_out.to_file(DATA_DIR / "auswahl_segmente.gpkg", layer="gebaeude", driver="GPKG")
    seg_out.loc[seg_out["gewaehlt"], ["seg_id", "u", "v", "highway", "length"]].to_csv(
        DATA_DIR / "auswahl_segmente.csv", index=False
    )
    
    fig = erstelle_auswahl_karte(seg_out, gebaeude, seg_out["gewaehlt"].to_numpy(), set(abgedeckt), grenze, radius_m)
    
    bericht_dict = {
        "quelle": quelle,
        "dauer": dauer,
        "segmente": kz["segmente"],
        "laenge_km": kz["laenge_km"],
        "anteil_netz": kz["laenge_km"] * 1000 / laenge.sum() if laenge.sum() > 0 else 0,
        "anteil_erreichbar_gew": kz["anteil_erreichbar_gew"],
        "anteil_gesamt_gew": kz["anteil_gesamt_gew"],
        "anteil_gesamt_anzahl": kz["anteil_gesamt_anzahl"],
        "greedy_laenge_km": kz_greedy["laenge_km"],
        "greedy_segmente": kz_greedy["segmente"],
        "ersparnis": 1 - kz["laenge_km"] / kz_greedy["laenge_km"] if kz_greedy["laenge_km"] > 0 else 0
    }
    
    return bericht_dict, fig, str(DATA_DIR / "auswahl_segmente.gpkg")