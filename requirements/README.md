# Locked dependencies

`requirements.txt` is the editable dependency manifest. The two lock files cover
Linux on CPython 3.11 and 3.12, including the test dependencies already used by CI.
CI and production must install a committed lock, rather than resolve the manifest.

From the repository root, with the matching Python interpreter:

```bash
python -m pip install --require-hashes -r requirements/linux-py312.lock
python -m pip check
```

Use `linux-py311.lock` for Python 3.11. The Python version used for installation
must match the selected lock. Render uses the Python 3.12 lock.

To update dependencies, first edit the manifest, then run the following under
**each** supported Python version in a separate tooling environment containing
`pip-tools==7.6.2`:

```bash
python -P -m piptools compile --resolver=backtracking --generate-hashes --strip-extras --no-emit-index-url --no-emit-trusted-host --output-file requirements/linux-py312.lock requirements.txt
```

The `-P` option is necessary: the bot has a root-level `build.py`, which must not
shadow pip-tools' external `build` package. Change the output filename under
Python 3.11. Do not regenerate locks during deployment.

Install each new lock into an empty application environment, run `pip check` and
the full test suite, then audit every dependency using `pip-audit==2.10.1` from a
separate tooling environment:

```bash
pip-audit -r requirements/linux-py312.lock --require-hashes --disable-pip --strict --vulnerability-service pypi --format json --output audit-pypi.json
pip-audit -r requirements/linux-py312.lock --require-hashes --disable-pip --strict --vulnerability-service osv --format json --output audit-osv.json
```

Repeat for Python 3.11. Both audits must finish successfully with no advisories
and no ignored dependencies; collection errors are failures, not clean results.
No vulnerability allowlist is used. CI preserves the installed inventory, audit
JSON, security-test report, complete test report and coverage artifacts.

OpenAI and google-genai stay on their compatible 1.x SDK branches (`<2`). Their
exact resolved versions are fixed by the locks. The pytest 9 upgrade requires the
new pytest-asyncio release; keep the strict fixture and deprecation checks.
