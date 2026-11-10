#!/usr/bin/env python3
from pathlib import Path
from rdflib import Graph
from pyshacl import validate

base = Path(__file__).resolve().parents[1]
data  = base/"ontology"/"project.ttl"
shapes = base/"ontology"/"shapes.ttl"

dg = Graph().parse(data, format="turtle")
sg = Graph().parse(shapes, format="turtle")

conforms, rgraph, rtext = validate(
    data_graph=dg,
    shacl_graph=sg,
    inference="rdfs",
    abort_on_first=False,
    meta_shacl=False,
)
print("Conforms:", conforms)
print(rtext)