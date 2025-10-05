import time
import os
import threading
import random
import smtplib
import psutil
import json
from collections import deque
from functools import wraps
from email.mime.text import MIMEText
from flask import Flask, request, Response
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from openai import OpenAI

# ---------------- Events & Globals ----------------
auto_like_pause_event = threading.Event()
auto_like_pause_event.set()  # Par défaut, auto-like actif

net_stats = {"last_bytes_sent": 0, "last_bytes_recv": 0}
bandwidth_data = {"time": [], "upload": [], "download": []}

script_dir = os.path.dirname(os.path.abspath(__file__))
config_perso_path = os.path.join(script_dir, "config_perso.json")
config_path = os.path.join(script_dir, "config.json")

# Charger d'abord config.json (paramètres par défaut)
with open(config_path, "r", encoding="utf-8") as f:
    config = json.load(f)

# Puis surcharger avec config_perso.json s'il existe
if os.path.exists(config_perso_path):
    with open(config_perso_path, "r", encoding="utf-8") as f:
        config_perso = json.load(f)
    config.update(config_perso)

# ---- Identifiants de base ----
USERNAME = config["USERNAME"]
PASSWORD = config["PASSWORD"]
EMAIL_SENDER = config["EMAIL_SENDER"]
EMAIL_PASSWORD = config["EMAIL_PASSWORD"]
EMAIL_RECEIVER = config["EMAIL_RECEIVER"]
EMAIL_LOGIN_TIKTOK = config["EMAIL_LOGIN_TIKTOK"]
EMAIL_PASSWORD_TIKTOK = config["EMAIL_PASSWORD_TIKTOK"]

# ---- ChatGPT Config ----
OPENAI_API_KEY = config.get("OPENAI_API_KEY", "")
CHATGPT_MODEL = config.get("CHATGPT_MODEL", "gpt-5-nano")  # ex: "gpt-4o-mini"
CHATGPT_SYSTEM_PROMPT = config.get(
    "CHATGPT_SYSTEM_PROMPT",
    "Tu es un assistant TikTok, sympathique, concis et engageant."
)
ENABLE_AUTO_CHATGPT = config.get("ENABLE_AUTO_CHATGPT", False)
CHATGPT_MIN_INTERVAL = config.get("CHATGPT_MIN_INTERVAL", 4)  # secondes min entre réponses envoyées
CHATGPT_MAX_INTERVAL = config.get("CHATGPT_MAX_INTERVAL", 8)  # secondes max entre réponses envoyées

_effective_api_key = OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")
client = OpenAI(api_key=_effective_api_key) if _effective_api_key else None

# ---- Bot Config ----
WINDOW_SIZE = tuple(config["WINDOW_SIZE"])
CLICK_INTERVAL_MIN = config["CLICK_INTERVAL_MIN"]
CLICK_INTERVAL_MAX = config["CLICK_INTERVAL_MAX"]
HUMAN_PAUSE_FREQ_MIN = config["HUMAN_PAUSE_FREQ_MIN"]
HUMAN_PAUSE_FREQ_MAX = config["HUMAN_PAUSE_FREQ_MAX"]
HUMAN_PAUSE_MIN = config["HUMAN_PAUSE_MIN"]
HUMAN_PAUSE_MAX = config["HUMAN_PAUSE_MAX"]
CLEAR_INTERVAL = config["CLEAR_INTERVAL"]
HUMAN_DELAYS = config["HUMAN_DELAYS"]
REFRESH_INTERVAL = 20 * 60  # 20 minutes

# ---- Auto Messages (manuels) ----
AUTO_MESSAGES = config.get("AUTO_MESSAGES", [])
ENABLE_AUTO_MESSAGES = config.get("ENABLE_AUTO_MESSAGES", False)

running = False
driver = None
current_live = "https://www.tiktok.com/"
ngrok_url = None
status_message = "Bot en attente..."
likes_sent = 0
bot_start_time = None
next_pause_time = None

app = Flask(__name__)

# ============== Helpers & Utilities ==============
def save_config_to_json():
    global config, AUTO_MESSAGES, ENABLE_AUTO_MESSAGES, ENABLE_AUTO_CHATGPT, CHATGPT_MODEL, CHATGPT_SYSTEM_PROMPT
    try:
        config["AUTO_MESSAGES"] = AUTO_MESSAGES
        config["ENABLE_AUTO_MESSAGES"] = ENABLE_AUTO_MESSAGES
        config["ENABLE_AUTO_CHATGPT"] = ENABLE_AUTO_CHATGPT
        config["CHATGPT_MODEL"] = CHATGPT_MODEL
        config["CHATGPT_SYSTEM_PROMPT"] = CHATGPT_SYSTEM_PROMPT
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        set_status("✅ Configuration sauvegardée dans le JSON")
        return True
    except Exception as e:
        set_status(f"⚠️ Erreur sauvegarde JSON : {e}")
        return False

def set_status(msg):
    global status_message
    status_message = msg
    print(msg)

def send_email_alert(subject, body):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_SENDER
        msg["To"] = EMAIL_RECEIVER
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        set_status(f"✉️ Email envoyé : {subject}")
    except Exception as e:
        set_status(f"⚠️ Erreur envoi email : {e}")

def get_human_delay():
    base = random.choice(HUMAN_DELAYS)
    variation = random.uniform(-5, 5)
    delay = max(100, base + variation)
    return delay / 1000.0

def try_action(description, func, retries=3, wait=2, fatal=True):
    for attempt in range(1, retries + 1):
        try:
            func()
            set_status(f"✔️ {description} réussie (tentative {attempt})")
            return True
        except Exception as e:
            set_status(f"⚠️ {description} échouée (tentative {attempt}): {e}")
            time.sleep(wait)
    if fatal:
        send_email_alert("⚠️ Bot TikTok - Échec critique", f"L'étape '{description}' a échoué après {retries} tentatives.")
    return False

def get_bandwidth():
    global net_stats
    counters = psutil.net_io_counters()
    sent = counters.bytes_sent
    recv = counters.bytes_recv
    if net_stats["last_bytes_sent"] == 0:
        net_stats["last_bytes_sent"] = sent
        net_stats["last_bytes_recv"] = recv
        return 0, 0
    upload = (sent - net_stats["last_bytes_sent"]) / 1024
    download = (recv - net_stats["last_bytes_recv"]) / 1024
    net_stats["last_bytes_sent"] = sent
    net_stats["last_bytes_recv"] = recv
    return upload, download

# ============== Flask Auth ==============
def check_auth(username, password):
    return username == USERNAME and password == PASSWORD

