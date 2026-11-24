import json
import os
import sys
import time
from collections import Counter

import requests
from supabase import create_client


MODEL = "gemini-3.5-flash-lite"
BATCH_SIZE = 40
PAGE_SIZE = 1000


def paginar_tabla(supabase, tabla, columnas, orden):
    """Lee todas las filas usando paginación explícita para evitar el límite por defecto."""
    filas = []
    inicio = 0
    while True:
        respuesta = (
            supabase.table(tabla)
            .select(columnas)
            .order(orden)
            .range(inicio, inicio + PAGE_SIZE - 1)
            .execute()
        )
        lote = respuesta.data or []
        filas.extend(lote)
        if len(lote) < PAGE_SIZE:
            break
        inicio += PAGE_SIZE
    return filas


def obtener_titulos_y_frecuencias(supabase):
    filas = paginar_tabla(supabase, "conversaciones", "ai_title,created_at", "created_at")
    titulos = []
    for fila in filas:
        titulo = str(fila.get("ai_title") or "").strip()
        if not titulo or titulo.lower() in {"no generado", "nan", "none", "null"}:
            continue
        titulos.append(titulo)
    return Counter(titulos)


def obtener_catalogo(supabase):
    filas = paginar_tabla(
        supabase,
        "catalogo_motivos_recurrentes",
        "ai_title,motivo_recurrente,origen,revisado",
        "ai_title",
    )
    return {str(fila.get("ai_title") or "").strip(): fila for fila in filas}


