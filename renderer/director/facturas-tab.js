// Pestaña "Facturas" (vista Director) — v0.2.6. Bandeja de pedidos de
// factura por WhatsApp: acá se aprueba (emite el CAE real en ARCA, Director-
// only sin excepción) o rechaza cada pedido. Ver sidecar/facturas.py.
// Mismo estilo que mensajes-whatsapp-tab.js.

let facturasApiBase = null;

const ESTADO_FACTURA_TAG = {
  recibido: { clase: 'proceso', label: 'Procesando...' },
  borrador_generado: { clase: 'proceso', label: 'Borrador listo' },
  error_extraccion: { clase: 'nuevo', label: 'Sin borrador (falló la IA)' },
  emitida: { clase: 'resuelto', label: 'Emitida' },
  rechazada: { clase: '', label: 'Rechazada' },
  error_emision: { clase: 'nuevo', label: 'Error al emitir' },
};

function initFacturasTab(apiBase) {
  facturasApiBase = apiBase;
}

async function facturasFetch(path, opts) {
  const res = await fetch(`${facturasApiBase}${path}`, opts);
  return res.json();
}

async function loadFacturasTab() {
  const el = document.getElementById('tab-facturas');
  el.innerHTML = `
    <div class="card">
      <h3>Facturas</h3>
      <p class="help-text">
        Cada pedido de factura por WhatsApp ("facturale...") pasa primero por acá — la IA arma
        un borrador (monto/concepto/cliente), lo revisás (editable) y recién ahí se pide el CAE
        REAL a ARCA. Emitir es irreversible: una vez aprobada, la factura ya existe ante ARCA.
      </p>
    </div>
    <div id="facturas-lista"></div>
  `;
  await _renderListaFacturas();
}

async function _renderListaFacturas() {
  const el = document.getElementById('facturas-lista');
  el.innerHTML = '<div class="empty">Cargando...</div>';
  const data = await facturasFetch('/api/v1/facturas');
  if (!data.ok) {
    el.innerHTML = `<div class="error-box">${escHtml(data.error)}</div>`;
    return;
  }
  const facturas = data.facturas;
  if (facturas.length === 0) {
    el.innerHTML = '<div class="empty">Ningún pedido de factura todavía.</div>';
    return;
  }

  // Candidatas a "comprobante asociado" para una nota de crédito: cualquier
  // factura (no nota de crédito) ya emitida -- se arma una sola vez acá,
  // con lo que ya trajo el fetch, en vez de otro viaje al servidor.
  const facturasEmitidas = facturas.filter((x) => x.estado === 'emitida' && !x.es_nota_credito);

  el.innerHTML = facturas.map((f) => _cardFactura(f, facturasEmitidas)).join('');

  facturas.forEach((f) => {
    if (f.estado !== 'borrador_generado' && f.estado !== 'error_extraccion') return;
    const card = document.getElementById(`factura-${f.id}`);
    const btnAprobar = card.querySelector('.btn-aprobar');
    const btnRechazar = card.querySelector('.btn-rechazar');
    const inputMonto = card.querySelector('.input-monto');
    const inputConcepto = card.querySelector('.input-concepto');
    const selectAsociada = card.querySelector('.select-asociada');
    const nombreCbte = f.es_nota_credito ? 'nota de crédito' : 'factura';

    btnAprobar.addEventListener('click', async () => {
      const monto_final = parseFloat(inputMonto.value);
      const concepto_final = inputConcepto.value.trim();
      if (!monto_final || monto_final <= 0) {
        showToast('El monto tiene que ser un número mayor a cero.', 'error');
        return;
      }
      const factura_asociada_id = selectAsociada ? selectAsociada.value : null;
      if (f.es_nota_credito && !factura_asociada_id) {
        showToast('Elegí a qué factura corresponde esta nota de crédito.', 'error');
        return;
      }
      const ok = await showConfirm(
        `Emitir ${nombreCbte} real en ARCA`,
        `Se va a pedir un CAE real por <strong>$${monto_final.toLocaleString('es-AR')}</strong> ` +
        `(${escHtml(concepto_final)}) a ${escHtml(f.cliente_nombre || 'Consumidor Final')}.<br><br>` +
        `Esto es <strong>irreversible</strong> — el comprobante queda registrado ante ARCA apenas se confirma.`,
        { confirmLabel: `Emitir ${nombreCbte}`, danger: true },
      );
      if (!ok) return;
      btnAprobar.disabled = true;
      btnAprobar.textContent = 'Emitiendo...';
      const result = await facturasFetch(`/api/v1/facturas/${f.id}/aprobar`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ monto_final, concepto_final, factura_asociada_id }),
      });
      if (!result.ok) {
        showToast(result.error, 'error');
        btnAprobar.disabled = false;
        btnAprobar.textContent = `Emitir ${nombreCbte}`;
        return;
      }
      showToast(`${f.es_nota_credito ? 'Nota de crédito' : 'Factura'} emitida — CAE ${result.cae}.`, 'success');
      await _renderListaFacturas();
    });

    btnRechazar.addEventListener('click', async () => {
      const ok = await showConfirm('Rechazar este pedido', 'No se emite nada.', { confirmLabel: 'Rechazar', danger: true });
      if (!ok) return;
      btnRechazar.disabled = true;
      const result = await facturasFetch(`/api/v1/facturas/${f.id}/rechazar`, { method: 'POST' });
      if (!result.ok) { showToast(result.error, 'error'); btnRechazar.disabled = false; return; }
      showToast('Pedido descartado, no se emitió nada.', 'default');
      await _renderListaFacturas();
    });
  });
}

