import os
import io
import time as time_lib
import threading
from datetime import datetime, timedelta, time, date, timezone
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from openpyxl.utils import get_column_letter
from supabase import create_client, Client
import extra_streamlit_components as stx

# ==========================================
# 1. CONFIGURACIÓN ÚNICA DE PÁGINA
# ==========================================
st.set_page_config(
    page_title="Dashboard Soporte BIMS",
    page_icon="📈",
    layout="wide"
)

# Oculta exclusivamente el aviso flotante 'Running...' sin ocultar el header principal
st.markdown("""
<style>
    [data-testid="stStatusWidget"] {
        display: none !important;
        visibility: hidden !important;
        width: 0 !important;
        height: 0 !important;
    }

    [data-testid="stStatusWidget"] * {
        visibility: hidden !important;
    }
</style>
""", unsafe_allow_html=True)

try:
    from sync_intercom import sincronizar_intercom
    SYNC_AVAILABLE = True
except ImportError:
    SYNC_AVAILABLE = False

INTERCOM_APP_ID = "co9kozj6"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

# ==========================
# CONFIGURACIÓN DE SUPABASE
# ==========================
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

@st.cache_resource
def init_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_supabase()

# ==========================================
# AUTENTICACIÓN PERSISTENTE ROBUSTA (LOCALSTORAGE + QUERY PARAMS)
# ==========================================
if "user_authenticated" not in st.session_state:
    st.session_state["user_authenticated"] = False
if "user_email" not in st.session_state:
    st.session_state["user_email"] = ""

# 1. Recuperar token de sesión de la URL al recargar o reabrir
token_url = st.query_params.get("session_token", None)

if token_url and not st.session_state["user_authenticated"]:
    try:
        res = supabase.table("usuarios_autorizados")\
            .select("*")\
            .eq("email", str(token_url).strip().lower())\
            .eq("activo", True)\
            .execute()
        if len(res.data) > 0:
            st.session_state["user_authenticated"] = True
            st.session_state["user_email"] = res.data[0].get("email")
            st.session_state["user_name"] = res.data[0].get("nombre")
    except Exception:
        pass

# 2. Script JS de auto-restauración vía LocalStorage
st.components.v1.html("""
<script>
    const storedUser = localStorage.getItem('bims_user_session');
    const urlParams = new URLSearchParams(window.location.search);
    
    // Si hay usuario en localStorage pero la URL perdió el token al desconectarse, auto-restaurar
    if (storedUser && !urlParams.has('session_token')) {
        urlParams.set('session_token', storedUser);
        window.location.search = urlParams.toString();
    }
</script>
""", height=0)

# Estado Global de Sincronización libre de restricciones de st.session_state para Hilos
@st.cache_resource
def obtener_estado_sync_global():
    return {"status": "idle", "processed": 0, "log": "", "error": None}

GLOBAL_SYNC_STATE = obtener_estado_sync_global()

def verificar_credenciales_supabase(email_val, pass_val):
    """Consulta la tabla 'usuarios_autorizados' en Supabase para validar el ingreso."""
    try:
        res = supabase.table("usuarios_autorizados")\
            .select("*")\
            .eq("email", email_val.strip().lower())\
            .eq("password", pass_val.strip())\
            .eq("activo", True)\
            .execute()
        return len(res.data) > 0, res.data[0] if res.data else None
    except Exception:
        return False, None

# ==========================================
# PANTALLA DE LOGIN CENTRADA Y ACOTADA
# ==========================================
if not st.session_state["user_authenticated"]:
    st.markdown("""
    <style>
        .stApp { background-color: #0f172a; color: #f8fafc; }

        header[data-testid="stHeader"] {
            background-color: #0f172a !important;
        }

        .block-container {
            max-width: 520px !important;
            padding-top: 5rem !important;
            margin: 0 auto !important;
        }

        .login-card-header {
            background-color: #1e293b;
            border: 1px solid #334155;
            border-bottom: none;
            border-top-left-radius: 16px;
            border-top-right-radius: 16px;
            padding: 28px 24px 10px 24px;
            text-align: center;
        }

        .login-title {
            color: #38bdf8;
            font-size: 1.45rem;
            font-weight: 700;
            margin-bottom: 4px;
        }

        .login-subtitle {
            color: #94a3b8;
            font-size: 0.85rem;
        }

        div[data-testid="stForm"] {
            background-color: #1e293b !important;
            border: 1px solid #334155 !important;
            border-top: none !important;
            border-bottom-left-radius: 16px !important;
            border-bottom-right-radius: 16px !important;
            padding: 0 24px 28px 24px !important;
            box-shadow: 0 15px 25px -5px rgba(0, 0, 0, 0.4);
        }

        div[data-testid="stTextInput"] {
            width: 100% !important;
        }

        div[data-testid="stTextInput"] small, 
        div[data-testid="stInputInstructions"], 
        .st-emotion-cache-12w0q3e, 
        [data-testid="stFormSubmitButton"] + div {
            display: none !important;
            visibility: hidden !important;
        }

        div[data-testid="stTextInput"] input {
            background-color: #0f172a !important;
            color: #f8fafc !important;
            border: 1px solid #334155 !important;
            border-radius: 8px !important;
            padding: 10px 12px !important;
        }

        input::-ms-reveal,
        input::-ms-clear {
            display: none !important;
        }

        div[data-testid="stTextInput"] input:focus {
            border-color: #38bdf8 !important;
            box-shadow: 0 0 0 1px #38bdf8 !important;
        }

        .stButton>button { 
            background-color: #0284c7 !important; 
            color: white !important; 
            font-weight: bold !important; 
            border-radius: 8px !important; 
            border: none !important;
            height: 44px !important;
            margin-top: 8px !important;
        }

        .loading-badge {
            background-color: #0f172a;
            border: 1px solid #0284c7;
            border-radius: 8px;
            padding: 10px;
            color: #38bdf8;
            font-size: 0.88rem;
            font-weight: 600;
            text-align: center;
            margin-top: 10px;
        }
    </style>

    <div class="login-card-header">
        <div class="login-title">Dashboard Soporte BIMS</div>
        <div class="login-subtitle">Ingresa tus credenciales autorizadas para acceder.</div>
    </div>
    """, unsafe_allow_html=True)

    with st.form("form_login_global", clear_on_submit=False):
        input_user_email = st.text_input("Correo Electrónico")
        input_user_pass = st.text_input("Contraseña", type="password")
        
        status_box = st.empty()
        btn_login_user = st.form_submit_button("Iniciar Sesión", use_container_width=True)

        if btn_login_user:
            if not input_user_email.strip() or not input_user_pass.strip():
                status_box.warning("Por favor ingresa tu correo y contraseña.")
            else:
                with status_box.container():
                    st.markdown("""
                    <div class="loading-badge">
                        ⏳ Verificando credenciales...
                    </div>
                    """, unsafe_allow_html=True)
                    
                    time_lib.sleep(0.5)
                    valido, datos_user = verificar_credenciales_supabase(input_user_email, input_user_pass)
                    
                if valido:
                    email_user = datos_user.get("email")
                    st.session_state["user_authenticated"] = True
                    st.session_state["user_email"] = email_user
                    st.session_state["user_name"] = datos_user.get("nombre")
    
                    # Guardar en URL y LocalStorage simultáneamente
                    st.query_params["session_token"] = email_user
                    st.components.v1.html(f"""
                    <script>
                        localStorage.setItem('bims_user_session', '{email_user}');
                    </script>
                    """, height=0)
    
                    status_box.success("Acceso concedido.")
                    time_lib.sleep(0.3)
                    st.rerun()
                else:
                    status_box.error("Credenciales incorrectas o usuario no activo.")

    st.stop()
    
# ==========================================
# CÓDIGO PRINCIPAL DEL DASHBOARD
# ==========================================

def obtener_fecha_local_hoy():
    """Retorna la fecha actual exacta en Paraguay (UTC-3)."""
    tz_py = timezone(timedelta(hours=-3))
    return datetime.now(tz_py).date()

def convertir_a_minutos(val):
    """Garantiza la conversión limpia a minutos numéricos float."""
    if pd.isna(val) or val is None:
        return None
    try:
        v = float(val)
        return round(v, 2)
    except (ValueError, TypeError):
        return None

def obtener_tiempo_transcurrido(fecha_dt):
    """Calcula el tiempo transcurrido relativo desde la última actualización de Supabase."""
    if pd.isna(fecha_dt) or fecha_dt is None:
        return "Sin registros"
    now_local = pd.Timestamp.now(tz="America/Asuncion")
    if fecha_dt.tzinfo is None:
        fecha_dt = fecha_dt.tz_localize("America/Asuncion")
    else:
        fecha_dt = fecha_dt.tz_convert("America/Asuncion")
    
    diff = now_local - fecha_dt
    secs = int(diff.total_seconds())
    if secs < 60:
        return "hace un momento"
    elif secs < 3600:
        return f"hace {secs // 60} min"
    elif secs < 86400:
        return f"hace {secs // 3600} h"
    else:
        return f"hace {secs // 86400} días"