def authenticate():
    return Response('Authentification requise', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# ============== Core Bot ==============
def toggle_running():
    global running, bot_start_time
    running = not running
    if running:
        if not bot_start_time:
            bot_start_time = time.time()
        set_status("▶️ Bot activé")
    else:
        set_status("⏸️ Bot en pause")

def auto_like():
    global running, driver, likes_sent, next_pause_time, auto_like_pause_event
    actions = None
    next_pause_time = time.time() + random.randint(HUMAN_PAUSE_FREQ_MIN, HUMAN_PAUSE_FREQ_MAX)
    while True:
        if running and driver:
            auto_like_pause_event.wait()
            if not actions:
                actions = ActionChains(driver)
            try:
                if "live terminé" in driver.page_source.lower():
                    set_status("⚠️ Live terminé détecté !")
                    send_email_alert("Bot TikTok - Live terminé", f"Le live {current_live} est terminé.")
                    running = False
                    continue
                if random.random() < 0.9:
                    actions.send_keys("l").perform()
                    likes_sent += 1
                    set_status(f"💖 Like #{likes_sent}")
                else:
                    set_status("⏭️ Like sauté (simulation humaine)")
            except Exception as e:
                set_status(f"⚠️ Erreur auto_like: {e}")
            if time.time() >= next_pause_time:
                pause_duration = random.randint(HUMAN_PAUSE_MIN, HUMAN_PAUSE_MAX)
                set_status(f"⏸️ Pause humaine pour {pause_duration} sec...")
                time.sleep(pause_duration)
                next_pause_time = time.time() + random.randint(HUMAN_PAUSE_FREQ_MIN, HUMAN_PAUSE_FREQ_MAX)
            time.sleep(get_human_delay())
        else:
            time.sleep(0.1)

def auto_message_loop():
    global ENABLE_AUTO_MESSAGES, AUTO_MESSAGES
    while True:
        if ENABLE_AUTO_MESSAGES and running and driver and AUTO_MESSAGES:
            msg = random.choice(AUTO_MESSAGES)
            send_message_to_tiktok(msg)
            delay = random.randint(config.get("AUTO_MESSAGE_DELAY_MIN", 30), config.get("AUTO_MESSAGE_DELAY_MAX", 120))
            set_status(f"💬 Prochain auto-message dans {delay}s")
            time.sleep(delay)
        else:
            time.sleep(1)

def launch_driver():
    """
    Lance Selenium avec undetected_chromedriver en alignant ChromeDriver
    sur la version majeure de Chrome détectée (Windows), puis ouvre le live
    et exécute la séquence de connexion TikTok existante.
    """
    import os
    import time
    import subprocess

    # Imports internes pour la détection Windows (registry)
    try:
        import winreg
    except Exception:
        winreg = None

    import undetected_chromedriver as uc
    global driver, current_live

    # 1) Fermer l'instance existante si présente
    if driver:
        try:
            driver.quit()
        except:
            pass
    time.sleep(0.5)

    # 2) Détection de la version majeure de Chrome et du binaire
    def _detect_chrome_major_and_path():
        """
        Retourne (major_version:int|None, chrome_path:str|None)
        Tentatives:
        - Registre Windows (HKCU/HKLM) BLBeacon.version
        - Chemins connus (Program Files / Program Files (x86))
        - Appel --version
        """
        def _parse_major(v):
            try:
                return int(v.split('.')[0])
            except Exception:
                return None

        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]

        # a) Registre Windows
        if winreg is not None:
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for subkey in (
                    r"SOFTWARE\Google\Chrome\BLBeacon",
                    r"SOFTWARE\WOW6432Node\Google\Chrome\BLBeacon",
                ):
                    try:
                        with winreg.OpenKey(root, subkey) as k:
                            ver, _ = winreg.QueryValueEx(k, "version")
                            major = _parse_major(ver)
                            # Essayer de retrouver le chemin par défaut si possible
                            for cp in chrome_paths:
                                if os.path.exists(cp):
                                    return major, cp
                            return major, None
                    except Exception:
                        pass

        # b) Chemins connus
        for cp in chrome_paths:
            if os.path.exists(cp):
                # Essayer d'obtenir la version via --version
                try:
                    out = subprocess.run([cp, "--version"], capture_output=True, text=True, timeout=5)
                    ver_str = (out.stdout or out.stderr or "").strip()
                    # Exemple: "Google Chrome 140.0.7339.207"
                    parts = ver_str.split()
                    ver = parts[-1] if parts else ""
                    major = _parse_major(ver)
                    return major, cp
                except Exception:
                    return None, cp

        # c) Fallback: rien trouvé
        return None, None

    major, chrome_path = _detect_chrome_major_and_path()

    # 3) Options UC
    options = uc.ChromeOptions()

    # Fixer explicitement le binaire si trouvé
    if chrome_path and os.path.exists(chrome_path):
        # Compatible Selenium/ChromeOptions
        options.binary_location = chrome_path

    # 4) Instanciation UC en forçant version_main si détecté
    kwargs = {}
    if major:
        kwargs["version_main"] = major  # Aligne ChromeDriver sur la version majeure du Chrome installé

    driver = uc.Chrome(options=options, **kwargs)

    # 5) Fenêtre et navigation initiale
    time.sleep(0.5)
    driver.set_window_size(WINDOW_SIZE[0], WINDOW_SIZE[1])
    time.sleep(0.5)
    driver.set_window_position(100, 100)
    time.sleep(0.5)
    driver.get(current_live)
    time.sleep(3)
    time.sleep(0.5)
    driver.refresh()

    set_status("🔄 Page rafraîchie")
    time.sleep(7)
    time.sleep(0.5)

    # 6) Séquence de connexion TikTok (reprend votre logique existante)
    try_action("Bouton 'Se connecter'", lambda: driver.find_element(
        "xpath", "//div[text()='Se connecter']/ancestor::button").click())
    time.sleep(0.5)

    try_action("Option 'Utiliser téléphone/email'", lambda: driver.find_element(
        "xpath", "//div[contains(text(),\"Utiliser le téléphone/l'e-mail\")]").click())
    time.sleep(0.5)

    try_action("Lien 'Connexion email'", lambda: driver.find_element(
        "xpath", "//a[contains(@href,'/login/phone-or-email/email')]").click())

    def fill_email():
        email_input = driver.find_element("xpath", "//input[@placeholder=\"E-mail ou nom d'utilisateur\"]")
        email_input.clear()
        email_input.send_keys(EMAIL_LOGIN_TIKTOK)
        time.sleep(0.5)

    try_action("Remplissage Email", fill_email)

    def fill_password():
        password_input = driver.find_element("xpath", "//input[@placeholder='Mot de passe']")
        password_input.clear()
        password_input.send_keys(EMAIL_PASSWORD_TIKTOK)
        time.sleep(0.5)

    try_action("Remplissage Mot de passe", fill_password)
    time.sleep(0.5)

    try_action("Bouton 'Se connecter' final", lambda: driver.find_element(
        "xpath", "//button[@data-e2e='login-button']").click())

