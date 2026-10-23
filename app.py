# app.py
import streamlit as st
import sys
from pathlib import Path
import pandas as pd

sys.path.append(str(Path(__file__).parent / "src"))

from daten_karte import lade_und_verarbeite_kartendaten
from ortsteile import verarbeite_ortsteile, verarbeite_schadensgebiet
from abdeckung import fuehre_abdeckung_aus
from auswahl import fuehre_auswahl_aus

st.set_page_config(page_title="Katastrophenschutz Routing", layout="wide")

st.sidebar.title("Navigation")
schritt = st.sidebar.radio("Wähle einen Schritt:", [
    "1. Kartendaten & OSM", 
    "2. Einsatzgebiet festlegen", 
    "3. Lautsprecher-Abdeckung",
    "4. Segment-Auswahl (Optimierung)"
])

if schritt == "1. Kartendaten & OSM":
    st.title("🚨 Katastrophenschutz Routing-Tool")
    st.subheader("Schritt 1: Kartendaten & Preprocessing")

    st.sidebar.header("Parameter")
    ort_input = st.sidebar.text_input("Ort (OSM-kompatibel)", value="Henstedt-Ulzburg, Deutschland")
    puffer_input = st.sidebar.slider("Puffer (Meter)", min_value=50, max_value=500, value=150, step=50)

    if st.button("Daten von OpenStreetMap laden & verarbeiten", type="primary"):
        with st.spinner(f"Lade Daten für {ort_input}..."):
            try:
                stats, fig, gpkg_pfad = lade_und_verarbeite_kartendaten(ort_input, puffer_input)
                st.success("Daten erfolgreich geladen und im `data/`-Ordner gespeichert!")
                
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Fläche", f"{stats['flaeche']:.1f} km²")
                col2.metric("Straßenlänge", f"{stats['laenge_km']:.0f} km")
                col3.metric("Knoten im Netz", f"{stats['knoten']:,}")
                col4.metric("Gebäude", f"{stats['gebaeude']:,}")
                
                st.pyplot(fig)
                st.info(f"Gespeicherte Datei: `{gpkg_pfad}`")
            except Exception as e:
                st.error(f"Ein Fehler ist aufgetreten: {e}")

elif schritt == "2. Einsatzgebiet festlegen":
    st.title("🚨 Katastrophenschutz Routing-Tool")
    st.subheader("Schritt 1b: Einsatzgebiet (Ortsteile oder Schadensgebiet)")

    modus = st.sidebar.radio("Modus wählen:", ["Ortsteile aus OpenStreetMap", "Schadensgebiet (Koordinaten & Radius)"])

    if modus == "Ortsteile aus OpenStreetMap":
        st.sidebar.header("Parameter Ortsteile")
        stufen_input = st.sidebar.text_input("OSM-Verwaltungsstufen", value="9,10")

        if st.button("Ortsteile generieren", type="primary"):
            with st.spinner("Analysiere Ortsteile und Gebäude..."):
                try:
                    tabelle, ausserhalb, gesamt_n, fig, out_gpkg = verarbeite_ortsteile(stufen_input)
                    st.success("Ortsteile erfolgreich aufbereitet!")
                    
                    col1, col2 = st.columns(2)
                    col1.metric("Gefundene Ortsteile", len(tabelle))
                    col2.metric("Gebäude außerhalb von Ortsteilen", f"{ausserhalb} ({ausserhalb / gesamt_n:.1%})")
                    
                    st.dataframe(tabelle)
                    st.pyplot(fig)
                    st.info(f"Gespeicherte GeoPackage-Datei: `{out_gpkg}`")
                except Exception as e:
                    st.error(f"Ein Fehler ist aufgetreten: {e}")

    else:
        st.sidebar.header("Parameter Schadensgebiet")
        coords_input = st.sidebar.text_input("Schadensort (Breite, Länge)", value="53.79,9.99")
        radius_input = st.sidebar.number_input("Warnradius (Meter)", min_value=100, max_value=10000, value=1500, step=100)

        if st.button("Schadensgebiet berechnen", type="primary"):
            with st.spinner("Berechne Schadensgebiet und betroffene Gebäude..."):
                try:
                    tabelle, info, gesamt_n, fig, out_gpkg = verarbeite_schadensgebiet(coords_input, radius_input)
                    st.success("Schadensgebiet erfolgreich ermittelt!")
                    
                    # Statusmeldung zum Ort
                    if info["imort"]:
                        st.info("Der Schadensort liegt direkt im Ort.")
                    else:
                        st.warning(f"Der Schadensort liegt {info['abstand_m']:.0f} m außerhalb der Ortsgrenze.")
                    
                    col1, col2, col3 = st.columns(3)
                    col1.metric("Fläche im Ort", f"{tabelle['flaeche_km2'].iloc[0]} km²")
                    col2.metric("Betroffene Gebäude", int(tabelle['gebaeude'].iloc[0]))
                    col3.metric("Anteil am Ort", f"{tabelle['anteil_gebaeude'].iloc[0]:.1%}")
                    
                    st.dataframe(tabelle)
                    st.pyplot(fig)
                    st.info(f"Gespeicherte GeoPackage-Datei: `{out_gpkg}`")
                except Exception as e:
                    st.error(f"Ein Fehler ist aufgetreten: {e}")


