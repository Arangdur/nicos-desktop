# Vendor

Librerías de terceros vendorizadas acá mismo (no vía CDN, no vía
`node_modules` en el build) -- NicOS nunca depende de la red para
renderizar su interfaz, ni siquiera al arrancar. Cada archivo se
descarga una vez y se commitea tal cual.

## chart.umd.min.js

Chart.js 4.5.1 (MIT), build UMD minificado -- se referencia con un
`<script>` normal, define `window.Chart` global. Usado por la
gráfica de barras Ingresos/Gastos/Saldo en el Resumen del Director
(`renderer/director/app.js`).

Para actualizar la versión: `npm install chart.js@<version> --no-save`
en cualquier carpeta temporal, copiar `dist/chart.umd.min.js` acá, y
actualizar este número de versión.
