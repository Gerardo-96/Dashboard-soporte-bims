import streamlit as st
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd

TZ_PY = ZoneInfo("America/Asuncion")

def formatear_fecha_corta(fecha_str: str) -> str:
    dt = datetime.strptime(fecha_str, "%Y-%m-%d")
    meses = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    return f"{dt.day:02d} {meses[dt.month - 1]}"

def formatear_fecha_larga(fecha_str: str) -> str:
    dt = datetime.strptime(fecha_str, "%Y-%m-%d")
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    return f"Sábado {dt.day:02d} de {meses[dt.month - 1]} {dt.year}"

@st.dialog("📅 Calendario de Guardias Sabatinas (09:00 - 12:00)", width="large")
def modal_calendario_guardias(supabase):
    hoy_py = datetime.now(TZ_PY).strftime("%Y-%m-%d")

    res = (
        supabase.table("guardias_sabado")
        .select("id, fecha, nota_cambio, usuarios_autorizados(id, nombre)")
        .gte("fecha", hoy_py)
        .order("fecha", desc=False)
        .execute()
    )
    guardias = res.data or []

    if not guardias:
        st.info("No hay guardias programadas próximamente.")
        return

    # --- Sección superior: Intercambio rápido entre compañeros ---
    with st.expander("🔄 Intercambiar guardia entre compañeros", expanded=False):
        opciones = {
            f"{formatear_fecha_corta(g['fecha'])} — {g['usuarios_autorizados']['nombre']}": g["fecha"]
            for g in guardias
        }
        etiquetas = list(opciones.keys())

        col1, col2 = st.columns(2)
        with col1:
            sel_a = st.selectbox("Turno 1 (Agente que cede):", etiquetas, index=0)
        with col2:
            sel_b = st.selectbox("Turno 2 (Compañero que cubre):", etiquetas, index=min(1, len(etiquetas) - 1))

        if st.button("Confirmar intercambio", type="primary", use_container_width=True):
            fecha_a = opciones[sel_a]
            fecha_b = opciones[sel_b]

            if fecha_a == fecha_b:
                st.warning("Debes seleccionar dos fechas diferentes para intercambiar.")
            else:
                try:
                    supabase.rpc(
                        "intercambiar_guardia_sabado",
                        {"fecha_a": fecha_a, "fecha_b": fecha_b}
                    ).execute()
                    st.success("✅ ¡Intercambio registrado correctamente!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Error al intercambiar guardias: {e}")

    st.divider()

    # --- Tabla completa de próximos sábados ---
    filas = []
    for idx, g in enumerate(guardias):
        filas.append({
            "Estado": "🟢 Próximo" if idx == 0 else "Programado",
            "Fecha": formatear_fecha_larga(g["fecha"]),
            "Agente asignado": g["usuarios_autorizados"]["nombre"],
            "Observación": g.get("nota_cambio") or "—"
        })

    st.dataframe(
        pd.DataFrame(filas),
        use_container_width=True,
        hide_index=True
    )


def render_widget_guardia(supabase, usuario_actual_id=None):
    """Widget compacto para mostrar en la vista principal del dashboard."""
    hoy_py = datetime.now(TZ_PY).strftime("%Y-%m-%d")

    res = (
        supabase.table("guardias_sabado")
        .select("id, fecha, usuarios_autorizados(id, nombre)")
        .gte("fecha", hoy_py)
        .order("fecha", desc=False)
        .limit(2)
        .execute()
    )
    proximas = res.data or []

    if not proximas:
        return

    actual = proximas[0]
    siguiente = proximas[1] if len(proximas) > 1 else None
    agente_actual = actual["usuarios_autorizados"]["nombre"]
    es_mi_turno = usuario_actual_id and (actual["usuarios_autorizados"]["id"] == usuario_actual_id)

    with st.container(border=True):
        col_info, col_btn = st.columns([4, 1.5], vertical_alignment="center")

        with col_info:
            badge = " · 🔔 **¡Te toca guardia!**" if es_mi_turno else ""
            st.caption(f"🛡️ GUARDIA SABATINA (09:00 - 12:00){badge}")
            st.markdown(f"### {agente_actual} — *{formatear_fecha_corta(actual['fecha'])}*")

            if siguiente:
                st.caption(
                    f"Próximo sábado ({formatear_fecha_corta(siguiente['fecha'])}): "
                    f"**{siguiente['usuarios_autorizados']['nombre']}**"
                )

        with col_btn:
            if st.button("Ver / Cambiar", key="btn_abrir_guardias", use_container_width=True):
                modal_calendario_guardias(supabase)
