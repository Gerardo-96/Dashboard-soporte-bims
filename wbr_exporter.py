import io
import tempfile
import kaleido
import os
import copy
import pandas as pd
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

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


def obtener_formas_grafico_ordenadas(slide):
    """
    Extrae las formas placeholder de gráficos en la diapositiva:
    prioriza gráficos nativos (has_chart) y, si no hay, imágenes grandes (shape_type == 13).
    Ordena de izquierda a derecha y de arriba a abajo.
    """
    charts = []
    pictures = []

    def recolectar(shp):
        if shp.has_chart:
            charts.append(shp)
        elif shp.shape_type == 13:  # PICTURE (ignora logos pequeños < 2.2 pulgadas)
            if (shp.width / 914400.0) >= 2.2 and (shp.height / 914400.0) >= 1.5:
                pictures.append(shp)
        elif shp.shape_type == 6:  # GROUP
            for sub in shp.shapes:
                recolectar(sub)

    for s in slide.shapes:
        recolectar(s)

    objetivos = charts if charts else pictures
    objetivos.sort(key=lambda s: (round(s.top / 914400.0, 1), s.left))
    return objetivos


def preparar_figura_para_pptx(fig, width_emu, height_emu, path_png, fondo_oscuro=True):
    """Prepara el diccionario de especificación para renderizar todas las figuras en una sola pasada."""
    width_in = max(2.0, width_emu / 914400.0)
    height_in = max(1.5, height_emu / 914400.0)
    px_w = int(width_in * 130)
    px_h = int(height_in * 130)

    fig_export = copy.deepcopy(fig)
    if fondo_oscuro:
        fig_export.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#f8fafc", size=12),
        )
    else:
        fig_export.update_layout(
            paper_bgcolor="rgba(255,255,255,0)",
            plot_bgcolor="rgba(255,255,255,0)",
            font=dict(color="#1e293b", size=12),
        )

    return {
        "fig": fig_export,
        "path": path_png,
        "opts": {"format": "png", "width": px_w, "height": px_h, "scale": 2}
    }


def insertar_png_en_lugar_de_forma(slide, target_shape, path_png):
    """Elimina el placeholder de la slide e inserta el PNG renderizado en sus mismas coordenadas."""
    if not os.path.exists(path_png):
        return
    left = target_shape.left
    top = target_shape.top
    width = target_shape.width
    height = target_shape.height

    sp_elem = target_shape._element
    sp_elem.getparent().remove(sp_elem)

    slide.shapes.add_picture(path_png, left, top, width=width, height=height)

def obtener_todas_las_tablas(slide):
    """Extrae todas las tablas de un slide ordenadas de arriba a abajo (por posición Y)."""
    tablas = []
    for shp in slide.shapes:
        if shp.has_table:
            tablas.append(shp)
    tablas.sort(key=lambda t: t.top)
    return tablas


