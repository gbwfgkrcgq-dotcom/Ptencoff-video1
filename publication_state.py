import json
import subprocess


STATE_FILE = "state.json"


def git(*args, data=None, check=True):
    return subprocess.run(
        ["git", *args], input=data, text=True, capture_output=True, check=check
    )


def load_state(state_ref):
    branch = git("ls-remote", "--exit-code", "origin", state_ref, check=False)
    if branch.returncode == 2:
        return None, {"sent": 0, "pending": None}
    if branch.returncode != 0:
        raise RuntimeError("Не удалось проверить ветку состояния.")
    git("fetch", "--no-tags", "origin", state_ref)
    revision = git("rev-parse", "FETCH_HEAD").stdout.strip()
    return revision, json.loads(git("show", f"{revision}:{STATE_FILE}").stdout)


def record_state(state_ref, previous, sent, pending):
    content = json.dumps({"sent": sent, "pending": pending}) + "\n"
    blob = git("hash-object", "-w", "--stdin", data=content).stdout.strip()
    tree = git("mktree", data=f"100644 blob {blob}\t{STATE_FILE}\n").stdout.strip()
    args = ["commit-tree", tree, "-m", "Update video publication status"]
    if previous:
        args.extend(["-p", previous])
    revision = git(*args).stdout.strip()
    git("push", f"--force-with-lease={state_ref}:{previous or ''}", "origin", f"{revision}:{state_ref}")
    return revision
