import os
import streamlit as st
import pandas as pd
import io
import time as time_lib
from datetime import datetime, timedelta, time
import plotly.express as px
import plotly.graph_objects as go
from openpyxl.utils import get_column_letter
from supabase import create_client, Client

# Intentar importar la función de sincronización desde sync_intercom.py
try:
    from sync_intercom import sincronizar_intercom
    SYNC_AVAILABLE = True
except ImportError:
    SYNC_AVAILABLE = False

INTERCOM_APP_ID = "co9kozj6"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")  # Cambiar por tu clave deseada

# ==========================
# CONFIGURACIÓN DE SUPABASE
# ==========================
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://fpkuulubmyxuievvfsrj.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_49BZ9GrO1-3udRQj070uLQ_tgxYV7l1")

@st.cache_resource
def init_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_supabase()

def obtener_datos_supabase():
    """Obtiene todos los registros de la tabla 'conversaciones' paginando en lotes de 1000."""
    todos_los_datos = []
    lote = 0
    tamanio_lote = 1000

    while True:
        inicio = lote * tamanio_lote
        fin = inicio + tamanio_lote - 1
        
        response = supabase.table("conversaciones").select("*").range(inicio, fin).execute()
        datos = response.data
        
        if not datos:
            break
            
        todos_los_datos.extend(datos)
        
        if len(datos) < tamanio_lote:
            break
            
        lote += 1

    df = pd.DataFrame(todos_los_datos)
    return df

