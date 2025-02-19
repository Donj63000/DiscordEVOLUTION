#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from flask import Flask
from threading import Thread

app = Flask(__name__)

@app.route('/')
def home():

    return "Le bot est en ligne ! (keep-alive)"

def run_server():
   
    app.run(host='0.0.0.0', port=8080)

def keep_alive():

    server_thread = Thread(target=run_server)
    server_thread.daemon = True
    server_thread.start()
