# src/routen.py
from pathlib import Path
import time
import math
import os
import re
from xml.sax.saxutils import escape

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy.sparse as sp
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from scipy.sparse.csgraph import connected_components, dijkstra
from shapely.geometry import Point
import plotly.graph_objects as go
import plotly.colors as pc


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

DEPOT_LAT = 53.789818257473414
DEPOT_LON = 9.990570083864876
V_DURCHSAGE_KMH = 15.0
DURCHSAGE_AUF_VERBINDUNG = False
TEMPOFAKTOR = 0.8
V_MAX_KMH = 80.0
KNOTENSTRAFE_S = 3.0
ORTSLAGE_PUFFER_M = 40
ORTSLAGE_SCHWELLE = 2.0
ZEITFENSTER_MIN = 90
ZEITLIMIT_S = 120
MAX_FAHRZEUGE = 20
BALANCE_KOEFFIZIENT = 3
RADIUS_M = 80
STRAFE = 10_000_000

STANDARD_TEMPO = {
    "trunk": (50, 100), "primary": (50, 100), "secondary": (50, 100),
    "tertiary": (50, 100), "unclassified": (50, 100), "residential": (30, 50),
    "living_street": (7, 7), "service": (20, 30),
}
STANDARD_SONST = (50, 50)
HIGHWAY_RANG = ["trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "service"]
TOKEN_TEMP = {
    "walk": 7, "de:walk": 7, "living_street": 7, "de:living_street": 7,
    "de:urban": 50, "urban": 50, "de:rural": 100, "rural": 100,
}

def lese_maxspeed(wert):
    if wert is None or (isinstance(wert, float) and np.isnan(wert)):
        return np.nan
    werte = []
    text = str(wert).lower()
    for t in re.findall(r"[a-z]{2}:[a-z_]*\d*|[a-z_]+|\d+(?:\.\d+)?\s*(?:mph)?", text):
        t = t.strip()
        v = None
        if t in TOKEN_TEMP:
            v = TOKEN_TEMP[t]
        elif re.fullmatch(r"\d+(?:\.\d+)?", t):
            v = float(t)
        elif re.fullmatch(r"\d+(?:\.\d+)?\s*mph", t):
            v = float(re.match(r"\d+(?:\.\d+)?", t).group()) * 1.609
        else:
            m = re.search(r"(\d+)$", t)
            if m:
                v = float(m.group(1))
        if v is not None:
            werte.append(v)
    return min(werte) if werte else np.nan

def lese_highway(wert):
    tokens = re.findall(r"[a-z_]+", str(wert).lower())
    for h in HIGHWAY_RANG:
        if h in tokens or h + "_link" in tokens:
            return h
    return tokens[0] if tokens else ""

def gebaeude_fuer_ortslage():
    try:
        return gpd.read_file(DATA_DIR / "abdeckung_segmente.gpkg", layer="gebaeude")
    except Exception:
        return None

def ortslage_bestimmen(strassen, gebaeude):
    if gebaeude is None:
        return pd.Series(True, index=strassen.index)
    if gebaeude.crs != strassen.crs:
        gebaeude = gebaeude.to_crs(strassen.crs)
    zentroide = gpd.GeoDataFrame(geometry=gebaeude.geometry.centroid, crs=gebaeude.crs)
    puffer = gpd.GeoDataFrame({"eid": strassen.index}, geometry=strassen.geometry.buffer(ORTSLAGE_PUFFER_M), crs=strassen.crs)
    j = gpd.sjoin(zentroide, puffer, predicate="within")
    anzahl = j.groupby("eid").size().reindex(strassen.index, fill_value=0)
    dichte = anzahl / np.maximum(strassen["length"], 30.0) * 100.0
    return dichte >= ORTSLAGE_SCHWELLE

def tempoprofil(s, gebaeude, a_tempo, a_vmax, a_tempofaktor, a_knotenstrafe, a_durchsage_auf_verbindung, a_tempo_verbindung, a_tempo_verbindung_durchsage):
    pauschal = a_tempo_verbindung is not None
    if pauschal:
        s["limit_kmh"] = np.nan
        s["tempo_quelle"] = "pauschal"
        s["v_dur_kmh"] = float(a_tempo)
        v_frei = pd.Series(float(a_tempo_verbindung), index=s.index)
        strafe = 0.0
    else:
        leer = pd.Series(np.nan, index=s.index)
        osm = s["maxspeed"].map(lese_maxspeed) if "maxspeed" in s.columns else leer
        zone = s["zone:maxspeed"].map(lese_maxspeed) if "zone:maxspeed" in s.columns else leer
        hw = s["highway"].map(lese_highway)
        innen = ortslage_bestimmen(s, gebaeude)
        std = pd.Series([STANDARD_TEMPO.get(h, STANDARD_SONST)[0 if i else 1] for h, i in zip(hw, innen)], index=s.index, dtype=float)
        limit = osm.copy()
        quelle = pd.Series("", index=s.index, dtype=object)
        quelle.loc[osm.notna()] = "osm_maxspeed"
        m = limit.isna() & zone.notna()
        limit.loc[m] = zone.loc[m]
        quelle.loc[m] = "osm_zone"
        m = limit.isna()
        limit.loc[m] = std.loc[m]
        quelle.loc[m & innen] = "standard_innerorts"
        quelle.loc[m & ~innen] = "standard_ausserorts"

        korrektur_pfad = DATA_DIR / "tempo_korrekturen.csv"
        if korrektur_pfad.exists():
            try:
                k = pd.read_csv(korrektur_pfad).dropna(subset=["kanten_id", "limit_kmh"])
                kid = k["kanten_id"].astype(int)
                ok = kid.isin(limit.index)
                for i, v in zip(kid[ok], k.loc[ok, "limit_kmh"].astype(float)):
                    limit.at[i] = v
                    quelle.at[i] = "korrektur"
            except Exception:
                pass

        limit = limit.clip(lower=5.0)
        s["limit_kmh"] = limit
        s["tempo_quelle"] = quelle
        s["v_dur_kmh"] = np.minimum(float(a_tempo), limit)
        v_frei = (np.minimum(limit, a_vmax) * a_tempofaktor).clip(lower=5.0)
        strafe = float(a_knotenstrafe)

    cap = a_tempo if a_tempo_verbindung_durchsage is None else a_tempo_verbindung_durchsage
    v_conn = np.minimum(v_frei, float(cap)) if a_durchsage_auf_verbindung else v_frei
    s["v_frei_kmh"] = v_frei
    s["v_conn_kmh"] = v_conn
    s["t_frei"] = s["length"] / (v_frei / 3.6) + strafe
    s["t_conn"] = s["length"] / (v_conn / 3.6) + strafe

def netz_laden(lat, lon):
    strassen = gpd.read_file(DATA_DIR / "daten_ort.gpkg", layer="strassen")
    crs = strassen.crs
    n_e = len(strassen)
    ist_bab = strassen["highway"].astype(str).str.contains("motorway")
    strassen["befahrbar"] = ~ist_bab

    codes, uniq = pd.factorize(np.concatenate([strassen["u"].to_numpy(), strassen["v"].to_numpy()]))
    strassen["ui"], strassen["vi"] = codes[:n_e], codes[n_e:]
    n = len(uniq)
    erste = np.array([g.coords[0] for g in strassen.geometry])
    letzte = np.array([g.coords[-1] for g in strassen.geometry])
    xy = np.zeros((n, 2))
    cnt = np.zeros(n)
    np.add.at(xy, strassen["ui"].to_numpy(), erste)
    np.add.at(cnt, strassen["ui"].to_numpy(), 1)
    np.add.at(xy, strassen["vi"].to_numpy(), letzte)
    np.add.at(cnt, strassen["vi"].to_numpy(), 1)
    xy /= cnt[:, None]

    b = strassen[strassen["befahrbar"]]
    A = sp.csr_matrix((np.ones(len(b)), (b["ui"], b["vi"])), shape=(n, n))
    _, lab = connected_components(A, directed=True, connection="strong")
    gross = np.argmax(np.bincount(lab))
    im_netz = lab == gross
    strassen["im_netz"] = strassen["befahrbar"] & im_netz[strassen["ui"]] & im_netz[strassen["vi"]]

    p = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(crs).iloc[0]
    kandidaten = np.where(im_netz)[0]
    d = np.hypot(xy[kandidaten, 0] - p.x, xy[kandidaten, 1] - p.y)
    depot = int(kandidaten[np.argmin(d)])

    return dict(strassen=strassen, crs=crs, n=n, xy=xy, depot=depot, depot_xy=xy[depot])

def regime_bauen(netz, zeitspalte):
    s = netz["strassen"]
    e = s[s["im_netz"]]
    idx = e.groupby(["ui", "vi"])[zeitspalte].idxmin().to_numpy()
    beste = e.loc[idx]
    W = sp.csr_matrix((np.maximum(beste[zeitspalte].to_numpy(float), 0.01),
                       (beste["ui"].to_numpy(), beste["vi"].to_numpy())),
                      shape=(netz["n"], netz["n"]))
    bk = {(int(u), int(v)): int(i) for u, v, i in zip(beste["ui"], beste["vi"], beste.index)}
    return dict(W=W, bk=bk, t=s[zeitspalte])

def pflichtkanten(netz):
    auswahl = pd.read_csv(DATA_DIR / "auswahl_segmente.csv")
    sk = pd.read_csv(DATA_DIR / "segment_kanten.csv")
    sk = sk[sk["seg_id"].isin(auswahl["seg_id"])]
    s = netz["strassen"]
    sk = sk.join(s[["ui", "vi", "length", "im_netz", "v_dur_kmh"]], on="kanten_id")
    sk = sk[sk["im_netz"]].reset_index(drop=True)
    return sk

def kostenmatrix(netz, req, reg_frei, reg_conn, serv):
    ui = req["ui"].to_numpy()
    vi = req["vi"].to_numpy()
    depot = netz["depot"]

    quellen = np.unique(vi)
    zeile = {int(q): i for i, q in enumerate(quellen)}
    dist_c, _ = dijkstra(reg_conn["W"], directed=True, indices=quellen, return_predecessors=True)
    d_hin, pred_hin = dijkstra(reg_frei["W"], directed=True, indices=[depot], return_predecessors=True)
    d_rueck, pred_rueck = dijkstra(reg_frei["W"].T.tocsr(), directed=True, indices=[depot], return_predecessors=True)

    ende_zeilen = np.array([zeile[int(v)] for v in vi])
    M = len(req)
    mat = np.zeros((M + 1, M + 1))
    mat[1:, 1:] = dist_c[np.ix_(ende_zeilen, ui)] + serv[None, :]
    mat[0, 1:] = d_hin[0, ui] + serv
    mat[1:, 0] = d_rueck[0, vi]
    np.fill_diagonal(mat, 0)
    pi = dict(zeile=zeile, hin=pred_hin[0], rueck=pred_rueck[0], W_conn=reg_conn["W"], W_frei=reg_frei["W"])
    return np.rint(mat).astype(np.int64), pi

def loese_vrp(mat, gruppen, k, fenster_s, zeitlimit):
    n = len(mat)
    manager = pywrapcp.RoutingIndexManager(n, k, 0)
    routing = pywrapcp.RoutingModel(manager)
    liste = mat.tolist()
    cb = routing.RegisterTransitMatrix(liste)
    routing.SetArcCostEvaluatorOfAllVehicles(cb)
    routing.AddDimension(cb, 0, int(fenster_s), True, "Zeit")
    if BALANCE_KOEFFIZIENT:
        routing.GetDimensionOrDie("Zeit").SetGlobalSpanCostCoefficient(int(BALANCE_KOEFFIZIENT))
    for g in gruppen:
        routing.AddDisjunction([manager.NodeToIndex(i) for i in g], STRAFE, 1)

    p = pywrapcp.DefaultRoutingSearchParameters()
    p.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    p.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    p.time_limit.FromSeconds(int(zeitlimit))
    loesung = routing.SolveWithParameters(p)
    if loesung is None:
        return None, list(range(len(gruppen)))

    routen = []
    for v in range(k):
        idx = routing.Start(v)
        folge = []
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if node != 0:
                folge.append(node)
            idx = loesung.Value(routing.NextVar(idx))
        routen.append(folge)
    besucht = {x for r in routen for x in r}
    fehlend = [i for i, g in enumerate(gruppen) if not (set(g) & besucht)]
    return routen, fehlend

def suche_fahrzeuge(mat, gruppen, serv_min, fenster_s, zeitlimit, max_fahrzeuge=20):
    k = max(1, math.ceil(serv_min / fenster_s))
    while k <= max_fahrzeuge:
        routen, fehlend = loese_vrp(mat, gruppen, k, fenster_s, zeitlimit)
        if routen is not None and not fehlend:
            return k, routen
        k += 1
    return None, None

def pfad(W, start, ziel):
    # Einfacher Dijkstra für Teilstücke
    dist, predecessors = dijkstra(W, directed=True, indices=[start], return_predecessors=True)
    p = [ziel]
    while p[-1] != start:
        nxt = predecessors[0, p[-1]]
        if nxt < 0:
            raise RuntimeError("Pfad nicht rekonstruierbar")
        p.append(int(nxt))
    return p[::-1]

def teilstuecke(netz, req, serv, pi, routen, reg_frei, reg_conn, sende_zwischen, seg_von_kante):
    s = netz["strassen"]
    depot = netz["depot"]
    zeilen = []
    for fz, folge in enumerate(routen, start=1):
        if not folge:
            continue
        nr = 0

        def leg(knoten, reg, art, sendet):
            nonlocal nr
            for x, y in zip(knoten[:-1], knoten[1:]):
                kid = reg["bk"][(x, y)]
                nr += 1
                zeilen.append(dict(fahrzeug=fz, nr=nr, typ="Verbindung", art=art, sendet=sendet,
                                   kanten_id=kid, laenge_m=float(s.at[kid, "length"]),
                                   dauer_s=float(reg["t"].at[kid]),
                                   seg_id=seg_von_kante.get(kid, -1), geometry=s.at[kid, "geometry"]))

        erste = int(req.iloc[folge[0] - 1]["ui"])
        if erste != depot:
            leg(pfad(pi["W_frei"], depot, erste), reg_frei, "anfahrt", False)
        aktuell = erste
        for node in folge:
            r = req.iloc[node - 1]
            start = int(r["ui"])
            if aktuell != start:
                leg(pfad(pi["W_conn"] if sende_zwischen else pi["W_frei"], aktuell, start), reg_conn if sende_zwischen else reg_frei, "zwischen", sende_zwischen)
            kid = int(r["kanten_id"])
            nr += 1
            zeilen.append(dict(fahrzeug=fz, nr=nr, typ="Durchsage", art="durchsage", sendet=True,
                               kanten_id=kid, laenge_m=float(r["length"]), dauer_s=float(serv[node - 1]),
                               seg_id=int(r["seg_id"]), geometry=s.at[kid, "geometry"]))
            aktuell = int(r["vi"])
        if aktuell != depot:
            leg(pfad(pi["W_frei"], aktuell, depot), reg_frei, "rueckfahrt", False)
    return gpd.GeoDataFrame(zeilen, geometry="geometry", crs=netz["crs"])

def _linien(gdf, farbe, breite, name, gruppe, zeigen=True):
    """Alle Linien eines GeoDataFrames als EINE Plotly-Spur (schnell)."""
    g = gdf.to_crs(4326)
    lon, lat = [], []
    for geom in g.geometry:
        if geom is None or geom.is_empty:
            continue
        teile = geom.geoms if geom.geom_type == "MultiLineString" else [geom]
        for t in teile:
            x, y = t.xy
            lon += list(x) + [None]
            lat += list(y) + [None]
    return go.Scattermap(lon=lon, lat=lat, mode="lines",
                         line=dict(color=farbe, width=breite),
                         name=name, legendgroup=gruppe, showlegend=zeigen,
                         hoverinfo="name")

def rahmen_zeilen(p, k=None):
    """Rahmendaten als Textzeilen (für Karte UND Anzeige)."""
    zeilen = [
        f"Zeitfenster je Tour: {p['fenster_min']} min",
        f"Durchsagetempo: {p['tempo_kmh']:g} km/h",
        f"Durchsage auf Verbindungsfahrten: {'Ja' if p['durchsage_auf_verbindung'] else 'Nein'}",
        f"Opt. Zeitlimit: {p['zeitlimit_s']} s",
        f"Max. Fahrzeuge: {p['max_fahrzeuge']}",
    ]
    if k is not None:
        zeilen.append(f"Eingesetzte Fahrzeuge: {k}")
    return zeilen

def erstelle_routen_karte(netz, teil, anzahl, params):
    fig = go.Figure()
    fig.add_trace(_linien(netz["strassen"][netz["strassen"]["im_netz"]],
                          "#cccccc", 1, "Straßennetz", "netz"))
    farben = pc.qualitative.D3
    for fz in sorted(teil["fahrzeug"].unique()):
        t = teil[teil["fahrzeug"] == fz]
        c = farben[(fz - 1) % len(farben)]
        g = f"fz{fz}"
        fig.add_trace(_linien(t[~t["sendet"]], c, 2, f"Fz {fz} – Verbindung", g))
        fig.add_trace(_linien(t[t["sendet"]],  c, 5, f"Fz {fz} – Durchsage", g))

    depot = gpd.GeoSeries([Point(netz["depot_xy"])], crs=netz["crs"]).to_crs(4326).iloc[0]
    fig.add_trace(go.Scattermap(lon=[depot.x], lat=[depot.y], mode="markers",
                                marker=dict(size=16, color="black"), name="Depot"))

    fig.add_annotation(xref="paper", yref="paper", x=0.01, y=0.99,
                       xanchor="left", yanchor="top", showarrow=False, align="left",
                       text="<br>".join(rahmen_zeilen(params, anzahl)),
                       bgcolor="rgba(255,255,255,0.85)", bordercolor="#888", borderwidth=1)
    fig.update_layout(
        map=dict(style="open-street-map", center=dict(lon=depot.x, lat=depot.y), zoom=12),
        margin=dict(l=0, r=0, t=0, b=0), height=700,
        legend=dict(x=0.99, xanchor="right", y=0.99, yanchor="top",
                    bgcolor="rgba(255,255,255,0.85)"))
    return fig

def fuehre_routenplanung_aus(
    fenster_min=90, 
    tempo_kmh=15.0, 
    durchsage_auf_verbindung=False, 
    tempofaktor=0.8, 
    vmax=80.0, 
    knotenstrafe_s=3.0, 
    zeitlimit_s=60, 
    max_fahrzeuge=5,
    depot_lat=DEPOT_LAT,
    depot_lon=DEPOT_LON
):
    # Übergebene Koordinaten für das Depot nutzen
    netz = netz_laden(depot_lat, depot_lon)
    s = netz["strassen"]
    gebaeude = gebaeude_fuer_ortslage()
    
    tempoprofil(s, gebaeude, tempo_kmh, vmax, tempofaktor, knotenstrafe_s, durchsage_auf_verbindung, None, None)
    reg_frei = regime_bauen(netz, "t_frei")
    reg_conn = regime_bauen(netz, "t_conn") if durchsage_auf_verbindung else reg_frei

    req = pflichtkanten(netz)
    serv = req["length"].to_numpy(float) / (req["v_dur_kmh"].to_numpy(float) / 3.6)
    mat, pi = kostenmatrix(netz, req, reg_frei, reg_conn, serv)

    fenster_s = fenster_min * 60
    hin_zurueck = mat[0, 1:] + mat[1:, 0]
    zu_weit_knoten = set(np.where(hin_zurueck > fenster_s)[0] + 1)

    gruppen_seg = {}
    for i, sid in enumerate(req["seg_id"].to_numpy(), start=1):
        gruppen_seg.setdefault(int(sid), []).append(i)
    gruppen = [list(kn) for kn in gruppen_seg.values() if any(k not in zu_weit_knoten for k in kn)]

    serv_min = sum(min(serv[k - 1] for k in g) for g in gruppen)

    k, routen = suche_fahrzeuge(mat, gruppen, serv_min, fenster_s, zeitlimit_s, max_fahrzeuge=max_fahrzeuge)
    if routen is None:
        raise ValueError(f"Keine zulässige Routen-Lösung mit maximal {max_fahrzeuge} Fahrzeugen gefunden.")

    sk_alle = pd.read_csv(DATA_DIR / "segment_kanten.csv")
    seg_von_kante = dict(zip(sk_alle["kanten_id"], sk_alle["seg_id"]))
    teil = teilstuecke(netz, req, serv, pi, routen, reg_frei, reg_conn, durchsage_auf_verbindung, seg_von_kante)

    routen_gpkg = DATA_DIR / "routen.gpkg"
    teil.to_file(routen_gpkg, layer="routen", driver="GPKG")
    gpd.GeoDataFrame({"name": ["Start/Ende"]}, geometry=[Point(netz["depot_xy"])], crs=netz["crs"]).to_file(
        routen_gpkg, layer="depot", driver="GPKG")

    params = dict(fenster_min=fenster_min, tempo_kmh=tempo_kmh,
                  durchsage_auf_verbindung=durchsage_auf_verbindung,
                  zeitlimit_s=zeitlimit_s, max_fahrzeuge=max_fahrzeuge,
                  depot_lat=depot_lat, depot_lon=depot_lon)
    
    fig = erstelle_routen_karte(netz, teil, k, params)
    
    # Kennzahlen je Fahrzeug / Route
    je_fz = teil.groupby("fahrzeug")
    touren = []
    for fz, t in je_fz:
        dauer_s = float(t["dauer_s"].sum())
        dauer_durchsage_s = float(t.loc[t["typ"] == "Durchsage", "dauer_s"].sum())
        touren.append({
            "fahrzeug": int(fz),
            "fahrzeit_min": dauer_s / 60,
            "davon_durchsage_min": dauer_durchsage_s / 60,
            "davon_verbindung_min": (dauer_s - dauer_durchsage_s) / 60,
            "strecke_km": float(t["laenge_m"].sum() / 1000),
            "durchsage_km": float(t.loc[t["typ"] == "Durchsage", "laenge_m"].sum() / 1000),
            "segmente": int((t["typ"] == "Durchsage").sum()),
            "auslastung": dauer_s / fenster_s,  # Anteil des Zeitfensters
        })

    summary = {
        "fahrzeuge": k,
        "gesamt_laenge_km": float(teil["laenge_m"].sum() / 1000),
        "durchsage_laenge_km": float(teil.loc[teil["typ"] == "Durchsage", "laenge_m"].sum() / 1000),
        "gesamt_fahrzeit_min": sum(x["fahrzeit_min"] for x in touren),
        "laengste_tour_min": max(x["fahrzeit_min"] for x in touren),
        "fenster_min": fenster_min,
        "touren": touren,
    }
    
    return summary, fig, str(routen_gpkg)