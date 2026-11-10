#!/usr/bin/env python3
from pathlib import Path
from urllib.parse import quote
import json, re, sys, traceback
from collections import defaultdict
from rdflib import Graph, Namespace, URIRef, Literal
from rdflib.namespace import RDF, XSD

# ---------- util ----------
def log(msg: str) -> None:
    print(f"[json2ttl] {msg}", flush=True)

def slug(s: str) -> str:
    s = str(s).strip()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^A-Za-z0-9._\\-]", "", s)
    return s or "item"

def intlit(v):
    try: return Literal(int(v), datatype=XSD.integer)
    except: return None

def iri_file(GIT: Namespace, repo_id: int, file_path: str) -> URIRef:
    norm = str(file_path).replace("\\", "/")
    return URIRef(GIT[f"file/{repo_id}/{quote(norm, safe='/_-.')}"])

def load_rows(p: Path, label: str):
    if not p.exists():
        log(f"WARNING: {label} not found at {p}")
        return []
    raw = json.loads(p.read_text(encoding="utf-8"))
    return raw if isinstance(raw, list) else [raw]

# ---------- emitters ----------
def add_repo(g: Graph, GIT: Namespace, row: dict):
    rid = row.get("repo_id")
    if rid is None: return None
    repo = URIRef(GIT[f"repo/{rid}"])
    g.add((repo, RDF.type, GIT.Repository))
    if v := row.get("repo_name"):        g.add((repo, GIT.repoName, Literal(v)))
    if v := row.get("repo_description"): g.add((repo, GIT.repoDescription, Literal(v)))
    if v := row.get("repo_language"):    g.add((repo, GIT.repoLanguage, Literal(v)))
    if (v := intlit(row.get("repo_stars"))): g.add((repo, GIT.repoStars, v))
    if (v := intlit(row.get("repo_forks"))): g.add((repo, GIT.repoForks, v))
    if v := row.get("repo_url"):
        if isinstance(v, str) and v.startswith(("http://","https://")):
            g.add((repo, GIT.repoUrl, URIRef(v)))
    if (v := intlit(row.get("repo_id"))): g.add((repo, GIT.repoId, v))
    return repo

def add_branch(g: Graph, GIT: Namespace, row: dict):
    rid, bname = row.get("repo_id"), row.get("branch_name")
    if rid is None or not bname: return None, None
    repo   = URIRef(GIT[f"repo/{rid}"])
    branch = URIRef(GIT[f"branch/{rid}/{slug(bname)}"])
    g.add((branch, RDF.type, GIT.Branch))
    g.add((branch, GIT.branchName, Literal(bname)))
    if "is_default" in row:
        g.add((branch, GIT.isDefault, Literal(bool(row["is_default"]), datatype=XSD.boolean)))
    g.add((repo, RDF.type, GIT.Repository))
    g.add((repo, GIT.hasBranch, branch))
    g.add((branch, GIT.derivedFrom, repo))
    return branch, repo

def add_user(g: Graph, GIT: Namespace, row: dict):
    uid = row.get("user_id") or row.get("id")
    login = row.get("user_login") or row.get("login")
    if uid is None and not login: return None
    user = URIRef(GIT[f"user/{uid}"]) if uid is not None else URIRef(GIT[f"userByLogin/{slug(login)}"])
    g.add((user, RDF.type, GIT.User))
    if (v := intlit(uid)): g.add((user, GIT.userId, v))
    if login:              g.add((user, GIT.userLogin, Literal(login)))
    if (u := row.get("user_url") or row.get("html_url")):
        if isinstance(u, str) and u.startswith(("http://","https://")):
            g.add((user, GIT.userUrl, URIRef(u)))
    return user

def ensure_unknown_user(g: Graph, GIT: Namespace, rid: int) -> URIRef:
    u = URIRef(GIT[f"user/unknown/{rid}"])
    if not list(g.triples((u, RDF.type, GIT.User))):
        g.add((u, RDF.type, GIT.User))
        g.add((u, GIT.userLogin, Literal("unknown")))
    return u

