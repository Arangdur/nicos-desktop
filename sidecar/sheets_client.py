"""
Cliente de Google Sheets para la hoja "Bot WhatsApp Consultorio".

Esquema de columnas (definido en manual_marianela.md, jarvis-trabajo/):
  Llenadas por el bot: Fecha, Tipo, Telefono, Notas, Estado
  Llenadas a mano por Marianela: Nombre, Apellido, DNI, Obra Social, Medicamentos, Urgente

No asumimos un orden fijo de columnas — leemos la fila de encabezados y mapeamos
por nombre, así un reordenamiento manual de columnas en Sheets no rompe la app.
"""
import os
import json

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_NAME = "Bot WhatsApp Consultorio"

# Nombre de hoja/tab dentro del spreadsheet. Si el tab tiene otro nombre real,
# ajustar acá (no confirmado por auditoría — a verificar contra el Sheet real).
TAB_NAME = "Mensajes"

EXPECTED_COLUMNS = [
    "Fecha", "Tipo", "Telefono", "Notas", "Estado",
    "Nombre", "Apellido", "DNI", "Obra Social", "Medicamentos", "Urgente",
]


class SheetsConfigError(Exception):
    pass


def _get_credentials():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise SheetsConfigError(
            "Falta GOOGLE_SERVICE_ACCOUNT_JSON — configurá la cuenta de servicio "
            "en la pantalla de Ajustes de la app."
        )
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SheetsConfigError(f"GOOGLE_SERVICE_ACCOUNT_JSON no es JSON válido: {e}")
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def _get_spreadsheet_id():
    spreadsheet_id = os.getenv("WHATSAPP_SHEET_ID")
    if not spreadsheet_id:
        raise SheetsConfigError(
            "Falta WHATSAPP_SHEET_ID — configurá el ID de la planilla "
            f"'{SHEET_NAME}' en la pantalla de Ajustes de la app."
        )
    return spreadsheet_id


def _service():
    creds = _get_credentials()
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def list_messages():
    """Devuelve todas las filas como lista de dicts, mapeadas por nombre de columna."""
    service = _service()
    spreadsheet_id = _get_spreadsheet_id()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{TAB_NAME}!A1:Z1000")
        .execute()
    )
    rows = result.get("values", [])
    if not rows:
        return []
    headers = rows[0]
    messages = []
    for i, row in enumerate(rows[1:], start=2):  # fila 2 en adelante (1-indexed, con header en fila 1)
        row_dict = {"_row": i}
        for col_idx, header in enumerate(headers):
            row_dict[header] = row[col_idx] if col_idx < len(row) else ""
        messages.append(row_dict)
    return messages


def update_message(row_number, updates: dict):
    """Actualiza celdas puntuales de una fila. `updates` = {nombre_columna: valor_nuevo}.
    Nunca toca columnas fuera de EXPECTED_COLUMNS + lo que exista como encabezado real,
    para no pisar por error una columna que no conocemos.
    """
    service = _service()
    spreadsheet_id = _get_spreadsheet_id()
    header_result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{TAB_NAME}!A1:Z1")
        .execute()
    )
    headers = header_result.get("values", [[]])[0]

    data = []
    for col_name, value in updates.items():
        if col_name not in headers:
            continue  # no escribimos en columnas que no existen en el Sheet real
        col_idx = headers.index(col_name)
        col_letter = _col_letter(col_idx)
        data.append({
            "range": f"{TAB_NAME}!{col_letter}{row_number}",
            "values": [[value]],
        })

    if not data:
        return {"updated": 0}

    body = {"valueInputOption": "USER_ENTERED", "data": data}
    result = (
        service.spreadsheets()
        .values()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
        .execute()
    )
    return {"updated": result.get("totalUpdatedCells", 0)}


def _col_letter(idx):
    """0 -> A, 1 -> B, ..., 25 -> Z, 26 -> AA, etc."""
    letters = ""
    idx += 1
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters
