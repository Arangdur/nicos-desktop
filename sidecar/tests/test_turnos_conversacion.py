"""
Fase C -- motor de reserva/cancelación de turnos por WhatsApp. Todo
mockeado (nunca pega a la red real de DrApp ni de IA). Prueba que:

1. Sin DrApp configurado, todo devuelve None -- mensajes_whatsapp.py usa
   el borrador genérico de la IA como respaldo, nunca se rompe nada.
2. ofrecer_horarios arma hasta 3 opciones REALES desde la disponibilidad
   real (mockeada) y guarda la conversación -- nunca inventa un horario.
3. procesar_eleccion solo puede elegir un ÍNDICE de la lista ya ofrecida
   -- si la IA no está segura, pide aclaración y NO reserva nada.
4. Un 409 (conflicto real) nunca se trata como éxito -- se avisa y se
   puede volver a ofrecer.
5. Si el paciente no existe en DrApp, se deriva a una persona -- nunca se
   crea un paciente nuevo solo.
6. Cancelación: 24hs+ cancela sola, <24hs deriva, 0 o 2+ turnos futuros
   deriva (nunca cancela algo ambiguo), Psiquiatría queda afuera.
7. mensajes_whatsapp.generar_borrador() enruta correctamente según haya o
   no una conversación activa para el teléfono.

Usa base de datos temporal -- nunca toca nicos.db real.

Uso: python3 sidecar/tests/test_turnos_conversacion.py
"""
import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db  # noqa: E402
import drapp_client  # noqa: E402
import mensajes_whatsapp  # noqa: E402
import turnos_conversacion  # noqa: E402

DRAPP_ENV = {
    "DRAPP_RESOURCE_ID": "resources/8c8a2304",
    "DRAPP_SERVICE_KEY_MEDICINA_GENERAL": "pms_specialties:medicina-general/pms_practices:consulta",
    "DRAPP_SERVICE_KEY_PSIQUIATRIA": "pms_specialties:psiquiatria/pms_practices:consulta",
}

ESP_MEDICINA_GENERAL = {"outcome": "success", "data": {"especialidad": "medicina_general"}}
ESP_PSIQUIATRIA = {"outcome": "success", "data": {"especialidad": "psiquiatria"}}

DISPONIBILIDAD_FAKE = {
    "slots": {
        # v0.2.6 (21/08) -- ofrecer_horarios ahora toma UN horario por día
        # distinto (no todos seguidos del mismo día) -- 3 días separados
        # para que el test siga cubriendo eso. El 11:10 del primer día
        # queda a propósito para confirmar que NO se ofrece (se prioriza
        # variedad de días sobre el segundo horario del mismo día).
        # v0.2.7 (20/08) -- horarios ajustados a la franja real de Ordoñez
        # (ver FRANJA_ORDONEZ_MEDICINA_GENERAL): 2026-08-20 es jueves
        # (11:00-14:00), 2026-08-21 es viernes y 2026-08-24 es lunes
        # (ambos 10:00-14:00) -- fuera de esa franja el horario se filtra
        # aunque tenga capacity, así que estos tienen que caer adentro.
        "2026-08-20": {"11:00": {"capacity": 1}, "11:10": {"capacity": 1}},
        "2026-08-21": {"10:00": {"capacity": 1}},
        "2026-08-24": {"11:00": {"capacity": 1}},
    },
}

# v0.2.7 (20/08) -- Fase de Psiquiatría: mismo criterio, horarios dentro de
# la franja real de Posse (ver FRANJA_POSSE_PSIQUIATRIA) -- viernes 8:30-9:30
# y lunes 15-18:30.
DISPONIBILIDAD_PSIQUIATRIA_FAKE = {
    "slots": {
        "2026-08-21": {"08:30": {"capacity": 1}},  # viernes
        "2026-08-24": {"15:30": {"capacity": 1}},  # lunes
    },
}


def _reset_db():
    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_file.close()
    os.environ["NICOS_DB_PATH"] = db_file.name
    import importlib
    importlib.reload(db)
    db.run_migrations()
    return db_file.name


class _BaseTemp(unittest.TestCase):
    def setUp(self):
        self.db_file = _reset_db()

    def tearDown(self):
        os.unlink(self.db_file)


