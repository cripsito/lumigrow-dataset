# Germinadora dataset

Layout: `devices/<deviceId>/<lotId>/{crops, dataset.json}` plus device-wide
`observations.csv` and `vertex_manifest.jsonl`.

To push to GitHub:

```bash
cd "$(pwd)"
git init           # only the first time
git remote add origin <your-repo-url>
git add .
git commit -m "dataset update"
git push -u origin main
```

Re-running the export from the app updates the same files in place;
use git diff to see what changed before committing.
