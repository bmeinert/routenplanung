# app.py
import sys
import json
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).parent / "src"))

from daten_karte import lade_und_verarbeite_kartendaten
from ortsteile import (verarbeite_ortsteile, verarbeite_schadensgebiet,
                       setze_gesamten_ort, EINSATZGEBIET_DATEI)
from abdeckung import fuehre_abdeckung_aus
from auswahl import fuehre_auswahl_aus
from routen import fuehre_routenplanung_aus

DATA_DIR = Path(__file__).parent / "data"
EINSATZGEBIET = DATA_DIR / EINSATZGEBIET_DATEI

st.set_page_config(page_title="Katastrophenschutz Routing", layout="wide")

# ---------------------------------------------------------------- Session-Helfer
# Streamlit verwirft Widget-Werte, wenn das Widget auf einer Seite nicht gezeichnet
# wird. Dieser Trick hält alle Widgets mit Key-Präfix "w_" über Seitenwechsel hinweg.
for _k in list(st.session_state.keys()):
    if _k.startswith("w_"):
        st.session_state[_k] = st.session_state[_k]


def speichere(key, **daten):
    for v in daten.values():
        if isinstance(v, plt.Figure):
            plt.close(v)  # Figur bleibt für st.pyplot nutzbar, pyplot sammelt sie aber nicht an
    st.session_state.setdefault("ergebnisse", {})[key] = daten


def hole(key):
    return st.session_state.get("ergebnisse", {}).get(key)


def verwerfe(*keys):
    for k in keys:
        st.session_state.get("ergebnisse", {}).pop(k, None)


# ---------------------------------------------------------------- Einsatzgebiet-Helfer
def lese_einsatzgebiet():
    if not EINSATZGEBIET.exists():
        return None
    try:
        return gpd.read_file(EINSATZGEBIET, layer="ortsteile")
    except Exception:
        return None


def gebiet_mtime():
    return EINSATZGEBIET.stat().st_mtime if EINSATZGEBIET.exists() else None


def lese_auswahl_meta():
    pfad = DATA_DIR / "auswahl_meta.json"
    if not pfad.exists():
        return None
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except Exception:
        return None


def zeige_aktives_gebiet():
    g = lese_einsatzgebiet()
    if g is None:
        st.sidebar.warning("Kein Einsatzgebiet festgelegt – Optimierung nutzt den gesamten Ort.")
        return
    modus = g["modus"].iloc[0] if "modus" in g.columns else "unbekannt"
    flaeche = g.geometry.area.sum() / 1e6
    st.sidebar.success(f"📍 Einsatzgebiet: **{modus}** · {flaeche:.1f} km²")


def pruefe_aktualitaet():
    """Warnt, wenn die Auswahl für ein anderes Einsatzgebiet berechnet wurde."""
    meta = lese_auswahl_meta()
    if meta is None:
        return
    if meta.get("gebiet_mtime") != gebiet_mtime():
        st.warning("⚠️ Die Segment-Auswahl wurde für ein **anderes Einsatzgebiet** berechnet. "
                   "Bitte Schritt 4 erneut ausführen.")
    else:
        namen = ", ".join(meta["namen"]) if meta["namen"] else "–"
        st.caption(f"Auswahl berechnet für: {meta['modus']} ({namen}), "
                   f"Radius {meta['radius_m']} m, Ziel {meta['zielabdeckung']:.0%}")


