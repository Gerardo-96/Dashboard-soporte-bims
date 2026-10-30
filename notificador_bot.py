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
import matplotlib
matplotlib.use('Agg')  # Modo servidor sin interfaz gráfica
import matplotlib.pyplot as plt
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
# ENDPOINT EXCLUSIVO: REPORTE DIARIO CON GRÁFICO
# ==========================================
@app.post("/ejecutar-reporte-email-diario")
@app.get("/ejecutar-reporte-email-diario")
def trigger_reporte_email(authorization: str = Header(None)):
    """
    Cron exclusivo para enviar el resumen diario por correo con el gráfico
    apilado de Distribución de Carga (Cerrados vs Abiertos).
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

    # 1. Traer lista maestra de agentes autorizados
    agentes_omitir = [
        "monica", "mónica", "monica (bot)", "soporte", 
        "gerardo gaona", "gerardo.gaona@itti.digital", 
        "none", "nan", "sin asignar", ""
    ]
    
    lista_agentes_maestra = set()
    try:
        res_u = supabase.table("usuarios_autorizados").select("nombre").eq("activo", True).execute()
        for u in (res_u.data or []):
            nom = u.get("nombre", "").strip()
            if nom and nom.lower() not in agentes_omitir:
                lista_agentes_maestra.add(nom)
    except Exception:
        pass

    # 2. Consultar conversaciones del día (UTC) y abiertas
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

    # 3. Consolidar métricas de carga por agente
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

    # 4. Generación del gráfico de barras idéntico al Dashboard
    img_buffer = io.BytesIO()
    hay_grafico = False

    if not df_res.empty and df_res["total"].sum() > 0:
        fig, ax = plt.subplots(figsize=(8, 4.2), facecolor='#1e293b')
        ax.set_facecolor('#1e293b')

        nombres = df_res["agente"].tolist()
        val_cerrados = df_res["cerrados"].tolist()
        val_pendientes = df_res["pendientes"].tolist()
        val_totales = df_res["total"].tolist()

        ancho_barra = 0.55
        indices = range(len(nombres))

        # Barra base: Cerrados Hoy (Verde Esmeralda)
        ax.bar(indices, val_cerrados, label='Cerrados Hoy', color='#10b981', width=ancho_barra)
        # Barra superior apilada: Abiertos Pendientes (Naranja/Coral)
        ax.bar(indices, val_pendientes, bottom=val_cerrados, label='Abiertos Pendientes', color='#f97316', width=ancho_barra)

        # Números internos y total en la cabecera
        for i, (c, p, tot) in enumerate(zip(val_cerrados, val_pendientes, val_totales)):
            if tot > 0:
                ax.text(i, tot + (max(val_totales) * 0.03), str(tot), ha='center', va='bottom', color='#f8fafc', fontweight='bold', fontsize=11)
            if c > 0:
                ax.text(i, c / 2, str(c), ha='center', va='center', color='#ffffff', fontweight='bold', fontsize=10)
            if p > 0:
                ax.text(i, c + (p / 2), str(p), ha='center', va='center', color='#ffffff', fontweight='bold', fontsize=10)

        ax.set_xticks(indices)
        ax.set_xticklabels(nombres, color='#cbd5e1', fontsize=10, rotation=15, ha='right')
        ax.yaxis.set_visible(False)  # Quita números y líneas del eje Y
        for spine in ax.spines.values():
            spine.set_visible(False)

        ax.legend(facecolor='#1e293b', edgecolor='#334155', labelcolor='#cbd5e1', loc='upper right', framealpha=0.9)
        plt.title('Distribución y Carga del Equipo (Cierre de Jornada)', color='#f8fafc', fontsize=12, fontweight='bold', pad=18)
        
        plt.tight_layout()
        plt.savefig(img_buffer, format='png', dpi=130, facecolor=fig.get_facecolor(), edgecolor='none')
        plt.close(fig)
        img_buffer.seek(0)
        hay_grafico = True

    # 5. Armado de la tabla HTML
    filas_table = "".join([
        f"<tr><td style='padding:10px;border:1px solid #e2e8f0;font-weight:600;'>{r['agente']}</td>"
        f"<td style='padding:10px;border:1px solid #e2e8f0;text-align:center;'>{r['total']}</td>"
        f"<td style='padding:10px;border:1px solid #e2e8f0;text-align:center;color:#10b981;font-weight:600;'>{r['cerrados']}</td>"
        f"<td style='padding:10px;border:1px solid #e2e8f0;text-align:center;color:#f97316;font-weight:600;'>{r['pendientes']}</td></tr>"
        for _, r in df_res.iterrows()
    ]) if not df_res.empty else "<tr><td colspan='4' style='text-align:center;padding:12px;'>Sin actividad registrada.</td></tr>"

    seccion_grafico_html = """
    <div style="margin-top:20px;text-align:center;">
        <img src="cid:grafico_distribucion" style="width:100%;max-width:600px;border-radius:10px;border:1px solid #334155;" alt="Gráfico de Carga">
    </div>
    """ if hay_grafico else ""

    html_body = f"""
    <div style="font-family: Arial, sans-serif; color: #1e293b; max-width: 640px; margin: 0 auto; border: 1px solid #e2e8f0; border-radius: 10px; padding: 24px; background-color: #ffffff;">
        <h2 style="color: #0284c7; margin-top: 0;">📊 Cierre Operativo Soporte BIMS</h2>
        <p style="font-size: 0.95rem; color: #475569;">
            Resumen consolidado de carga correspondiente a la jornada del <b>{hoy_str}</b>:
        </p>

        {seccion_grafico_html}

        <h3 style="color:#1e293b;margin-top:25px;font-size:1rem;">Detalle por Agente</h3>
        <table style='border-collapse:collapse;width:100%;font-size:0.9rem;'>
            <thead>
                <tr style='background:#0284c7;color:white;'>
                    <th style='padding:10px;border:1px solid #0284c7;text-align:left;'>Agente</th>
                    <th style='padding:10px;border:1px solid #0284c7;text-align:center;'>Total Asignados</th>
                    <th style='padding:10px;border:1px solid #0284c7;text-align:center;'>Cerrados Hoy</th>
                    <th style='padding:10px;border:1px solid #0284c7;text-align:center;'>Pendientes Actuales</th>
                </tr>
            </thead>
            <tbody>
                {filas_table}
            </tbody>
        </table>

        <p style="font-size:0.78rem;color:#94a3b8;margin-top:24px;border-top:1px solid #e2e8f0;padding-top:12px;">
            Notificación automática de fin de jornada generada por el Bot Notificador BIMS.
        </p>
    </div>
    """

    msg_mail = MIMEMultipart('related')
    msg_mail['From'] = EMAIL_EMISOR
    msg_mail['To'] = email_dest
    msg_mail['Subject'] = f"📈 Cierre Operativo Soporte BIMS - {hoy_str}"

    msg_alt = MIMEMultipart('alternative')
    msg_mail.attach(msg_alt)
    msg_alt.attach(MIMEText(html_body, 'html'))

    # Adjuntar imagen como elemento inline CID
    if hay_grafico:
        img_mime = MIMEImage(img_buffer.read())
        img_mime.add_header('Content-ID', '<grafico_distribucion>')
        img_mime.add_header('Content-Disposition', 'inline', filename="grafico_distribucion.png")
        msg_mail.attach(img_mime)

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(EMAIL_EMISOR, EMAIL_PASSWORD)
            server.send_message(msg_mail)
        print(f"✅ Reporte con gráfico enviado exitosamente a {email_dest}")
        return {"status": "success", "destino": email_dest, "fecha": hoy_str}
    except Exception as ex:
        print(f"❌ Error SMTP en reporte diario: {ex}")
        return {"status": "error_smtp", "error": str(ex)}
