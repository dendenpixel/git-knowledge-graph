#!/usr/bin/env python3
"""
Json → Turtle converter aligned to your ontology.

Inputs (all optional; missing files are skipped):
  <project_root>/data/repos.json
  <project_root>/data/branches.json
  <project_root>/data/commits.json
  <project_root>/data/files.json

Output:
  <project_root>/ontology/project.ttl

Namespace:
  PREFIX git: <http://www.semanticweb.org/user/ontologies/2025/9/gitOntology_Project#>

Classes used:
  git:Repository, git:Branch, git:Commit, git:File, git:User

Object properties used:
  git:hasBranch, git:belongsTo, git:hasCommit, git:initialCommit,
  git:madeBy, git:parentCommit, git:updatesFile, git:wasChanged

Data properties used:
  git:repoName, git:repoDescription, git:repoLanguage, git:repoStars, git:repoForks, git:repoUrl
  git:branchName, git:isDefault
  git:commitMessage, git:commitDate, git:sha
  git:fileName, git:fileStatus, git:fileAdditions, git:fileDeletions, git:fileChanges
  git:userLogin, git:userUrl, git:userId (optional if present)

Notes:
- We attach commits to branches with git:hasCommit (your ontology).
- We also attach branches to repos with git:hasBranch and git:belongsTo.
- Users are linked to commits with git:madeBy (author/committer both use this).
- Files changed by a commit use git:updatesFile / git:wasChanged.
"""

from pathlib import Path
from urllib.parse import quote
import json, re, sys, traceback
from rdflib import Graph, Namespace, URIRef, Literal
from rdflib.namespace import RDF, XSD

# ---------- helpers ----------

def log(msg: str) -> None:
    print(f"[json2ttl] {msg}", flush=True)

def slug(s: str) -> str:
    s = str(s).strip()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^A-Za-z0-9._\\-]", "", s)
    return s or "item"

def iri_for_file(GIT: Namespace, repo_id: int, file_path: str) -> URIRef:
    # normalise to forward slashes and URL-encode path segments
    norm = str(file_path).replace("\\", "/")
    return URIRef(GIT[f"file/{repo_id}/{quote(norm, safe='/_-.')}"])

def load_json_list(p: Path, label: str):
    if not p.exists():
        log(f"WARNING: {label} not found at {p}")
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            log(f"Loaded {label}: {len(raw)} rows")
            return raw
        log(f"Loaded {label}: single object -> treating as 1 row")
        return [raw]
    except Exception as e:
        log(f"ERROR reading {label}: {e}")
        raise

# ---------- adders (emit triples using YOUR vocabulary) ----------

def add_repo(g: Graph, GIT: Namespace, row: dict):
    rid = row.get("repo_id")
    if rid is None:
        return
    repo = URIRef(GIT[f"repo/{rid}"])
    g.add((repo, RDF.type, GIT.Repository))
    if v := row.get("repo_name"):        g.add((repo, GIT.repoName, Literal(v)))
    if v := row.get("repo_description"): g.add((repo, GIT.repoDescription, Literal(v)))
    if v := row.get("repo_language"):    g.add((repo, GIT.repoLanguage, Literal(v)))
    if "repo_stars" in row:              g.add((repo, GIT.repoStars, Literal(int(row["repo_stars"]), datatype=XSD.integer)))
    if "repo_forks" in row:              g.add((repo, GIT.repoForks, Literal(int(row["repo_forks"]), datatype=XSD.integer)))
    if (v := row.get("repo_url")) and isinstance(v, str) and v.startswith(("http://","https://")):
        g.add((repo, GIT.repoUrl, URIRef(v)))
    # Optional id as data property if present
    if "repo_id" in row:
        g.add((repo, GIT.repoId, Literal(int(row["repo_id"]), datatype=XSD.integer)))

def add_branch(g: Graph, GIT: Namespace, row: dict):
    rid = row.get("repo_id")
    bname = row.get("branch_name")
    if rid is None or not bname:
        return

    repo   = URIRef(GIT[f"repo/{rid}"])
    branch = URIRef(GIT[f"branch/{rid}/{slug(bname)}"])

    g.add((repo,   RDF.type, GIT.Repository))
    g.add((branch, RDF.type, GIT.Branch))
    g.add((branch, GIT.branchName, Literal(bname)))

    # default flag
    if "is_default" in row:
        g.add((branch, GIT.isDefault, Literal(bool(row["is_default"]), datatype=XSD.boolean)))

    # connect repository ↔ branch
    g.add((repo,   GIT.hasBranch, branch))
    g.add((branch, GIT.belongsTo, repo))

def add_user(g: Graph, GIT: Namespace, row: dict):
    uid = row.get("user_id")
    if uid is None and not row.get("user_login"):
        return
    # Prefer numeric id; fall back to login-based IRI
    iri = URIRef(GIT[f"user/{uid}"]) if uid is not None else URIRef(GIT[f"userByLogin/{slug(row.get('user_login'))}"])
    g.add((iri, RDF.type, GIT.User))
    if "user_id" in row:
        g.add((iri, GIT.userId, Literal(int(row["user_id"]), datatype=XSD.integer)))
    if v := row.get("user_login"):
        g.add((iri, GIT.userLogin, Literal(v)))
    if (v := row.get("user_url")) and isinstance(v, str) and v.startswith(("http://","https://")):
        g.add((iri, GIT.userUrl, URIRef(v)))

