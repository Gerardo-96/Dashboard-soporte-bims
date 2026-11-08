import io
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.chart.data import CategoryChartData

# Paleta corporativa
C_FONDO = RGBColor(15, 23, 42)       # #0f172a
C_AZUL_OSC = RGBColor(2, 132, 199)   # #0284c7
C_AZUL_CLA = RGBColor(56, 189, 248)  # #38bdf8
C_VERDE = RGBColor(52, 211, 153)     # #34d399
C_BLANCO = RGBColor(248, 250, 252)   # #f8fafc
C_GRIS = RGBColor(148, 163, 184)     # #94a3b8

def crear_presentacion():
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    return prs

def agregar_slide_base(prs, titulo):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    # Fondo sólido oscuro
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = C_FONDO

    # Encabezado estándar
    tb = slide.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8))
    tf = tb.text_frame
    p = tf.paragraphs[0]
    p.text = titulo
    p.font.size = Pt(20)
    p.font.bold = True
    p.font.color.rgb = C_BLANCO
    return slide

def agregar_tarjeta_metrica(slide, x, y, w, h, titulo, valor, sub=""):
    shape = slide.shapes.add_shape(1, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor(30, 41, 59)
    shape.line.color.rgb = C_AZUL_CLA
    shape.line.width = Pt(1.5)

    tf = shape.text_frame
    tf.word_wrap = True
    
    p0 = tf.paragraphs[0]
    p0.text = titulo.upper()
    p0.alignment = PP_ALIGN.CENTER
    p0.font.size = Pt(10)
    p0.font.color.rgb = C_GRIS
    p0.font.bold = True

    p1 = tf.add_paragraph()
    p1.text = str(valor)
    p1.alignment = PP_ALIGN.CENTER
    p1.font.size = Pt(26)
    p1.font.color.rgb = C_AZUL_CLA
    p1.font.bold = True

    if sub:
        p2 = tf.add_paragraph()
        p2.text = str(sub)
        p2.alignment = PP_ALIGN.CENTER
        p2.font.size = Pt(9)
        p2.font.color.rgb = C_GRIS

def exportar_wbr_pptx(datos):
    prs = crear_presentacion()
    sem_label = datos.get("semana_label", "Semana Actual")

    # =========================================================================
    # SLIDE 1: Evolutivo de Volumen (5 Semanas)
    # =========================================================================
    s1 = agregar_slide_base(prs, f"WBR | Soporte & Experiencia — Evolutivo de Volumen ({sem_label})")
    agregar_tarjeta_metrica(s1, 0.8, 1.4, 5.7, 1.3, "Chats Recibidos (5 Semanas)", f"{datos.get('tot_5w_rec', 0):,}")
    agregar_tarjeta_metrica(s1, 6.8, 1.4, 5.7, 1.3, "Promedio % Chats Cerrados (5 Semanas)", f"{datos.get('pct_5w_cer', 0)}%", f"{datos.get('tot_5w_cer', 0):,} cerrados")

    df_5w = datos.get("df_5w")
    if df_5w is not None and not df_5w.empty:
        cdata_1 = CategoryChartData()
        cdata_1.categories = df_5w["Semana"].tolist()
        cdata_1.add_series("Recibidos", tuple(df_5w["Recibidos"]))
        cdata_1.add_series("Cerrados", tuple(df_5w["Cerrados"]))

        chart1 = s1.shapes.add_chart(
            XL_CHART_TYPE.COLUMN_CLUSTERED,
            Inches(0.8), Inches(2.9), Inches(11.7), Inches(4.1), cdata_1
        ).chart
        chart1.has_legend = True
        chart1.legend.position = XL_LEGEND_POSITION.TOP
        chart1.legend.include_in_layout = False

    # =========================================================================
    # SLIDE 2: Detalle Operativo Semanal
    # =========================================================================
    s2 = agregar_slide_base(prs, f"WBR | Detalle Operativo Semanal — {sem_label}")
    agregar_tarjeta_metrica(s2, 0.8, 1.3, 5.7, 1.2, "Chats Recibidos", f"{datos.get('tot_sem_rec', 0):,}")
    agregar_tarjeta_metrica(s2, 6.8, 1.3, 5.7, 1.2, "Chats Cerrados y Efectividad", f"{datos.get('tot_sem_cer', 0):,}", f"{datos.get('pct_sem_cer', 0)}% cerrados")

    df_dias = datos.get("df_dias")
    if df_dias is not None and not df_dias.empty:
        cdata_2 = CategoryChartData()
        cdata_2.categories = df_dias["Fecha"].tolist()
        cdata_2.add_series("Recibidos", tuple(df_dias["Recibidos"]))
        cdata_2.add_series("Cerrados", tuple(df_dias["Cerrados"]))
        chart2 = s2.shapes.add_chart(
            XL_CHART_TYPE.COLUMN_CLUSTERED,
            Inches(0.8), Inches(2.7), Inches(6.5), Inches(4.3), cdata_2
        ).chart
        chart2.has_legend = True
        chart2.legend.position = XL_LEGEND_POSITION.TOP

    df_agentes = datos.get("df_agentes")
    if df_agentes is not None and not df_agentes.empty:
        cdata_pie = CategoryChartData()
        cdata_pie.categories = df_agentes["Agente"].tolist()
        cdata_pie.add_series("Cerrados", tuple(df_agentes["Cerrados"]))
        chart_pie = s2.shapes.add_chart(
            XL_CHART_TYPE.DOUGHNUT,
            Inches(7.6), Inches(2.7), Inches(4.9), Inches(4.3), cdata_pie
        ).chart
        chart_pie.has_legend = True
        chart_pie.legend.position = XL_LEGEND_POSITION.RIGHT

    # =========================================================================
    # SLIDE 3: Tiempos de Gestión y SLA
    # =========================================================================
    s3 = agregar_slide_base(prs, f"WBR | Tiempos de Gestión y SLA — {sem_label}")
    agregar_tarjeta_metrica(s3, 0.8, 1.3, 5.7, 1.2, "Tiempo Promedio Gestión", f"{datos.get('foco_gest', 0)} min")
    agregar_tarjeta_metrica(s3, 6.8, 1.3, 5.7, 1.2, "Tiempo Prom. 1ra Respuesta", f"{datos.get('foco_1ra', 0)} min")

    df_tiempos = datos.get("df_tiempos")
    if df_tiempos is not None and not df_tiempos.empty:
        # Gráfico Gestión
        cdata_tg = CategoryChartData()
        cdata_tg.categories = df_tiempos["Semana"].tolist()
        cdata_tg.add_series("Gestión (min)", tuple(df_tiempos["Prom_Gestion"]))
        s3.shapes.add_chart(
            XL_CHART_TYPE.COLUMN_CLUSTERED,
            Inches(0.8), Inches(2.7), Inches(5.7), Inches(4.2), cdata_tg
        )

        # Gráfico 1ra Respuesta
        cdata_t1 = CategoryChartData()
        cdata_t1.categories = df_tiempos["Semana"].tolist()
        cdata_t1.add_series("1ra Respuesta (min)", tuple(df_tiempos["Prom_1ra"]))
        s3.shapes.add_chart(
            XL_CHART_TYPE.COLUMN_CLUSTERED,
            Inches(6.8), Inches(2.7), Inches(5.7), Inches(4.2), cdata_t1
        )

    # =========================================================================
    # SLIDE 4: Horario Extendido
    # =========================================================================
    s4 = agregar_slide_base(prs, f"WBR | Operación en Horario Extendido — {sem_label}")
    agregar_tarjeta_metrica(s4, 0.8, 1.3, 5.7, 1.2, "Total Chats Extendido", f"{datos.get('tot_ext', 0)}", f"Domingo: {datos.get('tot_domingo', 0)}")
    agregar_tarjeta_metrica(s4, 6.8, 1.3, 5.7, 1.2, "Efectividad Cierre", f"{datos.get('pct_ext_cerrados', 0)}%", f"{datos.get('tot_ext_cerrados', 0)} cerrados")

    df_dias_ext = datos.get("df_dias_ext")
    if df_dias_ext is not None and not df_dias_ext.empty:
        cdata_dext = CategoryChartData()
        cdata_dext.categories = df_dias_ext["Día"].tolist()
        cdata_dext.add_series("Chats", tuple(df_dias_ext["Cantidad"]))
        s4.shapes.add_chart(
            XL_CHART_TYPE.BAR_CLUSTERED,
            Inches(0.8), Inches(2.7), Inches(11.7), Inches(4.2), cdata_dext
        )

    out = io.BytesIO()
    prs.save(out)
    out.seek(0)
    return out