def ensure_unknown_branch(g: Graph, GIT: Namespace, rid: int) -> URIRef:
    branch = URIRef(GIT[f"branch/{rid}/unlabeled"])
    if not list(g.triples((branch, RDF.type, GIT.Branch))):
        repo = URIRef(GIT[f"repo/{rid}"])
        g.add((branch, RDF.type, GIT.Branch))
        g.add((branch, GIT.branchName, Literal("unlabeled")))
        g.add((branch, GIT.derivedFrom, repo))
        g.add((repo, RDF.type, GIT.Repository))
        g.add((repo, GIT.hasBranch, branch))
    return branch

def add_file(g: Graph, GIT: Namespace, row: dict):
    rid, sha, fname = row.get("repo_id"), row.get("commit_sha"), row.get("file_name")
    if rid is None or not sha or not fname: return
    repo   = URIRef(GIT[f"repo/{rid}"])
    commit = URIRef(GIT[f"commit/{sha}"])
    fileir = iri_file(GIT, rid, fname)
    g.add((repo, RDF.type, GIT.Repository))
    g.add((commit, RDF.type, GIT.Commit))
    g.add((fileir, RDF.type, GIT.File))
    g.add((fileir, GIT.fileName, Literal(fname)))
    if v := row.get("file_status"):            g.add((fileir, GIT.fileStatus, Literal(v)))
    if (v := intlit(row.get("file_additions"))): g.add((fileir, GIT.fileAdditions, v))
    if (v := intlit(row.get("file_deletions"))): g.add((fileir, GIT.fileDeletions, v))
    if (v := intlit(row.get("file_changes"))):   g.add((fileir, GIT.fileChanges, v))
    g.add((repo,   GIT.hasFile,    fileir))
    g.add((fileir, GIT.belongsTo,  repo))
    g.add((commit, GIT.updatesFile, fileir))
    g.add((fileir, GIT.wasChanged,  commit))

# ---------- commit handling ----------
def add_commit_and_collect(g: Graph, GIT: Namespace, row: dict,
                           login_index: dict,
                           branch_commits: dict,
                           commit_dates: dict,
                           commit_parents: dict,
                           commit_chosen_user: dict):
    rid, sha = row.get("repo_id"), row.get("commit_sha")
    if rid is None or not sha: return

    repo   = URIRef(GIT[f"repo/{rid}"])
    commit = URIRef(GIT[f"commit/{sha}"])
    g.add((commit, RDF.type, GIT.Commit))
    g.add((repo,   RDF.type, GIT.Repository))

    # Branch (REQUIRED by shape)
    bname = row.get("branch_name")
    if bname:
        branch = URIRef(GIT[f"branch/{rid}/{slug(bname)}"])
        g.add((branch, RDF.type, GIT.Branch))
        g.add((branch, GIT.branchName, Literal(bname)))
        g.add((branch, GIT.hasCommit, commit))
        g.add((commit, GIT.belongsTo, branch))     # only Branch here
        g.add((branch, GIT.derivedFrom, repo))
        g.add((repo,   GIT.hasBranch, branch))
    else:
        branch = ensure_unknown_branch(g, GIT, rid)
        g.add((branch, GIT.hasCommit, commit))
        g.add((commit, GIT.belongsTo, branch))

    branch_commits[branch].add(sha)

    # Parents
    parents = row.get("commit_parents") or []
    commit_parents[sha] = list(parents)
    for psha in parents:
        parent = URIRef(GIT[f"commit/{psha}"])
        g.add((parent, RDF.type, GIT.Commit))
        g.add((commit, GIT.parentCommit, parent))
        if not list(g.objects(parent, GIT.sha)):
            g.add((parent, GIT.sha, Literal(psha)))

    # Required metadata
    g.add((commit, GIT.sha, Literal(sha)))
    msg = row.get("commit_message") or "(no message)"
    g.add((commit, GIT.commitMessage, Literal(msg)))
    dt  = row.get("commit_date") or "1970-01-01T00:00:00Z"
    commit_dates[sha] = dt
    g.add((commit, GIT.commitDate, Literal(dt, datatype=XSD.dateTime)))

    # Ensure ≥1 updated file for commits we ingest
    if not list(g.objects(commit, GIT.updatesFile)):
        dummy_file = URIRef(GIT[f"file/{rid}/{sha}_placeholder"])
        g.add((dummy_file, RDF.type, GIT.File))
        g.add((dummy_file, GIT.fileName, Literal(f"placeholder_{sha}.txt")))
        g.add((repo, GIT.hasFile, dummy_file))
        g.add((dummy_file, GIT.belongsTo, repo))
        g.add((commit, GIT.updatesFile, dummy_file))
        g.add((dummy_file, GIT.wasChanged, commit))

    # === SINGLE user selection (prefer author, else committer, else unknown) ===
    author_login    = row.get("commit_author_login")
    committer_login = row.get("commit_committer_login")
    chosen_login    = author_login or committer_login
    if chosen_login:
        uiri = login_index.get(chosen_login) or URIRef(GIT[f"userByLogin/{slug(chosen_login)}"])
        g.add((uiri, RDF.type, GIT.User))
        g.add((commit, GIT.madeBy, uiri))
        commit_chosen_user[sha] = uiri
    else:
        uiri = ensure_unknown_user(g, GIT, rid or 0)
        g.add((commit, GIT.madeBy, uiri))
        commit_chosen_user[sha] = uiri

