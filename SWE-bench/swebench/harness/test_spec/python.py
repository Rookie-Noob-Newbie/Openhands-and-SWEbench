import os
import posixpath
import re
import requests

from swebench.harness.constants import (
    SWEbenchInstance,
    MAP_REPO_TO_ENV_YML_PATHS,
    MAP_REPO_TO_INSTALL,
    MAP_REPO_TO_REQS_PATHS,
    MAP_REPO_VERSION_TO_SPECS,
    NON_TEST_EXTS,
    SWE_BENCH_URL_RAW,
    START_TEST_OUTPUT,
    END_TEST_OUTPUT,
    REPO_BASE_COMMIT_BRANCH,
)
from swebench.harness.utils import get_modified_files, load_cached_environment_yml
from functools import cache

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36"
}

REPLACE_REQ_PACKAGES = [
    # pkg-to-replace, replacement
    ("types-pkg_resources", "types-setuptools")
]
FIXED_COMMANDS = [
    {
        "astropy__astropy-8707": "pip install pytest==6.2.5"
    },
    {
        "astropy__astropy-8872" :"sed -i.bak -e 's/from distutils.version import LooseVersion/from packaging.version import Version/' -e 's/LooseVersion(/Version(/g' astropy/units/tests/test_quantity.py"
    },
    {
        "psf__requests-2317" : '''
python - <<'PY'
from pathlib import Path
nl = chr(10)
p = Path("test_requests.py")
text = p.read_text()

# 1) 补充 http.client import
text = text.replace("import io"+nl+"import requests"+nl+"import pytest"+nl+"",
                    "import http.client"+nl+"import io"+nl+"import requests"+nl+"import pytest"+nl+"")

# 2) 在 u 函数后插入兼容补丁
shim = """# Compat shim: vendored urllib3 in requests 2.4.3 calls getresponse(buffering=True),
# but http.client.HTTPConnection on Py3 does not accept that keyword.
from requests.packages.urllib3.connection import HTTPConnection as _Urllib3HTTPConnection

def _compat_getresponse(self, *args, **kwargs):
    kwargs.pop("buffering", None)
    return http.client.HTTPConnection.getresponse(self, *args, **kwargs)

_Urllib3HTTPConnection.getresponse = _compat_getresponse

"""
needle = "    def u(s):"+nl+"        return s.decode('unicode-escape')"+nl+""+nl+""
if shim not in text:
    text = text.replace(needle, needle + shim)

# 3) 外域重定向改为 example.com
text = text.replace("params={'url': 'http://www.google.co.uk'},",
                    "params={'url': 'http://example.com/'},", 1)

# 4) history 用 httpbin 并断言长度
text = text.replace("r = requests.get('https://httpbin.org/redirect/5')",
                    "r = requests.get(httpbin('redirect', '5'))")
text = text.replace("total = r.history[-1].history",
                    "assert len(r.history) == 5"+nl+"        total = r.history[-1].history", 1)

p.write_text(text)
PY
'''
    }
]

@cache
def get_environment_yml_by_commit(repo: str, commit: str, env_name: str) -> str:
    for req_path in MAP_REPO_TO_ENV_YML_PATHS[repo]:
        reqs_url = posixpath.join(SWE_BENCH_URL_RAW, repo, commit, req_path)
        reqs = requests.get(reqs_url, headers=HEADERS)
        if reqs.status_code == 200:
            break
    else:
        raise ValueError(
            f"Could not find environment.yml at paths {MAP_REPO_TO_ENV_YML_PATHS[repo]} for repo {repo} at commit {commit}"
        )

    lines = reqs.text.split("\n")
    cleaned = []
    for line in lines:
        # Rename environment to given name
        if line.startswith("name:"):
            cleaned.append(f"name: {env_name}")
            continue
        cleaned.append(line)

    return "\n".join(cleaned)