def procesar_fechas_df(df):
    """Convierte las fechas UTC a hora local (UTC-3) y normaliza métricas numéricas."""
    if df.empty or "created_at" not in df.columns:
        df["created_at_dt"] = pd.Series(dtype='datetime64[ns, America/Asuncion]')
        df["created_at_fmt"] = pd.Series(dtype='str')
        df["fecha_solo"] = pd.Series(dtype='object')
        df["hora_solo"] = pd.Series(dtype='object')
        df["fecha_calificacion_dt"] = pd.Series(dtype='datetime64[ns, America/Asuncion]')
        df["fecha_calificacion_fmt"] = pd.Series(dtype='str')
        df["fecha_calificacion_solo"] = pd.Series(dtype='object')
        df["updated_at_local"] = pd.Series(dtype='datetime64[ns, America/Asuncion]')
        df["es_cerrado"] = pd.Series(dtype='bool')
        df["por_agente"] = pd.Series(dtype='str')
        df["agente_asignado"] = pd.Series(dtype='str')
        df["tenant"] = pd.Series(dtype='str')
        df["company"] = pd.Series(dtype='str')
        df["nombre_contacto"] = pd.Series(dtype='str')
        df["motivo_normalizado"] = pd.Series(dtype='str')
        df["resumen_ia"] = pd.Series(dtype='str')
        return df
    
    created_dt = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
    local_dt = created_dt.dt.tz_convert("America/Asuncion")
    
    df["created_at_dt"] = local_dt
    df["created_at_fmt"] = local_dt.dt.strftime("%Y-%m-%d %H:%M").fillna("Sin fecha")
    df["fecha_solo"] = local_dt.dt.date
    df["hora_solo"] = local_dt.dt.time

    # Procesamiento de Marca de Tiempo de Puntuación CSAT (Respaldado en created_at)
    if "fecha_calificacion" in df.columns:
        calif_utc = pd.to_datetime(df["fecha_calificacion"], errors="coerce", utc=True)
        local_calif_dt = calif_utc.dt.tz_convert("America/Asuncion")
        df["fecha_calificacion_dt"] = local_calif_dt.fillna(df["created_at_dt"])
    else:
        df["fecha_calificacion_dt"] = df["created_at_dt"]

    df["fecha_calificacion_fmt"] = df["fecha_calificacion_dt"].dt.strftime("%Y-%m-%d %H:%M").fillna("Sin fecha")
    df["fecha_calificacion_solo"] = df["fecha_calificacion_dt"].dt.date

    col_cierre = "fecha_primer_cierre" if "fecha_primer_cierre" in df.columns else "fecha_cierre"
    if col_cierre in df.columns:
        cierre_dt = pd.to_datetime(df[col_cierre], errors="coerce", utc=True)
        local_cierre = cierre_dt.dt.tz_convert("America/Asuncion")
        df["fecha_cierre_fmt"] = local_cierre.dt.strftime("%Y-%m-%d %H:%M").fillna("")

    if "updated_at" in df.columns:
        updated_dt = pd.to_datetime(df["updated_at"], errors="coerce", utc=True)
        df["updated_at_local"] = updated_dt.dt.tz_convert("America/Asuncion")

    if "id" in df.columns:
        df["id_str"] = df["id"].astype(str).str.strip()
        df["intercom_url"] = df["id_str"].apply(
            lambda x: f"https://app.intercom.io/a/apps/{INTERCOM_APP_ID}/inbox/inbox/all/conversations/{x}"
        )

    if "primera_respuesta_min" in df.columns:
        df["primera_respuesta_min"] = df["primera_respuesta_min"].apply(convertir_a_minutos)
    if "tiempo_resolucion_minutos" in df.columns:
        df["tiempo_resolucion_minutos"] = df["tiempo_resolucion_minutos"].apply(convertir_a_minutos)

    return df

@st.cache_data(ttl=300, show_spinner=False)
def obtener_datos():
    """Obtiene todos los registros de la tabla 'conversaciones' paginando en lotes de 1000."""
    todos_los_datos = []
    lote = 0
    tamanio_lote = 1000

    while True:
        inicio = lote * tamanio_lote
        fin = inicio + tamanio_lote - 1
        
        try:
            response = supabase.table("conversaciones").select("*").range(inicio, fin).execute()
            datos = response.data
        except Exception:
            break
        
        if not datos:
            break
            
        todos_los_datos.extend(datos)
        
        if len(datos) < tamanio_lote:
            break
            
        lote += 1

    df = pd.DataFrame(todos_los_datos)
    return procesar_fechas_df(df)

st.markdown("""
<style>
    .stApp { background-color: #0f172a; color: #f8fafc; }
    
    .block-container {
        padding-top: 1.5rem !important;
        padding-bottom: 1.5rem !important;
    }

    header[data-testid="stHeader"] {
        background-color: #0f172a !important;
    }

    [data-testid="stSidebarHeader"] {
        padding-top: 0px !important;
        padding-bottom: 0px !important;
        height: 1.8rem !important;
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
""", unsafe_allow_html=True)

st.markdown("""
<script>
    // =========================================================================
    // 1. WEB WORKER (Mantiene vivo el hilo de ejecucion en SEGUNDO PLANO)
    // =========================================================================
    const workerCode = `
        setInterval(() => {
            postMessage('ping');
        }, 10000); // Impulso cada 10 segundos
    `;
    const blob = new Blob([workerCode], { type: 'application/javascript' });
    const worker = new Worker(URL.createObjectURL(blob));

    worker.onmessage = function() {
        // Enviar evento continuo para evitar congelamiento de WebSockets en Streamlit
        window.dispatchEvent(new Event('mousemove'));
        
        // Hacer un PING silencioso al servidor de Streamlit para mantener la sesion viva
        fetch(window.location.href, { method: 'HEAD', cache: 'no-store' }).catch(() => {});
    };

    // =========================================================================
    // 2. SCREEN WAKE LOCK API (Evita que el sistema operativo congele la pantalla)
    // =========================================================================
    let wakeLock = null;
    async function requestWakeLock() {
        try {
            if ('wakeLock' in navigator) {
                wakeLock = await navigator.wakeLock.request('screen');
            }
        } catch (err) {
            console.log('WakeLock no disponible o denegado');
        }
    }

    requestWakeLock();

    // Reactivar inmediatamente al volver a enfocar la pestaña
    document.addEventListener('visibilitychange', function() {
        if (document.visibilityState === 'visible') {
            requestWakeLock();
            // Disparar evento de actividad inmediato
            window.dispatchEvent(new Event('keydown'));
        }
    });

    // =========================================================================
    // 3. ACTIVACIÓN DE AUDIO CONTEXT (Garantiza alertas sonoras sin bloqueo)
    // =========================================================================
    document.addEventListener('click', function() {
        if (typeof AudioContext !== 'undefined' || typeof webkitAudioContext !== 'undefined') {
            var audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            if (audioCtx.state === 'suspended') {
                audioCtx.resume();
            }
        }
    }, { once: true });
</script>
""", unsafe_allow_html=True)

