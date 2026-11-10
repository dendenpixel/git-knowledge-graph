#!/usr/bin/env python3
from pathlib import Path
from rdflib import Graph
from owlrl import DeductiveClosure, OWLRL_Semantics

base = Path(__file__).resolve().parents[1]
g = Graph().parse(base/"ontology"/"project.ttl", format="turtle")
DeductiveClosure(OWLRL_Semantics).expand(g)
out = base/"ontology"/"project_inferred.ttl"
g.serialize(out, format="turtle")
print("Wrote:", "Triples:", len(g))
