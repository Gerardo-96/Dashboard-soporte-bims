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
# ENDPOINT EXCLUSIVO: REPORTE DIARIO CON BARRAS HTML
# ==========================================
@app.post("/ejecutar-reporte-email-diario")
@app.get("/ejecutar-reporte-email-diario")
def trigger_reporte_email(authorization: str = Header(None)):
    """
    Cron exclusivo para enviar el resumen diario por correo con gráfico
    de barras visual apilado en HTML/CSS nativo (sin dependencias externas).
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
    if not EMAIL_EMISOR or not EMAIL_PASSWORD:
        return {"status": "error", "reason": "Credenciales SMTP incompletas en variables de entorno"}

    now_dt = datetime.now(tz_py)
    hoy_str = now_dt.strftime("%Y-%m-%d")

    # Si es fin de semana o feriado oficial, no despacha reporte
    if hoy_str in FERIADOS or now_dt.weekday() in [5, 6]:
        return {"status": "skipped", "reason": "Día no hábil / Feriado"}

    agentes_omitir = [
        "monica", "mónica", "monica (bot)", "soporte", 
        "gerardo gaona", "gerardo.gaona@itti.digital", 
        "none", "nan", "sin asignar", ""
    ]

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

    # 2. Consultar chats abiertos y cerrados hoy
    hoy_local_dt = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    hoy_inicio_utc = hoy_local_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    res_abiertos = supabase.table("conversaciones")\
        .select("id, agente_asignado, estado, created_at, updated_at, fecha_cierre")\
        .not_.in_("estado", ESTADOS_CERRADOS)\
        .execute()

    res_cerrados_hoy = supabase.table("conversaciones")\
        .select("id, agente_asignado, estado, created_at, updated_at, fecha_cierre")\
        .in_("estado", ESTADOS_CERRADOS)\
        .gte("fecha_cierre", hoy_inicio_utc)\
        .execute()

    todos = (res_abiertos.data or []) + (res_cerrados_hoy.data or [])
    unicos = {r["id"]: r for r in todos}
    data_cargas = list(unicos.values())

    df_c = pd.DataFrame(data_cargas) if data_cargas else pd.DataFrame()

    if not df_c.empty:
        df_c["fecha_ref"] = df_c["fecha_cierre"].fillna(df_c["updated_at"])
        fecha_ref_dt = pd.to_datetime(df_c["fecha_ref"], format="ISO8601", errors="coerce", utc=True)
        df_c["fecha_solo_py"] = fecha_ref_dt.dt.tz_convert("America/Asuncion").dt.date

        df_c["agente_clean"] = df_c["agente_asignado"].fillna("Sin asignar").astype(str).str.strip()
        for ag in df_c["agente_clean"].unique():
            if ag.lower() not in agentes_omitir:
                lista_agentes_maestra.add(ag)

        df_c["es_cerrado"] = df_c["estado"].fillna("").astype(str).str.strip().str.lower().isin(
            [e.lower() for e in ESTADOS_CERRADOS]
        )
        hoy_date_py = now_dt.date()
        df_c["es_cerrado_hoy"] = df_c["es_cerrado"] & (df_c["fecha_solo_py"] == hoy_date_py)
        df_c["es_abierto_actual"] = ~df_c["es_cerrado"]

    # 3. Consolidar métricas
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

        total_asig = pendientes + cerrados_hoy
        resumen.append({
            "agente": agente,
            "cerrados": cerrados_hoy,
            "pendientes": pendientes,
            "total": total_asig
        })

    df_res = pd.DataFrame(resumen)
    if not df_res.empty:
        df_res = df_res.sort_values(by="total", ascending=False)

    # 4. Construcción del Gráfico Visual en Barras HTML
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

    # 5. Armado de la tabla descriptiva
    filas_table = "".join([
        f"<tr><td style='padding:9px;border:1px solid #334155;color:#f8fafc;'>{r['agente']}</td>"
        f"<td style='padding:9px;border:1px solid #334155;text-align:center;font-weight:700;color:#f8fafc;'>{r['total']}</td>"
        f"<td style='padding:9px;border:1px solid #334155;text-align:center;color:#10b981;font-weight:700;'>{r['cerrados']}</td>"
        f"<td style='padding:9px;border:1px solid #334155;text-align:center;color:#f97316;font-weight:700;'>{r['pendientes']}</td></tr>"
        for _, r in df_res.iterrows()
    ]) if not df_res.empty else "<tr><td colspan='4' style='text-align:center;padding:12px;color:#94a3b8;'>Sin actividad registrada.</td></tr>"

    html_body = f"""
    <div style="font-family: Arial, sans-serif; background-color: #0f172a; padding: 25px; color: #f8fafc;">
        <div style="max-width: 620px; margin: 0 auto; background-color: #1e293b; border-radius: 12px; border: 1px solid #334155; padding: 24px;">
            <h2 style="color: #38bdf8; margin-top: 0; font-size: 1.25rem;">📊 Cierre Operativo Soporte BIMS - {hoy_str}</h2>
            <p style="color: #94a3b8; font-size: 0.88rem; margin-bottom: 20px;">
                Distribución y carga de trabajo acumulada durante la jornada:
            </p>

            <!-- CONTENEDOR DEL GRÁFICO VISUAL -->
            <div style="background-color: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 16px; margin-bottom: 24px;">
                <div style="display: flex; gap: 15px; margin-bottom: 15px; font-size: 0.78rem;">
                    <span style="color: #10b981; font-weight: 700;">🟢 Cerrados Hoy</span>
                    <span style="color: #f97316; font-weight: 700;">🔴 Abiertos Pendientes</span>
                </div>
                {''.join(barras_grafico_html)}
            </div>

            <!-- TABLA DE DETALLE -->
            <table style='border-collapse:collapse;width:100%;font-size:0.85rem;background-color:#0f172a;border-radius:6px;overflow:hidden;'>
                <thead>
                    <tr style='background:#0284c7;color:white;'>
                        <th style='padding:9px;border:1px solid #0284c7;text-align:left;'>Agente</th>
                        <th style='padding:9px;border:1px solid #0284c7;text-align:center;'>Total</th>
                        <th style='padding:9px;border:1px solid #0284c7;text-align:center;'>Cerrados</th>
                        <th style='padding:9px;border:1px solid #0284c7;text-align:center;'>Abiertos</th>
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

    msg_mail = MIMEMultipart('alternative')
    msg_mail['From'] = EMAIL_EMISOR
    msg_mail['To'] = email_dest
    msg_mail['Subject'] = f"📈 Cierre Operativo Soporte BIMS - {hoy_str}"
    msg_mail.attach(MIMEText(html_body, 'html'))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=18) as server:
            server.starttls()
            server.login(EMAIL_EMISOR, EMAIL_PASSWORD)
            server.send_message(msg_mail)
        print(f"✅ Reporte diario enviado a {email_dest}")
        return {"status": "success", "destino": email_dest, "fecha": hoy_str}
    except Exception as ex:
        print(f"❌ Error SMTP en reporte diario: {ex}")
        return {"status": "error_smtp", "error": str(ex)}