def clean_environment_yml(yml_text: str) -> str:
    """
    Clean environment.yml by removing packages that have been yanked from PyPI

    conda style yamls take the form:
    ...
    - channels:
        ...
    - dependencies:
        ...
    - pip:
        - pkg_to_replace
        - pkg_to_replace
    - ... (more dependencies)

    We want to replace packages in the pip section only.
    """
    pip_match = re.search(r"^(\s*-\s*pip\s*:\s*\n)", yml_text, flags=re.MULTILINE)
    if not pip_match:
        return yml_text
    pip_line_start = pip_match.start()
    # get indentation level of pip line
    pip_indent = len(pip_match.group(1)) - len(pip_match.group(1).lstrip())
    pip_content_start = pip_match.end()
    # find where pip section ends by looking for a line that's at same or less indentation
    # or a line that starts a new top-level dependency (not pip)
    lines_after_pip = yml_text[pip_content_start:].split("\n")
    pip_section_end = pip_content_start
    for ix, line in enumerate(lines_after_pip):
        if line.strip() == "":
            continue
        line_indent = len(line) - len(line.lstrip())
        if line_indent <= pip_indent:
            # +1 to account for the newline
            pip_section_end = pip_content_start + sum(
                len(l) + 1 for l in lines_after_pip[:ix]
            )
            break
    else:
        pip_section_end = len(yml_text)
    prefix = yml_text[:pip_content_start]
    pip_portion = yml_text[pip_content_start:pip_section_end]
    suffix = yml_text[pip_section_end:]
    for pkg_to_replace, replacement in REPLACE_REQ_PACKAGES:
        if replacement == None:
            pip_portion = re.sub(
                rf"^(\s*-\s*){re.escape(pkg_to_replace)}([<>~]=?.*|$)\n?",
                "",
                pip_portion,
                flags=re.MULTILINE,
            )
        else:
            pip_portion = re.sub(
                rf"^(\s*-\s*){re.escape(pkg_to_replace)}([<>=!~]=?.*|$)",
                rf"\1{replacement}",
                pip_portion,
                flags=re.MULTILINE,
            )
    return prefix + pip_portion + suffix


def get_environment_yml(instance: SWEbenchInstance, env_name: str) -> str:
    """
    Get environment.yml for given task instance

    Args:
        instance (dict): SWE Bench Task instance
        env_name (str): Rename retrieved environment.yml to this name
    Returns:
        environment.yml (str): Returns environment.yml as string
    """
    # Attempt to find environment.yml at each path based on task instance's repo
    commit = (
        instance["environment_setup_commit"]
        if "environment_setup_commit" in instance
        else instance["base_commit"]
    )
    yml_text = get_environment_yml_by_commit(instance["repo"], commit, env_name)
    yml_text = clean_environment_yml(yml_text)
    return yml_text


@cache
def get_requirements_by_commit(repo: str, commit: str) -> str:
    for req_path in MAP_REPO_TO_REQS_PATHS[repo]:
        reqs_url = posixpath.join(SWE_BENCH_URL_RAW, repo, commit, req_path)
        reqs = requests.get(reqs_url, headers=HEADERS)
        if reqs.status_code == 200:
            break
    else:
        raise ValueError(
            f"Could not find requirements.txt at paths {MAP_REPO_TO_REQS_PATHS[repo]} for repo {repo} at commit {commit}"
        )

    lines = reqs.text
    original_req = []
    additional_reqs = []
    req_dir = "/".join(req_path.split("/")[:-1])
    exclude_line = lambda line: any(
        [line.strip().startswith(x) for x in ["-e .", "#", ".[test"]]
    )

    for line in lines.split("\n"):
        if line.strip().startswith("-r"):
            # Handle recursive requirements
            file_name = line[len("-r") :].strip()
            reqs_url = os.path.join(
                SWE_BENCH_URL_RAW,
                repo,
                commit,
                req_dir,
                file_name,
            )
            reqs = requests.get(reqs_url, headers=HEADERS)
            if reqs.status_code == 200:
                for line_extra in reqs.text.split("\n"):
                    if not exclude_line(line_extra):
                        additional_reqs.append(line_extra)
        else:
            if not exclude_line(line):
                original_req.append(line)

    # Combine all requirements into single text body
    additional_reqs.append("\n".join(original_req))
    all_reqs = "\n".join(additional_reqs)

    return all_reqs