function _cardFactura(f, facturasEmitidas) {
  const tag = ESTADO_FACTURA_TAG[f.estado] || { clase: '', label: f.estado };
  const accionable = f.estado === 'borrador_generado' || f.estado === 'error_extraccion';

  return `
    <div class="card" id="factura-${f.id}" style="margin-bottom:var(--space-3);">
      <div class="row-between">
        <div>
          <div style="font-weight:600;">${escHtml(f.telefono)}</div>
          <div style="font-size:12px; color:var(--muted);">${new Date(f.recibido_at + 'Z').toLocaleString('es-AR')}</div>
        </div>
        <div class="row-wrap" style="gap:var(--space-2);">
          ${f.es_nota_credito ? '<span class="tag proceso">Nota de crédito</span>' : ''}
          <span class="tag ${tag.clase}">${tag.label}</span>
        </div>
      </div>

      <p style="margin-top:var(--space-3); padding:10px; background:var(--bg-subtle); border-radius:6px;">
        "${escHtml(f.texto_original)}"
      </p>

      ${f.estado === 'error_extraccion' ? `
        <div class="error-box" style="margin-top:var(--space-2);">
          La IA no pudo extraer los datos (${escHtml(f.error_detalle || 'error desconocido')}) — completá
          monto y concepto a mano abajo.
        </div>
      ` : ''}

      ${f.estado === 'error_emision' ? `
        <div class="error-box" style="margin-top:var(--space-2);">
          ARCA rechazó la emisión: ${escHtml(f.error_detalle || 'error desconocido')}. Podés corregir y
          volver a intentar.
        </div>
      ` : ''}

      ${accionable ? `
        <div class="row-wrap" style="margin-top:var(--space-3); gap:var(--space-2);">
          <div style="flex:1; min-width:120px;">
            <label>Monto</label>
            <input type="number" class="input-monto" value="${f.monto || ''}" min="1" step="0.01" />
          </div>
          <div style="flex:2; min-width:200px;">
            <label>Concepto</label>
            <input type="text" class="input-concepto" value="${escHtml(f.concepto || 'Servicios profesionales')}" />
          </div>
        </div>
        <p class="help-text" style="margin-top:var(--space-2);">
          Cliente: ${escHtml(f.cliente_nombre || 'Consumidor Final')}${f.cliente_doc_nro ? ` (Doc. ${f.cliente_doc_nro})` : ''}
        </p>
        ${f.es_nota_credito ? `
          <label style="margin-top:var(--space-2);">Factura que corrige</label>
          ${facturasEmitidas.length === 0 ? `
            <p class="error-box">Todavía no hay ninguna factura emitida para referenciar -- no se puede emitir esta nota de crédito.</p>
          ` : `
            <select class="select-asociada">
              <option value="">-- elegir factura --</option>
              ${facturasEmitidas.map((e) => `
                <option value="${e.id}" ${e.cliente_nombre && f.cliente_nombre && e.cliente_nombre.toLowerCase() === f.cliente_nombre.toLowerCase() ? 'selected' : ''}>
                  C ${String(e.pto_venta).padStart(4, '0')}-${String(e.numero_cbte).padStart(8, '0')} —
                  $${(e.monto || 0).toLocaleString('es-AR')} — ${escHtml(e.cliente_nombre || 'Consumidor Final')}
                </option>
              `).join('')}
            </select>
          `}
        ` : ''}
        <div class="row-wrap" style="margin-top:var(--space-2);">
          <button class="primary btn-aprobar">Emitir ${f.es_nota_credito ? 'nota de crédito' : 'factura'}</button>
          <button class="secondary btn-rechazar">Rechazar</button>
        </div>
      ` : f.estado === 'emitida' ? `
        <p style="margin-top:var(--space-2); font-size:13px; color:var(--muted);">
          ${f.es_nota_credito ? 'Nota de Crédito C' : 'Factura C'} N°${String(f.pto_venta).padStart(4, '0')}-${String(f.numero_cbte).padStart(8, '0')} —
          $${(f.monto || 0).toLocaleString('es-AR')} — CAE ${escHtml(f.cae)} (vto. ${f.cae_vencimiento})
          — <a href="${facturasApiBase}/facturas/${f.id}/pdf" target="_blank">Ver PDF</a>
        </p>
      ` : ''}
    </div>
  `;
}