class TestOfrecerHorarios(_BaseTemp):
    """v0.2.7 (20/08) -- estos tests ejercitan `_ofrecer_horarios_especialidad`
    directamente (la mecánica de armar la oferta para UNA especialidad ya
    conocida: capacity, franja, preferencia de fecha, variedad de días) --
    la detección de especialidad en sí (menú, salteo, "otras especialidades")
    tiene su propia clase, TestDeteccionEspecialidad."""

    def test_sin_drapp_configurado_devuelve_none(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DRAPP_RESOURCE_ID", None)
            os.environ.pop("DRAPP_SERVICE_KEY_MEDICINA_GENERAL", None)
            self.assertIsNone(turnos_conversacion.ofrecer_horarios("+5493584390001"))

    @patch("drapp_client.consultar_disponibilidad")
    def test_arma_hasta_tres_opciones_reales(self, mock_disp):
        mock_disp.return_value = DISPONIBILIDAD_FAKE
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion._ofrecer_horarios_especialidad("medicina_general", "+5493584390002")
        self.assertIsNotNone(resultado)
        self.assertIsNone(resultado["accion"])  # ofrecer nunca ejecuta nada en DrApp
        texto = resultado["texto"]
        self.assertIn("1)", texto)
        self.assertIn("2)", texto)
        self.assertIn("3)", texto)

        conv = turnos_conversacion.hay_conversacion_activa("+5493584390002")
        self.assertIsNotNone(conv)
        opciones = json.loads(conv["opciones_json"])
        self.assertEqual(len(opciones), 3)
        # Nunca un horario inventado -- tiene que salir tal cual de la disponibilidad mockeada.
        self.assertEqual(opciones[0]["day"], "2026-08-20")
        self.assertEqual(opciones[0]["time"], "11:00")

    @patch("drapp_client.consultar_disponibilidad")
    def test_ignora_horarios_sin_capacidad_real(self, mock_disp):
        # v0.2.6 (20/08) -- hallazgo real: DrApp devuelve en la grilla TODOS
        # los horarios del día, ocupados o no -- se llegó a ofrecer (y
        # crear) un turno a las 11:00 que ya estaba reservado por otro
        # paciente porque el código tomaba el primer horario de la lista
        # sin mirar "capacity" (0 = sin lugar, -1 = anómalo/bloqueado).
        disponibilidad = {
            "slots": {
                "2026-08-20": {
                    "11:00": {"capacity": -1},  # ya reservado / bloqueado
                    "11:15": {"capacity": 0},   # ocupado
                    "12:15": {"capacity": 1},   # el único de verdad libre (dentro de la franja de Aneit, jueves 11-13)
                },
            },
        }
        mock_disp.return_value = disponibilidad
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion._ofrecer_horarios_especialidad("medicina_general", "+5493584390006")
        self.assertIsNotNone(resultado)
        conv = turnos_conversacion.hay_conversacion_activa("+5493584390006")
        opciones = json.loads(conv["opciones_json"])
        self.assertEqual(len(opciones), 1)
        self.assertEqual(opciones[0]["time"], "12:15")

    @patch("drapp_client.consultar_disponibilidad")
    def test_ignora_horarios_fuera_de_la_franja_real_de_ordonez(self, mock_disp):
        # v0.2.7 (20/08) -- hallazgo real (Nicolás, mirando la config real de
        # DrApp): Medicina General en Ordoñez es solo lunes/martes/viernes
        # 10-14hs y jueves 11-14hs -- nunca miércoles. La grilla de
        # disponibilidad mezcla Ordoñez y Posse sin indicar cuál es cuál, así
        # que un horario con capacity igual puede no ser de Ordoñez.
        disponibilidad = {
            "slots": {
                "2026-08-19": {"10:00": {"capacity": 1}},  # miércoles -- nunca hay Ordoñez
                "2026-08-20": {
                    "09:00": {"capacity": 1},  # jueves pero antes de las 11 -- no es Ordoñez
                    "11:30": {"capacity": 1},  # jueves 11-14 -- este sí
                },
            },
        }
        mock_disp.return_value = disponibilidad
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion._ofrecer_horarios_especialidad("medicina_general", "+5493584390008")
        self.assertIsNotNone(resultado)
        conv = turnos_conversacion.hay_conversacion_activa("+5493584390008")
        opciones = json.loads(conv["opciones_json"])
        self.assertEqual(len(opciones), 1)
        self.assertEqual(opciones[0]["day"], "2026-08-20")
        self.assertEqual(opciones[0]["time"], "11:30")

    @patch("drapp_client.consultar_disponibilidad")
    def test_sin_horarios_disponibles_no_crea_conversacion(self, mock_disp):
        mock_disp.return_value = {"slots": {}}
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion._ofrecer_horarios_especialidad("medicina_general", "+5493584390003")
        self.assertIn("no tenemos horarios", resultado["texto"].lower())
        self.assertIsNone(resultado["accion"])
        self.assertIsNone(turnos_conversacion.hay_conversacion_activa("+5493584390003"))

    @patch("drapp_client.consultar_disponibilidad", side_effect=drapp_client.DrAppAPIError("server_error", "falló", 500))
    def test_error_de_drapp_devuelve_none(self, mock_disp):
        with patch.dict(os.environ, DRAPP_ENV):
            self.assertIsNone(turnos_conversacion._ofrecer_horarios_especialidad("medicina_general", "+5493584390004"))

    @patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE)
    @patch("ai_router.interpretar_preferencia_fecha", return_value={"outcome": "success", "data": {"dias_desde_hoy": 7}})
    def test_preferencia_de_fecha_del_mensaje_corre_la_ventana_de_busqueda(self, mock_pref, mock_disp):
        # v0.2.6 (21/08) -- pedido real de Nicolás: "para la semana que
        # viene" tiene que buscarse desde esa fecha, no desde hoy.
        import datetime
        with patch.dict(os.environ, DRAPP_ENV):
            turnos_conversacion._ofrecer_horarios_especialidad("medicina_general", "+5493584390005", "quiero un turno para la semana que viene")

        mock_pref.assert_called_once_with("quiero un turno para la semana que viene")
        desde_usado = mock_disp.call_args[0][2]  # consultar_disponibilidad(resource, service, desde, hasta)
        desde_esperado = (datetime.datetime.now().date() + datetime.timedelta(days=7)).isoformat()
        self.assertEqual(desde_usado, desde_esperado)

    @patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE)
    def test_sin_preferencia_de_fecha_busca_desde_hoy(self, mock_disp):
        import datetime
        with patch.dict(os.environ, DRAPP_ENV):
            turnos_conversacion._ofrecer_horarios_especialidad("medicina_general", "+5493584390006", "quiero un turno")
        desde_usado = mock_disp.call_args[0][2]
        self.assertEqual(desde_usado, datetime.datetime.now().date().isoformat())

    @patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE)
    def test_ofrece_un_horario_por_dia_distinto_no_todos_seguidos(self, mock_disp):
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion._ofrecer_horarios_especialidad("medicina_general", "+5493584390007")
        conv = turnos_conversacion.hay_conversacion_activa("+5493584390007")
        opciones = json.loads(conv["opciones_json"])
        dias = [o["day"] for o in opciones]
        self.assertEqual(len(dias), len(set(dias)))  # ningún día repetido
        self.assertNotIn("11:10", resultado["texto"])  # el 2do horario del mismo día no se ofrece


class TestDeteccionEspecialidad(_BaseTemp):
    """v0.2.7 (20/08) -- Fase de Psiquiatría: `ofrecer_horarios` (punto de
    entrada público) ahora tiene que saber primero QUÉ especialidad pide el
    paciente -- estos tests cubren esa capa (menú, salteo, derivación a
    otras especialidades), no la mecánica de armar la oferta en sí (eso ya
    lo cubre TestOfrecerHorarios)."""

    def _conv_de(self, telefono):
        conn = db.get_connection()
        return dict(conn.execute(
            "SELECT * FROM turnos_conversacion WHERE telefono = ? ORDER BY creado_at DESC LIMIT 1", (telefono,)
        ).fetchone())

    @patch("ai_router.interpretar_especialidad", return_value={"outcome": "success", "data": {"especialidad": None}})
    def test_mensaje_ambiguo_muestra_el_menu(self, mock_esp):
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.ofrecer_horarios("+5493584390040", "quiero un turno")
        self.assertIn("1) Medicina General", resultado["texto"])
        self.assertIn("2) Psiquiatría", resultado["texto"])
        self.assertIn("3) Otros turnos", resultado["texto"])
        self.assertIsNone(resultado["accion"])
        conv = self._conv_de("+5493584390040")
        self.assertEqual(conv["estado"], "esperando_especialidad")

    @patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE)
    @patch("ai_router.interpretar_especialidad", return_value=ESP_MEDICINA_GENERAL)
    def test_mensaje_que_ya_dice_medicina_general_salta_el_menu(self, mock_esp, mock_disp):
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.ofrecer_horarios("+5493584390041", "necesito un chequeo general")
        self.assertIn("1)", resultado["texto"])
        self.assertNotIn("¿Para qué especialidad", resultado["texto"])
        conv = self._conv_de("+5493584390041")
        self.assertEqual(conv["estado"], "esperando_eleccion")
        self.assertEqual(conv["especialidad"], "medicina_general")

    @patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_PSIQUIATRIA_FAKE)
    @patch("ai_router.interpretar_especialidad", return_value=ESP_PSIQUIATRIA)
    def test_mensaje_que_ya_dice_psiquiatria_salta_el_menu(self, mock_esp, mock_disp):
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.ofrecer_horarios("+5493584390042", "necesito turno con el psiquiatra")
        self.assertIn("Psiquiatría", resultado["texto"])
        conv = self._conv_de("+5493584390042")
        self.assertEqual(conv["estado"], "esperando_eleccion")
        self.assertEqual(conv["especialidad"], "psiquiatria")

    @patch("ai_router.interpretar_especialidad", return_value={"outcome": "success", "data": {"especialidad": "otras_especialidades"}})
    def test_otras_especialidades_deriva_a_stefania_sin_crear_conversacion(self, mock_esp):
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.ofrecer_horarios("+5493584390043", "necesito turno con el cardiólogo")
        self.assertIn("Stefania Rufinetto", resultado["texto"])
        self.assertIn("3537", resultado["texto"])
        self.assertIsNone(resultado["accion"])
        self.assertIsNone(turnos_conversacion.hay_conversacion_activa("+5493584390043"))

    @patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_PSIQUIATRIA_FAKE)
    @patch("ai_router.interpretar_especialidad")
    def test_respuesta_al_menu_con_el_numero_2_ofrece_psiquiatria(self, mock_esp, mock_disp):
        # v0.2.7 (20/08) -- responder "2" al menú tiene que interpretarse
        # igual que si hubiera dicho "psiquiatría" en texto libre -- mismo
        # clasificador, mismo resultado.
        telefono = "+5493584390044"
        mock_esp.return_value = {"outcome": "success", "data": {"especialidad": None}}
        with patch.dict(os.environ, DRAPP_ENV):
            turnos_conversacion.ofrecer_horarios(telefono, "quiero un turno")  # crea esperando_especialidad
        self.assertEqual(self._conv_de(telefono)["estado"], "esperando_especialidad")

        mock_esp.return_value = ESP_PSIQUIATRIA
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.procesar_eleccion(telefono, "2")

        self.assertIn("Psiquiatría", resultado["texto"])
        conv = self._conv_de(telefono)
        self.assertEqual(conv["estado"], "esperando_eleccion")
        self.assertEqual(conv["especialidad"], "psiquiatria")

    @patch("ai_router.interpretar_especialidad")
    def test_respuesta_al_menu_no_clara_vuelve_a_preguntar(self, mock_esp):
        telefono = "+5493584390045"
        mock_esp.return_value = {"outcome": "success", "data": {"especialidad": None}}
        with patch.dict(os.environ, DRAPP_ENV):
            turnos_conversacion.ofrecer_horarios(telefono, "quiero un turno")

        resultado = turnos_conversacion.procesar_eleccion(telefono, "no sé")
        self.assertIn("1 (Medicina General)", resultado["texto"])
        self.assertEqual(self._conv_de(telefono)["estado"], "esperando_especialidad")  # sigue esperando


