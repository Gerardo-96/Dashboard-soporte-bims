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

# ==========================================
# PISO ESTÁTICO DE SEGURIDAD (CORTE TRANSICIÓN)
# 11 de Noviembre de 2026 a las 15:00:00 hs (PY) -> 18:00:00Z en UTC
# ==========================================
DT_CORTE_LOCAL = datetime(2026, 11, 11, 15, 0, 0, tzinfo=tz_local)
TS_CORTE_LOCAL = int(DT_CORTE_LOCAL.timestamp())

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
    except Exception:
        pass
    contact_cache[contact_id] = None
    return None

def get_conversation_detail(conv_id):
    """Obtiene el detalle individual completo de una conversación para asegurar rating y estadísticas."""
    url = f"https://api.intercom.io/conversations/{conv_id}"
    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

def get_attr(contact, key):
    if not contact:
        return "Sin datos"
    return contact.get("custom_attributes", {}).get(key, "Sin datos")

def obtener_agentes():
    """
    Retorna el diccionario id -> nombre para resolver asignaciones históricas de Intercom.
    Ya NO sobreescribe estado_agentes para permitir el control manual interno desde Streamlit.
    """
    url = "https://api.intercom.io/admins"
    agentes_dict = {}
    try:
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            admins = data.get("admins", [])
            for a in admins:
                agentes_dict[str(a.get("id")).strip()] = a.get("name", "Desconocido")
    except Exception as e:
        print(f"⚠️ Error al consultar admins de Intercom: {e}")
    return agentes_dict

"""
def obtener_canal(conv):
    source_type = conv.get("source", {}).get("type", "chat")
    if source_type == "whatsapp":
        return "WhatsApp"
    elif source_type == "email":
        return "Correo electrónico"
    elif source_type == "conversation":
        return "Chat (conversation)"
    else:
        return "Chat (conversation)"
"""

def obtener_canal(conv):
    source = conv.get("source", {}) or {}
    source_type = str(source.get("type", "")).lower()
    delivered_as = str(source.get("delivered_as", "")).lower()

    if "whatsapp" in source_type or "whatsapp" in delivered_as:
        return "WhatsApp"
    elif any(k in source_type or k in delivered_as for k in ["email", "mail", "correo"]):
        return "Correo electrónico"
    else:
        return "Chat (conversation)"

def extraer_agente_de_etiquetas(tags: list[str]) -> str | None:
    agente_l1 = None
    agente_l2_especifico = None
    tiene_nivel_l2 = False
    es_agendamiento = False

    for t in tags:
        t_str = str(t).strip()
        t_lower = t_str.lower()

        if t_lower.startswith("l2:"):
            partes = t_str.split(":", 1)
            if len(partes) > 1 and partes[1].strip():
                agente_l2_especifico = partes[1].strip()
        elif t_lower == "niv-l2":
            tiene_nivel_l2 = True
        elif t_lower.startswith("agente:"):
            partes = t_str.split(":", 1)
            if len(partes) > 1 and partes[1].strip():
                agente_l1 = partes[1].strip()
        elif t_lower == "agendamiento" or "agendamiento" in t_lower:
            es_agendamiento = True

    if agente_l2_especifico:
        return agente_l2_especifico
    if tiene_nivel_l2:
        return "Gerardo Gaona"
    if agente_l1:
        return agente_l1
    if es_agendamiento:
        return "Agendamiento"

    return None