def clean_requirements(requirements_text: str) -> str:
    """
    Clean requirements.txt by replacing / removing packages

    E.g. types-pkg_resources has been yanked from PyPI, so we replace it with types-setuptools
    """
    for pkg_to_replace, replacement in REPLACE_REQ_PACKAGES:
        if replacement == None:
            requirements_text = re.sub(
                rf"^{re.escape(pkg_to_replace)}([<>=!~]=?.*|$)\n?",
                "",
                requirements_text,
                flags=re.MULTILINE,
            )
        else:
            # this replacement removes version specifier of the original package
            requirements_text = re.sub(
                rf"^{re.escape(pkg_to_replace)}([<>=!~]=?.*|$)",
                replacement,
                requirements_text,
                flags=re.MULTILINE,
            )
    return requirements_text


def get_requirements(instance: SWEbenchInstance) -> str:
    """
    Get requirements.txt for given task instance

    Args:
        instance (dict): task instance
    Returns:
        requirements.txt (str): Returns requirements.txt as string
    """
    # Attempt to find requirements.txt at each path based on task instance's repo
    commit = (
        instance["environment_setup_commit"]
        if "environment_setup_commit" in instance
        else instance["base_commit"]
    )

    requirements_text = get_requirements_by_commit(instance["repo"], commit)
    requirements_text = clean_requirements(requirements_text)
    return requirements_text


def get_test_directives(instance: SWEbenchInstance) -> list:
    """
    Get test directives from the test_patch of a task instance

    Args:
        instance (dict): task instance
    Returns:
        directives (list): List of test directives
    """
    # For seq2seq code repos, testing command is fixed
    if instance["repo"] == "swe-bench/humaneval":
        return ["test.py"]

    # Get test directives from test patch and remove non-test files
    diff_pat = r"diff --git a/.* b/(.*)"
    test_patch = instance["test_patch"]
    directives = re.findall(diff_pat, test_patch)
    directives = [
        d for d in directives if not any(d.endswith(ext) for ext in NON_TEST_EXTS)
    ]

    # For Django tests, remove extension + "tests/" prefix and convert slashes to dots (module referencing)
    if instance["repo"] == "django/django":
        directives_transformed = []
        for d in directives:
            d = d[: -len(".py")] if d.endswith(".py") else d
            d = d[len("tests/") :] if d.startswith("tests/") else d
            d = d.replace("/", ".")
            directives_transformed.append(d)
        directives = directives_transformed

    return directives


def make_repo_script_list_py(
    specs, repo, repo_directory, base_commit, env_name
) -> list:
    """
    Create a list of bash commands to set up the repository for testing.
    This is the setup script for the instance image.
    """
    branch = REPO_BASE_COMMIT_BRANCH.get(repo, {}).get(base_commit, "")
    branch = f"--branch {branch}" if branch else ""
    setup_commands = [
        f"git clone -o origin {branch} --single-branch https://github.com/{repo} {repo_directory}",
        f"chmod -R 777 {repo_directory}",  # So nonroot user can run tests
        f"cd {repo_directory}",
        f"git reset --hard {base_commit}",
        # Remove the remote and tags so the agent won't see newer commits.
        "git remote remove origin",
        # Remove only tags pointing to commits after target timestamp
        f"TARGET_TIMESTAMP=$(git show -s --format=%ci {base_commit})",
        'git tag -l | while read tag; do TAG_COMMIT=$(git rev-list -n 1 "$tag"); TAG_TIME=$(git show -s --format=%ci "$TAG_COMMIT"); if [[ "$TAG_TIME" > "$TARGET_TIMESTAMP" ]]; then git tag -d "$tag"; fi; done',
        "git reflog expire --expire=now --all",
        "git gc --prune=now --aggressive",
        # Verify future logs aren't available
        "AFTER_TIMESTAMP=$(date -d \"$TARGET_TIMESTAMP + 1 second\" '+%Y-%m-%d %H:%M:%S')",
        'COMMIT_COUNT=$(git log --oneline --all --since="$AFTER_TIMESTAMP" | wc -l)',
        '[ "$COMMIT_COUNT" -eq 0 ] || exit 1',
        # Make sure conda is available for later use
        "source /opt/miniconda3/bin/activate",
        f"conda activate {env_name}",
        'echo "Current environment: $CONDA_DEFAULT_ENV"',
    ]
    if repo in MAP_REPO_TO_INSTALL:
        setup_commands.append(MAP_REPO_TO_INSTALL[repo])

    # Run pre-install set up if provided
    if "pre_install" in specs:
        for pre_install in specs["pre_install"]:
            setup_commands.append(pre_install)

    if "install" in specs:
        setup_commands.append(specs["install"])

    # If the setup modifies the repository in any way, it can be
    # difficult to get a clean diff.  This ensures that `git diff`
    # will only reflect the changes from the user while retaining the
    # original state of the repository plus setup commands.
    clean_diff_commands = [
        "git config --global user.email setup@swebench.config",
        "git config --global user.name SWE-bench",
        "git commit --allow-empty -am SWE-bench",
    ]

    setup_commands += clean_diff_commands

    return setup_commands


