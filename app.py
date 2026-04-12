import os
import re
import json
import time
import ssl
import base64
import random
import threading
from datetime import datetime, date
from email.message import EmailMessage
import cv2
import numpy as np
import qrcode
import face_recognition
import mysql.connector
import smtplib
from fpdf import FPDF
from flask import Flask, render_template, request, redirect, send_file, url_for, session, flash, Response, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
from config import Config
# =========================================================
# APP + CONFIG
# =========================================================
app = Flask(__name__)
app.config.from_object(Config)
app.config["UPLOAD_FOLDER"] = "static/fotos"
# =========================================================
# DB
# =========================================================
def get_db_connection():
    return mysql.connector.connect(
        host=app.config["MYSQL_HOST"],
        user=app.config["MYSQL_USER"],
        password=app.config["MYSQL_PASSWORD"],
        database=app.config["MYSQL_DATABASE"]
    )
def obtener_usuario_sesion():
    if not session.get("user_id"):
        return None
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT p.id_persona, p.nombre, p.apellido, p.foto
        FROM usuarios u
        JOIN personas p ON u.id_persona = p.id_persona
        WHERE u.id_usuario = %s
    """, (session.get("user_id"),))
    usuario = cursor.fetchone()
    cursor.close()
    conn.close()
    if usuario and usuario.get("foto"):
        if isinstance(usuario["foto"], (bytes, bytearray)):
            import base64
            usuario["foto"] = base64.b64encode(usuario["foto"]).decode("utf-8")
    elif usuario:
        usuario["foto"] = None

    return usuario
# =========================================================
# HELPERS
# =========================================================
def generar_carnet_unico():
    """
    Genera un carnet único con prefijo '7691-YY-' y 5 dígitos aleatorios.
    Verifica que no exista en la tabla personas.
    """
    yy = datetime.now().strftime("%y")
    prefijo = f"7691-{yy}-"
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        while True:
            numero_random = random.randint(10000, 99999)
            carnet = f"{prefijo}{numero_random}"
            cursor.execute("SELECT id_persona FROM personas WHERE carnet = %s", (carnet,))
            if cursor.fetchone() is None:
                return carnet
    finally:
        cursor.close()
        conn.close()
def _decode_dataurl_image(dataurl: str):
    if not dataurl:
        return None
    dataurl = dataurl.strip()
    m = re.match(r"data:image/[^;]+;base64,(.*)", dataurl)
    if m:
        dataurl = m.group(1)
    return base64.b64decode(dataurl)
def generate_id_card_pdf(nombre, apellido, correo, foto_bytes, carnet, id_persona, firma_base64=None):
    conn = None
    cursor = None
    foto_path = None
    qr_path = None
    firma_img_path = None
    try:
        year = datetime.now().year
        # =========================
        # TAMAÑO CARNET (CR80)
        # =========================
        CARD_W = 85.6
        CARD_H = 54.0
        pdf = FPDF(orientation="L", unit="mm", format=(CARD_H, CARD_W))
        pdf.add_page()
        pdf.set_auto_page_break(False)
        # Fondo blanco
        pdf.set_fill_color(255, 255, 255)
        pdf.rect(0, 0, CARD_W, CARD_H, "F")
        # =========================
        # LOGO
        # =========================
        logo_path = os.path.join("static", "img", "logo.png")
        if os.path.exists(logo_path):
            pdf.image(logo_path, x=3.6, y=0.75, w=13.2, h=13.2)
        # =========================
        # UNIVERSIDAD
        # =========================
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Times", "B", 16)
        pdf.text(19.5, 5.5, "UNIVERSIDAD")
        pdf.text(19.5, 12.5, "MARIANO GÁLVEZ")
        # =========================
        # AÑO
        # =========================
        pdf.set_font("Times", "B", 13)
        pdf.text(67.0, 19.5, str(year))
        # =========================
        # FOTO
        # =========================
        foto_path = os.path.join("static", f"temp_foto_{id_persona}.png")
        with open(foto_path, "wb") as f:
            f.write(foto_bytes)
        pdf.image(foto_path, x=4.5, y=16.2, w=23.4, h=25.3)
        # =========================
        # NOMBRE Y APELLIDO
        # =========================
        pdf.set_font("Times", "B", 11)
        pdf.text(32.4, 20.5, nombre)
        pdf.text(32.4, 24.5, apellido)
        # =========================
        # ID
        # =========================
        pdf.set_font("Helvetica", "", 8)
        pdf.text(32.4, 29, "Carnet")
        pdf.text(32.4, 32,  carnet)
        # =========================
        # FIRMA
        # =========================
        firma_bytes = _decode_dataurl_image(firma_base64)
        # Si no viene del form, buscar en BD
        if not firma_bytes:
            try:
                conn = get_db_connection()
                cursor = conn.cursor(dictionary=True)
                cursor.execute("SELECT firma FROM personas WHERE id_persona=%s", (id_persona,))
                row = cursor.fetchone()
                if row and row.get("firma"):
                    firma_bytes = _decode_dataurl_image(row["firma"])
            except Exception:
                pass
        if firma_bytes:
            firma_img_path = os.path.join("static", f"temp_firma_{id_persona}.png")
            with open(firma_img_path, "wb") as f:
                f.write(firma_bytes)
            pdf.image(firma_img_path, x=33.7, y=36.5, w=27.3, h=6.3)
            pdf.text(38.7, 46.5, "Firma")
        # =========================
        # QR
        # =========================
        qr_data = f"ID:{id_persona};EMAIL:{correo}"
        qr_img = qrcode.make(qr_data)
        qr_path = os.path.join("static", f"temp_qr_{id_persona}.png")
        qr_img.save(qr_path)
        pdf.image(qr_path, x=63.6, y=21.5, w=16.5, h=16.5)
        # =========================
        # SALIDA
        # =========================
        return pdf.output(dest="S").encode("latin1")
    except Exception as e:
        print("Error generando PDF:", e)
        return None
    finally:
        try:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        except Exception:
            pass
        # Eliminar archivos temporales
        for p in (foto_path, qr_path, firma_img_path):
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
def send_email_with_pdf(to_email, pdf_bytes, filename, subject="Tu carnet institucional"):
    smtp_server = app.config.get("SMTP_SERVER") or os.getenv("SMTP_SERVER")
    smtp_port = app.config.get("SMTP_PORT") or os.getenv("SMTP_PORT") or "587"
    try:
        smtp_port = int(smtp_port)
    except Exception:
        smtp_port = 587
    smtp_user = app.config.get("SMTP_USER") or os.getenv("SMTP_USER")
    smtp_pass = app.config.get("SMTP_PASSWORD") or os.getenv("SMTP_PASSWORD")
    sender_email = app.config.get("SENDER_EMAIL") or os.getenv("SENDER_EMAIL")
    use_tls_raw = app.config.get("SMTP_USE_TLS") or os.getenv("SMTP_USE_TLS", "true")
    use_tls = str(use_tls_raw).lower() == "true"
    missing = []
    if not smtp_server: missing.append("SMTP_SERVER")
    if not smtp_user: missing.append("SMTP_USER")
    if not smtp_pass: missing.append("SMTP_PASSWORD")
    if not sender_email: missing.append("SENDER_EMAIL")
    if missing:
        err = "Faltan variables SMTP: " + ", ".join(missing)
        print(err)
        return (False, err)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = to_email
    msg.set_content("Adjunto encontrarás tu carnet institucional en formato PDF.")
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=filename)
    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as server:
            if use_tls:
                server.starttls(context=context)
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return (True, None)
    except Exception as e:
        err = f"Error enviando correo con PDF: {e}"
        print(err)
        return (False, err)
# =========================================================
# AUTH ROUTES
# =========================================================
@app.route("/")
def home():
    return redirect(url_for("login"))
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM usuarios WHERE username = %s", (username,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()
        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id_usuario"]
            session["rol"] = user["rol"]
            if user["rol"] == "administrativo":
                return redirect(url_for("dashboard_admin"))
            elif user["rol"] == "catedratico":
                return redirect(url_for("mis_cursos"))
            flash("Rol no autorizado.", "error")
            return redirect(url_for("login"))
        flash("Usuario o contraseña incorrectos", "error")
        return redirect(url_for("login"))
    return render_template("login.html")
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))
# =========================================================
# DASHBOARDS
# =========================================================
@app.route("/admin")
def dashboard_admin():
    if session.get("rol") != "administrativo":
        return redirect(url_for("login"))
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    usuario = obtener_usuario_sesion()
    cursor.execute("SELECT COUNT(*) AS total FROM personas")
    total_personas = cursor.fetchone()["total"]
    cursor.execute("SELECT COUNT(*) AS total FROM personas WHERE tipo_persona='catedratico'")
    total_docentes = cursor.fetchone()["total"]
    cursor.execute("SELECT COUNT(*) AS total FROM personas WHERE tipo_persona='administrativo'")
    total_admins = cursor.fetchone()["total"]
    cursor.execute("SELECT COUNT(*) AS total FROM personas WHERE tipo_persona='estudiante'")
    total_estudiantes = cursor.fetchone()["total"]
    cursor.close()
    conn.close()
    return render_template(
        "admin.html",
        usuario=usuario,
        total_personas=total_personas,
        total_docentes=total_docentes,
        total_admins=total_admins,
        total_estudiantes=total_estudiantes
    )

@app.route("/docente")
def dashboard_docente():
    if session.get("rol") != "catedratico":
        return redirect(url_for("login"))
    usuario = obtener_usuario_sesion()
    return render_template("catedratico/mis_cursos.html", usuario=usuario)
# =========================================================
# REGISTRO PERSONAS
# =========================================================
@app.route("/registrar", methods=["GET", "POST"])
def registrar_persona():
    if session.get("rol") != "administrativo":
        return redirect(url_for("login"))
    # Obtener datos del usuario para la plantilla (evita que 'usuario' sea undefined)
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT p.nombre, p.apellido, p.foto
        FROM usuarios u
        JOIN personas p ON u.id_persona = p.id_persona
        WHERE u.id_usuario = %s
    """, (session.get("user_id"),))
    usuario = cursor.fetchone()
    cursor.close()
    conn.close()
    if usuario and usuario.get("foto"):
        if isinstance(usuario["foto"], (bytes, bytearray)):
            usuario["foto"] = usuario["foto"].decode("utf-8")
    else:
        if usuario:
            usuario["foto"] = None
    if request.method == "POST":
        # ========= 1) Capturar form =========
        nombre = request.form["nombre"].strip()
        apellido = request.form["apellido"].strip()
        telefono = request.form["telefono"].strip()
        correo = request.form["correo"].strip().lower()
        tipo_persona = request.form["tipo_persona"]
        carrera = request.form.get("carrera", "")
        seccion = request.form.get("seccion", "")
        username = request.form.get("username")
        password = request.form.get("password")
        imagen_base64 = request.form.get("fotografia")
        firma = request.form.get("firma", "").strip()
        # Para re-renderizar y NO perder datos
        form_data = request.form.to_dict(flat=True)
        # ========= 2) Validaciones =========
        if not correo.endswith("@miumg.edu.gt"):
            flash("Debe usar correo institucional @miumg.edu.gt", "danger")
            # NO es error de foto, puede redirigir sin problema
            return redirect(url_for("registrar_persona"))
        if not imagen_base64:
            flash("Debe capturar la fotografía", "danger")
            # Aquí sí conviene re-render para no perder datos
            form_data["fotografia"] = ""
            return render_template("registrar.html", form_data=form_data, retake_photo=True, usuario=usuario)
        try:
            _, encoded = imagen_base64.split(",", 1)
            imagen_bytes = base64.b64decode(encoded)
        except Exception:
            flash("Error procesando la imagen. Vuelva a tomar la fotografía.", "danger")
            form_data["fotografia"] = ""
            return render_template("registrar.html", form_data=form_data, retake_photo=True, usuario=usuario)

        # ========= 3) DB =========
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT COUNT(*) AS total FROM personas WHERE correo = %s", (correo,))
        if cursor.fetchone()["total"] > 0:
            cursor.close()
            conn.close()
            flash("El correo ya está registrado", "warning")
            return redirect(url_for("registrar_persona"))

        carnet = generar_carnet_unico()

        cursor.close()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO personas
            (nombre, apellido, telefono, correo, carnet, tipo_persona, carrera, seccion, foto, firma)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (nombre, apellido, telefono, correo, carnet, tipo_persona, carrera, seccion, imagen_bytes, firma))

        id_persona = cursor.lastrowid
        conn.commit()

        # ========= 4) Guardar foto en carpeta =========
        def limpiar_nombre(texto):
            return re.sub(r"[^\w\-]", "_", texto)

        nombre_limpio = limpiar_nombre(f"{nombre}_{apellido}")
        carpeta_persona = os.path.join("static", "rostros", f"{id_persona}_{nombre_limpio}")
        os.makedirs(carpeta_persona, exist_ok=True)

        nombre_archivo = f"{nombre_limpio}_1.png"
        ruta_foto = os.path.join(carpeta_persona, nombre_archivo)

        with open(ruta_foto, "wb") as f:
            f.write(imagen_bytes)

        # ========= 5) Encoding facial =========
        try:
            img = cv2.imread(ruta_foto)
            if img is None:
                raise ValueError("No se pudo leer la imagen")

            rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            encodings = face_recognition.face_encodings(rgb_img)

            if not encodings:
                raise ValueError("No se detectó un rostro válido")

            encoding_json = json.dumps(encodings[0].tolist())
            cursor.execute("""
                UPDATE personas
                SET encoding_facial = %s
                WHERE id_persona = %s
            """, (encoding_json, id_persona))
            conn.commit()

        except Exception as e:
            print("Error encoding:", e)

            # 1) borrar registro creado
            cursor.execute("DELETE FROM personas WHERE id_persona = %s", (id_persona,))
            conn.commit()

            # 2) borrar archivo
            try:
                if os.path.exists(ruta_foto):
                    os.remove(ruta_foto)
            except:
                pass

            # 3) (opcional) borrar carpeta si queda vacía
            try:
                if os.path.isdir(carpeta_persona) and len(os.listdir(carpeta_persona)) == 0:
                    os.rmdir(carpeta_persona)
            except:
                pass

            cursor.close()
            conn.close()

            flash("No se detectó un rostro válido. Vuelva a tomar la fotografía.", "danger")

            # ✅ AQUÍ LA CLAVE: NO redirect, re-render con datos
            form_data["fotografia"] = ""   # obligar a retomar foto
            # Si quieres conservar la firma: NO la borres
            # form_data["firma"] = form_data.get("firma", "")

            return render_template("registrar.html", form_data=form_data, retake_photo=True, usuario=usuario)

        # ========= 6) Crear usuario si aplica =========
        if tipo_persona in ["catedratico", "administrativo"]:
            if not username:
                username = correo
            if not password:
                password = "123456"
            password_hash = generate_password_hash(password)

            cursor.execute("""
                INSERT INTO usuarios (id_persona, username, password, rol)
                VALUES (%s, %s, %s, %s)
            """, (id_persona, username, password_hash, tipo_persona))
            conn.commit()

        # ========= 7) PDF + correo =========
        try:
            pdf_bytes = generate_id_card_pdf(nombre, apellido, correo, imagen_bytes, carnet, id_persona, firma)
            if pdf_bytes:
                send_ok, send_err = send_email_with_pdf(correo, pdf_bytes, f"carnet_{id_persona}.pdf")
                if send_ok:
                    flash("Persona registrada y carnet enviado por correo.", "success")
                else:
                    flash(f"Persona registrada, pero error enviando carnet: {send_err}", "warning")
            else:
                flash("Persona registrada, pero no se pudo generar el carnet PDF.", "warning")
        except Exception as e:
            flash(f"Persona registrada, pero error al enviar carnet: {e}", "warning")

        cursor.close()
        conn.close()
        return redirect(url_for("registrar_persona"))

    # GET normal
    return render_template("registrar.html", usuario=usuario)


