#Importamos la clase FLask para crear una aplicacion web
#render_template para mostrar paginas HTML
#request para recibir datos del formulario
#redirect para redireccionar a paginas HTML
#url_form para generar URL's
#Response para enviar video en streaming
from flask import Flask, render_template, request, redirect, url_for, Response, jsonify, send_file
import threading, uuid, time, csv, io
from datetime import datetime, date, timedelta
import base64
import binascii
import re
#Libreria para conectarse al motor de base de datos Mysql
import mysql.connector
#Libreria OpenCV para visión_artificial y reconocimiento facial
import cv2
#Libreria PIL permite abrir y manipular imagenes
from PIL import Image
#Libreria para trabajar con matrices y arreglos
import numpy as np
#Libreria OS para manejar archivos y directorios del sistema
import os

#Creamos la aplicacion Flask
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATASET_DIR = os.path.join(BASE_DIR, "dataset")
RESOURCES_DIR = os.path.join(BASE_DIR, "resources")
CLASSIFIER_PATH = os.path.join(BASE_DIR, "classifier.xml")

# Estado compartido de vigilancia y configuracion en memoria.
estado_camara = {
    "activa": False,
    "rostros_ahora": 0,
    "fps": 0,
    "umbral": 70,
    "espera_minutos": 2,
}
ultima_deteccion_por_persona = {}
detecciones_recientes = []
camara_actual = None
camara_bloqueo = threading.Lock()

@app.errorhandler(mysql.connector.Error)
def handle_database_error(exc):
    app.logger.exception("Error de base de datos en %s: %s", request.path, exc)
    if request.path.startswith('/api/'):
        return jsonify({"error": "Base de datos no disponible."}), 503
    return render_template('db_error.html'), 503

############################ Conexion a la base de datos ############################

#Conexión a Mysql
mydb = None
mycursor = None


def get_db_cursor():
    global mydb, mycursor

    if mycursor is not None and mydb is not None:
        try:
            if mydb.is_connected():
                return mycursor
        except Exception:
            pass

    try:
        mydb = mysql.connector.connect(
            host="localhost",
            user="root",
            password="",
            database="reconocimiento_facial",
            connection_timeout=2,
        )
        mycursor = mydb.cursor()
        return mycursor
    except mysql.connector.Error as exc:
        mydb = None
        mycursor = None
        app.logger.warning("No se pudo conectar a MySQL: %s", exc)
        return None

############################ FUNCION PARA GENERAR DATASET ############################

#Esta función captura imágenes desde la camara y las guarda para entrenar 
#el reconocimiento facial

def generate_dataset(nbr):
    #Cargamos el clasificador HaarCascade para detectar rostros de manera frontal
    face_classifier_path = os.path.join(RESOURCES_DIR, "haarcascade_frontalface_default.xml")
    face_classifier = cv2.CascadeClassifier(face_classifier_path)

    #Creamos una funcion face_cropped()
    def face_cropped(img):
        
        #Convertimos la imagen a escala de grises
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        #Buscamos rostros en la imagen
        faces = face_classifier.detectMultiScale(gray, 1.3, 5)

        #Si no encuentra rostros
        if len(faces) == 0:
            return None
        
        #Vamos recorrer los rostros detectados
        for (x, y, w, h) in faces:

            #Vamos a recortar la región donde está el rostro detectado
            cropped_face = img[y:y+h, x:x+w]

            #Devolvemos el rostro recortado
            return cropped_face
        
    #Abrimos la cámara

    #Abrimos la webcam principal
    cap = cv2.VideoCapture(0)

    cursor = get_db_cursor()

    #Obtenemos el ultimo ID de la imagen registrada
    if cursor is not None:
        cursor.execute("select ifnull(max(img_id), 0) from img_dataset")

        #Recupera la fila de la consulta
        row = cursor.fetchone()

        #Recuperamos el ultimo ID registrado
        lastid = row[0] if row else 0
    else:
        lastid = 0

    #Inicializamos las variables
    img_id = lastid

    #Para el dataset se tomarán 100 fotos
    max_imgid =  img_id + 100

    #Contador visual
    count_img = 0

    #Capturamos las imagenes
    while True:

        ret, img = cap.read()

        if not ret:
            continue

        cropped_face = face_cropped(img)

        if cropped_face is None:
            continue

        count_img += 1
        img_id += 1

        face = cv2.resize(cropped_face, (200, 200))
        face = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)

        file_name_path = os.path.join(DATASET_DIR, nbr + "." + str(img_id) + ".jpg")

        cv2.imwrite(file_name_path, face)

        cv2.putText(
            face,
            str(count_img),
            (50, 50),
            cv2.FONT_HERSHEY_COMPLEX,
            1,
            (255, 255, 255),
            2
        )

        if cursor is not None:
            cursor.execute(
                """
                INSERT INTO img_dataset
                (img_id, img_person)
                VALUES (%s, %s)
                """,
                (img_id, nbr)
            )

            mydb.commit()

        frame = cv2.imencode('.jpg', face)[1].tobytes()

        yield (
            b'--frame\r\n'
            b'Content-Type: image/jpeg\r\n\r\n' +
            frame +
            b'\r\n'
        )

        if count_img >= 100:
            break

    cap.release()
    cv2.destroyAllWindows()

# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< Train Classifier >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>



train_jobs = {}

def obtener_fotos_entrenables(nbr=None):
    """Devuelve fotos del dataset con el formato de nombre usado por el modelo actual."""
    fotos = []
    if not os.path.isdir(DATASET_DIR):
        return fotos

    for nombre in os.listdir(DATASET_DIR):
        raiz, extension = os.path.splitext(nombre)
        coincidencia = re.fullmatch(r"(\d{1,3})\.(\d+)", raiz)
        if extension.lower() != '.jpg' or coincidencia is None:
            continue

        codigo_persona, id_imagen = coincidencia.groups()
        if nbr is not None and codigo_persona != nbr:
            continue
        ruta = os.path.join(DATASET_DIR, nombre)
        if os.path.isfile(ruta):
            fotos.append((int(id_imagen), ruta))

    return sorted(fotos, key=lambda foto: foto[0])


def background_train(nbr, job_id, fotos):
    """Entrena LBPH con las fotos guardadas sin volver a activar la cámara."""
    ruta_temporal = os.path.join(BASE_DIR, f"classifier.{job_id}.tmp.xml")
    try:
        caras = []
        etiquetas = []
        total = len(fotos)

        for indice, (img_id, ruta) in enumerate(fotos, start=1):
            with Image.open(ruta) as imagen:
                cara = np.array(imagen.convert("L"), dtype="uint8")
            caras.append(cara)
            etiquetas.append(int(img_id))
            train_jobs[job_id]["progress"] = 10 + int(55 * indice / total)

        reconocedor = cv2.face.LBPHFaceRecognizer_create()
        train_jobs[job_id]["progress"] = 70
        reconocedor.train(caras, np.array(etiquetas))
        reconocedor.write(ruta_temporal)
        os.replace(ruta_temporal, CLASSIFIER_PATH)
        train_jobs[job_id].update({"status": "finished", "progress": 100})
    except (OSError, ValueError, cv2.error) as exc:
        try:
            if os.path.exists(ruta_temporal):
                os.remove(ruta_temporal)
        except OSError as cleanup_exc:
            app.logger.warning(
                "No se pudo limpiar el archivo temporal %s: %s",
                ruta_temporal,
                cleanup_exc
            )
        app.logger.exception("No se pudo entrenar el modelo para %s: %s", nbr, exc)
        train_jobs[job_id].update({
            "status": "failed",
            "progress": 0,
            "error": "No se pudo completar el entrenamiento."
        })

@app.route('/train_classifier/<nbr>', methods=['POST'])
def train_classifier(nbr):
    if not re.fullmatch(r"\d{1,3}", nbr):
        return jsonify({"error": "Código de persona no válido."}), 400

    cursor = get_db_cursor()
    if cursor is None:
        return jsonify({"error": "Base de datos no disponible."}), 503
    cursor.execute("SELECT prs_nbr FROM prs_mstr WHERE prs_nbr = %s", (nbr,))
    if cursor.fetchone() is None:
        return jsonify({"error": "No se encontró la persona."}), 404

    fotos_persona = obtener_fotos_entrenables(nbr)
    if len(fotos_persona) < 10:
        return jsonify({
            "error": "Se requieren al menos 10 fotos válidas para entrenar.",
            "fotos_validas": len(fotos_persona)
        }), 400

    fotos_modelo = obtener_fotos_entrenables()
    if len(fotos_modelo) < 10:
        return jsonify({"error": "No hay suficientes fotos válidas en el modelo."}), 400

    job_id = str(uuid.uuid4())
    train_jobs[job_id] = {"status": "running", "progress": 5, "nbr": nbr}
    threading.Thread(
        target=background_train,
        args=(nbr, job_id, fotos_modelo),
        daemon=True
    ).start()
    return jsonify({"job_id": job_id}), 202

