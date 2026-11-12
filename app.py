import os
import io
import requests
import time as time_lib
import threading
from datetime import datetime, timedelta, time, date, timezone
import numpy as np
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

    /* Los iframes se utilizan únicamente como ejecutores invisibles de JavaScript.
       Ocultamos su contenedor para evitar que aparezca una pequeña línea o guion
       en la interfaz, sin alterar los modales que insertan en el documento principal. */
    [data-testid="stIFrame"],
    [data-testid="stIFrame"] iframe,
    iframe[title="st.iframe"] {
        display: none !important;
        visibility: hidden !important;
        width: 0 !important;
        height: 0 !important;
        min-height: 0 !important;
        border: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
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

# URL de tu webhook FastAPI de Python desplegado en Render
NOTIFICADOR_API_URL = os.environ.get("NOTIFICADOR_API_URL", "https://dashboard-soporte-bims-ctcu.onrender.com")
CRON_SECRET_TOKEN = os.environ.get("CRON_SECRET_TOKEN", "bims_secret_cron_123")

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

@st.cache_data(ttl=600, show_spinner=False)
def validar_token_url_rapido(token_limpio):
    try:
        res = supabase.table("usuarios_autorizados")\
            .select("email, nombre")\
            .eq("email", token_limpio)\
            .eq("activo", True)\
            .limit(1)\
            .execute()
        return res.data[0] if (res.data and len(res.data) > 0) else None
    except Exception:
        return None

token_url = st.query_params.get("session_token", None)
if token_url and not st.session_state["user_authenticated"]:
    t_clean = str(token_url).strip().lower()
    user_info = validar_token_url_rapido(t_clean)
    if user_info:
        st.session_state["user_authenticated"] = True
        st.session_state["user_email"] = user_info.get("email")
        st.session_state["user_name"] = user_info.get("nombre")

# 2. Script JS de auto-restauración vía LocalStorage seguro (sin recarga infinita)
st.html("""
<script>
    (function() {
        try {
            const storedUser = localStorage.getItem('bims_user_session');
            const urlParams = new URLSearchParams(window.location.search);
            
            // Solo auto-restaura si hay un usuario válido y no venimos de un logout explícito
            if (storedUser && !urlParams.has('session_token') && !sessionStorage.getItem('bims_just_logged_out')) {
                urlParams.set('session_token', storedUser);
                window.location.search = urlParams.toString();
            }
        } catch(e) {}
    })();
</script>
""", unsafe_allow_javascript=True)

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

@st.cache_data(ttl=300)
def obtener_permisos_usuario(identificador_usuario):
    """
    Consulta en Supabase las banderas de permisos del usuario.
    identificador_usuario puede ser el email o el nombre según tu autenticación.
    """
    if not identificador_usuario:
        return {"acceso_wbr": False, "acceso_admin": False}
    try:
        res = (
            supabase.table("usuarios_autorizados")
            .select("acceso_wbr, acceso_admin")
            .or_(f"email.eq.{identificador_usuario},nombre.eq.{identificador_usuario}")
            .limit(1)
            .execute()
        )
        if res.data:
            return {
                "acceso_wbr": bool(res.data[0].get("acceso_wbr", False)),
                "acceso_admin": bool(res.data[0].get("acceso_admin", False))
            }
    except Exception as e:
        print(f"Error consultando permisos: {e}")
    return {"acceso_wbr": False, "acceso_admin": False}

# ==========================================
# PANTALLA DE LOGIN CON MODAL INSTANTÁNEO (ANTI-DEFORMACIÓN AL DAR ENTER)
# ==========================================
if not st.session_state["user_authenticated"]:
    st.html("""
    <style>
        .stApp { 
            background-color: #0b1120 !important; 
            color: #f8fafc; 
        }

        header[data-testid="stHeader"] {
            background-color: #0b1120 !important;
        }

        [data-testid="stSidebar"] {
            display: none !important;
        }

        .stAppDeployButton, 
        header[data-testid="stHeader"] [data-testid="stToolbarActions"],
        #MainMenu, 
        footer {
            display: none !important;
            visibility: hidden !important;
        }

        /* Centrado natural superior */
        div[data-testid="stAppViewBlockContainer"] {
            padding-top: 4.5rem !important;
            padding-bottom: 2rem !important;
        }

        /* Formulario con dimensiones y posición física fija */
        div[data-testid="stForm"] {
            width: 100% !important;
            max-width: 440px !important;
            margin: 0 auto !important;
            background: linear-gradient(145deg, #1e293b 0%, #0f172a 100%) !important;
            border: 1px solid #334155 !important;
            border-radius: 16px !important;
            padding: 28px 30px 32px 30px !important;
            box-shadow: 0 20px 30px -8px rgba(0, 0, 0, 0.6) !important;
        }

        .login-logo-circle {
            width: 56px;
            height: 56px;
            margin: 0 auto 12px auto;
            border-radius: 50%;
            background: rgba(56, 189, 248, 0.12);
            border: 1px solid rgba(56, 189, 248, 0.3);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.7rem;
        }

        .login-title-txt {
            color: #f8fafc;
            font-size: 1.4rem;
            font-weight: 700;
            text-align: center;
            letter-spacing: -0.3px;
            margin-bottom: 4px;
        }

        .login-sub-txt {
            color: #94a3b8;
            font-size: 0.85rem;
            text-align: center;
            margin-bottom: 22px;
        }

        div[data-testid="stTextInput"] input {
            background-color: #0b1120 !important;
            color: #f8fafc !important;
            border: 1px solid #334155 !important;
            border-radius: 8px !important;
            padding: 10px 14px !important;
        }

        div[data-testid="stTextInput"] input:focus {
            border-color: #38bdf8 !important;
            box-shadow: 0 0 0 1px #38bdf8 !important;
        }

        div[data-testid="stInputInstructions"] {
            display: none !important;
        }

        .stButton>button { 
            background: #0284c7 !important; 
            color: white !important; 
            font-weight: 700 !important; 
            border-radius: 8px !important; 
            border: none !important;
            height: 44px !important;
            margin-top: 12px !important;
            box-shadow: 0 4px 12px rgba(2, 132, 199, 0.35) !important;
            transition: all 0.2s ease !important;
        }

        .stButton>button:hover {
            background: #0369a1 !important;
            transform: translateY(-1px);
        }

    </style>

    """)

    # El listener global se ejecuta en un iframe same-origin para poder
    # escuchar los botones reales de Streamlit y superponer el modal al documento.
    st.iframe("""
<script>
    (function() {
        const doc = window.parent.document;
        if (doc.__bimsLoginListenerInstalled) return;
        doc.__bimsLoginListenerInstalled = true;

        function showLoginOverlay() {
            let overlay = doc.getElementById('bims-login-loading-overlay');
            if (!overlay) {
                overlay = doc.createElement('div');
                overlay.id = 'bims-login-loading-overlay';
                overlay.innerHTML = `
                    <div class="bims-login-load-box">
                        <div class="bims-login-spinner"></div>
                        <div class="bims-login-title">Iniciando sesión...</div>
                        <div class="bims-login-subtitle">Validando tus credenciales</div>
                    </div>`;
                doc.body.appendChild(overlay);
            }
            overlay.style.setProperty('display', 'flex', 'important');
            overlay.style.setProperty('z-index', '2147483647', 'important');
        }

        doc.addEventListener('click', function(event) {
            const button = event.target.closest('button');
            if (button && button.innerText && button.innerText.trim().includes('Iniciar Sesión')) {
                showLoginOverlay();
            }
        }, true);
        doc.addEventListener('submit', function(event) {
            if (event.target.closest('[data-testid="stForm"]')) showLoginOverlay();
        }, true);

        const styleId = 'bims-login-overlay-style';
        if (!doc.getElementById(styleId)) {
            const style = doc.createElement('style');
            style.id = styleId;
            style.textContent = `
                #bims-login-loading-overlay { position:fixed; inset:0; z-index:2147483647; display:none;
                    align-items:center; justify-content:center; background:rgba(11,17,32,.94);
                    backdrop-filter:blur(9px); font-family:Arial,sans-serif; }
                #bims-login-loading-overlay .bims-login-load-box { display:flex; flex-direction:column;
                    align-items:center; gap:13px; padding:30px 38px; border-radius:18px;
                    background:linear-gradient(145deg,#1e293b,#0f172a); border:1px solid #334155;
                    box-shadow:0 20px 45px rgba(0,0,0,.45); text-align:center; }
                #bims-login-loading-overlay .bims-login-spinner { width:38px;height:38px;border-radius:50%;
                    border:3px solid rgba(56,189,248,.2);border-top-color:#38bdf8;
                    animation:bimsLoginSpin .75s linear infinite; }
                #bims-login-loading-overlay .bims-login-title {color:#f8fafc;font-weight:700;font-size:17px;}
                #bims-login-loading-overlay .bims-login-subtitle {color:#94a3b8;font-size:13px;}
                @keyframes bimsLoginSpin {to{transform:rotate(360deg);}}
            `;
            doc.head.appendChild(style);
        }
    })();
    </script>
    """, width=1, height=1)

    col_izq, col_centro, col_der = st.columns([1, 1.15, 1])

    with col_centro:
        with st.form("form_login_global", clear_on_submit=False):
            st.markdown("""
            <div class="login-logo-circle">📈</div>
            <div class="login-title-txt">Dashboard Soporte BIMS</div>
            <div class="login-sub-txt">Ingresa tus credenciales autorizadas</div>
            """, unsafe_allow_html=True)

            input_user_email = st.text_input("Correo Electrónico", placeholder="usuario@bims.com")
            input_user_pass = st.text_input("Contraseña", type="password", placeholder="••••••••")
            
            btn_login_user = st.form_submit_button("Iniciar Sesión", use_container_width=True)

        login_status = st.empty()

        if btn_login_user:
            if not input_user_email.strip() or not input_user_pass.strip():
                st.iframe("""
                <script>
                    const modal = window.parent.document.getElementById('bims-login-loading-overlay');
                    if (modal) modal.style.display = 'none';
                </script>
                """, width=1, height=1)
                login_status.warning("Completa tu correo y contraseña.")
            else:
                # El spinner nativo de Streamlit se gestiona automáticamente al terminar
                # la consulta, tanto si las credenciales son correctas como si no.
                with st.spinner("Validando tus credenciales..."):
                    valido, datos_user = verificar_credenciales_supabase(
                        input_user_email,
                        input_user_pass
                    )

                if valido:
                    email_user = datos_user.get("email")
                    st.session_state["user_authenticated"] = True
                    st.session_state["user_email"] = email_user
                    st.session_state["user_name"] = datos_user.get("nombre")

                    st.query_params["session_token"] = email_user
                    st.html(f"""
                    <script>
                        localStorage.setItem('bims_user_session', {email_user!r});
                        sessionStorage.removeItem('bims_just_logged_out');
                    </script>
                    """, unsafe_allow_javascript=True)

                    st.rerun()
                else:
                    # Si falló la contraseña, ocultamos el overlay para permitir reintentar
                    st.iframe("""
                    <script>
                        const modal = window.parent.document.getElementById('bims-login-loading-overlay');
                        if (modal) modal.style.display = 'none';
                    </script>
                    """, width=1, height=1)
                    login_status.error("Credenciales incorrectas o usuario inactivo.")

    # Al volver del cierre de sesión, retirar el overlay global cuando el login ya está renderizado.
    st.iframe("""
    <script>
        const doc = window.parent.document;
        window.setTimeout(() => {
            const logoutOverlay = doc.getElementById('bims-logout-loading-overlay');
            if (logoutOverlay) logoutOverlay.remove();
        }, 700);
    </script>
    """, width=1, height=1)
    st.stop()
    
# ==========================================
# FUNCIONES AUXILIARES DE TIEMPOS Y REGLAS DE NEGOCIO
# ==========================================

# ==========================================
# MAPA EN TIEMPO REAL DE PRESENCIA DE AGENTES
# ==========================================
@st.cache_data(ttl=30, show_spinner=False)
def obtener_mapa_estados_agentes():
    """Recupera la presencia de cada agente guardada en la tabla estado_agentes."""
    try:
        res = supabase.table("estado_agentes").select("nombre, away").execute()
        return {
            r["nombre"].strip().lower(): ("🟡 Ausente" if r.get("away") else "🟢 Disponible")
            for r in (res.data or [])
        }
    except Exception:
        return {}

# ==========================================
# CONSULTA GLOBAL CACHEADA DE CARGA (CORREGIDA PARA MAYÚSCULAS/MINÚSCULAS)
# ==========================================
@st.cache_data(ttl=30, show_spinner=False)
def obtener_cargas_agentes_db(hoy_inicio_utc):
    """
    Recupera chats abiertos y cerrados hoy manejando variantes de mayúsculas/minúsculas.
    """
    estados_cerrados_list = [
        "cerrado", "closed", "resolved", "resuelto", "snoozed",
        "Cerrado", "Closed", "Resolved", "Resuelto", "Snoozed"
    ]

    # 1. Chats abiertos actualmente
    res_abiertos = supabase.table("conversaciones")\
        .select("id, agente_asignado, etiquetas, estado, created_at, updated_at, fecha_cierre")\
        .not_.in_("estado", estados_cerrados_list)\
        .execute()

    # 2. Chats cerrados hoy (donde tanto el cierre como la asignación/creación sean de hoy)
    res_cerrados_hoy = supabase.table("conversaciones")\
        .select("id, agente_asignado, etiquetas, estado, created_at, updated_at, fecha_cierre")\
        .in_("estado", estados_cerrados_list)\
        .gte("fecha_cierre", hoy_inicio_utc)\
        .gte("created_at", hoy_inicio_utc)\
        .execute()

    # 3. Respaldo para chats cerrados cuya fecha_cierre sea NULL
    res_cerrados_respaldo = supabase.table("conversaciones")\
        .select("id, agente_asignado, etiquetas, estado, created_at, updated_at, fecha_cierre")\
        .in_("estado", estados_cerrados_list)\
        .is_("fecha_cierre", "null")\
        .gte("updated_at", hoy_inicio_utc)\
        .gte("created_at", hoy_inicio_utc)\
        .execute()

    todos = (res_abiertos.data or []) + (res_cerrados_hoy.data or []) + (res_cerrados_respaldo.data or [])
    
    unicos = {r["id"]: r for r in todos}
    return list(unicos.values())

# ==========================================
# FRAGMENTO DE CARGA DE AGENTES EN VIVO
# ==========================================
@st.fragment(run_every=60)
def renderizar_panel_carga_asignador():
    tz_py = timezone(timedelta(hours=-3))
    now_dt = datetime.now(tz_py)

    # Validar jornada laboral (Lunes a Viernes de 08:00 a 18:00 hs PY)
    fecha_str = now_dt.strftime("%Y-%m-%d")
    dia_semana = now_dt.weekday()
    hora_actual = now_dt.time()

    if (
        fecha_str in FERIADOS
        or dia_semana not in [0, 1, 2, 3, 4]
        or not (time(8, 0, 0) <= hora_actual <= time(18, 0, 0))
    ):
        st.info(f"⏸️ **Panel pausado fuera de jornada laboral.** Horario operativo: L-V de 08:00 a 18:00 hs (PY). Hora actual: {now_dt.strftime('%H:%M:%S')} hs.")
        return

    try:
        hoy_local_dt = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        hoy_inicio_utc = hoy_local_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        data_cargas = obtener_cargas_agentes_db(hoy_inicio_utc)
        mapa_estados = obtener_mapa_estados_agentes()

        # Lista de agentes/entidades a EXCLUIR explícitamente
        agentes_omitir = [
            "monica", "mónica", "monica (bot)", "soporte", 
            "gerardo gaona", "gerardo.gaona@itti.digital", "soporte bims", "laura.almiron@tivahq.com", "laura almirón", "rebeca cartes",
            "none", "nan", "sin asignar", ""
        ]

        # 1. Obtener lista maestra de agentes desde la tabla usuarios_autorizados
        lista_agentes_maestra = set()
        try:
            res_u = supabase.table("usuarios_autorizados").select("nombre").eq("activo", True).execute()
            for u in (res_u.data or []):
                nombre_u = u.get("nombre", "").strip()
                if nombre_u and nombre_u.lower() not in agentes_omitir:
                    lista_agentes_maestra.add(nombre_u)
        except Exception:
            pass

        df_c = pd.DataFrame(data_cargas) if data_cargas else pd.DataFrame()

        # Garantizar presencia de columnas en df_c
        for col in ["id", "agente_asignado", "estado", "fecha_cierre", "updated_at", "created_at"]:
            if col not in df_c.columns:
                df_c[col] = None

        if not df_c.empty:
            # Fecha de cierre / resolución en hora local
            df_c["fecha_ref"] = df_c["fecha_cierre"].fillna(df_c["updated_at"])
            fecha_ref_dt = pd.to_datetime(df_c["fecha_ref"], format="ISO8601", errors="coerce", utc=True)
            df_c["fecha_ref_py"] = fecha_ref_dt.dt.tz_convert("America/Asuncion")
            df_c["fecha_solo_py"] = df_c["fecha_ref_py"].dt.date

            # Fecha de creación / asignación en hora local
            created_dt = pd.to_datetime(df_c["created_at"], format="ISO8601", errors="coerce", utc=True)
            df_c["created_py"] = created_dt.dt.tz_convert("America/Asuncion")
            df_c["created_solo_py"] = df_c["created_py"].dt.date

            def limpiar_agente_carga(r):
                ag_orig = str(r.get("agente_asignado", "")).strip()
                if ag_orig.lower() in ["soporte bims", "soporte", "sin asignar", "", "none", "nan"]:
                    ag_tag = extraer_agente_desde_etiquetas_str(r.get("etiquetas"))
                    if ag_tag:
                        return ag_tag
                return ag_orig if ag_orig else "Sin asignar"
            
            df_c["agente_clean"] = df_c.apply(limpiar_agente_carga, axis=1)
            
            # Incorporar cualquier otro agente humano presente en las conversaciones de hoy
            for ag in df_c["agente_clean"].unique():
                if ag.lower() not in agentes_omitir:
                    lista_agentes_maestra.add(ag)

            estados_cerrados = ["cerrado", "closed", "resolved", "resuelto", "snoozed"]
            df_c["es_cerrado"] = df_c["estado"].fillna("").astype(str).str.strip().str.lower().isin(estados_cerrados)

            hoy_date_py = now_dt.date()

            # Solo cuenta como cerrado hoy si FUE CERRADO HOY y ADEMÁS FUE ASIGNADO/CREADO HOY
            df_c["es_cerrado_hoy"] = (
                df_c["es_cerrado"] 
                & (df_c["fecha_solo_py"] == hoy_date_py) 
                & (df_c["created_solo_py"] == hoy_date_py)
            )
            df_c["es_abierto_actual"] = ~df_c["es_cerrado"]

        # 2. Agrupamiento por agente humano
        resumen = []
        for agente in sorted(list(lista_agentes_maestra)):
            if agente.lower() in agentes_omitir:
                continue

            if not df_c.empty and "agente_clean" in df_c.columns:
                grp = df_c[df_c["agente_clean"] == agente]
                pendientes = int(grp["es_abierto_actual"].sum()) if not grp.empty else 0
                cerrados_hoy = int(grp["es_cerrado_hoy"].sum()) if not grp.empty else 0
            else:
                pendientes = 0
                cerrados_hoy = 0

            total_asignados = pendientes + cerrados_hoy

            # Presencia actual con coincidencia flexible por nombre
            estado_presencia = "🟢 Disponible"
            agente_lower = agente.lower()
            for k_nombre, v_estado in mapa_estados.items():
                if k_nombre in agente_lower or agente_lower in k_nombre:
                    estado_presencia = v_estado
                    break

            etiqueta_grafico = f"{'🟢' if 'Disponible' in estado_presencia else '🟡'} {agente}"

            resumen.append({
                "Estado": estado_presencia,
                "Agente de Soporte": agente,
                "Agente Grafico": etiqueta_grafico,
                "Total Asignados Hoy 📋": total_asignados,
                "Cerrados (Hoy) 🟢": cerrados_hoy,
                "Pendientes (Abiertos) 🔴": pendientes
            })

        df_resumen = pd.DataFrame(resumen)

        if not df_resumen.empty:
            df_resumen = df_resumen.sort_values(by="Total Asignados Hoy 📋", ascending=False)

            col1, col2, col3 = st.columns(3)
            col1.metric("Total Chats Pendientes", df_resumen["Pendientes (Abiertos) 🔴"].sum())
            col2.metric("Total Cerrados Hoy", df_resumen["Cerrados (Hoy) 🟢"].sum())
            col3.metric("Última actualización", now_dt.strftime("%H:%M:%S hs"))

            st.markdown("<br>", unsafe_allow_html=True)

            # ==========================================
            # GRÁFICO DE BARRAS VERTICAL ACUMULADO
            # ==========================================
            fig_carga = go.Figure()

            fig_carga.add_trace(go.Bar(
                x=df_resumen["Agente Grafico"],
                y=df_resumen["Cerrados (Hoy) 🟢"],
                name="Cerrados Hoy",
                marker=dict(
                    color="#10b981",
                    cornerradius=8
                ),
                text=df_resumen["Cerrados (Hoy) 🟢"].apply(lambda v: str(v) if v > 0 else ""),
                textposition="inside",
                insidetextanchor="middle",
                hovertemplate="<b>%{x}</b><br>Cerrados Hoy: %{y}<extra></extra>"
            ))

            fig_carga.add_trace(go.Bar(
                x=df_resumen["Agente Grafico"],
                y=df_resumen["Pendientes (Abiertos) 🔴"],
                name="Abiertos Pendientes",
                marker=dict(
                    color="#f97316",
                    cornerradius=8
                ),
                text=df_resumen["Pendientes (Abiertos) 🔴"].apply(lambda v: str(v) if v > 0 else ""),
                textposition="inside",
                insidetextanchor="middle",
                hovertemplate="<b>%{x}</b><br>Abiertos Pendientes: %{y}<extra></extra>"
            ))

            for idx, row in df_resumen.iterrows():
                fig_carga.add_annotation(
                    x=row["Agente Grafico"],
                    y=row["Total Asignados Hoy 📋"],
                    text=f"<b>{row['Total Asignados Hoy 📋']}</b>",
                    showarrow=False,
                    yshift=12,
                    font=dict(color="#f8fafc", size=13)
                )

            fig_carga.update_layout(
                barmode="stack",
                title="<b>Distribución y Carga Actual del Equipo (Apilado)</b>",
                paper_bgcolor="#1e293b",
                plot_bgcolor="#1e293b",
                font=dict(color="#cbd5e1", size=12),
                xaxis=dict(gridcolor="#334155", title=None),
                yaxis=dict(gridcolor="#334155", title="Cantidad de Chats", showgrid=False, showticklabels=False, dtick=1),
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="right",
                    x=1
                ),
                margin=dict(t=60, b=30, l=40, r=40),
                height=380
            )

            st.plotly_chart(fig_carga, use_container_width=True)

            st.markdown("<br>", unsafe_allow_html=True)

            cols_tabla = ["Estado", "Agente de Soporte", "Total Asignados Hoy 📋", "Cerrados (Hoy) 🟢", "Pendientes (Abiertos) 🔴"]

            st.dataframe(
                df_resumen[cols_tabla],
                column_config={
                    "Estado": st.column_config.TextColumn("Presencia", help="Disponibilidad en Intercom"),
                    "Agente de Soporte": st.column_config.TextColumn("Agente", help="Nombre del agente"),
                    "Total Asignados Hoy 📋": st.column_config.NumberColumn("Total Asignados Hoy", help="Total gestionados durante la jornada"),
                    "Cerrados (Hoy) 🟢": st.column_config.NumberColumn("Cerrados Hoy", help="Total resueltos durante el día de hoy"),
                    "Pendientes (Abiertos) 🔴": st.column_config.NumberColumn("Abiertos Actuales", help="Chats que tiene asignados pendientes en este momento")
                },
                hide_index=True,
                use_container_width=True
            )
        else:
            st.info("No hay asignaciones registradas para agentes humanos el día de hoy.")

    except Exception as e:
        st.error(f"Error consultando carga en tiempo real: {e}")

def obtener_fecha_local_hoy():
    """Retorna la fecha actual exacta en Paraguay (UTC-3)."""
    tz_py = timezone(timedelta(hours=-3))
    return datetime.now(tz_py).date()

def obtener_tiempo_transcurrido(fecha_dt):
    """Calcula el tiempo transcurrido exacto garantizando la conversión a America/Asuncion."""
    if pd.isna(fecha_dt) or fecha_dt is None:
        return "Sin registros"
    
    # Aseguramos hora actual en Paraguay
    now_local = pd.Timestamp.now(tz="America/Asuncion")
    
    # Parseo seguro de la fecha recibida
    fecha_dt = pd.to_datetime(fecha_dt, errors="coerce")
    if fecha_dt.tzinfo is None:
        fecha_dt = fecha_dt.tz_localize("UTC").tz_convert("America/Asuncion")
    else:
        fecha_dt = fecha_dt.tz_convert("America/Asuncion")
    
    diff = now_local - fecha_dt
    secs = int(diff.total_seconds())
    
    # Evitamos tiempos negativos por pequeños desajustes de reloj
    if secs < 0:
        return "hace un momento"
    elif secs < 60:
        return "hace un momento"
    elif secs < 3600:
        return f"hace {secs // 60} min"
    elif secs < 86400:
        return f"hace {secs // 3600} h"
    else:
        return f"hace {secs // 86400} días"

FERIADOS_DEFAULT = [
    "2026-01-01", "2026-03-02", "2026-04-02", "2026-04-03", "2026-05-01",
    "2026-05-14", "2026-05-15", "2026-06-12", "2026-06-22", "2026-06-30",
    "2026-08-15", "2026-09-28", "2026-12-08", "2026-12-25", "2026-12-31"
]

@st.cache_data(ttl=86400, show_spinner=False)
def obtener_feriados_activos():
    """Recupera la lista de feriados (se ejecuta máximo 1 vez al día)."""
    try:
        res = supabase.table("config_notificaciones").select("feriados").eq("id", 1).execute()
        if res.data and res.data[0].get("feriados"):
            return sorted(list(set(res.data[0]["feriados"])))
    except Exception:
        pass
    return sorted(FERIADOS_DEFAULT)

def guardar_feriados_db(lista_feriados):
    """Actualiza los feriados en Supabase y limpia la caché en memoria."""
    try:
        limpios = sorted(list(set(str(d).strip() for d in lista_feriados if str(d).strip())))
        supabase.table("config_notificaciones").update({"feriados": limpios}).eq("id", 1).execute()
        obtener_feriados_activos.clear()
        return True
    except Exception as e:
        st.error(f"Error guardando feriados: {e}")
        return False

FERIADOS = obtener_feriados_activos()

def evaluar_horario_dashboard_dt(dt_series):
    """Evaluación vectorizada sobre la Serie completa de Datetime de Pandas."""
    dias_semana = dt_series.dt.dayofweek
    horas_decimal = dt_series.dt.hour + (dt_series.dt.minute / 60.0)
    fechas_str = dt_series.dt.strftime("%Y-%m-%d")

    es_feriado = fechas_str.isin(FERIADOS)
    es_normal_lv = (dias_semana.isin([0, 1, 2, 3, 4])) & (horas_decimal >= 8.0) & (horas_decimal <= 17.5)
    es_normal_sab = (dias_semana == 5) & (horas_decimal >= 9.0) & (horas_decimal <= 11.75)
    
    es_ext_lj = (dias_semana.isin([0, 1, 2, 3])) & ((horas_decimal >= 19.0) | (horas_decimal <= 2.0))
    es_ext_vs = (dias_semana.isin([4, 5, 6])) & ((horas_decimal >= 18.0) | (horas_decimal <= 3.0))

    condiciones = [
        es_feriado,
        es_normal_lv | es_normal_sab,
        es_ext_lj | es_ext_vs
    ]
    elecciones = ["fuera de horario", "normal", "extendido"]
    return np.select(condiciones, elecciones, default="fuera de horario")

