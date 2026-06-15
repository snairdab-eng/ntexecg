# NTEXECG — Infraestructura y Entornos v1.0

---

## 1. Visión general — 3 servidores

```text
┌──────────────────────────────────────────────────────────────────┐
│                       RED LOCAL (LAN)                           │
│                                                                  │
│  ┌──────────────────────┐       ┌──────────────────────────┐    │
│  │       NTRADER        │       │         NTEXECG          │    │
│  │  Windows Server 2025 │       │   Ubuntu Server 24.04    │    │
│  │  NinjaTrader 24/7    │──LAN──▶  Gateway 24/7           │    │
│  │  Fuente de datos     │Samba  │  Docker + Nginx + PG     │    │
│  └──────────────────────┘       └──────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
                              │ VPN
              ┌───────────────┴───────────────┐
              │           NTDEV               │
              │    Windows Server 2025        │
              │    VS Code + Claude Code      │
              │    Docker Desktop (dev local) │
              │    Git (push al repo)         │
              └───────────────────────────────┘
```

---

## 2. NTRADER — Windows Server 2025

### Rol

```text
Servidor dedicado a NinjaTrader Desktop.
Fuente de datos de mercado en tiempo real.
Corre 24/7 en la misma LAN que NTEXECG.
Sin VS Code, sin Docker, sin Claude Code.
```

### Software

```text
NinjaTrader Desktop (conectado a Tradovate — feed CME en tiempo real)
NTraderExecutionBridge.cs (compilado y activo en cada chart)
```

### Configurar NinjaTrader en NTRADER

```text
PASO 1 — Compilar el bridge:
  NinjaTrader → Tools → Edit NinjaScript → Strategy
  Abrir NTraderExecutionBridge.cs

  Agregar en NormalizeInstrumentForBot() antes del return final:
    if (upper.StartsWith("MYM")) return "MYM";
    if (upper.StartsWith("MJY")) return "MJY";
    if (upper.StartsWith("M6E")) return "M6E";
    if (upper.StartsWith("6J"))  return "6J";
    if (upper.StartsWith("6E"))  return "6E";
    // Agregar cualquier otro instrumento que se opere

  Compilar (F5)

PASO 2 — Crear estructura de carpetas:
  mkdir C:\NTraderSystem\bridge\out
  mkdir C:\NTraderSystem\bridge\in
  mkdir C:\NTraderSystem\bridge\processed
  mkdir C:\NTraderSystem\bridge\error

PASO 3 — Abrir charts y adjuntar bridge (uno por instrumento):
  Nuevo chart → instrumento (MES) → periodo 5m
  Estrategias → Agregar → NTraderExecutionBridge
  Parámetros:
    BridgeOutputFolder: C:\NTraderSystem\bridge\out
    BridgeInputFolder:  C:\NTraderSystem\bridge\in
  Habilitar estrategia

  Repetir para: MNQ, MJY, MGC, M2K, M6E, 6J, 6E
  (solo los instrumentos que se van a operar)

PASO 4 — Verificar exportación:
  C:\NTraderSystem\bridge\out\ debe contener:
  ├── bars_MES_5m.json     (actualiza cada ~10 segundos)
  ├── bars_MES_15m.json
  ├── bars_MES_1h.json
  ├── bars_MES_4h.json
  └── heartbeat_MES.json   (actualiza cada 15 segundos)
```

### Configurar carpeta compartida Samba en NTRADER

```text
PASO 1 — Compartir la carpeta:
  Clic derecho C:\NTraderSystem\bridge
  → Propiedades → Compartir → Uso compartido avanzado
  → Marcar "Compartir esta carpeta"
  → Nombre del recurso compartido: bridge
  → Permisos: agregar usuario con acceso Lectura

PASO 2 — Habilitar SMB en firewall:
  Windows Defender Firewall → Reglas de entrada
  → Habilitar "Compartir archivos e impresoras (SMB de entrada)"
  → Solo perfil de red Privada

PASO 3 — IP fija recomendada:
  Panel de control → Red → Adaptador → IPv4
  Asignar IP fija (ej: 192.168.1.100)
  Evita problemas de montaje si la IP cambia

PASO 4 — Verificar desde NTEXECG:
  ping 192.168.1.100
  smbclient //192.168.1.100/bridge -U usuario
```