@app.route('/train_status/<job_id>')
def train_status(job_id):
    info = train_jobs.get(job_id, {"status": "unknown", "progress": 0})
    return jsonify(info)


 
 
# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< Face Recognition >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
def registrar_deteccion(prs_nbr, nombre, confianza, estado):
    """Registra asistencia una vez dentro del intervalo configurable."""
    ahora = datetime.now()
    clave = str(prs_nbr or "desconocido")
    ultima = ultima_deteccion_por_persona.get(clave)
    intervalo = timedelta(minutes=estado_camara["espera_minutos"])

    if ultima is not None and ahora - ultima < intervalo:
        return False

    ultima_deteccion_por_persona[clave] = ahora
    detecciones_recientes.insert(0, {
        "prs_nbr": prs_nbr or "",
        "nombre": nombre or "DESCONOCIDO",
        "confianza": confianza,
        "estado": estado,
        "hora": ahora.strftime("%H:%M"),
        "fecha_hora": ahora.isoformat(timespec="seconds"),
    })
    del detecciones_recientes[20:]

    if estado == "desconocido":
        return True

    cursor = get_db_cursor()
    if cursor is None:
        app.logger.warning("No se pudo registrar asistencia: Base de datos no disponible")
        return True

    try:
        cursor.execute(
            """
            INSERT INTO asistencia (prs_nbr, fecha_hora, confianza, estado)
            VALUES (%s, %s, %s, %s)
            """,
            (prs_nbr, ahora, confianza, estado)
        )
        mydb.commit()
    except mysql.connector.Error as exc:
        if mydb is not None:
            mydb.rollback()
        app.logger.exception("No se pudo guardar la deteccion de asistencia: %s", exc)
    return True


