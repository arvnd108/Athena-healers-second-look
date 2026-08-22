"""SecondLook Tier 1 -- knowledge-graph evidence retrieval.

`secondlook` (the parent package) is a PEP 420 implicit namespace package,
deliberately: this repo owns `secondlook.tier1`, and the separate Tier 2
repo (Athena_tier_2) owns everything else directly under `secondlook/`
(mutation_validation.py, uniprot.py, etc.). For the two to combine into one
`secondlook` namespace when both are pip-installed into the same
environment, NEITHER repo may ship a `secondlook/__init__.py` at the top
level -- a real `__init__.py` there makes it a regular package, which wins
over and hides the other distribution's contribution entirely rather than
merging with it (confirmed empirically: `secondlook.__path__` had only one
entry until both top-level `__init__.py` files were removed).

Editable installs of both packages must also use the "compat" (.pth-based)
editable mode -- setuptools' default finder-based editable install resolves
`secondlook` to a single directory and does not participate in namespace
merging either:

    pip install -e . --config-settings editable_mode=compat

Do not add a `secondlook/__init__.py` back without re-verifying the merge
still works with `import secondlook; secondlook.__path__` showing both
repos' src directories.
"""
