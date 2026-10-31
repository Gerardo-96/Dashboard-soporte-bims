import os
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta, time
import pandas as pd
from supabase import create_client
from fastapi import FastAPI, Header, HTTPException
import io
from email.mime.image import MIMEImage
import base64

app = FastAPI()

# ==========================================
# CONFIGURACIÓN DE ENTORNO
# ==========================================
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
EVOLUTION_API_URL = os.environ.get("EVOLUTION_API_URL", "")
EVOLUTION_API_KEY = os.environ.get("EVOLUTION_API_KEY", "")
INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "bims_instance")
RENDER_DEPLOY_HOOK_URL = os.environ.get("RENDER_DEPLOY_HOOK_URL", "")

SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
EMAIL_EMISOR = os.environ.get("EMAIL_EMISOR", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
CRON_SECRET_TOKEN = os.environ.get("CRON_SECRET_TOKEN", "bims_secret_cron_123")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
tz_py = timezone(timedelta(hours=-3))

FERIADOS = [
    "2026-01-01", "2026-03-02", "2026-04-02", "2026-04-03", "2026-05-01", 
    "2026-05-14", "2026-05-15", "2026-06-12", "2026-06-22", "2026-06-30", 
    "2026-08-15", "2026-09-28", "2026-12-08", "2026-12-25", "2026-12-31"
]

ESTADOS_CERRADOS = ["cerrado", "closed", "resolved", "resuelto", "snoozed", "Cerrado", "Closed", "Resolved"]

# ==========================================
# FUNCIONES AUXILIARES Y AUTO-HEALING
# ==========================================

def es_horario_laboral(now_dt):
    """
    Verifica si estamos en jornada laboral (Paraguay) y NO es feriado:
    - Lunes a Viernes: 08:00 a 18:00 hs
    - Sábados: 09:00 a 12:00 hs
    """
    fecha_str = now_dt.strftime("%Y-%m-%d")
    
    # 1. Si hoy es feriado, se inhabilita el envío de notificaciones
    if fecha_str in FERIADOS:
        return False

    dia_semana = now_dt.weekday()
    hora_actual = now_dt.time()
    
    # 2. Evaluación de franja según el día de la semana
    if dia_semana in [0, 1, 2, 3, 4]:  # Lunes a Viernes
        return time(8, 15, 0) <= hora_actual <= time(18, 0, 0)
    elif dia_semana == 5:  # Sábado
        return time(9, 15, 0) <= hora_actual <= time(12, 0, 0)
        
    return False

def parsear_fecha_utc(fecha_raw):
    """Convierte de forma segura un string ISO/UTC a datetime de Paraguay, o retorna None."""
    if not fecha_raw or pd.isna(fecha_raw):
        return None
    try:
        fecha_str = str(fecha_raw).replace("Z", "+00:00")
        return datetime.fromisoformat(fecha_str).astimezone(tz_py)
    except Exception:
        return None

def asegurar_conexion_whatsapp():
    """Valida la conexión con WhatsApp y reconecta/reinicia si detecta caída."""
    base_url = EVOLUTION_API_URL.rstrip('/')
    url_state = f"{base_url}/instance/connectionState/{INSTANCE_NAME}"
    
    try:
        res = requests.get(url_state, headers={"apikey": EVOLUTION_API_KEY}, timeout=8)
        if res.status_code == 200:
            estado = res.json().get("instance", {}).get("state")
            if estado == "open":
                return True # Conexión saludable
    except Exception as e:
        print(f"⚠️ Error verificando estado de WhatsApp: {e}")

    print("🚨 [Auto-Healing] Conexión de WhatsApp caída o inestable. Intentando reconectar...")

    # Intento 1: Llamar al endpoint de conexión
    try:
        url_connect = f"{base_url}/instance/connect/{INSTANCE_NAME}"
        requests.get(url_connect, headers={"apikey": EVOLUTION_API_KEY}, timeout=10)
    except Exception as e:
        print(f"⚠️ Error al intentar re-conectar: {e}")

    # Intento 2: Si hay Deploy Hook configurado, disparar reinicio automático de la instancia
    if RENDER_DEPLOY_HOOK_URL:
        print("🔄 [Auto-Healing] Disparando reinicio automático de Evolution API en Render...")
        try:
            requests.post(RENDER_DEPLOY_HOOK_URL, timeout=10)
        except Exception as e:
            print(f"❌ Error al disparar Deploy Hook: {e}")
            
    return False

# ==========================================
# ENVÍO DE CORREO VÍA GMAIL REST API (HTTPS / PUERTO 443)
# ==========================================
def obtener_access_token_gmail():
    """Genera un token de acceso temporal utilizando el refresh token de Google."""
    client_id = os.environ.get("GMAIL_CLIENT_ID", "")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN", "")

    if not client_id or not client_secret or not refresh_token:
        print("❌ Faltan credenciales de Gmail OAuth en las variables de entorno.")
        return None

    url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }

    try:
        resp = requests.post(url, data=payload, timeout=12)
        if resp.status_code == 200:
            return resp.json().get("access_token")
        else:
            print(f"❌ Error renovando Access Token de Gmail: {resp.text}")
            return None
    except Exception as e:
        print(f"❌ Excepción contactando endpoint OAuth de Google: {e}")
        return None

