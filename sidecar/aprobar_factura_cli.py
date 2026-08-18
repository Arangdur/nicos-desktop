"""Herramienta de línea de comandos para aprobar/rechazar un pedido de
factura MIENTRAS no existe la pestaña en el front de NicOS Desktop.
Emite de verdad si aprobás -- mismo camino que usaría el botón del front.

Uso:
  python3 aprobar_factura_cli.py listar
  python3 aprobar_factura_cli.py ver <id>
  python3 aprobar_factura_cli.py aprobar <id> [<id_factura_asociada>]  # el 2do arg es obligatorio si es nota de crédito
  python3 aprobar_factura_cli.py rechazar <id>
"""
import sys

import facturas

RESUELTO_BY = "nicolas-cli"
ROL = "director"


def listar():
    for f in facturas.list_facturas():
        print(f"{f['id']}  [{f['estado']:18}]  ${f['monto'] or '?':<10}  {f['concepto'] or ''}  -- {f['texto_original'][:50]}")


def ver(factura_id):
    f = facturas._get_factura(factura_id)
    for k, v in f.items():
        print(f"{k}: {v}")


def aprobar(factura_id, factura_asociada_id=None):
    resultado = facturas.aprobar_y_emitir(
        factura_id, RESUELTO_BY, ROL, factura_asociada_id=factura_asociada_id,
    )
    print("EMITIDA:", resultado)


def rechazar(factura_id):
    resultado = facturas.rechazar(factura_id, RESUELTO_BY, ROL)
    print("RECHAZADA:", resultado)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    accion = sys.argv[1]
    try:
        if accion == "listar":
            listar()
        elif accion == "ver":
            ver(sys.argv[2])
        elif accion == "aprobar":
            aprobar(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
        elif accion == "rechazar":
            rechazar(sys.argv[2])
        else:
            print(__doc__)
    except facturas.FacturaError as e:
        print("ERROR:", e)
        sys.exit(1)
