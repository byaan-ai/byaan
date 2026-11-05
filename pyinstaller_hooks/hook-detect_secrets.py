from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# detect-secrets discovers plugins and filters by listing its package directories, so bundle modules and sources.
hiddenimports = collect_submodules("detect_secrets.plugins") + collect_submodules("detect_secrets.filters")
datas = collect_data_files("detect_secrets", include_py_files=True)
