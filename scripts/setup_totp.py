#!/usr/bin/env python3
"""SEC-1 Tarea 2 — provisioning one-shot del 2FA TOTP del panel.

Genera un secreto TOTP + su URI otpauth:// y los imprime UNA vez. El operador:
  1. escanea el QR (o pega la URI) en su app de autenticación (Google/Aegis/…),
  2. copia el secreto a `UI_TOTP_SECRET` del .env del server y reinicia.
Con `UI_TOTP_SECRET` vacío el 2FA queda apagado (comportamiento actual).

Uso:  python -m scripts.setup_totp [nombre_de_cuenta]
"""
import sys

from app.core.totp import provisioning_uri, random_secret


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "admin"
    secret = random_secret()
    print("── NTEXECG · 2FA TOTP (guárdalo AHORA; no se vuelve a mostrar) ──")
    print(f"UI_TOTP_SECRET={secret}")
    print()
    print("otpauth URI (pégalo/escanéalo en tu app de autenticación):")
    print(f"  {provisioning_uri(secret, name)}")
    print()
    print("Pasos: 1) mete el secreto a tu app  2) pon UI_TOTP_SECRET en el .env "
          "del server  3) reinicia el servicio.")
    print("Alternativa SIN código propio: Cloudflare Access con OTP delante del "
          "hostname (2FA a nivel edge) — convive con este lote.")


if __name__ == "__main__":
    main()