class TestProcesarEleccion(_BaseTemp):
    def _ofrecer(self, telefono="+5493584390010", mensaje_id=None):
        # v0.2.7 (20/08) -- llama directo a la versión ya acotada a Medicina
        # General -- estos tests son sobre qué pasa DESPUÉS de tener una
        # oferta (elegir, identificar, reservar), no sobre la detección de
        # especialidad en sí (ver TestDeteccionEspecialidad).
        with patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE):
            with patch.dict(os.environ, DRAPP_ENV):
                turnos_conversacion._ofrecer_horarios_especialidad("medicina_general", telefono, mensaje_id=mensaje_id)
        return telefono

    def test_sin_conversacion_activa_devuelve_none(self):
        self.assertIsNone(turnos_conversacion.procesar_eleccion("+5493584390099", "el segundo"))

    def test_cerrar_conversacion_activa_la_cancela(self):
        # v0.2.6 (21/08) -- hallazgo real: rechazar la oferta no cerraba
        # la conversación -- el próximo mensaje quedaba atrapado.
        telefono = self._ofrecer("+5493584390011")
        self.assertIsNotNone(turnos_conversacion.hay_conversacion_activa(telefono))
        turnos_conversacion.cerrar_conversacion_activa(telefono)
        self.assertIsNone(turnos_conversacion.hay_conversacion_activa(telefono))
        # Un mensaje nuevo de este teléfono ya no queda atrapado -- vuelve a
        # ser tratado como "sin conversación activa".
        self.assertIsNone(turnos_conversacion.procesar_eleccion(telefono, "hola buen día"))

    def test_cerrar_conversacion_activa_es_no_op_sin_conversacion(self):
        turnos_conversacion.cerrar_conversacion_activa("+5493584390012")  # no debe romper

    def test_rechazar_mensaje_ajeno_no_cierra_la_conversacion_real(self):
        # v0.2.7 (20/08) -- hallazgo real: un paciente ya tenía una oferta
        # aprobada y enviada (conversación activa, originada por
        # "mensaje-real"). Un mensaje DUPLICADO posterior ("mensaje-otro",
        # mismo teléfono) generó su propio borrador dentro de esa misma
        # conversación -- pero rechazar ESE mensaje no debe cerrar la
        # conversación real, porque el paciente todavía puede responder
        # "1/2/3" a la oferta que sí recibió.
        telefono = self._ofrecer("+5493584390014", mensaje_id="mensaje-real")
        turnos_conversacion.cerrar_conversacion_activa(telefono, mensaje_id="mensaje-otro")
        self.assertIsNotNone(turnos_conversacion.hay_conversacion_activa(telefono))

        # Rechazar el mensaje que SÍ originó la conversación, en cambio, la cierra.
        turnos_conversacion.cerrar_conversacion_activa(telefono, mensaje_id="mensaje-real")
        self.assertIsNone(turnos_conversacion.hay_conversacion_activa(telefono))

    def test_conversacion_vieja_expira_sola(self):
        telefono = self._ofrecer("+5493584390013")
        conn = db.get_connection()
        # Simula que la conversación se creó hace más tiempo del permitido.
        vencida = (
            datetime.datetime.utcnow()
            - datetime.timedelta(hours=turnos_conversacion.CONVERSACION_VIGENCIA_HORAS + 1)
        ).isoformat()
        conn.execute("UPDATE turnos_conversacion SET creado_at = ? WHERE telefono = ?", (vencida, telefono))
        conn.commit()

        self.assertIsNone(turnos_conversacion.hay_conversacion_activa(telefono))
        conv = conn.execute(
            "SELECT estado FROM turnos_conversacion WHERE telefono = ? ORDER BY creado_at DESC LIMIT 1", (telefono,)
        ).fetchone()
        self.assertEqual(conv["estado"], "expirado")

    @patch("drapp_client.crear_turno")
    @patch("drapp_client.buscar_paciente_por_telefono")
    @patch("ai_router.interpretar_eleccion_turno")
    def test_eleccion_clara_crea_el_turno_de_verdad(self, mock_interp, mock_buscar, mock_crear):
        telefono = self._ofrecer()
        mock_interp.return_value = {"outcome": "success", "data": {"eleccion": 1}}
        mock_buscar.return_value = {"id": "consumers/xyz789"}
        mock_crear.return_value = {"id": "events/nuevo123"}

        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.procesar_eleccion(telefono, "el segundo por favor")

        self.assertIn("confirmado", resultado["texto"].lower())
        self.assertEqual(resultado["accion"], "turno_creado")
        mock_crear.assert_called_once_with(
            "resources/8c8a2304", "pms_specialties:medicina-general/pms_practices:consulta",
            "consumers/xyz789", "2026-08-21", "10:00",  # opción de índice 1 -- primer horario del 2do día distinto
        )
        conv = self._conv_de(telefono)
        self.assertEqual(conv["estado"], "confirmado")
        self.assertEqual(conv["drapp_event_id"], "events/nuevo123")

    @patch("drapp_client.crear_turno")
    @patch("drapp_client.buscar_paciente_por_telefono")
    @patch("ai_router.interpretar_eleccion_turno", return_value={"outcome": "success", "data": {"eleccion": 0}})
    def test_confirmacion_saluda_por_el_nombre_si_lo_conoce(self, mock_interp, mock_buscar, mock_crear):
        telefono = self._ofrecer()
        mock_buscar.return_value = {"id": "consumers/xyz789", "firstName": "María José"}
        mock_crear.return_value = {"id": "events/x"}
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.procesar_eleccion(telefono, "el primero")
        self.assertIn("maría", resultado["texto"].lower())

    @patch("drapp_client.crear_turno")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value={"id": "consumers/xyz789"})
    @patch("ai_router.interpretar_eleccion_turno", return_value={"outcome": "success", "data": {"eleccion": 0}})
    def test_confirmacion_incluye_la_ubicacion_real_que_asigno_drapp(self, mock_interp, mock_buscar, mock_crear):
        # v0.2.6 -- hallazgo real (21/08): la disponibilidad mezcla varios
        # consultorios sin indicar cuál es cuál -- el paciente tiene que
        # enterarse de dónde quedó el turno, no solo la hora.
        telefono = self._ofrecer()
        mock_crear.return_value = {
            "id": "events/x",
            "location": {"id": "place-46ace5", "label": "EL PUENTE", "address": "Calle 1 746, Ordoñez"},
        }
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.procesar_eleccion(telefono, "el primero")
        self.assertIn("el puente", resultado["texto"].lower())

    @patch("drapp_client.cancelar_turno")
    @patch("drapp_client.crear_turno")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value={"id": "consumers/xyz789"})
    @patch("ai_router.interpretar_eleccion_turno", return_value={"outcome": "success", "data": {"eleccion": 0}})
    def test_turno_en_consultorio_equivocado_se_cancela_solo_y_no_se_confirma(self, mock_interp, mock_buscar, mock_crear, mock_cancelar):
        # v0.2.7 (20/08) -- hallazgo real: Medicina General es solo en
        # Ordoñez, pero DrApp elige el consultorio en silencio -- ya hubo un
        # turno real cargado a mano en Posse por error. Si esto vuelve a
        # pasar por WhatsApp, el bot tiene que cancelarlo solo, nunca
        # confirmárselo al paciente.
        telefono = self._ofrecer()
        mock_crear.return_value = {
            "id": "events/mal-asignado",
            "location": {"id": "place-7mtlojwwiev0sezrafw9oe", "label": "P-SIA", "address": "Justiniano Posse"},
        }
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.procesar_eleccion(telefono, "el primero")

        mock_cancelar.assert_called_once_with("events/mal-asignado")
        self.assertIsNone(resultado["accion"])
        self.assertNotIn("confirmado", resultado["texto"].lower())
        conv = self._conv_de(telefono)
        self.assertEqual(conv["estado"], "derivado")

    @patch("drapp_client.crear_turno")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value={"id": "consumers/xyz789"})
    @patch("ai_router.interpretar_eleccion_turno", return_value={"outcome": "success", "data": {"eleccion": 0}})
    def test_psiquiatria_reserva_de_punta_a_punta_en_posse(self, mock_interp, mock_buscar, mock_crear):
        # v0.2.7 (20/08) -- Fase de Psiquiatría: mismo flujo end-to-end que
        # Medicina General, pero apuntando a Posse -- service key, franja,
        # y red de seguridad de ubicación todas correctas para esta
        # especialidad.
        telefono = "+5493584390050"
        with patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_PSIQUIATRIA_FAKE):
            with patch.dict(os.environ, DRAPP_ENV):
                turnos_conversacion._ofrecer_horarios_especialidad("psiquiatria", telefono)

        mock_crear.return_value = {
            "id": "events/psi-nuevo",
            "location": {"id": "place-7mtlojwwiev0sezrafw9oe", "label": "P-SIA", "address": "Justiniano Posse"},
        }
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.procesar_eleccion(telefono, "el primero")

        self.assertIn("confirmado", resultado["texto"].lower())
        self.assertEqual(resultado["accion"], "turno_creado")
        mock_crear.assert_called_once_with(
            "resources/8c8a2304", "pms_specialties:psiquiatria/pms_practices:consulta",
            "consumers/xyz789", "2026-08-21", "08:30",
        )
        conv = self._conv_de(telefono)
        self.assertEqual(conv["estado"], "confirmado")

    @patch("drapp_client.cancelar_turno")
    @patch("drapp_client.crear_turno")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value={"id": "consumers/xyz789"})
    @patch("ai_router.interpretar_eleccion_turno", return_value={"outcome": "success", "data": {"eleccion": 0}})
    def test_psiquiatria_en_consultorio_equivocado_tambien_se_cancela_sola(self, mock_interp, mock_buscar, mock_crear, mock_cancelar):
        # La red de seguridad de ubicación también corre para Psiquiatría --
        # si DrApp la asigna a Ordoñez (fuera de la excepción mensual, que
        # este bot no maneja), se cancela sola igual que Medicina General.
        telefono = "+5493584390051"
        with patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_PSIQUIATRIA_FAKE):
            with patch.dict(os.environ, DRAPP_ENV):
                turnos_conversacion._ofrecer_horarios_especialidad("psiquiatria", telefono)

        mock_crear.return_value = {
            "id": "events/psi-mal-asignado",
            "location": {"id": "place-46ace5", "label": "Aneit", "address": "Ordoñez"},
        }
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.procesar_eleccion(telefono, "el primero")

        mock_cancelar.assert_called_once_with("events/psi-mal-asignado")
        self.assertIsNone(resultado["accion"])
        self.assertEqual(self._conv_de(telefono)["estado"], "derivado")

    def _conv_de(self, telefono):
        conn = db.get_connection()
        return dict(conn.execute(
            "SELECT * FROM turnos_conversacion WHERE telefono = ? ORDER BY creado_at DESC LIMIT 1", (telefono,)
        ).fetchone())

    @patch("drapp_client.crear_turno")
    @patch("ai_router.clasificar_y_redactar_mensaje")
    @patch("ai_router.interpretar_preferencia_fecha")
    @patch("ai_router.interpretar_eleccion_turno")
    def test_ia_no_entiende_no_reserva_nada(self, mock_interp, mock_pref, mock_redactar, mock_crear):
        telefono = self._ofrecer()
        mock_interp.return_value = {"outcome": "success", "data": {"eleccion": None}}
        mock_pref.return_value = {"outcome": "success", "data": {"dias_desde_hoy": None}}
        # Si la IA tampoco puede redactar un saludo natural (both_failed), el
        # respaldo sigue siendo la línea fija -- nunca se cuelga sin responder.
        mock_redactar.return_value = {"outcome": "both_failed", "error": "sin proveedores"}

        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.procesar_eleccion(telefono, "no sé, cualquiera")

        self.assertIn("no llegué a entender", resultado["texto"].lower())
        self.assertIsNone(resultado["accion"])
        mock_crear.assert_not_called()

    @patch("ai_router.clasificar_y_redactar_mensaje")
    @patch("ai_router.interpretar_preferencia_fecha")
    @patch("ai_router.interpretar_eleccion_turno")
    def test_saludo_dentro_de_conversacion_activa_lo_redacta_la_ia(self, mock_interp, mock_pref, mock_redactar):
        # v0.2.6 (20/08) -- pedido real de Nicolás: un "hola" en medio de la
        # conversación de turno no debería recibir la línea robótica de "no
        # entendí" -- que la IA redacte algo natural, sin cerrar la oferta.
        telefono = self._ofrecer()
        mock_interp.return_value = {"outcome": "success", "data": {"eleccion": None}}
        mock_pref.return_value = {"outcome": "success", "data": {"dias_desde_hoy": None}}
        mock_redactar.return_value = {
            "outcome": "success",
            "data": {
                "clasificacion": "ambiguo", "requiere_profesional": False, "urgente": False,
                "borrador_respuesta": "¡Hola! ¿en qué te puedo ayudar?",
            },
        }

        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.procesar_eleccion(telefono, "hola buen dia")

        self.assertEqual(resultado["texto"], "¡Hola! ¿en qué te puedo ayudar?")
        self.assertIsNone(resultado["accion"])
        conv = self._conv_de(telefono)
        self.assertEqual(conv["estado"], "esperando_eleccion")  # la oferta sigue en pie

    @patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE)
    @patch("ai_router.interpretar_preferencia_fecha", return_value={"outcome": "success", "data": {"dias_desde_hoy": 7}})
    @patch("ai_router.interpretar_eleccion_turno", return_value={"outcome": "success", "data": {"eleccion": None}})
    def test_pedido_de_otra_fecha_reofrece_en_vez_de_decir_no_entendi(self, mock_interp, mock_pref, mock_disp):
        # v0.2.6 (21/08) -- pedido real de Nicolás: si ninguna opción le
        # sirve y pide otra fecha, hay que reofrecer con esa fecha, no
        # solo decir "no entendí cuál elegiste".
        telefono = self._ofrecer()
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.procesar_eleccion(telefono, "¿no tenés para la semana que viene?")

        self.assertIsNotNone(resultado)
        self.assertNotIn("no llegué a entender", resultado["texto"].lower())
        self.assertIn("horarios", resultado["texto"].lower())
        # La conversación vieja quedó cerrada y se abrió una nueva con las
        # opciones reofrecidas.
        conv = self._conv_de(telefono)
        self.assertEqual(conv["estado"], "esperando_eleccion")
        conv = self._conv_de(telefono)
        self.assertEqual(conv["estado"], "esperando_eleccion")  # sigue abierta

    @patch("drapp_client.crear_turno", side_effect=drapp_client.DrAppConflictError("conflict", "ocupado", 409))
    @patch("drapp_client.buscar_paciente_por_telefono", return_value={"id": "consumers/xyz789"})
    @patch("ai_router.interpretar_eleccion_turno", return_value={"outcome": "success", "data": {"eleccion": 0}})
    def test_conflicto_409_no_se_trata_como_exito(self, mock_interp, mock_buscar, mock_crear):
        telefono = self._ofrecer()
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.procesar_eleccion(telefono, "el primero")
        self.assertIn("se ocupó", resultado["texto"].lower())
        self.assertIsNone(resultado["accion"])
        conv = self._conv_de(telefono)
        self.assertEqual(conv["estado"], "expirado")

    @patch("drapp_client.crear_turno")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value=None)
    @patch("ai_router.interpretar_eleccion_turno", return_value={"outcome": "success", "data": {"eleccion": 0}})
    def test_paciente_no_encontrado_por_telefono_pide_identificacion(self, mock_interp, mock_buscar, mock_crear):
        telefono = self._ofrecer()
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.procesar_eleccion(telefono, "el primero")
        self.assertIn("dni", resultado["texto"].lower())
        self.assertIsNone(resultado["accion"])
        mock_crear.assert_not_called()
        conv = self._conv_de(telefono)
        self.assertEqual(conv["estado"], "esperando_identificacion")
        self.assertEqual(conv["eleccion_index"], 0)  # recuerda qué había elegido

    @patch("drapp_client.crear_turno")
    @patch("drapp_client.buscar_pacientes_por_texto")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value=None)
    @patch("ai_router.interpretar_eleccion_turno", return_value={"outcome": "success", "data": {"eleccion": 1}})
    def test_identificacion_con_match_unico_reserva_el_turno_recordado(self, mock_interp, mock_buscar_tel, mock_buscar_texto, mock_crear):
        telefono = self._ofrecer()
        with patch.dict(os.environ, DRAPP_ENV):
            turnos_conversacion.procesar_eleccion(telefono, "el segundo")  # pide identificación

        mock_buscar_texto.return_value = [{"id": "consumers/dni12345678"}]
        mock_crear.return_value = {"id": "events/nuevo456"}
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.procesar_eleccion(telefono, "40123456")

        self.assertEqual(resultado["accion"], "turno_creado")
        mock_crear.assert_called_once_with(
            "resources/8c8a2304", "pms_specialties:medicina-general/pms_practices:consulta",
            "consumers/dni12345678", "2026-08-21", "10:00",  # la opción de índice 1, recordada
        )
        conv = self._conv_de(telefono)
        self.assertEqual(conv["estado"], "confirmado")

    @patch("drapp_client.buscar_pacientes_por_texto")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value=None)
    @patch("ai_router.interpretar_eleccion_turno", return_value={"outcome": "success", "data": {"eleccion": 0}})
    def test_identificacion_con_dos_matches_deriva_no_adivina(self, mock_interp, mock_buscar_tel, mock_buscar_texto):
        telefono = self._ofrecer()
        with patch.dict(os.environ, DRAPP_ENV):
            turnos_conversacion.procesar_eleccion(telefono, "el primero")

        mock_buscar_texto.return_value = [{"id": "consumers/juan-perez-1"}, {"id": "consumers/juan-perez-2"}]
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.procesar_eleccion(telefono, "Juan Pérez")

        self.assertIn("no pude confirmar", resultado["texto"].lower())
        self.assertIsNone(resultado["accion"])
        conv = self._conv_de(telefono)
        self.assertEqual(conv["estado"], "derivado")

    @patch("drapp_client.buscar_pacientes_por_texto", return_value=[])
    @patch("drapp_client.buscar_paciente_por_telefono", return_value=None)
    @patch("ai_router.interpretar_eleccion_turno", return_value={"outcome": "success", "data": {"eleccion": 0}})
    def test_identificacion_sin_matches_deriva(self, mock_interp, mock_buscar_tel, mock_buscar_texto):
        telefono = self._ofrecer()
        with patch.dict(os.environ, DRAPP_ENV):
            turnos_conversacion.procesar_eleccion(telefono, "el primero")
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.procesar_eleccion(telefono, "no sé mi dni")
        self.assertIn("no pude confirmar", resultado["texto"].lower())
        conv = self._conv_de(telefono)
        self.assertEqual(conv["estado"], "derivado")


class TestIniciarCancelacion(_BaseTemp):
    def test_sin_drapp_configurado_devuelve_none(self):
        os.environ.pop("DRAPP_RESOURCE_ID", None)
        os.environ.pop("DRAPP_SERVICE_KEY_MEDICINA_GENERAL", None)
        self.assertIsNone(turnos_conversacion.iniciar_cancelacion("+5493584390020"))

    @patch("drapp_client.buscar_paciente_por_telefono", return_value=None)
    def test_paciente_no_encontrado_por_telefono_pide_identificacion(self, mock_buscar):
        telefono = "+5493584390021"
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.iniciar_cancelacion(telefono)
        self.assertIn("no te encuentro", resultado["texto"].lower())
        self.assertIsNone(resultado["accion"])
        conv = self._conv_de(telefono)
        self.assertEqual(conv["estado"], "esperando_identificacion")
        self.assertEqual(conv["tipo"], "cancelacion")

    def _conv_de(self, telefono):
        conn = db.get_connection()
        return dict(conn.execute(
            "SELECT * FROM turnos_conversacion WHERE telefono = ? ORDER BY creado_at DESC LIMIT 1", (telefono,)
        ).fetchone())

    @patch("drapp_client.cancelar_turno")
    @patch("drapp_client.listar_turnos_de_paciente")
    @patch("drapp_client.buscar_pacientes_por_texto")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value=None)
    def test_identificacion_de_cancelacion_con_match_unico_cancela(self, mock_buscar_tel, mock_buscar_texto, mock_listar, mock_cancelar):
        import datetime
        telefono = "+5493584390027"
        with patch.dict(os.environ, DRAPP_ENV):
            turnos_conversacion.iniciar_cancelacion(telefono)  # pide identificación

        en_3_dias = datetime.datetime.now() + datetime.timedelta(days=3)
        mock_buscar_texto.return_value = [{"id": "consumers/dni40123456"}]
        mock_listar.return_value = [{
            "id": "events/abc", "status": "booked",
            "service": {"label": "Medicina General / Consulta"},
            "day": en_3_dias.strftime("%Y-%m-%d"), "time": en_3_dias.strftime("%H:%M"),
        }]
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.procesar_eleccion(telefono, "40123456")

        self.assertEqual(resultado["accion"], "turno_cancelado")
        mock_cancelar.assert_called_once_with("events/abc")
        conv = self._conv_de(telefono)
        self.assertEqual(conv["estado"], "cancelado")

    @patch("drapp_client.listar_turnos_de_paciente", return_value=[])
    @patch("drapp_client.buscar_paciente_por_telefono", return_value={"id": "consumers/x"})
    def test_sin_turnos_futuros_pide_aclaracion(self, mock_buscar, mock_listar):
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.iniciar_cancelacion("+5493584390022")
        self.assertIn("no encontré", resultado["texto"].lower())
        self.assertIsNone(resultado["accion"])

    @patch("drapp_client.cancelar_turno")
    @patch("drapp_client.listar_turnos_de_paciente")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value={"id": "consumers/x"})
    def test_24hs_o_mas_cancela_solo(self, mock_buscar, mock_listar, mock_cancelar):
        import datetime
        en_3_dias = (datetime.datetime.now() + datetime.timedelta(days=3))
        mock_listar.return_value = [{
            "id": "events/abc", "status": "booked",
            "service": {"label": "Medicina General / Consulta"},
            "day": en_3_dias.strftime("%Y-%m-%d"), "time": en_3_dias.strftime("%H:%M"),
        }]
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.iniciar_cancelacion("+5493584390023")
        self.assertIn("cancelamos", resultado["texto"].lower())
        self.assertEqual(resultado["accion"], "turno_cancelado")
        mock_cancelar.assert_called_once_with("events/abc")

    @patch("drapp_client.cancelar_turno")
    @patch("drapp_client.listar_turnos_de_paciente")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value={"id": "consumers/x"})
    def test_menos_de_24hs_deriva_no_cancela(self, mock_buscar, mock_listar, mock_cancelar):
        import datetime
        en_5_horas = (datetime.datetime.now() + datetime.timedelta(hours=5))
        mock_listar.return_value = [{
            "id": "events/abc", "status": "booked",
            "service": {"label": "Medicina General / Consulta"},
            "day": en_5_horas.strftime("%Y-%m-%d"), "time": en_5_horas.strftime("%H:%M"),
        }]
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.iniciar_cancelacion("+5493584390024")
        self.assertIn("menos de 24hs", resultado["texto"].lower())
        self.assertIsNone(resultado["accion"])
        mock_cancelar.assert_not_called()

    @patch("drapp_client.cancelar_turno")
    @patch("drapp_client.listar_turnos_de_paciente")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value={"id": "consumers/x"})
    def test_dos_turnos_futuros_deriva_no_cancela_ninguno(self, mock_buscar, mock_listar, mock_cancelar):
        import datetime
        en_3_dias = (datetime.datetime.now() + datetime.timedelta(days=3))
        base = {
            "status": "booked", "service": {"label": "Medicina General / Consulta"},
            "day": en_3_dias.strftime("%Y-%m-%d"), "time": en_3_dias.strftime("%H:%M"),
        }
        mock_listar.return_value = [dict(base, id="events/uno"), dict(base, id="events/dos")]
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.iniciar_cancelacion("+5493584390025")
        self.assertIn("más de un turno", resultado["texto"].lower())
        self.assertIsNone(resultado["accion"])
        mock_cancelar.assert_not_called()

    @patch("drapp_client.cancelar_turno")
    @patch("drapp_client.listar_turnos_de_paciente")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value={"id": "consumers/x"})
    def test_psiquiatria_tambien_se_puede_cancelar(self, mock_buscar, mock_listar, mock_cancelar):
        # v0.2.7 (20/08) -- antes Psiquiatría quedaba afuera a propósito (no
        # se agendaba por WhatsApp); ahora que sí, también se puede
        # cancelar -- misma ventana de 24hs, confirmado con Nicolás.
        import datetime
        en_3_dias = (datetime.datetime.now() + datetime.timedelta(days=3))
        mock_listar.return_value = [{
            "id": "events/psi", "status": "booked",
            "service": {"label": "Psiquiatría / Consulta Psiquiatría"},
            "day": en_3_dias.strftime("%Y-%m-%d"), "time": en_3_dias.strftime("%H:%M"),
        }]
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.iniciar_cancelacion("+5493584390026")
        self.assertIn("cancelamos", resultado["texto"].lower())
        self.assertEqual(resultado["accion"], "turno_cancelado")
        mock_cancelar.assert_called_once_with("events/psi")

    @patch("drapp_client.cancelar_turno")
    @patch("drapp_client.listar_turnos_de_paciente")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value={"id": "consumers/x"})
    def test_otra_especialidad_no_se_cancela_por_este_bot(self, mock_buscar, mock_listar, mock_cancelar):
        # Un turno de una especialidad que este bot no maneja (ej.
        # Cardiología, si comparte cuenta de DrApp) se ignora -- nunca lo
        # toca, aunque sea el único turno futuro del paciente.
        import datetime
        en_3_dias = (datetime.datetime.now() + datetime.timedelta(days=3))
        mock_listar.return_value = [{
            "id": "events/card", "status": "booked",
            "service": {"label": "Cardiología / Consulta"},
            "day": en_3_dias.strftime("%Y-%m-%d"), "time": en_3_dias.strftime("%H:%M"),
        }]
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.iniciar_cancelacion("+5493584390029")
        self.assertIn("no encontré", resultado["texto"].lower())
        self.assertIsNone(resultado["accion"])
        mock_cancelar.assert_not_called()