def refresh_live_loop():
    global driver, current_live
    while True:
        time.sleep(REFRESH_INTERVAL)
        try:
            if driver:
                live_url = driver.current_url
                set_status("♻️ Rafraîchissement automatique du live...")
                driver.get(live_url)
                time.sleep(5)
                set_status(f"✅ Live rechargé : {live_url}")
                send_email_alert("Bot TikTok - Rafraîchissement", f"Le live a été rechargé : {live_url}")
            else:
                set_status("⚠️ Aucun driver actif pour rafraîchir le live.")
        except Exception as e:
            set_status(f"⚠️ Erreur refresh_live_loop : {e}")

def send_message_to_tiktok(msg):
    global driver, auto_like_pause_event
    if driver:
        try:
            auto_like_pause_event.clear()
            set_status("⏸️ Auto-like en pause pour envoi message...")
            time.sleep(0.5)
            chat_box = driver.find_element(
                "xpath",
                "//div[@contenteditable='plaintext-only' and @placeholder='Saisis ton message...']"
            )
            chat_box.click()
            time.sleep(get_human_delay())
            chat_box.send_keys(msg)
            time.sleep(get_human_delay())
            chat_box.send_keys(Keys.ENTER)
            set_status(f"💬 Message envoyé : {msg}")
            time.sleep(get_human_delay())
        except Exception as e:
            set_status(f"⚠️ Erreur envoi message : {e}")
        finally:
            auto_like_pause_event.set()
            set_status("▶️ Auto-like réactivé après envoi message")
    else:
        set_status("⚠️ Driver non lancé, impossible d'envoyer le message.")

# ============== ChatGPT Integration ==============
client = None
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)

def chatgpt_generate_reply(user_text, previous_dialog=None):
    if client is None:
        return None
    messages = [{"role": "system", "content": CHATGPT_SYSTEM_PROMPT}]
    previous_dialog = previous_dialog or []
    for turn in previous_dialog[-6:]:
        messages.append({"role": "user", "content": turn.get("user", "")})
        messages.append({"role": "assistant", "content": turn.get("assistant", "")})
    messages.append({"role": "user", "content": user_text})
    try:
        comp = client.chat.completions.create(
            model=CHATGPT_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=120
        )
        reply = comp.choices[0].message.content.strip()
        return reply
    except Exception as e:
        set_status(f"⚠️ Erreur ChatGPT: {e}")
        return None

# Extraction des commentaires (DOM variable selon TikTok)
def get_live_comments(driver):
    results = []
    try:
        # Fallback 1: data-e2e chatroom
        nodes = driver.find_elements(By.XPATH, "//*[contains(@data-e2e,'chat')]")
        for n in nodes:
            try:
                txt = n.text.strip()
                if txt and len(txt) < 300:
                    results.append({"user": "", "content": txt})
            except Exception:
                pass
        # Fallback 2: classes génériques (à ajuster selon DOM réel)
        items = driver.find_elements(By.CSS_SELECTOR, ".comment-item, .css-*, [class*='comment']")
        for c in items:
            try:
                content = c.text.strip()
                if content and len(content) < 300:
                    results.append({"user": "", "content": content})
            except Exception:
                pass
    except Exception:
        pass
    # Dédupliquer par contenu
    unique = []
    seen = set()
    for r in results:
        key = r["content"]
        if key not in seen:
            unique.append(r)
            seen.add(key)
    return unique

# Boucle IA: lire commentaires → générer → envoyer
def live_reply_loop():
    global ENABLE_AUTO_CHATGPT, driver, running
    dialog_by_user = {}
    seen_last = deque(maxlen=200)
    while True:
        try:
            # Activation condition modifiée pour n'activer que si bot lancé
            if ENABLE_AUTO_CHATGPT and running and driver:
                comments = get_live_comments(driver)
                for com in comments:
                    content = com.get("content", "").strip()
                    user = com.get("user", "").strip() or "viewer"
                    if not content or content in seen_last:
                        continue
                    seen_last.append(content)
                    history = dialog_by_user.get(user, [])
                    reply = chatgpt_generate_reply(content, previous_dialog=history)
                    if reply:
                        send_message_to_tiktok(reply)
                        history.append({"user": content, "assistant": reply})
                        dialog_by_user[user] = history[-10:]
                        time.sleep(random.uniform(CHATGPT_MIN_INTERVAL, CHATGPT_MAX_INTERVAL))
            time.sleep(2)
        except Exception as e:
            set_status(f"⚠️ Erreur live_reply_loop: {e}")
            time.sleep(2)
