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

def obtener_feriados_guardias(supabase) -> set:
    try:
        res = supabase.table("config_notificaciones").select("feriados").eq("id", 1).limit(1).execute()
        if res.data and res.data[0].get("feriados"):
            return {str(f).strip() for f in res.data[0]["feriados"] if str(f).strip()}
    except Exception:
        pass
    return set()

@st.dialog("📅 Calendario de Guardias Sabatinas (09:00 - 12:00)", width="large")
def modal_calendario_guardias(supabase):
    hoy_py = datetime.now(TZ_PY).strftime("%Y-%m-%d")
    feriados_set = obtener_feriados_guardias(supabase)

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

    # Solo permitir intercambiar sábados hábiles (no feriados)
    guardias_habiles = [g for g in guardias if g["fecha"] not in feriados_set]

    with st.expander("🔄 Intercambiar guardia entre compañeros", expanded=False):
        if len(guardias_habiles) < 2:
            st.info("No hay suficientes turnos hábiles disponibles para intercambiar.")
        else:
            opciones = {
                f"{formatear_fecha_corta(g['fecha'])} — {g['usuarios_autorizados']['nombre']}": g["fecha"]
                for g in guardias_habiles
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

    filas = []
    proximo_asignado = False
    for g in guardias:
        es_feriado = g["fecha"] in feriados_set
        if es_feriado:
            estado_txt = "🎉 Feriado (Sin guardia)"
        elif not proximo_asignado:
            estado_txt = "🟢 Próximo"
            proximo_asignado = True
        else:
            estado_txt = "Programado"

        filas.append({
            "Estado": estado_txt,
            "Fecha": formatear_fecha_larga(g["fecha"]),
            "Agente asignado": "—" if es_feriado else g["usuarios_autorizados"]["nombre"],
            "Observación": "Omitido por feriado oficial" if es_feriado else (g.get("nota_cambio") or "—")
        })

    st.dataframe(
        pd.DataFrame(filas),
        use_container_width=True,
        hide_index=True
    )


def render_widget_guardia(supabase, usuario_actual_id=None):
    """Tarjeta .metric-card fija en cabecera con botón integrado a la derecha."""
    hoy_py = datetime.now(TZ_PY).strftime("%Y-%m-%d")
    feriados_set = obtener_feriados_guardias(supabase)

    res = (
        supabase.table("guardias_sabado")
        .select("id, fecha, usuarios_autorizados(id, nombre)")
        .gte("fecha", hoy_py)
        .order("fecha", desc=False)
        .limit(6)
        .execute()
    )
    proximas = [g for g in (res.data or []) if g["fecha"] not in feriados_set][:2]
    if not proximas:
        return

    actual = proximas[0]
    siguiente = proximas[1] if len(proximas) > 1 else None
    agente_actual = actual["usuarios_autorizados"]["nombre"]
    es_mi_turno = usuario_actual_id and (actual["usuarios_autorizados"]["id"] == usuario_actual_id)

    borde_color = "#fbbf24" if es_mi_turno else "#38bdf8"
    badge_turno = (
        '<span style="background:rgba(251,191,36,0.15); color:#fbbf24; padding:1px 7px; '
        'border-radius:10px; font-size:0.63rem; white-space:nowrap; flex-shrink:0;">¡Te toca!</span>'
        if es_mi_turno else ""
    )
    texto_siguiente = (
        f"Próx. ({formatear_fecha_corta(siguiente['fecha'])}): <b>{siguiente['usuarios_autorizados']['nombre']}</b>"
        if siguiente else "09:00 - 12:00 hs"
    )

    st.markdown(f"""
    <div class="metric-card guardia-header-card" style="
        border-left: 4px solid {borde_color};
        padding: 12px 95px 12px 16px;
        margin-bottom: 0px;
    ">
        <div class="metric-card-title" style="font-size: 0.66rem; display: flex; align-items: center; gap: 6px; flex-wrap: nowrap;">
            <span style="white-space: nowrap;">🛡️ GUARDIA {formatear_fecha_corta(actual['fecha']).upper()} (09-12 HS)</span>
            {badge_turno}
        </div>
        <div style="font-size: 1.18rem; font-weight: 800; color: #f8fafc; margin-top: 3px; line-height: 1.2;">
            {agente_actual}
        </div>
        <div style="font-size: 0.72rem; color: #94a3b8; margin-top: 4px;">
            {texto_siguiente}
        </div>
    </div>
    <style>
        .metric-card.guardia-header-card,
        .metric-card.guardia-header-card:hover {{
            transform: none !important;
            box-shadow: 0 4px 12px -2px rgba(0, 0, 0, 0.35) !important;
        }}
        div.st-key-btn_abrir_guardias {{
            margin-top: -62px !important;
            margin-bottom: 24px !important;
            padding-right: 16px !important;
            width: 100% !important;
            text-align: right !important;
            position: relative !important;
            z-index: 10 !important;
        }}
        div.st-key-btn_abrir_guardias > div[data-testid="stButton"] {{
            display: flex !important;
            justify-content: flex-end !important;
            width: 100% !important;
        }}
        div.st-key-btn_abrir_guardias button {{
            margin-left: auto !important;
            margin-right: 0 !important;
            width: auto !important;
            min-width: 112px !important;
            padding: 6px 12px !important;
            font-size: 0.76rem !important;
            height: 34px !important;
            margin-top: 0 !important;
        }}
    </style>
    """, unsafe_allow_html=True)

    if st.button("📅 Ver", key="btn_abrir_guardias"):
        modal_calendario_guardias(supabase)