def calcular_minutos_habiles_rapido(dt_inicio, dt_fin):
    """
    Calcula minutos de gestión dentro de las franjas operativas reales.

    Importante: los turnos extendidos que cruzan medianoche se construyen como
    intervalos completos (p. ej. lunes 19:00 -> martes 02:00), evitando contar
    el tramo 00:00-02:00 como si perteneciera al mismo día calendario.
    """
    if pd.isna(dt_inicio) or pd.isna(dt_fin) or dt_fin <= dt_inicio:
        return np.nan

    tz = "America/Asuncion"
    total_minutos_habiles = 0.0

    # Incluimos el día anterior porque un ticket iniciado después de medianoche
    # puede pertenecer a la continuación del turno extendido del día anterior.
    dia_actual = dt_inicio.date() - timedelta(days=1)
    dia_fin = dt_fin.date()

    while dia_actual <= dia_fin:
        fecha_str = dia_actual.strftime("%Y-%m-%d")
        dia_semana = dia_actual.weekday()

        # Si el día que origina el turno es feriado, no computamos ese turno.
        if fecha_str in FERIADOS:
            dia_actual += timedelta(days=1)
            continue

        intervalos = []

        # Turno normal
        if dia_semana in [0, 1, 2, 3, 4]:  # L-V
            intervalos.append((time(8, 0), time(17, 30), False))
        elif dia_semana == 5:  # Sábado
            intervalos.append((time(9, 0), time(11, 45), False))

        # Turno extendido: intervalo real que cruza medianoche.
        if dia_semana in [0, 1, 2]:  # L-M-X: 19:00 -> 02:00 siguiente
            intervalos.append((time(19, 0), time(2, 0), True))
        elif dia_semana in [3, 4, 5, 6]:  # J-V-S-D: 18:00 -> 03:00 siguiente
            intervalos.append((time(18, 0), time(3, 0), True))

        for h_inicio, h_fin, cruza_medianoche in intervalos:
            inicio_franja = pd.Timestamp.combine(dia_actual, h_inicio).tz_localize(tz)
            if cruza_medianoche:
                fin_franja = pd.Timestamp.combine(
                    dia_actual + timedelta(days=1), h_fin
                ).tz_localize(tz)
            else:
                fin_franja = pd.Timestamp.combine(dia_actual, h_fin).tz_localize(tz)

            start_overlap = max(dt_inicio, inicio_franja)
            end_overlap = min(dt_fin, fin_franja)

            if start_overlap < end_overlap:
                total_minutos_habiles += (end_overlap - start_overlap).total_seconds() / 60.0

        dia_actual += timedelta(days=1)

    return round(total_minutos_habiles, 1)

def extraer_agente_desde_etiquetas_str(etiquetas_val):
    """Extrae el nombre del agente si existe etiqueta 'Agente:', 'L2:' o 'agendamiento'."""
    if pd.isna(etiquetas_val) or not str(etiquetas_val).strip():
        return None
    tags = [t.strip() for t in str(etiquetas_val).split(",") if t.strip()]
    ag_l2 = None
    es_agendamiento = False

    for t in tags:
        t_lower = t.lower()
        if t_lower.startswith("agente:"):
            partes = t.split(":", 1)
            if len(partes) > 1 and partes[1].strip():
                return partes[1].strip()
        elif t_lower.startswith("l2:"):
            partes = t.split(":", 1)
            if len(partes) > 1 and partes[1].strip():
                ag_l2 = f"L2 - {partes[1].strip()}"
        elif t_lower == "agendamiento" or "agendamiento" in t_lower:
            es_agendamiento = True

    if ag_l2:
        return ag_l2
    if es_agendamiento:
        return "Agendamiento"
    return None

def procesar_fechas_df(df):
    """Convierte las fechas UTC a hora local (UTC-3) y normaliza métricas VECTORIZADAS."""
    if df.empty or "created_at" not in df.columns:
        return df

    created_dt = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
    local_dt = created_dt.dt.tz_convert("America/Asuncion")

    df["created_at_dt"] = local_dt
    df["created_at_fmt"] = local_dt.dt.strftime("%Y-%m-%d %H:%M").fillna("Sin fecha")
    df["fecha_solo"] = local_dt.dt.date
    df["hora_solo"] = local_dt.dt.time

    # Procesamiento de updated_at para controlar frescura en sidebar
    if "updated_at" in df.columns:
        df["updated_at_dt"] = pd.to_datetime(df["updated_at"], errors="coerce", utc=True).dt.tz_convert("America/Asuncion")
        df["updated_at_local"] = df["updated_at_dt"]
    else:
        df["updated_at_local"] = df["created_at_dt"]

    # Evaluamos la franja de horario vectorizada
    df["horario_evaluado"] = evaluar_horario_dashboard_dt(df["created_at_dt"])

    # Marca de tiempo para CSAT
    if "fecha_calificacion" in df.columns:
        calif_utc = pd.to_datetime(df["fecha_calificacion"], errors="coerce", utc=True)
        local_calif_dt = calif_utc.dt.tz_convert("America/Asuncion")
        df["fecha_calificacion_dt"] = local_calif_dt.fillna(df["created_at_dt"])
    else:
        df["fecha_calificacion_dt"] = df["created_at_dt"]

    df["fecha_calificacion_fmt"] = df["fecha_calificacion_dt"].dt.strftime("%Y-%m-%d %H:%M").fillna("Sin fecha")
    df["fecha_calificacion_solo"] = df["fecha_calificacion_dt"].dt.date

    # Fechas de Cierre
    col_cierre = "fecha_primer_cierre" if "fecha_primer_cierre" in df.columns else "fecha_cierre"
    if col_cierre in df.columns:
        cierre_dt = pd.to_datetime(df[col_cierre], errors="coerce", utc=True)
        local_cierre = cierre_dt.dt.tz_convert("America/Asuncion")
        df["fecha_cierre_dt"] = local_cierre
        df["fecha_cierre_fmt"] = local_cierre.dt.strftime("%Y-%m-%d %H:%M").fillna("")

    if "primera_respuesta_min" in df.columns:
        df["primera_respuesta_min"] = pd.to_numeric(df["primera_respuesta_min"], errors="coerce").round(2)

    # CÁLCULO VECTORIZADO DE TIEMPO DE GESTIÓN
    if "created_at_dt" in df.columns and "fecha_cierre_dt" in df.columns:
        df["tiempo_resolucion_minutos"] = df.apply(
            lambda r: calcular_minutos_habiles_rapido(r["created_at_dt"], r["fecha_cierre_dt"])
            if r.get("horario_evaluado") != "fuera de horario" else np.nan,
            axis=1
        )

    if "id" in df.columns:
        df["id_str"] = df["id"].astype(str).str.strip()
        df["intercom_url"] = df["id_str"].apply(
            lambda x: f"https://app.intercom.io/a/apps/{INTERCOM_APP_ID}/inbox/inbox/all/conversations/{x}"
        )

    # =========================================================================
    # RESOLUCIÓN DE AGENTE POR ETIQUETAS (DESDE 11/11/2026 15:00 HS O USUARIO ÚNICO)
    # =========================================================================
    if "etiquetas" in df.columns and "created_at_dt" in df.columns:
        dt_corte_15hs = pd.Timestamp("2026-11-11 15:00:00", tz="America/Asuncion")
        
        def resolver_agente_hibrido(r):
            ag_orig = str(r.get("agente_asignado", "")).strip()
            ag_lower = ag_orig.lower()
            es_post_corte = pd.notna(r.get("created_at_dt")) and r["created_at_dt"] >= dt_corte_15hs
            es_cuenta_generica = ag_lower in ["soporte bims", "soporte", "sin asignar", "", "none", "nan"]

            if es_post_corte or es_cuenta_generica:
                ag_tag = extraer_agente_desde_etiquetas_str(r.get("etiquetas"))
                if ag_tag:
                    return ag_tag

            return ag_orig if ag_orig else "Sin asignar"

        df["agente_asignado"] = df.apply(resolver_agente_hibrido, axis=1)
        
        # Recalcular exclusión (incluyendo 'soporte bims' como excluido si no tiene etiqueta)
        ag_clean = df["agente_asignado"].str.lower().fillna("")
        df["por_agente"] = np.where(
            ag_clean.isin(["sin asignar", "", "none", "nan", "monica", "mónica", "monica (bot)", "soporte", "soporte bims"]) | ag_clean.str.contains("bot"),
            "excluido",
            "no excluido"
        )

    return df

    created_dt = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
    local_dt = created_dt.dt.tz_convert("America/Asuncion")

    df["created_at_dt"] = local_dt
    df["created_at_fmt"] = local_dt.dt.strftime("%Y-%m-%d %H:%M").fillna("Sin fecha")
    df["fecha_solo"] = local_dt.dt.date
    df["hora_solo"] = local_dt.dt.time

    # Procesamiento de updated_at para controlar frescura en sidebar
    if "updated_at" in df.columns:
        df["updated_at_dt"] = pd.to_datetime(df["updated_at"], errors="coerce", utc=True).dt.tz_convert("America/Asuncion")
        df["updated_at_local"] = df["updated_at_dt"]
    else:
        df["updated_at_local"] = df["created_at_dt"]

    # Evaluamos la franja de horario vectorizada
    df["horario_evaluado"] = evaluar_horario_dashboard_dt(df["created_at_dt"])

    # Marca de tiempo para CSAT
    if "fecha_calificacion" in df.columns:
        calif_utc = pd.to_datetime(df["fecha_calificacion"], errors="coerce", utc=True)
        local_calif_dt = calif_utc.dt.tz_convert("America/Asuncion")
        df["fecha_calificacion_dt"] = local_calif_dt.fillna(df["created_at_dt"])
    else:
        df["fecha_calificacion_dt"] = df["created_at_dt"]

    df["fecha_calificacion_fmt"] = df["fecha_calificacion_dt"].dt.strftime("%Y-%m-%d %H:%M").fillna("Sin fecha")
    df["fecha_calificacion_solo"] = df["fecha_calificacion_dt"].dt.date

    # Fechas de Cierre
    col_cierre = "fecha_primer_cierre" if "fecha_primer_cierre" in df.columns else "fecha_cierre"
    if col_cierre in df.columns:
        cierre_dt = pd.to_datetime(df[col_cierre], errors="coerce", utc=True)
        local_cierre = cierre_dt.dt.tz_convert("America/Asuncion")
        df["fecha_cierre_dt"] = local_cierre
        df["fecha_cierre_fmt"] = local_cierre.dt.strftime("%Y-%m-%d %H:%M").fillna("")

    if "primera_respuesta_min" in df.columns:
        df["primera_respuesta_min"] = pd.to_numeric(df["primera_respuesta_min"], errors="coerce").round(2)

    # CÁLCULO VECTORIZADO ULTRA RÁPIDO DE TIEMPO DE GESTIÓN
    if "created_at_dt" in df.columns and "fecha_cierre_dt" in df.columns:
        # Aplicamos el cálculo de minutos hábiles descontando noches y tiempo muerto
        df["tiempo_resolucion_minutos"] = df.apply(
            lambda r: calcular_minutos_habiles_rapido(r["created_at_dt"], r["fecha_cierre_dt"])
            if r.get("horario_evaluado") != "fuera de horario" else np.nan,
            axis=1
        )

    if "id" in df.columns:
        df["id_str"] = df["id"].astype(str).str.strip()
        df["intercom_url"] = df["id_str"].apply(
            lambda x: f"https://app.intercom.io/a/apps/{INTERCOM_APP_ID}/inbox/inbox/all/conversations/{x}"
        )

    # Normalización del agente por etiquetas para tickets a partir del 12/11/2026
    if "etiquetas" in df.columns and "created_at_dt" in df.columns:
        fecha_cambio = date(2026, 11, 12)
        
        def resolver_agente_hibrido(r):
            ag_orig = str(r.get("agente_asignado", "")).strip()
            if pd.notna(r.get("created_at_dt")) and r["created_at_dt"].date() >= fecha_cambio:
                etiquetas_str = str(r.get("etiquetas", ""))
                tags = [t.strip() for t in etiquetas_str.split(",") if t.strip()]
                
                ag_encontrado = None
                es_agendamiento = False
                
                for t in tags:
                    t_lower = t.lower()
                    if t_lower.startswith("agente:"):
                        partes = t.split(":", 1)
                        if len(partes) > 1 and partes[1].strip():
                            return partes[1].strip()
                    elif t_lower.startswith("l2:"):
                        partes = t.split(":", 1)
                        if len(partes) > 1 and partes[1].strip():
                            ag_encontrado = f"L2 - {partes[1].strip()}"
                    elif t_lower == "agendamiento" or "agendamiento" in t_lower:
                        es_agendamiento = True

                if ag_encontrado:
                    return ag_encontrado
                if es_agendamiento:
                    return "Agendamiento"

            return ag_orig if ag_orig else "Sin asignar"

        df["agente_asignado"] = df.apply(resolver_agente_hibrido, axis=1)
        
        # Recalcular exclusión
        ag_clean = df["agente_asignado"].str.lower().fillna("")
        df["por_agente"] = np.where(
            ag_clean.isin(["sin asignar", "", "none", "nan", "monica", "mónica", "monica (bot)", "soporte"]) | ag_clean.str.contains("bot"),
            "excluido",
            "no excluido"
        )

    return df

COLUMNAS_DASHBOARD = (
    "id, created_at, updated_at, estado, agente_asignado, por_agente, canal, "
    "primera_respuesta_min, tiempo_resolucion_minutos, rating, fecha_calificacion, "
    "feedback, agente_evaluado, tenant, company, nombre_contacto, "
    "etiquetas, fecha_cierre, modulo, cliente, tipo_contacto, nivel, motivo_normalizado, ticket_relacionado, ai_title"
)

@st.cache_data(ttl=86400, show_spinner=False)
def obtener_lista_motivos_csat():
    """Recupera la lista de motivos activos guardados en Supabase."""
    try:
        res = supabase.table("motivos_csat")\
            .select("nombre")\
            .eq("activo", True)\
            .order("nombre")\
            .execute()
        motivos = [r["nombre"] for r in (res.data or [])]
        if "Sin categorizar" in motivos:
            motivos.remove("Sin categorizar")
            motivos.insert(0, "Sin categorizar")
        return motivos if motivos else ["Sin categorizar", "Otro"]
    except Exception:
        return ["Sin categorizar", "Lentitud en Respuesta", "Falta de Solución", "Otro"]

def obtener_inicio_trimestre_anterior():
    """Calcula el primer día del trimestre anterior a la fecha actual."""
    hoy = obtener_fecha_local_hoy()
    q_actual = (hoy.month - 1) // 3 + 1
    
    if q_actual == 1:
        anio = hoy.year - 1
        mes = 10
    else:
        anio = hoy.year
        mes = 3 * (q_actual - 2) + 1
        
    return f"{anio}-{mes:02d}-01T00:00:00Z"


# ==========================================
# CACHÉ DE DATOS EN SUPABASE
# ==========================================
@st.cache_data(ttl=86400, show_spinner=False)
def obtener_datos_historicos_q():
    """Descarga datos históricos desde el Q anterior usando created_at (1 vez al día)."""
    todos_los_datos = []
    lote = 0
    tamanio_lote = 1000

    hoy = obtener_fecha_local_hoy()
    fecha_inicio_q_ant = f"{obtener_inicio_trimestre_anterior()[:10]}T00:00:00Z"
    fecha_hasta_ayer = f"{hoy}T00:00:00Z"

    while True:
        inicio = lote * tamanio_lote
        fin = inicio + tamanio_lote - 1
        
        try:
            response = supabase.table("conversaciones")\
                .select(COLUMNAS_DASHBOARD)\
                .gte("created_at", fecha_inicio_q_ant)\
                .lt("created_at", fecha_hasta_ayer)\
                .range(inicio, fin)\
                .execute()
            
            datos = response.data or []
        except Exception as e:
            st.error(f"Error consultando histórico Supabase: {e}")
            break
        
        if not datos:
            break
            
        todos_los_datos.extend(datos)
        if len(datos) < tamanio_lote:
            break
        lote += 1

    return todos_los_datos


@st.cache_data(ttl=900)
def obtener_timestamp_sync():
    return datetime.now(timezone(timedelta(hours=-3)))

@st.cache_data(ttl=900, show_spinner=False)
def obtener_datos_hoy():
    """Descarga conversaciones desde el 11/11/2026 a las 15:00 hs PY en adelante Y todos los chats abiertos."""
    tz_py = timezone(timedelta(hours=-3))
    
    # Piso ESTÁTICO fijo: 11 de Noviembre de 2026 a las 15:00:00 hs (PY) -> 18:00:00Z en UTC
    piso_fijo_py = datetime(2026, 11, 11, 15, 0, 0, tzinfo=tz_py)
    piso_fijo_utc = piso_fijo_py.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    estados_cerrados = ["cerrado", "closed", "resolved", "resuelto", "snoozed"]

    try:
        # 1. Traer chats con actividad desde el 11/11/2026 15:00 hs PY en adelante
        res_hoy = supabase.table("conversaciones")\
            .select(COLUMNAS_DASHBOARD)\
            .gte("updated_at", piso_fijo_utc)\
            .execute()
        datos = res_hoy.data or []
        
        # 2. Traer TODOS los chats que sigan abiertos en Supabase
        res_abiertos = supabase.table("conversaciones")\
            .select(COLUMNAS_DASHBOARD)\
            .not_.in_("estado", estados_cerrados)\
            .execute()
        datos_abiertos = res_abiertos.data or []

        # Combinar ambos conjuntos sin duplicados
        todos_recientes = {r["id"]: r for r in (datos + datos_abiertos)}
        return list(todos_recientes.values())

    except Exception as e:
        st.error(f"Error consultando datos frescos en Supabase: {e}")
        return []

def obtener_datos():
    """Une los datos históricos de 24h con los datos frescos de hoy garantizando la información del agente actual."""
    datos_historicos = obtener_datos_historicos_q()
    datos_hoy = obtener_datos_hoy()
    
    # Se coloca datos_hoy primero para que drop_duplicates mantenga el agente reasignado
    todos_los_datos = datos_hoy + datos_historicos
    
    if not todos_los_datos:
        return pd.DataFrame()
        
    df = pd.DataFrame(todos_los_datos)
    df = df.drop_duplicates(subset=["id"], keep="first").copy()
    
    return procesar_fechas_df(df)

