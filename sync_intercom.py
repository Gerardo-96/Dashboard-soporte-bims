import os
import argparse
import requests
from datetime import datetime, timedelta, timezone, date
from supabase import create_client, Client

# ==========================================
# CONFIGURACIÓN DE SUPABASE & INTERCOM
# ==========================================
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

ACCESS_TOKEN = os.environ.get("INTERCOM_ACCESS_TOKEN", "") or os.environ.get("INTERCOM_TOKEN", "")

tz_local = timezone(timedelta(hours=-3))

headers = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json"
}

contact_cache = {}

def get_contact(contact_id):
    if contact_id in contact_cache:
        return contact_cache[contact_id]
    url = f"https://api.intercom.io/contacts/{contact_id}"
    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            data = r.json()
            contact_cache[contact_id] = data
            return data
    except:
        pass
    contact_cache[contact_id] = None
    return None

def get_conversation_detail(conv_id):
    """Obtiene el detalle individual completo de una conversación para asegurar rating y estadísticas"""
    url = f"https://api.intercom.io/conversations/{conv_id}"
    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return None

def get_attr(contact, key):
    if not contact:
        return "Sin datos"
    return contact.get("custom_attributes", {}).get(key, "Sin datos")

def obtener_agentes():
    url = "https://api.intercom.io/admins"
    try:
        resp = requests.get(url, headers=headers)
        data = resp.json()
        return {str(a["id"]).strip(): a["name"] for a in data.get("admins", [])}
    except:
        return {}

def obtener_canal(conv):
    source_type = conv.get("source", {}).get("type", "chat")
    if source_type == "whatsapp":
        return "WhatsApp"
    elif source_type == "email":
        return "Correo electrónico"
    elif source_type == "chat":
        return "Chat (web)"
    else:
        return f"Chat ({source_type})"

def extraer_motivo_normalizado(modulo, tipo_contacto, etiquetas, cx_explanation):
    if modulo and modulo.strip():
        m_clean = modulo.replace("mod-", "").replace("_", " ").strip().title()
        if tipo_contacto and tipo_contacto.strip():
            t_clean = tipo_contacto.replace("tipo-", "").replace("_", " ").strip().title()
            return f"{m_clean} - {t_clean}"
        return f"Módulo {m_clean}"
    if tipo_contacto and tipo_contacto.strip():
        return tipo_contacto.replace("tipo-", "").replace("_", " ").strip().title()
    if cx_explanation and cx_explanation.strip():
        return cx_explanation.strip()
    if etiquetas:
        tags_list = [t.strip() for t in etiquetas.split(",") if t.strip() and not t.strip().startswith(("cli-", "Niv-"))]
        if tags_list:
            return tags_list[0].replace("_", " ").title()
    return "Consulta General"

