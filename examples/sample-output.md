```text
examples/requirements-duplicates.txt:4: duplicate package 'requests' (first on line 1: ==2.28.0; this line: ==2.31.0)
examples/requirements-duplicates.txt:6: duplicate package 'my-package' (first on line 5: ==1.0; this line: ==2.0)
examples/requirements-duplicates.txt:7: duplicate package 'requests' (first on line 1: ==2.28.0; this line: ==2.31.0)
examples/requirements-duplicates.txt:8: duplicate package 'Django' (first on line 2: ==4.2.7; this line: ==4.2.7)
examples/configs/requirements.txt:3: duplicate package 'celery' (first on line 1: ==5.3.4; this line: ==5.3.4)
Checked 4 file(s), 5 problem(s) in 2 file(s).
```

Output of `sh examples/run-example.sh` — the checker on the whole
`examples/` folder. It exits 1, because the examples contain files
with duplicates on purpose; the script itself exits 0.