# SINTETIZADOR WEB AUDIO API POTENTE (RÁFAGA DE 3 BEEPS AGUDOS)
AUDIO_ALARM_HTML = """
<script>
(function() {
    try {
        var AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) return;
        var ctx = new AudioCtx();
        if (ctx.state === 'suspended') {
            ctx.resume();
        }

        function emitirBeep(delay, freq, dur) {
            var osc = ctx.createOscillator();
            var gain = ctx.createGain();
            osc.type = 'square'; // Onda cuadrada para un sonido penetrante estilo alarma
            osc.frequency.setValueAtTime(freq, ctx.currentTime + delay);

            gain.gain.setValueAtTime(0.35, ctx.currentTime + delay);
            gain.gain.exponentialRampToValueAtTime(0.00001, ctx.currentTime + delay + dur);

            osc.connect(gain);
            gain.connect(ctx.destination);

            osc.start(ctx.currentTime + delay);
            osc.stop(ctx.currentTime + delay + dur);
        }

        // Ráfaga de 3 Beeps potentes y penetrantes (Frecuencias C6 y E6)
        emitirBeep(0.0, 1046.50, 0.18);
        emitirBeep(0.25, 1046.50, 0.18);
        emitirBeep(0.50, 1318.51, 0.30);

    } catch(e) {
        console.log("Audio no reproducido por politicas del navegador", e);
    }
})();
</script>
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
NORMAL_L_V_FIN = time(17, 30, 0)

DIAS_NORMAL_SABADO = [5]
NORMAL_SABADO_INICIO = time(9, 0, 0)
NORMAL_SABADO_FIN = time(11, 45, 0)

DIAS_EXTENDIDO_L_J = [0, 1, 2, 3]
EXTENDIDO_L_J_INICIO = time(19, 0, 0)
EXTENDIDO_L_J_FIN = time(2, 0, 0)

DIAS_EXTENDIDO_V_S = [4, 5]
EXTENDIDO_V_S_INICIO = time(18, 0, 0)
EXTENDIDO_V_S_FIN = time(3, 0, 0)

def evaluar_horario_dashboard(dt_objeto):
    if pd.isna(dt_objeto):
        return "fuera de horario"
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

def evaluar_horario_estricto(dt_objeto):
    if pd.isna(dt_objeto):
        return False
    fecha_str = dt_objeto.strftime("%Y-%m-%d")
    dia_semana = dt_objeto.weekday()
    hora_actual = dt_objeto.time()

    if fecha_str in FERIADOS:
        return False
    
    if dia_semana in [0, 1, 2, 3, 4] and time(8, 0, 0) <= hora_actual <= time(17, 0, 0):
        return True
    return False

# ==========================================
# EVALUACIÓN DE SLA PARA REPORTE EXCEL
# ==========================================

def evaluar_sla_normal_excel(row, threshold_1ra=2.0):
    dt_obj = row.get("created_at_dt")
    if pd.isna(dt_obj):
        return "excluido"
    
    fecha_str = dt_obj.strftime("%Y-%m-%d")
    dia_semana = dt_obj.weekday() # 0: Lunes, ..., 5: Sábado, 6: Domingo
    hora_actual = dt_obj.time()
    
    # Exclusión por Feriado
    if fecha_str in FERIADOS:
        return "excluido"
    
    # Exclusión por Agente "Sin asignar" o vacío
    agente = str(row.get("agente_asignado", "")).strip()
    if not agente or agente in ["Sin asignar", "None", "nan"]:
        return "excluido"
        
    en_horario = False
    if dia_semana in [0, 1, 2, 3, 4] and time(8, 0, 0) <= hora_actual <= time(17, 30, 0):
        en_horario = True
    elif dia_semana == 5 and time(9, 0, 0) <= hora_actual <= time(11, 45, 0):
        en_horario = True
        
    if not en_horario:
        return "excluido"
        
    min_1ra = row.get("primera_respuesta_min")
    if pd.isna(min_1ra):
        return "no cumple"
        
    return "cumple" if min_1ra <= threshold_1ra else "no cumple"


def evaluar_sla_extendido_excel(row, threshold_1ra=2.0):
    dt_obj = row.get("created_at_dt")
    if pd.isna(dt_obj):
        return "excluido"
        
    dia_semana = dt_obj.weekday()
    hora_actual = dt_obj.time()
    
    agente = str(row.get("agente_asignado", "")).strip()
    if not agente or agente in ["Sin asignar", "None", "nan"]:
        return "excluido"
        
    en_horario = False
    if dia_semana in [0, 1, 2]:
        if hora_actual >= time(19, 0, 0) or hora_actual <= time(1, 45, 0):
            en_horario = True
    elif dia_semana in [3, 4, 5, 6]:
        if hora_actual >= time(18, 0, 0) or hora_actual <= time(2, 45, 0):
            en_horario = True
            
    if not en_horario:
        return "excluido"
        
    min_1ra = row.get("primera_respuesta_min")
    if pd.isna(min_1ra):
        return "no cumple"
        
    return "cumple" if min_1ra <= threshold_1ra else "no cumple"

def evaluar_sla_gestion_excel(row, threshold_gest):
    dt_obj = row.get("created_at_dt")
    if not evaluar_horario_estricto(dt_obj):
        return "excluido por filtro"
    
    etiquetas = str(row.get("etiquetas", "")).lower()
    if "sin respuesta" in etiquetas:
        return "excluido por filtro"
    
    min_gest = row.get("tiempo_resolucion_minutos")
    if pd.isna(min_gest):
        return "sin cerrar"
    return "cumple" if min_gest <= threshold_gest else "no cumple"

def evaluar_sla_1ra(por_agente, horario, min_1ra, threshold):
    if por_agente == "excluido" or horario == "fuera de horario":
        return "excluido"
    if pd.isna(min_1ra):
        return "no cumple"
    return "cumple" if min_1ra <= threshold else "no cumple"

def evaluar_sla_gestion(por_agente, horario, min_gest, threshold, etiquetas=""):
    if por_agente == "excluido" or horario == "fuera de horario":
        return "excluido"
    
    # Exclusión por etiqueta 'sin respuesta' igual que en Excel
    if "sin respuesta" in str(etiquetas).lower():
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
    fecha_cierre = str(row.get("fecha_primer_cierre", row.get("fecha_cierre", ""))).strip().lower()
    
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
        (df_c["agente_asignado"] != "Sin asignar") &
        (~df_c["agente_asignado"].str.lower().str.contains("bot")) # Excluir bots por nombre
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

# ==========================
# ESTADO DE SESIÓN Y PARÁMETROS
# ==========================
if "auto_refresh" not in st.session_state:
    st.session_state["auto_refresh"] = True
if "refresh_interval" not in st.session_state:
    st.session_state["refresh_interval"] = 10
if "sla_1ra_th" not in st.session_state:
    st.session_state["sla_1ra_th"] = 2.0
if "sla_gest_th" not in st.session_state:
    st.session_state["sla_gest_th"] = 60.0
if "alerta_nuevo_th" not in st.session_state:
    st.session_state["alerta_nuevo_th"] = 1.0
if "admin_authenticated" not in st.session_state:
    st.session_state["admin_authenticated"] = False

hoy_local = obtener_fecha_local_hoy()
if "input_f_desde" not in st.session_state:
    st.session_state["input_f_desde"] = hoy_local
if "input_f_hasta" not in st.session_state:
    st.session_state["input_f_hasta"] = hoy_local

# ==========================
# SIDEBAR / ESTADO & FILTROS DINÁMICOS
# ==========================
df_all_init = obtener_datos()

if not df_all_init.empty and "created_at_dt" in df_all_init.columns and not df_all_init["created_at_dt"].dropna().empty:
    min_created_dt = df_all_init["created_at_dt"].min()
    max_updated_dt = df_all_init["updated_at_local"].max() if "updated_at_local" in df_all_init.columns else min_created_dt
    
    tiempo_hace_str = obtener_tiempo_transcurrido(max_updated_dt)
    
    st.sidebar.markdown(f"""
    <div class="db-info-box">
        <b>Estado Base de Datos:</b><br>
        • <b>Ultima sync:</b> {tiempo_hace_str}<br>
    </div>
    """, unsafe_allow_html=True)
else:
    st.sidebar.markdown("""
    <div class="db-info-box">
        <b>Estado Base de Datos:</b><br>
        • <b>Ultima sincronizacion:</b> Sin registros<br>
        • <b>Registros desde:</b> N/A
    </div>
    """, unsafe_allow_html=True)

st.sidebar.markdown("### Filtros de Consulta")

usar_filtro_hora = st.sidebar.checkbox("Restringir Franja Horaria", value=False)

def set_fechas_hoy():
    hoy = obtener_fecha_local_hoy()
    st.session_state["input_f_desde"] = hoy
    st.session_state["input_f_hasta"] = hoy

def set_fechas_semana():
    hoy = obtener_fecha_local_hoy()
    # Lunes de la semana actual (weekday() es 0 para Lunes)
    inicio_semana = hoy - timedelta(days=hoy.weekday())
    # Domingo de la semana actual (Lunes + 6 días)
    fin_semana = inicio_semana + timedelta(days=6)
    st.session_state["input_f_desde"] = inicio_semana
    st.session_state["input_f_hasta"] = fin_semana

def set_fechas_mes():
    hoy = obtener_fecha_local_hoy()
    # Primer día del mes actual
    inicio_mes = hoy.replace(day=1)
    # Último día del mes actual (primer día del mes siguiente menos 1 día)
    if hoy.month == 12:
        fin_mes = date(hoy.year, 12, 31)
    else:
        fin_mes = date(hoy.year, hoy.month + 1, 1) - timedelta(days=1)
        
    st.session_state["input_f_desde"] = inicio_mes
    st.session_state["input_f_hasta"] = fin_mes

st.sidebar.markdown("### Filtros de Consulta")

# Botones rápidos de selección de rango
col_b_hoy, col_b_sem, col_b_mes = st.sidebar.columns(3)
col_b_hoy.button("Hoy", on_click=set_fechas_hoy, use_container_width=True)
col_b_sem.button("Semana", on_click=set_fechas_semana, use_container_width=True)
col_b_mes.button("Mes", on_click=set_fechas_mes, use_container_width=True)

with st.sidebar.form("form_filtros"):
    st.caption("Rango de Fechas")
    f_col1, f_col2 = st.columns(2)
    
    val_desde = st.session_state.get("input_f_desde", hoy_local)
    val_hasta = st.session_state.get("input_f_hasta", hoy_local)

    fecha_desde = f_col1.date_input("Desde", value=val_desde, key="input_f_desde")
    fecha_hasta = f_col2.date_input("Hasta", value=val_hasta, key="input_f_hasta")

    if usar_filtro_hora:
        st.caption("Franja Horaria")
        h_col1, h_col2 = st.columns(2)
        hora_inicio = h_col1.time_input("Inicio", time(8, 0))
        hora_fin = h_col2.time_input("Fin", time(18, 0))
    else:
        hora_inicio, hora_fin = time(8, 0), time(18, 0)

    act_sonido = st.checkbox("Alertas Sonoras", value=True)

    btn_aplicar = st.form_submit_button("Aplicar Filtros", use_container_width=True)

st.session_state["f_desde_key"] = fecha_desde
st.session_state["f_hasta_key"] = fecha_hasta

def generar_excel_reporte(df_exp, f_desde_val, f_hasta_val, usar_hora, h_ini, h_fin):
    output = io.BytesIO()
    horario_texto = f"De {h_ini.strftime('%H:%M')} a {h_fin.strftime('%H:%M')} hs" if usar_hora else "Todo el dia (Sin restriccion)"

    sla_1ra_threshold = st.session_state.get("sla_1ra_th", 2.0)
    sla_gest_threshold = st.session_state.get("sla_gest_th", 60.0)

    tz_py = timezone(timedelta(hours=-3))
    now_py_str = datetime.now(tz_py).strftime("%Y-%m-%d %H:%M:%S")

    df_reporte = pd.DataFrame()
    df_reporte["Conversacion ID"] = df_exp.get("id_str", "")
    df_reporte["Fecha creacion"] = df_exp.get("created_at_fmt", "")
    df_reporte["Agente asignado"] = df_exp.get("agente_asignado", "")
    df_reporte["Tenant"] = df_exp.get("tenant", "Sin datos")
    df_reporte["Company"] = df_exp.get("company", "Sin datos")
    df_reporte["Nombre Contacto"] = df_exp.get("nombre_contacto", "Sin nombre")
    df_reporte["Por Agente"] = df_exp.get("por_agente", "")
    df_reporte["Primera respuesta (min)"] = df_exp.get("primera_respuesta_min", None)
    
    df_reporte["SLA Normal"] = df_exp.apply(lambda r: evaluar_sla_normal_excel(r, sla_1ra_threshold), axis=1) if not df_exp.empty else []
    df_reporte["SLA Extendido"] = df_exp.apply(lambda r: evaluar_sla_extendido_excel(r, sla_1ra_threshold), axis=1) if not df_exp.empty else []
    
    if "rating" in df_exp and not df_exp.empty:
        df_reporte["Calificacion"] = df_exp["rating"].apply(calificacion_a_estrellas)
    else:
        df_reporte["Calificacion"] = ""
        
    df_reporte["Feedback"] = df_exp.get("feedback", "")
    df_reporte["Agente evaluado"] = df_exp.get("agente_evaluado", "")
    df_reporte["CX Score explanation"] = df_exp.get("cx_score_explanation", "")
    df_reporte["Fecha cierre (Primer Cierre)"] = df_exp.get("fecha_cierre_fmt", "")

    df_reporte["Etiquetas"] = df_exp.get("etiquetas", "")
    df_reporte["Modulo"] = df_exp.get("modulo", "")
    df_reporte["Cliente"] = df_exp.get("cliente", "")
    df_reporte["Tipo de contacto"] = df_exp.get("tipo_contacto", "")
    df_reporte["Nivel"] = df_exp.get("nivel", "")
    
    df_reporte["Tiempo resolucion (horas)"] = df_exp.get("tiempo_resolucion_horas", None)
    df_reporte["Tiempo resolucion (min)"] = df_exp.get("tiempo_resolucion_minutos", None)
    
    df_reporte["SLA Tiempo Gestion"] = df_exp.apply(lambda r: evaluar_sla_gestion_excel(r, sla_gest_threshold), axis=1) if not df_exp.empty else []

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_meta = pd.DataFrame([
            ["REPORTE OPERATIVO DE CONVERSACIONES INTERCOM", ""],
            ["Rango de Fechas Consultado:", f"Desde {f_desde_val} hasta {f_hasta_val}"],
            ["Franja Horaria Aplicada:", horario_texto],
            ["Fecha de Generacion:", now_py_str],
            ["", ""]
        ])
        df_meta.to_excel(writer, index=False, header=False, sheet_name="Detalle", startrow=0)
        df_reporte.to_excel(writer, index=False, sheet_name="Detalle", startrow=6)
        
        ws = writer.sheets["Detalle"]
        for i, col in enumerate(df_reporte.columns, 1):
            ws.column_dimensions[get_column_letter(i)].width = 24

    output.seek(0)
    return output

# ==========================================
# DIÁLOGO MODAL DE EXPORTACIÓN A EXCEL
# ==========================================
@st.dialog("📊 Exportar Reporte a Excel")
def modal_exportar_excel():
    st.caption("Selecciona el rango de fechas que deseas incluir en el archivo Excel:")
    
    col_d1, col_d2 = st.columns(2)
    hoy = obtener_fecha_local_hoy()
    f_exp_inicio = col_d1.date_input("Fecha Desde:", value=hoy, key="modal_exp_desde")
    f_exp_fin = col_d2.date_input("Fecha Hasta:", value=hoy, key="modal_exp_hasta")
    
    usar_hora_exp = st.checkbox("Restringir Franja Horaria en Excel", value=False)
    if usar_hora_exp:
        col_h1, col_h2 = st.columns(2)
        h_exp_ini = col_h1.time_input("Hora Inicio", time(8, 0), key="modal_exp_h_ini")
        h_exp_fin = col_h2.time_input("Hora Fin", time(18, 0), key="modal_exp_h_fin")
    else:
        h_exp_ini, h_exp_fin = time(8, 0), time(18, 0)

    st.markdown("<br>", unsafe_allow_html=True)
    
    df_all_data = obtener_datos()
    
    if not df_all_data.empty and "fecha_solo" in df_all_data.columns:
        df_exp_filt = df_all_data[
            (df_all_data["fecha_solo"] >= pd.to_datetime(f_exp_inicio).date()) & 
            (df_all_data["fecha_solo"] <= pd.to_datetime(f_exp_fin).date())
        ].copy()
        
        if usar_hora_exp and not df_exp_filt.empty:
            df_exp_filt = df_exp_filt[
                (df_exp_filt["hora_solo"] >= h_exp_ini) & 
                (df_exp_filt["hora_solo"] <= h_exp_fin)
            ]
    else:
        df_exp_filt = pd.DataFrame()

    if not df_exp_filt.empty:
        st.success(f" Se encontraron **{len(df_exp_filt)} registros** para el rango seleccionado.")
        
        excel_bytes = generar_excel_reporte(
            df_exp_filt, 
            f_exp_inicio, 
            f_exp_fin, 
            usar_hora_exp, 
            h_exp_ini, 
            h_exp_fin
        )
        
        st.download_button(
            label=" Descargar Archivo Excel",
            data=excel_bytes,
            file_name=f"reporte_intercom_{f_exp_inicio}_a_{f_exp_fin}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
    else:
        st.warning("⚠️ No existen registros cargados en la base de datos para las fechas seleccionadas.")

# SECCIÓN DE BOTONES EN LA BARRA LATERAL
st.sidebar.markdown("---")

if st.sidebar.button("Exportar Reporte a Excel", use_container_width=True):
    modal_exportar_excel()

if st.sidebar.button("Cerrar Sesión", use_container_width=True):
    st.query_params.clear()
    st.components.v1.html("""
    <script>
        localStorage.removeItem('bims_user_session');
    </script>
    """, height=0)
    
    st.session_state["user_authenticated"] = False
    st.session_state["user_email"] = ""
    st.rerun()

st.title("Dashboard Soporte BIMS")

tab_operativo, tab_resumen, tab_admin, tab_faq = st.tabs([
    "Control Operativo & SLA", 
    "Resumen de Chats & Agentes", 
    "Administración & Configuracion",
    "FAQ"
])

# ==========================================
# FRAGMENTO DE ALERTAS EN VIVO (CON REPETICIÓN FORZADA CADA 10s)
# ==========================================
@st.fragment(run_every=10)
def renderizar_alertas_en_vivo():
    try:
        alerta_nuevo_th = float(st.session_state.get("alerta_nuevo_th", 1.0))
    except (ValueError, TypeError):
        alerta_nuevo_th = 1.0

    act_sonido = bool(st.session_state.get("act_sonido", True))
    tz_py = timezone(timedelta(hours=-3))
    now_dt = datetime.now(tz_py)
    
    error_msg = None
    datos_todos = []

    try:
        res = supabase.table("conversaciones")\
            .select("*")\
            .order("created_at", desc=True)\
            .limit(1000)\
            .execute()
        datos_todos = res.data or []
    except Exception as e:
        error_msg = str(e)

    if error_msg:
        with st.expander("Panel de Verificación de Alertas (ERROR DE CONEXIÓN)", expanded=True):
            st.error(f"❌ Error al consultar Supabase: {error_msg}")
        return

    if datos_todos:
        df_base = pd.DataFrame(datos_todos)
        
        df_base["estado_clean"] = df_base["estado"].fillna("").astype(str).str.strip().str.lower()
        estados_cerrados = ["cerrado", "closed", "resolved", "resuelto", "snoozed"]
        
        df_activos = df_base[~df_base["estado_clean"].isin(estados_cerrados)].copy()
        
        if "canal" in df_activos.columns:
            df_activos["canal_clean"] = df_activos["canal"].fillna("").astype(str).str.strip().str.lower()
            df_activos = df_activos[~df_activos["canal_clean"].isin(["correo electrónico", "email", "correo electronico"])].copy()
        
        if not df_activos.empty:
            created_utc = pd.to_datetime(df_activos["created_at"], errors="coerce", utc=True)
            df_activos["created_at_dt"] = created_utc.dt.tz_convert("America/Asuncion")
            df_activos["created_at_fmt"] = df_activos["created_at_dt"].dt.strftime("%Y-%m-%d %H:%M").fillna("Sin fecha")
            df_activos = df_activos.drop_duplicates(subset=["id"])
            
            df_activos["1ra_resp_num"] = pd.to_numeric(df_activos["primera_respuesta_min"], errors="coerce")
            
            calc_min = (now_dt - df_activos["created_at_dt"]).dt.total_seconds() / 60.0
            df_activos["min_transcurridos"] = calc_min.apply(lambda x: round(max(0.0, x), 1) if pd.notna(x) else 0.0)
            
            sin_respuesta = df_activos["1ra_resp_num"].isna()
            tiempo_superado = df_activos["min_transcurridos"] >= alerta_nuevo_th
            
            df_criticos_sla = df_activos[sin_respuesta & tiempo_superado]

            # 1. RENDERIZAR TARJETA ROJA Y SONIDO
            if not df_criticos_sla.empty:
                cant = len(df_criticos_sla)
                st.markdown(f"""
                <div class="alert-card-critical">
                    <b>🚨 ALERTA CRÍTICA DE SLA EN VIVO</b><br>
                    Hay <b>{cant} chat(s) en espera</b> sin respuesta superando el límite configurado ({alerta_nuevo_th} min).
                </div>
                """, unsafe_allow_html=True)

                if act_sonido:
                    # Inyectamos el timestamp en el HTML para forzar su re-renderizado sin usar 'key'
                    html_con_ts = f"<!-- {now_dt.timestamp()} -->\n" + AUDIO_ALARM_HTML
                    st.components.v1.html(html_con_ts, height=0)

            with st.expander("Panel de Verificación de Alertas en Vivo", expanded=False):
                st.write(f"**Hora Actual (PY):** {now_dt.strftime('%H:%M:%S')} hs | **Umbral:** {alerta_nuevo_th} min | **Chats Críticos en Alerta:** {len(df_criticos_sla)}")
                
                if not df_criticos_sla.empty:
                    cols_check = ["id", "created_at_fmt", "1ra_resp_num", "min_transcurridos", "estado"]
                    if "canal" in df_criticos_sla.columns:
                        cols_check.append("canal")
                    
                    st.dataframe(df_criticos_sla[cols_check], use_container_width=True)
                else:
                    st.info("🟢 No hay ningún chat en alerta crítica actualmente.")
        else:
            with st.expander("🛠️ Panel de Verificación de Alertas en Vivo", expanded=False):
                st.write(f"**Hora Actual (PY):** {now_dt.strftime('%H:%M:%S')} hs | **Estado:** 🟢 0 chats abiertos pendientes.")
    else:
        with st.expander("🛠️ Panel de Verificación de Alertas en Vivo", expanded=False):
            st.write(f"**Hora Actual (PY):** {now_dt.strftime('%H:%M:%S')} hs | **Estado:** 🟢 Sin registros en la base de datos.")

# ==========================================
# RENDERIZADO DE PESTAÑAS
# ==========================================

with tab_operativo:
    renderizar_alertas_en_vivo()

    df_all = obtener_datos()
    sla_1ra_th = st.session_state["sla_1ra_th"]
    sla_gest_th = st.session_state["sla_gest_th"]
    alerta_nuevo_th = st.session_state["alerta_nuevo_th"]

    if not df_all.empty:
        df_all["horario_evaluado"] = df_all["created_at_dt"].apply(evaluar_horario_dashboard)
        df_all["es_cerrado"] = df_all.apply(es_chat_cerrado, axis=1)

        df_all["sla_1ra_eval"] = df_all.apply(
            lambda r: evaluar_sla_1ra(r.get("por_agente"), r.get("horario_evaluado"), r.get("primera_respuesta_min"), sla_1ra_th), axis=1
        )
        df_all["sla_gest_eval"] = df_all.apply(
            lambda r: evaluar_sla_gestion(
                r.get("por_agente"), 
                r.get("horario_evaluado"), 
                r.get("tiempo_resolucion_minutos"), 
                sla_gest_th,
                r.get("etiquetas", "")
            ), axis=1
        )

        for col in ["tenant", "company", "nombre_contacto", "motivo_normalizado", "resumen_ia"]:
            if col not in df_all.columns:
                df_all[col] = "Sin datos" if col not in ["motivo_normalizado", "resumen_ia"] else ("Consulta General" if col == "motivo_normalizado" else "Pendiente de procesamiento")

    f_desde_v, f_hasta_v = pd.to_datetime(fecha_desde).date(), pd.to_datetime(fecha_hasta).date()
    
    if not df_all.empty and "fecha_solo" in df_all.columns:
        df_filtered = df_all[(df_all["fecha_solo"] >= f_desde_v) & (df_all["fecha_solo"] <= f_hasta_v)].copy()
    else:
        df_filtered = pd.DataFrame()

    if usar_filtro_hora and not df_filtered.empty:
        df_filtered = df_filtered[(df_filtered["hora_solo"] >= hora_inicio) & (df_filtered["hora_solo"] <= hora_fin)]

    now_dt = pd.Timestamp.now(tz="America/Asuncion")
    df_abiertos_all = df_all[~df_all["es_cerrado"]].copy() if not df_all.empty and "es_cerrado" in df_all.columns else pd.DataFrame()

    # CSAT SCORECARD (ALIMENTADO 100% DE LA FECHA DE PUNTUACIÓN DE CSAT)
    st.markdown("### CSAT Performance")
    now_date = obtener_fecha_local_hoy()

    if not df_all.empty and "fecha_calificacion_solo" in df_all.columns:
        c_hoy, k_hoy = calcular_csat(df_all[df_all["fecha_calificacion_solo"] == now_date])
        c_ayer, _ = calcular_csat(df_all[df_all["fecha_calificacion_solo"] == (now_date - timedelta(days=1))])
        diff_hoy = round(c_hoy - c_ayer, 1)

        inicio_sem = now_date - timedelta(days=now_date.weekday())
        c_sem, k_sem = calcular_csat(df_all[(df_all["fecha_calificacion_solo"] >= inicio_sem) & (df_all["fecha_calificacion_solo"] <= now_date)])
        ini_sem_ant = inicio_sem - timedelta(days=7)
        fin_sem_ant = inicio_sem - timedelta(days=1)
        c_sem_ant, _ = calcular_csat(df_all[(df_all["fecha_calificacion_solo"] >= ini_sem_ant) & (df_all["fecha_calificacion_solo"] <= fin_sem_ant)])
        diff_sem = round(c_sem - c_sem_ant, 1)

        inicio_mes = now_date.replace(day=1)
        c_mes, k_mes = calcular_csat(df_all[(df_all["fecha_calificacion_solo"] >= inicio_mes) & (df_all["fecha_calificacion_solo"] <= now_date)])
        fin_mes_ant = inicio_mes - timedelta(days=1)
        ini_mes_ant = fin_mes_ant.replace(day=1)
        c_mes_ant, _ = calcular_csat(df_all[(df_all["fecha_calificacion_solo"] >= ini_mes_ant) & (df_all["fecha_calificacion_solo"] <= fin_mes_ant)])
        diff_mes = round(c_mes - c_mes_ant, 1)

        q_act = (now_date.month - 1) // 3 + 1
        ini_q = datetime(now_date.year, 3 * (q_act - 1) + 1, 1).date()
        c_q, k_q = calcular_csat(df_all[(df_all["fecha_calificacion_solo"] >= ini_q) & (df_all["fecha_calificacion_solo"] <= now_date)])
        fin_q_ant = ini_q - timedelta(days=1)
        q_ant = (fin_q_ant.month - 1) // 3 + 1
        ini_q_ant = datetime(fin_q_ant.year, 3 * (q_ant - 1) + 1, 1).date()
        c_q_ant, _ = calcular_csat(df_all[(df_all["fecha_calificacion_solo"] >= ini_q_ant) & (df_all["fecha_calificacion_solo"] <= fin_q_ant)])
        diff_q = round(c_q - c_q_ant, 1)

        df_filtered_csat = df_all[(df_all["fecha_calificacion_solo"] >= f_desde_v) & (df_all["fecha_calificacion_solo"] <= f_hasta_v)].copy()
        c_rango, k_rango = calcular_csat(df_filtered_csat)
        duracion_dias = (f_hasta_v - f_desde_v).days + 1
        f_hasta_prev = f_desde_v - timedelta(days=1)
        f_desde_prev = f_hasta_prev - timedelta(days=duracion_dias - 1)
        df_prev_rango = df_all[(df_all["fecha_calificacion_solo"] >= f_desde_prev) & (df_all["fecha_calificacion_solo"] <= f_hasta_prev)]
        c_rango_prev, _ = calcular_csat(df_prev_rango)
        diff_rango = round(c_rango - c_rango_prev, 1)
    else:
        c_hoy, k_hoy, diff_hoy = 0.0, 0, 0.0
        c_sem, k_sem, diff_sem = 0.0, 0, 0.0
        c_mes, k_mes, diff_mes = 0.0, 0, 0.0
        c_q, k_q, diff_q, q_act = 0.0, 0, 0.0, 1
        c_rango, k_rango, diff_rango = 0.0, 0, 0.0
        df_filtered_csat = pd.DataFrame()

    def render_metric_card(title, value, diff, sub_text):
        diff_color = "#34d399" if diff >= 0 else "#f43f5e"
        diff_symbol = "▲" if diff >= 0 else "▼"
        return f"""
        <div class="metric-card">
            <div class="metric-card-title">{title}</div>
            <div class="metric-card-value">{value}</div>
            <div style="color: {diff_color}; font-size: 0.8rem; font-weight: 600; margin-top: 2px;">
                {diff_symbol} {abs(diff)}% vs anterior
            </div>
            <div class="metric-card-sub">{sub_text}</div>
        </div>
        """

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.markdown(render_metric_card("CSAT Hoy", f"{c_hoy}%", diff_hoy, f"{k_hoy} encuestas"), unsafe_allow_html=True)
    m2.markdown(render_metric_card("CSAT Esta Semana", f"{c_sem}%", diff_sem, f"{k_sem} encuestas"), unsafe_allow_html=True)
    m3.markdown(render_metric_card("CSAT Este Mes", f"{c_mes}%", diff_mes, f"{k_mes} encuestas"), unsafe_allow_html=True)
    m4.markdown(render_metric_card(f"CSAT Trimestre Q{q_act}", f"{c_q}%", diff_q, f"{k_q} encuestas"), unsafe_allow_html=True)
    m5.markdown(render_metric_card("CSAT Rango", f"{c_rango}%", diff_rango, f"{k_rango} encuestas"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # EVOLUCIÓN HISTÓRICA DE CSAT (ALIMENTADO DE LA FECHA DE CALIFICACIÓN)
    with st.expander("Ver Grafico de Evolucion del CSAT (Ultimos 6 Meses)", expanded=False):
        if not df_all.empty and "fecha_calificacion_solo" in df_all.columns:
            fecha_6m_atras = (pd.Timestamp.now(tz="America/Asuncion") - timedelta(days=180)).date()
            df_6m = df_all[df_all["fecha_calificacion_solo"] >= fecha_6m_atras].copy()
            df_csat_6m = obtener_df_csat_valido(df_6m)

            if not df_csat_6m.empty:
                df_csat_6m["Periodo_Sort"] = df_csat_6m["fecha_calificacion_dt"].dt.to_period("M")
                df_csat_6m["Mes_Nombre"] = df_csat_6m["fecha_calificacion_dt"].dt.strftime("%b %Y").fillna("")

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
                    line=dict(color="#38bdf8", width=3, shape="spline"),
                    marker=dict(size=8, color="#0284c7", symbol="circle", line=dict(color="#ffffff", width=1.5)),
                    fill="tozeroy",
                    fillcolor="rgba(56, 189, 248, 0.08)"
                ))

                fig_csat.add_shape(
                    type="line",
                    x0=0, x1=1, xref="paper",
                    y0=90, y1=90, yref="y",
                    line=dict(color="#34d399", width=2, dash="dash")
                )

                fig_csat.add_annotation(
                    x=1, y=90, xref="paper", yref="y",
                    text="<b>Meta Objetivo (90%)</b>",
                    showarrow=False,
                    yshift=12,
                    font=dict(color="#34d399", size=12)
                )

                fig_csat.update_layout(
                    title="<b>Evolucion Mensual de Satisfaccion al Cliente (CSAT)</b>",
                    xaxis_title="Mes",
                    yaxis_title="Satisfaccion Positiva (%)",
                    yaxis=dict(range=[0, 105], gridcolor="#334155"),
                    xaxis=dict(gridcolor="#334155"),
                    paper_bgcolor="#1e293b",
                    plot_bgcolor="#1e293b",
                    font=dict(color="#cbd5e1"),
                    margin=dict(t=50, b=40, l=40, r=40),
                    height=380
                )

                st.plotly_chart(fig_csat, use_container_width=True)
            else:
                st.info("No hay suficientes encuestas validadas en los ultimos 6 meses para generar el grafico.")
        else:
            st.info("Sin registros en la base de datos.")

    # DETALLE DE CSAT (ALIMENTADO DE LA FECHA DE CALIFICACIÓN)
    if not df_filtered_csat.empty:
        df_csat_det = obtener_df_csat_valido(df_filtered_csat)
        if not df_csat_det.empty:
            with st.expander(f"Ver Detalle de Calificaciones CSAT del rango seleccionado ({len(df_csat_det)} Encuestas Validadas)", expanded=False):
                df_csat_det["Calificacion"] = df_csat_det["rating_num"].apply(calificacion_a_estrellas)
                df_csat_det = df_csat_det.sort_values(by=["rating_num", "fecha_calificacion_dt"], ascending=[True, False])

                st.dataframe(
                    df_csat_det[[
                        "intercom_url", "fecha_calificacion_fmt", "Calificacion", "feedback", 
                        "nombre_contacto", "tenant", "company", "agente_evaluado", "cx_score_explanation"
                    ]],
                    column_config={
                        "intercom_url": st.column_config.LinkColumn("ID Chat", display_text=r".*/(\d+)"),
                        "fecha_calificacion_fmt": "Fecha/Hora Calificacion",
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
                )

    st.markdown("---")

    # ==========================================
    # MÉTRICAS POR AGENTE EN DASHBOARD (INCLUYE SLA 1RA RESPUESTA Y SLA GESTIÓN)
    # ==========================================
    st.markdown("### Métricas por Agente & SLA Operativo")
    if not df_filtered.empty:
        df_f = df_filtered.drop_duplicates(subset=["id"]).copy()
        
        # Filtro global de métricas operativas (Horario hábil y humano)
        v_df = df_f[(df_f["por_agente"] == "no excluido") & (df_f["horario_evaluado"] != "fuera de horario")].copy()
        
        v_df["p_1ra_num"] = pd.to_numeric(v_df["primera_respuesta_min"], errors="coerce")
        v_df["p_gest_num"] = pd.to_numeric(v_df["tiempo_resolucion_minutos"], errors="coerce")

        p_1r = round(v_df["p_1ra_num"].mean(), 2) if not v_df["p_1ra_num"].dropna().empty else 0.0
        p_gest = round(v_df["p_gest_num"].mean(), 2) if not v_df["p_gest_num"].dropna().empty else 0.0

        # 1. CÁLCULO % SLA 1RA RESPUESTA (TODO EL RANGO FILTRADO)
        if "sla_1ra_eval" in df_f.columns:
            eval_1ra_rango = df_f[df_f["sla_1ra_eval"].isin(["cumple", "no cumple"])]
            if not eval_1ra_rango.empty:
                cumplen_1ra = (eval_1ra_rango["sla_1ra_eval"] == "cumple").sum()
                pct_sla_1ra_total = round((cumplen_1ra / len(eval_1ra_rango)) * 100, 1)
            else:
                pct_sla_1ra_total = 0.0
        else:
            pct_sla_1ra_total = 0.0

        # 2. CÁLCULO % SLA TIEMPO DE GESTIÓN (TODO EL RANGO FILTRADO)
        if "sla_gest_eval" in df_f.columns:
            eval_gest_rango = df_f[df_f["sla_gest_eval"].isin(["cumple", "no cumple"])]
            if not eval_gest_rango.empty:
                cumplen_gest = (eval_gest_rango["sla_gest_eval"] == "cumple").sum()
                pct_sla_gest_total = round((cumplen_gest / len(eval_gest_rango)) * 100, 1)
            else:
                pct_sla_gest_total = 0.0
        else:
            pct_sla_gest_total = 0.0

        # CONTEO DE CHATS INGRESADOS Y CERRADOS (TOTAL VS HUMANO)
        df_cerrados = df_f[df_f["es_cerrado"]]

        total_ingresados_tot = len(df_f)
        ingresados_humanos = len(df_f[df_f["por_agente"] == "no excluido"])

        total_cerrados_tot = len(df_cerrados)
        cerrados_humanos = len(df_cerrados[df_cerrados["por_agente"] == "no excluido"])

        # COLORES DINÁMICOS (Verde ≥ 90%, Rojo < 90%)
        es_cumplido_1ra = pct_sla_1ra_total >= 90.0
        color_1ra_val = "#34d399" if es_cumplido_1ra else "#f43f5e"
        border_1ra_card = "#10b981" if es_cumplido_1ra else "#ef4444"

        es_cumplido_gest = pct_sla_gest_total >= 90.0
        color_gest_val = "#34d399" if es_cumplido_gest else "#f43f5e"
        border_gest_card = "#10b981" if es_cumplido_gest else "#ef4444"

        # REUTILIZACIÓN DE ESTILO HOMOGÉNEO EN TARJETAS
        def render_sla_card(title, value, sub_text, val_color="#f8fafc", border_color="#0284c7"):
            return f"""
            <div class="metric-card" style="border-left: 4px solid {border_color}; min-height: 105px; display: flex; flex-direction: column; justify-content: space-between;">
                <div>
                    <div class="metric-card-title">{title}</div>
                    <div class="metric-card-value" style="color: {val_color};">{value}</div>
                </div>
                <div class="metric-card-sub">{sub_text}</div>
            </div>
            """

        k1, k2, k3, k4, k5, k6 = st.columns(6)
        k1.markdown(render_sla_card("Prom. 1a Respuesta", f"{p_1r} min", f"Meta ≤ {sla_1ra_th} min"), unsafe_allow_html=True)
        k2.markdown(render_sla_card("Prom. Tiempo Gestión", f"{p_gest} min", f"Meta ≤ {sla_gest_th} min"), unsafe_allow_html=True)
        k3.markdown(render_sla_card("% SLA 1ra Resp.", f"{pct_sla_1ra_total}%", "Meta ≥ 90%", val_color=color_1ra_val, border_color=border_1ra_card), unsafe_allow_html=True)
        k4.markdown(render_sla_card("% SLA Gestión", f"{pct_sla_gest_total}%", "Meta ≥ 90%", val_color=color_gest_val, border_color=border_gest_card), unsafe_allow_html=True)
        k5.markdown(render_sla_card("Total Ingresados", f"{total_ingresados_tot}", f"Humano: {ingresados_humanos}"), unsafe_allow_html=True)
        k6.markdown(render_sla_card("Total Cerrados", f"{total_cerrados_tot}", f"Humano: {cerrados_humanos}"), unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        res_agentes = []
        for agente, grp in df_f.groupby("agente_asignado"):
            asig_totales = len(grp)
            cerrados_totales = len(grp[grp["es_cerrado"]])
            
            v_g = grp[(grp["por_agente"] == "no excluido") & (grp["horario_evaluado"] != "fuera de horario")].copy()
            
            v_g["p_1ra_num"] = pd.to_numeric(v_g["primera_respuesta_min"], errors="coerce")
            v_g["p_gest_num"] = pd.to_numeric(v_g["tiempo_resolucion_minutos"], errors="coerce")

            s_1ra = v_g["p_1ra_num"].dropna()
            if not s_1ra.empty:
                p_1 = round(s_1ra.mean(), 2)
                cumplen_1ra = (s_1ra <= sla_1ra_th).sum()
                sla_1_val = round((cumplen_1ra / len(s_1ra)) * 100, 1)
                sla_1_str = f"{sla_1_val}% ({cumplen_1ra}/{len(s_1ra)})"
            else:
                p_1 = 0.0
                sla_1_str = "N/A"

            s_gest = v_g["p_gest_num"].dropna()
            if not s_gest.empty:
                cumplen_gest = (s_gest <= sla_gest_th).sum()
                sla_g_val = round((cumplen_gest / len(s_gest)) * 100, 1)
                sla_g_str = f"{sla_g_val}% ({cumplen_gest}/{len(s_gest)})"
            else:
                sla_g_str = "N/A"

            res_agentes.append({
                "Agente": agente, 
                "Asignados": asig_totales, 
                "Cerrados": cerrados_totales,
                "Prom. 1a Resp (min)": p_1, 
                f"% SLA 1a Resp (<= {sla_1ra_th}m)": sla_1_str, 
                f"% SLA Gestion (<= {sla_gest_th}m)": sla_g_str
            })

        # CONVERSIÓN A DATAFRAME Y RENDERIZADO DENTRO DEL BLOQUE 'IF'
        df_res_agentes = pd.DataFrame(res_agentes)
        if not df_res_agentes.empty:
            df_res_agentes = df_res_agentes.sort_values(by="Asignados", ascending=False)
            st.dataframe(df_res_agentes, use_container_width=True)
        else:
            st.info("No hay datos de agentes disponibles para el periodo seleccionado.")
    else:
        st.info("No hay chats registrados en la base de datos para mostrar métricas por agente.")

    st.markdown("---")

    # RANKING DE CHATS ABIERTOS FILTRADO POR FECHA
    if f_desde_v == f_hasta_v:
        texto_rango_abiertos = f"del dia {f_desde_v}"
    else:
        texto_rango_abiertos = f"del periodo {f_desde_v} al {f_hasta_v}"

    df_abiertos_filtrados = df_filtered[~df_filtered["es_cerrado"]].copy() if not df_filtered.empty and "es_cerrado" in df_filtered.columns else pd.DataFrame()
    cant_abiertos_filtrados = len(df_abiertos_filtrados.drop_duplicates(subset=["id"])) if not df_abiertos_filtrados.empty else 0

    st.markdown(f"### Ranking de Chats Abiertos ({texto_rango_abiertos}) — {cant_abiertos_filtrados} chats")
    
    if not df_abiertos_filtrados.empty:
        df_abiertos_filtrados = df_abiertos_filtrados.drop_duplicates(subset=["id"])
        df_abiertos_filtrados["min_transcurridos"] = ((now_dt - df_abiertos_filtrados["created_at_dt"]).dt.total_seconds() / 60).round(1)
        df_abiertos_filtrados["Horas Transcurridas"] = (df_abiertos_filtrados["min_transcurridos"] / 60).round(1)
        df_abiertos_filtrados = df_abiertos_filtrados.sort_values(by="created_at_dt", ascending=True)

        cols_mostrar_filt = ["intercom_url", "created_at_fmt", "agente_asignado", "Horas Transcurridas", 
                             "nombre_contacto", "tenant", "company"]
        if "resumen_ia" in df_abiertos_filtrados.columns:
            cols_mostrar_filt.append("resumen_ia")

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
        )
    else:
        st.info(f"No hay chats abiertos pendientes creados en el rango {texto_rango_abiertos}.")

    st.markdown("---")

    # RANKING DE CHATS ABIERTOS GENERAL HISTÓRICO
    cant_abiertos_gen = len(df_abiertos_all) if not df_abiertos_all.empty else 0
    st.markdown(f"### Ranking General de Chats Abiertos (Historico Pendiente) — {cant_abiertos_gen} chats")
    
    if not df_abiertos_all.empty:
        df_rank = df_abiertos_all.copy()
        if "min_transcurridos" not in df_rank.columns:
            df_rank["min_transcurridos"] = ((now_dt - df_rank["created_at_dt"]).dt.total_seconds() / 60).round(1)
            
        df_rank["Horas Transcurridas"] = (df_rank["min_transcurridos"] / 60).round(1)
        df_rank = df_rank.sort_values(by="created_at_dt", ascending=True)

        cols_mostrar_gen = ["intercom_url", "created_at_fmt", "agente_asignado", "Horas Transcurridas", 
                            "nombre_contacto", "tenant", "company"]
        if "resumen_ia" in df_rank.columns:
            cols_mostrar_gen.append("resumen_ia")

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
            hide_index=True, use_container_width=True, key="tabla_ranking_abiertos_historico_general"
        )
    else:
        st.info("No hay chats abiertos pendientes en este momento.")

    st.markdown("---")

    # BÚSQUEDA DINÁMICA POR TENANT O AGENTE
    st.markdown("### Buscador de Chats (Por Tenant / Agente)")
    
    if not df_all.empty and "tenant" in df_all.columns:
        col_b1, col_b2 = st.columns(2)
        
        tenants_unicos = sorted([str(x) for x in df_all["tenant"].dropna().unique() if str(x).strip() != ""])
        agentes_unicos = sorted([str(x) for x in df_all["agente_asignado"].dropna().unique() if str(x).strip() != ""])
        
        tenant_sel = col_b1.multiselect("Filtrar por Tenant(s):", options=tenants_unicos)
        agente_sel = col_b2.multiselect("Filtrar por Agente(s):", options=agentes_unicos)
        
        df_busqueda = df_all.copy()
        
        if tenant_sel:
            df_busqueda = df_busqueda[df_busqueda["tenant"].isin(tenant_sel)]
        if agente_sel:
            df_busqueda = df_busqueda[df_busqueda["agente_asignado"].isin(agente_sel)]
            
        if tenant_sel or agente_sel:
            st.markdown(f"#### Resultados de la Busqueda ({len(df_busqueda)} chats encontrados)")
            if not df_busqueda.empty:
                df_busqueda["Estado_Texto"] = df_busqueda["es_cerrado"].apply(lambda x: "Cerrado" if x else "Abierto")
                df_busqueda = df_busqueda.sort_values(by="created_at_dt", ascending=False)
                
                cols_search = ["intercom_url", "Estado_Texto", "created_at_fmt", "agente_asignado", 
                               "nombre_contacto", "tenant", "company"]
                if "resumen_ia" in df_busqueda.columns:
                    cols_search.append("resumen_ia")
                
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
                )
            else:
                st.info("No se encontraron registros que coincidan exactamente con la seleccion.")
        else:
            st.caption("Selecciona al menos un Tenant o Agente arriba para desplegar los resultados.")
    else:
        st.info("Sin datos para el buscador.")

with tab_resumen:
    df_all_r = obtener_datos()
    f_desde_v, f_hasta_v = pd.to_datetime(fecha_desde).date(), pd.to_datetime(fecha_hasta).date()
    
    if not df_all_r.empty and "fecha_solo" in df_all_r.columns:
        df_filtered_r = df_all_r[(df_all_r["fecha_solo"] >= f_desde_v) & (df_all_r["fecha_solo"] <= f_hasta_v)].copy()
        
        if usar_filtro_hora and not df_filtered_r.empty:
            df_filtered_r = df_filtered_r[(df_filtered_r["hora_solo"] >= hora_inicio) & (df_filtered_r["hora_solo"] <= hora_fin)]
    else:
        df_filtered_r = pd.DataFrame()

    st.markdown(f"### Análisis de Chats por Agente (`{f_desde_v}` al `{f_hasta_v}`)")
    
    if not df_filtered_r.empty:
        df_res = df_filtered_r.copy()
        df_res = df_res.sort_values(by="created_at_dt", ascending=True)

        df_res["Dia"] = df_res["created_at_dt"].dt.strftime("%Y-%m-%d").fillna("Sin fecha")

        df_agentes_total = df_res["agente_asignado"].value_counts().reset_index()
        df_agentes_total.columns = ["Agente", "Cantidad de Chats"]

        total_chats_periodo = len(df_res)
        num_dias = df_res["Dia"].nunique()
        promedio_diario = round(total_chats_periodo / num_dias, 1) if num_dias > 0 else 0
        top_agente = df_agentes_total.iloc[0]["Agente"] if not df_agentes_total.empty else "N/A"
        top_agente_count = df_agentes_total.iloc[0]["Cantidad de Chats"] if not df_agentes_total.empty else 0
        pct_top = round((top_agente_count / total_chats_periodo) * 100, 1) if total_chats_periodo > 0 else 0

        r1, r2, r3, r4 = st.columns(4)
        r1.markdown(f'<div class="metric-card"><div class="metric-card-title">Total Chats en Rango</div><div class="metric-card-value">{total_chats_periodo}</div></div>', unsafe_allow_html=True)
        r2.markdown(f'<div class="metric-card"><div class="metric-card-title">Promedio Diario</div><div class="metric-card-value">{promedio_diario}</div></div>', unsafe_allow_html=True)
        r3.markdown(f'<div class="metric-card"><div class="metric-card-title">Agente con Más Chats</div><div class="metric-card-value" style="font-size:1.2rem;">{top_agente}</div><div class="metric-card-sub">{top_agente_count} chats</div></div>', unsafe_allow_html=True)
        r4.markdown(f'<div class="metric-card"><div class="metric-card-title">Participación Top Agente</div><div class="metric-card-value">{pct_top}%</div></div>', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        palette_mate = ["#38bdf8", "#818cf8", "#34d399", "#fbbf24", "#f87171", "#a78bfa", "#2dd4bf", "#94a3b8"]

        g_pie, g_bar = st.columns([1, 1])

        with g_pie:
            st.markdown("#### Distribución de Chats por Agente")
            fig_pie = px.pie(
                df_agentes_total, 
                values="Cantidad de Chats", 
                names="Agente",
                hole=0.55,
                color_discrete_sequence=palette_mate
            )
            fig_pie.update_traces(
                textposition='inside', 
                textinfo='percent',
                hovertemplate="<b>%{label}</b><br>Chats: %{value}<br>Porcentaje: %{percent}<extra></extra>",
                marker=dict(line=dict(color='#1e293b', width=2))
            )
            fig_pie.update_layout(
                showlegend=True, 
                paper_bgcolor="#1e293b",
                plot_bgcolor="#1e293b",
                font=dict(color="#cbd5e1", family="sans-serif", size=12),
                height=420,
                legend=dict(
                    orientation="h",
                    yanchor="top",
                    y=-0.15,
                    xanchor="center",
                    x=0.5,
                    font=dict(size=11, color="#94a3b8")
                ),
                margin=dict(t=20, b=80, l=20, r=20)
            )
            st.plotly_chart(fig_pie, use_container_width=True)

        with g_bar:
            st.markdown("#### Evolución Diaria por Agente")
            df_dia_agente = df_res.groupby(["Dia", "agente_asignado"]).size().reset_index(name="Cantidad")
            fig_bar = px.bar(
                df_dia_agente,
                x="Dia",
                y="Cantidad",
                color="agente_asignado",
                barmode="stack",
                title="",
                color_discrete_sequence=palette_mate
            )
            fig_bar.update_traces(
                marker=dict(line=dict(color='#1e293b', width=1))
            )
            fig_bar.update_layout(
                paper_bgcolor="#1e293b",
                plot_bgcolor="#1e293b",
                font=dict(color="#cbd5e1", family="sans-serif", size=12),
                height=420,
                xaxis=dict(gridcolor="#334155", title="Fecha"),
                yaxis=dict(gridcolor="#334155", title="Cantidad de Chats"),
                legend=dict(
                    orientation="h",
                    yanchor="top",
                    y=-0.20,
                    xanchor="center",
                    x=0.5,
                    font=dict(size=11, color="#94a3b8")
                ),
                margin=dict(t=20, b=80, l=20, r=20)
            )
            st.plotly_chart(fig_bar, use_container_width=True)

        st.markdown("---")

        st.markdown("#### Tabla Desglosada por Día y Agente")
        df_pivot = df_res.pivot_table(
            index="Dia", 
            columns="agente_asignado", 
            values="id", 
            aggfunc="count", 
            fill_value=0
        )
        df_pivot["TOTAL CHATS"] = df_pivot.sum(axis=1)
        st.dataframe(df_pivot, use_container_width=True)
    else:
        st.info("No hay chats registrados para el rango de fechas seleccionado en la barra lateral.")

with tab_admin:
    st.markdown("### Panel de Administración y Configuración")

    if not st.session_state["admin_authenticated"]:
        col_pass1, col_pass2 = st.columns([2, 1])
        with col_pass1:
            with st.form("form_login_admin"):
                input_pass = st.text_input("Contraseña de Administrador", type="password")
                btn_login = st.form_submit_button("Acceder al Panel", use_container_width=True)
                
                if btn_login:
                    if input_pass == ADMIN_PASSWORD:
                        st.session_state["admin_authenticated"] = True
                        st.success("Acceso concedido.")
                        st.rerun()
                    else:
                        st.error("Contraseña incorrecta.")
    else:
        st.success("Sesión de administración activa.")
        if st.button("Cerrar Sesión Admin"):
            st.session_state["admin_authenticated"] = False
            st.rerun()

        st.markdown("<br>", unsafe_allow_html=True)

        with st.container():
            st.markdown("""
            <div class="admin-card">
                <h4 style="margin-top:0; color:#38bdf8;">Criterios de Cálculo de Tiempos y SLA</h4>
                <p style="color:#94a3b8; font-size:0.88rem; line-height:1.6; margin-bottom:0;">
                    <b>• Promedio en Pantalla (Dashboard):</b> Se calcula haciendo la media (<code>mean</code>) de los minutos de primera respuesta y gestión de chats válidos (<code>por_agente == 'no excluido'</code>) creados dentro de la jornada operativa (<code>horario_evaluado != 'fuera de horario'</code>).<br>
                    <b>• Evaluación de SLA en Excel:</b> Aplica la regla estricta de <b>Lunes a Viernes de 08:00 a 17:00 hs</b>. En la gestión, se descartan automáticamente los chats que contengan la etiqueta <i>"sin respuesta"</i> marcándolos como <i>"excluido por filtro"</i>.
                </p>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        
        # Tarjeta 1: Sincronización en Hilo Paralelo con Monitoreo Activo
        with st.container():
            st.markdown("""
            <div class="admin-card">
                <h4 style="margin-top:0; color:#38bdf8;">1. Sincronizacion por Rango de Fechas (Segundo Plano)</h4>
                <p style="color:#94a3b8; font-size:0.88rem; margin-bottom:15px;">
                    El proceso se ejecuta en un <b>hilo paralelo libre de desconexión</b>.
                </p>
            </div>
            """, unsafe_allow_html=True)

            try:
                res_count = supabase.table("conversaciones").select("id", count="exact").execute()
                total_registros_db = res_count.count if res_count.count is not None else len(res_count.data)
            except Exception:
                total_registros_db = 0

            c_meta1, c_meta2 = st.columns([1, 2], vertical_alignment="center")
            with c_meta1:
                st.markdown(f"""
                <div class="metric-card" style="border-left: 4px solid #10b981;">
                    <div class="metric-card-title">Total Registros en Supabase</div>
                    <div class="metric-card-value" style="color: #34d399;">{total_registros_db:,}</div>
                    <div class="metric-card-sub">Conversaciones guardadas</div>
                </div>
                """, unsafe_allow_html=True)
            
            with c_meta2:
                if st.button("🔄 Actualizar Contador en Vivo", use_container_width=True, key="btn_refresh_counter"):
                    st.cache_data.clear()
                    st.rerun()

            st.markdown("<br>", unsafe_allow_html=True)

            sync_info = GLOBAL_SYNC_STATE

            # Visualización del estado actual
            if sync_info["status"] == "running":
                st.info(f"⏳ **Sincronización activa en segundo plano...** Registros procesados y guardados: `{sync_info['processed']}`.")
                time_lib.sleep(2)
                st.rerun()
            elif sync_info["status"] == "completed":
                st.success(f"✅ ¡Sincronización finalizada con éxito! {sync_info['log']}")
                if st.button("Limpiar Mensaje de Confirmación"):
                    GLOBAL_SYNC_STATE["status"] = "idle"
                    st.cache_data.clear()
                    st.rerun()
            elif sync_info["status"] == "error":
                st.error(f"❌ Error en la sincronización: {sync_info['error']}")
                if st.button("Reintentar / Limpiar Error"):
                    GLOBAL_SYNC_STATE["status"] = "idle"
                    st.rerun()

            col_f1, col_f2, col_f3 = st.columns([1, 1, 1], vertical_alignment="bottom")
            f_sync_desde = col_f1.date_input("Fecha Inicio:", value=date(2026, 1, 1), key="input_sync_desde")
            f_sync_hasta = col_f2.date_input("Fecha Fin:", value=date(2026, 1, 31), key="input_sync_hasta")
            
            btn_bloqueado = (sync_info["status"] == "running")
            
            if col_f3.button("Sincronizar Rango en Segundo Plano", use_container_width=True, key="btn_iniciar_rango", disabled=btn_bloqueado):
                if SYNC_AVAILABLE:
                    GLOBAL_SYNC_STATE["status"] = "running"
                    GLOBAL_SYNC_STATE["processed"] = 0
                    GLOBAL_SYNC_STATE["log"] = ""
                    GLOBAL_SYNC_STATE["error"] = None

                    def tarea_sync_paralela(f_inicio, f_final):
                        try:
                            def cb_progreso(proc, tot):
                                GLOBAL_SYNC_STATE["processed"] = proc

                            tot_f = sincronizar_intercom(fecha_desde=f_inicio, fecha_hasta=f_final, progress_callback=cb_progreso)
                            GLOBAL_SYNC_STATE["status"] = "completed"
                            GLOBAL_SYNC_STATE["log"] = f"Se actualizaron {tot_f} registros para el rango {f_inicio} a {f_final} a las {datetime.now().strftime('%H:%M:%S')}."
                        except Exception as ex_thread:
                            GLOBAL_SYNC_STATE["status"] = "error"
                            GLOBAL_SYNC_STATE["error"] = str(ex_thread)

                    hilo_sync = threading.Thread(target=tarea_sync_paralela, args=(f_sync_desde, f_sync_hasta), daemon=True)
                    hilo_sync.start()
                    st.rerun()
                else:
                    st.error("No se encontró el módulo `sync_intercom.py` en el proyecto.")
                    
        st.markdown("<br>", unsafe_allow_html=True)

        # Tarjeta 2: Parámetros Globales
        with st.container():
            st.markdown("""
            <div class="admin-card">
                <h4 style="margin-top:0; color:#38bdf8;">2. Parametros Globales del Dashboard</h4>
                <p style="color:#94a3b8; font-size:0.88rem; margin-bottom:15px;">Ajusta los tiempos de refresco en vivo, alertas y límites objetivo para los SLA de atención.</p>
            </div>
            """, unsafe_allow_html=True)
            
            col_cfg1, col_cfg2, col_cfg3 = st.columns(3)
            
            with col_cfg1:
                st.markdown("<b>Refresco Automatico</b>", unsafe_allow_html=True)
                cfg_auto = st.checkbox("Activar Autorefresh por defecto", value=st.session_state["auto_refresh"])
                cfg_interval = st.number_input("Intervalo predeterminado (segundos):", min_value=3, max_value=60, value=st.session_state["refresh_interval"])
            
            with col_cfg2:
                st.markdown("<b>Umbrales de SLA (Minutos)</b>", unsafe_allow_html=True)
                cfg_sla_1ra = st.number_input("SLA Primera Respuesta (min):", min_value=0.5, max_value=30.0, value=float(st.session_state["sla_1ra_th"]), step=0.5)
                cfg_sla_gest = st.number_input("SLA Tiempo de Gestion (min):", min_value=5.0, max_value=480.0, value=float(st.session_state["sla_gest_th"]), step=5.0)

            with col_cfg3:
                st.markdown("<b>Alerta de Chat Nuevo</b>", unsafe_allow_html=True)
                cfg_alerta_nuevo = st.number_input("Disparar Alerta tras (min sin responder):", min_value=0.5, max_value=60.0, value=float(st.session_state["alerta_nuevo_th"]), step=0.5)

            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Guardar Configuración de Parámetros", use_container_width=True):
                st.session_state["auto_refresh"] = cfg_auto
                st.session_state["refresh_interval"] = cfg_interval
                st.session_state["sla_1ra_th"] = cfg_sla_1ra
                st.session_state["sla_gest_th"] = cfg_sla_gest
                st.session_state["alerta_nuevo_th"] = cfg_alerta_nuevo
                st.success("Configuración actualizada correctamente.")
                st.rerun()

        st.markdown("<br>", unsafe_allow_html=True)

        # Tarjeta 3: Gestión de Usuarios Autorizados
        with st.container():
            st.markdown("""
            <div class="admin-card">
                <h4 style="margin-top:0; color:#38bdf8;">3. Gestion de Usuarios Autorizados</h4>
                <p style="color:#94a3b8; font-size:0.88rem; margin-bottom:15px;">Administra los correos y contraseñas que tienen permitido acceder a este Dashboard.</p>
            </div>
            """, unsafe_allow_html=True)

            with st.expander("➕ Crear Nuevo Usuario Autorizado", expanded=False):
                with st.form("form_nuevo_usuario"):
                    col_u1, col_u2, col_u3 = st.columns(3)
                    n_nombre = col_u1.text_input("Nombre / Agente:")
                    n_email = col_u2.text_input("Correo Electronico:")
                    n_pass = col_u3.text_input("Contraseña de Acceso:")
                    btn_crear_u = st.form_submit_button("Guardar Usuario", use_container_width=True)

                    if btn_crear_u:
                        if n_email.strip() and n_pass.strip() and n_nombre.strip():
                            try:
                                supabase.table("usuarios_autorizados").insert({
                                    "email": n_email.strip().lower(),
                                    "password": n_pass.strip(),
                                    "nombre": n_nombre.strip(),
                                    "activo": True
                                }).execute()
                                st.success(f"Usuario {n_email} creado exitosamente.")
                                st.rerun()
                            except Exception as ex:
                                st.error(f"Error al registrar usuario: {str(ex)}")
                        else:
                            st.warning("Completa todos los campos obligatorios.")

            try:
                res_users = supabase.table("usuarios_autorizados").select("*").order("created_at", desc=True).execute()
                df_users = pd.DataFrame(res_users.data)
                
                if not df_users.empty:
                    st.markdown("<b>Listado de Usuarios Registrados:</b>", unsafe_allow_html=True)
                    st.dataframe(
                        df_users[["id", "nombre", "email", "password", "activo", "created_at"]],
                        column_config={
                            "id": "ID",
                            "nombre": "Nombre",
                            "email": "Correo",
                            "password": "Contraseña",
                            "activo": st.column_config.CheckboxColumn("Acceso Activo"),
                            "created_at": "Fecha Alta"
                        },
                        hide_index=True,
                        use_container_width=True
                    )

                    col_edit1, col_edit2 = st.columns(2)
                    id_toggle = col_edit1.number_input("ID de usuario a activar/desactivar:", min_value=1, step=1)
                    if col_edit2.button("Alternar Estado (Activo/Inactivo)", use_container_width=True):
                        usr_actual = df_users[df_users["id"] == id_toggle]
                        if not usr_actual.empty:
                            nuevo_estado = not bool(usr_actual.iloc[0]["activo"])
                            supabase.table("usuarios_autorizados").update({"activo": nuevo_estado}).eq("id", id_toggle).execute()
                            st.success(f"Estado del usuario ID {id_toggle} actualizado.")
                            st.rerun()
                        else:
                            st.error("ID de usuario no encontrado.")
                else:
                    st.info("No hay usuarios registrados aún en la base de datos.")
            except Exception as e:
                st.error(f"No se pudo cargar la tabla de usuarios: {str(e)}")