# =========================================================
# RECONOCIMIENTO MULTICÁMARA
# =========================================================
CAMERAS = {
    "cam1": {"nombre": "Puerta Principal", "ubicacion": "Puerta Principal", "source": "http://192.168.1.78:4747/video"},
    "cam2": {"nombre": "Salón 306", "ubicacion": "Salón 306", "source": "http://192.168.1.72:4747/video"},
}

TOLERANCE = 0.50
SCALE = 0.35
MIN_SECONDS_BETWEEN_LOGS = 10
TIPO_REGISTRO = "puerta_principal"

RECOGNIZE_EVERY_N_FRAMES = 15
STREAM_FPS = 20
JPEG_QUALITY = 45
DETECT_MODEL = "hog"

cam_state = {}
state_lock = threading.Lock()


def init_cam_state():
    for cam_id in CAMERAS.keys():
        cam_state[cam_id] = {
            "latest_jpeg": None,
            "latest_match": {
                "matched": False,
                "id_persona": None,
                "nombre": None,
                "apellido": None,
                "carnet": None,
                "correo": None,
                "dist": None,
                "timestamp": None,
                "cam_id": cam_id
            },
            "last_log_time": {}
        }

init_cam_state()


def load_known_faces():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT id_persona, nombre, apellido, carnet, correo, encoding_facial
        FROM personas
        WHERE encoding_facial IS NOT NULL
          AND encoding_facial <> ''
          AND estado = 'activo'
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    known_encodings = []
    known_people = []

    for r in rows:
        try:
            enc_list = json.loads(r["encoding_facial"])
            enc = np.array(enc_list, dtype=np.float32)
            if enc.shape == (128,):
                known_encodings.append(enc)
                known_people.append({
                    "id_persona": r["id_persona"],
                    "nombre": r["nombre"],
                    "apellido": r["apellido"],
                    "carnet": r["carnet"],
                    "correo": r["correo"],
                })
        except Exception as e:
            print(f"Encoding inválido id_persona={r.get('id_persona')}: {e}")

    print(f"✔ Encodings cargados: {len(known_encodings)}")
    return known_encodings, known_people