def enviar_correo_gmail_api(email_emisor, email_dest, asunto, html_body):
    """Construye el mensaje MIME y lo envía por la API de Gmail por puerto 443."""
    access_token = obtener_access_token_gmail()
    if not access_token:
        return {"status": "error_token", "error": "No se pudo obtener el Access Token de Gmail"}

    # Construcción estándar del correo HTML
    msg = MIMEMultipart("alternative")
    msg["From"] = email_emisor
    msg["To"] = email_dest
    msg["Subject"] = asunto
    msg.attach(MIMEText(html_body, "html"))

    # Gmail API requiere codificación Base64 URL-Safe
    raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    url_send = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    try:
        res = requests.post(url_send, json={"raw": raw_message}, headers=headers, timeout=15)
        if res.status_code in [200, 201]:
            print(f"✅ Reporte diario enviado exitosamente vía Gmail API a {email_dest}")
            return {"status": "success", "destino": email_dest, "gmail_id": res.json().get("id")}
        else:
            print(f"❌ Error en Gmail API: {res.text}")
            return {"status": "error_gmail_api", "error": res.json()}
    except Exception as ex:
        print(f"❌ Excepción enviando correo por Gmail API: {ex}")
        return {"status": "error_http", "error": str(ex)}
        

def calcular_minutos_habiles_rapido(dt_inicio, dt_fin):
    if pd.isna(dt_inicio) or pd.isna(dt_fin) or dt_fin <= dt_inicio:
        return 0.0

    tz = "America/Asuncion"
    total_minutos_habiles = 0.0
    dia_actual = dt_inicio.date() - timedelta(days=1)
    dia_fin = dt_fin.date()

    while dia_actual <= dia_fin:
        fecha_str = dia_actual.strftime("%Y-%m-%d")
        dia_semana = dia_actual.weekday()

        if fecha_str in FERIADOS:
            dia_actual += timedelta(days=1)
            continue

        intervalos = []
        if dia_semana in [0, 1, 2, 3, 4]:
            intervalos.append((time(8, 0), time(17, 30), False))
        elif dia_semana == 5:
            intervalos.append((time(9, 0), time(11, 45), False))

        for h_inicio, h_fin, cruza_medianoche in intervalos:
            inicio_franja = pd.Timestamp.combine(dia_actual, h_inicio).tz_localize(tz)
            fin_franja = pd.Timestamp.combine(dia_actual, h_fin).tz_localize(tz)
            
            start_overlap = max(dt_inicio, inicio_franja)
            end_overlap = min(dt_fin, fin_franja)
            
            if start_overlap < end_overlap:
                total_minutos_habiles += (end_overlap - start_overlap).total_seconds() / 60.0

        dia_actual += timedelta(days=1)

    return round(total_minutos_habiles, 1)

def enviar_whatsapp(numero_destino, texto, menciones=[], mencionar_todos=False):
    """Envía un mensaje de WhatsApp con formato estándar para Evolution API v2."""
    base_url = EVOLUTION_API_URL.rstrip('/')
    url = f"{base_url}/message/sendText/{INSTANCE_NAME}"
    
    headers = {
        "apikey": EVOLUTION_API_KEY, 
        "Content-Type": "application/json"
    }
    
    payload = {
        "number": str(numero_destino),
        "text": str(texto)
    }
    
    # Mención a todos en el grupo
    if mencionar_todos:
        payload["mentionsEveryOne"] = True
    elif menciones:
        payload["mentionsEveryOne"] = False
        payload["mentioned"] = menciones

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        print(f"Respuesta Evolution API ({res.status_code}): {res.text}")
    except Exception as e:
        print(f"Error enviando WhatsApp: {e}")

# ==========================================
# PROCESAMIENTO PRINCIPAL
# ==========================================