def add_commit(g: Graph, GIT: Namespace, row: dict, login_index: dict):
    sha = row.get("commit_sha")
    rid = row.get("repo_id")
    bname = row.get("branch_name")  # optional, if present we wire to that branch
    if not sha:
        return

    commit = URIRef(GIT[f"commit/{sha}"])
    g.add((commit, RDF.type, GIT.Commit))
    g.add((commit, GIT.sha, Literal(sha)))

    if rid is not None:
        repo = URIRef(GIT[f"repo/{rid}"])
        g.add((repo, RDF.type, GIT.Repository))
        # It’s often handy to keep repo membership; belongsTo is in your ontology
        g.add((commit, GIT.belongsTo, repo))

    # Link to branch using hasCommit (your ontology)
    if rid is not None and bname:
        branch = URIRef(GIT[f"branch/{rid}/{slug(bname)}"])
        g.add((branch, RDF.type, GIT.Branch))
        g.add((branch, GIT.hasCommit, commit))
        # initial commit if flagged
        if row.get("is_initial"):
            g.add((branch, GIT.initialCommit, commit))

    # commit metadata
    if v := row.get("commit_message"): g.add((commit, GIT.commitMessage, Literal(v)))
    if v := row.get("commit_date"):
        # string → xsd:dateTime (expects ISO 8601)
        g.add((commit, GIT.commitDate, Literal(v, datatype=XSD.dateTime)))

    # parent relations (array of SHAs)
    for psha in (row.get("commit_parents") or []):
        parent = URIRef(GIT[f"commit/{psha}"])
        g.add((parent, RDF.type, GIT.Commit))
        g.add((commit, GIT.parentCommit, parent))

    # author/committer → madeBy (collapse to one property per your ontology list)
    def user_uri_for_login(login: str):
        if not login:
            return None
        return login_index.get(login) or URIRef(GIT[f"userByLogin/{slug(login)}"])

    for key in ("commit_author_login", "commit_committer_login"):
        ulogin = row.get(key)
        uiri = user_uri_for_login(ulogin)
        if uiri is not None:
            g.add((uiri, RDF.type, GIT.User))
            g.add((commit, GIT.madeBy, uiri))

def add_file(g: Graph, GIT: Namespace, row: dict):
    rid   = row.get("repo_id")
    sha   = row.get("commit_sha")
    fname = row.get("file_name")
    if rid is None or not sha or not fname:
        return

    repo   = URIRef(GIT[f"repo/{rid}"])
    commit = URIRef(GIT[f"commit/{sha}"])
    fileir = iri_for_file(GIT, rid, fname)

    g.add((repo,   RDF.type, GIT.Repository))
    g.add((commit, RDF.type, GIT.Commit))
    g.add((fileir, RDF.type, GIT.File))

    # file attributes
    g.add((fileir, GIT.fileName, Literal(fname)))
    if v := row.get("file_status"):          g.add((fileir, GIT.fileStatus, Literal(v)))
    if "file_additions" in row:              g.add((fileir, GIT.fileAdditions, Literal(int(row["file_additions"]), datatype=XSD.integer)))
    if "file_deletions" in row:              g.add((fileir, GIT.fileDeletions, Literal(int(row["file_deletions"]), datatype=XSD.integer)))
    if "file_changes" in row:                g.add((fileir, GIT.fileChanges,   Literal(int(row["file_changes"]),   datatype=XSD.integer)))

    # link commit ↔ file
    g.add((commit, GIT.updatesFile, fileir))
    g.add((fileir,  GIT.wasChanged, commit))

# ---------- main ----------

def main():
    # Resolve paths relative to this file, so it's portable.
    project_root = Path(__file__).resolve().parents[1]
    data_dir     = project_root / "data"
    out_path     = project_root / "ontology" / "project.ttl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Use YOUR ontology namespace
    GIT = Namespace("http://www.semanticweb.org/user/ontologies/2025/9/gitOntology_Project#")

    # Build graph
    g = Graph()
    g.bind("git", GIT)
    g.bind("xsd", XSD)

    # Load all inputs (skip missing with warnings)
    repos_rows    = load_json_list(data_dir / "repos.json",    "repos.json")
    branches_rows = load_json_list(data_dir / "branches.json", "branches.json")
    commits_rows  = load_json_list(data_dir / "commits.json",  "commits.json")
    files_rows    = load_json_list(data_dir / "files.json",    "files.json")
    users_rows    = load_json_list(data_dir / "users.json",    "users.json")

    # Index users by login (helps when commits reference logins only)
    login_index = {}
    for row in users_rows:
        uiri = None
        if row.get("user_id") is not None:
            uiri = URIRef(GIT[f"user/{row['user_id']}"])
        elif row.get("user_login"):
            uiri = URIRef(GIT[f"userByLogin/{slug(row['user_login'])}"])
        if uiri is not None:
            login_index[row.get("user_login")] = uiri
            add_user(g, GIT, row)

    # Emit repos, branches, commits, files
    for row in repos_rows:    add_repo(g, GIT, row)
    for row in branches_rows: add_branch(g, GIT, row)
    for row in commits_rows:  add_commit(g, GIT, row, login_index)
    for row in files_rows:    add_file(g, GIT, row)

    # Write TTL
    g.serialize(destination=str(out_path), format="turtle")
    log(f"Wrote: {out_path}")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