def make_env_script_list_py_from_conda(
    instance, specs, env_name, cached_environment_yml
) -> list:
    HEREDOC_DELIMITER = "EOF_59812759871"
    reqs_commands = [
        "source /opt/miniconda3/bin/activate",
        f"cat <<'{HEREDOC_DELIMITER}' > /root/environment.yml\n{cached_environment_yml}\n{HEREDOC_DELIMITER}",
        "conda env create -f /root/environment.yml",
        f"conda activate {env_name}",
    ]
    return reqs_commands


def make_env_script_list_py(instance, specs, env_name) -> list:
    """
    Creates the list of commands to set up the conda environment for testing.
    This is the setup script for the environment image.
    """
    cached_environment_yml = load_cached_environment_yml(instance["instance_id"])
    if cached_environment_yml:
        return make_env_script_list_py_from_conda(
            instance, specs, env_name, cached_environment_yml
        )
    HEREDOC_DELIMITER = "EOF_59812759871"
    reqs_commands = [
        "source /opt/miniconda3/bin/activate",
    ]
    # Create conda environment according to install instructinos
    pkgs = specs.get("packages", "")
    if pkgs == "requirements.txt":
        # Create environment
        cmd = f"conda create -n {env_name} python={specs['python']} -y"
        reqs_commands.append(cmd)

        # Install dependencies
        reqs = get_requirements(instance)
        path_to_reqs = "$HOME/requirements.txt"
        reqs_commands.append(
            f"cat <<'{HEREDOC_DELIMITER}' > {path_to_reqs}\n{reqs}\n{HEREDOC_DELIMITER}"
        )
        cmd = f"conda activate {env_name} && python -m pip install -r {path_to_reqs}"
        reqs_commands.append(cmd)
        reqs_commands.append(f"rm {path_to_reqs}")
    elif pkgs == "environment.yml":
        # Create environment from yml
        reqs = get_environment_yml(instance, env_name)
        path_to_reqs = "environment.yml"
        reqs_commands.append(
            f"cat <<'{HEREDOC_DELIMITER}' > {path_to_reqs}\n{reqs}\n{HEREDOC_DELIMITER}"
        )
        if "no_use_env" in specs and specs["no_use_env"]:
            # `conda create` based installation
            cmd = (
                f"conda create -c conda-forge -n {env_name} python={specs['python']} -y"
            )
            reqs_commands.append(cmd)

            # Install dependencies
            cmd = f"conda env update -f {path_to_reqs}"
            reqs_commands.append(cmd)
        else:
            # `conda env create` based installation
            cmd = f"conda env create --file {path_to_reqs}"
            reqs_commands.append(cmd)

            cmd = f"conda activate {env_name} && conda install python={specs['python']} -y"
            reqs_commands.append(cmd)

        # Remove environment.yml
        reqs_commands.append(f"rm {path_to_reqs}")
    else:
        # Create environment + install dependencies
        cmd = f"conda create -n {env_name} python={specs['python']} {pkgs} -y"
        reqs_commands.append(cmd)

    reqs_commands.append(f"conda activate {env_name}")

    # Install additional packages if specified
    if "pip_packages" in specs:
        pip_packages = " ".join(specs["pip_packages"])
        cmd = f"python -m pip install {pip_packages}"
        reqs_commands.append(cmd)
    return reqs_commands