def sincronizar_intercom(dias=None, fecha_desde=None, fecha_hasta=None, progress_callback=None):
    """
    Sincroniza conversaciones de Intercom hacia Supabase.
    Soporta el cálculo por 'dias' o por rango explícito con 'fecha_desde' y 'fecha_hasta'.
    """
    ahora_local = datetime.now(tz=tz_local)
    
    # Evaluar si se recibió rango explícito de fechas
    if fecha_desde and fecha_hasta:
        if isinstance(fecha_desde, str):
            fecha_desde = datetime.strptime(fecha_desde, "%Y-%m-%d").date()
        elif isinstance(fecha_desde, datetime):
            fecha_desde = fecha_desde.date()

        if isinstance(fecha_hasta, str):
            fecha_hasta = datetime.strptime(fecha_hasta, "%Y-%m-%d").date()
        elif isinstance(fecha_hasta, datetime):
            fecha_hasta = fecha_hasta.date()

        dt_inicio = datetime.combine(fecha_desde, datetime.min.time()).replace(tzinfo=tz_local)
        dt_fin = datetime.combine(fecha_hasta, datetime.max.time()).replace(tzinfo=tz_local)
    else:
        # Modo por días
        dias_val = dias if dias is not None else 3
        dt_inicio = (ahora_local - timedelta(days=dias_val)).replace(hour=0, minute=0, second=0, microsecond=0)
        dt_fin = ahora_local

    start_ts = int(dt_inicio.timestamp())
    end_ts = int(dt_fin.timestamp())

    agentes = obtener_agentes()
    conversations_url = "https://api.intercom.io/conversations/search"
    starting_after = None
    total_procesadas = 0

    print(f"🔄 Sincronizando conversaciones de Intercom desde {dt_inicio.strftime('%Y-%m-%d %H:%M:%S')} hasta {dt_fin.strftime('%Y-%m-%d %H:%M:%S')}...")

    while True:
        payload = {
            "query": {
                "operator": "AND",
                "value": [
                    {"field": "updated_at", "operator": ">=", "value": start_ts},
                    {"field": "updated_at", "operator": "<=", "value": end_ts}
                ]
            },
            "pagination": {"per_page": 100}
        }
        if starting_after:
            payload["pagination"]["starting_after"] = starting_after

        r = requests.post(conversations_url, headers=headers, json=payload)
        if r.status_code != 200:
            print(f"❌ Error al consultar Intercom API ({r.status_code}): {r.text}")
            raise Exception(f"Intercom API Error {r.status_code}: {r.text}")

        data = r.json()
        convs = data.get("conversations") or data.get("data", [])
        if not convs:
            break

        lote_registros = []

        for conv_summary in convs:
            canal = obtener_canal(conv_summary)
            if canal == "Correo electrónico":
                continue

            conv_id = str(conv_summary.get("id"))
            conv = get_conversation_detail(conv_id) or conv_summary

            created_at = datetime.fromtimestamp(conv["created_at"], tz=tz_local)
            
            admin_id = conv.get("admin_assignee_id")
            agente = agentes.get(str(admin_id).strip(), "Sin asignar") if admin_id else "Sin asignar"
            por_agente = "excluido" if agente.lower() == "sin asignar" else "no excluido"

            # Extraer calificación
            rating_info = conv.get("conversation_rating") or {}
            rating = rating_info.get("rating")
            calificacion = str(rating) if rating is not None else ""
            feedback = rating_info.get("remark", "") or ""

            custom_attrs = conv.get("custom_attributes") or {}
            cx_explanation = custom_attrs.get("CX Score explanation", "")

            teammate_info = rating_info.get("teammate") or {}
            agente_eval_id = teammate_info.get("id")
            agente_evaluado = agentes.get(str(agente_eval_id).strip(), "") if agente_eval_id else ""

            # Extraer estado y PRIMERA fecha de cierre
            state_intercom = str(conv.get("state", "")).lower()
            statistics = conv.get("statistics", {}) or {}
            
            # Usamos first_close_at en lugar de last_close_at
            first_close_at = statistics.get("first_close_at") or statistics.get("last_close_at")
            
            fecha_cierre = datetime.fromtimestamp(first_close_at, tz=tz_local) if first_close_at else None
            estado = "Cerrado" if (state_intercom in ["closed", "resolved"] or fecha_cierre is not None) else "Abierto"

            time_to_reply = statistics.get("time_to_admin_reply")
            primera_respuesta_min = round(time_to_reply / 60, 2) if time_to_reply is not None else None

            # Cálculo de tiempos de gestión con respecto al PRIMER cierre
            tiempo_res_hrs, tiempo_res_min = None, None
            if fecha_cierre:
                secs = (fecha_cierre - created_at).total_seconds()
                tiempo_res_hrs = round(secs / 3600, 2)
                tiempo_res_min = round(secs / 60, 2)

            tags = [tag.get("name") for tag in conv.get("tags", {}).get("tags", [])]
            modulo = next((t for t in tags if t.startswith("mod-")), "")
            cliente = next((t for t in tags if t.startswith("cli-")), "")
            tipo_contacto = next((t for t in tags if t.startswith("tipo-")), "")
            nivel = next((t for t in tags if t.startswith("Niv-")), "")
            etiquetas_str = ", ".join(tags)
            motivo = extraer_motivo_normalizado(modulo, tipo_contacto, etiquetas_str, cx_explanation)

            contacts = conv.get("contacts", {}).get("contacts", [])
            tenant, company, nombre_contacto = "Sin datos", "Sin datos", "Sin nombre"
            if contacts:
                c_data = get_contact(contacts[0].get("id"))
                if c_data:
                    tenant = get_attr(c_data, "tenant")
                    company = get_attr(c_data, "Company")
                    nombre_contacto = c_data.get("name") or c_data.get("email") or "Sin nombre"

            # Estructurar objeto compatible con la tabla PostgreSQL en Supabase
            registro = {
                "id": conv_id,
                "created_at": created_at.isoformat(),
                "canal": canal,
                "tenant": tenant,
                "company": company,
                "nombre_contacto": nombre_contacto,
                "agente_asignado": agente,
                "por_agente": por_agente,
                "horario": "normal",
                "primera_respuesta_min": primera_respuesta_min,
                "tiempo_resolucion_horas": tiempo_res_hrs,
                "tiempo_resolucion_minutos": tiempo_res_min,
                "sla": "evaluar",
                "sla_tiempo_gestion": "evaluar",
                "calificacion": calificacion,
                "rating": rating,
                "feedback": feedback,
                "agente_evaluado": agente_evaluado,
                "cx_score_explanation": cx_explanation,
                "fecha_cierre": fecha_cierre.isoformat() if fecha_cierre else None,
                "etiquetas": etiquetas_str,
                "modulo": modulo,
                "cliente": cliente,
                "tipo_contacto": tipo_contacto,
                "nivel": nivel,
                "motivo_normalizado": motivo,
                "estado": estado,
                "updated_at": datetime.now(tz=tz_local).isoformat()
            }
            lote_registros.append(registro)

        # Inserción/Actualización masiva en Supabase
        if lote_registros:
            supabase.table("conversaciones").upsert(lote_registros).execute()
            total_procesadas += len(lote_registros)

        if progress_callback:
            progress_callback(total_procesadas, total_procesadas)

        next_page = data.get("pages", {}).get("next", {}).get("starting_after")
        if not next_page:
            break
        starting_after = next_page

    print(f"✅ Sincronización completada en Supabase. Total procesadas: {total_procesadas}")
    return total_procesadas

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sincronizador incremental Intercom -> Supabase")
    parser.add_argument("--dias", type=int, default=3, help="Días a sincronizar hacia atrás")
    parser.add_argument("--desde", type=str, default=None, help="Fecha inicio en formato YYYY-MM-DD")
    parser.add_argument("--hasta", type=str, default=None, help="Fecha fin en formato YYYY-MM-DD")
    args = parser.parse_args()
    
    sincronizar_intercom(dias=args.dias, fecha_desde=args.desde, fecha_hasta=args.hasta)