def clasificar_lote(titulos, api_key):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    prompt = (
        "Actuá como especialista en taxonomía de incidencias de soporte. "
        "Recibirás títulos breves generados por IA. Agrupá únicamente los que "
        "describan el mismo problema o solicitud; normalizá singular/plural y "
        "redacciones equivalentes. Conservá diferencias entre productos, documentos "
        "y acciones: 'Certificación de factura' y 'Certificación de facturas' pueden "
        "compartir categoría, pero 'Certificación de ventas' debe quedar diferenciada. "
        "'Anulación de factura' no debe confundirse con 'Cambio de factura'. "
        "Usá categorías breves y claras. Si el título es ambiguo, mantenelo como "
        "categoría independiente. No inventes contexto. Respondé exclusivamente "
        'JSON válido: {"items":[{"ai_title":"título exacto",'
        '"motivo_recurrente":"categoría"}]}. Devolvé un elemento por título, '
        "sin omitir ni alterar el texto original. TÍTULOS: "
        + json.dumps(titulos, ensure_ascii=False)
    )
    cuerpo = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "maxOutputTokens": 5000,
        },
    }

    # Gemini puede responder temporalmente 429/5xx por cuotas o alta demanda.
    # Reintentar con espera exponencial evita perder la ejecución por un pico breve.
    max_intentos = 6
    respuesta = None
    for intento in range(1, max_intentos + 1):
        try:
            respuesta = requests.post(
                url,
                headers={
                    "x-goog-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json=cuerpo,
                timeout=90,
            )
        except requests.RequestException as error:
            if intento == max_intentos:
                raise RuntimeError(
                    f"No se pudo conectar con Gemini tras {max_intentos} intentos: {error}"
                ) from error
            espera = min(15 * (2 ** (intento - 1)), 180)
            print(
                f"Fallo de conexión con Gemini (intento {intento}/{max_intentos}). "
                f"Nuevo intento en {espera}s.",
                flush=True,
            )
            time.sleep(espera)
            continue

        if respuesta.status_code < 400:
            break

        if respuesta.status_code not in {408, 429, 500, 502, 503, 504}:
            raise RuntimeError(
                f"Gemini respondió HTTP {respuesta.status_code}: {respuesta.text[:500]}"
            )

        if intento == max_intentos:
            raise RuntimeError(
                f"Gemini sigue respondiendo HTTP {respuesta.status_code} "
                f"tras {max_intentos} intentos: {respuesta.text[:500]}"
            )

        espera = min(15 * (2 ** (intento - 1)), 180)
        print(
            f"Gemini respondió HTTP {respuesta.status_code} "
            f"(intento {intento}/{max_intentos}). Nuevo intento en {espera}s.",
            flush=True,
        )
        time.sleep(espera)

    payload = respuesta.json()
    candidatos = payload.get("candidates") or []
    partes = candidatos[0].get("content", {}).get("parts", []) if candidatos else []
    texto = "\n".join(
        str(parte.get("text", "")).strip()
        for parte in partes
        if parte.get("text")
    ).strip()
    if texto.startswith("```"):
        texto = texto.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    datos = json.loads(texto)
    items = datos.get("items", []) if isinstance(datos, dict) else []
    esperados = set(titulos)
    resultados = {}
    for item in items:
        titulo = str(item.get("ai_title") or "").strip()
        motivo = str(item.get("motivo_recurrente") or "").strip()
        if titulo in esperados and motivo:
            resultados[titulo] = {
                "ai_title": titulo,
                "motivo_recurrente": motivo,
                "origen": "ia",
                "revisado": False,
            }

    faltantes = esperados - set(resultados)
    if faltantes:
        raise ValueError(
            "Gemini no clasificó todos los títulos del lote: "
            + ", ".join(sorted(faltantes)[:10])
        )
    return list(resultados.values())


def main():
    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_key = os.environ.get("SUPABASE_KEY", "").strip()
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not all([supabase_url, supabase_key, gemini_key]):
        raise RuntimeError(
            "Faltan secretos. Se requieren SUPABASE_URL, SUPABASE_KEY y GEMINI_API_KEY."
        )

    supabase = create_client(supabase_url, supabase_key)
    frecuencias = obtener_titulos_y_frecuencias(supabase)
    print(f"Títulos AI válidos encontrados: {len(frecuencias)}")

    total_clasificados = 0
    numero_lote = 0

    while True:
        catalogo = obtener_catalogo(supabase)
        pendientes = [
            titulo for titulo in frecuencias
            if titulo not in catalogo
        ]
        pendientes.sort(key=lambda titulo: (-frecuencias[titulo], titulo.lower()))

        if not pendientes:
            print("No quedan títulos pendientes. Proceso finalizado.")
            break

        lote = pendientes[:BATCH_SIZE]
        numero_lote += 1
        print(
            f"Lote {numero_lote}: clasificando {len(lote)} títulos "
            f"({len(pendientes)} pendientes antes del lote)."
        )

        nuevos = clasificar_lote(lote, gemini_key)

        # Insertar, no hacer upsert: así una corrección manual preexistente
        # nunca se reemplaza accidentalmente por una nueva propuesta de IA.
        try:
            supabase.table("catalogo_motivos_recurrentes").insert(nuevos).execute()
            insertados = len(nuevos)
        except Exception:
            # Si hubo una edición concurrente o un título se registró durante
            # la clasificación, volver a consultar y reintentar solo los ausentes.
            catalogo_actualizado = obtener_catalogo(supabase)
            realmente_nuevos = [
                fila for fila in nuevos if fila["ai_title"] not in catalogo_actualizado
            ]
            if not realmente_nuevos:
                insertados = 0
            else:
                supabase.table("catalogo_motivos_recurrentes").insert(
                    realmente_nuevos
                ).execute()
                insertados = len(realmente_nuevos)

        total_clasificados += insertados
        print(f"Lote {numero_lote} guardado: {insertados} títulos nuevos.")

        if insertados == 0:
            # Evita un bucle infinito si otro proceso está modificando el catálogo.
            catalogo_final = obtener_catalogo(supabase)
            pendientes_actuales = [
                titulo for titulo in frecuencias
                if titulo not in catalogo_final
            ]
            if pendientes_actuales:
                raise RuntimeError(
                    "No se pudo guardar el lote y siguen existiendo pendientes. "
                    "Se detiene la ejecución para evitar un ciclo infinito."
                )

    print(
        f"Resumen: {total_clasificados} títulos clasificados en "
        f"{numero_lote} lotes."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
