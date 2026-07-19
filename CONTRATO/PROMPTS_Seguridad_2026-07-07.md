# Seguridad de NTEXECG — Revisión y prompt de endurecimiento · 2026-07-07

> Revisión del arquitecto saliente (Fable) para que **Opus 4.8** implemente.
> Contexto: panel single-admin expuesto a internet (ntexecg.lipatolicucho.com,
> detrás de Cloudflare), que ARMA despacho de órdenes (paper). La superficie
> que protege la contraseña es, en la práctica, el control del gateway.

## A. Estado actual (verificado en código)

**Sano (conservar):**
- Password con **bcrypt** (hash en .env, nunca en claro) y comparación que
  no lanza; sesión **JWT HS256 firmado** con expiración 8h.
- Cookie de sesión `httponly` + `samesite=lax` + `secure` en producción —
  XSS no puede leerla y el CSRF clásico queda mitigado por Lax.
- Webhook de LuxAlgo con **token por estrategia HASHEADO + salt** (NX-22),
  dedupe NX-10, y el token en claro se muestra UNA sola vez.
- AuditLog de cambios de config; HTTPS vía Cloudflare.

**Huecos (en orden de riesgo):**
1. **Sin rate-limit ni lockout en /ui/login** — fuerza bruta ilimitada
   contra el único usuario admin. Ni siquiera se auditan los intentos
   fallidos.
2. **Sin segundo factor** — la contraseña es el único secreto que separa
   internet del botón "armar despacho".
3. **SESSION_SECRET sin validación de fuerza** — HS256 con secreto corto
   es forjable; los tests ya avisan (HMAC < 32 bytes) y nada impide que
   producción arranque igual de débil.
4. **Sin CSP y CDNs sin SRI** (Tailwind y Alpine sin integrity; htmx sí
   tiene tras el fix del 07-06). Un compromiso de cdn.jsdelivr/tailwind
   inyecta JS con la sesión del admin. Tampoco hay HSTS/X-Frame-Options/
   nosniff propios.
5. **El token del webhook viaja en el query string** — el flash del alta
   ("cópialo YA: <token>") va como parámetro de URL en el redirect →
   queda en logs de Cloudflare/proxy e historial del navegador. Es un
   secreto de larga vida filtrándose a sitios de baja protección.
6. **Sesiones no revocables** — JWT puro sin estado: logout solo borra la
   cookie; un token robado vale 8h sin remedio, y no hay procedimiento de
   rotación del secreto.

## B. 📋 PROMPT PARA OPUS 4.8 — LOTE SEC-1: endurecimiento del panel

Eres el implementador de NTEXECG (FastAPI; solo paper/demo). Archivos:
`app/core/auth.py`, `app/api/auth_routes.py`, `app/core/config.py`,
`app/main.py` (middleware), `app/templates/base.html`, tests
(`tests/test_security_lote8.py` o nuevo `tests/test_security_lote9.py`).
NO commit/push. Verifica con `.venv\Scripts\python.exe -m pytest -q`
(cuelgue flaky → `-o faulthandler_timeout=300 --timeout=600`).
⚠ Este lote toca el LOGIN: cualquier error deja al operador fuera de su
panel — cada cambio con test de camino feliz Y de degradación, y el
resumen final debe incluir los pasos de migración del .env.

Tareas (en este orden):
1. **Rate-limit + lockout del login** (en memoria, sin dependencias
   nuevas): contador por IP y por usuario; tras 5 fallos → backoff
   exponencial (2s, 4s, 8s…) y tras 10 → bloqueo 15 min con respuesta
   429 genérica ("intenta más tarde" — sin revelar si el usuario existe).
   TODO intento fallido y todo lockout se registra en AuditLog
   (actor=anonymous, action=LOGIN_FAILED/LOGIN_LOCKOUT, ip). El login
   exitoso también se audita (LOGIN_OK, ip). Ojo: detrás de Cloudflare la
   IP real viene en `CF-Connecting-IP` — úsala con fallback a client.host.
2. **2FA TOTP opcional** (pyotp, dependencia mínima): nuevo setting
   `UI_TOTP_SECRET` (vacío = 2FA apagado, comportamiento actual intacto).
   Con secreto configurado, el login pide el código de 6 dígitos en el
   mismo form (campo extra) y `authenticate` exige password Y TOTP
   (ventana ±1 periodo). Provisioning: un script one-shot
   `scripts/setup_totp.py` que genera el secreto + URI otpauth:// y lo
   imprime UNA vez (el operador lo mete a su app de autenticación y al
   .env del server). Los intentos TOTP fallidos cuentan para el lockout.
   Documenta en el resumen la alternativa SIN código: Cloudflare Access
   delante del hostname (2FA a nivel edge) — que el operador elija.