def registrar_entrada(id_persona, ubicacion):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO registros_entrada (id_persona, ubicacion, fecha, hora, tipo_registro)
        VALUES (%s, %s, %s, %s, %s)
    """, (id_persona, ubicacion, date.today(), datetime.now().time().replace(microsecond=0), TIPO_REGISTRO))
    conn.commit()
    cursor.close()
    conn.close()


KNOWN_ENCODINGS, KNOWN_PEOPLE = load_known_faces()
if not KNOWN_ENCODINGS:
    print("⚠ No hay encodings en la base de datos (personas.activo con encoding_facial).")


def open_camera(source):
    if isinstance(source, int):
        cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
    else:
        cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def camera_loop(cam_id, source):
    known_encodings, known_people = KNOWN_ENCODINGS, KNOWN_PEOPLE
    if not known_encodings:
        print(f"[{cam_id}] No hay encodings para reconocimiento.")
        return

    print(f"[{cam_id}] Intentando abrir: {source}")
    cap = open_camera(source)
    if not cap.isOpened():
        print(f"[{cam_id}] ❌ No se pudo abrir la cámara: {source}")
        return
    print(f"[{cam_id}] Cámara abierta")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 360)
    cap.set(cv2.CAP_PROP_FPS, STREAM_FPS)

    frame_count = 0
    last_boxes = []
    last_labels = []

    frame_interval = 1.0 / STREAM_FPS
    next_frame_time = time.time()

    while True:
        ret, frame = cap.read()
        if not ret or frame is None:
            time.sleep(0.05)
            continue

        frame_count += 1
        do_recognize = (frame_count % RECOGNIZE_EVERY_N_FRAMES == 0)

        if do_recognize:
            small = cv2.resize(frame, (0, 0), fx=SCALE, fy=SCALE)
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

            face_locations = face_recognition.face_locations(rgb, model=DETECT_MODEL)
            face_encodings = face_recognition.face_encodings(rgb, face_locations)

            last_boxes = []
            last_labels = []
            frame_match = None

            for (top, right, bottom, left), face_enc in zip(face_locations, face_encodings):
                distances = face_recognition.face_distance(known_encodings, face_enc)
                best_idx = int(np.argmin(distances))
                best_distance = float(distances[best_idx])

                if best_distance <= TOLERANCE:
                    person = known_people[best_idx]
                    pid = person["id_persona"]

                    now_ts = time.time()
                    with state_lock:
                        last_ts = cam_state[cam_id]["last_log_time"].get(pid, 0)

                    if now_ts - last_ts >= MIN_SECONDS_BETWEEN_LOGS:
                        try:
                            ubicacion_real = CAMERAS[cam_id]["ubicacion"]
                            registrar_entrada(pid, ubicacion=ubicacion_real)
                            with state_lock:
                                cam_state[cam_id]["last_log_time"][pid] = now_ts
                        except Exception as e:
                            print(f"[{cam_id}] Error registrando entrada:", e)

                    frame_match = {
                        "matched": True,
                        "id_persona": pid,
                        "nombre": person["nombre"],
                        "apellido": person["apellido"],
                        "carnet": person["carnet"],
                        "correo": person["correo"],
                        "dist": round(best_distance, 4),
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "cam_id": cam_id
                    }

                    label = f"{person['nombre']} {person['apellido']} ({person['carnet']})"
                    color = (0, 255, 0)
                else:
                    label = f"NO REGISTRADO (dist={best_distance:.2f})"
                    color = (0, 0, 255)

                inv = 1.0 / SCALE
                top2, right2, bottom2, left2 = int(top*inv), int(right*inv), int(bottom*inv), int(left*inv)

                last_boxes.append((top2, right2, bottom2, left2, color))
                last_labels.append((label, left2, top2, color))

            if frame_match:
                with state_lock:
                    cam_state[cam_id]["latest_match"] = frame_match

        for (top2, right2, bottom2, left2, color) in last_boxes:
            cv2.rectangle(frame, (left2, top2), (right2, bottom2), color, 2)

        for (label, left2, top2, color) in last_labels:
            cv2.putText(frame, label, (left2, max(20, top2 - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 3)
            cv2.putText(frame, label, (left2, max(20, top2 - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        if ok:
            with state_lock:
                cam_state[cam_id]["latest_jpeg"] = jpg.tobytes()

        now = time.time()
        sleep_time = next_frame_time - now
        if sleep_time > 0:
            time.sleep(sleep_time)
        next_frame_time = max(next_frame_time + frame_interval, now + frame_interval)


def mjpeg_generator(cam_id):
    while True:
        with state_lock:
            frame = cam_state[cam_id]["latest_jpeg"]
        if frame is None:
            time.sleep(0.05)
            continue

        yield (b"--frame\r\n"
               b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")

        time.sleep(1.0 / STREAM_FPS)


@app.route("/video_feed/<cam_id>")
def video_feed(cam_id):
    if cam_id not in CAMERAS:
        return "Cámara no existe", 404
    return Response(mjpeg_generator(cam_id),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/last_match/<cam_id>")
def last_match(cam_id):
    if cam_id not in CAMERAS:
        return jsonify({"error": "Cámara no existe"}), 404
    with state_lock:
        return jsonify(cam_state[cam_id]["latest_match"])


@app.route("/monitor/<cam_id>")
def monitor(cam_id):
    if cam_id not in CAMERAS:
        return "Cámara no existe", 404
    # Obtener datos del usuario para la plantilla
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT p.nombre, p.apellido, p.foto
        FROM usuarios u
        JOIN personas p ON u.id_persona = p.id_persona
        WHERE u.id_usuario = %s
    """, (session.get("user_id"),))
    usuario = cursor.fetchone()
    cursor.close()
    conn.close()

    if usuario and usuario.get("foto"):
        if isinstance(usuario["foto"], (bytes, bytearray)):
            usuario["foto"] = usuario["foto"].decode("utf-8")
    else:
        if usuario:
            usuario["foto"] = None

    return render_template("monitor.html", cam_id=cam_id, nombre_cam=CAMERAS[cam_id]["nombre"], usuario=usuario)


