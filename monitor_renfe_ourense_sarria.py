"""
Chequea si Renfe puso a la venta el billete Ourense -> Sarria del sabado
12/09/2026 con salida cerca de las 12:32 (la combinacion que conecta con el
AVE Madrid->Ourense de las 12:18, ver memoria project-viaje-camino-santiago).
Si aparece, carga una notificacion en Fosfovita (Supabase, tabla
recordatorios_app) y manda un mail de aviso. Corre via Tarea Programada de
Windows cada 6 horas.

El buscador de Renfe (renfe.com -> venta.renfe.com) es una app clasica
multi-pagina con estado de sesion en un parametro opaco de la URL (?c=...),
no una API JSON reutilizable -> se maneja con un browser headless
(Playwright), llenando origen/destino y avanzando la tira de fechas de a un
dia por vez con el boton "Ir a dia siguiente" hasta llegar a la fecha
buscada.

Credenciales: se leen SIEMPRE de variables de entorno (SUPABASE_URL,
SUPABASE_ANON_KEY, GMAIL_USER, GMAIL_APP_PASSWORD), las carga
run_check.bat (gitignored).
"""
import os
import re
import smtplib
import sys
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText

import requests
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(__file__)
LOG_PATH = os.path.join(BASE_DIR, "check.log")

ORIGEN = "Ourense"
DESTINO = "Sarria"
FECHA_VIAJE = date(2026, 9, 12)
HORA_OBJETIVO = "12:32"
VENTANA_MIN = (11, 30)  # desde 11:30
VENTANA_MAX = (13, 30)  # hasta 13:30

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

ARG_TZ = timezone(timedelta(hours=-3))


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        print(f"ERROR: falta la variable de entorno {name}.", file=sys.stderr)
        sys.exit(1)
    return value


SUPABASE_URL = _require_env("SUPABASE_URL")
SUPABASE_ANON_KEY = _require_env("SUPABASE_ANON_KEY")
SUPABASE_USER = "pablo"
STATE_KEY = f"renfe_{ORIGEN.lower()}_{DESTINO.lower()}_{FECHA_VIAJE.isoformat()}"

GMAIL_USER = _require_env("GMAIL_USER")
GMAIL_APP_PASSWORD = _require_env("GMAIL_APP_PASSWORD")
NOTIFY_TO = "pablofernandez1983@gmail.com"

# (dep_h, dep_m) -> (arr_h, arr_m); DOTALL porque entre ambas horas va la
# duracion del viaje ("1 horas 16 minutos"), que tiene digitos propios y no
# se puede saltar con una clase [^\d].
TIME_PAIR_RE = re.compile(
    r"(\d{1,2}):(\d{2})\s*h.{0,80}?(\d{1,2}):(\d{2})\s*h",
    re.DOTALL,
)


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _supabase_headers() -> dict:
    return {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "Content-Type": "application/json",
    }


def load_state() -> dict:
    try:
        resp = requests.get(
            f"{SUPABASE_URL}/rest/v1/automation_state",
            headers=_supabase_headers(),
            params={"key": f"eq.{STATE_KEY}", "select": "value"},
            timeout=20,
        )
        resp.raise_for_status()
        rows = resp.json()
        if rows:
            return rows[0]["value"]
    except Exception as e:
        log(f"ERROR leyendo estado de Supabase (se asume vacio): {e}")
    return {}


def save_state(value: dict) -> None:
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/automation_state",
        headers={**_supabase_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
        json={"key": STATE_KEY, "value": value, "updated_at": datetime.now(tz=ARG_TZ).isoformat()},
        timeout=20,
    )
    resp.raise_for_status()


def insertar_notificacion(texto: str) -> None:
    fila = {
        "texto": texto,
        "scheduled_at": datetime.now(tz=ARG_TZ).isoformat(),
        "tipo": "notificacion",
        "usuario": SUPABASE_USER,
        "completado": False,
        "borrado": False,
        "sonado": False,
    }
    resp = requests.post(
        f"{SUPABASE_URL}/rest/v1/recordatorios_app",
        headers={**_supabase_headers(), "Prefer": "return=representation"},
        json=[fila],
        timeout=20,
    )
    resp.raise_for_status()
    log("Insertada notificacion en recordatorios_app.")


def send_email(subject: str, body: str) -> None:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = NOTIFY_TO
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.ehlo()
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, NOTIFY_TO, msg.as_string())