### Configuración para operación 24/7

```text
Windows Update:
  Configuración → Windows Update → Opciones avanzadas
  → Pausar actualizaciones
  → Aplicar manualmente en fin de semana (cuando mercados cerrados)
  → Deshabilitar reinicios automáticos

Energía (nunca suspender):
  Panel de control → Opciones de energía
  → Plan: Alto rendimiento
  → Suspensión: Nunca
  → Apagar pantalla: Nunca

NinjaTrader auto-start:
  Acceso directo de NinjaTrader en la carpeta de inicio de Windows:
  C:\Users\{usuario}\AppData\Roaming\Microsoft\Windows\Start Menu\
  Programs\Startup\
  O usar Task Scheduler para iniciar NT al arrancar Windows
```

---

## 3. NTEXECG — Ubuntu Server 24.04 LTS

### Rol

```text
Gateway de señales corriendo 24/7.
En la misma LAN que NTRADER.
Monta \\NTRADER\bridge via Samba directamente (sin VPN).
Recibe webhooks reales de LuxAlgo.
Envía señales aprobadas a TradersPost.
```

### Software a instalar

```bash
# Actualizar sistema
sudo apt update && sudo apt upgrade -y

# Docker Engine
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# CIFS Utils (para montar Samba desde NTRADER)
sudo apt install -y cifs-utils

# Nginx
sudo apt install -y nginx

# Certbot (SSL)
sudo snap install certbot --classic
sudo ln -s /snap/bin/certbot /usr/bin/certbot

# UFW (firewall)
sudo ufw enable
sudo ufw allow ssh
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
# NO abrir 5432 (PostgreSQL) ni 8000 (app directamente)

# Fail2ban
sudo apt install -y fail2ban

# Git
sudo apt install -y git
```

### Configurar montaje Samba desde NTRADER

```bash
# Crear punto de montaje
sudo mkdir -p /mnt/ntbridge

# Crear archivo de credenciales (fuera del repo, nunca en git)
sudo nano /etc/ntbridge-credentials
# Contenido:
#   username=usuario_windows_ntrader
#   password=password_ntrader
#   domain=WORKGROUP
sudo chmod 600 /etc/ntbridge-credentials

# Montar (prueba inicial)
sudo mount -t cifs \
    //192.168.1.100/bridge \
    /mnt/ntbridge \
    -o credentials=/etc/ntbridge-credentials,ro,iocharset=utf8,vers=3.0

# Verificar
ls /mnt/ntbridge/
stat /mnt/ntbridge/heartbeat_MES.json
# Timestamp debe ser reciente (< 60 segundos)

# Montaje permanente en /etc/fstab
echo "//192.168.1.100/bridge  /mnt/ntbridge  cifs  credentials=/etc/ntbridge-credentials,ro,vers=3.0,_netdev  0  0" | sudo tee -a /etc/fstab

sudo mount -a   # Verificar fstab
```

### Configurar Nginx

```nginx
# /etc/nginx/sites-available/ntexecg

server {
    listen 80;
    server_name tu-dominio.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl;
    server_name tu-dominio.com;

    ssl_certificate /etc/letsencrypt/live/tu-dominio.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tu-dominio.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    client_max_body_size 1m;
    access_log /var/log/nginx/ntexecg_access.log;
    error_log  /var/log/nginx/ntexecg_error.log;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 30s;
    }

    location /health {
        proxy_pass http://localhost:8000/health;
        access_log off;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/ntexecg /etc/nginx/sites-enabled/
sudo certbot --nginx -d tu-dominio.com
sudo systemctl reload nginx
```

### Variables de entorno (.env en NTEXECG)

