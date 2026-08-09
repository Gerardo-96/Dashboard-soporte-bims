import os
import io
import time as time_lib
from datetime import datetime, timedelta, time
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from openpyxl.utils import get_column_letter
from supabase import create_client, Client

try:
    from sync_intercom import sincronizar_intercom
    SYNC_AVAILABLE = True
except ImportError:
    SYNC_AVAILABLE = False

INTERCOM_APP_ID = "co9kozj6"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")[cite: 5]

# ==========================
# CONFIGURACIÓN DE SUPABASE
# ==========================
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")[cite: 5]
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")[cite: 5]

@st.cache_resource
def init_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)[cite: 5]

supabase = init_supabase()[cite: 5]

def obtener_fecha_local_hoy():
    """Retorna la fecha actual en la zona horaria de Paraguay (America/Asuncion)."""
    return pd.Timestamp.now(tz="America/Asuncion").date()[cite: 5]

def convertir_a_minutos(val):
    """Garantiza la conversión limpia a minutos numéricos float."""
    if pd.isnan(val) or val is None:[cite: 5]
        return None[cite: 5]
    try:
        v = float(val)[cite: 5]
        return round(v, 2)[cite: 5]
    except (ValueError, TypeError):
        return None[cite: 5]

def procesar_fechas_df(df):
    """Convierte las fechas UTC a hora local (UTC-3) y normaliza métricas numéricas."""
    if df.empty or "created_at" not in df.columns:[cite: 5]
        return df[cite: 5]
    
    created_dt = pd.to_datetime(df["created_at"], errors="coerce", utc=True)[cite: 5]
    local_dt = created_dt.dt.tz_convert("America/Asuncion")[cite: 5]
    
    df["created_at_dt"] = local_dt[cite: 5]
    df["created_at_fmt"] = local_dt.dt.strftime("%Y-%m-%d %H:%M").fillna("Sin fecha")[cite: 5]
    df["fecha_solo"] = local_dt.dt.date[cite: 5]
    df["hora_solo"] = local_dt.dt.time[cite: 5]

    col_cierre = "fecha_primer_cierre" if "fecha_primer_cierre" in df.columns else "fecha_cierre"[cite: 5]
    if col_cierre in df.columns:[cite: 5]
        cierre_dt = pd.to_datetime(df[col_cierre], errors="coerce", utc=True)[cite: 5]
        local_cierre = cierre_dt.dt.tz_convert("America/Asuncion")[cite: 5]
        df["fecha_cierre_fmt"] = local_cierre.dt.strftime("%Y-%m-%d %H:%M").fillna("")[cite: 5]

    if "updated_at" in df.columns:[cite: 5]
        updated_dt = pd.to_datetime(df["updated_at"], errors="coerce", utc=True)[cite: 5]
        df["updated_at_local"] = updated_dt.dt.tz_convert("America/Asuncion")[cite: 5]

    if "id" in df.columns:[cite: 5]
        df["id_str"] = df["id"].astype(str).str.strip()[cite: 5]
        df["intercom_url"] = df["id_str"].apply(
            lambda x: f"https://app.intercom.io/a/apps/{INTERCOM_APP_ID}/inbox/inbox/all/conversations/{x}"
        )[cite: 5]

    if "primera_respuesta_min" in df.columns:[cite: 5]
        df["primera_respuesta_min"] = df["primera_respuesta_min"].apply(convertir_a_minutos)[cite: 5]
    if "tiempo_resolucion_minutos" in df.columns:[cite: 5]
        df["tiempo_resolucion_minutos"] = df["tiempo_resolucion_minutos"].apply(convertir_a_minutos)[cite: 5]

    return df[cite: 5]

@st.cache_data(ttl=10)
def obtener_datos_supabase():
    """Obtiene todos los registros de la tabla 'conversaciones' paginando en lotes de 1000."""
    todos_los_datos = [][cite: 5]
    lote = 0[cite: 5]
    tamanio_lote = 1000[cite: 5]

    while True:
        inicio = lote * tamanio_lote[cite: 5]
        fin = inicio + tamanio_lote - 1[cite: 5]
        
        try:
            response = supabase.table("conversaciones").select("*").range(inicio, fin).execute()[cite: 5]
            datos = response.data[cite: 5]
        except Exception:
            break[cite: 5]
        
        if not datos:[cite: 5]
            break[cite: 5]
            
        todos_los_datos.extend(datos)[cite: 5]
        
        if len(datos) < tamanio_lote:[cite: 5]
            break[cite: 5]
            
        lote += 1[cite: 5]

    df = pd.DataFrame(todos_los_datos)[cite: 5]
    return procesar_fechas_df(df)[cite: 5]

# ==========================
# CONTROL DEL SIDEBAR PERSONALIZADO
# ==========================
if "sidebar_state" not in st.session_state:[cite: 5]
    st.session_state["sidebar_state"] = "expanded"[cite: 5]

def toggle_sidebar():
    if st.session_state["sidebar_state"] == "expanded":[cite: 5]
        st.session_state["sidebar_state"] = "collapsed"[cite: 5]
    else:
        st.session_state["sidebar_state"] = "expanded"[cite: 5]

st.set_page_config(
    page_title="Executive Operations Control Center", 
    layout="wide",
    initial_sidebar_state=st.session_state["sidebar_state"]
)[cite: 5]

st.markdown("""
<style>
    .stApp { background-color: #0f172a; color: #f8fafc; }
    
    .block-container {
        padding-top: 1.2rem !important;
        padding-bottom: 1.5rem !important;
    }
    
    /* Ocultar únicamente barra superior y menús no deseados */
    header[data-testid="stHeader"],
    [data-testid="stStatusWidget"],
    [data-testid="stDecoration"],
    [data-testid="stAppViewerBadge"],
    .stAppViewerBadge,
    div[class*="stAppViewerBadge"],
    .stAppToolbar,
    div[data-testid="stDecoration"],
    #MainMenu,
    footer,
    .stApp > footer {
        display: none !important;
        visibility: hidden !important;
        opacity: 0 !important;
        height: 0px !important;
        width: 0px !important;
        pointer-events: none !important;
    }

    [data-testid="stSidebarHeader"] {
        padding-top: 0px !important;
        padding-bottom: 0px !important;
        height: 1rem !important;
    }
    
    [data-testid="stSidebarUserContent"] {
        padding-top: 0.2rem !important;
    }

    .metric-card {
        background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
        color: #f8fafc;
        padding: 16px;
        border-radius: 10px;
        border: 1px solid #334155;
        border-left: 4px solid #0284c7;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2);
    }
    .metric-card-title {
        font-size: 0.78rem;
        color: #94a3b8;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    .metric-card-value {
        font-size: 1.5rem;
        font-weight: 700;
        color: #f8fafc;
        margin-top: 4px;
    }
    .metric-card-sub {
        font-size: 0.75rem;
        color: #38bdf8;
        margin-top: 2px;
    }

    .db-info-box {
        background-color: #1e293b; color: #94a3b8; padding: 12px;
        border-radius: 8px; border: 1px solid #334155; font-size: 0.85rem; margin-bottom: 12px;
    }
    .alert-card-critical {
        background-color: #451a03; color: #fef3c7; padding: 16px;
        border-radius: 8px; border-left: 6px solid #d97706; margin-bottom: 15px;
    }
    .admin-card {
        background-color: #1e293b;
        border: 1px solid #334155;
        border-radius: 10px;
        padding: 18px;
        margin-bottom: 15px;
    }
    .stButton>button { background-color: #0284c7; color: white; font-weight: bold; border-radius: 8px; border: none; }
</style>
""", unsafe_allow_html=True)[cite: 5]

AUDIO_ALARM_HTML = """
<audio autoplay>
  <source src="https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3" type="audio/mpeg">
</audio>
"""[cite: 5]

# ==========================
# PARÁMETROS HORARIOS Y FERIADOS
# ==========================
FERIADOS = [
    "2026-04-02", "2026-04-03", "2026-05-01", "2026-05-14", "2026-05-15",
    "2026-06-12", "2026-06-22", "2026-06-30", "2026-08-15", "2026-09-29",
    "2026-12-08", "2026-12-25"
][cite: 5]

DIAS_NORMAL_L_V = [0, 1, 2, 3, 4][cite: 5]
NORMAL_L_V_INICIO = time(8, 0, 0)[cite: 5]
NORMAL_L_V_FIN = time(18, 0, 0)[cite: 5]

