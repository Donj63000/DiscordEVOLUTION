#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Module alive.py
---------------
Ce module fournit une fonction keep_alive() qui lance un petit
serveur Flask sur le port 8080. Son rôle est de recevoir des pings
HTTP réguliers (via UptimeRobot ou autre) afin de maintenir une instance
hébergée en vie, par exemple sur Replit (plan gratuit).
"""

from flask import Flask
from threading import Thread

# Création de l'application Flask
app = Flask(__name__)

@app.route('/')
def home():
    """
    Route basique, renvoie simplement un message
    indiquant que le bot est bien en vie.
    """
    return "Le bot est en ligne ! (keep-alive)"

def run_server():
    """
    Démarre le serveur Flask sur 0.0.0.0, port 8080.
    Sur des hébergeurs comme Replit, l'URL publique du Repl
    redirige vers ce port automatiquement.
    """
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    """
    Lance le serveur Flask dans un thread séparé,
    pour ne pas bloquer le bot Discord.
    """
    server_thread = Thread(target=run_server)
    server_thread.daemon = True
    server_thread.start()