3. **Fail-fast del SESSION_SECRET**: al arrancar (lifespan), si
   `SESSION_SECRET` tiene < 32 bytes → log CRÍTICO y las rutas /ui
   responden 503 "config insegura" (en APP_ENV=test se permite corto para
   no romper la suite — gate por entorno). Incluye en el resumen el
   comando para generar uno bueno (`python -c "import secrets;
   print(secrets.token_urlsafe(48))"`) y el paso de rotación (invalida
   sesiones activas — avisar).
4. **Headers de seguridad + SRI**: middleware que agrega a las respuestas
   HTML: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
   `Referrer-Policy: same-origin`, y HSTS solo si la request llegó por
   https. CSP: empieza HONESTA y funcional — `default-src 'self'` con
   los tres CDN permitidos explícitamente en script-src (Tailwind CDN
   requiere 'unsafe-inline' para estilos y los templates usan JS inline:
   documenta ese compromiso; NO rompas Alpine/HTMX — prueba las páginas
   con los tests de render existentes). Agrega SRI (integrity+crossorigin)
   a Alpine con el hash REAL del archivo servido (verifícalo como se hizo
   con htmx el 07-06 — jamás un hash inventado); Tailwind CDN no soporta
   SRI por ser dinámico: déjalo anotado como candidato a self-host.
5. **Token del webhook fuera del query string**: en el alta/rotación de
   estrategia, el token en claro NO viaja en el redirect. Patrón: guárdalo
   una vez en un store efímero en memoria (id aleatorio, TTL 60s, un solo
   read) y el redirect lleva solo el id; la página destino lo pide por
   fetch (`GET /ui/strategies/token-once/<id>`, misma sesión), lo muestra
   y el store lo destruye. Actualiza el flujo de promoción del Puente
   (redirect a /ui/riesgo?aplicar=1) para que el token siga visible UNA
   vez ahí. Tests: el token no aparece en ninguna Location; el one-time
   read se agota.
6. **Revocación de sesiones**: agrega `sid` (jti aleatorio) al JWT y un
   set en memoria de sesiones revocadas + endpoint POST /ui/logout-all
   (autenticado) que revoca todas las emitidas antes de `now` (guarda un
   watermark `sessions_valid_from` en memoria; verify_session_token
   rechaza iat < watermark). Es best-effort en memoria (se pierde al
   reiniciar — documentarlo: reiniciar el servicio también sirve como
   revocación global, y esa es la garantía dura).
7. Tests: lockout (5/10 fallos, 429, audit rows); TOTP on/off (login sin
   código falla cuando está activo; código válido pasa; inválido cuenta
   para lockout); fail-fast del secreto corto (503 en prod-mode, permitido
   en test); headers presentes en HTML y ausentes en /api JSON si así lo
   decides (documenta); token-once (un read, expira, no está en URLs);
   logout-all invalida tokens previos.

Invariantes: el webhook de LuxAlgo NO se toca (su auth por token hasheado
ya es correcta y TradingView no puede hacer 2FA); cero dependencias
pesadas (pyotp es aceptable); comportamiento actual 100% intacto cuando
las features nuevas no están configuradas (2FA off, secreto fuerte ya
puesto); solo paper/demo. Al final: `git diff --stat`, pasos de migración
del .env (secreto nuevo + TOTP opcional), y "LISTO PARA COMMIT" solo con
suite verde.

## C. Nota para el operador (sin código)

- **Hoy mismo, sin esperar el lote**: verifica que el `SESSION_SECRET` del
  server tenga ≥ 32 caracteres aleatorios y que `UI_PASSWORD` sea un hash
  bcrypt de una contraseña larga y única. Son dos líneas del .env.
- **Cloudflare ya está delante**: activar "Bot Fight" / rate limiting de
  /ui/login y (si quieres 2FA sin código) Cloudflare Access con OTP por
  email o app — protege el panel entero a nivel edge y convive con el
  lote SEC-1.
- El riesgo real que protege todo esto: quien entre al panel puede armar/
  desarmar despacho y editar brackets. Paper hoy — pero los hábitos de
  seguridad se fijan antes de que el dinero sea real.