DIAS_NORMAL_SABADO = [5][cite: 5]
NORMAL_SABADO_INICIO = time(9, 0, 0)[cite: 5]
NORMAL_SABADO_FIN = time(12, 0, 0)[cite: 5]

DIAS_EXTENDIDO_L_J = [0, 1, 2, 3][cite: 5]
EXTENDIDO_L_J_INICIO = time(19, 0, 0)[cite: 5]
EXTENDIDO_L_J_FIN = time(2, 0, 0)[cite: 5]

DIAS_EXTENDIDO_V_S = [4, 5][cite: 5]
EXTENDIDO_V_S_INICIO = time(18, 0, 0)[cite: 5]
EXTENDIDO_V_S_FIN = time(3, 0, 0)[cite: 5]

def evaluar_horario_dashboard(dt_objeto):
    if pd.isna(dt_objeto):[cite: 5]
        return "fuera de horario"[cite: 5]
    fecha_str = dt_objeto.strftime("%Y-%m-%d")[cite: 5]
    dia_semana = dt_objeto.weekday()[cite: 5]
    hora_actual = dt_objeto.time()[cite: 5]

    dt_ayer = dt_objeto - timedelta(days=1)[cite: 5]
    dia_ayer = dt_ayer.weekday()[cite: 5]
    fecha_ayer_str = dt_ayer.strftime("%Y-%m-%d")[cite: 5]

    if dia_ayer in DIAS_EXTENDIDO_L_J and fecha_ayer_str not in FERIADOS and hora_actual <= EXTENDIDO_L_J_FIN:[cite: 5]
        return "extendido"[cite: 5]
    if dia_ayer in DIAS_EXTENDIDO_V_S and fecha_ayer_str not in FERIADOS and hora_actual <= EXTENDIDO_V_S_FIN:[cite: 5]
        return "extendido"[cite: 5]

    if fecha_str in FERIADOS:[cite: 5]
        return "fuera de horario"[cite: 5]

    if dia_semana in DIAS_NORMAL_L_V and NORMAL_L_V_INICIO <= hora_actual <= NORMAL_L_V_FIN:[cite: 5]
        return "normal"[cite: 5]
    if dia_semana in DIAS_NORMAL_SABADO and NORMAL_SABADO_INICIO <= hora_actual <= NORMAL_SABADO_FIN:[cite: 5]
        return "normal"[cite: 5]

    if dia_semana in DIAS_EXTENDIDO_L_J and hora_actual >= EXTENDIDO_L_J_INICIO:[cite: 5]
        return "extendido"[cite: 5]
    if dia_semana in DIAS_EXTENDIDO_V_S and hora_actual >= EXTENDIDO_V_S_INICIO:[cite: 5]
        return "extendido"[cite: 5]

    return "fuera de horario"[cite: 5]

def evaluar_horario_estricto(dt_objeto):
    if pd.isna(dt_objeto):[cite: 5]
        return False[cite: 5]
    fecha_str = dt_objeto.strftime("%Y-%m-%d")[cite: 5]
    dia_semana = dt_objeto.weekday()[cite: 5]
    hora_actual = dt_objeto.time()[cite: 5]

    if fecha_str in FERIADOS:[cite: 5]
        return False[cite: 5]
    
    if dia_semana in [0, 1, 2, 3, 4] and time(8, 0, 0) <= hora_actual <= time(17, 0, 0):[cite: 5]
        return True[cite: 5]
    return False[cite: 5]

def evaluar_sla_1ra_excel(row, threshold_1ra):
    dt_obj = row.get("created_at_dt")[cite: 5]
    if not evaluar_horario_estricto(dt_obj):[cite: 5]
        return "excluido por filtro"[cite: 5]
    
    min_1ra = row.get("primera_respuesta_min")[cite: 5]
    if pd.isna(min_1ra):[cite: 5]
        return "no cumple"[cite: 5]
    return "cumple" if min_1ra <= threshold_1ra else "no cumple"[cite: 5]

def evaluar_sla_gestion_excel(row, threshold_gest):
    dt_obj = row.get("created_at_dt")[cite: 5]
    if not evaluar_horario_estricto(dt_obj):[cite: 5]
        return "excluido por filtro"[cite: 5]
    
    etiquetas = str(row.get("etiquetas", "")).lower()[cite: 5]
    if "sin respuesta" in etiquetas:[cite: 5]
        return "excluido por filtro"[cite: 5]
    
    min_gest = row.get("tiempo_resolucion_minutos")[cite: 5]
    if pd.isna(min_gest):[cite: 5]
        return "sin cerrar"[cite: 5]
    return "cumple" if min_gest <= threshold_gest else "no cumple"[cite: 5]

def evaluar_sla_1ra(por_agente, horario, min_1ra, threshold):
    if por_agente == "excluido" or horario == "fuera de horario":[cite: 5]
        return "excluido"[cite: 5]
    if pd.isna(min_1ra):[cite: 5]
        return "no cumple"[cite: 5]
    return "cumple" if min_1ra <= threshold else "no cumple"[cite: 5]

def evaluar_sla_gestion(por_agente, horario, min_gest, threshold):
    if por_agente == "excluido" or horario == "fuera de horario":[cite: 5]
        return "excluido"[cite: 5]
    if pd.isna(min_gest):[cite: 5]
        return "sin cerrar"[cite: 5]
    return "cumple" if min_gest <= threshold else "no cumple"[cite: 5]

def calificacion_a_estrellas(x):
    if pd.isna(x) or str(x).strip() in ["", "None", "nan", "null"]:[cite: 5]
        return ""[cite: 5]
    try:
        val = int(float(x))[cite: 5]
        return "★" * val if val > 0 else ""[cite: 5]
    except:
        return ""[cite: 5]

def es_chat_cerrado(row):
    estado = str(row.get("estado", "")).strip().lower()[cite: 5]
    fecha_cierre = str(row.get("fecha_primer_cierre", row.get("fecha_cierre", ""))).strip().lower()[cite: 5]
    
    if estado in ["cerrado", "closed", "resolved", "resuelto", "snoozed"]:[cite: 5]
        return True[cite: 5]
    if fecha_cierre not in ["", "none", "nan", "nat", "null"]:[cite: 5]
        return True[cite: 5]
    return False[cite: 5]

def obtener_df_csat_valido(df_sub):
    if df_sub.empty:[cite: 5]
        return pd.DataFrame()[cite: 5]
    df_c = df_sub.copy()[cite: 5]
    
    df_c["rating_num"] = pd.to_numeric(df_c["rating"], errors="coerce")[cite: 5]
    
    df_csat = df_c[
        df_c["rating_num"].notna() &
        (df_c["rating_num"] >= 1) & (df_c["rating_num"] <= 5) &
        (df_c["canal"] != "Correo electrónico") &
        (df_c["agente_asignado"].fillna("").str.strip() != "") &
        (df_c["agente_asignado"] != "Sin asignar")
    ][cite: 5]
    return df_csat[cite: 5]

def calcular_csat(df_sub):
    df_valid = obtener_df_csat_valido(df_sub)[cite: 5]
    if df_valid.empty:[cite: 5]
        return 0.0, 0[cite: 5]
    ratings = df_valid["rating_num"][cite: 5]
    positivas = len(ratings[ratings >= 4])[cite: 5]
    total = len(ratings)[cite: 5]
    return round((positivas / total) * 100, 1), total[cite: 5]

def tiempo_hace(dt_obj):
    if not isinstance(dt_obj, datetime) or pd.isna(dt_obj):[cite: 5]
        return "Desconocido"[cite: 5]
    now_local = pd.Timestamp.now(tz="America/Asuncion").replace(tzinfo=None)[cite: 5]
    dt_clean = dt_obj.replace(tzinfo=None)[cite: 5]
    diff = now_local - dt_clean[cite: 5]
    secs = int(diff.total_seconds())[cite: 5]
    if secs < 60:[cite: 5]
        return f"Hace {secs} seg"[cite: 5]
    elif secs < 3600:[cite: 5]
        return f"Hace {secs // 60} min"[cite: 5]
    elif secs < 86400:[cite: 5]
        return f"Hace {secs // 3600} hora(s)"[cite: 5]
    else:
        return f"Hace {secs // 86400} dia(s)"[cite: 5]

# ==========================
# ESTADO DE SESIÓN Y PARÁMETROS
# ==========================
if "auto_refresh" not in st.session_state:[cite: 5]
    st.session_state["auto_refresh"] = True[cite: 5]