@app.route("/cameras_status")
def cameras_status():
    out = {}
    with state_lock:
        for cam_id in CAMERAS.keys():
            out[cam_id] = {
                "source": CAMERAS[cam_id],
                "has_frame": cam_state[cam_id]["latest_jpeg"] is not None,
                "last_match_ts": cam_state[cam_id]["latest_match"].get("timestamp"),
            }
    return jsonify(out)


def start_camera_threads():
    for cam_id, cam_data in CAMERAS.items():
        t = threading.Thread(target=camera_loop, args=(cam_id, cam_data["source"]), daemon=True)
        t.start()

# =========================================================
# RUTAS CURSOS
# =========================================================   
@app.route('/cursos/nuevo', methods=['GET', 'POST'])
def crear_curso():

    if session.get("rol") != "administrativo":
        return redirect(url_for("login"))

    usuario = obtener_usuario_sesion()

    conexion = get_db_connection()
    cursor = conexion.cursor()

    if request.method == 'POST':
        nombre_curso = request.form['nombre_curso']
        carrera = request.form['carrera']
        seccion = request.form['seccion']
        id_catedratico = request.form['id_catedratico']

        cursor.execute("""
            INSERT INTO cursos (nombre_curso, carrera, seccion, id_catedratico)
            VALUES (%s, %s, %s, %s)
        """, (nombre_curso, carrera, seccion, id_catedratico))

        conexion.commit()
        cursor.close()
        conexion.close()

        flash('Curso registrado correctamente.', 'success')
        return redirect(url_for('listar_cursos'))

    # Obtener catedráticos para el formulario
    cursor.execute("""
        SELECT id_persona, nombre, apellido
        FROM personas
        WHERE tipo_persona = 'catedratico'
        AND estado = 'activo'
        ORDER BY nombre, apellido
    """)
    catedraticos = cursor.fetchall()

    cursor.close()
    conexion.close()

    return render_template(
        'cursos/nuevo.html',
        usuario=usuario,
        catedraticos=catedraticos
    )