# ---------------------------------------------------------------- Anzeige-Funktionen
def zeige_schritt1(r):
    st.success("Daten erfolgreich geladen und im `data/`-Ordner gespeichert!")
    s = r["stats"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Fläche", f"{s['flaeche']:.1f} km²")
    c2.metric("Straßenlänge", f"{s['laenge_km']:.0f} km")
    c3.metric("Knoten im Netz", f"{s['knoten']:,}")
    c4.metric("Gebäude", f"{s['gebaeude']:,}")
    st.pyplot(r["fig"])
    st.info(f"Gespeicherte Datei: `{r['pfad']}`")


def zeige_einsatzgebiet(r):
    st.success(f"Einsatzgebiet festgelegt: **{r['titel']}**")
    if r.get("hinweis"):
        level, text = r["hinweis"]
        getattr(st, level)(text)
    cols = st.columns(len(r["metriken"]))
    for col, (label, wert) in zip(cols, r["metriken"].items()):
        col.metric(label, wert)
    if r.get("tabelle") is not None:
        st.dataframe(r["tabelle"])
    st.pyplot(r["fig"])
    st.info(f"Gespeicherte GeoPackage-Datei: `{r['pfad']}`")


def zeige_abdeckung(r):
    st.success("Abdeckungsanalyse erfolgreich abgeschlossen!")
    st.markdown("### 📊 Sensitivitätsanalyse nach Hörweiten")
    df = pd.DataFrame(r["ergebnisse"])
    df["anteil_zahl"] = df["anteil_zahl"].apply(lambda x: f"{x:.1%}")
    df["anteil_gew"] = df["anteil_gew"].apply(lambda x: f"{x:.1%}")
    st.dataframe(df.rename(columns={
        "radius": "Radius (m)", "anteil_zahl": "Erreichbar (Anzahl)",
        "anteil_gew": "Erreichbar (Gewichtet)", "nicht_erreicht": "Nicht erreichbar",
        "segs_mit_geb": "Segmente m. Gebäude", "gesamt_segs": "Gesamt-Segmente"}))
    st.markdown(f"### 🗺️ Unterversorgte Gebäude bei {r['radius']} m Radius")
    st.pyplot(r["fig"])


def zeige_auswahl(r):
    st.success("Segment-Auswahl erfolgreich optimiert!")
    b = r["bericht"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Verwendete Methode", b["quelle"])
    c2.metric("Gewählte Segmente", b["segmente"])
    c3.metric("Benötigte Länge", f"{b['laenge_km']:.1f} km")
    c4.metric("Netz-Anteil", f"{b['anteil_netz']:.0%}")
    c5, c6 = st.columns(2)
    c5.metric("Erreichte Zielabdeckung", f"{b['anteil_erreichbar_gew']:.1%}")
    c6.metric("Vergleich zu Greedy", f"{b['ersparnis']:.1%}")
    st.pyplot(r["fig"])
    st.info(f"Gespeicherte GeoPackage-Datei: `{r['pfad']}`")


def zeige_routen(r):
    st.success("Touren erfolgreich geplant!")
    s = r["summary"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Eingesetzte Fahrzeuge", f"{s['fahrzeuge']} von {r['max_autos']}")
    c2.metric("Gesamtfahrstrecke", f"{s['gesamt_laenge_km']:.1f} km")
    c3.metric("Davon Durchsagestrecke", f"{s['durchsage_laenge_km']:.1f} km")
    c4.metric("Längste Tour", f"{s['laengste_tour_min']:.0f} min",
              help=f"Zeitfenster je Tour: {s['fenster_min']} min")

    st.markdown("### ⏱️ Fahrzeit je Route")
    df = pd.DataFrame(s["touren"]).rename(columns={
        "fahrzeug": "Fahrzeug",
        "fahrzeit_min": "Fahrzeit gesamt (min)",
        "davon_durchsage_min": "mit Durchsage (min)",
        "davon_verbindung_min": "Verbindung (min)",
        "strecke_km": "Strecke (km)",
        "durchsage_km": "Durchsagestrecke (km)",
        "segmente": "Segmente",
        "auslastung": "Zeitfenster-Auslastung",
    })
    st.dataframe(
        df.style.format({
            "Fahrzeit gesamt (min)": "{:.0f}",
            "mit Durchsage (min)": "{:.0f}",
            "Verbindung (min)": "{:.0f}",
            "Strecke (km)": "{:.1f}",
            "Durchsagestrecke (km)": "{:.1f}",
            "Zeitfenster-Auslastung": "{:.0%}",
        }),
        hide_index=True,
    )

    st.plotly_chart(r["fig"], use_container_width=True, key="route_aktuell")
    st.info(f"Gespeicherte GeoPackage-Datei: `{r['pfad']}`")


# ---------------------------------------------------------------- Navigation
st.sidebar.title("Navigation")
schritt = st.sidebar.radio("Wähle einen Schritt:", [
    "1. Kartendaten & OSM",
    "2. Einsatzgebiet festlegen",
    "3. Lautsprecher-Abdeckung",
    "4. Segment-Auswahl (Optimierung)",
    "5. Tourenplanung (Routen)",
], key="w_schritt")
zeige_aktives_gebiet()
st.sidebar.divider()

st.title("🚨 Katastrophenschutz Routing-Tool")

# ================================================================ Schritt 1
if schritt == "1. Kartendaten & OSM":
    st.subheader("Schritt 1: Kartendaten & Preprocessing")
    st.sidebar.header("Parameter")
    ort_input = st.sidebar.text_input("Ort (OSM-kompatibel)", value="Henstedt-Ulzburg, Deutschland", key="w_ort")
    puffer_input = st.sidebar.slider("Puffer (Meter)", 50, 500, 150, 50, key="w_puffer")

    if st.button("Daten von OpenStreetMap laden & verarbeiten", type="primary"):
        with st.spinner(f"Lade Daten für {ort_input}..."):
            try:
                stats, fig, pfad = lade_und_verarbeite_kartendaten(ort_input, puffer_input)
                speichere("kartendaten", stats=stats, fig=fig, pfad=pfad)
                # Neue Kartendaten machen alles Folgende ungültig
                verwerfe("einsatzgebiet", "abdeckung", "auswahl", "routen")
            except Exception as e:
                st.error(f"Ein Fehler ist aufgetreten: {e}")

    if hole("kartendaten"):
        zeige_schritt1(hole("kartendaten"))

# ================================================================ Schritt 2
elif schritt == "2. Einsatzgebiet festlegen":
    st.subheader("Schritt 2: Einsatzgebiet (Gesamter Ort, Ortsteile oder Schadensgebiet)")
    modus = st.sidebar.radio("Modus wählen:", ["Gesamter Ort", "Ortsteile aus OpenStreetMap",
                                               "Schadensgebiet (Koordinaten & Radius)"], key="w_modus")

    def neues_gebiet(**daten):
        speichere("einsatzgebiet", **daten)
        verwerfe("auswahl", "routen")  # alte Auswahl/Touren gehören zum alten Gebiet

    if modus == "Gesamter Ort":
        st.info("Verwendet die in Schritt 1 ermittelte Ortsgrenze.")
        if st.button("Gesamten Ort als Einsatzgebiet festlegen", type="primary"):
            try:
                df, fig, pfad = setze_gesamten_ort()
                neues_gebiet(titel="Gesamter Ort",
                             metriken={"Einsatzgebiet": "Gesamter Ort",
                                       "Fläche": f"{df.geometry.area.sum() / 1e6:.1f} km²"},
                             tabelle=None, fig=fig, pfad=pfad)
            except Exception as e:
                st.error(f"Ein Fehler ist aufgetreten: {e}")

    elif modus == "Ortsteile aus OpenStreetMap":
        stufen_input = st.sidebar.text_input("OSM-Verwaltungsstufen", value="9,10", key="w_stufen")
        if st.button("Ortsteile generieren", type="primary"):
            with st.spinner("Analysiere Ortsteile und Gebäude..."):
                try:
                    tabelle, ausserhalb, gesamt_n, fig, pfad = verarbeite_ortsteile(stufen_input)
                    neues_gebiet(titel="Ortsteile",
                                 metriken={"Gefundene Ortsteile": len(tabelle),
                                           "Gebäude außerhalb": f"{ausserhalb} ({ausserhalb / gesamt_n:.1%})"},
                                 tabelle=tabelle, fig=fig, pfad=pfad)
                except Exception as e:
                    st.error(f"Ein Fehler ist aufgetreten: {e}")

    else:
        coords = st.sidebar.text_input("Schadensort (Breite, Länge)", value="53.79,9.99", key="w_coords")
        radius = st.sidebar.number_input("Warnradius (Meter)", 100, 10000, 1500, 100, key="w_warnradius")
        if st.button("Schadensgebiet berechnen", type="primary"):
            with st.spinner("Berechne Schadensgebiet und betroffene Gebäude..."):
                try:
                    tabelle, info, gesamt_n, fig, pfad = verarbeite_schadensgebiet(coords, radius)
                    hinweis = (("info", "Der Schadensort liegt direkt im Ort.") if info["imort"]
                               else ("warning", f"Der Schadensort liegt {info['abstand_m']:.0f} m außerhalb der Ortsgrenze."))
                    neues_gebiet(titel="Schadensgebiet", hinweis=hinweis,
                                 metriken={"Fläche im Ort": f"{tabelle['flaeche_km2'].iloc[0]} km²",
                                           "Betroffene Gebäude": int(tabelle["gebaeude"].iloc[0]),
                                           "Anteil am Ort": f"{tabelle['anteil_gebaeude'].iloc[0]:.1%}"},
                                 tabelle=tabelle, fig=fig, pfad=pfad)
                except Exception as e:
                    st.error(f"Ein Fehler ist aufgetreten: {e}")

    if hole("einsatzgebiet"):
        zeige_einsatzgebiet(hole("einsatzgebiet"))

# ================================================================ Schritt 3
elif schritt == "3. Lautsprecher-Abdeckung":
    st.subheader("Schritt 3: Abdeckungsmodell für Lautsprecherwagen")
    st.sidebar.header("Parameter Abdeckung")
    radien_input = st.sidebar.text_input("Hörweiten in m (kommagetrennt)", value="50,80,100,150", key="w_radien")
    haupt_radius = st.sidebar.number_input("Haupt-Radius für Karte (m)", 10, 500, 80, 10, key="w_hauptradius")

    if st.button("Abdeckung berechnen & analysieren", type="primary"):
        with st.spinner("Berechne Straßensegmente und Puffer-Abdeckungen..."):
            try:
                ergebnisse, fig, genutzt = fuehre_abdeckung_aus(radien_input, int(haupt_radius))
                speichere("abdeckung", ergebnisse=ergebnisse, fig=fig, radius=genutzt)
                verwerfe("auswahl", "routen")  # Segment-IDs können sich geändert haben
            except Exception as e:
                st.error(f"Ein Fehler ist aufgetreten: {e}")

    if hole("abdeckung"):
        zeige_abdeckung(hole("abdeckung"))
        st.info("Gespeichert in `data/`: `abdeckung_segmente.gpkg`, `segment_kanten.csv`, `abdeckung_R*.csv`")

# ================================================================ Schritt 4
elif schritt == "4. Segment-Auswahl (Optimierung)":
    st.subheader("Schritt 4: Gewichtetes Set Cover (Optimale Segment-Auswahl)")
    st.sidebar.header("Parameter Optimierung")
    radius_wahl = st.sidebar.number_input("Verwendeter Hörradius (m)", 10, 500, 80, 10, key="w_optradius")
    ziel = st.sidebar.slider("Zielabdeckung des Gewichts", 0.5, 1.0, 0.95, 0.01, key="w_ziel")
    zeitlimit = st.sidebar.slider("Opt. Zeitlimit (Sekunden)", 10, 300, 60, 10, key="w_optzeit")

    gebiet = lese_einsatzgebiet()
    if gebiet is not None:
        ortsteile_datei = EINSATZGEBIET_DATEI
        optionen = gebiet["name"].tolist()
        modus_txt = gebiet["modus"].iloc[0] if "modus" in gebiet.columns else ""
        st.info(f"📌 Aktives Einsatzgebiet aus Schritt 2: **{modus_txt}**")
    else:
        ortsteile_datei, optionen = None, []
        st.info("📌 Kein Einsatzgebiet festgelegt – es wird der gesamte Ort verwendet.")

    # Key hängt am Änderungszeitpunkt: neues Einsatzgebiet => Multiselect startet frisch mit allen Optionen
    gewaehlt = st.multiselect("Einsatzgebiet einschränken", options=optionen, default=optionen,
                              key=f"w_teile_{gebiet_mtime()}")

    if st.button("Optimierung starten", type="primary"):
        with st.spinner("Löse Optimierungsmodell (MILP / Greedy) für das gewählte Gebiet..."):
            try:
                bericht, fig, pfad = fuehre_auswahl_aus(
                    radius_m=int(radius_wahl), zielabdeckung=ziel, zeitlimit_s=int(zeitlimit),
                    ortsteile_namen=gewaehlt if gewaehlt else None, ortsteile_datei=ortsteile_datei)
                speichere("auswahl", bericht=bericht, fig=fig, pfad=pfad)
                verwerfe("routen")
            except Exception as e:
                st.error(f"Ein Fehler ist aufgetreten: {e}")

    if hole("auswahl"):
        pruefe_aktualitaet()
        zeige_auswahl(hole("auswahl"))

# ================================================================ Schritt 5
elif schritt == "5. Tourenplanung (Routen)":
    st.subheader("Schritt 5: Tourenplanung für die Lautsprecherwagen")
    st.sidebar.header("Parameter Depot & Fahrzeuge")
    depot_lat = st.sidebar.number_input("Depot Breite (Lat)", value=53.792925, format="%.6f", key="w_lat")
    depot_lon = st.sidebar.number_input("Depot Länge (Lon)", value=9.979717, format="%.6f", key="w_lon")
    max_autos = st.sidebar.number_input("Verfügbare Einsatzfahrzeuge (max.)", 1, 50, 5, 1, key="w_autos")

    st.sidebar.header("Parameter Touren")
    fenster = st.sidebar.slider("Zeitfenster je Tour (Minuten)", 30, 480, 90, 15, key="w_fenster")
    tempo = st.sidebar.number_input("Durchsagetempo (km/h)", 5.0, 30.0, 15.0, 2.5, key="w_tempo")
    durchsage_verb = st.sidebar.checkbox("Durchsage auch auf Verbindungsfahrten", value=False, key="w_verb")
    zeitlimit_r = st.sidebar.slider("Opt. Zeitlimit (Sekunden)", 30, 300, 60, 30, key="w_routzeit")

    pruefe_aktualitaet()

    if st.button("Touren berechnen", type="primary"):
        with st.spinner("Berechne optimale Fahrzeugtouren und Tempoprofile..."):
            try:
                summary, fig, pfad = fuehre_routenplanung_aus(
                    fenster_min=int(fenster), tempo_kmh=float(tempo),
                    durchsage_auf_verbindung=durchsage_verb, zeitlimit_s=int(zeitlimit_r),
                    max_fahrzeuge=int(max_autos), depot_lat=float(depot_lat), depot_lon=float(depot_lon))
                speichere("routen", summary=summary, fig=fig, pfad=pfad, max_autos=int(max_autos))
            except Exception as e:
                st.error(f"Ein Fehler ist aufgetreten: {e}")

    if hole("routen"):
        zeige_routen(hole("routen"))