# =============================================================================
# FUNCIONES PARA TABLAS DE MATRIZ DE AGENTES (SLIDES 6 Y 7)
# =============================================================================
def poblar_tabla_matriz_agentes(tabla_shape, df_agentes, slide, x_def=4.3, y_def=1.5, w_def=8.3):
    if df_agentes is None or df_agentes.empty:
        return

    cols_esperadas = ["Agente Asignado", "Regular (3★)", "Malo (2★)", "Muy malo (1★)", "Todos los casos", "% del Total"]
    
    df_poblar = df_agentes.copy()
    if "% del Total a L1" in df_poblar.columns:
        df_poblar = df_poblar.rename(columns={"% del Total a L1": "% del Total"})
    elif "% del Total a L3" in df_poblar.columns:
        df_poblar = df_poblar.rename(columns={"% del Total a L3": "% del Total"})

    for col in cols_esperadas:
        if col not in df_poblar.columns:
            df_poblar[col] = 0

    num_filas = len(df_poblar) + 1

    if tabla_shape is None:
        shape_t = slide.shapes.add_table(num_filas, 6, Inches(x_def), Inches(y_def), Inches(w_def), Inches(0.28 * num_filas))
        table = shape_t.table
        headers = ["Agente Asignado", "Regular (3★)", "Malo (2★)", "Muy malo (1★)", "Todos los casos", "% del Total"]
        for c_idx, h_text in enumerate(headers):
            cell = table.cell(0, c_idx)
            cell.fill.solid()
            cell.fill.fore_color.rgb = C_HEADER_TABLA
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            p = cell.text_frame.paragraphs[0]
            p.text = h_text
            p.font.bold = True
            p.font.size = Pt(9)
            p.font.color.rgb = C_TEXTO_BLANCO
            if c_idx > 0:
                p.alignment = PP_ALIGN.CENTER
    else:
        table = tabla_shape.table
        while len(table.rows) < num_filas:
            table.rows._tbl.append(table.rows[0]._tr)

    for r_idx, (_, row) in enumerate(df_poblar.iterrows(), start=1):
        c0 = table.cell(r_idx, 0)
        c0.fill.solid()
        c0.fill.fore_color.rgb = C_FONDO_TABLA
        p0 = c0.text_frame.paragraphs[0]
        p0.text = str(row.get("Agente Asignado", "Sin asignar"))
        p0.font.bold = True
        p0.font.size = Pt(8.5)
        p0.font.color.rgb = C_TEXTO_BLANCO

        c1 = table.cell(r_idx, 1)
        c1.fill.solid()
        c1.fill.fore_color.rgb = C_FONDO_TABLA
        p1 = c1.text_frame.paragraphs[0]
        p1.text = str(row.get("Regular (3★)", 0))
        p1.alignment = PP_ALIGN.CENTER
        p1.font.size = Pt(8.5)
        p1.font.color.rgb = C_DORADO

        c2 = table.cell(r_idx, 2)
        c2.fill.solid()
        c2.fill.fore_color.rgb = C_FONDO_TABLA
        p2 = c2.text_frame.paragraphs[0]
        p2.text = str(row.get("Malo (2★)", 0))
        p2.alignment = PP_ALIGN.CENTER
        p2.font.size = Pt(8.5)
        p2.font.color.rgb = RGBColor(249, 115, 22)

        c3 = table.cell(r_idx, 3)
        c3.fill.solid()
        c3.fill.fore_color.rgb = C_FONDO_TABLA
        p3 = c3.text_frame.paragraphs[0]
        p3.text = str(row.get("Muy malo (1★)", 0))
        p3.alignment = PP_ALIGN.CENTER
        p3.font.size = Pt(8.5)
        p3.font.color.rgb = RGBColor(239, 68, 68)

        c4 = table.cell(r_idx, 4)
        c4.fill.solid()
        c4.fill.fore_color.rgb = RGBColor(30, 41, 59)
        p4 = c4.text_frame.paragraphs[0]
        p4.text = str(row.get("Todos los casos", 0))
        p4.alignment = PP_ALIGN.CENTER
        p4.font.bold = True
        p4.font.size = Pt(8.5)
        p4.font.color.rgb = C_AZUL_TXT

        c5 = table.cell(r_idx, 5)
        c5.fill.solid()
        c5.fill.fore_color.rgb = RGBColor(30, 41, 59)
        p5 = c5.text_frame.paragraphs[0]
        p5.text = str(row.get("% del Total", "0%"))
        p5.alignment = PP_ALIGN.CENTER
        p5.font.bold = True
        p5.font.size = Pt(8.5)
        p5.font.color.rgb = C_AZUL_TXT


# =============================================================================
# FUNCIONES PARA TABLAS DE DETALLE INFERIORES (MOTIVOS / TICKETS)
# =============================================================================
def poblar_tabla_l1_motivos(tabla_shape, df_l1_motivos, slide):
    if df_l1_motivos is None or df_l1_motivos.empty:
        return

    df_ord = df_l1_motivos.sort_values(by=["rating_int", "Cantidad"], ascending=[True, False])
    mapa_stars = {1: "★ Muy malo (1★)", 2: "★★ Malo (2★)", 3: "★★★ Regular (3★)"}
    num_filas = len(df_ord) + 1

    if tabla_shape is None:
        shape_t = slide.shapes.add_table(num_filas, 3, Inches(4.3), Inches(4.2), Inches(8.3), Inches(0.28 * num_filas))
        table = shape_t.table
        table.columns[0].width = Inches(2.2)
        table.columns[1].width = Inches(4.8)
        table.columns[2].width = Inches(1.3)
        headers = ["Calificación", "Motivo normalizado", "Cantidad"]
        for c_idx, h_text in enumerate(headers):
            cell = table.cell(0, c_idx)
            cell.fill.solid()
            cell.fill.fore_color.rgb = C_HEADER_TABLA
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            p = cell.text_frame.paragraphs[0]
            p.text = h_text
            p.font.bold = True
            p.font.size = Pt(9.5)
            p.font.color.rgb = C_TEXTO_BLANCO
            if c_idx == 2:
                p.alignment = PP_ALIGN.CENTER
    else:
        table = tabla_shape.table
        while len(table.rows) < num_filas:
            table.rows._tbl.append(table.rows[0]._tr)

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


