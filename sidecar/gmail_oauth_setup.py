#!/usr/bin/env python3
"""
Helper interactivo, UNA VEZ POR CASILLA -- genera el refresh token que
gmail_client.py necesita. Esto NO lo puede correr un agente en la nube ni
nada automático: abre tu navegador real y te pide loguearte con la cuenta
de Gmail de esa casilla.

Pasos previos (una sola vez, sirve para las dos casillas):
1. Andá a https://console.cloud.google.com/ , creá un proyecto (o usá uno
   existente).
2. "APIs & Services" -> "Library" -> buscá "Gmail API" -> Enable.
3. "APIs & Services" -> "Credentials" -> "Create Credentials" -> "OAuth
   client ID" -> tipo de aplicación "Desktop app". Te da un Client ID y un
   Client Secret -- pegalos en Ajustes del Director (NicOS), en la sección
   "Mail entrante -- Gmail".
4. Si el proyecto está en modo "Testing" (lo normal), en "OAuth consent
   screen" -> "Test users" agregá las dos casillas
   (novogen.salud@gmail.com y fundacion.abate@gmail.com) -- si no, Google
   rechaza el login.

Uso (una corrida por casilla, logueado con la cuenta de Gmail de ESA
casilla en el navegador que se abre):

    python3 sidecar/gmail_oauth_setup.py consultorio
    python3 sidecar/gmail_oauth_setup.py abate

Requiere GMAIL_CLIENT_ID y GMAIL_CLIENT_SECRET ya en el entorno (exportalos
vos mismo antes de correr esto, o pegalos cuando el script te los pida) --
son los mismos que ya cargaste en Ajustes del Director.

Al final imprime el refresh token -- pegalo en Ajustes del Director, en el
campo de esa casilla, y guardá. gmail_client.py lo toma de ahí (vía
variable de entorno que arma electron/main.js), no hace falta que quede en
ningún archivo.
"""
import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

from gmail_client import CASILLAS, SCOPES


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in CASILLAS:
        sys.exit(f"Uso: python3 {sys.argv[0]} <consultorio|abate>")
    casilla = sys.argv[1]

    client_id = os.getenv("GMAIL_CLIENT_ID") or input("GMAIL_CLIENT_ID: ").strip()
    client_secret = os.getenv("GMAIL_CLIENT_SECRET") or input("GMAIL_CLIENT_SECRET: ").strip()

    print(f"\nSe va a abrir el navegador -- logueate con {CASILLAS[casilla]} exactamente.\n")
    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=SCOPES,
    )
    # access_type=offline + prompt=consent es lo que garantiza que Google
    # devuelva un refresh_token -- sin esto, un login repetido con la misma
    # cuenta puede no traerlo (Google solo lo manda la primera vez que se
    # consiente, salvo que se fuerce el consentimiento de nuevo).
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    if not creds.refresh_token:
        sys.exit(
            "Google no devolvió un refresh token. Probablemente ya habías dado consentimiento "
            "antes con esta cuenta -- entrá a https://myaccount.google.com/permissions, revocá "
            "el acceso de esta app, y volvé a correr el script."
        )

    print(f"\nListo. Refresh token para '{casilla}' ({CASILLAS[casilla]}):\n")
    print(creds.refresh_token)
    print(f"\nPegalo en Ajustes del Director, campo de '{casilla}', y guardá.\n")


if __name__ == "__main__":
    main()
