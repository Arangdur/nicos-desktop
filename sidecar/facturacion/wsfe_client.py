"""
Cliente de WSFE (Web Service de Facturación Electrónica) de ARCA/AFIP --
FEv1. Usa el token/sign que entrega `wsaa_client` para pedir CAE, consultar
puntos de venta habilitados y el último comprobante autorizado.

SOAP armado a mano (sin zeep/lxml, ver nota en wsaa_client.py) -- son pocos
métodos, no justifica la dependencia.
"""
import ssl
import xml.etree.ElementTree as ET

import requests
from requests.adapters import HTTPAdapter

WSFE_URL = "https://servicios1.afip.gov.ar/wsfev1/service.asmx"
NS = {"soap": "http://schemas.xmlsoap.org/soap/envelope/"}


class WsfeError(Exception):
    pass


class _LegacyTlsAdapter(HTTPAdapter):
    """El servidor de WSFE (servicios1.afip.gov.ar) usa una clave DH vieja
    y chica para el handshake TLS -- OpenSSL moderno la rechaza por default
    (DH_KEY_TOO_SMALL). Bajar el SECLEVEL a 1 es el fix estándar para este
    servidor puntual de ARCA (problema conocido, no algo genérico que
    debería aplicarse a otros hosts)."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


_session = requests.Session()
_session.mount("https://servicios1.afip.gov.ar", _LegacyTlsAdapter())


def _post(soap_action: str, body_xml: str) -> ET.Element:
    envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ar="http://ar.gov.afip.dif.FEV1/">
  <soapenv:Header/>
  <soapenv:Body>
    {body_xml}
  </soapenv:Body>
</soapenv:Envelope>"""
    resp = _session.post(
        WSFE_URL,
        data=envelope.encode("utf-8"),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f"http://ar.gov.afip.dif.FEV1/{soap_action}",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise WsfeError(f"WSFE respondió HTTP {resp.status_code}: {resp.text[:800]}")
    envelope_el = ET.fromstring(resp.text)
    body = envelope_el.find("soap:Body", NS)
    fault = body.find("soap:Fault", NS) if body is not None else None
    if fault is not None:
        raise WsfeError(f"WSFE rechazó la solicitud: {fault.findtext('faultstring', 'error desconocido')}")
    return body


def _strip_ns(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _elem_to_dict(elem: ET.Element) -> dict:
    """FEv1 devuelve XML sin namespace en los elementos internos -- un dict
    plano alcanza para lo que necesitamos leer (no hay listas anidadas
    profundas en las respuestas que usamos acá)."""
    resultado = {}
    for child in elem:
        tag = _strip_ns(child.tag)
        if len(child) > 0:
            resultado[tag] = _elem_to_dict(child)
        else:
            resultado[tag] = child.text
    return resultado


def consultar_puntos_venta(cuit: str, token: str, sign: str) -> list:
    """FEParamGetPtosVenta -- de solo lectura, no crea nada. Devuelve los
    puntos de venta habilitados para web service en la cuenta de `cuit`."""
    body = f"""<ar:FEParamGetPtosVenta>
      <ar:Auth>
        <ar:Token>{token}</ar:Token>
        <ar:Sign>{sign}</ar:Sign>
        <ar:Cuit>{cuit}</ar:Cuit>
      </ar:Auth>
    </ar:FEParamGetPtosVenta>"""
    body_el = _post("FEParamGetPtosVenta", body)
    result = body_el.find(".//{http://ar.gov.afip.dif.FEV1/}FEParamGetPtosVentaResult")
    if result is None:
        raise WsfeError("Respuesta sin FEParamGetPtosVentaResult -- ver XML crudo con debug.")
    data = _elem_to_dict(result)
    errores = data.get("Errors")
    if errores:
        raise WsfeError(f"ARCA devolvió error: {errores}")
    pto = data.get("ResultGet", {}).get("PtoVenta", [])
    return pto if isinstance(pto, list) else [pto] if pto else []


def consultar_ultimo_autorizado(cuit: str, token: str, sign: str, pto_venta: int, tipo_cbte: int) -> int:
    """FECompUltimoAutorizado -- de solo lectura. `tipo_cbte`: 11=Factura C
    (la que corresponde a Monotributo, sin discriminar IVA), 6=Factura B,
    1=Factura A, 13=Nota de Crédito C. Cada tipo tiene su propia numeración
    correlativa en ARCA -- consultar el último autorizado de 11 no sirve
    para saber el próximo número de una 13."""
    body = f"""<ar:FECompUltimoAutorizado>
      <ar:Auth>
        <ar:Token>{token}</ar:Token>
        <ar:Sign>{sign}</ar:Sign>
        <ar:Cuit>{cuit}</ar:Cuit>
      </ar:Auth>
      <ar:PtoVta>{pto_venta}</ar:PtoVta>
      <ar:CbteTipo>{tipo_cbte}</ar:CbteTipo>
    </ar:FECompUltimoAutorizado>"""
    body_el = _post("FECompUltimoAutorizado", body)
    result = body_el.find(".//{http://ar.gov.afip.dif.FEV1/}FECompUltimoAutorizadoResult")
    if result is None:
        raise WsfeError("Respuesta sin FECompUltimoAutorizadoResult.")
    data = _elem_to_dict(result)
    errores = data.get("Errors")
    if errores:
        raise WsfeError(f"ARCA devolvió error: {errores}")
    return int(data.get("CbteNro", 0))


def solicitar_cae(
    cuit: str, token: str, sign: str, pto_venta: int, tipo_cbte: int,
    numero_cbte: int, importe: float, concepto: int = 2,
    doc_tipo: int = 99, doc_nro: int = 0, comprobantes_asociados: list = None,
) -> dict:
    """FECAESolicitar -- ESTO EMITE UNA FACTURA FISCAL REAL, IRREVERSIBLE.
    No llamar sin haber confirmado los datos con Nicolás primero.

    `concepto`: 1=Productos, 2=Servicios (consultas médicas van acá),
    3=Productos y Servicios. `doc_tipo`/`doc_nro`: 99/0 = Consumidor Final.
    Para Factura C (Monotributo, `tipo_cbte`=11) no se discrimina IVA --
    ImpNeto = ImpTotal, ImpIVA = 0.

    `comprobantes_asociados`: para una Nota de Crédito (`tipo_cbte`=13),
    ARCA exige referenciar la factura que corrige -- lista de dicts
    {'tipo': int, 'pto_venta': int, 'numero': int} (normalmente uno solo).
    None para una factura normal.

    Devuelve {'cae': ..., 'vencimiento': ..., 'resultado': 'A'|'R'|'P'} --
    'A' es Aprobado. Si 'R' (Rechazado) o 'P' (Parcial), no hay CAE válido,
    el mensaje de error viene en `Observaciones`."""
    hoy = __import__("datetime").date.today().strftime("%Y%m%d")
    importe_str = f"{importe:.2f}"

    campos_servicio = ""
    if concepto in (2, 3):
        # Obligatorio para Servicios: rango de fechas del servicio prestado
        # y vencimiento de pago -- usamos hoy para las tres, es una consulta
        # puntual, no un servicio con periodo de facturación real.
        campos_servicio = f"""
        <ar:FchServDesde>{hoy}</ar:FchServDesde>
        <ar:FchServHasta>{hoy}</ar:FchServHasta>
        <ar:FchVtoPago>{hoy}</ar:FchVtoPago>"""

    cbtes_asoc_xml = ""
    if comprobantes_asociados:
        items = "".join(
            f"""
          <ar:CbteAsoc>
            <ar:Tipo>{c['tipo']}</ar:Tipo>
            <ar:PtoVta>{c['pto_venta']}</ar:PtoVta>
            <ar:Nro>{c['numero']}</ar:Nro>
          </ar:CbteAsoc>"""
            for c in comprobantes_asociados
        )
        # Va después de MonCotiz y antes de Tributos/Iva/Opcionales en el
        # orden del schema de WSFEv1 -- acá no mandamos esos otros bloques,
        # así que cierra el detalle.
        cbtes_asoc_xml = f"\n        <ar:CbtesAsoc>{items}\n        </ar:CbtesAsoc>"

    body = f"""<ar:FECAESolicitar>
      <ar:Auth>
        <ar:Token>{token}</ar:Token>
        <ar:Sign>{sign}</ar:Sign>
        <ar:Cuit>{cuit}</ar:Cuit>
      </ar:Auth>
      <ar:FeCAEReq>
        <ar:FeCabReq>
          <ar:CantReg>1</ar:CantReg>
          <ar:PtoVta>{pto_venta}</ar:PtoVta>
          <ar:CbteTipo>{tipo_cbte}</ar:CbteTipo>
        </ar:FeCabReq>
        <ar:FeDetReq>
          <ar:FECAEDetRequest>
            <ar:Concepto>{concepto}</ar:Concepto>
            <ar:DocTipo>{doc_tipo}</ar:DocTipo>
            <ar:DocNro>{doc_nro}</ar:DocNro>
            <ar:CbteDesde>{numero_cbte}</ar:CbteDesde>
            <ar:CbteHasta>{numero_cbte}</ar:CbteHasta>
            <ar:CbteFch>{hoy}</ar:CbteFch>
            <ar:ImpTotal>{importe_str}</ar:ImpTotal>
            <ar:ImpTotConc>0.00</ar:ImpTotConc>
            <ar:ImpNeto>{importe_str}</ar:ImpNeto>
            <ar:ImpOpEx>0.00</ar:ImpOpEx>
            <ar:ImpIVA>0.00</ar:ImpIVA>
            <ar:ImpTrib>0.00</ar:ImpTrib>{campos_servicio}
            <ar:MonId>PES</ar:MonId>
            <ar:MonCotiz>1</ar:MonCotiz>{cbtes_asoc_xml}
          </ar:FECAEDetRequest>
        </ar:FeDetReq>
      </ar:FeCAEReq>
    </ar:FECAESolicitar>"""
    body_el = _post("FECAESolicitar", body)
    result = body_el.find(".//{http://ar.gov.afip.dif.FEV1/}FECAESolicitarResult")
    if result is None:
        raise WsfeError("Respuesta sin FECAESolicitarResult.")
    data = _elem_to_dict(result)

    errores = data.get("Errors")
    if errores:
        raise WsfeError(f"ARCA rechazó la solicitud antes de procesar el detalle: {errores}")

    det_resp = data.get("FeDetResp", {}).get("FECAEDetResponse", {})
    resultado = det_resp.get("Resultado")
    observaciones = det_resp.get("Observaciones")
    if resultado != "A":
        raise WsfeError(f"ARCA no aprobó la factura (Resultado={resultado}). Observaciones: {observaciones}")

    return {
        "cae": det_resp.get("CAE"),
        "vencimiento": det_resp.get("CAEFchVto"),
        "resultado": resultado,
        "numero": numero_cbte,
        "pto_venta": pto_venta,
        "tipo_cbte": tipo_cbte,
    }