def buscar_trenes() -> list:
    """Devuelve una lista de (hora_salida 'HH:MM', hora_llegada 'HH:MM') para
    ORIGEN -> DESTINO en FECHA_VIAJE, navegando el buscador real de Renfe."""
    hoy = date.today()
    dias_a_avanzar = (FECHA_VIAJE - hoy).days
    if dias_a_avanzar < 0:
        log(f"La fecha buscada ({FECHA_VIAJE}) ya paso. No se hace nada.")
        return []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=UA)
        page.goto("https://www.renfe.com/es/es", wait_until="networkidle", timeout=60000)
        try:
            page.click("#onetrust-accept-btn-handler", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(1000)

        page.click("#origin")
        page.type("#origin", ORIGEN, delay=80)
        page.wait_for_timeout(800)
        page.click("#awesomplete_list_1_item_0")
        page.wait_for_timeout(300)

        page.click("#destination")
        page.type("#destination", DESTINO, delay=80)
        page.wait_for_timeout(800)
        page.click("#awesomplete_list_2_item_0")
        page.wait_for_timeout(300)

        btn = page.query_selector("button:has-text('Buscar billete')")
        if btn is None:
            raise RuntimeError("No se encontro el boton 'Buscar billete' en la home de Renfe.")
        with page.expect_navigation(timeout=15000, wait_until="networkidle"):
            btn.click()
        page.wait_for_timeout(2000)

        if dias_a_avanzar > 0:
            for i in range(dias_a_avanzar):
                next_btn = page.query_selector("button.move_to_tomorrow")
                if next_btn is None:
                    log(f"No se pudo seguir avanzando la tira de fechas (dia {i+1}/{dias_a_avanzar}). "
                        f"Puede que Renfe no permita buscar tan lejos todavia.")
                    browser.close()
                    return []
                next_btn.click()
                page.wait_for_timeout(350)
            page.wait_for_timeout(2000)

        body_text = page.inner_text("body")
        browser.close()

    fecha_txt = FECHA_VIAJE.strftime("%d %B").lstrip("0")
    idx = body_text.find("Filtrar")
    idx_fin = body_text.find("Viaje de Ida\n", idx if idx != -1 else 0)
    seccion = body_text[idx:idx_fin] if idx != -1 and idx_fin != -1 else body_text

    return [(f"{d1}:{d2}", f"{a1}:{a2}") for d1, d2, a1, a2 in TIME_PAIR_RE.findall(seccion)]


def en_ventana(hora: str) -> bool:
    h, m = (int(x) for x in hora.split(":"))
    minutos = h * 60 + m
    return (VENTANA_MIN[0] * 60 + VENTANA_MIN[1]) <= minutos <= (VENTANA_MAX[0] * 60 + VENTANA_MAX[1])


def main() -> None:
    estado = load_state()
    if estado.get("notified"):
        log("Ya se habia notificado este viaje anteriormente. No se vuelve a chequear.")
        return

    try:
        trenes = buscar_trenes()
    except Exception as e:
        log(f"ERROR buscando trenes en Renfe: {e}")
        return

    if not trenes:
        log(f"Sin trenes {ORIGEN}->{DESTINO} para {FECHA_VIAJE} (o busqueda vacia). Se reintenta en el proximo chequeo.")
        return

    log(f"Trenes encontrados para {FECHA_VIAJE}: {trenes}")
    candidatos = [t for t in trenes if en_ventana(t[0])]

    if not candidatos:
        log(f"Hay {len(trenes)} tren(es) para {FECHA_VIAJE} pero ninguno sale entre "
            f"{VENTANA_MIN[0]:02d}:{VENTANA_MIN[1]:02d} y {VENTANA_MAX[0]:02d}:{VENTANA_MAX[1]:02d} "
            f"(el de las {HORA_OBJETIVO} todavia no esta a la venta).")
        return

    salida, llegada = candidatos[0]
    texto = (
        f"🚆 ¡Ya salió a la venta el tren {ORIGEN}->{DESTINO} del sábado "
        f"{FECHA_VIAJE.strftime('%d/%m')}! Sale {salida} hs, llega {llegada} hs. "
        f"Comprá en venta.renfe.com."
    )
    try:
        insertar_notificacion(texto)
    except Exception as e:
        log(f"ERROR insertando notificacion en Supabase: {e}")
        return

    try:
        send_email(
            "🚆 Tren Ourense -> Sarria disponible",
            f"{texto}\n\nSe cargó una notificación en Fosfovita.\n"
            f"Buscar y comprar en: https://www.renfe.com/es/es",
        )
    except Exception as e:
        log(f"ERROR mandando mail de aviso (la notificacion en Fosfovita si se cargo): {e}")

    save_state({"notified": True, "salida": salida, "llegada": llegada, "detectado": datetime.now(tz=ARG_TZ).isoformat()})
    log("Notificacion enviada y estado guardado.")


if __name__ == "__main__":
    main()
    sys.exit(0)
