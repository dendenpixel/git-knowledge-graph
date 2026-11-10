# Git Knowledge Graph

Git history as an RDF knowledge graph: my last full pull turned 23 GitHub repos (63 branches, 6,597 commits, 481 users, 11,846 file records) into 27 MB of triples, validated with pySHACL, queried with SPARQL and browsable in a small Flask app.

Built as university coursework in 2025. The brief and marking material aren't included.

It was built without version control, so the git history here starts at the import in October 2026.

## What's in it

- `ontology/gitOntology.ttl`: the OWL ontology (Repository, Branch, Commit, InitialCommit, MergeCommit, User, File).
- `ontology/shapes.ttl`: the pySHACL shapes the data has to pass.
- `scripts/`: the pipeline (fetch from the GitHub API, JSON to Turtle, merge, OWL-RL reasoning, validation, queries).
- `queries/`: four SPARQL queries.
- `app/` + `run.py`: a Flask browser for classes, instances, triples, SPARQL and validation.
- `ontology/Flask/sample_synthetic.ttl`: a hand-written, fake 53-triple sample (two users, one repo, three commits including a merge) so the app runs straight after cloning. The real dumps aren't in the repo (the biggest was 41 MB), see below to regenerate them.

## How to run

Python 3.10 or newer. On Windows:

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

On macOS/Linux use `python3` and `source venv/bin/activate` instead. Then open http://127.0.0.1:5001. With the shipped sample the first page loads in about a second. With a real 2.6 MB graph in `ontology/Flask/` the first `/` request takes around 15 s, because the app computes the OWL-RL closure on first load and caches it after that. Set `FLASK_DEBUG=1` if you want Flask's debugger.

The browser loads every `.ttl` file in `ontology/Flask/`, so to browse real data drop a converted graph in there next to the ontology and shapes.

### Regenerating the real data

The data is pulled live from the GitHub API, so you need a token in `GITHUB_TOKEN` (Windows `set GITHUB_TOKEN=...`, macOS/Linux `export GITHUB_TOKEN=...`). The repo list is `REPOS` at the top of `scripts/git_data.py`.

```sh
python scripts/git_data.py            # writes data/date/*.json
# copy the five JSON files from data/date/ up into data/ (the converter reads data/)
python scripts/JsontottlConverter.py  # data/*.json -> ontology/project.ttl
python scripts/combineOntowithttl.py  # + ontology -> ontology/combined.ttl
python scripts/Reason.py              # OWL-RL -> ontology/project_inferred.ttl
python scripts/Validate.py            # pySHACL -> ontology/shacl_results.ttl
python scripts/queryRunner.py         # runs the four queries
```

All the dumps (`project.ttl`, `combined.ttl`, `project_inferred.ttl`, the Flask graphs and every `data/*.json`) are gitignored. The 27 MB figure is the size of `project.ttl` from that pull, and `combined.ttl` came out at 28 MB and `project_inferred.ttl` at 41 MB.

## Tests

There's no unit test suite. The checks are the SHACL shapes and a boot test:

- `python scripts/Validate.py` over the full 27 MB graph wrote `ontology/shacl_results.ttl` with `sh:conforms true` (that file is kept in the repo).
- Boot test on 23 Oct 2026 (Python 3.13, the pinned versions in `requirements.txt`): fresh venv, `pip install -r requirements.txt`, `python run.py`, then `/` returned HTTP 200 with 391 base and 1,108 inferred triples, and `/validate` said `Conforms: True` on the synthetic sample.

## Schema

| Class        | Key properties / relations |
| ------------ | -------------------------- |
| `Repository` | `hasBranch`, `hasFile`, `repoName`, `repoUrl`, `repoStars`, `repoForks` |
| `Branch`     | `branchName`, `derivedFrom` (-> Repository), `hasCommit`, `initialCommit`, `isDefault` |
| `Commit`     | `sha`, `commitMessage`, `commitDate`, `madeBy` (-> User), `belongsTo` (-> Branch), `parentCommit` (0..n), `updatesFile` |
| `User`       | `userId`, `userLogin`, `userUrl` |
| `File`       | `fileName`, `belongsTo` (-> Repository), `wasChanged` (<- Commit), `updatesFile` (<- Commit) |

SHACL rules (`ontology/shapes.ttl`):

- A repository has at least one branch and at least one file.
- A branch has exactly one name, one initial commit and one parent repository.
- A commit has exactly one author, one date, one sha, belongs to exactly one branch and updates at least one file.
- A commit has 0 parents (initial), 1 parent (normal) or 2+ (merge), exactly one of those.

Raw API data doesn't always fit those shapes, so `scripts/JsontottlConverter.py` fixes it up on the way in: every commit gets exactly one branch (with a fallback when the API doesn't link one), each branch's initial commit is its earliest parentless commit, a commit with no files gets a placeholder file, and the author is the commit author, then the committer, then "unknown".

## Queries

| File | Question |
| ---- | -------- |
| `queries/unmergedBranches.rq` | Repos with more than 5 branches whose commits aren't reachable from the default branch via `parentCommit+` |
| `queries/MoreThan3.rq` | Users who committed to several repos on the same day (curbed to 2, see below) |
| `queries/commitIsMerge.rq` | Commits with 2 or more parents |
| `queries/commitMessages.rq` | Commits mentioning "security" or "vulnerability" that reached a given branch |

You can run them all with `scripts/queryRunner.py` or load any of them from the SPARQL page in the app.

## What broke

- **The fetch burned the token.** Pulling 23 repos, three of them huge (kubernetes, tensorflow, llvm-project), used up a token's 5,000 requests an hour in about 20 minutes. I capped the pull (3 branches per repo, the newest 120 commits per branch, file details only for the newest 30) and made those caps shrink to fit whatever quota is left, which is the `[caps]` line `git_data.py` prints.
- **The data couldn't answer one question.** Nobody in my data committed to three repos on the same day, so `MoreThan3.rq` is curbed to 2 to show any results, and the comment in the query says so. Change it back to 3 for the strict version.

## Status and limits

- Finished coursework, not maintained.
- The full pipeline holds everything in memory, so the queries over the 28 MB graph take 10 to 20 minutes and a fair bit of RAM.
- The fetch writes to `data/date/` and the converter reads `data/`, so there's a manual copy in between.
- `run.py` uses Flask's development server, which is fine locally and nothing more.

AI use: this README was drafted with Claude from the original notes and checked against a fresh-venv boot test.

Licence: MIT, see `LICENSE`.
