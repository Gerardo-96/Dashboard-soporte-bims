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

@st.dialog("Calendario de guardias sabatinas (09:00 - 12:00)", width="large")
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

    with st.expander("Intercambiar guardia entre compañeros", expanded=False):
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
                        st.success("Intercambio registrado correctamente.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al intercambiar guardias: {e}")

    st.divider()

    filas = []
    proximo_asignado = False
    for g in guardias:
        es_feriado = g["fecha"] in feriados_set
        if es_feriado:
            estado_txt = "Feriado (sin guardia)"
        elif not proximo_asignado:
            estado_txt = "Próximo"
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
    """Permite a todos consultar el calendario y destaca al usuario de la próxima guardia hábil."""
    hoy_py = datetime.now(TZ_PY).strftime("%Y-%m-%d")
    es_proxima_guardia = False

    try:
        feriados_set = obtener_feriados_guardias(supabase)
        res = (
            supabase.table("guardias_sabado")
            .select("fecha, usuarios_autorizados(id, nombre)")
            .gte("fecha", hoy_py)
            .order("fecha", desc=False)
            .limit(30)
            .execute()
        )

        # Identifica el primer sábado hábil; no alerta por turnos posteriores.
        primera_proxima = next(
            (
                g for g in (res.data or [])
                if g.get("fecha") not in feriados_set and g.get("usuarios_autorizados")
            ),
            None
        )
        es_proxima_guardia = bool(
            usuario_actual_id
            and primera_proxima
            and primera_proxima["usuarios_autorizados"].get("id") == usuario_actual_id
        )
    except Exception:
        # Un fallo al consultar la asignación no debe quitar el acceso general al calendario.
        es_proxima_guardia = False

    if es_proxima_guardia:
        if st.button(
            "⚠︎",
            help="Tienes asignada la próxima guardia. Haz clic para consultar el calendario.",
            key="btn_abrir_guardias_header_alert",
            type="tertiary"
        ):
            modal_calendario_guardias(supabase)
    else:
        if st.button(
            "Abrir calendario",
            icon=":material/calendar_month:",
            help="Consultar calendario de guardias sabatinas.",
            key="btn_abrir_guardias_header_calendar",
            type="tertiary"
        ):
            modal_calendario_guardias(supabase)