```env
APP_ENV=production
APP_NAME=NTEXECG
APP_VERSION=1.0.0

DATABASE_URL=postgresql+asyncpg://ntexecg:password_seguro@db:5432/ntexecg
POSTGRES_USER=ntexecg
POSTGRES_PASSWORD=password_seguro
POSTGRES_DB=ntexecg

SECRET_KEY=clave_larga_y_aleatoria_minimo_32_chars
WEBHOOK_TOKEN_SALT=salt_largo_y_aleatorio_minimo_32_chars
LUXALGO_WEBHOOK_SECRET=token_global_para_nuevas_estrategias

TRADERSPOST_ENABLED=false
DRY_RUN=true
DEFAULT_TIMEZONE=America/New_York
LOG_LEVEL=INFO
MAX_RETRY_ATTEMPTS=3
RETRY_BACKOFF_SECONDS=1

# Datos de mercado — bridge de NTRADER (LAN directa)
MARKET_DATA_PROVIDER=ninja_trader_bridge
NTBRIDGE_PATH=/mnt/ntbridge
NTBRIDGE_HEARTBEAT_MAX_AGE=60
MARKET_DATA_FALLBACK_ENABLED=false

# Noticias
NEWS_CACHE_TTL_MINUTES=60
```

### Configurar cron en NTEXECG

```bash
crontab -e

# Backup diario PostgreSQL a las 02:00 AM
0 2 * * * cd /ruta/proyecto && docker compose exec -T db pg_dump -U ntexecg ntexecg | gzip > /backups/ntexecg_$(date +\%Y\%m\%d).sql.gz

# Limpiar backups > 30 días
0 3 * * * find /backups -name "ntexecg_*.sql.gz" -mtime +30 -delete

# Verificar contratos próximos a vencer (Lun-Vie 13:00 UTC = 08:00 ET)
0 13 * * 1-5 cd /ruta/proyecto && docker compose exec -T app python scripts/rollover_alert.py --days 7 >> /var/log/ntexecg_rollover.log 2>&1

# Verificar montaje de NTRADER cada 5 minutos (remontar si se desconectó)
*/5 * * * * mountpoint -q /mnt/ntbridge || (echo "$(date): remontando /mnt/ntbridge" >> /var/log/ntexecg_bridge.log && sudo mount -a)
```

### Comandos frecuentes en NTEXECG

```bash
# Primer deploy
git clone https://github.com/tu-usuario/ntexecg.git
cd ntexecg
nano .env    # Configurar variables de producción
docker compose up -d --build
docker compose exec app alembic upgrade head
docker compose exec app python scripts/seed_dev_data.py

# Deploy de actualización (desde NTDEV via SSH/VPN)
git pull
docker compose up -d --build
docker compose exec app alembic upgrade head

# Verificar bridge de NTRADER
ls -la /mnt/ntbridge/
stat /mnt/ntbridge/heartbeat_MES.json    # Timestamp < 60s

# Verificar dentro del contenedor
docker compose exec app ls /mnt/ntbridge/

# Verificar ATR del bridge
docker compose exec app python -c "
import asyncio
from app.services.market_data_service import NinjaTraderBridgeProvider
p = NinjaTraderBridgeProvider('/mnt/ntbridge')
print('MES activo:', asyncio.run(p.is_active('MES')))
print('ATR 5m:', asyncio.run(p.get_atr('MES', '5m')))
"

# Health check
curl https://tu-dominio.com/health

# Ver logs
docker compose logs -f app

# Remontar bridge si se desconectó
sudo umount /mnt/ntbridge && sudo mount -a

# Backup manual
docker compose exec -T db pg_dump -U ntexecg ntexecg > backup_$(date +%Y%m%d).sql
```

---

## 4. NTDEV — Windows Server 2025 (sitio remoto)

### Rol

```text
Desarrollo exclusivo. Conectado via VPN a NTRADER y NTEXECG.
No tiene NinjaTrader de producción.
No monta \\NTRADER\bridge (inestable via VPN).
Usa YfinanceProvider en desarrollo local.
```

### Software a instalar

