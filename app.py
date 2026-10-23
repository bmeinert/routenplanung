
import streamlit as st
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent / "src"))

from daten_karte import lade_und_verarbeite_kartendaten
from ortsteile import verarbeite_ortsteile, verarbeite_schadensgebiet

st.set_page_config(page_title="Katastrophenschutz Routing", layout="wide")

st.sidebar.title("Navigation")
schritt = st.sidebar.radio("Wähle einen Schritt:", ["1. Kartendaten & OSM", "2. Einsatzgebiet festlegen"])

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