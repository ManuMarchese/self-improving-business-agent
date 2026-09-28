#!/usr/bin/env python3
"""Arma train.jsonl/dev.jsonl de py_funcs desde _fuente/sanitized-mbpp.json.

Solo stdlib. Determinista: random.Random(42) (decision del supervisor 2026-09-23).

  train = 80 de los task_id 601-974 (pool ordenado, un sample).
  dev   = los 43 de 511-600 + 17 de 601-974 que NO estan en train
          (mismo rng, sample sobre los 40 restantes) -> 60, disjunto de train.
  11-510 no se copian (reserva del examen externo).

Uso: python tasks/py_funcs/build_splits.py
"""
import json
import random
from pathlib import Path

AQUI = Path(__file__).resolve().parent
FUENTE = AQUI / "_fuente" / "sanitized-mbpp.json"
SEED = 42
N_TRAIN = 80
N_DEV_EXTRA = 17


def fila(row):
    return {
        "id": f"mbpp-{row['task_id']}",
        "input": {
            "enunciado": row["prompt"],
            "firma_ejemplo": row["test_list"][0],
        },
        "expected": {
            "tests": row["test_list"],
            "test_imports": row["test_imports"],
        },
    }


def escribir(path, rows):
    lineas = [json.dumps(fila(r), ensure_ascii=False) for r in rows]
    path.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return len(rows)


def main():
    if not FUENTE.exists():
        print(f"falta fuente: {FUENTE}")
        return 1
    data = json.loads(FUENTE.read_text(encoding="utf-8"))
    by_id = {row["task_id"]: row for row in data}

    pool_train = sorted(tid for tid in by_id if 601 <= tid <= 974)
    dev_base = sorted(tid for tid in by_id if 511 <= tid <= 600)
    if len(pool_train) < N_TRAIN or len(dev_base) < 43:
        print("fuente no alcanza para los rangos")
        return 1

    rng = random.Random(SEED)
    train_ids = sorted(rng.sample(pool_train, N_TRAIN))
    restantes = sorted(set(pool_train) - set(train_ids))
    dev_extra = sorted(rng.sample(restantes, N_DEV_EXTRA))
    dev_ids = sorted(dev_base + dev_extra)

    assert len(train_ids) == N_TRAIN
    assert len(dev_ids) == 60
    assert not set(train_ids) & set(dev_ids)
    assert not any(11 <= tid <= 510 for tid in train_ids + dev_ids)

    n_t = escribir(AQUI / "train.jsonl", [by_id[i] for i in train_ids])
    n_d = escribir(AQUI / "dev.jsonl", [by_id[i] for i in dev_ids])
    print(f"train.jsonl: {n_t} (80 de 601-974, seed={SEED})")
    print(f"dev.jsonl:   {n_d} (43 de 511-600 + 17 extra, disjunto)")
    print(f"dev extra de 601-974: {dev_extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