class TestIniciarReprogramacion(_BaseTemp):
    """v0.2.8 (24/08) -- pedido real de Nicolás: reprogramar en un solo
    paso (buscar el turno actual + ofrecer horarios nuevos de la misma
    especialidad + mover el turno viejo al elegido), en vez de cancelar y
    tener que pedir uno nuevo aparte."""

    def _conv_de(self, telefono):
        conn = db.get_connection()
        return dict(conn.execute(
            "SELECT * FROM turnos_conversacion WHERE telefono = ? ORDER BY creado_at DESC LIMIT 1", (telefono,)
        ).fetchone())

    def test_sin_drapp_configurado_devuelve_none(self):
        os.environ.pop("DRAPP_RESOURCE_ID", None)
        os.environ.pop("DRAPP_SERVICE_KEY_MEDICINA_GENERAL", None)
        self.assertIsNone(turnos_conversacion.iniciar_reprogramacion("+5493584390060"))

    @patch("drapp_client.buscar_paciente_por_telefono", return_value=None)
    def test_paciente_no_encontrado_por_telefono_pide_identificacion(self, mock_buscar):
        telefono = "+5493584390061"
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.iniciar_reprogramacion(telefono)
        self.assertIn("no te encuentro", resultado["texto"].lower())
        self.assertIsNone(resultado["accion"])
        conv = self._conv_de(telefono)
        self.assertEqual(conv["estado"], "esperando_identificacion")
        self.assertEqual(conv["tipo"], "reprogramacion")

    @patch("drapp_client.listar_turnos_de_paciente", return_value=[])
    @patch("drapp_client.buscar_paciente_por_telefono", return_value={"id": "consumers/x"})
    def test_sin_turnos_futuros_no_ofrece_nada(self, mock_buscar, mock_listar):
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.iniciar_reprogramacion("+5493584390062")
        self.assertIn("no encontré", resultado["texto"].lower())
        self.assertIsNone(resultado["accion"])

    @patch("drapp_client.listar_turnos_de_paciente")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value={"id": "consumers/x"})
    def test_dos_turnos_futuros_deriva(self, mock_buscar, mock_listar):
        import datetime
        en_3_dias = (datetime.datetime.now() + datetime.timedelta(days=3))
        base = {
            "status": "booked", "service": {"label": "Medicina General / Consulta"},
            "day": en_3_dias.strftime("%Y-%m-%d"), "time": en_3_dias.strftime("%H:%M"),
        }
        mock_listar.return_value = [dict(base, id="events/uno"), dict(base, id="events/dos")]
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.iniciar_reprogramacion("+5493584390063")
        self.assertIn("más de un turno", resultado["texto"].lower())
        self.assertIsNone(resultado["accion"])

    @patch("drapp_client.listar_turnos_de_paciente")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value={"id": "consumers/x"})
    def test_menos_de_24hs_deriva_no_ofrece(self, mock_buscar, mock_listar):
        import datetime
        en_5_horas = (datetime.datetime.now() + datetime.timedelta(hours=5))
        mock_listar.return_value = [{
            "id": "events/abc", "status": "booked",
            "service": {"label": "Medicina General / Consulta"},
            "day": en_5_horas.strftime("%Y-%m-%d"), "time": en_5_horas.strftime("%H:%M"),
        }]
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.iniciar_reprogramacion("+5493584390064")
        self.assertIn("menos de 24hs", resultado["texto"].lower())
        self.assertIsNone(resultado["accion"])

    @patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE)
    @patch("drapp_client.listar_turnos_de_paciente")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value={"id": "consumers/x"})
    def test_24hs_o_mas_ofrece_horarios_nuevos_de_la_misma_especialidad(self, mock_buscar, mock_listar, mock_disp):
        import datetime
        en_3_dias = (datetime.datetime.now() + datetime.timedelta(days=3))
        mock_listar.return_value = [{
            "id": "events/viejo", "status": "booked",
            "service": {"label": "Medicina General / Consulta"},
            "day": en_3_dias.strftime("%Y-%m-%d"), "time": en_3_dias.strftime("%H:%M"),
        }]
        telefono = "+5493584390065"
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.iniciar_reprogramacion(telefono)
        self.assertIn("reprogramar tu turno", resultado["texto"].lower())
        self.assertIn("1)", resultado["texto"])
        self.assertIsNone(resultado["accion"])  # todavía no se movió nada
        conv = self._conv_de(telefono)
        self.assertEqual(conv["tipo"], "reprogramacion")
        self.assertEqual(conv["estado"], "esperando_eleccion")
        self.assertEqual(conv["especialidad"], "medicina_general")
        self.assertEqual(conv["drapp_event_id"], "events/viejo")  # el turno VIEJO a mover

    @patch("drapp_client.reprogramar_turno")
    @patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE)
    @patch("drapp_client.listar_turnos_de_paciente")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value={"id": "consumers/x"})
    @patch("ai_router.interpretar_eleccion_turno", return_value={"outcome": "success", "data": {"eleccion": 0}})
    def test_eleccion_confirma_la_reprogramacion_de_verdad(self, mock_interp, mock_buscar, mock_listar, mock_disp, mock_reprog):
        import datetime
        en_3_dias = (datetime.datetime.now() + datetime.timedelta(days=3))
        mock_listar.return_value = [{
            "id": "events/viejo", "status": "booked",
            "service": {"label": "Medicina General / Consulta"},
            "day": en_3_dias.strftime("%Y-%m-%d"), "time": en_3_dias.strftime("%H:%M"),
        }]
        mock_reprog.return_value = {
            "id": "events/viejo",
            "location": {"id": "place-46ace5", "label": "Aneit", "address": "Ordoñez"},
        }
        telefono = "+5493584390066"
        with patch.dict(os.environ, DRAPP_ENV):
            turnos_conversacion.iniciar_reprogramacion(telefono)
            resultado = turnos_conversacion.procesar_eleccion(telefono, "el primero")

        self.assertIn("reprogramamos", resultado["texto"].lower())
        self.assertEqual(resultado["accion"], "turno_reprogramado")
        mock_reprog.assert_called_once_with("events/viejo", "2026-08-20", "11:00")
        conv = self._conv_de(telefono)
        self.assertEqual(conv["estado"], "confirmado")

    @patch("drapp_client.reprogramar_turno")
    @patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE)
    @patch("drapp_client.listar_turnos_de_paciente")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value={"id": "consumers/x"})
    @patch("ai_router.interpretar_eleccion_turno", return_value={"outcome": "success", "data": {"eleccion": 0}})
    def test_reprogramacion_en_consultorio_equivocado_se_deriva_no_confirma(self, mock_interp, mock_buscar, mock_listar, mock_disp, mock_reprog):
        # Misma red de seguridad que crear un turno nuevo -- pero acá no se
        # "cancela" nada (el turno YA se movió en DrApp), se deriva para
        # que una persona lo resuelva a mano.
        import datetime
        en_3_dias = (datetime.datetime.now() + datetime.timedelta(days=3))
        mock_listar.return_value = [{
            "id": "events/viejo", "status": "booked",
            "service": {"label": "Medicina General / Consulta"},
            "day": en_3_dias.strftime("%Y-%m-%d"), "time": en_3_dias.strftime("%H:%M"),
        }]
        mock_reprog.return_value = {
            "id": "events/viejo",
            "location": {"id": "place-7mtlojwwiev0sezrafw9oe", "label": "P-SIA", "address": "Justiniano Posse"},
        }
        telefono = "+5493584390067"
        with patch.dict(os.environ, DRAPP_ENV):
            turnos_conversacion.iniciar_reprogramacion(telefono)
            resultado = turnos_conversacion.procesar_eleccion(telefono, "el primero")

        self.assertIsNone(resultado["accion"])
        self.assertNotIn("reprogramamos", resultado["texto"].lower())
        conv = self._conv_de(telefono)
        self.assertEqual(conv["estado"], "derivado")

    @patch("drapp_client.reprogramar_turno")
    @patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE)
    @patch("drapp_client.listar_turnos_de_paciente")
    @patch("drapp_client.buscar_pacientes_por_texto")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value=None)
    @patch("ai_router.interpretar_eleccion_turno", return_value={"outcome": "success", "data": {"eleccion": 0}})
    def test_identificacion_de_reprogramacion_con_match_unico_ofrece_y_confirma(
        self, mock_interp, mock_buscar_tel, mock_buscar_texto, mock_listar, mock_disp, mock_reprog,
    ):
        import datetime
        telefono = "+5493584390068"
        with patch.dict(os.environ, DRAPP_ENV):
            turnos_conversacion.iniciar_reprogramacion(telefono)  # pide identificación

        en_3_dias = datetime.datetime.now() + datetime.timedelta(days=3)
        mock_buscar_texto.return_value = [{"id": "consumers/dni40123456"}]
        mock_listar.return_value = [{
            "id": "events/viejo", "status": "booked",
            "service": {"label": "Medicina General / Consulta"},
            "day": en_3_dias.strftime("%Y-%m-%d"), "time": en_3_dias.strftime("%H:%M"),
        }]
        mock_reprog.return_value = {"id": "events/viejo", "location": {"id": "place-46ace5"}}

        with patch.dict(os.environ, DRAPP_ENV):
            ofrecido = turnos_conversacion.procesar_eleccion(telefono, "40123456")
        self.assertIn("reprogramar tu turno", ofrecido["texto"].lower())
        self.assertEqual(self._conv_de(telefono)["tipo"], "reprogramacion")

        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.procesar_eleccion(telefono, "el primero")
        self.assertEqual(resultado["accion"], "turno_reprogramado")
        mock_reprog.assert_called_once_with("events/viejo", "2026-08-20", "11:00")


