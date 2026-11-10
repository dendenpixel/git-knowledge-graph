#!/usr/bin/env python3
from pathlib import Path
from rdflib import Graph

base = Path(__file__).resolve().parents[1]
g = Graph().parse(base/"ontology"/"combined.ttl", format="turtle")
for name in ["unmergedBranches", "MoreThan3", "commitIsMerge", "commitMessages"]:
    qpath = base/"queries"/f"{name}.rq"
    print(f"\n== {name} ==")
    res = g.query(qpath.read_text(encoding="utf-8"))
    for row in res:
        print(tuple(str(v) for v in row))
