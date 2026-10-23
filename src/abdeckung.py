# src/abdeckung.py
from pathlib import Path
import geopandas as gpd
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

AUSGESCHLOSSEN_HIGHWAY = {"motorway", "motorway_link", "trunk", "trunk_link"}
NEBENGEBAEUDE = {
    "garage", "garages", "carport", "shed", "roof", "hut", "cabin",
    "greenhouse", "barn", "stable", "cowshed", "farm_auxiliary", "silo",
    "service", "transformer_tower", "container", "ruins", "construction",
    "bunker", "static_caravan", "toilets", "kiosk",
}
STANDARD_GESCHOSSE = 2

def lade_daten():
    gpkg_pfad = DATA_DIR / "daten_ort.gpkg"
    strassen = gpd.read_file(gpkg_pfad, layer="strassen")
    gebaeude = gpd.read_file(gpkg_pfad, layer="gebaeude")
    grenze = gpd.read_file(gpkg_pfad, layer="ortsgrenze")
    return strassen, gebaeude, grenze

def segmente_bilden(strassen):
    s = strassen.copy()
    s["highway"] = s["highway"].astype(str)
    maske = ~s["highway"].apply(lambda t: any(h in t for h in AUSGESCHLOSSEN_HIGHWAY))
    s = s[maske].copy()

    a = s[["u", "v"]].min(axis=1)
    b = s[["u", "v"]].max(axis=1)
    s["seg_key"] = a.astype(str) + "_" + b.astype(str) + "_" + s["length"].round(0).astype(int).astype(str)
    s["kanten_id"] = s.index

    zuordnung = s[["seg_key", "kanten_id", "u", "v", "oneway"]].copy()
    segmente = s.drop_duplicates("seg_key").copy()
    segmente = segmente.reset_index(drop=True)
    segmente["seg_id"] = segmente.index
    zuordnung = zuordnung.merge(segmente[["seg_key", "seg_id"]], on="seg_key")

    return segmente[["seg_id", "u", "v", "highway", "length", "geometry"]], zuordnung

def gebaeude_vorbereiten(gebaeude):
    g = gebaeude.copy()
    typ = g["building"].astype(str) if "building" in g.columns else pd.Series("yes", index=g.index)
    g["relevant"] = ~typ.isin(NEBENGEBAEUDE)

    if "building:levels" in g.columns:
        etagen = pd.to_numeric(g["building:levels"], errors="coerce")
    else:
        etagen = pd.Series(np.nan, index=g.index)
    etagen = etagen.fillna(STANDARD_GESCHOSSE).clip(lower=1, upper=30)
    g["geschosse"] = etagen
    g["gewicht"] = g["grundflaeche_m2"] * g["geschosse"]
    g.loc[~g["relevant"], "gewicht"] = 0.0

    g = g.reset_index(drop=True)
    g["geb_id"] = g.index
    return g[["geb_id", "relevant", "geschosse", "gewicht", "geometry"]]

def abdeckung_berechnen(segmente, gebaeude, radius):
    puffer = segmente[["seg_id", "geometry"]].copy()
    puffer["geometry"] = puffer.geometry.buffer(radius)

    ziel = gebaeude[gebaeude["relevant"]][["geb_id", "geometry"]]
    paare = gpd.sjoin(ziel, puffer, how="inner", predicate="intersects")
    paare = paare[["seg_id", "geb_id"]].reset_index(drop=True)

    geom_seg = segmente.set_index("seg_id").geometry
    geom_geb = gebaeude.set_index("geb_id").geometry
    paare["abstand_m"] = [
        geom_geb[g].distance(geom_seg[s]) for s, g in zip(paare["seg_id"], paare["geb_id"])
    ]
    return paare

def bericht_daten(radius, paare, gebaeude, segmente):
    relevant = gebaeude[gebaeude["relevant"]]
    erreicht = relevant["geb_id"].isin(paare["geb_id"])
    anteil_zahl = erreicht.mean()
    anteil_gew = relevant.loc[erreicht, "gewicht"].sum() / relevant["gewicht"].sum() if relevant["gewicht"].sum() > 0 else 0
    gebs_pro_seg = paare.groupby("seg_id").size() if not paare.empty else pd.Series([0])
    
    stats = {
        "radius": radius,
        "anteil_zahl": anteil_zahl,
        "anteil_gew": anteil_gew,
        "nicht_erreicht": int((~erreicht).sum()),
        "segs_mit_geb": len(gebs_pro_seg),
        "gesamt_segs": len(segmente)
    }
    return stats, erreicht

def erstelle_abdeckungs_karte(radius, segmente, gebaeude, erreicht, grenze):
    relevant = gebaeude[gebaeude["relevant"]].copy()
    relevant["erreicht"] = erreicht.values
    fig, ax = plt.subplots(figsize=(10, 10))
    grenze.boundary.plot(ax=ax, color="red", linewidth=1)
    segmente.plot(ax=ax, color="lightsteelblue", linewidth=0.5)
    relevant[relevant["erreicht"]].plot(ax=ax, color="lightgrey", linewidth=0)
    relevant[~relevant["erreicht"]].plot(ax=ax, color="crimson", linewidth=0)
    ax.set_title(f"Nicht erreichbare Gebäude (rot) bei {radius} m Hörweite")
    ax.set_axis_off()
    return fig

def fuehre_abdeckung_aus(radien_m_str="50,80,100,150", haupt_radius=80):
    radien = [int(x.strip()) for x in radien_m_str.split(",") if x.strip()]
    if haupt_radius not in radien:
        haupt_radius = radien[0]
        
    strassen, gebaeude_roh, grenze = lade_daten()
    segmente, kanten_zuordnung = segmente_bilden(strassen)
    gebaeude = gebaeude_vorbereiten(gebaeude_roh)

    ergebnisse_liste = []
    haupt_fig = None
    
    for r in radien:
        paare = abdeckung_berechnen(segmente, gebaeude, r)
        paare.to_csv(DATA_DIR / f"abdeckung_R{r}.csv", index=False)
        stats, erreicht = bericht_daten(r, paare, gebaeude, segmente)
        ergebnisse_liste.append(stats)
        
        if r == haupt_radius:
            haupt_fig = erstelle_abdeckungs_karte(r, segmente, gebaeude, erreicht, grenze)

    # Im data-Ordner speichern
    segmente.to_file(DATA_DIR / "abdeckung_segmente.gpkg", layer="segmente", driver="GPKG")
    gebaeude.to_file(DATA_DIR / "abdeckung_segmente.gpkg", layer="gebaeude", driver="GPKG")
    kanten_zuordnung.to_csv(DATA_DIR / "segment_kanten.csv", index=False)
    
    return ergebnisse_liste, haupt_fig, haupt_radius