class TestIntegracionMensajesWhatsapp(_BaseTemp):
    @patch("ai_router.clasificar_y_redactar_mensaje")
    def test_conversacion_activa_salta_la_clasificacion_generica(self, mock_clasificar):
        with patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE):
            with patch.dict(os.environ, DRAPP_ENV):
                turnos_conversacion._ofrecer_horarios_especialidad("medicina_general", "+5493584390030")

        mensaje_id = mensajes_whatsapp.registrar_mensaje_entrante("+5493584390030", "el segundo")["id"]

        with patch("ai_router.interpretar_eleccion_turno", return_value={"outcome": "success", "data": {"eleccion": 1}}):
            with patch("drapp_client.buscar_paciente_por_telefono", return_value={"id": "consumers/x"}):
                with patch("drapp_client.crear_turno", return_value={"id": "events/x"}):
                    with patch.dict(os.environ, DRAPP_ENV):
                        resultado = mensajes_whatsapp.generar_borrador(mensaje_id)

        self.assertTrue(resultado["ok"])
        mock_clasificar.assert_not_called()  # nunca corrió la clasificación genérica
        mensaje = mensajes_whatsapp.list_mensajes()[0]
        self.assertIn("confirmado", mensaje["borrador_respuesta"].lower())
        self.assertFalse(mensaje["requiere_profesional"])
        self.assertEqual(mensaje["accion_drapp"], "turno_creado")  # el tag que ve Marianela/Nicolás

    @patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE)
    @patch("ai_router.interpretar_especialidad", return_value=ESP_MEDICINA_GENERAL)
    @patch("ai_router.clasificar_y_redactar_mensaje")
    def test_turno_nuevo_con_drapp_usa_horarios_reales(self, mock_clasificar, mock_esp, mock_disp):
        mock_clasificar.return_value = {
            "outcome": "success",
            "data": {"clasificacion": "turno_nuevo", "requiere_profesional": False, "urgente": False, "borrador_respuesta": "texto genérico de la IA"},
        }
        mensaje_id = mensajes_whatsapp.registrar_mensaje_entrante("+5493584390031", "quiero un turno")["id"]

        with patch.dict(os.environ, DRAPP_ENV):
            mensajes_whatsapp.generar_borrador(mensaje_id)

        mensaje = mensajes_whatsapp.list_mensajes()[0]
        self.assertIn("1)", mensaje["borrador_respuesta"])  # opciones reales, no el texto genérico
        self.assertNotIn("texto genérico", mensaje["borrador_respuesta"])
        self.assertIsNone(mensaje["accion_drapp"])  # solo ofreció, todavía no reservó nada

    @patch("ai_router.clasificar_y_redactar_mensaje")
    def test_turno_nuevo_sin_drapp_usa_el_texto_generico_de_respaldo(self, mock_clasificar):
        mock_clasificar.return_value = {
            "outcome": "success",
            "data": {"clasificacion": "turno_nuevo", "requiere_profesional": False, "urgente": False, "borrador_respuesta": "alguien va a confirmar el horario"},
        }
        os.environ.pop("DRAPP_RESOURCE_ID", None)
        os.environ.pop("DRAPP_SERVICE_KEY_MEDICINA_GENERAL", None)
        mensaje_id = mensajes_whatsapp.registrar_mensaje_entrante("+5493584390032", "quiero un turno")["id"]

        mensajes_whatsapp.generar_borrador(mensaje_id)

        mensaje = mensajes_whatsapp.list_mensajes()[0]
        self.assertEqual(mensaje["borrador_respuesta"], "alguien va a confirmar el horario")

    @patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE)
    @patch("drapp_client.listar_turnos_de_paciente")
    @patch("drapp_client.buscar_paciente_por_telefono", return_value={"id": "consumers/x"})
    @patch("ai_router.clasificar_y_redactar_mensaje")
    def test_reprogramacion_con_drapp_ofrece_horarios_nuevos(self, mock_clasificar, mock_buscar, mock_listar, mock_disp):
        # v0.2.8 (24/08) -- pedido real de Nicolás: "reprogramacion" ya era
        # una clasificación válida de la IA, pero nunca estaba conectada a
        # ninguna acción real -- siempre caía en el texto genérico.
        import datetime
        en_3_dias = datetime.datetime.now() + datetime.timedelta(days=3)
        mock_listar.return_value = [{
            "id": "events/viejo", "status": "booked",
            "service": {"label": "Medicina General / Consulta"},
            "day": en_3_dias.strftime("%Y-%m-%d"), "time": en_3_dias.strftime("%H:%M"),
        }]
        mock_clasificar.return_value = {
            "outcome": "success",
            "data": {"clasificacion": "reprogramacion", "requiere_profesional": False, "urgente": False, "borrador_respuesta": "texto genérico de la IA"},
        }
        mensaje_id = mensajes_whatsapp.registrar_mensaje_entrante("+5493584390033", "quiero cambiar mi turno")["id"]

        with patch.dict(os.environ, DRAPP_ENV):
            mensajes_whatsapp.generar_borrador(mensaje_id)

        mensaje = mensajes_whatsapp.list_mensajes()[0]
        self.assertIn("reprogramar tu turno", mensaje["borrador_respuesta"].lower())
        self.assertNotIn("texto genérico", mensaje["borrador_respuesta"])
        self.assertIsNone(mensaje["accion_drapp"])  # solo ofreció, todavía no movió nada