with tab_faq:
    st.markdown("### Preguntas Frecuentes & Criterios Operativos")
    st.caption("Guía detallada sobre las reglas de negocio, horarios de atención, medición de SLA y evaluación del CSAT.")

    with st.expander("1. ¿Cómo se evalúa el SLA en la Pantalla (Dashboard En Vivo)?", expanded=True):
        st.markdown("""
        * **Chats Evaluados:** Se incluyen solo los chats asignados a agentes humanos (`por_agente == 'no excluido'`) y que hayan sido creados dentro de la jornada operativa (`horario_evaluado != 'fuera de horario'`).
        * **Promedios de Respuesta y Gestión:** Se calcula la media numérica (`mean`) en minutos de la **Primera Respuesta** y del **Tiempo de Resolución/Gestión** de los chats válidos del rango de fechas consultado.
        * **Porcentaje de Cumplimiento:** Es la proporción de conversaciones donde el tiempo de respuesta o gestión fue menor o igual al umbral objetivo configurado (ejemplo: $\le 2.0$ min para 1ra respuesta y $\le 60.0$ min para gestión).
        """)

    with st.expander("2. ¿Cuáles son los Horarios y Criterios de Exclusión para la medición del SLA?", expanded=False):
        st.markdown("""
        #### 📅 Días Feriados
        Las conversaciones creadas en días feriados oficiales se marcan automáticamente como **`excluido`**.
        
        #### 🕒 Horario Hábil Normal
        * **Lunes a Viernes:** 08:00 a 18:00 hs.
        * **Sábados:** 09:00 a 12:00 hs.

        #### 🌙 Horario Extendido (Turno Noche / Fin de Semana)
        * **Lunes a Miércoles:** 19:00 a 02:00 hs (del día siguiente)
        * **Jueves a Domingo:** 18:00 a 03:00 hs (del día siguiente)

        #### 🚫 Exclusiones Generales
        * Chats asignados únicamente a Bots (`Monica (Bot)`, etc.).
        * Conversaciones del canal de Correo Electrónico (`email`).
        """)

    with st.expander("3. ¿Cómo se miden el SLA Normal, SLA Extendido y SLA Gestión en el reporte de Excel?", expanded=False):
        st.markdown("""
        En el archivo Excel descargable, cada conversación contiene tres columnas de evaluación independiente:

        * **SLA Normal:** 
          * **Cumple:** Creado en Día Laboral, Horario Normal, con agente asignado, y cuya primera respuesta tomó $\le$ al límite ($2.0$ min).
          * **No Cumple:** Creado en Día Laboral, Horario Normal pero la primera respuesta superó el tiempo límite.
          * **Excluído** Creado fuera de los días laborales o en feriado, o fuera del Horario Normal, o atendido exclusivamente por el bot.

        * **SLA Extendido:** 
          * **Cumple:** Creado dentro de la franja de Horario Extendido, con agente asignado, y con primera respuesta $\le$ al límite ($2.0$ min).
          * **No Cumple:** Creado en Horario Extendido pero la primera respuesta superó el límite de tiempo.
          * **Excluido por filtro:** Creado fuera del Horario Extendido.

        * **SLA Tiempo Gestión:** 
          * **Cumple:** Creado en horario hábil, sin la etiqueta *"Sin Respuesta"* y resuelto/cerrado en un tiempo $\le$ al umbral ($60$ min).
          * **No Cumple:** Creado en horario hábil pero su tiempo de resolución superó el umbral.
          * **Sin cerrar:** Conversación aún abierta pendiente de resolución.
          * **Excluido por filtro:** Conversación que contiene la etiqueta *"sin respuesta"*, creada en feriado o fuera de horario hábil.
        """)

    with st.expander("4. ¿Cómo se calcula y agrupa la métrica CSAT (Satisfacción del Cliente)?", expanded=False):
        st.markdown("""
        * **Origen de la Fecha:** Las métricas, gráficos y tablas de CSAT se alimentan de la **marca de tiempo exacta en que el cliente calificó la atención** (`fecha_calificacion`).
        * **Calificaciones Válidas:** Se consideran las puntuaciones numéricas entre **1 y 5 estrellas** de conversaciones atendidas por agentes humanos.
        * **Fórmula de CSAT:**
          $$\\text{CSAT (\\%)} = \\left( \\frac{\\text{Total de Calificaciones Positivas (4 y 5 estrellas)}}{\\text{Total de Encuestas Validadas}} \\right) \\times 100$$
        """)