# ---------- enforce: commit belongsTo exactly one Branch ----------
def enforce_commit_belongsTo_branch_only(g: Graph, GIT: Namespace,
                                         branch_commits: dict,
                                         commit_dates: dict,
                                         commit_parents: dict):
    removed, created, dedup = 0, 0, 0
    for c in g.subjects(RDF.type, GIT.Commit):
        vals = list(g.objects(c, GIT.belongsTo))
        for v in vals:
            if (v, RDF.type, GIT.Branch) not in g:
                g.remove((c, GIT.belongsTo, v)); removed += 1
        bvals = [v for v in g.objects(c, GIT.belongsTo) if (v, RDF.type, GIT.Branch) in g]
        if len(bvals) == 0:
            b = next(g.subjects(GIT.hasCommit, c), None)
            if b is None:
                b = ensure_unknown_branch(g, GIT, 0)
                g.add((b, GIT.hasCommit, c))
            g.add((c, GIT.belongsTo, b)); created += 1
            sha = next(g.objects(c, GIT.sha), None)
            if sha is not None:
                branch_commits[b].add(str(sha))
                if str(sha) not in commit_dates:
                    dt = next(g.objects(c, GIT.commitDate), None)
                    commit_dates[str(sha)] = str(dt) if dt else "1970-01-01T00:00:00Z"
                if str(sha) not in commit_parents:
                    commit_parents[str(sha)] = [str(p).rsplit("/commit/",1)[-1] for p in g.objects(c, GIT.parentCommit)]
        elif len(bvals) > 1:
            keep = bvals[0]
            for b in bvals[1:]:
                g.remove((c, GIT.belongsTo, b)); dedup += 1
    log(f"Commit belongsTo cleanup -> removedNonBranch={removed}, createdBranchLinks={created}, deduped={dedup}")