def procesar_alertas_y_reportes():

    # Verificar salud de la conexión antes de evaluar chats
    if not asegurar_conexion_whatsapp():
        print("❌ Se pausó la evaluación de alertas debido a un reinicio en curso de WhatsApp.")
        return {"status": "reconnecting", "reason": "Restableciendo conexión con WhatsApp"}
        
    now_dt = datetime.now(tz_py)

    if not es_horario_laboral(now_dt):
        print(f"⏰ [{now_dt.strftime('%Y-%m-%d %H:%M:%S')}] Fuera de horario laboral. Omitido.")
        return {"status": "skipped", "reason": "Fuera de horario laboral"}

    cfg_res = supabase.table("config_notificaciones").select("*").eq("id", 1).execute()
    if not cfg_res.data:
        return {"status": "error", "reason": "Configuración no encontrada"}
    cfg = cfg_res.data[0]

    usrs_res = supabase.table("usuarios_autorizados").select("nombre, telefono_whatsapp").execute()
    mapa_tels = {
        u["nombre"].strip().lower(): u["telefono_whatsapp"].strip() 
        for u in (usrs_res.data or []) if u.get("telefono_whatsapp")
    }

    # -------------------------------------------------------------
    # A. ALERTA INDIVIDUAL (Únicamente para chats SIN ASIGNAR >= X min)
    # -------------------------------------------------------------
    min_indiv_th = float(cfg.get("minutos_alerta_individual", 5.0))
    tel_destino_indiv = cfg.get("telefono_alerta_individual")

    if tel_destino_indiv:
        res_activos = supabase.table("conversaciones")\
            .select("id, created_at, updated_at, tenant, nombre_contacto, primera_respuesta_min, estado, agente_asignado")\
            .is_("primera_respuesta_min", "null")\
            .not_.in_("estado", ESTADOS_CERRADOS)\
            .execute()

        for c in (res_activos.data or []):
            agente_raw = str(c.get("agente_asignado") or "").strip()
            agente_lower = agente_raw.lower()

            # Descarte de Mónica (IA) y de cualquier agente humano que ya esté asignado
            if any(bot_tag in agente_lower for bot_tag in ["monica", "mónica"]):
                continue
                
            # 2. Evaluación de 'Sin Asignar': Solo se evalúa si NO tiene agente humano asignado
            es_sin_asignar = (
                not agente_lower or 
                agente_lower in ["sin asignar", "none", "nan", "soporte"] or 
                "sin asignar" in agente_lower
            )

            if not es_sin_asignar:
                continue

            fecha_ref = c.get("updated_at") or c.get("created_at")
            created_dt = parsear_fecha_utc(fecha_ref)
            if not created_dt:
                continue

            min_trans = (now_dt - created_dt).total_seconds() / 60.0

            if min_trans >= min_indiv_th:
                # Agregamos la mención visible en el texto
                msg = (
                    f"⚠️ *@todos chat sin 1ra Respuesta*\n\n"
                    f"*ID:* {c['id']}\n"
                    f"*Contacto:* {c.get('nombre_contacto', 'Sin nombre')}\n"
                    f"*Tenant:* {c.get('tenant', 'Sin datos')}\n"
                    f"*Espera:* {round(min_trans, 1)} min\n"
                )
                
                # Enviamos con el flag activar mención global
                enviar_whatsapp(tel_destino_indiv, msg, mencionar_todos=True)

    # -------------------------------------------------------------
    # B. ALERTA GRUPAL RECURRENTE (Múltiplos de X hs HÁBILES)
    # -------------------------------------------------------------
    hs_grupo_th = float(cfg.get("horas_alerta_grupo", 3.0))
    minutos_grupo_th = hs_grupo_th * 60.0
    jid_grupo = cfg.get("jid_grupo_whatsapp")

    print(f"📌 [PUNTO DE CONTROL 1] Llegó a Sección B. JID: '{jid_grupo}' | Umbral: {hs_grupo_th}")
    
    if jid_grupo and hs_grupo_th > 0:
        res_abiertos = supabase.table("conversaciones")\
            .select("id, created_at, tenant, agente_asignado, nombre_contacto, estado, ultimo_multiplo_notificado, ai_title")\
            .not_.in_("estado", ESTADOS_CERRADOS)\
            .execute()

        chats_recuperados = res_abiertos.data or []
        print(f"📌 [PUNTO 2] Supabase devolvió {len(chats_recuperados)} chats abiertos.")

        for c in chats_recuperados:
            agente_raw = str(c.get("agente_asignado") or "").strip()
            agente_lower = agente_raw.lower()

            created_dt = parsear_fecha_utc(c.get("created_at"))
            if not created_dt:
                continue

            minutos_habiles = calcular_minutos_habiles_rapido(created_dt, now_dt)
            hs_habiles = round(minutos_habiles / 60.0, 1)

            # Múltiplo actual según las horas transcurridas
            multiplo_actual = int(minutos_habiles // minutos_grupo_th)
            ultimo_multiplo = int(c.get("ultimo_multiplo_notificado") or 0)

            print(f"🔍 [EVALUANDO GRUPO] Chat: {c['id']} | Agente: '{agente_raw}' | Hs Hábiles: {hs_habiles} | Múltiplo actual: {multiplo_actual} | Último notificado: {ultimo_multiplo}")

            # Descarte de IA, bots y chats sin asignar (sin la cadena vacía)
            if not agente_lower or any(b in agente_lower for b in ["monica", "mónica", "soporte", "sin asignar", "none", "nan"]):
                print(f"⏭️ [OMITIDO BOT/SIN ASIGNAR] Chat {c['id']} ({agente_raw})")
                continue

            # Si alcanzó un nuevo múltiplo (1x, 2x, 3x...), dispara la alerta correspondiente
            if multiplo_actual >= 1 and multiplo_actual > ultimo_multiplo:
                tel_agente = None
                
                # Búsqueda limpia del teléfono del agente
                if agente_lower in mapa_tels:
                    tel_agente = mapa_tels[agente_lower]
                else:
                    primer_nombre = agente_lower.split()[0] if agente_lower.split() else agente_lower
                    for nom_usr, tel_usr in mapa_tels.items():
                        if primer_nombre in nom_usr or nom_usr in agente_lower:
                            tel_agente = tel_usr
                            break

                tag_agente = f"@{tel_agente}" if tel_agente else f"*{agente_raw}*"
                menciones_list = [tel_agente] if tel_agente else []

                ai_title = c.get("ai_title") or "Sin título"

                if multiplo_actual == 1:
                    header_alerta = "🚨 *ALERTA DE SLA: Chat Pendiente*"
                else:
                    header_alerta = f"🚨 *REITERACIÓN DE SLA #{multiplo_actual}: Chat Pendiente*"

                msg_chat = (
                    f"{header_alerta}\n\n"
                    f"*ID:* {c['id']}\n"
                    f"*Título IA:* {ai_title}\n"
                    f"*Cliente:* {c.get('nombre_contacto', 'Sin nombre')}\n"
                    f"*Tenant:* {c.get('tenant', 'Sin datos')}\n"
                    f"*Agente:* {tag_agente}\n"
                    f"*Espera:* {hs_habiles} hs hábiles"
                )

                enviar_whatsapp(jid_grupo, msg_chat, menciones=menciones_list)

                # Actualizar el registro en Supabase con el nuevo múltiplo notificado
                supabase.table("conversaciones")\
                    .update({"ultimo_multiplo_notificado": multiplo_actual})\
                    .eq("id", c['id'])\
                    .execute()
                
    
    return {"status": "success", "processed_at": now_dt.strftime("%Y-%m-%d %H:%M:%S")}

# ==========================================
# RUTAS DEL SERVIDOR FASTAPI
# ==========================================

@app.get("/")
def home():
    return {"status": "ok", "service": "Notificador BIMS activo."}

@app.get("/ejecutar-notificaciones")
def trigger_notificaciones(authorization: str = Header(None)):
    if authorization != f"Bearer {CRON_SECRET_TOKEN}":
        raise HTTPException(status_code=401, detail="No autorizado")
    
    try:
        resultado = procesar_alertas_y_reportes()
        print(f"Resultado ejecucion: {resultado}")
    except Exception as e:
        print(f"Error procesando notificaciones: {e}")

    return {"status": "ok"}

@app.get("/health-status")
def health_status():
    """Consulta el estado de salud del webhook y la conexión con WhatsApp."""
    base_url = EVOLUTION_API_URL.rstrip('/')
    url_state = f"{base_url}/instance/connectionState/{INSTANCE_NAME}"
    
    estado_whatsapp = "disconnected"
    evolution_online = False

    try:
        res = requests.get(url_state, headers={"apikey": EVOLUTION_API_KEY}, timeout=5)
        if res.status_code == 200:
            evolution_online = True
            datos = res.json()
            estado_whatsapp = datos.get("instance", {}).get("state", "disconnected")
    except Exception as e:
        print(f"Error consultando estado de WhatsApp: {e}")

    return {
        "webhook_online": True,
        "evolution_online": evolution_online,
        "whatsapp_state": estado_whatsapp,
        "status_general": "OK" if (evolution_online and estado_whatsapp == "open") else "CRITICAL"
    }

@app.post("/reiniciar-servicio")
def reiniciar_servicio(authorization: str = Header(None)):
    """Dispara un re-despliegue/reinicio del contenedor en Render."""
    if authorization != f"Bearer {CRON_SECRET_TOKEN}":
        raise HTTPException(status_code=401, detail="No autorizado")

    if not RENDER_DEPLOY_HOOK_URL:
        return {"status": "error", "message": "Deploy Hook URL no configurado"}

    try:
        res = requests.post(RENDER_DEPLOY_HOOK_URL, timeout=10)
        return {
            "status": "success",
            "message": "Petición de reinicio enviada a Render exitosamente.",
            "render_response_code": res.status_code
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

# ==========================================
# ENDPOINT EXCLUSIVO: REPORTE DIARIO CON BARRAS HTML & MÉTRICAS COMPLETAS
# ==========================================
@app.post("/ejecutar-reporte-email-diario")
@app.get("/ejecutar-reporte-email-diario")
def trigger_reporte_email(authorization: str = Header(None)):
    """
    Cron exclusivo para enviar el resumen diario por correo con métricas completas de SLA,
    CSAT, volumen general y gráfico de barras HTML idéntico a la pestaña 1 del Dashboard.
    Envío mediante la API REST de Gmail (vía HTTPS / puerto 443).
    """
    if authorization != f"Bearer {CRON_SECRET_TOKEN}":
        raise HTTPException(status_code=401, detail="No autorizado")

    cfg_res = supabase.table("config_notificaciones").select("*").eq("id", 1).execute()
    if not cfg_res.data:
        raise HTTPException(status_code=404, detail="Configuración no encontrada en Supabase")

    cfg = cfg_res.data[0]
    email_dest = cfg.get("email_destino_reporte")

    if not email_dest:
        return {"status": "skipped", "reason": "No hay email de destino configurado"}
    if not EMAIL_EMISOR:
        return {"status": "error", "reason": "Falta la variable de entorno EMAIL_EMISOR en Render"}

    now_dt = datetime.now(tz_py)
    hoy_str = now_dt.strftime("%Y-%m-%d")
    hoy_date_py = now_dt.date()

    # Si es fin de semana o feriado oficial, no despacha reporte
    #if hoy_str in FERIADOS or now_dt.weekday() in [5, 6]:
        #return {"status": "skipped", "reason": "Día no hábil / Feriado"}

    agentes_omitir = [
        "monica", "mónica", "monica (bot)", "soporte", 
        "gerardo gaona", "gerardo.gaona@itti.digital", 
        "none", "nan", "sin asignar", ""
    ]

    # Umbrales estándar del Dashboard
    sla_1ra_th = 2.0
    sla_gest_th = 60.0

    # 1. Obtener lista de agentes autorizados
    lista_agentes_maestra = set()
    try:
        res_u = supabase.table("usuarios_autorizados").select("nombre").eq("activo", True).execute()
        for u in (res_u.data or []):
            nom = u.get("nombre", "").strip()
            if nom and nom.lower() not in agentes_omitir:
                lista_agentes_maestra.add(nom)
    except Exception:
        pass

    # 2. Consultar chats necesarios desde Supabase
    hoy_local_dt = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    hoy_inicio_utc = hoy_local_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    COLUMNAS_CONSULTA = (
        "id, created_at, updated_at, estado, agente_asignado, por_agente, "
        "primera_respuesta_min, tiempo_resolucion_minutos, rating, fecha_calificacion, "
        "etiquetas, fecha_cierre"
    )

    # A. Chats abiertos actualmente
    res_abiertos = supabase.table("conversaciones")\
        .select(COLUMNAS_CONSULTA)\
        .not_.in_("estado", ESTADOS_CERRADOS)\
        .execute()

    # B. Chats creados hoy
    res_creados_hoy = supabase.table("conversaciones")\
        .select(COLUMNAS_CONSULTA)\
        .gte("created_at", hoy_inicio_utc)\
        .execute()

    # C. Chats calificados hoy (para CSAT exacto)
    res_csat_hoy = supabase.table("conversaciones")\
        .select(COLUMNAS_CONSULTA)\
        .gte("fecha_calificacion", hoy_inicio_utc)\
        .execute()

    # D. Chats cerrados hoy
    res_cerrados_hoy = supabase.table("conversaciones")\
        .select(COLUMNAS_CONSULTA)\
        .in_("estado", ESTADOS_CERRADOS)\
        .gte("fecha_cierre", hoy_inicio_utc)\
        .execute()

    todos = (res_abiertos.data or []) + (res_creados_hoy.data or []) + (res_csat_hoy.data or []) + (res_cerrados_hoy.data or [])
    unicos = {r["id"]: r for r in todos}
    df_all = pd.DataFrame(list(unicos.values())) if unicos else pd.DataFrame()

    # 3. Normalización y cálculo de fechas en America/Asuncion
    if not df_all.empty:
        # created_at
        c_dt = pd.to_datetime(df_all["created_at"], errors="coerce", utc=True)
        df_all["created_at_dt"] = c_dt.dt.tz_convert("America/Asuncion")
        df_all["fecha_creado_py"] = df_all["created_at_dt"].dt.date

        # fecha_cierre / updated_at
        df_all["fecha_ref"] = df_all["fecha_cierre"].fillna(df_all["updated_at"])
        ref_dt = pd.to_datetime(df_all["fecha_ref"], errors="coerce", utc=True)
        df_all["fecha_cierre_dt"] = ref_dt.dt.tz_convert("America/Asuncion")
        df_all["fecha_cierre_py"] = df_all["fecha_cierre_dt"].dt.date

        # fecha_calificacion
        calif_dt = pd.to_datetime(df_all.get("fecha_calificacion"), errors="coerce", utc=True)
        df_all["fecha_calif_py"] = calif_dt.dt.tz_convert("America/Asuncion").dt.date

        # Estado y agente
        df_all["estado_clean"] = df_all["estado"].fillna("").astype(str).str.strip().str.lower()
        df_all["es_cerrado"] = df_all["estado_clean"].isin([e.lower() for e in ESTADOS_CERRADOS])
        df_all["agente_clean"] = df_all["agente_asignado"].fillna("Sin asignar").astype(str).str.strip()
        df_all["por_agente_clean"] = df_all.get("por_agente", pd.Series(dtype=str)).fillna("").astype(str).str.strip().str.lower()
        df_all["etiquetas_clean"] = df_all.get("etiquetas", pd.Series(dtype=str)).fillna("").astype(str).str.strip().str.lower()

        # Evaluación de horario hábil
        dias_sem = df_all["created_at_dt"].dt.dayofweek
        horas_dec = df_all["created_at_dt"].dt.hour + (df_all["created_at_dt"].dt.minute / 60.0)
        es_normal_lv = (dias_sem.isin([0, 1, 2, 3, 4])) & (horas_dec >= 8.0) & (horas_dec <= 17.5)
        es_normal_sab = (dias_sem == 5) & (horas_dec >= 9.0) & (horas_dec <= 11.75)
        df_all["es_horario_habil"] = (es_normal_lv | es_normal_sab) & (~df_all["created_at_dt"].dt.strftime("%Y-%m-%d").isin(FERIADOS))

        # Tiempo de resolución en minutos hábiles
        df_all["tiempo_res_min"] = df_all.apply(
            lambda r: calcular_minutos_habiles_rapido(r["created_at_dt"], r["fecha_cierre_dt"])
            if r.get("es_horario_habil") and r.get("es_cerrado") else None,
            axis=1
        )
    else:
        df_all = pd.DataFrame()

    # ==========================================
    # CÁLCULOS IDÉNTICOS A LA PESTAÑA 1 DEL DASHBOARD
    # ==========================================
    # Chats del día (creados hoy)
    df_hoy = df_all[df_all["fecha_creado_py"] == hoy_date_py].copy() if not df_all.empty else pd.DataFrame()

    # Conteo general
    total_recibidos_hoy = len(df_hoy)
    total_abiertos_actuales = len(df_all[~df_all["es_cerrado"]]) if not df_all.empty else 0
    
    # Cerrados hoy que además fueron ASIGNADOS/CREADOS HOY
    cerrados_hoy_asig_hoy = (
        (df_all["es_cerrado"]) & 
        (df_all["fecha_cierre_py"] == hoy_date_py) & 
        (df_all["fecha_creado_py"] == hoy_date_py)
    ) if not df_all.empty else pd.Series(dtype=bool)
    total_cerrados_hoy = int(cerrados_hoy_asig_hoy.sum()) if not df_all.empty else 0

    # 1. CSAT del día
    csat_pct = 0.0
    csat_total = 0
    if not df_all.empty:
        df_csat = df_all[
            (df_all["fecha_calif_py"] == hoy_date_py) & 
            (df_all["rating"].notna()) &
            (df_all["por_agente_clean"] != "excluido")
        ].copy()
        if not df_csat.empty:
            df_csat["rating_num"] = pd.to_numeric(df_csat["rating"], errors="coerce")
            df_csat = df_csat.dropna(subset=["rating_num"])
            csat_total = len(df_csat)
            if csat_total > 0:
                positivas = (df_csat["rating_num"] >= 4).sum()
                csat_pct = round((positivas / csat_total) * 100, 1)

    # 2. Promedio y Cumplimiento de SLA 1ra Respuesta
    prom_1ra = 0.0
    pct_sla_1ra = 0.0
    if not df_hoy.empty:
        es_agente_valido_1ra = (
            (df_hoy["por_agente_clean"] == "no excluido") &
            (~df_hoy["agente_clean"].str.lower().isin(agentes_omitir))
        )
        df_1ra = df_hoy[es_agente_valido_1ra & df_hoy["es_horario_habil"]].copy()
        df_1ra["p_1ra_num"] = pd.to_numeric(df_1ra["primera_respuesta_min"], errors="coerce")
        s_1ra = df_1ra["p_1ra_num"].dropna()
        if not s_1ra.empty:
            prom_1ra = round(s_1ra.mean(), 2)

        # Cumplimiento SLA 1ra Respuesta (excluidos, cumple, no cumple)
        df_1ra_eval = df_hoy[es_agente_valido_1ra & df_hoy["es_horario_habil"]].copy()
        if not df_1ra_eval.empty:
            cumplen_1ra = (df_1ra_eval["primera_respuesta_min"] <= sla_1ra_th).sum()
            pct_sla_1ra = round((cumplen_1ra / len(df_1ra_eval)) * 100, 1)

    # 3. Promedio y Cumplimiento de SLA Gestión
    prom_gest = 0.0
    pct_sla_gest = 0.0
    if not df_hoy.empty:
        es_sin_respuesta = df_hoy["etiquetas_clean"].str.contains("sin respuesta", na=False)
        es_agente_valido_gest = (
            (df_hoy["por_agente_clean"] == "no excluido") &
            (~df_hoy["agente_clean"].str.lower().isin(agentes_omitir))
        )
        df_gest = df_hoy[
            es_agente_valido_gest & 
            df_hoy["es_horario_habil"] & 
            (~es_sin_respuesta) & 
            df_hoy["es_cerrado"]
        ].copy()
        df_gest["p_gest_num"] = pd.to_numeric(df_gest["tiempo_res_min"], errors="coerce")
        s_gest = df_gest["p_gest_num"].dropna()
        if not s_gest.empty:
            prom_gest = round(s_gest.mean(), 2)
            cumplen_gest = (s_gest <= sla_gest_th).sum()
            pct_sla_gest = round((cumplen_gest / len(s_gest)) * 100, 1)

    # ==========================================
    # DISTRIBUCIÓN POR AGENTE (BARRAS Y TABLA)
    # ==========================================
    if not df_all.empty:
        df_all["es_cerrado_hoy_asig_hoy"] = cerrados_hoy_asig_hoy
        df_all["es_abierto_actual"] = ~df_all["es_cerrado"]
        for ag in df_all["agente_clean"].unique():
            if ag.lower() not in agentes_omitir:
                lista_agentes_maestra.add(ag)

    resumen_agentes = []
    for agente in sorted(list(lista_agentes_maestra)):
        if agente.lower() in agentes_omitir:
            continue

        if not df_all.empty:
            grp = df_all[df_all["agente_clean"] == agente]
            pendientes = int(grp["es_abierto_actual"].sum()) if not grp.empty else 0
            cerrados = int(grp["es_cerrado_hoy_asig_hoy"].sum()) if not grp.empty else 0
        else:
            pendientes = 0
            cerrados = 0

        resumen_agentes.append({
            "agente": agente,
            "cerrados": cerrados,
            "pendientes": pendientes,
            "total": cerrados + pendientes
        })

    df_res = pd.DataFrame(resumen_agentes)
    if not df_res.empty:
        df_res = df_res.sort_values(by="total", ascending=False)

    max_total = df_res["total"].max() if (not df_res.empty and df_res["total"].max() > 0) else 1
    barras_grafico_html = []

    for _, r in df_res.iterrows():
        nom = r["agente"]
        c = r["cerrados"]
        p = r["pendientes"]
        tot = r["total"]

        pct_c = round((c / max_total) * 100, 1)
        pct_p = round((p / max_total) * 100, 1)

        txt_c = f"{c}" if c > 0 else ""
        txt_p = f"{p}" if p > 0 else ""

        barras_grafico_html.append(f"""
        <div style="margin-bottom: 14px;">
            <div style="display: flex; justify-content: space-between; font-size: 0.85rem; color: #cbd5e1; margin-bottom: 4px;">
                <span><b>{nom}</b></span>
                <span style="color: #f8fafc; font-weight: 700;">Total: {tot}</span>
            </div>
            <div style="background-color: #0f172a; border-radius: 6px; height: 26px; display: flex; overflow: hidden; width: 100%; border: 1px solid #334155;">
                <div style="width: {pct_c}%; background-color: #10b981; color: white; font-size: 0.78rem; font-weight: 700; display: flex; align-items: center; justify-content: center;" title="Cerrados: {c}">
                    {txt_c}
                </div>
                <div style="width: {pct_p}%; background-color: #f97316; color: white; font-size: 0.78rem; font-weight: 700; display: flex; align-items: center; justify-content: center;" title="Pendientes: {p}">
                    {txt_p}
                </div>
            </div>
        </div>
        """)

    filas_table = "".join([
        f"<tr><td style='padding:9px;border:1px solid #334155;color:#f8fafc;'>{r['agente']}</td>"
        f"<td style='padding:9px;border:1px solid #334155;text-align:center;font-weight:700;color:#f8fafc;'>{r['total']}</td>"
        f"<td style='padding:9px;border:1px solid #334155;text-align:center;color:#10b981;font-weight:700;'>{r['cerrados']}</td>"
        f"<td style='padding:9px;border:1px solid #334155;text-align:center;color:#f97316;font-weight:700;'>{r['pendientes']}</td></tr>"
        for _, r in df_res.iterrows()
    ]) if not df_res.empty else "<tr><td colspan='4' style='text-align:center;padding:12px;color:#94a3b8;'>Sin actividad registrada.</td></tr>"

    color_sla_1ra = "#10b981" if pct_sla_1ra >= 90.0 else "#ef4444"
    color_sla_gest = "#10b981" if pct_sla_gest >= 90.0 else "#ef4444"
    color_csat = "#10b981" if csat_pct >= 90.0 else "#38bdf8"

    # ==========================================
    # PLANTILLA HTML DEL CORREO
    # ==========================================
    html_body = f"""
    <div style="font-family: Arial, sans-serif; background-color: #0f172a; padding: 25px; color: #f8fafc;">
        <div style="max-width: 660px; margin: 0 auto; background-color: #1e293b; border-radius: 12px; border: 1px solid #334155; padding: 24px;">
            <h2 style="color: #38bdf8; margin-top: 0; font-size: 1.3rem;">Cierre Operativo Soporte BIMS - {hoy_str}</h2>
            <p style="color: #94a3b8; font-size: 0.88rem; margin-bottom: 20px;">
                Resumen ejecutivo de indicadores de servicio, SLA y distribución de carga de la jornada:
            </p>

            <!-- 1. BLOQUE DE RESUMEN DE VOLUMEN DEL DÍA -->
            <table style="width: 100%; border-collapse: separate; border-spacing: 8px; margin-bottom: 16px;">
                <tr>
                    <td style="background-color: #0f172a; border-radius: 8px; padding: 12px; border: 1px solid #334155; border-left: 4px solid #0284c7; text-align: center; width: 33%;">
                        <div style="color: #94a3b8; font-size: 0.75rem; text-transform: uppercase; font-weight: 600;">Ingresados Hoy</div>
                        <div style="color: #f8fafc; font-size: 1.4rem; font-weight: 700; margin-top: 4px;">{total_recibidos_hoy}</div>
                    </td>
                    <td style="background-color: #0f172a; border-radius: 8px; padding: 12px; border: 1px solid #334155; border-left: 4px solid #10b981; text-align: center; width: 33%;">
                        <div style="color: #94a3b8; font-size: 0.75rem; text-transform: uppercase; font-weight: 600;">Cerrados Hoy</div>
                        <div style="color: #10b981; font-size: 1.4rem; font-weight: 700; margin-top: 4px;">{total_cerrados_hoy}</div>
                        <div style="color: #64748b; font-size: 0.7rem;">(Asignados hoy)</div>
                    </td>
                    <td style="background-color: #0f172a; border-radius: 8px; padding: 12px; border: 1px solid #334155; border-left: 4px solid #f97316; text-align: center; width: 33%;">
                        <div style="color: #94a3b8; font-size: 0.75rem; text-transform: uppercase; font-weight: 600;">Pendientes Actuales</div>
                        <div style="color: #f97316; font-size: 1.4rem; font-weight: 700; margin-top: 4px;">{total_abiertos_actuales}</div>
                    </td>
                </tr>
            </table>

            <!-- 2. BLOQUE DE MÉTRICAS OPERATIVAS Y SLA -->
            <table style="width: 100%; border-collapse: separate; border-spacing: 8px; margin-bottom: 24px;">
                <tr>
                    <td style="background-color: #0f172a; border-radius: 8px; padding: 12px; border: 1px solid #334155; border-left: 4px solid {color_csat}; text-align: center; width: 20%;">
                        <div style="color: #94a3b8; font-size: 0.72rem; text-transform: uppercase; font-weight: 600;">CSAT Hoy</div>
                        <div style="color: {color_csat}; font-size: 1.3rem; font-weight: 700; margin-top: 4px;">{csat_pct}%</div>
                        <div style="color: #64748b; font-size: 0.7rem;">{csat_total} encuestas</div>
                    </td>
                    <td style="background-color: #0f172a; border-radius: 8px; padding: 12px; border: 1px solid #334155; border-left: 4px solid #0284c7; text-align: center; width: 20%;">
                        <div style="color: #94a3b8; font-size: 0.72rem; text-transform: uppercase; font-weight: 600;">Prom. 1ra Resp.</div>
                        <div style="color: #f8fafc; font-size: 1.3rem; font-weight: 700; margin-top: 4px;">{prom_1ra}m</div>
                        <div style="color: #64748b; font-size: 0.7rem;">Meta ≤ {sla_1ra_th}m</div>
                    </td>
                    <td style="background-color: #0f172a; border-radius: 8px; padding: 12px; border: 1px solid #334155; border-left: 4px solid {color_sla_1ra}; text-align: center; width: 20%;">
                        <div style="color: #94a3b8; font-size: 0.72rem; text-transform: uppercase; font-weight: 600;">% SLA 1ra</div>
                        <div style="color: {color_sla_1ra}; font-size: 1.3rem; font-weight: 700; margin-top: 4px;">{pct_sla_1ra}%</div>
                        <div style="color: #64748b; font-size: 0.7rem;">Meta ≥ 90%</div>
                    </td>
                    <td style="background-color: #0f172a; border-radius: 8px; padding: 12px; border: 1px solid #334155; border-left: 4px solid #0284c7; text-align: center; width: 20%;">
                        <div style="color: #94a3b8; font-size: 0.72rem; text-transform: uppercase; font-weight: 600;">Prom. Gestión</div>
                        <div style="color: #f8fafc; font-size: 1.3rem; font-weight: 700; margin-top: 4px;">{prom_gest}m</div>
                        <div style="color: #64748b; font-size: 0.7rem;">Meta ≤ {sla_gest_th}m</div>
                    </td>
                    <td style="background-color: #0f172a; border-radius: 8px; padding: 12px; border: 1px solid #334155; border-left: 4px solid {color_sla_gest}; text-align: center; width: 20%;">
                        <div style="color: #94a3b8; font-size: 0.72rem; text-transform: uppercase; font-weight: 600;">% SLA Gest.</div>
                        <div style="color: {color_sla_gest}; font-size: 1.3rem; font-weight: 700; margin-top: 4px;">{pct_sla_gest}%</div>
                        <div style="color: #64748b; font-size: 0.7rem;">Meta ≥ 90%</div>
                    </td>
                </tr>
            </table>

            <!-- 3. CONTENEDOR DEL GRÁFICO VISUAL -->
            <div style="background-color: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 16px; margin-bottom: 24px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                    <span style="font-weight: 700; font-size: 0.95rem; color: #38bdf8;">Carga por Agente (Asignados Hoy vs Abiertos)</span>
                    <div style="font-size: 0.78rem;">
                        <span style="color: #10b981; font-weight: 700; margin-right: 12px;">🟢 Cerrados Hoy</span>
                        <span style="color: #f97316; font-weight: 700;">🔴 Pendientes</span>
                    </div>
                </div>
                {''.join(barras_grafico_html)}
            </div>

            <!-- 4. TABLA DE DETALLE -->
            <table style='border-collapse:collapse;width:100%;font-size:0.85rem;background-color:#0f172a;border-radius:6px;overflow:hidden;'>
                <thead>
                    <tr style='background:#0284c7;color:white;'>
                        <th style='padding:9px;border:1px solid #0284c7;text-align:left;'>Agente</th>
                        <th style='padding:9px;border:1px solid #0284c7;text-align:center;'>Total</th>
                        <th style='padding:9px;border:1px solid #0284c7;text-align:center;'>Cerrados (Hoy)</th>
                        <th style='padding:9px;border:1px solid #0284c7;text-align:center;'>Pendientes</th>
                    </tr>
                </thead>
                <tbody>
                    {filas_table}
                </tbody>
            </table>

            <p style="font-size:0.75rem;color:#64748b;margin-top:22px;border-top:1px solid #334155;padding-top:12px;">
                Reporte diario automatizado emitido por el sistema de monitoreo BIMS.
            </p>
        </div>
    </div>
    """

    asunto_mail = f"Cierre Operativo Soporte BIMS - {hoy_str}"

    return enviar_correo_gmail_api(
        email_emisor=EMAIL_EMISOR,
        email_dest=email_dest,
        asunto=asunto_mail,
        html_body=html_body
    )
