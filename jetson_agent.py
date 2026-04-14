#!/usr/bin/env python3
"""
Jetson Agent Script
Ova skripta se pokreće na Jetson Orin Nano uređaju.
1. Uspostavlja Reverse SSH tunnel prema VPS-u.
2. Detektira svoju javnu IP adresu.
3. Šalje "heartbeat" VPS-u svakih 30 sekundi s javnom IP i portom.
4. Pokreće lokalni AI server (LLM) koji obrađuje upite.
"""

import os
import sys
import time
import json
import socket
import threading
import subprocess
import requests
from datetime import datetime

# KONFIGURACIJA
VPS_HOST = "tvoj-vps-ip-ili-domena.com"  # IP ili domena tvog VPS-a
VPS_PORT = 5000                          # Port na kojem VPS prima heartbeat
JETSON_API_PORT = 8000                   # Port na kojem Jetson vrti LLM API
SSH_USER = "root"                        # Korisnik na VPS-u
SSH_KEY_PATH = "/home/nvidia/.ssh/id_rsa" # Put do SSH ključa na Jetsonu
REMOTE_PORT = 8000                       # Port na VPS-u koji forwarda na Jetson

HEARTBEAT_INTERVAL = 30  # Sekundi

def get_public_ip():
    """Dohvaća javnu IP adresu Jetsona preko vanjskog servisa."""
    try:
        response = requests.get('https://api.ipify.org?format=json', timeout=5)
        return response.json().get('ip')
    except Exception as e:
        print(f"[GREŠKA] Ne mogu dohvatiti javnu IP: {e}")
        return None

def send_heartbeat():
    """Šalje heartbeat VPS-u s javnom IP adresom."""
    public_ip = get_public_ip()
    if not public_ip:
        print("[INFO] Preskačem heartbeat, nema javne IP.")
        return

    payload = {
        "device_id": "jetson-orin-nano-01",
        "public_ip": public_ip,
        "api_port": JETSON_API_PORT,
        "timestamp": datetime.now().isoformat(),
        "status": "online"
    }

    try:
        # Šaljemo na VPS endpoint za registraciju
        url = f"http://{VPS_HOST}:{VPS_PORT}/api/jetson/heartbeat"
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code == 200:
            print(f"[OK] Heartbeat poslan: {public_ip}:{JETSON_API_PORT}")
        else:
            print(f"[WARN] Heartbeat odbijen: {response.status_code}")
    except Exception as e:
        print(f"[GREŠKA] Slanje heartbeat-a nije uspjelo: {e}")

def start_ssh_tunnel():
    """Pokreće reverse SSH tunnel u pozadini."""
    print(f"[INFO] Pokrećem Reverse SSH Tunnel na {VPS_HOST}...")
    # Komanda za SSH tunnel:
    # Spaja remote port (8000) na VPS-u na local host (127.0.0.1:8000) na Jetsonu
    # -N: ne izvršavaj komandu, -R: remote forward, -i: key, -o: keep alive
    cmd = [
        "ssh", "-i", SSH_KEY_PATH,
        "-o", "StrictHostKeyChecking=no",
        "-o", "ServerAliveInterval=60",
        "-o", "ServerAliveCountMax=3",
        "-N", "-R", f"{REMOTE_PORT}:127.0.0.1:{JETSON_API_PORT}",
        f"{SSH_USER}@{VPS_HOST}"
    ]
    
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print("[OK] SSH Tunnel pokrenut.")
        return process
    except Exception as e:
        print(f"[GREŠKA] Ne mogu pokrenuti SSH tunnel: {e}")
        return None

def run_local_llm_server():
    """
    Ovdje ide tvoja logika za pokretanje LLM-a na Jetsonu.
    Za sada simuliramo jednostavan Flask/FastAPI server.
    """
    print(f"[INFO] Pokrećem lokalni AI server na portu {JETSON_API_PORT}...")
    # OVDJE ĆEŠ UBACITI SVOJU LOGIKU ZA LLAMA/MISTRAL ETC.
    # Primjer: subprocess.run(["python3", "llm_server.py"])
    pass

def heartbeat_loop():
    """Petlja za slanje heartbeat signala."""
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)

if __name__ == "__main__":
    print("=== Jetson Agent Start ===")
    
    # 1. Pokreni SSH Tunnel
    tunnel_process = start_ssh_tunnel()
    
    # 2. Pokreni LLM Server (u stvarnoj implementaciji ovo blokira ili ide u thread)
    # Za demo svrhe, pokrećemo server u threadu ako koristi Flask/FastAPI
    llm_thread = threading.Thread(target=run_local_llm_server)
    llm_thread.daemon = True
    llm_thread.start()

    # 3. Pokreni Heartbeat petlju
    try:
        heartbeat_loop()
    except KeyboardInterrupt:
        print("Gašenje agenta...")
        if tunnel_process:
            tunnel_process.terminate()