class TestListConversacionesRecientes(_BaseTemp):
    # v0.2.7 (20/08) -- panel de salud de Fase C en el Resumen del Director.
    def test_trae_conversaciones_dentro_de_la_ventana(self):
        with patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE):
            with patch.dict(os.environ, DRAPP_ENV):
                turnos_conversacion._ofrecer_horarios_especialidad("medicina_general", "+5493584390060")

        conversaciones = turnos_conversacion.list_conversaciones_recientes(horas=24)
        self.assertEqual(len(conversaciones), 1)
        self.assertEqual(conversaciones[0]["telefono"], "+5493584390060")
        self.assertEqual(conversaciones[0]["estado"], "esperando_eleccion")
        self.assertEqual(conversaciones[0]["especialidad"], "medicina_general")

    def test_conversacion_vieja_fuera_de_la_ventana_no_aparece(self):
        with patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE):
            with patch.dict(os.environ, DRAPP_ENV):
                turnos_conversacion._ofrecer_horarios_especialidad("medicina_general", "+5493584390061")

        conn = db.get_connection()
        hace_2_dias = (datetime.datetime.utcnow() - datetime.timedelta(days=2)).isoformat()
        conn.execute(
            "UPDATE turnos_conversacion SET creado_at = ?, actualizado_at = ? WHERE telefono = ?",
            (hace_2_dias, hace_2_dias, "+5493584390061"),
        )
        conn.commit()

        self.assertEqual(turnos_conversacion.list_conversaciones_recientes(horas=24), [])


if __name__ == "__main__":
    unittest.main()