def sincronizar_intercom(dias=None, fecha_desde=None, fecha_hasta=None, progress_callback=None):
    """
    Sincroniza conversaciones de Intercom hacia Supabase.
    Aplica corte estricto infranqueable: ignora cualquier registro modificado antes
    de hoy 11/11/2026 a las 15:00 hs (PY) para blindar la cuota de Supabase.
    """
    ahora_local = datetime.now(tz=tz_local)
    es_rango_manual = bool(fecha_desde and fecha_hasta)

    if es_rango_manual:
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

        # PISO INFRANQUEABLE: Si el rango incluye hoy (o arranca hoy), jamás permitir inicio antes de las 15:00 hs PY
        if fecha_desde == DT_CORTE_LOCAL.date():
            dt_inicio = max(dt_inicio, DT_CORTE_LOCAL)
    else:
        # Modo continuo: jamás consultar antes de las 15:00 hs del 11/11/2026
        dt_inicio = max(DT_CORTE_LOCAL, ahora_local - timedelta(days=(dias or 1)))
        dt_fin = ahora_local

    start_ts = int(dt_inicio.timestamp())
    end_ts = int(dt_fin.timestamp())

    agentes_dict = obtener_agentes()
    conversations_url = "https://api.intercom.io/conversations/search"
    starting_after = None
    total_procesadas = 0

    print(f"🔄 Sincronizando conversaciones de Intercom desde {dt_inicio.strftime('%Y-%m-%d %H:%M:%S')} hasta {dt_fin.strftime('%Y-%m-%d %H:%M:%S')}...")

    while True:
        query_conditions = [
            {"field": "updated_at", "operator": ">=", "value": start_ts},
            {"field": "updated_at", "operator": "<=", "value": end_ts}
        ]

        payload = {
            "query": {
                "operator": "AND",
                "value": query_conditions
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

            # Timestamp de actualización del sumario
            updated_at_orig = conv_summary.get("updated_at")

            # FILTRO INFRANQUEABLE: Si el chat se actualizó antes de las 15:00 hs de hoy, descartar de inmediato
            if updated_at_orig and updated_at_orig < TS_CORTE_LOCAL:
                continue

            conv_id = str(conv_summary.get("id"))
            conv = get_conversation_detail(conv_id) or conv_summary

            # Re-validación con el objeto completo
            updated_at_detail = conv.get("updated_at") or updated_at_orig
            if updated_at_detail and updated_at_detail < TS_CORTE_LOCAL:
                continue

            created_at_orig = conv.get("created_at")
            statistics = conv.get("statistics", {}) or {}

            # =========================================================================
            # 1. DEDUCCIÓN DE INICIO REAL EN BANDEJA HUMANA
            # =========================================================================
            ts_inicio_ref = None
            first_close_at = statistics.get("first_close_at") or statistics.get("last_close_at")
            parts = conv.get("conversation_parts", {}).get("conversation_parts", [])

            for p in parts:
                if p.get("part_type") == "bulk_reassignment":
                    continue
                if p.get("part_type") == "assignment":
                    ts_inicio_ref = p.get("created_at")
                    break

            if not ts_inicio_ref:
                first_assign_stat = statistics.get("first_assignment_at") or statistics.get("first_assignment_to_team_at")
                if first_assign_stat and (not first_close_at or first_assign_stat <= first_close_at):
                    ts_inicio_ref = first_assign_stat

            if not ts_inicio_ref:
                ts_inicio_ref = created_at_orig

            inicio_real = datetime.fromtimestamp(ts_inicio_ref, tz=tz_local)

            hace_7_meses = ahora_local - timedelta(days=210)
            if inicio_real < hace_7_meses:
                continue

            # =========================================================================
            # 2. PRIMERA RESPUESTA HUMANA
            # =========================================================================
            first_admin_reply = statistics.get("first_admin_reply_at")

            if not first_admin_reply and parts:
                for p in parts:
                    if p.get("part_type") == "comment" and p.get("author", {}).get("type") in ["admin", "user_admin"]:
                        if not p.get("author", {}).get("from_ai_agent", False):
                            first_admin_reply = p.get("created_at")
                            break

            if first_admin_reply and first_admin_reply >= ts_inicio_ref:
                secs_espera = first_admin_reply - ts_inicio_ref
                primera_respuesta_min = round(secs_espera / 60.0, 2)
            else:
                primera_respuesta_min = None

            # =========================================================================
            # 3. TIEMPO DE GESTIÓN Y CIERRE
            # =========================================================================
            state_intercom = str(conv.get("state", "")).lower()
            fecha_cierre = datetime.fromtimestamp(first_close_at, tz=tz_local) if first_close_at else None
            estado = "Cerrado" if (state_intercom in ["closed", "resolved"] or fecha_cierre is not None) else "Abierto"

            tiempo_res_hrs, tiempo_res_min = None, None
            if fecha_cierre and fecha_cierre >= inicio_real:
                secs = (fecha_cierre - inicio_real).total_seconds()
                tiempo_res_hrs = round(secs / 3600, 2)
                tiempo_res_min = round(secs / 60, 2)

            # =========================================================================
            # 4. RESOLUCIÓN DE AGENTE: ETIQUETAS (Agente: / L2: / agendamiento)
            # =========================================================================
            tags = [tag.get("name") for tag in conv.get("tags", {}).get("tags", [])]

            tags_lower = [str(t).strip().lower() for t in tags]
            es_spam = "spam" in tags_lower or "spam-erp" in tags_lower
            if es_spam:
                continue

            es_correo = (canal == "Correo electrónico") or ("correo" in tags_lower)
            if es_correo:
                canal = "Correo electrónico"

            # Contar cantidad total de respuestas humanas en todo el hilo
            cantidad_respuestas_agente = sum(
                1 for p in parts
                if p.get("part_type") == "comment"
                and p.get("author", {}).get("type") in ["admin", "user_admin"]
                and not p.get("author", {}).get("from_ai_agent", False)
                and not p.get("author", {}).get("is_ai_answer", False)
                and not any(b in str(p.get("author", {}).get("name", "")).lower() for b in ["monica", "mónica", "fin", "bot"])
            )
            
            agente_por_tag = extraer_agente_de_etiquetas(tags)

            dt_creacion = datetime.fromtimestamp(created_at_orig, tz=tz_local)

            # A partir de hoy 11/11/2026 15:00 hs (PY), se prioriza la etiqueta
            # Si tiene etiqueta L2 o el chat fue creado desde el corte, se prioriza la etiqueta
            es_tag_l2 = any(str(t).strip().lower().startswith("l2:") or str(t).strip().lower() == "niv-l2" for t in tags)
            if (dt_creacion >= DT_CORTE_LOCAL or es_tag_l2) and agente_por_tag:
                agente = agente_por_tag
                
            else:
                # Fallback al esquema tradicional para chats anteriores o sin etiqueta
                agente = "Sin asignar"
                admin_id_directo = conv.get("admin_assignee_id") or conv_summary.get("admin_assignee_id")
                if admin_id_directo and str(admin_id_directo).strip() not in ["", "None", "0", "null"]:
                    agente = agentes_dict.get(str(admin_id_directo).strip(), f"Agente ({admin_id_directo})")

                if agente == "Sin asignar":
                    assigned_to = conv.get("assigned_to") or conv_summary.get("assigned_to")
                    if isinstance(assigned_to, dict) and assigned_to.get("type") == "admin":
                        a_id = str(assigned_to.get("id", "")).strip()
                        agente = assigned_to.get("name") or agentes_dict.get(a_id, f"Agente ({a_id})")

                if agente == "Sin asignar" and parts:
                    assignments_admin = [
                        p.get("assigned_to") for p in parts 
                        if p.get("part_type") == "assignment" and isinstance(p.get("assigned_to"), dict) and p.get("assigned_to", {}).get("type") == "admin"
                    ]
                    if assignments_admin:
                        last_assign = assignments_admin[-1]
                        a_id = str(last_assign.get("id", "")).strip()
                        agente = last_assign.get("name") or agentes_dict.get(a_id, f"Agente ({a_id})")

                if agente == "Sin asignar" and parts:
                    admin_parts = [
                        p.get("author") for p in parts 
                        if p.get("author", {}).get("type") in ["admin", "user_admin"] and 
                           p.get("author", {}).get("name") and 
                           p.get("author", {}).get("name", "").strip().lower() not in ["fin", "monica (bot)", "mónica (bot)"] and
                           not p.get("author", {}).get("from_ai_agent", False)
                    ]
                    if admin_parts:
                        agente = admin_parts[-1].get("name")

                if agente == "Sin asignar" and agente_por_tag:
                    agente = agente_por_tag

            # Validación de por_agente: Solo humanos L1 y Agendamiento entran a medición L1 (L2 e IA quedan excluidos)
            agente_clean_check = agente.strip().lower()
            etiquetas_lower_check = ", ".join(tags).lower()

            agentes_l2_excluidos = {
                "martin moreira", "martín moreira",
                "dario olmedo", "darío olmedo",
                "ivan amarilla", "iván amarilla",
                "gerardo gaona",
                "hector trinidad", "héctor trinidad"
            }

            es_nivel_2 = (
                "niv-l2" in etiquetas_lower_check
                or "l2:" in etiquetas_lower_check
                or agente_clean_check.startswith("l2")
                or agente_clean_check in agentes_l2_excluidos
            )

            es_excluido = (
                es_correo
                or es_nivel_2
                or agente_clean_check in ["sin asignar", "", "none", "nan", "monica", "mónica", "monica (bot)", "mónica (bot)", "laura almirón", "soporte", "soporte bims"]
                or "bot" in agente_clean_check
            )
            por_agente = "excluido" if es_excluido else "no excluido"

            # =========================================================================
            # 5. CSAT / RATING
            # =========================================================================
            rating_data = conv.get("conversation_rating") or conv_summary.get("conversation_rating") or {}
            teammate_data = rating_data.get("teammate") or {}
            rating_type = teammate_data.get("type")

            if rating_type == "admin":
                rating = rating_data.get("rating")
                calificacion = str(rating) if rating is not None else ""
                feedback = rating_data.get("remark") or ""
                cx_explanation = rating_data.get("remark") or ""
                ts_rating = rating_data.get("created_at")
                fecha_calificacion_iso = datetime.fromtimestamp(ts_rating, tz=tz_local).isoformat() if ts_rating else None
            else:
                rating = None
                calificacion = ""
                feedback = ""
                cx_explanation = ""
                fecha_calificacion_iso = None

            teaser_admin = rating_data.get("teaser", {}).get("admin", {}) if isinstance(rating_data.get("teaser"), dict) else {}
            admin_eval_id = str(teaser_admin.get("id", "")).strip() if teaser_admin else ""
            agente_evaluado = agentes_dict.get(admin_eval_id, agente)

            # =========================================================================
            # 6. ETIQUETAS Y METADATOS
            # =========================================================================
            modulo = next((t for t in tags if t.startswith("mod-")), "")
            cliente = next((t for t in tags if t.startswith("cli-")), "")
            tipo_contacto = next((t for t in tags if t.startswith("tipo-")), "")
            nivel = next((t for t in tags if t.startswith("Niv-")), "")
            etiquetas_str = ", ".join(tags)

            custom_attrs = conv.get("custom_attributes", {}) or {}
            ai_title_raw = custom_attrs.get("AI Title")
            ai_title = str(ai_title_raw).strip() if (ai_title_raw and str(ai_title_raw).strip() != "") else "No Generado"

            contacts = conv.get("contacts", {}).get("contacts", [])
            tenant, company, nombre_contacto = "Sin datos", "Sin datos", "Sin nombre"
            if contacts:
                c_data = get_contact(contacts[0].get("id"))
                if c_data:
                    tenant = get_attr(c_data, "tenant")
                    company = get_attr(c_data, "Company")
                    nombre_contacto = c_data.get("name") or c_data.get("email") or "Sin nombre"

            fecha_actualizacion_iso = datetime.fromtimestamp(updated_at_detail, tz=tz_local).isoformat() if updated_at_detail else datetime.now(tz=tz_local).isoformat()

            registro = {
                "id": conv_id,
                "created_at": inicio_real.isoformat(),
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
                "fecha_calificacion": fecha_calificacion_iso,
                "agente_evaluado": agente_evaluado,
                "cx_score_explanation": cx_explanation,
                "fecha_cierre": fecha_cierre.isoformat() if fecha_cierre else None,
                "etiquetas": etiquetas_str,
                "modulo": modulo,
                "cliente": cliente,
                "tipo_contacto": tipo_contacto,
                "nivel": nivel,
                "estado": estado,
                "updated_at": fecha_actualizacion_iso,
                "ai_title": ai_title,
                "cantidad_respuestas_agente": cantidad_respuestas_agente
            }
            lote_registros.append(registro)

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
    parser.add_argument("--dias", type=int, default=1, help="Días a sincronizar hacia atrás")
    parser.add_argument("--desde", type=str, default=None, help="Fecha inicio en formato YYYY-MM-DD")
    parser.add_argument("--hasta", type=str, default=None, help="Fecha fin en formato YYYY-MM-DD")
    args = parser.parse_args()
    
    sincronizar_intercom(dias=args.dias, fecha_desde=args.desde, fecha_hasta=args.hasta)