def make_eval_script_list_py(
    instance, specs, env_name, repo_directory, base_commit, test_patch
) -> list:
    """
    Applies the test patch and runs the tests.
    """
    HEREDOC_DELIMITER = "EOF_114329324912"
    CUSTOM_PATCH_DELIMITER = "EOF_CUSTOM_PATCH_789"  # 新增
    test_files = get_modified_files(test_patch)
    # Reset test files to the state they should be in before the patch.
    reset_tests_command = f"git checkout {base_commit} {' '.join(test_files)}"
    apply_test_patch_command = (
        f"git apply -v - <<'{HEREDOC_DELIMITER}'\n{test_patch}\n{HEREDOC_DELIMITER}"
    )
    # 新增：处理自定义测试补丁
    custom_test_patch = specs.get("custom_test_patch", "")
    apply_custom_patch_command = ""
    if custom_test_patch:
        apply_custom_patch_command = (
            f"git apply -v - <<'{CUSTOM_PATCH_DELIMITER}'\n{custom_test_patch}\n{CUSTOM_PATCH_DELIMITER}"
        )
    fixed_patch = specs.get("fixed_patch", "")
    fixed_patch_commands = []
    if fixed_patch:
        fixed_patch_command = (
            f"""
pip install certifi
python - << 'PYEOF'
from pathlib import Path
path = Path("test_requests.py")
text = path.read_text()
marker = "from __future__ import division"
patch = \"\"\"{fixed_patch}\"\"\"
if marker not in text:
    raise SystemExit("没在文件里找到 'from __future__ import division' 这一行，脚本没改任何东西。")
# 只替换第一次出现的 marker
text = text.replace(marker, patch, 1)
path.write_text(text)
PYEOF
            """
        )
        fixed_patch_commands.append(fixed_patch_command)
    instance_id = instance["instance_id"]
    for fixed_command_dict in FIXED_COMMANDS:
        if instance_id in fixed_command_dict:
            fixed_command = fixed_command_dict[instance_id]
            fixed_patch_commands.append(fixed_command)
#     fixed_patch_commands = []
#     for fixed_patch in FIXED_PATCHES:
#         fixed_patch_command = (
#             f"""
# pip install certifi
# python - << 'PYEOF'
# from pathlib import Path

# path = Path("test_requests.py")
# text = path.read_text()

# marker = "from __future__ import division"
# patch = \"\"\"{fixed_patch}\"\"\"
# if marker not in text:
#     raise SystemExit("没在文件里找到 'from __future__ import division' 这一行，脚本没改任何东西。")

# # 只替换第一次出现的 marker
# text = text.replace(marker, patch, 1)
# path.write_text(text)
# PYEOF
#             """
#         )
#         fixed_patch_commands.append(fixed_patch_command)
    test_command = " ".join(
        [
            MAP_REPO_VERSION_TO_SPECS[instance["repo"]][instance["version"]][
                "test_cmd"
            ],
            *get_test_directives(instance),
        ]
    )
    eval_commands = [
        "source /opt/miniconda3/bin/activate",
        f"conda activate {env_name}",
        f"cd {repo_directory}",
    ]
    if "eval_commands" in specs:
        eval_commands += specs["eval_commands"]
    eval_commands += [
        f"git config --global --add safe.directory {repo_directory}",  # for nonroot user
        f"cd {repo_directory}",
        # This is just informational, so we have a record
        "git status",
        "git show",
        f"git -c core.fileMode=false diff {base_commit}",
        "source /opt/miniconda3/bin/activate",
        f"conda activate {env_name}",
    ]
    if "install" in specs:
        eval_commands.append(specs["install"])
    eval_commands += [
        reset_tests_command,
        apply_test_patch_command,
    ]
    
    # 新增：应用自定义补丁（在原始 test_patch 之后）
    if apply_custom_patch_command:
        eval_commands.append(apply_custom_patch_command)
    if fixed_patch_commands:
        eval_commands += fixed_patch_commands
    if instance["instance_id"] == "django__django-10097":
        # Special handling for django__django-10097
        eval_commands += [
            f": '{START_TEST_OUTPUT}'",
            "DJANGO_SKIP_GENERIC_INLINE_ADMIN=1 && ./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1",
            "DJANGO_SKIP_GENERIC_INLINE_ADMIN= && ./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1 generic_inline_admin.tests ",
            f": '{END_TEST_OUTPUT}'",
            reset_tests_command,
        ]
    else:
        eval_commands += [
            f": '{START_TEST_OUTPUT}'",
            test_command,
            f": '{END_TEST_OUTPUT}'",
            reset_tests_command,
        ]
    return eval_commands
