#!/usr/bin/env python3
"""Lee perfiles publicos (business/creator) via Meta Graph API oficial (Business Discovery).

Solo stdlib (urllib). Solo lectura. Jamas imprime el token.

Uso:
  python scripts/ig_api.py --token-file <ruta-fuera-del-repo> <usuario> [<usuario2> ...]
  python scripts/ig_api.py --token-file <ruta> --posts 6 <usuario>

  META_IG_TOKEN_FILE tambien sirve como ruta por defecto. El archivo es el JSON
  {access_token, expires_at, ig_user_id, ...} (ver <RUTA-FUERA-DEL-REPO>/).

Seguridad:
  - El archivo del token NUNCA va dentro del repo.
  - Este script jamas imprime el token (errores enmascarados).
  - Cuentas personales NO las ve la API (solo Business/Creator): eso no es un
    error del script, es limite de Meta -> {"error": ...}.

Codigos de salida: 0 todo OK, 1 problema de token/archivo, 2 algun perfil con error.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from retry_policy import ErrorCritico

GRAPH = "https://graph.facebook.com/v26.0"
APP_ID = "936619743392459"  # noqa: solo referencia documental, no se usa como secreto


def load_token_file(path):
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    tok = doc.get("access_token", "")
    uid = doc.get("ig_user_id", "")
    if not tok or not uid:
        raise ValueError("archivo sin access_token o ig_user_id")
    exp = doc.get("expires_at", "")
    return tok, uid, exp


def api_get(token, path, params):
    q = dict(params or {})
    q["access_token"] = token
    url = GRAPH + path + "?" + urllib.parse.urlencode(q)
    try:
        with urllib.request.urlopen(url, timeout=25) as r:
            return json.load(r), None
    except urllib.error.HTTPError as ex:
        try:
            body = json.load(ex)
        except ValueError:
            body = {}
        err = (body.get("error") or {})
        return None, {"http": ex.code, "code": err.get("code"), "message": (err.get("message") or "")[:160]}
    except Exception as ex:
        return None, {"http": 0, "code": None, "message": f"error de red: {type(ex).__name__}"}


def discover_profile(token, ig_user_id, username, n_posts):
    n = max(1, min(int(n_posts), 25))
    fields = (
        f"business_discovery.username({username})"
        "{followers_count,follows_count,media_count,biography,name,username,website,"
        f"media.limit({n}){{caption,like_count,comments_count,view_count,timestamp,media_type,permalink}}}}"
    )
    data, err = api_get(token, f"/{ig_user_id}", {"fields": fields})
    if err:
        msg = err.get("message", "")
        if err.get("code") == 190:
            return {"_error": "token inválido o expirado (re-login)"}
        if err.get("http") in (400, 401, 403) or err.get("code") in (100, 10):
            return {"_error": "objetivo no visible por API (personal/privado/bloqueado); si otros andan, el token está bien"}
        if err.get("http") == 429 or err.get("code") in (4, 17, 32, 613):
            return {"_error": "límite de Meta alcanzado (429): esperar y reintentar"}
        return {"_error": f"API Meta: HTTP {err.get('http')} {msg}"}
    user = (data or {}).get("business_discovery")
    if not user:
        return {"_error": "perfil no visible por API (personal, inexistente o con candado de edad)"}
    return user


def summarize(user, n_posts):
    media = ((user.get("media") or {}).get("data")) or []
    posts = []
    for m in media[:n_posts]:
        cap = (m.get("caption") or "").replace("\n", " ")
        ts = (m.get("timestamp") or "")[:10] or "?"
        posts.append(
            {
                "fecha": ts,
                "tipo": m.get("media_type") or "?",
                "likes": m.get("like_count"),
                "vistas": m.get("view_count"),
                "comentarios": m.get("comments_count"),
                "caption": cap[:140],
            }
        )
    return {
        "usuario": user.get("username"),
        "nombre": user.get("name"),
        "bio": user.get("biography"),
        "link": user.get("website"),
        "privada": False,
        "seguidores": user.get("followers_count"),
        "seguidos": user.get("follows_count"),
        "posts_totales": user.get("media_count"),
        "ultimos_posts": posts,
        "fuente": "meta-api",
    }


def read_profiles(token_path, usernames, n_posts, pause=3):
    try:
        token, uid, exp = load_token_file(token_path)
    except (OSError, ValueError, KeyError) as ex:
        raise ErrorCritico(f"token ilegible ({type(ex).__name__}): {token_path}")
    out = []
    for i, u in enumerate(usernames):
        if i:
            time.sleep(pause)
        user = discover_profile(token, uid, u, n_posts)
        if "_error" in user:
            out.append({"usuario": u, "error": user["_error"], "fuente": "meta-api"})
        else:
            out.append(summarize(user, n_posts))
    return out


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("usuarios", nargs="+")
    ap.add_argument("--token-file", default=os.environ.get("META_IG_TOKEN_FILE", ""))
    ap.add_argument("--posts", type=int, default=12)
    a = ap.parse_args(argv)
    if not a.token_file or not os.path.exists(a.token_file):
        print("Falta --token-file <ruta-fuera-del-repo> (o env META_IG_TOKEN_FILE).", file=sys.stderr)
        return 1
    try:
        out = read_profiles(a.token_file, a.usuarios, a.posts)
    except ErrorCritico as ex:
        print(f"Token critico: {ex}", file=sys.stderr)
        return 1
    if out is None:
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if all("error" not in o for o in out) else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