def face_recognition():
    """Genera el flujo de vigilancia y mantiene un único recurso de cámara."""
    global camara_actual

    face_cascade = cv2.CascadeClassifier(
        os.path.join(RESOURCES_DIR, "haarcascade_frontalface_default.xml")
    )
    recognizer = cv2.face.LBPHFaceRecognizer_create()
    if not os.path.isfile(CLASSIFIER_PATH):
        app.logger.warning("No existe classifier.xml; la vigilancia no puede reconocer.")
        yield b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'
        return

    try:
        recognizer.read(CLASSIFIER_PATH)
        with camara_bloqueo:
            if camara_actual is None:
                camara_actual = cv2.VideoCapture(0)
            cap = camara_actual

        cap.set(3, 960)
        cap.set(4, 540)
        inicio_fps = time.time()
        contador_frames = 0

        while estado_camara["activa"]:
            ret, frame = cap.read()
            if not ret:
                time.sleep(.1)
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, 1.1, 10)
            estado_camara["rostros_ahora"] = len(faces)

            for (x, y, w, h) in faces:
                person_id, prediction = recognizer.predict(gray[y:y + h, x:x + w])
                confianza = max(0, min(100, int(100 * (1 - prediction / 300))))
                nombre = "DESCONOCIDO"
                codigo = ""
                estado = "desconocido"

                if confianza >= estado_camara["umbral"]:
                    cursor = get_db_cursor()
                    if cursor is not None:
                        cursor.execute(
                            """
                            SELECT b.prs_nbr, b.prs_name
                            FROM img_dataset a
                            LEFT JOIN prs_mstr b ON a.img_person = b.prs_nbr
                            WHERE a.img_id = %s
                            LIMIT 1
                            """,
                            (person_id,)
                        )
                        fila = cursor.fetchone()
                        if fila:
                            codigo, nombre = fila
                            estado = "presente"

                if estado == "presente":
                    color = (61, 214, 140)
                    etiqueta = f"{nombre} · {confianza}%"
                else:
                    color = (107, 107, 255)
                    etiqueta = f"DESCONOCIDO · {confianza}%"
                    confianza = max(confianza, 0)

                cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
                cv2.putText(
                    frame, etiqueta, (x, max(20, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, .58, color, 2, cv2.LINE_AA
                )
                registrar_deteccion(codigo, nombre if estado == "presente" else "DESCONOCIDO",
                                    confianza, estado)

            ok, encoded = cv2.imencode('.jpg', frame)
            if not ok:
                continue

            contador_frames += 1
            transcurrido = time.time() - inicio_fps
            if transcurrido >= 1:
                estado_camara["fps"] = round(contador_frames / transcurrido, 1)
                inicio_fps = time.time()
                contador_frames = 0

            yield (
                b'--frame\r\n'
                b'Content-Type: image/jpeg\r\n\r\n' +
                encoded.tobytes() +
                b'\r\n'
            )
    except (cv2.error, OSError) as exc:
        app.logger.exception("Error en la captura de vigilancia: %s", exc)
    finally:
        with camara_bloqueo:
            if camara_actual is not None:
                camara_actual.release()
                camara_actual = None
        estado_camara["activa"] = False
        estado_camara["rostros_ahora"] = 0
 
 
@app.route('/')
def home():
    cursor = get_db_cursor()
    if cursor is not None:
        cursor.execute("select prs_nbr, prs_name, prs_skill, prs_active, prs_added from prs_mstr")
        data = cursor.fetchall()
    else:
        data = []
 
    return render_template('index.html', data=data, db_available=cursor is not None)
 
@app.route('/addprsn')
def addprsn():
    cursor = get_db_cursor()
    if cursor is not None:
        cursor.execute(
            "SELECT COALESCE(MAX(CAST(prs_nbr AS UNSIGNED)), 100) + 1 FROM prs_mstr"
        )
        row = cursor.fetchone()
        nbr = row[0] if row else 101
    else:
        nbr = 101

    return render_template(
        'addprsn.html',
        newnbr=int(nbr),
        db_available=cursor is not None,
        error=None,
        form_data={}
    )
 
@app.route('/addprsn_submit', methods=['POST'])
def addprsn_submit():
    prsnbr = (request.form.get('txtnbr') or '').strip()
    prsname = (request.form.get('txtname') or '').strip()
    prsskill = (request.form.get('optskill') or '').strip()
    seccion = (request.form.get('txtsection') or '').strip()
    consentimiento = request.form.get('consent') in ('on', '1', 'true')
    form_data = {
        'txtname': prsname,
        'optskill': prsskill,
        'txtsection': seccion,
        'consent': consentimiento
    }

    cursor = get_db_cursor()
    if not prsname or not consentimiento:
        mensaje = "Ingresa el nombre completo y confirma el consentimiento informado."
        if not cursor:
            mensaje = "Base de datos no disponible. " + mensaje
        codigo = 101
        if cursor is not None:
            cursor.execute(
                "SELECT COALESCE(MAX(CAST(prs_nbr AS UNSIGNED)), 100) + 1 FROM prs_mstr"
            )
            fila = cursor.fetchone()
            codigo = int(fila[0]) if fila and fila[0] else 101
        return render_template(
            'addprsn.html',
            newnbr=codigo,
            db_available=cursor is not None,
            error=mensaje,
            form_data=form_data
        ), 400

    if not prsnbr.isdigit() or not re.fullmatch(r"\d{1,3}", prsnbr):
        return render_template(
            'addprsn.html',
            newnbr=101,
            db_available=cursor is not None,
            error="El código generado no es válido. Recarga el formulario.",
            form_data=form_data
        ), 400

    if (
        len(prsname) > 50
        or prsskill not in ('Usuario', 'Profesor', 'Administrador')
        or not seccion
        or len(seccion) > 32
    ):
        return render_template(
            'addprsn.html',
            newnbr=int(prsnbr),
            db_available=cursor is not None,
            error="Verifica el nombre, rol y grado/sección ingresados.",
            form_data=form_data
        ), 400

    if cursor is None:
        return render_template(
            'addprsn.html',
            newnbr=int(prsnbr),
            db_available=False,
            error="Base de datos no disponible. No se guardó la persona.",
            form_data=form_data
        ), 503

    cursor.execute(
        "SELECT COALESCE(MAX(CAST(prs_nbr AS UNSIGNED)), 100) + 1 FROM prs_mstr"
    )
    fila = cursor.fetchone()
    siguiente_codigo = str(int(fila[0])) if fila and fila[0] else '101'
    if prsnbr != siguiente_codigo:
        return render_template(
            'addprsn.html',
            newnbr=int(siguiente_codigo),
            db_available=True,
            error="El código se actualizó. Verifica los datos y vuelve a enviarlos.",
            form_data=form_data
        ), 409

    try:
        cursor.execute(
            """
            INSERT INTO prs_mstr (prs_nbr, prs_name, prs_skill, seccion)
            VALUES (%s, %s, %s, %s)
            """,
            (prsnbr, prsname, prsskill, seccion)
        )
        mydb.commit()
    except mysql.connector.Error as exc:
        if mydb is not None:
            mydb.rollback()
        app.logger.exception("No se pudo guardar la persona %s: %s", prsnbr, exc)
        return render_template(
            'addprsn.html',
            newnbr=int(prsnbr),
            db_available=False,
            error="No se pudo guardar la persona. Verifica la conexión y la migración de la base de datos.",
            form_data=form_data
        ), 503

    return redirect(url_for('vfdataset_page', prs=prsnbr))
 
@app.route('/vfdataset_page/<prs>')
def vfdataset_page(prs):
    if not re.fullmatch(r"\d{1,3}", prs):
        return redirect(url_for('addprsn'))

    cursor = get_db_cursor()
    persona = None
    if cursor is not None:
        cursor.execute(
            "SELECT prs_name, prs_skill, seccion FROM prs_mstr WHERE prs_nbr = %s",
            (prs,)
        )
        persona = cursor.fetchone()
    if persona is None and cursor is not None:
        return redirect(url_for('addprsn'))

    return render_template(
        'gendataset.html',
        prs=prs,
        persona=persona,
        db_available=cursor is not None,
        error=None
    )
 
@app.route('/api/fotos/<prs>', methods=['GET', 'POST'])
def fotos_persona_api(prs):
    if not re.fullmatch(r"\d{1,3}", prs):
        return jsonify({"error": "Código de persona no válido."}), 400

    cursor = get_db_cursor()
    if cursor is None:
        return jsonify({"error": "Base de datos no disponible."}), 503

    cursor.execute("SELECT prs_nbr FROM prs_mstr WHERE prs_nbr = %s", (prs,))
    if cursor.fetchone() is None:
        return jsonify({"error": "No se encontró la persona."}), 404

    if request.method == 'GET':
        fotos = []
        if os.path.isdir(DATASET_DIR):
            patron = re.compile(re.escape(prs) + r"\.(\d+)\.jpg", re.IGNORECASE)
            for nombre in os.listdir(DATASET_DIR):
                coincidencia = patron.fullmatch(nombre)
                if coincidencia is None:
                    continue
                img_id = int(coincidencia.group(1))
                ruta = os.path.join(DATASET_DIR, nombre)
                if os.path.isfile(ruta):
                    fotos.append({
                        "id": img_id,
                        "url": url_for('foto_persona', prs=prs, img_id=img_id),
                        "valida": True
                    })
        fotos.sort(key=lambda foto: foto["id"])
        return jsonify({"fotos": fotos, "validas": len(fotos)})

    contenido = request.get_json(silent=True) or {}
    imagen_base64 = contenido.get("imagen", "")
    prefijo = "data:image/jpeg;base64,"
    if not isinstance(imagen_base64, str) or not imagen_base64.startswith(prefijo):
        return jsonify({"error": "La imagen JPEG enviada no es válida."}), 400

    try:
        bytes_imagen = base64.b64decode(imagen_base64[len(prefijo):], validate=True)
    except (binascii.Error, ValueError, TypeError):
        return jsonify({"error": "No se pudo leer la imagen enviada."}), 400
    if not bytes_imagen:
        return jsonify({"error": "La imagen enviada está vacía."}), 400

    try:
        frame = cv2.imdecode(np.frombuffer(bytes_imagen, dtype=np.uint8), cv2.IMREAD_COLOR)
    except cv2.error:
        frame = None
    if frame is None:
        return jsonify({"error": "No se pudo abrir la imagen enviada."}), 400

    ruta_cascade = os.path.join(RESOURCES_DIR, "haarcascade_frontalface_default.xml")
    detector = cv2.CascadeClassifier(ruta_cascade)
    if detector.empty():
        app.logger.error("No se pudo cargar el clasificador Haar: %s", ruta_cascade)
        return jsonify({"error": "No está disponible el detector facial."}), 500

    gris = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    rostros = detector.detectMultiScale(gris, scaleFactor=1.1, minNeighbors=5)
    if len(rostros) != 1:
        return jsonify({
            "valida": False,
            "motivo": "Debe aparecer un solo rostro en la foto."
        })

    x, y, ancho, alto = rostros[0]
    rostro = gris[y:y + alto, x:x + ancho]
    nitidez = cv2.Laplacian(rostro, cv2.CV_64F).var()
    if nitidez < 45:
        return jsonify({
            "valida": False,
            "motivo": "La foto está borrosa; mejora la iluminación y vuelve a intentarlo."
        })

    cursor.execute("SELECT COALESCE(MAX(img_id), 0) + 1 FROM img_dataset")
    fila = cursor.fetchone()
    img_id = int(fila[0]) if fila and fila[0] else 1
    ruta_foto = os.path.join(DATASET_DIR, f"{prs}.{img_id}.jpg")
    while os.path.exists(ruta_foto):
        img_id += 1
        ruta_foto = os.path.join(DATASET_DIR, f"{prs}.{img_id}.jpg")
    rostro = cv2.resize(rostro, (200, 200))

    if not cv2.imwrite(ruta_foto, rostro):
        app.logger.error("No se pudo guardar la foto de entrenamiento: %s", ruta_foto)
        return jsonify({"error": "No se pudo guardar la foto capturada."}), 500

    try:
        cursor.execute(
            "INSERT INTO img_dataset (img_id, img_person) VALUES (%s, %s)",
            (img_id, prs)
        )
        mydb.commit()
    except mysql.connector.Error as exc:
        if mydb is not None:
            mydb.rollback()
        if os.path.exists(ruta_foto):
            os.remove(ruta_foto)
        app.logger.exception("No se pudo registrar la foto %s: %s", img_id, exc)
        return jsonify({"error": "No se pudo registrar la foto en la base de datos."}), 503

    return jsonify({
        "valida": True,
        "foto": {
            "id": img_id,
            "url": url_for('foto_persona', prs=prs, img_id=img_id),
            "valida": True
        }
    }), 201

@app.route('/api/fotos/<prs>/<int:img_id>', methods=['DELETE'])
def eliminar_foto_persona(prs, img_id):
    if not re.fullmatch(r"\d{1,3}", prs):
        return jsonify({"error": "Código de persona no válido."}), 400

    cursor = get_db_cursor()
    if cursor is None:
        return jsonify({"error": "Base de datos no disponible."}), 503

    cursor.execute(
        "SELECT img_id FROM img_dataset WHERE img_id = %s AND img_person = %s",
        (img_id, prs)
    )
    registro = cursor.fetchone()
    ruta_foto = os.path.join(DATASET_DIR, f"{prs}.{img_id}.jpg")
    if registro is None and not os.path.isfile(ruta_foto):
        return jsonify({"error": "No se encontró la foto solicitada."}), 404

    if registro is not None:
        cursor.execute(
            "DELETE FROM img_dataset WHERE img_id = %s AND img_person = %s",
            (img_id, prs)
        )
        mydb.commit()

    try:
        if os.path.isfile(ruta_foto):
            os.remove(ruta_foto)
    except OSError as exc:
        app.logger.exception("No se pudo eliminar el archivo de foto %s: %s", ruta_foto, exc)
        return jsonify({"error": "La foto se desvinculó, pero no se pudo borrar su archivo."}), 500

    return jsonify({"eliminada": True})

@app.route('/dataset/<prs>/<int:img_id>')
def foto_persona(prs, img_id):
    if not re.fullmatch(r"\d{1,3}", prs):
        return "Código no válido", 400

    ruta_foto = os.path.join(DATASET_DIR, f"{prs}.{img_id}.jpg")
    if not os.path.isfile(ruta_foto):
        return "Archivo de foto no encontrado", 404
    return send_file(ruta_foto, mimetype="image/jpeg")

@app.route('/vidfeed_dataset/<nbr>')
def vidfeed_dataset(nbr):
    #Video streaming route. Put this in the src attribute of an img tag
    return Response(generate_dataset(nbr), mimetype='multipart/x-mixed-replace; boundary=frame')
 
@app.route('/video_feed')
def video_feed():
    # Video streaming route. Put this in the src attribute of an img tag
    return Response(face_recognition(), mimetype='multipart/x-mixed-replace; boundary=frame')
 
@app.route('/api/camara/iniciar', methods=['POST'])
def iniciar_camara():
   if not os.path.isfile(CLASSIFIER_PATH):
       return jsonify({
           "activa": False,
           "error": "El modelo classifier.xml no está disponible."
       }), 409

   estado_camara["activa"] = True
   return jsonify({"activa": True, "mensaje": "Cámara iniciada."})


@app.route('/api/camara/detener', methods=['POST'])
def detener_camara():
   global camara_actual
   estado_camara["activa"] = False
   estado_camara["rostros_ahora"] = 0
   with camara_bloqueo:
       if camara_actual is not None:
           camara_actual.release()
           camara_actual = None
   return jsonify({"activa": False, "mensaje": "Cámara detenida."})


@app.route('/api/detecciones')
def api_detecciones():
   try:
       limite = int(request.args.get('limite', 20))
   except (TypeError, ValueError):
       limite = 20
   limite = min(max(limite, 1), 100)
   recientes = list(detecciones_recientes[:limite])

   presentes_hoy = 0
   desconocidos = 0
   cursor = get_db_cursor()
   if cursor is not None:
       try:
           cursor.execute(
               """
               SELECT estado, COUNT(*)
               FROM asistencia
               WHERE DATE(fecha_hora) = CURDATE()
               GROUP BY estado
               """
           )
           for estado, cantidad in cursor.fetchall():
               if estado == "presente":
                   presentes_hoy += int(cantidad)
               elif estado == "desconocido":
                   desconocidos += int(cantidad)
       except mysql.connector.Error as exc:
           app.logger.warning("No se pudieron consultar los contadores de asistencia: %s", exc)

   return jsonify({
       "detecciones": recientes,
       "rostros_ahora": estado_camara["rostros_ahora"],
       "presentes_hoy": presentes_hoy,
       "ausentes": 0,
       "desconocidos": desconocidos,
       "fps": estado_camara["fps"],
       "camara_activa": estado_camara["activa"],
   })


@app.route('/api/estado_modelo')
def api_estado_modelo():
   fotos = obtener_fotos_entrenables()
   personas = set()
   fecha_entrenamiento = None
   if os.path.isfile(CLASSIFIER_PATH):
       fecha_entrenamiento = datetime.fromtimestamp(
           os.path.getmtime(CLASSIFIER_PATH)
       ).strftime("%Y-%m-%d %H:%M")

   cursor = get_db_cursor()
   if cursor is not None:
       try:
           cursor.execute("SELECT DISTINCT img_person FROM img_dataset")
           personas = {fila[0] for fila in cursor.fetchall()}
       except mysql.connector.Error as exc:
           app.logger.warning("No se pudo consultar el modelo: %s", exc)

   if not personas:
       personas = {str(img_id).split(".", 1)[0] for img_id, _ in fotos}

   return jsonify({
       "entrenado": os.path.isfile(CLASSIFIER_PATH),
       "fotos": len(fotos),
       "personas": len(personas),
       "fecha_entrenamiento": fecha_entrenamiento,
   })


@app.route('/api/configuracion', methods=['POST'])
def api_configuracion():
   contenido = request.get_json(silent=True) or {}
   umbral = contenido.get("umbral")
   espera = contenido.get("espera_minutos")
   if umbral is not None:
       try:
           umbral = int(umbral)
           if 1 <= umbral <= 100:
               estado_camara["umbral"] = umbral
       except (TypeError, ValueError):
           return jsonify({"error": "El umbral debe ser un número entero."}), 400
   if espera is not None:
       try:
           espera = int(espera)
           if 1 <= espera <= 1440:
               estado_camara["espera_minutos"] = espera
       except (TypeError, ValueError):
           return jsonify({"error": "La espera debe ser un número entero."}), 400
   return jsonify({
       "umbral": estado_camara["umbral"],
       "espera_minutos": estado_camara["espera_minutos"],
   })


def consultar_asistencias(fecha=None, seccion=None, estado=None, busqueda=None,
                         limite=None, desplazamiento=0):
    """Consulta filtrada de asistencia usando parámetros SQL."""
    cursor = get_db_cursor()
    if cursor is None:
        return None, "Base de datos no disponible"

    condiciones = []
    parametros = []
    if fecha:
        try:
            datetime.strptime(fecha, "%Y-%m-%d")
        except ValueError:
            return [], "Formato de fecha no válido"
        condiciones.append("DATE(a.fecha_hora) = %s")
        parametros.append(fecha)
    if seccion:
        condiciones.append("p.seccion = %s")
        parametros.append(seccion)
    if estado:
        condiciones.append("a.estado = %s")
        parametros.append(estado)
    if busqueda:
        condiciones.append("(p.prs_name LIKE %s OR p.prs_nbr LIKE %s)")
        parametros.extend((f"%{busqueda}%", f"%{busqueda}%"))

    clausula = " AND ".join(condiciones)
    if clausula:
        clausula = "WHERE " + clausula

    try:
        cursor.execute(
            f"""
            SELECT a.id, a.prs_nbr, p.prs_name, p.seccion,
                   a.fecha_hora, a.confianza, a.estado
            FROM asistencia a
            LEFT JOIN prs_mstr p ON p.prs_nbr = a.prs_nbr
            {clausula}
            ORDER BY a.fecha_hora DESC
            """ + (
                " LIMIT %s OFFSET %s" if limite is not None else ""
            ),
            tuple(parametros + ([limite, desplazamiento] if limite is not None else []))
        )
        filas = cursor.fetchall()
        return filas, None
    except mysql.connector.Error as exc:
        app.logger.warning("No se pudieron consultar los registros: %s", exc)
        return None, "Base de datos no disponible"


@app.route('/registros')
def registros():
    fecha = (request.args.get('fecha') or '').strip()
    seccion = (request.args.get('seccion') or '').strip()
    estado = (request.args.get('estado') or '').strip()
    busqueda = (request.args.get('q') or '').strip()
    pagina = request.args.get('pagina', 1, type=int)
    pagina = max(pagina or 1, 1)
    por_pagina = 15

    filas, error = consultar_asistencias(
        fecha=fecha, seccion=seccion, estado=estado, busqueda=busqueda
    )
    if filas is None:
        return render_template(
            'registros.html',
            filas=[],
            error=error,
            fecha=fecha,
            seccion=seccion,
            estado=estado,
            busqueda=busqueda,
            pagina=pagina,
            total_paginas=1,
            secciones=[],
        ), 503
    if isinstance(filas, list) and error:
        return render_template(
            'registros.html',
            filas=[],
            error=error,
            fecha=fecha,
            seccion=seccion,
            estado=estado,
            busqueda=busqueda,
            pagina=pagina,
            total_paginas=1,
            secciones=[],
        ), 400

    total = len(filas)
    total_paginas = max(1, (total + por_pagina - 1) // por_pagina)
    pagina = min(pagina, total_paginas)
    inicio = (pagina - 1) * por_pagina
    filas_pagina = filas[inicio:inicio + por_pagina]

    cursor = get_db_cursor()
    secciones = []
    if cursor is not None:
        try:
            cursor.execute(
                "SELECT DISTINCT seccion FROM prs_mstr "
                "WHERE seccion IS NOT NULL AND seccion <> '' "
                "ORDER BY seccion"
            )
            secciones = [fila[0] for fila in cursor.fetchall()]
        except mysql.connector.Error as exc:
            app.logger.warning("No se pudieron cargar las secciones: %s", exc)

    return render_template(
        'registros.html',
        filas=filas_pagina,
        error=None,
        fecha=fecha,
        seccion=seccion,
        estado=estado,
        busqueda=busqueda,
        pagina=pagina,
        total_paginas=total_paginas,
        secciones=secciones,
    )


@app.route('/registros/exportar')
def exportar_registros():
    fecha = (request.args.get('fecha') or '').strip()
    seccion = (request.args.get('seccion') or '').strip()
    estado = (request.args.get('estado') or '').strip()
    busqueda = (request.args.get('q') or '').strip()
    formato = (request.args.get('formato') or 'csv').strip().lower()

    filas, error = consultar_asistencias(
        fecha=fecha, seccion=seccion, estado=estado, busqueda=busqueda
    )
    if filas is None:
        return jsonify({"error": error}), 503

    if formato == 'pdf':
        return render_template(
            'registros.html',
            filas=filas,
            error=None,
            fecha=fecha,
            seccion=seccion,
            estado=estado,
            busqueda=busqueda,
            pagina=1,
            total_paginas=1,
            secciones=[],
            print_mode=True,
        )

    salida = io.StringIO()
    salida.write('\ufeff')
    escritor = csv.writer(salida)
    escritor.writerow(['Persona', 'Código', 'Sección', 'Hora', 'Confianza', 'Estado'])
    for fila in filas:
        _, codigo, nombre, seccion_persona, fecha_hora, confianza, estado_fila = fila
        hora = fecha_hora.strftime('%Y-%m-%d %H:%M') if hasattr(fecha_hora, 'strftime') else fecha_hora
        escritor.writerow([
            nombre or 'Desconocido',
            codigo or '',
            seccion_persona or '',
            hora,
            f"{confianza}% ",
            estado_fila,
        ])

    respuesta = Response(salida.getvalue(), mimetype='text/csv; charset=utf-8')
    respuesta.headers['Content-Disposition'] = (
        'attachment; filename=registros_asistencia.csv'
    )
    return respuesta


@app.route('/fr_page')
def fr_page():
   return render_template(
       'fr_page.html',
       umbral=estado_camara["umbral"],
       espera=estado_camara["espera_minutos"],
   )
 
if __name__ == "__main__":
    app.run(host='127.0.0.1', port=5000, debug=False)