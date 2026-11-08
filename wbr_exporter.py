import io
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

COLOR_FONDO = RGBColor(11, 17, 32)      # #0b1120
COLOR_TEXTO = RGBColor(248, 250, 252)   # #f8fafc
COLOR_SUBTEXTO = RGBColor(148, 163, 184)# #94a3b8
COLOR_AZUL = RGBColor(2, 132, 199)      # #0284c7
COLOR_TURQUESA = RGBColor(56, 189, 248) # #38bdf8

def crear_presentacion_base():
    prs = Presentation()
    # Dimensiones estándar 16:9 (13.33 x 7.5 pulgadas)
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    return prs

def agregar_slide_con_fondo(prs, titulo_slide):
    blank_layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank_layout)

    # Fondo oscuro
    background = slide.background
    fill = background.fill
    fill.solid()
    fill.fore_color.rgb = COLOR_FONDO

    # Encabezado estándar WBR
    tb = slide.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(11.7), Inches(0.8))
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = titulo_slide
    p.font.size = Pt(22)
    p.font.bold = True
    p.font.color.rgb = COLOR_TEXTO
    return slide

def exportar_wbr_pptx(datos_semana, figuras_dict):
    """
    Genera el archivo PPTX en memoria bytes a partir de los datos calculados y figuras Plotly.
    """
    prs = crear_presentacion_base()
    semana_label = datos_semana.get("semana_label", "Semana")

    # SLIDE 1: Evolutivo de Volumen (5 Semanas)
    slide1 = agregar_slide_con_fondo(prs, f"WBR | Soporte & Experiencia | {semana_label} – Evolutivo")
    if "fig_5w" in figuras_dict and figuras_dict["fig_5w"]:
        img_bytes = figuras_dict["fig_5w"].to_image(format="png", width=1200, height=520, scale=2)
        slide1.shapes.add_picture(io.BytesIO(img_bytes), Inches(0.8), Inches(1.8), width=Inches(11.7))

    # SLIDE 2: Detalle Operativo Diario & Distribución Agentes
    slide2 = agregar_slide_con_fondo(prs, f"WBR | Soporte & Experiencia | {semana_label}")
    if "fig_dias" in figuras_dict and figuras_dict["fig_dias"]:
        img_dias = figuras_dict["fig_dias"].to_image(format="png", width=650, height=480, scale=2)
        slide2.shapes.add_picture(io.BytesIO(img_dias), Inches(0.8), Inches(1.8), width=Inches(6.2))
    if "fig_pie_ag" in figuras_dict and figuras_dict["fig_pie_ag"]:
        img_pie = figuras_dict["fig_pie_ag"].to_image(format="png", width=550, height=480, scale=2)
        slide2.shapes.add_picture(io.BytesIO(img_pie), Inches(7.2), Inches(1.8), width=Inches(5.3))

    # SLIDE 3: Tiempos de Gestión y SLA
    slide3 = agregar_slide_con_fondo(prs, f"WBR | Soporte & Experiencia | Evolutivo x Semana – Tiempo de Gestión y 1ra RTA")
    if "fig_bar_gest" in figuras_dict and figuras_dict["fig_bar_gest"]:
        img_g = figuras_dict["fig_bar_gest"].to_image(format="png", width=600, height=450, scale=2)
        slide3.shapes.add_picture(io.BytesIO(img_g), Inches(0.8), Inches(2.0), width=Inches(5.7))
    if "fig_bar_1ra" in figuras_dict and figuras_dict["fig_bar_1ra"]:
        img_1 = figuras_dict["fig_bar_1ra"].to_image(format="png", width=600, height=450, scale=2)
        slide3.shapes.add_picture(io.BytesIO(img_1), Inches(6.8), Inches(2.0), width=Inches(5.7))

    # SLIDE 4: Calidad y Sentimiento CSAT
    slide4 = agregar_slide_con_fondo(prs, f"WBR | Soporte & Experiencia | {semana_label} – Chats Evaluados & Sentimiento")
    if "fig_niv" in figuras_dict and figuras_dict["fig_niv"]:
        img_niv = figuras_dict["fig_niv"].to_image(format="png", width=650, height=450, scale=2)
        slide4.shapes.add_picture(io.BytesIO(img_niv), Inches(0.8), Inches(2.0), width=Inches(6.2))
    if "fig_sent" in figuras_dict and figuras_dict["fig_sent"]:
        img_sent = figuras_dict["fig_sent"].to_image(format="png", width=550, height=450, scale=2)
        slide4.shapes.add_picture(io.BytesIO(img_sent), Inches(7.2), Inches(2.0), width=Inches(5.3))

    # SLIDE 5: Horario Extendido
    slide5 = agregar_slide_con_fondo(prs, f"WBR | Soporte & Experiencia | {semana_label} – Chats Extendidos")
    if "fig_pie_ext" in figuras_dict and figuras_dict["fig_pie_ext"]:
        img_pe = figuras_dict["fig_pie_ext"].to_image(format="png", width=550, height=450, scale=2)
        slide5.shapes.add_picture(io.BytesIO(img_pe), Inches(0.8), Inches(2.0), width=Inches(5.3))
    if "fig_bar_dias_ext" in figuras_dict and figuras_dict["fig_bar_dias_ext"]:
        img_be = figuras_dict["fig_bar_dias_ext"].to_image(format="png", width=650, height=450, scale=2)
        slide5.shapes.add_picture(io.BytesIO(img_be), Inches(6.5), Inches(2.0), width=Inches(6.0))

    out_buffer = io.BytesIO()
    prs.save(out_buffer)
    out_buffer.seek(0)
    return out_buffer