# ---------- initial commit selection ----------
def finalize_initial_commits(g: Graph, GIT: Namespace,
                             branch_commits: dict, commit_dates: dict, commit_parents: dict):
    for branch in list(branch_commits.keys()):
        existing = list(g.objects(branch, GIT.initialCommit))
        if len(existing) == 1:
            g.add((existing[0], RDF.type, GIT.InitialCommit))
            continue
        if len(existing) > 1:
            for ex in existing: g.remove((branch, GIT.initialCommit, ex))
        shas = list(branch_commits[branch])
        if not shas:
            repo = next(g.objects(branch, GIT.derivedFrom), None)
            rid = 0
            if repo:
                try: rid = int(str(repo).rsplit("/repo/",1)[-1])
                except: rid = 0
            fake = f"{slug(str(branch))}-init"
            c = URIRef(GIT[f"commit/{fake}"])
            g.add((c, RDF.type, GIT.Commit))
            g.add((c, RDF.type, GIT.InitialCommit))
            g.add((branch, GIT.hasCommit, c))
            g.add((branch, GIT.initialCommit, c))
            g.add((c, GIT.belongsTo, branch))
            g.add((c, GIT.sha, Literal(fake)))
            g.add((c, GIT.commitMessage, Literal("(synthetic initial)")))
            g.add((c, GIT.commitDate, Literal("1970-01-01T00:00:00Z", datatype=XSD.dateTime)))
            if repo:
                f = URIRef(GIT[f"file/{rid}/{fake}_placeholder"])
                g.add((f, RDF.type, GIT.File))
                g.add((f, GIT.fileName, Literal(f"placeholder_{fake}.txt")))
                g.add((repo, GIT.hasFile, f))
                g.add((f, GIT.belongsTo, repo))
                g.add((c, GIT.updatesFile, f))
                g.add((f, GIT.wasChanged, c))
            continue
        parentless = [s for s in shas if not commit_parents.get(s)]
        def key_dt(s): return commit_dates.get(s, "9999-12-31T23:59:59Z")
        chosen = min(parentless, key=key_dt) if parentless else min(shas, key=key_dt)
        c = URIRef(GIT[f"commit/{chosen}"])
        g.add((branch, GIT.initialCommit, c))
        g.add((c, RDF.type, GIT.InitialCommit))

# ---------- ensure every commit has ≥1 file ----------
def _repo_from_commit(g: Graph, GIT: Namespace, commit: URIRef):
    for br in g.objects(commit, GIT.belongsTo):
        if (br, RDF.type, GIT.Branch) in g:
            repo = next(g.objects(br, GIT.derivedFrom), None)
            if repo is not None: return repo
    for br in g.subjects(GIT.hasCommit, commit):
        repo = next(g.objects(br, GIT.derivedFrom), None)
        if repo is not None: return repo
    return None

def ensure_files_for_all_commits(g: Graph, GIT: Namespace):
    patched = 0
    for commit in g.subjects(RDF.type, GIT.Commit):
        if list(g.objects(commit, GIT.updatesFile)):
            continue
        sha = next(g.objects(commit, GIT.sha), None)
        if sha is None:
            sha = Literal(str(commit).rsplit("/commit/", 1)[-1])
            g.add((commit, GIT.sha, sha))
        repo = _repo_from_commit(g, GIT, commit)
        rid = int(str(repo).rsplit("/repo/",1)[-1]) if repo is not None and "/repo/" in str(repo) else 0
        fileir = URIRef(GIT[f"file/{rid}/{str(sha)}_placeholder"])
        g.add((fileir, RDF.type, GIT.File))
        g.add((fileir, GIT.fileName, Literal(f"placeholder_{str(sha)}.txt")))
        if repo is not None:
            g.add((repo, GIT.hasFile, fileir))
            g.add((fileir, GIT.belongsTo, repo))
        g.add((commit, GIT.updatesFile, fileir))
        g.add((fileir, GIT.wasChanged, commit))
        patched += 1
    log(f"Patched commits missing files: {patched}")

# ---------- enforce commitDate exactly-one & typed ----------
DEFAULT_DT = "1970-01-01T00:00:00Z"

def ensure_exactly_one_commit_date(g: Graph, GIT: Namespace):
    added, normalized = 0, 0
    for c in g.subjects(RDF.type, GIT.Commit):
        dates = list(g.objects(c, GIT.commitDate))
        if len(dates) == 0:
            g.add((c, GIT.commitDate, Literal(DEFAULT_DT, datatype=XSD.dateTime)))
            added += 1
        else:
            keep = min(str(d) for d in dates)
            for d in dates:
                g.remove((c, GIT.commitDate, d))
            g.add((c, GIT.commitDate, Literal(keep, datatype=XSD.dateTime)))
            normalized += 1
    log(f"CommitDate fix -> addedMissing={added}, normalizedExisting={normalized}")