# ========================================================= 
# Lista de cursos
# =========================================================
@app.route('/cursos')
def listar_cursos():
    if session.get("rol") != "administrativo":
        return redirect(url_for("login"))

    usuario = obtener_usuario_sesion()

    conn = get_db_connection()
    cursor = conn.cursor()


    conexion = get_db_connection()
    cursor = conexion.cursor()

    cursor.execute("""
        SELECT c.id_curso, c.nombre_curso, c.carrera, c.seccion,
               p.nombre, p.apellido
        FROM cursos c
        LEFT JOIN personas p ON c.id_catedratico = p.id_persona
        ORDER BY c.id_curso DESC
    """)
    cursos = cursor.fetchall()

    cursor.close()
    conexion.close()

    return render_template('cursos/listar.html', 
                           usuario=usuario,
                           cursos=cursos)

# =========================================================
# Inscripción de estudiantes a cursos
# =========================================================

@app.route('/cursos/<int:id_curso>/inscribir', methods=['GET', 'POST'])
def inscribir_estudiantes(id_curso):

    if session.get("rol") != "administrativo":
        return redirect(url_for("login"))

    usuario = obtener_usuario_sesion()

    conn = get_db_connection()
    cursor = conn.cursor()

    conexion = get_db_connection()
    cursor = conexion.cursor()

    cursor.execute("""
        SELECT id_curso, nombre_curso, carrera, seccion
        FROM cursos
        WHERE id_curso = %s
    """, (id_curso,))
    curso = cursor.fetchone()

    if not curso:
        cursor.close()
        conexion.close()
        flash('Curso no encontrado.', 'danger')
        return redirect(url_for('listar_cursos'))

    carrera_curso = curso[2]

    if request.method == 'POST':
        estudiantes_seleccionados = request.form.getlist('estudiantes')

        for id_estudiante in estudiantes_seleccionados:
            try:
                cursor.execute("""
                    INSERT INTO inscripciones (id_estudiante, id_curso)
                    VALUES (%s, %s)
                """, (id_estudiante, id_curso))
            except mysql.connector.Error:
                pass

        conexion.commit()
        cursor.close()
        conexion.close()

        flash('Estudiantes inscritos correctamente.', 'success')
        return redirect(url_for('listar_cursos'))

    cursor.execute("""
        SELECT id_persona, carnet, nombre, apellido, correo
        FROM personas
        WHERE tipo_persona = 'estudiante'
          AND carrera = %s
          AND estado = 'activo'
        ORDER BY nombre, apellido
    """, (carrera_curso,))
    estudiantes = cursor.fetchall()

    cursor.execute("""
        SELECT id_estudiante
        FROM inscripciones
        WHERE id_curso = %s
    """, (id_curso,))
    inscritos = [fila[0] for fila in cursor.fetchall()]

    cursor.close()
    conexion.close()

    return render_template(
        'cursos/inscribir.html',
        curso=curso,
        estudiantes=estudiantes,
        inscritos=inscritos,
        usuario=usuario
    )
