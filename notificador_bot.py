import os
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
import pandas as pd
from supabase import create_client

# Configuración Base de Datos y Evolution API
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
EVOLUTION_API_URL = os.environ.get("EVOLUTION_API_URL", "")
EVOLUTION_API_KEY = os.environ.get("EVOLUTION_API_KEY", "")
INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "bims_instance")

# Configuración Email
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
EMAIL_EMISOR = os.environ.get("EMAIL_EMISOR", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
tz_py = timezone(timedelta(hours=-3))

def enviar_whatsapp(numero_destino, texto, menciones=[]):
    url = f"{EVOLUTION_API_URL}/message/sendText/{INSTANCE_NAME}"
    headers = {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}
    payload = {
        "number": numero_destino,
        "options": {"delay": 1200, "presence": "composing", "linkPreview": True, "mentions": menciones},
        "textMessage": {"text": texto}
    }
    try:
        requests.post(url, json=payload, headers=headers)
    except Exception as e:
        print(f"Error enviando WhatsApp: {e}")

def procesar_alertas_y_reportes():
    now_dt = datetime.now(tz_py)
    
    # 1. Obtener parámetros en vivo desde Supabase
    cfg_res = supabase.table("config_notificaciones").select("*").eq("id", 1).execute()
    if not cfg_res.data:
        return
    cfg = cfg_res.data[0]

    # Obtener mapeo de teléfonos de agentes
    usrs_res = supabase.table("usuarios_autorizados").select("nombre, telefono_whatsapp").execute()
    mapa_tels = {u["nombre"].strip().lower(): u["telefono_whatsapp"].strip() for u in (usrs_res.data or []) if u.get("telefono_whatsapp")}

    # -------------------------------------------------------------
    # A. ALERTA INDIVIDUAL (Mensaje sin primera respuesta >= X min)
    # -------------------------------------------------------------
    min_indiv_th = float(cfg.get("minutos_alerta_individual", 5.0))
    tel_destino_indiv = cfg.get("telefono_alerta_individual")

    if tel_destino_indiv:
        res_activos = supabase.table("conversaciones")\
            .select("id, created_at, tenant, nombre_contacto, primera_respuesta_min")\
            .not_.in_("estado", ["cerrado", "closed", "resolved", "resuelto", "snoozed"])\
            .is_("primera_respuesta_min", "null")\
            .execute()

        for c in (res_activos.data or []):
            created_dt = datetime.fromisoformat(c["created_at"].replace("Z", "+00:00")).astimezone(tz_py)
            min_trans = (now_dt - created_dt).total_seconds() / 60.0

            if min_trans >= min_indiv_th:
                url_chat = f"https://app.intercom.io/a/apps/co9kozj6/inbox/inbox/all/conversations/{c['id']}"
                msg = f"⚠️ *ALERTA BIMS: Chat Sin Respuesta (> {int(min_indiv_th)} min)*\n\n💬 *ID:* {c['id']}\n👤 *Contacto:* {c.get('nombre_contacto')}\n🏢 *Tenant:* {c.get('tenant')}\n⏳ *Espera:* {round(min_trans, 1)} min\n🔗 {url_chat}"
                enviar_whatsapp(tel_destino_indiv, msg)

    # -------------------------------------------------------------
    # B. ALERTA GRUPAL (Chats pendientes >= X horas)
    # -------------------------------------------------------------
    hs_grupo_th = float(cfg.get("horas_alerta_grupo", 5.0))
    jid_grupo = cfg.get("jid_grupo_whatsapp")

    if jid_grupo:
        res_abiertos = supabase.table("conversaciones")\
            .select("id, created_at, tenant, agente_asignado, nombre_contacto")\
            .not_.in_("estado", ["cerrado", "closed", "resolved", "resuelto", "snoozed"])\
            .execute()

        mensajes_criticos = []
        menciones_list = []

        for c in (res_abiertos.data or []):
            agente = str(c.get("agente_asignado", "")).strip()
            if agente.lower() in ["monica", "mónica", "soporte", "sin asignar", "none", "nan"]:
                continue

            created_dt = datetime.fromisoformat(c["created_at"].replace("Z", "+00:00")).astimezone(tz_py)
            hs_trans = (now_dt - created_dt).total_seconds() / 3600.0

            if hs_trans >= hs_grupo_th:
                tel_agente = mapa_tels.get(agente.lower())
                tag_agente = f"@{tel_agente}" if tel_agente else f"*{agente}*"
                if tel_agente: menciones_list.append(tel_agente)

                url_chat = f"https://app.intercom.io/a/apps/co9kozj6/inbox/inbox/all/conversations/{c['id']}"
                mensajes_criticos.append(f"• Chat [{c['id']}]({url_chat}) | {tag_agente} | Tenant: {c.get('tenant')} | ⏳ {round(hs_trans, 1)} hs")

        if mensajes_criticos:
            texto_grupo = f"🚨 *ALERTA DE SLA: Chats Pendientes (> {hs_grupo_th} hs)*\n\n" + "\n".join(mensajes_criticos)
            enviar_whatsapp(jid_grupo, texto_grupo, menciones=menciones_list)

    # -------------------------------------------------------------
    # C. RESUMEN DIARIO EMAIL (Lunes a Viernes a las X hs)
    # -------------------------------------------------------------
    hora_cfg_str = cfg.get("hora_reporte_diario", "18:00:00")
    hora_objetivo = datetime.strptime(hora_cfg_str, "%H:%M:%S").time()

    # Ejecutar solo Lunes a Viernes (0 a 4) si la hora actual coincide con el rango
    if now_dt.weekday() in range(0, 5) and now_dt.time().hour == hora_objetivo.hour and now_dt.time().minute <= 15:
        email_dest = cfg.get("email_destino_reporte")
        if email_dest:
            hoy_str = now_dt.strftime("%Y-%m-%d")
            res_hoy = supabase.table("conversaciones").select("id, agente_asignado, primera_respuesta_min, estado").gte("created_at", f"{hoy_str}T00:00:00Z").execute()
            df_h = pd.DataFrame(res_hoy.data or [])

            if not df_h.empty:
                grp = df_h.groupby("agente_asignado").agg(total=("id", "count"), prom_resp=("primera_respuesta_min", "mean")).reset_index()
                filas_table = "".join([f"<tr><td style='padding:8px;border:1px solid #ddd;'>{r['agente_asignado']}</td><td style='padding:8px;border:1px solid #ddd;'>{r['total']}</td><td style='padding:8px;border:1px solid #ddd;'>{round(r['prom_resp'], 1) if pd.notna(r['prom_resp']) else 0} min</td></tr>" for _, r in grp.iterrows()])

                html_body = f"<h2>📊 Resumen Diario Soporte BIMS - {hoy_str}</h2><table style='border-collapse:collapse;width:100%;'><tr style='background:#0284c7;color:white;'><th>Agente</th><th>Chats Total</th><th>Prom. Respuesta</th></tr>{filas_table}</table>"
                
                msg_mail = MIMEMultipart()
                msg_mail['From'], msg_mail['To'], msg_mail['Subject'] = EMAIL_EMISOR, email_dest, f"📈 Reporte Diario Soporte BIMS - {hoy_str}"
                msg_mail.attach(MIMEText(html_body, 'html'))

                with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                    server.starttls()
                    server.login(EMAIL_EMISOR, EMAIL_PASSWORD)
                    server.send_message(msg_mail)

if __name__ == "__main__":
    procesar_alertas_y_reportes()