st.markdown("""
<style>
    .stApp { background-color: #0b1120; color: #f8fafc; }
    
    .block-container {
        padding-top: 1.5rem !important;
        padding-bottom: 1.5rem !important;
    }

    header[data-testid="stHeader"] {
        background-color: #0b1120 !important;
    }

    [data-testid="stSidebarHeader"] {
        padding-top: 0px !important;
        padding-bottom: 0px !important;
        height: 1.8rem !important;
    }
    
    [data-testid="stSidebarUserContent"] {
        padding-top: 0.2rem !important;
    }

    div[data-testid="stDateInput"] button {
        display: none !important;
        visibility: hidden !important;
        width: 0 !important;
        padding: 0 !important;
        margin: 0 !important;
    }

    /* Metric Cards con mejor jerarquía, borde sutil e iluminación */
    .metric-card {
        background: linear-gradient(145deg, #1e293b 0%, #0f172a 100%);
        color: #f8fafc;
        padding: 18px 20px;
        border-radius: 12px;
        border: 1px solid #334155;
        box-shadow: 0 4px 12px -2px rgba(0, 0, 0, 0.35);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .metric-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 20px -3px rgba(0, 0, 0, 0.45);
    }
    .metric-card-title {
        font-size: 0.74rem;
        color: #94a3b8;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.8px;
        display: flex;
        align-items: center;
        gap: 6px;
    }
    .metric-card-value {
        font-size: 1.65rem;
        font-weight: 800;
        color: #f8fafc;
        margin-top: 6px;
        letter-spacing: -0.5px;
    }
    .metric-card-sub {
        font-size: 0.76rem;
        color: #38bdf8;
        margin-top: 4px;
        font-weight: 500;
    }

    .db-info-box {
        background-color: #1e293b; color: #94a3b8; padding: 12px 14px;
        border-radius: 10px; border: 1px solid #334155; font-size: 0.84rem; margin-bottom: 12px;
    }

    /* Alerta Crítica con animación de pulso luminoso continuo */
    @keyframes pulsoCritico {
        0% {
            box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.55);
            border-color: #ef4444;
        }
        70% {
            box-shadow: 0 0 0 12px rgba(239, 68, 68, 0);
            border-color: #dc2626;
        }
        100% {
            box-shadow: 0 0 0 0 rgba(239, 68, 68, 0);
            border-color: #ef4444;
        }
    }

    /* Ocultar botones nativos de desarrollo de Streamlit (Share, GitHub, etc.) */
    .stAppDeployButton, 
    header[data-testid="stHeader"] [data-testid="stToolbarActions"],
    #MainMenu, 
    footer {
        display: none !important;
        visibility: hidden !important;
    }

    .alert-card-critical {
        background: linear-gradient(90deg, #451a03 0%, #1e1b4b 100%);
        color: #fef3c7;
        padding: 16px 20px;
        border-radius: 10px;
        border: 1px solid #ef4444;
        border-left: 6px solid #ef4444;
        margin-bottom: 15px;
        animation: pulsoCritico 2.2s infinite;
    }

    /* Mejora para distribuir el texto a la izquierda y el contador a la derecha */
    .alert-card-critical-content {
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
        gap: 12px;
        width: 100%;
    }
    
    .admin-card {
        background-color: #1e293b;
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 15px;
    }
    .stButton>button { 
        background-color: #0284c7; 
        color: white; 
        font-weight: bold; 
        border-radius: 8px; 
        border: none; 
        transition: background-color 0.2s ease;
    }
    .stButton>button:hover {
        background-color: #0369a1;
    }

    /* Estilizar el encabezado del expander para que sea el propio Banner de Alerta */
    div[data-testid="stExpander"]:has(.alerta-ancla-activa) {
        border: none !important;
        background: transparent !important;
    }

    div[data-testid="stExpander"]:has(.alerta-ancla-activa) > details {
        border: 1px solid #ef4444 !important;
        border-left: 6px solid #ef4444 !important;
        border-radius: 10px !important;
        background: linear-gradient(90deg, #451a03 0%, #1e1b4b 100%) !important;
        box-shadow: 0 4px 15px rgba(239, 68, 68, 0.25);
        animation: pulsoCritico 2.2s infinite;
        overflow: hidden;
    }

    div[data-testid="stExpander"]:has(.alerta-ancla-activa) > details > summary {
        padding: 14px 18px !important;
        color: #fef3c7 !important;
        font-weight: 700 !important;
        font-size: 0.95rem !important;
    }

    div[data-testid="stExpander"]:has(.alerta-ancla-activa) > details > summary:hover {
        background: rgba(239, 68, 68, 0.15) !important;
        color: #ffffff !important;
    }

    div[data-testid="stExpander"]:has(.alerta-ancla-activa) > details > summary svg {
        fill: #fca5a5 !important;
    }

    /* Fondo oscuro limpio para el contenido interno cuando se despliega */
    div[data-testid="stExpander"]:has(.alerta-ancla-activa) > details > div[data-testid="stExpanderDetails"] {
        background-color: #0b1120 !important;
        border-top: 1px solid rgba(239, 68, 68, 0.3) !important;
        padding: 16px !important;
    }
</style>
""", unsafe_allow_html=True)

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
            osc.type = 'square';
            osc.frequency.setValueAtTime(freq, ctx.currentTime + delay);

            gain.gain.setValueAtTime(0.35, ctx.currentTime + delay);
            gain.gain.exponentialRampToValueAtTime(0.00001, ctx.currentTime + delay + dur);

            osc.connect(gain);
            gain.connect(ctx.destination);

            osc.start(ctx.currentTime + delay);
            osc.stop(ctx.currentTime + delay + dur);
        }

        emitirBeep(0.0, 1046.50, 0.18);
        emitirBeep(0.25, 1046.50, 0.18);
        emitirBeep(0.50, 1318.51, 0.30);

    } catch(e) {
        console.log("Audio no reproducido por politicas del navegador", e);
    }
})();
</script>
"""

def evaluar_horario_estricto(dt_objeto):
    if pd.isna(dt_objeto):
        return False
    fecha_str = dt_objeto.strftime("%Y-%m-%d")
    dia_semana = dt_objeto.weekday()
    hora_actual = dt_objeto.time()

    if fecha_str in FERIADOS or dia_semana == 6:
        return False

    # Lunes a Viernes: 08:00 a 17:30 hs
    if dia_semana in [0, 1, 2, 3, 4] and time(8, 0, 0) <= hora_actual <= time(18, 0, 0):
        return True
    # Sábados: 09:00 a 11:45 hs
    if dia_semana == 5 and time(9, 0, 0) <= hora_actual <= time(12, 0, 0):
        return True

    return False

# ==========================================
# EVALUACIÓN DE SLA
# ==========================================

def evaluar_sla_normal_excel(row, threshold_1ra=2.0):
    dt_obj = row.get("created_at_dt")
    if pd.isna(dt_obj):
        return "excluido"
    
    fecha_str = dt_obj.strftime("%Y-%m-%d")
    dia_semana = dt_obj.weekday()
    hora_actual = dt_obj.time()
    
    if fecha_str in FERIADOS:
        return "excluido por feriado"
    
    agente = str(row.get("agente_asignado", "")).strip()
    if not agente or agente in ["Sin asignar", "None", "nan", "Monica", "Soporte", "soporte", "SOPORTE"]:
        return "excluido por agente"
        
    en_horario = False
    if dia_semana in [0, 1, 2, 3, 4] and time(8, 0, 0) <= hora_actual <= time(17, 30, 0):
        en_horario = True
    elif dia_semana == 5 and time(9, 0, 0) <= hora_actual <= time(11, 45, 0):
        en_horario = True
        
    if not en_horario:
        return "excluido por horario"
        
    min_1ra = row.get("primera_respuesta_min")
    if pd.isna(min_1ra):
        return "no cumple"
        
    return "cumple" if min_1ra <= threshold_1ra else "no cumple"


def evaluar_sla_extendido_excel(row, threshold_1ra=2.0):
    # =================================================================
    # INTERRUPTOR TEMPORAL: Desactiva el cálculo de SLA Extendido
    return "excluido por horario" 
    # =================================================================
    
    dt_obj = row.get("created_at_dt")
    if pd.isna(dt_obj):
        return "excluido"

    fecha_str = dt_obj.strftime("%Y-%m-%d")
    dia_semana = dt_obj.weekday()
    hora_actual = dt_obj.time()

    if fecha_str in FERIADOS:
        return "excluido por feriado"
    
    agente = str(row.get("agente_asignado", "")).strip()
    if not agente or agente in ["Sin asignar", "None", "nan", "Monica", "Soporte", "soporte", "SOPORTE"]:
        return "excluido por agente"
        
    en_horario = False
    if dia_semana in [0, 1, 2]:
        if hora_actual >= time(19, 0, 0) or hora_actual <= time(1, 45, 0):
            en_horario = True
    elif dia_semana in [3, 4, 5, 6]:
        if hora_actual >= time(18, 0, 0) or hora_actual <= time(2, 45, 0):
            en_horario = True
            
    if not en_horario:
        return "excluido por horario"
        
    min_1ra = row.get("primera_respuesta_min")
    if pd.isna(min_1ra):
        return "no cumple"
        
    return "cumple" if min_1ra <= threshold_1ra else "no cumple"

def evaluar_sla_gestion_excel(row, threshold_gest):
    # 1. Exclusión por Agente (Monica, Bots o Sin Asignar)
    agente = str(row.get("agente_asignado", "")).strip().lower()
    por_agente = str(row.get("por_agente", "")).strip().lower()
    
    if por_agente == "excluido" or not agente or agente in ["sin asignar", "none", "nan", "monica", "monica (bot)", "Soporte", "soporte", "SOPORTE"]:
        return "excluido por filtro"

    # 2. Exclusión por Horario (Usa exactamente la misma franja que el Dashboard: Normal + Extendido)
    horario_eval = str(row.get("horario_evaluado", "")).strip().lower()
    if horario_eval == "fuera de horario":
        return "excluido por filtro"

    # 3. Exclusión por Etiqueta "Sin Respuesta"
    etiquetas = str(row.get("etiquetas", "")).lower()
    if "sin respuesta" in etiquetas:
        return "excluido por filtro"

    # 4. Verificación de Estado (Abierto vs Cerrado)
    estado_raw = str(row.get("estado", "")).strip().lower()
    es_cerrado = estado_raw in ["cerrado", "closed", "resolved", "resuelto", "snoozed"]

    if not es_cerrado:
        return "sin cerrar"

    # 5. Evaluación del Tiempo de Gestión
    min_gest = row.get("tiempo_resolucion_minutos")
    if pd.isna(min_gest):
        return "no cumple"

    return "cumple" if min_gest <= threshold_gest else "no cumple"

def calificacion_a_estrellas(x):
    if pd.isna(x) or str(x).strip() in ["", "None", "nan", "null"]:
        return ""
    try:
        val = int(float(x))
        return "★" * val if val > 0 else ""
    except:
        return ""

# ==========================================
# GESTIÓN UNIFICADA Y ULTRA RÁPIDA DE CSAT
# ==========================================

def obtener_df_csat_valido(df_in):
    """Retorna las conversaciones con calificaciones CSAT válidas en un solo filtro."""
    if df_in.empty:
        return pd.DataFrame()

    df_c = df_in.copy()
    df_c["rating_num"] = pd.to_numeric(df_c["rating"], errors="coerce")
    df_c = df_c.dropna(subset=["rating_num"])

    if "por_agente" in df_c.columns:
        df_c = df_c[df_c["por_agente"] == "no excluido"]

    return df_c.drop_duplicates(subset=["id"])

def calcular_csat_rapido(df_csat_preparado):
    """Calcula CSAT instantáneo sobre DataFrame pre-filtrado retornando (pct, positivas, total)."""
    if df_csat_preparado.empty:
        return 0.0, 0, 0
    ratings = df_csat_preparado["rating_num"]
    positivas = int((ratings >= 4).sum())
    total = len(ratings)
    pct = round((positivas / total) * 100, 1) if total > 0 else 0.0
    return pct, positivas, total

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

# ==========================================
# CARGA ÚNICA GLOBAL DE DATOS (ÓPTIMO EN RAM)
# ==========================================
df_all = obtener_datos()

if not df_all.empty and "updated_at_local" in df_all.columns:
    # Tomamos la fecha máxima real parseada
    max_updated_dt = df_all["updated_at_local"].dropna().max()
    tiempo_hace_str = obtener_tiempo_transcurrido(max_updated_dt)

    # Obtiene el timestamp global del servidor (compartido por todos los clientes)
    ultima_sync_dt = obtener_timestamp_sync()
    sync_hace_str = obtener_tiempo_transcurrido(ultima_sync_dt)
    
    st.sidebar.markdown(f"""
    <div class="db-info-box">
        • <b>Última actividad:</b> {tiempo_hace_str}<br>
        • <b>Última sync:</b> {sync_hace_str}<br>
    </div>
    """, unsafe_allow_html=True)
else:
    st.sidebar.markdown("""
    <div class="db-info-box">
        <b>Ultima sincronizacion:</b> Sin registros<br>
    </div>
    """, unsafe_allow_html=True)

# ==========================================
# GESTIÓN MANUAL DE PRESENCIA DEL AGENTE (CON MODAL FLOTANTE SIN PARPADEO)
# ==========================================
nombre_usuario_sesion = st.session_state.get("user_name", "")
email_usuario_sesion = st.session_state.get("user_email", "")

if nombre_usuario_sesion:
    @st.fragment
    def renderizar_selector_presencia_sidebar():
        st.markdown("### Mi Estado de Presencia")
        
        clave_sesion_away = f"estado_away_{email_usuario_sesion}"
        clave_notificar = f"notificar_cambio_{email_usuario_sesion}"

        # 1. Consultar a Supabase SOLO la primera vez que carga la sesión
        if clave_sesion_away not in st.session_state:
            try:
                res_mi_estado = (
                    supabase.table("estado_agentes")
                    .select("away")
                    .ilike("nombre", nombre_usuario_sesion.strip())
                    .limit(1)
                    .execute()
                )
                st.session_state[clave_sesion_away] = (
                    bool(res_mi_estado.data[0].get("away", False))
                    if res_mi_estado.data else False
                )
            except Exception:
                st.session_state[clave_sesion_away] = False

        opciones_presencia = ["🟢 Disponible", "🟡 Ausente"]
        idx_def = 1 if st.session_state[clave_sesion_away] else 0

        # 2. Callback instantáneo al cambiar el radio button
        def guardar_cambio_presencia():
            valor_elegido = st.session_state["radio_mi_presencia"]
            nuevo_away = (valor_elegido == "🟡 Ausente")
            st.session_state[clave_sesion_away] = nuevo_away
            st.session_state[clave_notificar] = valor_elegido

            try:
                supabase.table("estado_agentes").upsert({
                    "id": email_usuario_sesion.lower().strip() or nombre_usuario_sesion.lower().strip(),
                    "nombre": nombre_usuario_sesion.strip(),
                    "email": email_usuario_sesion.strip(),
                    "away": nuevo_away,
                    "updated_at": datetime.now(timezone.utc).isoformat()
                }, on_conflict="id").execute()
                obtener_mapa_estados_agentes.clear()
            except Exception as ex_st:
                st.caption(f"Error guardando estado: {ex_st}")

        st.radio(
            "Indica si estás activo en la bandeja:",
            options=opciones_presencia,
            index=idx_def,
            horizontal=True,
            key="radio_mi_presencia",
            on_change=guardar_cambio_presencia
        )

        # 3. Modal visual efímero (se muestra y desaparece solo sin recargar la página)
        if st.session_state.get(clave_notificar):
            estado_nuevo = st.session_state.pop(clave_notificar)
            es_ausente = "Ausente" in estado_nuevo
            color_borde = "#fbbf24" if es_ausente else "#10b981"
            icono = "🟡" if es_ausente else "🟢"
            titulo_txt = "Estado: Ausente" if es_ausente else "Estado: Disponible"
            sub_txt = (
                "No recibirás nuevas asignaciones mientras estés ausente."
                if es_ausente else
                "Estás activo y visible para recibir chats en la bandeja."
            )

            st.iframe(f"""
            <script>
            (function() {{
                const doc = window.parent.document;
                const prev = doc.getElementById('bims-presencia-modal');
                if (prev) prev.remove();

                const modal = doc.createElement('div');
                modal.id = 'bims-presencia-modal';
                modal.innerHTML = `
                    <div style="
                        position: fixed;
                        inset: 0;
                        z-index: 2147483647;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        background: rgba(11, 17, 32, 0.55);
                        backdrop-filter: blur(4px);
                        opacity: 1;
                        transition: opacity 0.35s ease;
                        font-family: 'Segoe UI', Arial, sans-serif;
                    " id="bims-presencia-backdrop">
                        <div style="
                            background: linear-gradient(145deg, #1e293b 0%, #0f172a 100%);
                            border: 1px solid #334155;
                            border-left: 5px solid {color_borde};
                            border-radius: 14px;
                            padding: 24px 32px;
                            box-shadow: 0 20px 45px rgba(0,0,0,0.6);
                            text-align: center;
                            max-width: 380px;
                            transform: scale(1);
                            transition: transform 0.25s ease;
                        ">
                            <div style="font-size: 2.2rem; margin-bottom: 8px;">{icono}</div>
                            <div style="color: #f8fafc; font-weight: 700; font-size: 1.15rem; margin-bottom: 6px;">
                                {titulo_txt}
                            </div>
                            <div style="color: #94a3b8; font-size: 0.88rem; line-height: 1.45;">
                                {sub_txt}
                            </div>
                        </div>
                    </div>
                `;
                doc.body.appendChild(modal);

                // Desvanecer y limpiar del DOM tras 1.8 segundos
                setTimeout(() => {{
                    const bd = doc.getElementById('bims-presencia-backdrop');
                    if (bd) bd.style.opacity = '0';
                    setTimeout(() => {{
                        if (modal) modal.remove();
                    }}, 350);
                }}, 1800);
            }})();
            </script>
            """, width=1, height=1)

    with st.sidebar:
        renderizar_selector_presencia_sidebar()
        st.markdown("---")
        
st.sidebar.markdown("### Filtros de Consulta")

usar_filtro_hora = st.sidebar.checkbox("Restringir Franja Horaria", value=False)

def set_fechas_hoy():
    hoy = obtener_fecha_local_hoy()
    st.session_state["input_f_desde"] = hoy
    st.session_state["input_f_hasta"] = hoy

def set_fechas_semana():
    hoy = obtener_fecha_local_hoy()
    inicio_semana = hoy - timedelta(days=hoy.weekday())
    fin_semana = inicio_semana + timedelta(days=6)
    st.session_state["input_f_desde"] = inicio_semana
    st.session_state["input_f_hasta"] = fin_semana

def set_fechas_mes():
    hoy = obtener_fecha_local_hoy()
    inicio_mes = hoy.replace(day=1)
    if hoy.month == 12:
        fin_mes = date(hoy.year, 12, 31)
    else:
        fin_mes = date(hoy.year, hoy.month + 1, 1) - timedelta(days=1)
        
    st.session_state["input_f_desde"] = inicio_mes
    st.session_state["input_f_hasta"] = fin_mes

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
 
    # 1. Evaluamos primero los SLA de 1ra Respuesta
    sla_norm_series = df_exp.apply(lambda r: evaluar_sla_normal_excel(r, sla_1ra_threshold), axis=1) if not df_exp.empty else pd.Series(dtype=str)
    sla_ext_series = df_exp.apply(lambda r: evaluar_sla_extendido_excel(r, sla_1ra_threshold), axis=1) if not df_exp.empty else pd.Series(dtype=str)

    df_reporte["SLA Normal"] = sla_norm_series
    df_reporte["SLA Extendido"] = sla_ext_series

    # 2. Filtrar Primera Respuesta (min): solo si SLA Normal o Extendido es 'cumple' o 'no cumple'
    if not df_exp.empty:
        prim_resp_raw = df_exp.get("primera_respuesta_min", pd.Series(dtype=float))
        es_valido_1ra = sla_norm_series.isin(["cumple", "no cumple"]) | sla_ext_series.isin(["cumple", "no cumple"])
        df_reporte["Primera respuesta (min)"] = np.where(es_valido_1ra, prim_resp_raw, np.nan)
    else:
        df_reporte["Primera respuesta (min)"] = None
    
    if "rating" in df_exp and not df_exp.empty:
        df_reporte["Calificacion"] = df_exp["rating"].apply(calificacion_a_estrellas)
    else:
        df_reporte["Calificacion"] = ""

    df_reporte["Motivo Normalizado"] = df_exp.get("motivo_normalizado", "")
    df_reporte["Ticket Relacionado"] = df_exp.get("ticket_relacionado", "") 
    df_reporte["Feedback"] = df_exp.get("feedback", "")
    df_reporte["Agente evaluado"] = df_exp.get("agente_evaluado", "")
    df_reporte["Fecha cierre (Primer Cierre)"] = df_exp.get("fecha_cierre_fmt", "")

    df_reporte["Etiquetas"] = df_exp.get("etiquetas", "")
    df_reporte["Modulo"] = df_exp.get("modulo", "")
    df_reporte["Cliente"] = df_exp.get("cliente", "")
    df_reporte["Tipo de contacto"] = df_exp.get("tipo_contacto", "")
    df_reporte["Nivel"] = df_exp.get("nivel", "")
    
    # Evaluar SLA de Gestión primero
    sla_gest_series = df_exp.apply(lambda r: evaluar_sla_gestion_excel(r, sla_gest_threshold), axis=1) if not df_exp.empty else pd.Series(dtype=str)
    df_reporte["SLA Tiempo Gestion"] = sla_gest_series

    # Filtrar tiempo de resolución en minutos y horas: solo se completa si es 'cumple' o 'no cumple'
    if not df_exp.empty:
        minutos_raw = df_exp.get("tiempo_resolucion_minutos", pd.Series(dtype=float))
        es_valido_gestion = sla_gest_series.isin(["cumple", "no cumple"])
        
        df_reporte["Tiempo resolucion (min)"] = np.where(es_valido_gestion, minutos_raw, np.nan)
        df_reporte["Tiempo resolucion (horas)"] = np.where(es_valido_gestion, (minutos_raw / 60.0).round(2), np.nan)
    else:
        df_reporte["Tiempo resolucion (min)"] = None
        df_reporte["Tiempo resolucion (horas)"] = None

    df_reporte["AI Title"] = df_exp.get("ai_title", "No Generado")

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
    
    if not df_all.empty and "fecha_solo" in df_all.columns:
        df_exp_filt = df_all[
            (df_all["fecha_solo"] >= pd.to_datetime(f_exp_inicio).date()) & 
            (df_all["fecha_solo"] <= pd.to_datetime(f_exp_fin).date())
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

# -------------------------------------------------------------------------
# MODAL GLOBAL DE CIERRE DE SESIÓN (INSERTADO DIRECTAMENTE EN EL DOCUMENTO)
# -------------------------------------------------------------------------
st.iframe("""
<script>
(function() {
    const doc = window.parent.document;
    if (doc.__bimsLogoutListenerInstalled) return;
    doc.__bimsLogoutListenerInstalled = true;

    function showLogoutOverlay() {
        let overlay = doc.getElementById('bims-logout-loading-overlay');
        if (!overlay) {
            overlay = doc.createElement('div');
            overlay.id = 'bims-logout-loading-overlay';
            overlay.innerHTML = `
                <div class="bims-logout-card">
                    <div class="bims-logout-spinner"></div>
                    <div class="bims-logout-title">Cerrando tu sesión...</div>
                    <div class="bims-logout-subtitle">Volviendo a la pantalla de acceso</div>
                </div>`;
            doc.body.appendChild(overlay);
        }
        overlay.style.setProperty('display', 'flex', 'important');
        overlay.style.setProperty('z-index', '2147483647', 'important');
        try {
            localStorage.removeItem('bims_user_session');
            sessionStorage.setItem('bims_just_logged_out', 'true');
        } catch (e) {}
    }

    const styleId = 'bims-logout-overlay-style';
    if (!doc.getElementById(styleId)) {
        const style = doc.createElement('style');
        style.id = styleId;
        style.textContent = `
            #bims-logout-loading-overlay { position:fixed !important; inset:0 !important; width:100vw !important; height:100vh !important;
                z-index:2147483647 !important; display:none; align-items:center; justify-content:center; pointer-events:auto;
                background:rgba(11,17,32,.96); backdrop-filter:blur(10px); font-family:Arial,sans-serif; }
            #bims-logout-loading-overlay .bims-logout-card { display:flex; flex-direction:column;
                align-items:center; gap:13px; padding:30px 38px; border-radius:18px;
                background:linear-gradient(145deg,#1e293b,#0f172a); border:1px solid #334155;
                box-shadow:0 20px 45px rgba(0,0,0,.5); text-align:center; }
            #bims-logout-loading-overlay .bims-logout-spinner { width:38px;height:38px;border-radius:50%;
                border:3px solid rgba(56,189,248,.2);border-top-color:#38bdf8;
                animation:bimsLogoutSpin .75s linear infinite; }
            #bims-logout-loading-overlay .bims-logout-title {color:#f8fafc;font-weight:700;font-size:17px;}
            #bims-logout-loading-overlay .bims-logout-subtitle {color:#94a3b8;font-size:13px;}
            @keyframes bimsLogoutSpin {to{transform:rotate(360deg);}}
        `;
        doc.head.appendChild(style);
    }

    doc.addEventListener('click', function(event) {
        const button = event.target.closest('button');
        if (button && button.innerText && button.innerText.includes('Cerrar Sesión')) {
            showLogoutOverlay();
        }
    }, true);
})();
</script>
""", width=1, height=1)

if st.sidebar.button("Cerrar Sesión", use_container_width=True, key="btn_logout_sidebar"):
    # El listener muestra el overlay de inmediato. Esta limpieza adicional
    # evita que una sesión antigua de localStorage vuelva a autenticarse.
    st.query_params.clear()
    
    st.session_state["user_authenticated"] = False
    st.session_state["user_email"] = ""
    if "admin_authenticated" in st.session_state:
        st.session_state["admin_authenticated"] = False
        
    st.rerun()
# ==========================================
# INDICADOR DE ESTADO EN VIVO (CABECERA)
# ==========================================
@st.cache_data(ttl=60, show_spinner=False)
def obtener_estado_bot_salud():
    try:
        url_base = NOTIFICADOR_API_URL.replace("/health-status", "").rstrip('/')
        url_salud = f"{url_base}/health-status"
        res = requests.get(url_salud, timeout=3)
        if res.status_code == 200:
            data = res.json()
            online = data.get("status_general") == "OK"
            state = data.get("whatsapp_state", "desconectado")
            return online, state
    except Exception:
        pass
    return False, "offline"

bot_online, bot_state = obtener_estado_bot_salud()

# Estilos CSS de pulso sutil tipo "radar"
st.html("""
<style>
@keyframes bimsPing {
    75%, 100% {
        transform: scale(2.2);
        opacity: 0;
    }
}
.bims-pulse-container {
    position: relative;
    display: inline-flex;
    width: 9px;
    height: 9px;
    margin-right: 3px;
}
.bims-pulse-ring {
    position: absolute;
    inset: 0;
    border-radius: 50%;
    animation: bimsPing 1.8s cubic-bezier(0, 0, 0.2, 1) infinite;
}
.bims-pulse-dot {
    position: relative;
    display: inline-flex;
    border-radius: 50%;
    width: 9px;
    height: 9px;
}
</style>
""")

if bot_online:
    badge_html = f"""
    <div style="display: flex; align-items: center; gap: 14px; margin-bottom: 1rem;">
        <h1 style="margin: 0; padding: 0;">Dashboard Soporte BIMS</h1>
        <span style="
            background: rgba(6, 95, 70, 0.35); 
            color: #34d399; 
            border: 1px solid rgba(5, 150, 105, 0.6); 
            padding: 4px 12px; 
            border-radius: 20px; 
            font-size: 0.82rem; 
            font-weight: 600; 
            display: inline-flex; 
            align-items: center; 
            gap: 7px;
            box-shadow: 0 0 12px rgba(16, 185, 129, 0.15);" 
            title="Conexión de WhatsApp Activa ({bot_state})">
            <span class="bims-pulse-container">
                <span class="bims-pulse-ring" style="background-color: #34d399;"></span>
                <span class="bims-pulse-dot" style="background-color: #10b981;"></span>
            </span>
            Bot Operativo
        </span>
    </div>
    """
else:
    badge_html = f"""
    <div style="display: flex; align-items: center; gap: 14px; margin-bottom: 1rem;">
        <h1 style="margin: 0; padding: 0;">Dashboard Soporte BIMS</h1>
        <span style="
            background: rgba(127, 29, 29, 0.35); 
            color: #f87171; 
            border: 1px solid rgba(220, 38, 38, 0.6); 
            padding: 4px 12px; 
            border-radius: 20px; 
            font-size: 0.82rem; 
            font-weight: 600; 
            display: inline-flex; 
            align-items: center; 
            gap: 7px;
            box-shadow: 0 0 12px rgba(239, 68, 68, 0.2);" 
            title="Conexión de WhatsApp Caída ({bot_state})">
            <span class="bims-pulse-container">
                <span class="bims-pulse-ring" style="background-color: #f87171;"></span>
                <span class="bims-pulse-dot" style="background-color: #ef4444;"></span>
            </span>
            Bot Desconectado
        </span>
    </div>
    """

st.markdown(badge_html, unsafe_allow_html=True)

# 1. Obtener usuario actual (ajusta según tu variable de sesión o auth existente)
usuario_actual = st.session_state.get("usuario_conectado") or st.session_state.get("user_email", "")
permisos = obtener_permisos_usuario(usuario_actual)

puede_ver_wbr = permisos["acceso_wbr"]
puede_ver_admin = permisos["acceso_admin"]

# 2. Construir lista dinámica de pestañas
titulos_tabs = [
    "Control Operativo & SLA", 
    "Distribución de Carga",
    "Resumen de Chats & Agentes"
]

if puede_ver_wbr:
    titulos_tabs.append("Informe WBR Gerencial")

if puede_ver_admin:
    titulos_tabs.append("Administración & Configuracion")

titulos_tabs.append("FAQ")

# 3. Crear las pestañas
tabs_creadas = st.tabs(titulos_tabs)

# Mapear las pestañas a variables de forma segura
idx = 0
tab_operativo = tabs_creadas[idx]; idx += 1
tab_carga_vivo = tabs_creadas[idx]; idx += 1
tab_resumen = tabs_creadas[idx]; idx += 1

tab_wbr = None
if puede_ver_wbr:
    tab_wbr = tabs_creadas[idx]; idx += 1

tab_admin = None
if puede_ver_admin:
    tab_admin = tabs_creadas[idx]; idx += 1

tab_faq = tabs_creadas[idx]

# ==========================================
# FRAGMENTO DE ALERTAS EN VIVO (CADA 10s)
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
        COLUMNAS_ALERTAS = "id, created_at, updated_at, estado, canal, primera_respuesta_min, tenant, nombre_contacto, agente_asignado"

        res = supabase.table("conversaciones")\
            .select(COLUMNAS_ALERTAS)\
            .not_.in_("estado", ["cerrado", "closed", "resolved", "resuelto", "snoozed"])\
            .order("created_at", desc=True)\
            .limit(100)\
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
        
        # 1. Filtramos chats abiertos
        df_activos = df_base[~df_base["estado_clean"].isin(estados_cerrados)].copy()
        
        # 2. FILTRO EXCLUSIVO: Solo consideramos chats con agente "Sin Asignar" / vacíos / "soporte"
        # (Se descartan Mónica y cualquier agente humano ya asignado)
        agentes_permitidos_sin_asignar = ["sin asignar", "none", "nan", "", "soporte"]
        df_activos["agente_clean"] = df_activos["agente_asignado"].fillna("").astype(str).str.strip().str.lower()
        
        df_activos = df_activos[
            df_activos["agente_clean"].isin(agentes_permitidos_sin_asignar) | 
            df_activos["agente_clean"].str.contains("sin asignar", na=False)
        ].copy()

        if "canal" in df_activos.columns:
            df_activos["canal_clean"] = df_activos["canal"].fillna("").astype(str).str.strip().str.lower()
            df_activos = df_activos[~df_activos["canal_clean"].isin(["correo electrónico", "email", "correo electronico"])].copy()

        if not df_activos.empty:
            df_activos["id_str"] = df_activos["id"].astype(str).str.strip()
            df_activos["intercom_url"] = df_activos["id_str"].apply(
                lambda x: f"https://app.intercom.io/a/apps/{INTERCOM_APP_ID}/inbox/inbox/all/conversations/{x}"
            )

            # Usamos updated_at para medir el tiempo desde la derivación efectiva
            fecha_ref = pd.to_datetime(df_activos["updated_at"].fillna(df_activos["created_at"]), errors="coerce", utc=True)
            df_activos["created_at_dt"] = fecha_ref.dt.tz_convert("America/Asuncion")
            df_activos["created_at_fmt"] = df_activos["created_at_dt"].dt.strftime("%Y-%m-%d %H:%M").fillna("Sin fecha")
            df_activos = df_activos.drop_duplicates(subset=["id"])
            
            df_activos["1ra_resp_num"] = pd.to_numeric(df_activos["primera_respuesta_min"], errors="coerce")
            
            calc_min = (now_dt - df_activos["created_at_dt"]).dt.total_seconds() / 60.0
            df_activos["min_transcurridos"] = calc_min.apply(lambda x: round(max(0.0, x), 1) if pd.notna(x) else 0.0)
            
            sin_respuesta = df_activos["1ra_resp_num"].isna()
            tiempo_superado = df_activos["min_transcurridos"] >= alerta_nuevo_th
            
            df_criticos_sla = df_activos[sin_respuesta & tiempo_superado].copy()

            if not df_criticos_sla.empty:
                cant = len(df_criticos_sla)
                palabra_chat = "chat" if cant == 1 else "chats"

                # Título que se mostrará integrado en el banner
                titulo_banner = f"🚨 ALERTA CRÍTICA: Hay {cant} {palabra_chat} en espera de asignación humana (> {alerta_nuevo_th} min) — Haz clic para desplegar y gestionar"

                with st.expander(titulo_banner, expanded=False):
                    # Marcador invisible que activa las reglas CSS del banner sobre este expander
                    st.markdown('<div class="alerta-ancla-activa" style="display:none;"></div>', unsafe_allow_html=True)

                    if act_sonido and evaluar_horario_estricto(now_dt):
                        html_con_ts = f"<!-- {now_dt.timestamp()} -->\n" + AUDIO_ALARM_HTML
                        st.components.v1.html(html_con_ts, height=0)

                    df_criticos_sla["atendido"] = False
                    cols_check = ["atendido", "intercom_url", "created_at_fmt", "min_transcurridos", "nombre_contacto", "tenant", "agente_asignado", "estado"]
                    if "canal" in df_criticos_sla.columns:
                        cols_check.append("canal")

                    df_criticos_editor = df_criticos_sla.reindex(columns=cols_check).dropna(how="all", axis=1).copy()

                    edited_data = st.data_editor(
                        df_criticos_editor,
                        column_config={
                            "atendido": st.column_config.CheckboxColumn("Atendido", default=False),
                            "intercom_url": st.column_config.LinkColumn(
                                "ID Conversación", 
                                display_text=r".*/(\d+)"
                            ),
                            "created_at_fmt": st.column_config.TextColumn("Fecha Derivación", disabled=True),
                            "min_transcurridos": st.column_config.NumberColumn("Min. Transcurridos", disabled=True),
                            "nombre_contacto": st.column_config.TextColumn("Contacto", disabled=True),
                            "tenant": st.column_config.TextColumn("Tenant", disabled=True),
                            "agente_asignado": st.column_config.TextColumn("Estado Asignación", disabled=True),
                            "estado": st.column_config.TextColumn("Estado", disabled=True),
                            "canal": st.column_config.TextColumn("Canal", disabled=True),
                        },
                        hide_index=True,
                        use_container_width=True,
                        key="editor_alertas_tabla"
                    )

                    cambios = st.session_state.get("editor_alertas_tabla", {}).get("edited_rows", {})
                    if cambios:
                        actualizados = 0
                        for fila_idx, datos_editados in cambios.items():
                            if datos_editados.get("atendido") is True:
                                row_data = df_criticos_sla.iloc[int(fila_idx)]
                                chat_id = row_data["id"]
                                id_str = row_data["id_str"]

                                try:
                                    supabase.table("conversaciones")\
                                        .update({"primera_respuesta_min": 0.0})\
                                        .eq("id", chat_id)\
                                        .execute()
                                    actualizados += 1
                                except Exception as ex_update:
                                    st.error(f"Error al actualizar chat {id_str}: {ex_update}")

                        if actualizados > 0:
                            st.cache_data.clear()
                            st.rerun()

            
            
# ==========================================
# RENDERIZADO DE PESTAÑAS
# ==========================================

with tab_operativo:
    renderizar_alertas_en_vivo()

    sla_1ra_th = st.session_state["sla_1ra_th"]
    sla_gest_th = st.session_state["sla_gest_th"]
    alerta_nuevo_th = st.session_state["alerta_nuevo_th"]

    if not df_all.empty:
        # Extraemos el estado limpiando espacios y minúsculas
        estado_clean = df_all.get("estado", pd.Series(dtype=str)).fillna("").astype(str).str.strip().str.lower()
        
        # Un chat está CERRADO únicamente si su estado en Supabase/Intercom indica cierre explícito
        df_all["es_cerrado"] = estado_clean.isin(["cerrado", "closed", "resolved", "resuelto", "snoozed"])

        # Identificamos si el agente asignado es sin asignar o Mónica
        agente_clean = df_all.get("agente_asignado", pd.Series(dtype=str)).fillna("").astype(str).str.strip().str.lower()
        es_monica_o_sin_asignar = agente_clean.isin(["sin asignar", "none", "nan", "monica", "monica (bot)", "soporte"])

        # Regla de Horario Normal idéntica a evaluar_sla_normal_excel
        dt_1ra = df_all["created_at_dt"]
        dia_1ra = dt_1ra.dt.dayofweek
        hora_1ra = dt_1ra.dt.time
        fecha_1ra = dt_1ra.dt.strftime("%Y-%m-%d")
        
        es_horario_normal_1ra = (
            ((dia_1ra.isin([0, 1, 2, 3, 4])) & (hora_1ra >= time(8, 0)) & (hora_1ra <= time(17, 30))) |
            ((dia_1ra == 5) & (hora_1ra >= time(9, 0)) & (hora_1ra <= time(11, 45)))
        ) & (~fecha_1ra.isin(FERIADOS))
        
        # EVALUACIÓN VECTORIZADA DE SLA 1RA RESPUESTA Y GESTIÓN
        cond_1ra = [
            (df_all["por_agente"] == "excluido") | (~es_horario_normal_1ra) | es_monica_o_sin_asignar,
            df_all["primera_respuesta_min"].isna(),
            df_all["primera_respuesta_min"] <= sla_1ra_th
        ]
        val_1ra = ["excluido", "no cumple", "cumple"]
        df_all["sla_1ra_eval"] = np.select(cond_1ra, val_1ra, default="no cumple")

        es_sin_respuesta = df_all.get("etiquetas", pd.Series(dtype=str)).astype(str).str.lower().str.contains("sin respuesta", na=False)
        # La evaluación de Gestión usa las mismas reglas que el Excel:
        # agente válido + horario válido + sin "Sin Respuesta" + conversación cerrada.
        cond_gest = [
            (df_all["por_agente"] == "excluido") | (df_all["horario_evaluado"] == "fuera de horario") | es_sin_respuesta | es_monica_o_sin_asignar,
            ~df_all["es_cerrado"],
            df_all["tiempo_resolucion_minutos"].isna(),
            df_all["tiempo_resolucion_minutos"] <= sla_gest_th
        ]
        val_gest = ["excluido", "sin cerrar", "sin cerrar", "cumple"]
        df_all["sla_gest_eval"] = np.select(cond_gest, val_gest, default="no cumple")

        for col in ["tenant", "company", "nombre_contacto", "motivo_normalizado", "ai_title"]:
            if col not in df_all.columns:
                if col == "motivo_normalizado":
                    df_all[col] = "Consulta General"
                elif col == "ai_title":
                    df_all[col] = "No Generado"
                else:
                    df_all[col] = "Sin datos"
            else:
                if col == "ai_title":
                    df_all[col] = df_all[col].fillna("No Generado").replace(["", "None", "nan", "null"], "No Generado")

    f_desde_v, f_hasta_v = pd.to_datetime(fecha_desde).date(), pd.to_datetime(fecha_hasta).date()
    
    if not df_all.empty and "fecha_solo" in df_all.columns:
        df_filtered = df_all[(df_all["fecha_solo"] >= f_desde_v) & (df_all["fecha_solo"] <= f_hasta_v)].copy()
    else:
        df_filtered = pd.DataFrame()

    if usar_filtro_hora and not df_filtered.empty:
        df_filtered = df_filtered[(df_filtered["hora_solo"] >= hora_inicio) & (df_filtered["hora_solo"] <= hora_fin)]

    now_dt = pd.Timestamp.now(tz="America/Asuncion")
    df_abiertos_all = df_all[~df_all["es_cerrado"]].copy() if not df_all.empty and "es_cerrado" in df_all.columns else pd.DataFrame()

    # CSAT SCORECARD ULTRA-RÁPIDO (PRE-PROCESADO EN UN SOLO PASO)
    st.markdown("### CSAT Performance")
    now_date = obtener_fecha_local_hoy()

    df_csat_global = obtener_df_csat_valido(df_all)

    if not df_csat_global.empty and "fecha_calificacion_solo" in df_csat_global.columns:
        c_hoy, pos_hoy, k_hoy = calcular_csat_rapido(df_csat_global[df_csat_global["fecha_calificacion_solo"] == now_date])
        c_ayer, _, _ = calcular_csat_rapido(df_csat_global[df_csat_global["fecha_calificacion_solo"] == (now_date - timedelta(days=1))])
        diff_hoy = round(c_hoy - c_ayer, 1)

        inicio_sem = now_date - timedelta(days=now_date.weekday())
        c_sem, pos_sem, k_sem = calcular_csat_rapido(df_csat_global[(df_csat_global["fecha_calificacion_solo"] >= inicio_sem) & (df_csat_global["fecha_calificacion_solo"] <= now_date)])
        ini_sem_ant = inicio_sem - timedelta(days=7)
        fin_sem_ant = inicio_sem - timedelta(days=1)
        c_sem_ant, _, _ = calcular_csat_rapido(df_csat_global[(df_csat_global["fecha_calificacion_solo"] >= ini_sem_ant) & (df_csat_global["fecha_calificacion_solo"] <= fin_sem_ant)])
        diff_sem = round(c_sem - c_sem_ant, 1)

        inicio_mes = now_date.replace(day=1)
        c_mes, pos_mes, k_mes = calcular_csat_rapido(df_csat_global[(df_csat_global["fecha_calificacion_solo"] >= inicio_mes) & (df_csat_global["fecha_calificacion_solo"] <= now_date)])
        fin_mes_ant = inicio_mes - timedelta(days=1)
        ini_mes_ant = fin_mes_ant.replace(day=1)
        c_mes_ant, _, _ = calcular_csat_rapido(df_csat_global[(df_csat_global["fecha_calificacion_solo"] >= ini_mes_ant) & (df_csat_global["fecha_calificacion_solo"] <= fin_mes_ant)])
        diff_mes = round(c_mes - c_mes_ant, 1)

        q_act = (now_date.month - 1) // 3 + 1
        q_ant = 4 if q_act == 1 else q_act - 1
        ini_q = datetime(now_date.year, 3 * (q_act - 1) + 1, 1).date()
        c_q, pos_q, k_q = calcular_csat_rapido(df_csat_global[(df_csat_global["fecha_calificacion_solo"] >= ini_q) & (df_csat_global["fecha_calificacion_solo"] <= now_date)])
        fin_q_ant = ini_q - timedelta(days=1)
        ini_q_ant = datetime(fin_q_ant.year, 3 * (q_ant - 1) + 1, 1).date()
        c_q_ant, _, _ = calcular_csat_rapido(df_csat_global[(df_csat_global["fecha_calificacion_solo"] >= ini_q_ant) & (df_csat_global["fecha_calificacion_solo"] <= fin_q_ant)])
        diff_q = round(c_q - c_q_ant, 1)

        df_filtered_csat = df_csat_global[(df_csat_global["fecha_calificacion_solo"] >= f_desde_v) & (df_csat_global["fecha_calificacion_solo"] <= f_hasta_v)]
        c_rango, pos_rango, k_rango = calcular_csat_rapido(df_filtered_csat)
        duracion_dias = (f_hasta_v - f_desde_v).days + 1
        f_hasta_prev = f_desde_v - timedelta(days=1)
        f_desde_prev = f_hasta_prev - timedelta(days=duracion_dias - 1)
        df_prev_rango = df_csat_global[(df_csat_global["fecha_calificacion_solo"] >= f_desde_prev) & (df_csat_global["fecha_calificacion_solo"] <= f_hasta_prev)]
        c_rango_prev, _, _ = calcular_csat_rapido(df_prev_rango)
        diff_rango = round(c_rango - c_rango_prev, 1)
    else:
        c_hoy, pos_hoy, k_hoy, diff_hoy = 0.0, 0, 0, 0.0
        c_sem, pos_sem, k_sem, diff_sem = 0.0, 0, 0, 0.0
        c_mes, pos_mes, k_mes, diff_mes = 0.0, 0, 0, 0.0
        c_q, pos_q, k_q, diff_q, q_act, q_ant = 0.0, 0, 0, 0.0, 1, 4
        c_rango, pos_rango, k_rango, diff_rango = 0.0, 0, 0, 0.0
        df_filtered_csat = pd.DataFrame()

    def render_metric_card(title, value, diff, comp_texto, sub_text, icono="⭐"):
        diff_color = "#34d399" if diff >= 0 else "#f43f5e"
        diff_bg = "rgba(52, 211, 153, 0.12)" if diff >= 0 else "rgba(244, 63, 94, 0.12)"
        diff_symbol = "▲" if diff >= 0 else "▼"
        return f"""
        <div class="metric-card" style="border-left: 4px solid #38bdf8;">
            <div class="metric-card-title"><span>{icono}</span> {title}</div>
            <div class="metric-card-value">{value}</div>
            <div style="display: inline-block; background: {diff_bg}; color: {diff_color}; padding: 2px 8px; border-radius: 6px; font-size: 0.78rem; font-weight: 700; margin-top: 4px;">
                {diff_symbol} {abs(diff)}% {comp_texto}
            </div>
            <div class="metric-card-sub" style="color: #94a3b8; font-size: 0.74rem; margin-top: 6px;">{sub_text}</div>
        </div>
        """

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.markdown(render_metric_card("CSAT Hoy", f"{c_hoy}%", diff_hoy, "vs ayer", f"{pos_hoy} de {k_hoy} encuestas", icono="🎯"), unsafe_allow_html=True)
    m2.markdown(render_metric_card("CSAT Esta Semana", f"{c_sem}%", diff_sem, "vs semana anterior", f"{pos_sem} de {k_sem} encuestas", icono="📅"), unsafe_allow_html=True)
    m3.markdown(render_metric_card("CSAT Este Mes", f"{c_mes}%", diff_mes, "vs mes anterior", f"{pos_mes} de {k_mes} encuestas", icono="📊"), unsafe_allow_html=True)
    m4.markdown(render_metric_card(f"CSAT Trimestre Q{q_act}", f"{c_q}%", diff_q, f"vs Q{q_ant}", f"{pos_q} de {k_q} encuestas", icono="📈"), unsafe_allow_html=True)
    m5.markdown(render_metric_card("CSAT Rango", f"{c_rango}%", diff_rango, "vs período anterior", f"{pos_rango} de {k_rango} encuestas", icono="🔍"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # EVOLUCIÓN HISTÓRICA DE CSAT (SEMANAL DESDE Q ANTERIOR)
    q_act_evo = (now_date.month - 1) // 3 + 1
    q_ant_evo = 4 if q_act_evo == 1 else q_act_evo - 1
    titulo_expander_csat = f"Ver Gráfico de Evolución del CSAT (Desde Q{q_ant_evo})"

    with st.expander(titulo_expander_csat, expanded=False):
        if not df_csat_global.empty and "fecha_calificacion_solo" in df_csat_global.columns:
            anio_inicio_q = now_date.year - 1 if q_act_evo == 1 else now_date.year
            mes_inicio_q = 10 if q_act_evo == 1 else 3 * (q_ant_evo - 1) + 1
            fecha_inicio_q_ant_evo = date(anio_inicio_q, mes_inicio_q, 1)

            df_csat_evo = df_csat_global[df_csat_global["fecha_calificacion_solo"] >= fecha_inicio_q_ant_evo].copy()

            if not df_csat_evo.empty:
                # Agrupación por semana ISO (Lunes a Domingo)
                df_csat_evo["Inicio_Semana"] = df_csat_evo["fecha_calificacion_dt"].dt.to_period("W-SUN").apply(lambda r: r.start_time)
                df_csat_evo["Semana_Etiqueta"] = df_csat_evo["fecha_calificacion_dt"].dt.isocalendar().week.apply(lambda w: f"S{w}")
                # Rango legible de fechas de Lunes a Domingo para el tooltip
                df_csat_evo["Rango_Fechas"] = df_csat_evo["fecha_calificacion_dt"].dt.to_period("W-SUN").apply(
                    lambda r: f"{r.start_time.strftime('%d/%m')} al {r.end_time.strftime('%d/%m')}"
                )

                res_csat_semanal = []
                for _, grp in df_csat_evo.groupby(["Inicio_Semana", "Semana_Etiqueta"]):
                    tot = len(grp)
                    pos = len(grp[grp["rating_num"] >= 4])
                    val_csat = round((pos / tot) * 100, 1) if tot > 0 else 0.0
                    res_csat_semanal.append({
                        "Inicio_Semana": grp["Inicio_Semana"].iloc[0],
                        "Semana": grp["Semana_Etiqueta"].iloc[0],
                        "Rango_Fechas": grp["Rango_Fechas"].iloc[0],
                        "CSAT %": val_csat,
                        "Encuestas": tot
                    })

                df_evo_csat = pd.DataFrame(res_csat_semanal).sort_values("Inicio_Semana")

                fig_csat = go.Figure()

                fig_csat.add_trace(go.Scatter(
                    x=df_evo_csat["Semana"],
                    y=df_evo_csat["CSAT %"],
                    mode="lines+text",
                    name="CSAT (%)",
                    text=[f"<b>{v}%</b>" for v in df_evo_csat["CSAT %"]],
                    textposition="top center",
                    # %{customdata[0]} = Rango de fechas | %{customdata[1]} = Cantidad de encuestas
                    hovertemplate=(
                        "<b>%{x}</b> (%{customdata[0]})<br>"
                        "CSAT: <b>%{y}%</b><br>"
                        "Total encuestas: %{customdata[1]}<extra></extra>"
                    ),
                    customdata=np.stack((df_evo_csat["Rango_Fechas"], df_evo_csat["Encuestas"]), axis=-1),
                    line=dict(color="#38bdf8", width=3, shape="spline"),
                    fill="tozeroy",
                    fillcolor="rgba(56, 189, 248, 0.08)"
                ))

                fig_csat.add_shape(
                    type="line",
                    x0=0, x1=1, xref="paper",
                    y0=90, y1=90, yref="y",
                    line=dict(color="#34d399", width=1.5, dash="dash")
                )

                # Anotación reubicada al inicio del gráfico (lado izquierdo) y por debajo de la línea
                fig_csat.add_annotation(
                    x=0.01, y=90, xref="paper", yref="y",
                    text="<b>Meta Objetivo (90%)</b>",
                    showarrow=False,
                    xanchor="left",
                    yshift=-12,  # Debajo de la línea para no chocar con los números de la curva
                    font=dict(color="#34d399", size=11),
                    bgcolor="rgba(30, 41, 59, 0.75)"  # Fondo translúcido protector
                )

                fig_csat.update_layout(
                    title=f"<b>Evolución Semanal de Satisfacción al Cliente (Desde Q{q_ant_evo})</b>",
                    xaxis_title="Semana del Año",
                    yaxis_title="",
                    yaxis=dict(
                        range=[0, 120],  # Rango ampliado para que el 100% tenga suficiente aire superior
                        showgrid=False,
                        showline=False,
                        zeroline=False,
                        showticklabels=False
                    ),
                    xaxis=dict(gridcolor="#334155"),
                    paper_bgcolor="#1e293b",
                    plot_bgcolor="#1e293b",
                    font=dict(color="#cbd5e1"),
                    margin=dict(t=50, b=40, l=20, r=20),
                    height=380
                )

                st.plotly_chart(fig_csat, use_container_width=True)
            else:
                st.info(f"No hay encuestas registradas desde Q{q_ant_evo} para generar el gráfico.")
        else:
            st.info("Sin registros en la base de datos.")

    # DEFINIR CORREO AUTORIZADO A EDITAR (Tu usuario o Sesión Admin)
    USUARIO_ADMIN_EMAIL = "gerardo.gaona@itti.digital" 

    es_super_usuario = (
        st.session_state.get("user_email", "").strip().lower() == USUARIO_ADMIN_EMAIL.lower()
        or st.session_state.get("admin_authenticated", False)
    )

    # DETALLE Y CATEGORIZACIÓN CSAT DIRECTO EN TABLA (Con Form para Guardado Seguro)
    if not df_filtered.empty and "rating" in df_filtered.columns:
        df_csat_det = df_filtered.dropna(subset=["rating"]).copy()
        df_csat_det["rating_num"] = pd.to_numeric(df_csat_det["rating"], errors="coerce")
        df_csat_det = df_csat_det.dropna(subset=["rating_num"])

        if not df_csat_det.empty:
            with st.expander(f"Ver Detalle de Calificaciones CSAT ({len(df_csat_det)} Encuestas)", expanded=False):
                df_csat_det["Calificacion"] = df_csat_det["rating_num"].apply(calificacion_a_estrellas)
            
                OPCIONES_MOTIVOS = obtener_lista_motivos_csat()

                df_csat_det["motivo_normalizado"] = df_csat_det.get("motivo_normalizado", pd.Series(dtype=str)).fillna("Sin categorizar")
                df_csat_det["ticket_relacionado"] = df_csat_det.get("ticket_relacionado", pd.Series(dtype=str)).fillna("")
                # Asignar 'Nivel 1' por defecto si viene nulo, vacío o como 'Sin Nivel'
                df_csat_det["nivel"] = (
                    df_csat_det.get("nivel", pd.Series(dtype=str))
                    .fillna("Nivel 1")
                    .replace(["", "None", "nan", "null", "Sin Nivel"], "Niv-L1")
                )

                cols_csat_deseadas = [
                    "id_str", 
                    "intercom_url", 
                    "fecha_calificacion_fmt", 
                    "agente_evaluado",
                    "nivel",
                    "rating_num", 
                    "Calificacion", 
                    "feedback", 
                    "motivo_normalizado", 
                    "ticket_relacionado", 
                    "nombre_contacto", 
                    "tenant", 
                    "company"
                ]

                # ORDEN ASCENDENTE POR CALIFICACIÓN
                df_editor = df_csat_det.reindex(columns=cols_csat_deseadas)\
                    .sort_values(by=["rating_num", "fecha_calificacion_fmt"], ascending=[True, False])\
                    .reset_index(drop=True)\
                    .copy()

                config_columnas = {
                    "id_str": None,
                    "rating_num": None,
                    "intercom_url": st.column_config.LinkColumn("ID Chat", display_text=r".*/(\d+)"),
                    "fecha_calificacion_fmt": st.column_config.TextColumn("Fecha/Hora Calificación", disabled=True),
                    "agente_evaluado": st.column_config.TextColumn("Agente Evaluado", disabled=True),
                    "nivel": st.column_config.TextColumn("Nivel", disabled=True),
                    "Calificacion": st.column_config.TextColumn("Puntaje", disabled=True),
                    "feedback": st.column_config.TextColumn("Comentario / Feedback", disabled=True),
                    "nombre_contacto": st.column_config.TextColumn("Contacto", disabled=True),
                    "tenant": st.column_config.TextColumn("Tenant", disabled=True),
                    "company": st.column_config.TextColumn("Company", disabled=True),
                }

                if es_super_usuario:
                    config_columnas["motivo_normalizado"] = st.column_config.SelectboxColumn(
                        "Motivo Normalizado",
                        options=OPCIONES_MOTIVOS,
                        required=True
                    )
                    config_columnas["ticket_relacionado"] = st.column_config.TextColumn("Ticket Relacionado")
                else:
                    config_columnas["motivo_normalizado"] = st.column_config.TextColumn("Motivo Normalizado", disabled=True)
                    config_columnas["ticket_relacionado"] = st.column_config.TextColumn("Ticket Relacionado", disabled=True)
    
                # ENVOLVEMOS LA TABLA EN UN FORMULARIO PARA EVITAR SOBREESCRITURAS DE ESTADO
                with st.form("form_tabla_csat_edicion", clear_on_submit=False):
                    edited_df = st.data_editor(
                        df_editor,
                        column_config=config_columnas,
                        hide_index=True,
                        use_container_width=True,
                        key="editor_csat_tabla"
                    )

                    if es_super_usuario:
                        btn_guardar_cambios = st.form_submit_button("Guardar Cambios", use_container_width=True)
                    else:
                        btn_guardar_cambios = False

                # PROCESAMIENTO SEGURO AL HACER CLIC EN GUARDAR
                if btn_guardar_cambios and es_super_usuario:
                    cambios = st.session_state.get("editor_csat_tabla", {}).get("edited_rows", {})
                
                    if cambios:
                        errores = 0
                        guardados = 0
                        for fila_idx, datos_editados in cambios.items():
                            chat_id = df_editor.iloc[int(fila_idx)]["id_str"]
                            id_chat_val = int(chat_id) if str(chat_id).isdigit() else chat_id
                            
                            payload_update = {}
                            if "motivo_normalizado" in datos_editados:
                                payload_update["motivo_normalizado"] = datos_editados["motivo_normalizado"]
                            if "ticket_relacionado" in datos_editados:
                                payload_update["ticket_relacionado"] = str(datos_editados["ticket_relacionado"]).strip()

                            if payload_update:
                                try:
                                    supabase.table("conversaciones")\
                                        .update(payload_update)\
                                        .eq("id", id_chat_val)\
                                        .execute()
                                    guardados += 1
                                except Exception as ex:
                                    errores += 1
                                    st.error(f"Error al actualizar chat {chat_id}: {ex}")
    
                        if guardados > 0:
                            st.success(f"✅ Se actualizaron {guardados} registros.")
                            st.cache_data.clear()
                            time_lib.sleep(0.5)
                            st.rerun()
                    else:
                        st.info("No se detectaron cambios pendientes para guardar.")
        st.markdown("---")

    # ==========================================
    # MÉTRICAS POR AGENTE EN DASHBOARD
    # ==========================================
    st.markdown("### Métricas por Agente & SLA Operativo")
    if not df_filtered.empty:
        df_f = df_filtered.drop_duplicates(subset=["id"]).copy()
        
        # Universos independientes para que cada métrica coincida con su Excel.
        # 1) Primera respuesta: NO exige cierre ni excluye "Sin Respuesta".
        #    Debe incluir exactamente los registros evaluables por SLA 1ra respuesta.
        # 2) Gestión: sí exige cierre y excluye "Sin Respuesta".
        etiquetas_f = df_f.get("etiquetas", pd.Series(index=df_f.index, dtype=str)).fillna("").astype(str).str.lower()
        es_sin_respuesta_f = etiquetas_f.str.contains("sin respuesta", na=False)
        agente_f = df_f.get("agente_asignado", pd.Series(index=df_f.index, dtype=str)).fillna("").astype(str).str.strip().str.lower()
        por_agente_f = df_f.get("por_agente", pd.Series(index=df_f.index, dtype=str)).fillna("").astype(str).str.strip().str.lower()

        # Primera respuesta: mismo universo que el Excel (SLA Normal O Extendido).
        # Importante: aquí NO exigimos cierre ni excluimos la etiqueta "Sin Respuesta".
        es_agente_valido_1ra = (
            (por_agente_f == "no excluido") &
            ~agente_f.isin(["", "sin asignar", "none", "nan", "monica", "monica (bot)", "soporte"])
        )
        dt_1ra = df_f["created_at_dt"]
        dia_1ra = dt_1ra.dt.dayofweek
        hora_1ra = dt_1ra.dt.time
        fecha_1ra = dt_1ra.dt.strftime("%Y-%m-%d")
        normal_1ra = (
            (dia_1ra.isin([0,1,2,3,4])) &
            (hora_1ra >= time(8,0)) & (hora_1ra <= time(17,30))
        ) | (
            (dia_1ra == 5) &
            (hora_1ra >= time(9,0)) & (hora_1ra <= time(11,45))
        )
        # extendido_1ra original guardado
        # extendido_1ra = (
        #     (dia_1ra.isin([0,1,2])) &
        #     ((hora_1ra >= time(19,0)) | (hora_1ra <= time(1,45)))
        # ) | (
        #     (dia_1ra.isin([3,4,5,6])) &
        #     ((hora_1ra >= time(18,0)) | (hora_1ra <= time(2,45)))
        # )
        
        # INTERRUPTOR TEMPORAL: Forzamos a False para ignorar horario extendido
        extendido_1ra = False
        no_feriado_1ra = ~fecha_1ra.isin(FERIADOS)
        es_horario_1ra = (normal_1ra | extendido_1ra) & no_feriado_1ra
        v_1ra = df_f[es_agente_valido_1ra & es_horario_1ra].copy()
        v_1ra["p_1ra_num"] = pd.to_numeric(v_1ra["primera_respuesta_min"], errors="coerce")
        s_1ra_total = v_1ra["p_1ra_num"].dropna()
        p_1r = round(s_1ra_total.mean(), 2) if not s_1ra_total.empty else 0.0

        # Gestión: mismas reglas que el Excel.
        es_agente_valido_gest = (
            (por_agente_f == "no excluido") &
            ~agente_f.isin(["", "sin asignar", "none", "nan", "monica", "monica (bot)"])
        )
        es_horario_gest = df_f["horario_evaluado"] != "fuera de horario"
        v_df = df_f[
            es_agente_valido_gest &
            es_horario_gest &
            (~es_sin_respuesta_f) &
            (df_f["es_cerrado"])
        ].copy()
        v_df["p_gest_num"] = pd.to_numeric(v_df["tiempo_resolucion_minutos"], errors="coerce")
        s_gest_total = v_df["p_gest_num"].dropna()
        p_gest = round(s_gest_total.mean(), 2) if not s_gest_total.empty else 0.0

        if "sla_1ra_eval" in df_f.columns:
            eval_1ra_rango = df_f[df_f["sla_1ra_eval"].isin(["cumple", "no cumple"])]
            if not eval_1ra_rango.empty:
                cumplen_1ra = (eval_1ra_rango["sla_1ra_eval"] == "cumple").sum()
                pct_sla_1ra_total = round((cumplen_1ra / len(eval_1ra_rango)) * 100, 1)
            else:
                pct_sla_1ra_total = 0.0
        else:
            pct_sla_1ra_total = 0.0

        if "sla_gest_eval" in df_f.columns:
            eval_gest_rango = df_f[df_f["sla_gest_eval"].isin(["cumple", "no cumple"])]
            if not eval_gest_rango.empty:
                cumplen_gest = (eval_gest_rango["sla_gest_eval"] == "cumple").sum()
                pct_sla_gest_total = round((cumplen_gest / len(eval_gest_rango)) * 100, 1)
            else:
                pct_sla_gest_total = 0.0
        else:
            pct_sla_gest_total = 0.0

        df_cerrados = df_f[df_f["es_cerrado"]]

        total_ingresados_tot = len(df_f)
        # "Humano": incluye a Monica y también conversaciones con etiqueta
        # "Sin Respuesta". Solo se excluyen bots/sin asignar.
        es_humano_f = (
            ~agente_f.isin(["", "sin asignar", "none", "monica", "mónica", "nan", "monica (bot)"])
        )
        ingresados_humanos = int(es_humano_f.sum())

        total_cerrados_tot = len(df_cerrados)
        cerrados_humanos = int((es_humano_f & df_f["es_cerrado"]).sum())

        es_cumplido_1ra = pct_sla_1ra_total >= 90.0
        color_1ra_val = "#34d399" if es_cumplido_1ra else "#f43f5e"
        border_1ra_card = "#10b981" if es_cumplido_1ra else "#ef4444"

        es_cumplido_gest = pct_sla_gest_total >= 90.0
        color_gest_val = "#34d399" if es_cumplido_gest else "#f43f5e"
        border_gest_card = "#10b981" if es_cumplido_gest else "#ef4444"

        def render_sla_card(title, value, sub_text, val_color="#f8fafc", border_color="#0284c7", icono="⚡"):
            return f"""
            <div class="metric-card" style="border-left: 4px solid {border_color}; min-height: 110px; display: flex; flex-direction: column; justify-content: space-between;">
                <div>
                    <div class="metric-card-title"><span>{icono}</span> {title}</div>
                    <div class="metric-card-value" style="color: {val_color};">{value}</div>
                </div>
                <div class="metric-card-sub" style="color: #94a3b8; font-size: 0.74rem;">{sub_text}</div>
            </div>
            """

        k1, k2, k3, k4, k5, k6 = st.columns(6)
        k1.markdown(render_sla_card("Prom. 1a Respuesta", f"{p_1r} min", f"Meta ≤ {sla_1ra_th} min", icono="⚡"), unsafe_allow_html=True)
        k2.markdown(render_sla_card("Prom. Gestión", f"{p_gest} min", f"Meta ≤ {sla_gest_th} min", icono="⏱️"), unsafe_allow_html=True)
        k3.markdown(render_sla_card("% SLA 1ra Resp.", f"{pct_sla_1ra_total}%", "Meta ≥ 90%", val_color=color_1ra_val, border_color=border_1ra_card, icono="🎯"), unsafe_allow_html=True)
        k4.markdown(render_sla_card("% SLA Gestión", f"{pct_sla_gest_total}%", "Meta ≥ 90%", val_color=color_gest_val, border_color=border_gest_card, icono="🏆"), unsafe_allow_html=True)
        k5.markdown(render_sla_card("Total Ingresados", f"{total_ingresados_tot}", f"Humano: {ingresados_humanos}", icono="📥"), unsafe_allow_html=True)
        k6.markdown(render_sla_card("Total Cerrados", f"{total_cerrados_tot}", f"Humano: {cerrados_humanos}", icono="✅"), unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        res_agentes = []
        for agente, grp in df_f.groupby("agente_asignado"):
            asig_totales = len(grp)
            cerrados_totales = len(grp[grp["es_cerrado"]])
            
            agente_g = grp.get("agente_asignado", pd.Series(index=grp.index, dtype=str)).fillna("").astype(str).str.strip().str.lower()
            etiquetas_g = grp.get("etiquetas", pd.Series(index=grp.index, dtype=str)).fillna("").astype(str).str.lower()
            es_sin_respuesta_g = etiquetas_g.str.contains("sin respuesta", na=False)
            por_agente_g = grp.get("por_agente", pd.Series(index=grp.index, dtype=str)).fillna("").astype(str).str.strip().str.lower()
            es_agente_valido_g = (por_agente_g == "no excluido") & ~agente_g.isin(["", "sin asignar", "none", "nan", "monica", "monica (bot)"])

            # Primera respuesta: mismo criterio que el promedio general.
            v_1ra_g = grp[es_agente_valido_g & (grp["horario_evaluado"] != "fuera de horario")].copy()
            v_1ra_g["p_1ra_num"] = pd.to_numeric(v_1ra_g["primera_respuesta_min"], errors="coerce")

            # Gestión: mismas exclusiones que Excel.
            v_g = grp[
                es_agente_valido_g
                & (grp["horario_evaluado"] != "fuera de horario")
                & (~es_sin_respuesta_g)
                & (grp["es_cerrado"])
            ].copy()
            v_g["p_gest_num"] = pd.to_numeric(v_g["tiempo_resolucion_minutos"], errors="coerce")

            s_1ra = v_1ra_g["p_1ra_num"].dropna()
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

        df_res_agentes = pd.DataFrame(res_agentes)
        if not df_res_agentes.empty:
            df_res_agentes = df_res_agentes.sort_values(by="Asignados", ascending=False)
            st.dataframe(df_res_agentes, use_container_width=True)
        else:
            st.info("No hay datos de agentes disponibles para el periodo seleccionado.")
    else:
        st.info("No hay chats registrados en la base de datos para mostrar métricas por agente.")

    # ==============================================================================
    # LISTADO DETALLADO: CHATS CON PRIMERA RESPUESTA FUERA DE SLA
    # ==============================================================================
    st.markdown("---")

    # Tomamos el umbral configurado de SLA 1ra Respuesta
    umbral_sla_1ra_resp = float(st.session_state.get("sla_1ra_th", 2.0))

    if not df_filtered.empty:
        df_fuera_eval = df_filtered.copy()
        
        # 1. Aplicar la misma evaluación exacta que el Excel para 1ra Respuesta
        df_fuera_eval["sla_eval_excel"] = df_fuera_eval.apply(
            lambda r: evaluar_sla_normal_excel(r, umbral_sla_1ra_resp), axis=1
        )
        
        # 2. Filtrar únicamente chats que fueron EVALUADOS dentro de jornada y NO CUMPLIERON
        # (Esto excluye automáticamente los que fueron fuera de horario, feriados o de bots)
        df_criticos_fuera = df_fuera_eval[df_fuera_eval["sla_eval_excel"] == "no cumple"].copy()

        # 3. Conversión para etiquetas en pantalla
        df_criticos_fuera["1ra_resp_num"] = pd.to_numeric(df_criticos_fuera["primera_respuesta_min"], errors="coerce")
        now_dt_fuera = pd.Timestamp.now(tz="America/Asuncion")
        calc_min_fuera = (now_dt_fuera - df_criticos_fuera["created_at_dt"]).dt.total_seconds() / 60.0
        df_criticos_fuera["min_espera_calc"] = calc_min_fuera.apply(lambda x: round(max(0.0, x), 1) if pd.notna(x) else 0.0)

        with st.expander(f"⚠️ Chats fuera de objetivo SLA 1ra Respuesta (> {umbral_sla_1ra_resp} min) [{len(df_criticos_fuera)}]", expanded=False):
            if not df_criticos_fuera.empty:
                # Clasificacion clara para cada registro
                df_criticos_fuera["estado_sla"] = df_criticos_fuera.apply(
                    lambda r: f"Atendido tarde ({round(r['1ra_resp_num'], 1)} min)" if pd.notna(r['1ra_resp_num']) else f"🚨 Pendiente ({r['min_espera_calc']} min)",
                    axis=1
                )

                cols_mostrar_fuera = [
                    "intercom_url", "nombre_contacto", "tenant", 
                    "agente_asignado", "estado_sla", "created_at_fmt", "estado"
                ]
                
                if "canal" in df_criticos_fuera.columns:
                    cols_mostrar_fuera.append("canal")

                st.dataframe(
                    df_criticos_fuera.reindex(columns=cols_mostrar_fuera).dropna(how="all", axis=1),
                    column_config={
                        "intercom_url": st.column_config.LinkColumn(
                            "ID Conversación", 
                            display_text=r".*/(\d+)"
                        ),
                        "nombre_contacto": st.column_config.TextColumn("Contacto"),
                        "tenant": st.column_config.TextColumn("Tenant"),
                        "agente_asignado": st.column_config.TextColumn("Agente Asignado"),
                        "estado_sla": st.column_config.TextColumn("Estado SLA 1ra Resp."),
                        "created_at_fmt": st.column_config.TextColumn("Fecha Creación/Derivación"),
                        "estado": st.column_config.TextColumn("Estado Chat"),
                        "canal": st.column_config.TextColumn("Canal")
                    },
                    hide_index=True,
                    use_container_width=True
                )
            else:
                st.success("🟢 ¡Excelente! Todos los chats evaluados en este período cumplen con el SLA de primera respuesta.")
    
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
        
        if "min_transcurridos" not in df_abiertos_filtrados.columns and "created_at_dt" in df_abiertos_filtrados.columns:
            df_abiertos_filtrados["min_transcurridos"] = ((now_dt - df_abiertos_filtrados["created_at_dt"]).dt.total_seconds() / 60).round(1)
            
        if "min_transcurridos" in df_abiertos_filtrados.columns:
            df_abiertos_filtrados["Horas Transcurridas"] = (df_abiertos_filtrados["min_transcurridos"] / 60).round(1)
        else:
            df_abiertos_filtrados["Horas Transcurridas"] = 0.0

        if "created_at_dt" in df_abiertos_filtrados.columns:
            df_abiertos_filtrados = df_abiertos_filtrados.sort_values(by="created_at_dt", ascending=True)

        cols_mostrar_filt = ["intercom_url", "created_at_fmt", "agente_asignado", "Horas Transcurridas", "ai_title",
                             "nombre_contacto", "tenant", "company"]

        st.dataframe(
            df_abiertos_filtrados.reindex(columns=cols_mostrar_filt).dropna(how="all", axis=1),
            column_config={
                "intercom_url": st.column_config.LinkColumn("ID Conversacion", display_text=r".*/(\d+)"),
                "created_at_fmt": "Fecha Creacion", 
                "agente_asignado": "Agente Asignado",
                "Horas Transcurridas": "Horas Abierto",
                "ai_title": "AI Title",
                "nombre_contacto": "Contacto",
                "tenant": "Tenant",
                "company": "Company",
            },
            hide_index=True, use_container_width=True, key="tabla_ranking_abiertos_filtrados"
        )
    else:
        st.info(f"No hay chats abiertos pendientes creados en el rango {texto_rango_abiertos}.")

    st.markdown("---")

    # ==========================================
    # RANKING DE CHATS ABIERTOS GENERAL HISTÓRICO CON FILTRO MULTI-AGENTE
    # ==========================================
    if not df_abiertos_all.empty:
        # Exclusión explícita de Mónica, Soporte y sin asignar
        agentes_omitidos = ["monica", "mónica", "monica (bot)", "sin asignar", "none", "nan", ""]
        df_abiertos_rank_filtro = df_abiertos_all[
            ~df_abiertos_all["agente_asignado"].fillna("").astype(str).str.strip().str.lower().isin(agentes_omitidos)
        ].copy()
    else:
        df_abiertos_rank_filtro = pd.DataFrame()

    cant_abiertos_gen = len(df_abiertos_rank_filtro) if not df_abiertos_rank_filtro.empty else 0
    st.markdown(f"### Ranking General de Chats Abiertos (Histórico Pendiente) — {cant_abiertos_gen} chats")
    
    if not df_abiertos_rank_filtro.empty:
        df_rank = df_abiertos_rank_filtro.copy()

        agentes_abiertos_unicos = sorted([
            str(a).strip() for a in df_rank["agente_asignado"].dropna().unique() if str(a).strip() != ""
        ])
        
        agentes_rank_sel = st.multiselect(
            "Filtrar Ranking por Agente(s) Asignado(s):",
            options=agentes_abiertos_unicos,
            default=[],
            placeholder="Todos los agentes (Por defecto)",
            key="multiselect_agentes_ranking_gen"
        )

        if agentes_rank_sel:
            df_rank = df_rank[df_rank["agente_asignado"].isin(agentes_rank_sel)]

        if "min_transcurridos" not in df_rank.columns and "created_at_dt" in df_rank.columns:
            df_rank["min_transcurridos"] = ((now_dt - df_rank["created_at_dt"]).dt.total_seconds() / 60).round(1)
            
        if "min_transcurridos" in df_rank.columns:
            df_rank["Horas Transcurridas"] = (df_rank["min_transcurridos"] / 60).round(1)
        else:
            df_rank["Horas Transcurridas"] = 0.0

        if "created_at_dt" in df_rank.columns:
            df_rank = df_rank.sort_values(by="created_at_dt", ascending=True)

        cols_deseadas = ["intercom_url", "ai_title", "created_at_fmt", "agente_asignado", "Horas Transcurridas", 
                         "nombre_contacto", "tenant", "company"]

        st.dataframe(
            df_rank.reindex(columns=cols_deseadas).dropna(how="all", axis=1),
            column_config={
                "intercom_url": st.column_config.LinkColumn("ID Conversación", display_text=r".*/(\d+)"),
                "ai_title": "AI Title",
                "created_at_fmt": "Fecha Creación", 
                "agente_asignado": "Agente Asignado",
                "Horas Transcurridas": "Horas Abierto",
                "nombre_contacto": "Contacto",
                "tenant": "Tenant",
                "company": "Company",
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

                st.dataframe(
                    df_busqueda.reindex(columns=cols_search).dropna(how="all", axis=1),
                    column_config={
                        "intercom_url": st.column_config.LinkColumn("ID Conversacion", display_text=r".*/(\d+)"),
                        "Estado_Texto": "Estado",
                        "created_at_fmt": "Fecha Creacion",
                        "agente_asignado": "Agente Asignado",
                        "nombre_contacto": "Contacto",
                        "tenant": "Tenant",
                        "company": "Company",
                    },
                    hide_index=True, use_container_width=True, key="tabla_busqueda_tenant_agente"
                )
            else:
                st.info("No se encontraron registros que coincidan exactamente con la seleccion.")
        else:
            st.caption("Selecciona al menos un Tenant o Agente arriba para desplegar los resultados.")
    else:
        st.info("Sin datos para el buscador.")

with tab_carga_vivo:
    st.markdown("### Distribución y Carga de Trabajo del Equipo (Tiempo Real)")
    st.caption("Panel en vivo pensado para el asignador. Muestra la cantidad de chats abiertos activos y resueltos hoy por cada agente.")
    renderizar_panel_carga_asignador()

with tab_resumen:
    f_desde_v, f_hasta_v = pd.to_datetime(fecha_desde).date(), pd.to_datetime(fecha_hasta).date()
    if not df_all.empty and "fecha_solo" in df_all.columns:
        df_filtered_r = df_all[(df_all["fecha_solo"] >= f_desde_v) & (df_all["fecha_solo"] <= f_hasta_v)].copy()
        if usar_filtro_hora and not df_filtered_r.empty:
            df_filtered_r = df_filtered_r[(df_filtered_r["hora_solo"] >= hora_inicio) & (df_filtered_r["hora_solo"] <= hora_fin)]
    else:
        df_filtered_r = pd.DataFrame()

    st.markdown(f"### Análisis Operativo & Métricas (`{f_desde_v}` al `{f_hasta_v}`)")
    
    if not df_filtered_r.empty:
        df_res = df_filtered_r.copy().sort_values(by="created_at_dt", ascending=True)
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
        r1.markdown(f'<div class="metric-card" style="border-left: 4px solid #0284c7;"><div class="metric-card-title">Total Chats en Rango</div><div class="metric-card-value">{total_chats_periodo:,}</div><div class="metric-card-sub">{num_dias} días consultados</div></div>', unsafe_allow_html=True)
        r2.markdown(f'<div class="metric-card" style="border-left: 4px solid #38bdf8;"><div class="metric-card-title">Promedio Diario</div><div class="metric-card-value">{promedio_diario}</div><div class="metric-card-sub">chats por jornada</div></div>', unsafe_allow_html=True)
        r3.markdown(f'<div class="metric-card" style="border-left: 4px solid #10b981;"><div class="metric-card-title">Agente con Más Chats</div><div class="metric-card-value" style="font-size:1.35rem; color:#34d399;">{top_agente}</div><div class="metric-card-sub">{top_agente_count} chats atendidos</div></div>', unsafe_allow_html=True)
        r4.markdown(f'<div class="metric-card" style="border-left: 4px solid #818cf8;"><div class="metric-card-title">Participación Top Agente</div><div class="metric-card-value">{pct_top}%</div><div class="metric-card-sub">del total acumulado</div></div>', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        palette_mate = ["#0284c7", "#38bdf8", "#0ea5e9", "#818cf8", "#34d399", "#fbbf24", "#f87171", "#94a3b8"]
        
        # ==========================================
        # 1. BLOQUE GENERAL DE AGENTES Y EVOLUCIÓN
        # ==========================================
        g_pie, g_bar = st.columns([1, 1.2])

        with g_pie:
            st.markdown("#### Distribución de Chats por Agente")
            fig_pie = px.pie(
                df_agentes_total, 
                values="Cantidad de Chats", 
                names="Agente", 
                hole=0.5, 
                color_discrete_sequence=palette_mate
            )
            fig_pie.update_traces(
                textposition='inside', 
                textinfo='percent', 
                hovertemplate="<b>%{label}</b><br>Chats: %{value}<br>Porcentaje: %{percent}<extra></extra>", 
                marker=dict(line=dict(color='#0b1120', width=2))
            )
            fig_pie.update_layout(
                showlegend=True, 
                paper_bgcolor="rgba(0,0,0,0)", 
                plot_bgcolor="rgba(0,0,0,0)", 
                font=dict(color="#cbd5e1", size=11), 
                height=360, 
                legend=dict(orientation="v", y=0.5, x=1.05),
                margin=dict(t=20, b=20, l=10, r=10)
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
                color_discrete_sequence=palette_mate
            )
            fig_bar.update_traces(marker=dict(cornerradius=4))
            fig_bar.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", 
                plot_bgcolor="rgba(0,0,0,0)", 
                font=dict(color="#cbd5e1", size=11), 
                height=360, 
                xaxis=dict(gridcolor="#1e293b", title=None),
                yaxis=dict(gridcolor="#1e293b", title="Cantidad de Chats"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, title=None),
                margin=dict(t=20, b=20, l=20, r=20)
            )
            st.plotly_chart(fig_bar, use_container_width=True)

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("#### Tabla General de Chats por Día y Agente")
        df_pivot = df_res.pivot_table(index="Dia", columns="agente_asignado", values="id", aggfunc="count", fill_value=0)
        df_pivot["TOTAL CHATS"] = df_pivot.sum(axis=1)
        st.dataframe(df_pivot, use_container_width=True)

        st.markdown("---")

        # ==========================================
        # 2. ANTIGÜEDAD DE CHATS PENDIENTES HISTÓRICOS POR DÍA
        # ==========================================
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("#### Antigüedad de Chats Pendientes por Día")

        df_abiertos_historicos = df_all[~df_all["es_cerrado"]].copy() if not df_all.empty and "es_cerrado" in df_all.columns else pd.DataFrame()

        if not df_abiertos_historicos.empty:
            agentes_excluidos = ["monica", "mónica", "monica (bot)", "soporte", "sin asignar", "none", "nan", ""]
            df_abiertos_filtro = df_abiertos_historicos[
                ~df_abiertos_historicos["agente_asignado"].fillna("").astype(str).str.strip().str.lower().isin(agentes_excluidos)
            ].copy()

            if not df_abiertos_filtro.empty:
                now_py_resumen = pd.Timestamp.now(tz="America/Asuncion")
                df_abiertos_filtro["dias_antiguedad_num"] = df_abiertos_filtro.apply(
                    lambda r: (calcular_minutos_habiles_rapido(r["created_at_dt"], now_py_resumen) / 60.0 / 8.0),
                    axis=1
                ).fillna(0.0)

                def clasificar_antiguedad(dias_habiles):
                    if dias_habiles < 1.0: 
                        return "0 días (Hoy)"
                    elif dias_habiles < 2.0: 
                        return "1 día hábil"
                    elif dias_habiles < 3.0: 
                        return "2 días hábiles"
                    elif dias_habiles <= 4.0: 
                        return "3 días hábiles"
                    else: 
                        return ">3 días hábiles"

                df_abiertos_filtro["Rango_Antiguedad"] = df_abiertos_filtro["dias_antiguedad_num"].apply(clasificar_antiguedad)
                df_antiguedad_grp = df_abiertos_filtro.groupby(["agente_asignado", "Rango_Antiguedad"]).size().reset_index(name="Cantidad")

                orden_rangos = ["0 días (Hoy)", "1 día hábil", "2 días hábiles", "3 días hábiles", ">3 días hábiles"]
                color_map_antiguedad = {
                    "0 días (Hoy)": "#10b981",
                    "1 día hábil": "#38bdf8",
                    "2 días hábiles": "#fbbf24",
                    "3 días hábiles": "#f97316",
                    ">3 días hábiles": "#ef4444"
                }

                fig_antiguedad = px.bar(
                    df_antiguedad_grp,
                    x="agente_asignado",
                    y="Cantidad",
                    color="Rango_Antiguedad",
                    category_orders={"Rango_Antiguedad": orden_rangos},
                    color_discrete_map=color_map_antiguedad,
                    barmode="stack",
                    text="Cantidad"
                )

                fig_antiguedad.update_traces(textposition="inside", marker=dict(cornerradius=4))
                fig_antiguedad.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", 
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#cbd5e1", size=12),
                    xaxis=dict(gridcolor="#1e293b", title=None),
                    yaxis=dict(gridcolor="#1e293b", title="Cantidad de Chats Abiertos"),
                    legend_title_text="Antigüedad", 
                    height=380,
                    margin=dict(t=20, b=20, l=20, r=20)
                )

                st.plotly_chart(fig_antiguedad, use_container_width=True)
            else:
                st.info("🟢 No existen chats abiertos asignados a agentes humanos actualmente.")
        else:
            st.info("🟢 No existen chats abiertos pendientes en el sistema.")

        st.markdown("---")

        # ==========================================
        # 3. ANTIGÜEDAD DE CHATS PENDIENTES POR FRANJA HORARIA
        # ==========================================
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("#### Antigüedad de Chats Pendientes por Hora")

        df_abiertos_horas = df_all[~df_all["es_cerrado"]].copy() if not df_all.empty and "es_cerrado" in df_all.columns else pd.DataFrame()

        if not df_abiertos_horas.empty:
            agentes_excluidos_hs = ["monica", "mónica", "monica (bot)", "soporte", "sin asignar", "none", "nan", ""]
            df_abiertos_hs_filtro = df_abiertos_horas[
                ~df_abiertos_horas["agente_asignado"].fillna("").astype(str).str.strip().str.lower().isin(agentes_excluidos_hs)
            ].copy()

            if not df_abiertos_hs_filtro.empty:
                now_py_horas = pd.Timestamp.now(tz="America/Asuncion")
                df_abiertos_hs_filtro["horas_abierto_num"] = df_abiertos_hs_filtro.apply(
                    lambda r: (calcular_minutos_habiles_rapido(r["created_at_dt"], now_py_horas) / 60.0),
                    axis=1
                ).fillna(0.0)

                def clasificar_franja_horas(horas):
                    if horas <= 1.0:
                        return "0 a 1 hora"
                    elif horas <= 3.0:
                        return ">1 a 3 horas"
                    elif horas <= 5.0:
                        return ">3 a 5 horas"
                    else:
                        return ">5 horas"

                df_abiertos_hs_filtro["Franja_Horaria"] = df_abiertos_hs_filtro["horas_abierto_num"].apply(clasificar_franja_horas)
                df_horas_grp = df_abiertos_hs_filtro.groupby(["agente_asignado", "Franja_Horaria"]).size().reset_index(name="Cantidad")

                orden_franjas = ["0 a 1 hora", ">1 a 3 horas", ">3 a 5 horas", ">5 horas"]
                color_map_horas = {
                    "0 a 1 hora": "#10b981",
                    ">1 a 3 horas": "#fbbf24",
                    ">3 a 5 horas": "#f97316",
                    ">5 horas": "#ef4444"
                }

                fig_horas = px.bar(
                    df_horas_grp,
                    x="agente_asignado",
                    y="Cantidad",
                    color="Franja_Horaria",
                    category_orders={"Franja_Horaria": orden_franjas},
                    color_discrete_map=color_map_horas,
                    barmode="stack",
                    text="Cantidad"
                )

                fig_horas.update_traces(textposition="inside", marker=dict(cornerradius=4))
                fig_horas.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", 
                    plot_bgcolor="rgba(0,0,0,0)", 
                    font=dict(color="#cbd5e1", size=12),
                    xaxis=dict(gridcolor="#1e293b", title=None),
                    yaxis=dict(gridcolor="#1e293b", title="Cantidad de Chats Abiertos"),
                    legend_title_text="Franja Horaria", 
                    height=380,
                    margin=dict(t=20, b=20, l=20, r=20)
                )

                st.plotly_chart(fig_horas, use_container_width=True)
            else:
                st.info("🟢 No existen chats abiertos asignados a agentes humanos actualmente.")
        else:
            st.info("🟢 No existen chats abiertos pendientes en el sistema.")

        st.markdown("---")

        # ==========================================
        # 4. ANÁLISIS DE EVALUACIONES NEGATIVAS (<=3 ★)
        # ==========================================
        st.markdown("### 📊 Análisis de Evaluaciones Negativas (1, 2 y 3 ★)")
        
        df_res["rating_num"] = pd.to_numeric(df_res.get("rating"), errors="coerce")
        df_negativos_res = df_res[df_res["rating_num"] <= 3].copy()

        if not df_negativos_res.empty:
            g_motivo, g_nivel = st.columns([1.1, 1])

            # A. Por Motivo Normalizado
            with g_motivo:
                st.markdown("#### Evaluaciones Negativas por Motivo")
                df_motivos_cnt = df_negativos_res.get("motivo_normalizado", pd.Series(dtype=str)).fillna("Sin categorizar").value_counts().reset_index()
                df_motivos_cnt.columns = ["Motivo Normalizado", "Cantidad"]

                fig_motivos = px.bar(
                    df_motivos_cnt, 
                    x="Cantidad", 
                    y="Motivo Normalizado", 
                    orientation="h",
                    color="Cantidad",
                    color_continuous_scale=["#f97316", "#ef4444"],
                    text="Cantidad"
                )
                fig_motivos.update_traces(
                    textposition="outside", 
                    marker=dict(cornerradius=4, line=dict(color="#0b1120", width=1))
                )
                fig_motivos.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", 
                    plot_bgcolor="rgba(0,0,0,0)", 
                    font=dict(color="#cbd5e1", size=11), 
                    height=360, 
                    xaxis=dict(gridcolor="#1e293b", title=None),
                    yaxis=dict(autorange="reversed", gridcolor="#1e293b", title=None),
                    coloraxis_showscale=False,
                    margin=dict(t=20, b=20, l=10, r=20)
                )
                st.plotly_chart(fig_motivos, use_container_width=True)

            # B. Por Nivel de Soporte
            with g_nivel:
                st.markdown("#### Evaluaciones Negativas por Nivel")
                niveles_series = (
                    df_negativos_res.get("nivel", pd.Series(dtype=str))
                    .fillna("Niv-L1")
                    .replace(["", "None", "nan", "null", "Sin Nivel"], "Niv-L1")
                )
    
                df_nivel_cnt = niveles_series.value_counts().reset_index()
                df_nivel_cnt.columns = ["Nivel", "Cantidad"]

                fig_nivel = px.bar(
                    df_nivel_cnt, 
                    x="Nivel", 
                    y="Cantidad", 
                    color="Nivel",
                    color_discrete_map={"Niv-L1": "#38bdf8", "Niv-L2": "#0284c7", "Niv-L3": "#ef4444"},
                    text="Cantidad"
                )
                fig_nivel.update_traces(textposition="outside", marker=dict(cornerradius=6))
                fig_nivel.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", 
                    plot_bgcolor="rgba(0,0,0,0)", 
                    font=dict(color="#cbd5e1", size=11), 
                    height=360, 
                    xaxis=dict(gridcolor="#1e293b", title=None),
                    yaxis=dict(gridcolor="#1e293b", title=None, showticklabels=False),
                    showlegend=False,
                    margin=dict(t=20, b=20, l=10, r=20)
                )
                st.plotly_chart(fig_nivel, use_container_width=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # C. Ticket Relacionado
            st.markdown("#### Evaluaciones Negativas por Ticket Relacionado")
            tickets_series = df_negativos_res.get("ticket_relacionado", pd.Series(dtype=str)).fillna("").astype(str).str.strip()
            tickets_validos = tickets_series[~tickets_series.isin(["", "None", "nan", "null"])]

            if not tickets_validos.empty:
                df_tickets_cnt = tickets_validos.value_counts().reset_index()
                df_tickets_cnt.columns = ["Ticket / Tarea", "Cantidad"]

                fig_ticket = px.bar(
                    df_tickets_cnt, 
                    x="Cantidad", 
                    y="Ticket / Tarea", 
                    orientation="h",
                    color="Cantidad",
                    color_continuous_scale=["#38bdf8", "#0284c7"],
                    text="Cantidad"
                )
                fig_ticket.update_traces(
                    textposition="outside", 
                    marker=dict(cornerradius=4, line=dict(color="#0b1120", width=1))
                )
                fig_ticket.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", 
                    plot_bgcolor="rgba(0,0,0,0)", 
                    font=dict(color="#cbd5e1", size=11), 
                    height=300, 
                    xaxis=dict(gridcolor="#1e293b", title=None),
                    yaxis=dict(autorange="reversed", gridcolor="#1e293b", title=None),
                    coloraxis_showscale=False,
                    margin=dict(t=20, b=20, l=10, r=20)
                )
                st.plotly_chart(fig_ticket, use_container_width=True)
            else:
                st.info("No hay tickets/tareas vinculadas a las calificaciones negativas del período.")
        else:
            st.info("🟢 No se registraron evaluaciones negativas (1, 2 o 3 ★) para el período seleccionado.")

        st.markdown("---")

# ==========================================
# FUNCIÓN AISLADA DEL WBR CON ST.FRAGMENT (BLINDADA CONTRA NameError)
# ==========================================
@st.fragment
def renderizar_informe_wbr(df_base_in):
    st.markdown("""
        <div style="display: flex; align-items: center; gap: 12px;">
            <div>
                <h3 style="margin: 0; font-size: 1.35rem; font-weight: 700; color: #f8fafc; letter-spacing: -0.3px;">
                    Informe Ejecutivo Semanal
                </h3>
            </div>
        </div>
    """, unsafe_allow_html=True)

    if df_base_in.empty or "created_at_dt" not in df_base_in.columns:
        st.info("No hay datos históricos suficientes cargados en la base de datos para generar el WBR.")
        return

    df_wbr_base = df_base_in.copy()

    # Normalizaciones de fechas
    if "fecha_cierre_dt" not in df_wbr_base.columns:
        df_wbr_base["fecha_ref"] = df_wbr_base["fecha_cierre"].fillna(df_wbr_base["updated_at"])
        ref_dt = pd.to_datetime(df_wbr_base["fecha_ref"], errors="coerce", utc=True)
        df_wbr_base["fecha_cierre_dt"] = ref_dt.dt.tz_convert("America/Asuncion")

    df_wbr_base["iso_year"] = df_wbr_base["created_at_dt"].dt.isocalendar().year
    df_wbr_base["iso_week"] = df_wbr_base["created_at_dt"].dt.isocalendar().week
    df_wbr_base["semana_str"] = df_wbr_base.apply(lambda r: f"Semana {r['iso_week']:02d} ({r['iso_year']})", axis=1)

    df_wbr_base["close_iso_year"] = df_wbr_base["fecha_cierre_dt"].dt.isocalendar().year
    df_wbr_base["close_iso_week"] = df_wbr_base["fecha_cierre_dt"].dt.isocalendar().week

    semanas_disponibles = (
        df_wbr_base[["iso_year", "iso_week", "semana_str"]]
        .drop_duplicates()
        .sort_values(by=["iso_year", "iso_week"], ascending=[False, False])
    )

    opciones_semanas = semanas_disponibles["semana_str"].tolist()
    if not opciones_semanas:
        st.info("No hay semanas disponibles en el registro.")
        return

    col_selector, col_separador, col_boton_slot = st.columns([2.2, 0.8, 1.8])
    with col_selector:
        semana_seleccionada_str = st.selectbox(
            "Seleccionar Semana del Reporte WBR:",
            options=opciones_semanas,
            index=0,
            key="sel_wbr_week"
        )

    contenedor_descarga = col_boton_slot.empty()

    if not semana_seleccionada_str:
        return

    fila_sem = semanas_disponibles[semanas_disponibles["semana_str"] == semana_seleccionada_str].iloc[0]
    target_year = int(fila_sem["iso_year"])
    target_week = int(fila_sem["iso_week"])

    from datetime import datetime as dt_calc
    fecha_ref_sem = dt_calc.fromisocalendar(target_year, target_week, 1)

    semanas_5 = []
    for i in range(4, -1, -1):
        f_s = fecha_ref_sem - timedelta(weeks=i)
        iso_y, iso_w, _ = f_s.isocalendar()
        semanas_5.append((iso_y, iso_w, f"Semana {iso_w}"))

    df_5_semanas_info = pd.DataFrame(semanas_5, columns=["iso_year", "iso_week", "etiqueta_semana"])

    # -------------------------------------------------------------
    # 1. CÁLCULO DEL EVOLUTIVO DE 5 SEMANAS
    # -------------------------------------------------------------
    res_5w = []
    tot_5w_recibidos = 0
    tot_5w_cerrados = 0

    for _, s_row in df_5_semanas_info.iterrows():
        sub_rec = df_wbr_base[
            (df_wbr_base["iso_year"] == s_row["iso_year"]) & 
            (df_wbr_base["iso_week"] == s_row["iso_week"])
        ]
        rec = len(sub_rec)

        sub_cer = df_wbr_base[
            (df_wbr_base["es_cerrado"] == True) &
            (df_wbr_base["close_iso_year"] == s_row["iso_year"]) & 
            (df_wbr_base["close_iso_week"] == s_row["iso_week"])
        ]
        cer = len(sub_cer)

        tot_5w_recibidos += rec
        tot_5w_cerrados += cer

        res_5w.append({
            "Semana": s_row["etiqueta_semana"],
            "Recibidos": rec,
            "Cerrados": cer
        })

    df_res_5w = pd.DataFrame(res_5w)
    pct_5w_cerrados = round((tot_5w_cerrados / tot_5w_recibidos * 100), 2) if tot_5w_recibidos > 0 else 0.0

    st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

    # =========================================================================
    # SLIDE 1: EVOLUTIVO VOLUMEN (5 SEMANAS)
    # =========================================================================
    st.markdown("#### 1. WBR | Soporte & Experiencia — Evolutivo de Volumen")

    s1_col1, s1_col2 = st.columns(2)
    s1_col1.markdown(f"""
    <div class="metric-card" style="border-left: 4px solid #0284c7; text-align: center; height: 110px; display: flex; flex-direction: column; justify-content: center;">
        <div class="metric-card-title" style="justify-content: center;">CHATS RECIBIDOS (5 SEMANAS)</div>
        <div class="metric-card-value" style="font-size: 2.1rem; color: #f8fafc;">{tot_5w_recibidos:,}</div>
    </div>
    """, unsafe_allow_html=True)

    s1_col2.markdown(f"""
    <div class="metric-card" style="border-left: 4px solid #10b981; text-align: center; height: 110px; display: flex; flex-direction: column; justify-content: center;">
        <div class="metric-card-title" style="justify-content: center;">PROMEDIO % CHATS CERRADOS (5 SEMANAS)</div>
        <div class="metric-card-value" style="font-size: 2.1rem; color: #34d399;">
            {pct_5w_cerrados}% <span style="font-size: 1.15rem; color: #94a3b8; font-weight: 500;">({tot_5w_cerrados:,} cerrados)</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    fig_5w = go.Figure()
    fig_5w.add_trace(go.Bar(
        x=df_res_5w["Semana"],
        y=df_res_5w["Recibidos"],
        name="Q. de chats recibidos",
        marker=dict(color="#0284c7", cornerradius=8),
        text=df_res_5w["Recibidos"],
        textposition="inside",
        insidetextfont=dict(color="#ffffff", size=13, family="Arial Black")
    ))
    fig_5w.add_trace(go.Bar(
        x=df_res_5w["Semana"],
        y=df_res_5w["Cerrados"],
        name="Q. de chats cerrados",
        marker=dict(color="#38bdf8", cornerradius=8),
        text=df_res_5w["Cerrados"],
        textposition="inside",
        insidetextfont=dict(color="#0f172a", size=13, family="Arial Black")
    ))
    fig_5w.update_layout(
        hovermode=False,
        barmode="group",
        title="<b>Q. de chats recibidos y Q. de chats cerrados (Fotografía Semanal Lunes a Domingo)</b>",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#cbd5e1"),
        xaxis=dict(gridcolor="#1e293b"),
        yaxis=dict(showgrid=False, showticklabels=False, zeroline=False, showline=False, title=None),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=50, b=20, l=30, r=20),
        height=380
    )
    st.plotly_chart(fig_5w, use_container_width=True)

    st.markdown("---")

    # =========================================================================
    # SLIDE 2: DETALLE OPERATIVO DE LA SEMANA SELECCIONADA
    # =========================================================================
    st.markdown(f"#### 2. WBR | {semana_seleccionada_str} — Detalle Operativo Semanal")

    df_target = df_wbr_base[
        (df_wbr_base["iso_year"] == target_year) & 
        (df_wbr_base["iso_week"] == target_week)
    ].copy()
    tot_sem_rec = len(df_target)

    df_target_cerrados = df_wbr_base[
        (df_wbr_base["es_cerrado"] == True) &
        (df_wbr_base["close_iso_year"] == target_year) & 
        (df_wbr_base["close_iso_week"] == target_week)
    ].copy()
    tot_sem_cer = len(df_target_cerrados)
    pct_sem_cer = round((tot_sem_cer / tot_sem_rec * 100), 2) if tot_sem_rec > 0 else 0.0

    sem_prev_num = (fecha_ref_sem - timedelta(weeks=1)).isocalendar()[1]
    sem_prev_year = (fecha_ref_sem - timedelta(weeks=1)).isocalendar()[0]
    
    tot_prev_rec = len(df_wbr_base[
        (df_wbr_base["iso_year"] == sem_prev_year) & 
        (df_wbr_base["iso_week"] == sem_prev_num)
    ])
    tot_prev_cer = len(df_wbr_base[
        (df_wbr_base["es_cerrado"] == True) &
        (df_wbr_base["close_iso_year"] == sem_prev_year) & 
        (df_wbr_base["close_iso_week"] == sem_prev_num)
    ])

    diff_rec_pct = round(((tot_sem_rec - tot_prev_rec) / tot_prev_rec * 100), 2) if tot_prev_rec > 0 else 0.0
    diff_cer_pct = round(((tot_sem_cer - tot_prev_cer) / tot_prev_cer * 100), 2) if tot_prev_cer > 0 else 0.0

    s3_m1, s3_m2 = st.columns(2)
    s3_m1.markdown(f"""
    <div class="metric-card" style="border-left: 4px solid #0284c7; text-align: center; height: 110px; display: flex; flex-direction: column; justify-content: center;">
        <div class="metric-card-title" style="justify-content: center;">CHATS RECIBIDOS</div>
        <div class="metric-card-value" style="font-size: 2.1rem; color: #f8fafc;">{tot_sem_rec:,}</div>
    </div>
    """, unsafe_allow_html=True)

    s3_m2.markdown(f"""
    <div class="metric-card" style="border-left: 4px solid #10b981; text-align: center; height: 110px; display: flex; flex-direction: column; justify-content: center;">
        <div class="metric-card-title" style="justify-content: center;">CHATS CERRADOS Y EFECTIVIDAD</div>
        <div class="metric-card-value" style="font-size: 2.1rem; color: #34d399;">
            {tot_sem_cer:,} <span style="font-size: 1.25rem; font-weight: 600; color: #38bdf8;">({pct_sem_cer}%)</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    col_dia_bar, col_pie_ag = st.columns([1.2, 1])

    with col_dia_bar:
        st.markdown("##### Chats Recibidos vs. Chats Cerrados (Día a Día)")

        dias_semana_l_d = [fecha_ref_sem.date() + timedelta(days=d) for d in range(7)]
        df_target["creado_dia"] = df_target["created_at_dt"].dt.date
        df_target_cerrados["cerrado_dia"] = df_target_cerrados["fecha_cierre_dt"].dt.date

        filas_dias = []
        for f_dia in dias_semana_l_d:
            str_dia = f_dia.strftime("%Y-%m-%d")
            cnt_rec = len(df_target[df_target["creado_dia"] == f_dia])
            cnt_cer = len(df_target_cerrados[df_target_cerrados["cerrado_dia"] == f_dia])
            filas_dias.append({
                "Fecha": str_dia,
                "Recibidos": cnt_rec,
                "Cerrados": cnt_cer
            })

        df_dias_agg = pd.DataFrame(filas_dias)

        fig_dias = go.Figure()
        fig_dias.add_trace(go.Bar(
            x=df_dias_agg["Fecha"],
            y=df_dias_agg["Recibidos"],
            name="Q. de chats recibidos",
            marker=dict(color="#0284c7", cornerradius=8),
            text=df_dias_agg["Recibidos"],
            textposition="inside",
            insidetextfont=dict(color="#ffffff", size=12, family="Arial Black")
        ))
        fig_dias.add_trace(go.Bar(
            x=df_dias_agg["Fecha"],
            y=df_dias_agg["Cerrados"],
            name="Q. de chats cerrados",
            marker=dict(color="#38bdf8", cornerradius=8),
            text=df_dias_agg["Cerrados"],
            textposition="inside",
            insidetextfont=dict(color="#0f172a", size=12, family="Arial Black")
        ))
        fig_dias.update_layout(
            hovermode=False,
            barmode="group",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#cbd5e1"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            xaxis=dict(gridcolor="#1e293b"),
            yaxis=dict(showgrid=False, showticklabels=False, zeroline=False, showline=False, title=None),
            margin=dict(t=30, b=20, l=20, r=20),
            height=360
        )
        st.plotly_chart(fig_dias, use_container_width=True)

        color_wow_rec = "#34d399" if diff_rec_pct <= 0 else "#f43f5e"
        color_wow_cer = "#34d399" if diff_cer_pct >= 0 else "#f43f5e"
        
        sin_resp_count = int(
            df_target_cerrados["etiquetas"]
            .fillna("")
            .astype(str)
            .str.lower()
            .str.contains("sin respuesta")
            .sum()
        )

        st.markdown(f"""
        <div style="background-color: #0f172a; border-radius: 10px; border: 1px solid #334155; padding: 14px 18px; font-size: 0.86rem; line-height: 1.6; height: 115px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center;">
            <div>• <b style="color: {color_wow_rec};">{diff_rec_pct:+.2f}%</b> chats recibidos vs Semana {sem_prev_num} ({tot_prev_rec})</div>
            <div>• <b style="color: {color_wow_cer};">{diff_cer_pct:+.2f}%</b> chats cerrados vs Semana {sem_prev_num} ({tot_prev_cer})</div>
            <div style="color: #94a3b8; font-size: 0.78rem;">Obs: De los {tot_sem_cer} chats cerrados en la semana, <b>{sin_resp_count}</b> se cerraron con etiqueta "sin respuesta".</div>
        </div>
        """, unsafe_allow_html=True)

    with col_pie_ag:
        st.markdown("##### Q. de chats cerrados por Agentes")
        
        agentes_excluir_pie = [
            "monica", "mónica", "monica (bot)", 
            "sin asignar", "none", "nan", ""
        ]
        
        df_target_cerrados["agente_nom"] = df_target_cerrados["agente_asignado"].fillna("Sin asignar").astype(str).str.strip()
        es_cerrado_ia = df_target_cerrados["agente_nom"].str.lower().isin(["monica", "mónica", "monica (bot)"])
        es_omitido_torta = df_target_cerrados["agente_nom"].str.lower().isin(agentes_excluir_pie)
        
        cnt_cerrados_ia = int(es_cerrado_ia.sum())
        cnt_cerrados_humanos = int((~es_omitido_torta).sum())
        pct_ia = round((cnt_cerrados_ia / tot_sem_cer * 100), 2) if tot_sem_cer > 0 else 0.0

        df_ag_pie = df_target_cerrados[~es_omitido_torta]["agente_nom"].value_counts().reset_index()
        df_ag_pie.columns = ["Agente", "Cerrados"]

        palette_donut = [
            "#0369a1", "#0284c7", "#38bdf8", "#0ea5e9", 
            "#7dd3fc", "#bae6fd", "#94a3b8", "#cbd5e1"
        ]

        fig_pie_w = go.Figure(data=[go.Pie(
            labels=df_ag_pie["Agente"],
            values=df_ag_pie["Cerrados"],
            hole=0.45,
            textinfo="label+value+percent",
            texttemplate="<b>%{label}</b><br>%{value} (%{percent})",
            textposition="outside",
            insidetextorientation="horizontal",
            marker=dict(colors=palette_donut, line=dict(color="#0b1120", width=2)),
            showlegend=False
        )])

        fig_pie_w.update_layout(
            hovermode=False,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#cbd5e1", size=11),
            margin=dict(t=30, b=30, l=40, r=40),
            height=360
        )
        st.plotly_chart(fig_pie_w, use_container_width=True)

        cuentas_unicas = df_target["tenant"].nunique() if "tenant" in df_target.columns else 0
        empresas_unicas = df_target["company"].nunique() if "company" in df_target.columns else 0
        num_agentes_activos = df_ag_pie["Agente"].nunique()
        prom_por_agente = round(cnt_cerrados_humanos / num_agentes_activos, 2) if num_agentes_activos > 0 else 0.0

        st.markdown(f"""
        <div class="metric-card" style="border-left: 4px solid #38bdf8; text-align: center; height: 115px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center; padding: 10px 14px;">
            <div class="metric-card-title" style="justify-content: center; margin: 0;"># Contactos Promedio por Agente: <span style="color:#38bdf8; font-size:1.1rem; font-weight:700; margin-left:6px;">{prom_por_agente}</span></div>
            <div style="font-size: 0.8rem; color: #94a3b8; margin-top: 4px;">
                <b>{cuentas_unicas}</b> Cuentas Atendidas | <b>{empresas_unicas}</b> Empresas Atendidas
            </div>
            <div style="font-size: 0.74rem; color: #64748b; margin-top: 4px; border-top: 1px solid #334155; padding-top: 3px;">
                🤖 <b>{cnt_cerrados_ia} chats ({pct_ia}%)</b> gestionados por IA.
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")

    # =========================================================================
    # SLIDE 3: TIEMPOS DE GESTIÓN Y 1RA RESPUESTA
    # =========================================================================
    st.markdown(f"#### 3. WBR | {semana_seleccionada_str} — Tiempos de Gestión y SLA Operativo")

    def calcular_metricas_tiempos_semana(df_sub):
        if df_sub.empty:
            return 0.0, 0.0, 0.0, 0.0

        agentes_omitir_t = ["", "sin asignar", "none", "nan", "monica", "monica (bot)", "soporte"]
        
        dt_col = df_sub["created_at_dt"]
        dias_col = dt_col.dt.dayofweek
        horas_col = dt_col.dt.time
        fechas_col = dt_col.dt.strftime("%Y-%m-%d")

        es_horario_1ra = (
            ((dias_col.isin([0, 1, 2, 3, 4])) & (horas_col >= time(8, 0)) & (horas_col <= time(17, 30))) |
            ((dias_col == 5) & (horas_col >= time(9, 0)) & (horas_col <= time(11, 45)))
        ) & (~fechas_col.isin(FERIADOS))

        es_agente_1ra = (
            (df_sub["por_agente"].fillna("").astype(str).str.lower() == "no excluido") &
            (~df_sub["agente_asignado"].fillna("").astype(str).str.strip().str.lower().isin(agentes_omitir_t))
        )

        sub_1ra = df_sub[es_horario_1ra & es_agente_1ra].copy()
        sub_1ra["p1_num"] = pd.to_numeric(sub_1ra["primera_respuesta_min"], errors="coerce")
        s_1ra = sub_1ra["p1_num"].dropna()

        prom_1ra_val = round(s_1ra.mean(), 2) if not s_1ra.empty else 0.0
        pct_sla_1ra_val = round(((s_1ra <= 2.0).sum() / len(s_1ra) * 100), 1) if not s_1ra.empty else 0.0

        etiquetas_s = df_sub["etiquetas"].fillna("").astype(str).str.lower()
        es_sin_resp = etiquetas_s.str.contains("sin respuesta", na=False)
        es_horario_gest = df_sub["horario_evaluado"] != "fuera de horario"
        
        sub_gest = df_sub[
            es_agente_1ra & 
            es_horario_gest & 
            (~es_sin_resp) & 
            (df_sub["es_cerrado"] == True)
        ].copy()
        
        sub_gest["pg_num"] = pd.to_numeric(sub_gest["tiempo_resolucion_minutos"], errors="coerce")
        s_gest = sub_gest["pg_num"].dropna()

        prom_gest_val = round(s_gest.mean(), 1) if not s_gest.empty else 0.0
        pct_sla_gest_val = round(((s_gest <= 60.0).sum() / len(s_gest) * 100), 1) if not s_gest.empty else 0.0

        return prom_gest_val, prom_1ra_val, pct_sla_gest_val, pct_sla_1ra_val

    evol_tiempos = []
    for _, s_row in df_5_semanas_info.iterrows():
        sub_w = df_wbr_base[
            (df_wbr_base["iso_year"] == s_row["iso_year"]) & 
            (df_wbr_base["iso_week"] == s_row["iso_week"])
        ]
        p_gest_w, p_1ra_w, sla_g_w, sla_1_w = calcular_metricas_tiempos_semana(sub_w)
        evol_tiempos.append({
            "Semana": s_row["etiqueta_semana"],
            "Prom_Gestion": p_gest_w,
            "Prom_1ra": p_1ra_w,
            "SLA_Gestion": sla_g_w,
            "SLA_1ra": sla_1_w
        })

    df_evol_tiempos = pd.DataFrame(evol_tiempos)

    foco_gest = df_evol_tiempos.iloc[-1]["Prom_Gestion"]
    foco_1ra = df_evol_tiempos.iloc[-1]["Prom_1ra"]
    foco_sla_gest = df_evol_tiempos.iloc[-1]["SLA_Gestion"]
    foco_sla_1ra = df_evol_tiempos.iloc[-1]["SLA_1ra"]

    prev_gest = df_evol_tiempos.iloc[-2]["Prom_Gestion"] if len(df_evol_tiempos) >= 2 else 0.0
    prev_1ra = df_evol_tiempos.iloc[-2]["Prom_1ra"] if len(df_evol_tiempos) >= 2 else 0.0

    wow_gest_pct = round(((foco_gest - prev_gest) / prev_gest * 100), 2) if prev_gest > 0 else 0.0
    wow_1ra_pct = round(((foco_1ra - prev_1ra) / prev_1ra * 100), 2) if prev_1ra > 0 else 0.0

    col_t1, col_t2 = st.columns(2)
    col_t1.markdown(f"""
    <div class="metric-card" style="border-left: 4px solid #0284c7; text-align: center; height: 110px; display: flex; flex-direction: column; justify-content: center;">
        <div class="metric-card-title" style="justify-content: center;">TIEMPO PROMEDIO TOTAL DE GESTIÓN</div>
        <div class="metric-card-value" style="font-size: 2.3rem; color: #38bdf8;">{foco_gest} min</div>
    </div>
    """, unsafe_allow_html=True)

    col_t2.markdown(f"""
    <div class="metric-card" style="border-left: 4px solid #38bdf8; text-align: center; height: 110px; display: flex; flex-direction: column; justify-content: center;">
        <div class="metric-card-title" style="justify-content: center;">TIEMPO PROM. DE 1RA RESPUESTA</div>
        <div class="metric-card-value" style="font-size: 2.3rem; color: #38bdf8;">{foco_1ra} min</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    col_g_gest, col_g_1ra = st.columns(2)

    with col_g_gest:
        st.markdown("##### Tiempo promedio total de gestión (min)")
        fig_bar_gest = go.Figure()
        fig_bar_gest.add_trace(go.Bar(
            x=df_evol_tiempos["Semana"],
            y=df_evol_tiempos["Prom_Gestion"],
            marker=dict(color="#0284c7", cornerradius=8),
            text=df_evol_tiempos["Prom_Gestion"].apply(lambda v: f"{v}"),
            textposition="inside",
            insidetextfont=dict(color="#ffffff", size=13, family="Arial Black")
        ))
        fig_bar_gest.update_layout(
            hovermode=False,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#cbd5e1"),
            xaxis=dict(gridcolor="#1e293b", title=None),
            yaxis=dict(showgrid=False, showticklabels=False, zeroline=False, showline=False, title=None),
            margin=dict(t=30, b=20, l=20, r=20),
            height=360
        )
        st.plotly_chart(fig_bar_gest, use_container_width=True)

        color_wow_g = "#34d399" if wow_gest_pct <= 0 else "#f43f5e"
        color_sla_g = "#34d399" if foco_sla_gest >= 90.0 else "#f43f5e"

        st.markdown(f"""
        <div style="background-color: #0f172a; border-radius: 10px; border: 1px solid #334155; padding: 14px 18px; font-size: 0.88rem; line-height: 1.6; text-align: center; height: 95px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center;">
            <div><b style="color: {color_wow_g};">{wow_gest_pct:+.2f}%</b> WoW {semana_seleccionada_str.split(' ')[0]} {target_week} vs Semana {sem_prev_num} ({prev_gest} min)</div>
            <div>SLA Gestión: <b style="color: {color_sla_g}; font-size: 1.05rem;">{foco_sla_gest}%</b></div>
        </div>
        """, unsafe_allow_html=True)

    with col_g_1ra:
        st.markdown("##### Tiempo promedio primera respuesta")
        fig_bar_1ra = go.Figure()
        fig_bar_1ra.add_trace(go.Bar(
            x=df_evol_tiempos["Semana"],
            y=df_evol_tiempos["Prom_1ra"],
            marker=dict(color="#0369a1", cornerradius=8),
            text=df_evol_tiempos["Prom_1ra"].apply(lambda v: f"{v}"),
            textposition="inside",
            insidetextfont=dict(color="#ffffff", size=13, family="Arial Black")
        ))
        fig_bar_1ra.update_layout(
            hovermode=False,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#cbd5e1"),
            xaxis=dict(gridcolor="#1e293b", title=None),
            yaxis=dict(showgrid=False, showticklabels=False, zeroline=False, showline=False, title=None),
            margin=dict(t=30, b=20, l=20, r=20),
            height=360
        )
        st.plotly_chart(fig_bar_1ra, use_container_width=True)

        color_wow_1 = "#34d399" if wow_1ra_pct <= 0 else "#f43f5e"
        color_sla_1 = "#34d399" if foco_sla_1ra >= 90.0 else "#f43f5e"

        st.markdown(f"""
        <div style="background-color: #0f172a; border-radius: 10px; border: 1px solid #334155; padding: 14px 18px; font-size: 0.88rem; line-height: 1.6; text-align: center; height: 95px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center;">
            <div><b style="color: {color_wow_1};">{wow_1ra_pct:+.2f}%</b> WoW {semana_seleccionada_str.split(' ')[0]} {target_week} vs Semana {sem_prev_num} ({prev_1ra} min)</div>
            <div>SLA 1ra Respuesta: <b style="color: {color_sla_1}; font-size: 1.05rem;">{foco_sla_1ra}%</b> <span style="font-size: 0.74rem; color: #94a3b8;">(Sin horario extendido)</span></div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")

    # =========================================================================
    # SLIDE 4: EVALUACIÓN DE CSAT & SENTIMIENTO
    # =========================================================================
    st.markdown(f"#### 4. WBR | {semana_seleccionada_str} — Chats Evaluados & Sentimiento")

    if "fecha_calificacion_dt" in df_target.columns:
        df_target_csat = df_wbr_base[
            (df_wbr_base["por_agente"].fillna("").astype(str).str.lower() != "excluido") &
            (df_wbr_base["rating"].notna()) &
            (df_wbr_base["fecha_calificacion_dt"].dt.isocalendar().year == target_year) &
            (df_wbr_base["fecha_calificacion_dt"].dt.isocalendar().week == target_week)
        ].copy()
    else:
        df_target_csat = df_target[
            (df_target["por_agente"].fillna("").astype(str).str.lower() != "excluido") &
            (df_target["rating"].notna())
        ].copy()

    df_target_csat["rating_num"] = pd.to_numeric(df_target_csat["rating"], errors="coerce")
    df_target_csat = df_target_csat.dropna(subset=["rating_num"])

    tot_evaluados = len(df_target_csat)
    tot_sin_calificar = max(0, tot_sem_rec - tot_evaluados)
    pct_calificados = round((tot_evaluados / tot_sem_rec * 100), 2) if tot_sem_rec > 0 else 0.0
    pct_sin_calificar = round((tot_sin_calificar / tot_sem_rec * 100), 2) if tot_sem_rec > 0 else 0.0

    cnt_5 = int((df_target_csat["rating_num"] == 5).sum())
    cnt_4 = int((df_target_csat["rating_num"] == 4).sum())
    cnt_3 = int((df_target_csat["rating_num"] == 3).sum())
    cnt_2 = int((df_target_csat["rating_num"] == 2).sum())
    cnt_1 = int((df_target_csat["rating_num"] == 1).sum())

    tot_positivas = cnt_5 + cnt_4
    tot_negativas = cnt_3 + cnt_2 + cnt_1

    def f_pct(c, t):
        return f"{round((c / t * 100), 1)}%" if t > 0 else "0.0%"

    c_c1, c_c2, c_c3, c_c4 = st.columns(4)

    c_c1.markdown(f"""
    <div class="metric-card" style="border-left: 4px solid #0284c7; text-align: center; height: 125px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center;">
        <div class="metric-card-title" style="justify-content: center;">TOTAL CHATS RECIBIDOS</div>
        <div class="metric-card-value" style="font-size: 2.2rem; color: #f8fafc;">{tot_sem_rec:,}</div>
        <div class="metric-card-sub" style="color: #94a3b8; font-size: 0.74rem;">100% de la jornada</div>
    </div>
    """, unsafe_allow_html=True)

    c_c2.markdown(f"""
    <div class="metric-card" style="border-left: 4px solid #38bdf8; text-align: center; height: 125px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center;">
        <div class="metric-card-title" style="justify-content: center;">SIN CALIFICAR</div>
        <div class="metric-card-value" style="color: #38bdf8; font-size: 2.2rem;">{pct_sin_calificar}%</div>
        <div class="metric-card-sub" style="color: #94a3b8; font-size: 0.74rem;">{tot_sin_calificar:,} conversaciones</div>
    </div>
    """, unsafe_allow_html=True)

    c_c3.markdown(f"""
    <div class="metric-card" style="border-left: 4px solid #10b981; text-align: center; height: 125px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center;">
        <div class="metric-card-title" style="justify-content: center;">CALIFICADOS</div>
        <div class="metric-card-value" style="color: #34d399; font-size: 2.2rem;">{pct_calificados}%</div>
        <div class="metric-card-sub" style="color: #94a3b8; font-size: 0.74rem;">{tot_evaluados:,} encuestas recibidas</div>
    </div>
    """, unsafe_allow_html=True)

    c_c4.markdown(f"""
    <div class="metric-card" style="border-left: 4px solid #818cf8; height: 125px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center; padding: 10px 14px;">
        <div class="metric-card-title" style="font-size: 0.7rem; margin-bottom: 3px;">TOP CALIFICACIONES</div>
        <div style="font-size: 0.75rem; line-height: 1.35; color: #cbd5e1;">
            • <b>5★:</b> {cnt_5} ({f_pct(cnt_5, tot_evaluados)}) | <b>4★:</b> {cnt_4} ({f_pct(cnt_4, tot_evaluados)})<br>
            • <b>3★:</b> {cnt_3} ({f_pct(cnt_3, tot_evaluados)}) | <b>2★:</b> {cnt_2} ({f_pct(cnt_2, tot_evaluados)})<br>
            • <b>1★:</b> {cnt_1} ({f_pct(cnt_1, tot_evaluados)})
        </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    col_p_niv, col_p_sent = st.columns([1.3, 1])

    with col_p_niv:
        st.markdown("##### Q. de Puntuaciones por Nivel (Solo Calificados)")

        mapa_escala = {
            5: "Excelente", 4: "Muy Bueno", 3: "Regular", 2: "Malo", 1: "Muy malo"
        }
        
        df_target["nivel_norm"] = (
            df_target["nivel"].fillna("Niv-L1")
            .replace(["", "None", "nan", "null", "Sin Nivel"], "Niv-L1")
        )
        df_target["rating_int"] = pd.to_numeric(df_target["rating"], errors="coerce").fillna(0).astype(int)

        df_target_calificados = df_target[df_target["rating_int"] > 0].copy()
        df_target_calificados["puntuacion_str"] = df_target_calificados["rating_int"].map(mapa_escala)

        orden_puntuacion = ["Excelente", "Muy Bueno", "Regular", "Malo", "Muy malo"]
        df_niv_grp = df_target_calificados.groupby(["puntuacion_str", "nivel_norm"]).size().reset_index(name="Cantidad")

        fig_niv = px.bar(
            df_niv_grp,
            x="puntuacion_str",
            y="Cantidad",
            color="nivel_norm",
            barmode="group",
            category_orders={"puntuacion_str": orden_puntuacion},
            color_discrete_map={"Niv-L1": "#38bdf8", "Niv-L2": "#0284c7", "Niv-L3": "#0369a1"},
            text="Cantidad"
        )
        fig_niv.update_traces(textposition="outside", marker=dict(cornerradius=6))
        fig_niv.update_layout(
            hovermode=False,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#cbd5e1", size=11),
            xaxis=dict(gridcolor="#1e293b", title=None),
            yaxis=dict(showgrid=False, showticklabels=False, zeroline=False, showline=False, title=None),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, title=None),
            margin=dict(t=30, b=20, l=20, r=20),
            height=360
        )
        st.plotly_chart(fig_niv, use_container_width=True)

    with col_p_sent:
        st.markdown("##### Sentimiento de Usuarios (CSAT Positivo vs Negativo)")

        df_sent = pd.DataFrame([
            {"Sentimiento": "Positivas (≥4★)", "Cantidad": tot_positivas},
            {"Sentimiento": "Negativas (≤3★)", "Cantidad": tot_negativas}
        ])
        df_sent = df_sent[df_sent["Cantidad"] > 0]

        fig_sent = go.Figure(data=[go.Pie(
            labels=df_sent["Sentimiento"],
            values=df_sent["Cantidad"],
            hole=0.45,
            textinfo="label+value+percent",
            texttemplate="<b>%{label}</b><br>%{value} (%{percent})",
            textposition="outside",
            marker=dict(colors=["#0284c7", "#f43f5e"], line=dict(color="#0b1120", width=2)),
            showlegend=False
        )])
        fig_sent.update_layout(
            hovermode=False,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#cbd5e1", size=11),
            margin=dict(t=30, b=30, l=30, r=30),
            height=360
        )
        st.plotly_chart(fig_sent, use_container_width=True)

    st.markdown("---")

    # =========================================================================
    # SLIDE 5: EVALUACIÓN DE CSAT — CASOS NIVEL 1
    # =========================================================================
    st.markdown(f"#### 5. WBR | {semana_seleccionada_str} — Detalle de Fricción en Nivel 1 (L1)")

    df_l1_neg = df_target[
        (df_target["nivel_norm"] == "Niv-L1") &
        (df_target["rating_int"].isin([1, 2, 3]))
    ].copy()

    df_l3_neg = df_target[
        (df_target["nivel_norm"] == "Niv-L3") &
        (df_target["rating_int"].isin([1, 2, 3]))
    ].copy()

    tot_l1_neg = len(df_l1_neg)
    pct_l1_del_total = round((tot_l1_neg / tot_negativas * 100), 1) if tot_negativas > 0 else 0.0

    tot_l3_neg = len(df_l3_neg)
    pct_l3_sobre_total_neg = round((tot_l3_neg / tot_negativas * 100), 1) if tot_negativas > 0 else 0.0

    col_card_l1, col_matriz_l1 = st.columns([1, 2.5])

    with col_card_l1:
        st.markdown(f"""
        <div class="metric-card" style="border-left: 4px solid #f59e0b; text-align: center; height: calc(100% - 10px); min-height: 200px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center; align-items: center;">
            <div class="metric-card-title" style="justify-content: center; font-size: 0.82rem;">CASOS NIVEL L1</div>
            <div class="metric-card-value" style="font-size: 3.4rem; color: #f59e0b; margin: 8px 0;">{tot_l1_neg}</div>
            <div class="metric-card-sub" style="color: #94a3b8; font-size: 0.85rem;">
                <b>{pct_l1_del_total}%</b> del total de reclamos ({tot_negativas} casos)
            </div>
        </div>
        """, unsafe_allow_html=True)

    with col_matriz_l1:
        if not df_l1_neg.empty:
            df_l1_neg["agente_nom"] = df_l1_neg["agente_asignado"].fillna("Sin asignar").astype(str).str.strip()
            
            pivot_l1 = df_l1_neg.pivot_table(
                index="agente_nom",
                columns="rating_int",
                values="id",
                aggfunc="count",
                fill_value=0
            ).reset_index()

            for col_star in [3, 2, 1]:
                if col_star not in pivot_l1.columns:
                    pivot_l1[col_star] = 0

            pivot_l1["Todos los casos"] = pivot_l1[3] + pivot_l1[2] + pivot_l1[1]
            pivot_l1["% del Total a L1"] = pivot_l1["Todos los casos"].apply(
                lambda c: f"{round((c / tot_l1_neg * 100), 1)}%" if tot_l1_neg > 0 else "0%"
            )

            pivot_l1 = pivot_l1.rename(columns={
                "agente_nom": "Agente Asignado",
                3: "Regular (3★)",
                2: "Malo (2★)",
                1: "Muy malo (1★)"
            })

            cols_show_l1 = ["Agente Asignado", "Regular (3★)", "Malo (2★)", "Muy malo (1★)", "Todos los casos", "% del Total a L1"]
            config_matriz_l1 = {
                "Agente Asignado": st.column_config.TextColumn("Agente Asignado"),
                "Regular (3★)": st.column_config.NumberColumn("Regular (3★)", format="%d"),
                "Malo (2★)": st.column_config.NumberColumn("Malo (2★)", format="%d"),
                "Muy malo (1★)": st.column_config.NumberColumn("Muy malo (1★)", format="%d"),
                "Todos los casos": st.column_config.NumberColumn("Todos los casos", format="%d"),
                "% del Total a L1": st.column_config.TextColumn("% del Total a L1")
            }
            
            st.dataframe(
                pivot_l1[cols_show_l1].sort_values("Todos los casos", ascending=False),
                column_config=config_matriz_l1,
                hide_index=True,
                use_container_width=True
            )
        else:
            st.info("🟢 Excelente: No se registraron calificaciones negativas en Nivel 1 esta semana.")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("##### Motivos Normalizados de Reclamos en Nivel 1")

    df_l1_motivos = pd.DataFrame()
    if not df_l1_neg.empty:
        df_l1_motivos = df_l1_neg.groupby(["rating_int", "motivo_normalizado"]).size().reset_index(name="Cantidad")
        df_l1_motivos["Calificación"] = df_l1_motivos["rating_int"].apply(
            lambda r: f"<span style='color:#fbbf24;'>{'★'*r}</span> {mapa_escala.get(r, '')} ({r}★)"
        )
        df_l1_motivos = df_l1_motivos.sort_values(by=["rating_int", "Cantidad"], ascending=[True, False])

        filas_html_l1 = []
        for rating_val, grp in df_l1_motivos.groupby("rating_int", sort=True):
            filas = grp.to_dict("records")
            rowspan_cnt = len(filas)
            for idx, row_item in enumerate(filas):
                td_calif = f"<td rowspan='{rowspan_cnt}' style='padding:10px 14px; border:1px solid #334155; font-weight:700; vertical-align:middle; background-color:#1e293b;'>{row_item['Calificación']}</td>" if idx == 0 else ""
                filas_html_l1.append(
                    f"<tr>{td_calif}<td style='padding:10px 14px; border:1px solid #334155; color:#f8fafc;'>{row_item['motivo_normalizado']}</td>"
                    f"<td style='padding:10px 14px; border:1px solid #334155; text-align:center; font-weight:700; color:#38bdf8;'>{row_item['Cantidad']}</td></tr>"
                )

        html_tabla_l1 = f"""
        <table style='width:100%; border-collapse:collapse; background-color:#0f172a; border-radius:8px; overflow:hidden; font-size:0.86rem; border:1px solid #334155;'>
            <thead>
                <tr style='background-color:#0284c7; color:white;'>
                    <th style='padding:10px; border:1px solid #0284c7; text-align:left; width:28%;'>Calificación</th>
                    <th style='padding:10px; border:1px solid #0284c7; text-align:left;'>Motivo Normalizado</th>
                    <th style='padding:10px; border:1px solid #0284c7; text-align:center; width:15%;'>Cantidad</th>
                </tr>
            </thead>
            <tbody>
                {''.join(filas_html_l1)}
            </tbody>
        </table>
        """
        st.markdown(html_tabla_l1, unsafe_allow_html=True)
    else:
        st.caption("Sin motivos registrados en L1.")

    st.markdown("---")

    # =========================================================================
    # SLIDE 6: EVALUACIÓN DE CSAT — CASOS NIVEL 3 / JIRA
    # =========================================================================
    st.markdown(f"#### 6. WBR | {semana_seleccionada_str} — Detalle de Fricción en L3")

    col_card_l3, col_matriz_l3 = st.columns([1, 2.5])

    with col_card_l3:
        st.markdown(f"""
        <div class="metric-card" style="border-left: 4px solid #ef4444; text-align: center; height: calc(100% - 10px); min-height: 200px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center; align-items: center;">
            <div class="metric-card-title" style="justify-content: center; font-size: 0.82rem;">CASOS NIVEL L3</div>
            <div class="metric-card-value" style="font-size: 3.4rem; color: #ef4444; margin: 8px 0;">{tot_l3_neg}</div>
            <div class="metric-card-sub" style="color: #94a3b8; font-size: 0.85rem;">
                <b>{pct_l3_sobre_total_neg}%</b> del total de reclamos ({tot_negativas} casos)
            </div>
        </div>
        """, unsafe_allow_html=True)

    with col_matriz_l3:
        if not df_l3_neg.empty:
            df_l3_neg["agente_nom"] = df_l3_neg["agente_asignado"].fillna("Sin asignar").astype(str).str.strip()
            pivot_l3 = df_l3_neg.pivot_table(
                index="agente_nom",
                columns="rating_int",
                values="id",
                aggfunc="count",
                fill_value=0
            ).reset_index()

            for col_star in [3, 2, 1]:
                if col_star not in pivot_l3.columns:
                    pivot_l3[col_star] = 0

            pivot_l3["Todos los casos"] = pivot_l3[3] + pivot_l3[2] + pivot_l3[1]
            pivot_l3["% del Total a L3"] = pivot_l3["Todos los casos"].apply(
                lambda c: f"{round((c / tot_l3_neg * 100), 1)}%" if tot_l3_neg > 0 else "0%"
            )

            pivot_l3 = pivot_l3.rename(columns={
                "agente_nom": "Agente Asignado",
                3: "Regular (3★)",
                2: "Malo (2★)",
                1: "Muy malo (1★)"
            })

            cols_show_l3 = ["Agente Asignado", "Regular (3★)", "Malo (2★)", "Muy malo (1★)", "Todos los casos", "% del Total a L3"]
            config_matriz_l3 = {
                "Agente Asignado": st.column_config.TextColumn("Agente Asignado"),
                "Regular (3★)": st.column_config.NumberColumn("Regular (3★)", format="%d"),
                "Malo (2★)": st.column_config.NumberColumn("Malo (2★)", format="%d"),
                "Muy malo (1★)": st.column_config.NumberColumn("Muy malo (1★)", format="%d"),
                "Todos los casos": st.column_config.NumberColumn("Todos los casos", format="%d"),
                "% del Total a L3": st.column_config.TextColumn("% del Total a L3")
            }
            
            st.dataframe(
                pivot_l3[cols_show_l3].sort_values("Todos los casos", ascending=False),
                column_config=config_matriz_l3,
                hide_index=True,
                use_container_width=True
            )
        else:
            st.info("🟢 Excelente: No se registraron calificaciones negativas en Nivel 3 esta semana.")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("##### Detalle de Casos L3 & Tickets Relacionados")

    if not df_l3_neg.empty:
        df_l3_neg["ticket_limpio"] = (
            df_l3_neg["ticket_relacionado"]
            .fillna("")
            .replace(["None", "nan", ""], "Sin ticket")
            .astype(str)
            .str.strip()
        )

        try:
            res_tickets = supabase.table("tickets_l3").select("ticket_id, prioridad, estado").execute()
            df_meta_tk = pd.DataFrame(res_tickets.data or [])
            if not df_meta_tk.empty:
                df_l3_neg = df_l3_neg.merge(df_meta_tk, left_on="ticket_limpio", right_on="ticket_id", how="left")
        except Exception:
            pass

        if "prioridad" not in df_l3_neg.columns:
            df_l3_neg["prioridad"] = "Media"
        else:
            df_l3_neg["prioridad"] = df_l3_neg["prioridad"].fillna("Media")

        if "estado" not in df_l3_neg.columns:
            df_l3_neg["estado"] = "Pendiente"
        else:
            df_l3_neg["estado"] = df_l3_neg["estado"].fillna("Pendiente")

        df_l3_neg["Calificación_HTML"] = df_l3_neg["rating_int"].apply(
            lambda r: f"<span style='color:#fbbf24;'>{'★'*r}</span> {mapa_escala.get(r, '')} ({r}★)"
        )
        df_l3_neg["motivo_clean"] = df_l3_neg["motivo_normalizado"].fillna("Sin categorizar")
        df_l3_neg["tenant_clean"] = df_l3_neg["tenant"].fillna("Sin tenant")
        df_l3_neg["agente_clean"] = df_l3_neg["agente_asignado"].fillna("Sin asignar")

        color_prio = {"Crítica": "#ef4444", "Alta": "#f97316", "Media": "#38bdf8", "Baja": "#94a3b8"}
        color_est = {"Resuelto": "#34d399", "Cerrado": "#10b981", "En progreso": "#38bdf8", "Abierto": "#fbbf24", "Pendiente": "#94a3b8"}

        df_l3_neg = df_l3_neg.sort_values(by=["rating_int", "agente_clean"], ascending=[True, True])

        filas_html_l3 = []
        for rating_val, grp in df_l3_neg.groupby("rating_int", sort=True):
            filas = grp.to_dict("records")
            rowspan_cnt = len(filas)
            for idx, r_item in enumerate(filas):
                td_calif = (
                    f"<td rowspan='{rowspan_cnt}' style='padding:10px 14px; border:1px solid #334155; "
                    f"font-weight:700; vertical-align:middle; background-color:#1e293b; text-align:center;'>"
                    f"{r_item['Calificación_HTML']}</td>"
                    if idx == 0 else ""
                )
                c_p = color_prio.get(r_item['prioridad'], "#94a3b8")
                c_e = color_est.get(r_item['estado'], "#94a3b8")

                ticket_txt = r_item['ticket_limpio']
                badge_ticket = (
                    f"<span style='background:#1e1b4b; border:1px solid #6366f1; color:#c7d2fe; padding:2px 8px; border-radius:4px; font-weight:600; font-family:monospace;'>{ticket_txt}</span>"
                    if ticket_txt != "Sin ticket" else f"<span style='color:#64748b;'>{ticket_txt}</span>"
                )

                badge_prio = f"<span style='color:{c_p}; font-weight:600; font-size:0.8rem;'>● {r_item['prioridad']}</span>"
                badge_est = f"<span style='border:1px solid {c_e}; color:{c_e}; padding:2px 8px; border-radius:12px; font-size:0.75rem; font-weight:600;'>{r_item['estado']}</span>"

                filas_html_l3.append(
                    f"<tr>{td_calif}"
                    f"<td style='padding:10px 14px; border:1px solid #334155; color:#f8fafc;'>{r_item['motivo_clean']}</td>"
                    f"<td style='padding:10px 14px; border:1px solid #334155;'>{badge_ticket}</td>"
                    f"<td style='padding:10px 14px; border:1px solid #334155; text-align:center;'>{badge_prio}</td>"
                    f"<td style='padding:10px 14px; border:1px solid #334155; text-align:center;'>{badge_est}</td>"
                    f"<td style='padding:10px 14px; border:1px solid #334155; color:#cbd5e1;'>{r_item['tenant_clean']}</td>"
                    f"<td style='padding:10px 14px; border:1px solid #334155; color:#38bdf8; font-weight:600;'>{r_item['agente_clean']}</td></tr>"
                )

        html_tabla_l3 = f"""
        <table style='width:100%; border-collapse:collapse; background-color:#0f172a; border-radius:8px; overflow:hidden; font-size:0.84rem; border:1px solid #334155;'>
            <thead>
                <tr style='background-color:#0284c7; color:white;'>
                    <th style='padding:10px; border:1px solid #0284c7; text-align:center; width:18%;'>Calificación</th>
                    <th style='padding:10px; border:1px solid #0284c7; text-align:left;'>Motivo</th>
                    <th style='padding:10px; border:1px solid #0284c7; text-align:left; width:14%;'>Ticket</th>
                    <th style='padding:10px; border:1px solid #0284c7; text-align:center; width:11%;'>Prioridad</th>
                    <th style='padding:10px; border:1px solid #0284c7; text-align:center; width:12%;'>Estado</th>
                    <th style='padding:10px; border:1px solid #0284c7; text-align:left; width:14%;'>Tenant</th>
                    <th style='padding:10px; border:1px solid #0284c7; text-align:left; width:13%;'>Agente</th>
                </tr>
            </thead>
            <tbody>
                {''.join(filas_html_l3)}
            </tbody>
        </table>
        """
        st.markdown(html_tabla_l3, unsafe_allow_html=True)
    else:
        st.info("🟢 No hay casos escalados a L3 con calificación negativa esta semana.")

    st.markdown("---")

    # =========================================================================
    # SLIDE 7: EVOLUTIVO CSAT & RATIO CONTACTOS POR EMPRESA
    # =========================================================================
    st.markdown(f"#### 7. WBR | {semana_seleccionada_str} — Evolutivo de Satisfacción & Frecuencia por Empresa")

    evol_csat = []
    for _, s_row in df_5_semanas_info.iterrows():
        if "fecha_calificacion_dt" in df_wbr_base.columns:
            sub_csat = df_wbr_base[
                (df_wbr_base["por_agente"].fillna("").astype(str).str.lower() != "excluido") &
                (df_wbr_base["rating"].notna()) &
                (df_wbr_base["fecha_calificacion_dt"].dt.isocalendar().year == s_row["iso_year"]) &
                (df_wbr_base["fecha_calificacion_dt"].dt.isocalendar().week == s_row["iso_week"])
            ].copy()
        else:
            sub_csat = df_wbr_base[
                (df_wbr_base["por_agente"].fillna("").astype(str).str.lower() != "excluido") &
                (df_wbr_base["rating"].notna()) &
                (df_wbr_base["iso_year"] == s_row["iso_year"]) &
                (df_wbr_base["iso_week"] == s_row["iso_week"])
            ].copy()

        sub_csat["r_num"] = pd.to_numeric(sub_csat["rating"], errors="coerce")
        sub_csat = sub_csat.dropna(subset=["r_num"])
        tot_c = len(sub_csat)
        pos_c = int((sub_csat["r_num"] >= 4).sum())
        pct_c = round((pos_c / tot_c * 100), 1) if tot_c > 0 else 0.0

        sub_vol = df_wbr_base[
            (df_wbr_base["iso_year"] == s_row["iso_year"]) & 
            (df_wbr_base["iso_week"] == s_row["iso_week"])
        ]
        tot_chats_w = len(sub_vol)
        empresas_w = sub_vol["company"].nunique() if "company" in sub_vol.columns else 0
        ratio_empresa = round(tot_chats_w / empresas_w, 2) if empresas_w > 0 else 0.0

        evol_csat.append({
            "Semana": s_row["etiqueta_semana"],
            "CSAT": pct_c,
            "Ratio_Empresa": ratio_empresa
        })

    df_evol_csat = pd.DataFrame(evol_csat)

    foco_csat = df_evol_csat.iloc[-1]["CSAT"]
    prev_csat = df_evol_csat.iloc[-2]["CSAT"] if len(df_evol_csat) >= 2 else 0.0
    diff_csat = round(foco_csat - prev_csat, 2)
    foco_ratio = df_evol_csat.iloc[-1]["Ratio_Empresa"]

    col_g_csat, col_g_ratio = st.columns(2)

    with col_g_csat:
        st.markdown("##### Satisfacción del Cliente (CSAT %)")
        fig_bar_csat = go.Figure()
        fig_bar_csat.add_trace(go.Bar(
            x=df_evol_csat["Semana"],
            y=df_evol_csat["CSAT"],
            marker=dict(color="#0284c7", cornerradius=8),
            text=df_evol_csat["CSAT"].apply(lambda v: f"{v}%"),
            textposition="inside",
            insidetextfont=dict(color="#ffffff", size=13, family="Arial Black")
        ))
        fig_bar_csat.update_layout(
            hovermode=False,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#cbd5e1"),
            xaxis=dict(gridcolor="#1e293b", title=None),
            yaxis=dict(showgrid=False, showticklabels=False, zeroline=False, showline=False, title=None),
            margin=dict(t=30, b=20, l=20, r=20),
            height=360
        )
        st.plotly_chart(fig_bar_csat, use_container_width=True)

        color_diff_csat = "#34d399" if diff_csat >= 0 else "#f43f5e"
        st.markdown(f"""
        <div style="background-color: #0f172a; border-radius: 10px; border: 1px solid #334155; padding: 14px 18px; font-size: 0.88rem; line-height: 1.6; text-align: center; height: 115px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center;">
            <b style="color: {color_diff_csat}; font-size: 1.1rem;">{diff_csat:+.2f}% CSAT</b>
            <span style="color: #cbd5e1; font-size: 0.85rem;">{semana_seleccionada_str.split(' ')[0]} {target_week} ({foco_csat}%) vs Semana {sem_prev_num} ({prev_csat}%)</span>
        </div>
        """, unsafe_allow_html=True)

    with col_g_ratio:
        st.markdown("##### Evolución Semana a Semana (Promedio Contactos por Empresa)")
        fig_bar_ratio = go.Figure()
        fig_bar_ratio.add_trace(go.Bar(
            x=df_evol_csat["Semana"],
            y=df_evol_csat["Ratio_Empresa"],
            marker=dict(color="#0369a1", cornerradius=8),
            text=df_evol_csat["Ratio_Empresa"].apply(lambda v: f"{v:.2f}"),
            textposition="inside",
            insidetextfont=dict(color="#ffffff", size=13, family="Arial Black")
        ))
        fig_bar_ratio.update_layout(
            hovermode=False,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#cbd5e1"),
            xaxis=dict(gridcolor="#1e293b", title=None),
            yaxis=dict( showgrid=False, showticklabels=False, zeroline=False, showline=False, title=None),
            margin=dict(t=30, b=20, l=20, r=20),
            height=360
        )
        st.plotly_chart(fig_bar_ratio, use_container_width=True)

        st.markdown(f"""
        <div class="metric-card" style="border-left: 4px solid #38bdf8; text-align: center; height: 115px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center; padding: 10px 14px;">
            <div class="metric-card-title" style="justify-content: center; margin: 0;"># Contactos Promedio por Empresa</div>
            <div class="metric-card-value" style="color: #38bdf8; font-size: 1.9rem; margin: 2px 0;">{foco_ratio:.2f}</div>
            <div style="font-size: 0.8rem; color: #34d399; font-weight: 600;">{pct_sem_cer}% Conversaciones Cerradas</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")

    # =========================================================================
    # SLIDE 8: OPERACIÓN EN HORARIO EXTENDIDO
    # =========================================================================
    st.markdown(f"#### 8. WBR | {semana_seleccionada_str} — Chats en Horario Extendido")

    df_ext_ingresados = df_target[df_target["horario_evaluado"] == "extendido"].copy()
    tot_ext = len(df_ext_ingresados)

    dias_num = df_ext_ingresados["created_at_dt"].dt.dayofweek
    tot_domingo = int((dias_num == 6).sum())
    derivados_l3_ext = int((df_ext_ingresados["nivel"].fillna("").astype(str).str.contains("L3", case=False)).sum())

    df_ext_cerrados_semana = df_wbr_base[
        (df_wbr_base["es_cerrado"] == True) &
        (df_wbr_base["horario_evaluado"] == "extendido") &
        (df_wbr_base["close_iso_year"] == target_year) &
        (df_wbr_base["close_iso_week"] == target_week)
    ].copy()

    tot_ext_cerrados = len(df_ext_cerrados_semana)
    pct_ext_cerrados = round((tot_ext_cerrados / tot_ext * 100), 1) if tot_ext > 0 else 0.0

    agentes_excluir_pie = [
        "monica", "mónica", "monica (bot)", 
        "sin asignar", "none", "nan", ""
    ]

    df_ext_cerrados_semana["agente_nom"] = df_ext_cerrados_semana["agente_asignado"].fillna("Sin asignar").astype(str).str.strip()
    cerrados_ia_ext = int(df_ext_cerrados_semana["agente_nom"].str.lower().isin(["monica", "mónica", "monica (bot)"]).sum())
    cerrados_hum_ext = tot_ext_cerrados - cerrados_ia_ext

    col_ext_pie, col_ext_dias = st.columns([1, 1.15], gap="medium")

    with col_ext_pie:
        st.markdown(f"""
        <div style="background: linear-gradient(145deg, #1e293b 0%, #0f172a 100%); border-radius: 12px; border: 1px solid #334155; border-left: 4px solid #38bdf8; padding: 14px 20px; text-align: center; height: 105px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center;">
            <div style="color: #94a3b8; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.8px; text-transform: uppercase;">CHATS CERRADOS EN HORARIO EXTENDIDO</div>
            <div style="color: #38bdf8; font-size: 2.2rem; font-weight: 800; line-height: 1.1; margin: 2px 0;">{tot_ext_cerrados}</div>
            <div style="color: #cbd5e1; font-size: 0.78rem;"><b>{cerrados_hum_ext}</b> por humanos | <b>{cerrados_ia_ext}</b> por Mónica</div>
        </div>
        """, unsafe_allow_html=True)

        df_ext_ag = df_ext_cerrados_semana[
            ~df_ext_cerrados_semana["agente_nom"].str.lower().isin(agentes_excluir_pie)
        ]["agente_nom"].value_counts().reset_index()
        df_ext_ag.columns = ["Agente", "Cantidad"]

        if not df_ext_ag.empty:
            palette_pie_wbr = ["#0284c7", "#0ea5e9", "#38bdf8", "#7dd3fc", "#94a3b8", "#cbd5e1"]

            fig_pie_ext = go.Figure(data=[go.Pie(
                labels=df_ext_ag["Agente"],
                values=df_ext_ag["Cantidad"],
                hole=0.45,
                textinfo="label+value+percent",
                texttemplate="<b>%{label}</b><br>%{value} (%{percent})",
                textposition="outside",
                insidetextorientation="horizontal",
                marker=dict(colors=palette_pie_wbr, line=dict(color="#0b1120", width=2)),
                showlegend=False
            )])
            
            fig_pie_ext.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#cbd5e1", size=10, family="Segoe UI, sans-serif"),
                margin=dict(t=25, b=25, l=40, r=40),
                height=310
            )
            st.plotly_chart(fig_pie_ext, use_container_width=True)

            st.markdown(f"""
            <div style="background-color: #0f172a; border-radius: 8px; border: 1px solid #334155; padding: 12px 16px; font-size: 0.82rem; color: #94a3b8; height: 60px; box-sizing: border-box; display: flex; align-items: center;">
                <span><b style="color: #38bdf8;">Obs:</b> De los {tot_ext_cerrados} cierres en horario extendido, {cerrados_hum_ext} fueron gestionados por soporte humano.</span>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.info("Sin cierres por agentes humanos en horario extendido para esta semana.")

    with col_ext_dias:
        st.markdown(f"""
        <div style="background: linear-gradient(145deg, #1e293b 0%, #0f172a 100%); border-radius: 12px; border: 1px solid #334155; border-left: 4px solid #10b981; padding: 14px 20px; text-align: center; height: 105px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center;">
            <div style="color: #94a3b8; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.8px; text-transform: uppercase;">CHATS INGRESADOS EN EXTENDIDO</div>
            <div style="color: #34d399; font-size: 2.2rem; font-weight: 800; line-height: 1.1; margin: 2px 0;">{tot_ext}</div>
            <div style="color: #cbd5e1; font-size: 0.78rem;">Domingo ingresaron <b>{tot_domingo}</b> chats</div>
        </div>
        """, unsafe_allow_html=True)

        if not df_ext_ingresados.empty:
            mapa_dias_nom = {
                0: "lunes", 1: "martes", 2: "miércoles", 3: "jueves",
                4: "viernes", 5: "sábado", 6: "domingo"
            }
            df_ext_ingresados["dia_nombre"] = df_ext_ingresados["created_at_dt"].dt.dayofweek.map(mapa_dias_nom)
            orden_dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
            
            df_dias_ext_cnt = df_ext_ingresados["dia_nombre"].value_counts().reindex(orden_dias, fill_value=0).reset_index()
            df_dias_ext_cnt.columns = ["Día", "Cantidad"]
            df_dias_ext_cnt["texto_barra"] = df_dias_ext_cnt["Cantidad"].apply(lambda v: f"  {v}" if v > 0 else "")

            fig_bar_dias_ext = go.Figure(go.Bar(
                x=df_dias_ext_cnt["Cantidad"],
                y=df_dias_ext_cnt["Día"],
                orientation="h",
                text=df_dias_ext_cnt["texto_barra"],
                textposition="outside",
                cliponaxis=False,
                marker=dict(
                    color="#0284c7",
                    cornerradius=5
                ),
                textfont=dict(color="#f8fafc", size=11, family="Segoe UI, sans-serif")
            ))

            fig_bar_dias_ext.update_layout(
                title=dict(
                    text="<b>Q. de chats ingresados por Día</b>",
                    font=dict(color="#f8fafc", size=13),
                    x=0.02, y=0.96
                ),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#94a3b8", size=11),
                xaxis=dict(
                    showgrid=False,
                    showticklabels=False,
                    zeroline=False
                ),
                yaxis=dict(
                    autorange="reversed",
                    tickfont=dict(color="#cbd5e1", size=11),
                    showgrid=False
                ),
                margin=dict(t=35, b=10, l=10, r=25),
                height=310
            )
            st.plotly_chart(fig_bar_dias_ext, use_container_width=True)

            st.markdown(f"""
            <div style="background-color: #0f172a; border-radius: 8px; border: 1px solid #334155; padding: 12px 16px; font-size: 0.82rem; line-height: 1.4; color: #cbd5e1; height: 60px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center;">
                <div>• <b>Volumen:</b> Ingresaron {tot_ext} chats en franjas nocturnas/fin de semana.</div>
                <div>• <b>Derivaciones:</b> {derivados_l3_ext} derivados a Nivel 3.</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.info("Sin registros diarios de ingreso en horario extendido.")

    # CÓDIGO OPTIMIZADO: Genera el archivo solo si el usuario pulsa descargar
    try:
        from wbr_exporter import exportar_wbr_con_plantilla
    
        paquete_datos_wbr = {
            "semana_label": semana_seleccionada_str,
            "target_week": target_week,
            "sem_prev_num": sem_prev_num,
            "tot_5w_rec": tot_5w_recibidos,
            "tot_5w_cer": tot_5w_cerrados,
            "pct_5w_cer": pct_5w_cerrados,
            "df_5w": df_res_5w,
            "tot_sem_rec": tot_sem_rec,
            "tot_sem_cer": tot_sem_cer,
            "pct_sem_cer": pct_sem_cer,
            "diff_rec_pct": diff_rec_pct,
            "diff_cer_pct": diff_cer_pct,
            "tot_prev_rec": tot_prev_rec,
            "tot_prev_cer": tot_prev_cer,
            "sin_resp_count": sin_resp_count,
            "prom_por_agente": prom_por_agente,
            "cuentas_unicas": cuentas_unicas,
            "empresas_unicas": empresas_unicas,
            "cnt_cerrados_ia": cnt_cerrados_ia,
            "pct_ia": pct_ia,
            "df_dias": df_dias_agg,
            "df_agentes": df_ag_pie,
            "foco_gest": foco_gest,
            "foco_1ra": foco_1ra,
            "prev_gest": prev_gest,
            "prev_1ra": prev_1ra,
            "wow_gest_pct": wow_gest_pct,
            "wow_1ra_pct": wow_1ra_pct,
            "foco_sla_gest": foco_sla_gest,
            "foco_sla_1ra": foco_sla_1ra,
            "tot_sin_calificar": tot_sin_calificar,
            "pct_sin_calificar": pct_sin_calificar,
            "tot_evaluados": tot_evaluados,
            "pct_calificados": pct_calificados,
            "cnt_5": cnt_5, "pct_5": f_pct(cnt_5, tot_evaluados),
            "cnt_4": cnt_4, "pct_4": f_pct(cnt_4, tot_evaluados),
            "cnt_3": cnt_3, "pct_3": f_pct(cnt_3, tot_evaluados),
            "cnt_2": cnt_2, "pct_2": f_pct(cnt_2, tot_evaluados),
            "cnt_1": cnt_1, "pct_1": f_pct(cnt_1, tot_evaluados),
            "foco_csat": foco_csat,
            "diff_csat_wow": diff_csat,
            "foco_ratio": foco_ratio,
            "tot_negativas": tot_negativas,
            "tot_l1_neg": tot_l1_neg,
            "pct_l1_total": pct_l1_del_total,
            "df_l1_agentes": pivot_l1 if ('pivot_l1' in locals() and not pivot_l1.empty) else None,
            "df_l1_motivos": df_l1_motivos if ('df_l1_motivos' in locals() and not df_l1_motivos.empty) else None,
            "tot_l3_neg": tot_l3_neg,
            "pct_l3_total": pct_l3_sobre_total_neg,
            "df_l3_agentes": pivot_l3 if ('pivot_l3' in locals() and not pivot_l3.empty) else None,
            "df_l3_neg": df_l3_neg if ('df_l3_neg' in locals() and not df_l3_neg.empty) else None,
            "df_tiempos": df_evol_tiempos,
            "df_evol_csat": df_evol_csat,
            "tot_ext": tot_ext,
            "tot_domingo": tot_domingo,
            "pct_ext_cerrados": pct_ext_cerrados,
            "tot_ext_cerrados": tot_ext_cerrados,
            "df_dias_ext": df_dias_ext_cnt if not df_ext_ingresados.empty else None,
            "df_agentes_ext": df_ext_ag if not df_ext_ag.empty else None,
        }
    
        # Función generadora diferida: solo se invoca cuando el usuario hace clic en Descargar
        def obtener_pptx_en_demanda():
            return exportar_wbr_con_plantilla(paquete_datos_wbr).getvalue()
    
        with contenedor_descarga.container():
            st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
            st.download_button(
                label="📥 Descargar Informe WBR (.pptx)",
                data=obtener_pptx_en_demanda,  # Se pasa la función sin paréntesis
                file_name=f"WBR_{semana_seleccionada_str.replace(' ', '_').replace('(', '').replace(')', '')}.pptx",
                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                key=f"btn_descarga_wbr_{target_week}_{target_year}"
            )
    except Exception as e_pptx:
        with contenedor_descarga.container():
            st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
            st.caption(f"⚠️ Error en descarga PPTX: {e_pptx}")


# Llamada limpia en la pestaña
if tab_wbr is not None:
    with tab_wbr:
        renderizar_informe_wbr(df_all)


if tab_admin is not None:
    with tab_admin:
        st.markdown("### Panel de Administración y Configuración")
        st.markdown("<br>", unsafe_allow_html=True)

        # ==========================================
        # CONTROL EXCLUSIVO DE SALUD Y REINICIO DEL BOT
        # ==========================================
        with st.container():
            st.markdown("""
            <div class="admin-card">
                <h4 style="margin-top:0; color:#38bdf8;">🤖 Estado y Control de Evolución API (WhatsApp Bot)</h4>
                <p style="color:#94a3b8; font-size:0.88rem; margin-bottom:15px;">
                    Monitoreo del servicio de notificaciones y reinicio forzado en Render.
                </p>
            </div>
            """, unsafe_allow_html=True)

            col_status1, col_status2 = st.columns([2, 1])

            # Consulta de salud del servicio en FastAPI
            estado_bot = "CRITICAL"
            wa_state = "disconnected"
            try:
                res_health = requests.get(f"{NOTIFICADOR_API_URL.rstrip('/')}/health-status", timeout=4)
                if res_health.status_code == 200:
                    data_h = res_health.json()
                    estado_bot = data_h.get("status_general", "CRITICAL")
                    wa_state = data_h.get("whatsapp_state", "disconnected")
            except Exception as e:
                estado_bot = "CRITICAL"
                wa_state = "offline"

            with col_status1:
                if estado_bot == "OK":
                    st.markdown(f"""
                    <div class="metric-card" style="border-left: 4px solid #10b981;">
                        <div class="metric-card-title">Estado del Bot WhatsApp</div>
                        <div class="metric-card-value" style="color: #34d399;">🟢 Operativo ({wa_state})</div>
                        <div class="metric-card-sub">Sesión activa y conectada</div>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown(f"""
                    <div class="metric-card" style="border-left: 4px solid #ef4444;">
                        <div class="metric-card-title">Estado del Bot WhatsApp</div>
                        <div class="metric-card-value" style="color: #f87171;">🔴 Desconectado ({wa_state})</div>
                        <div class="metric-card-sub">El Auto-Healing intentará reconectar automáticamente</div>
                    </div>
                    """, unsafe_allow_html=True)

            with col_status2:
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button("🔄 Reiniciar Instancia Bot", use_container_width=True, key="btn_reiniciar_bot_admin"):
                    with st.spinner("Enviando orden de reinicio a Render..."):
                        try:
                            headers = {"Authorization": f"Bearer {CRON_SECRET_TOKEN}"}
                            res_req = requests.post(f"{NOTIFICADOR_API_URL.rstrip('/')}/reiniciar-servicio", headers=headers, timeout=10)
                            res_json = res_req.json()

                            if res_json.get("status") == "success":
                                st.success("✅ Orden enviada exitosamente. Render reiniciará la instancia en segundos.")
                            else:
                                st.error(f"❌ Error: {res_json.get('message')}")
                        except Exception as ex_r:
                            st.error(f"❌ Error al comunicar con la API: {ex_r}")

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

            c_meta1, c_meta2 = st.columns([1, 2])
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
                    st.cache_data.clear()
                    st.rerun()

            col_f1, col_f2, col_f3 = st.columns([1, 1, 1])
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
                            tz_py = timezone(timedelta(hours=-3))
                            hora_actual_py = datetime.now(tz_py).strftime('%H:%M:%S')

                            GLOBAL_SYNC_STATE["log"] = f"Se actualizaron {tot_f} registros para el rango {f_inicio} a {f_final} a las {hora_actual_py} hs (PY)."
                        except Exception as ex_thread:
                            GLOBAL_SYNC_STATE["status"] = "error"
                            GLOBAL_SYNC_STATE["error"] = str(ex_thread)

                    hilo_sync = threading.Thread(target=tarea_sync_paralela, args=(f_sync_desde, f_sync_hasta), daemon=True)
                    hilo_sync.start()
                    st.rerun()
                else:
                    st.error("No se encontró el módulo `sync_intercom.py` en el proyecto.")

        st.markdown("<br>", unsafe_allow_html=True)

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

        # GESTIÓN DINÁMICA DE MOTIVOS CSAT
        st.markdown("<br>", unsafe_allow_html=True)
        with st.container():
            st.markdown("""
            <div class="admin-card">
                <h4 style="margin-top:0; color:#38bdf8;">4. Gestión de Motivos Normalizados (CSAT)</h4>
                <p style="color:#94a3b8; font-size:0.88rem; margin-bottom:15px;">
                    Crea y desactiva los motivos que se muestran al categorizar chats negativos.
                </p>
            </div>
            """, unsafe_allow_html=True)

            # Formulario para agregar nuevo motivo
            with st.form("form_nuevo_motivo_csat"):
                col_m1, col_m2 = st.columns([3, 1])
                nuevo_motivo_input = col_m1.text_input("Nuevo Motivo:")
                btn_guardar_motivo = col_m2.form_submit_button("Agregar Motivo", use_container_width=True)

                if btn_guardar_motivo:
                    txt_motivo = nuevo_motivo_input.strip()
                    if txt_motivo:
                        try:
                            supabase.table("motivos_csat").insert({"nombre": txt_motivo, "activo": True}).execute()
                            st.success(f"Motivo '{txt_motivo}' creado con éxito.")
                            st.cache_data.clear()
                            st.rerun()
                        except Exception as ex:
                            st.error(f"Error o motivo duplicado: {ex}")
                    else:
                        st.warning("Escribe un nombre para el motivo.")

            # Listado de motivos con opción de Cambiar Estado
            try:
                res_mot = supabase.table("motivos_csat").select("*").order("id", desc=False).execute()
                df_motivos = pd.DataFrame(res_mot.data or [])

                if not df_motivos.empty:
                    st.markdown("<b>Motivos Configurados Actualmente:</b>", unsafe_allow_html=True)
                    st.dataframe(
                        df_motivos[["id", "nombre", "activo"]],
                        column_config={
                            "id": "ID",
                            "nombre": "Nombre del Motivo",
                            "activo": st.column_config.CheckboxColumn("Estado Activo")
                        },
                        hide_index=True,
                        use_container_width=True
                    )

                    col_dis1, col_dis2 = st.columns([2, 1])

                    motivos_dict = {f"ID {r['id']}: {r['nombre']} ({'Activo' if r['activo'] else 'Inactivo'})": r['id'] for _, r in df_motivos.iterrows()}
                    motivo_seleccionado_str = col_dis1.selectbox("Seleccionar Motivo a Cambiar Estado:", options=list(motivos_dict.keys()))

                    if col_dis2.button("Alternar Estado (Activar/Deshabilitar)", use_container_width=True):
                        id_motivo_sel = motivos_dict[motivo_seleccionado_str]
                        motivo_row = df_motivos[df_motivos["id"] == id_motivo_sel].iloc[0]
                        nuevo_estado = not bool(motivo_row["activo"])

                        try:
                            supabase.table("motivos_csat")\
                                .update({"activo": nuevo_estado})\
                                .eq("id", id_motivo_sel)\
                                .execute()

                            st.success(f"El motivo ID {id_motivo_sel} ahora está {'Activo' if nuevo_estado else 'Deshabilitado'}.")
                            st.cache_data.clear()
                            st.rerun()
                        except Exception as ex:
                            st.error(f"Error al actualizar motivo: {ex}")
                else:
                    st.info("No hay motivos registrados en la base de datos.")
            except Exception as e:
                st.error(f"No se pudieron cargar los motivos: {e}")

        # GESTIÓN DE CONFIGURACIÓN DE NOTIFICACIONES
        st.markdown("<br>", unsafe_allow_html=True)
        with st.container():
            st.markdown("""
            <div class="admin-card">
                <h4 style="margin-top:0; color:#38bdf8;">5. Configuración de Notificaciones & Alertas (WhatsApp / Email)</h4>
                <p style="color:#94a3b8; font-size:0.88rem; margin-bottom:15px;">
                    Ajusta los umbrales de tiempo, correos y números sin modificar el código fuente.
                </p>
            </div>
            """, unsafe_allow_html=True)

            try:
                res_cfg = supabase.table("config_notificaciones").select("*").eq("id", 1).execute()
                cfg_data = res_cfg.data[0] if res_cfg.data else {}
            except Exception:
                cfg_data = {}

            try:
                hora_val = datetime.strptime(str(cfg_data.get("hora_reporte_diario", "18:00:00")), "%H:%M:%S").time()
            except Exception:
                hora_val = time(18, 0)

            with st.form("form_config_notificaciones"):
                st.markdown("##### 📧 Reporte Diario por Correo")
                col_e1, col_e2 = st.columns(2)
                v_hora_rep = col_e1.time_input("Hora de Envío (PY):", value=hora_val)
                v_email_dest = col_e2.text_input("Correo Destinatario:", value=cfg_data.get("email_destino_reporte", "admin@empresa.com"))

                st.markdown("---")
                st.markdown("##### 📲 Alerta Individual de Chat Sin Responder")
                col_w1, col_w2 = st.columns(2)
                v_min_indiv = col_w1.number_input("Disparar alerta tras (Minutos sin respuesta):", min_value=1.0, max_value=60.0, value=float(cfg_data.get("minutos_alerta_individual", 5.0)), step=1.0)
                v_tel_indiv = col_w2.text_input("Tu Número de WhatsApp (ej: 595981xxxxxx):", value=cfg_data.get("telefono_alerta_individual", ""))

                st.markdown("---")
                st.markdown("##### 👥 Alerta Grupal por Antigüedad de Chats")
                col_g1, col_g2 = st.columns(2)
                v_hs_grupo = col_g1.number_input("Disparar si el chat supera (Horas abierto):", min_value=1.0, max_value=72.0, value=float(cfg_data.get("horas_alerta_grupo", 5.0)), step=0.5)
                v_jid_grupo = col_g2.text_input("ID/JID del Grupo de WhatsApp (ej: 120363... @g.us):", value=cfg_data.get("jid_grupo_whatsapp", ""))

                btn_guardar_notif = st.form_submit_button("Guardar Parámetros de Notificación", use_container_width=True)

                if btn_guardar_notif:
                    try:
                        supabase.table("config_notificaciones").upsert({
                            "id": 1,
                            "hora_reporte_diario": v_hora_rep.strftime("%H:%M:%S"),
                            "email_destino_reporte": v_email_dest.strip(),
                            "minutos_alerta_individual": v_min_indiv,
                            "telefono_alerta_individual": v_tel_indiv.strip(),
                            "horas_alerta_grupo": v_hs_grupo,
                            "jid_grupo_whatsapp": v_jid_grupo.strip(),
                            "updated_at": datetime.now(timezone(timedelta(hours=-3))).isoformat()
                        }).execute()
                        st.success("✅ Configuración de notificaciones guardada en Supabase.")
                        st.rerun()
                    except Exception as ex_g:
                        st.error(f"Error al guardar configuración: {ex_g}")

        # ASIGNACIÓN DE TELÉFONOS A AGENTES PARA MENCIONES
        st.markdown("<br>", unsafe_allow_html=True)
        with st.container():
            st.markdown("""
            <div class="admin-card">
                <h4 style="margin-top:0; color:#38bdf8;">6. Mapeo de Agentes y Teléfonos de WhatsApp</h4>
                <p style="color:#94a3b8; font-size:0.88rem; margin-bottom:15px;">
                    Asigna el número de WhatsApp a cada agente para que el bot lo arrobe (@) en las alertas grupales.
                </p>
            </div>
            """, unsafe_allow_html=True)

            try:
                res_usrs = supabase.table("usuarios_autorizados").select("id, nombre, email, telefono_whatsapp").order("nombre").execute()
                df_usrs = pd.DataFrame(res_usrs.data or [])

                if not df_usrs.empty:
                    with st.form("form_telefonos_agentes"):
                        edited_usrs = st.data_editor(
                            df_usrs,
                            column_config={
                                "id": None,
                                "nombre": st.column_config.TextColumn("Agente / Usuario", disabled=True),
                                "email": st.column_config.TextColumn("Correo", disabled=True),
                                "telefono_whatsapp": st.column_config.TextColumn("Teléfono WhatsApp (ej: 595981xxxxxx)", required=True)
                            },
                            hide_index=True,
                            use_container_width=True,
                            key="editor_telefonos_agentes"
                        )
                        btn_save_tels = st.form_submit_button("Guardar Teléfonos de Agentes", use_container_width=True)

                        if btn_save_tels:
                            cambios = st.session_state.get("editor_telefonos_agentes", {}).get("edited_rows", {})
                            if cambios:
                                for fila_idx, datos_ed in cambios.items():
                                    if "telefono_whatsapp" in datos_ed:
                                        u_id = df_usrs.iloc[int(fila_idx)]["id"]
                                        supabase.table("usuarios_autorizados")\
                                            .update({"telefono_whatsapp": str(datos_ed["telefono_whatsapp"]).strip()})\
                                            .eq("id", u_id)\
                                            .execute()
                                st.success("✅ Teléfonos de agentes actualizados.")
                                st.rerun()
                else:
                    st.info("No hay usuarios registrados aún.")
            except Exception as ex_u:
                st.error(f"Error al cargar tabla de teléfonos: {ex_u}")

        # GESTIÓN DINÁMICA DE FERIADOS
        st.markdown("<br>", unsafe_allow_html=True)
        with st.container():
            st.markdown("""
            <div class="admin-card">
                <h4 style="margin-top:0; color:#38bdf8;">📅 Calendario de Feriados Oficiales & Móviles</h4>
                <p style="color:#94a3b8; font-size:0.88rem; margin-bottom:15px;">
                    Configura feriados trasladados o extraordinarios. Se sincroniza con el bot y se mantiene en memoria sin saturar la base de datos.
                </p>
            </div>
            """, unsafe_allow_html=True)

            feriados_actuales = obtener_feriados_activos()

            col_add1, col_add2 = st.columns([2, 1])
            fecha_nueva_feriado = col_add1.date_input("Agregar nueva fecha feriado:", value=obtener_fecha_local_hoy(), key="input_nuevo_feriado")

            if col_add2.button("➕ Agregar Feriado", use_container_width=True):
                str_f = fecha_nueva_feriado.strftime("%Y-%m-%d")
                if str_f not in feriados_actuales:
                    feriados_actuales.append(str_f)
                    if guardar_feriados_db(feriados_actuales):
                        st.success(f"Feriado {str_f} agregado correctamente.")
                        st.rerun()
                else:
                    st.info("Esa fecha ya se encuentra registrada como feriado.")

            df_feriados_adm = pd.DataFrame({"Fecha Feriado (YYYY-MM-DD)": feriados_actuales})
            st.dataframe(df_feriados_adm, use_container_width=True, hide_index=True)

            col_del1, col_del2 = st.columns([2, 1])
            feriado_a_borrar = col_del1.selectbox("Seleccionar feriado a remover:", options=feriados_actuales, key="sel_borrar_feriado")
            if col_del2.button("🗑️ Eliminar Feriado Seleccionado", use_container_width=True):
                feriados_actuales.remove(feriado_a_borrar)
                if guardar_feriados_db(feriados_actuales):
                    st.success(f"Feriado {feriado_a_borrar} eliminado.")
                    st.rerun()
        pass

with tab_faq:
    st.markdown("### Preguntas Frecuentes & Criterios Operativos")
    st.caption("Guía oficial sobre reglas de negocio, horarios hábiles, medición de SLA y evaluación del CSAT.")

    with st.expander("1. ¿Cómo se evalúa el SLA Operativo en Pantalla (Dashboard en Vivo)?", expanded=True):
        st.markdown(r"""
        * **Chats Evaluados:** Se incluyen exclusivamente conversaciones atendidas por **agentes humanos** (`por_agente == 'no excluido'`), excluyendo a *Mónica (Bot)*, *Soporte* o estados *Sin Asignar*.
        * **Franja Horaria Válida:** Solo califican los chats ingresados dentro de la jornada laboral hábil (Lunes a Viernes de 08:00 a 17:30 hs y Sábados de 09:00 a 11:45 hs). Los chats en feriados o fuera de horario quedan marcados como `fuera de horario` / `excluido`.
        * **Promedio 1ra Respuesta:** Media numérica en minutos desde la creación/derivación del chat hasta la primera intervención humana. Meta: $\le 2.0$ min.
        * **Promedio de Gestión:** Media en minutos hábiles de resolución de chats cerrados. **Importante:** Se descartan automáticamente los chats con etiqueta *"Sin Respuesta"*. Meta: $\le 60.0$ min.
        * **Porcentaje de Cumplimiento (% SLA):** Proporción de casos válidos que cumplieron la meta sobre el total evaluable. Objetivo operativo: $\ge 90\%$.
        """)

    with st.expander("2. ¿Cómo se computa el tiempo hábil para el SLA de Gestión?", expanded=False):
        st.markdown(r"""
        ### ⏱️ El SLA mide tiempo operativo real, no horas reloj

        El tiempo de resolución **no cuenta el tiempo calendario transcurrido**, sino únicamente los minutos transcurridos dentro de los turnos hábiles definidos:

        * **Lunes a Viernes:** 08:00 a 17:30 hs.
        * **Sábados:** 09:00 a 11:45 hs.
        * **Domingos y Feriados:** Inactivos (no acumulan minutos de SLA).
        *(Nota: El turno extendido nocturno se encuentra actualmente pausado en las evaluaciones activas).*

        > **Ejemplo:** Si un ticket ingresa un viernes a las 17:00 hs y se resuelve el lunes siguiente a las 08:30 hs, el SLA **no contabiliza 63 horas calendario, sino exactamente 60 minutos hábiles** (30 min del viernes + 30 min del lunes).
        """)

    with st.expander("3. ¿Por qué la etiqueta 'Sin Respuesta' se excluye solo en Gestión?", expanded=False):
        st.markdown(r"""
        * **En Tiempo de Gestión (Excluido):** Cuando un cliente abandona la consulta y se cierra por inactividad tras una hora de espera, incluirlo distorsionaría injustamente los tiempos del equipo. Por ello, se marca como `excluido por filtro` en gestión.
        * **En Primera Respuesta (Incluido):** El deber de atender con rapidez al cliente al momento de ingresar existe antes de saber si responderá luego. Si la primera atención tardó de más, el incumplimiento se contabiliza.
        * **En Conteo de Volumen (Incluido):** El chat ingresó y demandó tiempo de seguimiento, por lo que forma parte del volumen atendido en la jornada.
        """)

    with st.expander("4. ¿Cuál es el criterio para 'Cerrados Hoy' y 'Distribución de Carga'?", expanded=False):
        st.markdown(r"""
        Para asegurar una medición justa de la jornada diaria:
        * **Cerrados Hoy (en métricas de carga):** Solo contabiliza tickets cuya **fecha de cierre sea hoy Y cuya fecha de creación/asignación también sea de hoy**.
        * Los chats de días anteriores que se resuelvan hoy no alteran la carga de asignaciones generadas en el día, garantizando que el gráfico apilado refleje el flujo exacto de la jornada actual.
        """)

    with st.expander("5. ¿Cómo se mide y evoluciona el CSAT (Satisfacción del Cliente)?", expanded=False):
        st.markdown(r"""
        * **Origen de la Fecha:** Se toma la marca de tiempo exacta en que el cliente emitió la calificación (`fecha_calificacion`), no la fecha de apertura del chat.
        * **Encuestas Positivas:** Puntuaciones de **4 y 5 estrellas** sobre el total de encuestas validadas recibidas:
          $$\text{CSAT (\%)} = \left( \frac{\text{Encuestas Positivas}}{\text{Total Encuestas}} \right) \times 100$$
        * **Evolución Semanal:** Agrupa el rendimiento en semanas de **Lunes a Domingo** identificadas con la semana del año (**S41, S42...**) tomando como histórico desde el trimestre anterior ($Q_{n-1}$).
        """)

    with st.expander("6. ¿Cómo opera el Bot de Notificaciones y Alertas por WhatsApp?", expanded=False):
        st.markdown(r"""
        El sistema cuenta con un bot automatizado integrado con Evolution API y Render para el monitoreo proactivo de la atención:

        * **Horario Operativo del Bot:**
          * **Lunes a Viernes:** de 08:15 a 18:00 hs.
          * **Sábados:** de 09:15 a 12:00 hs.
          * **Domingos y Feriados:** Inactivo.
        
        * **Tipos de Alertas:**
          * **Alerta Grupal (Chat Sin Asignar):** Si un chat entrante no tiene agente asignado y supera el tiempo límite configurado ($\ge 1.0$ min), el bot envía una alerta con mención global (`@todos`) al grupo de soporte configurado.
          * **Alerta Grupal Recurrente (Antigüedad de Chats):** Para chats abiertos asignados a agentes humanos, el bot notifica en el grupo de WhatsApp cada vez que se cumple un múltiplo de horas hábiles (cada 3 hs hábiles). En cada reiteración menciona directamente al agente responsable con su `@telefono`.
        
        * **Indicador de Estado (Cabecera):**
          * **🟢 Bot Operativo (open):** Sesión de WhatsApp activa y lista para emitir alertas.
          * **🔴 Bot Desconectado:** La sesión se cerró o se desvinculó. Dispone de un mecanismo de auto-recuperación y un botón de reinicio manual en la pestaña de Administración.
        
        * **Reporte Diario por Correo:**
          * Se despacha automáticamente al correo configurado al finalizar la jornada (Lunes a Viernes a las 18:30 hs) vía Gmail API, incluyendo el resumen ejecutivo de volumen, cumplimiento de SLA, CSAT y el gráfico de distribución por agente.
        """)


# Retirar el overlay de inicio solo después de que el contenido autenticado haya sido construido.
if st.session_state.get("user_authenticated"):
    st.iframe("""
    <script>
    (function() {
        try {
            const doc = window.parent.document;
            window.setTimeout(function() {
                const loginOverlay = doc.getElementById('bims-login-loading-overlay');
                if (loginOverlay) loginOverlay.remove();
            }, 900);
        } catch (e) {}
    })();
    </script>
    """, width=1, height=1)
