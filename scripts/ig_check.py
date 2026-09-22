#!/usr/bin/env python3
"""Lee bio + ultimos posts de un perfil publico de Instagram usando cookies
de una cuenta secundaria (solo lectura).

Uso:
  python scripts/ig_check.py --cookies <ruta-fuera-del-repo> <usuario> [<usuario2> ...]
  python scripts/ig_check.py --cookies <ruta> --posts 6 holacarmenrevuelta

  IG_COOKIES tambien sirve como ruta por defecto.

  Via Meta API oficial (solo Business/Creator, sin cookies):
  python scripts/ig_check.py --api-token-file <ruta-token-fuera-del-repo> <usuario>
  META_IG_TOKEN_FILE tambien sirve como ruta por defecto.

Seguridad:
  - La ruta de cookies NUNCA va dentro del repo.
  - Este script jamas imprime valores de cookies.
  - Pausa de 3s entre perfiles para no forzar la cuenta.
  - Solo stdlib (urllib): cero dependencias.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.cookiejar import MozillaCookieJar

APP_ID = "936619743392459"  # web client publico de instagram.com
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def load_session(cookie_path):
    jar = MozillaCookieJar(cookie_path)
    jar.load(ignore_discard=True, ignore_expires=True)
    csrf = next((c.value for c in jar if c.name == "csrftoken"), "")
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    headers = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "X-IG-App-ID": APP_ID,
        "X-IG-WWW-Claim": "0",
        "X-CSRFToken": csrf,
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Referer": "https://www.instagram.com/",
    }
    return opener, headers


def fetch_profile(session, username):
    opener, headers = session
    url = "https://www.instagram.com/api/v1/users/web_profile_info/?" + urllib.parse.urlencode(
        {"username": username}
    )
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=20) as r:
            status = r.status
            body = r.read()
    except urllib.error.HTTPError as ex:
        if ex.code in (401, 403):
            return {"_error": f"HTTP {ex.code}: cookie invalida o bloqueo"}
        return {"_error": f"respuesta no-JSON (HTTP {ex.code})"}
    except Exception as ex:
        return {"_error": f"error de red: {type(ex).__name__}"}
    try:
        data = json.loads(body)
    except ValueError:
        return {"_error": f"respuesta no-JSON (HTTP {status})"}
    user = (data.get("data") or {}).get("user")
    if not user:
        return {"_error": "usuario no devuelto (privado/inexistente/bloqueo)"}
    return user


def summarize(user, n_posts):
    edges = (((user.get("edge_owner_to_timeline_media") or {}).get("edges")) or [])[:n_posts]
    posts = []
    for e in edges:
        node = e.get("node") or {}
        cap = (((node.get("edge_media_to_caption") or {}).get("edges")) or [{}])[0].get("node", {}).get("text", "")
        ts = node.get("taken_at_timestamp")
        posts.append(
            {
                "fecha": datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat() if ts else "?",
                "tipo": node.get("__typename") or node.get("product_type") or "?",
                "likes": (node.get("edge_liked_by") or {}).get("count"),
                "comentarios": (node.get("edge_media_to_comment") or {}).get("count"),
                "caption": (cap or "")[:140].replace("\n", " "),
            }
        )
    return {
        "usuario": user.get("username"),
        "nombre": user.get("full_name"),
        "bio": user.get("biography"),
        "link": user.get("external_url"),
        "privada": user.get("is_private"),
        "seguidores": user.get("edge_followed_by", {}).get("count"),
        "seguidos": user.get("edge_follow", {}).get("count"),
        "posts_totales": user.get("edge_owner_to_timeline_media", {}).get("count"),
        "ultimos_posts": posts,
    }


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("usuarios", nargs="+")
    ap.add_argument("--cookies", default=os.environ.get("IG_COOKIES", ""))
    ap.add_argument("--api-token-file", default=os.environ.get("META_IG_TOKEN_FILE", ""))
    ap.add_argument("--posts", type=int, default=12)
    a = ap.parse_args(argv)
    if a.api_token_file:
        if not os.path.exists(a.api_token_file):
            print("Falta --api-token-file <ruta-fuera-del-repo> (o env META_IG_TOKEN_FILE).", file=sys.stderr)
            return 1
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import ig_api

        out = ig_api.read_profiles(a.api_token_file, a.usuarios, a.posts)
        if out is None:
            return 1
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0 if all("error" not in o for o in out) else 2
    if not a.cookies or not os.path.exists(a.cookies):
        print("Falta --cookies <ruta-fuera-del-repo> (o env IG_COOKIES).", file=sys.stderr)
        return 1
    try:
        session = load_session(a.cookies)
    except Exception as ex:
        print(f"No se pudo cargar cookies: {type(ex).__name__}", file=sys.stderr)
        return 1
    out = []
    for i, u in enumerate(a.usuarios):
        if i:
            time.sleep(3)
        user = fetch_profile(session, u)
        if "_error" in user:
            out.append({"usuario": u, "error": user["_error"]})
        else:
            out.append(summarize(user, a.posts))
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if all("error" not in o for o in out) else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