if "refresh_interval" not in st.session_state:[cite: 5]
    st.session_state["refresh_interval"] = 5[cite: 5]
if "sla_1ra_th" not in st.session_state:[cite: 5]
    st.session_state["sla_1ra_th"] = 1.5[cite: 5]
if "sla_gest_th" not in st.session_state:[cite: 5]
    st.session_state["sla_gest_th"] = 60.0[cite: 5]
if "alerta_nuevo_th" not in st.session_state:[cite: 5]
    st.session_state["alerta_nuevo_th"] = 1.0[cite: 5]
if "admin_authenticated" not in st.session_state:[cite: 5]
    st.session_state["admin_authenticated"] = False[cite: 5]

hoy_local = obtener_fecha_local_hoy()[cite: 5]
if "input_f_desde" not in st.session_state:[cite: 5]
    st.session_state["input_f_desde"] = hoy_local[cite: 5]
if "input_f_hasta" not in st.session_state:[cite: 5]
    st.session_state["input_f_hasta"] = hoy_local[cite: 5]

# ==========================
# SIDEBAR / ESTADO & FILTROS DINÁMICOS
# ==========================
df_all_init = obtener_datos_supabase()[cite: 5]

if not df_all_init.empty and "created_at_dt" in df_all_init.columns:[cite: 5]
    min_created_dt = df_all_init["created_at_dt"].min()[cite: 5]
    max_updated_dt = df_all_init["updated_at_local"].max() if "updated_at_local" in df_all_init.columns else min_created_dt[cite: 5]
    min_created_str = min_created_dt.strftime('%d/%m/%Y') if pd.notna(min_created_dt) else "N/A"[cite: 5]
    
    st.sidebar.markdown(f"""
    <div class="db-info-box">
        <b>Estado Base de Datos:</b><br>
        • <b>Ultima sincronizacion:</b> {tiempo_hace(max_updated_dt)}<br>
        • <b>Registros desde:</b> {min_created_str}
    </div>
    """, unsafe_allow_html=True)[cite: 5]

st.sidebar.markdown("### Filtros de Consulta")[cite: 5]

usar_filtro_hora = st.sidebar.checkbox("Restringir Franja Horaria", value=False)[cite: 5]

with st.sidebar.form("form_filtros"):[cite: 5]
    st.caption("Rango de Fechas")[cite: 5]
    f_col1, f_col2 = st.columns(2)[cite: 5]
    fecha_desde = f_col1.date_input("Desde", key="input_f_desde")[cite: 5]
    fecha_hasta = f_col2.date_input("Hasta", key="input_f_hasta")[cite: 5]

    if usar_filtro_hora:[cite: 5]
        st.caption("Franja Horaria")[cite: 5]
        h_col1, h_col2 = st.columns(2)[cite: 5]
        hora_inicio = h_col1.time_input("Inicio", time(8, 0))[cite: 5]
        hora_fin = h_col2.time_input("Fin", time(18, 0))[cite: 5]
    else:
        hora_inicio, hora_fin = time(8, 0), time(18, 0)[cite: 5]

    act_sonido = st.checkbox("Alertas Sonoras", value=True)[cite: 5]

    btn_aplicar = st.form_submit_button("Aplicar Filtros", use_container_width=True)[cite: 5]

if st.sidebar.button("Establecer Fecha de Hoy", use_container_width=True):[cite: 5]
    st.session_state["input_f_desde"] = obtener_fecha_local_hoy()[cite: 5]
    st.session_state["input_f_hasta"] = obtener_fecha_local_hoy()[cite: 5]
    st.rerun()[cite: 5]

st.session_state["f_desde_key"] = fecha_desde[cite: 5]
st.session_state["f_hasta_key"] = fecha_hasta[cite: 5]

