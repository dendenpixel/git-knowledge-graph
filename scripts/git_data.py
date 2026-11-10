#!/usr/bin/env python3
import os, json, time
from pathlib import Path
from datetime import datetime, timedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ========= Auth =========
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

# ========= Base caps (auto-scaled to fit remaining quota) =========
MAX_BRANCHES            = 3      # default is 3, set 25 if want > 5 unmerged SPARQL, my git token rip otherwise
MAX_COMMITS_PER_BRANCH  = 120    # newest-first
FILES_FOR_TOP           = 30     # only newest K commits get detail call (files)
SINCE_DAYS              = None   # None = all history; or set 365 etc.
PAGE_SLEEP              = 0.15   # between paginated list calls
DETAIL_SLEEP            = 0.03   # between commit-detail calls
SAFETY_FRACTION         = 0.90   # spend up to 90% of remaining requests (budget buffer)

# ========= Repos (deduped, order preserved) =========
REPOS = [
    "codersforcauses/guild-volunteering",
    "cmdr2/github-actions-wizard",
    "codersforcauses/elucidate",
    "codersforcauses/og-social-cards",
    "codersforcauses/wajo",
    "codersforcauses/repair-lab",
    "codersforcauses/FoGUWA",
    "codersforcauses/foodbank",
    "codersforcauses/poops",
    "codersforcauses/wadl",
    "codersforcauses/home2health",
    "codersforcauses/teach-learn-grow",
    "cmdr2/screenrecorder",
    "cmdr2/study",
    "cmdr2/ggml",
    "codersforcauses/website",
    "codersforcauses/starick",
    "codersforcauses/p2s-frontend",
    "codersforcauses/penni",
    "codersforcauses/csf",
    "kubernetes/kubernetes",
    "tensorflow/tensorflow",
    "llvm/llvm-project",
]
REPOS = list(dict.fromkeys(REPOS))

# ========= Paths =========
base = Path(__file__).resolve().parents[1]
DATA_DIR = base / "data" / "date"
DATA_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILES = {
    "repos":    DATA_DIR / "repos.json",
    "branches": DATA_DIR / "branches.json",
    "commits":  DATA_DIR / "commits.json",
    "users":    DATA_DIR / "users.json",
    "files":    DATA_DIR / "files.json",
}

# ========= Robust HTTP session (retries + timeouts) =========
SESSION = requests.Session()
RETRY = Retry(
    total=5,                 # total retries
    connect=5, read=5,       # retry on connection/read errors
    backoff_factor=0.5,      # 0.5s, 1.0s, 2.0s, ...
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    raise_on_status=False,
)
ADAPTER = HTTPAdapter(max_retries=RETRY, pool_connections=50, pool_maxsize=50)
SESSION.mount("https://", ADAPTER)
SESSION.mount("http://", ADAPTER)

# ========= Helpers =========
def fetch_json(url, tries=3, timeout=30):
    """GET url, return (json, headers) or (None, {}) with retries + rate-limit handling."""
    for attempt in range(tries):
        try:
            r = SESSION.get(url, headers=HEADERS, timeout=timeout)
        except requests.RequestException as e:
            if attempt == tries - 1:
                print(f"[net] {e} -> giving up on {url}")
                return None, {}
            time.sleep(0.5 * (attempt + 1))
            continue

        if r.status_code == 200:
            return r.json(), r.headers

        if r.status_code in (403, 429):
            reset = r.headers.get("X-RateLimit-Reset")
            if reset:
                wait = max(0, int(reset) - int(time.time()))
                eta = time.strftime("%H:%M:%S", time.localtime(int(reset)))
                print(f"[rate-limit] out of requests. Resets in ~{wait}s at {eta}. Re-run to resume.")
            else:
                print("[rate-limit] out of requests. Re-run later to resume.")
            return None, {}

        if r.status_code in (500, 502, 503, 504):
            if attempt == tries - 1:
                print(f"[{r.status_code}] {url} -> giving up")
                return None, {}
            time.sleep(0.5 * (attempt + 1))
            continue

        # Soft-fail on others
        print(f"[{r.status_code}] {url} -> skipping")
        return None, {}

    return None, {}

def get_rate_remaining():
    data, headers = fetch_json("https://api.github.com/rate_limit")
    if not data:
        return None
    try:
        core = data["resources"]["core"]
        return int(core.get("remaining", 0))
    except Exception:
        return None

def estimate_cost_per_repo(b, c, k):
    """
    Rough estimate per repo:
      - 1 repo + 1 branches request
      - per branch: ceil(c/100) commit list pages + k commit detail calls
    """
    commit_pages = (c + 99) // 100
    return 2 + b * (commit_pages + min(k, c))

def scale_caps_to_budget(n_repos, remaining):
    """Auto-shrink K (files), then C (commits), then B (branches) to fit remaining*SAFETY_FRACTION."""
    global MAX_BRANCHES, MAX_COMMITS_PER_BRANCH, FILES_FOR_TOP
    if remaining is None:
        return  # unknown budget; keep defaults
    budget = max(0, int(remaining * SAFETY_FRACTION))
    MIN_B, MIN_C, MIN_K = 1, 40, 10

    while True:
        est_total = n_repos * estimate_cost_per_repo(MAX_BRANCHES, MAX_COMMITS_PER_BRANCH, FILES_FOR_TOP)
        if est_total <= budget:
            break
        if FILES_FOR_TOP > MIN_K:
            FILES_FOR_TOP = max(MIN_K, FILES_FOR_TOP - 10)
            continue
        if MAX_COMMITS_PER_BRANCH > MIN_C:
            MAX_COMMITS_PER_BRANCH = max(MIN_C, MAX_COMMITS_PER_BRANCH - 20)
            continue
        if MAX_BRANCHES > MIN_B:
            MAX_BRANCHES = max(MIN_B, MAX_BRANCHES - 1)
            continue
        break