st.set_page_config(page_title="Executive Operations Control Center", page_icon="⚡", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #0f172a; color: #f8fafc; }
    
    /* MARGEN SUPERIOR ESTÉTICO PARA EL CONTENIDO PRINCIPAL */
    .block-container {
        padding-top: 2.2rem !important;
        padding-bottom: 1.5rem !important;
    }
    
    /* OCULTAR LA BARRA SUPERIOR DE STREAMLIT */
    header[data-testid="stHeader"] {
        display: none !important;
    }
    
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }

    /* Colapsar espacio muerto en la cabecera del sidebar */
    [data-testid="stSidebarHeader"] {
        padding-top: 0px !important;
        padding-bottom: 0px !important;
        height: 2rem !important;
    }
    
    [data-testid="stSidebarUserContent"] {
        padding-top: 0.2rem !important;
    }
    
    [data-testid="stSidebarCollapseButton"] {
        margin-top: -0.6rem !important;
    }

    .metric-card {
        background-color: #1e293b; color: #f8fafc; padding: 16px;
        border-radius: 10px; border-left: 5px solid #38bdf8;
    }
    .db-info-box {
        background-color: #1e293b; color: #94a3b8; padding: 10px;
        border-radius: 8px; border: 1px solid #334155; font-size: 0.85rem; margin-bottom: 8px;
    }
    .alert-card-critical {
        background-color: #7f1d1d; color: #fef2f2; padding: 16px;
        border-radius: 8px; border-left: 6px solid #ef4444; margin-bottom: 15px;
    }
    .stButton>button { background-color: #0284c7; color: white; font-weight: bold; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

AUDIO_ALARM_HTML = """
<audio autoplay>
  <source src="https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3" type="audio/mpeg">
</audio>
"""

# ==========================
# PARÁMETROS HORARIOS Y FERIADOS
# ==========================
FERIADOS = [
    "2026-04-02", "2026-04-03", "2026-05-01", "2026-05-14", "2026-05-15",
    "2026-06-12", "2026-06-22", "2026-06-30", "2026-08-15", "2026-09-29",
    "2026-12-08", "2026-12-25"
]

DIAS_NORMAL_L_V = [0, 1, 2, 3, 4]
NORMAL_L_V_INICIO = time(8, 0, 0)
NORMAL_L_V_FIN = time(18, 0, 0)

DIAS_NORMAL_SABADO = [5]
NORMAL_SABADO_INICIO = time(9, 0, 0)
NORMAL_SABADO_FIN = time(12, 0, 0)

DIAS_EXTENDIDO_L_J = [0, 1, 2, 3]
EXTENDIDO_L_J_INICIO = time(19, 0, 0)
EXTENDIDO_L_J_FIN = time(2, 0, 0)

DIAS_EXTENDIDO_V_S = [4, 5]
EXTENDIDO_V_S_INICIO = time(18, 0, 0)
EXTENDIDO_V_S_FIN = time(3, 0, 0)

def evaluar_horario(dt_objeto):
    fecha_str = dt_objeto.strftime("%Y-%m-%d")
    dia_semana = dt_objeto.weekday()
    hora_actual = dt_objeto.time()

    dt_ayer = dt_objeto - timedelta(days=1)
    dia_ayer = dt_ayer.weekday()
    fecha_ayer_str = dt_ayer.strftime("%Y-%m-%d")

    if dia_ayer in DIAS_EXTENDIDO_L_J and fecha_ayer_str not in FERIADOS and hora_actual <= EXTENDIDO_L_J_FIN:
        return "extendido"
    if dia_ayer in DIAS_EXTENDIDO_V_S and fecha_ayer_str not in FERIADOS and hora_actual <= EXTENDIDO_V_S_FIN:
        return "extendido"

    if fecha_str in FERIADOS:
        return "fuera de horario"

    if dia_semana in DIAS_NORMAL_L_V and NORMAL_L_V_INICIO <= hora_actual <= NORMAL_L_V_FIN:
        return "normal"
    if dia_semana in DIAS_NORMAL_SABADO and NORMAL_SABADO_INICIO <= hora_actual <= NORMAL_SABADO_FIN:
        return "normal"

    if dia_semana in DIAS_EXTENDIDO_L_J and hora_actual >= EXTENDIDO_L_J_INICIO:
        return "extendido"
    if dia_semana in DIAS_EXTENDIDO_V_S and hora_actual >= EXTENDIDO_V_S_INICIO:
        return "extendido"

    return "fuera de horario"

def evaluar_sla_1ra(por_agente, horario, min_1ra, threshold):
    if por_agente == "excluido" or horario == "fuera de horario":
        return "excluido"
    if pd.isna(min_1ra):
        return "no cumple"
    return "cumple" if min_1ra <= threshold else "no cumple"

def evaluar_sla_gestion(por_agente, horario, min_gest, threshold):
    if por_agente == "excluido" or horario == "fuera de horario":
        return "excluido"
    if pd.isna(min_gest):
        return "sin cerrar"
    return "cumple" if min_gest <= threshold else "no cumple"

def calificacion_a_estrellas(x):
    if pd.isna(x) or str(x).strip() in ["", "None", "nan", "null"]:
        return ""
    try:
        val = int(float(x))
        return "★" * val if val > 0 else ""
    except:
        return ""

def es_chat_cerrado(row):
    estado = str(row.get("estado", "")).strip().lower()
    fecha_cierre = str(row.get("fecha_cierre", "")).strip().lower()
    
    if estado in ["cerrado", "closed", "resolved", "resuelto", "snoozed"]:
        return True
    if fecha_cierre not in ["", "none", "nan", "nat", "null"]:
        return True
    return False

def obtener_df_csat_valido(df_sub):
    if df_sub.empty:
        return pd.DataFrame()
    df_c = df_sub.copy()
    
    df_c["rating_num"] = pd.to_numeric(df_c["rating"], errors="coerce")
    
    df_csat = df_c[
        df_c["rating_num"].notna() &
        (df_c["rating_num"] >= 1) & (df_c["rating_num"] <= 5) &
        (df_c["canal"] != "Correo electrónico") &
        (df_c["agente_asignado"].fillna("").str.strip() != "") &
        (df_c["agente_asignado"] != "Sin asignar")
    ]
    return df_csat

def calcular_csat(df_sub):
    df_valid = obtener_df_csat_valido(df_sub)
    if df_valid.empty:
        return 0.0, 0
    ratings = df_valid["rating_num"]
    positivas = len(ratings[ratings >= 4])
    total = len(ratings)
    return round((positivas / total) * 100, 1), total

def tiempo_hace(dt_obj):
    if not isinstance(dt_obj, datetime) or pd.isna(dt_obj):
        return "Desconocido"
    diff = datetime.now() - dt_obj.replace(tzinfo=None)
    secs = int(diff.total_seconds())
    if secs < 60:
        return f"Hace {secs} seg"
    elif secs < 3600:
        return f"Hace {secs // 60} min"
    elif secs < 86400:
        return f"Hace {secs // 3600} hora(s)"
    else:
        return f"Hace {secs // 86400} día(s)"

# ==========================
# INITIAL SESSION STATE PARAMS
# ==========================
if "auto_refresh" not in st.session_state:
    st.session_state["auto_refresh"] = True
if "refresh_interval" not in st.session_state:
    st.session_state["refresh_interval"] = 5
if "sla_1ra_th" not in st.session_state:
    st.session_state["sla_1ra_th"] = 1.5
if "sla_gest_th" not in st.session_state:
    st.session_state["sla_gest_th"] = 60.0
if "admin_authenticated" not in st.session_state:
    st.session_state["admin_authenticated"] = False

# ==========================
# CARGA DE DATOS DE SUPABASE
# ==========================
df_all = obtener_datos_supabase()

# ==========================
# SIDEBAR / CONTROL CENTER
# ==========================
if not df_all.empty and "created_at" in df_all.columns:
    df_all["created_at"] = pd.to_datetime(df_all["created_at"])
    df_all["updated_at"] = pd.to_datetime(df_all["updated_at"])
    
    min_created_dt = df_all["created_at"].min()
    max_updated_dt = df_all["updated_at"].max() if "updated_at" in df_all.columns else min_created_dt
    
    st.sidebar.markdown(f"""
    <div class="db-info-box">
        <b>💾 Estado Base de Datos (Supabase):</b><br>
        • <b>Actualizado:</b> {tiempo_hace(max_updated_dt)}<br>
        • <b>Registros desde:</b> {min_created_dt.strftime('%d/%m/%Y')}
    </div>
    """, unsafe_allow_html=True)

hoy = datetime.now().date()

if "input_f_desde" not in st.session_state:
    st.session_state["input_f_desde"] = hoy
if "input_f_hasta" not in st.session_state:
    st.session_state["input_f_hasta"] = hoy

col_top1, col_top2 = st.sidebar.columns([1, 1])
with col_top1:
    auto_refresh_val = st.toggle("Autorefresh", value=st.session_state["auto_refresh"])
    st.session_state["auto_refresh"] = auto_refresh_val
with col_top2:
    if st.button("📅 Hoy", use_container_width=True):
        st.session_state["input_f_desde"] = datetime.now().date()
        st.session_state["input_f_hasta"] = datetime.now().date()
        st.rerun()

refresh_interval = st.sidebar.slider("Frecuencia de refresco (seg)", 3, 30, st.session_state["refresh_interval"])
st.session_state["refresh_interval"] = refresh_interval

with st.sidebar.form("form_filtros"):
    f_col1, f_col2 = st.columns(2)
    fecha_desde = f_col1.date_input("Desde", key="input_f_desde")
    fecha_hasta = f_col2.date_input("Hasta", key="input_f_hasta")

    usar_filtro_hora = st.checkbox("Restringir Franja Horaria")
    h_col1, h_col2 = st.columns(2)
    hora_inicio = h_col1.time_input("Inicio", time(8, 0))
    hora_fin = h_col2.time_input("Fin", time(18, 0))

    s_col1, s_col2 = st.columns(2)
    sla_1ra_th = s_col1.number_input("SLA 1ª (m)", value=float(st.session_state["sla_1ra_th"]), step=0.5)
    sla_gest_th = s_col2.number_input("SLA Gest (m)", value=float(st.session_state["sla_gest_th"]), step=5.0)
    st.session_state["sla_1ra_th"] = sla_1ra_th
    st.session_state["sla_gest_th"] = sla_gest_th

    act_sonido = st.checkbox("Alertas Sonoras", value=True)

    btn_aplicar = st.form_submit_button("Aplicar Filtro", use_container_width=True)

st.session_state["f_desde_key"] = fecha_desde
st.session_state["f_hasta_key"] = fecha_hasta

# PROCESAMIENTO DE DATOS
if not df_all.empty:
    df_all["fecha_cierre_dt"] = pd.to_datetime(df_all["fecha_cierre"], errors="coerce")
    df_all["fecha_solo"] = df_all["created_at"].dt.date
    df_all["hora_solo"] = df_all["created_at"].dt.time
    df_all["horario_evaluado"] = df_all["created_at"].apply(evaluar_horario)
    df_all["es_cerrado"] = df_all.apply(es_chat_cerrado, axis=1)

    df_all["sla_1ra_eval"] = df_all.apply(
        lambda r: evaluar_sla_1ra(r.get("por_agente"), r.get("horario_evaluado"), r.get("primera_respuesta_min"), sla_1ra_th), axis=1
    )
    df_all["sla_gest_eval"] = df_all.apply(
        lambda r: evaluar_sla_gestion(r.get("por_agente"), r.get("horario_evaluado"), r.get("tiempo_resolucion_minutos"), sla_gest_th), axis=1
    )

    for col in ["tenant", "company", "nombre_contacto", "motivo_normalizado"]:
        if col not in df_all.columns:
            df_all[col] = "Sin datos" if col != "motivo_normalizado" else "Consulta General"

f_desde, f_hasta = pd.to_datetime(fecha_desde).date(), pd.to_datetime(fecha_hasta).date()
df_filtered = df_all[(df_all["fecha_solo"] >= f_desde) & (df_all["fecha_solo"] <= f_hasta)].copy() if not df_all.empty else pd.DataFrame()

if usar_filtro_hora and not df_filtered.empty:
    df_filtered = df_filtered[(df_filtered["hora_solo"] >= hora_inicio) & (df_filtered["hora_solo"] <= hora_fin)]

# Exportador a Excel
def generar_excel_reporte(df_exp, f_desde_val, f_hasta_val, usar_hora, h_ini, h_fin):
    output = io.BytesIO()
    horario_texto = f"De {h_ini.strftime('%H:%M')} a {h_fin.strftime('%H:%M')} hs" if usar_hora else "Todo el día (Sin restricción)"

    df_reporte = pd.DataFrame()
    df_reporte["Conversación ID"] = df_exp.get("id", "")
    df_reporte["Fecha creación"] = df_exp["created_at"].dt.strftime("%Y-%m-%d %H:%M:%S") if "created_at" in df_exp else ""
    df_reporte["Canal de contacto"] = df_exp.get("canal", "")
    df_reporte["Tenant"] = df_exp.get("tenant", "Sin datos")
    df_reporte["Company"] = df_exp.get("company", "Sin datos")
    df_reporte["Nombre Contacto"] = df_exp.get("nombre_contacto", "Sin nombre")
    df_reporte["Agente asignado"] = df_exp.get("agente_asignado", "")
    df_reporte["Por Agente"] = df_exp.get("por_agente", "")
    df_reporte["Horario Evaluado"] = df_exp.get("horario_evaluado", "")
    df_reporte["Primera respuesta (min)"] = df_exp.get("primera_respuesta_min", None)
    df_reporte["SLA 1ª Resp"] = df_exp.get("sla_1ra_eval", "")
    
    if "rating" in df_exp:
        df_reporte["Calificación"] = df_exp["rating"].apply(calificacion_a_estrellas)
    else:
        df_reporte["Calificación"] = ""
        
    df_reporte["Feedback"] = df_exp.get("feedback", "")
    df_reporte["Agente evaluado"] = df_exp.get("agente_evaluado", "")
    df_reporte["CX Score explanation"] = df_exp.get("cx_score_explanation", "")
    df_reporte["Fecha cierre"] = df_exp.get("fecha_cierre", "").fillna("")
    df_reporte["Etiquetas"] = df_exp.get("etiquetas", "")
    df_reporte["Módulo"] = df_exp.get("modulo", "")
    df_reporte["Cliente"] = df_exp.get("cliente", "")
    df_reporte["Tipo de contacto"] = df_exp.get("tipo_contacto", "")
    df_reporte["Nivel"] = df_exp.get("nivel", "")
    df_reporte["Motivo Normalizado"] = df_exp.get("motivo_normalizado", "Consulta General")
    df_reporte["Tiempo resolución (horas)"] = df_exp.get("tiempo_resolucion_horas", None)
    df_reporte["Tiempo resolución (min)"] = df_exp.get("tiempo_resolucion_minutos", None)
    df_reporte["SLA Tiempo Gestión"] = df_exp.get("sla_gest_eval", "")

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_meta = pd.DataFrame([
            ["REPORTE OPERATIVO DE CONVERSACIONES INTERCOM", ""],
            ["Rango de Fechas Consultado:", f"Desde {f_desde_val} hasta {f_hasta_val}"],
            ["Franja Horaria Aplicada:", horario_texto],
            ["Fecha de Generación:", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
            ["", ""]
        ])
        df_meta.to_excel(writer, index=False, header=False, sheet_name="Detalle", startrow=0)
        df_reporte.to_excel(writer, index=False, sheet_name="Detalle", startrow=6)
        
        ws = writer.sheets["Detalle"]
        for i, col in enumerate(df_reporte.columns, 1):
            ws.column_dimensions[get_column_letter(i)].width = 24

    output.seek(0)
    return output

if not df_filtered.empty:
    st.sidebar.markdown("---")
    st.sidebar.download_button(
        label="📥 Exportar Excel",
        data=generar_excel_reporte(df_filtered, f_desde, f_hasta, usar_filtro_hora, hora_inicio, hora_fin),
        file_name=f"reporte_intercom_{f_desde}_a_{f_hasta}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True
    )

# Header
st.title("Dashboard Soporte BIMS")

# TABS PRINCIPALES
tab_operativo, tab_resumen, tab_admin = st.tabs([
    "Control Operativo & SLA", 
    "Resumen de Chats & Agentes", 
    "⚙️ Administración & Configuración"
])

# ==========================================
# PESTAÑA 1: CONTROL OPERATIVO & SLA
# ==========================================
with tab_operativo:
    now_dt = datetime.now()
    
    df_abiertos_all = df_all[~df_all["es_cerrado"]].copy() if not df_all.empty else pd.DataFrame()
    if not df_abiertos_all.empty:
        df_abiertos_all = df_abiertos_all.drop_duplicates(subset=["id"])

    if not df_abiertos_all.empty:
        df_abiertos_all["min_transcurridos"] = ((now_dt - df_abiertos_all["created_at"].dt.tz_localize(None)).dt.total_seconds() / 60).round(1)
        
        df_criticos_sla = df_abiertos_all[
            (df_abiertos_all["primera_respuesta_min"].isna()) & 
            (df_abiertos_all["min_transcurridos"] >= (sla_1ra_th * 0.8))
        ]

        if not df_criticos_sla.empty:
            st.markdown(f"""
            <div class="alert-card-critical">
                <b>¡ALERTA CRÍTICA DE SLA EN VIVO!</b><br>
                Hay <b>{len(df_criticos_sla)} chat(s) en espera</b> sin respuesta rozando o superando el límite de SLA ({sla_1ra_th} min).
            </div>
            """, unsafe_allow_html=True)

            if act_sonido:
                st.components.v1.html(AUDIO_ALARM_HTML, height=0)

    # CSAT SCORECARD
    st.markdown("### CSAT Performance")
    now_date = datetime.now().date()

    c_hoy, k_hoy = calcular_csat(df_all[df_all["fecha_solo"] == now_date]) if not df_all.empty else (0.0, 0)
    c_ayer, _ = calcular_csat(df_all[df_all["fecha_solo"] == (now_date - timedelta(days=1))]) if not df_all.empty else (0.0, 0)
    diff_hoy = round(c_hoy - c_ayer, 1)

    inicio_sem = now_date - timedelta(days=now_date.weekday())
    c_sem, k_sem = calcular_csat(df_all[(df_all["fecha_solo"] >= inicio_sem) & (df_all["fecha_solo"] <= now_date)]) if not df_all.empty else (0.0, 0)
    ini_sem_ant = inicio_sem - timedelta(days=7)
    fin_sem_ant = inicio_sem - timedelta(days=1)
    c_sem_ant, _ = calcular_csat(df_all[(df_all["fecha_solo"] >= ini_sem_ant) & (df_all["fecha_solo"] <= fin_sem_ant)]) if not df_all.empty else (0.0, 0)
    diff_sem = round(c_sem - c_sem_ant, 1)

    inicio_mes = now_date.replace(day=1)
    c_mes, k_mes = calcular_csat(df_all[(df_all["fecha_solo"] >= inicio_mes) & (df_all["fecha_solo"] <= now_date)]) if not df_all.empty else (0.0, 0)
    fin_mes_ant = inicio_mes - timedelta(days=1)
    ini_mes_ant = fin_mes_ant.replace(day=1)
    c_mes_ant, _ = calcular_csat(df_all[(df_all["fecha_solo"] >= ini_mes_ant) & (df_all["fecha_solo"] <= fin_mes_ant)]) if not df_all.empty else (0.0, 0)
    diff_mes = round(c_mes - c_mes_ant, 1)

    q_act = (now_date.month - 1) // 3 + 1
    ini_q = datetime(now_date.year, 3 * (q_act - 1) + 1, 1).date()
    c_q, k_q = calcular_csat(df_all[(df_all["fecha_solo"] >= ini_q) & (df_all["fecha_solo"] <= now_date)]) if not df_all.empty else (0.0, 0)
    fin_q_ant = ini_q - timedelta(days=1)
    q_ant = (fin_q_ant.month - 1) // 3 + 1
    ini_q_ant = datetime(fin_q_ant.year, 3 * (q_ant - 1) + 1, 1).date()
    c_q_ant, _ = calcular_csat(df_all[(df_all["fecha_solo"] >= ini_q_ant) & (df_all["fecha_solo"] <= fin_q_ant)]) if not df_all.empty else (0.0, 0)
    diff_q = round(c_q - c_q_ant, 1)

    c_rango, k_rango = calcular_csat(df_filtered) if not df_filtered.empty else (0.0, 0)
    duracion_dias = (f_hasta - f_desde).days + 1
    f_hasta_prev = f_desde - timedelta(days=1)
    f_desde_prev = f_hasta_prev - timedelta(days=duracion_dias - 1)
    df_prev_rango = df_all[(df_all["fecha_solo"] >= f_desde_prev) & (df_all["fecha_solo"] <= f_hasta_prev)] if not df_all.empty else pd.DataFrame()
    c_rango_prev, _ = calcular_csat(df_prev_rango)
    diff_rango = round(c_rango - c_rango_prev, 1)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("CSAT Hoy", f"{c_hoy}%", f"{diff_hoy}% vs Ayer", help=f"{k_hoy} respuestas validadas")
    m2.metric("CSAT Esta Semana", f"{c_sem}%", f"{diff_sem}% vs Sem. Ant.", help=f"{k_sem} respuestas validadas")
    m3.metric("CSAT Este Mes", f"{c_mes}%", f"{diff_mes}% vs Mes Ant.", help=f"{k_mes} respuestas validadas")
    m4.metric(f"CSAT Trimestre (Q{q_act})", f"{c_q}%", f"{diff_q}% vs Q Ant.", help=f"{k_q} respuestas validadas")
    m5.metric("CSAT Rango Seleccionado", f"{c_rango}%", f"{diff_rango}% vs Período Ant.", help=f"{k_rango} respuestas validadas ({f_desde} a {f_hasta})")

    # ==========================================
    # GRÁFICO DE EVOLUCIÓN HISTÓRICA DE CSAT (ÚLTIMOS 6 MESES)
    # ==========================================
    with st.expander("📈 Ver Gráfico de Evolución del CSAT (Últimos 6 Meses)", expanded=False):
        if not df_all.empty:
            fecha_6m_atras = (datetime.now() - timedelta(days=180)).date()
            df_6m = df_all[df_all["fecha_solo"] >= fecha_6m_atras].copy()
            df_csat_6m = obtener_df_csat_valido(df_6m)

            if not df_csat_6m.empty:
                df_csat_6m["Periodo_Sort"] = df_csat_6m["created_at"].dt.to_period("M")
                df_csat_6m["Mes_Nombre"] = df_csat_6m["created_at"].dt.strftime("%b %Y")

                res_csat_mensual = []
                for period, grp in df_csat_6m.groupby("Periodo_Sort"):
                    tot = len(grp)
                    pos = len(grp[grp["rating_num"] >= 4])
                    val_csat = round((pos / tot) * 100, 1)
                    res_csat_mensual.append({
                        "Periodo_Sort": period,
                        "Mes": grp["Mes_Nombre"].iloc[0],
                        "CSAT %": val_csat,
                        "Encuestas": tot
                    })

                df_evo_csat = pd.DataFrame(res_csat_mensual).sort_values("Periodo_Sort")

                fig_csat = go.Figure()

                fig_csat.add_trace(go.Scatter(
                    x=df_evo_csat["Mes"],
                    y=df_evo_csat["CSAT %"],
                    mode="lines+markers+text",
                    name="CSAT (%)",
                    text=[f"<b>{v}%</b><br>({n} enc.)" for v, n in zip(df_evo_csat["CSAT %"], df_evo_csat["Encuestas"])],
                    textposition="top center",
                    line=dict(color="#38bdf8", width=4, shape="spline"),
                    marker=dict(size=10, color="#0284c7", symbol="circle", line=dict(color="#ffffff", width=2)),
                    fill="tozeroy",
                    fillcolor="rgba(56, 189, 248, 0.1)"
                ))

                fig_csat.add_shape(
                    type="line",
                    x0=0, x1=1, xref="paper",
                    y0=90, y1=90, yref="y",
                    line=dict(color="#22c55e", width=2, dash="dash")
                )

                fig_csat.add_annotation(
                    x=1, y=90, xref="paper", yref="y",
                    text="<b>Meta Objetivo (90%)</b>",
                    showarrow=False,
                    yshift=12,
                    font=dict(color="#22c55e", size=12)
                )

                fig_csat.update_layout(
                    title="<b>Evolución Mensual de Satisfacción al Cliente (CSAT)</b>",
                    xaxis_title="Mes",
                    yaxis_title="Satisfacción Positiva (%)",
                    yaxis=dict(range=[0, 105], gridcolor="#334155"),
                    xaxis=dict(gridcolor="#334155"),
                    paper_bgcolor="#1e293b",
                    plot_bgcolor="#1e293b",
                    font=dict(color="#f8fafc"),
                    margin=dict(t=50, b=40, l=40, r=40),
                    height=380
                )

                st.plotly_chart(fig_csat, use_container_width=True)
            else:
                st.info("No hay suficientes encuestas validadas en los últimos 6 meses para generar el gráfico.")
        else:
            st.info("Sin registros en la base de datos.")

    # TABLA DE DETALLE DE CSAT DEL PERÍODO CONSULTADO
    if not df_filtered.empty:
        df_csat_det = obtener_df_csat_valido(df_filtered)
        if not df_csat_det.empty:
            with st.expander(f"📋 Ver Detalle de Calificaciones CSAT ({len(df_csat_det)} Encuestas Validadas)", expanded=False):
                df_csat_det["Acceso Directo"] = df_csat_det["id"].apply(
                    lambda x: f"https://app.intercom.io/a/apps/{INTERCOM_APP_ID}/inbox/inbox/all/conversations/{x}"
                )
                df_csat_det["Calificación"] = df_csat_det["rating_num"].apply(calificacion_a_estrellas)
                df_csat_det = df_csat_det.sort_values(by=["rating_num", "created_at"], ascending=[True, False])

                st.dataframe(
                    df_csat_det[[
                        "id", "Acceso Directo", "created_at", "Calificación", "feedback", 
                        "nombre_contacto", "tenant", "company", "agente_evaluado", "cx_score_explanation"
                    ]],
                    column_config={
                        "id": "ID Chat",
                        "Acceso Directo": st.column_config.LinkColumn("Intercom Link", display_text="Abrir Chat ↗"),
                        "created_at": "Fecha/Hora",
                        "Calificación": "Puntaje",
                        "feedback": "Comentario / Feedback",
                        "nombre_contacto": "Contacto",
                        "tenant": "Tenant",
                        "company": "Company",
                        "agente_evaluado": "Agente Evaluado",
                        "cx_score_explanation": "Explicación CX"
                    },
                    hide_index=True,
                    use_container_width=True
                )

    st.markdown("---")

    # RESUMEN POR AGENTE
    st.markdown("### Métricas por Agente")
    if not df_filtered.empty:
        v_df = df_filtered[(df_filtered["por_agente"] == "no excluido") & (df_filtered["horario_evaluado"] != "fuera de horario")]
        p_1r = round(v_df["primera_respuesta_min"].mean(), 2) if not v_df.empty else 0
        p_gest = round(v_df["tiempo_resolucion_minutos"].mean(), 2) if not v_df.empty else 0

        df_cerrados = df_filtered[df_filtered["es_cerrado"]]

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Prom. 1ª Respuesta", f"{p_1r} min")
        k2.metric("Prom. Tiempo Gestión", f"{p_gest} min")
        k3.metric("Total Chats Consultados", len(df_filtered))
        k4.metric("Total Chats Cerrados", len(df_cerrados))

        res_agentes = []
        for agente, grp in df_filtered.groupby("agente_asignado"):
            v_g = grp[(grp["por_agente"] == "no excluido") & (grp["horario_evaluado"] != "fuera de horario")]
            asig = len(grp)
            cerr = len(grp[grp["es_cerrado"]])
            p_1 = round(v_g["primera_respuesta_min"].mean(), 2) if not v_g.empty else 0
            
            v_g_1ra = v_g[v_g["primera_respuesta_min"].notna()]
            if not v_g_1ra.empty:
                cumplen_1ra = len(v_g_1ra[v_g_1ra["primera_respuesta_min"] <= sla_1ra_th])
                sla_1 = round((cumplen_1ra / len(v_g_1ra)) * 100, 1)
            else:
                sla_1 = 0.0

            v_g_gest = v_g[v_g["tiempo_resolucion_minutos"].notna()]
            if not v_g_gest.empty:
                cumplen_gest = len(v_g_gest[v_g_gest["tiempo_resolucion_minutos"] <= sla_gest_th])
                sla_g = round((cumplen_gest / len(v_g_gest)) * 100, 1)
            else:
                sla_g = 0.0

            res_agentes.append({
                "Agente": agente, 
                "Asignados": asig, 
                "Cerrados": cerr,
                "Prom. 1ª Resp (min)": p_1, 
                f"% SLA 1ª Resp (≤{sla_1ra_th}m)": f"{sla_1}%", 
                f"% SLA Gestión (≤{sla_gest_th}m)": f"{sla_g}%"
            })
        
        st.dataframe(pd.DataFrame(res_agentes), use_container_width=True)

    st.markdown("---")

    # RANKING DE CHATS ABIERTOS
    st.markdown("### Ranking de Chats Abiertos (Por Antigüedad)")
    if not df_abiertos_all.empty:
        df_rank = df_abiertos_all.copy()
        df_rank["Horas Transcurridas"] = (df_rank["min_transcurridos"] / 60).round(1)
        df_rank = df_rank.sort_values(by="created_at", ascending=True)

        df_rank["Acceso Directo"] = df_rank["id"].apply(
            lambda x: f"https://app.intercom.io/a/apps/{INTERCOM_APP_ID}/inbox/inbox/all/conversations/{x}"
        )

        st.dataframe(
            df_rank[[
                "id", "Acceso Directo", "created_at", "Horas Transcurridas", 
                "nombre_contacto", "tenant", "company", "canal", "agente_asignado", "motivo_normalizado"
            ]],
            column_config={
                "id": "ID Conversación",
                "Acceso Directo": st.column_config.LinkColumn("Intercom Link", display_text="Abrir Chat ↗"),
                "created_at": "Fecha Creación", 
                "Horas Transcurridas": "Horas Abierto",
                "nombre_contacto": "Contacto",
                "tenant": "Tenant",
                "company": "Company",
                "canal": "Canal", 
                "agente_asignado": "Agente Asignado", 
                "motivo_normalizado": "Motivo Normalizado"
            },
            hide_index=True, use_container_width=True, key="tabla_ranking_abiertos_unica"
        )
    else:
        st.info("No hay chats abiertos pendientes en este momento.")

# ==========================================
# PESTAÑA 2: RESUMEN DE CHATS & AGENTES
# ==========================================
with tab_resumen:
    st.markdown(f"### 📈 Análisis de Chats por Agente (`{f_desde}` al `{f_hasta}`)")
    
    if not df_filtered.empty:
        df_res = df_filtered.copy()
        df_res = df_res.sort_values(by="created_at", ascending=True)

        df_res["Día"] = df_res["created_at"].dt.strftime("%Y-%m-%d")

        df_agentes_total = df_res["agente_asignado"].value_counts().reset_index()
        df_agentes_total.columns = ["Agente", "Cantidad de Chats"]

        total_chats_periodo = len(df_res)
        num_dias = df_res["Día"].nunique()
        promedio_diario = round(total_chats_periodo / num_dias, 1) if num_dias > 0 else 0
        top_agente = df_agentes_total.iloc[0]["Agente"] if not df_agentes_total.empty else "N/A"
        top_agente_count = df_agentes_total.iloc[0]["Cantidad de Chats"] if not df_agentes_total.empty else 0
        pct_top = round((top_agente_count / total_chats_periodo) * 100, 1) if total_chats_periodo > 0 else 0

        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Total Chats en Rango", total_chats_periodo)
        r2.metric("Promedio Diario", f"{promedio_diario} chats")
        r3.metric("Agente con Más Chats", top_agente, f"{top_agente_count} chats")
        r4.metric("Participación Top Agente", f"{pct_top}%")

        st.markdown("---")

        g_pie, g_bar = st.columns([1, 1])

        with g_pie:
            st.markdown("#### 🥧 Distribución de Chats por Agente")
            fig_pie = px.pie(
                df_agentes_total, 
                values="Cantidad de Chats", 
                names="Agente",
                hole=0.4,
                color_discrete_sequence=px.colors.qualitative.Pastel
            )
            fig_pie.update_traces(textposition='inside', textinfo='percent+label+value')
            fig_pie.update_layout(showlegend=True, margin=dict(t=20, b=20, l=20, r=20))
            st.plotly_chart(fig_pie, use_container_width=True)

        with g_bar:
            st.markdown("#### 📊 Evolución Diaria por Agente")
            df_dia_agente = df_res.groupby(["Día", "agente_asignado"]).size().reset_index(name="Cantidad")
            fig_bar = px.bar(
                df_dia_agente,
                x="Día",
                y="Cantidad",
                color="agente_asignado",
                barmode="stack",
                title="Volumen de Chats por Día",
                color_discrete_sequence=px.colors.qualitative.Pastel
            )
            st.plotly_chart(fig_bar, use_container_width=True)

        st.markdown("---")

        st.markdown("#### 📋 Tabla Desglosada por Día y Agente")
        df_pivot = df_res.pivot_table(
            index="Día", 
            columns="agente_asignado", 
            values="id", 
            aggfunc="count", 
            fill_value=0
        )
        df_pivot["TOTAL CHATS"] = df_pivot.sum(axis=1)
        st.dataframe(df_pivot, use_container_width=True)
    else:
        st.info("No hay chats registrados para el rango de fechas seleccionado en la barra lateral.")

# ==========================================
# PESTAÑA 3: ADMINISTRACIÓN & CONFIGURACIÓN
# ==========================================
with tab_admin:
    st.markdown("### 🔒 Panel de Administración y Configuración")

    if not st.session_state["admin_authenticated"]:
        st.warning("Esta sección está protegida. Por favor ingresa la contraseña de administrador.")
        
        with st.form("form_login_admin"):
            input_pass = st.text_input("Contraseña", type="password")
            btn_login = st.form_submit_button("Acceder", use_container_width=True)
            
            if btn_login:
                if input_pass == ADMIN_PASSWORD:
                    st.session_state["admin_authenticated"] = True
                    st.success("Acceso concedido.")
                    st.rerun()
                else:
                    st.error("Contraseña incorrecta.")
    else:
        st.success("🔓 Sesión de administración activa.")
        if st.button("Cerrar Sesión Admin"):
            st.session_state["admin_authenticated"] = False
            st.rerun()

        st.markdown("---")
        
        # SECCIÓN 1: FORZAR ACTUALIZACIÓN CON INDICADOR DE PROGRESO
        st.markdown("#### 🔄 Forzar Sincronización Manual de Intercom")
        st.write("Sincroniza directamente los registros desde Intercom a la base de datos de Supabase.")
        
        c_sync1, c_sync2 = st.columns([1, 2])
        dias_a_sincronizar = c_sync1.number_input("Días hacia atrás:", min_value=1, max_value=90, value=3)
        
        if c_sync2.button("🚀 Iniciar Sincronización Manual", use_container_width=True):
            if SYNC_AVAILABLE:
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                try:
                    status_text.text("🔄 Iniciando conexión con Intercom API...")
                    progress_bar.progress(20)
                    time_lib.sleep(0.5)
                    
                    status_text.text(f"⏳ Procesando conversaciones de los últimos {dias_a_sincronizar} días...")
                    progress_bar.progress(50)
                    
                    # Llamada a la función sync
                    sincronizar_intercom(dias=dias_a_sincronizar)
                    
                    progress_bar.progress(90)
                    status_text.text("💾 Guardando cambios en Supabase...")
                    time_lib.sleep(0.5)
                    
                    progress_bar.progress(100)
                    status_text.text("✅ ¡Sincronización completada exitosamente!")
                    st.success("La base de datos fue actualizada. Recargando la vista...")
                    time_lib.sleep(1)
                    st.rerun()
                except Exception as e:
                    status_text.text("❌ Ocurrió un error durante la sincronización.")
                    st.error(f"Detalle del error: {str(e)}")
            else:
                st.error("No se encontró el módulo `sync_intercom.py` en el proyecto. Asegúrate de incluirlo junto con `app.py`.")

        st.markdown("---")

        # SECCIÓN 2: CONTROL DE REFRESCO AUTOMÁTICO Y TIEMPOS DE SLA
        st.markdown("#### ⚙️ Parámetros Globales del Dashboard")
        
        col_cfg1, col_cfg2 = st.columns(2)
        
        with col_cfg1:
            st.markdown("##### ⏱️ Refresco Automático")
            cfg_auto = st.checkbox("Activar Autorefresh por defecto", value=st.session_state["auto_refresh"])
            cfg_interval = st.number_input("Intervalo predeterminado (segundos):", min_value=3, max_value=60, value=st.session_state["refresh_interval"])
        
        with col_cfg2:
            st.markdown("##### 🎯 Umbrales de SLA (Minutos)")
            cfg_sla_1ra = st.number_input("SLA Primera Respuesta (m):", min_value=0.5, max_value=30.0, value=float(st.session_state["sla_1ra_th"]), step=0.5)
            cfg_sla_gest = st.number_input("SLA Tiempo de Gestión (m):", min_value=5.0, max_value=480.0, value=float(st.session_state["sla_gest_th"]), step=5.0)

        if st.button("💾 Guardar Configuración de Parámetros"):
            st.session_state["auto_refresh"] = cfg_auto
            st.session_state["refresh_interval"] = cfg_interval
            st.session_state["sla_1ra_th"] = cfg_sla_1ra
            st.session_state["sla_gest_th"] = cfg_sla_gest
            st.success("Configuración actualizada correctamente.")
            st.rerun()

        st.markdown("---")

        # SECCIÓN 3: DESCARGA DIRECTA DE EXCEL
        st.markdown("#### 📊 Descarga Masiva de Reportes Excel")
        st.write("Genera y descarga un reporte completo formateado en Excel para los registros filtrados actualmente o de forma masiva.")
        
        if not df_filtered.empty:
            st.download_button(
                label="📥 Descargar Reporte Filtrado en Excel",
                data=generar_excel_reporte(df_filtered, f_desde, f_hasta, usar_filtro_hora, hora_inicio, hora_fin),
                file_name=f"reporte_admin_intercom_{f_desde}_a_{f_hasta}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        else:
            st.info("No hay datos filtrados para descargar actualmente.")

if st.session_state["auto_refresh"]:
    time_lib.sleep(st.session_state["refresh_interval"])
    st.rerun()