```text
1. Git for Windows
   https://git-scm.com/download/win
   Configurar: line endings LF, editor VS Code

2. Docker Desktop for Windows
   https://www.docker.com/products/docker-desktop
   Habilitar: WSL2 backend (obligatorio)
   Recursos: mínimo 4 CPU, 8 GB RAM

3. VS Code (ya instalado ✅)
   Extensiones:
   ├── Python (Microsoft)
   ├── Pylance (Microsoft)
   ├── Docker (Microsoft)
   ├── GitLens (GitKraken)
   ├── REST Client (Huachao Mao)
   ├── Better Jinja (Samuel Colvin)
   ├── SQLTools + PostgreSQL driver
   └── Error Lens

4. Claude Code (instalador nativo — NO usar npm)
   PowerShell como administrador:
   irm https://claude.ai/install.ps1 | iex
   Verificar: claude --version && claude doctor
   Plan requerido: Max 5x ($100/mes)

5. Python 3.12+
   https://www.python.org/downloads/
   ✅ Add Python to PATH durante instalación

6. Cliente VPN
   Para conectarse a la LAN donde están NTRADER y NTEXECG
```

### .vscode/settings.json

```json
{
  "files.eol": "\n",
  "editor.formatOnSave": true,
  "python.defaultInterpreterPath": "python",
  "editor.rulers": [88],
  "files.trimTrailingWhitespace": true,
  "files.insertFinalNewline": true,
  "sqltools.connections": [
    {
      "name": "NTEXECG Dev DB",
      "driver": "PostgreSQL",
      "server": "localhost",
      "port": 5432,
      "database": "ntexecg",
      "username": "ntexecg"
    }
  ]
}
```

### Variables de entorno (.env en NTDEV)

```env
APP_ENV=development
APP_NAME=NTEXECG
APP_VERSION=1.0.0

DATABASE_URL=postgresql+asyncpg://ntexecg:password_dev@db:5432/ntexecg
POSTGRES_USER=ntexecg
POSTGRES_PASSWORD=password_dev
POSTGRES_DB=ntexecg

SECRET_KEY=dev_secret_key
WEBHOOK_TOKEN_SALT=dev_salt
LUXALGO_WEBHOOK_SECRET=dev_global_token

TRADERSPOST_ENABLED=false
DRY_RUN=true
DEFAULT_TIMEZONE=America/New_York
LOG_LEVEL=DEBUG

# NTDEV usa yfinance (no monta NTRADER via VPN)
MARKET_DATA_PROVIDER=yfinance
NTBRIDGE_PATH=/mnt/ntbridge
NTBRIDGE_HEARTBEAT_MAX_AGE=60
MARKET_DATA_FALLBACK_ENABLED=false
```

### Estrategia de uso de Claude Code (Max 5x)

```text
Sonnet 4.6 (default, 90%):
  Implementar servicios, modelos, templates, tests, refactoring

Opus 4.8 (10%, problemas complejos):
  Arquitectura de un módulo nuevo, debugging difícil,
  revisión del FilterPipeline, decisiones con trade-offs

Haiku 4.5 (ahorra límites):
  /model haiku para formateo, renombramiento, boilerplate simple

Comandos:
  /model sonnet   ← volver a default
  /model opus     ← problema difícil
  /status         ← ver modelo y uso actual
```

### Comandos frecuentes en NTDEV

```powershell
# Levantar entorno de desarrollo
docker compose -f docker-compose.dev.yml up -d

# Ver logs
docker compose -f docker-compose.dev.yml logs -f app

# Correr todos los tests
docker compose -f docker-compose.dev.yml exec app pytest -v

# Tests críticos (pipeline, SL, datos)
docker compose -f docker-compose.dev.yml exec app pytest `
  tests/test_filter_pipeline.py `
  tests/test_sl_tp_calculator.py `
  tests/test_market_data_service.py `
  tests/test_symbol_mapper.py -v

# Tests con coverage
docker compose -f docker-compose.dev.yml exec app pytest -v --cov=app --cov-report=term-missing

# Aplicar migraciones
docker compose -f docker-compose.dev.yml exec app alembic upgrade head

# Crear nueva migración
docker compose -f docker-compose.dev.yml exec app alembic revision --autogenerate -m "descripcion"

# Seed de datos
docker compose -f docker-compose.dev.yml exec app python scripts/seed_dev_data.py

# Simular webhook de entrada SHORT
python scripts/simulate_webhook.py `
  --strategy-id mes5m_confirmation_normal `
  --action sell --ticker MES --sentiment short `
  --price 5500.00 --interval 5 --token tu-token

