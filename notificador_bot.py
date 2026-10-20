import os
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta, time
import pandas as pd
from supabase import create_client
from fastapi import FastAPI, Header, HTTPException

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
    """Verifica si es de Lunes a Viernes entre las 08:00 y las 18:00 hs (Paraguay) y NO es feriado."""
    fecha_str = now_dt.strftime("%Y-%m-%d")
    
    # 1. Si hoy es feriado, se inhabilita el envío de notificaciones
    if fecha_str in FERIADOS:
        return False

    dia_semana = now_dt.weekday()
    hora_actual = now_dt.time()
    
    es_dia_habil = dia_semana in [0, 1, 2, 3, 4]
    es_hora_habil = time(8, 0, 0) <= hora_actual <= time(18, 0, 0)
    
    return es_dia_habil and es_hora_habil

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

def enviar_whatsapp(numero_destino, texto, menciones=[]):
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
    
    if menciones:
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
    # A. ALERTA INDIVIDUAL (Sin primera respuesta >= X min - Regla del Dashboard)
    # -------------------------------------------------------------
    min_indiv_th = float(cfg.get("minutos_alerta_individual", 5.0))
    tel_destino_indiv = cfg.get("telefono_alerta_individual")

    if tel_destino_indiv:
        # Filtra únicamente chats que NO tengan primera respuesta (primera_respuesta_min IS NULL)
        res_activos = supabase.table("conversaciones")\
            .select("id, created_at, tenant, nombre_contacto, primera_respuesta_min, estado")\
            .is_("primera_respuesta_min", "null")\
            .not_.in_("estado", ESTADOS_CERRADOS)\
            .execute()

        for c in (res_activos.data or []):
            created_dt = parsear_fecha_utc(c.get("created_at"))
            if not created_dt:
                continue

            min_trans = (now_dt - created_dt).total_seconds() / 60.0

            if min_trans >= min_indiv_th:
                url_chat = f"https://app.intercom.io/a/apps/co9kozj6/inbox/inbox/all/conversations/{c['id']}"
                msg = (
                    f"⚠️ *Chat Sin Respuesta (> {int(min_indiv_th)} min)*\n\n"
                    f"*ID:* {c['id']}\n"
                    f"*Contacto:* {c.get('nombre_contacto', 'Sin nombre')}\n"
                    f"*Tenant:* {c.get('tenant', 'Sin datos')}\n"
                    f"*Espera:* {round(min_trans, 1)} min\n"
                    f"🔗 {url_chat}"
                )
                enviar_whatsapp(tel_destino_indiv, msg)

    # -------------------------------------------------------------
    # B. ALERTA GRUPAL RECURRENTE (Múltiplos de X hs HÁBILES: 5h, 10h, 15h...)
    # -------------------------------------------------------------
    hs_grupo_th = float(cfg.get("horas_alerta_grupo", 5.0))
    minutos_grupo_th = hs_grupo_th * 60.0
    jid_grupo = cfg.get("jid_grupo_whatsapp")

    if jid_grupo and hs_grupo_th > 0:
        # Seleccionamos chats abiertos solicitando ultimo_multiplo_notificado
        res_abiertos = supabase.table("conversaciones")\
            .select("id, created_at, tenant, agente_asignado, nombre_contacto, estado, ultimo_multiplo_notificado, ai_title")\
            .not_.in_("estado", ESTADOS_CERRADOS)\
            .execute()

        for c in (res_abiertos.data or []):
            agente = str(c.get("agente_asignado", "")).strip()
            if agente.lower() in ["monica", "mónica", "soporte", "sin asignar", "none", "nan"]:
                continue

            created_dt = parsear_fecha_utc(c.get("created_at"))
            if not created_dt:
                continue

            minutos_habiles = calcular_minutos_habiles_rapido(created_dt, now_dt)
            hs_habiles = round(minutos_habiles / 60.0, 1)

            # Múltiplo actual según las horas transcurridas
            multiplo_actual = int(minutos_habiles // minutos_grupo_th)
            ultimo_multiplo = int(c.get("ultimo_multiplo_notificado") or 0)

            print(f"🔍 [EVALUANDO GRUPO] Chat: {c['id']} | Agente: '{agente}' | Hs Hábiles: {hs_habiles} | Múltiplo actual: {multiplo_actual} | Último notificado: {ultimo_multiplo}")

            # Si alcanzó un nuevo múltiplo (1x, 2x, 3x...), dispara el aviso correspondiente
            if multiplo_actual >= 1 and multiplo_actual > ultimo_multiplo:
                tel_agente = mapa_tels.get(agente.lower())
                tag_agente = f"@{tel_agente}" if tel_agente else f"*{agente}*"
                menciones_list = [tel_agente] if tel_agente else []

                ai_title = c.get("ai_title") or "Sin título"

                # Encabezado dinámico según el número de insistencia
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
                    f"*Espera:* {hs_habiles} hs hábiles (Objetivo: {int(hs_grupo_th * multiplo_actual)} hs)"
                )

                enviar_whatsapp(jid_grupo, msg_chat, menciones=menciones_list)

                # Actualizar el registro en Supabase con el nuevo múltiplo notificado
                supabase.table("conversaciones")\
                    .update({"ultimo_multiplo_notificado": multiplo_actual})\
                    .eq("id", c['id'])\
                    .execute()

    # -------------------------------------------------------------
    # C. RESUMEN DIARIO EMAIL
    # -------------------------------------------------------------
    hora_cfg_str = cfg.get("hora_reporte_diario", "18:00:00")
    try:
        hora_objetivo = datetime.strptime(hora_cfg_str, "%H:%M:%S").time()
    except Exception:
        hora_objetivo = time(18, 0)

    if now_dt.time().hour == hora_objetivo.hour and now_dt.time().minute <= 10:
        email_dest = cfg.get("email_destino_reporte")
        if email_dest:
            hoy_str = now_dt.strftime("%Y-%m-%d")
            res_hoy = supabase.table("conversaciones")\
                .select("id, agente_asignado, primera_respuesta_min, estado")\
                .gte("created_at", f"{hoy_str}T00:00:00Z")\
                .execute()
            df_h = pd.DataFrame(res_hoy.data or [])

            if not df_h.empty:
                grp = df_h.groupby("agente_asignado").agg(
                    total=("id", "count"), 
                    prom_resp=("primera_respuesta_min", "mean")
                ).reset_index()

                filas_table = "".join([
                    f"<tr><td style='padding:8px;border:1px solid #ddd;'>{r['agente_asignado']}</td>"
                    f"<td style='padding:8px;border:1px solid #ddd;'>{r['total']}</td>"
                    f"<td style='padding:8px;border:1px solid #ddd;'>{round(r['prom_resp'], 1) if pd.notna(r['prom_resp']) else 0} min</td></tr>"
                    for _, r in grp.iterrows()
                ])

                html_body = f"""
                <h2>📊 Resumen Diario Soporte BIMS - {hoy_str}</h2>
                <table style='border-collapse:collapse;width:100%;'>
                    <tr style='background:#0284c7;color:white;'>
                        <th style='padding:8px;border:1px solid #ddd;'>Agente</th>
                        <th style='padding:8px;border:1px solid #ddd;'>Chats Total</th>
                        <th style='padding:8px;border:1px solid #ddd;'>Prom. Respuesta</th>
                    </tr>
                    {filas_table}
                </table>
                """
                
                msg_mail = MIMEMultipart()
                msg_mail['From'] = EMAIL_EMISOR
                msg_mail['To'] = email_dest
                msg_mail['Subject'] = f"📈 Reporte Diario Soporte BIMS - {hoy_str}"
                msg_mail.attach(MIMEText(html_body, 'html'))

                try:
                    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                        server.starttls()
                        server.login(EMAIL_EMISOR, EMAIL_PASSWORD)
                        server.send_message(msg_mail)
                    print(f"✅ Reporte diario enviado a {email_dest}")
                except Exception as ex_mail:
                    print(f"Error enviando correo: {ex_mail}")

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