# Exportador a Excel
def generar_excel_reporte(df_exp, f_desde_val, f_hasta_val, usar_hora, h_ini, h_fin):
    output = io.BytesIO()[cite: 5]
    horario_texto = f"De {h_ini.strftime('%H:%M')} a {h_fin.strftime('%H:%M')} hs" if usar_hora else "Todo el dia (Sin restriccion)"[cite: 5]

    sla_1ra_threshold = st.session_state.get("sla_1ra_th", 1.5)[cite: 5]
    sla_gest_threshold = st.session_state.get("sla_gest_th", 60.0)[cite: 5]

    df_reporte = pd.DataFrame()[cite: 5]
    df_reporte["Conversacion ID"] = df_exp.get("id_str", "")[cite: 5]
    df_reporte["Fecha creacion"] = df_exp.get("created_at_fmt", "")[cite: 5]
    df_reporte["Agente asignado"] = df_exp.get("agente_asignado", "")[cite: 5]
    df_reporte["Tenant"] = df_exp.get("tenant", "Sin datos")[cite: 5]
    df_reporte["Company"] = df_exp.get("company", "Sin datos")[cite: 5]
    df_reporte["Nombre Contacto"] = df_exp.get("nombre_contacto", "Sin nombre")[cite: 5]
    df_reporte["Por Agente"] = df_exp.get("por_agente", "")[cite: 5]
    df_reporte["Primera respuesta (min)"] = df_exp.get("primera_respuesta_min", None)[cite: 5]
    
    df_reporte["SLA 1a Resp"] = df_exp.apply(lambda r: evaluar_sla_1ra_excel(r, sla_1ra_threshold), axis=1)[cite: 5]
    
    if "rating" in df_exp:[cite: 5]
        df_reporte["Calificacion"] = df_exp["rating"].apply(calificacion_a_estrellas)[cite: 5]
    else:
        df_reporte["Calificacion"] = ""[cite: 5]
        
    df_reporte["Feedback"] = df_exp.get("feedback", "")[cite: 5]
    df_reporte["Agente evaluado"] = df_exp.get("agente_evaluado", "")[cite: 5]
    df_reporte["CX Score explanation"] = df_exp.get("cx_score_explanation", "")[cite: 5]
    df_reporte["Fecha cierre (Primer Cierre)"] = df_exp.get("fecha_cierre_fmt", "")[cite: 5]

    df_reporte["Etiquetas"] = df_exp.get("etiquetas", "")[cite: 5]
    df_reporte["Modulo"] = df_exp.get("modulo", "")[cite: 5]
    df_reporte["Cliente"] = df_exp.get("cliente", "")[cite: 5]
    df_reporte["Tipo de contacto"] = df_exp.get("tipo_contacto", "")[cite: 5]
    df_reporte["Nivel"] = df_exp.get("nivel", "")[cite: 5]
    df_reporte["Motivo Normalizado"] = df_exp.get("motivo_normalizado", "Consulta General")[cite: 5]
    df_reporte["Resumen IA"] = df_exp.get("resumen_ia", "Sin resumen")[cite: 5]
    df_reporte["Tiempo resolucion (horas)"] = df_exp.get("tiempo_resolucion_horas", None)[cite: 5]
    df_reporte["Tiempo resolucion (min)"] = df_exp.get("tiempo_resolucion_minutos", None)[cite: 5]
    
    df_reporte["SLA Tiempo Gestion"] = df_exp.apply(lambda r: evaluar_sla_gestion_excel(r, sla_gest_threshold), axis=1)[cite: 5]

    with pd.ExcelWriter(output, engine="openpyxl") as writer:[cite: 5]
        df_meta = pd.DataFrame([
            ["REPORTE OPERATIVO DE CONVERSACIONES INTERCOM", ""],
            ["Rango de Fechas Consultado:", f"Desde {f_desde_val} hasta {f_hasta_val}"],
            ["Franja Horaria Aplicada:", horario_texto],
            ["Fecha de Generacion:", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
            ["", ""]
        ])[cite: 5]
        df_meta.to_excel(writer, index=False, header=False, sheet_name="Detalle", startrow=0)[cite: 5]
        df_reporte.to_excel(writer, index=False, sheet_name="Detalle", startrow=6)[cite: 5]
        
        ws = writer.sheets["Detalle"][cite: 5]
        for i, col in enumerate(df_reporte.columns, 1):[cite: 5]
            ws.column_dimensions[get_column_letter(i)].width = 24[cite: 5]

    output.seek(0)[cite: 5]
    return output[cite: 5]

# ==========================================
# BARRA SUPERIOR CON BOTÓN DE TOGGLE SIDEBAR
# ==========================================
col_btn, col_title = st.columns([2, 15], vertical_alignment="center")

with col_btn:
    btn_icon = "◀ Filtros" if st.session_state["sidebar_state"] == "expanded" else "▶ Filtros"
    st.button(btn_icon, on_click=toggle_sidebar, use_container_width=True)

with col_title:
    st.title("Dashboard Soporte BIMS")

tab_operativo, tab_resumen, tab_admin = st.tabs([
    "Control Operativo & SLA", 
    "Resumen de Chats & Agentes", 
    "Administracion & Configuracion"
])[cite: 5]

# =========================================================
# FRAGMENTO SIN PARPADEO PARA LA PESTAÑA DE CONTROL OPERATIVO
# =========================================================

refresh_sec = timedelta(seconds=st.session_state["refresh_interval"]) if st.session_state["auto_refresh"] else None[cite: 5]

@st.fragment(run_every=refresh_sec)
def renderizar_control_operativo():
    df_all = obtener_datos_supabase()[cite: 5]
    sla_1ra_th = st.session_state["sla_1ra_th"][cite: 5]
    sla_gest_th = st.session_state["sla_gest_th"][cite: 5]
    alerta_nuevo_th = st.session_state["alerta_nuevo_th"][cite: 5]

    if not df_all.empty:[cite: 5]
        df_all["horario_evaluado"] = df_all["created_at_dt"].apply(evaluar_horario_dashboard)[cite: 5]
        df_all["es_cerrado"] = df_all.apply(es_chat_cerrado, axis=1)[cite: 5]

        df_all["sla_1ra_eval"] = df_all.apply(
            lambda r: evaluar_sla_1ra(r.get("por_agente"), r.get("horario_evaluado"), r.get("primera_respuesta_min"), sla_1ra_th), axis=1
        )[cite: 5]
        df_all["sla_gest_eval"] = df_all.apply(
            lambda r: evaluar_sla_gestion(r.get("por_agente"), r.get("horario_evaluado"), r.get("tiempo_resolucion_minutos"), sla_gest_th), axis=1
        )[cite: 5]

        for col in ["tenant", "company", "nombre_contacto", "motivo_normalizado", "resumen_ia"]:[cite: 5]
            if col not in df_all.columns:[cite: 5]
                df_all[col] = "Sin datos" if col not in ["motivo_normalizado", "resumen_ia"] else ("Consulta General" if col == "motivo_normalizado" else "Pendiente de procesamiento")[cite: 5]

    f_desde_v, f_hasta_v = pd.to_datetime(fecha_desde).date(), pd.to_datetime(fecha_hasta).date()[cite: 5]
    df_filtered = df_all[(df_all["fecha_solo"] >= f_desde_v) & (df_all["fecha_solo"] <= f_hasta_v)].copy() if not df_all.empty else pd.DataFrame()[cite: 5]

    if usar_filtro_hora and not df_filtered.empty:[cite: 5]
        df_filtered = df_filtered[(df_filtered["hora_solo"] >= hora_inicio) & (df_filtered["hora_solo"] <= hora_fin)][cite: 5]

    now_dt = pd.Timestamp.now(tz="America/Asuncion")[cite: 5]
    
    df_abiertos_all = df_all[~df_all["es_cerrado"]].copy() if not df_all.empty else pd.DataFrame()[cite: 5]
    if not df_abiertos_all.empty:[cite: 5]
        df_abiertos_all = df_abiertos_all.drop_duplicates(subset=["id"])[cite: 5]

    if not df_abiertos_all.empty:[cite: 5]
        df_abiertos_all["min_transcurridos"] = ((now_dt - df_abiertos_all["created_at_dt"]).dt.total_seconds() / 60).round(1)[cite: 5]
        
        df_criticos_sla = df_abiertos_all[
            (df_abiertos_all["primera_respuesta_min"].isna()) & 
            (df_abiertos_all["min_transcurridos"] >= alerta_nuevo_th)
        ][cite: 5]

        if not df_criticos_sla.empty:[cite: 5]
            st.markdown(f"""
            <div class="alert-card-critical">
                <b>ALERTA CRITICA DE SLA EN VIVO</b><br>
                Hay <b>{len(df_criticos_sla)} chat(s) en espera</b> sin respuesta superando el limite configurado ({alerta_nuevo_th} min).
            </div>
            """, unsafe_allow_html=True)[cite: 5]

            if act_sonido:[cite: 5]
                st.components.v1.html(AUDIO_ALARM_HTML, height=0)[cite: 5]

    # CSAT SCORECARD
    st.markdown("### CSAT Performance")[cite: 5]
    now_date = obtener_fecha_local_hoy()[cite: 5]

    c_hoy, k_hoy = calcular_csat(df_all[df_all["fecha_solo"] == now_date]) if not df_all.empty else (0.0, 0)[cite: 5]
    c_ayer, _ = calcular_csat(df_all[df_all["fecha_solo"] == (now_date - timedelta(days=1))]) if not df_all.empty else (0.0, 0)[cite: 5]
    diff_hoy = round(c_hoy - c_ayer, 1)[cite: 5]

    inicio_sem = now_date - timedelta(days=now_date.weekday())[cite: 5]
    c_sem, k_sem = calcular_csat(df_all[(df_all["fecha_solo"] >= inicio_sem) & (df_all["fecha_solo"] <= now_date)]) if not df_all.empty else (0.0, 0)[cite: 5]
    ini_sem_ant = inicio_sem - timedelta(days=7)[cite: 5]
    fin_sem_ant = inicio_sem - timedelta(days=1)[cite: 5]
    c_sem_ant, _ = calcular_csat(df_all[(df_all["fecha_solo"] >= ini_sem_ant) & (df_all["fecha_solo"] <= fin_sem_ant)]) if not df_all.empty else (0.0, 0)[cite: 5]
    diff_sem = round(c_sem - c_sem_ant, 1)[cite: 5]

    inicio_mes = now_date.replace(day=1)[cite: 5]
    c_mes, k_mes = calcular_csat(df_all[(df_all["fecha_solo"] >= inicio_mes) & (df_all["fecha_solo"] <= now_date)]) if not df_all.empty else (0.0, 0)[cite: 5]
    fin_mes_ant = inicio_mes - timedelta(days=1)[cite: 5]
    ini_mes_ant = fin_mes_ant.replace(day=1)[cite: 5]
    c_mes_ant, _ = calcular_csat(df_all[(df_all["fecha_solo"] >= ini_mes_ant) & (df_all["fecha_solo"] <= fin_mes_ant)]) if not df_all.empty else (0.0, 0)[cite: 5]
    diff_mes = round(c_mes - c_mes_ant, 1)[cite: 5]

    q_act = (now_date.month - 1) // 3 + 1[cite: 5]
    ini_q = datetime(now_date.year, 3 * (q_act - 1) + 1, 1).date()[cite: 5]
    c_q, k_q = calcular_csat(df_all[(df_all["fecha_solo"] >= ini_q) & (df_all["fecha_solo"] <= now_date)]) if not df_all.empty else (0.0, 0)[cite: 5]
    fin_q_ant = ini_q - timedelta(days=1)[cite: 5]
    q_ant = (fin_q_ant.month - 1) // 3 + 1[cite: 5]
    ini_q_ant = datetime(fin_q_ant.year, 3 * (q_ant - 1) + 1, 1).date()[cite: 5]
    c_q_ant, _ = calcular_csat(df_all[(df_all["fecha_solo"] >= ini_q_ant) & (df_all["fecha_solo"] <= fin_q_ant)]) if not df_all.empty else (0.0, 0)[cite: 5]
    diff_q = round(c_q - c_q_ant, 1)[cite: 5]

    c_rango, k_rango = calcular_csat(df_filtered) if not df_filtered.empty else (0.0, 0)[cite: 5]
    duracion_dias = (f_hasta_v - f_desde_v).days + 1[cite: 5]
    f_hasta_prev = f_desde_v - timedelta(days=1)[cite: 5]
    f_desde_prev = f_hasta_prev - timedelta(days=duracion_dias - 1)[cite: 5]
    df_prev_rango = df_all[(df_all["fecha_solo"] >= f_desde_prev) & (df_all["fecha_solo"] <= f_hasta_prev)] if not df_all.empty else pd.DataFrame()[cite: 5]
    c_rango_prev, _ = calcular_csat(df_prev_rango)[cite: 5]
    diff_rango = round(c_rango - c_rango_prev, 1)[cite: 5]

    def render_metric_card(title, value, diff, sub_text):
        diff_color = "#34d399" if diff >= 0 else "#f43f5e"[cite: 5]
        diff_symbol = "▲" if diff >= 0 else "▼"[cite: 5]
        return f"""
        <div class="metric-card">
            <div class="metric-card-title">{title}</div>
            <div class="metric-card-value">{value}</div>
            <div style="color: {diff_color}; font-size: 0.8rem; font-weight: 600; margin-top: 2px;">
                {diff_symbol} {abs(diff)}% vs anterior
            </div>
            <div class="metric-card-sub">{sub_text}</div>
        </div>
        """[cite: 5]

    m1, m2, m3, m4, m5 = st.columns(5)[cite: 5]
    m1.markdown(render_metric_card("CSAT Hoy", f"{c_hoy}%", diff_hoy, f"{k_hoy} encuestas"), unsafe_allow_html=True)[cite: 5]
    m2.markdown(render_metric_card("CSAT Esta Semana", f"{c_sem}%", diff_sem, f"{k_sem} encuestas"), unsafe_allow_html=True)[cite: 5]
    m3.markdown(render_metric_card("CSAT Este Mes", f"{c_mes}%", diff_mes, f"{k_mes} encuestas"), unsafe_allow_html=True)[cite: 5]
    m4.markdown(render_metric_card(f"CSAT Trimestre Q{q_act}", f"{c_q}%", diff_q, f"{k_q} encuestas"), unsafe_allow_html=True)[cite: 5]
    m5.markdown(render_metric_card("CSAT Rango", f"{c_rango}%", diff_rango, f"{k_rango} encuestas"), unsafe_allow_html=True)[cite: 5]

    st.markdown("<br>", unsafe_allow_html=True)[cite: 5]

    # EVOLUCIÓN HISTÓRICA DE CSAT
    with st.expander("Ver Grafico de Evolucion del CSAT (Ultimos 6 Meses)", expanded=False):[cite: 5]
        if not df_all.empty:[cite: 5]
            fecha_6m_atras = (pd.Timestamp.now(tz="America/Asuncion") - timedelta(days=180)).date()[cite: 5]
            df_6m = df_all[df_all["fecha_solo"] >= fecha_6m_atras].copy()[cite: 5]
            df_csat_6m = obtener_df_csat_valido(df_6m)[cite: 5]

            if not df_csat_6m.empty:[cite: 5]
                df_csat_6m["Periodo_Sort"] = df_csat_6m["created_at_dt"].dt.to_period("M")[cite: 5]
                df_csat_6m["Mes_Nombre"] = df_csat_6m["created_at_dt"].dt.strftime("%b %Y").fillna("")[cite: 5]

                res_csat_mensual = [][cite: 5]
                for period, grp in df_csat_6m.groupby("Periodo_Sort"):[cite: 5]
                    tot = len(grp)[cite: 5]
                    pos = len(grp[grp["rating_num"] >= 4])[cite: 5]
                    val_csat = round((pos / tot) * 100, 1)[cite: 5]
                    res_csat_mensual.append({
                        "Periodo_Sort": period,
                        "Mes": grp["Mes_Nombre"].iloc[0],
                        "CSAT %": val_csat,
                        "Encuestas": tot
                    })[cite: 5]

                df_evo_csat = pd.DataFrame(res_csat_mensual).sort_values("Periodo_Sort")[cite: 5]

                fig_csat = go.Figure()[cite: 5]

                fig_csat.add_trace(go.Scatter(
                    x=df_evo_csat["Mes"],
                    y=df_evo_csat["CSAT %"],
                    mode="lines+markers+text",
                    name="CSAT (%)",
                    text=[f"<b>{v}%</b><br>({n} enc.)" for v, n in zip(df_evo_csat["CSAT %"], df_evo_csat["Encuestas"])],
                    textposition="top center",
                    line=dict(color="#38bdf8", width=3, shape="spline"),
                    marker=dict(size=8, color="#0284c7", symbol="circle", line=dict(color="#ffffff", width=1.5)),
                    fill="tozeroy",
                    fillcolor="rgba(56, 189, 248, 0.08)"
                ))[cite: 5]

                fig_csat.add_shape(
                    type="line",
                    x0=0, x1=1, xref="paper",
                    y0=90, y1=90, yref="y",
                    line=dict(color="#34d399", width=2, dash="dash")
                )[cite: 5]

                fig_csat.add_annotation(
                    x=1, y=90, xref="paper", yref="y",
                    text="<b>Meta Objetivo (90%)</b>",
                    showarrow=False,
                    yshift=12,
                    font=dict(color="#34d399", size=12)
                )[cite: 5]

                fig_csat.update_layout(
                    title="<b>Evolucion Mensual de Satisfaccion al Cliente (CSAT)</b>",
                    xaxis_title="Mes",
                    yaxis_title="Satisfaccion Positiva (%)",
                    yaxis=dict(range=[0, 105], gridcolor="#334155"),
                    xaxis=dict(gridcolor="#334155"),
                    paper_bgcolor="#1e293b",
                    plot_bgcolor="#1e293b",
                    font=dict(color="#f8fafc"),
                    margin=dict(t=50, b=40, l=40, r=40),
                    height=380
                )[cite: 5]

                st.plotly_chart(fig_csat, use_container_width=True)[cite: 5]
            else:
                st.info("No hay suficientes encuestas validadas en los ultimos 6 meses para generar el grafico.")[cite: 5]
        else:
            st.info("Sin registros en la base de datos.")[cite: 5]

    # DETALLE DE CSAT
    if not df_filtered.empty:[cite: 5]
        df_csat_det = obtener_df_csat_valido(df_filtered)[cite: 5]
        if not df_csat_det.empty:[cite: 5]
            with st.expander(f"Ver Detalle de Calificaciones CSAT ({len(df_csat_det)} Encuestas Validadas)", expanded=False):[cite: 5]
                df_csat_det["Calificacion"] = df_csat_det["rating_num"].apply(calificacion_a_estrellas)[cite: 5]
                df_csat_det = df_csat_det.sort_values(by=["rating_num", "created_at_dt"], ascending=[True, False])[cite: 5]

                st.dataframe(
                    df_csat_det[[
                        "intercom_url", "created_at_fmt", "Calificacion", "feedback", 
                        "nombre_contacto", "tenant", "company", "agente_evaluado", "cx_score_explanation"
                    ]],
                    column_config={
                        "intercom_url": st.column_config.LinkColumn("ID Chat", display_text=r".*/(\d+)"),
                        "created_at_fmt": "Fecha/Hora Creacion",
                        "Calificacion": "Puntaje",
                        "feedback": "Comentario / Feedback",
                        "nombre_contacto": "Contacto",
                        "tenant": "Tenant",
                        "company": "Company",
                        "agente_evaluado": "Agente Evaluado",
                        "cx_score_explanation": "Explicacion CX"
                    },
                    hide_index=True,
                    use_container_width=True
                )[cite: 5]

    st.markdown("---")[cite: 5]

    # METRICAS POR AGENTE EN DASHBOARD
    st.markdown("### Metricas por Agente")[cite: 5]
    if not df_filtered.empty:[cite: 5]
        v_df = df_filtered[(df_filtered["por_agente"] == "no excluido") & (df_filtered["horario_evaluado"] != "fuera de horario")][cite: 5]
        
        p_1r_series = pd.to_numeric(v_df["primera_respuesta_min"], errors="coerce")[cite: 5]
        p_gest_series = pd.to_numeric(v_df["tiempo_resolucion_minutos"], errors="coerce")[cite: 5]

        p_1r = round(p_1r_series.mean(), 2) if not p_1r_series.dropna().empty else 0[cite: 5]
        p_gest = round(p_gest_series.mean(), 2) if not p_gest_series.dropna().empty else 0[cite: 5]

        df_cerrados = df_filtered[df_filtered["es_cerrado"]][cite: 5]

        k1, k2, k3, k4 = st.columns(4)[cite: 5]
        k1.markdown(f'<div class="metric-card"><div class="metric-card-title">Prom. 1a Respuesta</div><div class="metric-card-value">{p_1r} min</div></div>', unsafe_allow_html=True)[cite: 5]
        k2.markdown(f'<div class="metric-card"><div class="metric-card-title">Prom. Tiempo Gestion</div><div class="metric-card-value">{p_gest} min</div></div>', unsafe_allow_html=True)[cite: 5]
        k3.markdown(f'<div class="metric-card"><div class="metric-card-title">Total Chats Consultados</div><div class="metric-card-value">{len(df_filtered)}</div></div>', unsafe_allow_html=True)[cite: 5]
        k4.markdown(f'<div class="metric-card"><div class="metric-card-title">Total Chats Cerrados</div><div class="metric-card-value">{len(df_cerrados)}</div></div>', unsafe_allow_html=True)[cite: 5]

        st.markdown("<br>", unsafe_allow_html=True)[cite: 5]

        res_agentes = [][cite: 5]
        for agente, grp in df_filtered.groupby("agente_asignado"):[cite: 5]
            v_g = grp[(grp["por_agente"] == "no excluido") & (grp["horario_evaluado"] != "fuera de horario")][cite: 5]
            asig = len(grp)[cite: 5]
            cerr = len(grp[grp["es_cerrado"]])[cite: 5]
            
            p_1_s = pd.to_numeric(v_g["primera_respuesta_min"], errors="coerce")[cite: 5]
            p_1 = round(p_1_s.mean(), 2) if not p_1_s.dropna().empty else 0[cite: 5]
            
            v_g_1ra = v_g[p_1_s.notna()][cite: 5]
            if not v_g_1ra.empty:[cite: 5]
                cumplen_1ra = len(v_g_1ra[pd.to_numeric(v_g_1ra["primera_respuesta_min"], errors="coerce") <= sla_1ra_th])[cite: 5]
                sla_1 = round((cumplen_1ra / len(v_g_1ra)) * 100, 1)[cite: 5]
            else:
                sla_1 = 0.0[cite: 5]

            v_g_gest = v_g[pd.to_numeric(v_g["tiempo_resolucion_minutos"], errors="coerce").notna()][cite: 5]
            if not v_g_gest.empty:[cite: 5]
                cumplen_gest = len(v_g_gest[pd.to_numeric(v_g_gest["tiempo_resolucion_minutos"], errors="coerce") <= sla_gest_th])[cite: 5]
                sla_g = round((cumplen_gest / len(v_g_gest)) * 100, 1)[cite: 5]
            else:
                sla_g = 0.0[cite: 5]

            res_agentes.append({
                "Agente": agente, 
                "Asignados": asig, 
                "Cerrados": cerr,
                "Prom. 1a Resp (min)": p_1, 
                f"% SLA 1a Resp (<= {sla_1ra_th}m)": f"{sla_1}%", 
                f"% SLA Gestion (<= {sla_gest_th}m)": f"{sla_g}%"
            })[cite: 5]
        
        st.dataframe(pd.DataFrame(res_agentes), use_container_width=True)[cite: 5]

    st.markdown("---")[cite: 5]

    # RANKING DE CHATS ABIERTOS FILTRADO POR FECHA DE CONSULTA
    if f_desde_v == f_hasta_v:[cite: 5]
        texto_rango_abiertos = f"del dia {f_desde_v}"[cite: 5]
    else:
        texto_rango_abiertos = f"del periodo {f_desde_v} al {f_hasta_v}"[cite: 5]

    df_abiertos_filtrados = df_filtered[~df_filtered["es_cerrado"]].copy() if not df_filtered.empty else pd.DataFrame()[cite: 5]
    cant_abiertos_filtrados = len(df_abiertos_filtrados.drop_duplicates(subset=["id"])) if not df_abiertos_filtrados.empty else 0[cite: 5]

    st.markdown(f"### Ranking de Chats Abiertos ({texto_rango_abiertos}) — {cant_abiertos_filtrados} chats")[cite: 5]
    
    if not df_abiertos_filtrados.empty:[cite: 5]
        df_abiertos_filtrados = df_abiertos_filtrados.drop_duplicates(subset=["id"])[cite: 5]
        df_abiertos_filtrados["min_transcurridos"] = ((now_dt - df_abiertos_filtrados["created_at_dt"]).dt.total_seconds() / 60).round(1)[cite: 5]
        df_abiertos_filtrados["Horas Transcurridas"] = (df_abiertos_filtrados["min_transcurridos"] / 60).round(1)[cite: 5]
        df_abiertos_filtrados = df_abiertos_filtrados.sort_values(by="created_at_dt", ascending=True)[cite: 5]

        cols_mostrar_filt = ["intercom_url", "created_at_fmt", "agente_asignado", "Horas Transcurridas", 
                             "nombre_contacto", "tenant", "company"][cite: 5]
        if "resumen_ia" in df_abiertos_filtrados.columns:[cite: 5]
            cols_mostrar_filt.append("resumen_ia")[cite: 5]

        st.dataframe(
            df_abiertos_filtrados[cols_mostrar_filt],
            column_config={
                "intercom_url": st.column_config.LinkColumn("ID Conversacion", display_text=r".*/(\d+)"),
                "created_at_fmt": "Fecha Creacion", 
                "agente_asignado": "Agente Asignado",
                "Horas Transcurridas": "Horas Abierto",
                "nombre_contacto": "Contacto",
                "tenant": "Tenant",
                "company": "Company",
                "resumen_ia": "Resumen IA"
            },
            hide_index=True, use_container_width=True, key="tabla_ranking_abiertos_filtrados"
        )[cite: 5]
    else:
        st.info(f"No hay chats abiertos pendientes creados en el rango {texto_rango_abiertos}.")[cite: 5]

    st.markdown("---")[cite: 5]

    # RANKING DE CHATS ABIERTOS GENERAL (TODOS LOS PENDIENTES HISTÓRICOS)
    cant_abiertos_gen = len(df_abiertos_all) if not df_abiertos_all.empty else 0[cite: 5]
    st.markdown(f"### Ranking General de Chats Abiertos (Historico Pendiente) — {cant_abiertos_gen} chats")[cite: 5]
    
    if not df_abiertos_all.empty:[cite: 5]
        df_rank = df_abiertos_all.copy()[cite: 5]
        df_rank["Horas Transcurridas"] = (df_rank["min_transcurridos"] / 60).round(1)[cite: 5]
        df_rank = df_rank.sort_values(by="created_at_dt", ascending=True)[cite: 5]

        cols_mostrar_gen = ["intercom_url", "created_at_fmt", "agente_asignado", "Horas Transcurridas", 
                            "nombre_contacto", "tenant", "company"][cite: 5]
        if "resumen_ia" in df_rank.columns:[cite: 5]
            cols_mostrar_gen.append("resumen_ia")[cite: 5]

        st.dataframe(
            df_rank[cols_mostrar_gen],
            column_config={
                "intercom_url": st.column_config.LinkColumn("ID Conversacion", display_text=r".*/(\d+)"),
                "created_at_fmt": "Fecha Creacion", 
                "agente_asignado": "Agente Asignado",
                "Horas Transcurridas": "Horas Abierto",
                "nombre_contacto": "Contacto",
                "tenant": "Tenant",
                "company": "Company",
                "resumen_ia": "Resumen IA"
            },
            hide_index=True, use_container_width=True, key="tabla_ranking_abiertos_unica"
        )[cite: 5]
    else:
        st.info("No hay chats abiertos pendientes en este momento.")[cite: 5]

    st.markdown("---")[cite: 5]

    # SECCIÓN DE BÚSQUEDA DINÁMICA POR TENANT O AGENTE
    st.markdown("### Buscador Especifico de Chats (Por Tenant / Agente)")[cite: 5]
    
    if not df_all.empty:[cite: 5]
        col_b1, col_b2 = st.columns(2)[cite: 5]
        
        tenants_unicos = sorted([str(x) for x in df_all["tenant"].dropna().unique() if str(x).strip() != ""])[cite: 5]
        agentes_unicos = sorted([str(x) for x in df_all["agente_asignado"].dropna().unique() if str(x).strip() != ""])[cite: 5]
        
        tenant_sel = col_b1.multiselect("Filtrar por Tenant(s):", options=tenants_unicos)[cite: 5]
        agente_sel = col_b2.multiselect("Filtrar por Agente(s):", options=agentes_unicos)[cite: 5]
        
        df_busqueda = df_all.copy()[cite: 5]
        
        if tenant_sel:[cite: 5]
            df_busqueda = df_busqueda[df_busqueda["tenant"].isin(tenant_sel)][cite: 5]
        if agente_sel:[cite: 5]
            df_busqueda = df_busqueda[df_busqueda["agente_asignado"].isin(agente_sel)][cite: 5]
            
        if tenant_sel or agente_sel:[cite: 5]
            st.markdown(f"#### Resultados de la Busqueda ({len(df_busqueda)} chats encontrados)")[cite: 5]
            if not df_busqueda.empty:[cite: 5]
                df_busqueda["Estado_Texto"] = df_busqueda["es_cerrado"].apply(lambda x: "Cerrado" if x else "Abierto")[cite: 5]
                df_busqueda = df_busqueda.sort_values(by="created_at_dt", ascending=False)[cite: 5]
                
                cols_search = ["intercom_url", "Estado_Texto", "created_at_fmt", "agente_asignado", 
                               "nombre_contacto", "tenant", "company"][cite: 5]
                if "resumen_ia" in df_busqueda.columns:[cite: 5]
                    cols_search.append("resumen_ia")[cite: 5]
                
                st.dataframe(
                    df_busqueda[cols_search],
                    column_config={
                        "intercom_url": st.column_config.LinkColumn("ID Conversacion", display_text=r".*/(\d+)"),
                        "Estado_Texto": "Estado",
                        "created_at_fmt": "Fecha Creacion",
                        "agente_asignado": "Agente Asignado",
                        "nombre_contacto": "Contacto",
                        "tenant": "Tenant",
                        "company": "Company",
                        "resumen_ia": "Resumen IA"
                    },
                    hide_index=True, use_container_width=True, key="tabla_busqueda_tenant_agente"
                )[cite: 5]
            else:
                st.info("No se encontraron registros que coincidan exactamente con la seleccion.")[cite: 5]
        else:
            st.caption("Selecciona al menos un Tenant o Agente arriba para desplegar los resultados.")[cite: 5]

# ==========================================
# RENDERIZADO DE PESTAÑAS
# ==========================================

with tab_operativo:[cite: 5]
    renderizar_control_operativo()[cite: 5]

with tab_resumen:[cite: 5]
    df_all_r = obtener_datos_supabase()[cite: 5]
    f_desde_v, f_hasta_v = pd.to_datetime(fecha_desde).date(), pd.to_datetime(fecha_hasta).date()[cite: 5]
    
    if not df_all_r.empty:[cite: 5]
        df_filtered_r = df_all_r[(df_all_r["fecha_solo"] >= f_desde_v) & (df_all_r["fecha_solo"] <= f_hasta_v)].copy()[cite: 5]
        
        if usar_filtro_hora and not df_filtered_r.empty:[cite: 5]
            df_filtered_r = df_filtered_r[(df_filtered_r["hora_solo"] >= hora_inicio) & (df_filtered_r["hora_solo"] <= hora_fin)][cite: 5]
    else:
        df_filtered_r = pd.DataFrame()[cite: 5]

    st.markdown(f"### Analisis de Chats por Agente (`{f_desde_v}` al `{f_hasta_v}`)")[cite: 5]
    
    if not df_filtered_r.empty:[cite: 5]
        df_res = df_filtered_r.copy()[cite: 5]
        df_res = df_res.sort_values(by="created_at_dt", ascending=True)[cite: 5]

        df_res["Dia"] = df_res["created_at_dt"].dt.strftime("%Y-%m-%d").fillna("Sin fecha")[cite: 5]

        df_agentes_total = df_res["agente_asignado"].value_counts().reset_index()[cite: 5]
        df_agentes_total.columns = ["Agente", "Cantidad de Chats"][cite: 5]

        total_chats_periodo = len(df_res)[cite: 5]
        num_dias = df_res["Dia"].nunique()[cite: 5]
        promedio_diario = round(total_chats_periodo / num_dias, 1) if num_dias > 0 else 0[cite: 5]
        top_agente = df_agentes_total.iloc[0]["Agente"] if not df_agentes_total.empty else "N/A"[cite: 5]
        top_agente_count = df_agentes_total.iloc[0]["Cantidad de Chats"] if not df_agentes_total.empty else 0[cite: 5]
        pct_top = round((top_agente_count / total_chats_periodo) * 100, 1) if total_chats_periodo > 0 else 0[cite: 5]

        r1, r2, r3, r4 = st.columns(4)[cite: 5]
        r1.markdown(f'<div class="metric-card"><div class="metric-card-title">Total Chats en Rango</div><div class="metric-card-value">{total_chats_periodo}</div></div>', unsafe_allow_html=True)[cite: 5]
        r2.markdown(f'<div class="metric-card"><div class="metric-card-title">Promedio Diario</div><div class="metric-card-value">{promedio_diario}</div></div>', unsafe_allow_html=True)[cite: 5]
        r3.markdown(f'<div class="metric-card"><div class="metric-card-title">Agente con Mas Chats</div><div class="metric-card-value" style="font-size:1.2rem;">{top_agente}</div><div class="metric-card-sub">{top_agente_count} chats</div></div>', unsafe_allow_html=True)[cite: 5]
        r4.markdown(f'<div class="metric-card"><div class="metric-card-title">Participacion Top Agente</div><div class="metric-card-value">{pct_top}%</div></div>', unsafe_allow_html=True)[cite: 5]

        st.markdown("<br>", unsafe_allow_html=True)[cite: 5]

        palette_e = ["#0284c7", "#6366f1", "#10b981", "#f59e0b", "#e11d48", "#8b5cf6", "#14b8a6"][cite: 5]

        g_pie, g_bar = st.columns([1, 1])[cite: 5]

        with g_pie:[cite: 5]
            st.markdown("#### Distribucion de Chats por Agente")[cite: 5]
            fig_pie = px.pie(
                df_agentes_total, 
                values="Cantidad de Chats", 
                names="Agente",
                hole=0.5,
                color_discrete_sequence=palette_e
            )[cite: 5]
            fig_pie.update_traces(textposition='inside', textinfo='percent+label', marker=dict(line=dict(color='#0f172a', width=1.5)))[cite: 5]
            fig_pie.update_layout(
                showlegend=True, 
                paper_bgcolor="#1e293b",
                plot_bgcolor="#1e293b",
                font=dict(color="#f8fafc"),
                margin=dict(t=30, b=30, l=30, r=30)
            )[cite: 5]
            st.plotly_chart(fig_pie, use_container_width=True)[cite: 5]

        with g_bar:[cite: 5]
            st.markdown("#### Evolucion Diaria por Agente")[cite: 5]
            df_dia_agente = df_res.groupby(["Dia", "agente_asignado"]).size().reset_index(name="Cantidad")[cite: 5]
            fig_bar = px.bar(
                df_dia_agente,
                x="Dia",
                y="Cantidad",
                color="agente_asignado",
                barmode="stack",
                title="Volumen de Chats por Dia",
                color_discrete_sequence=palette_e
            )[cite: 5]
            fig_bar.update_layout(
                paper_bgcolor="#1e293b",
                plot_bgcolor="#1e293b",
                font=dict(color="#f8fafc"),
                xaxis=dict(gridcolor="#334155"),
                yaxis=dict(gridcolor="#334155"),
                margin=dict(t=30, b=30, l=30, r=30)
            )[cite: 5]
            st.plotly_chart(fig_bar, use_container_width=True)[cite: 5]

        st.markdown("---")[cite: 5]

        st.markdown("#### Tabla Desglosada por Dia y Agente")[cite: 5]
        df_pivot = df_res.pivot_table(
            index="Dia", 
            columns="agente_asignado", 
            values="id", 
            aggfunc="count", 
            fill_value=0
        )[cite: 5]
        df_pivot["TOTAL CHATS"] = df_pivot.sum(axis=1)[cite: 5]
        st.dataframe(df_pivot, use_container_width=True)[cite: 5]
    else:
        st.info("No hay chats registrados para el rango de fechas seleccionado en la barra lateral.")[cite: 5]

with tab_admin:[cite: 5]
    st.markdown("### Panel de Administracion y Configuracion")[cite: 5]

    if not st.session_state["admin_authenticated"]:[cite: 5]
        
        col_pass1, col_pass2 = st.columns([2, 1])[cite: 5]
        with col_pass1:[cite: 5]
            with st.form("form_login_admin"):[cite: 5]
                input_pass = st.text_input("Contrasena de Administrador", type="password")[cite: 5]
                btn_login = st.form_submit_button("Acceder al Panel", use_container_width=True)[cite: 5]
                
                if btn_login:[cite: 5]
                    if input_pass == ADMIN_PASSWORD:[cite: 5]
                        st.session_state["admin_authenticated"] = True[cite: 5]
                        st.success("Acceso concedido.")[cite: 5]
                        st.rerun()[cite: 5]
                    else:
                        st.error("Contrasena incorrecta.")[cite: 5]
    else:
        st.success("Sesion de administracion activa.")[cite: 5]
        if st.button("Cerrar Sesion Admin"):[cite: 5]
            st.session_state["admin_authenticated"] = False[cite: 5]
            st.rerun()[cite: 5]

        st.markdown("<br>", unsafe_allow_html=True)[cite: 5]

        with st.container():[cite: 5]
            st.markdown("""
            <div class="admin-card">
                <h4 style="margin-top:0; color:#38bdf8;">ℹ️ Criterios de Cálculo de Tiempos y SLA</h4>
                <p style="color:#94a3b8; font-size:0.88rem; line-height:1.6; margin-bottom:0;">
                    <b>• Promedio en Pantalla (Dashboard):</b> Se calcula haciendo la media (<code>mean</code>) de los minutos de primera respuesta y gestión de chats válidos (<code>por_agente == 'no excluido'</code>) creados dentro de la jornada operativa (<code>horario_evaluado != 'fuera de horario'</code>).<br>
                    <b>• Evaluación de SLA en Excel:</b> Aplica la regla estricta de <b>Lunes a Viernes de 08:00 a 17:00 hs</b>. En la gestión, se descartan automáticamente los chats que contengan la etiqueta <i>"sin respuesta"</i> marcándolos como <i>"excluido por filtro"</i>.
                </p>
            </div>
            """, unsafe_allow_html=True)[cite: 5]

        st.markdown("<br>", unsafe_allow_html=True)[cite: 5]
        
        # Tarjeta 1: Sincronización
        with st.container():[cite: 5]
            st.markdown("""
            <div class="admin-card">
                <h4 style="margin-top:0; color:#38bdf8;">1. Forzar Sincronizacion Manual de Intercom</h4>
                <p style="color:#94a3b8; font-size:0.88rem; margin-bottom:15px;">Sincroniza directamente los registros desde Intercom a la base de datos de Supabase.</p>
            </div>
            """, unsafe_allow_html=True)[cite: 5]
            
            c_sync1, c_sync2 = st.columns([2, 1], vertical_alignment="bottom")[cite: 5]
            dias_a_sincronizar = c_sync1.number_input("Dias hacia atras a consultar:", min_value=1, max_value=365, value=2)[cite: 5]
            
            if c_sync2.button("Iniciar Sincronizacion Manual", use_container_width=True):[cite: 5]
                if SYNC_AVAILABLE:[cite: 5]
                    progress_bar = st.progress(0)[cite: 5]
                    status_text = st.empty()[cite: 5]
                    
                    try:
                        def callback_progreso(actual, total):[cite: 5]
                            porcentaje = int((actual / total) * 100) if total > 0 else 0[cite: 5]
                            progress_bar.progress(min(porcentaje, 100))[cite: 5]
                            status_text.text(f"Sincronizando {actual}/{total} conversaciones...")[cite: 5]

                        status_text.text("Iniciando conexion con Intercom API...")[cite: 5]
                        progress_bar.progress(10)[cite: 5]
                        
                        try:
                            sincronizar_intercom(dias=dias_a_sincronizar, progress_callback=callback_progreso)[cite: 5]
                        except TypeError:
                            status_text.text(f"Procesando conversaciones de los ultimos {dias_a_sincronizar} dias...")[cite: 5]
                            progress_bar.progress(50)[cite: 5]
                            sincronizar_intercom(dias=dias_a_sincronizar)[cite: 5]
                        
                        progress_bar.progress(100)[cite: 5]
                        status_text.text("Sincronizacion completada exitosamente!")[cite: 5]
                        st.cache_data.clear()[cite: 5]
                        st.success("La base de datos fue actualizada. Recargando la vista...")[cite: 5]
                        time_lib.sleep(1)[cite: 5]
                        st.rerun()[cite: 5]
                    except Exception as e:
                        status_text.text("Ocurrio un error durante la sincronizacion.")[cite: 5]
                        st.error(f"Detalle del error: {str(e)}")[cite: 5]
                else:
                    st.error("No se encontro el modulo `sync_intercom.py` en el proyecto.")[cite: 5]

        st.markdown("<br>", unsafe_allow_html=True)[cite: 5]

        # Tarjeta 2: Parámetros Globales
        with st.container():[cite: 5]
            st.markdown("""
            <div class="admin-card">
                <h4 style="margin-top:0; color:#38bdf8;">2. Parametros Globales del Dashboard</h4>
                <p style="color:#94a3b8; font-size:0.88rem; margin-bottom:15px;">Ajusta los tiempos de refresco en vivo, alertas y limites objetivo para los SLA de atencion.</p>
            </div>
            """, unsafe_allow_html=True)[cite: 5]
            
            col_cfg1, col_cfg2, col_cfg3 = st.columns(3)[cite: 5]
            
            with col_cfg1:[cite: 5]
                st.markdown("<b>Refresco Automatico</b>", unsafe_allow_html=True)[cite: 5]
                cfg_auto = st.checkbox("Activar Autorefresh por defecto", value=st.session_state["auto_refresh"])[cite: 5]
                cfg_interval = st.number_input("Intervalo predeterminado (segundos):", min_value=3, max_value=60, value=st.session_state["refresh_interval"])[cite: 5]
            
            with col_cfg2:[cite: 5]
                st.markdown("<b>Umbrales de SLA (Minutos)</b>", unsafe_allow_html=True)[cite: 5]
                cfg_sla_1ra = st.number_input("SLA Primera Respuesta (min):", min_value=0.5, max_value=30.0, value=float(st.session_state["sla_1ra_th"]), step=0.5)[cite: 5]
                cfg_sla_gest = st.number_input("SLA Tiempo de Gestion (min):", min_value=5.0, max_value=480.0, value=float(st.session_state["sla_gest_th"]), step=5.0)[cite: 5]

            with col_cfg3:[cite: 5]
                st.markdown("<b>Alerta de Chat Nuevo</b>", unsafe_allow_html=True)[cite: 5]
                cfg_alerta_nuevo = st.number_input("Disparar Alerta tras (min sin responder):", min_value=0.5, max_value=60.0, value=float(st.session_state["alerta_nuevo_th"]), step=0.5)[cite: 5]

            st.markdown("<br>", unsafe_allow_html=True)[cite: 5]
            if st.button("Guardar Configuracion de Parametros", use_container_width=True):[cite: 5]
                st.session_state["auto_refresh"] = cfg_auto[cite: 5]
                st.session_state["refresh_interval"] = cfg_interval[cite: 5]
                st.session_state["sla_1ra_th"] = cfg_sla_1ra[cite: 5]
                st.session_state["sla_gest_th"] = cfg_sla_gest[cite: 5]
                st.session_state["alerta_nuevo_th"] = cfg_alerta_nuevo[cite: 5]
                st.success("Configuracion actualizada correctamente.")[cite: 5]
                st.rerun()[cite: 5]

        st.markdown("<br>", unsafe_allow_html=True)[cite: 5]

        # Tarjeta 3: Descarga Masiva
        with st.container():[cite: 5]
            st.markdown("""
            <div class="admin-card">
                <h4 style="margin-top:0; color:#38bdf8;">3. Descarga Masiva de Reportes Excel</h4>
                <p style="color:#94a3b8; font-size:0.88rem; margin-bottom:15px;">Genera y descarga el archivo Excel completo con el formato formateado de los registros filtrados.</p>
            </div>
            """, unsafe_allow_html=True)[cite: 5]
            
            df_all_exp = obtener_datos_supabase()[cite: 5]
            if not df_all_exp.empty:[cite: 5]
                df_exp_filt = df_all_exp[(df_all_exp["fecha_solo"] >= pd.to_datetime(fecha_desde).date()) & (df_all_exp["fecha_solo"] <= pd.to_datetime(fecha_hasta).date())].copy()[cite: 5]
                if usar_filtro_hora and not df_exp_filt.empty:[cite: 5]
                    df_exp_filt = df_exp_filt[(df_exp_filt["hora_solo"] >= hora_inicio) & (df_exp_filt["hora_solo"] <= hora_fin)][cite: 5]
            else:
                df_exp_filt = pd.DataFrame()[cite: 5]

            if not df_exp_filt.empty:[cite: 5]
                st.download_button(
                    label="Descargar Reporte Filtrado en Excel",
                    data=generar_excel_reporte(df_exp_filt, fecha_desde, fecha_hasta, usar_filtro_hora, hora_inicio, hora_fin),
                    file_name=f"reporte_intercom_{fecha_desde}_a_{fecha_hasta}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )[cite: 5]
            else:
                st.info("No hay datos filtrados para descargar actualmente.")[cite: 5]