# Simular webhook de salida (flat)
python scripts/simulate_webhook.py `
  --strategy-id mes5m_confirmation_normal `
  --exit --ticker MES --token tu-token

# Verificar contratos próximos a vencer
python scripts/rollover_alert.py --days 7

# Abrir UI
start http://localhost:8000/ui

# Deploy a NTEXECG via VPN
ssh usuario@ip-ntexecg-vpn "cd ntexecg && git pull && docker compose up -d --build && docker compose exec app alembic upgrade head && curl http://localhost:8000/health"
```

---

## 5. Flujo de deploy completo

```text
NTDEV
  │
  ├── Código + tests en VS Code con Claude Code
  ├── pytest pasa ✅
  ├── git commit + git push
  │
  │ (via VPN)
  ▼
SSH a NTEXECG
  ├── git pull
  ├── docker compose up -d --build
  ├── docker compose exec app alembic upgrade head
  └── curl https://tu-dominio.com/health ✅
```

---

## 6. Roadmap de migración de datos

```text
ACTUAL (todas las fases de validación en paper):
  NTRADER → NinjaTrader Bridge → Samba → NTEXECG
  Gratis. Tiempo real del feed Tradovate.
  Riesgo: NTRADER debe estar activo.
  Mitigación: check 1.6 en pipeline bloquea entradas si NT inactivo.

CUANDO SE GENEREN GANANCIAS (Fase 5+):
  Evaluar si el bridge ha causado problemas en producción.
  Si sí: implementar proveedor independiente.

  Opción A — Tradovate Market Data API (gratis, ya incluida):
    pip install tradovate (o implementar con httpx)
    OAuth authentication
    MARKET_DATA_PROVIDER=tradovate en .env → reiniciar contenedor

  Opción B — Databento (~$50-150/mes, profesional):
    pip install databento
    $125 en créditos gratuitos para evaluar
    Cubre CME, CBOT, NYMEX, COMEX
    MARKET_DATA_PROVIDER=databento en .env → reiniciar contenedor

  La migración es TRANSPARENTE:
    Solo cambia MarketDataProvider implementado
    FilterPipeline, SLTPCalculator, QualityScorer no cambian
    NTRADER Bridge queda como herramienta secundaria/opcional
```

---

## 7. Checklist completo de infraestructura

### NTRADER

```text
[ ] Windows Server 2025 instalado y actualizado
[ ] NinjaTrader Desktop instalado y conectado a Tradovate
[ ] NTraderExecutionBridge.cs compilado con todos los instrumentos
    (MES, MNQ, MYM, M2K, MGC, MJY, M6E, 6J, 6E)
