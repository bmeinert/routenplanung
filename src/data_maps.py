from pathlib import Path
import osmnx as ox
import geopandas as gpd
import matplotlib.pyplot as plt

# Basis-Pfad ermitteln (vom src-Ordner einen Schritt hoch zum Projektroot)
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# Sicherstellen, dass der data-Ordner existiert
DATA_DIR.mkdir(parents=True, exist_ok=True)

def lade_und_verarbeite_kartendaten(ort: str, puffer_m: int = 150):
    """
    Lädt Straßen und Gebäude von OSM, projiziert sie und speichert sie im data/-Ordner.
    Gibt Kennzahlen und die Matplotlib-Figur zurück.
    """
    ox.settings.use_cache = True
    ox.settings.log_console = False

    # 1) Ortsgrenze holen und in ein metrisches CRS (UTM) projizieren
    grenze = ox.geocode_to_gdf(ort)
    crs_m = grenze.estimate_utm_crs()
    grenze_m = grenze.to_crs(crs_m)
    flaeche_km2 = grenze_m.area.sum() / 1e6

    # 2) Gepufferte Fläche für den Download
    suchflaeche = grenze_m.buffer(puffer_m).to_crs(4326).union_all()
    #suchflaeche = grenze_m.buffer(puffer_m).to_crs(4326).union_all()
    #if not isinstance(suchflaeche, (Polygon, MultiPolygon)):
    #    raise TypeError(f"Unerwarteter Geometrietyp: {suchflaeche.geom_type}")

    # 3) Straßennetz für Kfz laden
    G = ox.graph_from_polygon(suchflaeche, network_type="drive", simplify=True)
    G = ox.project_graph(G, to_crs=crs_m)
    knoten, kanten = ox.graph_to_gdfs(G)
    strassen_laenge_km = kanten['length'].sum() / 1000

    # 4) Gebäude laden und auf Polygone reduzieren
    gebaeude = ox.features_from_polygon(suchflaeche, tags={"building": True})
    if not gebaeude.empty:
        gebaeude = gebaeude[gebaeude.geom_type.isin(["Polygon", "MultiPolygon"])].to_crs(crs_m)
        gebaeude = gebaeude.reset_index(drop=True)
        gebaeude["zentroid"] = gebaeude.geometry.centroid
        gebaeude["grundflaeche_m2"] = gebaeude.geometry.area
    
    # 5) Dateipfade im data/-Ordner definieren
    gpkg_pfad = DATA_DIR / "daten_ort.gpkg"
    graph_pfad = DATA_DIR / "graph_ort.graphml"

    # 6) Speichern
    kanten.reset_index().to_file(gpkg_pfad, layer="strassen", driver="GPKG")
    knoten.reset_index().to_file(gpkg_pfad, layer="knoten", driver="GPKG")
    if not gebaeude.empty:
        gebaeude.drop(columns=["zentroid"]).to_file(gpkg_pfad, layer="gebaeude", driver="GPKG")
    grenze_m.to_file(gpkg_pfad, layer="ortsgrenze", driver="GPKG")
    ox.save_graphml(G, str(graph_pfad))

    # 7) Sichtprüfung / Matplotlib-Figur erstellen
    fig, ax = plt.subplots(figsize=(10, 10))
    grenze_m.boundary.plot(ax=ax, color="red", linewidth=1, label="Ortsgrenze")
    if not gebaeude.empty:
        gebaeude.plot(ax=ax, color="lightgrey", linewidth=0)
    kanten.plot(ax=ax, color="steelblue", linewidth=0.6)
    ax.set_title(f"{ort}: Straßen und Gebäude")
    ax.set_axis_off()
    
    stats = {
        "flaeche": flaeche_km2,
        "knoten": len(knoten),
        "kanten": len(kanten),
        "laenge_km": strassen_laenge_km,
        "gebaeude": len(gebaeude)
    }

    return stats, fig, str(gpkg_pfad)