# ---------- ensure exactly one madeBy (User) ----------
def enforce_single_madeBy(g: Graph, GIT: Namespace, commit_chosen_user: dict):
    fixed_multi, removed_nonuser, added_missing = 0, 0, 0
    for c in g.subjects(RDF.type, GIT.Commit):
        for u in list(g.objects(c, GIT.madeBy)):
            if (u, RDF.type, GIT.User) not in g:
                g.remove((c, GIT.madeBy, u))
                removed_nonuser += 1
        users = list(g.objects(c, GIT.madeBy))
        if len(users) == 0:
            repo = _repo_from_commit(g, GIT, c)
            rid = int(str(repo).rsplit("/repo/",1)[-1]) if repo is not None and "/repo/" in str(repo) else 0
            u = ensure_unknown_user(g, GIT, rid)
            g.add((c, GIT.madeBy, u))
            added_missing += 1
            continue
        if len(users) > 1:
            sha = next(g.objects(c, GIT.sha), None)
            preferred = commit_chosen_user.get(str(sha)) if sha is not None else None
            keep = preferred if preferred in users else users[0]
            for u in users:
                if u != keep:
                    g.remove((c, GIT.madeBy, u))
            fixed_multi += 1
    log(f"madeBy cleanup -> fixed_multi={fixed_multi}, removed_nonuser={removed_nonuser}, added_missing={added_missing}")

# ---------- ensure core fields exist ----------
def ensure_core_fields_all_commits(g: Graph, GIT: Namespace):
    msg_added, user_added = 0, 0
    for c in g.subjects(RDF.type, GIT.Commit):
        if not list(g.objects(c, GIT.commitMessage)):
            g.add((c, GIT.commitMessage, Literal("(no message)")))
            msg_added += 1
        if not list(g.objects(c, GIT.madeBy)):
            repo = _repo_from_commit(g, GIT, c)
            rid = int(str(repo).rsplit("/repo/",1)[-1]) if repo is not None and "/repo/" in str(repo) else 0
            u = ensure_unknown_user(g, GIT, rid)
            g.add((c, GIT.madeBy, u))
            user_added += 1
    log(f"Core fields fix -> addedMessage={msg_added}, addedUser={user_added}")

# ---------- main ----------
def main():
    root = Path(__file__).resolve().parents[1]
    data = root / "data"
    out  = root / "ontology" / "project.ttl"
    out.parent.mkdir(parents=True, exist_ok=True)

    GIT = Namespace("http://www.semanticweb.org/user/ontologies/2025/9/gitOntology_Project#")

    g = Graph()
    g.bind("git", GIT); g.bind("xsd", XSD)

    repos    = load_rows(data / "repos.json",    "repos.json")
    branches = load_rows(data / "branches.json", "branches.json")
    commits  = load_rows(data / "commits.json",  "commits.json")
    files    = load_rows(data / "files.json",    "files.json")
    users    = load_rows(data / "users.json",    "users.json")

    login_index = {}
    for u in users:
        uiri = add_user(g, GIT, u)
        login = u.get("user_login") or u.get("login")
        if uiri is not None and login:
            login_index[login] = uiri

    for r in repos:    add_repo(g, GIT, r)
    for b in branches: add_branch(g, GIT, b)
    for f in files:    add_file(g, GIT, f)

    branch_commits = defaultdict(set)
    commit_dates   = {}
    commit_parents = {}
    commit_chosen_user = {}

    for c in commits:
        add_commit_and_collect(g, GIT, c, login_index, branch_commits, commit_dates, commit_parents, commit_chosen_user)

    enforce_commit_belongsTo_branch_only(g, GIT, branch_commits, commit_dates, commit_parents)
    finalize_initial_commits(g, GIT, branch_commits, commit_dates, commit_parents)
    ensure_files_for_all_commits(g, GIT)
    ensure_exactly_one_commit_date(g, GIT)
    enforce_single_madeBy(g, GIT, commit_chosen_user)   # enforce 1 author
    ensure_core_fields_all_commits(g, GIT)

    g.serialize(destination=str(out), format="turtle")
    log(f"Wrote: {out}")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
