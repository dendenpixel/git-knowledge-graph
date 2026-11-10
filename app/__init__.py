from flask import Flask, render_template, request, redirect
from pathlib import Path
import html
from rdflib import Graph, RDF, RDFS, OWL, Namespace
from rdflib.plugins.sparql.processor import prepareQuery
from owlrl import DeductiveClosure, OWLRL_Semantics
from pyshacl import validate

project_root = Path(__file__).resolve().parents[1]
ontology_directory = project_root / "ontology" / "Flask"
queries_directory = project_root / "queries"
SH = Namespace("http://www.w3.org/ns/shacl#")

#cache to store the ttl not reload from every action.
_cache = {
    "sig": None,
    "base": None,
    "inferred": None,
}

def de_cached() -> tuple[int, float]: #The logic to make the caching work.
    if not ontology_directory.exists():
        return (0, 0.0)
    files = sorted(ontology_directory.glob("*.ttl")) #If no new ttl file, reuse current graph.
    total_mtime = sum(p.stat().st_mtime for p in files) if files else 0.0
    return (len(files), total_mtime)

def parse_ttl() -> Graph:
    sig = de_cached()
    if _cache["base"] is not None and _cache["sig"] == sig:
        return _cache["base"]

    g = Graph()
    if ontology_directory.exists():
        for ttl in sorted(ontology_directory.glob("*.ttl")):
            g.parse(ttl, format="turtle")

    _cache["sig"] = sig
    _cache["base"] = g
    _cache["inferred"] = None
    return g

def inference_loader() -> Graph:
    sig = de_cached()
    if _cache["inferred"] is not None and _cache["sig"] == sig:
        return _cache["inferred"]

    base = parse_ttl()
    g = Graph()
    for t in base:
        g.add(t)
    if len(g) > 0:
        DeductiveClosure(OWLRL_Semantics).expand(g)

    _cache["sig"] = sig
    _cache["inferred"] = g
    return g

PREFIXES = """
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl:  <http://www.w3.org/2002/07/owl#>
"""

def rq_sparqlFiles() -> list[str]:
    if queries_directory.exists():
        return sorted(p.name for p in queries_directory.glob("*.rq"))
    return []

def previous_next_triple(g: Graph, limit: int, offset: int):
    rows = []
    for i, (s, p, o) in enumerate(g):
        if i < offset:
            continue
        if len(rows) >= limit:
            break
        rows.append((s, p, o))
    return rows

def _run_shacl():
    data_g = parse_ttl()

    shapes_path = ontology_directory / "shapes.ttl"
    shapes_g = Graph()
    if shapes_path.exists():
        shapes_g.parse(shapes_path, format="turtle")

    conforms, report_graph, report_text = validate(
        data_graph=data_g,
        shacl_graph=shapes_g if len(shapes_g) > 0 else None,
        inference="rdfs",
        abort_on_first=False,
        meta_shacl=False,
        advanced=True,
        debug=False,
    )

    # extract sh:ValidationResult rows for a neat table
    out = []
    for res in report_graph.subjects(RDF.type, SH.ValidationResult):
        row = {
            "severity":       next(report_graph.objects(res, SH.resultSeverity), None),
            "focusNode":      next(report_graph.objects(res, SH.focusNode), None),
            "resultPath":     next(report_graph.objects(res, SH.resultPath), None),
            "message":        next(report_graph.objects(res, SH.resultMessage), None),
            "sourceShape":    next(report_graph.objects(res, SH.sourceShape), None),
            "constraintComp": next(report_graph.objects(res, SH.sourceConstraintComponent), None),
        }
        # make everything printable
        for k, v in list(row.items()):
            row[k] = "" if v is None else str(v)
        out.append(row)

    return bool(conforms), str(report_text), out

# ---------- app factory + routes ----------
def create_app():
    app = Flask(__name__)

    @app.get("/")
    def home():
        base = parse_ttl()
        inf  = inference_loader()
        stats = {
            "ttl_dir": str(ontology_directory),
            "base_triples": len(base),
            "inferred_triples": len(inf),
        }
        return render_template("home.html", title="Git Ontology Viewer", stats=stats)

    @app.get("/classes")
    def classes():
        g = inference_loader()
        # collect classes declared + any class seen as rdf:type target
        klasses = set(g.subjects(RDF.type, OWL.Class)) | set(g.subjects(RDF.type, RDFS.Class))
        for _, _, c in g.triples((None, RDF.type, None)):
            klasses.add(c)

        rows = []
        for c in klasses:
            n = sum(1 for _ in g.subjects(RDF.type, c))
            if n > 0:
                rows.append((n, str(c)))
        rows.sort(key=lambda x: x[0], reverse=True)
        return render_template("classes.html", title="Classes", rows=rows)

    @app.get("/instances")
    def instances():
        cls_iri = request.args.get("class")
        if not cls_iri:
            return redirect("/classes")

        g = inference_loader()
        limit = int(request.args.get("limit", 100))

        items, count = [], 0
        for s in g.subjects(RDF.type, cls_iri):  # rdflib accepts string IRI here
            count += 1
            if len(items) < limit:
                items.append(str(s))

        return render_template(
            "instances.html",
            title="Instances",
            cls=cls_iri,
            items=items,
            count=count,
            limit=limit,
        )

    @app.get("/triples")
    def triples():
        g = inference_loader()
        limit  = max(1, min(1000, int(request.args.get("limit", 100))))
        offset = max(0, int(request.args.get("offset", 0)))
        total  = len(g)

        rows = previous_next_triple(g, limit, offset)
        prev_offset = max(0, offset - limit) if offset > 0 else None
        next_offset = offset + limit if (offset + limit) < total else None

        return render_template(
            "triples.html",
            title="Triples",
            total=total,
            rows=rows,
            limit=limit,
            offset=offset,
            prev_offset=prev_offset,
            next_offset=next_offset,
        )

    @app.route("/sparql", methods=["GET", "POST"])
    def sparql():
        g = inference_loader()

        default_q = "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 25"
        q = default_q
        err = ""
        headers = rows = None
        limit = 200

        # load canned query
        load = request.args.get("load")
        if load:
            p = queries_directory / load
            if p.exists():
                q = p.read_text(encoding="utf-8")

        if request.method == "POST":
            q = request.form.get("q") or default_q
            limit = max(1, min(5000, int(request.form.get("limit", "200"))))
            try:
                pq = prepareQuery(q)
                res = g.query(pq)
                headers = [str(v) for v in res.vars]
                rows = []
                for i, r in enumerate(res):
                    if i >= limit:
                        break
                    rows.append([html.escape(str(val)) for val in r])
            except Exception as e:
                err = str(e)

        return render_template(
            "sparql.html",
            title="SPARQL",
            q=q,
            prefixes=PREFIXES,
            headers=headers,
            rows=rows,
            error=err,
            limit=limit,
            examples=rq_sparqlFiles(),
        )

    @app.get("/validate")
    def validate_page():
        conforms, report_text, issues = _run_shacl()
        return render_template(
            "validate.html",
            title="Validation",
            conforms=conforms,
            report=report_text,
            violations=issues,
            shapes_exists=(ontology_directory / "shapes.ttl").exists(),
        )

    return app
