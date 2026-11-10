#!/usr/bin/env python3
from pathlib import Path
from rdflib import Graph, Namespace

base = Path(__file__).resolve().parents[1]
g = Graph().parse(base/"ontology"/"project.ttl", format="turtle")

print("Triples:", len(g))
GIT = Namespace("http://www.semanticweb.org/user/ontologies/2025/9/gitOntology_Project/")
q = """
PREFIX git: <http://www.semanticweb.org/user/ontologies/2025/9/gitOntology_Project#>
SELECT (COUNT(DISTINCT ?r) AS ?repos) (COUNT(DISTINCT ?b) AS ?branches) (COUNT(DISTINCT ?c) AS ?commits)
WHERE {
  OPTIONAL { ?r a git:Repository. }
  OPTIONAL { ?b a git:Branch. }
  OPTIONAL { ?c a git:Commit. }
}
"""
for row in g.query(q):
    repos    = int(row.repos.toPython())
    branches = int(row.branches.toPython())
    commits  = int(row.commits.toPython())
    print({"repos": repos, "branches": branches, "commits": commits})