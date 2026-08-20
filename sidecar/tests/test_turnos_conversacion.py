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

DRAPP_ENV = {"DRAPP_RESOURCE_ID": "resources/8c8a2304", "DRAPP_SERVICE_KEY_MEDICINA_GENERAL": "pms_specialties:medicina-general/pms_practices:consulta"}

DISPONIBILIDAD_FAKE = {
    "slots": {
        # v0.2.6 (21/08) -- ofrecer_horarios ahora toma UN horario por día
        # distinto (no todos seguidos del mismo día) -- 3 días separados
        # para que el test siga cubriendo eso. El 10:10 del primer día
        # queda a propósito para confirmar que NO se ofrece (se prioriza
        # variedad de días sobre el segundo horario del mismo día).
        "2026-08-20": {"10:00": {"capacity": 1}, "10:10": {"capacity": 1}},
        "2026-08-21": {"09:00": {"capacity": 1}},
        "2026-08-24": {"11:00": {"capacity": 1}},
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
    def test_sin_drapp_configurado_devuelve_none(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DRAPP_RESOURCE_ID", None)
            os.environ.pop("DRAPP_SERVICE_KEY_MEDICINA_GENERAL", None)
            self.assertIsNone(turnos_conversacion.ofrecer_horarios("+5493584390001"))

    @patch("drapp_client.consultar_disponibilidad")
    def test_arma_hasta_tres_opciones_reales(self, mock_disp):
        mock_disp.return_value = DISPONIBILIDAD_FAKE
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.ofrecer_horarios("+5493584390002")
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
        self.assertEqual(opciones[0]["time"], "10:00")

    @patch("drapp_client.consultar_disponibilidad")
    def test_sin_horarios_disponibles_no_crea_conversacion(self, mock_disp):
        mock_disp.return_value = {"slots": {}}
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.ofrecer_horarios("+5493584390003")
        self.assertIn("no tenemos horarios", resultado["texto"].lower())
        self.assertIsNone(resultado["accion"])
        self.assertIsNone(turnos_conversacion.hay_conversacion_activa("+5493584390003"))

    @patch("drapp_client.consultar_disponibilidad", side_effect=drapp_client.DrAppAPIError("server_error", "falló", 500))
    def test_error_de_drapp_devuelve_none(self, mock_disp):
        with patch.dict(os.environ, DRAPP_ENV):
            self.assertIsNone(turnos_conversacion.ofrecer_horarios("+5493584390004"))

    @patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE)
    @patch("ai_router.interpretar_preferencia_fecha", return_value={"outcome": "success", "data": {"dias_desde_hoy": 7}})
    def test_preferencia_de_fecha_del_mensaje_corre_la_ventana_de_busqueda(self, mock_pref, mock_disp):
        # v0.2.6 (21/08) -- pedido real de Nicolás: "para la semana que
        # viene" tiene que buscarse desde esa fecha, no desde hoy.
        import datetime
        with patch.dict(os.environ, DRAPP_ENV):
            turnos_conversacion.ofrecer_horarios("+5493584390005", "quiero un turno para la semana que viene")

        mock_pref.assert_called_once_with("quiero un turno para la semana que viene")
        desde_usado = mock_disp.call_args[0][2]  # consultar_disponibilidad(resource, service, desde, hasta)
        desde_esperado = (datetime.datetime.now().date() + datetime.timedelta(days=7)).isoformat()
        self.assertEqual(desde_usado, desde_esperado)

    @patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE)
    def test_sin_preferencia_de_fecha_busca_desde_hoy(self, mock_disp):
        import datetime
        with patch.dict(os.environ, DRAPP_ENV):
            turnos_conversacion.ofrecer_horarios("+5493584390006", "quiero un turno")
        desde_usado = mock_disp.call_args[0][2]
        self.assertEqual(desde_usado, datetime.datetime.now().date().isoformat())

    @patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE)
    def test_ofrece_un_horario_por_dia_distinto_no_todos_seguidos(self, mock_disp):
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.ofrecer_horarios("+5493584390007")
        conv = turnos_conversacion.hay_conversacion_activa("+5493584390007")
        opciones = json.loads(conv["opciones_json"])
        dias = [o["day"] for o in opciones]
        self.assertEqual(len(dias), len(set(dias)))  # ningún día repetido
        self.assertNotIn("10:10", resultado["texto"])  # el 2do horario del mismo día no se ofrece


class TestProcesarEleccion(_BaseTemp):
    def _ofrecer(self, telefono="+5493584390010"):
        with patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE):
            with patch.dict(os.environ, DRAPP_ENV):
                turnos_conversacion.ofrecer_horarios(telefono)
        return telefono

    def test_sin_conversacion_activa_devuelve_none(self):
        self.assertIsNone(turnos_conversacion.procesar_eleccion("+5493584390099", "el segundo"))

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
            "consumers/xyz789", "2026-08-21", "09:00",  # opción de índice 1 -- primer horario del 2do día distinto
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
        mock_crear.return_value = {"id": "events/x", "location": {"label": "EL PUENTE", "address": "Calle 1 746, Ordoñez"}}
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.procesar_eleccion(telefono, "el primero")
        self.assertIn("el puente", resultado["texto"].lower())

    def _conv_de(self, telefono):
        conn = db.get_connection()
        return dict(conn.execute(
            "SELECT * FROM turnos_conversacion WHERE telefono = ? ORDER BY creado_at DESC LIMIT 1", (telefono,)
        ).fetchone())

    @patch("drapp_client.crear_turno")
    @patch("ai_router.interpretar_eleccion_turno")
    def test_ia_no_entiende_no_reserva_nada(self, mock_interp, mock_crear):
        telefono = self._ofrecer()
        mock_interp.return_value = {"outcome": "success", "data": {"eleccion": None}}

        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.procesar_eleccion(telefono, "no sé, cualquiera")

        self.assertIn("no llegué a entender", resultado["texto"].lower())
        self.assertIsNone(resultado["accion"])
        mock_crear.assert_not_called()

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
            "consumers/dni12345678", "2026-08-21", "09:00",  # la opción de índice 1, recordada
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
    def test_psiquiatria_queda_afuera(self, mock_buscar, mock_listar, mock_cancelar):
        import datetime
        en_3_dias = (datetime.datetime.now() + datetime.timedelta(days=3))
        mock_listar.return_value = [{
            "id": "events/psi", "status": "booked",
            "service": {"label": "Psiquiatría / Consulta Psiquiatría"},
            "day": en_3_dias.strftime("%Y-%m-%d"), "time": en_3_dias.strftime("%H:%M"),
        }]
        with patch.dict(os.environ, DRAPP_ENV):
            resultado = turnos_conversacion.iniciar_cancelacion("+5493584390026")
        self.assertIn("no encontré", resultado["texto"].lower())  # no hay turnos de Medicina General
        self.assertIsNone(resultado["accion"])
        mock_cancelar.assert_not_called()


class TestIntegracionMensajesWhatsapp(_BaseTemp):
    @patch("ai_router.clasificar_y_redactar_mensaje")
    def test_conversacion_activa_salta_la_clasificacion_generica(self, mock_clasificar):
        with patch("drapp_client.consultar_disponibilidad", return_value=DISPONIBILIDAD_FAKE):
            with patch.dict(os.environ, DRAPP_ENV):
                turnos_conversacion.ofrecer_horarios("+5493584390030")

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
    @patch("ai_router.clasificar_y_redactar_mensaje")
    def test_turno_nuevo_con_drapp_usa_horarios_reales(self, mock_clasificar, mock_disp):
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


if __name__ == "__main__":
    unittest.main()