#=========================================================
#PARA QUE EL CATEDRATICO PUEDA VER SUS CURSOS Y ASISTENCIAS
# =========================================================
@app.route('/mis_cursos')
def mis_cursos():
    if session.get("rol") != "catedratico":
        return redirect(url_for("login"))

    usuario = obtener_usuario_sesion()

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT id_persona
        FROM usuarios
        WHERE id_usuario = %s
    """, (session.get("user_id"),))
    fila = cursor.fetchone()

    if not fila:
        cursor.close()
        conn.close()
        flash("No se encontró el usuario.", "danger")
        return redirect(url_for("login"))

    id_catedratico = fila["id_persona"]

    cursor.close()

    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT id_curso, nombre_curso, carrera, seccion
        FROM cursos
        WHERE id_catedratico = %s
        ORDER BY nombre_curso
    """, (id_catedratico,))
    cursos = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        'catedratico/mis_cursos.html',
        cursos=cursos,
        usuario=usuario
    )
# =========================================================
# PARA VER LAS ASISTENCIASDE LOS CURSOS DEL CATEDRATICO
# =========================================================
from datetime import date
import base64

@app.route('/curso/<int:id_curso>/asistencia', methods=['GET'])
def ver_asistencia_curso(id_curso):

    if session.get("rol") != "catedratico":
        flash('Debe iniciar sesión como catedrático.', 'danger')
        return redirect(url_for('login'))

    usuario = obtener_usuario_sesion()

    if not usuario:
        flash('Debe iniciar sesión.', 'danger')
        return redirect(url_for('login'))

    id_catedratico = usuario["id_persona"]
    fecha_hoy = date.today()

    conexion = get_db_connection()
    cursor = conexion.cursor(dictionary=True)

    # Obtener curso
    cursor.execute("""
        SELECT id_curso, nombre_curso, carrera, seccion
        FROM cursos
        WHERE id_curso = %s AND id_catedratico = %s
    """, (id_curso, id_catedratico))

    curso = cursor.fetchone()

    if not curso:
        cursor.close()
        conexion.close()
        flash('No tiene acceso a este curso.', 'danger')
        return redirect(url_for('mis_cursos'))

    # Obtener estudiantes inscritos
    cursor.execute("""
        SELECT p.id_persona, p.nombre, p.apellido, p.correo, p.foto, p.carnet
        FROM inscripciones i
        JOIN personas p ON i.id_estudiante = p.id_persona
        WHERE i.id_curso = %s
        ORDER BY p.nombre, p.apellido
    """, (id_curso,))

    estudiantes_db = cursor.fetchall()

    estudiantes = []

    for e in estudiantes_db:

        # revisar si tiene registro hoy
        cursor.execute("""
            SELECT ubicacion, tipo_registro, hora
            FROM registros_entrada
            WHERE id_persona = %s AND fecha = %s
            ORDER BY hora DESC
            LIMIT 1
        """, (e["id_persona"], fecha_hoy))

        registro = cursor.fetchone()

        presente = registro is not None

        foto_base64 = None
        if e["foto"]:
            if isinstance(e["foto"], (bytes, bytearray)):
                foto_base64 = base64.b64encode(e["foto"]).decode("utf-8")

        estudiantes.append({
            "id_persona": e["id_persona"],
            "nombre_completo": f'{e["nombre"]} {e["apellido"]}',
            "correo": e["correo"],
            "carnet": e["carnet"],
            "foto": foto_base64,
            "presente": presente,
            "ubicacion": registro["ubicacion"] if registro else None,
            "hora": registro["hora"] if registro else None
        })

    cursor.close()
    conexion.close()

    return render_template(
        'catedratico/arbol_asistencia.html',
        curso=curso,
        estudiantes=estudiantes,
        usuario=usuario
    )
