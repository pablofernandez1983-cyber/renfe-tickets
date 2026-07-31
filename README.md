# Monitor Renfe Ourense -> Sarria

Chequea si Renfe puso a la venta el billete **Ourense -> Sarria** del **sábado
12/09/2026** con salida cerca de las **12:32** (la combinación que conecta
con el AVE Madrid->Ourense de las 12:18). Si aparece un tren que sale entre
las 11:30 y las 13:30, carga una notificación en **Fosfovita** (Supabase,
tabla `recordatorios_app`) y manda un mail de aviso a
pablofernandez1983@gmail.com.

## Cómo funciona

`monitor_renfe_ourense_sarria.py` usa Playwright (Chromium headless) porque
el buscador de Renfe (renfe.com -> venta.renfe.com) es una app clásica
multi-página con estado de sesión en un parámetro opaco de la URL (`?c=...`),
no una API JSON reutilizable directamente:

1. Llena origen "Ourense" y destino "Sarria" en el buscador de la home y
   clickea "Buscar billete" (por defecto busca para hoy).
2. En la página de resultados hay una tira de fechas con un botón "Ir a día
   siguiente" — se clickea tantas veces como días faltan hasta el 12/9 (AJAX,
   no recarga la página completa).
3. Lee el texto de la sección de resultados y extrae con regex los pares
   (hora salida, hora llegada) de cada tren listado.
4. Si algún tren sale entre 11:30 y 13:30, se considera "apareció" y se
   notifica. Si no, no hace nada (se reintenta en el próximo chequeo).

**Estado**: guardado en Supabase (`automation_state`, key
`renfe_ourense_sarria_2026-09-12`) — una vez notificado, no se vuelve a
chequear el sitio (evita golpear Renfe innecesariamente y evita
duplicar la alarma).

## Cuándo corre

Corre por dos vías en paralelo, compartiendo estado en Supabase
(`automation_state`) para no duplicar la notificación:

- **Local**: Tarea Programada `RenfeOurenseSarriaCheck` (Task Scheduler de
  Windows), cada 6 horas durante 45 días desde el 31/7/2026 (se
  autodesactiva sola). Pausar: `Disable-ScheduledTask -TaskName
  "RenfeOurenseSarriaCheck"`. Log local: `check.log`.
- **Cloud**: GitHub Actions (`.github/workflows/check.yml`), cron cada 6
  horas — no depende de que la PC esté prendida. Probado en vivo: Renfe no
  bloquea las IPs de GitHub Actions. Pausar: deshabilitar el workflow en
  GitHub o borrar/desactivar el cron.

## Credenciales

`run_check.bat` (gitignored) setea `SUPABASE_URL`, `SUPABASE_ANON_KEY`,
`GMAIL_USER`, `GMAIL_APP_PASSWORD` — mismo proyecto de Supabase que usa
Fosfovita/river-tickets. Ver `run_check.bat.example`.

## Por qué no quedó como rutina cloud

No se probó como rutina cloud (RemoteTrigger) porque el flujo necesita un
browser real (Playwright) con ~40+ clicks secuenciales para llegar a la
fecha, y sitios de venta de pasajes suelen tener detección de bots más
estricta con IPs de datacenter — más seguro y más simple correrlo local con
Task Scheduler, mismo patrón que
[el monitor de entradas de River](../river-tickets/README.md).
