#!/usr/bin/env python3
"""CLI del motor: python -m motor ping | complete "prompt" | eval | seal | compare | learn."""
import os
import sys
import time


def cmd_ping():
    """3 llamadas reales: 2 unicas + 1 repetida para ver la cache."""
    from motor.llm import complete

    n = str(time.time())  # nonce de sesion: garantiza misses en la 1 y 2
    plan = [
        ("1 prompt OK", "Respondé exactamente: OK", n),
        ("2 prompt PING", "Respondé exactamente: PING", n),
        ("3 repite el 1 (cache)", "Respondé exactamente: OK", n),
    ]
    for label, prompt, nonce in plan:
        r = complete(prompt, rol="rapido", nonce=nonce)
        print(
            f"{label}: text={r['text']!r} latencia={r['latency_s']}s "
            f"tokens_in={r['tokens_in']} tokens_out={r['tokens_out']} "
            f"cached={r['cached']} model={r['model']}"
        )
    return 0


def cmd_complete(args):
    from motor.llm import complete

    if not args:
        print('Uso: python -m motor complete "prompt" [--rol rapido|fuerte]')
        return 1
    rol = "rapido"
    prompt_parts = []
    i = 0
    while i < len(args):
        if args[i] == "--rol" and i + 1 < len(args):
            rol = args[i + 1]
            i += 2
        else:
            prompt_parts.append(args[i])
            i += 1
    prompt = " ".join(prompt_parts)
    if not prompt:
        print('Uso: python -m motor complete "prompt" [--rol rapido|fuerte]')
        return 1
    r = complete(prompt, rol=rol)
    print(
        f"text={r['text']!r} latencia={r['latency_s']}s "
        f"tokens_in={r['tokens_in']} tokens_out={r['tokens_out']} "
        f"cached={r['cached']} model={r['model']}"
    )
    return 0


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd, rest = argv[1], argv[2:]
    if cmd == "ping":
        return cmd_ping()
    if cmd == "complete":
        return cmd_complete(rest)
    if cmd == "eval":
        from motor.evaluate import cmd_eval

        return cmd_eval(rest)
    if cmd == "seal":
        from motor.evaluate import cmd_seal

        return cmd_seal(rest)
    if cmd == "compare":
        from motor.compare import cmd_compare

        return cmd_compare(rest)
    if cmd == "skills":
        from motor.skills import cmd_skills

        return cmd_skills(rest)
    if cmd == "learn":
        from motor.learn import cmd_learn

        return cmd_learn(rest)
    print(f"Subcomando desconocido: {cmd}\n{__doc__}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
