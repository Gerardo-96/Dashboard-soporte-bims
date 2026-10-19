import os
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
import pandas as pd
from supabase import create_client

# CONFIGURACIÓN DE ENTORNO
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
EVOLUTION_API_URL = os.environ.get("EVOLUTION_API_URL", "")
EVOLUTION_API_KEY = os.environ.get("EVOLUTION_API_KEY", "")
INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "bims_instance")

SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
EMAIL_EMISOR = os.environ.get("EMAIL_EMISOR", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
tz_py = timezone(timedelta(hours=-3))

def es_horario_laboral(now_dt):
    """Verifica si es de Lunes a Viernes entre las 08:00 y las 18:00 hs (Paraguay)."""
    dia_semana = now_dt.weekday() # 0: Lunes, ..., 4: Viernes, 5: Sábado, 6: Domingo
    hora_actual = now_dt.time()
    
    es_dia_habil = dia_semana in [0, 1, 2, 3, 4]
    es_hora_habil = datetime.strptime("08:00:00", "%H:%M:%S").time() <= hora_actual <= datetime.strptime("18:00:00", "%H:%M:%S").time()
    
    return es_dia_habil and es_hora_habil

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

    # 🛑 1. VALIDACIÓN DE HORARIO LABORAL
    if not es_horario_laboral(now_dt):
        print(f"⏰ [{now_dt.strftime('%Y-%m-%d %H:%M:%S')}] Fuera de horario laboral (L-V 08:00 a 18:00 hs). Proceso omitido.")
        return

    # 2. Cargar parámetros desde Supabase (Consumo de recursos ultra liviano)
    cfg_res = supabase.table("config_notificaciones").select("*").eq("id", 1).execute()
    if not cfg_res.data:
        return
    cfg = cfg_res.data[0]

    # Mapeo de teléfonos de agentes
    usrs_res = supabase.table("usuarios_autorizados").select("nombre, telefono_whatsapp").execute()
    mapa_tels = {u["nombre"].strip().lower(): u["telefono_whatsapp"].strip() for u in (usrs_res.data or []) if u.get("telefono_whatsapp")}

    # -------------------------------------------------------------
    # A. ALERTA INDIVIDUAL (Sin primera respuesta >= X min)
    # -------------------------------------------------------------
    min_indiv_th = float(cfg.get("minutos_alerta_individual", 5.0))
    tel_destino_indiv = cfg.get("telefono_alerta_individual")

    if tel_destino_indiv:
        # Consultar solo chats abiertos sin primera respuesta
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
                msg = f"⚠️ *ALERTA BIMS: Chat Sin Respuesta (> {int(min_indiv_th)} min)*\n\n💬 *ID:* {c['id']}\n👤 *Contacto:* {c.get('nombre_contacto', 'Sin nombre')}\n🏢 *Tenant:* {c.get('tenant', 'Sin datos')}\n⏳ *Espera:* {round(min_trans, 1)} min\n🔗 {url_chat}"
                enviar_whatsapp(tel_destino_indiv, msg)

    # -------------------------------------------------------------
    # B. ALERTA GRUPAL (Chats pendientes >= X horas HÁBILES)
    # -------------------------------------------------------------
    hs_grupo_th = float(cfg.get("horas_alerta_grupo", 5.0))
    minutos_grupo_th = hs_grupo_th * 60.0  # Convertimos horas límite a minutos hábiles
    jid_grupo = cfg.get("jid_grupo_whatsapp")

    if jid_grupo:
        res_abiertos = supabase.table("conversaciones")\
            .select("id, created_at, tenant, agente_asignado, nombre_contacto, horario_evaluado")\
            .not_.in_("estado", ["cerrado", "closed", "resolved", "resuelto", "snoozed"])\
            .execute()

        mensajes_criticos = []
        menciones_list = []

        for c in (res_abiertos.data or []):
            agente = str(c.get("agente_asignado", "")).strip()
            if agente.lower() in ["monica", "mónica", "soporte", "sin asignar", "none", "nan"]:
                continue

            created_dt = datetime.fromisoformat(c["created_at"].replace("Z", "+00:00")).astimezone(tz_py)
            
            # CÁLCULO DE MINUTOS HÁBILES OPERATIVOS (Omite noches, fines de semana y feriados)
            minutos_habiles = calcular_minutos_habiles_rapido(created_dt, now_dt)
            
            if pd.notna(minutos_habiles):
                horas_habiles = round(minutos_habiles / 60.0, 1)

                if minutos_habiles >= minutos_grupo_th:
                    tel_agente = mapa_tels.get(agente.lower())
                    tag_agente = f"@{tel_agente}" if tel_agente else f"*{agente}*"
                    if tel_agente: 
                        menciones_list.append(tel_agente)

                    url_chat = f"https://app.intercom.io/a/apps/co9kozj6/inbox/inbox/all/conversations/{c['id']}"
                    mensajes_criticos.append(
                        f"• Chat [{c['id']}]({url_chat}) | {tag_agente} | Tenant: {c.get('tenant', 'Sin datos')} | ⏳ {horas_habiles} hs hábiles"
                    )

        if mensajes_criticos:
            texto_grupo = f"🚨 *ALERTA DE SLA: Chats Pendientes (> {hs_grupo_th} hs hábiles)*\n\n" + "\n".join(mensajes_criticos)
            enviar_whatsapp(jid_grupo, texto_grupo, menciones=menciones_list)

    # -------------------------------------------------------------
    # C. RESUMEN DIARIO EMAIL (Exactamente a la hora configurada)
    # -------------------------------------------------------------
    hora_cfg_str = cfg.get("hora_reporte_diario", "18:00:00")
    hora_objetivo = datetime.strptime(hora_cfg_str, "%H:%M:%S").time()

    # Si coincide con la hora objetivo configurada (ventana de 10 min)
    if now_dt.time().hour == hora_objetivo.hour and now_dt.time().minute <= 10:
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

                try:
                    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                        server.starttls()
                        server.login(EMAIL_EMISOR, EMAIL_PASSWORD)
                        server.send_message(msg_mail)
                    print(f"✅ Reporte diario enviado a {email_dest}")
                except Exception as ex_mail:
                    print(f"Error enviando correo: {ex_mail}")

if __name__ == "__main__":
    procesar_alertas_y_reportes()