def poblar_tabla_l3_casos(tabla_shape, df_l3_neg, slide):
    if df_l3_neg is None or df_l3_neg.empty:
        return

    df_ord = df_l3_neg.sort_values(by=["rating_int", "ticket_limpio"], ascending=[True, True])
    mapa_stars = {1: "★ Muy malo (1★)", 2: "★★ Malo (2★)", 3: "★★★ Regular (3★)"}
    num_filas = len(df_ord) + 1

    if tabla_shape is None:
        shape_t = slide.shapes.add_table(num_filas, 5, Inches(4.3), Inches(4.2), Inches(8.3), Inches(0.28 * num_filas))
        table = shape_t.table
        table.columns[0].width = Inches(1.8)
        table.columns[1].width = Inches(2.7)
        table.columns[2].width = Inches(1.4)
        table.columns[3].width = Inches(1.1)
        table.columns[4].width = Inches(1.3)
        headers = ["Calificación", "Motivo", "Ticket", "Prioridad", "Estado"]
        for c_idx, h_text in enumerate(headers):
            cell = table.cell(0, c_idx)
            cell.fill.solid()
            cell.fill.fore_color.rgb = C_HEADER_TABLA
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            p = cell.text_frame.paragraphs[0]
            p.text = h_text
            p.font.bold = True
            p.font.size = Pt(9.5)
            p.font.color.rgb = C_TEXTO_BLANCO
            if c_idx in [2, 3, 4]:
                p.alignment = PP_ALIGN.CENTER
    else:
        table = tabla_shape.table
        while len(table.rows) < num_filas:
            table.rows._tbl.append(table.rows[0]._tr)

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


