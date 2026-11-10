#!/usr/bin/env python3
from pathlib import Path
import sys
from rdflib import Graph

def main():
    base = Path(__file__).resolve().parents[1]
    ont_path = base / "ontology" / "gitOntology.ttl"
    data_path = base / "ontology" / "project.ttl"
    out_path  = base / "ontology" / "combined.ttl"

    if len(sys.argv) > 1:
        out_path = Path(sys.argv[1]).resolve()

    if not ont_path.exists():
        sys.exit(f"[ERR] Ontology not found: {ont_path}")
    if not data_path.exists():
        sys.exit(f"[ERR] Data not found: {data_path}")

    g = Graph()

    print(f"[INFO] Loading ontology: {ont_path}")
    g.parse(ont_path, format="turtle")
    t1 = len(g)

    print(f"[INFO] Loading data: {data_path}")
    g.parse(data_path, format="turtle")
    t2 = len(g)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    g.serialize(out_path, format="turtle") 
    print(f"[OK] Wrote: {out_path}")
    print(f"[STATS] Triples after ontology: {t1}")
    print(f"[STATS] Triples after merge:    {t2}")

if __name__ == "__main__":
    main()