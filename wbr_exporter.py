import io
import os
import pandas as pd
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.chart.data import CategoryChartData

# Paleta corporativa
C_FONDO_TABLA = RGBColor(15, 23, 42)      # #0f172a
C_HEADER_TABLA = RGBColor(2, 132, 199)    # #0284c7
C_BORDE_TABLA = RGBColor(51, 65, 85)      # #334155
C_TEXTO_BLANCO = RGBColor(248, 250, 252)  # #f8fafc
C_TEXTO_MUTED = RGBColor(148, 163, 184)   # #94a3b8
C_DORADO = RGBColor(251, 191, 36)         # #fbbf24
C_AZUL_TXT = RGBColor(56, 189, 248)       # #38bdf8

def reemplazar_en_text_frame(tf, reemplazos):
    for p in tf.paragraphs:
        full_text = "".join(r.text for r in p.runs)
        modificado = False
        for k, v in reemplazos.items():
            if k in full_text:
                full_text = full_text.replace(k, str(v))
                modificado = True
        if modificado:
            if len(p.runs) > 0:
                p.runs[0].text = full_text
                for r in p.runs[1:]:
                    r.text = ""
            else:
                p.text = full_text

def procesar_forma_reemplazos(shape, reemplazos):
    if shape.has_text_frame:
        reemplazar_en_text_frame(shape.text_frame, reemplazos)
    if shape.has_table:
        for row in shape.table.rows:
            for cell in row.cells:
                reemplazar_en_text_frame(cell.text_frame, reemplazos)
    if shape.shape_type == 6:  # MSO_SHAPE_TYPE.GROUP
        for sub_shape in shape.shapes:
            procesar_forma_reemplazos(sub_shape, reemplazos)

def construir_tabla_motivos_l1(slide, df_l1_motivos, x=4.5, y=3.8, w=8.0):
    if df_l1_motivos is None or df_l1_motivos.empty:
        return

    num_filas = len(df_l1_motivos) + 1
    table_shape = slide.shapes.add_table(num_filas, 3, Inches(x), Inches(y), Inches(w), Inches(0.32 * num_filas))
    table = table_shape.table

    table.columns[0].width = Inches(2.2)
    table.columns[1].width = Inches(4.6)
    table.columns[2].width = Inches(1.2)

    headers = ["Calificación", "Motivo normalizado", "Cantidad"]
    for col_idx, h_text in enumerate(headers):
        cell = table.cell(0, col_idx)
        cell.fill.solid()
        cell.fill.fore_color.rgb = C_HEADER_TABLA
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        p = cell.text_frame.paragraphs[0]
        p.text = h_text
        p.font.bold = True
        p.font.size = Pt(9.5)
        p.font.color.rgb = C_TEXTO_BLANCO
        if col_idx == 2:
            p.alignment = PP_ALIGN.CENTER

    df_ord = df_l1_motivos.sort_values(by=["rating_int", "Cantidad"], ascending=[True, False])
    mapa_stars = {1: "★ Muy malo (1★)", 2: "★★ Malo (2★)", 3: "★★★ Regular (3★)"}

    fila_actual = 1
    bloques_merge = []

    for rating_val, grp in df_ord.groupby("rating_int", sort=True):
        inicio_grupo = fila_actual
        for _, row in grp.iterrows():
            c0 = table.cell(fila_actual, 0)
            c0.fill.solid()
            c0.fill.fore_color.rgb = RGBColor(30, 41, 59)
            p0 = c0.text_frame.paragraphs[0]
            p0.text = mapa_stars.get(rating_val, f"{rating_val}★")
            p0.font.bold = True
            p0.font.size = Pt(9)
            p0.font.color.rgb = C_DORADO

            c1 = table.cell(fila_actual, 1)
            c1.fill.solid()
            c1.fill.fore_color.rgb = C_FONDO_TABLA
            p1 = c1.text_frame.paragraphs[0]
            p1.text = str(row.get("motivo_normalizado", ""))
            p1.font.size = Pt(8.5)
            p1.font.color.rgb = C_TEXTO_BLANCO

            c2 = table.cell(fila_actual, 2)
            c2.fill.solid()
            c2.fill.fore_color.rgb = C_FONDO_TABLA
            p2 = c2.text_frame.paragraphs[0]
            p2.text = str(row.get("Cantidad", 0))
            p2.alignment = PP_ALIGN.CENTER
            p2.font.bold = True
            p2.font.size = Pt(9)
            p2.font.color.rgb = C_AZUL_TXT

            fila_actual += 1

        fin_grupo = fila_actual - 1
        if fin_grupo > inicio_grupo:
            bloques_merge.append((inicio_grupo, fin_grupo))

    for ini, fin in bloques_merge:
        table.cell(ini, 0).merge(table.cell(fin, 0))
        table.cell(ini, 0).vertical_anchor = MSO_ANCHOR.MIDDLE

