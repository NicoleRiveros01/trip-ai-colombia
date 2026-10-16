import os
import csv
import time
import re
import random
import concurrent.futures
from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from google import genai
from google.genai import types
from dotenv import load_dotenv  # <-- 1. Importa esto

# Cargamos las variables del archivo .env
load_dotenv()  # <-- 2. Ejecuta esto antes de leer la variable

app = Flask(__name__)

# CONFIGURACIÓN DE LA BASE DE DATOS LOCAL (SQLite)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///trip_ai.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# CONFIGURACIÓN DEL CLIENTE DE GEMINI (Segura mediante os.environ.get)
api_key_val = os.environ.get("GEMINI_API_KEY")  # <-- 3. Lee exactamente el nombre del .env
client = genai.Client(api_key=api_key_val)

# --- CONSTANTE DE TRANSPORTE URBANO BOGOTÁ ---
PASAJE_TRANSMILENIO_IDA_VUELTA = 7100

# --- DEFINICIÓN DE LA TABLA EN LA BASE DE DATOS ---
class Destino(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    municipio = db.Column(db.String(50), nullable=False)
    corredor = db.Column(db.String(100), nullable=False)
    tipo_plan = db.Column(db.String(50), nullable=False)
    precio_transporte = db.Column(db.String(50), nullable=False)
    costo_actividades = db.Column(db.Integer, nullable=False)
    detalle_actividad = db.Column(db.String(200), nullable=False)

# --- CARGAR EL CSV AUTOMÁTICAMENTE A LA BASE DE DATOS AL INICIAR ---
with app.app_context():
    db.drop_all()  # Limpia cualquier tabla vieja
    db.create_all()  # Crea la estructura limpia en SQLite
    
    # Obtenemos la ruta absoluta de la carpeta donde está este app.py
    basedir = os.path.abspath(os.path.dirname(__file__))
    csv_path = os.path.join(basedir, 'Destinos.csv')
    
    if os.path.exists(csv_path):
        with open(csv_path, mode='r', encoding='utf-8-sig') as archivo_csv:
            lector = csv.DictReader(archivo_csv)
            contador = 0
            for fila in lector:
                if 'municipio' in fila and fila['municipio']:
                    nuevo_destino = Destino(
                        municipio=fila['municipio'].strip(),
                        corredor=fila['corredor'].strip(),
                        tipo_plan=fila['tipo_plan'].strip(),
                        precio_transporte=fila['precio_transporte'].strip(),
                        costo_actividades=int(fila['costo_actividades']),
                        detalle_actividad=fila['detalle_actividad'].strip()
                    )
                    db.session.add(nuevo_destino)
                    contador += 1
            db.session.commit()
        print(f"¡Base de datos cargada exitosamente con {contador} registros desde Destinos.csv!")
    else:
        print(f"⚠️ ERROR: No se encontró el archivo en la ruta absoluta: {csv_path}")

# --- PLAN B DE RESPALDO (LOCAL, INTELIGENTE Y UNIVERSAL) ---
def generar_itinerario_desde_bd(texto_usuario):
    texto_lower = texto_usuario.lower()
    texto_limpio = texto_lower.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    
    todos_los_destinos = Destino.query.all()
    if not todos_los_destinos:
        return "⚠️ No hay destinos cargados en la base de datos local."

    # 1. DÍAS
    dias_solicitados = 2  
    match_dias = re.search(r'\b(\d+)\s*(?:dias|día|días|dia)\b', texto_lower)
    num_dias_detectado = None
    if match_dias:
        dias_solicitados = int(match_dias.group(1))
        num_dias_detectado = dias_solicitados

    # 2. PERSONAS
    personas = 1
    match_pers = re.search(r'\b(\d+)\s*(?:personas|persona|viajeros|integrantes|adultos|pax)\b', texto_lower)
    num_pers_detectado = None
    if match_pers:
        personas = int(match_pers.group(1))
        num_pers_detectado = personas

    # 3. PRESUPUESTO
    presupuesto_usuario = 0
    todos_los_numeros = []
    for match in re.finditer(r'\b(\d+[\'\.\,]?\d+)\b|\b(\d+)\b', texto_usuario):
        num_str = match.group(0).replace(".", "").replace(",", "").replace("'", "")
        if num_str.isdigit():
            val = int(num_str)
            todos_los_numeros.append(val)

    candidatos_presupuesto = []
    for val in todos_los_numeros:
        if val == num_dias_detectado and val < 10:
            continue
        if val == num_pers_detectado and val < 20:
            continue
        if val > 0:
            candidatos_presupuesto.append(val)

    if candidatos_presupuesto:
        presupuesto_usuario = max(candidatos_presupuesto)

    # 4. TIPOS DE PLAN
    tipos_plan_disponibles = set(d.tipo_plan.lower() for d in todos_los_destinos)
    planes_mencionados = []
    for plan in tipos_plan_disponibles:
        plan_limpio = plan.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
        if plan_limpio in texto_limpio:
            planes_mencionados.append(plan)

    # 5. MUNICIPIOS
    municipios_en_bd = set(d.municipio.lower() for d in todos_los_destinos)
    municipios_mencionados = []
    for m in municipios_en_bd:
        m_limpio = m.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
        if re.search(r'\b' + re.escape(m_limpio) + r'\b', texto_limpio):
            if m not in municipios_mencionados:
                municipios_mencionados.append(m)

    # 6. CONSTRUCCIÓN DE RUTA INTELIGENTE (Con Clúster Geográfico por Corredor Vial)
    destinos_seleccionados = []
    nota_informativa = ""

    if municipios_mencionados:
        primer_mun = municipios_mencionados[0]
        filas_ref = [d for d in todos_los_destinos if d.municipio.lower() == primer_mun]
        corredor_principal = filas_ref[0].corredor if filas_ref else todos_los_destinos[0].corredor
        
        municipios_validos = []
        for m in municipios_mencionados:
            f_m = [d for d in todos_los_destinos if d.municipio.lower() == m]
            if f_m and f_m[0].corredor == corredor_principal:
                if m not in municipios_validos:
                    municipios_validos.append(m)
        
        if not municipios_validos:
            municipios_validos = [primer_mun]

        for m_name in municipios_validos:
            filas_mun = [d for d in todos_los_destinos if d.municipio.lower() == m_name]
            if filas_mun:
                if planes_mencionados:
                    match_plan = [f for f in filas_mun if f.tipo_plan.lower() in planes_mencionados]
                    destinos_seleccionados.append(match_plan[0] if match_plan else random.choice(filas_mun))
                else:
                    destinos_seleccionados.append(random.choice(filas_mun))

        if len(destinos_seleccionados) < dias_solicitados:
            complementarios = [d for d in todos_los_destinos if d.corredor == corredor_principal and d not in destinos_seleccionados]
            complementarios.sort(key=lambda x: x.costo_actividades)
            while len(destinos_seleccionados) < dias_solicitados and complementarios:
                destinos_seleccionados.append(complementarios.pop(0))

        nombres_str = ", ".join([m.capitalize() for m in municipios_validos])
        nota_informativa = f"💡 **Nota del sistema:** Clúster de transporte optimizado por el corredor **{corredor_principal}** pasando por **{nombres_str}**.\n\n"

    elif planes_mencionados:
        filas_plan = [d for d in todos_los_destinos if d.tipo_plan.lower() in planes_mencionados]
        filas_plan.sort(key=lambda x: x.costo_actividades)
        for f in filas_plan:
            if len(destinos_seleccionados) < dias_solicitados:
                destinos_seleccionados.append(f)

    destinos_disponibles = [d for d in todos_los_destinos if d not in destinos_seleccionados]
    destinos_disponibles.sort(key=lambda x: x.costo_actividades)
    while len(destinos_seleccionados) < dias_solicitados and destinos_disponibles:
        barato_d = destinos_disponibles.pop(0)
        destinos_seleccionados.append(barato_d)

    destinos_seleccionados = destinos_seleccionados[:dias_solicitados]

    # 7. CÁLCULO FINANCIERO DETALLADO
    costo_urbano_total = PASAJE_TRANSMILENIO_IDA_VUELTA * personas
    costo_intermunicipal_total = 0
    costo_actividades_total = 0

    valores_transporte_destinos = []
    for d in destinos_seleccionados:
        precios_nums = [int(p) for p in re.findall(r'\d+', d.precio_transporte)]
        val_transporte = sum(precios_nums) // len(precios_nums) if precios_nums else 10000
        valores_transporte_destinos.append(val_transporte)
        
        subtotal_actividad_mun = d.costo_actividades * personas
        costo_actividades_total += subtotal_actividad_mun

    if valores_transporte_destinos:
        transporte_ida = sum(valores_transporte_destinos)
        transporte_vuelta = valores_transporte_destinos[-1]
        costo_intermunicipal_total = (transporte_ida + transporte_vuelta) * personas

    tiene_plan_gastronomico = any('gastronómico' in d.tipo_plan.lower() for d in destinos_seleccionados)
    tarifa_alimentacion_diaria = 35000 if tiene_plan_gastronomico else 45000
    costo_alimentacion_total = tarifa_alimentacion_diaria * dias_solicitados * personas

    noches = max(0, dias_solicitados - 1)
    costo_hospedaje_total = 50000 * noches * personas

    gasto_total = costo_urbano_total + costo_intermunicipal_total + costo_actividades_total + costo_alimentacion_total + costo_hospedaje_total
    gasto_por_persona = gasto_total // personas

    # 8. RESPUESTA ESTRUCTURADA
    itinerario_respaldo = (
        f"🛡️ **[Modo Respaldo Local Inteligente TRIP-AI]**\n"
        f"{nota_informativa}"
        f"Ruta diseñada para **{personas} persona(s)** con duración de **{dias_solicitados} día(s)**:\n\n"
    )

    for i, d in enumerate(destinos_seleccionados, 1):
        itinerario_respaldo += (
            f"### **Día {i}: {d.municipio}**\n"
            f"* **Corredor Vial:** {d.corredor}\n"
            f"* **Tipo de Plan:** {d.tipo_plan}\n"
            f"* **Actividad Principal:** {d.detalle_actividad}\n"
            f"* **Costo Actividad ({personas} pers.):** ${d.costo_actividades * personas:,} COP\n\n"
        )

    itinerario_respaldo += (
        f"---\n"
        f"### **💰 Desglose Financiero (Grupal e Individual)**\n"
        f"* **Transporte Urbano (TransMilenio/SITP R/T):** ${costo_urbano_total:,} COP *(${costo_urbano_total // personas:,} c/u)*\n"
        f"* **Transporte Intermunicipal (Ida y Retorno):** ${costo_intermunicipal_total:,} COP *(${costo_intermunicipal_total // personas:,} c/u)*\n"
        f"* **Total Actividades:** ${costo_actividades_total:,} COP *(${costo_actividades_total // personas:,} c/u)*\n"
        f"* **Alimentación Base Estudiantil ({dias_solicitados} día(s) - 2 comidas aprox.):** ${costo_alimentacion_total:,} COP *(${costo_alimentacion_total // personas:,} c/u)*\n"
    )

    if noches > 0:
        itinerario_respaldo += f"* **Hospedaje Económico Mochilero ({noches} noche(s) a $50k/noche):** ${costo_hospedaje_total:,} COP *(${costo_hospedaje_total // personas:,} c/u)*\n\n"
    else:
        itinerario_respaldo += f"* **Hospedaje (Excursión de 1 día - Sin pernoctación):** $0 COP *(0 c/u)*\n\n"

    itinerario_respaldo += f"👉 **GASTO TOTAL ESTIMADO:** **${gasto_total:,} COP** *(${gasto_por_persona:,} COP por persona)*\n"

    if presupuesto_usuario > 0:
        diferencia = presupuesto_usuario - gasto_total
        diferencia_por_persona = diferencia // personas
        presupuesto_por_persona = presupuesto_usuario // personas
        
        itinerario_respaldo += f"* **Presupuesto aportado:** ${presupuesto_usuario:,} COP *(${presupuesto_por_persona:,} COP por persona)*\n"
        if diferencia >= 0:
            itinerario_respaldo += f"✅ **¡Presupuesto cumplido!** Te sobran: **${diferencia:,} COP** *(${diferencia_por_persona:,} COP c/u)* para imprevistos.\n"
        else:
            itinerario_respaldo += (
                f"⚠️ **Presupuesto insuficiente:** Faltan ${abs(diferencia):,} COP en total "
                f"*(${abs(diferencia_por_persona):,} COP por persona)* para cubrir la logística y subsistencia del plan de {dias_solicitados} días.\n"
            )
            
            # --- MAGIA: PLAN ALTERNATIVO VIABLE (PASADÍA AUTOMÁTICO) ---
            # Buscamos el destino más barato del corredor o de toda la base para ofrecer un pasadía de 1 día viable
            destino_pasadia = min(todos_los_destinos, key=lambda x: x.costo_actividades)
            p_nums = [int(p) for p in re.findall(r'\d+', destino_pasadia.precio_transporte)]
            v_trans = (sum(p_nums) // len(p_nums) if p_nums else 10000) * 2 # Ida y vuelta
            costo_trans_pasadia = (PASAJE_TRANSMILENIO_IDA_VUELTA + v_trans) * personas
            costo_act_pasadia = destino_pasadia.costo_actividades * personas
            costo_alim_pasadia = 45000 * personas # 1 día de alimentación
            
            gasto_pasadia_total = costo_trans_pasadia + costo_act_pasadia + costo_alim_pasadia
            remanente_pasadia = presupuesto_usuario - gasto_pasadia_total
            
            if remanente_pasadia >= 0:
                itinerario_respaldo += (
                    f"\n🔄 **¡Pero no te quedes en casa! Plan Alternativo Viable (Pasadía de 1 Día):**\n"
                    f"Para ajustarnos estrictamente a tus **${presupuesto_usuario:,} COP**, TRIP-AI recalculó una ruta optimizada:\n"
                    f"- **Destino:** {destino_pasadia.municipio.capitalize()} (Corredor: {destino_pasadia.corredor})\n"
                    f"- **Actividad:** {destino_pasadia.detalle_actividad} (${destino_pasadia.costo_actividades * personas:,} COP)\n"
                    f"- **Transporte Total (Urbano + Intermunicipal R/T):** ${costo_trans_pasadia:,} COP\n"
                    f"- **Alimentación (2 comidas base):** ${costo_alim_pasadia:,} COP\n"
                    f"- 💰 **GASTO TOTAL REAL:** **${gasto_pasadia_total:,} COP**\n"
                    f"- ✅ **¡Presupuesto Cumplido!** Te sobran **${remanente_pasadia:,} COP** para hidratación y snacks.\n"
                )
            else:
                itinerario_respaldo += (
                    f"\n💡 *Consejo TRIP-AI:* Tu presupuesto es muy ajustado. Te recomendamos buscar un pasadía económico o llevar provisiones de casa para reducir costos de alimentación.\n"
                )
    else:
        itinerario_respaldo += f"*(Cálculo basado estrictamente in tarifas oficiales).* \n"

    itinerario_respaldo += (
        f"\n📌 *Nota financiera: Los montos presentados son **valores estimados y aproximados** de referencia bajo estándar estudiantil/mochilero. "
        f"El rubro de alimentación contempla un estimado de dos comidas base por persona. "
        f"El costo final puede subir o bajar dependiendo de las decisiones del viajero (por ejemplo, si llevan provisiones de casa o deciden prescindir de algún consumo).* \n"
    )

    itinerario_respaldo += f"\n**💡 Nota del sistema:** Generado mediante el respaldo local inteligente de TRIP-AI."
    
    return itinerario_respaldo

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/generar", methods=["POST"])
def generar():
    data = request.json
    texto_usuario = data.get("textoUsuario", "")

    texto_lower = texto_usuario.lower()
    todos_los_destinos = Destino.query.all()
    destinos_coincidentes = []

    for d in todos_los_destinos:
        if (d.municipio.lower() in texto_lower or 
            d.tipo_plan.lower() in texto_lower or 
            any(palabra in texto_lower for palabra in d.detalle_actividad.lower().split())):
            destinos_coincidentes.append(d)

    if not destinos_coincidentes:
        destinos_coincidentes = todos_los_destinos[:4]

    info_bd_texto = "DATOS OFICIALES DISPONIBLES PARA ARMAR EL VIAJE:\n"
    for d in destinos_coincidentes:
        info_bd_texto += (
            f"- Municipio: {d.municipio} | Corredor: {d.corredor} | Plan: {d.tipo_plan} | "
            f"Transporte Intermunicipal: ${d.precio_transporte} COP | Actividad: {d.detalle_actividad} (${d.costo_actividades:,} COP)\n"
        )

    system_instruction = (
   "Eres el guía experto y director creativo de TRIP-AI Colombia, especializado en turismo inteligente, "
        "sostenible y en transporte público por el departamento de Cundinamarca saliendo desde Bogotá. "
        "Tu tono debe ser profesional, claro, amigable y estructurado, explicando con orden cada detalle.\n\n"
        
        "REGLAS CRÍTICAS DE PLANIFICACIÓN QUE DEBES CUMPLIR ESTRICTAMENTE:\n"
        "1. ADAPTACIÓN DE DÍAS Y MUNICIPIOS: Lee con atención cuántos días y qué municipios o tipos de plan pide el usuario. "
        "Si el usuario pide varios días en un mismo municipio (ej. Suesca), usa las distintas actividades registradas en la base de datos para cada día sin repetir. "
        "Si el usuario nombra varios municipios, distribuye los días entre ellos.\n"
        "2. CLÚSTER GEOGRÁFICO POR CORREDOR VIAL: Agrupa los destinos estrictamente por el mismo corredor vial de transporte público (nodo de salida desde Bogotá). "
        "Si el usuario nombra municipios de corredores distintos e incompatibles (ej. Calle 80 y Calle 13), prioriza el corredor del primer municipio mencionado "
        "y ajusta el itinerario para evitar trayectos ilógicos de retorno a Bogotá.\n"
        "3. RELLENO DE CORREDOR: Si un municipio no tiene suficientes actividades para cubrir todos los días solicitados, adviértelo con una "
        "nota del sistema al inicio e integra destinos complementarios que pertenezcan estrictamente al mismo corredor vial.\n"
        "4. FILTRADO DE PLANES Y EXCEPCIÓN GASTRONÓMICA: Si el usuario especifica una categoría (ej. naturaleza, cultural, gastronómico), "
        "filtra y selecciona preferiblemente actividades que coincidan con dicho plan. "
        "**Regla especial gastronómica:** Si el plan o la actividad del día es de tipo *Gastronómico* (donde el usuario va a comer como atractivo principal, ej. Andrés Carne de Res o Cabaña Alpina), ten en cuenta que la comida principal ya está integrada en el costo de la actividad, por lo que el rubro de alimentación general de ese día se reduce o se ajusta a $35,000 COP para evitar duplicidades.\n"
        "5. TRANSPORTE Y COSTOS OFICIALES (¡NO INVENTES PRECIOS DE RUTA!): Utiliza exclusivamente los datos de costos de transporte intermunicipal, "
        "precios de actividades y corredores provistos en la base de datos de abajo. Incluye siempre el pasaje urbano de TransMilenio / SITP ($7,100 COP por persona ida y vuelta).\n"
        "6. CÁLCULO DETALLADO Y FLEXIBLE DE SUBSISTENCIA (ESTÁNDAR ESTUDIANTIL/MOCHILERO):\n"
        "   - **Alimentación Base:** Calcula un estimado de referencia de $45,000 COP diarios por persona (enfocado en 2 comidas base), multiplicado por el total de días y personas (ajustando a $35,000 los días con planes gastronómicos).\n"
        "   - **Hospedaje Económico:** Calcula un estimado de $50,000 COP por noche por persona (para hostales o camping organizado enfocado en estudiantes), **únicamente si el viaje es de 2 o más días** (días >= 2). Si el viaje es de solo 1 día, el costo de hospedaje es $0 COP.\n"
        "7. CÁLCULO FINANCIERO Y ANÁLISIS DE PRESUPUESTO:\n"
        "   - Suma todos los rubros por separado: Transporte Urbano + Transporte Intermunicipal + Actividades + Alimentación Estimada + Hospedaje Estimado = **Gasto Total Estimado (Logística + Subsistencia)**.\n"
        "   - Muestra los resultados financieros tanto de forma **grupal** como **individual por persona** (c/u) para facilitar la gestión del presupuesto (ideal para grupos de estudiantes).\n"
        "   - Compara este Gasto Total con el presupuesto aportado. Si alcanza, indica el remanente. Si no alcanza, especifica claramente el déficit exacto (cuánto falta tanto en total como por persona) y aclara que los valores son referencias aproximadas sujetas a las decisiones del viajero.\n"
        "8. PRIORIZACIÓN ECONÓMICA EN BÚSQUEDAS ABIERTAS: Si el usuario **NO** menciona un municipio o destino específico en su mensaje, pero da un presupuesto o pide un tipo de plan general (ej. 'plan ecológico económico'), debes priorizar y seleccionar preferiblemente las **actividades más económicas** de la base de datos para intentar ajustarte al presupuesto.\n"
        "9. PLAN ALTERNATIVO VIABLE EN DÉFICIT CRÍTICO: Si el presupuesto aportado por el usuario es sustancialmente inferior al gasto total estimado de un viaje largo (ej. pedir 3 días con $150,000 COP), "
        "no te limites a reportar la deuda. Asume el rol de asesor de viajes proactivo e incluye obligatoriamente una sección titulada '🔄 Plan Alternativo Viable (Pasadía de 1 Día)', "
        "recalculando una alternativa optimizada y económica en un destino del mismo corredor que sí se ajuste exactamente al presupuesto del usuario, demostrando un diseño tolerante a errores y orientado a la conversión.\n\n"
        
        "ESTRUCTURA DE RESPUESTA REQUERIDA:\n"
        "- Utiliza el encabezado oficial: 🤖 **[Modo IA Experta TRIP-AI]**\n"
        "- Si aplica por falta de actividades o ajuste de corredor, incluye una breve y clara **Nota del sistema** al inicio.\n"
        "- Presenta la ruta organizada día por día con viñetas limpias.\n"
        "- Agrega la sección de **Desglose Financiero y Presupuesto** detallando explícitamente por separado (con su respectivo valor grupal e individual c/u): Transporte Urbano, Transporte Intermunicipal, Actividades, Alimentación Estimada (con su respectiva aclaración de referencia y ajuste gastronómico), Hospedaje Estimado (aclarando si aplica o es $0 por ser de 1 día), y la Validación Final de Presupuesto.\n"
        "- Si hay déficit crítico, incluye la sección del **Plan Alternativo Viable (Pasadía de 1 Día)**.\n"
        "- Cierra con la nota financiera: *'📌 Nota financiera: Los montos presentados son valores estimados y aproximados de referencia bajo estándar estudiantil/mochilero...'*\n\n"
        
        f"DATOS OFICIALES DE LA BASE DE DATOS:\n{info_bd_texto}"
    )

    def llamar_ia():
        response = client.models.generate_content(
            model='gemini-3.8-flash',
            contents=texto_usuario,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.4,
                max_output_tokens=8192
            ),
        )
        return response.text

    itinerario = ""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(llamar_ia)
        try:
            itinerario = future.result(timeout=120)
            print("✨ ¡IA respondió con éxito!")
        except Exception as e:
            print(f"⚡ [CAMBIO INMEDIATO A RESPALDO LOCAL]: {str(e)}")
            itinerario = generar_itinerario_desde_bd(texto_usuario)

    return jsonify({"itinerario": itinerario})

if __name__ == "__main__":
    app.run(debug=True)