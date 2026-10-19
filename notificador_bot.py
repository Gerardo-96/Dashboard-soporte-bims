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

# ==========================================
# FUNCIONES AUXILIARES
# ==========================================

def es_horario_laboral(now_dt):
    """Verifica si es de Lunes a Viernes entre las 08:00 y las 18:00 hs (Paraguay)."""
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
    url = f"{EVOLUTION_API_URL}/message/sendText/{INSTANCE_NAME}"
    headers = {
        "apikey": EVOLUTION_API_KEY, 
        "Content-Type": "application/json"
    }
    payload = {
        "number": numero_destino,
        "text": texto
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

    # A. ALERTA INDIVIDUAL
    min_indiv_th = float(cfg.get("minutos_alerta_individual", 5.0))
    tel_destino_indiv = cfg.get("telefono_alerta_individual")

    if tel_destino_indiv:
        res_activos = supabase.table("conversaciones")\
            .select("id, created_at, tenant, nombre_contacto, primera_respuesta_min")\
            .not_.in_("estado", ["cerrado", "closed", "resolved", "resuelto", "snoozed"])\
            .is_("primera_respuesta_min", "null")\
            .execute()

        for c in (res_activos.data or []):
            created_dt = parsear_fecha_utc(c.get("created_at"))
            if not created_dt:
                continue

            min_trans = (now_dt - created_dt).total_seconds() / 60.0

            if min_trans >= min_indiv_th:
                url_chat = f"https://app.intercom.io/a/apps/co9kozj6/inbox/inbox/all/conversations/{c['id']}"
                msg = (
                    f"⚠️ *ALERTA BIMS: Chat Sin Respuesta (> {int(min_indiv_th)} min)*\n\n"
                    f"💬 *ID:* {c['id']}\n"
                    f"👤 *Contacto:* {c.get('nombre_contacto', 'Sin nombre')}\n"
                    f"🏢 *Tenant:* {c.get('tenant', 'Sin datos')}\n"
                    f"⏳ *Espera:* {round(min_trans, 1)} min\n"
                    f"🔗 {url_chat}"
                )
                enviar_whatsapp(tel_destino_indiv, msg)

    # B. ALERTA GRUPAL
    hs_grupo_th = float(cfg.get("horas_alerta_grupo", 5.0))
    minutos_grupo_th = hs_grupo_th * 60.0
    jid_grupo = cfg.get("jid_grupo_whatsapp")

    if jid_grupo:
        res_abiertos = supabase.table("conversaciones")\
            .select("id, created_at, tenant, agente_asignado, nombre_contacto")\
            .not_.in_("estado", ["cerrado", "closed", "resolved", "resuelto", "snoozed"])\
            .execute()

        mensajes_criticos, menciones_list = [], []

        for c in (res_abiertos.data or []):
            agente = str(c.get("agente_asignado", "")).strip()
            if agente.lower() in ["monica", "mónica", "soporte", "sin asignar", "none", "nan"]:
                continue

            created_dt = parsear_fecha_utc(c.get("created_at"))
            if not created_dt:
                continue

            minutos_habiles = calcular_minutos_habiles_rapido(created_dt, now_dt)

            if minutos_habiles >= minutos_grupo_th:
                tel_agente = mapa_tels.get(agente.lower())
                tag_agente = f"@{tel_agente}" if tel_agente else f"*{agente}*"
                if tel_agente:
                    menciones_list.append(tel_agente)

                url_chat = f"https://app.intercom.io/a/apps/co9kozj6/inbox/inbox/all/conversations/{c['id']}"
                mensajes_criticos.append(
                    f"• Chat [{c['id']}]({url_chat}) | {tag_agente} | Tenant: {c.get('tenant', 'Sin datos')} | ⏳ {round(minutos_habiles / 60.0, 1)} hs hábiles"
                )

        if mensajes_criticos:
            texto_grupo = f"🚨 *ALERTA DE SLA: Chats Pendientes (> {hs_grupo_th} hs hábiles)*\n\n" + "\n".join(mensajes_criticos)
            enviar_whatsapp(jid_grupo, texto_grupo, menciones=menciones_list)

    # C. RESUMEN DIARIO EMAIL
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
    """Ruta raíz para verificar que el servidor de notificaciones está vivo."""
    return {"status": "ok", "service": "Notificador BIMS activo."}

@app.get("/ejecutar-notificaciones")
def trigger_notificaciones(authorization: str = Header(None)):
    """Endpoint principal llamado por cron-job.org."""
    if authorization != f"Bearer {CRON_SECRET_TOKEN}":
        raise HTTPException(status_code=401, detail="No autorizado")
    
    try:
        resultado = procesar_alertas_y_reportes()
        print(f"Resultado ejecucion: {resultado}")
    except Exception as e:
        print(f"Error procesando notificaciones: {e}")

    # Retorno ultra liviano obligatorio para evitar errores de buffer en cron-job.org
    return {"status": "ok"}