def construir_tabla_casos_l3(slide, df_l3_neg, x=4.3, y=3.8, w=8.3):
    if df_l3_neg is None or df_l3_neg.empty:
        return

    num_filas = len(df_l3_neg) + 1
    table_shape = slide.shapes.add_table(num_filas, 5, Inches(x), Inches(y), Inches(w), Inches(0.32 * num_filas))
    table = table_shape.table

    table.columns[0].width = Inches(1.8)
    table.columns[1].width = Inches(2.7)
    table.columns[2].width = Inches(1.4)
    table.columns[3].width = Inches(1.1)
    table.columns[4].width = Inches(1.3)

    headers = ["Calificación", "Motivo", "Ticket", "Prioridad", "Estado"]
    for col_idx, h_text in enumerate(headers):
        cell = table.cell(0, col_idx)
        cell.fill.solid()
        cell.fill.fore_color.rgb = C_HEADER_TABLA
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        p = cell.text_frame.paragraphs[0]
        p.text = h_text
        p.font.bold = True
        p.font.size = Pt(9.5)
        p.font.color.rgb = C_TEXTO_BLANCO
        if col_idx in [2, 3, 4]:
            p.alignment = PP_ALIGN.CENTER

    df_ord = df_l3_neg.sort_values(by=["rating_int", "ticket_limpio"], ascending=[True, True])
    mapa_stars = {1: "★ Muy malo (1★)", 2: "★★ Malo (2★)", 3: "★★★ Regular (3★)"}

    fila_actual = 1
    bloques_merge = []

    for rating_val, grp in df_ord.groupby("rating_int", sort=True):
        inicio_grupo = fila_actual
        for _, row in grp.iterrows():
            c0 = table.cell(fila_actual, 0)
            c0.fill.solid()
            c0.fill.fore_color.rgb = RGBColor(30, 41, 59)
            p0 = c0.text_frame.paragraphs[0]
            p0.text = mapa_stars.get(rating_val, f"{rating_val}★")
            p0.font.bold = True
            p0.font.size = Pt(9)
            p0.font.color.rgb = C_DORADO

            c1 = table.cell(fila_actual, 1)
            c1.fill.solid()
            c1.fill.fore_color.rgb = C_FONDO_TABLA
            p1 = c1.text_frame.paragraphs[0]
            p1.text = str(row.get("motivo_clean", ""))[:45]
            p1.font.size = Pt(8.5)
            p1.font.color.rgb = C_TEXTO_BLANCO

            c2 = table.cell(fila_actual, 2)
            c2.fill.solid()
            c2.fill.fore_color.rgb = C_FONDO_TABLA
            p2 = c2.text_frame.paragraphs[0]
            p2.text = str(row.get("ticket_limpio", "-"))
            p2.alignment = PP_ALIGN.CENTER
            p2.font.bold = True
            p2.font.size = Pt(8.5)
            p2.font.color.rgb = RGBColor(129, 140, 248)

            c3 = table.cell(fila_actual, 3)
            c3.fill.solid()
            c3.fill.fore_color.rgb = C_FONDO_TABLA
            p3 = c3.text_frame.paragraphs[0]
            p3.text = str(row.get("prioridad", "Media"))
            p3.alignment = PP_ALIGN.CENTER
            p3.font.size = Pt(8.5)
            p3.font.color.rgb = C_TEXTO_BLANCO

            c4 = table.cell(fila_actual, 4)
            c4.fill.solid()
            c4.fill.fore_color.rgb = C_FONDO_TABLA
            p4 = c4.text_frame.paragraphs[0]
            p4.text = str(row.get("estado", "Pendiente"))
            p4.alignment = PP_ALIGN.CENTER
            p4.font.size = Pt(8.5)
            p4.font.color.rgb = RGBColor(52, 211, 153)

            fila_actual += 1

        fin_grupo = fila_actual - 1
        if fin_grupo > inicio_grupo:
            bloques_merge.append((inicio_grupo, fin_grupo))

    for ini, fin in bloques_merge:
        table.cell(ini, 0).merge(table.cell(fin, 0))
        table.cell(ini, 0).vertical_anchor = MSO_ANCHOR.MIDDLE

