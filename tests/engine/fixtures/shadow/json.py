# A helper file that happens to share a name with a standard module.
# It must never replace the real json for the engine's own worker.
raise ImportError("this is the folder's json.py, not the standard one")
