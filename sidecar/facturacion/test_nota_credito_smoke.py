"""
Self-check rápido para el agregado de Nota de Crédito -- no llama a ARCA de
verdad, solo verifica que el XML de FECAESolicitar arme bien el bloque
CbtesAsoc cuando corresponde (nota de crédito) y que NO aparezca cuando no
corresponde (factura normal). Es la parte más frágil de este cambio (XML
armado a mano) -- el resto (routing, DB, IA) es directo.

Uso: python3 facturacion/test_nota_credito_smoke.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from facturacion import wsfe_client  # noqa: E402

capturado = {}


def _fake_post(soap_action, body_xml):
    capturado["body"] = body_xml
    import xml.etree.ElementTree as ET
    xml_resp = """<result xmlns="http://ar.gov.afip.dif.FEV1/">
      <FECAESolicitarResult>
        <FeDetResp><FECAEDetResponse>
          <Resultado>A</Resultado><CAE>123</CAE><CAEFchVto>20260101</CAEFchVto>
        </FECAEDetResponse></FeDetResp>
      </FECAESolicitarResult>
    </result>"""
    return ET.fromstring(xml_resp)


wsfe_client._post = _fake_post

# Caso 1: factura normal -- NO debe aparecer CbtesAsoc.
wsfe_client.solicitar_cae("cuit", "tok", "sign", 4, 11, 1, 1000.0)
assert "CbtesAsoc" not in capturado["body"], "factura normal no debería llevar CbtesAsoc"

# Caso 2: nota de crédito -- SÍ debe aparecer, con los datos de la factura asociada.
wsfe_client.solicitar_cae(
    "cuit", "tok", "sign", 4, 13, 1, 500.0,
    comprobantes_asociados=[{"tipo": 11, "pto_venta": 4, "numero": 7}],
)
body = capturado["body"]
assert "<ar:CbtesAsoc>" in body, "nota de crédito debería llevar CbtesAsoc"
assert "<ar:Tipo>11</ar:Tipo>" in body
assert "<ar:PtoVta>4</ar:PtoVta>" in body
assert "<ar:Nro>7</ar:Nro>" in body

print("OK -- CbtesAsoc se arma bien para nota de crédito y no aparece en factura normal.")
