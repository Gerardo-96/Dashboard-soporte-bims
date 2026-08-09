import os
import time
from datetime import datetime, date
import pandas as pd
from dateutil.relativedelta import relativedelta

# Importamos las funciones de tus módulos existentes
from sync_intercom import sincronizar_intercom, supabase

def generar_rangos_mensuales(fecha_inicio, fecha_fin):
    """Genera una lista de tuplas con el inicio y fin de cada mes."""
    rangos = []
    actual = fecha_inicio.replace(day=1)
    
    while actual <= fecha_fin:
        siguiente_mes = actual + relativedelta(months=1)
        fin_mes = min(siguiente_mes - relativedelta(days=1), fecha_fin)
        rangos.append((actual, fin_mes))
        actual = siguiente_mes
        
    return rangos

def poblar_base_de_datos():
    fecha_desde = date(2026, 1, 1)
    fecha_hasta = pd.Timestamp.now(tz="America/Asuncion").date()

    rangos_meses = generar_rangos_mensuales(fecha_desde, fecha_hasta)
    
    print("=" * 60)
    print(f"🚀 INICIANDO POBLAMIENTO MASIVO DESDE {fecha_desde} HASTA {fecha_hasta}")
    print(f"📅 Total de lotes mensuales a procesar: {len(rangos_meses)}")
    print("=" * 60)

    for i, (inicio, fin) in enumerate(rangos_meses, 1):
        dias_diferencia = (fecha_hasta - inicio).days + 1
        print(f"\n[Lote {i}/{len(rangos_meses)}] Procesando mes: {inicio.strftime('%B %Y')} ({inicio} al {fin})")
        print(f"👉 Consultando desde {dias_diferencia} días atrás...")

        try:
            # Ejecuta la sincronización usando tu lógica de sync_intercom.py
            sincronizar_intercom(dias=dias_diferencia)
            print(f"✅ Mes {inicio.strftime('%B %Y')} procesado con éxito.")
        except Exception as e:
            print(f"❌ Error al procesar el lote de {inicio.strftime('%B %Y')}: {str(e)}")
            print("⏳ Reintentando en 10 segundos...")
            time.sleep(10)

        # Pausa de seguridad de 5 segundos entre lotes para no superar los Rate Limits de Intercom
        print("⏸️ Pausa de seguridad (5 seg)...")
        time.sleep(5)

    print("\n" + "=" * 60)
    print("🎉 POBLAMIENTO HISTÓRICO COMPLETADO EXITOSAMENTE")
    print("=" * 60)

if __name__ == "__main__":
    poblar_base_de_datos()