elif schritt == "3. Lautsprecher-Abdeckung":
    st.title("🚨 Katastrophenschutz Routing-Tool")
    st.subheader("Schritt 2: Abdeckungsmodell für Lautsprecherwagen")

    st.sidebar.header("Parameter Abdeckung")
    radien_input = st.sidebar.text_input("Hörweiten in m (kommagetrennt)", value="50,80,100,150")
    haupt_radius_input = st.sidebar.number_input("Haupt-Radius für Karte (m)", min_value=10, max_value=500, value=80, step=10)

    if st.button("Abdeckung berechnen & analysieren", type="primary"):
        with st.spinner("Berechne Straßensegmente und Puffer-Abdeckungen..."):
            try:
                ergebnisse, fig, genutzter_radius = fuehre_abdeckung_aus(radien_input, int(haupt_radius_input))
                st.success("Abdeckungsanalyse erfolgreich abgeschlossen!")
                
                # Sensitivitätsanalyse als Tabelle anzeigen
                st.markdown("### 📊 Sensitivitätsanalyse nach Hörweiten")
                df_res = pd.DataFrame(ergebnisse)
                df_res["anteil_zahl"] = df_res["anteil_zahl"].apply(lambda x: f"{x:.1%}")
                df_res["anteil_gew"] = df_res["anteil_gew"].apply(lambda x: f"{x:.1%}")
                st.dataframe(df_res.rename(columns={
                    "radius": "Radius (m)",
                    "anteil_zahl": "Erreichbar (Anzahl)",
                    "anteil_gew": "Erreichbar (Gewichtet)",
                    "nicht_erreicht": "Nicht erreichbar",
                    "segs_mit_geb": "Segmente m. Gebäude",
                    "gesamt_segs": "Gesamt-Segmente"
                }))
                
                st.markdown(f"### 🗺️ Unterversorgte Gebäude bei {genutzter_radius} m Radius")
                st.pyplot(fig)
                st.info("Gespeicherte Dateien im `data/`-Ordner: `abdeckung_segmente.gpkg`, `segment_kanten.csv`, `abdeckung_R*.csv`")
                
            except Exception as e:
                st.error(f"Ein Fehler ist aufgetreten: {e}")


elif schritt == "4. Segment-Auswahl (Optimierung)":
    st.title("🚨 Katastrophenschutz Routing-Tool")
    st.subheader("Schritt 3: Gewichtetes Set Cover (Optimale Segment-Auswahl)")

    st.sidebar.header("Parameter Optimierung")
    radius_wahl = st.sidebar.number_input("Verwendeter Hörradius (m)", min_value=10, max_value=500, value=80, step=10)
    ziel_abdeckung = st.sidebar.slider("Zielabdeckung des Gewichts", min_value=0.5, max_value=1.0, value=0.95, step=0.01)
    zeitlimit_input = st.sidebar.slider("Opt. Zeitlimit (Sekunden)", min_value=10, max_value=300, value=60, step=10)

    # Prüfen, ob Daten aus Schritt 1b vorliegen (Ortsteile oder Schadensgebiet)
    ortsteile_datei = None
    verfuegbare_ortsteile = []
    
    if (Path("data/schadensgebiet.gpkg").exists()):
        ortsteile_datei = "schadensgebiet.gpkg"
        verfuegbare_ortsteile = ["Schadensgebiet"]
        st.info("📌 Verwende definiertes Schadensgebiet aus Schritt 1b.")
    elif (Path("data/ortsteile.gpkg").exists()):
        ortsteile_datei = "ortsteile.gpkg"
        try:
            df_ortsteile = gpd.read_file(Path("data/ortsteile.gpkg"), layer="ortsteile")
            verfuegbare_ortsteile = df_ortsteile["name"].tolist()
        except Exception:
            pass
        st.info("📌 Verwende Ortsteile aus Schritt 1b.")

    gewaehlte_ortsteile = st.multiselect("Einsatzgebiet einschränken (aus Schritt 1b)", options=verfuegbare_ortsteile, default=verfuegbare_ortsteile)

    if st.button("Optimierung starten", type="primary"):
        with st.spinner("Löse Optimierungsmodell (MILP / Greedy) für das gewählte Gebiet..."):
            try:
                bericht, fig, out_gpkg = führe_auswahl_aus(
                    radius_m=int(radius_wahl), 
                    zielabdeckung=ziel_abdeckung, 
                    zeitlimit_s=int(zeitlimit_input),
                    ortsteile_namen=gewaehlte_ortsteile if gewaehlte_ortsteile else None,
                    ortsteile_datei=ortsteile_datei
                )
                st.success("Segment-Auswahl erfolgreich optimiert!")
                
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Verwendete Methode", bericht["quelle"])
                col2.metric("Gewählte Segmente", bericht["segmente"])
                col3.metric("Benötigte Länge", f"{bericht['laenge_km']:.1f} km")
                col4.metric("Netz-Anteil", f"{bericht['anteil_netz']:.0%}")
                
                col5, col6 = st.columns(2)
                col5.metric("Erreichte Zielabdeckung", f"{bericht['anteil_erreichbar_gew']:.1%}")
                col6.metric("Vergleich zu Greedy", f"{bericht['ersparnis']:.1%}")
                
                st.pyplot(fig)
                st.info(f"Gespeicherte GeoPackage-Datei: `{out_gpkg}`")
                
            except Exception as e:
                st.error(f"Ein Fehler ist aufgetreten: {e}")