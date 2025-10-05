#!/usr/bin/env python3
"""
Point d'entrée principal pour lancer le bot TikTok.
Ce fichier orchestre le démarrage du backend et du frontend.
"""

import threading
import tik_backend
from tik_frontend import launch_ngrok, clear_terminal, launch_pyqt_control

def main():
    """Lance tous les composants du bot TikTok."""

    print("🚀 Démarrage du bot TikTok...")
    print("=" * 50)

    # Serveur Flask (daemon)
    flask_thread = threading.Thread(
        target=lambda: tik_backend.app.run(
            host="0.0.0.0", 
            port=5000, 
            debug=False, 
            use_reloader=False
        ),
        daemon=True
    )
    flask_thread.start()
    print("✓ Serveur Flask démarré sur http://0.0.0.0:5000")

    # Outils & boucles
    threading.Thread(target=launch_ngrok, daemon=True).start()
    print("✓ Ngrok lancé")

    threading.Thread(target=clear_terminal, daemon=True).start()
    print("✓ Nettoyage terminal activé")

    threading.Thread(target=tik_backend.refresh_live_loop, daemon=True).start()
    print("✓ Rafraîchissement live activé")

    threading.Thread(target=tik_backend.auto_message_loop, daemon=True).start()
    print("✓ Boucle auto-message activée")

    threading.Thread(target=tik_backend.live_reply_loop, daemon=True).start()
    print("✓ Boucle réponses ChatGPT activée")

    # Selenium + Auto-like
    threading.Thread(target=tik_backend.launch_driver, daemon=True).start()
    print("✓ Driver Selenium lancé")

    threading.Thread(target=tik_backend.auto_like, daemon=True).start()
    print("✓ Auto-like activé")

    print("=" * 50)
    print("🎯 Lancement de l'interface PyQt6...")
    print()

    # UI PyQt6 dans le thread principal
    launch_pyqt_control()

if __name__ == "__main__":
    main()
