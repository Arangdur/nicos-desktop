// Pestaña "Calendario" (vista Operativa) — v0.2.5. Vista mensual de los
// turnos de Medicina General que ya conoce NicOS (recordatorios_turnos:
// cargados a mano o sincronizados desde DrApp) -- Psiquiatría no aparece acá
// a propósito, DrApp ya la cubre con su propia agenda. Es de SOLO LECTURA,
// mismo criterio que la pestaña Recordatorios: Marianela ve, el Director (o
// la sync de DrApp) es quien carga.
//
// No pega a ninguna ruta nueva del backend -- reusa window.nicos.recordatoriosList()
// (la misma que ya usa recordatorios-avisos.js) y agrupa por fecha acá en el
// front. Si el día de mañana hay muchos turnos, conviene mover el agrupado al
// sidecar -- por ahora la cantidad es chica y no vale la pena la ruta nueva.

const DIAS_SEMANA = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom'];
const MESES = [
  'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
  'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre',
];

let calendarioAnio = null;
let calendarioMes = null; // 0-11
let calendarioDiaSeleccionado = null; // "YYYY-MM-DD"
let calendarioTurnosPorFecha = {};

function _hoyISO() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

async function loadCalendarioTab() {
  const hoy = new Date();
  if (calendarioAnio === null) {
    calendarioAnio = hoy.getFullYear();
    calendarioMes = hoy.getMonth();
    calendarioDiaSeleccionado = _hoyISO();
  }
  const el = document.getElementById('tab-calendario');
  el.innerHTML = `
    <div class="card">
      <div class="row-between">
        <h3 style="margin:0;">Calendario de turnos — Medicina General</h3>
        <div class="row-wrap">
          <button class="secondary" id="cal-prev">‹</button>
          <button class="secondary" id="cal-hoy">Hoy</button>
          <button class="secondary" id="cal-next">›</button>
        </div>
      </div>
      <p class="help-text">
        Psiquiatría no aparece acá -- ya tiene su propia agenda en DrApp. Hacé clic en un
        día para ver el detalle.
      </p>
      <div id="cal-grid"></div>
    </div>
    <div class="card">
      <h3 id="cal-dia-titulo">Turnos del día</h3>
      <div id="cal-dia-lista"></div>
    </div>
  `;

  document.getElementById('cal-prev').addEventListener('click', () => _cambiarMes(-1));
  document.getElementById('cal-next').addEventListener('click', () => _cambiarMes(1));
  document.getElementById('cal-hoy').addEventListener('click', () => {
    const h = new Date();
    calendarioAnio = h.getFullYear();
    calendarioMes = h.getMonth();
    calendarioDiaSeleccionado = _hoyISO();
    _renderCalendario();
  });

  await _cargarTurnosYRenderizar();
}

function _cambiarMes(delta) {
  calendarioMes += delta;
  if (calendarioMes > 11) { calendarioMes = 0; calendarioAnio += 1; }
  if (calendarioMes < 0) { calendarioMes = 11; calendarioAnio -= 1; }
  _renderCalendario();
}

async function _cargarTurnosYRenderizar() {
  document.getElementById('cal-grid').innerHTML = '<div class="empty">Cargando...</div>';
  const data = await window.nicos.recordatoriosList();
  if (!data.ok) {
    document.getElementById('cal-grid').innerHTML = `<div class="error-box">${escHtml(data.error)}</div>`;
    return;
  }
  calendarioTurnosPorFecha = {};
  data.recordatorios.forEach((r) => {
    if (!calendarioTurnosPorFecha[r.fecha_turno]) calendarioTurnosPorFecha[r.fecha_turno] = [];
    calendarioTurnosPorFecha[r.fecha_turno].push(r);
  });
  _renderCalendario();
}

const ESTADO_TAG_CAL = {
  pendiente: { clase: 'proceso', label: 'Pendiente' },
  enviado: { clase: 'resuelto', label: 'Recordatorio enviado' },
  sin_telefono: { clase: 'nuevo', label: 'Sin teléfono' },
  fallo_envio: { clase: 'nuevo', label: 'Falló el envío' },
};