from flask import Flask,  jsonify
# =========================================================
# RUTA DE CONFIRMAR ASISTENCIA
# =========================================================
@app.route('/curso/<int:id_curso>/confirmar_asistencia', methods=['POST'])
def confirmar_asistencia(id_curso):
    if session.get("rol") != "catedratico":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "success": False,
                "message": "Debe iniciar sesión como catedrático.",
                "redirect_url": url_for('login')
            }), 401

        flash('Debe iniciar sesión como catedrático.', 'danger')
        return redirect(url_for('login'))

    usuario = obtener_usuario_sesion()
    if not usuario:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "success": False,
                "message": "Debe iniciar sesión.",
                "redirect_url": url_for('login')
            }), 401

        flash('Debe iniciar sesión.', 'danger')
        return redirect(url_for('login'))

    fecha_hoy = date.today()

    conexion = get_db_connection()
    cursor = conexion.cursor(dictionary=True)

    try:
        # Obtener curso validando que pertenece al catedrático
        cursor.execute("""
            SELECT id_curso, nombre_curso, carrera, seccion, id_catedratico
            FROM cursos
            WHERE id_curso = %s AND id_catedratico = %s
        """, (id_curso, usuario["id_persona"]))
        curso = cursor.fetchone()

        if not curso:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({
                    "success": False,
                    "message": "No tiene acceso a este curso.",
                    "redirect_url": url_for('mis_cursos')
                }), 403

            flash("No tiene acceso a este curso.", "danger")
            return redirect(url_for('mis_cursos'))

        # Obtener datos del docente
        cursor.execute("""
            SELECT id_persona, nombre, apellido, correo
            FROM personas
            WHERE id_persona = %s
        """, (usuario["id_persona"],))
        docente = cursor.fetchone()

        # Obtener estudiantes inscritos
        cursor.execute("""
            SELECT p.id_persona, p.nombre, p.apellido, p.correo, p.carnet
            FROM inscripciones i
            JOIN personas p ON i.id_estudiante = p.id_persona
            WHERE i.id_curso = %s
            ORDER BY p.nombre, p.apellido
        """, (id_curso,))
        estudiantes_db = cursor.fetchall()

        estudiantes_pdf = []

        for e in estudiantes_db:
            cursor.execute("""
                SELECT id_registro, ubicacion, hora
                FROM registros_entrada
                WHERE id_persona = %s AND fecha = %s
                ORDER BY hora DESC
                LIMIT 1
            """, (e["id_persona"], fecha_hoy))
            registro = cursor.fetchone()

            presente = registro is not None
            estado = 'presente' if presente else 'ausente'

            # Insertar asistencia si no existe
            cursor.execute("""
                SELECT id_asistencia
                FROM asistencias
                WHERE id_estudiante = %s AND id_curso = %s AND fecha = %s
            """, (e["id_persona"], id_curso, fecha_hoy))
            existe = cursor.fetchone()

            if not existe:
                cursor.execute("""
                    INSERT INTO asistencias (id_estudiante, id_curso, fecha, estado)
                    VALUES (%s, %s, %s, %s)
                """, (e["id_persona"], id_curso, fecha_hoy, estado))

            estudiantes_pdf.append({
                "id_persona": e["id_persona"],
                "nombre_completo": f"{e['nombre']} {e['apellido']}",
                "correo": e["correo"],
                "carnet": e["carnet"],
                "presente": presente
            })

        conexion.commit()

        # Generar PDF
        ruta_pdf, nombre_archivo = generar_pdf_asistencia(curso, docente, estudiantes_pdf, fecha_hoy)

        # Enviar correo
        try:
            enviar_pdf_por_correo(docente["correo"], ruta_pdf, curso)
        except Exception as e:
            print("Error enviando correo:", e)

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "success": True,
                "message": "La asistencia fue confirmada, el PDF fue generado y el correo enviado.",
                "download_url": url_for('descargar_reporte_asistencia', nombre_archivo=nombre_archivo),
                "redirect_url": url_for('mis_cursos')
            })

        flash("La asistencia fue confirmada correctamente.", "success")
        return redirect(url_for('mis_cursos'))

    except Exception as e:
        conexion.rollback()

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "success": False,
                "message": f"Ocurrió un error: {str(e)}"
            }), 500

        flash(f"Ocurrió un error: {str(e)}", "danger")
        return redirect(url_for('mis_cursos'))

    finally:
        cursor.close()
        conexion.close()
