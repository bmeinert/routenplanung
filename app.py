# app.py
import streamlit as st
import sys
from pathlib import Path

# Damit Python den src-Ordner findet
sys.path.append(str(Path(__file__).parent / "src"))
from data_maps import lade_und_verarbeite_kartendaten  # Ggf. Skript-Namen anpassen falls ohne Nummer

st.set_page_config(page_title="Katastrophenschutz Routing", layout="wide")

st.title("🚨 Katastrophenschutz Routing-Tool")
st.subheader("Schritt 1: Kartendaten & Preprocessing")

# Sidebar für Eingaben
st.sidebar.header("Parameter")
ort_input = st.sidebar.text_input("Ort (OSM-kompatibel)", value="Henstedt-Ulzburg, Deutschland")
puffer_input = st.sidebar.slider("Puffer (Meter)", min_value=50, max_value=500, value=150, step=50)

if st.button("Daten von OpenStreetMap laden & verarbeiten", type="primary"):
    with st.spinner(f"Lade Daten für {ort_input}... Das kann einen Moment dauern."):
        try:
            # Funktion aus dem src-Ordner aufrufen
            stats, fig, gpkg_pfad = lade_und_verarbeite_kartendaten(ort_input, puffer_input)
            
            st.success("Daten erfolgreich geladen und im `data/`-Ordner gespeichert!")
            
            # Kennzahlen als Metriken anzeigen
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Fläche", f"{stats['flaeche']:.1f} km²")
            col2.metric("Straßenlänge", f"{stats['laenge_km']:.0f} km")
            col3.metric("Knoten im Netz", f"{stats['knoten']:,}")
            col4.metric("Gebäude", f"{stats['gebaeude']:,}")
            
            # Karte in Streamlit anzeigen
            st.pyplot(fig)
            
            st.info(f"Gespeicherte Datei: `{gpkg_pfad}`")
            
        except Exception as e:
            st.error(f"Ein Fehler ist aufgetreten: {e}")