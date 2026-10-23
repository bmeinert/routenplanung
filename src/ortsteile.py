# src/ortsteile.py
from pathlib import Path
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import re
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

PLACE_ARTEN = ["suburb", "quarter", "neighbourhood", "village", "hamlet", "locality"]
SCHADENSGEBIET_NAME = "Schadensgebiet"

def osm_aufbereiten(f, stufen, art):
    """Wählt aus OSM-Features die Ortsteil-Polygone aus."""
    if f is None or len(f) == 0:
        return None
    f = f[f.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    if art == "admin":
        if "admin_level" not in f.columns:
            return None
        f = f[f["admin_level"].astype(str).isin(stufen)].copy()
        f["quelle"] = "osm_admin_" + f["admin_level"].astype(str)
    else:
        if "place" not in f.columns:
            return None
        f = f[f["place"].isin(PLACE_ARTEN)].copy()
        f["quelle"] = "osm_place_" + f["place"].astype(str)
    if len(f) == 0:
        return None
    if "name" not in f.columns:
        f["name"] = np.nan
    f = f.reset_index(drop=True)
    leer = f["name"].isna()
    f.loc[leer, "name"] = [f"Ortsteil {i + 1}" for i in range(int(leer.sum()))]
    return f[["name", "quelle", "geometry"]]

def aus_osm(grenze, stufen):
    import osmnx as ox
    flaeche = grenze.to_crs(4326).geometry.union_all()
    for art, tags in (("admin", {"boundary": "administrative"}), ("place", {"place": PLACE_ARTEN})):
        try:
            f = ox.features_from_polygon(flaeche, tags=tags)
        except Exception:
            continue
        g = osm_aufbereiten(f, stufen, art)
        if g is not None:
            return g.set_crs(4326, allow_override=True) if g.crs is None else g
    return None

def lade_gebaeude():
    eingabe_geb = DATA_DIR / "abdeckung_segmente.gpkg"
    eingabe = DATA_DIR / "daten_ort.gpkg"
    try:
        g = gpd.read_file(eingabe_geb, layer="gebaeude")
        return g[["relevant", "gewicht", "geometry"]]
    except Exception:
        g = gpd.read_file(eingabe, layer="gebaeude")
        g = g.assign(relevant=True, gewicht=g.geometry.area)
        return g[["relevant", "gewicht", "geometry"]]

def bewerte(gebiete, gebaeude):
    rel = gebaeude[gebaeude["relevant"]]
    zent = rel.geometry.centroid
    gesamt_g = rel["gewicht"].sum()
    gesamt_n = len(rel)
    zeilen = []
    for _, r in gebiete.iterrows():
        m = zent.within(r.geometry)
        zeilen.append(dict(
            name=r["name"], 
            quelle=r["quelle"],
            flaeche_km2=round(r.geometry.area / 1e6, 2),
            gebaeude=int(m.sum()),
            anteil_gebaeude=round(m.sum() / gesamt_n, 4) if gesamt_n > 0 else 0,
            anteil_gewicht=round(rel.loc[m, "gewicht"].sum() / gesamt_g, 4) if gesamt_g > 0 else 0
        ))
    tabelle = pd.DataFrame(zeilen).sort_values("gebaeude", ascending=False).reset_index(drop=True)
    ausserhalb = int((~zent.within(gebiete.geometry.union_all())).sum()) if len(gebiete) > 0 else len(zent)
    return tabelle, ausserhalb, gesamt_n

def erstelle_ortsteile_karte(gebiete, gebaeude, grenze):
    fig, ax = plt.subplots(figsize=(10, 10))
    if not gebaeude.empty:
        gebaeude.plot(ax=ax, color="#cccccc", linewidth=0)
    grenze.boundary.plot(ax=ax, color="red", linewidth=1)
    farben = plt.cm.tab20(np.linspace(0, 1, 20))
    for i, r in gebiete.iterrows():
        c = farben[i % 20]
        gpd.GeoSeries([r.geometry], crs=gebiete.crs).plot(ax=ax, facecolor=c, alpha=0.18, edgecolor=c, linewidth=2)
        p = r.geometry.representative_point()
        ax.annotate(r["name"], (p.x, p.y), ha="center", fontsize=10, fontweight="bold")
    ax.set_title("Ortsteile Übersicht")
    ax.set_axis_off()
    return fig

def verarbeite_ortsteile(stufen_str="9,10"):
    gpkg_pfad = DATA_DIR / "daten_ort.gpkg"
    if not gpkg_pfad.exists():
        raise FileNotFoundError("Die Datei 'daten_ort.gpkg' wurde nicht gefunden. Bitte zuerst Schritt 1 ausführen!")
        
    grenze = gpd.read_file(gpkg_pfad, layer="ortsgrenze")
    stufen = [s.strip() for s in stufen_str.split(",")]
    
    gebiete = aus_osm(grenze, stufen)
    if gebiete is None or len(gebiete) == 0:
        raise ValueError("In OpenStreetMap wurden keine Ortsteil-Flächen für diesen Ort gefunden.")
        
    gebiete = gebiete.to_crs(grenze.crs)
    gebaeude = lade_gebaeude()
    tabelle, ausserhalb, gesamt_n = bewerte(gebiete, gebaeude)
    
    csv_pfad = DATA_DIR / "ortsteile_liste.csv"
    out_gpkg = DATA_DIR / "ortsteile.gpkg"
    tabelle.to_csv(csv_pfad, index=False)
    gebiete.to_file(out_gpkg, layer="ortsteile", driver="GPKG")
    
    fig = erstelle_ortsteile_karte(gebiete, gebaeude, grenze)
    return tabelle, ausserhalb, gesamt_n, fig, str(out_gpkg)

def verarbeite_schadensgebiet(schadensort_str: str, radius_m: float):
    gpkg_pfad = DATA_DIR / "daten_ort.gpkg"
    if not gpkg_pfad.exists():
        raise FileNotFoundError("Die Datei 'daten_ort.gpkg' wurde nicht gefunden. Bitte zuerst Schritt 1 ausführen!")
        
    grenze = gpd.read_file(gpkg_pfad, layer="ortsgrenze")
    
    # Koordinaten parsen
    zahlen = re.findall(r"-?\d+(?:\.\d+)?", schadensort_str)
    if len(zahlen) != 2:
        raise ValueError("Koordinaten nicht lesbar. Erwartet: Breite,Länge (z.B. 53.79,9.99)")
    lat, lon = float(zahlen[0]), float(zahlen[1])
    
    # Schadensgebiet als Kreis umrechnen und auf Ortsgrenze zuschneiden[cite: 3]
    punkt = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(grenze.crs).iloc[0]
    kreis = punkt.buffer(radius_m, 64)
    ort = grenze.geometry.union_all()
    abstand = float(punkt.distance(ort))
    gebiet = kreis.intersection(ort)
    
    if gebiet.geom_type not in ("Polygon", "MultiPolygon"):
        teile = [g for g in getattr(gebiet, "geoms", []) if g.geom_type in ("Polygon", "MultiPolygon")]
        gebiet = unary_union(teile) if teile else Polygon()
        
    if gebiet.is_empty or gebiet.area < 1.0:
        raise ValueError(f"Der Kreis (Radius {radius_m:.0f} m) liegt nicht im Ort. Abstand zur Grenze: {abstand:.0f} m.")
        
    info = dict(abstand_m=abstand, anteil_ausserhalb=float(1 - gebiet.area / kreis.area), imort=abstand == 0.0)
    
    gdf = gpd.GeoDataFrame({
        "name": [SCHADENSGEBIET_NAME], 
        "quelle": ["schadensort"], 
        "lat": [lat],
        "lon": [lon], 
        "radius_m": [radius_m],
        "flaeche_km2": [round(gebiet.area / 1e6, 3)]
    }, geometry=[gebiet], crs=grenze.crs)
    
    gebaeude = lade_gebaeude()
    tabelle, _, gesamt_n = bewerte(gdf, gebaeude)
    
    out_gpkg = DATA_DIR / "schadensgebiet.gpkg"
    gdf.to_file(out_gpkg, layer="ortsteile", driver="GPKG")
    tabelle.to_csv(DATA_DIR / "ortsteile_liste.csv", index=False)
    
    # Karte für Schadensgebiet rendern[cite: 3]
    rel = gebaeude[gebaeude["relevant"]]
    im = rel.geometry.centroid.within(gdf.geometry.iloc[0])
    fig, ax = plt.subplots(figsize=(10, 10))
    if not rel[~im].empty:
        rel[~im].plot(ax=ax, color="#d9d9d9", linewidth=0)
    if not rel[im].empty:
        rel[im].plot(ax=ax, color="#444444", linewidth=0)
    gdf.plot(ax=ax, facecolor="orange", alpha=0.2, edgecolor="darkorange", linewidth=2)
    gpd.GeoSeries([kreis], crs=grenze.crs).boundary.plot(ax=ax, color="darkorange", linewidth=1, linestyle="--")
    grenze.boundary.plot(ax=ax, color="red", linewidth=1)
    ax.plot(punkt.x, punkt.y, marker="*", color="black", markersize=18)
    ax.set_axis_off()
    
    return tabelle, info, gesamt_n, fig, str(out_gpkg)