# =========================================================
# DESCARGAR PDF
# =========================================================
@app.route('/descargar_reporte_asistencia/<nombre_archivo>')
def descargar_reporte_asistencia(nombre_archivo):
    carpeta_reportes = os.path.join("static", "reportes")
    ruta_pdf = os.path.join(carpeta_reportes, nombre_archivo)

    if not os.path.exists(ruta_pdf):
        flash("El archivo PDF no existe.", "danger")
        return redirect(url_for('mis_cursos'))

    return send_file(ruta_pdf, as_attachment=True)


# =========================================================
# GENERAR PDF
# =========================================================
def generar_pdf_asistencia(curso, docente, estudiantes, fecha_hoy):
    carpeta_reportes = os.path.join("static", "reportes")
    os.makedirs(carpeta_reportes, exist_ok=True)

    nombre_archivo = f"asistencia_curso_{curso['id_curso']}_{fecha_hoy}.pdf"
    ruta_pdf = os.path.join(carpeta_reportes, nombre_archivo)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Logo
    logo_path = os.path.join("static", "img", "logo.png")
    if os.path.exists(logo_path):
        pdf.image(logo_path, 10, 10, 25)

    # Encabezado
    pdf.set_font("Arial", "B", 16)
    pdf.cell(0, 10, "UNIVERSIDAD MARIANO GALVEZ", ln=True, align="C")
    pdf.cell(0, 10, "DE GUATEMALA", ln=True, align="C")

    pdf.set_font("Times", "", 13)
    pdf.cell(0, 8, "REPORTE DE ASISTENCIA", ln=True, align="C")

    pdf.ln(16)

    # Datos del curso
    pdf.set_font("Arial", "B", 11)

    pdf.cell(35, 8, "Curso:")
    pdf.set_font("Arial", "", 11)
    pdf.cell(70, 8, curso["nombre_curso"])

    pdf.set_font("Arial", "B", 11)
    pdf.cell(30, 8, "Carrera:")
    pdf.set_font("Arial", "", 11)
    pdf.cell(0, 8, curso["carrera"], ln=True)

    pdf.set_font("Arial", "B", 11)
    pdf.cell(35, 8, "Seccion:")
    pdf.set_font("Arial", "", 11)
    pdf.cell(70, 8, curso["seccion"] if curso["seccion"] else "-")

    pdf.set_font("Arial", "B", 11)
    pdf.cell(30, 8, "Fecha:")
    pdf.set_font("Arial", "", 11)
    pdf.cell(0, 8, str(fecha_hoy), ln=True)

    pdf.set_font("Arial", "B", 11)
    pdf.cell(35, 8, "Catedratico:")
    pdf.set_font("Arial", "", 11)
    pdf.cell(70, 8, f"{docente['nombre']} {docente['apellido']}")

    pdf.set_font("Arial", "B", 11)
    pdf.cell(30, 8, "Correo:")
    pdf.set_font("Arial", "", 11)
    pdf.cell(0, 8, docente["correo"], ln=True)

    pdf.ln(16)

    # Tabla
    pdf.set_fill_color(52, 73, 94)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Arial", "B", 10)

    pdf.cell(10, 10, "No", 1, 0, "C", True)
    pdf.cell(30, 10, "Carnet", 1, 0, "C", True)
    pdf.cell(55, 10, "Nombre", 1, 0, "C", True)
    pdf.cell(60, 10, "Correo", 1, 0, "C", True)
    pdf.cell(35, 10, "Estado", 1, 1, "C", True)

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", "", 9)

    for i, est in enumerate(estudiantes, start=1):
        estado = "PRESENTE" if est["presente"] else "AUSENTE"
        nombre = est["nombre_completo"][:28]
        correo = est["correo"][:32] if est["correo"] else ""

        pdf.cell(10, 10, str(i), 1, 0, "C")
        pdf.cell(30, 10, str(est["carnet"]) if est["carnet"] else "-", 1, 0, "C")
        pdf.cell(55, 10, nombre, 1, 0, "L")
        pdf.cell(60, 10, correo, 1, 0, "L")
        pdf.cell(35, 10, estado, 1, 1, "C")

    pdf.ln(10)
    pdf.output(ruta_pdf)

    return ruta_pdf, nombre_archivo


# =========================================================
# ENVIAR PDF POR CORREO
# =========================================================
def enviar_pdf_por_correo(destinatario, ruta_pdf, curso):
    remitente = "16mynorgomez@gmail.com"
    password = "yuef jvmk gisp trtb"

    msg = EmailMessage()
    msg["Subject"] = f"Reporte de asistencia - {curso['nombre_curso']}"
    msg["From"] = remitente
    msg["To"] = destinatario

    msg.set_content(
        f"Adjunto se envia el reporte oficial de asistencia del curso {curso['nombre_curso']}."
    )

    with open(ruta_pdf, "rb") as f:
        pdf_data = f.read()

    msg.add_attachment(
        pdf_data,
        maintype="application",
        subtype="pdf",
        filename=os.path.basename(ruta_pdf)
    )

    contexto = ssl.create_default_context()

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=contexto) as smtp:
        smtp.login(remitente, password)
        smtp.send_message(msg)
# =========================================================
# MAIN
# =========================================================
if __name__ == "__main__":
    start_camera_threads()
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)