def exportar_wbr_con_plantilla(datos):
    """
    Rellena la plantilla template_wbr.pptx o template_wbr_2.pptx con los datos semanales.
    """
    ruta_plantilla = "template_wbr_2.pptx" if os.path.exists("template_wbr_2.pptx") else "template_wbr.pptx"
    
    if not os.path.exists(ruta_plantilla):
        raise FileNotFoundError(f"No se encontró el archivo de plantilla: {ruta_plantilla}")

    prs = Presentation(ruta_plantilla)

    reemplazos = {
        # Cabecera / Semanas
        "{{SEMANA_LABEL}}": str(datos.get("semana_label", "")),
        "{{TARGET_WEEK}}": str(datos.get("target_week", "")),
        "{{SEM_PREV_NUM}}": str(datos.get("sem_prev_num", "")),

        # Slide 1: 5 Semanas
        "{{TOT_5W_REC}}": f"{datos.get('tot_5w_rec', 0):,}",
        "{{TOT_5W_CER}}": f"{datos.get('tot_5w_cer', 0):,}",
        "{{PCT_5W_CER}}": f"{datos.get('pct_5w_cer', 0)}",

        # Slide 2: Detalle Operativo Semanal
        "{{TOT_SEM_REC}}": f"{datos.get('tot_sem_rec', 0):,}",
        "{{TOT_SEM_CER}}": f"{datos.get('tot_sem_cer', 0):,}",
        "{{PCT_SEM_CER}}": f"{datos.get('pct_sem_cer', 0)}",
        "{{DIFF_REC_PCT}}": f"{datos.get('diff_rec_pct', 0):+.2f}",
        "{{DIFF_CER_PCT}}": f"{datos.get('diff_cer_pct', 0):+.2f}",
        "{{TOT_PREV_REC}}": f"{datos.get('tot_prev_rec', 0):,}",
        "{{TOT_PREV_CER}}": f"{datos.get('tot_prev_cer', 0):,}",
        "{{SIN_RESP_COUNT}}": f"{datos.get('sin_resp_count', 0):,}",
        "{{PROM_POR_AGENTE}}": f"{datos.get('prom_por_agente', 0):.2f}",
        "{{CUENTAS_UNICAS}}": str(datos.get("cuentas_unicas", 0)),
        "{{EMPRESAS_UNICAS}}": str(datos.get("empresas_unicas", 0)),
        "{{CNT_CERRADOS_IA}}": str(datos.get("cnt_cerrados_ia", 0)),
        "{{PCT_IA}}": f"{datos.get('pct_ia', 0):.2f}",

        # Slide 3: Tiempos de Gestión y 1ra Respuesta
        "{{FOCO_GEST}}": f"{datos.get('foco_gest', 0)}",
        "{{FOCO_1RA}}": f"{datos.get('foco_1ra', 0)}",
        "{{PREV_GEST}}": f"{datos.get('prev_gest', 0)}",
        "{{PREV_1RA}}": f"{datos.get('prev_1ra', 0)}",
        "{{WOW_GEST_PCT}}": f"{datos.get('wow_gest_pct', 0):+.2f}",
        "{{WOW_1RA_PCT}}": f"{datos.get('wow_1ra_pct', 0):+.2f}",
        "{{FOCO_SLA_GEST}}": f"{datos.get('foco_sla_gest', 0)}",
        "{{FOCO_SLA_1RA}}": f"{datos.get('foco_sla_1ra', 0)}",

        # Slide 4: Chats Evaluados & CSAT
        "{{TOT_SIN_CALIFICAR}}": f"{datos.get('tot_sin_calificar', 0):,}",
        "{{PCT_SIN_CALIFICAR}}": f"{datos.get('pct_sin_calificar', 0)}",
        "{{TOT_EVALUADOS}}": f"{datos.get('tot_evaluados', 0):,}",
        "{{PCT_CALIFICADOS}}": f"{datos.get('pct_calificados', 0)}",
        "{{CNT_5}}": str(datos.get("cnt_5", 0)),
        "{{PCT_5}}": str(datos.get("pct_5", 0)),
        "{{CNT_4}}": str(datos.get("cnt_4", 0)),
        "{{PCT_4}}": str(datos.get("pct_4", 0)),
        "{{CNT_3}}": str(datos.get("cnt_3", 0)),
        "{{PCT_3}}": str(datos.get("pct_3", 0)),
        "{{CNT_2}}": str(datos.get("cnt_2", 0)),
        "{{PCT_2}}": str(datos.get("pct_2", 0)),
        "{{CNT_1}}": str(datos.get("cnt_1", 0)),
        "{{PCT_1}}": str(datos.get("pct_1", 0)),

        # Slide 5: Evolutivo CSAT & Empresa
        "{{FOCO_CSAT}}": f"{datos.get('foco_csat', 0)}",
        "{{DIFF_CSAT_WOW}}": f"{datos.get('diff_csat_wow', 0):+}",
        "{{FOCO_RATIO}}": f"{datos.get('foco_ratio', 0):.2f}",

        # Slide 6 y 7: Fricción L1 y L3
        "{{TOT_NEGATIVAS}}": f"{datos.get('tot_negativas', 0)}",
        "{{TOT_L1_NEG}}": f"{datos.get('tot_l1_neg', 0)}",
        "{{PCT_L1_TOTAL}}": f"{datos.get('pct_l1_total', 0)}",
        "{{TOT_L3_NEG}}": f"{datos.get('tot_l3_neg', 0)}",
        "{{PCT_L3_TOTAL}}": f"{datos.get('pct_l3_total', 0)}",

        # Slide 8: Horario Extendido
        "{{TOT_EXT}}": f"{datos.get('tot_ext', 0)}",
        "{{TOT_DOMINGO}}": f"{datos.get('tot_domingo', 0)}",
        "{{PCT_EXT_CER}}": f"{datos.get('pct_ext_cerrados', 0)}",
        "{{TOT_EXT_CER}}": f"{datos.get('tot_ext_cerrados', 0)}"
    }

    # 1. Reemplazar comodines en todas las diapositivas
    for slide in prs.slides:
        for shape in slide.shapes:
            procesar_forma_reemplazos(shape, reemplazos)

    # 2. Inyección de datos en los gráficos de la plantilla
    # Slide 1: Gráfico 5 semanas
    try:
        df_5w = datos.get("df_5w")
        if df_5w is not None and not df_5w.empty and len(prs.slides) > 0:
            for s in prs.slides[0].shapes:
                if s.has_chart:
                    cd = CategoryChartData()
                    cd.categories = df_5w["Semana"].tolist()
                    cd.add_series("Recibidos", tuple(df_5w["Recibidos"]))
                    cd.add_series("Cerrados", tuple(df_5w["Cerrados"]))
                    s.chart.replace_data(cd)
                    break
    except Exception as e:
        print(f"Nota gráfico Slide 1: {e}")

    # Slide 2: Gráficos de Día y Agentes
    try:
        if len(prs.slides) > 1:
            df_dias = datos.get("df_dias")
            df_agentes = datos.get("df_agentes")
            ch_list = [s.chart for s in prs.slides[1].shapes if s.has_chart]
            if len(ch_list) >= 1 and df_dias is not None and not df_dias.empty:
                cd_dias = CategoryChartData()
                cd_dias.categories = df_dias["Fecha"].tolist()
                cd_dias.add_series("Recibidos", tuple(df_dias["Recibidos"]))
                cd_dias.add_series("Cerrados", tuple(df_dias["Cerrados"]))
                ch_list[0].replace_data(cd_dias)
            if len(ch_list) >= 2 and df_agentes is not None and not df_agentes.empty:
                cd_ag = CategoryChartData()
                cd_ag.categories = df_agentes["Agente"].tolist()
                cd_ag.add_series("Cerrados", tuple(df_agentes["Cerrados"]))
                ch_list[1].replace_data(cd_ag)
    except Exception as e:
        print(f"Nota gráficos Slide 2: {e}")

    # 3. Poblar tablas de motivos L1 (Slide 6) y L3 (Slide 7)
    try:
        if len(prs.slides) >= 6:
            construir_tabla_motivos_l1(prs.slides[5], datos.get("df_l1_motivos"))
    except Exception as e:
        print(f"Nota tabla L1: {e}")

    try:
        if len(prs.slides) >= 7:
            construir_tabla_casos_l3(prs.slides[6], datos.get("df_l3_neg"))
    except Exception as e:
        print(f"Nota tabla L3: {e}")

    output = io.BytesIO()
    prs.save(output)
    output.seek(0)
    return output