# =============================================================================
# EXPORTADOR PRINCIPAL (CON CAPTURAS PNG HD DE PLOTLY)
# =============================================================================
def exportar_wbr_con_plantilla(datos):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    ruta_2 = os.path.join(base_dir, "template_wbr_2.pptx")
    ruta_1 = os.path.join(base_dir, "template_wbr.pptx")
    
    if os.path.exists(ruta_2):
        ruta_plantilla = ruta_2
    elif os.path.exists(ruta_1):
        ruta_plantilla = ruta_1
    elif os.path.exists("template_wbr_2.pptx"):
        ruta_plantilla = "template_wbr_2.pptx"
    elif os.path.exists("template_wbr.pptx"):
        ruta_plantilla = "template_wbr.pptx"
    else:
        raise FileNotFoundError("No se encontró el archivo de plantilla PowerPoint (.pptx).")

    prs = Presentation(ruta_plantilla)

    reemplazos = {
        "{{SEMANA_LABEL}}": str(datos.get("semana_label", "")),
        "{{TARGET_WEEK}}": str(datos.get("target_week", "")),
        "{{SEM_PREV_NUM}}": str(datos.get("sem_prev_num", "")),

        "{{TOT_5W_REC}}": f"{datos.get('tot_5w_rec', 0):,}",
        "{{TOT_5W_CER}}": f"{datos.get('tot_5w_cer', 0):,}",
        "{{PCT_5W_CER}}": f"{datos.get('pct_5w_cer', 0)}",

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

        "{{FOCO_GEST}}": f"{datos.get('foco_gest', 0)}",
        "{{FOCO_1RA}}": f"{datos.get('foco_1ra', 0)}",
        "{{PREV_GEST}}": f"{datos.get('prev_gest', 0)}",
        "{{PREV_1RA}}": f"{datos.get('prev_1ra', 0)}",
        "{{WOW_GEST_PCT}}": f"{datos.get('wow_gest_pct', 0):+.2f}",
        "{{WOW_1RA_PCT}}": f"{datos.get('wow_1ra_pct', 0):+.2f}",
        "{{FOCO_SLA_GEST}}": f"{datos.get('foco_sla_gest', 0)}",
        "{{FOCO_SLA_1RA}}": f"{datos.get('foco_sla_1ra', 0)}",

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

        "{{FOCO_CSAT}}": f"{datos.get('foco_csat', 0)}",
        "{{DIFF_CSAT_WOW}}": f"{datos.get('diff_csat_wow', 0):+}",
        "{{FOCO_RATIO}}": f"{datos.get('foco_ratio', 0):.2f}",

        "{{TOT_NEGATIVAS}}": f"{datos.get('tot_negativas', 0)}",
        "{{TOT_L1_NEG}}": f"{datos.get('tot_l1_neg', 0)}",
        "{{PCT_L1_TOTAL}}": f"{datos.get('pct_l1_total', 0)}",
        "{{TOT_L3_NEG}}": f"{datos.get('tot_l3_neg', 0)}",
        "{{PCT_L3_TOTAL}}": f"{datos.get('pct_l3_total', 0)}",

        "{{TOT_EXT}}": f"{datos.get('tot_ext', 0)}",
        "{{TOT_DOMINGO}}": f"{datos.get('tot_domingo', 0)}",
        "{{PCT_EXT_CER}}": f"{datos.get('pct_ext_cerrados', 0)}",
        "{{TOT_EXT_CER}}": f"{datos.get('tot_ext_cerrados', 0)}",

        "{{TOT_CORREOS_REC}}": f"{datos.get('tot_correos_rec', 0):,}",
        "{{TOT_CORREOS_CER}}": f"{datos.get('tot_correos_cer', 0):,}",
        "{{PCT_COR_RES}}": f"{datos.get('pct_cor_res', 0)}",
        "{{TOT_RESP_CORREOS}}": f"{datos.get('tot_resp_correos', 0):,}",
    }

    # 1. Reemplazo de Textos en todas las diapositivas (incluida portada en índice 0)
    for slide in prs.slides:
        for shape in slide.shapes:
            procesar_forma_reemplazos(shape, reemplazos)

    # Detectar si la diapositiva índice 0 es portada (sin gráficos)
    offset = 1 if (len(prs.slides) > 0 and len(obtener_formas_grafico_ordenadas(prs.slides[0])) == 0) else 0

    # 2. Reemplazo de Gráficos en lote único (1 sola instancia de Chromium para todas las slides)
    mapeo_slides_figs = [
        (offset + 0, [datos.get("fig_5w")]),
        (offset + 1, [datos.get("fig_dias"), datos.get("fig_pie_w")]),
        (offset + 2, [datos.get("fig_bar_gest"), datos.get("fig_bar_1ra")]),
        (offset + 3, [datos.get("fig_niv"), datos.get("fig_sent")]),
        (offset + 4, [datos.get("fig_bar_csat"), datos.get("fig_bar_ratio")]),
        (offset + 7, [datos.get("fig_niveles_wbr")]),
        (offset + 8, [datos.get("fig_pie_ext"), datos.get("fig_bar_dias_ext")]),
    ]

    with tempfile.TemporaryDirectory() as tmp_dir:
        tareas_render = []

        for idx_slide, lista_figs in mapeo_slides_figs:
            if len(prs.slides) <= idx_slide:
                continue
            slide_obj = prs.slides[idx_slide]
            formas_graf = obtener_formas_grafico_ordenadas(slide_obj)
            figs_validas = [f for f in lista_figs if f is not None]

            for j, target_shp in enumerate(formas_graf):
                if j >= len(figs_validas):
                    break
                png_file = os.path.join(tmp_dir, f"s{idx_slide}_g{j}.png")
                spec = preparar_figura_para_pptx(
                    figs_validas[j], target_shp.width, target_shp.height, png_file, fondo_oscuro=True
                )
                tareas_render.append((slide_obj, target_shp, png_file, spec))

        if tareas_render:
            kaleido.write_fig_sync([t[3] for t in tareas_render])
            for slide_obj, target_shp, png_file, _ in tareas_render:
                insertar_png_en_lugar_de_forma(slide_obj, target_shp, png_file)

    # 3. Poblar Tablas de L1 (Slide 6 -> índice offset + 5) y L3 (Slide 7 -> índice offset + 6)
    if len(prs.slides) > offset + 5:
        s_l1 = prs.slides[offset + 5]
        tablas_s6 = obtener_todas_las_tablas(s_l1)
        t_agentes_l1 = tablas_s6[0] if len(tablas_s6) >= 1 else None
        t_motivos_l1 = tablas_s6[1] if len(tablas_s6) >= 2 else None

        poblar_tabla_matriz_agentes(t_agentes_l1, datos.get("df_l1_agentes"), s_l1, y_def=1.5)
        poblar_tabla_l1_motivos(t_motivos_l1, datos.get("df_l1_motivos"), s_l1)

    if len(prs.slides) > offset + 6:
        s_l3 = prs.slides[offset + 6]
        tablas_s7 = obtener_todas_las_tablas(s_l3)
        t_agentes_l3 = tablas_s7[0] if len(tablas_s7) >= 1 else None
        t_casos_l3 = tablas_s7[1] if len(tablas_s7) >= 2 else None

        poblar_tabla_matriz_agentes(t_agentes_l3, datos.get("df_l3_agentes"), s_l3, y_def=1.5)
        poblar_tabla_l3_casos(t_casos_l3, datos.get("df_l3_neg"), s_l3)

    out = io.BytesIO()
    prs.save(out)
    out.seek(0)
    return out