function _renderCalendario() {
  const hoyISO = _hoyISO();
  const primerDiaMes = new Date(calendarioAnio, calendarioMes, 1);
  // getDay(): 0=domingo -- lo corremos para que la semana arranque en lunes.
  const offset = (primerDiaMes.getDay() + 6) % 7;
  const diasEnMes = new Date(calendarioAnio, calendarioMes + 1, 0).getDate();

  const celdas = [];
  for (let i = 0; i < offset; i++) celdas.push(null);
  for (let dia = 1; dia <= diasEnMes; dia++) celdas.push(dia);

  const filasHtml = [];
  for (let i = 0; i < celdas.length; i += 7) {
    const semana = celdas.slice(i, i + 7);
    filasHtml.push(`
      <div class="cal-semana" style="display:grid; grid-template-columns:repeat(7,1fr); gap:6px; margin-bottom:6px;">
        ${semana.map((dia) => _celdaDia(dia, hoyISO)).join('')}
      </div>
    `);
  }

  document.getElementById('cal-grid').innerHTML = `
    <div class="row-between" style="margin:var(--space-3) 0;">
      <strong style="font-size:var(--text-lg);">${MESES[calendarioMes]} ${calendarioAnio}</strong>
    </div>
    <div style="display:grid; grid-template-columns:repeat(7,1fr); gap:6px; margin-bottom:6px;">
      ${DIAS_SEMANA.map((d) => `<div style="text-align:center; font-size:var(--text-xs); color:var(--muted); font-weight:600;">${d}</div>`).join('')}
    </div>
    ${filasHtml.join('')}
  `;

  // v0.2.5 -- Impeccable P1 (re-critique): las celdas eran <div> con solo
  // click -- Tab las saltaba entero, no había forma de abrir el detalle de
  // un día con el teclado.
  document.querySelectorAll('[data-cal-dia]').forEach((celda) => {
    const abrir = () => {
      calendarioDiaSeleccionado = celda.dataset.calDia;
      _renderCalendario();
      _renderListaDia();
    };
    celda.addEventListener('click', abrir);
    celda.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); abrir(); }
    });
  });

  _renderListaDia();
}

function _celdaDia(dia, hoyISO) {
  if (dia === null) return '<div></div>';
  const fechaISO = `${calendarioAnio}-${String(calendarioMes + 1).padStart(2, '0')}-${String(dia).padStart(2, '0')}`;
  const turnos = calendarioTurnosPorFecha[fechaISO] || [];
  const necesitaAtencion = turnos.some((t) => t.estado === 'sin_telefono' || t.estado === 'fallo_envio');
  const esHoy = fechaISO === hoyISO;
  const esSeleccionado = fechaISO === calendarioDiaSeleccionado;

  return `
    <div data-cal-dia="${fechaISO}" tabindex="0" role="button" aria-label="Turnos del día ${dia}" style="
      min-height:56px; padding:6px; border-radius:var(--radius-sm); cursor:pointer;
      border:1px solid ${esSeleccionado ? 'var(--navy)' : 'var(--border)'};
      background:${esSeleccionado ? 'var(--navy-soft)' : 'var(--card-bg)'};
    ">
      <div style="font-size:var(--text-sm); font-weight:${esHoy ? '700' : '400'}; color:${esHoy ? 'var(--blue)' : 'var(--text)'};">
        ${dia}
      </div>
      ${turnos.length > 0 ? `
        <div style="font-size:var(--text-xs); color:${necesitaAtencion ? 'var(--red)' : 'var(--muted)'}; margin-top:2px;">
          ${turnos.length} turno${turnos.length === 1 ? '' : 's'}${necesitaAtencion ? ' ⚠' : ''}
        </div>
      ` : ''}
    </div>
  `;
}

function _renderListaDia() {
  const turnos = (calendarioTurnosPorFecha[calendarioDiaSeleccionado] || [])
    .slice()
    .sort((a, b) => a.hora_turno.localeCompare(b.hora_turno));

  const fechaLegible = calendarioDiaSeleccionado
    ? new Date(calendarioDiaSeleccionado + 'T00:00:00').toLocaleDateString('es-AR', { weekday: 'long', day: 'numeric', month: 'long' })
    : '';
  document.getElementById('cal-dia-titulo').textContent = `Turnos del ${fechaLegible}`;

  const el = document.getElementById('cal-dia-lista');
  if (turnos.length === 0) {
    el.innerHTML = '<div class="empty">Ningún turno este día.</div>';
    return;
  }
  el.innerHTML = turnos.map((t) => {
    const tag = ESTADO_TAG_CAL[t.estado] || { clase: '', label: t.estado };
    return `
      <div class="row-between" style="padding:10px 0; border-bottom:1px solid var(--border);">
        <div>
          <div style="font-weight:600;">${t.hora_turno} — ${escHtml(t.paciente_nombre)}</div>
          <div style="font-size:12px; color:var(--muted);">${escHtml(t.practica)}${t.cobertura ? ' · ' + escHtml(t.cobertura) : ''}${t.telefono ? ' · ' + escHtml(t.telefono) : ''}</div>
        </div>
        <span class="tag ${tag.clase}">${tag.label}</span>
      </div>
    `;
  }).join('');
}
