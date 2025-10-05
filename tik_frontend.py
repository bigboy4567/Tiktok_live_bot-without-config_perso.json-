import sys
import time
import os
import subprocess
import requests
import threading
import tik_backend
from flask import render_template_string, request
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QLabel,
    QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QCheckBox,
    QListWidget, QMessageBox, QFrame, QDialog
)
import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg

# Importer les fonctions et variables pour un accès direct
from tik_backend import (
    # Fonctions
    save_config_to_json,
    set_status,
    send_email_alert,
    get_human_delay,
    try_action,
    get_bandwidth,
    check_auth,
    authenticate,
    requires_auth,
    toggle_running,
    auto_like,
    auto_message_loop,
    launch_driver,
    refresh_live_loop,
    send_message_to_tiktok,
    chatgpt_generate_reply,
    get_live_comments,
    live_reply_loop,
    # Variables globales de configuration
    AUTO_MESSAGES,
    CHATGPT_MAX_INTERVAL,
    CHATGPT_MIN_INTERVAL,
    CHATGPT_MODEL,
    CHATGPT_SYSTEM_PROMPT,
    CLEAR_INTERVAL,
    CLICK_INTERVAL_MAX,
    CLICK_INTERVAL_MIN,
    EMAIL_LOGIN_TIKTOK,
    EMAIL_PASSWORD,
    EMAIL_PASSWORD_TIKTOK,
    EMAIL_RECEIVER,
    EMAIL_SENDER,
    ENABLE_AUTO_CHATGPT,
    ENABLE_AUTO_MESSAGES,
    HUMAN_DELAYS,
    HUMAN_PAUSE_FREQ_MAX,
    HUMAN_PAUSE_FREQ_MIN,
    HUMAN_PAUSE_MAX,
    HUMAN_PAUSE_MIN,
    OPENAI_API_KEY,
    PASSWORD,
    REFRESH_INTERVAL,
    USERNAME,
    WINDOW_SIZE,
    # Variables d'état
    likes_sent,
    bot_start_time,
    next_pause_time,
    status_message,
    client,
    running,
    bandwidth_data,
    app
)

# ============== PyQt6 UI ==============

class CharLimitDialog(QDialog):
    def __init__(self, parent=None, max_chars=100, initial_text=""):
        super().__init__(parent)
        self.setWindowTitle("Nouveau Message")
        self.max_chars = max_chars
        
        # Layout principal
        layout = QVBoxLayout()
        
        # Label d'instruction
        label = QLabel("Entrez le message :")
        layout.addWidget(label)
        
        # Champ de saisie
        self.text_input = QLineEdit()
        self.text_input.setText(initial_text)  # ✅ On pré-remplit le texte ici
        layout.addWidget(self.text_input)
        
        # Compteur de caractères
        current_len = len(initial_text)
        self.char_counter = QLabel(f"{current_len} / {max_chars} caractères")
        self.char_counter.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.char_counter.setStyleSheet("color: green;")
        layout.addWidget(self.char_counter)
        
        # Boutons OK et Annuler
        btn_layout = QHBoxLayout()
        self.btn_ok = QPushButton("OK")
        self.btn_cancel = QPushButton("Annuler")
        btn_layout.addWidget(self.btn_ok)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)
        
        self.setLayout(layout)
        
        # Connexions
        self.text_input.textChanged.connect(self.update_counter)
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel.clicked.connect(self.reject)
        
        self.setMinimumWidth(400)
    
    def update_counter(self, text):
        current_len = len(text)
        self.char_counter.setText(f"{current_len} / {self.max_chars} caractères")
        
        # Couleur verte si OK, rouge si dépassement
        if current_len > self.max_chars:
            self.char_counter.setStyleSheet("color: red; font-weight: bold;")
        else:
            self.char_counter.setStyleSheet("color: green;")
    
    def get_text(self):
        return self.text_input.text()

class BotWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🖥️ Contrôle TikTok Bot (PyQt6)")
        self.resize(840, 900)

        # Style global (Fusion + palette sombre)
        app = QApplication.instance()
        if app is not None:
            app.setStyle("Fusion")
            palette = QPalette()
            # Fonds
            palette.setColor(QPalette.ColorRole.Window, QColor(17, 17, 17))       # #111
            palette.setColor(QPalette.ColorRole.Base, QColor(27, 27, 31))         # #1b1b1f
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor(24, 24, 28))
            # Texte
            palette.setColor(QPalette.ColorRole.WindowText, QColor(234, 234, 234))
            palette.setColor(QPalette.ColorRole.Text, QColor(234, 234, 234))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor(234, 234, 234))
            palette.setColor(QPalette.ColorRole.ToolTipText, QColor(234, 234, 234))
            # Boutons et éléments
            palette.setColor(QPalette.ColorRole.Button, QColor(32, 32, 36))
            palette.setColor(QPalette.ColorRole.Highlight, QColor(0, 242, 234))   # #00f2ea accent
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
            app.setPalette(palette)

        # Feuille de style applicative
        self.setStyleSheet(self._qss())

        # Contenu principal: onglets
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.setCentralWidget(self.tabs)

        # Onglet Contrôle
        self.tab_control = QWidget()
        self.tabs.addTab(self.tab_control, "🎮 Contrôle")
        self._build_control_tab()

        # Onglet Messages
        self.tab_messages = QWidget()
        self.tabs.addTab(self.tab_messages, "💬 Messages")
        self._build_messages_tab()

        # Timer UI
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.update_stats_ui)
        self.ui_timer.start(1000)

    def _header(self, parent_layout: QVBoxLayout, title_text: str, subtitle_text: str = ""):
        wrap = QVBoxLayout()
        lbl_title = QLabel(title_text)
        lbl_title.setObjectName("pageTitle")
        wrap.addWidget(lbl_title, alignment=Qt.AlignmentFlag.AlignHCenter)
        if subtitle_text:
            lbl_sub = QLabel(subtitle_text)
            lbl_sub.setObjectName("pageSubtitle")
            wrap.addWidget(lbl_sub, alignment=Qt.AlignmentFlag.AlignHCenter)
        parent_layout.addLayout(wrap)

    def _card(self, parent_layout: QVBoxLayout, title: str | None = None) -> QVBoxLayout:
        frame = QFrame()
        frame.setObjectName("card")
        v = QVBoxLayout(frame)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(10)
        if title:
            t = QLabel(title)
            t.setObjectName("cardTitle")
            v.addWidget(t)
        parent_layout.addWidget(frame)
        return v

    def _build_control_tab(self):
        global ENABLE_AUTO_MESSAGES, ENABLE_AUTO_CHATGPT, CHATGPT_MODEL

        base = QVBoxLayout(self.tab_control)
        base.setContentsMargins(16, 16, 16, 16)
        base.setSpacing(14)

        self._header(
            base,
            "🚀 Panel de contrôle TikTok Bot",
            "Actions rapides, IA et télémétrie en temps réel"
        )

        # Carte: Envoi manuel
        card_send = self._card(base, "Envoi manuel")
        hl_msg = QHBoxLayout()
        self.msg_edit = QLineEdit()
        self.msg_edit.setPlaceholderText("Message à envoyer…")
        btn_send = QPushButton("💬 Envoyer")
        btn_send.setObjectName("accentButton")
        btn_send.clicked.connect(self.on_send_message)
        hl_msg.addWidget(self.msg_edit)
        hl_msg.addWidget(btn_send)
        card_send.addLayout(hl_msg)

        # Carte: Auto-like
        card_like = self._card(base, "Auto-like")
        hl_btns = QHBoxLayout()
        btn_start = QPushButton("▶️ Démarrer")
        btn_start.setObjectName("accentButton")
        btn_stop = QPushButton("⏸️ Arrêter")
        btn_stop.setObjectName("dangerButton")
        btn_start.clicked.connect(lambda: self.set_running(True))
        btn_stop.clicked.connect(lambda: self.set_running(False))
        hl_btns.addWidget(btn_start)
        hl_btns.addWidget(btn_stop)
        card_like.addLayout(hl_btns)

        # Carte: Automations & IA
        card_toggles = self._card(base, "Automations & IA")
        self.chk_auto = QCheckBox("Activer l’envoi automatique de messages (liste)")
        self.chk_auto.setChecked(ENABLE_AUTO_MESSAGES)
        self.chk_auto.stateChanged.connect(self.on_toggle_auto_messages)
        card_toggles.addWidget(self.chk_auto)

        self.chk_ai = QCheckBox("Activer réponses IA (ChatGPT) aux commentaires")
        self.chk_ai.setChecked(ENABLE_AUTO_CHATGPT)
        self.chk_ai.stateChanged.connect(self.on_toggle_ai)
        card_toggles.addWidget(self.chk_ai)

        row_ai = QHBoxLayout()
        self.model_edit = QLineEdit(CHATGPT_MODEL)
        self.model_edit.setPlaceholderText("Modèle ChatGPT (ex: gpt-5-nano, gpt-4o-mini)")
        btn_set_model = QPushButton("💾 Enregistrer modèle")
        btn_set_model.setObjectName("ghostButton")
        btn_set_model.clicked.connect(self.on_save_model)
        row_ai.addWidget(self.model_edit)
        row_ai.addWidget(btn_set_model)
        card_toggles.addLayout(row_ai)

        # Carte: Statistiques & statut
        card_stats = self._card(base, "Statistiques")
        badges = QHBoxLayout()
        self.lbl_auto_status = QLabel("Auto-messages : OFF")
        self.lbl_auto_status.setObjectName("badge")
        self.lbl_ai_status = QLabel("IA (ChatGPT) : OFF")
        self.lbl_ai_status.setObjectName("badge")
        self.lbl_msg_count = QLabel("Messages configurés : 0")
        self.lbl_msg_count.setObjectName("badge")
        badges.addWidget(self.lbl_auto_status)
        badges.addWidget(self.lbl_ai_status)
        badges.addWidget(self.lbl_msg_count)
        card_stats.addLayout(badges)

        grid = QVBoxLayout()
        self.lbl_likes = QLabel("Likes envoyés : 0")
        self.lbl_uptime = QLabel("Temps de fonctionnement : 0s")
        self.lbl_next_pause = QLabel("Prochaine pause : -")
        self.lbl_status = QLabel("Status: En attente...")
        for lab in [self.lbl_likes, self.lbl_uptime, self.lbl_next_pause, self.lbl_status]:
            lab.setObjectName("statLine")
            grid.addWidget(lab)
        card_stats.addLayout(grid)

        # Graphe bande passante harmonisé au thème
        self.fig = Figure(figsize=(6.6, 1.8), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_ylim(0, 100)
        self.ax.set_title("Upload / Download (KB/s)", fontsize=9, color="#eaeaea")
        self.ax.set_facecolor("#1b1b1f")
        self.ax.get_xaxis().set_visible(False)
        for spine in self.ax.spines.values():
            spine.set_color("#2a2a2a")
        self.ax.tick_params(colors="#b7b7b7")
        self.canvas = FigureCanvasQTAgg(self.fig)
        card_stats.addWidget(self.canvas)

    def _build_messages_tab(self):
        global AUTO_MESSAGES
        base = QVBoxLayout(self.tab_messages)
        base.setContentsMargins(16, 16, 16, 16)
        base.setSpacing(14)

        self._header(base, "🎯 Gestion des Messages Automatiques", "Ajouter, modifier et prioriser les messages")

        # Carte: compteur
        card_info = self._card(base)
        self.lbl_count = QLabel(f"Messages configurés : {len(AUTO_MESSAGES)}")
        self.lbl_count.setObjectName("badge")
        card_info.addWidget(self.lbl_count)

        # Carte: liste + actions
        card_list = self._card(base, "Messages")
        self.list_messages = QListWidget()
        card_list.addWidget(self.list_messages)
        self.refresh_messages_list()

        row = QHBoxLayout()
        btn_add = QPushButton("➕ Ajouter")
        btn_add.setObjectName("accentButton")
        btn_edit = QPushButton("✏️ Modifier")
        btn_edit.setObjectName("ghostButton")
        btn_del = QPushButton("🗑️ Supprimer")
        btn_del.setObjectName("dangerButton")
        btn_clear = QPushButton("🧹 Tout effacer")
        btn_clear.setObjectName("ghostButton")
        btn_add.clicked.connect(self.add_message)
        btn_edit.clicked.connect(self.edit_message)
        btn_del.clicked.connect(self.delete_message)
        btn_clear.clicked.connect(self.clear_all_messages)
        for b in (btn_add, btn_edit, btn_del, btn_clear):
            row.addWidget(b)
        card_list.addLayout(row)

    # ---- Contrôle ----
    def on_send_message(self):
        txt = self.msg_edit.text().strip()
        if txt:
            send_message_to_tiktok(txt)
            self.msg_edit.clear()

    def set_running(self, val: bool):
        global running, bot_start_time
        tik_backend.running = val
        if running and not bot_start_time:
            bot_start_time = time.time()
        set_status("▶️ Auto-like démarré" if running else "⏸️ Auto-like arrêté")

    def on_toggle_auto_messages(self, state):
        global ENABLE_AUTO_MESSAGES
        tik_backend.ENABLE_AUTO_MESSAGES = (state == Qt.CheckState.Checked.value)
        save_config_to_json()
        set_status(f"🔁 Auto-messages {'activés' if ENABLE_AUTO_MESSAGES else 'désactivés'} et sauvegardé")

    def on_toggle_ai(self, state):
        global ENABLE_AUTO_CHATGPT, running
        tik_backend.ENABLE_AUTO_CHATGPT = (state == Qt.CheckState.Checked.value)
        save_config_to_json()
        if ENABLE_AUTO_CHATGPT and not running:
            set_status("🧠 IA armée (en attente). Lance le bot pour activer les réponses IA.")
        else:
            set_status(f"🧠 IA (ChatGPT) {'activée' if ENABLE_AUTO_CHATGPT else 'désactivée'} et sauvegardée")

    def on_save_model(self):
        global CHATGPT_MODEL
        model = self.model_edit.text().strip()
        if model:
            tik_backend.CHATGPT_MODEL = model
            save_config_to_json()
            set_status(f"💾 Modèle ChatGPT sauvegardé: {CHATGPT_MODEL}")

    # ---- Messages ----
    def refresh_messages_list(self):
        global AUTO_MESSAGES
        self.list_messages.clear()
        for i, msg in enumerate(AUTO_MESSAGES, 1):
            display = msg[:60] + "..." if len(msg) > 60 else msg
            self.list_messages.addItem(f"{i}. {display}")
        if hasattr(self, "lbl_count") and self.lbl_count is not None:
            self.lbl_count.setText(f"Messages configurés : {len(AUTO_MESSAGES)}")

    def add_message(self):
        global AUTO_MESSAGES
        
        # Utiliser le dialogue personnalisé
        dialog = CharLimitDialog(self, max_chars=100)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_msg = dialog.get_text().strip()
            
            # Vérifier la longueur
            if len(new_msg) > 100:
                QMessageBox.warning(self, "Validation", "Le message ne peut pas dépasser 100 caractères.")
                return
            
            if new_msg:
                AUTO_MESSAGES.append(new_msg)
                self.refresh_messages_list()
                save_config_to_json()
                set_status(f"✅ Message ajouté et sauvegardé : {new_msg[:30]}...")
            else:
                QMessageBox.warning(self, "Validation", "Le message ne peut pas être vide.")

    def edit_message(self):
        global AUTO_MESSAGES
        current = self.list_messages.currentRow()
        if current < 0 or current >= len(AUTO_MESSAGES):
            QMessageBox.warning(self, "Sélection", "Veuillez sélectionner un message à modifier.")
            return
        
        # Utiliser le dialogue personnalisé avec le texte existant
        dialog = CharLimitDialog(self, max_chars=100, initial_text=AUTO_MESSAGES[current])
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_msg = dialog.get_text().strip()
            
            # Vérifier la longueur
            if len(new_msg) > 100:
                QMessageBox.warning(self, "Validation", "Le message ne peut pas dépasser 100 caractères.")
                return
            
            if new_msg:
                AUTO_MESSAGES[current] = new_msg
                self.refresh_messages_list()
                save_config_to_json()
                set_status("✅ Message modifié et sauvegardé")
            else:
                QMessageBox.warning(self, "Validation", "Le message ne peut pas être vide.")

    def delete_message(self):
        global AUTO_MESSAGES
        current = self.list_messages.currentRow()
        if current < 0 or current >= len(AUTO_MESSAGES):
            QMessageBox.warning(self, "Sélection", "Veuillez sélectionner un message à supprimer.")
            return
        confirm = QMessageBox.question(self, "Confirmation", "Êtes-vous sûr de vouloir supprimer ce message ?")
        if confirm == QMessageBox.StandardButton.Yes:
            del AUTO_MESSAGES[current]
            self.refresh_messages_list()
            save_config_to_json()
            set_status("🗑️ Message supprimé et sauvegardé")

    def clear_all_messages(self):
        global AUTO_MESSAGES
        confirm = QMessageBox.question(self, "Confirmation", "Êtes-vous sûr de vouloir supprimer TOUS les messages ?")
        if confirm == QMessageBox.StandardButton.Yes:
            AUTO_MESSAGES.clear()
            self.refresh_messages_list()
            save_config_to_json()
            set_status("🗑️ Tous les messages supprimés et sauvegardés")

    # ---- UI refresh ----
    def update_stats_ui(self):
        global likes_sent, bot_start_time, next_pause_time
        global ENABLE_AUTO_MESSAGES, ENABLE_AUTO_CHATGPT, AUTO_MESSAGES
        global status_message, client, running

        # Uptime
        uptime = 0
        if bot_start_time:
            uptime = int(time.time() - bot_start_time)
        self.lbl_likes.setText(f"Likes envoyés : {likes_sent}")
        self.lbl_uptime.setText(f"Temps de fonctionnement : {uptime}s")

        # Prochaine pause
        if next_pause_time:
            remaining = int(max(0, next_pause_time - time.time()))
            self.lbl_next_pause.setText(f"Prochaine pause : {remaining}s")
        else:
            self.lbl_next_pause.setText("Prochaine pause : -")

        # Statuts
        self.lbl_auto_status.setText(f"Auto-messages : {'ON' if ENABLE_AUTO_MESSAGES else 'OFF'}")

        # IA active uniquement si: toggle IA + bot lancé + client OpenAI initialisé
        ai_active = ENABLE_AUTO_CHATGPT and running and (client is not None)
        self.lbl_ai_status.setText(f"IA (ChatGPT) : {'ON' if ai_active else 'OFF'}")

        self.lbl_msg_count.setText(f"Messages configurés : {len(AUTO_MESSAGES)}")
        self.lbl_status.setText(f"Status: {status_message}")

        # Bande passante
        try:
            up, down = get_bandwidth()
            bandwidth_data["time"].append(time.time())
            bandwidth_data["upload"].append(up)
            bandwidth_data["download"].append(down)

            if len(bandwidth_data["upload"]) > 30:
                bandwidth_data["upload"].pop(0)
                bandwidth_data["download"].pop(0)
                bandwidth_data["time"].pop(0)

            self.ax.clear()
            self.ax.set_facecolor("#1b1b1f")
            self.ax.plot(bandwidth_data["upload"], label="up", color="#00f2ea", linewidth=1.5)
            self.ax.plot(bandwidth_data["download"], label="down", color="#9f9f9f", linewidth=1.2, linestyle="--")
            ymax = max(100, max(bandwidth_data["upload"] + bandwidth_data["download"] + [0]))
            self.ax.set_ylim(0, ymax)
            self.ax.get_xaxis().set_visible(False)
            self.ax.legend(loc="upper right", fontsize=7, facecolor="#1b1b1f", edgecolor="#2a2a2a")
            for spine in self.ax.spines.values():
                spine.set_color("#2a2a2a")
            self.ax.tick_params(colors="#b7b7b7")
            self.ax.set_title("Upload / Download (KB/s)", fontsize=9, color="#eaeaea")
            self.canvas.draw_idle()
        except Exception:
            pass

    # ---- Stylesheet ----
    def _qss(self) -> str:
        return """
        QWidget {
            background-color: #111111;
            color: #eaeaea;
            font-size: 13px;
        }
        #pageTitle {
            font-size: 20px;
            font-weight: 700;
            color: #00f2ea;
            padding: 4px 0 2px 0;
        }
        #pageSubtitle {
            font-size: 12px;
            color: #b7b7b7;
        }
        QTabWidget::pane {
            border: 1px solid #2a2a2a;
            border-radius: 10px;
            margin-top: 8px;
            background: #1b1b1f;
        }
        QTabBar::tab {
            padding: 8px 14px;
            margin-right: 6px;
            color: #cfcfcf;
            background: transparent;
            border: 1px solid transparent;
            border-top-left-radius: 8px;
            border-top-right-radius: 8px;
        }
        QTabBar::tab:selected {
            color: #ffffff;
            background: #1b1b1f;
            border: 1px solid #2a2a2a;
            border-bottom: 2px solid #00f2ea;
        }
        QTabBar::tab:hover {
            color: #ffffff;
        }

        #card {
            background: #1b1b1f;
            border: 1px solid #2a2a2a;
            border-radius: 12px;
        }
        #cardTitle {
            font-size: 14px;
            font-weight: 600;
            color: #eaeaea;
            margin-bottom: 2px;
        }

        QLabel#badge {
            background: rgba(0,242,234,0.12);
            color: #aef7f4;
            border: 1px solid rgba(0,242,234,0.35);
            border-radius: 10px;
            padding: 4px 8px;
        }
        QLabel#statLine {
            color: #a8a8a8;
        }

        QLineEdit {
            background: #17171a;
            border: 1px solid #2a2a2a;
            border-radius: 10px;
            padding: 8px 10px;
            selection-background-color: #00f2ea;
            selection-color: #000000;
        }
        QLineEdit:focus {
            border-color: #00f2ea;
            background: #1a1a1e;
        }
        QLineEdit[echoMode="2"] { /* password if any in future */
            letter-spacing: 2px;
        }
        QLineEdit::placeholder {
            color: #8f8f8f;
        }

        QListWidget {
            background: #17171a;
            border: 1px solid #2a2a2a;
            border-radius: 10px;
            padding: 6px;
        }

        QCheckBox {
            spacing: 8px;
        }
        QCheckBox::indicator {
            width: 18px; height: 18px;
            border-radius: 4px;
            border: 1px solid #3a3a3a;
            background: #202024;
        }
        QCheckBox::indicator:checked {
            background: #00f2ea;
            border-color: #00f2ea;
        }

        QPushButton {
            border: 1px solid #2a2a2a;
            border-radius: 10px;
            padding: 8px 12px;
            background: #202024;
            color: #eaeaea;
        }
        QPushButton:hover {
            background: #24242a;
        }
        QPushButton:pressed {
            background: #1c1c21;
        }

        QPushButton#accentButton {
            background: #00f2ea;
            color: #000000;
            border-color: #00c9c3;
        }
        QPushButton#accentButton:hover {
            background: #1af5ef;
        }
        QPushButton#accentButton:pressed {
            background: #00d6cf;
        }

        QPushButton#dangerButton {
            background: #2a191b;
            color: #ffb3b8;
            border-color: #5a2a2f;
        }
        QPushButton#dangerButton:hover {
            background: #3a1f23;
        }
        QPushButton#dangerButton:pressed {
            background: #251417;
        }

        QPushButton#ghostButton {
            background: transparent;
            color: #cfcfcf;
            border-color: #2a2a2a;
        }
        QPushButton#ghostButton:hover {
            background: rgba(255,255,255,0.04);
        }

        QToolTip {
            color: #ffffff;
            background-color: #2a82da;
            border: 1px solid #ffffff;
        }
        """
    
def launch_pyqt_control():
    app_qt = QApplication.instance() or QApplication(sys.argv)
    win = BotWindow()
    win.show()
    app_qt.exec()

# ============== Flask (Web Panel) ==============
HTML_PAGE = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <title>Bot TikTok</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: Arial, sans-serif; background: #121212; color: #f5f5f5; text-align: center; margin:0; padding:20px; }
        h1 { color: #00f2ea; }
        h2 { color: #00f2ea; margin-top: 30px; }
        .btn { background: #00f2ea; border: none; padding: 12px 20px; margin: 5px; border-radius: 6px; cursor: pointer; font-size: 16px; transition: 0.3s; color: #121212; }
        .btn:hover { background: #00bfb3; }
        .btn-danger { background: #ff4757; color: white; }
        .btn-danger:hover { background: #ff3838; }
        .btn-small { padding: 8px 12px; font-size: 14px; margin: 2px; }
        input, textarea { padding: 10px; margin: 10px; border-radius: 6px; border: none; width: 80%; max-width: 400px; background: #1e1e2f; color: #f5f5f5; }
        .card { background: #1e1e2f; padding: 20px; border-radius: 10px; margin-top: 20px; }
        #status { margin-top: 20px; font-size: 18px; }
        label { display: block; margin: 10px; }
        .message-list { max-height: 300px; overflow-y: auto; background: #121212; border-radius: 6px; padding: 10px; margin: 10px 0; text-align: left; }
        .message-item { background: #1e1e2f; padding: 10px; margin: 5px 0; border-radius: 6px; display: flex; justify-content: space-between; align-items: center; }
        .message-text { flex-grow: 1; margin-right: 10px; word-break: break-word; }
        .message-actions { display: flex; gap: 5px; }
        .input-group { display: flex; align-items: center; justify-content: center; gap: 10px; margin: 10px 0; }
        .input-group input { margin: 0; }
    </style>
</head>
<body>
    <h1>🚀 Bot TikTok</h1>
    <div class="card">
        <form method="post" action="/control">
            <button class="btn" name="action" value="start">▶️ Démarrer</button>
            <button class="btn" name="action" value="stop">⏸️ Arrêter</button>
            <br>
            <input type="text" name="live_url" placeholder="Lien TikTok Live">
            <button class="btn" name="action" value="change_live">🌐 Changer Live</button>
            <br>
            <label>
                <input type="checkbox" name="auto_messages" onchange="this.form.submit()" {{'checked' if auto_messages else ''}}>
                Activer l'envoi auto de messages
            </label>
        </form>
    </div>
    <div class="card">
        <h2>💬 Gestion des Auto-Messages</h2>
        <div class="input-group">
            <input type="text" id="newMessage" placeholder="Nouveau message..." maxlength="200">
            <button class="btn" onclick="addMessage()">➕ Ajouter</button>
        </div>
        <div class="message-list" id="messagesList">
            {% for message in messages %}
            <div class="message-item" data-index="{{ loop.index0 }}">
                <span class="message-text">{{ loop.index }}. {{ message }}</span>
                <div class="message-actions">
                    <button class="btn btn-small" onclick="editMessage({{ loop.index0 }}, '{{ message|replace("'", "\\'") }}')">✏️</button>
                    <button class="btn btn-small btn-danger" onclick="deleteMessage({{ loop.index0 }})">🗑️</button>
                </div>
            </div>
            {% endfor %}
        </div>
        <div style="margin-top: 15px;">
            <button class="btn btn-danger" onclick="clearAllMessages()" {% if not messages %}disabled{% endif %}>🧹 Tout effacer</button>
            <span style="margin-left: 20px;">Messages configurés : <strong id="messageCount">{{ messages|length }}</strong></span>
        </div>
    </div>
    <div class="card">
        <h2>📊 Statistiques</h2>
        <p>Likes envoyés : <span id="likes">0</span></p>
        <p>Temps de fonctionnement : <span id="uptime">0s</span></p>
        <p>Prochaine pause prévue : <span id="next_pause">-</span></p>
        <p>Auto-messages : <span id="auto_status">{{ 'ON' if auto_messages else 'OFF' }}</span></p>
        <p>Messages configurés : <span id="message_count">{{ messages|length }}</span></p>
    </div>
    <h3 id="status">Status: En attente...</h3>
    <script>
        setInterval(function(){
            fetch("/status?_=" + new Date().getTime())
                .then(res => res.json())
                .then(data => {
                    document.getElementById("status").innerText = "Status: " + data.status;
                    document.getElementById("likes").innerText = data.likes;
                    document.getElementById("uptime").innerText = data.uptime;
                    document.getElementById("next_pause").innerText = data.next_pause;
                    document.getElementById("auto_status").innerText = data.auto_messages ? "ON" : "OFF";
                    document.getElementById("message_count").innerText = data.message_count;
                    document.getElementById("messageCount").innerText = data.message_count;
                });
        }, 2000);
        function addMessage() {
            const input = document.getElementById('newMessage');
            const message = input.value.trim();
            if (!message) { alert('Veuillez saisir un message'); return; }
            if (message.length > 200) { alert('Le message est trop long (max 200 caractères)'); return; }
            fetch('/messages', {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: 'action=add&message=' + encodeURIComponent(message)
            })
            .then(response => response.json())
            .then(data => { if (data.success) { location.reload(); } else { alert('Erreur: ' + data.error); } });
        }
        function editMessage(index, currentMessage) {
            const newMessage = prompt('Modifier le message:', currentMessage);
            if (newMessage === null) return;
            if (!newMessage.trim()) { alert('Le message ne peut pas être vide'); return; }
            fetch('/messages', {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: 'action=edit&index=' + index + '&message=' + encodeURIComponent(newMessage.trim())
            })
            .then(response => response.json())
            .then(data => { if (data.success) { location.reload(); } else { alert('Erreur: ' + data.error); } });
        }
        function deleteMessage(index) {
            if (!confirm('Êtes-vous sûr de vouloir supprimer ce message ?')) { return; }
            fetch('/messages', {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: 'action=delete&index=' + index
            })
            .then(response => response.json())
            .then(data => { if (data.success) { location.reload(); } else { alert('Erreur: ' + data.error); } });
        }

        function clearAllMessages() {
            if (!confirm('Êtes-vous sûr de vouloir supprimer TOUS les messages ?')) { return; }
            fetch('/messages', {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: 'action=clear'
            })
            .then(response => response.json())
            .then(data => { if (data.success) { location.reload(); } else { alert('Erreur: ' + data.error); } });
        }

        document.getElementById('newMessage').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') { addMessage(); }
        });
    </script>
</body>
</html>
"""

# --------- Routes Flask ---------
@app.route("/", methods=["GET"])
@requires_auth
def index():
    return render_template_string(HTML_PAGE, auto_messages=ENABLE_AUTO_MESSAGES, messages=AUTO_MESSAGES)

@app.route("/messages", methods=["POST"])
@requires_auth
def manage_messages():
    global AUTO_MESSAGES
    action = request.form.get("action")
    try:
        if action == "add":
            message = request.form.get("message", "").strip()
            if not message:
                return {"success": False, "error": "Message vide"}
            if len(message) > 200:
                return {"success": False, "error": "Message trop long"}
            AUTO_MESSAGES.append(message)
            save_config_to_json()
            set_status(f"✅ Message ajouté via web : {message[:30]}...")
            return {"success": True}

        elif action == "edit":
            index_i = int(request.form.get("index"))
            message = request.form.get("message", "").strip()
            if not message:
                return {"success": False, "error": "Message vide"}
            if index_i < 0 or index_i >= len(AUTO_MESSAGES):
                return {"success": False, "error": "Index invalide"}
            AUTO_MESSAGES[index_i] = message
            save_config_to_json()
            set_status(f"✅ Message modifié via web")
            return {"success": True}

        elif action == "delete":
            index_i = int(request.form.get("index"))
            if index_i < 0 or index_i >= len(AUTO_MESSAGES):
                return {"success": False, "error": "Index invalide"}
            deleted_msg = AUTO_MESSAGES.pop(index_i)
            save_config_to_json()
            set_status(f"🗑️ Message supprimé via web : {deleted_msg[:30]}...")
            return {"success": True}

        elif action == "clear":
            AUTO_MESSAGES.clear()
            save_config_to_json()
            set_status("🗑️ Tous les messages supprimés via web")
            return {"success": True}

        else:
            return {"success": False, "error": "Action invalide"}

    except Exception as e:
        set_status(f"⚠️ Erreur gestion messages web : {e}")
        return {"success": False, "error": str(e)}

@app.route("/control", methods=["POST"])
@requires_auth
def control():
    global running, current_live, driver, ENABLE_AUTO_MESSAGES
    action = request.form.get("action")
    live_url = request.form.get("live_url")
    auto_messages_toggle = request.form.get("auto_messages")

    if auto_messages_toggle is not None:
        tik_backend.ENABLE_AUTO_MESSAGES = not ENABLE_AUTO_MESSAGES
        save_config_to_json()
        set_status(f"🔁 Auto-messages {'activés' if ENABLE_AUTO_MESSAGES else 'désactivés'}")

    if action == "start":
        toggle_running()
    elif action == "stop":
        tik_backend.running = False
        set_status("⏸️ Bot arrêté via web")
    elif action == "change_live" and live_url:
        current_live = live_url
        if driver:
            driver.get(current_live)
        set_status(f"🌐 Live changé : {current_live}")

    return render_template_string(HTML_PAGE, auto_messages=ENABLE_AUTO_MESSAGES, messages=AUTO_MESSAGES)

@app.route("/status", methods=["GET"])
@requires_auth
def status():
    global likes_sent, bot_start_time, next_pause_time, ENABLE_AUTO_MESSAGES, AUTO_MESSAGES
    uptime = "0s"
    if bot_start_time:
        uptime = f"{int(time.time()-bot_start_time)}s"
    next_pause_str = "-"
    if next_pause_time:
        next_pause_str = f"{int(max(0, next_pause_time - time.time()))}s"
    return {
        "status": status_message,
        "likes": likes_sent,
        "uptime": uptime,
        "next_pause": next_pause_str,
        "auto_messages": ENABLE_AUTO_MESSAGES,
        "message_count": len(AUTO_MESSAGES)
    }

# --------- Utilitaires additionnels ---------
def clear_terminal():
    while True:
        time.sleep(CLEAR_INTERVAL)
        os.system('cls' if os.name == 'nt' else 'clear')
        set_status("🧹 Terminal nettoyé automatiquement.")

def close_driver():
    global driver
    if driver:
        try:
            driver.quit()
            set_status("✅ Fenêtre Selenium fermée.")
        except:
            pass

# Option simple: désactivable si pyngrok préféré
def launch_ngrok():
    global ngrok_url
    try:
        proc = subprocess.Popen(["ngrok", "http", "5000"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(5)
        tunnels = requests.get("http://127.0.0.1:4040/api/tunnels").json()["tunnels"]
        ngrok_url = tunnels[0]["public_url"]
        set_status(f"🌐 URL publique ngrok : {ngrok_url}")
        send_email_alert("Bot TikTok - Ngrok", f"Ton URL ngrok : {ngrok_url}")
    except Exception as e:
        set_status(f"⚠️ Erreur ngrok : {e}")

# --------- Main ---------
if __name__ == "__main__":
    # Serveur Flask (daemon)
    flask_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False),
        daemon=True
    )
    flask_thread.start()

    # Outils & boucles
    threading.Thread(target=launch_ngrok, daemon=True).start()
    threading.Thread(target=clear_terminal, daemon=True).start()
    threading.Thread(target=refresh_live_loop, daemon=True).start()
    threading.Thread(target=auto_message_loop, daemon=True).start()
    threading.Thread(target=live_reply_loop, daemon=True).start()  # ChatGPT loop

    # Selenium + Auto-like
    threading.Thread(target=launch_driver, daemon=True).start()
    threading.Thread(target=auto_like, daemon=True).start()

    # UI PyQt6 dans le thread principal
    launch_pyqt_control()
