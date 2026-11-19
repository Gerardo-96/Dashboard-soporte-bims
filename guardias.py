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
    """Widget compacto estilizado para la esquina superior derecha del header."""
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

    borde_color = "#fbbf24" if es_mi_turno else "#38bdf8"
    badge_turno = (
        '<span style="background:rgba(251,191,36,0.15); color:#fbbf24; padding:1px 7px; '
        'border-radius:10px; font-size:0.68rem; margin-left:6px;">¡Te toca!</span>'
        if es_mi_turno else ""
    )
    texto_siguiente = (
        f"Próx. ({formatear_fecha_corta(siguiente['fecha'])}): <b>{siguiente['usuarios_autorizados']['nombre']}</b>"
        if siguiente else "09:00 - 12:00 hs"
    )

    st.markdown(f"""
    <div class="metric-card" style="
        border-left: 4px solid {borde_color};
        padding: 12px 16px;
        margin-bottom: 6px;
    ">
        <div class="metric-card-title" style="font-size: 0.7rem; justify-content: space-between;">
            <span>🛡️ GUARDIA SÁBADO {formatear_fecha_corta(actual['fecha']).upper()} (09-12 HS){badge_turno}</span>
        </div>
        <div style="font-size: 1.25rem; font-weight: 800; color: #f8fafc; margin-top: 3px; line-height: 1.2;">
            {agente_actual}
        </div>
        <div style="font-size: 0.74rem; color: #94a3b8; margin-top: 4px;">
            {texto_siguiente}
        </div>
    </div>
    """, unsafe_allow_html=True)

    if st.button("📅 Ver calendario / Cambiar turno", key="btn_abrir_guardias", use_container_width=True):
        modal_calendario_guardias(supabase)