[ ] C:\NTraderSystem\bridge\ creada con subcarpetas
[ ] Carpeta bridge compartida como \\NTRADER\bridge (Samba)
[ ] Firewall Windows: SMB habilitado en red privada
[ ] IP fija asignada a NTRADER (192.168.x.x)
[ ] Windows Update: reinicios automáticos deshabilitados
[ ] Energía: suspensión deshabilitada (plan Alto rendimiento)
[ ] NinjaTrader configurado para arrancar automáticamente
[ ] Charts activos con bridge: MES, MNQ, y los que se operen
[ ] C:\NTraderSystem\bridge\out\ tiene archivos actualizándose
[ ] heartbeat_MES.json existe y timestamp es reciente (< 15s)
```

### NTEXECG

```text
[ ] Ubuntu Server 24.04 LTS instalado y actualizado
[ ] Docker Engine instalado y corriendo
[ ] UFW: solo 22, 80, 443 abiertos
[ ] Fail2ban configurado
[ ] SSH solo por clave pública (PasswordAuthentication no)
[ ] cifs-utils instalado
[ ] Ping a NTRADER (192.168.x.x) responde desde NTEXECG
[ ] /etc/ntbridge-credentials creado (chmod 600)
[ ] /mnt/ntbridge montado desde \\NTRADER\bridge
[ ] ls /mnt/ntbridge/ muestra archivos de NinjaTrader
[ ] stat /mnt/ntbridge/heartbeat_MES.json → timestamp < 60s
[ ] /etc/fstab configurado para montaje permanente
[ ] Cron: remontaje automático cada 5 min configurado
[ ] mkdir -p /backups (para backups de DB)
[ ] .env configurado (dry_run=true, traderspost_enabled=false)
[ ] docker compose up -d --build sin errores
[ ] docker compose exec app alembic upgrade head ✅
[ ] docker compose exec app python scripts/seed_dev_data.py ✅
[ ] Nginx con HTTPS funcionando (certbot)
[ ] GET https://tu-dominio.com/health → 200
[ ] UI accesible en https://tu-dominio.com/ui
[ ] Bridge status en dashboard: ● Activo para MES
[ ] Primera señal de LuxAlgo recibida y visible en UI
[ ] Señal APPROVE muestra ATR del bridge y SL calculado
[ ] stopLoss visible en detalle de señal
[ ] Backup de DB configurado en cron
[ ] Rollover alert configurado en cron
```

### NTDEV

```text
[ ] Git instalado y configurado (LF, VS Code como editor)
[ ] Docker Desktop con WSL2 backend activo
[ ] VS Code instalado ✅
[ ] Claude Code instalado (instalador nativo, NO npm)
    claude --version ✅
    claude doctor ✅ (sin errores)
[ ] Python 3.12+ con PATH configurado
[ ] Extensiones VS Code instaladas
[ ] Cliente VPN configurado y conectado
[ ] Ping a NTEXECG via VPN responde
[ ] SSH a NTEXECG via VPN funciona
[ ] docker compose -f docker-compose.dev.yml up -d sin errores
[ ] GET http://localhost:8000/health → 200
[ ] GET http://localhost:8000/ui → 200
[ ] pytest pasa sin errores (con YfinanceProvider)
[ ] seed_dev_data.py ejecutado
[ ] Simular webhook → señal visible en UI con SL calculado
[ ] Deploy a NTEXECG via VPN verificado
```

---

## 8. Reglas de seguridad operativa

```text
1.  NTDEV no recibe webhooks reales de LuxAlgo.
2.  NTDEV no monta \\NTRADER\bridge de forma permanente.
3.  NTRADER no tiene VS Code, Docker ni Claude Code.
4.  NTEXECG no tiene VS Code ni Claude Code.
5.  El .env de producción nunca llega al repositorio (.gitignore).
6.  dry_run=true es el default en producción (NTEXECG).
7.  TRADERSPOST_ENABLED=false es el default.
8.  Los tokens de webhook no se loguean en texto plano.
9.  PostgreSQL (5432) no se expone al exterior en NTEXECG.
10. La app (8000) solo accesible via Nginx en 443.
11. Backups diarios de PostgreSQL verificados.
12. /mnt/ntbridge en NTEXECG es READ-ONLY (ro en mount).
13. Si /mnt/ntbridge no está accesible: BLOCK entradas, alerta dashboard.
14. Si heartbeat_{symbol}.json > 60s: BLOCK entradas para ese símbolo.
    Las salidas se permiten siempre para proteger posiciones abiertas.
15. Verificar que stopLoss llega a TradersPost en cuentas paper
    antes de activar cualquier cuenta real.
16. El paso a live requiere confirmación manual ("CONFIRMAR" en UI).
17. NTRADER: actualizaciones de Windows solo en fin de semana,
    de forma manual, cuando los mercados están cerrados.
18. SSH a NTEXECG y NTRADER: solo con clave pública, sin password.
19. Cualquier cambio de configuración en UI genera AuditLog.
    No hay cambios sin rastro.
20. El montaje Samba de /mnt/ntbridge es solo de lectura.
    NTEXECG nunca escribe en la carpeta del bridge.
```