def fetch_repo(full_name):
    data, _ = fetch_json(f"https://api.github.com/repos/{full_name}")
    return data

def fetch_branches(full_name):
    allb, page = [], 1
    while True:
        data, headers = fetch_json(f"https://api.github.com/repos/{full_name}/branches?per_page=100&page={page}")
        if not data:
            break
        allb.extend(data)
        link = headers.get("Link", "") if headers else ""
        if 'rel="next"' not in link:
            break
        page += 1
        time.sleep(PAGE_SLEEP)
    return allb or []

def fetch_commit_detail(full_name, sha):
    data, _ = fetch_json(f"https://api.github.com/repos/{full_name}/commits/{sha}")
    return data or {}

def fetch_all_commits(full_name, branch, cap=None, since_iso=None):
    all_commits, page = [], 1
    while True:
        url = f"https://api.github.com/repos/{full_name}/commits?sha={branch}&per_page=100&page={page}"
        if since_iso:
            url += f"&since={since_iso}"
        commits, headers = fetch_json(url)
        if not commits:
            break
        all_commits.extend(commits)
        if cap and len(all_commits) >= cap:
            all_commits = all_commits[:cap]
            break
        link = headers.get("Link", "") if headers else ""
        if 'rel="next"' not in link:
            break
        page += 1
        time.sleep(PAGE_SLEEP)
    return all_commits

def save_json(path: Path, rows):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    print(f"{path}: {len(rows)} records")

# ========= Main =========
def main():
    # Scale caps to current budget
    remaining = get_rate_remaining()
    scale_caps_to_budget(len(REPOS), remaining)
    print(f"[caps] branches={MAX_BRANCHES}, commits/branch={MAX_COMMITS_PER_BRANCH}, files_for_top={FILES_FOR_TOP}, since_days={SINCE_DAYS}")

    SINCE_ISO = None if SINCE_DAYS is None else (datetime.utcnow() - timedelta(days=SINCE_DAYS)).isoformat() + "Z"

    repos_out, branches_out, commits_out, users_out, files_out = [], [], [], [], []
    seen_users = set()

    for repo_name in REPOS:
        repo = fetch_repo(repo_name)
        if not repo:
            continue

        repo_id = repo["id"]
        default_branch = repo.get("default_branch", "main")
        print(f"Processing {repo_name}")

        repos_out.append({
            "repo_id": repo_id,
            "repo_name": repo["full_name"],
            "repo_description": repo.get("description", ""),
            "repo_language": repo.get("language", ""),
            "repo_stars": repo.get("stargazers_count", 0),
            "repo_forks": repo.get("forks_count", 0),
            "repo_url": repo["html_url"],
        })

        branches = fetch_branches(repo_name) or []
        # default first, then others; cap to MAX_BRANCHES
        branches_sorted = sorted(branches, key=lambda b: b["name"] != default_branch)[:MAX_BRANCHES]

        for br in branches_sorted:
            branch_name = br["name"]
            is_default = branch_name == default_branch
            branches_out.append({
                "repo_id": repo_id,
                "branch_name": branch_name,
                "commit_sha": br["commit"]["sha"],
                "is_default": is_default,
            })

            commits = fetch_all_commits(
                repo_name,
                branch_name,
                cap=MAX_COMMITS_PER_BRANCH,
                since_iso=SINCE_ISO,
            )

            for i, cm in enumerate(commits):
                sha = cm["sha"]
                info = cm.get("commit", {}) or {}
                author = cm.get("author") or {}
                committer = cm.get("committer") or {}
                parents = [p.get("sha") for p in (cm.get("parents") or []) if p.get("sha")]
                is_initial = len(parents) == 0

                files = []
                if i < FILES_FOR_TOP:
                    detail = fetch_commit_detail(repo_name, sha) or {}
                    files = detail.get("files", []) or []
                    time.sleep(DETAIL_SLEEP)

                commits_out.append({
                    "repo_id": repo_id,
                    "branch_name": branch_name,
                    "commit_sha": sha,
                    "commit_message": info.get("message", ""),
                    "commit_date": (info.get("author") or {}).get("date", ""),
                    "commit_author_login": author.get("login", ""),
                    "commit_committer_login": committer.get("login", ""),
                    "commit_parent_count": len(parents),
                    "commit_parents": parents,
                    "is_initial": is_initial,
                })

                for f in files:
                    files_out.append({
                        "repo_id": repo_id,
                        "commit_sha": sha,
                        "file_name": f.get("filename", ""),
                        "file_status": f.get("status", ""),
                        "file_additions": f.get("additions", 0),
                        "file_deletions": f.get("deletions", 0),
                        "file_changes": f.get("changes", 0),
                    })

                for u in (author, committer):
                    if u and u.get("login"):
                        key = (u["login"], repo_id)
                        if key not in seen_users:
                            users_out.append({
                                "user_login": u["login"],
                                "repo_id": repo_id,
                                "user_id": u.get("id", ""),
                                "user_url": u.get("html_url", ""),
                            })
                            seen_users.add(key)

    save_json(OUTPUT_FILES["repos"],    repos_out)
    save_json(OUTPUT_FILES["branches"], branches_out)
    save_json(OUTPUT_FILES["commits"],  commits_out)
    save_json(OUTPUT_FILES["users"],    users_out)
    save_json(OUTPUT_FILES["files"],    files_out)

if __name__ == "__main__":
    main()
